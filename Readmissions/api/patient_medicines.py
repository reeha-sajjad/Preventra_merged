"""
A patient's medicines at home: what to take, how much, how, and how often.

Who sees it:
    care team (doctor, nurse, case manager, superadmin) - on the patient's page,
                       the clinical layer (main._require_detail): a hospital
                       admin or insurer after giving a reason
    patient            their own, on their home page
    caretaker          for a patient who linked them, on the same page
Who changes it:
    doctor             adds a medicine, changes its dose or how often, or stops
                       it - for a patient on their list (care team, or
                       emergency access). Every change is kept in `changes`.

Where the list first comes from (scripts/load_patient_medicines.py):
    mimic   orders still running when the patient went home (MIMIC-IV
            prescriptions). In-hospital orders are the nearest thing MIMIC has
            to a discharge medicine list, not the list itself.
    demo    made up for the demo, by condition, where no MIMIC orders were
            loaded. Every screen says so.
    doctor  added or changed by a doctor in the app.

What this is NOT: a prescription, or an input to the risk model. The model
scored the patient from their discharge data (patient_worklist.raw_inputs);
this list sits beside it in its own collection and changing it never changes a
score. Nothing is deleted: a stopped medicine is kept, marked stopped.

Stored in neuroshield.patient_medicines, one document per patient.
"""
from __future__ import annotations

import secrets
from datetime import datetime, timezone
from typing import Optional

from fastapi import HTTPException

COLLECTION = "patient_medicines"
EDIT_ROLES = ("doctor",)
SOURCES = {"mimic": "From the hospital's orders at discharge (MIMIC-IV)",
           "demo": "Demo medicines - made up for this demo, not from any hospital record",
           "doctor": "Recorded by a doctor"}
ROUTES = ("By mouth", "Injection", "Inhaled", "On the skin", "Under the tongue", "Eye or ear drops", "Other")
HOW_OFTEN = ("Once a day", "Twice a day", "Three times a day", "Four times a day", "At bedtime",
             "Every 4-6 hours as needed", "As needed", "Once a week", "Other")
MAX_TEXT = 80
MAX_REASON = 200
SHOW_STOPPED = 10
MODEL_NOTE = "Changing a medicine here doesn't change the risk score."


def _col(db):
    return db[COLLECTION]


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(at) -> Optional[str]:
    return at.isoformat() if hasattr(at, "isoformat") else at


def _who(user: dict) -> dict:
    return {"id": str(user.get("_id", "")), "name": user.get("name") or user.get("email", ""),
            "role": user.get("role", "")}


def _text(value, name: str, required: bool = False, limit: int = MAX_TEXT) -> Optional[str]:
    text = " ".join(str(value or "").split())
    if required and not text:
        raise HTTPException(status_code=422, detail=f"Give the {name}")
    if len(text) > limit:
        raise HTTPException(status_code=422, detail=f"Keep the {name} under {limit} characters")
    return text or None


def clean(item: dict) -> dict:
    """The fields a person can type, checked."""
    return {"drug": _text(item.get("drug"), "medicine name", required=True),
            "dose": _text(item.get("dose"), "dose", required=True),
            "route": _text(item.get("route"), "route") or "By mouth",
            "how_often": _text(item.get("how_often"), "how often") or "As directed",
            "instructions": _text(item.get("instructions"), "instructions", limit=MAX_REASON)}


def _public(item: dict) -> dict:
    return {"id": item["id"], "drug": item["drug"], "dose": item["dose"], "route": item.get("route"),
            "how_often": item.get("how_often"), "instructions": item.get("instructions"),
            "source": item.get("source", "doctor"), "source_label": SOURCES.get(item.get("source", "doctor")),
            "changed_by": (item.get("changed_by") or {}).get("name"), "changed_at": _iso(item.get("changed_at")),
            "stopped_at": _iso(item.get("stopped_at")), "stop_reason": item.get("stop_reason")}


def view(db, patient_id: str, user: Optional[dict] = None) -> dict:
    """The current list (and the last few stopped), with where it came from."""
    doc = _col(db).find_one({"patient_id": str(patient_id)}) or {}
    items = doc.get("items") or []
    active = [_public(i) for i in items if not i.get("stopped_at")]
    stopped = sorted((_public(i) for i in items if i.get("stopped_at")),
                     key=lambda i: i["stopped_at"] or "", reverse=True)[:SHOW_STOPPED]
    sources = sorted({i["source"] for i in active})
    return {"patient_id": str(patient_id), "medicines": active, "stopped": stopped,
            "has_demo": "demo" in sources, "sources": [{"key": s, "label": SOURCES[s]} for s in sources],
            "model_note": MODEL_NOTE,
            "can_edit": bool(user) and user.get("role") in EDIT_ROLES,
            "routes": list(ROUTES), "how_often_options": list(HOW_OFTEN)}


def for_home(db, patient_id: str) -> dict:
    """What the patient and their caretaker are shown: the current list only."""
    full = view(db, patient_id)
    keep = ("id", "drug", "dose", "route", "how_often", "instructions", "source")
    return {"medicines": [{k: m[k] for k in keep} for m in full["medicines"]],
            "has_demo": full["has_demo"], "sources": full["sources"]}


def _require_editor(user: dict) -> None:
    if user["role"] not in EDIT_ROLES:
        raise HTTPException(status_code=403, detail="Doctors change a patient's medicines")


def _change(patient_id: str, action: str, user: dict, item_id: str, before, after, reason=None) -> dict:
    return {"action": action, "item_id": item_id, "before": before, "after": after, "reason": reason,
            "by": _who(user), "at": _now(), "patient_id": str(patient_id)}


def add(db, user: dict, patient_id: str, item: dict) -> dict:
    _require_editor(user)
    fields = clean(item)
    now = _now()
    new = {**fields, "id": secrets.token_hex(6), "source": "doctor", "added_at": now,
           "changed_at": now, "changed_by": _who(user)}
    _col(db).update_one({"patient_id": str(patient_id)},
                        {"$push": {"items": new, "changes": _change(patient_id, "added", user, new["id"], None, fields)},
                         "$setOnInsert": {"patient_id": str(patient_id)}}, upsert=True)
    return view(db, patient_id, user)


def _find(db, patient_id: str, item_id: str) -> dict:
    doc = _col(db).find_one({"patient_id": str(patient_id)}) or {}
    for i in doc.get("items") or []:
        if i["id"] == item_id and not i.get("stopped_at"):
            return i
    raise HTTPException(status_code=404, detail="Medicine not found")


def change(db, user: dict, patient_id: str, item_id: str, item: dict) -> dict:
    """A new dose, route or how often. The medicine becomes the doctor's: it
    is no longer the MIMIC or demo entry it started as."""
    _require_editor(user)
    old = _find(db, patient_id, item_id)
    fields = clean({**old, **{k: v for k, v in item.items() if v is not None}})
    before = {k: old.get(k) for k in fields}
    if before == fields:
        return view(db, patient_id, user)
    now = _now()
    sets = {f"items.$.{k}": v for k, v in fields.items()}
    sets.update({"items.$.source": "doctor", "items.$.changed_at": now, "items.$.changed_by": _who(user)})
    _col(db).update_one({"patient_id": str(patient_id), "items.id": item_id},
                        {"$set": sets, "$push": {"changes": _change(patient_id, "changed", user, item_id,
                                                                    {**before, "source": old.get("source")},
                                                                    fields)}})
    return view(db, patient_id, user)


def stop(db, user: dict, patient_id: str, item_id: str, reason: Optional[str]) -> dict:
    _require_editor(user)
    old = _find(db, patient_id, item_id)
    why = _text(reason, "reason", limit=MAX_REASON)
    now = _now()
    _col(db).update_one({"patient_id": str(patient_id), "items.id": item_id},
                        {"$set": {"items.$.stopped_at": now, "items.$.stop_reason": why,
                                  "items.$.changed_by": _who(user), "items.$.changed_at": now},
                         "$push": {"changes": _change(patient_id, "stopped", user, item_id,
                                                      {k: old.get(k) for k in ("drug", "dose")}, None, why)}})
    return view(db, patient_id, user)


def history(db, patient_id: str, limit: int = 50) -> list:
    doc = _col(db).find_one({"patient_id": str(patient_id)}, {"changes": 1}) or {}
    rows = list(reversed(doc.get("changes") or []))[:limit]
    return [{**{k: v for k, v in r.items() if k != "at"}, "at": _iso(r.get("at"))} for r in rows]

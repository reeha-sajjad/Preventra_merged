"""
Medicines given: a caretaker ticks "Today's medicines given" for a patient home
from hospital, and the care team sees the log on the patient's page. The
Readmissions side of GLP-1's GLP1/Backend/core/doses.py - one daily tick rather
than dose by dose, because Readmissions keeps no medicine list per patient.

Why only caretakers tick: a carer who gives the medicines is recording
something they did; a patient ticking their own can't be checked. The log is
what was recorded, not proof - the screens say "logged by caretaker".

Who sees it:
    caretaker          ticks for a patient who linked them; can undo their own
                       tick within UNDO_HOURS (a mis-tap)
    patient            sees it on their home page (read only)
    care team          on the patient's page - the clinical layer, the same rule
                       as the rest of it (main._require_detail)

Stored in neuroshield.medicines_given. An undone tick is kept, marked `undone`.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional

from bson import ObjectId
from bson.errors import InvalidId
from fastapi import HTTPException

COLLECTION = "medicines_given"
UNDO_HOURS = 24
SHOW_DAYS = 56
MAX_NOTE = 120
EXPECTED_4_WEEKS = 28
SAME_DAY_HOURS = 12                # a second tick this soon is the same day's


def _col(db):
    return db[COLLECTION]


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _aware(at: datetime) -> datetime:
    return at if at.tzinfo else at.replace(tzinfo=timezone.utc)


def _public(doc: dict, user: Optional[dict]) -> dict:
    at = _aware(doc["given_at"])
    mine = bool(user) and doc["logged_by"]["id"] == str(user.get("_id"))
    return {"id": str(doc["_id"]), "given_at": at.isoformat(), "logged_by": doc["logged_by"]["name"],
            "note": doc.get("note", ""), "can_undo": mine and _now() - at < timedelta(hours=UNDO_HOURS)}


def log(db, patient_id: str, user: Optional[dict] = None) -> dict:
    since = _now() - timedelta(days=SHOW_DAYS)
    docs = list(_col(db).find({"patient_id": str(patient_id), "undone": {"$exists": False},
                               "given_at": {"$gte": since}}).sort("given_at", -1).limit(500))
    four_weeks = _now() - timedelta(days=28)
    return {"patient_id": str(patient_id), "entries": [_public(d, user) for d in docs],
            "last_4_weeks": sum(_aware(d["given_at"]) >= four_weeks for d in docs),
            "expected_4_weeks": EXPECTED_4_WEEKS,
            "can_tick": bool(user) and user.get("role") == "caretaker"}


def tick(db, user: dict, patient_id: str, note: Optional[str] = None) -> dict:
    if user["role"] != "caretaker":
        raise HTTPException(status_code=403, detail="Caretakers record the medicines they give")
    note = (note or "").strip()
    if len(note) > MAX_NOTE:
        raise HTTPException(status_code=422, detail=f"Keep the note under {MAX_NOTE} characters")
    now = _now()
    if _col(db).find_one({"patient_id": str(patient_id), "undone": {"$exists": False},
                          "given_at": {"$gte": now - timedelta(hours=SAME_DAY_HOURS)}}):
        raise HTTPException(status_code=409, detail="Today's medicines are already recorded")
    _col(db).insert_one({"patient_id": str(patient_id), "given_at": now, "note": note, "recorded_at": now,
                         "logged_by": {"id": str(user["_id"]), "name": user.get("name") or user.get("email", ""),
                                       "role": "caretaker"}})
    return log(db, patient_id, user)


def undo(db, user: dict, patient_id: str, entry_id: str) -> dict:
    try:
        oid = ObjectId(entry_id)
    except (InvalidId, TypeError):
        raise HTTPException(status_code=404, detail="Not found")
    doc = _col(db).find_one({"_id": oid, "patient_id": str(patient_id), "undone": {"$exists": False}})
    if not doc:
        raise HTTPException(status_code=404, detail="Not found")
    if doc["logged_by"]["id"] != str(user["_id"]):
        raise HTTPException(status_code=403, detail="Only the person who recorded it can undo it")
    if _now() - _aware(doc["given_at"]) >= timedelta(hours=UNDO_HOURS):
        raise HTTPException(status_code=409, detail=f"A tick can be undone within {UNDO_HOURS} hours")
    _col(db).update_one({"_id": oid}, {"$set": {"undone": _now()}})
    return log(db, patient_id, user)
"""
Where a patient fills their prescriptions: setting and changing a patient's
pharmacy. The twin of api/patient_insurer.py.

The pharmacy is what puts a patient on a pharmacy's page (api/access.
patient_scope: pharmacy_id on the patient's care_actions record). Until now only
scripts/seed_pharmacies.py set it, so a patient added in the app never reached
any pharmacy. The same billing staff who set the insurer set this
(api/member_ids.EDIT_ROLES: superadmin, hospital admin, case manager).

    pharmacies_for_picker   the pharmacies to choose from
    current                 the patient's pharmacy, and whether the viewer may change it
    set_pharmacy            set, change or clear it ("" clears: they stopped filling there)
    history                 every change, newest first
    check_new / file_new    the same for patients being added (api/main.py
                            _add_manual_records): checked before anything is
                            written, filed right after the patient is created

A change takes the patient off the old pharmacy's page on its next request
(the scope is read live), and is kept in pharmacy_history: from, to, who, when.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from fastapi import HTTPException

from api import access, auth, member_ids

SET_ROLES = member_ids.EDIT_ROLES
SEE_ROLES = SET_ROLES + ("doctor", "nurse", "pharmacy")
HISTORY_LIMIT = 20


def _pharmacies(db):
    return db.client[auth.IDENTITY_DB]["pharmacies"]


def _require_set(user: dict) -> None:
    if user["role"] not in SET_ROLES:
        raise HTTPException(status_code=403, detail="Hospital admins and case managers set a patient's pharmacy")


def _by(user: dict) -> dict:
    return {"id": str(user.get("_id", "")), "role": user.get("role", ""),
            "name": user.get("name") or user.get("email", "")}


def resolve(db, value: Optional[str]) -> Optional[dict]:
    """A pharmacy by its id, or by its name (any capitalisation)."""
    text = (value or "").strip()
    if not text:
        return None
    doc = _pharmacies(db).find_one({"_id": text})
    if doc is None:
        for candidate in _pharmacies(db).find({}, {"name": 1}):
            if str(candidate.get("name", "")).strip().lower() == text.lower():
                doc = candidate
                break
    return {"id": doc["_id"], "name": doc.get("name") or doc["_id"]} if doc else None


def pharmacies_for_picker(db, user: dict) -> list:
    _require_set(user)
    return [{"id": d["_id"], "name": d.get("name") or d["_id"]}
            for d in _pharmacies(db).find({}, {"name": 1}).sort("name", 1)]


def _record(db, patient_id: str) -> dict:
    return db["care_actions"].find_one(
        {"patient_id": {"$in": access.id_variants([patient_id])}},
        {"patient_id": 1, "hospital_id": 1, "pharmacy_id": 1, "pharmacy_history": 1}) or {}


def current(db, user: dict, scope: Optional[list], patient_id: str) -> dict:
    access.require_patient(scope, str(patient_id))
    if user["role"] not in SEE_ROLES:
        raise HTTPException(status_code=403, detail="For the care team and the patient's pharmacy")
    doc = _record(db, str(patient_id))
    ph = resolve(db, doc.get("pharmacy_id")) if doc.get("pharmacy_id") else None
    last = (doc.get("pharmacy_history") or [None])[-1] if user["role"] in SET_ROLES else None
    return {"patient_id": str(patient_id), "pharmacy": ph,
            "can_set": user["role"] in SET_ROLES,
            "last_change": _public_change(db, last) if last else None}


def _public_change(db, r: dict) -> dict:
    names = {d["_id"]: d.get("name") or d["_id"] for d in _pharmacies(db).find(
        {"_id": {"$in": [x for x in (r.get("from"), r.get("to")) if x]}})}
    at = r.get("at")
    return {"from": names.get(r.get("from"), r.get("from")) if r.get("from") else None,
            "to": names.get(r.get("to"), r.get("to")) if r.get("to") else None,
            "by": (r.get("by") or {}).get("name"), "by_role": (r.get("by") or {}).get("role"),
            "at": at.isoformat() if hasattr(at, "isoformat") else at}


def set_pharmacy(db, user: dict, scope: Optional[list], patient_id: str, pharmacy: Optional[str]) -> dict:
    """Set, change or clear ("" or None) a patient's pharmacy. Returns current()."""
    access.require_patient(scope, str(patient_id))
    _require_set(user)
    doc = _record(db, str(patient_id))
    if not doc or not doc.get("hospital_id"):
        raise HTTPException(status_code=409, detail="This patient is not in any hospital yet")
    wanted = (pharmacy or "").strip()
    ph = resolve(db, wanted) if wanted else None
    if wanted and ph is None:
        raise HTTPException(status_code=422, detail="Choose a pharmacy from the list")
    new_id, old_id = (ph or {}).get("id"), doc.get("pharmacy_id")
    if new_id == old_id:
        return current(db, user, scope, patient_id)
    change = {"from": old_id, "to": new_id, "by": _by(user), "at": datetime.now(timezone.utc)}
    update: dict = {"$push": {"pharmacy_history": change}}
    if new_id:
        update["$set"] = {"pharmacy_id": new_id}
    else:
        update["$unset"] = {"pharmacy_id": ""}
    db["care_actions"].update_one({"_id": doc["_id"]}, update)
    return current(db, user, scope, patient_id)


def history(db, user: dict, scope: Optional[list], patient_id: str) -> list:
    access.require_patient(scope, str(patient_id))
    _require_set(user)
    rows = list(reversed(_record(db, str(patient_id)).get("pharmacy_history") or []))[:HISTORY_LIMIT]
    return [_public_change(db, r) for r in rows]


# ------------------------------------------------- patients being added
def check_new(db, records: list) -> dict:
    """{row number: [problems]} for the pharmacy of records about to be added."""
    problems: dict = {}
    for row, record in enumerate(records, start=1):
        raw = (record.get("pharmacy") or "").strip()
        if raw and resolve(db, raw) is None:
            problems[row] = [f"no pharmacy called '{raw}'"]
    return problems


def file_new(db, user: dict, patient_id: str, record: dict) -> None:
    """Put a just-added patient with their pharmacy. check_new has passed."""
    ph = resolve(db, record.get("pharmacy"))
    if ph is None:
        return
    db["care_actions"].update_one({"patient_id": str(patient_id)}, {
        "$set": {"pharmacy_id": ph["id"]},
        "$push": {"pharmacy_history": {"from": None, "to": ph["id"], "by": _by(user),
                                       "at": datetime.now(timezone.utc), "via": "added"}}})

"""
Medicines given: a daily tick that the day's medicines were given or taken, for
a patient home from hospital, and the care team sees the log on the patient's
page. The Readmissions side of GLP-1's GLP1/Backend/core/doses.py - one daily
tick rather than dose by dose.

Who ticks - one person per patient, so a day is never counted twice:
    caretaker          "Today's medicines given", for a patient who linked them
    patient            "Today's medicines taken", only while they have no
                       caretaker: not every patient has someone at home. Once
                       a caretaker is linked, the caretaker ticks and the
                       patient reads it.
Either can add a short note (MAX_NOTE), and undo their own tick within
UNDO_HOURS (a mis-tap). The log is what was recorded, not proof - each entry
says who logged it and whether that was the patient or their caretaker.

Who sees it:
    patient, caretaker on the patient's home page
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


def has_caretaker(db, patient_id: str) -> bool:
    """Whether anyone is linked as this patient's caretaker (api/caretakers.py)."""
    from api import access
    return bool(db["care_actions"].find_one(
        {"patient_id": {"$in": access.id_variants([str(patient_id)])}, "caretaker_ids.0": {"$exists": True}},
        {"_id": 1}))


def can_tick(db, user: Optional[dict], patient_id: str) -> bool:
    """A caretaker always; the patient only while they have no caretaker."""
    if not user:
        return False
    if user.get("role") == "caretaker":
        return True
    return user.get("role") == "patient" and not has_caretaker(db, patient_id)


def _public(doc: dict, user: Optional[dict]) -> dict:
    at = _aware(doc["given_at"])
    mine = bool(user) and doc["logged_by"]["id"] == str(user.get("_id"))
    return {"id": str(doc["_id"]), "given_at": at.isoformat(), "logged_by": doc["logged_by"]["name"],
            "by_role": doc["logged_by"].get("role", "caretaker"),
            "note": doc.get("note", ""), "can_undo": mine and _now() - at < timedelta(hours=UNDO_HOURS)}


def log(db, patient_id: str, user: Optional[dict] = None) -> dict:
    since = _now() - timedelta(days=SHOW_DAYS)
    docs = list(_col(db).find({"patient_id": str(patient_id), "undone": {"$exists": False},
                               "given_at": {"$gte": since}}).sort("given_at", -1).limit(500))
    four_weeks = _now() - timedelta(days=28)
    return {"patient_id": str(patient_id), "entries": [_public(d, user) for d in docs],
            "last_4_weeks": sum(_aware(d["given_at"]) >= four_weeks for d in docs),
            "expected_4_weeks": EXPECTED_4_WEEKS,
            "can_tick": can_tick(db, user, patient_id),
            # Already ticked for today (the same SAME_DAY_HOURS rule tick refuses on).
            "ticked_today": bool(docs) and _now() - _aware(docs[0]["given_at"]) < timedelta(hours=SAME_DAY_HOURS),
            "has_caretaker": has_caretaker(db, patient_id)}


def tick(db, user: dict, patient_id: str, note: Optional[str] = None) -> dict:
    if user["role"] == "patient" and has_caretaker(db, patient_id):
        raise HTTPException(status_code=403, detail="Your caretaker records your medicines")
    if not can_tick(db, user, patient_id):
        raise HTTPException(status_code=403, detail="Patients and their caretakers record medicines")
    note = (note or "").strip()
    if len(note) > MAX_NOTE:
        raise HTTPException(status_code=422, detail=f"Keep the note under {MAX_NOTE} characters")
    now = _now()
    if _col(db).find_one({"patient_id": str(patient_id), "undone": {"$exists": False},
                          "given_at": {"$gte": now - timedelta(hours=SAME_DAY_HOURS)}}):
        raise HTTPException(status_code=409, detail="Today's medicines are already recorded")
    _col(db).insert_one({"patient_id": str(patient_id), "given_at": now, "note": note, "recorded_at": now,
                         "logged_by": {"id": str(user["_id"]), "name": user.get("name") or user.get("email", ""),
                                       "role": user["role"]}})
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
"""
Doses given: a caretaker who gives the patient their GLP-1 injection ticks
"Dose given", and the care team sees the log on the patient's page.

Why only caretakers tick: a patient ticking their own dose can't be checked and
adds a chore; a carer who gives the injection is recording something they did.
The log is what was recorded, not proof the dose was taken - the screens say
"logged by caretaker".

Who sees it:
    caretaker          ticks for a patient who linked them; can undo their own
                       tick within UNDO_HOURS (a mis-tap)
    patient            sees the log on their home page (read only)
    doctor, nurse,     see it on the patient's page - the clinical layer, so the
    case_manager,      same rule as the rest of it (core/access.require_detail):
    superadmin         a hospital admin or insurer after giving a reason
    hospital_admin,
    insurer

Stored in glp1_analytics.dose_log. Nothing is deleted except an undone mis-tap,
which is kept in the entry's `undone` field rather than removed.
"""
from datetime import datetime, timedelta, timezone
from typing import Optional

from bson import ObjectId
from bson.errors import InvalidId
from fastapi import HTTPException

from core.mongo import get_db, get_shared_identity_db

COLLECTION = "dose_log"
UNDO_HOURS = 24
SHOW_DAYS = 56                     # eight weeks on screen
MAX_NOTE = 120
EXPECTED_PER_4_WEEKS = {"weekly": 4, "daily": 28}


def _col():
    return get_db()[COLLECTION]


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _aware(at: datetime) -> datetime:
    return at if at.tzinfo else at.replace(tzinfo=timezone.utc)


async def _name(user: dict) -> str:
    try:
        account = await get_shared_identity_db().users.find_one({"_id": ObjectId(user["id"])}, {"name": 1})
    except (InvalidId, TypeError):
        account = None
    return (account or {}).get("name") or user.get("email", "")


def _public(doc: dict, user: Optional[dict] = None) -> dict:
    at = _aware(doc["given_at"])
    mine = bool(user) and doc["logged_by"]["id"] == user.get("id")
    return {"id": str(doc["_id"]), "given_at": at.isoformat(), "logged_by": doc["logged_by"]["name"],
            "note": doc.get("note", ""),
            "can_undo": mine and _now() - at < timedelta(hours=UNDO_HOURS)}


async def log(patient_idx: int, user: Optional[dict] = None, frequency: Optional[str] = None) -> dict:
    """The last SHOW_DAYS of ticks, newest first, and how many in the last four
    weeks against what the schedule expects (when Zoe knows it)."""
    since = _now() - timedelta(days=SHOW_DAYS)
    docs = await _col().find({"patient_idx": int(patient_idx), "undone": {"$exists": False},
                              "given_at": {"$gte": since}}).sort("given_at", -1).to_list(length=500)
    four_weeks = _now() - timedelta(days=28)
    recent = sum(_aware(d["given_at"]) >= four_weeks for d in docs)
    return {"patient_idx": int(patient_idx), "entries": [_public(d, user) for d in docs],
            "last_4_weeks": recent, "expected_4_weeks": EXPECTED_PER_4_WEEKS.get(frequency or ""),
            "frequency": frequency}


async def tick(user: dict, patient_idx: int, note: Optional[str] = None) -> dict:
    """A caretaker records that they gave the dose, now."""
    if user["role"] != "caretaker":
        raise HTTPException(status_code=403, detail="Caretakers record the doses they give")
    note = (note or "").strip()
    if len(note) > MAX_NOTE:
        raise HTTPException(status_code=422, detail=f"Keep the note under {MAX_NOTE} characters")
    now = _now()
    # Two taps in a row are one dose, not two.
    last = await _col().find_one({"patient_idx": int(patient_idx), "undone": {"$exists": False},
                                  "given_at": {"$gte": now - timedelta(hours=2)}})
    if last:
        raise HTTPException(status_code=409, detail="A dose was already recorded in the last two hours")
    await _col().insert_one({"patient_idx": int(patient_idx), "given_at": now, "note": note,
                             "logged_by": {"id": user["id"], "name": await _name(user), "role": "caretaker"},
                             "recorded_at": now})
    return {"ok": True}


async def undo(user: dict, patient_idx: int, dose_id: str) -> dict:
    try:
        oid = ObjectId(dose_id)
    except (InvalidId, TypeError):
        raise HTTPException(status_code=404, detail="Not found")
    doc = await _col().find_one({"_id": oid, "patient_idx": int(patient_idx), "undone": {"$exists": False}})
    if not doc:
        raise HTTPException(status_code=404, detail="Not found")
    if doc["logged_by"]["id"] != user["id"]:
        raise HTTPException(status_code=403, detail="Only the person who recorded a dose can undo it")
    if _now() - _aware(doc["given_at"]) >= timedelta(hours=UNDO_HOURS):
        raise HTTPException(status_code=409, detail=f"A dose can be undone within {UNDO_HOURS} hours")
    await _col().update_one({"_id": oid}, {"$set": {"undone": _now()}})
    return {"ok": True}
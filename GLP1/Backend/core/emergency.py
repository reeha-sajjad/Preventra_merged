"""
Emergency access ("break the glass"): a doctor or nurse opens a patient of their
own hospital who is not on their care team - the patient arrived in the
emergency department, a colleague is away - by giving a reason and a note. The
record opens for EMERGENCY_HOURS, then closes again on its own. Every grant is
logged, flagged, and reviewed afterwards by the hospital admin
(45 CFR 164.312(a)(2)(ii) emergency access procedure; 164.312(b) audit controls).

The Readmissions twin is Readmissions/api/emergency.py. Grants from both apps
live in one collection, shared_identity.emergency_access, so the hospital admin
reviews both in one place (User Management, served by the Readmissions auth
service: Readmissions/api/emergency.py review functions).

How a doctor finds the patient: by the exact patient number, never by browsing
or partial search, so the feature cannot be used to fish through the hospital's
patients (minimum necessary, 45 CFR 164.502(b)). The lookup says only whether
the number is a patient of their hospital who is not on their list - no name,
no diagnosis - until they give a reason.

Grant shape (shared with Readmissions):
    app, patient_id (str), hospital_id, user_id, email, name, role,
    reason, reason_label, note, granted_at, expires_at,
    review: None | {outcome, note, by: {id, email}, at}
"""
from datetime import datetime, timedelta, timezone
from typing import Optional

from bson import ObjectId
from bson.errors import InvalidId
from fastapi import HTTPException

from core import access_log
from core.access import CARE_TEAM_ROLES, EMERGENCY_COLLECTION, EMERGENCY_REASON, patient_hospital
from core.mongo import get_shared_identity_db

APP = "glp1"
EMERGENCY_HOURS = 24
MIN_NOTE = 10
MAX_NOTE = 500
# Must match Readmissions/api/emergency.py REASONS.
REASONS = {
    "emergency_treatment": "Emergency treatment",
    "urgent_consult":      "Urgent consultation requested",
    "covering":            "Covering for the patient's care team",
}


def _col():
    return get_shared_identity_db()[EMERGENCY_COLLECTION]


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _aware(at: datetime) -> datetime:
    return at if at.tzinfo else at.replace(tzinfo=timezone.utc)


def _require_role(user: dict) -> None:
    if user["role"] not in CARE_TEAM_ROLES:
        raise HTTPException(status_code=403, detail="Emergency access is for doctors and nurses")
    if not user.get("hospital_id"):
        raise HTTPException(status_code=403, detail="Your account is not placed in a hospital yet")


def _not_found() -> HTTPException:
    # Same answer for another hospital's patient and for a number that does not
    # exist, so nothing can be learned by trying numbers.
    return HTTPException(status_code=404, detail="No patient with that number in your hospital")


def public(doc: dict) -> dict:
    expires = _aware(doc["expires_at"])
    review = doc.get("review")
    if review and isinstance(review.get("at"), datetime):
        review = {**review, "at": _aware(review["at"]).isoformat()}
    return {"id": str(doc["_id"]), "app": doc["app"], "patient_id": doc["patient_id"],
            "hospital_id": doc.get("hospital_id"), "reason": doc["reason"],
            "reason_label": doc["reason_label"], "note": doc.get("note", ""),
            "user": {"id": doc["user_id"], "email": doc.get("email", ""), "name": doc.get("name", ""),
                     "role": doc.get("role")},
            "granted_at": _aware(doc["granted_at"]).isoformat(), "expires_at": expires.isoformat(),
            "active": expires > _now(), "review": review}


async def _active(user: dict, patient_idx: int) -> Optional[dict]:
    return await _col().find_one({"app": APP, "user_id": user["id"], "patient_id": str(int(patient_idx)),
                                  "expires_at": {"$gt": _now()}}, sort=[("expires_at", -1)])


async def lookup(user: dict, scope: Optional[list], patient_idx: int) -> dict:
    """Is this exact patient number one of the caller's hospital's patients, and
    is it already on their list? Says nothing else."""
    _require_role(user)
    idx = int(patient_idx)
    grant = await _active(user, idx)
    if grant:
        return {"patient_idx": idx, "status": "granted", "grant": public(grant)}
    if scope is not None and idx in scope:
        return {"patient_idx": idx, "status": "on_your_list"}
    if await patient_hospital(idx) != user["hospital_id"]:
        raise _not_found()
    return {"patient_idx": idx, "status": "available", "hours": EMERGENCY_HOURS,
            "reasons": [{"key": k, "label": v} for k, v in REASONS.items()]}


async def grant(user: dict, scope: Optional[list], patient_idx: int, reason: str,
                note: Optional[str]) -> dict:
    _require_role(user)
    idx = int(patient_idx)
    if reason not in REASONS:
        raise HTTPException(status_code=422, detail="Choose a reason from the list")
    note = (note or "").strip()
    if len(note) < MIN_NOTE:
        raise HTTPException(status_code=422,
                            detail=f"Say briefly why (at least {MIN_NOTE} characters); it is reviewed")
    if len(note) > MAX_NOTE:
        raise HTTPException(status_code=422, detail=f"Keep the note under {MAX_NOTE} characters")
    hospital = await patient_hospital(idx)
    if hospital != user["hospital_id"]:
        raise _not_found()
    if scope is not None and idx in scope:
        existing = await _active(user, idx)
        if existing:
            return public(existing)
        raise HTTPException(status_code=409, detail="This patient is already on your list")

    account = None
    try:
        account = await get_shared_identity_db().users.find_one({"_id": ObjectId(user["id"])}, {"name": 1})
    except (InvalidId, TypeError):
        pass
    now = _now()
    doc = {"app": APP, "patient_id": str(idx), "hospital_id": hospital,
           "user_id": user["id"], "email": user.get("email", ""), "name": (account or {}).get("name", ""),
           "role": user["role"], "reason": reason, "reason_label": REASONS[reason], "note": note,
           "granted_at": now, "expires_at": now + timedelta(hours=EMERGENCY_HOURS), "review": None}
    doc["_id"] = (await _col().insert_one(doc)).inserted_id
    # The opening itself, in the shared access log, under its own reason.
    await access_log.record(user, idx, hospital, EMERGENCY_REASON)
    return public(doc)


async def mine(user: dict) -> list:
    """The caller's own emergency grants still open, newest first."""
    if user["role"] not in CARE_TEAM_ROLES:
        return []
    docs = await _col().find({"app": APP, "user_id": user["id"], "expires_at": {"$gt": _now()}}) \
        .sort("granted_at", -1).to_list(length=100)
    return [public(d) for d in docs]


async def for_patient(user: dict, patient_idx: int) -> Optional[dict]:
    """The caller's active grant for this patient, if that is how they reach it -
    for the banner on the patient's page."""
    if user["role"] not in CARE_TEAM_ROLES:
        return None
    grant = await _active(user, patient_idx)
    return public(grant) if grant else None
"""
Emergency access ("break the glass"): a doctor or nurse opens a patient of their
own hospital who is not on their care team - the patient arrived in the
emergency department, a colleague is away - by giving a reason and a note. The
record opens for EMERGENCY_HOURS, then closes again on its own. Every grant is
logged, flagged, and reviewed afterwards by the hospital admin
(45 CFR 164.312(a)(2)(ii) emergency access procedure; 164.312(b) audit controls).

The GLP-1 twin is GLP1/Backend/core/emergency.py - same rules, same reasons,
same grant shape. Grants from both apps live in one collection,
shared_identity.emergency_access, so the review below (served at
/auth/admin/emergency-access, shown in User Management in both apps) covers
both apps in one list.

How a doctor finds the patient: by the exact patient number, never by browsing
or partial search, so the feature cannot be used to fish through the hospital's
patients (minimum necessary, 45 CFR 164.502(b)). The lookup says only whether
the number is a patient of their hospital who is not on their list.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional

from bson import ObjectId
from bson.errors import InvalidId
from fastapi import HTTPException
from pymongo import DESCENDING

from api import access, access_log, auth

APP = "readmissions"
EMERGENCY_HOURS = 24
MIN_NOTE = 10
MAX_NOTE = 500
CARE_TEAM_ROLES = ("doctor", "nurse")
# Must match GLP1/Backend/core/emergency.py REASONS.
REASONS = {
    "emergency_treatment": "Emergency treatment",
    "urgent_consult":      "Urgent consultation requested",
    "covering":            "Covering for the patient's care team",
}
REVIEW_OUTCOMES = {"appropriate": "Appropriate", "inappropriate": "Not appropriate - follow up"}


def _col(db):
    return db.client[auth.IDENTITY_DB][access.EMERGENCY_COLLECTION]


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


def _patient_hospital(db, patient_id: str) -> Optional[str]:
    doc = db["care_actions"].find_one({"patient_id": {"$in": access.id_variants([patient_id])}},
                                      {"hospital_id": 1, "patient_id": 1})
    return (doc or {}).get("hospital_id")


def _canonical(db, patient_id: str) -> str:
    """The patient id as the records store it (ids can arrive as 1101 or MIMIC-1101)."""
    doc = db["care_actions"].find_one({"patient_id": {"$in": access.id_variants([patient_id])}},
                                      {"patient_id": 1})
    return str(doc["patient_id"]) if doc else str(patient_id)


def _active(db, user: dict, patient_id: str) -> Optional[dict]:
    return _col(db).find_one({"app": APP, "user_id": str(user["_id"]), "patient_id": patient_id,
                              "expires_at": {"$gt": _now()}}, sort=[("expires_at", DESCENDING)])


def _exact(patient_id) -> str:
    pid = str(patient_id or "").strip()
    if not pid:
        raise HTTPException(status_code=422, detail="Type the patient's number exactly")
    return pid


def lookup(db, user: dict, scope: Optional[list], patient_id) -> dict:
    """Is this exact patient number one of the caller's hospital's patients, and
    is it already on their list? Says nothing else."""
    _require_role(user)
    if _patient_hospital(db, _exact(patient_id)) != user["hospital_id"]:
        raise _not_found()
    pid = _canonical(db, _exact(patient_id))
    grant = _active(db, user, pid)
    if grant:
        return {"patient_id": pid, "status": "granted", "grant": public(grant)}
    if access.in_scope(scope, pid):
        return {"patient_id": pid, "status": "on_your_list"}
    return {"patient_id": pid, "status": "available", "hours": EMERGENCY_HOURS,
            "reasons": [{"key": k, "label": v} for k, v in REASONS.items()]}


def grant(db, user: dict, scope: Optional[list], patient_id, reason: str, note: Optional[str],
          session: str) -> dict:
    _require_role(user)
    if reason not in REASONS:
        raise HTTPException(status_code=422, detail="Choose a reason from the list")
    note = (note or "").strip()
    if len(note) < MIN_NOTE:
        raise HTTPException(status_code=422,
                            detail=f"Say briefly why (at least {MIN_NOTE} characters); it is reviewed")
    if len(note) > MAX_NOTE:
        raise HTTPException(status_code=422, detail=f"Keep the note under {MAX_NOTE} characters")
    hospital = _patient_hospital(db, _exact(patient_id))
    if hospital != user["hospital_id"]:
        raise _not_found()
    pid = _canonical(db, _exact(patient_id))
    if access.in_scope(scope, pid):
        existing = _active(db, user, pid)
        if existing:
            return public(existing)
        raise HTTPException(status_code=409, detail="This patient is already on your list")

    now = _now()
    doc = {"app": APP, "patient_id": pid, "hospital_id": hospital,
           "user_id": str(user["_id"]), "email": user.get("email", ""), "name": user.get("name", ""),
           "role": user["role"], "reason": reason, "reason_label": REASONS[reason], "note": note,
           "granted_at": now, "expires_at": now + timedelta(hours=EMERGENCY_HOURS), "review": None}
    doc["_id"] = _col(db).insert_one(doc).inserted_id
    # The opening itself, in the shared access log, under its own reason.
    access_log.record(db, user, access_log.APP, pid, hospital, access.EMERGENCY_REASON, session)
    return public(doc)


def mine(db, user: dict) -> list:
    if user["role"] not in CARE_TEAM_ROLES:
        return []
    return [public(d) for d in _col(db).find({"app": APP, "user_id": str(user["_id"]),
                                              "expires_at": {"$gt": _now()}})
            .sort("granted_at", DESCENDING).limit(100)]


def for_patient(db, user: dict, patient_id) -> Optional[dict]:
    """The caller's active grant for this patient, when that - not the care
    team - is how they reach it. For the banner on the patient's page."""
    if user["role"] not in CARE_TEAM_ROLES:
        return None
    grant = _active(db, user, _canonical(db, str(patient_id)))
    if not grant or access.on_care_team(db, user, patient_id):
        return None
    return public(grant)


# ------------------------------------------------------------------ review
# The hospital admin (and the superadmin) review every emergency access in
# their hospital, in both apps, from User Management.
def review_list(db, actor: dict, status: str = "to_review", hospital_id: Optional[str] = None,
                limit: int = 200) -> dict:
    query: dict = {}
    if actor["role"] == "hospital_admin":
        query["hospital_id"] = actor.get("hospital_id") or "__none__"
    elif hospital_id:
        query["hospital_id"] = hospital_id
    to_review = _col(db).count_documents({**query, "review": None})
    if status == "to_review":
        query["review"] = None
    docs = _col(db).find(query).sort("granted_at", DESCENDING).limit(max(1, min(int(limit), 500)))
    return {"items": [public(d) for d in docs], "to_review": to_review,
            "outcomes": [{"key": k, "label": v} for k, v in REVIEW_OUTCOMES.items()]}


def review(db, actor: dict, grant_id: str, outcome: str, note: Optional[str]) -> dict:
    if outcome not in REVIEW_OUTCOMES:
        raise HTTPException(status_code=422, detail="Choose appropriate or not appropriate")
    try:
        oid = ObjectId(grant_id)
    except (InvalidId, TypeError):
        raise HTTPException(status_code=404, detail="Emergency access not found")
    doc = _col(db).find_one({"_id": oid})
    if not doc or (actor["role"] == "hospital_admin" and doc.get("hospital_id") != actor.get("hospital_id")):
        raise HTTPException(status_code=404, detail="Emergency access not found")
    note = (note or "").strip()[:MAX_NOTE]
    if outcome == "inappropriate" and not note:
        raise HTTPException(status_code=422, detail="Say what was wrong, so it can be followed up")
    rev = {"outcome": outcome, "outcome_label": REVIEW_OUTCOMES[outcome], "note": note,
           "by": {"id": str(actor["_id"]), "email": actor.get("email", "")}, "at": _now()}
    _col(db).update_one({"_id": oid}, {"$set": {"review": rev}})
    return public(_col(db).find_one({"_id": oid}))
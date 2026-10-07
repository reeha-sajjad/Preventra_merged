"""
Who opened which GLP-1 patient's clinical details, when, and why.

Written to `shared_identity.access_log`, the one log both products share. The
shape must stay identical to Readmissions/api/access_log.py, which owns the
collection's indexes and serves the log back to User Management.

An entry is also the grant: a hospital admin or insurer who gave a reason for a
patient is not asked again until they sign in again (the session is derived
from the sign-in token in core/security.py).
"""

from datetime import datetime, timezone
from typing import Optional

from core.mongo import get_shared_identity_db

APP = "glp1"
COLLECTION = "access_log"


def _collection():
    return get_shared_identity_db()[COLLECTION]


def _grant_query(user: dict) -> dict:
    return {"user_id": user["id"], "session": user.get("session", ""), "app": APP}


async def has_opened(user: dict, patient_idx: int) -> bool:
    doc = await _collection().find_one({**_grant_query(user), "patient_id": str(int(patient_idx))},
                                       {"_id": 1})
    return doc is not None


async def opened(user: dict) -> set:
    docs = await _collection().find(_grant_query(user), {"_id": 0, "patient_id": 1}).to_list(length=None)
    return {int(d["patient_id"]) for d in docs}


async def record(user: dict, patient_idx: int, hospital_id: Optional[str], reason: str) -> dict:
    from core.access import REASONS, LOGGED_REASONS     # avoid an import cycle
    label = LOGGED_REASONS.get(reason) or REASONS[reason]
    entry = {"user_id": user["id"], "email": user.get("email", ""), "role": user["role"],
             "user_hospital_id": user.get("hospital_id"), "app": APP,
             "patient_id": str(int(patient_idx)), "hospital_id": hospital_id,
             "reason": reason, "reason_label": label, "session": user.get("session", ""),
             "at": datetime.now(timezone.utc)}
    await _collection().insert_one(dict(entry))
    return entry
"""Who opened which patient's clinical details, when, and why.

One collection for both products, next to the accounts:
`shared_identity.access_log`. GLP-1 writes its own entries there
(GLP1/Backend/core/access_log.py, which must keep the same shape); User
Management in this service reads them back, one hospital at a time.

An entry is also the grant. A hospital admin or insurer who gave a reason for a
patient is not asked again for that patient until they sign in again: the
lookup is by (user, session, app, patient), and the session is the sign-in
(session_of), so signing in again starts a new one.

    user_id, email, role    who looked
    user_hospital_id        where they work (None for superadmin and insurers)
    app                     "readmissions" or "glp1"
    patient_id              as a string, in that app's own ids
    hospital_id             the patient's hospital - what a hospital admin's
                            view of the log is filtered on
    reason, reason_label    the key from access.REASONS, and how it read then
    session                 see session_of()
    at                      UTC
"""
from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Optional

from api import access, auth

COLLECTION = "access_log"
APP = "readmissions"
MAX_ENTRIES = 500


def collection(db):
    return db.client[auth.IDENTITY_DB][COLLECTION]


def session_of(token: str) -> str:
    """One sign-in: the token's `sid` claim (see api/auth.py), which survives a
    refresh. A token from before `sid` existed is its own session, named by a
    hash so the token itself is never stored. Called only on a token that has
    already been verified."""
    try:
        sid = auth.decode_token(token).get("sid")
    except Exception:                                     # noqa: BLE001
        sid = None
    return sid or hashlib.sha256((token or "").encode()).hexdigest()[:32]


def ensure_indexes(db) -> None:
    try:
        collection(db).create_index([("user_id", 1), ("session", 1), ("app", 1), ("patient_id", 1)],
                                    name="grant_lookup")
        collection(db).create_index([("hospital_id", 1), ("at", -1)], name="by_hospital")
    except Exception as exc:                              # noqa: BLE001
        # Runs at import, like the users index: a blip must not stop the boot.
        print(f"WARNING: could not create the access_log indexes ({type(exc).__name__}: {exc})")


def _grant_query(user: dict, app: str, session: str) -> dict:
    return {"user_id": str(user["_id"]), "session": session, "app": app}


def has_opened(db, user: dict, app: str, patient_id, session: str) -> bool:
    return collection(db).find_one({**_grant_query(user, app, session),
                                    "patient_id": str(patient_id)}, {"_id": 1}) is not None


def opened(db, user: dict, app: str, session: str) -> set:
    """Every patient this user opened in this app during this sign-in."""
    return {d["patient_id"] for d in collection(db).find(_grant_query(user, app, session),
                                                         {"patient_id": 1})}


def record(db, user: dict, app: str, patient_id, hospital_id: Optional[str],
           reason: str, session: str) -> dict:
    label = access.LOGGED_REASONS.get(reason) or access.REASONS[reason]
    entry = {"user_id": str(user["_id"]), "email": user.get("email", ""), "role": user["role"],
             "user_hospital_id": user.get("hospital_id"), "app": app,
             "patient_id": str(patient_id), "hospital_id": hospital_id,
             "reason": reason, "reason_label": label, "session": session,
             "at": datetime.now(timezone.utc)}
    collection(db).insert_one(dict(entry))
    return entry


def entries(db, actor: dict, hospital_id: Optional[str] = None, app: Optional[str] = None,
            limit: int = 200) -> list:
    """The log as a manager may read it: a hospital admin sees who opened its
    own hospital's patients - including our team and insurers - and nothing
    else; the superadmin sees everything, or one hospital."""
    if actor["role"] == "hospital_admin":
        hospital_id = actor.get("hospital_id") or "__none__"
    query: dict = {}
    if hospital_id:
        query["hospital_id"] = hospital_id
    if app:
        query["app"] = app
    cursor = (collection(db).find(query, {"_id": 0, "session": 0})
              .sort("at", -1).limit(max(1, min(int(limit), MAX_ENTRIES))))
    out = []
    for e in cursor:
        at = e.get("at")
        if isinstance(at, datetime):
            e["at"] = (at if at.tzinfo else at.replace(tzinfo=timezone.utc)).isoformat()
        out.append(e)
    return out
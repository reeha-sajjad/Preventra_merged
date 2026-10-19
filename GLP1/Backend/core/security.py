"""
Who is making this request: the dependency every GLP-1 /api route runs.

This service never issues tokens - the Readmissions API does, for both
products. Here the token only proves identity. The account is then read from
`shared_identity.users` on every request, so an approval, a role change or a
removal made by an admin applies to the very next request, not when the token
expires.
"""

import hashlib

from bson import ObjectId
from bson.errors import InvalidId
from fastapi import Depends, HTTPException
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from jose import jwt, JWTError

from core.config import settings
from core.mongo import get_shared_identity_db

_ALGORITHM = "HS256"
_bearer_scheme = HTTPBearer()

# Must stay identical to ROLES and STATUSES in Readmissions/api/auth.py, which
# owns the accounts. The two services deploy separately, so they cannot share
# one module.
ROLES = ("superadmin", "hospital_admin", "doctor", "nurse", "case_manager",
         "insurer", "pharmacy", "caretaker", "patient")
STATUSES = ("pending", "active")
DEFAULT_ROLE = "case_manager"


def effective(account: dict) -> dict:
    """Role, status and hospital as the rest of GLP-1 must treat them.

    An account without a valid `status` predates fixed roles, when signup stored
    any role string it was sent - so a stored "superadmin" there proves nothing.
    Read such accounts as an active case_manager with no hospital, exactly as
    the Readmissions side does.
    """
    if account.get("status") not in STATUSES or account.get("role") not in ROLES:
        role, status, hospital_id = DEFAULT_ROLE, "active", None
    else:
        role, status, hospital_id = account["role"], account["status"], account.get("hospital_id")
    return {"id": str(account["_id"]), "email": account.get("email", ""),
            "role": role, "status": status, "hospital_id": hospital_id,
            "insurer_id": account.get("insurer_id"),
            "pharmacy_id": account.get("pharmacy_id"),
            "must_change_password": bool(account.get("must_change_password")),
            "app_access": account.get("app_access") or ["glp1", "readmissions"]}


async def current_user(
    credentials: HTTPAuthorizationCredentials = Depends(_bearer_scheme),
) -> dict:
    try:
        claims = jwt.decode(credentials.credentials, settings.shared_secret_key,
                            algorithms=[_ALGORITHM], options={"require_exp": True})
        account = await get_shared_identity_db().users.find_one(
            {"_id": ObjectId(claims.get("sub"))})
    except (JWTError, InvalidId, TypeError):
        raise HTTPException(status_code=401, detail="Invalid or expired token")

    if account is None:
        raise HTTPException(status_code=401, detail="Account no longer exists")

    user = effective(account)
    if user["status"] != "active":
        raise HTTPException(status_code=403,
                            detail="This account is waiting for approval by an administrator")
    # A temporary password an admin has seen - and may have sent in a message -
    # must not unlock patient data. The portal makes them replace it first.
    if user["must_change_password"]:
        raise HTTPException(status_code=403, detail="Set a new password before continuing")
    if "glp1" not in user["app_access"]:
        raise HTTPException(status_code=403, detail="This account doesn't have access to GLP-1")
    # Which sign-in this is, for the access log (core/access_log.py): the
    # token's `sid`, or for an older token without one, a hash of it. The same
    # rule as Readmissions/api/access_log.session_of.
    user["session"] = (claims.get("sid")
                       or hashlib.sha256(credentials.credentials.encode()).hexdigest()[:32])
    return user
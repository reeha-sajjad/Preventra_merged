"""User Management: hospitals, insurers, pharmacies, and the accounts that work for them.

Accounts are shared by both products, so this lives once, here in the shared
auth service, and both apps' Settings pages call it.

Who may do what - enforced here, never only in a frontend:

    superadmin      creates hospitals, insurers, pharmacies and hospital admins;
                    places insurer and pharmacy staff; manages
                    every account except other superadmins
    hospital_admin  manages doctors, nurses, case managers and patients in its
                    own hospital, and nothing outside it
    caretaker       a family or home carer signs up themselves and is active at
                    once, attached to nothing; they see a patient only once
                    the patient (or staff, with the patient's agreement) links
                    them (api/caretakers.py). The superadmin can manage them.

No path here can create a superadmin or promote anyone to one; that is
scripts/create_superadmin.py, run by someone with direct cluster access.

Approving a self-signup: a sign-up names the role it is asking for (see
auth.SIGNUP_ROLES) and, for a hospital role, the hospital it wants to join.
It waits as pending, attached to nothing, until someone approves it:

    hospital admin  sees the requests for its own hospital - for the roles it
                    may hand out - and approves (keeping or changing the role)
                    or declines them. It never sees another hospital's requests.
                    It can also approve by adding the person's email, as before.
    superadmin      sees every request, with the requested hospital already
                    filled in, and can approve or decline any of them.

A request to be a hospital admin only ever reaches the superadmin: one
hospital admin cannot make another.
"""
from __future__ import annotations

import csv
import io
import os
import secrets
from typing import Optional

from bson import ObjectId
from bson.errors import InvalidId
from fastapi import HTTPException

from api import auth

HOSPITALS = "hospitals"
INSURERS = "insurers"
PHARMACIES = "pharmacies"

MANAGER_ROLES = ("superadmin", "hospital_admin")

# Which roles each manager may hand out. superadmin is absent from both on
# purpose: it is never assignable through this module.
ASSIGNABLE = {
    "superadmin": tuple(r for r in auth.ROLES if r != "superadmin"),
    "hospital_admin": ("doctor", "nurse", "case_manager", "patient"),
}

MAX_IMPORT_ROWS = 1000

# GLP-1's database, where each GLP-1 patient's record (insurer, care team) lives.
GLP1_DB = os.environ.get("GLP1_DB", "glp1_analytics")

# No 0/O or 1/l/I: the admin reads this out or types it into a message.
_TEMP_ALPHABET = "abcdefghjkmnpqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789"


def _identity(db):
    return db.client[auth.IDENTITY_DB]


def hospitals(db):
    return _identity(db)[HOSPITALS]


def insurers(db):
    return _identity(db)[INSURERS]


def pharmacies(db):
    return _identity(db)[PHARMACIES]


def temporary_password() -> str:
    return "".join(secrets.choice(_TEMP_ALPHABET) for _ in range(12))


def _forbidden(detail: str):
    return HTTPException(status_code=403, detail=detail)


def require_manager(actor: dict) -> None:
    if actor["role"] not in MANAGER_ROLES:
        raise _forbidden("Only a superadmin or a hospital admin can manage users")


def _require_superadmin(actor: dict) -> None:
    if actor["role"] != "superadmin":
        raise _forbidden("Only a superadmin can do this")


def admin_view(account: dict) -> dict:
    """An account as it appears in User Management. Never the password hash."""
    account = auth.effective(account)
    return {**auth.public_view(account),
            "name": account.get("name", ""),
            "insurer_id": account.get("insurer_id"),
            "pharmacy_id": account.get("pharmacy_id"),
            # What a self-signup asked for; None for accounts an admin created.
            "requested_role": account.get("requested_role"),
            "requested_hospital_id": account.get("requested_hospital_id"),
            "declined": account.get("declined"),
            "must_change_password": bool(account.get("must_change_password")),
            "created_at": account.get("created_at", "")}


# ------------------------------------------------------- organisations
def _create_org(collection, actor: dict, name: str) -> dict:
    name = (name or "").strip()
    if not name:
        raise HTTPException(status_code=422, detail="A name is required")
    doc = {"_id": auth.slugify_org(name), "name": name,
           "created_at": auth._now(), "created_by": actor["email"]}
    if collection.find_one({"_id": doc["_id"]}):
        raise HTTPException(status_code=409, detail=f"'{doc['_id']}' already exists")
    collection.insert_one(doc)
    return {"id": doc["_id"], "name": name}


def create_hospital(db, actor: dict, name: str) -> dict:
    _require_superadmin(actor)
    return _create_org(hospitals(db), actor, name)


def create_insurer(db, actor: dict, name: str) -> dict:
    _require_superadmin(actor)
    return _create_org(insurers(db), actor, name)


def create_pharmacy(db, actor: dict, name: str) -> dict:
    _require_superadmin(actor)
    return _create_org(pharmacies(db), actor, name)


def list_pharmacies(db, actor: dict) -> list:
    require_manager(actor)
    return [{"id": p["_id"], "name": p["name"]} for p in pharmacies(db).find().sort("name", 1)]


def list_hospitals(db, actor: dict) -> list:
    """Every hospital for the superadmin; only its own for a hospital admin."""
    require_manager(actor)
    query = {} if actor["role"] == "superadmin" else {"_id": actor["hospital_id"]}
    return [{"id": h["_id"], "name": h["name"]}
            for h in hospitals(db).find(query).sort("name", 1)]


def list_insurers(db, actor: dict) -> list:
    require_manager(actor)
    return [{"id": i["_id"], "name": i["name"]} for i in insurers(db).find().sort("name", 1)]


# ------------------------------------------------------------ rules
def _check_assignable(actor: dict, role: str) -> None:
    if role not in auth.ROLES:
        raise HTTPException(status_code=422, detail=f"Unknown role '{role}'")
    if role not in ASSIGNABLE[actor["role"]]:
        raise _forbidden(f"A {actor['role']} cannot assign the role '{role}'")


def _placement(db, actor: dict, role: str, hospital_id: Optional[str],
               insurer_id: Optional[str], pharmacy_id: Optional[str] = None) -> dict:
    """Where an account with this role belongs, checked against what exists and
    what the actor may touch. A hospital admin can only ever place people in
    its own hospital, whatever the request says."""
    if role in auth.HOSPITAL_ROLES:
        if actor["role"] == "hospital_admin":
            hospital_id = actor["hospital_id"]
        if not hospital_id:
            raise HTTPException(status_code=422, detail=f"A {role} must belong to a hospital")
        if not hospitals(db).find_one({"_id": hospital_id}):
            raise HTTPException(status_code=422, detail=f"No hospital '{hospital_id}'")
        return {"hospital_id": hospital_id, "insurer_id": None, "pharmacy_id": None}
    if role == "insurer":
        if not insurer_id:
            raise HTTPException(status_code=422, detail="An insurer account must belong to an insurer")
        if not insurers(db).find_one({"_id": insurer_id}):
            raise HTTPException(status_code=422, detail=f"No insurer '{insurer_id}'")
        return {"hospital_id": None, "insurer_id": insurer_id, "pharmacy_id": None}
    if role == "pharmacy":
        # A pharmacy's staff belong to the pharmacy, which our team assigns -
        # like an insurer's. They see the patients who fill there, in any hospital.
        if not pharmacy_id:
            raise HTTPException(status_code=422, detail="A pharmacy account must belong to a pharmacy")
        if not pharmacies(db).find_one({"_id": pharmacy_id}):
            raise HTTPException(status_code=422, detail=f"No pharmacy '{pharmacy_id}'")
        return {"hospital_id": None, "insurer_id": None, "pharmacy_id": pharmacy_id}
    return {"hospital_id": None, "insurer_id": None, "pharmacy_id": None}


def _load_target(db, actor: dict, user_id: str) -> dict:
    try:
        target = auth.users(db).find_one({"_id": ObjectId(user_id)})
    except (InvalidId, TypeError):
        target = None
    # The same answer for "does not exist" and "not yours", so a hospital admin
    # cannot probe for accounts in other hospitals.
    if not target or not _may_manage(actor, auth.effective(target)):
        raise HTTPException(status_code=404, detail="No such user")
    return target


def _is_unattached_signup(target: dict) -> bool:
    """A self-signup nobody has approved yet: pending, in no hospital, insurer
    or pharmacy. (A suspended account is pending too, but keeps its hospital.)"""
    return (target["status"] == "pending" and not target.get("hospital_id")
            and not target.get("insurer_id") and not target.get("pharmacy_id"))


def _is_request_for(actor: dict, target: dict) -> bool:
    """A sign-up asking to join this hospital admin's hospital, in a role the
    admin may hand out - so, never a request to be a hospital admin."""
    return (actor["role"] == "hospital_admin" and _is_unattached_signup(target)
            and bool(actor.get("hospital_id"))
            and target.get("requested_hospital_id") == actor["hospital_id"]
            and target["role"] in ASSIGNABLE["hospital_admin"])


def _may_manage(actor: dict, target: dict) -> bool:
    if target["role"] == "superadmin" or str(target["_id"]) == str(actor["_id"]):
        return False                       # no one edits a superadmin, or themselves
    if actor["role"] == "superadmin":
        return True
    if _is_request_for(actor, target):
        return True
    return (target["hospital_id"] == actor["hospital_id"]
            and target["role"] in ASSIGNABLE["hospital_admin"])


# ------------------------------------------------------------ accounts
def list_users(db, actor: dict, hospital_id: Optional[str] = None,
               status: Optional[str] = None, role: Optional[str] = None) -> list:
    require_manager(actor)
    query: dict = {}
    if actor["role"] == "hospital_admin":
        # Its own staff and patients, plus the sign-ups asking to join it.
        query["$or"] = [
            {"hospital_id": actor["hospital_id"], "role": {"$in": [*ASSIGNABLE["hospital_admin"], "hospital_admin"]}},
            {"status": "pending", "hospital_id": None, "insurer_id": None, "pharmacy_id": None,
             "requested_hospital_id": actor["hospital_id"] or "__none__",
             "role": {"$in": list(ASSIGNABLE["hospital_admin"])}},
        ]
    elif hospital_id:
        query["hospital_id"] = hospital_id
    if status:
        query["status"] = status
    if role:
        query["role"] = role
    accounts = list(auth.users(db).find(query, {"password_hash": 0}).sort("created_at", -1))
    insurance = patient_insurers(db, accounts)
    return [{**admin_view(u), "patient_insurer_id": insurance.get(str(u["_id"]))} for u in accounts]


def patient_insurers(db, accounts: list) -> dict:
    """account id -> the insurer of the patient record that login opens.

    A patient's insurance is kept on their patient record, not on the login
    (a login's own insurer_id is only for insurer staff). Looked up live, so it
    never goes stale: GLP-1's record first, else the Readmissions one."""
    ids = [str(a["_id"]) for a in accounts if a.get("role") == "patient"]
    if not ids:
        return {}
    found: dict = {}
    for collection in (db.client[GLP1_DB]["patient_access"], db["care_actions"]):
        for rec in collection.find({"patient_account_id": {"$in": ids}, "insurer_id": {"$nin": [None, ""]}},
                                   {"patient_account_id": 1, "insurer_id": 1}):
            found.setdefault(str(rec["patient_account_id"]), rec["insurer_id"])
    return found


def create_user(db, actor: dict, email: str, role: str, name: str = "",
                hospital_id: Optional[str] = None, insurer_id: Optional[str] = None,
                pharmacy_id: Optional[str] = None) -> dict:
    """Add an account with a temporary password, or approve a pending self-signup
    with the same email. Returns the account and, for a new one, the temporary
    password - shown once and never stored in plain text."""
    require_manager(actor)
    _check_assignable(actor, role)
    email = auth.normalise_email(email)
    if not auth._EMAIL_RE.match(email):
        raise HTTPException(status_code=422, detail="A valid email address is required")
    place = _placement(db, actor, role, hospital_id, insurer_id, pharmacy_id)
    now = auth._now()
    fields = {"role": role, "status": "active", "name": (name or "").strip(),
              **place, "updated_at": now}

    existing = auth.users(db).find_one({"email": email})
    if existing:
        current = auth.effective(existing)
        if not _is_unattached_signup(current):
            raise HTTPException(status_code=409, detail="An account with that email already exists")
        wanted = existing.get("requested_hospital_id")
        if actor["role"] == "hospital_admin" and wanted and wanted != actor["hospital_id"]:
            raise HTTPException(status_code=409,
                                detail="This person asked to join another hospital")
        auth.users(db).update_one({"_id": existing["_id"]},
                                  {"$set": {**fields, "approved_by": actor["email"]}})
        return {"user": admin_view({**existing, **fields}), "temporary_password": None,
                "approved_existing": True}

    password = temporary_password()
    doc = {"email": email, "password_hash": auth.hash_password(password),
           "must_change_password": True,
           "app_access": list(auth.DEFAULT_APP_ACCESS),
           "created_at": now, "created_by": actor["email"], **fields}
    try:
        doc["_id"] = auth.users(db).insert_one(doc).inserted_id
    except Exception as exc:
        if "duplicate key" in str(exc).lower() or "E11000" in str(exc):
            raise HTTPException(status_code=409,
                                detail="An account with that email already exists") from exc
        raise
    return {"user": admin_view(doc), "temporary_password": password, "approved_existing": False}


def update_user(db, actor: dict, user_id: str, role: Optional[str] = None,
                status: Optional[str] = None, hospital_id: Optional[str] = None,
                insurer_id: Optional[str] = None, name: Optional[str] = None,
                pharmacy_id: Optional[str] = None) -> dict:
    """Change a role, approve or suspend (status), move or name an account."""
    require_manager(actor)
    target = _load_target(db, actor, user_id)
    current = auth.effective(target)

    new_role = role or current["role"]
    if role is not None:
        _check_assignable(actor, role)
    if status is not None and status not in auth.STATUSES:
        raise HTTPException(status_code=422, detail=f"Status must be one of {list(auth.STATUSES)}")

    # Approving a sign-up without naming a hospital puts it where it asked to go.
    if hospital_id is None:
        hospital_id = current["hospital_id"]
        if not hospital_id and _is_unattached_signup(current):
            hospital_id = target.get("requested_hospital_id")
    place = _placement(db, actor, new_role, hospital_id,
                       insurer_id if insurer_id is not None else target.get("insurer_id"),
                       pharmacy_id if pharmacy_id is not None else target.get("pharmacy_id"))
    fields = {"role": new_role, "status": status or current["status"], **place,
              "updated_at": auth._now()}
    if name is not None:
        fields["name"] = name.strip()
    auth.users(db).update_one({"_id": target["_id"]}, {"$set": fields})
    return admin_view({**target, **fields})


def decline_signup(db, actor: dict, user_id: str) -> dict:
    """Turn down a sign-up's request to join a hospital.

    The account stays pending and sees nothing; it just leaves that hospital's
    list. The superadmin still sees it and can place it elsewhere. Who declined,
    and which hospital, is kept on the account."""
    require_manager(actor)
    target = _load_target(db, actor, user_id)
    current = auth.effective(target)
    if not _is_unattached_signup(current) or not target.get("requested_hospital_id"):
        raise HTTPException(status_code=409, detail="There is no pending request to decline")
    declined = {"hospital_id": target["requested_hospital_id"], "by": actor["email"],
                "at": auth._now()}
    fields = {"requested_hospital_id": None, "declined": declined, "updated_at": auth._now()}
    auth.users(db).update_one({"_id": target["_id"]}, {"$set": fields})
    return admin_view({**target, **fields})


def list_public_hospitals(db) -> list:
    """The hospitals someone signing up may ask to join: names and ids only,
    for the portal's sign-up form. Nothing else about them leaves here."""
    return [{"id": h["_id"], "name": h["name"]}
            for h in hospitals(db).find({}, {"name": 1}).sort("name", 1)]


def import_users(db, actor: dict, csv_text: str, hospital_id: Optional[str] = None,
                 insurer_id: Optional[str] = None, pharmacy_id: Optional[str] = None) -> dict:
    """Create accounts from CSV with columns email, name, role (header row
    required). Each row succeeds or fails on its own, so one bad line does not
    lose the rest; failures come back with their line number."""
    require_manager(actor)
    reader = csv.DictReader(io.StringIO((csv_text or "").strip()))
    columns = {c.strip().lower() for c in (reader.fieldnames or [])}
    if not {"email", "role"} <= columns:
        raise HTTPException(status_code=422,
                            detail="The CSV needs a header row with at least: email, role (and optionally name)")
    rows = list(reader)
    if len(rows) > MAX_IMPORT_ROWS:
        raise HTTPException(status_code=422, detail=f"At most {MAX_IMPORT_ROWS} rows per import")

    created, failed = [], []
    for line, row in enumerate(rows, start=2):          # line 1 is the header
        row = {(k or "").strip().lower(): (v or "").strip() for k, v in row.items()}
        try:
            result = create_user(db, actor, row.get("email", ""), row.get("role", "").lower(),
                                 row.get("name", ""), hospital_id, insurer_id, pharmacy_id)
            created.append({"line": line, **result})
        except HTTPException as exc:
            failed.append({"line": line, "email": row.get("email", ""), "error": exc.detail})
    return {"created": created, "failed": failed}


# ------------------------------------------------------------ own password
def change_password(db, token: str, current_password: str, new_password: str) -> dict:
    """Replace your own password, clearing the must-change flag a temporary
    password carries. Returns a token with the updated claims."""
    claims = auth.decode_token(token)
    account = auth.authenticate(db, token, allow_pending=True, allow_password_change=True)
    if not auth.password_matches(current_password or "", account.get("password_hash", "")):
        raise HTTPException(status_code=401, detail="The current password is incorrect")
    if len(new_password or "") < auth.MIN_PASSWORD_LENGTH:
        raise HTTPException(status_code=422, detail=f"The new password must be at least "
                                                    f"{auth.MIN_PASSWORD_LENGTH} characters")
    if new_password == current_password:
        raise HTTPException(status_code=422, detail="Choose a password different from the current one")
    fields = {"password_hash": auth.hash_password(new_password),
              "must_change_password": False, "updated_at": auth._now()}
    auth.users(db).update_one({"_id": account["_id"]}, {"$set": fields})
    return auth.issue_token({**account, **fields}, exp=claims["exp"], sid=claims.get("sid"))
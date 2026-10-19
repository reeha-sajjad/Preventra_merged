"""Shared login: the service that issues tokens for both products.

One account works across GLP-1 and Readmissions. This module owns the identity
side of that: it stores accounts in `shared_identity.users` on the shared
cluster, checks passwords, and signs a JWT that either product can verify on its
own with the same secret.

Issuing lives in exactly one place - here. Each product verifies independently
and never calls back, which is what keeps the two backends separately
deployable. A second service that also issued tokens would put us back to two
logins, which is the problem this replaces.

Compatibility matters: accounts already exist in that collection, created
elsewhere with bcrypt at cost 12 (`$2b$12$`, 60 chars). The hashing here matches,
so those accounts keep working and nobody has to reset a password.

Token claims, frozen by agreement between both products:

    {"sub": "<users._id as a string>", "email": ..., "role": ..., "status": ...,
     "hospital_id": ..., "must_change_password": <bool>,
     "app_access": ["glp1", "readmissions"], "sid": <sign-in id>, "exp": <unix>}

`sid` is random per sign-in and kept when the token is refreshed, so it names
one sign-in session - the access log's "not asked again until you sign in
again" is keyed on it. Tokens issued before it existed have none; see
api/access_log.session_of for the fallback.

The claims are for display only. Both backends re-read the account on every
request (see `authenticate`), so an approval, a role change or a removal takes
effect on the next request, not when the token runs out.
"""
from __future__ import annotations

import os
import re
import secrets
import time
from datetime import datetime
from typing import Optional

import bcrypt
import jwt
from bson import ObjectId
from bson.errors import InvalidId
from dotenv import load_dotenv
from fastapi import Header, HTTPException, Request

# Loaded here rather than left to the importer: api.main calls load_dotenv()
# after importing this module, so without this the secret below reads as empty
# on any machine that keeps it in .env rather than in real environment
# variables. Idempotent, so the duplicate call costs nothing - do not remove it.
load_dotenv()

# Shared with the other product. Symmetric (HS256): holding it means being able
# to MINT tokens, not merely check them, so it must never reach a browser bundle
# or a commit.
SHARED_SECRET_KEY = os.environ.get("SHARED_SECRET_KEY", "")
ALGORITHM = "HS256"

# 12 hours. Long enough that nobody is signed out mid-session, short enough that
# a token copied out of a URL or a chat log stops working the same day. There is
# no refresh flow yet, so this is also the hard session length.
TOKEN_TTL_SECONDS = int(os.environ.get("SHARED_AUTH_TOKEN_TTL", str(12 * 60 * 60)))

# Which applications a new account may use. Both, by default: the demo has no
# per-product entitlement model, and a user who cannot open either app is
# useless. The value is stored on the account so it can be narrowed per user
# later without changing this code.
DEFAULT_APP_ACCESS = [a.strip() for a in
                      os.environ.get("SHARED_AUTH_DEFAULT_APPS", "glp1,readmissions").split(",")
                      if a.strip()]

IDENTITY_DB = os.environ.get("SHARED_IDENTITY_DB", "shared_identity")
USERS_COLLECTION = "users"

# ------------------------------------------------------------------------ roles
# The complete list. Every account has exactly one, and the same strings are
# used in the database, the token, both backends and both frontends. GLP-1 keeps
# a copy in GLP1/Backend/core/security.py that must stay identical to this one.
ROLES = ("superadmin", "hospital_admin", "doctor", "nurse", "case_manager",
         "insurer", "pharmacy", "caretaker", "patient")
STATUSES = ("pending", "active")

# What a self-signup becomes when it names no role (an older portal, or a
# script). It sees nothing until an admin approves it either way.
DEFAULT_ROLE = "case_manager"

# The roles someone may ASK for when they sign up. Asking grants nothing: the
# account is pending, sees no patients, and has no hospital or insurer until an
# admin approves it - confirming the role or changing it as they do. What it
# buys is a sign-up list the admin can sort by who people say they are.
# superadmin is never on offer; that account comes from scripts/create_superadmin.py.
SIGNUP_ROLES = ("doctor", "nurse", "case_manager", "hospital_admin", "insurer", "pharmacy",
                "caretaker", "patient")

# Roles whose sign-up is active at once. A caretaker (a family or home carer)
# belongs to no organisation, so there is nobody to approve them - and an
# active caretaker still sees nothing until a patient, or hospital staff with
# the patient's agreement, links them (api/caretakers.py). The link is the
# approval.
ACTIVE_ON_SIGNUP = ("caretaker",)

# Roles that work for one hospital. superadmin belongs to none, because it
# oversees all of them; insurer belongs to an insurer organisation instead, and
# pharmacy (a pharmacy's staff) to a pharmacy organisation; a caretaker to no
# organisation - to the patients who linked them.
HOSPITAL_ROLES = ("hospital_admin", "doctor", "nurse", "case_manager", "patient")

# Enforced by the database itself once scripts/migrate_user_roles.py has run, so
# a bad write fails even if it comes from code that skipped this module.
USERS_VALIDATOR = {"$jsonSchema": {
    "bsonType": "object",
    "required": ["email", "password_hash", "role", "status"],
    "properties": {
        "email": {"bsonType": "string"},
        "password_hash": {"bsonType": "string"},
        "role": {"enum": list(ROLES)},
        "status": {"enum": list(STATUSES)},
        "hospital_id": {"bsonType": ["string", "null"]},
    },
}}

BCRYPT_ROUNDS = 12
MIN_PASSWORD_LENGTH = 8
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

# Comparing against a real hash when no account exists keeps the failed-login
# path the same cost as the successful one, so response time does not quietly
# reveal which email addresses are registered.
_DUMMY_HASH = bcrypt.hashpw(b"timing-equaliser", bcrypt.gensalt(rounds=BCRYPT_ROUNDS))


def users(db):
    """The shared identity collection. Note this is NOT the product database -
    accounts are deliberately held apart from either product's own data."""
    return db.client[IDENTITY_DB][USERS_COLLECTION]


def ensure_indexes(db) -> None:
    """Guarantee a unique index on email, the account's natural key.

    The constraint has to live in the database, not in a check-then-insert in
    the signup handler: two simultaneous signups would both find nothing and
    both insert. Signup relies on the resulting duplicate-key error.

    An equivalent index may already exist under a different name - the identity
    collection is shared and was created by another codebase - so look before
    creating, or the create fails with a name conflict and the failure says
    nothing about whether the constraint is actually in place.
    """
    collection = users(db)
    try:
        for index in collection.list_indexes():
            if list(index["key"].items()) == [("email", 1)] and index.get("unique"):
                return                                    # already enforced, under any name
        collection.create_index("email", unique=True, name="email_unique")
    except Exception as exc:                              # noqa: BLE001
        # Runs at import: a cluster blip here must not stop the service booting.
        # Surfaced rather than swallowed, because without this index two accounts
        # can share an email and one of them silently becomes unreachable.
        print(f"WARNING: could not verify the unique index on "
              f"{IDENTITY_DB}.{USERS_COLLECTION}.email ({type(exc).__name__}: {exc}). "
              f"Duplicate signups may not be refused.")


# --------------------------------------------------------------------- helpers
def slugify_org(name: str) -> str:
    """"City Hospital" -> "city-hospital". The id format for hospitals and
    insurers, readable in URLs and in the database."""
    slug = re.sub(r"[^a-z0-9]+", "-", (name or "").strip().lower()).strip("-")
    return slug or "unaffiliated"


def normalise_email(email: str) -> str:
    return (email or "").strip().lower()


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt(rounds=BCRYPT_ROUNDS)).decode()


def password_matches(password: str, stored_hash: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode("utf-8"), (stored_hash or "").encode("utf-8"))
    except (ValueError, TypeError):
        # A malformed or truncated hash in the database is not a reason to let
        # someone in.
        return False


def _require_secret() -> None:
    if not SHARED_SECRET_KEY:
        raise HTTPException(
            status_code=500,
            detail="SHARED_SECRET_KEY is not configured; this service cannot issue tokens")


def effective(account: dict) -> dict:
    """The account's role, status and hospital as the rest of the system must
    treat them.

    An account without a `status` field predates fixed roles. Its `role` is
    whatever its owner typed at signup, and the old signup accepted any string -
    so a stored "superadmin" proves nothing. Such accounts are read as an active
    case_manager with no hospital, which is also what the backfill script writes.
    """
    if account.get("status") not in STATUSES or account.get("role") not in ROLES:
        role, status, hospital_id = DEFAULT_ROLE, "active", None
    else:
        role, status, hospital_id = account["role"], account["status"], account.get("hospital_id")
    return {**account, "role": role, "status": status, "hospital_id": hospital_id,
            # Older accounts predate this field; treat them as having the default
            # rather than locking their owners out of both products.
            "app_access": account.get("app_access") or list(DEFAULT_APP_ACCESS)}


# ----------------------------------------------------------------- token issue
def issue_token(user: dict, exp: Optional[int] = None, sid: Optional[str] = None) -> dict:
    """Sign a token for an account and return it with the claims it carries.

    `exp` and `sid` keep an existing expiry and sign-in id when re-issuing, so
    refreshing the claims never stretches a session past its original length
    and never starts a new one."""
    _require_secret()
    user = effective(user)
    expires_at = exp if exp is not None else int(time.time()) + TOKEN_TTL_SECONDS
    claims = {
        "sub": str(user["_id"]),
        "email": user["email"],
        "role": user["role"],
        "status": user["status"],
        "hospital_id": user["hospital_id"],
        "must_change_password": bool(user.get("must_change_password")),
        "app_access": user["app_access"],
        "sid": sid or secrets.token_hex(16),
        "exp": expires_at,
    }
    return {"token": jwt.encode(claims, SHARED_SECRET_KEY, algorithm=ALGORITHM),
            "token_type": "bearer",
            "expires_in": max(0, expires_at - int(time.time())),
            "user": public_view(user)}


def decode_token(token: str) -> dict:
    """Verify a token and return its claims. Used by /auth/me; each product does
    the same thing independently with its own copy of this logic."""
    _require_secret()
    try:
        return jwt.decode(token, SHARED_SECRET_KEY, algorithms=[ALGORITHM],
                          options={"require": ["exp"]})
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token has expired; sign in again")
    except jwt.InvalidTokenError as exc:
        raise HTTPException(status_code=401, detail="Invalid token") from exc


def _validate_credentials(email: str, password: str) -> str:
    email = normalise_email(email)
    if not _EMAIL_RE.match(email):
        raise HTTPException(status_code=422, detail="A valid email address is required")
    if len(password or "") < MIN_PASSWORD_LENGTH:
        raise HTTPException(status_code=422,
                            detail=f"Password must be at least {MIN_PASSWORD_LENGTH} characters")
    return email


def _now() -> str:
    return datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")


# ----------------------------------------------------------------------- signup
def requested_role(role: Optional[str]) -> str:
    """The role a sign-up asks for, from SIGNUP_ROLES; case_manager if none."""
    if role is None or not str(role).strip():
        return DEFAULT_ROLE
    key = str(role).strip().lower().replace(" ", "_").replace("-", "_")
    if key == "superadmin":
        raise HTTPException(status_code=422,
                            detail="Superadmin accounts are created by our team, not by signing up")
    if key not in SIGNUP_ROLES:
        raise HTTPException(status_code=422,
                            detail=f"Choose one of: {', '.join(SIGNUP_ROLES)}")
    return key


def requested_hospital(db, role: str, hospital_id: Optional[str]) -> Optional[str]:
    """The hospital a sign-up asks to join, checked against the real list.

    Only hospital roles ask for one; an insurer belongs to an insurer, which our
    team assigns. Blank or "none" ("my hospital isn't listed") asks for none, and
    the request then waits for the superadmin."""
    hospital_id = (hospital_id or "").strip()
    if role not in HOSPITAL_ROLES or not hospital_id or hospital_id == "none":
        return None
    if not db.client[IDENTITY_DB]["hospitals"].find_one({"_id": hospital_id}, {"_id": 1}):
        raise HTTPException(status_code=422, detail="No such hospital - choose one from the list")
    return hospital_id


def signup(db, email: str, password: str, app_access: Optional[list] = None,
           role: Optional[str] = None, hospital_id: Optional[str] = None) -> dict:
    """Create a self-service account and return a signed token for it.

    The person says which role they are joining as (SIGNUP_ROLES) and, for a
    hospital role, which hospital. Both are a request, filed for that
    hospital's admin to approve or decline (the superadmin sees every request).
    Until then the account is pending, `hospital_id` stays empty and every data
    request is refused: choosing a hospital is never enough to see its
    patients. The request is kept in `requested_role` / `requested_hospital_id`.

    Someone whose request was declined may sign up again with the same email:
    the new sign-up replaces the declined one (new password, role and
    hospital) and goes back into the queue. Past declines are kept on the
    account. Any other existing account with that email is refused, as before.
    """
    email = _validate_credentials(email, password)
    role = requested_role(role)
    wanted_hospital = requested_hospital(db, role, hospital_id)
    now = _now()
    doc = {"email": email,
           "password_hash": hash_password(password),
           "role": role,
           # Kept after approval too, so an admin can see what was asked for
           # even once the role has been changed.
           "requested_role": role,
           "requested_hospital_id": wanted_hospital,
           "status": "active" if role in ACTIVE_ON_SIGNUP else "pending",
           "hospital_id": None,
           "app_access": list(app_access) if app_access else list(DEFAULT_APP_ACCESS),
           "created_at": now,
           "updated_at": now}
    existing = users(db).find_one({"email": email})
    if existing and _was_declined(existing):
        fields = {k: v for k, v in doc.items() if k not in ("email", "created_at")}
        users(db).update_one({"_id": existing["_id"], "declined": existing["declined"]},
                             {"$set": {**fields, "declined": None},
                              "$push": {"previous_declines": existing["declined"]}})
        return issue_token({**existing, **fields, "declined": None})
    if existing:
        raise HTTPException(status_code=409, detail="An account with that email already exists")
    try:
        doc["_id"] = users(db).insert_one(doc).inserted_id
    except Exception as exc:
        if "duplicate key" in str(exc).lower() or "E11000" in str(exc):
            raise HTTPException(status_code=409,
                                detail="An account with that email already exists") from exc
        raise
    return issue_token(doc)


def _was_declined(account: dict) -> bool:
    """A sign-up an admin turned down, still attached to nothing - the only
    kind of existing account a new sign-up may replace. It never had access,
    so replacing it gives nobody anything they did not have."""
    return (bool(account.get("declined")) and account.get("status") == "pending"
            and not account.get("hospital_id") and not account.get("insurer_id")
            and not account.get("pharmacy_id")
            and not account.get("requested_hospital_id"))


# ------------------------------------------------------------------------ login
def login(db, email: str, password: str) -> dict:
    """Check a password and return a signed token.

    The same message and the same amount of work for an unknown address as for a
    wrong password: distinguishing them tells an attacker which accounts exist.
    """
    account = users(db).find_one({"email": normalise_email(email)})
    stored = account.get("password_hash") if account else _DUMMY_HASH.decode()
    if not password_matches(password or "", stored) or account is None:
        raise HTTPException(status_code=401, detail="Incorrect email or password")
    return issue_token(account)


def get_account(db, sub: str) -> dict:
    """Look up the account a token refers to, so /auth/me reflects the database
    rather than whatever the token said when it was signed."""
    try:
        account = users(db).find_one({"_id": ObjectId(sub)})
    except (InvalidId, TypeError):
        raise HTTPException(status_code=401, detail="Invalid token subject")
    if not account:
        raise HTTPException(status_code=401, detail="Account no longer exists")
    return account


# ------------------------------------------------------------ who is this user
def bearer_token(authorization: Optional[str]) -> str:
    scheme, _, token = (authorization or "").partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        raise HTTPException(status_code=401,
                            detail="Missing Authorization: Bearer <token> header")
    return token.strip()


def authenticate(db, token: str, allow_pending: bool = False,
                 app: Optional[str] = None, allow_password_change: bool = False) -> dict:
    """The account behind a token, read from the database on every call.

    The token only proves who someone is. What they may do comes from the
    current record, which is why an approval, a role change or a deleted account
    takes effect on the very next request.

    An account still on a temporary password is refused everything except the
    calls needed to replace it: a password an admin has seen, and may have sent
    in a message, should not unlock patient data.
    """
    account = effective(get_account(db, decode_token(token).get("sub")))
    if account["status"] != "active" and not allow_pending:
        raise HTTPException(status_code=403,
                            detail="This account is waiting for approval by an administrator")
    if account.get("must_change_password") and not allow_password_change:
        raise HTTPException(status_code=403,
                            detail="Set a new password before continuing")
    if app and app not in account["app_access"]:
        raise HTTPException(status_code=403, detail=f"This account does not have access to {app}")
    return account


def user_dependency(get_db, app: str, protected_prefix: str = "/api/"):
    """A FastAPI dependency that admits only active users to `protected_prefix`.

    Built as a factory so the service passes its own database handle, and a test
    can build the same guard around a mongomock one. The account is left on
    `request.state.user` for the handlers that later filter by hospital and role.
    """
    def require_user(request: Request,
                     authorization: Optional[str] = Header(default=None)):
        if not request.url.path.startswith(protected_prefix):
            return None
        account = authenticate(get_db(), bearer_token(authorization), app=app)
        request.state.user = account
        return account
    return require_user


def refresh(db, token: str) -> dict:
    """Re-issue a token carrying the account's current claims.

    The portal calls this before deciding what to show, so a user approved since
    they last signed in sees their apps without signing in again. The expiry is
    kept, so this cannot be used to stay signed in indefinitely."""
    claims = decode_token(token)
    account = authenticate(db, token, allow_pending=True, allow_password_change=True)
    return issue_token(account, exp=claims["exp"], sid=claims.get("sid"))


def public_view(account: dict) -> dict:
    """An account as the portal may see it. Never includes the password hash."""
    account = effective(account)
    return {"sub": str(account["_id"]), "email": account.get("email", ""),
            "role": account["role"], "status": account["status"],
            "hospital_id": account["hospital_id"],
            "must_change_password": bool(account.get("must_change_password")),
            "app_access": account["app_access"],
            # So the portal's waiting screen can say which hospital was asked.
            "requested_hospital_id": account.get("requested_hospital_id")}


# --------------------------------------------------------- operator-only paths
# Neither of these is reachable over HTTP. They are what the scripts in
# scripts/ call, run by someone with direct access to the cluster.
def bootstrap_superadmin(db, email: str, password: Optional[str] = None) -> dict:
    """Create a superadmin, or promote an existing account to one.

    The only way a superadmin comes into existence: no endpoint can grant the
    role, so no hospital admin can create one or promote anyone to it. A password
    is required for a new account and optional when promoting an existing one.
    """
    email = normalise_email(email)
    existing = users(db).find_one({"email": email})
    now = _now()
    promote = {"role": "superadmin", "status": "active", "hospital_id": None,
               "app_access": list(DEFAULT_APP_ACCESS), "updated_at": now}
    if existing:
        if password:
            _validate_credentials(email, password)
            promote["password_hash"] = hash_password(password)
        users(db).update_one({"_id": existing["_id"]}, {"$set": promote})
        return {**existing, **promote}
    _validate_credentials(email, password)
    doc = {"email": email, "password_hash": hash_password(password),
           **promote, "created_at": now}
    doc["_id"] = users(db).insert_one(doc).inserted_id
    return doc


def backfill_users(db) -> int:
    """Rewrite accounts that predate fixed roles as an active case_manager with
    no hospital - the same reading `effective` already gives them - so the
    stricter database validator can be switched on. Returns how many changed."""
    legacy = {"$or": [{"status": {"$nin": list(STATUSES)}},
                      {"role": {"$nin": list(ROLES)}}]}
    result = users(db).update_many(legacy, {"$set": {
        "role": DEFAULT_ROLE, "status": "active", "hospital_id": None,
        "updated_at": _now()}})
    return result.modified_count


def auth_config() -> dict:
    """What this service is configured to do. Reports whether a secret is set,
    never the secret itself, so a broken deploy can be diagnosed over HTTP."""
    return {"secret_configured": bool(SHARED_SECRET_KEY), "algorithm": ALGORITHM,
            "identity_database": IDENTITY_DB, "token_ttl_seconds": TOKEN_TTL_SECONDS,
            "default_app_access": list(DEFAULT_APP_ACCESS), "roles": list(ROLES),
            "signup_roles": list(SIGNUP_ROLES)}
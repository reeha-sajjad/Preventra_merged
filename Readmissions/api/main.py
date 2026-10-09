import os
import json
import uuid
import hashlib
import re
import time
import shutil
import threading
from datetime import datetime, timedelta
from typing import List, Optional

import certifi
from dotenv import load_dotenv
from bson import ObjectId
from bson.errors import InvalidId
from fastapi import (Body, Depends, FastAPI, File, Form, Header, HTTPException, Query,
                     Request, UploadFile)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from pymongo import MongoClient, ASCENDING, DESCENDING
from pymongo.errors import AutoReconnect, ServerSelectionTimeoutError

from api import doctor_service
from api.db_utils import get_db_name, get_latest_batch_date, get_mongo_client
from api import auth as shared_auth
from api import user_admin
from api import access
from api import access_log
from api import hospital
from api import risk_watch
from api.chatbot_service import answer_turn
from api.chatbot_queries import _patient_id_filter
from api.gemini_insights import generate_roi_and_counterfactual, generate_week_narrative
from models import early_warning
from models.monitoring_rules import (RED_FLAGS, CARRIED_FORWARD_SOURCE, CARRY, DISEASE_NEUTRAL,
                                     LABEL_TO_SIGNAL, MODEL_SOURCE, SIGNAL_RULES,
                                     score_week, signal_plan, signals_for,
                                     source_for, variant_for, week_sources)
from models.discharge_baseline import (derive_baseline, describe as describe_baseline,
                                       relative_observations)
from models.icd_groups import (GROUP_LABELS, classify as classify_group,
                                classify_all as classify_group_all,
                                _match_code, _match_title)

load_dotenv()

# ---------------------------------------------------------------------------
# API key authentication
# ---------------------------------------------------------------------------
# If API_KEY is unset, auth is skipped (local/dev convenience). Set it in .env
# to require every request to send a matching X-API-Key header.
API_KEY = os.environ.get("API_KEY")

# ---------------------------------------------------------------------------
# Manual entry
# ---------------------------------------------------------------------------
# Typing a new patient into the worklist by hand is off by default. The three
# create endpoints below refuse while it is off, and the frontend hides the
# page to match (VITE_MANUAL_ENTRY_ENABLED).
#
# Refusing server-side rather than only hiding the nav link is the point: a
# hidden link is a suggestion, and these endpoints write to patient_worklist
# and move the executive_summary counts. Editing an EXISTING patient is a
# separate feature and is unaffected.
#
# Turning it back on is one variable on each side, no code change.
MANUAL_ENTRY_ENABLED = os.environ.get(
    "MANUAL_ENTRY_ENABLED", "false").strip().lower() in ("1", "true", "yes", "on")


def require_manual_entry():
    if not MANUAL_ENTRY_ENABLED:
        raise HTTPException(
            status_code=403,
            detail="Manual patient entry is turned off on this deployment.")


# /healthz is probed by the hosting platform to decide whether the container is
# alive; it must answer before the key is checked, or a correct deployment
# looks dead to its own health check.
# /auth/* is the shared login and User Management. It is called by the portal
# before anyone has a token, and by GLP-1, which does not hold our service key.
# Putting it behind the key would mean shipping the key to every browser, which
# defeats the point of having one. Everything under /auth/ that needs a user
# checks the bearer token itself.
def _skips_api_key(path: str) -> bool:
    return path == "/healthz" or path.startswith("/auth/")


def require_api_key(request: Request,
                    x_api_key: Optional[str] = Header(default=None, alias="X-API-Key")):
    if _skips_api_key(request.url.path):
        return
    if API_KEY and x_api_key != API_KEY:
        raise HTTPException(status_code=401, detail="Invalid or missing API key")


# Every /api/* request must also come from an active, signed-in user. The API
# key above only says "this is our frontend"; it ships in a public bundle, so it
# cannot say who is asking. The account is re-read from the database on every
# request, which is what makes an approval or a removal take effect immediately.
# `db` is defined further down; the lambda looks it up when a request arrives.
_require_account = shared_auth.user_dependency(lambda: db, app="readmissions")


def require_user(request: Request, account=Depends(_require_account),
                 authorization: Optional[str] = Header(default=None),
                 x_hospital_id: Optional[str] = Header(default=None, alias="X-Hospital-Id")):
    """The account, plus two things every handler below may need: which
    hospital the superadmin picked (the hospital picker sends it as a header;
    nobody else can use it) and which sign-in this is, for the access log."""
    if account is not None:
        request.state.user = access.acting_as(account, x_hospital_id)
        request.state.session = access_log.session_of(shared_auth.bearer_token(authorization))
    return account

app = FastAPI(title="Neuroshield API",
              dependencies=[Depends(require_api_key), Depends(require_user)])


@app.get("/healthz")
def healthz():
    """
    Liveness only - deliberately does not touch MongoDB.

    A health check that queried Atlas would report the container as dead during
    a database blip and have the platform restart a process that is working
    perfectly, which makes the outage longer rather than shorter. Database
    reachability surfaces as a 503 on the endpoints that need it.
    """
    return {"status": "ok"}

# The API key above is a service-level gate - "this caller is our own frontend",
# not "this caller is a particular person". Per-user identity is the shared login
# below, which issues a token both products verify with the same secret.


# ---------------------------------------------------------------------------
# Shared login
# ---------------------------------------------------------------------------
# Accounts live in the `shared_identity` database on the shared cluster, apart
# from either product's own data. This service is the only thing that issues
# tokens; each product verifies them independently. See api/auth.py.

# Role and hospital are deliberately absent: a self-signup is always a pending
# case_manager with no hospital, and an admin assigns both. Older portals still
# send `role` and `org_name`; pydantic drops unknown fields, so they are ignored.
class SignupRequest(BaseModel):
    email: str
    password: str
    # The role, and for a hospital role the hospital, they are asking for - a
    # request that hospital's admin approves or declines. See auth.signup.
    role: Optional[str] = None
    hospital_id: Optional[str] = None


class LoginRequest(BaseModel):
    email: str
    password: str


@app.post("/auth/signup")
def auth_signup(body: SignupRequest):
    """Create a pending account, filed under the role and hospital it asks for,
    and return a token for it. The portal uses the token to show the "waiting
    for approval" screen; every data request made with it is refused until an
    admin approves the account."""
    return shared_auth.signup(db, body.email, body.password, role=body.role,
                              hospital_id=body.hospital_id)


@app.get("/auth/hospitals")
def auth_hospitals():
    """The hospitals a sign-up may ask to join - names and ids only. Open, like
    sign-up itself: the portal's form needs it before anyone has an account."""
    return user_admin.list_public_hospitals(db)


@app.post("/auth/login")
def auth_login(body: LoginRequest):
    return shared_auth.login(db, body.email, body.password)


@app.get("/auth/me")
def auth_me(authorization: Optional[str] = Header(default=None)):
    """Who the bearer of this token is, read back from the database rather than
    from the token, so a role or access change takes effect without re-issuing.
    Answers for pending accounts too, so they can be told they are pending."""
    account = shared_auth.authenticate(db, shared_auth.bearer_token(authorization),
                                       allow_pending=True, allow_password_change=True)
    return shared_auth.public_view(account)


@app.post("/auth/refresh")
def auth_refresh(authorization: Optional[str] = Header(default=None)):
    """A new token carrying the account's current role and status, with the
    same expiry as the old one. See api/auth.py:refresh."""
    return shared_auth.refresh(db, shared_auth.bearer_token(authorization))


class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str


@app.post("/auth/change-password")
def auth_change_password(body: ChangePasswordRequest,
                         authorization: Optional[str] = Header(default=None)):
    """Replace your own password. The only data-free call an account on a
    temporary password may make besides /auth/me and /auth/refresh."""
    return user_admin.change_password(db, shared_auth.bearer_token(authorization),
                                      body.current_password, body.new_password)


# ---------------------------------------------------------------------------
# User Management - see api/user_admin.py for who may do what
# ---------------------------------------------------------------------------
def require_manager(authorization: Optional[str] = Header(default=None)) -> dict:
    account = shared_auth.authenticate(db, shared_auth.bearer_token(authorization))
    user_admin.require_manager(account)
    return account


class OrgRequest(BaseModel):
    name: str


class CreateUserRequest(BaseModel):
    email: str
    role: str
    name: str = ""
    hospital_id: Optional[str] = None
    insurer_id: Optional[str] = None


class UpdateUserRequest(BaseModel):
    role: Optional[str] = None
    status: Optional[str] = None
    hospital_id: Optional[str] = None
    insurer_id: Optional[str] = None
    name: Optional[str] = None


class ImportUsersRequest(BaseModel):
    csv: str
    hospital_id: Optional[str] = None
    insurer_id: Optional[str] = None


@app.get("/auth/admin/hospitals")
def admin_list_hospitals(actor: dict = Depends(require_manager)):
    return user_admin.list_hospitals(db, actor)


@app.post("/auth/admin/hospitals", status_code=201)
def admin_create_hospital(body: OrgRequest, actor: dict = Depends(require_manager)):
    return user_admin.create_hospital(db, actor, body.name)


@app.get("/auth/admin/insurers")
def admin_list_insurers(actor: dict = Depends(require_manager)):
    return user_admin.list_insurers(db, actor)


@app.post("/auth/admin/insurers", status_code=201)
def admin_create_insurer(body: OrgRequest, actor: dict = Depends(require_manager)):
    return user_admin.create_insurer(db, actor, body.name)


@app.get("/auth/admin/users")
def admin_list_users(hospital_id: Optional[str] = None, status: Optional[str] = None,
                     role: Optional[str] = None, actor: dict = Depends(require_manager)):
    return user_admin.list_users(db, actor, hospital_id, status, role)


@app.post("/auth/admin/users", status_code=201)
def admin_create_user(body: CreateUserRequest, actor: dict = Depends(require_manager)):
    return user_admin.create_user(db, actor, body.email, body.role, body.name,
                                  body.hospital_id, body.insurer_id)


@app.patch("/auth/admin/users/{user_id}")
def admin_update_user(user_id: str, body: UpdateUserRequest,
                      actor: dict = Depends(require_manager)):
    return user_admin.update_user(db, actor, user_id, body.role, body.status,
                                  body.hospital_id, body.insurer_id, body.name)


@app.post("/auth/admin/users/{user_id}/decline")
def admin_decline_signup(user_id: str, actor: dict = Depends(require_manager)):
    """Turn down a sign-up's request to join the admin's hospital."""
    return user_admin.decline_signup(db, actor, user_id)


@app.post("/auth/admin/users/import")
def admin_import_users(body: ImportUsersRequest, actor: dict = Depends(require_manager)):
    return user_admin.import_users(db, actor, body.csv, body.hospital_id, body.insurer_id)


@app.get("/auth/admin/access-log")
def admin_access_log(hospital_id: Optional[str] = None,
                     app_name: Optional[str] = Query(None, alias="app"),
                     limit: int = Query(200, ge=1, le=access_log.MAX_ENTRIES),
                     actor: dict = Depends(require_manager)):
    """Who opened which patient's clinical details, in both apps. A hospital
    admin sees its own hospital's patients only (see api/access_log.py)."""
    return access_log.entries(db, actor, hospital_id=hospital_id, app=app_name, limit=limit)


@app.get("/auth/config")
def auth_configuration():
    """Configuration state, carrying no secret. Lets a misconfigured deploy be
    diagnosed over HTTP instead of by guessing."""
    return shared_auth.auth_config()


# ---------------------------------------------------------------------------
# Database availability
# ---------------------------------------------------------------------------
# Atlas rejects the TLS handshake outright when the caller's IP is not on the
# cluster's access list, and pymongo surfaces that as AutoReconnect rather than
# an auth error. Unhandled it becomes a bare 500, which the dashboard renders as
# "Failed to fetch" - a message that sends someone hunting through frontend code
# for a fault that is entirely in network configuration.
#
# Answering 503 with the actual reason is the difference between a five-minute
# fix and an afternoon.
@app.exception_handler(AutoReconnect)
@app.exception_handler(ServerSelectionTimeoutError)
async def database_unreachable(request, exc):
    print(f"[db] unreachable on {request.url.path}: {exc}")
    return JSONResponse(
        status_code=503,
        content={"detail": "Cannot reach the patient database. If this persists, check that "
                           "this machine's IP address is on the MongoDB Atlas access list."},
    )


# Which sites may call this API from a browser. Unset means any of them, which
# is right for local development and wrong once deployed: the API key is
# compiled into the public frontend bundle, so a wildcard lets any page on the
# internet read patient data using a visitor's browser. Set ALLOWED_ORIGINS to
# the deployed frontend origin in production.
ALLOWED_ORIGINS = [o.strip() for o in
                   os.environ.get("ALLOWED_ORIGINS", "*").split(",") if o.strip()]

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    # Only meaningful with a pinned origin list; browsers reject credentialed
    # requests against a wildcard.
    allow_credentials=ALLOWED_ORIGINS != ["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
INPUT_DIR = os.path.join(os.path.dirname(__file__), "..", "data", "input")

# ---------------------------------------------------------------------------
# MongoDB connection
# ---------------------------------------------------------------------------
MONGO_URI = os.environ.get("MONGO_URI", "mongodb://localhost:27017")
_mongo_client = get_mongo_client(MONGO_URI)
db = _mongo_client[get_db_name()]

# Email is the account key for the shared login; the unique index that enforces
# that lives on a different database on the same cluster, so it is created here
# rather than wherever the product collections are set up.
shared_auth.ensure_indexes(db)
access_log.ensure_indexes(db)


# ---------------------------------------------------------------------------
# Whose patients this request may touch - see api/access.py
# ---------------------------------------------------------------------------
# require_user has already put the signed-in account on request.state.user for
# every /api route. The scope is worked out once per request and reused.
def _scope(request: Request):
    if not hasattr(request.state, "scope"):
        request.state.scope = access.patient_scope(db, request.state.user)
    return request.state.scope


def _require_patient(request: Request, patient_id) -> None:
    """404 unless this patient is one the caller may see."""
    access.require_patient(_scope(request), patient_id)


def _require(request: Request, action: str) -> None:
    """403 unless the caller's role may take this kind of action."""
    access.require(request.state.user, action)


def _opened(request: Request) -> set:
    """Patients whose clinical layer a reason role opened this sign-in."""
    if not hasattr(request.state, "opened"):
        user = request.state.user
        request.state.opened = (access_log.opened(db, user, access_log.APP, request.state.session)
                                if access.needs_reason(user) else set())
    return request.state.opened


def _view(request: Request) -> access.ScopedDB:
    """The database as this caller may read it."""
    return access.ScopedDB(db, request.state.user, _scope(request), opened=_opened(request))


def _patient_hospital(patient_id) -> Optional[str]:
    doc = db["care_actions"].find_one(_patient_id_filter(str(patient_id)), {"hospital_id": 1})
    return (doc or {}).get("hospital_id")


def _detail_access(request: Request, patient_id) -> str:
    """open | granted | reason_required - for a patient already in scope."""
    user = request.state.user
    if not access.needs_reason(user):
        return "open"
    return "granted" if str(patient_id) in _opened(request) else "reason_required"


def _require_detail(request: Request, patient_id) -> None:
    """The clinical layer of one patient (see api/access.py).

    404 outside the caller's patients, as everywhere. A hospital admin or an
    insurer then needs a reason given this sign-in, or gets the structured 403
    the frontends turn into the prompt. The superadmin is never asked, but its
    first look at each patient in a sign-in is logged all the same.
    """
    _require_patient(request, patient_id)
    user = request.state.user
    if user["role"] == "superadmin":
        session = request.state.session
        if not access_log.has_opened(db, user, access_log.APP, patient_id, session):
            access_log.record(db, user, access_log.APP, patient_id, _patient_hospital(patient_id),
                              access.SUPERADMIN_REASON, session)
        return
    if _detail_access(request, patient_id) == "reason_required":
        raise HTTPException(status_code=403, detail=access.REASON_REQUIRED)


# ---------------------------------------------------------------------------
# Drivers format mapping
# ---------------------------------------------------------------------------

DISCHARGE_MAPPING = {
    "1": "Home",
    "2": "Another Short Term Hospital",
    "3": "Skilled Nursing Facility",
    "4": "Intermediate Care Facility",
    "5": "Another Type of Inpatient Care Institution",
    "6": "Home with Home Health Service",
    "7": "Left Against Medical Advice",
    "8": "Home under care of Home IV provider",
    "9": "Admitted as an inpatient to this hospital",
    "10": "Neonate discharged to another hospital for neonatal intensive care",
    "11": "Expired",
    "12": "Still patient or expected to return for outpatient services",
    "13": "Hospice / home",
    "14": "Hospice / medical facility",
    "15": "Ward, unit, etc. within this hospital",
    "16": "Discharged/transferred/referred another institution for outpatient services",
    "17": "Discharged/transferred/referred to this institution for outpatient services",
    "18": "NULL",
    "19": "Expired at home",
    "20": "Expired in a medical facility",
    "21": "Expired, place unknown",
    "22": "Discharged/transferred to another rehab fac including rehab units of a hospital",
    "23": "Discharged/transferred to a long term care hospital",
    "24": "Discharged/transferred to a nursing facility certified under Medicaid but not Medicare",
    "25": "Not Mapped",
    "26": "Unknown/Invalid",
    "30": "Discharged/transferred to another Type of Health Care Institution not Defined Elsewhere",
    "27": "Discharged/transferred to a federal health care facility",
    "28": "Discharged/transferred to a psychiatric hospital",
}


def format_driver_string(raw: str) -> str:
    raw = raw.strip()
    if not raw:
        return raw

    if "Discharge disposition ID: " in raw:
        parts = raw.split(" (", 1)
        label_val = parts[0]
        rest = " (" + parts[1] if len(parts) > 1 else ""

        label_parts = label_val.split(": ")
        if len(label_parts) == 2:
            val_id = label_parts[1].strip()
            mapped_val = DISCHARGE_MAPPING.get(val_id, f"ID {val_id}")
            return f"Discharge Destination: {mapped_val}{rest}"

    return raw


def _worklist_driver(patient_id: str, row: dict) -> str:
    """
    Pick one of the patient's top-3 drivers for the worklist column.

    Always showing driver_1 made the column near-useless at a glance: rank 1 is
    dominated by a handful of features - "Days since previous discharge" alone
    holds 21.3% of the cohort and the top 5 labels cover 55% - so scanning the
    column told you almost nothing about how patients differ. Rotating across
    the top 3 surfaces materially more of what the model actually found.

    The choice is derived from a hash of patient_id, NOT drawn at call time. A
    fresh random pick per request would reshuffle the column on every sort,
    filter, page change and refresh, which reads as a bug. Hashing makes it
    stable for a given patient forever while still varying across rows.

    All three drivers remain in the response as driver_1/2/3, in rank order, so
    nothing is hidden - this only chooses which one the summary column shows.
    """
    drivers = []
    for i in (1, 2, 3):
        d = format_driver_string(str(row.get(f"driver_{i}", "")).strip())
        # The SHAP walker emits this filler when a patient has fewer than three
        # risk-increasing features; it is not a driver and must not be shown.
        if d and not d.startswith("No further risk-increasing factor"):
            drivers.append(d)
    if not drivers:
        return ""
    idx = int(hashlib.md5(patient_id.encode("utf-8")).hexdigest(), 16) % len(drivers)
    return drivers[idx]


def _extract_drivers_from_row(row: dict) -> list:
    """Parses driver_1/driver_2/driver_3 strings off a patient_worklist or
    risk_registry row into structured {category, label, value, explanation} dicts."""
    drivers = []
    for i in range(1, 4):
        raw = format_driver_string(str(row.get(f"driver_{i}", "")).strip())
        if not raw:
            continue
        # Split on the FIRST ": " for the label, then the LAST " (" for the
        # explanation. MIMIC driver labels contain brackets (e.g. "Sodium,
        # last value before discharge"), which the previous first-bracket
        # split truncated.
        label, sep, rest = raw.partition(": ")
        if not sep:
            label, value, explanation = raw, "", ""
        elif rest.endswith(")") and " (" in rest:
            value, _, explanation = rest.rpartition(" (")
            explanation = explanation.rstrip(")")
        else:
            value, explanation = rest, ""
        drivers.append({
            "category": "clinical",
            "label": label.strip(),
            "value": value.strip(),
            "explanation": explanation.strip(),
        })
    return drivers


# ---------------------------------------------------------------------------
# Patients
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Short-lived caches for values that change only when a batch is loaded
# ---------------------------------------------------------------------------
# Every Atlas round trip from this machine costs roughly 0.7 seconds, and a
# worklist page was making three: resolve the batch date, count the matches,
# fetch the rows. The first two answer questions whose answers change only when
# the loader or the simulator runs, so they are cached briefly rather than asked
# on every keystroke.
#
# The trade is that a freshly loaded batch can take up to CACHE_TTL_SECONDS to
# appear. That is acceptable for a dashboard over batch-computed data, and it is
# stated here rather than being a surprise.
CACHE_TTL_SECONDS = 60
_cache: dict = {}


def _cached(key, produce):
    hit = _cache.get(key)
    now = time.time()
    if hit and now - hit[0] < CACHE_TTL_SECONDS:
        return hit[1]
    value = produce()
    _cache[key] = (now, value)
    return value


def _latest_batch(collection: str = "patient_worklist"):
    return _cached(f"batch:{collection}", lambda: get_latest_batch_date(db, collection))


# Only the fields the worklist renders. Pulling whole documents - including
# raw_inputs and the secondary diagnosis list - was a large part of why this
# endpoint was slow, and none of it reaches the table.
_WORKLIST_PROJECTION = {
    "_id": 0, "patient_id": 1, "risk_score": 1, "risk_band": 1,
    "current_score": 1, "current_band": 1, "discharge_score": 1,
    "trend_delta": 1, "monitoring_status": 1, "weeks_tracked": 1,
    "discharge_date": 1, "primary_diagnosis": 1, "primary_icd_code": 1,
    "clinical_group": 1, "group_label": 1, "group_evidence": 1,
    "group_confidence": 1, "primary_driver_label": 1,
    # The secondary diagnoses were dropped from this projection when the
    # endpoint returned every row at once. At a page of 50 they cost a few KB
    # and they are the whole point of the diagnosis column: a patient averages
    # 11.8 coded diagnoses and the extract keeps the first four.
    "secondary_diagnoses": 1, "clinical_groups": 1, "n_diagnoses_coded": 1,
    # driver_1 only: the column shows one deterministic pick, precomputed as
    # primary_driver_label by the refresh script. driver_1 stays as the fallback
    # for rows that script has not reached; drivers 2 and 3 are not rendered in
    # the table and were pure payload.
    "driver_1": 1,
}

# Column -> the stored field it sorts on. Sorting in MongoDB against an index is
# what keeps latency flat as the cohort grows; sorting in the browser needs the
# whole cohort in memory first, which is what made this slow.
_WORKLIST_SORTS = {
    "score": "current_score",
    "baseline": "discharge_score",
    "delta": "trend_delta",
    "trend": "status_rank",
    "band": "band_rank",
    "id": "patient_id",
    "diagnosis": "primary_diagnosis",
    "driver": "primary_driver_label",
    "date": "discharge_date",
}


def _diagnosis_list(row: dict) -> list:
    """
    The diagnoses recorded for this admission, principal first, each tagged
    with the monitoring group it puts the patient in.

    Only four are available: MIMIC codes a mean of 11.8 diagnoses per stay, but
    the Phase-1 extract keeps the principal plus three secondaries
    (N_SECONDARY in models/mimic_diagnoses.py), and only the principal keeps
    its ICD code. Sending fewer than we have would be a worse answer than
    saying how many are missing, so n_diagnoses_coded travels with the list.
    """
    out = []
    code = (row.get("primary_icd_code") or "").strip()
    title = row.get("primary_diagnosis") or ""
    if title or code:
        g = _match_code(code) or _match_title(title)
        out.append({"position": "principal", "code": code, "title": title,
                    "group": g or "", "group_label": GROUP_LABELS.get(g, "") if g else ""})
    for t in (row.get("secondary_diagnoses") or []):
        if not t:
            continue
        g = _match_title(t)
        # Secondaries arrive as prose with no code, so this is keyword matching
        # and it is shown as such rather than dressed up as a coded match.
        out.append({"position": "secondary", "code": "", "title": t,
                    "group": g or "", "group_label": GROUP_LABELS.get(g, "") if g else ""})
    return out


@app.get("/api/patients")
def get_patients(request: Request,
                 page: int = Query(1, ge=1),
                 limit: int = Query(200, ge=1, le=5000),
                 group: Optional[str] = Query(None, description="clinical group key(s), comma separated"),
                 match: str = Query("any", description="any | all - how to combine several groups"),
                 band: Optional[str] = Query(None, description="High | Medium | Low"),
                 status: Optional[str] = Query(None, description="monitoring status"),
                 q: Optional[str] = Query(None, description="diagnosis or ICD code search"),
                 sort: str = Query("score-desc", description="<column>-asc|desc"),
                 doctor: Optional[str] = Query(None, description="registry doctor_id"),
                 nurse: Optional[str] = Query(None, description="nurse account id"),
                 unassigned: Optional[str] = Query(None, description="doctor | nurse")):
    """
    The patient worklist.

    Filtering, sorting and paging all happen in MongoDB against indexed fields.
    The previous version read every worklist row, aggregated the whole weekly
    monitoring collection and the risk registry, normalised the lot in Python
    and only then sliced out the requested page - 27 seconds against Atlas for
    2,000 patients, and it grew with the cohort rather than the page size.

    The trend summary is now denormalised onto the row by
    scripts/refresh_worklist_summary.py, which is correct because it changes
    only when the loader or the simulator runs. Rows that script has not reached
    fall back to the discharge score below rather than disappearing.
    """
    batch_date = _latest_batch()
    if not batch_date:
        raise HTTPException(status_code=404, detail="Patient worklist data not found")

    # Only the caller's patients. Part of the query itself, so the count cache
    # below - keyed by the query - can never hand one account another's total.
    query: dict = {"batch_date": batch_date, **access.scope_query(_scope(request))}
    # The Staff page's drill-down and the Overview's "no doctor" link.
    care = hospital.care_filter(db, _scope(request), doctor=doctor, nurse=nurse,
                                unassigned=unassigned)
    if care is not None:
        query["$and"] = query.get("$and", []) + [{"patient_id": {"$in": access.id_variants(care)}}]
    # A hospital admin or insurer sees the overview layer here. Filtering or
    # sorting by diagnosis would hand back the clinical layer one row at a
    # time, so those are ignored for them; search matches the patient id only.
    redacted = access.needs_reason(request.state.user)
    if redacted:
        group = None
        if sort.partition("-")[0] in access.CLINICAL_SORTS:
            sort = "score-desc"
    # Conditions are matched on clinical_groups - every condition the patient
    # has - not clinical_group, which is only the plan they are monitored
    # under. Filtering on the plan hid a diabetic heart failure patient from
    # the diabetes list even though the diagnosis is right there on the record.
    groups = [g.strip() for g in (group or "").split(",") if g.strip() and g.strip() != "All"]
    if groups:
        query["clinical_groups"] = ({"$all": groups} if match == "all"
                                    else {"$in": groups})
    if band and band != "All":
        # current_band where the refresh has run, risk_band where it has not.
        query["$or"] = [{"current_band": band},
                        {"current_band": {"$exists": False}, "risk_band": band}]
    if status and status != "All":
        query["monitoring_status"] = ({"$in": ["action_required", "deteriorating"]}
                                      if status == "NeedsAttention" else status)
    if q and redacted:
        query["$and"] = query.get("$and", []) + [
            {"patient_id": {"$regex": re.escape(q.strip()), "$options": "i"}}]
    elif q:
        # Anchored on the ICD code, contained for the diagnosis text. Escaped so
        # a stray bracket in a search box cannot become a regex.
        needle = re.escape(q.strip())
        query["$and"] = query.get("$and", []) + [{"$or": [
            {"primary_diagnosis": {"$regex": needle, "$options": "i"}},
            {"primary_icd_code": {"$regex": f"^{needle}", "$options": "i"}},
            {"patient_id": {"$regex": needle, "$options": "i"}},
            # The table shows the secondary diagnoses, so searching has to
            # reach them - otherwise a term visible on screen returns nothing.
            {"secondary_diagnoses": {"$regex": needle, "$options": "i"}},
        ]}]

    column, _, direction = sort.partition("-")
    sort_field = _WORKLIST_SORTS.get(column, "current_score")
    order = ASCENDING if direction == "asc" else DESCENDING

    # The count depends only on the filters, not on the page or the sort, so
    # paging through 160 pages of results counts once rather than 160 times.
    total = _cached(f"count:{json.dumps(query, sort_keys=True, default=str)}",
                    lambda: db["patient_worklist"].count_documents(query))
    cursor = (db["patient_worklist"]
              .find(query, _WORKLIST_PROJECTION)
              # patient_id as a tiebreak so paging is stable: without it two rows
              # with the same score can swap between pages and a patient is
              # either shown twice or not at all.
              .sort([(sort_field, order), ("patient_id", ASCENDING)])
              .skip((page - 1) * limit)
              .limit(limit)
              # MongoDB streams a cursor in batches of 101 by default, so a
              # 4,000-row export cost 40 network round trips to Atlas - about
              # 29 seconds, almost none of it query time. One batch, one trip.
              .batch_size(max(limit, 101)))

    data = []
    for row in cursor:
        pid = str(row.get("patient_id", "")).strip()
        if not pid:
            continue
        try:
            discharge = float(row.get("discharge_score", row.get("risk_score", 0)) or 0)
        except (TypeError, ValueError):
            discharge = 0.0
        try:
            current = float(row.get("current_score", discharge) or discharge)
        except (TypeError, ValueError):
            current = discharge

        data.append({
            "id": pid,
            # risk_score is the primary triage number and is the CURRENT score,
            # not a stale discharge-time one.
            "risk_score": current,
            "risk_band": row.get("current_band") or row.get("risk_band", "Low"),
            "discharge_score": discharge,
            "discharge_band": row.get("risk_band", "Low"),
            "current_score": current,
            "trend_delta": row.get("trend_delta"),
            "monitoring_status": row.get("monitoring_status", "insufficient_data"),
            "weeks_tracked": row.get("weeks_tracked", 1),
            "primary_driver_label": (row.get("primary_driver_label")
                                     or _worklist_driver(pid, row)),
            "discharge_date": row.get("discharge_date", ""),
            # What the patient was actually treated for. A risk score beside a
            # diagnosis is triageable; a risk score on its own is not.
            "primary_diagnosis": row.get("primary_diagnosis", ""),
            "primary_icd_code": row.get("primary_icd_code", ""),
            "clinical_group": row.get("clinical_group", ""),
            "group_label": row.get("group_label", ""),
            # Which diagnosis put the patient in that group. Without it a row
            # admitted for liver disease and filtered under "heart failure"
            # reads as a filtering bug rather than a documented comorbidity.
            "group_evidence": row.get("group_evidence", ""),
            "group_confidence": row.get("group_confidence", ""),
            # Every condition on the record, so the row can show why it
            # matched any of the conditions currently selected.
            "clinical_groups": row.get("clinical_groups") or [],
            "conditions": [{"key": k, "label": GROUP_LABELS.get(k, k)}
                           for k in (row.get("clinical_groups") or [])],
            "diagnoses": _diagnosis_list(row),
            "n_diagnoses_coded": row.get("n_diagnoses_coded", 0),
            "driver_1": format_driver_string(str(row.get("driver_1", ""))),
        })

    # Who looks after each patient, and who pays - the overview layer, so it is
    # on every row for every role.
    teams = hospital.care_teams(db, [r["id"] for r in data])
    for r in data:
        team = teams.get(r["id"], {})
        r.update({"doctor": team.get("doctor"), "nurses": team.get("nurses", []),
                  "insurer": team.get("insurer"), "hospital_id": team.get("hospital_id")})
    if redacted:
        data = [access.redact(r) for r in data]
    return {"page": page, "limit": limit, "total": total, "data": data, "redacted": redacted}


@app.get("/api/patient-groups")
def get_patient_groups(request: Request):
    """
    Which conditions are present in the current batch, and how many patients
    each covers. Drives the dashboard's condition filter, so the list offers
    only groups that would actually return rows.
    """
    batch_date = _latest_batch()
    if not batch_date:
        return {"batch_date": None, "groups": []}

    # Counted on clinical_groups, and unwound first, so the number beside a
    # condition is how many patients HAVE it - which is what the filter now
    # returns. Counting the monitoring plan instead understated every condition
    # that loses the priority tie: 288 patients are monitored for heart
    # disease, 547 have it.
    #
    # The counts therefore sum to more than the cohort. That is correct: a
    # patient with heart failure and diabetes is one patient in two conditions.
    rows = _view(request)["patient_worklist"].aggregate([
        {"$match": {"batch_date": batch_date}},
        {"$unwind": "$clinical_groups"},
        {"$group": {"_id": "$clinical_groups", "count": {"$sum": 1}}},
        {"$sort": {"count": -1}},
    ])
    groups = [{"key": r["_id"],
               "label": GROUP_LABELS.get(r["_id"], r["_id"]),
               "count": r["count"]}
              for r in rows if r["_id"]]
    return {"batch_date": batch_date, "groups": groups}


@app.post("/api/cache/clear")
def clear_cache(request: Request):
    """Drop the short-lived caches, so a freshly loaded batch appears at once."""
    _require(request, "clear_cache")
    n = len(_cache)
    _cache.clear()
    return {"cleared": n}


@app.get("/api/patients/{patient_id}")
def get_patient(patient_id: str, request: Request):
    _require_detail(request, patient_id)
    batch_date = get_latest_batch_date(db, "patient_worklist")
    if not batch_date:
        raise HTTPException(status_code=404, detail="Patient worklist data not found")

    row = db["patient_worklist"].find_one(
        {**_patient_id_filter(patient_id), "batch_date": batch_date},
        {"_id": 0},
    )
    if not row:
        raise HTTPException(status_code=404, detail="Patient not found")

    drivers = _extract_drivers_from_row(row)

    # Score history: the post-discharge weekly monitor when the patient has one
    # (Discharge, then Wk 1..N - what "7 weeks monitored" refers to), otherwise
    # one point per admission from the risk registry.
    history = []
    try:
        for doc in (db[WEEKLY_COLLECTION]
                    .find(_patient_id_filter(patient_id), {"_id": 0, "week_number": 1, "risk_score": 1})
                    .sort("week_number", 1)):
            try:
                wk, score = int(doc.get("week_number", len(history))), float(doc.get("risk_score", 0))
            except (ValueError, TypeError):
                continue
            history.append({"week": "Discharge" if wk == 0 else f"Wk {wk}", "score": score})
    except Exception as e:
        print(f"Error fetching weekly monitoring history: {e}")

    try:
        registry_docs = [] if len(history) > 1 else list(
            db["risk_registry"]
            .find(_patient_id_filter(patient_id), {"_id": 0, "batch_date": 1, "risk_score": 1})
            .sort("batch_date", 1)
        )
        if registry_docs:
            history = []
        for doc in registry_docs:
            d = doc.get("batch_date", "")
            try:
                dt = datetime.strptime(d, "%Y-%m-%d")
                label = f"{dt.month}/{dt.day}"
            except ValueError:
                label = d
            try:
                score = float(doc.get("risk_score", 0))
            except (ValueError, TypeError):
                score = 0.0
            history.append({"week": label, "score": score})
    except Exception as e:
        print(f"Error fetching risk registry history: {e}")

    if not history:
        try:
            current_score = float(row.get("risk_score", 0))
        except (ValueError, TypeError):
            current_score = 0.0
        history.append({"week": "Current", "score": current_score})

    return {
        "id": str(row.get("patient_id", "")),
        # risk_score/risk_band are the DISCHARGE figures. current_score/
        # current_band are where the patient is now, after weekly monitoring,
        # and are what the worklist and the forecast both read. Both are sent
        # because the detail page shows one under the other - labelling the
        # discharge score "current" put it in direct contradiction with the
        # early-warning panel sitting beneath it on the same screen.
        "risk_score": row.get("risk_score", 0),
        "risk_band": row.get("risk_band", "Low"),
        "current_score": row.get("current_score", row.get("risk_score", 0)),
        "current_band": row.get("current_band", row.get("risk_band", "Low")),
        "weeks_tracked": row.get("weeks_tracked", 0),
        "monitoring_status": row.get("monitoring_status", ""),
        "discharge_date": row.get("discharge_date", ""),
        "admit_date": row.get("admit_date", ""),
        "primary_diagnosis": row.get("primary_diagnosis", ""),
        "primary_icd_code": row.get("primary_icd_code", ""),
        "secondary_diagnoses": row.get("secondary_diagnoses", []),
        "n_diagnoses_coded": row.get("n_diagnoses_coded", 0),
        # Same shape the worklist sends, so the detail panel renders the full
        # diagnosis list with the same tagging rather than a second variant of
        # it. The worklist row is the summary; this is where a coordinator
        # opens the record, so nothing is folded away here.
        "diagnoses": _diagnosis_list(row),
        "clinical_group": row.get("clinical_group", ""),
        "group_label": row.get("group_label", ""),
        "group_evidence": row.get("group_evidence", ""),
        "group_confidence": row.get("group_confidence", ""),
        "conditions": [{"key": k, "label": GROUP_LABELS.get(k, k)}
                       for k in (row.get("clinical_groups") or [])],
        "drivers": drivers,
        "history": history,
        # Level and direction of the current score, the same verdict as the
        # trend page (roi_model.roi_case).
        "care_plan": suggested_actions(
            row.get("current_score", row.get("risk_score", 0)) or 0,
            row.get("monitoring_status")),
    }


# ---------------------------------------------------------------------------
# Readmission risk trend monitoring
#
# Each risk_registry row is one ADMISSION re-scored at its discharge, so a
# patient's series is their admission history, not a fixed-cadence monitoring
# feed. The intervals are irregular and wide - on MIMIC-IV the median gap
# between consecutive admissions is ~83 days and the 75th percentile is ~355 -
# so every point carries its own elapsed time, and both the status rules and
# the Gemini narration read that interval rather than assuming a week.
#
# A true weekly cadence needs a source that samples weekly (post-discharge
# telemetry); until that exists, this module describes what the data is.
# ---------------------------------------------------------------------------

WEEK_TREND_NOISE_BAND = 5.0  # points; movement within this band vs. trailing avg = "stable"

# The model predicts 30-day readmission, so a return inside 30 days is the
# genuinely urgent pattern. An equally large score jump across a two-year gap is
# a new illness episode, not a deterioration a care team could have caught, and
# escalating it the same way is what made the old status badges meaningless.
RAPID_RETURN_DAYS = 30


def _days_between(earlier: str, later: str):
    """Whole days between two YYYY-MM-DD strings, or None if either is unusable."""
    try:
        return (datetime.strptime(later, "%Y-%m-%d")
                - datetime.strptime(earlier, "%Y-%m-%d")).days
    except (ValueError, TypeError):
        return None


def _describe_interval(days) -> str:
    """Plain-English elapsed time, used in the UI label and the Gemini prompt."""
    if days is None:
        return "interval unknown"
    if days < 0:
        return "overlapping admissions"
    if days == 0:
        return "same day"
    if days == 1:
        return "1 day later"
    if days < 45:
        return f"{days} days later"
    if days < 365:
        return f"{days} days later, about {round(days / 30.4)} months"
    years = days / 365.25
    return f"{days} days later, about {years:.1f} years"

_TREND_STATUS_LABELS = {
    "action_required":   "Action Required",
    "deteriorating":     "Deterioration",
    "improving":         "Improving",
    "stable":            "Stable",
    "insufficient_data": "Insufficient Data",
}

def _classify_trend_status(scores: list, gaps: list = None) -> str:
    """
    Classify a patient's admission score series (oldest first) into a monitoring
    status. Shared by the worklist and the per-patient trend endpoint so the
    dashboard badge and the detail page can never disagree.

    `gaps` is parallel to `scores`, holding days since the previous discharge
    (None for the first admission). It gates the escalation tier: a sharp jump
    only means "act now" if the patient actually came back quickly. Without it
    the old rule fired "escalate immediately" on admissions years apart.
    """
    if len(scores) < 2:
        return "insufficient_data"

    latest = scores[-1]
    last_delta = latest - scores[-2]
    trailing = scores[max(0, len(scores) - 4):-1]  # up to 3 admissions before the latest
    vs_trailing = latest - (sum(trailing) / len(trailing))

    # Unknown gap keeps the original behaviour rather than silently downgrading.
    last_gap = gaps[-1] if gaps and len(gaps) == len(scores) else None
    returned_fast = last_gap is None or last_gap <= RAPID_RETURN_DAYS

    if last_delta >= SCORE_JUMP_ALERT_THRESHOLD and returned_fast:
        return "action_required"
    if vs_trailing >= WEEK_TREND_NOISE_BAND:
        return "deteriorating"
    if vs_trailing <= -WEEK_TREND_NOISE_BAND:
        return "improving"
    return "stable"


WEEKLY_COLLECTION = "weekly_monitoring"


def _weekly_monitoring_series() -> dict:
    """
    {patient_id: {scores, bands, gaps}} from the post-discharge weekly monitor,
    ordered week 0 (discharge) -> week N.

    This is the real Phase 2 shape: a fixed 7-day cadence inside the 30-day
    window. Gaps are therefore a constant 7 days, which is what lets the
    escalation rule in _classify_trend_status fire on a genuine week-over-week
    jump - the thing the module was always meant to detect.
    """
    pipeline = [
        {"$sort": {"week_number": 1}},
        {"$group": {
            "_id": "$patient_id",
            "scores": {"$push": "$risk_score"},
            "bands": {"$push": "$risk_band"},
        }},
    ]
    out = {}
    for doc in db[WEEKLY_COLLECTION].aggregate(pipeline):
        scores = []
        for raw in doc.get("scores", []):
            try:
                scores.append(float(raw))
            except (TypeError, ValueError):
                continue
        if not scores:
            continue
        out[str(doc["_id"])] = {
            "scores": scores,
            "bands": doc.get("bands", []),
            "gaps": [None] + [7] * (len(scores) - 1),
            "kind": "weekly",
        }
    return out


def _weekly_series_by_patient() -> dict:
    """
    One aggregation over risk_registry returning
    {patient_id: {scores, bands, gaps}} with each patient's admission series
    ordered oldest first.

    Done as a single grouped query rather than per-patient lookups so the
    worklist stays one round trip regardless of cohort size. patient_id is
    normalised to str because it is stored as an int in some batches and a
    str in others.

    The date fields go through $ifNull so a document missing admit_date still
    pushes a null and the arrays stay index-aligned with `scores`; a bare $push
    on a missing field would silently shorten the array and shift every gap.
    """
    pipeline = [
        {"$sort": {"batch_date": 1}},
        {"$group": {
            "_id": "$patient_id",
            "scores": {"$push": "$risk_score"},
            "bands": {"$push": "$risk_band"},
            "admits": {"$push": {"$ifNull": ["$admit_date", None]}},
            "discharges": {"$push": {"$ifNull": ["$discharge_date", None]}},
            "batches": {"$push": {"$ifNull": ["$batch_date", None]}},
        }},
    ]

    series_by_patient: dict = {}
    for doc in db["risk_registry"].aggregate(pipeline):
        scores, keep = [], []
        for i, raw in enumerate(doc.get("scores", [])):
            try:
                scores.append(float(raw))
                keep.append(i)
            except (TypeError, ValueError):
                continue
        if not scores:
            continue

        def _at(field, i):
            arr = doc.get(field) or []
            return arr[i] if i < len(arr) else None

        admits = [_at("admits", i) for i in keep]
        discharges = [_at("discharges", i) for i in keep]
        batches = [_at("batches", i) for i in keep]

        series_by_patient[str(doc["_id"])] = {
            "scores": scores,
            "bands": doc.get("bands", []),
            "gaps": _gaps_from_dates(admits, discharges, batches),
            "admits": admits,
            "discharges": discharges,
            "batches": batches,
        }
    return series_by_patient


def _gaps_from_dates(admits: list, discharges: list, batches: list) -> list:
    """
    Days each admission began after the PREVIOUS one ended - the time the
    patient actually spent out of hospital, which is what "since last time"
    means to a care coordinator.

    Falls back to batch_date spacing for rows written before the loader stored
    admit/discharge dates. First element is always None.
    """
    gaps = [None]
    for i in range(1, len(admits)):
        gap = _days_between(discharges[i - 1], admits[i])
        if gap is None:
            gap = _days_between(batches[i - 1], batches[i])
        gaps.append(gap)
    return gaps


_TREND_STATUS_ACTIONS = {
    "action_required": (
        f"Risk jumped sharply at a readmission within {RAPID_RETURN_DAYS} days of the previous "
        "discharge, the pattern this model is trained to catch. Escalate now, prioritize outreach, "
        "and review the discharge plan from the previous stay for what went unresolved."
    ),
    "deteriorating": (
        "Risk has climbed across this patient's recent admissions. Alert the assigned care "
        "coordinator and review whether the underlying conditions are being managed between stays."
    ),
    "improving": (
        "Risk has fallen across recent admissions and the trajectory is favourable. No urgent "
        "action; log the positive trend and continue routine follow-up."
    ),
    "stable": (
        "No meaningful change in risk across recent admissions. Continue routine follow-up; no new "
        "action needed."
    ),
    "insufficient_data": (
        "Only one scored admission on record, so there is no trajectory to assess yet. Risk will be "
        "re-scored at the patient's next admission."
    ),
}


def _drivers_with_sources(doc: dict, patient_id: str) -> list:
    """
    Parse a weekly document's drivers and attribute each to the feed it came
    from.

    The attribution is DERIVED, not stored: feed names are deterministic in
    patient and signal, so a document written before this existed still gets
    the same answer as one written after, and no migration was needed. They are
    also placeholders - see models/monitoring_rules.SIGNAL_FEEDS.
    """
    origin = str(doc.get("source", ""))
    from_model = doc.get("week_number") == 0 or doc.get("scored_by") == "model"
    observed = bool(doc.get("observed", True))

    drivers = _extract_drivers_from_row(doc)
    for d in drivers:
        signal = LABEL_TO_SIGNAL.get(d["label"])
        if from_model or signal is None:
            d["source"] = dict(MODEL_SOURCE)
        elif not observed:
            # The values are last week's, held over. Naming a pharmacy or a
            # device here would claim a reading that was never taken.
            d["source"] = dict(CARRIED_FORWARD_SOURCE)
        else:
            d["source"] = source_for(signal, patient_id, origin)
    return drivers


def _weekly_trend_response(patient_id: str, docs: list) -> dict:
    """
    Shape the post-discharge weekly monitor into the same envelope the
    admission-history path returns, so the UI renders one component either way.

    Week 0 is the discharge score and is the baseline; weeks 1..N are the
    post-discharge re-scores. `observed` records whether that week produced any
    monitoring contact - vitals, adherence, pharmacy or follow-up. A
    carried-forward week is reported rather than hidden, because that is what a
    real programme shows when a patient records nothing that week.
    """
    # All weeks belong to ONE index admission, so the diagnosis is a property of
    # the series rather than of any single week. It is read from the worklist
    # row because weekly_monitoring stores scores, not admission attributes.
    wl = db["patient_worklist"].find_one(
        _patient_id_filter(patient_id),
        {"_id": 0, "primary_diagnosis": 1, "primary_icd_code": 1,
         "secondary_diagnoses": 1, "admit_date": 1, "discharge_date": 1},
    ) or {}

    # The clinical group decides how this patient's weekly signals were weighted
    # and worded. It is stored on each document by the simulator, but a document
    # written before grouping existed has none - so it is recomputed from the
    # worklist row rather than falling back silently to the generic rules.
    stored = next((d for d in docs if d.get("clinical_group")), None)
    baseline_record = next((d.get("discharge_baseline") for d in docs
                            if d.get("discharge_baseline")), None)
    if stored:
        group = {"group": stored["clinical_group"],
                 "label": stored.get("group_label", ""),
                 "evidence": stored.get("group_evidence", ""),
                 "confidence": stored.get("group_confidence", "")}
    else:
        group = classify_group(wl.get("primary_icd_code", ""),
                               wl.get("primary_diagnosis", ""),
                               wl.get("secondary_diagnoses") or [])
    if not baseline_record:
        baseline_record = derive_baseline(patient_id, group["group"])

    weeks, prev = [], None
    for doc in docs:
        try:
            score = float(doc.get("risk_score", 0))
        except (TypeError, ValueError):
            score = 0.0

        wk = int(doc.get("week_number", len(weeks)))
        if prev is None:
            delta, week_trend = None, "stable"
        else:
            delta = round(score - prev, 1)
            week_trend = ("increasing" if delta >= WEEK_TREND_NOISE_BAND else
                          "decreasing" if delta <= -WEEK_TREND_NOISE_BAND else "stable")

        observed = bool(doc.get("observed", True))

        # Where this week's numbers came from. Week 0 is not a monitoring week
        # at all - it is the model reading the discharge record - so it is
        # attributed to the model rather than to a home device.
        origin = str(doc.get("source", ""))
        from_model = wk == 0 or doc.get("scored_by") == "model"
        drivers = _drivers_with_sources(doc, patient_id)

        if from_model:
            sources = [{**MODEL_SOURCE, "signals": ["94 discharge-time features"]}]
        elif observed:
            sources = week_sources(patient_id, origin, group["group"])
        else:
            # Nothing arrived. Listing the feeds anyway would imply they
            # reported, which is the opposite of what happened.
            sources = []

        weeks.append({
            "week_number": wk,
            "batch_date": doc.get("week_date", ""),
            "week_date": doc.get("week_date", ""),
            "week_label": "Discharge" if wk == 0 else f"Wk {wk}",
            "risk_score": score,
            "risk_band": doc.get("risk_band", "Low"),
            # Readings that need assessing today - see models/monitoring_rules.
            "red_flags": [RED_FLAGS[k][1] for k in (doc.get("red_flags") or []) if k in RED_FLAGS],
            "delta": delta,
            "week_trend": week_trend,
            "days_after_discharge": int(doc.get("days_after_discharge", 7 * wk)),
            "days_since_prev": None if wk == 0 else 7,
            "interval_label": ("discharge baseline" if wk == 0 else
                               "7 days later" if observed else
                               "7 days later, no readings recorded - score carried forward"),
            "observed": observed,
            "rapid_return": False,
            "drivers": drivers,
            "sources": sources,
        })
        prev = score

    scores = [w["risk_score"] for w in weeks]
    gaps = [w["days_since_prev"] for w in weeks]
    status = _classify_trend_status(scores, gaps)
    # A red flag in the latest week (saturation below 90%, and so on - see
    # models/monitoring_rules.RED_FLAGS) needs assessing today, whatever the
    # trend says.
    if docs and docs[-1].get("red_flags"):
        status = "action_required"
    first, latest = (scores[0], scores[-1]) if scores else (0.0, 0.0)

    return {
        "patient_id": patient_id,
        "series_kind": "weekly",
        "primary_diagnosis": wl.get("primary_diagnosis", ""),
        "primary_icd_code": wl.get("primary_icd_code", ""),
        "secondary_diagnoses": wl.get("secondary_diagnoses", []),
        "index_admit_date": wl.get("admit_date", ""),
        "index_discharge_date": wl.get("discharge_date", ""),
        # Surfaced so the UI can label the series honestly rather than implying
        # these are observed clinical measurements.
        "data_source": docs[0].get("source", ""),
        "simulated": docs[0].get("source") == "simulated",
        "monitoring_status": status,
        "status_label": _TREND_STATUS_LABELS[status],
        "recommended_action": _WEEKLY_STATUS_ACTIONS[status],
        "weeks_tracked": len(weeks),
        "first_score": first,
        "latest_score": latest,
        "net_change": round(latest - first, 1),
        "median_gap_days": 7 if len(weeks) > 1 else None,
        "span_days": 7 * (len(weeks) - 1),
        "weeks_observed": sum(1 for w in weeks if w["observed"]),
        # Whether there is a case for intervening at THIS point in the series.
        # Sent with the trend so the page can decide whether to lead with money
        # or with "no action needed" before it asks Gemini for anything.
        "roi_case": roi_case(latest, status),
        # Which condition this patient is monitored as, what it was decided on,
        # and which readings matter for them. Surfaced rather than applied
        # silently: a grouping that changes someone's score without saying so is
        # exactly the kind of hidden logic this project has avoided.
        "clinical_group": group,
        "signal_plan": signal_plan(group["group"]),
        # What every relative reading above was measured against. Shown, because
        # "+2.4 kg" is only meaningful next to the weight it is 2.4 kg above.
        "discharge_baseline": baseline_record,
        "baseline_rows": describe_baseline(baseline_record) if baseline_record else [],
        "weeks": weeks,
    }


# Weekly cadence changes what an escalation MEANS, so the copy differs from the
# admission-history wording: here a jump really did happen inside one week.
_WEEKLY_STATUS_ACTIONS = {
    "action_required": (
        "Risk rose sharply within a single monitoring week. Escalate now: contact the patient, "
        "review medication adherence and symptoms, and bring the follow-up appointment forward."
    ),
    "deteriorating": (
        "Risk has climbed steadily over recent monitoring weeks. Alert the assigned care "
        "coordinator, increase check-in frequency, and review the discharge plan."
    ),
    "improving": (
        "Risk is falling week over week and recovery is on track. No urgent action; consider "
        "reducing check-in frequency as the trend holds."
    ),
    "stable": (
        "No meaningful week-to-week change. Continue routine weekly monitoring; no new action needed."
    ),
    "insufficient_data": (
        "Not enough monitoring weeks yet to assess a trend. Continue routine weekly monitoring."
    ),
}


@app.get("/api/patients/{patient_id}/trend")
def get_patient_trend(patient_id: str, request: Request):
    _require_detail(request, patient_id)
    """Weekly readmission-risk trend for one patient: one point per batch run,
    with that week's drivers and an overall stability/deterioration verdict."""
    # Post-discharge weekly monitoring takes precedence: it is the series this
    # module was designed for - one score per week inside the 30-day window.
    # The admission-history path below is the fallback for patients who have no
    # monitoring record.
    weekly_docs = list(
        db[WEEKLY_COLLECTION]
        .find(_patient_id_filter(patient_id), {"_id": 0})
        .sort("week_number", 1)
    )
    if weekly_docs:
        return _weekly_trend_response(patient_id, weekly_docs)

    registry_docs = list(
        db["risk_registry"]
        .find(_patient_id_filter(patient_id), {"_id": 0})
        .sort("batch_date", 1)
    )

    if not registry_docs:
        # No weekly history yet — fall back to the patient's current worklist
        # row so the page still renders a single-point series.
        batch_date = get_latest_batch_date(db, "patient_worklist")
        row = db["patient_worklist"].find_one(
            {**_patient_id_filter(patient_id), "batch_date": batch_date},
            {"_id": 0},
        ) if batch_date else None
        if not row:
            raise HTTPException(status_code=404, detail="Patient not found")
        registry_docs = [row]

    gaps = _gaps_from_dates(
        [d.get("admit_date") for d in registry_docs],
        [d.get("discharge_date") for d in registry_docs],
        [d.get("batch_date") for d in registry_docs],
    )

    weeks = []
    prev_score = None
    for idx, doc in enumerate(registry_docs, start=1):
        try:
            score = float(doc.get("risk_score", 0))
        except (ValueError, TypeError):
            score = 0.0

        batch_date = doc.get("batch_date", "")
        admit_date = doc.get("admit_date") or ""
        discharge_date = doc.get("discharge_date") or batch_date

        # The axis label carries the YEAR: these points span years, and a bare
        # "3/6" repeated across a decade of admissions is unreadable.
        try:
            dt = datetime.strptime(discharge_date, "%Y-%m-%d")
            week_label = f"{dt.month}/{dt.day}/{dt:%y}"
        except ValueError:
            week_label = discharge_date or f"Admission {idx}"

        days_since_prev = gaps[idx - 1] if idx - 1 < len(gaps) else None

        if prev_score is None:
            delta, week_trend = None, "stable"
        else:
            delta = round(score - prev_score, 1)
            if delta >= WEEK_TREND_NOISE_BAND:
                week_trend = "increasing"
            elif delta <= -WEEK_TREND_NOISE_BAND:
                week_trend = "decreasing"
            else:
                week_trend = "stable"

        weeks.append({
            "week_number": idx,
            "batch_date": batch_date,
            "week_label": week_label,
            "risk_score": score,
            "risk_band": doc.get("risk_band", "Low"),
            "delta": delta,
            "week_trend": week_trend,
            # What this point actually is: an admission, on these dates, this
            # long after the patient last left hospital.
            "admit_date": admit_date,
            "discharge_date": discharge_date,
            "los_days": doc.get("los_days"),
            # The diagnosis for THIS admission, not the patient's latest - a
            # trend of scores is only readable next to what each stay was for.
            "primary_diagnosis": doc.get("primary_diagnosis", ""),
            "primary_icd_code": doc.get("primary_icd_code", ""),
            "days_since_prev": days_since_prev,
            "interval_label": _describe_interval(days_since_prev) if idx > 1 else "first scored admission",
            "rapid_return": (days_since_prev is not None
                             and 0 <= days_since_prev <= RAPID_RETURN_DAYS),
            # Every driver in an admission series comes from the same place -
            # the model reading that stay's discharge record - so they are
            # attributed to it rather than to a monitoring feed.
            "drivers": [{**d, "source": dict(MODEL_SOURCE)}
                        for d in _extract_drivers_from_row(doc)],
            "sources": [{**MODEL_SOURCE, "signals": ["94 discharge-time features"]}],
        })
        prev_score = score

    monitoring_status = _classify_trend_status([w["risk_score"] for w in weeks], gaps)

    first_score = weeks[0]["risk_score"] if weeks else 0.0
    latest_score = weeks[-1]["risk_score"] if weeks else 0.0

    observed = [g for g in gaps if g is not None]
    span_days = _days_between(weeks[0]["discharge_date"], weeks[-1]["discharge_date"]) if weeks else None

    return {
        "patient_id": patient_id,
        "series_kind": "admissions",
        "simulated": False,
        "monitoring_status": monitoring_status,
        "status_label": _TREND_STATUS_LABELS[monitoring_status],
        "recommended_action": _TREND_STATUS_ACTIONS[monitoring_status],
        "weeks_tracked": len(weeks),
        "first_score": first_score,
        "latest_score": latest_score,
        "net_change": round(latest_score - first_score, 1),
        # Cadence is a property of the data, not a promise the UI can make, so
        # it is measured per patient and rendered rather than assumed.
        "median_gap_days": sorted(observed)[len(observed) // 2] if observed else None,
        "span_days": span_days,
        "roi_case": roi_case(latest_score, monitoring_status),
        "weeks": weeks,
    }


# ---------------------------------------------------------------------------
# AI insights — ROI estimate + counterfactual explanation (Gemini)
# ---------------------------------------------------------------------------

class DriverInsightInput(BaseModel):
    label: str = ""
    value: str = ""
    explanation: str = ""
    # {kind, feed, channel} - which monitoring feed this value arrived on. The
    # narrative reads differently when a number is a pharmacy fact rather than
    # a patient's own report, so it is passed through to Gemini.
    source: Optional[dict] = None


class AIInsightsRequest(BaseModel):
    patient_id: str
    risk_score: float
    risk_band: str
    drivers: list[DriverInsightInput] = []
    # Where the patient is heading, from the monitoring series. Optional so an
    # older client still works, but without it the ROI verdict can only judge
    # the level - and "55% and falling" deserves different advice from
    # "55% and climbing".
    trend_status: Optional[str] = None


@app.post("/api/ai-insights")
def get_ai_insights(payload: AIInsightsRequest, request: Request):
    """
    ROI for enrolling this patient in a care-coordination intervention, plus a
    counterfactual risk explanation.

    The money is computed here, from the itemised model in api/roi_model.py, so
    the same patient always produces the same figures and every line item is
    auditable. Gemini is given that breakdown and writes only the narrative
    around it — it no longer prices anything or does the arithmetic.
    """
    _require_detail(request, payload.patient_id)
    roi = compute_roi(payload.risk_score, trend_status=payload.trend_status)
    try:
        narrative = generate_roi_and_counterfactual(
            patient_id=payload.patient_id,
            risk_score=payload.risk_score,
            risk_band=payload.risk_band,
            drivers=[d.model_dump() for d in payload.drivers],
            roi=roi,
        )
        return {**roi, **narrative}
    except Exception as exc:
        print(f"[ai-insights] Gemini call failed: {exc}")
        raise HTTPException(
            status_code=503,
            detail="AI insights are temporarily unavailable. Please try again in a moment.",
        )


class WeekNarrativeRequest(BaseModel):
    patient_id: str
    week_number: int
    risk_score: float
    risk_band: str
    delta: Optional[float] = None
    week_trend: str = "stable"
    drivers: list[DriverInsightInput] = []
    # Interval context. Optional so an older client still gets a narrative, but
    # without it Gemini is explicitly told not to characterise the pace of the
    # change rather than assuming a week has passed.
    days_since_prev: Optional[int] = None
    interval_label: str = ""
    admit_date: str = ""
    discharge_date: str = ""
    los_days: Optional[float] = None
    # Which series this point belongs to. Defaults to the admission history so
    # an older client is never silently told the patient is at home.
    series_kind: str = "admissions"
    observed: bool = True
    primary_diagnosis: str = ""
    # The condition this patient is monitored as, and what that means for which
    # signals matter. Without it the narrative reasons about a 2 kg gain the
    # same way for a heart failure patient and a post-operative one.
    clinical_group: str = ""
    group_focus: str = ""


@app.post("/api/week-narrative")
def get_week_narrative(payload: WeekNarrativeRequest, request: Request):
    """On-demand Gemini call: narrate why one admission's score in the trend
    series is where it is, using that admission's drivers, its delta from the
    previous admission, and the elapsed time between the two. Triggered per
    point from the risk trend module."""
    _require_detail(request, payload.patient_id)
    try:
        narrative = generate_week_narrative(
            patient_id=payload.patient_id,
            week_number=payload.week_number,
            risk_score=payload.risk_score,
            risk_band=payload.risk_band,
            delta=payload.delta,
            week_trend=payload.week_trend,
            drivers=[d.model_dump() for d in payload.drivers],
            days_since_prev=payload.days_since_prev,
            interval_label=payload.interval_label,
            clinical_group=payload.clinical_group,
            group_focus=payload.group_focus,
            admit_date=payload.admit_date,
            discharge_date=payload.discharge_date,
            los_days=payload.los_days,
            series_kind=payload.series_kind,
            observed=payload.observed,
            primary_diagnosis=payload.primary_diagnosis,
        )
    except Exception as exc:
        print(f"[week-narrative] Gemini call failed: {exc}")
        raise HTTPException(
            status_code=503,
            detail="AI explanation is temporarily unavailable. Please try again in a moment.",
        )
    return {"narrative": narrative}


# ---------------------------------------------------------------------------
# Chatbot
# ---------------------------------------------------------------------------

class ChatbotMessage(BaseModel):
    role: str
    text: str


class ChatbotQueryRequest(BaseModel):
    question: str
    # The recent transcript, so follow-up questions resolve against what was
    # already asked. Replayed by the client and therefore untrusted; the
    # chatbot service trims it and treats it as conversation, never as
    # instruction.
    history: List[ChatbotMessage] = []


@app.post("/api/chatbot/query")
def chatbot_query(payload: ChatbotQueryRequest, request: Request):
    # The chatbot reads through the caller's view of the database, so every
    # question - however it is phrased - is answered from their patients only.
    # `entities` are the patient ids, conditions, diagnoses and clinicians the
    # answer was phrased from, which the chat window turns into links.
    try:
        return answer_turn(
            payload.question, _view(request),
            history=[m.model_dump() for m in payload.history])
    except Exception as exc:
        print(f"[chatbot] unexpected error handling question: {exc}")
        return {"answer": "Sorry, I couldn't process that question. Please try again in a moment.",
                "entities": []}


# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------

def _batch_dates() -> list:
    """Every batch in the worklist, oldest first. Dates only - no patient data."""
    return sorted(d for d in db["patient_worklist"].distinct("batch_date") if d)


def _band_counts(request: Request, batch_date: str) -> dict:
    """The caller's patients in one batch, counted by discharge band - the same
    field the stored executive_summary counts, so the superadmin's figures are
    unchanged and everyone else's cover only their own patients."""
    rows = _view(request)["patient_worklist"].aggregate([
        {"$match": {"batch_date": batch_date}},
        {"$group": {"_id": "$risk_band", "n": {"$sum": 1}}},
    ])
    counts: dict = {}
    for r in rows:
        band = str(r["_id"] or "").capitalize()
        counts[band] = counts.get(band, 0) + r["n"]
    return counts


@app.get("/api/summary")
def get_summary(request: Request):
    # Worked out from the caller's own patients rather than read from the
    # stored executive_summary, which covers every hospital at once.
    dates = _batch_dates()
    if not dates:
        raise HTTPException(status_code=404, detail="Executive summary data not found")
    counts = _band_counts(request, dates[-1])
    previous = _band_counts(request, dates[-2]) if len(dates) > 1 else None
    high_delta = counts.get("High", 0) - previous.get("High", 0) if previous else 0

    return {
        "batch_date": dates[-1],
        "total_patients": sum(counts.values()),
        "high_count": counts.get("High", 0),
        "medium_count": counts.get("Medium", 0),
        "low_count": counts.get("Low", 0),
        "high_delta": high_delta,
    }


@app.get("/api/summary/history")
def get_summary_history(request: Request):
    dates = _batch_dates()[-8:]
    rows = _view(request)["patient_worklist"].aggregate([
        {"$match": {"batch_date": {"$in": dates},
                    "risk_band": {"$in": ["High", "high", "HIGH"]}}},
        {"$group": {"_id": "$batch_date", "n": {"$sum": 1}}},
    ])
    high_by_date = {r["_id"]: r["n"] for r in rows}

    history = []
    for date_str in dates:
        try:
            dt = datetime.strptime(date_str, "%Y-%m-%d")
            label = f"{dt.month}/{dt.day}"
        except ValueError:
            label = date_str
        history.append({"week_label": label, "high_count": high_by_date.get(date_str, 0)})

    # Pad to at least 2 points so charts render
    if len(history) < 2:
        base = history[0]["high_count"] if history else 20
        today = datetime.today()
        synthetic = [
            {
                "week_label": f"{(today - timedelta(weeks=7 - i)).month}/{(today - timedelta(weeks=7 - i)).day}",
                "high_count": max(0, base - (7 - i) * 2 + (i % 3)),
            }
            for i in range(7)
        ]
        history = synthetic + history

    return history


# ---------------------------------------------------------------------------
# Model metrics
# ---------------------------------------------------------------------------

@app.get("/api/model/metrics")
def get_model_metrics():
    """
    Performance of the model that is actually serving, read from the card the
    training notebook writes beside the bundle.

    This used to read data/model_card.json - a path that does not exist - and
    fall back to a hardcoded {0.70, 0.72, 0.76, 0.52}. Those numbers were not
    the model's: real precision at the operating threshold is 0.34, so the
    dashboard was overstating it by more than double, silently, with no way to
    tell from the response that anything had gone wrong.
    A missing card now raises instead of inventing a flattering answer.
    """
    card_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "data", "mimic", "model", "results", "model_card_phase1.json")
    try:
        with open(card_path, "r") as f:
            card = json.load(f)
    except FileNotFoundError:
        raise HTTPException(
            status_code=503,
            detail="Model card not found, so performance metrics cannot be reported.")

    m = card["metrics"]
    return {
        "auc_roc": m["auc_roc"],
        "auc_pr": m.get("auc_pr"),
        "brier": m.get("brier"),
        # Named for the readmitted class, which is the one being predicted.
        "precision": m.get("precision_readmit", m.get("precision")),
        "recall": m.get("recall_readmit", m.get("recall")),
        "threshold": m["threshold"],
        # Context, so 0.34 precision is read against a 19.6% base rate rather
        # than against an unstated expectation of 1.0.
        "prevalence": card.get("prevalence"),
        "architecture": card.get("architecture"),
        "dataset": card.get("dataset"),
        "trained_at": card.get("trained_at"),
        # The About page renders these, so a retrain updates the public model
        # card without anyone editing JSX.
        "cohort": card.get("cohort", {}),
        "notes": card.get("notes", []),
    }


@app.get("/api/analytics/top-drivers")
def get_top_drivers(risk_band: str = "High", top_n: int = 5):
    """
    Aggregates driver_1 / driver_2 / driver_3 across all patients of the
    given risk_band in the latest batch and returns the top N labels by frequency.
    """
    batch_date = get_latest_batch_date(db, "patient_worklist")
    if not batch_date:
        raise HTTPException(status_code=404, detail="Patient worklist data not found")

    cursor = db["patient_worklist"].find(
        {"batch_date": batch_date, "risk_band": {"$regex": f"^{risk_band}$", "$options": "i"}},
        {"_id": 0, "driver_1": 1, "driver_2": 1, "driver_3": 1},
    )

    counts: dict = {}
    cohort_size = 0

    for row in cursor:
        cohort_size += 1
        for col in ("driver_1", "driver_2", "driver_3"):
            raw = format_driver_string(str(row.get(col, "")).strip())
            if not raw:
                continue
            label = raw.split(": ")[0].strip() if ": " in raw else raw
            counts[label] = counts.get(label, 0) + 1

    if cohort_size == 0:
        return []

    sorted_drivers = sorted(counts.items(), key=lambda x: x[1], reverse=True)[:top_n]

    return [
        {
            "name": label,
            "count": count,
            "percent": f"{round(count / cohort_size * 100)}%",
        }
        for label, count in sorted_drivers
    ]


# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------

# In-memory run registry (swap for a DB in production)
_pipeline_runs: list[dict] = []


def _run_pipeline_task(run_id: str, filepath: str):
    import time

    run = next((r for r in _pipeline_runs if r["id"] == run_id), None)
    if not run:
        return

    steps = ["file_received", "cleaning", "feature_engineering", "model_scoring", "registry_updated"]
    for step in steps:
        time.sleep(1.5)
        run["current_step"] = step

    try:
        with open(filepath, encoding="utf-8-sig") as f:
            run["patient_count"] = sum(1 for _ in f) - 1
    except Exception:
        run["patient_count"] = 0

    run["status"] = "Completed"


def _visible_runs(request: Request) -> list:
    """Upload runs from the caller's own hospital; every run for the superadmin
    unless it picked one hospital."""
    user = request.state.user
    if user["role"] == "superadmin" and not user.get("acting_hospital_id"):
        return _pipeline_runs
    return [r for r in _pipeline_runs if r.get("hospital_id") == access.hospital_of(user)]


@app.get("/api/pipeline/runs")
def get_pipeline_runs(request: Request):
    return list(reversed(_visible_runs(request)))


@app.post("/api/pipeline/upload")
async def upload_pipeline_file(request: Request, file: UploadFile = File(...)):
    _require(request, "add_patients")
    if not file.filename.endswith(".csv"):
        raise HTTPException(status_code=400, detail="Only .csv files are accepted.")

    os.makedirs(INPUT_DIR, exist_ok=True)
    run_id = f"RUN-{str(uuid.uuid4())[:8].upper()}"
    dest = os.path.join(INPUT_DIR, f"{run_id}_{file.filename}")

    with open(dest, "wb") as out:
        shutil.copyfileobj(file.file, out)

    run_record = {
        "id": run_id,
        "filename": file.filename,
        "run_date": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "patient_count": 0,
        "status": "Running",
        "current_step": "file_received",
        "hospital_id": access.hospital_of(request.state.user),
    }
    _pipeline_runs.append(run_record)

    thread = threading.Thread(target=_run_pipeline_task, args=(run_id, dest), daemon=True)
    thread.start()

    return {"run_id": run_id, "status": "Running"}


@app.get("/api/pipeline/status/{run_id}")
def get_pipeline_status(run_id: str, request: Request):
    run = next((r for r in _visible_runs(request) if r["id"] == run_id), None)
    if not run:
        raise HTTPException(status_code=404, detail="Pipeline run not found")
    return run


# ---------------------------------------------------------------------------
# Model Studio: train, compare, save and deploy models on a hospital's own data
# ---------------------------------------------------------------------------
# The flow, the sandbox and the deploy rules live in api/model_studio.py; these
# routes only check the role and translate errors. A hospital admin works on
# their own hospital's models; the superadmin sees every hospital's.
from api import model_studio  # noqa: E402


def _studio_user(request: Request) -> dict:
    _require(request, "manage_models")
    return request.state.user


def _studio_call(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc).strip("'\""))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.get("/api/studio/overview")
def studio_overview(request: Request):
    return _studio_call(model_studio.overview, db, _studio_user(request))


@app.get("/api/studio/models")
def studio_models(request: Request, target: Optional[str] = Query(None)):
    return _studio_call(model_studio.list_models, db, _studio_user(request), target)


@app.get("/api/studio/models/{model_id}")
def studio_model(model_id: str, request: Request):
    return _studio_call(model_studio.get_model, db, model_id, _studio_user(request))


@app.post("/api/studio/models/{model_id}/deploy")
def studio_deploy(model_id: str, request: Request):
    return _studio_call(model_studio.deploy_model, db, model_id, _studio_user(request))


@app.delete("/api/studio/models/{model_id}")
def studio_delete(model_id: str, request: Request):
    _studio_call(model_studio.delete_model, db, model_id, _studio_user(request))
    return {"deleted": model_id}


@app.post("/api/studio/jobs")
def studio_start(request: Request, file: UploadFile = File(...),
                 target: str = Form(...), mode: str = Form("new"),
                 algorithm: str = Form("auto"), name: str = Form(""),
                 base_model_id: Optional[str] = Form(None)):
    user = _studio_user(request)
    path = _studio_call(model_studio.save_upload, file.file, file.filename or "")
    try:
        return _studio_call(model_studio.create_job, db, user, path, file.filename,
                            target=target, mode=mode, base_model_id=base_model_id or None,
                            algorithm=algorithm, name=name)
    finally:
        if os.path.exists(path):  # moved into the job's directory on success
            os.remove(path)


@app.get("/api/studio/jobs/{job_id}")
def studio_job(job_id: str, request: Request):
    user = _studio_user(request)
    return _studio_call(lambda: model_studio.public_job(model_studio.get_job(job_id, user)))


@app.post("/api/studio/jobs/{job_id}/confirm")
def studio_confirm(job_id: str, request: Request, plan: dict = Body(default={})):
    return _studio_call(model_studio.confirm_job, db, job_id, _studio_user(request), plan)


@app.post("/api/studio/jobs/{job_id}/scan")
def studio_scan(job_id: str, request: Request, plan: dict = Body(default={})):
    return _studio_call(model_studio.scan_job, job_id, _studio_user(request), plan)


@app.post("/api/studio/jobs/{job_id}/save")
def studio_save(job_id: str, request: Request, body: dict = Body(default={})):
    return _studio_call(model_studio.save_job_model, db, job_id, _studio_user(request),
                        (body or {}).get("name"))


@app.delete("/api/studio/jobs/{job_id}")
def studio_discard(job_id: str, request: Request):
    _studio_call(model_studio.discard_job, job_id, _studio_user(request))
    return {"discarded": job_id}


@app.post("/api/studio/score")
def studio_score(request: Request, file: UploadFile = File(...), target: str = Form(...)):
    user = _studio_user(request)
    path = _studio_call(model_studio.save_upload, file.file, file.filename or "")
    try:
        return _studio_call(model_studio.score_file, db, user, target, path)
    finally:
        os.remove(path)


# ---------------------------------------------------------------------------
# Manual patient inference
# ---------------------------------------------------------------------------

# The MIMIC-IV model is a HistGradientBoostingClassifier wrapped in
# CalibratedClassifierCV. It exposes neither `.tree_` nor `.decision_path`, so
# the previous decision-path driver walker cannot run on it at all. Feature
# building, scoring, banding and SHAP-based driver extraction now live in
# api/mimic_scoring.py; this module just exposes them over HTTP.
from api import mimic_scoring
from api.roi_model import compute_roi, roi_case, suggested_actions


class ManualPatientInput(BaseModel):
    """
    Discharge-time fields a clinician can realistically supply.

    Every field is optional: the model was trained with missing values present,
    so a blank field is scored as genuinely unknown rather than as zero. See
    api/mimic_scoring for the full contract.
    """
    discharge_date: Optional[str] = None

    # demographics / administrative
    anchor_age: Optional[float] = None
    gender: Optional[str] = None
    race: Optional[str] = None
    insurance: Optional[str] = None
    marital_status: Optional[str] = None
    admission_type: Optional[str] = None

    # stay and utilisation
    los_days: Optional[float] = None
    n_prior_adm: Optional[float] = None
    days_since_prev: Optional[float] = None
    readmit_history: Optional[int] = None
    ed_visit: Optional[int] = None
    ed_hours: Optional[float] = None
    is_emergency: Optional[int] = None
    n_procedures: Optional[float] = None
    n_diagnoses: Optional[float] = None

    # severity
    drg_severity: Optional[float] = None
    drg_mortality: Optional[float] = None

    # medications
    n_drug_orders: Optional[float] = None
    n_distinct_drugs: Optional[float] = None
    med_insulin: Optional[int] = None
    med_anticoagulant: Optional[int] = None
    med_opioid: Optional[int] = None
    med_diuretic: Optional[int] = None
    med_antipsychotic: Optional[int] = None

    # discharge destination
    disch_home: Optional[int] = None
    disch_snf: Optional[int] = None
    disch_ama: Optional[int] = None

    # Charlson comorbidities (charlson_score is derived, never accepted as input)
    myocardial_infarction: Optional[bool] = None
    congestive_heart_failure: Optional[bool] = None
    peripheral_vascular: Optional[bool] = None
    cerebrovascular: Optional[bool] = None
    dementia: Optional[bool] = None
    chronic_pulmonary: Optional[bool] = None
    rheumatic: Optional[bool] = None
    peptic_ulcer: Optional[bool] = None
    mild_liver: Optional[bool] = None
    diabetes_uncomplicated: Optional[bool] = None
    diabetes_complicated: Optional[bool] = None
    hemiplegia: Optional[bool] = None
    renal_disease: Optional[bool] = None
    malignancy: Optional[bool] = None
    severe_liver: Optional[bool] = None
    metastatic_cancer: Optional[bool] = None
    hiv_aids: Optional[bool] = None

    # selected labs
    lab_sodium_last: Optional[float] = None
    lab_creatinine_last: Optional[float] = None
    lab_hemoglobin_last: Optional[float] = None
    lab_albumin_min: Optional[float] = None
    lab_wbc_max: Optional[float] = None
    lab_glucose_last: Optional[float] = None
    lab_hba1c_last: Optional[float] = None
    lab_potassium_last: Optional[float] = None
    lab_bun_last: Optional[float] = None
    lab_platelets_last: Optional[float] = None


class WorklistSaveRequest(BaseModel):
    patient_id: str
    discharge_date: str
    risk_score: float
    risk_band: str
    driver_1: str
    driver_2: str
    driver_3: str
    raw_inputs: Optional[dict] = None


def _split_driver(s: str) -> dict:
    return mimic_scoring._split_driver(s)


def _get_risk_band(risk_score: float) -> str:
    return mimic_scoring.risk_band(risk_score)


def _score_manual_input(payload: "ManualPatientInput") -> dict:
    """Score a manual-entry payload with the MIMIC model."""
    raw = payload.model_dump(exclude={"discharge_date"}, exclude_none=True)
    try:
        return mimic_scoring.score_manual_entry(raw)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=503, detail=f"Model artefacts missing: {exc}")
    except Exception as exc:
        print(f"[scoring] manual entry failed: {exc}")
        raise HTTPException(status_code=503, detail=f"Scoring unavailable: {exc}")


@app.get("/api/manual-entry/schema")
def get_manual_entry_schema():
    """
    Field list and valid category values, so the form stays in sync with the model.

    Still served while manual entry is off: the Update Patient page reads the
    same schema to render its form, and that feature is unaffected.
    """
    try:
        return mimic_scoring.manual_entry_schema()
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"Schema unavailable: {exc}")

@app.post("/api/patients/predict", dependencies=[Depends(require_manual_entry)])
def predict_patient(payload: ManualPatientInput, request: Request):
    _require(request, "add_patients")
    scored = _score_manual_input(payload)
    discharge_date = payload.discharge_date or datetime.now().strftime("%Y-%m-%d")
    patient_id = f"MANUAL-{int(datetime.now().timestamp())}"

    return {
        "patient_id": patient_id,
        "discharge_date": discharge_date,
        **scored,
    }


@app.post("/api/patients/worklist-add", dependencies=[Depends(require_manual_entry)])
def add_to_worklist(payload: WorklistSaveRequest, request: Request):
    """
    Persist a manually scored patient into patient_worklist and update
    executive_summary counts (total_patients + the relevant band count).

    Handles the upsert case: if the patient already exists in this batch
    (e.g. re-submitted), the old band count is decremented and the new one
    incremented so totals stay accurate.
    """
    _require(request, "add_patients")
    # The id comes from the browser. If it already names a patient the caller
    # cannot see - another hospital's, or an unowned legacy record - refuse
    # with the same 404 as for any unseen patient rather than overwrite it.
    known = (db["care_actions"].find_one(_patient_id_filter(payload.patient_id), {"_id": 1})
             or db["patient_worklist"].find_one(_patient_id_filter(payload.patient_id), {"_id": 1}))
    if known:
        _require_patient(request, payload.patient_id)

    batch_date = get_latest_batch_date(db, "patient_worklist")
    if not batch_date:
        batch_date = datetime.now().strftime("%Y-%m-%d")

    # Map risk_band → executive_summary field name
    _BAND_FIELD = {"High": "high_count", "Medium": "medium_count", "Low": "low_count"}
    new_band_field = _BAND_FIELD.get(payload.risk_band)

    # Check if this patient already exists in the worklist for this batch
    existing = db["patient_worklist"].find_one(
        {"patient_id": payload.patient_id, "batch_date": batch_date},
        {"risk_band": 1},
    )

    doc = {
        "patient_id":         payload.patient_id,
        "batch_date":         batch_date,
        "risk_score":         payload.risk_score,
        "risk_band":          payload.risk_band,
        "discharge_date":     payload.discharge_date,
        "driver_1":           payload.driver_1,
        "driver_2":           payload.driver_2,
        "driver_3":           payload.driver_3,
        "source":             "manual",
        "raw_inputs":         payload.raw_inputs or {},
    }

    db["patient_worklist"].update_one(
        {"patient_id": payload.patient_id, "batch_date": batch_date},
        {"$set": doc},
        upsert=True,
    )

    # Build the executive_summary $inc delta
    summary_delta: dict = {}
    if existing is None:
        # Brand-new patient — increment total and band count
        summary_delta["total_patients"] = 1
        if new_band_field:
            summary_delta[new_band_field] = 1
    else:
        old_band_field = _BAND_FIELD.get(existing.get("risk_band"))
        if old_band_field != new_band_field:
            # Band changed — swap counts (total unchanged)
            if old_band_field:
                summary_delta[old_band_field] = -1
            if new_band_field:
                summary_delta[new_band_field] = 1

    if summary_delta:
        db["executive_summary"].update_one(
            {"batch_date": batch_date},
            {"$inc": summary_delta},
        )

    # A new patient belongs to the hospital of whoever added it; without an
    # ownership record nobody but the superadmin could see it (api/access.py).
    db["care_actions"].update_one(
        _patient_id_filter(payload.patient_id),
        {"$setOnInsert": {"patient_id": payload.patient_id, "notes": [],
                          "coordinator_name": None, "assigned_at": None,
                          "assigned_nurse_ids": [],
                          "hospital_id": access.hospital_of(request.state.user)}},
        upsert=True,
    )

    return {
        "status":     "added",
        "patient_id": payload.patient_id,
        "batch_date": batch_date,
    }


# ---------------------------------------------------------------------------
# Patient data update — recalculate an existing patient's risk after a care
# provider edits their metrics, then optionally commit + raise a score-change
# alert.
# ---------------------------------------------------------------------------

SCORE_JUMP_ALERT_THRESHOLD = 15.0
_BAND_RANK = {"Low": 1, "Medium": 2, "High": 3}


class UpdateCommitRequest(BaseModel):
    risk_score: float
    risk_band: str
    driver_1: str
    driver_2: str
    driver_3: str
    raw_inputs: dict
    discharge_date: Optional[str] = None


@app.get("/api/patients/{patient_id}/edit")
def get_patient_for_edit(patient_id: str, request: Request):
    """Return a patient's stored raw_inputs (prefilled with defaults for any
    missing field) plus their current risk_score/band, for the Update Patient form."""
    _require_detail(request, patient_id)
    batch_date = get_latest_batch_date(db, "patient_worklist")
    if not batch_date:
        raise HTTPException(status_code=404, detail="Patient worklist data not found")

    row = db["patient_worklist"].find_one(
        {**_patient_id_filter(patient_id), "batch_date": batch_date},
        {"_id": 0, "raw_inputs": 1, "risk_score": 1, "risk_band": 1, "discharge_date": 1},
    )
    if not row:
        raise HTTPException(status_code=404, detail="Patient not found")

    raw_inputs = {**ManualPatientInput().model_dump(), **(row.get("raw_inputs") or {})}
    raw_inputs["discharge_date"] = raw_inputs.get("discharge_date") or row.get("discharge_date") or ""

    return {
        "patient_id":     patient_id,
        "risk_score":     row.get("risk_score", 0),
        "risk_band":      row.get("risk_band", "Low"),
        "discharge_date": row.get("discharge_date", ""),
        "raw_inputs":     raw_inputs,
    }


@app.post("/api/patients/{patient_id}/predict-update")
def predict_patient_update(patient_id: str, payload: ManualPatientInput, request: Request):
    """Recompute risk_score/band/drivers for an edited patient. Preview only — does NOT persist."""
    _require_patient(request, patient_id)
    _require(request, "edit_patients")
    _require_detail(request, patient_id)
    scored = _score_manual_input(payload)
    discharge_date = payload.discharge_date or datetime.now().strftime("%Y-%m-%d")

    return {
        "patient_id":     patient_id,
        "discharge_date": discharge_date,
        **scored,
    }


@app.post("/api/patients/{patient_id}/update")
def commit_patient_update(patient_id: str, payload: UpdateCommitRequest, request: Request):
    """
    Persist a recalculated score for an existing patient, update executive_summary
    band counts, and raise an alert if the new score represents a meaningful
    increase over the previous one (15+ points, or a jump to a higher risk band).
    """
    _require_patient(request, patient_id)
    _require(request, "edit_patients")
    _require_detail(request, patient_id)
    batch_date = get_latest_batch_date(db, "patient_worklist")
    if not batch_date:
        batch_date = datetime.now().strftime("%Y-%m-%d")

    _BAND_FIELD = {"High": "high_count", "Medium": "medium_count", "Low": "low_count"}
    new_band_field = _BAND_FIELD.get(payload.risk_band)

    existing = db["patient_worklist"].find_one(
        {**_patient_id_filter(patient_id), "batch_date": batch_date},
        {"risk_band": 1, "risk_score": 1, "discharge_date": 1},
    )
    if not existing:
        raise HTTPException(status_code=404, detail="Patient not found")

    old_score = float(existing.get("risk_score", 0))
    old_band = existing.get("risk_band", "Low")

    doc = {
        "patient_id":     patient_id,
        "batch_date":     batch_date,
        "risk_score":     payload.risk_score,
        "risk_band":      payload.risk_band,
        "discharge_date": payload.discharge_date or existing.get("discharge_date", ""),
        "driver_1":       payload.driver_1,
        "driver_2":       payload.driver_2,
        "driver_3":       payload.driver_3,
        "raw_inputs":     payload.raw_inputs,
    }
    db["patient_worklist"].update_one(
        {**_patient_id_filter(patient_id), "batch_date": batch_date},
        {"$set": doc},
        upsert=True,
    )

    old_band_field = _BAND_FIELD.get(old_band)
    if old_band_field != new_band_field:
        summary_delta: dict = {}
        if old_band_field:
            summary_delta[old_band_field] = -1
        if new_band_field:
            summary_delta[new_band_field] = 1
        if summary_delta:
            db["executive_summary"].update_one({"batch_date": batch_date}, {"$inc": summary_delta})

    score_delta = payload.risk_score - old_score
    band_jumped_up = _BAND_RANK.get(payload.risk_band, 0) > _BAND_RANK.get(old_band, 0)
    alert_created = False

    if score_delta >= SCORE_JUMP_ALERT_THRESHOLD or band_jumped_up:
        db["alerts"].insert_one({
            "patient_id":    patient_id,
            "old_score":     old_score,
            "new_score":     payload.risk_score,
            "old_band":      old_band,
            "new_band":      payload.risk_band,
            "delta":         round(score_delta, 1),
            "triggered_at":  datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "acknowledged":  False,
        })
        alert_created = True

    return {
        "status":        "updated",
        "patient_id":    patient_id,
        "batch_date":    batch_date,
        "old_score":     old_score,
        "new_score":     payload.risk_score,
        "alert_created": alert_created,
    }


@app.get("/api/alerts")
def get_alerts(request: Request):
    """Unacknowledged risk-increase alerts, newest first."""
    docs = list(
        _view(request)["alerts"]
        .find({"acknowledged": False})
        .sort("triggered_at", DESCENDING)
    )
    for d in docs:
        d["id"] = str(d.pop("_id"))
    return docs


@app.get("/api/rising-risk")
def get_rising_risk(request: Request, limit: int = Query(50, ge=1, le=500)):
    """The caller's patients whose latest monitoring week got worse: a red flag,
    a rise of risk_watch.RISE_POINTS or more, or a move up a band. Urgent first.
    A doctor or nurse gets their own patients only - the same scope as the list."""
    _require(request, "watch_risk")
    return risk_watch.watchlist(_view(request), db, request.state.user, limit=limit)


class RiskSeenRequest(BaseModel):
    week_number: int


@app.post("/api/rising-risk/{patient_id}/seen")
def mark_rising_risk_seen(patient_id: str, body: RiskSeenRequest, request: Request):
    """Take one patient-week off the caller's bell. A newer week that rises
    again brings the patient back."""
    _require(request, "watch_risk")
    access.require_patient(_scope(request), patient_id)
    risk_watch.mark_seen(db, request.state.user, patient_id, body.week_number)
    return {"patient_id": patient_id, "week_number": body.week_number, "seen": True}


@app.post("/api/alerts/{alert_id}/acknowledge")
def acknowledge_alert(alert_id: str, request: Request):
    _require(request, "respond_alerts")
    try:
        oid = ObjectId(alert_id)
    except InvalidId:
        raise HTTPException(status_code=400, detail="Invalid alert id")

    # Looked up through the caller's view: an alert about someone else's
    # patient is "not found", exactly like one that does not exist.
    if not _view(request)["alerts"].find_one({"_id": oid}, {"_id": 1}):
        raise HTTPException(status_code=404, detail="Alert not found")
    db["alerts"].update_one({"_id": oid}, {"$set": {"acknowledged": True}})
    return {"status": "acknowledged", "id": alert_id}


# ---------------------------------------------------------------------------
# Care coordinator workflow
# ---------------------------------------------------------------------------

class AssignCoordinatorRequest(BaseModel):
    coordinator_name: str
    # Naming a clinician here makes every future alert for this patient route to
    # them, ahead of the condition and specialty rules. See doctor_service.route.
    doctor_id: Optional[str] = None


class AddNoteRequest(BaseModel):
    text: str
    author: str = "Care Team"


# ---------------------------------------------------------------------------
# Hospital pages - see api/hospital.py. All overview layer: counts, names and
# assignments, no clinical content, so no reason is asked for any of it.
# ---------------------------------------------------------------------------

@app.get("/api/overview")
def get_overview(request: Request):
    """How is my hospital doing? Not for doctors and nurses, who land on their
    own patient list instead."""
    _require(request, "view_overview")
    return hospital.overview(db, _view(request), request.state.user, _batch_dates())


@app.get("/api/staff")
def get_staff(request: Request):
    """Who looks after whom: doctors and nurses with their patient counts."""
    _require(request, "view_staff")
    return hospital.staff(db, _view(request), request.state.user, _batch_dates())


class CareTeamRequest(BaseModel):
    patient_ids: List[str]
    doctor_id: Optional[str] = None
    nurse_ids: Optional[List[str]] = None
    add_nurse_ids: Optional[List[str]] = None


@app.post("/api/care-team")
def set_care_team(payload: CareTeamRequest, request: Request):
    """Assign a doctor and nurses to one or more patients. Takes effect at once:
    the next request the doctor or nurse makes already includes the patient."""
    _require(request, "assign")
    return hospital.assign(db, request.state.user, _scope(request), payload.patient_ids,
                           doctor_id=payload.doctor_id, nurse_ids=payload.nurse_ids,
                           add_nurse_ids=payload.add_nurse_ids)


_SUMMARY_FIELDS = {"_id": 0, "patient_id": 1, "risk_score": 1, "risk_band": 1,
                   "current_score": 1, "current_band": 1, "monitoring_status": 1,
                   "trend_delta": 1, "weeks_tracked": 1, "discharge_date": 1}


@app.get("/api/patients/{patient_id}/summary")
def get_patient_summary(patient_id: str, request: Request):
    """The overview layer of one patient - risk, care team, insurer - and
    whether the caller may open the clinical layer yet. What a hospital admin
    sees before giving a reason, and what the page asks first for everyone."""
    _require_patient(request, patient_id)
    row = db["patient_worklist"].find_one(
        {**_patient_id_filter(patient_id), "batch_date": _latest_batch()}, _SUMMARY_FIELDS)
    if not row:
        raise HTTPException(status_code=404, detail="Patient not found")
    team = hospital.care_teams(db, [patient_id])[str(patient_id)]
    state = _detail_access(request, patient_id)
    user = request.state.user
    return {
        "id": str(row.pop("patient_id")),
        **row,
        "current_score": row.get("current_score", row.get("risk_score")),
        "current_band": row.get("current_band") or row.get("risk_band"),
        **team,
        "detail_access": state,
        "reasons": ([{"key": k, "label": v} for k, v in access.REASONS.items()]
                    if state == "reason_required" else []),
        "can_assign": user["role"] in access.ACTIONS["assign"],
    }


class OpenPatientRequest(BaseModel):
    reason: str


@app.post("/api/patients/{patient_id}/open")
def open_patient(patient_id: str, payload: OpenPatientRequest, request: Request):
    """A hospital admin or insurer opens one patient's clinical layer, giving a
    reason. Logged once per patient per sign-in; nobody else is ever asked."""
    _require_patient(request, patient_id)
    user = request.state.user
    if not access.needs_reason(user):
        return {"patient_id": patient_id, "detail_access": "open"}
    if payload.reason not in access.REASONS:
        raise HTTPException(status_code=422, detail="Choose one of the listed reasons")
    if str(patient_id) not in _opened(request):
        access_log.record(db, user, access_log.APP, patient_id, _patient_hospital(patient_id),
                          payload.reason, request.state.session)
        _opened(request).add(str(patient_id))
    return {"patient_id": patient_id, "detail_access": "granted"}


@app.post("/api/patients/{patient_id}/assign")
def assign_coordinator(patient_id: str, payload: AssignCoordinatorRequest, request: Request):
    _require_patient(request, patient_id)
    _require(request, "assign")
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    fields = {
        "patient_id": patient_id,
        "coordinator_name": payload.coordinator_name,
        "assigned_at": now,
    }
    if payload.doctor_id is not None:
        # An empty string clears the assignment and lets routing fall back to
        # the condition rules, which is the only way to undo a named clinician.
        doctor_id = payload.doctor_id.strip()
        if doctor_id and not _view(request)["doctors"].find_one({"doctor_id": doctor_id}):
            raise HTTPException(status_code=404, detail="Doctor not found")
        fields["assigned_doctor_id"] = doctor_id or None

    db["care_actions"].update_one(
        _patient_id_filter(patient_id),
        {"$set": fields, "$setOnInsert": {"notes": []}},
        upsert=True,
    )
    return {"status": "assigned", "patient_id": patient_id,
            "coordinator_name": payload.coordinator_name,
            "assigned_doctor_id": fields.get("assigned_doctor_id")}


@app.post("/api/patients/{patient_id}/notes")
def add_care_note(patient_id: str, payload: AddNoteRequest, request: Request):
    _require_patient(request, patient_id)
    _require(request, "add_notes")
    note = {
        "text": payload.text,
        "author": payload.author,
        "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }
    db["care_actions"].update_one(
        _patient_id_filter(patient_id),
        {
            "$push": {"notes": note},
            "$setOnInsert": {"patient_id": patient_id, "coordinator_name": None, "assigned_at": None},
        },
        upsert=True,
    )
    return {"status": "added", "note": note}


@app.get("/api/patients/{patient_id}/care-actions")
def get_care_actions(patient_id: str, request: Request):
    _require_detail(request, patient_id)
    doc = db["care_actions"].find_one(_patient_id_filter(patient_id), {"_id": 0})
    if not doc:
        return {"patient_id": patient_id, "coordinator_name": None, "assigned_at": None, "notes": []}
    doc.setdefault("notes", [])
    return doc


# ---------------------------------------------------------------------------
# Clinician loop: forecast -> alert -> doctor -> recommendation
# ---------------------------------------------------------------------------
# The forecast itself lives in models/early_warning.py and the alert lifecycle
# in api/doctor_service.py. This section is only the HTTP surface over them.

class RegisterDoctorRequest(BaseModel):
    name: str
    specialty: str
    email: str
    clinical_groups: List[str] = []


class RespondRequest(BaseModel):
    doctor_id: str
    recommendation: str
    actions: List[str] = []
    urgency: str = "routine"


class AcknowledgeRequest(BaseModel):
    doctor_id: str


class DismissRequest(BaseModel):
    doctor_id: str
    reason: str = ""


class ScanRequest(BaseModel):
    limit: Optional[int] = None


def _patient_group(row: dict) -> str:
    """The clinical group a forecast should be judged against."""
    groups = row.get("clinical_groups") or []
    if groups:
        return groups[0]
    return row.get("clinical_group") or "general"


_FORECAST_META_FIELDS = {
    "_id": 0, "patient_id": 1, "group_label": 1, "clinical_group": 1,
    "clinical_groups": 1, "primary_diagnosis": 1, "anchor_age": 1,
    "gender": 1, "discharge_date": 1,
}


class ForecastContext:
    """
    The inputs a forecast needs, loaded in bulk.

    A cohort sweep over 4,000 patients reading three collections one patient at
    a time is 12,000 round trips to Atlas, which takes minutes and fails halfway
    through on a flaky connection. Loading the three collections once and
    forecasting from memory turns that into three queries. The whole weekly
    series is about 20,000 small documents, which fits comfortably.

    Built with no patient_ids it loads the whole cohort; with one id it is the
    cheap path for a single patient's page.
    """

    def __init__(self, db, patient_ids=None):
        week_query = {} if patient_ids is None else {"patient_id": {"$in": patient_ids}}
        self.weeks = {}
        # Only the four fields a forecast reads. The stored week also carries
        # three driver sentences and a narrative, which together are most of the
        # document's bytes and none of its arithmetic - pulling all 20,000 in
        # full times the cursor out against Atlas.
        #
        # Deliberately unsorted: week_number has no index, so asking the server
        # to sort the whole collection is a blocking in-memory sort for an
        # ordering forecast() re-establishes per patient anyway.
        cursor = db[WEEKLY_COLLECTION].find(
            week_query,
            {"_id": 0, "patient_id": 1, "week_number": 1, "risk_score": 1,
             "monitoring": 1},
        ).batch_size(2000)
        for doc in cursor:
            self.weeks.setdefault(str(doc.get("patient_id")), []).append(doc)

        batch_date = get_latest_batch_date(db, "patient_worklist")
        row_query = {"batch_date": batch_date} if batch_date else {}
        if patient_ids is not None:
            row_query["patient_id"] = {"$in": patient_ids}
        self.rows = {str(r.get("patient_id")): r
                     for r in db["patient_worklist"].find(row_query, _FORECAST_META_FIELDS)}

        care_query = {} if patient_ids is None else {"patient_id": {"$in": patient_ids}}
        self.care = {str(c.get("patient_id")): c for c in
                     db["care_actions"].find(care_query,
                                             {"_id": 0, "patient_id": 1,
                                              "assigned_doctor_id": 1})}
        self.bands = mimic_scoring_bands()

    def for_patient(self, patient_id: str) -> tuple:
        key = str(patient_id)
        row = self.rows.get(key) or {}
        result = early_warning.forecast(
            self.weeks.get(key, []), group=_patient_group(row), bands=self.bands)
        meta = {
            "group_label": row.get("group_label"),
            "primary_diagnosis": row.get("primary_diagnosis"),
            "anchor_age": row.get("anchor_age"),
            "gender": row.get("gender"),
            "discharge_date": row.get("discharge_date"),
        }
        return result, meta, (self.care.get(key) or {}).get("assigned_doctor_id")


def _forecast_for(patient_id: str) -> tuple:
    """
    Forecast one patient. Returns (forecast, patient_meta, assigned_doctor_id).

    patient_id is stored as an int on some rows and a string on others, so the
    single-patient path resolves it through _patient_id_filter first and then
    reuses the shared context builder - the sweep and this endpoint must never
    read a patient differently.
    """
    row = db["patient_worklist"].find_one(_patient_id_filter(patient_id),
                                          {"_id": 0, "patient_id": 1})
    resolved = row.get("patient_id") if row else patient_id
    ctx = ForecastContext(db, patient_ids=[resolved, str(resolved)])
    return ctx.for_patient(resolved)


def mimic_scoring_bands() -> dict:
    """Band edges, read from the same file the scorer uses so they cannot drift."""
    try:
        with open(os.path.join(BASE_DIR, "docs", "mimic",
                               "band_thresholds_mimic.json")) as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return early_warning.DEFAULT_BANDS


# ---- registry -------------------------------------------------------------

@app.post("/api/doctors")
def register_doctor(payload: RegisterDoctorRequest, request: Request):
    _require(request, "manage_doctors")
    # The registry is keyed by email, and registering an existing email updates
    # that entry - so an email already registered in another hospital must be
    # refused, not quietly rewritten from here.
    email = (payload.email or "").strip().lower()
    if (db["doctors"].find_one({"email": email}, {"_id": 1})
            and not _view(request)["doctors"].find_one({"email": email}, {"_id": 1})):
        raise HTTPException(status_code=409, detail="That email is registered to another hospital")
    try:
        doctor = doctor_service.register_doctor(
            db, payload.name, payload.specialty, payload.email, payload.clinical_groups)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    hospital = access.hospital_of(request.state.user)
    if hospital:
        db["doctors"].update_one({"doctor_id": doctor["doctor_id"]},
                                 {"$set": {"hospital_id": hospital}})
        doctor = {**doctor, "hospital_id": hospital}
    return doctor


@app.get("/api/doctors")
def get_doctors(request: Request, include_inactive: bool = False):
    return {
        "doctors": doctor_service.list_doctors(_view(request), active_only=not include_inactive),
        "specialties": sorted(set(doctor_service.SPECIALTY_FOR_GROUP.values())),
        "clinical_groups": [{"key": k, "label": GROUP_LABELS.get(k, k),
                             "specialty": v}
                            for k, v in doctor_service.SPECIALTY_FOR_GROUP.items()],
    }


@app.get("/api/doctors/{doctor_id}")
def get_one_doctor(doctor_id: str, request: Request):
    doctor = doctor_service.get_doctor(_view(request), doctor_id)
    if not doctor:
        raise HTTPException(status_code=404, detail="Doctor not found")
    return doctor


@app.delete("/api/doctors/{doctor_id}")
def deactivate_doctor(doctor_id: str, request: Request):
    _require(request, "manage_doctors")
    if not doctor_service.get_doctor(_view(request), doctor_id):
        raise HTTPException(status_code=404, detail="Doctor not found")
    if not doctor_service.deactivate_doctor(db, doctor_id):
        raise HTTPException(status_code=404, detail="Doctor not found")
    return {"status": "deactivated", "doctor_id": doctor_id}


# ---- forecast -------------------------------------------------------------

@app.get("/api/patients/{patient_id}/forecast")
def get_patient_forecast(patient_id: str, request: Request):
    """
    The early-warning forecast for one patient.

    Always returns a renderable object: a patient with one week of monitoring
    gets status "insufficient_history" and the red flags that do apply, not an
    error and not a slope invented from a single point.
    """
    _require_detail(request, patient_id)
    result, meta, _ = _forecast_for(patient_id)
    if result.get("status") == "no_data":
        exists = db["patient_worklist"].count_documents(
            _patient_id_filter(patient_id)) > 0
        if not exists:
            raise HTTPException(status_code=404, detail="Patient not found")
    return {"patient_id": patient_id, "patient": meta, "forecast": result}


# Sweeps are kept in memory, newest last, like _pipeline_runs above. They are
# progress records for a job the user is watching, not an audit trail - the
# alerts themselves are the durable output.
_forecast_scans: list = []
_MAX_SCAN_HISTORY = 20


def _run_forecast_scan_task(scan_id: str, limit: Optional[int]) -> None:
    run = next(r for r in _forecast_scans if r["id"] == scan_id)

    def progress(**fields):
        run.update(fields)

    try:
        run["current_step"] = "loading"
        ctx = ForecastContext(db)
        run["cohort"] = len(ctx.weeks)
        result = doctor_service.scan_cohort(db, ctx.for_patient, limit=limit,
                                            progress=progress)
        run.update(result)
        run["status"] = "Completed"
        run["current_step"] = "done"
    except Exception as exc:
        # A sweep that dies must say so. Left as "Running" it looks like a job
        # that is merely slow, and nobody goes looking for the alerts that were
        # never raised.
        print(f"[forecast] scan {scan_id} failed: {exc}")
        run["status"] = "Failed"
        run["error"] = str(exc)


@app.post("/api/forecast/scan")
def run_forecast_scan(payload: ScanRequest, request: Request):
    """
    Sweep the monitored cohort and raise alerts for anything high or critical.

    Returns immediately with a scan id. Reading 20,000 weekly documents off a
    small Atlas tier takes over a minute before any forecasting starts, which is
    past the point where an HTTP client gives up - so the sweep runs on a thread
    and the caller polls /api/forecast/scan/{scan_id}.

    Safe to run repeatedly: one alert per patient per monitoring week, updated
    rather than duplicated, and reopened only if the severity has worsened.
    """
    _require(request, "run_sweeps")
    scan_id = f"SCAN-{str(uuid.uuid4())[:8].upper()}"
    run = {
        "id": scan_id,
        "started_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "status": "Running",
        "current_step": "queued",
        "limit": payload.limit,
        "scanned": 0,
        "total": None,
    }
    _forecast_scans.append(run)
    del _forecast_scans[:-_MAX_SCAN_HISTORY]

    threading.Thread(target=_run_forecast_scan_task,
                     args=(scan_id, payload.limit), daemon=True).start()
    return {"scan_id": scan_id, "status": "Running"}


@app.get("/api/forecast/scan/{scan_id}")
def get_forecast_scan(scan_id: str, request: Request):
    # Sweeps cover every hospital, so their records are for whoever may run one.
    run = next((r for r in _forecast_scans if r["id"] == scan_id), None)
    if run and request.state.user["role"] not in access.ACTIONS["run_sweeps"]:
        run = None
    if not run:
        raise HTTPException(status_code=404, detail="Scan not found")
    return run


@app.get("/api/forecast/scans")
def get_forecast_scans(request: Request):
    if request.state.user["role"] not in access.ACTIONS["run_sweeps"]:
        return []
    return list(reversed(_forecast_scans))


# ---- alerts and the doctor's inbox ---------------------------------------

@app.get("/api/doctors/{doctor_id}/alerts")
def get_doctor_alerts(doctor_id: str, request: Request, status: Optional[str] = None,
                      limit: int = 50):
    _require(request, "read_inboxes")
    view = _view(request)
    if not doctor_service.get_doctor(view, doctor_id):
        raise HTTPException(status_code=404, detail="Doctor not found")
    return {"doctor_id": doctor_id,
            "alerts": doctor_service.inbox(view, doctor_id, status=status, limit=limit)}


@app.get("/api/doctors/{doctor_id}/notifications")
def get_doctor_notifications(doctor_id: str, request: Request):
    view = _view(request)
    if not doctor_service.get_doctor(view, doctor_id):
        raise HTTPException(status_code=404, detail="Doctor not found")
    return doctor_service.unread_count(view, doctor_id)


@app.get("/api/clinical-alerts/unrouted")
def get_unrouted_alerts(request: Request, limit: int = 50):
    """Alerts with no registered doctor to send them to. Never silently dropped."""
    _require(request, "read_inboxes")
    return {"alerts": doctor_service.unrouted_alerts(_view(request), limit=limit)}


@app.get("/api/patients/{patient_id}/clinical-alerts")
def get_patient_clinical_alerts(patient_id: str, request: Request):
    _require_detail(request, patient_id)
    return {"patient_id": patient_id,
            "alerts": doctor_service.patient_alerts(_view(request), patient_id)}


def _require_clinical_alert(request: Request, alert_id: str) -> None:
    """404 unless the alert is about one of the caller's patients; 403 unless
    their role responds to alerts."""
    if not _view(request)["clinical_alerts"].find_one({"alert_id": alert_id}, {"_id": 1}):
        raise HTTPException(status_code=404, detail="Alert not found")
    _require(request, "respond_alerts")


@app.post("/api/clinical-alerts/{alert_id}/acknowledge")
def acknowledge_clinical_alert(alert_id: str, payload: AcknowledgeRequest, request: Request):
    _require_clinical_alert(request, alert_id)
    try:
        return doctor_service.acknowledge(db, alert_id, payload.doctor_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@app.post("/api/clinical-alerts/{alert_id}/respond")
def respond_to_clinical_alert(alert_id: str, payload: RespondRequest, request: Request):
    _require_clinical_alert(request, alert_id)
    try:
        return doctor_service.respond(
            db, alert_id, payload.doctor_id, payload.recommendation,
            actions=payload.actions, urgency=payload.urgency)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.post("/api/clinical-alerts/{alert_id}/dismiss")
def dismiss_clinical_alert(alert_id: str, payload: DismissRequest, request: Request):
    _require_clinical_alert(request, alert_id)
    try:
        return doctor_service.dismiss(db, alert_id, payload.doctor_id, payload.reason)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@app.get("/api/clinical-alerts/actions")
def get_recommended_actions():
    """The structured vocabulary a doctor picks from when replying."""
    return {
        "actions": [{"key": k, "label": v}
                    for k, v in doctor_service.RECOMMENDED_ACTIONS.items()],
        "urgencies": list(doctor_service.URGENCIES),
    }

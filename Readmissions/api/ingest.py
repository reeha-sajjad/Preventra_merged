"""
The ingest API: how a hospital's systems send discharges and weekly check-ins.

Machine-to-machine, so it lives under /ingest/v1, not /api/. Everything under
/api/ needs a signed-in person, and a hospital's interface engine is not one.
It authenticates with its own per-hospital credential instead:

    Authorization: Bearer ing_<client_id>.<secret>

The hospital is taken from the credential, never from the request body. The
frontend API_KEY ships in the public browser bundle and is never accepted here.

    POST /ingest/v1/encounters   {"bundles": [...]}, at most 500 discharges
    POST /ingest/v1/weeks        {"weeks": [...]}, at most 2,000 weekly check-ins
    POST /ingest/v1/files        the pilot data request's CSVs, as multipart
    GET  /ingest/v1/batches/{id} what happened to a batch
    GET  /ingest/v1/ping         is this credential working

Every record is checked on its own (ingestion/schema.py): the bad ones come
back with their reasons and the rest go ahead. Accepted records are stored
(ingest_stays, ingest_weeks) and queued (ingest_jobs) for the scoring worker,
ingestion/worker.py, which writes them to the dashboard.

Hospital admins manage their own credentials and code mappings through
/api/ingest/... (a signed-in person, access action "manage_ingest").

See docs/pilot_ingestion_build.md.
"""
from __future__ import annotations

import hashlib
import hmac
import io
import re
import secrets
import time
import uuid
from collections import defaultdict, deque
from datetime import datetime, timedelta
from typing import Optional

import pandas as pd
from fastapi import APIRouter, Depends, File, Form, Header, HTTPException, Request, Response, UploadFile
from pydantic import BaseModel
from pymongo import ReplaceOne, UpdateOne

from api import access, auth
from features.weekly_rows import week_number
from ingestion import canonical, schema, vocab
from ingestion import profile as data_profile

CLIENTS = "ingest_clients"
MAPPINGS = "ingest_mappings"
STAYS = "ingest_stays"
WEEKS = "ingest_weeks"
RAW = "ingest_raw"
BATCHES = "ingest_batches"
JOBS = "ingest_jobs"

MAX_BODY_BYTES = 10 * 1024 * 1024
UPLOAD_MAX_BYTES = 50 * 1024 * 1024      # a file uploaded on the Data page; it is stored in batches
RAW_KEEP_DAYS = 90
IDEMPOTENCY_HOURS = 24
RATE_PER_MINUTE = 60
JOB_SIZE = 200
PREFIX_RE = re.compile(r"^[A-Z0-9]{2,10}$")

router = APIRouter(prefix="/ingest/v1", tags=["ingest"])
admin = APIRouter(prefix="/api/ingest", tags=["ingest admin"])

# The product database. api/main.py binds a getter rather than the handle, so
# this module always uses whatever main.db is now - the same database as every
# other route, tests included.
_get_db = None


def bind(get_db) -> None:
    global _get_db
    _get_db = get_db


def _db():
    return _get_db()


def _now() -> datetime:
    return datetime.utcnow()


_indexed: dict = {}


def ensure_indexes(db) -> None:
    key = (id(db.client), db.name)
    if key in _indexed:
        return
    db[STAYS].create_index([("hospital_id", 1), ("encounter_key", 1)], unique=True)
    db[STAYS].create_index([("hospital_id", 1), ("patient_id", 1)])
    db[STAYS].create_index([("hospital_id", 1), ("patient_key", 1)])      # check-ins that name only the patient
    db[WEEKS].create_index([("hospital_id", 1), ("encounter_key", 1), ("week_number", 1)], unique=True)
    db[WEEKS].create_index([("patient_id", 1)])
    db[RAW].create_index("received_at", expireAfterSeconds=RAW_KEEP_DAYS * 86400)
    db[BATCHES].create_index([("client_id", 1), ("idempotency_key", 1)])
    db[JOBS].create_index([("status", 1), ("created_at", 1)])
    db[JOBS].create_index("batch_id")
    _indexed[key] = db.client


# ------------------------------------------------------------- credentials
def _hash(secret: str) -> str:
    return hashlib.sha256(secret.encode("utf-8")).hexdigest()


def create_client(db, hospital_id: str, patient_prefix: str, created_by: str) -> dict:
    """A new credential. The secret is returned here once and stored hashed."""
    prefix = (patient_prefix or "").strip().upper()
    if not PREFIX_RE.match(prefix):
        raise HTTPException(status_code=422,
                            detail="patient_prefix must be 2 to 10 capital letters or digits")
    client_id = f"ic_{secrets.token_hex(4)}"
    secret = secrets.token_urlsafe(32)
    db[CLIENTS].insert_one({"_id": client_id, "hospital_id": hospital_id, "patient_prefix": prefix,
                            "secret_sha256": _hash(secret), "created_by": created_by,
                            "created_at": _now(), "last_used_at": None, "revoked": False})
    return {"client_id": client_id, "hospital_id": hospital_id, "patient_prefix": prefix,
            "token": f"ing_{client_id}.{secret}",
            "note": "Copy the token now. It is stored hashed and cannot be shown again."}


_UNAUTHORISED = HTTPException(status_code=401, detail="Invalid or missing ingest credential",
                              headers={"WWW-Authenticate": "Bearer"})
_calls: dict = defaultdict(deque)


def require_ingest_client(request: Request,
                          authorization: Optional[str] = Header(default=None),
                          content_length: Optional[int] = Header(default=None)) -> dict:
    """The calling hospital, from its credential. The same 401 for a missing,
    malformed, unknown, revoked or wrong credential."""
    if content_length is not None and content_length > MAX_BODY_BYTES:
        raise HTTPException(status_code=413, detail="Send at most 10 MB per request")
    token = (authorization or "").removeprefix("Bearer ").strip()
    m = re.match(r"^ing_(ic_[0-9a-f]{8})\.(.+)$", token)
    if not m:
        raise _UNAUTHORISED
    client = _db()[CLIENTS].find_one({"_id": m.group(1)})
    if not client or client.get("revoked") or not hmac.compare_digest(
            client["secret_sha256"], _hash(m.group(2))):
        raise _UNAUTHORISED
    now = time.monotonic()
    window = _calls[client["_id"]]
    while window and now - window[0] > 60:
        window.popleft()
    if len(window) >= RATE_PER_MINUTE:
        raise HTTPException(status_code=429, detail=f"At most {RATE_PER_MINUTE} requests a minute")
    window.append(now)
    last = client.get("last_used_at")
    if not last or _now() - last > timedelta(minutes=1):
        _db()[CLIENTS].update_one({"_id": client["_id"]}, {"$set": {"last_used_at": _now()}})
    ctx = {"client_id": client["_id"], "hospital_id": client["hospital_id"],
           "patient_prefix": client["patient_prefix"]}
    request.state.ingest = ctx
    return ctx


# ---------------------------------------------------------------- batches
def _mapping(db, hospital_id: str) -> dict:
    return db[MAPPINGS].find_one({"_id": hospital_id}) or {}


def _replay(db, client: dict, idempotency_key: Optional[str]) -> Optional[dict]:
    if not idempotency_key:
        return None
    since = _now() - timedelta(hours=IDEMPOTENCY_HOURS)
    prior = db[BATCHES].find_one({"client_id": client["client_id"], "idempotency_key": idempotency_key,
                                  "created_at": {"$gte": since}})
    return prior["response"] if prior else None


def _queue(db, kind: str, hospital_id: str, keys: list, batch_id: str) -> list:
    jobs = []
    for i in range(0, len(keys), JOB_SIZE):
        jobs.append({"_id": uuid.uuid4().hex, "kind": kind, "hospital_id": hospital_id,
                     "keys": keys[i:i + JOB_SIZE], "batch_id": batch_id, "status": "queued",
                     "attempts": 0, "created_at": _now(), "lease_until": None, "error": None})
    if jobs:
        db[JOBS].insert_many(jobs)
    return [j["_id"] for j in jobs]


def _record_batch(db, client: dict, kind: str, records: list, accepted: list, rejected: list,
                  warnings: list, job_ids: list, idempotency_key: Optional[str],
                  history_only: bool = False) -> dict:
    batch_id = f"b_{uuid.uuid4().hex[:16]}"
    response = {"batch_id": batch_id, "accepted": len(accepted), "rejected": rejected,
                "warnings": warnings, "status": "queued" if job_ids else "done",
                **({"history_only": True} if history_only else {})}
    db[RAW].insert_one({"_id": batch_id, "hospital_id": client["hospital_id"],
                        "client_id": client["client_id"], "kind": kind, "received_at": _now(),
                        "records": records})
    # `source` says which way it came: the machine API, an upload on the Data
    # page, a database pull, or a re-score - so the Activity list can say so.
    db[BATCHES].insert_one({"_id": batch_id, "hospital_id": client["hospital_id"],
                            "client_id": client["client_id"], "kind": kind, "created_at": _now(),
                            "source": client.get("source", "api"), "by": client.get("by"),
                            "history_only": history_only,
                            "received": len(records), "accepted": len(accepted),
                            "rejected": rejected, "warnings": warnings, "job_ids": job_ids,
                            "idempotency_key": idempotency_key, "response": response})
    if job_ids:
        db[JOBS].update_many({"_id": {"$in": job_ids}}, {"$set": {"batch_id": batch_id}})
    return response


def _dates(doc):
    """BSON stores datetimes, not dates."""
    if isinstance(doc, dict):
        return {k: _dates(v) for k, v in doc.items()}
    if isinstance(doc, list):
        return [_dates(v) for v in doc]
    if hasattr(doc, "isoformat") and not isinstance(doc, datetime):
        return datetime(doc.year, doc.month, doc.day)
    return doc


def take_encounters(db, client: dict, bundles: list, idempotency_key: Optional[str] = None,
                    history_only: bool = False) -> dict:
    """Check, store and queue discharges. With `history_only` they are stored
    for training (outcomes, the export) and as earlier admissions of later
    patients, but not scored onto the worklist: past discharges sent to build a
    hospital's first model must not fill its dashboard."""
    ensure_indexes(db)
    replay = _replay(db, client, idempotency_key)
    if replay:
        return replay
    if len(bundles) > schema.MAX_BUNDLES:
        raise HTTPException(status_code=413, detail=f"Send at most {schema.MAX_BUNDLES} discharges per request")
    mapping, hospital, prefix = profile_of(db, client["hospital_id"]), client["hospital_id"], client["patient_prefix"]
    ops, accepted, rejected, warnings = [], [], [], []
    for i, raw in enumerate(bundles):
        key = ((raw or {}).get("encounter") or {}).get("encounter_key") if isinstance(raw, dict) else None
        stay, errors, warns = schema.check_bundle(raw if isinstance(raw, dict) else {}, mapping)
        if errors:
            rejected.append({"index": i, "encounter_key": key, "errors": errors})
            continue
        enc = stay["encounter"]
        doc = _dates({**stay, "hospital_id": hospital, "encounter_key": enc["encounter_key"],
                      "patient_key": enc["patient_key"],
                      "patient_id": f"{prefix}-{enc['patient_key']}", "received_at": _now(),
                      "warnings": warns, **({"history": True} if history_only else {})})
        ops.append(ReplaceOne({"hospital_id": hospital, "encounter_key": enc["encounter_key"]},
                              doc, upsert=True))
        accepted.append(enc["encounter_key"])
        warnings += [{"index": i, "encounter_key": enc["encounter_key"], "warning": w} for w in warns]
    if ops:
        db[STAYS].bulk_write(ops, ordered=False)
    job_ids = [] if history_only else _queue(db, "discharge", hospital, list(dict.fromkeys(accepted)), "")
    return _record_batch(db, client, "encounters", bundles, accepted, rejected, warnings, job_ids,
                         idempotency_key, history_only)


def take_weeks(db, client: dict, weeks: list, idempotency_key: Optional[str] = None,
               history_only: bool = False) -> dict:
    ensure_indexes(db)
    replay = _replay(db, client, idempotency_key)
    if replay:
        return replay
    if len(weeks) > schema.MAX_WEEKS:
        raise HTTPException(status_code=413, detail=f"Send at most {schema.MAX_WEEKS} weekly check-ins per request")
    hospital = client["hospital_id"]
    checked = [(i, *schema.check_week(raw if isinstance(raw, dict) else {})) for i, raw in enumerate(weeks)]
    fields = {"encounter_key": 1, "patient_key": 1, "patient_id": 1, "encounter.discharge_datetime": 1}
    wanted = {w["encounter_key"] for _, w, errs in checked if not errs and w.get("encounter_key")}
    stays = {s["encounter_key"]: s for s in db[STAYS].find(
        {"hospital_id": hospital, "encounter_key": {"$in": list(wanted)}}, fields)}
    # A check-in without its discharge's key follows the patient's latest
    # discharge on or before that week - most monitoring exports only name the
    # patient.
    unkeyed = {w["patient_key"] for _, w, errs in checked if not errs and not w.get("encounter_key")}
    by_patient = defaultdict(list)
    for s in db[STAYS].find({"hospital_id": hospital, "patient_key": {"$in": list(unkeyed)},
                             "encounter.discharge_datetime": {"$ne": None}}, fields):
        by_patient[s["patient_key"]].append(s)
    ops, accepted, rejected = [], [], []
    for i, week, errors in checked:
        stay = None
        if not errors:
            if week.get("encounter_key"):
                stay = stays.get(week["encounter_key"])
                if not stay:
                    errors = [f"encounter_key '{week['encounter_key']}': no discharge with this key. "
                              "Send the discharge first."]
                elif stay["patient_key"] != week["patient_key"]:
                    errors = ["patient_key does not match the discharge it follows"]
            else:
                earlier = [s for s in by_patient.get(week["patient_key"], [])
                           if pd.Timestamp(s["encounter"]["discharge_datetime"]).normalize()
                           <= pd.Timestamp(week["week_date"])]
                stay = max(earlier, key=lambda s: s["encounter"]["discharge_datetime"], default=None)
                if not stay:
                    errors = [f"patient '{week['patient_key']}': no dated discharge on or before "
                              f"{week['week_date']}. Send the discharge first."]
            if stay and not errors:
                if not (stay.get("encounter") or {}).get("discharge_datetime"):
                    errors = ["the discharge this check-in follows has no date, so its week cannot be told"]
                elif pd.Timestamp(week["week_date"]) < pd.Timestamp(
                        stay["encounter"]["discharge_datetime"]).normalize():
                    errors = ["week_date is before the discharge"]
        if errors:
            rejected.append({"index": i, "encounter_key": (weeks[i] or {}).get("encounter_key")
                             if isinstance(weeks[i], dict) else None, "errors": errors})
            continue
        week["encounter_key"] = stay["encounter_key"]
        n = week_number(stay["encounter"]["discharge_datetime"], week["week_date"])
        when = datetime(week["week_date"].year, week["week_date"].month, week["week_date"].day)
        ops.append(UpdateOne(
            {"hospital_id": hospital, "encounter_key": week["encounter_key"], "week_number": n},
            {"$set": {**{f"readings.{k}": v for k, v in week["readings"].items()},
                      "patient_key": week["patient_key"], "patient_id": stay["patient_id"],
                      "week_date": when, "source": week["source"], "received_at": _now(),
                      **({"note": week["note"]} if week.get("note") else {})}},
            upsert=True))
        accepted.append(stay["patient_id"])
    if ops:
        db[WEEKS].bulk_write(ops, ordered=False)
    job_ids = [] if history_only else _queue(db, "week", hospital, list(dict.fromkeys(accepted)), "")
    return _record_batch(db, client, "weeks", weeks, accepted, rejected, [], job_ids, idempotency_key,
                         history_only)


def batch_status(db, client: dict, batch_id: str) -> dict:
    batch = db[BATCHES].find_one({"_id": batch_id, "hospital_id": client["hospital_id"]})
    if not batch:
        raise HTTPException(status_code=404, detail="Batch not found")
    jobs = list(db[JOBS].find({"_id": {"$in": batch["job_ids"]}},
                              {"status": 1, "keys": 1, "error": 1, "result": 1}))
    done = [j for j in jobs if j["status"] == "done"]
    failed = [j for j in jobs if j["status"] == "failed"]
    status = ("done" if len(done) == len(jobs) else "failed" if failed and len(done) + len(failed) == len(jobs)
              else "processing" if any(j["status"] == "running" for j in jobs) or done else "queued")
    return {"batch_id": batch_id, "kind": batch["kind"], "status": status,
            "received": batch["received"], "accepted": batch["accepted"],
            "rejected": batch["rejected"], "warnings": batch["warnings"],
            "scored": sum((j.get("result") or {}).get("scored", 0) for j in done),
            "not_scored": [n for j in done for n in (j.get("result") or {}).get("not_scored", [])],
            "failed": sum(len(j["keys"]) for j in failed),
            "errors": [j["error"] for j in failed if j.get("error")]}


# ------------------------------------------------------------------ files
_ENCOUNTER_FIELDS = list(schema.Encounter.model_fields)
_PATIENT_FIELDS = list(schema.PatientIn.model_fields)
_VITALS = {f"discharge_{k}": k for k in schema.DischargeVitals.model_fields}
_READINGS = list(schema.Readings.model_fields)


def _read_csv(upload: Optional[UploadFile], name: str, max_bytes: int = MAX_BODY_BYTES) -> tuple:
    """(header, rows) - every cell a string, empty cells left out of the rows."""
    if upload is None:
        return [], []
    data = upload.file.read(max_bytes + 1)
    if len(data) > max_bytes:
        raise HTTPException(status_code=413, detail=f"{name} is larger than {max_bytes // (1024 * 1024)} MB")
    try:
        frame = pd.read_csv(io.BytesIO(data), dtype=str, keep_default_na=False, encoding="utf-8-sig")
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=422, detail=f"{name} could not be read as CSV: {exc}")
    return [str(c) for c in frame.columns], [{k: v for k, v in row.items() if v != ""}
                                             for row in frame.to_dict("records")]


def _csv(upload: Optional[UploadFile], name: str, max_bytes: int = MAX_BODY_BYTES) -> list:
    return _read_csv(upload, name, max_bytes)[1]


def bundles_from_files(files: dict) -> tuple:
    """The pilot data request's CSVs -> (discharge bundles, weekly check-ins).

    Rows are already in our field names (data_profile.apply). A hospital that
    sends a date of birth instead of an age gets the age at admission; the
    hospital's extra columns travel with each discharge as `extras`, from the
    patient's row and the encounter's (the encounter's win)."""
    by_enc = defaultdict(lambda: defaultdict(list))
    for table in ("diagnoses", "labs", "medications", "procedures"):
        for row in files.get(table, []):
            by_enc[row.get("encounter_key")][table].append(
                {k: v for k, v in row.items() if k not in ("encounter_key", "extras")})
    drg = {row.get("encounter_key"): {k: v for k, v in row.items() if k not in ("encounter_key", "extras")}
           for row in files.get("drg", [])}
    people = {row.get("patient_key"): row for row in files.get("patients", [])}
    edition = data_profile.file_icd_version(v for row in files.get("encounters", []) for f, v in row.items()
                                            if f.startswith("diagnosis_code_"))
    bundles = []
    for row in files.get("encounters", []):
        key = row.get("encounter_key")
        person = people.get(row.get("patient_key"), {})
        vitals = {_VITALS[k]: v for k, v in row.items() if k in _VITALS}
        # sex etc. may be on encounters.csv too (schema.Bundle moves them)
        encounter = {k: v for k, v in row.items() if k in _ENCOUNTER_FIELDS or k in schema.PERSON_FIELDS}
        born = row.get("date_of_birth") or person.get("date_of_birth")
        if "age_years" not in encounter and born:
            age = data_profile.age_at(born, row.get("admit_datetime"))
            if age is not None:
                encounter["age_years"] = age
        bundle = {"stage": row.get("stage", "final"), "encounter": encounter,
                  "patient": {k: v for k, v in person.items() if k in _PATIENT_FIELDS},
                  **{t: by_enc[key][t] for t in ("diagnoses", "labs", "medications", "procedures")}}
        # Diagnosis codes carried as columns of the discharge (diag_1, diag_2...)
        # join any from a diagnoses file, after them in rank.
        wide = [(int(f.rsplit("_", 1)[1]), str(v).strip()) for f, v in row.items()
                if f.startswith("diagnosis_code_") and str(v).strip()]
        start = len(bundle["diagnoses"])
        for i, (_, code) in enumerate(sorted(wide)):
            version = data_profile.icd_version(code, edition)
            bundle["diagnoses"].append({"icd_code": data_profile.normalise_icd(code, version),
                                        "icd_version": version, "sequence": start + i + 1})
        if row.get("reported_outcome") is not None:
            bundle["reported_outcome"] = row["reported_outcome"]
        if key in drg:
            bundle["drg"] = drg[key]
        if vitals:
            bundle["discharge_vitals"] = vitals
        extras = {**(person.get("extras") or {}), **(row.get("extras") or {})}
        if extras:
            bundle["extras"] = extras
        bundles.append(bundle)
    weeks = []
    for row in files.get("weeks", []):
        weeks.append({**{k: row[k] for k in ("patient_key", "encounter_key", "week_date", "source", "note")
                         if k in row},
                      "readings": {k: v for k, v in row.items() if k in _READINGS}})
    return bundles, weeks


def _store_key(name) -> str:
    """A column name as a database key: MongoDB keys cannot hold a dot or start
    with a dollar sign, and hospital columns do (Adm.Date)."""
    text = str(name).replace(".", "\uff0e")
    return "\uff04" + text[1:] if text.startswith("$") else text


def _load_key(name: str) -> str:
    text = name.replace("\uff0e", ".")
    return "$" + text[1:] if text.startswith("\uff04") else text


def store_columns(mapping: dict) -> dict:
    return {_store_key(k): v for k, v in (mapping or {}).items()}


def profile_of(db, hospital_id: str) -> dict:
    """The hospital's data profile (ingestion/profile.py); stored on its
    mapping document, next to its code mapping."""
    found = _mapping(db, hospital_id)
    if found.get("columns"):
        found = {**found, "columns": {t: {_load_key(k): v for k, v in (m or {}).items()}
                                      for t, m in found["columns"].items()}}
    for field in vocab.VOCABULARIES:
        if isinstance(found.get(field), dict):
            found = {**found, field: {_load_key(k): v for k, v in found[field].items()}}
    return found


def hospital_prefix(db, hospital_id: str) -> Optional[str]:
    """The prefix every patient id from this hospital starts with: its
    profile's, else the one its first credential was given."""
    found = profile_of(db, hospital_id).get("patient_prefix")
    if found:
        return found
    first = db[CLIENTS].find_one({"hospital_id": hospital_id}, {"patient_prefix": 1}, sort=[("created_at", 1)])
    return first["patient_prefix"] if first else None


def take_files(db, client: dict, files: dict, idempotency_key: Optional[str] = None,
               history_only: bool = False, chunked: bool = False) -> dict:
    """Rows as the hospital sent them, one list per table -> stored and queued.

    The hospital's profile renames its columns, reads its dates and converts
    its units first; then the rows take the same path as JSON. Discharges go
    first, so the weekly check-ins can follow them. With `chunked` (uploads and
    database pulls, which may be large) the records go in batches of the
    request limits and each kind returns a list of batches; otherwise one batch
    per kind, as the machine route has always answered."""
    prof = profile_of(db, client["hospital_id"])
    mapped = {t: data_profile.apply(t, rows, prof) for t, rows in files.items() if rows}
    bundles, week_rows = bundles_from_files(mapped)
    out = {}
    if not chunked:
        if bundles:
            out["encounters"] = take_encounters(db, client, bundles,
                                                f"{idempotency_key}:encounters" if idempotency_key else None,
                                                history_only)
        if week_rows:
            out["weeks"] = take_weeks(db, client, week_rows,
                                      f"{idempotency_key}:weeks" if idempotency_key else None, history_only)
        return out
    if bundles:
        out["encounters"] = [take_encounters(db, client, bundles[i:i + schema.MAX_BUNDLES],
                                             history_only=history_only)
                             for i in range(0, len(bundles), schema.MAX_BUNDLES)]
    if week_rows:
        out["weeks"] = [take_weeks(db, client, week_rows[i:i + schema.MAX_WEEKS], history_only=history_only)
                        for i in range(0, len(week_rows), schema.MAX_WEEKS)]
    return out


def queue_rescore(db, hospital_id: str, by: str) -> dict:
    """Re-score every ingested patient of the hospital whose score predates the
    models deployed now, from the discharges already stored. Recorded as a
    batch, so the Activity list shows it and its progress."""
    from api import scoring_service
    ensure_indexes(db)
    keys = scoring_service.due_for_rescore(db, hospital_id)
    if not keys:
        return {"queued": 0}
    job_ids = _queue(db, "discharge", hospital_id, keys, "")
    batch = _record_batch(db, {"client_id": "rescore", "hospital_id": hospital_id, "source": "rescore", "by": by},
                          "rescore", [], keys, [], [], job_ids, None)
    return {"queued": len(keys), "batch_id": batch["batch_id"]}


# ----------------------------------------------------------------- routes
class EncountersIn(BaseModel):
    bundles: list


class WeeksIn(BaseModel):
    weeks: list


@router.get("/ping")
def ping(client: dict = Depends(require_ingest_client)):
    return {"hospital_id": client["hospital_id"], "client_id": client["client_id"]}


@router.post("/encounters", status_code=202)
def post_encounters(payload: EncountersIn, client: dict = Depends(require_ingest_client),
                    idempotency_key: Optional[str] = Header(default=None)):
    return take_encounters(_db(), client, payload.bundles, idempotency_key)


@router.post("/weeks", status_code=202)
def post_weeks(payload: WeeksIn, client: dict = Depends(require_ingest_client),
               idempotency_key: Optional[str] = Header(default=None)):
    return take_weeks(_db(), client, payload.weeks, idempotency_key)


@router.post("/files", status_code=202)
def post_files(client: dict = Depends(require_ingest_client),
               idempotency_key: Optional[str] = Header(default=None),
               encounters: Optional[UploadFile] = File(default=None),
               patients: Optional[UploadFile] = File(default=None),
               diagnoses: Optional[UploadFile] = File(default=None),
               labs: Optional[UploadFile] = File(default=None),
               medications: Optional[UploadFile] = File(default=None),
               procedures: Optional[UploadFile] = File(default=None),
               drg: Optional[UploadFile] = File(default=None),
               weeks: Optional[UploadFile] = File(default=None)):
    """The nightly file drop: the hospital's CSVs in one request, read through
    its data profile. Discharges are taken first, so the weekly check-ins can
    follow them."""
    files = {name: _csv(f, f"{name}.csv") for name, f in (
        ("encounters", encounters), ("patients", patients), ("diagnoses", diagnoses), ("labs", labs),
        ("medications", medications), ("procedures", procedures), ("drg", drg), ("weeks", weeks))}
    if not files["encounters"] and not files["weeks"]:
        raise HTTPException(status_code=422, detail="Send encounters.csv, weeks.csv or both")
    return take_files(_db(), client, files, idempotency_key)


@router.get("/batches/{batch_id}")
def get_batch(batch_id: str, client: dict = Depends(require_ingest_client)):
    return batch_status(_db(), client, batch_id)


# ------------------------------------------------------------ admin side
def _admin_hospital(request: Request, hospital_id: Optional[str] = None) -> str:
    """The hospital an admin acts for: their own, or for the superadmin the one
    picked (hospital picker) or named."""
    user = request.state.user
    access.require(user, "manage_ingest")
    hospital = access.hospital_of(user) or (hospital_id if user["role"] == "superadmin" else None)
    if not hospital:
        raise HTTPException(status_code=422, detail="Choose a hospital first")
    return hospital


class ClientRequest(BaseModel):
    patient_prefix: str
    hospital_id: Optional[str] = None


class MappingRequest(BaseModel):
    admission_type: dict = {}
    discharge_disposition: dict = {}
    payer_class: dict = {}
    sex: dict = {}
    marital_status: dict = {}
    race_ethnicity: dict = {}


@admin.post("/clients", status_code=201)
def new_client(payload: ClientRequest, request: Request):
    hospital = _admin_hospital(request, payload.hospital_id)
    # A superadmin may name any hospital; a mistyped one would make a working
    # credential whose patients nobody can see.
    if not _db().client[auth.IDENTITY_DB]["hospitals"].find_one({"_id": hospital}, {"_id": 1}):
        raise HTTPException(status_code=422, detail="No such hospital")
    # One prefix per hospital, however its data arrives: a second one would give
    # the same patient two ids.
    existing = hospital_prefix(_db(), hospital)
    if existing and (payload.patient_prefix or "").strip().upper() != existing:
        raise HTTPException(status_code=422, detail=f"This hospital's patient ids start with {existing}; "
                                                     f"use {existing}")
    made = create_client(_db(), hospital, payload.patient_prefix, request.state.user.get("email", ""))
    _db()[MAPPINGS].update_one({"_id": hospital}, {"$setOnInsert": {"_id": hospital}}, upsert=True)
    _db()[MAPPINGS].update_one({"_id": hospital, "patient_prefix": {"$in": [None, ""]}},
                               {"$set": {"patient_prefix": made["patient_prefix"]}})
    return made


@admin.get("/clients")
def list_clients(request: Request, hospital_id: Optional[str] = None):
    hospital = _admin_hospital(request, hospital_id)
    return {"clients": [{k: v for k, v in c.items() if k != "secret_sha256"} | {"client_id": c["_id"]}
                        for c in _db()[CLIENTS].find({"hospital_id": hospital}, {"_id": 1, "hospital_id": 1,
                                                    "patient_prefix": 1, "created_by": 1, "created_at": 1,
                                                    "last_used_at": 1, "revoked": 1})]}


@admin.delete("/clients/{client_id}")
def revoke_client(client_id: str, request: Request):
    hospital = _admin_hospital(request)
    result = _db()[CLIENTS].update_one({"_id": client_id, "hospital_id": hospital},
                                     {"$set": {"revoked": True, "revoked_at": _now()}})
    if not result.matched_count:
        raise HTTPException(status_code=404, detail="Credential not found")
    return {"client_id": client_id, "revoked": True}


@admin.get("/mapping")
def get_mapping(request: Request, hospital_id: Optional[str] = None):
    hospital = _admin_hospital(request, hospital_id)
    found = profile_of(_db(), hospital)
    return {"hospital_id": hospital, "canonical": {f: list(v) for f, v in vocab.VOCABULARIES.items()},
            "mapping": {f: found.get(f, {}) for f in vocab.VOCABULARIES}}


@admin.put("/mapping")
def put_mapping(payload: MappingRequest, request: Request, hospital_id: Optional[str] = None):
    """Translate the hospital's own codes into the canonical ones. Every target
    must be a canonical value; anything else is refused, not stored."""
    hospital = _admin_hospital(request, hospital_id)
    body = payload.model_dump()
    problems = [f"{field}: '{code}' -> '{target}' is not one of {', '.join(vocab.VOCABULARIES[field])}"
                for field, codes in body.items() for code, target in codes.items()
                if str(target).upper() not in vocab.VOCABULARIES[field]]
    if problems:
        raise HTTPException(status_code=422, detail=problems)
    clean = {f: {str(k).strip(): str(v).upper() for k, v in codes.items()} for f, codes in body.items()}
    # $set, not a replace: the same document holds the hospital's data profile
    # (columns, units, prefix), which a codes save must leave alone.
    _db()[MAPPINGS].update_one({"_id": hospital}, {"$set": {**{f: store_columns(c) for f, c in clean.items()},
                                                           "updated_by": request.state.user.get("email", ""),
                                                           "updated_at": _now()}}, upsert=True)
    return {"hospital_id": hospital, "mapping": clean}


@admin.get("/batches")
def recent_batches(request: Request, hospital_id: Optional[str] = None, limit: int = 20):
    """The Activity list: each batch with where it came from and how far its
    scoring has got."""
    hospital = _admin_hospital(request, hospital_id)
    db = _db()
    rows = db[BATCHES].find({"hospital_id": hospital}, {"response": 0}).sort("created_at", -1).limit(
        min(max(limit, 1), 100))
    out = []
    for r in rows:
        progress = batch_status(db, {"hospital_id": hospital}, r["_id"])
        out.append({**{k: v for k, v in r.items() if k not in ("_id", "job_ids", "idempotency_key")},
                    "batch_id": r["_id"],
                    **{k: progress[k] for k in ("status", "scored", "not_scored", "failed", "errors")}})
    return {"batches": out}


# ------------------------------------------------------- data profile, upload
class ProfileRequest(BaseModel):
    columns: Optional[dict] = None       # {table: {their column: our field or null}}
    extras: Optional[dict] = None        # {table: [their columns to keep]}: only those kept
    drop: Optional[dict] = None          # {table: [their columns not to keep]}: every other one kept
    units: Optional[dict] = None         # {"weight": "kg"|"lb", "temperature": "C"|"F"}
    dayfirst: Optional[bool] = None
    patient_prefix: Optional[str] = None


def _catalogue() -> dict:
    return {t: {"required": data_profile.required(t), "optional": data_profile.FIELDS[t][1],
                "extras_allowed": t in data_profile.EXTRA_TABLES} for t in data_profile.TABLES}


def _public_profile(db, hospital: str) -> dict:
    found = profile_of(db, hospital)
    return {"columns": found.get("columns") or {}, "extras": found.get("extras") or {},
            "drop": found.get("drop") or {}, "outcome": found.get("outcome"),
            "units": {"weight": "kg", "temperature": "C", **(found.get("units") or {})},
            "dayfirst": bool(found.get("dayfirst")), "patient_prefix": hospital_prefix(db, hospital)}


@admin.get("/profile")
def get_profile(request: Request, hospital_id: Optional[str] = None):
    hospital = _admin_hospital(request, hospital_id)
    return {"hospital_id": hospital, "profile": _public_profile(_db(), hospital), "tables": _catalogue()}


@admin.put("/profile")
def put_profile(payload: ProfileRequest, request: Request, hospital_id: Optional[str] = None):
    """Save how this hospital's files read. Only the parts sent change; a table
    sent under `columns`, `extras` or `drop` replaces that table's entry; a
    table's `drop` list keeps every other column, in place of an `extras` list."""
    hospital = _admin_hospital(request, hospital_id)
    db = _db()
    problems, update, unset = [], {}, {}
    for table, mapping in (payload.columns or {}).items():
        if table not in data_profile.TABLES:
            problems.append(f"columns: no table '{table}'")
            continue
        targets = [v for v in (mapping or {}).values() if v]
        unknown = [v for v in targets if v not in data_profile.fields(table)]
        twice = sorted({v for v in targets if targets.count(v) > 1})
        problems += [f"{table}: '{v}' is not one of our fields" for v in unknown]
        problems += [f"{table}: '{v}' is mapped from more than one column" for v in twice]
        update[f"columns.{table}"] = store_columns({str(k): v for k, v in (mapping or {}).items() if v})
    for table, cols in (payload.extras or {}).items():
        if table not in data_profile.EXTRA_TABLES:
            problems.append(f"extras: only {' and '.join(data_profile.EXTRA_TABLES)} columns can be kept")
            continue
        cols = [str(c) for c in (cols or [])]
        if len(cols) > schema.MAX_EXTRAS:
            problems.append(f"extras: at most {schema.MAX_EXTRAS} columns per table")
        update[f"extras.{table}"] = cols
    for table, cols in (payload.drop or {}).items():
        if table not in data_profile.EXTRA_TABLES:
            problems.append(f"drop: only {' and '.join(data_profile.EXTRA_TABLES)} columns are kept")
            continue
        if table in (payload.extras or {}):
            problems.append(f"{table}: send the columns to keep or the ones to drop, not both")
            continue
        update[f"drop.{table}"] = [str(c) for c in (cols or [])]
        unset[f"extras.{table}"] = ""
    if payload.units is not None:
        allowed = {"weight": ("kg", "lb"), "temperature": ("C", "F")}
        bad = [f"units: {k} must be one of {', '.join(allowed[k])}" if k in allowed else f"units: no unit '{k}'"
               for k, v in payload.units.items() if k not in allowed or v not in allowed[k]]
        problems += bad
        update["units"] = dict(payload.units)
    if payload.dayfirst is not None:
        update["dayfirst"] = payload.dayfirst
    if payload.patient_prefix is not None:
        prefix = payload.patient_prefix.strip().upper()
        current = hospital_prefix(db, hospital)
        if not PREFIX_RE.match(prefix):
            problems.append("patient_prefix must be 2 to 10 capital letters or digits")
        elif current and prefix != current and db[STAYS].count_documents({"hospital_id": hospital}, limit=1):
            problems.append(f"patient_prefix: patients from this hospital already have ids starting {current}; "
                            "changing it would give them second ids")
        update["patient_prefix"] = prefix
    if problems:
        raise HTTPException(status_code=422, detail=problems)
    if update:
        change = {"$set": {**update, "updated_by": request.state.user.get("email", ""), "updated_at": _now()}}
        if unset:
            change["$unset"] = unset
        db[MAPPINGS].update_one({"_id": hospital}, change, upsert=True)
    return {"hospital_id": hospital, "profile": _public_profile(db, hospital)}


@admin.post("/preview")
def preview_file(request: Request, file: UploadFile = File(...), table: Optional[str] = Form(default=None),
                 hospital_id: Optional[str] = None):
    """What one file would give, before anything is stored: which table it is,
    how its columns map, what is missing, kept or dropped, and the first rows
    as they would be read."""
    hospital = _admin_hospital(request, hospital_id)
    table = table or data_profile.detect_table(file.filename or "")
    if table not in data_profile.TABLES:
        raise HTTPException(status_code=422, detail=f"Could not tell which table {file.filename} is; choose one")
    headers, rows = _read_csv(file, file.filename or table, UPLOAD_MAX_BYTES)
    prof = profile_of(_db(), hospital)
    return {"filename": file.filename, "rows": len(rows), "headers": headers,
            **data_profile.check(table, headers, prof),
            "sample": rows[:5], "read_as": data_profile.apply(table, rows[:5], prof)}


@admin.post("/upload", status_code=202)
def upload_files(request: Request, hospital_id: Optional[str] = None,
                 history_only: bool = Form(default=False),
                 encounters: Optional[UploadFile] = File(default=None),
                 patients: Optional[UploadFile] = File(default=None),
                 diagnoses: Optional[UploadFile] = File(default=None),
                 labs: Optional[UploadFile] = File(default=None),
                 medications: Optional[UploadFile] = File(default=None),
                 procedures: Optional[UploadFile] = File(default=None),
                 drg: Optional[UploadFile] = File(default=None),
                 weeks: Optional[UploadFile] = File(default=None)):
    """The Data page's upload: the same path as the machine route, for a
    signed-in admin, with the hospital's own prefix. With `history_only`, past
    discharges are stored for training and not put on the worklist."""
    hospital = _admin_hospital(request, hospital_id)
    db = _db()
    prefix = hospital_prefix(db, hospital)
    if not prefix:
        raise HTTPException(status_code=422, detail="Set this hospital's patient id prefix on the Mapping tab first")
    files = {name: _csv(f, f"{name}.csv", UPLOAD_MAX_BYTES) for name, f in (
        ("encounters", encounters), ("patients", patients), ("diagnoses", diagnoses), ("labs", labs),
        ("medications", medications), ("procedures", procedures), ("drg", drg), ("weeks", weeks))}
    if not files["encounters"] and not files["weeks"]:
        raise HTTPException(status_code=422, detail="Upload an encounters file, a weekly check-ins file or both")
    client = {"client_id": "upload", "hospital_id": hospital, "patient_prefix": prefix, "source": "upload",
              "by": request.state.user.get("email")}
    return take_files(db, client, files, history_only=history_only, chunked=True)


# ---------------------------------------------------------------- re-scoring
@admin.get("/scoring")
def scoring_status(request: Request, hospital_id: Optional[str] = None):
    """What is deployed for the hospital, how many of its patients were scored
    by something else, and when the nightly check runs."""
    from api import scoring_service
    from ingestion import scheduler
    hospital = _admin_hospital(request, hospital_id)
    db = _db()
    last = db[BATCHES].find_one({"hospital_id": hospital, "source": "rescore"}, {"response": 0},
                                sort=[("created_at", -1)])
    return {**scoring_service.rescore_status(db, hospital), "nightly": scheduler.describe(db),
            "last_rescore": ({"batch_id": last["_id"], "at": last["created_at"], "by": last.get("by"),
                              "patients": last["accepted"],
                              "status": batch_status(db, {"hospital_id": hospital}, last["_id"])["status"]}
                             if last else None)}


@admin.post("/rescore", status_code=202)
def rescore_now(request: Request, hospital_id: Optional[str] = None):
    """Re-score now, rather than waiting for the nightly check."""
    hospital = _admin_hospital(request, hospital_id)
    return queue_rescore(_db(), hospital, request.state.user.get("email", ""))


# -------------------------------------------------------------- training file
@admin.get("/export/discharges.csv")
def export_discharges(request: Request, hospital_id: Optional[str] = None):
    """The hospital's discharges with their outcomes, in the columns live
    scoring produces - the file to train its own model on in Model Studio.
    Patient-level data: every download is recorded."""
    hospital = _admin_hospital(request, hospital_id)
    db = _db()
    frame = canonical.training_table(list(db[STAYS].find({"hospital_id": hospital}, {"_id": 0})))
    db["ingest_exports"].insert_one({"hospital_id": hospital, "by": request.state.user.get("email"),
                                     "at": _now(), "rows": len(frame), "kind": "discharges"})
    return Response(frame.to_csv(index=False), media_type="text/csv", headers={
        "Content-Disposition": f'attachment; filename="{hospital}_discharges_trainable.csv"'})

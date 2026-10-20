"""
Database connections: a hospital's own database, read on a schedule.

Set up on the Data page by the hospital's admin (or the superadmin with the
hospital picked), access action "manage_ingest":

    GET    /api/ingest/connections            the hospital's connections
    POST   /api/ingest/connections            add one
    PUT    /api/ingest/connections/{id}       change one (a blank password keeps the stored one)
    DELETE /api/ingest/connections/{id}
    POST   /api/ingest/connections/test       try it before or after saving
    POST   /api/ingest/connections/{id}/run   pull now, optionally as history

A pull (run_pull, in the worker) reads each query's rows since the last
successful pull, less an overlap, and hands them to the same path as an
uploaded file (api/ingest.take_files): the hospital's data profile, the checks,
storage and scoring. Only after the rows are stored does the watermark move.

The database password is encrypted with INGEST_SECRET_KEY, which lives in the
server's environment and never in the database; it is never sent back to a
browser. Without the key, connections cannot be saved or run. Changing the key
makes stored passwords unreadable: re-enter them.
"""
from __future__ import annotations

import secrets
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from api import ingest, secret_box
from ingestion import profile as data_profile
from ingestion import pull, scheduler

CONNECTIONS = scheduler.CONNECTIONS
DEFAULT_OVERLAP_HOURS = 2
FIRST_PULL_DAYS = 30

router = APIRouter(prefix="/api/ingest/connections", tags=["ingest connections"])


# ------------------------------------------------------------------ secrets
def _encrypt(password: str) -> str:
    try:
        return secret_box.encrypt(password)
    except secret_box.SecretKeyMissing:
        raise HTTPException(status_code=503, detail="INGEST_SECRET_KEY is not set on the server, so a database "
                                                    "password cannot be stored safely")


def _decrypt(token: str) -> str:
    try:
        return secret_box.decrypt(token)
    except (secret_box.SecretKeyMissing, secret_box.SecretUnreadable) as exc:
        raise pull.PullError(str(exc))


# ------------------------------------------------------------------- shapes
class ConnectionIn(BaseModel):
    name: Optional[str] = Field(default=None, max_length=80)
    dialect: Optional[str] = None
    host: Optional[str] = Field(default=None, max_length=255)
    port: Optional[int] = Field(default=None, ge=1, le=65535)
    database: Optional[str] = Field(default=None, max_length=128)
    username: Optional[str] = Field(default=None, max_length=128)
    password: Optional[str] = Field(default=None, max_length=512)
    queries: Optional[dict] = None              # {table: SQL}
    schedule_at: Optional[str] = None           # "HH:MM", local time (scheduler.NIGHTLY_TZ)
    enabled: Optional[bool] = None
    overlap_hours: Optional[int] = Field(default=None, ge=0, le=168)
    first_since: Optional[str] = None           # date of the first pull's :since
    connection_id: Optional[str] = None         # /test only: use this one's stored password


class RunIn(BaseModel):
    history_only: bool = False


def _public(doc: dict) -> dict:
    out = {k: v for k, v in doc.items() if k not in ("_id", "password_enc")}
    return {**out, "connection_id": doc["_id"], "has_password": bool(doc.get("password_enc")),
            "dialect_label": pull.LABELS.get(doc.get("dialect"), doc.get("dialect"))}


def _validate(body: dict, creating: bool) -> list:
    problems = []
    if creating:
        for field in ("name", "dialect", "host", "database", "username", "password", "queries"):
            if not body.get(field):
                problems.append(f"{field} is required")
    if body.get("dialect") and body["dialect"] not in pull.DRIVERS:
        problems.append(f"dialect must be one of {', '.join(pull.DRIVERS)}")
    for table, sql in (body.get("queries") or {}).items():
        if table not in data_profile.TABLES:
            problems.append(f"queries: no table '{table}'")
        elif pull.check_query(sql):
            problems.append(f"queries.{table}: {pull.check_query(sql)}")
    if body.get("queries") is not None and not any(t in body["queries"] for t in ("encounters", "weeks")):
        problems.append("queries: include encounters, weeks or both")
    if body.get("schedule_at"):
        try:
            scheduler.parse_at(body["schedule_at"])
        except (ValueError, TypeError):
            problems.append("schedule_at must be a time of day, HH:MM")
    if body.get("first_since"):
        try:
            datetime.fromisoformat(body["first_since"])
        except ValueError:
            problems.append("first_since must be a date, YYYY-MM-DD")
    return problems


def _url(doc: dict, password: str):
    return pull.build_url(doc["dialect"], doc["host"], doc.get("port"), doc["database"], doc["username"], password)


def _since(doc: dict, now: datetime) -> datetime:
    """The :since of the next pull, in the hospital's local time (how its
    database stores times): the last successful pull less the overlap, or for
    the first pull `first_since` (default 30 days back)."""
    if doc.get("watermark"):
        utc = doc["watermark"] - timedelta(hours=doc.get("overlap_hours", DEFAULT_OVERLAP_HOURS))
        return utc.replace(tzinfo=timezone.utc).astimezone(scheduler.zone()).replace(tzinfo=None)
    if doc.get("first_since"):
        return datetime.fromisoformat(doc["first_since"])
    local_now = now.replace(tzinfo=timezone.utc).astimezone(scheduler.zone()).replace(tzinfo=None)
    return local_now - timedelta(days=FIRST_PULL_DAYS)


# ------------------------------------------------------------------- routes
def _mine(db, hospital: str, connection_id: str) -> dict:
    doc = db[CONNECTIONS].find_one({"_id": connection_id, "hospital_id": hospital})
    if not doc:
        raise HTTPException(status_code=404, detail="Connection not found")
    return doc


@router.get("")
def list_connections(request: Request, hospital_id: Optional[str] = None):
    hospital = ingest._admin_hospital(request, hospital_id)
    docs = ingest._db()[CONNECTIONS].find({"hospital_id": hospital}).sort("created_at", 1)
    return {"connections": [_public(d) for d in docs], "dialects": pull.LABELS,
            "default_ports": pull.DEFAULT_PORTS, "default_at": scheduler.nightly_at(),
            "timezone": str(scheduler.zone()), "key_set": secret_box.ready()}


@router.post("", status_code=201)
def create_connection(payload: ConnectionIn, request: Request, hospital_id: Optional[str] = None):
    hospital = ingest._admin_hospital(request, hospital_id)
    body = payload.model_dump(exclude={"connection_id"})
    problems = _validate(body, creating=True)
    if problems:
        raise HTTPException(status_code=422, detail=problems)
    db, now = ingest._db(), datetime.utcnow()
    enabled = body.get("enabled", True) is not False
    doc = {"_id": f"dc_{secrets.token_hex(4)}", "hospital_id": hospital, "name": body["name"],
           "dialect": body["dialect"], "host": body["host"], "port": body.get("port"),
           "database": body["database"], "username": body["username"],
           "password_enc": _encrypt(body["password"]),
           "queries": {t: s.strip() for t, s in body["queries"].items() if s and s.strip()},
           "schedule_at": body.get("schedule_at") or None, "enabled": enabled,
           "overlap_hours": body["overlap_hours"] if body.get("overlap_hours") is not None
           else DEFAULT_OVERLAP_HOURS, "first_since": body.get("first_since") or None,
           "watermark": None, "last_run": None, "created_by": request.state.user.get("email"),
           "created_at": now,
           "next_run_at": scheduler.next_run(body.get("schedule_at") or scheduler.nightly_at(), now)}
    db[CONNECTIONS].insert_one(doc)
    return _public(doc)


@router.put("/{connection_id}")
def update_connection(connection_id: str, payload: ConnectionIn, request: Request,
                      hospital_id: Optional[str] = None):
    hospital = ingest._admin_hospital(request, hospital_id)
    db = ingest._db()
    current = _mine(db, hospital, connection_id)
    body = {k: v for k, v in payload.model_dump(exclude={"connection_id"}).items() if v is not None}
    problems = _validate(body, creating=False)
    if problems:
        raise HTTPException(status_code=422, detail=problems)
    update = {k: v for k, v in body.items() if k != "password"}
    if body.get("password"):
        update["password_enc"] = _encrypt(body["password"])
    if "queries" in update:
        update["queries"] = {t: s.strip() for t, s in update["queries"].items() if s and s.strip()}
    if "schedule_at" in update or "enabled" in update:
        update["next_run_at"] = scheduler.next_run(
            update.get("schedule_at", current.get("schedule_at")) or scheduler.nightly_at(), datetime.utcnow())
    db[CONNECTIONS].update_one({"_id": connection_id}, {"$set": update})
    return _public(db[CONNECTIONS].find_one({"_id": connection_id}))


@router.delete("/{connection_id}")
def delete_connection(connection_id: str, request: Request, hospital_id: Optional[str] = None):
    hospital = ingest._admin_hospital(request, hospital_id)
    db = ingest._db()
    _mine(db, hospital, connection_id)
    db[CONNECTIONS].delete_one({"_id": connection_id})
    return {"connection_id": connection_id, "deleted": True}


@router.post("/test")
def test_connection(payload: ConnectionIn, request: Request, hospital_id: Optional[str] = None):
    """Connect, run each query for the last day, and show its columns, a few
    rows and how they map - nothing is stored."""
    hospital = ingest._admin_hospital(request, hospital_id)
    db = ingest._db()
    body = payload.model_dump()
    stored = _mine(db, hospital, body["connection_id"]) if body.get("connection_id") else {}
    merged = {**stored, **{k: v for k, v in body.items() if v not in (None, "")}}
    problems = _validate({k: merged.get(k) for k in ("dialect", "queries", "schedule_at")}, creating=False)
    for field in ("dialect", "host", "database", "username"):
        if not merged.get(field):
            problems.append(f"{field} is required")
    if problems:
        raise HTTPException(status_code=422, detail=problems)
    try:
        password = body.get("password") or (_decrypt(stored["password_enc"]) if stored.get("password_enc") else "")
        url = _url(merged, password)
    except pull.PullError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    since = datetime.utcnow() - timedelta(days=1)
    report = pull.probe(url, merged.get("queries") or {}, since)
    prof = ingest.profile_of(db, hospital)
    for table, result in report["tables"].items():
        if result.get("ok"):
            result["mapping"] = data_profile.check(table, result["columns"], prof)
    return report


@router.post("/{connection_id}/run", status_code=202)
def run_now(connection_id: str, request: Request, payload: Optional[RunIn] = None,
            hospital_id: Optional[str] = None):
    hospital = ingest._admin_hospital(request, hospital_id)
    db = ingest._db()
    _mine(db, hospital, connection_id)
    job_id = scheduler.queue_pull(db, connection_id, hospital, history_only=bool(payload and payload.history_only),
                                  by=request.state.user.get("email", ""))
    return {"connection_id": connection_id, "job_id": job_id, "status": "queued"}


# --------------------------------------------------------------- the worker
def run_pull(db, job: dict) -> dict:
    """Pull one connection (an ingest job of kind "pull"). The watermark moves
    only after everything read is stored, so a failed night is re-read next
    time; the outcome is kept on the connection for the Data page."""
    connection_id = job["keys"][0]
    conn = db[CONNECTIONS].find_one({"_id": connection_id})
    if not conn:
        return {"scored": 0, "not_scored": [], "note": "the connection was deleted"}
    started = datetime.utcnow()
    history_only = bool(job.get("history_only"))
    since = _since(conn, started)
    try:
        prefix = ingest.hospital_prefix(db, conn["hospital_id"])
        if not prefix:
            raise pull.PullError("set the hospital's patient id prefix on the Mapping tab first")
        rows = pull.fetch(_url(conn, _decrypt(conn["password_enc"])), conn["queries"], since)
        client = {"client_id": connection_id, "hospital_id": conn["hospital_id"], "patient_prefix": prefix,
                  "source": "pull", "by": job.get("by") or "schedule"}
        taken = ingest.take_files(db, client, rows, history_only=history_only, chunked=True)
    except Exception as exc:                                         # noqa: BLE001
        db[CONNECTIONS].update_one({"_id": connection_id}, {"$set": {"last_run": {
            "at": started, "ok": False, "since": since, "history_only": history_only,
            "error": str(exc)[:400] if isinstance(exc, pull.PullError) else type(exc).__name__}}})
        raise
    counts = {t: len(r) for t, r in rows.items()}
    batches = [b["batch_id"] for kind in taken.values() for b in kind]
    db[CONNECTIONS].update_one({"_id": connection_id}, {"$set": {"watermark": started, "last_run": {
        "at": started, "ok": True, "since": since, "rows": counts, "batch_ids": batches,
        "history_only": history_only, "accepted": sum(b["accepted"] for kind in taken.values() for b in kind),
        "rejected": sum(len(b["rejected"]) for kind in taken.values() for b in kind)}}})
    return {"scored": 0, "not_scored": [], "pulled": counts, "batches": batches}

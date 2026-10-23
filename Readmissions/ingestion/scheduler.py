"""
The worker's clock: what runs on its own, and when.

    nightly re-score   once a night, every hospital whose patients were scored
                       by other models than the ones deployed now is re-scored
                       from its stored discharges (api/ingest.queue_rescore)
    database pulls     each enabled connection runs at its own time of day
                       (api/ingest_connections.py)

The worker calls tick() about every half minute. Each run is claimed with one
atomic update of its next_run_at, so two workers never start the same run, and
a worker that was down when a run fell due starts it when it comes back.

Times are a wall-clock "HH:MM" in NIGHTLY_TZ (default UTC), the time zone of
the hospital the deployment serves - on a clinic's own server, its local time.

    NIGHTLY_AT=02:00   when the nightly re-score runs, and the default time of
                       a database pull that has none of its own
    NIGHTLY_TZ=UTC     an IANA zone name, e.g. Asia/Karachi
"""
from __future__ import annotations

import os
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

SCHEDULE = "ingest_schedule"
CONNECTIONS = "ingest_connections"
JOBS = "ingest_jobs"
NIGHTLY = "nightly-rescore"


def nightly_at() -> str:
    """Set but empty - a blank variable on the host - is the default too."""
    at = os.environ.get("NIGHTLY_AT", "").strip() or "02:00"
    try:
        parse_at(at)
    except ValueError:
        return "02:00"
    return at


def zone() -> ZoneInfo:
    try:
        return ZoneInfo(os.environ.get("NIGHTLY_TZ", "").strip() or "UTC")
    except ZoneInfoNotFoundError:
        return ZoneInfo("UTC")


def parse_at(value: str) -> tuple:
    hour, minute = (int(p) for p in str(value).strip().split(":", 1))
    if not (0 <= hour < 24 and 0 <= minute < 60):
        raise ValueError(f"'{value}' is not a time of day (HH:MM)")
    return hour, minute


def next_run(at: str, after: datetime) -> datetime:
    """The first `at` (local wall-clock time) strictly after `after` (naive UTC),
    as naive UTC - what is stored and compared."""
    hour, minute = parse_at(at)
    tz = zone()
    local = after.replace(tzinfo=timezone.utc).astimezone(tz)
    candidate = local.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if candidate <= local:
        candidate = (candidate + timedelta(days=1)).replace(hour=hour, minute=minute)
    return candidate.astimezone(timezone.utc).replace(tzinfo=None)


def _claim(collection, query: dict, next_at: datetime, now: datetime) -> Optional[dict]:
    """Take a due run: move its next_run_at on, atomically. None when another
    worker took it first, or it is not due."""
    return collection.find_one_and_update({**query, "next_run_at": {"$lte": now}},
                                          {"$set": {"next_run_at": next_at, "last_run_at": now}})


def queue_pull(db, connection_id: str, hospital_id: str, history_only: bool = False, by: str = "") -> str:
    """A job for the worker to pull from one connection now."""
    job_id = uuid.uuid4().hex
    db[JOBS].insert_one({"_id": job_id, "kind": "pull", "hospital_id": hospital_id, "keys": [connection_id],
                         "history_only": history_only, "by": by, "batch_id": "", "status": "queued",
                         "attempts": 0, "created_at": datetime.utcnow(), "lease_until": None, "error": None})
    return job_id


def tick(db, now: Optional[datetime] = None) -> dict:
    """Start whatever has fallen due. Returns what it started."""
    from api import ingest
    now = now or datetime.utcnow()
    started = {"rescored": {}, "pulls": [], "alerts": False}

    db[SCHEDULE].update_one({"_id": NIGHTLY}, {"$setOnInsert": {"next_run_at": next_run(nightly_at(), now)}},
                            upsert=True)
    if _claim(db[SCHEDULE], {"_id": NIGHTLY}, next_run(nightly_at(), now), now):
        for hospital in db["patient_worklist"].distinct("hospital_id", {"source": "ingest"}):
            if hospital:
                done = ingest.queue_rescore(db, hospital, "nightly")
                if done.get("queued"):
                    started["rescored"][hospital] = done["queued"]
        # Every monitored patient's latest week, forecast again: an alert the
        # upload path missed, or a care team that changed, is caught overnight.
        started["alerts"] = bool(ingest._queue(db, "alerts", "", ["all"], ""))

    for conn in db[CONNECTIONS].find({"enabled": True, "next_run_at": {"$lte": now}},
                                     {"_id": 1, "hospital_id": 1, "schedule_at": 1}):
        nxt = next_run(conn.get("schedule_at") or nightly_at(), now)
        if _claim(db[CONNECTIONS], {"_id": conn["_id"]}, nxt, now):
            queue_pull(db, conn["_id"], conn["hospital_id"], by="schedule")
            started["pulls"].append(conn["_id"])
    return started


def describe(db) -> dict:
    """When the nightly re-score runs next, for the Data page."""
    doc = db[SCHEDULE].find_one({"_id": NIGHTLY}) or {}
    return {"at": nightly_at(), "timezone": str(zone()),
            "next_run_at": doc.get("next_run_at") or next_run(nightly_at(), datetime.utcnow()),
            "last_run_at": doc.get("last_run_at")}

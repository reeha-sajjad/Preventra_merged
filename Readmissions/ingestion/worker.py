"""
The ingestion worker: takes queued jobs (ingest_jobs) and scores them.

    python -m ingestion.worker           run until stopped (a Railway service)
    python -m ingestion.worker --once    drain the queue and exit

A job is claimed atomically with a lease, so two workers never take the same
one, and a worker that dies mid-job leaves it to be retried once the lease
runs out. A job that fails three times is marked failed, with its error on the
job and on the batch (GET /ingest/v1/batches/{id}).

Runs with the same image and settings as the API: it reads the same MONGO_URI
and MONGO_DB, and it scores Model Studio models through the same sandbox
runner.
"""
from __future__ import annotations

import argparse
import os
import time
import traceback
from datetime import datetime, timedelta

from pymongo import ReturnDocument

JOBS = "ingest_jobs"
LEASE = timedelta(minutes=10)
MAX_ATTEMPTS = 3


def claim(db):
    """The oldest job nobody holds, now leased to this worker; or None."""
    now = datetime.utcnow()
    return db[JOBS].find_one_and_update(
        {"$or": [{"status": "queued"}, {"status": "running", "lease_until": {"$lt": now}}]},
        {"$set": {"status": "running", "lease_until": now + LEASE, "started_at": now},
         "$inc": {"attempts": 1}},
        sort=[("created_at", 1)], return_document=ReturnDocument.AFTER)


def handle(db, job: dict) -> dict:
    from api import scoring_service
    if job["kind"] == "discharge":
        return scoring_service.score_discharges(db, job["hospital_id"], job["keys"])
    if job["kind"] == "week":
        return scoring_service.score_weeks(db, job["hospital_id"], job["keys"])
    raise ValueError(f"unknown job kind '{job['kind']}'")


def run_one(db) -> bool:
    """Claim and process one job. False when the queue is empty."""
    job = claim(db)
    if not job:
        return False
    try:
        result = handle(db, job)
        db[JOBS].update_one({"_id": job["_id"]},
                            {"$set": {"status": "done", "result": result, "error": None,
                                      "finished_at": datetime.utcnow()}})
        print(f"[worker] {job['kind']} job {job['_id'][:8]}: {result.get('scored', 0)} scored, "
              f"{len(result.get('not_scored', []))} not scored")
    except Exception as exc:                                       # noqa: BLE001
        failed = job["attempts"] >= MAX_ATTEMPTS
        error = f"{type(exc).__name__}: {str(exc)[:400]}"
        db[JOBS].update_one({"_id": job["_id"]},
                            {"$set": {"status": "failed" if failed else "queued", "error": error,
                                      "lease_until": None, "finished_at": datetime.utcnow()}})
        # Counts and job ids only: clinical values never go into the logs. The
        # message (which can quote a value) is on the job, for that hospital;
        # the log gets the type and where it was raised.
        print(f"[worker] {job['kind']} job {job['_id'][:8]} attempt {job['attempts']} failed: "
              f"{type(exc).__name__} at " + " <- ".join(
                  f"{os.path.basename(f.filename)}:{f.lineno} {f.name}"
                  for f in reversed(traceback.extract_tb(exc.__traceback__))))
    return True


def run(db, once: bool = False, idle_seconds: float = 2.0) -> int:
    """Process jobs; with `once`, until the queue is empty. Returns how many."""
    done = 0
    while True:
        if run_one(db):
            done += 1
            continue
        if once:
            return done
        time.sleep(idle_seconds)


def main() -> None:
    ap = argparse.ArgumentParser(description="Score queued ingest jobs.")
    ap.add_argument("--once", action="store_true", help="drain the queue and exit")
    args = ap.parse_args()
    # The API's own database handle and settings (.env, MONGO_URI, MONGO_DB).
    from api.main import db
    print(f"[worker] watching {db.name}.{JOBS}" + (" (once)" if args.once else ""))
    n = run(db, once=args.once)
    if args.once:
        print(f"[worker] {n} job(s) processed")


if __name__ == "__main__":
    main()

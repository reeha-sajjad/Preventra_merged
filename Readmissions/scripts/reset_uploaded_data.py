#!/usr/bin/env python3
"""
Remove everything one hospital's UPLOADS put in, for a clean demo or a fresh start.

Only what came in through the Data page, the ingest API or a database pull is
touched: the uploaded patients on the worklist, their scores, weekly records,
care-team and alert records, their patients' own check-ins, discharge plans and
recorded outcomes, the stored discharges and check-ins, the upload
history, and - unless kept - the hospital's Model Studio models and its saved
file reading. The demo patients (MIMIC-...), the hospital itself, its staff
accounts and the access log are left alone: the access log is the record of
who looked at what, and is not wiped with the data it describes.

    .venv/bin/python scripts/reset_uploaded_data.py --hospital central-hospital          # preview
    .venv/bin/python scripts/reset_uploaded_data.py --hospital central-hospital --yes    # delete

    --keep-model     keep the hospital's Model Studio models and what is deployed
    --keep-reading   keep the saved file reading and patient id prefix, so the
                     next upload is read the same way without asking

Writes to whichever database MONGO_URI / MONGO_DB point at: check it first.
"""
from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv

INGESTED = "ingest"          # the `source` of every worklist, registry and weekly row an upload wrote


def plan(db, hospital: str, keep_model: bool, keep_reading: bool) -> list:
    """[(collection, filter)] of everything to remove, in the order removed."""
    pids = sorted(set(db["ingest_stays"].distinct("patient_id", {"hospital_id": hospital}))
                  | set(db["patient_worklist"].distinct("patient_id", {"hospital_id": hospital,
                                                                       "source": INGESTED})))
    mine = {"patient_id": {"$in": pids}}
    scored = {"hospital_id": hospital, "source": INGESTED}
    steps = [("patient_worklist", scored), ("risk_registry", scored), ("weekly_monitoring", scored),
             ("care_actions", mine), ("clinical_alerts", mine), ("alerts", mine), ("followups", mine),
             ("notifications", mine), ("risk_watch_seen", mine),
             ("checkins", mine), ("discharge_plans", mine), ("readmission_outcomes", mine),
             ("ingest_stays", {"hospital_id": hospital}), ("ingest_weeks", {"hospital_id": hospital}),
             ("ingest_jobs", {"hospital_id": hospital}), ("ingest_batches", {"hospital_id": hospital}),
             ("ingest_raw", {"hospital_id": hospital}), ("ingest_drafts", {"hospital_id": hospital})]
    if not keep_model:
        steps += [("studio_deployments", {"hospital_id": hospital}), ("studio_jobs", {"hospital_id": hospital}),
                  ("studio_models", {"hospital_id": hospital})]
    if not keep_reading:
        steps += [("ingest_mappings", {"_id": hospital})]
    return steps


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.strip().split("\n\n")[0])
    ap.add_argument("--hospital", required=True, help="the hospital id, e.g. central-hospital")
    ap.add_argument("--yes", action="store_true", help="delete; without it, only report what would go")
    ap.add_argument("--keep-model", action="store_true")
    ap.add_argument("--keep-reading", action="store_true")
    a = ap.parse_args()

    load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env"))
    from api import model_studio, scoring_service
    from api.db_utils import get_db_name, get_mongo_client
    client = get_mongo_client(os.environ.get("MONGO_URI", "mongodb://localhost:27017"))
    db = client[get_db_name()]
    identity = client[os.environ.get("SHARED_IDENTITY_DB", "shared_identity")]
    hospital = identity["hospitals"].find_one({"_id": a.hospital})
    if not hospital:
        sys.exit(f"  No hospital '{a.hospital}' in {identity.name}.hospitals. Nothing changed.")

    print(f"  database : {db.name}   hospital: {hospital.get('name', a.hospital)} ({a.hospital})")
    steps = plan(db, a.hospital, a.keep_model, a.keep_reading)
    existing = set(db.list_collection_names())
    counts = [(c, q, db[c].count_documents(q) if c in existing else 0) for c, q in steps]
    for c, _, n in counts:
        if n:
            print(f"  {c:20s} {n:9,}")
    if not any(n for _, _, n in counts):
        print("  Nothing uploaded for this hospital. Nothing to do.")
        return
    if not a.yes:
        print("\n  Preview only - run again with --yes to delete these.")
        return

    batches = db["patient_worklist"].distinct("batch_date", {"hospital_id": a.hospital, "source": INGESTED})
    if not a.keep_model:
        # A saved model's file lives in GridFS beside its registry entry.
        for m in db["studio_models"].find({"hospital_id": a.hospital}, {"artifact_id": 1}):
            if m.get("artifact_id"):
                try:
                    model_studio._gridfs(db).delete(m["artifact_id"])
                except Exception:                                           # noqa: BLE001
                    pass
    for c, q, n in counts:
        if n:
            db[c].delete_many(q)
    # The executive summary counts the worklist by band; recount without them.
    for batch in batches:
        scoring_service._write_summary(db, batch)
    print("\n  Done. The patients are off the list; the next upload starts from scratch"
          + (" (the saved reading is kept)." if a.keep_reading else "."))


if __name__ == "__main__":
    main()

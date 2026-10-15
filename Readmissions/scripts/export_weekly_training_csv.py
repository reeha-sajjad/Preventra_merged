#!/usr/bin/env python3
"""
Export weekly monitoring as a CSV for training a weekly model in Model Studio.

READ-ONLY: this only reads `weekly_monitoring`; nothing in the database changes.

WHAT IT CONTAINS
----------------
One row per patient per week (0 = discharge, 1-4 = monitoring weeks), with the
nested fields flattened into columns:

    monitoring.*          -> weight_change_kg, adherence_pct, refill_status, ...
    discharge_baseline.*  -> baseline_dry_weight_kg, baseline_spo2, ... (week 0 row)
    red_flags (a list)    -> n_red_flags and red_flags ("; "-joined)

plus `next_week_risk_score`: the same patient's score one week later. That is
the outcome to choose in Model Studio's plan review, as "yes when >= 40" (the
app's High band) - a next-week early warning. Week 4 has no next week, so its
outcome is blank and Model Studio leaves it out of training.

WHAT IT DOES NOT DO
-------------------
No week-to-week changes and no yes/no column: Model Studio does both. With
patient_id as the identifier and week_date as the date, its pipeline builds
features from each patient's earlier weeks (and its look-ahead check refuses
any feature that uses a later one).

PLANTED LEAKS
-------------
By default three columns that give the answer away are included on purpose,
to see Model Studio's leakage handling flag them:

    next_week_risk_band   next week's band - "High" IS the outcome
    next_week_driver_1    the sentence explaining next week's score
    simulated_trajectory  the scenario the simulator used to generate the weeks

Pass --no-leaks for a clean file. The data is simulated, so a model trained on
it learns the simulator (see models/early_warning.py) - fine for testing, not
for clinical claims.

    .venv/bin/python scripts/export_weekly_training_csv.py
    .venv/bin/python scripts/export_weekly_training_csv.py --no-leaks --out /tmp/weekly.csv

A HOSPITAL'S OWN WEEKS (--hospital <id>)
----------------------------------------
The weeks that hospital sent through the ingest API, one row per monitoring
week, built by the same code the worker scores a deployed weekly model with
(features/weekly_rows.model_rows): the week's readings relative to discharge,
the discharge score, age and sex - and not the week's own score, which after a
weekly model is deployed is that model's answer. The outcome is
`readmitted_within_7d`: an unplanned admission in the 7 days after the week,
from the hospital's stored stays; blank while those 7 days have not passed.
Each series is rebuilt from ingest_weeks, so the weeks before a readmission
are included even after the new discharge replaced them on the dashboard.

    .venv/bin/python scripts/export_weekly_training_csv.py --hospital <hospital_id>

The default output is ~/Downloads, not the repository: rows carry MIMIC-derived
patient ids, or a hospital's patient records, which must not be committed.
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd
from dotenv import load_dotenv
from pymongo.errors import PyMongoError

from api.db_utils import get_db_name, get_mongo_client
from features.weekly_rows import flatten, model_rows, readmitted_within

COLLECTION = "weekly_monitoring"
DEFAULT_OUT = os.path.join(os.path.expanduser("~"), "Downloads", "weekly_monitoring_trainable.csv")
LEAKS = ("next_week_risk_band", "next_week_driver_1", "simulated_trajectory")


def fetch(db, attempts: int = 3) -> list:
    """All weekly documents. Atlas occasionally times out on a cold connection,
    so a failed read is retried a couple of times before giving up."""
    for attempt in range(1, attempts + 1):
        try:
            return list(db[COLLECTION].find({}, {"_id": 0}))
        except PyMongoError as exc:
            if attempt == attempts:
                raise SystemExit(f"Could not read {COLLECTION}: {exc}")
            print(f"Read failed ({type(exc).__name__}); retrying…")
            time.sleep(3 * attempt)


def add_next_week(flat: pd.DataFrame) -> pd.DataFrame:
    """The same patient's following week: the outcome, and the planted leaks."""
    flat = flat.sort_values(["patient_id", "week_number"]).reset_index(drop=True)
    following = flat.groupby("patient_id", sort=False)
    is_next = following["week_number"].shift(-1) == flat["week_number"] + 1
    for src, dst in (("risk_score", "next_week_risk_score"), ("risk_band", "next_week_risk_band"),
                     ("driver_1", "next_week_driver_1")):
        flat[dst] = following[src].shift(-1).where(is_next)
    return flat


def hospital_table(db, hospital_id: str, as_of=None) -> pd.DataFrame:
    """One row per monitoring week the hospital sent, with readmitted_within_7d."""
    from api import scoring_service
    as_of = pd.Timestamp(as_of) if as_of else pd.Timestamp.now()
    by_patient = defaultdict(list)
    for stay in db[scoring_service.STAYS].find({"hospital_id": hospital_id}, {"_id": 0}):
        by_patient[stay["patient_id"]].append(stay)
    frames = []
    for stays in by_patient.values():
        stays.sort(key=lambda st: pd.Timestamp(st["encounter"]["admit_datetime"]))
        for i, stay in enumerate(stays):
            week0 = scoring_service.week0_for(db, hospital_id, stay)
            docs = scoring_service.scored_weeks(db, hospital_id, stay, week0) if week0 else []
            if not docs:
                continue
            rows = model_rows([week0] + docs, stay).iloc[1:].copy()
            # The next admission, if unplanned, is the readmission - as for the
            # 30-day outcome (features/discharge_features.cohort).
            nxt = stays[i + 1]["encounter"] if i + 1 < len(stays) else None
            planned = nxt and (nxt.get("admission_type") == "ELECTIVE" or nxt.get("planned_admission"))
            readmit_at = nxt["admit_datetime"] if nxt and not planned else None
            rows["readmitted_within_7d"] = readmitted_within(rows["week_date"], readmit_at, 7, as_of).values
            frames.append(rows)
    if not frames:
        return pd.DataFrame()
    table = pd.concat(frames, ignore_index=True)
    first = ["patient_id", "week_number", "week_date", "readmitted_within_7d"]
    return table[first + [c for c in table.columns if c not in first]]


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", default=None)
    ap.add_argument("--no-leaks", action="store_true", help="leave the planted leak columns out")
    ap.add_argument("--hospital", default=None,
                    help="export this hospital's ingested weeks instead of weekly_monitoring")
    args = ap.parse_args()

    load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env"))
    db = get_mongo_client(os.environ["MONGO_URI"])[get_db_name()]
    if args.hospital:
        out = args.out or os.path.join(os.path.expanduser("~"), "Downloads",
                                       f"{args.hospital}_weekly_trainable.csv")
        table = hospital_table(db, args.hospital)
        if table.empty:
            raise SystemExit(f"No scored ingested weeks for hospital '{args.hospital}'.")
        os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
        table.to_csv(out, index=False)
        known = table["readmitted_within_7d"].notna()
        print(f"{len(table):,} weeks, {table['patient_id'].nunique():,} patients, {table.shape[1]} columns")
        print(f"Outcome known (trainable): {known.sum():,}; readmitted within 7 days: "
              f"{table.loc[known, 'readmitted_within_7d'].mean():.1%}" if known.any()
              else "No outcome is known yet.")
        print("In Model Studio: outcome readmitted_within_7d; patient identifier patient_id; "
              "date week_date.")
        print(f"Written to {os.path.abspath(out)}")
        return

    args.out = args.out or DEFAULT_OUT
    docs = fetch(db)
    if not docs:
        raise SystemExit(f"{COLLECTION} is empty.")

    table = add_next_week(flatten(docs))
    if args.no_leaks:
        table = table.drop(columns=list(LEAKS), errors="ignore")
    first = ["patient_id", "week_number", "week_date", "risk_score", "next_week_risk_score"]
    table = table[first + [c for c in table.columns if c not in first]]

    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    table.to_csv(args.out, index=False)

    labelled = table["next_week_risk_score"].notna()
    print(f"{len(table):,} rows, {table['patient_id'].nunique():,} patients, {table.shape[1]} columns")
    print(f"Rows with a next week (trainable): {labelled.sum():,}; next week High (>= 40): "
          f"{(table.loc[labelled, 'next_week_risk_score'] >= 40).mean():.1%}")
    print("Planted leak columns: " + ("none (--no-leaks)" if args.no_leaks else ", ".join(LEAKS)))
    print("In Model Studio: outcome next_week_risk_score, 'at or above a threshold' 40; "
          "patient identifier patient_id; date week_date.")
    print(f"Written to {os.path.abspath(args.out)}")


if __name__ == "__main__":
    main()

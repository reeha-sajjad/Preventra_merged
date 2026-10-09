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

The default output is ~/Downloads, not the repository: rows carry MIMIC-derived
patient ids, which must not be committed.
"""
from __future__ import annotations

import argparse
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd
from dotenv import load_dotenv
from pymongo.errors import PyMongoError

from api.db_utils import get_db_name, get_mongo_client

COLLECTION = "weekly_monitoring"
DEFAULT_OUT = os.path.join(os.path.expanduser("~"), "Downloads", "weekly_monitoring_trainable.csv")
# How a row was produced, not anything about the patient.
BOOKKEEPING = ("model_version", "scored_by", "source", "pinned_by", "group_evidence", "group_label")
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


def flatten(docs: list) -> pd.DataFrame:
    df = pd.DataFrame(docs)

    def nested(col, prefix=""):
        values = df[col] if col in df.columns else pd.Series([None] * len(df))
        part = pd.json_normalize(values.apply(lambda v: v if isinstance(v, dict) else {}).tolist())
        return part.rename(columns=lambda c: f"{prefix}{c}")

    signals = nested("monitoring")
    baseline = nested("discharge_baseline", "baseline_").drop(
        columns=["baseline_source", "baseline_group"], errors="ignore")
    baseline = baseline.rename(columns=lambda c: c.replace("baseline_baseline_", "baseline_"))
    flags = df.get("red_flags", pd.Series([[]] * len(df))).apply(
        lambda v: v if isinstance(v, list) else [])
    groups = df.get("monitoring_groups", pd.Series([[]] * len(df))).apply(
        lambda v: ",".join(map(str, v)) if isinstance(v, list) else "")

    flat = df.drop(columns=["monitoring", "discharge_baseline", "red_flags", "monitoring_groups",
                            *BOOKKEEPING], errors="ignore")
    flat = pd.concat([flat.reset_index(drop=True), signals, baseline], axis=1)
    flat["n_red_flags"] = flags.apply(len).values
    flat["red_flags"] = flags.apply(lambda v: "; ".join(map(str, v))).values
    flat["monitoring_groups"] = groups.values
    return flat


def add_next_week(flat: pd.DataFrame) -> pd.DataFrame:
    """The same patient's following week: the outcome, and the planted leaks."""
    flat = flat.sort_values(["patient_id", "week_number"]).reset_index(drop=True)
    following = flat.groupby("patient_id", sort=False)
    is_next = following["week_number"].shift(-1) == flat["week_number"] + 1
    for src, dst in (("risk_score", "next_week_risk_score"), ("risk_band", "next_week_risk_band"),
                     ("driver_1", "next_week_driver_1")):
        flat[dst] = following[src].shift(-1).where(is_next)
    return flat


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", default=DEFAULT_OUT)
    ap.add_argument("--no-leaks", action="store_true", help="leave the planted leak columns out")
    args = ap.parse_args()

    load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env"))
    db = get_mongo_client(os.environ["MONGO_URI"])[get_db_name()]
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

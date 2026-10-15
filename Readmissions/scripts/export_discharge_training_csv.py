#!/usr/bin/env python3
"""
Export a hospital's ingested discharges as a CSV for training a discharge model
in Model Studio.

READ-ONLY: this only reads `ingest_stays`; nothing in the database changes.

WHAT IT CONTAINS
----------------
One row per discharge that could be scored (deaths, hospice and observation
stays are left out, as in scoring), built by the same code the worker scores
with (ingestion/canonical.py -> features/discharge_features.assemble), so a
model trained on this file reads exactly the columns live scoring produces:

    patient_key           the patient - choose it as the identifier, so the
                          train/test split keeps each patient on one side
    discharge_datetime    the date - choose it as the time column
    readmit_30d           the outcome: an unplanned admission within 30 days.
                          Blank while a discharge's 30 days have not yet
                          passed; Model Studio leaves those rows out.
    ...                   the 94 inputs, in the built-in model's order

    .venv/bin/python scripts/export_discharge_training_csv.py --hospital <hospital_id>
    .venv/bin/python scripts/export_discharge_training_csv.py --hospital <id> --out /tmp/d.csv

The default output is ~/Downloads, not the repository: rows are a hospital's
patient records, which must never be committed.
"""
from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd
from dotenv import load_dotenv

from api.db_utils import get_db_name, get_mongo_client
from features.discharge_features import OUTCOME, feature_names
from ingestion import canonical

STAYS = "ingest_stays"


def table(db, hospital_id: str, as_of=None) -> pd.DataFrame:
    """The hospital's scorable discharges with their outcome as far as known."""
    stays = list(db[STAYS].find({"hospital_id": hospital_id}, {"_id": 0}))
    if not stays:
        return pd.DataFrame(columns=["patient_key", "discharge_datetime", OUTCOME] + feature_names())
    feats = canonical.features(stays, as_of=pd.Timestamp(as_of) if as_of else pd.Timestamp.now(),
                               include_excluded=False)
    return feats[["patient_key", "discharge_datetime", OUTCOME] + feature_names()].reset_index(drop=True)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--hospital", required=True, help="the hospital_id whose discharges to export")
    ap.add_argument("--out", default=None)
    args = ap.parse_args()
    out = args.out or os.path.join(os.path.expanduser("~"), "Downloads",
                                   f"{args.hospital}_discharges_trainable.csv")

    load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env"))
    db = get_mongo_client(os.environ["MONGO_URI"])[get_db_name()]
    frame = table(db, args.hospital)
    if frame.empty:
        raise SystemExit(f"No ingested discharges for hospital '{args.hospital}'.")

    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
    frame.to_csv(out, index=False)
    known = frame[OUTCOME].notna()
    print(f"{len(frame):,} discharges, {frame['patient_key'].nunique():,} patients, {frame.shape[1]} columns")
    print(f"Outcome known (trainable): {known.sum():,}; readmitted within 30 days: "
          f"{frame.loc[known, OUTCOME].mean():.1%}" if known.any() else "No outcome is known yet.")
    print("In Model Studio: outcome readmit_30d; patient identifier patient_key; "
          "date discharge_datetime.")
    print(f"Written to {os.path.abspath(out)}")


if __name__ == "__main__":
    main()

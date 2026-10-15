#!/usr/bin/env python3
"""
Denormalise each patient's weekly trend onto their worklist row.

WHY
---
/api/patients used to rebuild the whole picture on every request: read every
worklist row with no projection, aggregate the entire weekly_monitoring
collection, aggregate risk_registry, normalise all of it in Python, and only
then slice out the requested page. With 2,000 patients that took 27 seconds
against Atlas, and the dashboard rendered empty for all of it. The cost grew
with the cohort rather than with the page size, which is the wrong direction.

The trend summary changes only when the loader or the weekly simulator runs -
never between requests - so it belongs on the row rather than being recomputed.
This script writes it there and builds the indexes the endpoint queries on.

Run it after either of:
    python scripts/load_mimic_to_mongo.py
    python scripts/simulate_weekly_monitoring.py

It is idempotent: running it twice writes the same values.

FIELDS WRITTEN
--------------
    current_score       the latest weekly re-score, or the discharge score
    current_band        band of that score
    discharge_score     week 0
    trend_delta         current minus discharge, None with no weekly history
    monitoring_status   improving / stable / deteriorating / action_required
    weeks_tracked       how many points the series has
    summary_refreshed   when this row was last computed

Anything absent falls back inside the API, so a row this script has not reached
still renders - it simply shows the discharge score with no trend.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv

load_dotenv()

# The summary itself lives in api/worklist_summary.py, shared with the
# ingestion worker so a row refreshed either way carries the same fields.
from api.main import db, get_latest_batch_date  # noqa: E402
from api.worklist_summary import refresh, summaries  # noqa: E402


def main() -> None:
    batch_date = get_latest_batch_date(db, "patient_worklist")
    print(f"batch date : {batch_date}")
    found = summaries(db)
    print(f"weekly series with 2+ points : {len(found):,}")
    written = refresh(db, batch_date)
    print(f"\n  {written:,} worklist rows refreshed; {len(found):,} carry a real weekly trend, "
          f"the rest fall back to the discharge score")

    # Indexes for the queries /api/patients actually issues: newest batch,
    # sorted by current score, optionally filtered by condition or band.
    print("\nensuring indexes ...")
    for spec in (
        [("batch_date", 1), ("current_score", -1)],
        [("batch_date", 1), ("clinical_group", 1)],
        # Multikey - one entry per condition the patient has, so selecting
        # several conditions at once is still an index scan.
        [("batch_date", 1), ("clinical_groups", 1)],
        [("batch_date", 1), ("current_band", 1)],
        [("batch_date", 1), ("monitoring_status", 1)],
        [("batch_date", 1), ("status_rank", -1)],
        [("batch_date", 1), ("band_rank", -1)],
        [("batch_date", 1), ("trend_delta", -1)],
        [("batch_date", 1), ("discharge_date", -1)],
        [("batch_date", 1), ("primary_diagnosis", 1)],
        [("patient_id", 1)],
    ):
        name = db["patient_worklist"].create_index(spec)
        print(f"  {name}")

    print("\ndone")


if __name__ == "__main__":
    main()

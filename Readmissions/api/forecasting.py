"""
The early-warning forecast's inputs, and raising alerts from it.

Kept out of api/main.py so the worker can raise alerts too: the forecast scan
used to run only when a superadmin pressed it, so a red flag in a week that
arrived by upload waited, unalerted, for someone to remember the button.

    ForecastContext   the weeks, worklist rows and care teams a forecast reads
    raise_alerts      forecast patients and raise or update their alerts
"""
from __future__ import annotations

import json
import os
from typing import Optional

from api import access, doctor_service
from api.db_utils import get_latest_batch_date
from models import early_warning

WEEKLY_COLLECTION = "weekly_monitoring"
_BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


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


def mimic_scoring_bands() -> dict:
    """Band edges, read from the same file the scorer uses so they cannot drift."""
    try:
        with open(os.path.join(_BASE, "docs", "mimic", "band_thresholds_mimic.json")) as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return early_warning.DEFAULT_BANDS


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
                                             {"_id": 0, "patient_id": 1, "hospital_id": 1,
                                              "assigned_doctor_id": 1})}
        self.bands = mimic_scoring_bands()

    def for_patient(self, patient_id: str) -> tuple:
        key = str(patient_id)
        row = self.rows.get(key) or {}
        care = self.care.get(key) or {}
        result = early_warning.forecast(
            self.weeks.get(key, []), group=_patient_group(row), bands=self.bands)
        meta = {
            "group_label": row.get("group_label"),
            "primary_diagnosis": row.get("primary_diagnosis"),
            "anchor_age": row.get("anchor_age"),
            "gender": row.get("gender"),
            "discharge_date": row.get("discharge_date"),
            # Routing stays inside the patient's hospital (doctor_service.route).
            "hospital_id": care.get("hospital_id"),
        }
        return result, meta, care.get("assigned_doctor_id")


def raise_alerts(db, patient_ids: Optional[list] = None, limit: Optional[int] = None,
                 progress=None) -> dict:
    """
    Forecast these patients - or everyone being monitored - and raise or update
    their alerts. Run after weekly readings are scored, every night, and when a
    superadmin asks; a repeat for the same week updates rather than duplicates.
    """
    ids = None if patient_ids is None else access.id_variants(list(patient_ids))
    ctx = ForecastContext(db, patient_ids=ids)
    return doctor_service.scan_cohort(db, ctx.for_patient, limit=limit, progress=progress,
                                      patient_ids=None if patient_ids is None
                                      else [str(p) for p in dict.fromkeys(patient_ids)])

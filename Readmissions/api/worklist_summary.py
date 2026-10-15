"""
Each patient's current score, band, trend and primary driver, copied onto their
worklist row so the worklist can sort and page in MongoDB.

Shared by scripts/refresh_worklist_summary.py (every patient, after a reload)
and the ingestion worker (only the patients a job touched), so a row written
either way carries the same fields. See the script for why these are stored
rather than computed per request.
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from pymongo import UpdateOne

WEEKLY_COLLECTION = "weekly_monitoring"

# Sort keys. The worklist sorts by trend severity and by risk band, neither of
# which orders correctly as a string - "High" < "Low" < "Medium" alphabetically.
STATUS_RANK = {"action_required": 4, "deteriorating": 3, "stable": 2,
               "improving": 1, "insufficient_data": 0}
BAND_RANK = {"High": 3, "Medium": 2, "Low": 1}


def summaries(db, patient_ids: Optional[list] = None) -> dict:
    """{patient_id: summary} from the weekly monitoring series."""
    from api.main import _classify_trend_status        # one definition, shared with the API
    from api.access import id_variants
    pipeline = ([{"$match": {"patient_id": {"$in": id_variants(patient_ids)}}}] if patient_ids is not None
                else []) + [
        {"$sort": {"week_number": 1}},
        {"$group": {"_id": "$patient_id", "scores": {"$push": "$risk_score"},
                    "bands": {"$push": "$risk_band"}, "flags": {"$push": "$red_flags"}}},
    ]
    out = {}
    for doc in db[WEEKLY_COLLECTION].aggregate(pipeline):
        scores = []
        for raw in doc.get("scores", []):
            try:
                scores.append(float(raw))
            except (TypeError, ValueError):
                continue
        if len(scores) < 2:
            continue
        bands = doc.get("bands") or []
        gaps = [None] + [7] * (len(scores) - 1)
        out[str(doc["_id"])] = {
            "discharge_score": scores[0],
            "current_score": scores[-1],
            "current_band": bands[-1] if bands else None,
            "trend_delta": round(scores[-1] - scores[0], 1),
            # A red flag in the latest week overrides the trend: see
            # models/monitoring_rules.RED_FLAGS.
            "monitoring_status": ("action_required" if (doc.get("flags") or [None])[-1]
                                  else _classify_trend_status(scores, gaps)),
            "weeks_tracked": len(scores),
        }
    return out


def fields_for(row: dict, summary: Optional[dict]) -> dict:
    """The fields written onto one worklist row."""
    from api.main import _worklist_driver
    pid = str(row.get("patient_id", ""))
    if summary:
        fields = dict(summary)
        if not fields.get("current_band"):
            fields["current_band"] = row.get("risk_band", "Low")
    else:
        # No weekly history: current IS the discharge score, and the status
        # says so rather than implying a flat trend was observed.
        score = float(row.get("risk_score", 0) or 0)
        fields = {"discharge_score": score, "current_score": score,
                  "current_band": row.get("risk_band", "Low"), "trend_delta": None,
                  "monitoring_status": "insufficient_data", "weeks_tracked": 1}
    fields["primary_driver_label"] = _worklist_driver(pid, row)
    fields["status_rank"] = STATUS_RANK.get(fields["monitoring_status"], 0)
    fields["band_rank"] = BAND_RANK.get(fields.get("current_band"), 0)
    fields["summary_refreshed"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    return fields


def refresh(db, batch_date: str, patient_ids: Optional[list] = None) -> int:
    """Rewrite the summary fields of the given patients' rows in this batch
    (every row when patient_ids is None). Returns how many rows it wrote."""
    from api.access import id_variants
    query = {"batch_date": batch_date}
    if patient_ids is not None:
        query["patient_id"] = {"$in": id_variants(patient_ids)}
    rows = list(db["patient_worklist"].find(
        query, {"_id": 0, "patient_id": 1, "risk_score": 1, "risk_band": 1,
                "driver_1": 1, "driver_2": 1, "driver_3": 1}))
    found = summaries(db, [r["patient_id"] for r in rows] if patient_ids is not None else None)
    ops = [UpdateOne({"patient_id": r["patient_id"], "batch_date": batch_date},
                     {"$set": fields_for(r, found.get(str(r["patient_id"])))}) for r in rows]
    for i in range(0, len(ops), 1000):
        db["patient_worklist"].bulk_write(ops[i:i + 1000], ordered=False)
    return len(ops)

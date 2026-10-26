"""
Predefined, parameterized MongoDB query functions backing the dashboard chatbot.

These are the only operations the chatbot is allowed to perform against
`patient_worklist`. The LLM never generates query syntax directly — it only
selects one of these functions (via Gemini tool/function calling) and the
arguments to call it with. Each function validates its own inputs and raises
ValueError/LookupError on bad input so the caller can turn that into a clean
user-facing message.
"""

import re
import time
from collections import defaultdict
from datetime import datetime, timedelta
from typing import Optional

from pymongo import DESCENDING

from api import access
from api.db_utils import get_latest_batch_date
# Collection names and the condition->specialty map live with the clinician
# code. doctor_service imports this module only inside functions, so importing
# its constants here does not create a cycle.
from api.doctor_service import CLINICAL_ALERTS, DOCTORS, SPECIALTY_FOR_GROUP

VALID_RISK_BANDS = ("low", "medium", "high")

_OPERATOR_TO_MONGO = {
    "gt": "$gt",
    "gte": "$gte",
    "lt": "$lt",
    "lte": "$lte",
    "eq": "$eq",
}

# A chatbot query that has not returned in five seconds will not be waited for
# anyway - the Gemini call around it times out at fifteen. Bounding it at the
# server means an accidental collection scan is killed there rather than left
# holding a connection from the same small pool the dashboard is using.
MAX_QUERY_MS = 5000

LIST_DEFAULT_LIMIT = 50
LIST_MAX_LIMIT = 200
TOP_N_MAX = 100


def _latest_batch_or_raise(db) -> str:
    batch_date = get_latest_batch_date(db, "patient_worklist")
    if not batch_date:
        raise LookupError("No patient worklist data is available.")
    return batch_date


def _current_score_dates(db, patient_ids) -> dict:
    """patient_id (as a string) -> the date of the weekly reading their current
    score comes from. "Risk score 96.0" said neither which score nor when; the
    current one is the latest week's, and this is that week's date."""
    latest: dict = {}
    for w in db["weekly_monitoring"].find(
            {"patient_id": {"$in": access.id_variants(list(patient_ids))}},
            {"_id": 0, "patient_id": 1, "week_number": 1, "week_date": 1}):
        key = str(w.get("patient_id"))
        if w.get("week_date") and w.get("week_number", -1) >= latest.get(key, (-1, None))[0]:
            latest[key] = (w.get("week_number", -1), w["week_date"])
    return {k: v[1] for k, v in latest.items()}


def _patient_id_filter(patient_id: str) -> dict:
    """patient_id is stored as an int in MongoDB, but callers (including the LLM)
    always pass it as a string. Match either representation."""
    candidates = [patient_id]
    try:
        candidates.append(int(patient_id))
    except (TypeError, ValueError):
        pass
    return {"patient_id": {"$in": candidates}}


def count_patients_by_risk_band(db, risk_band: Optional[str] = None) -> dict:
    """Counts patients grouped by risk_band, or the count for a single band."""
    batch_date = _latest_batch_or_raise(db)

    if risk_band is not None:
        band = risk_band.strip().lower()
        if band not in VALID_RISK_BANDS:
            raise ValueError(f"risk_band must be one of {VALID_RISK_BANDS}, got '{risk_band}'.")

    match: dict = {"batch_date": batch_date}
    if risk_band is not None:
        # risk_band is stored with inconsistent casing in production data
        # (e.g. "High" vs "high"), so match case-insensitively.
        match["current_band"] = {"$regex": f"^{band}$", "$options": "i"}

    pipeline = [
        {"$match": match},
        {"$group": {"_id": "$current_band", "count": {"$sum": 1}}},
    ]
    rows = list(db["patient_worklist"].aggregate(pipeline, maxTimeMS=MAX_QUERY_MS))

    counts = {band: 0 for band in VALID_RISK_BANDS}
    for row in rows:
        band_name = str(row.get("_id", "")).strip().lower()
        if band_name in counts:
            counts[band_name] = row["count"]

    if risk_band is not None:
        return {"risk_band": band, "count": counts[band]}

    return {"counts": counts, "total": sum(counts.values())}


def list_patients_by_risk_threshold(
    db, operator: str, threshold: float, limit: Optional[int] = None
) -> list:
    """Returns patient_id/risk_score pairs matching a risk_score comparison, sorted descending."""
    if operator not in _OPERATOR_TO_MONGO:
        raise ValueError(f"operator must be one of {tuple(_OPERATOR_TO_MONGO)}, got '{operator}'.")

    resolved_limit = LIST_DEFAULT_LIMIT if limit is None else limit
    if resolved_limit <= 0:
        raise ValueError("limit must be a positive integer.")
    resolved_limit = min(resolved_limit, LIST_MAX_LIMIT)

    batch_date = _latest_batch_or_raise(db)

    cursor = (
        db["patient_worklist"]
        .find(
            {"batch_date": batch_date, "current_score": {_OPERATOR_TO_MONGO[operator]: threshold}},
            {"_id": 0, "patient_id": 1, "current_score": 1},
        )
        .sort("current_score", DESCENDING)
        .limit(resolved_limit)
    )

    return [
        {"patient_id": row.get("patient_id"), "current risk score": row.get("current_score")}
        for row in cursor
    ]


def get_top_risk_patients(db, n: int) -> list:
    """Returns the top n patients by risk_score descending, capped at TOP_N_MAX."""
    if n <= 0:
        raise ValueError("n must be a positive integer.")
    n = min(n, TOP_N_MAX)

    batch_date = _latest_batch_or_raise(db)

    cursor = (
        db["patient_worklist"]
        .find(
            {"batch_date": batch_date},
            {"_id": 0, "patient_id": 1, "current_score": 1, "current_band": 1,
             "discharge_score": 1, "risk_score": 1, "discharge_date": 1},
        )
        .sort("current_score", DESCENDING)
        .limit(n)
    )
    rows = list(cursor)
    dates = _current_score_dates(db, [r.get("patient_id") for r in rows])
    return [
        {
            "patient_id": row.get("patient_id"),
            "current risk score": row.get("current_score"),
            "current band": row.get("current_band"),
            "current score as of": dates.get(str(row.get("patient_id"))),
            "risk score at discharge": row.get("discharge_score", row.get("risk_score")),
            "discharge date": row.get("discharge_date"),
        }
        for row in rows
    ]


def get_patient_drivers(db, patient_id: str) -> dict:
    """Returns risk_score, risk_band, and the three top drivers for one patient."""
    batch_date = _latest_batch_or_raise(db)

    row = db["patient_worklist"].find_one(
        {**_patient_id_filter(patient_id), "batch_date": batch_date},
        {
            "_id": 0,
            "patient_id": 1,
            "current_score": 1,
            "current_band": 1,
            "driver_1": 1,
            "driver_2": 1,
            "driver_3": 1,
        },
    )
    if not row:
        raise LookupError(f"No patient found with patient_id '{patient_id}'.")

    return {
        "patient_id": row.get("patient_id"),
        "current risk score": row.get("current_score"),
        "current band": row.get("current_band"),
        "current score as of": _current_score_dates(db, [row.get("patient_id")]).get(
            str(row.get("patient_id"))),
        "driver_1": row.get("driver_1"),
        "driver_2": row.get("driver_2"),
        "driver_3": row.get("driver_3"),
    }


def get_patient_details(db, patient_id: str) -> dict:
    """Returns the full patient_worklist document for one patient_id, with the
    patient's care team - the assigned doctor and nurses - as the patient list
    shows it. The care team lives in care_actions, not on the worklist row, so
    without it "who is this patient's doctor?" had no answer."""
    from api import hospital
    batch_date = _latest_batch_or_raise(db)

    row = db["patient_worklist"].find_one(
        {**_patient_id_filter(patient_id), "batch_date": batch_date},
        {"_id": 0},
    )
    if not row:
        raise LookupError(f"No patient found with patient_id '{patient_id}'.")
    # Every condition the patient has, by the names the screen uses. As keys
    # ("cardiac_other") the answer to "what other conditions do they have"
    # passed them over for the secondary diagnoses.
    row["conditions"] = [GROUP_LABELS.get(g, g) for g in row.pop("clinical_groups", None) or []]
    # Two scores, named apart: on the row they were risk_score (at discharge)
    # beside current_score, and an answer quoted whichever it saw first.
    at_discharge = row.pop("discharge_score", None)
    row["risk score at discharge"] = at_discharge if at_discharge is not None else row.get("risk_score")
    row["band at discharge"] = row.pop("risk_band", None)
    row.pop("risk_score", None)
    row["current risk score"] = row.pop("current_score", None)
    row["current band"] = row.pop("current_band", None)
    row["current score as of"] = _current_score_dates(db, [row["patient_id"]]).get(str(row["patient_id"]))
    row["monitored_as"] = row.pop("group_label", None)
    row.pop("clinical_group", None)            # the key of monitored_as

    try:
        team = hospital.care_teams(db, [row["patient_id"]]).get(str(row["patient_id"])) or {}
    except Exception as exc:                                             # noqa: BLE001
        print(f"[chatbot] could not read the care team for {patient_id}: {exc}")
        team = {}
    doctor = team.get("doctor")
    row["care_team"] = {
        "doctor": (f"{doctor['name']}" + (f" ({doctor['specialty']})" if doctor.get("specialty") else ""))
                  if doctor else "no doctor assigned",
        "nurses": [n.get("name") or n.get("email") for n in team.get("nurses") or []] or "no nurse assigned",
    }
    return row


def _period_label(batch_date_str: str, group_by: str) -> str:
    dt = datetime.strptime(batch_date_str, "%Y-%m-%d")
    if group_by == "day":
        return dt.strftime("%Y-%m-%d")
    if group_by == "week":
        week_start = dt - timedelta(days=dt.weekday())
        return week_start.strftime("%Y-%m-%d")
    if group_by == "month":
        return dt.strftime("%Y-%m")
    raise ValueError(f"group_by must be one of ('day', 'week', 'month'), got '{group_by}'.")


def get_risk_trend_over_time(
    db,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    group_by: str = "week",
) -> list:
    """Returns counts per risk_band bucketed by batch_date over the given range."""
    if group_by not in ("day", "week", "month"):
        raise ValueError(f"group_by must be one of ('day', 'week', 'month'), got '{group_by}'.")

    match: dict = {}
    date_filter: dict = {}
    if start_date:
        date_filter["$gte"] = start_date
    if end_date:
        date_filter["$lte"] = end_date
    if date_filter:
        match["batch_date"] = date_filter

    cursor = db["patient_worklist"].find(
        match, {"_id": 0, "batch_date": 1, "current_band": 1}
    )

    buckets: dict = defaultdict(lambda: {band: 0 for band in VALID_RISK_BANDS})
    for row in cursor:
        batch_date_str = row.get("batch_date")
        band = str(row.get("current_band", "")).strip().lower()
        if not batch_date_str or band not in VALID_RISK_BANDS:
            continue
        try:
            label = _period_label(batch_date_str, group_by)
        except ValueError:
            continue
        buckets[label][band] += 1

    return [
        {"period": label, **counts}
        for label, counts in sorted(buckets.items())
    ]


# ---------------------------------------------------------------------------
# Cohort analytics
# ---------------------------------------------------------------------------
# The functions above answer questions about ONE patient, or list patients.
# These answer questions about a GROUP: how many, what conditions, which
# drivers. They all share one filter so "patients above 80" means the same
# thing whichever question is asked about them.
#
# Two things to know about the cohort they run over:
#   - `current_score` is the patient's risk now, including any weekly movement
#     since discharge. It is the number the worklist shows.
#   - Rows without it (patients added by hand rather than loaded from the
#     cohort) fall out of every filter below, which is the behaviour we want.

from models.icd_groups import GROUP_LABELS  # noqa: E402

# Below this many patients an aggregate stops being an aggregate. "The most
# common diagnosis among the 2 patients above 95%" names those patients'
# diagnoses, which is a disclosure dressed as a statistic. Standard small-cell
# suppression, and it applies to every function in this section.
MIN_COHORT_FOR_AGGREGATE = 5

TOP_N_DEFAULT = 5
TOP_N_LIMIT = 20
SCORE_MIN, SCORE_MAX = 0.0, 100.0


def _cohort_filter(db, band: Optional[str] = None,
                   min_score: Optional[float] = None,
                   max_score: Optional[float] = None,
                   filters: Optional[list] = None) -> tuple:
    """
    Build the MongoDB match for a cohort question, and a plain-English
    description of it so the answer can restate what was actually counted.

    `filters` are run_cohort_query's {field, op, value}. Without them "drivers
    for patients who went from Low to High" had only a score range to reach
    for, and the answer named the wrong group - everyone above 70.
    """
    batch_date = _latest_batch_or_raise(db)
    match: dict = {"batch_date": batch_date}
    described = []

    if band is not None:
        b = str(band).strip().lower()
        if b not in VALID_RISK_BANDS:
            raise ValueError(f"band must be one of {VALID_RISK_BANDS}, got '{band}'.")
        match["current_band"] = {"$regex": f"^{b}$", "$options": "i"}
        described.append(f"in the {b} risk band")

    score: dict = {}
    for value, key, word in ((min_score, "$gte", "at or above"),
                             (max_score, "$lte", "at or below")):
        if value is None:
            continue
        value = float(value)
        if not SCORE_MIN <= value <= SCORE_MAX:
            raise ValueError(f"score must be between {SCORE_MIN:g} and {SCORE_MAX:g}, got {value:g}.")
        score[key] = value
        described.append(f"{word} {value:g}%")
    if score:
        match["current_score"] = score
    if filters:
        extra, more = _validate_filters(db, filters)
        match["$and"] = [extra]          # batch_date stays a top-level key; callers read it
        described += more
        if _names_a_diagnosis(filters) and db["patient_worklist"].count_documents(match) == 0:
            _no_such_diagnosis(filters)

    return match, (" and ".join(described) if described else "in the current batch")


def _capped_top_n(top_n: Optional[int]) -> int:
    n = TOP_N_DEFAULT if top_n is None else int(top_n)
    if n <= 0:
        raise ValueError("top_n must be a positive integer.")
    return min(n, TOP_N_LIMIT)


def count_patients(db, band: Optional[str] = None,
                   min_score: Optional[float] = None,
                   max_score: Optional[float] = None,
                   filters: Optional[list] = None) -> dict:
    """
    How many patients match a cohort filter.

    Deliberately separate from list_patients_by_risk_threshold, which caps its
    result at 200 rows: asking that function "how many patients score above 60"
    and counting what came back would answer 200 no matter the truth.
    """
    match, described = _cohort_filter(db, band, min_score, max_score, filters)
    count = db["patient_worklist"].count_documents(match)
    total = db["patient_worklist"].count_documents({"batch_date": match["batch_date"]})

    return {
        "count": count,
        "cohort_total": total,
        "percent_of_total": round(count / total * 100, 1) if total else 0.0,
        "filter": described,
    }


def _aggregate_or_suppress(db, match: dict, described: str, pipeline_tail: list,
                           label_key: str) -> dict:
    """Run a cohort aggregate, refusing when the cohort is too small to be one."""
    cohort_size = db["patient_worklist"].count_documents(match)
    if cohort_size == 0:
        return {"cohort_size": 0, "filter": described, "results": [],
                "note": "No patients match that filter."}
    if cohort_size < MIN_COHORT_FOR_AGGREGATE:
        return {"cohort_size": cohort_size, "filter": described, "results": [],
                "note": (f"Only {cohort_size} patient{'s' if cohort_size != 1 else ''} "
                         f"match{'' if cohort_size != 1 else 'es'} that filter — too few to "
                         f"summarise without effectively identifying them. Widen the filter.")}

    rows = list(db["patient_worklist"].aggregate([{"$match": match}] + pipeline_tail, maxTimeMS=MAX_QUERY_MS))
    return {
        "cohort_size": cohort_size,
        "filter": described,
        "results": [{label_key: r["_id"], "patients": r["count"],
                     "percent_of_cohort": round(r["count"] / cohort_size * 100, 1)}
                    for r in rows if r.get("_id")],
    }


def get_common_conditions(db, band: Optional[str] = None,
                          min_score: Optional[float] = None,
                          max_score: Optional[float] = None,
                          filters: Optional[list] = None,
                          top_n: Optional[int] = None) -> dict:
    """
    The most common monitoring conditions in a cohort.

    Counts membership, not primary group: a patient with both heart failure and
    diabetes counts toward both, so percentages sum above 100. That is the
    honest reading - 22% of patients carry more than one condition.
    """
    match, described = _cohort_filter(db, band, min_score, max_score, filters)
    n = _capped_top_n(top_n)
    out = _aggregate_or_suppress(db, match, described, [
        {"$unwind": "$clinical_groups"},
        {"$group": {"_id": "$clinical_groups", "count": {"$sum": 1}}},
        {"$sort": {"count": DESCENDING}},
        {"$limit": n},
    ], "condition")
    for row in out["results"]:
        row["condition"] = GROUP_LABELS.get(row["condition"], row["condition"])
    out["counts"] = "condition membership; patients with several conditions count once per condition"
    return out


def get_common_diagnoses(db, band: Optional[str] = None,
                         min_score: Optional[float] = None,
                         max_score: Optional[float] = None,
                         filters: Optional[list] = None,
                         top_n: Optional[int] = None) -> dict:
    """
    The most common principal diagnoses in a cohort, by coded diagnosis text.

    This is what each stay was admitted for. It is narrower than
    get_common_conditions: MIMIC codes thousands of distinct diagnoses, so the
    leader here is usually a few percent rather than a majority.
    """
    match, described = _cohort_filter(db, band, min_score, max_score, filters)
    n = _capped_top_n(top_n)
    return _aggregate_or_suppress(db, match, described, [
        {"$group": {"_id": "$primary_diagnosis", "count": {"$sum": 1}}},
        {"$sort": {"count": DESCENDING}},
        {"$limit": n},
    ], "diagnosis")


def get_common_drivers(db, band: Optional[str] = None,
                       min_score: Optional[float] = None,
                       max_score: Optional[float] = None,
                       filters: Optional[list] = None,
                       top_n: Optional[int] = None) -> dict:
    """
    The risk factors that recur most across a cohort.

    Each patient carries three ranked drivers, stored as a sentence like
    "Sodium, last value before discharge: 126 (below the normal range...)".
    Only the part before the colon is the factor; the rest is this patient's
    value and is not comparable across patients, so it is dropped here.
    """
    match, described = _cohort_filter(db, band, min_score, max_score, filters)
    n = _capped_top_n(top_n)

    cohort_size = db["patient_worklist"].count_documents(match)
    if cohort_size == 0:
        return {"cohort_size": 0, "filter": described, "results": [],
                "note": "No patients match that filter."}
    if cohort_size < MIN_COHORT_FOR_AGGREGATE:
        return {"cohort_size": cohort_size, "filter": described, "results": [],
                "note": (f"Only {cohort_size} patient{'s' if cohort_size != 1 else ''} "
                         f"match{'' if cohort_size != 1 else 'es'} that filter — too few to "
                         f"summarise without effectively identifying them. Widen the filter.")}

    counts: dict = {}
    cursor = db["patient_worklist"].find(
        match, {"_id": 0, "driver_1": 1, "driver_2": 1, "driver_3": 1})
    for row in cursor:
        # A factor is counted once per patient even if it appears in two of
        # their three driver slots, so a percentage means "share of patients".
        seen = set()
        for col in ("driver_1", "driver_2", "driver_3"):
            raw = str(row.get(col) or "").strip()
            if not raw:
                continue
            label = raw.split(": ", 1)[0].strip() if ": " in raw else raw
            if label and label not in seen:
                seen.add(label)
                counts[label] = counts.get(label, 0) + 1

    ranked = sorted(counts.items(), key=lambda kv: kv[1], reverse=True)[:n]
    return {
        "cohort_size": cohort_size,
        "filter": described,
        "results": [{"driver": label, "patients": c,
                     "percent_of_cohort": round(c / cohort_size * 100, 1)}
                    for label, c in ranked],
    }


def get_condition_overlap(db, band: Optional[str] = None,
                          min_score: Optional[float] = None,
                          max_score: Optional[float] = None,
                          filters: Optional[list] = None,
                          top_n: Optional[int] = None) -> dict:
    """
    How many patients carry more than one condition, and which conditions
    co-occur most often.

    Two questions in one function because they are always asked together and a
    pair count is meaningless without knowing how many patients have pairs at
    all. Pairs are unordered and de-duplicated per patient, so a patient with
    three conditions contributes three pairs, not six.
    """
    from itertools import combinations

    match, described = _cohort_filter(db, band, min_score, max_score, filters)
    n = _capped_top_n(top_n)

    cohort_size = db["patient_worklist"].count_documents(match)
    if cohort_size == 0:
        return {"cohort_size": 0, "filter": described, "results": [],
                "note": "No patients match that filter."}
    if cohort_size < MIN_COHORT_FOR_AGGREGATE:
        return {"cohort_size": cohort_size, "filter": described, "results": [],
                "note": (f"Only {cohort_size} patient{'s' if cohort_size != 1 else ''} "
                         f"match{'' if cohort_size != 1 else 'es'} that filter — too few to "
                         f"summarise without effectively identifying them. Widen the filter.")}

    pairs: dict = {}
    with_multiple = 0
    for row in db["patient_worklist"].find(match, {"_id": 0, "clinical_groups": 1}):
        groups = sorted(set(row.get("clinical_groups") or []))
        if len(groups) < 2:
            continue
        with_multiple += 1
        for a, b in combinations(groups, 2):
            pairs[(a, b)] = pairs.get((a, b), 0) + 1

    ranked = sorted(pairs.items(), key=lambda kv: kv[1], reverse=True)[:n]
    return {
        "cohort_size": cohort_size,
        "filter": described,
        "patients_with_multiple_conditions": with_multiple,
        "percent_with_multiple": round(with_multiple / cohort_size * 100, 1),
        "results": [
            {"conditions": f"{GROUP_LABELS.get(a, a)} + {GROUP_LABELS.get(b, b)}",
             "patients": count,
             "percent_of_cohort": round(count / cohort_size * 100, 1)}
            for (a, b), count in ranked
        ],
    }


# How far a score has to move before it is worth a coordinator's attention.
# Matches the product's risk bands, which are 20 points wide: a swing smaller
# than that cannot move a patient between bands and is noise for this purpose.
SHARP_CHANGE_POINTS = 20.0


def get_risk_change_since_discharge(db, band: Optional[str] = None,
                                    min_change: Optional[float] = None,
                                    direction: str = "increase",
                                    filters: Optional[list] = None) -> dict:
    """
    How many patients have moved materially since discharge, and by how much.

    `trend_delta` is current score minus discharge score, already stored on the
    row, so this is a count rather than a scan. Default threshold is a full
    band width - see SHARP_CHANGE_POINTS.
    """
    if direction not in ("increase", "decrease", "any"):
        raise ValueError("direction must be 'increase', 'decrease' or 'any'.")

    threshold = SHARP_CHANGE_POINTS if min_change is None else abs(float(min_change))
    if not 0 <= threshold <= SCORE_MAX:
        raise ValueError(f"min_change must be between 0 and {SCORE_MAX:g}.")

    match, described = _cohort_filter(db, band=band, filters=filters)
    match["trend_delta"] = (
        {"$gte": threshold} if direction == "increase" else
        {"$lte": -threshold} if direction == "decrease" else
        {"$not": {"$gt": -threshold, "$lt": threshold}})

    cohort_match = {k: v for k, v in match.items() if k != "trend_delta"}
    cohort_size = db["patient_worklist"].count_documents(cohort_match)
    count = db["patient_worklist"].count_documents(match)

    worded = {"increase": "rose", "decrease": "fell", "any": "moved"}[direction]
    return {
        "count": count,
        "cohort_size": cohort_size,
        "percent_of_cohort": round(count / cohort_size * 100, 1) if cohort_size else 0.0,
        "threshold_points": threshold,
        "direction": direction,
        "filter": described,
        "means": (f"patients whose risk {worded} by at least {threshold:g} points "
                  f"between discharge and now"),
    }


# ---------------------------------------------------------------------------
# Dynamic cohort queries
# ---------------------------------------------------------------------------
# The functions above each answer one shape of question, which means a question
# nobody anticipated gets refused even when the data is sitting right there.
# This section replaces "pick one of N fixed questions" with "describe the
# query you want", while keeping the property that makes the whole design safe:
#
#   the model never writes query syntax. It fills in a structured spec, and the
#   spec is validated field by field and compiled into a pipeline here.
#
# What that buys: filter on anything in FIELDS, group by anything groupable,
# aggregate anything numeric, in any combination. What it refuses: a field not
# on the list, an operator not on the list, a bucket that makes no sense for the
# field's type, and any cohort small enough that the answer would identify
# someone.

class Field:
    """One queryable column and what may legitimately be done to it."""

    def __init__(self, kind: str, label: str, groupable: bool = True,
                 aggregatable: bool = False, array: bool = False,
                 values: Optional[tuple] = None, stored: bool = True):
        self.kind = kind                  # "number" | "string"
        self.label = label                # how to name it back to the user
        self.groupable = groupable
        self.aggregatable = aggregatable  # avg/min/max only make sense on some
        self.array = array                # needs $unwind before grouping
        self.values = values              # closed vocabulary, if there is one
        self.stored = stored              # False: not a worklist column (see DOCTOR)


FIELDS = {
    "current_score":     Field("number", "current risk score", aggregatable=True),
    "discharge_score":   Field("number", "risk score at discharge", aggregatable=True),
    "trend_delta":       Field("number", "change in risk since discharge", aggregatable=True),
    "anchor_age":        Field("number", "age", aggregatable=True),
    "los_days":          Field("number", "length of stay in days", aggregatable=True),
    "n_prior_adm":       Field("number", "prior admissions", aggregatable=True),
    "n_diagnoses_coded": Field("number", "diagnoses coded", aggregatable=True),
    "weeks_tracked":     Field("number", "weeks monitored", aggregatable=True),
    "current_band":      Field("string", "current risk band", values=("Low", "Medium", "High")),
    "risk_band":         Field("string", "risk band at discharge", values=("Low", "Medium", "High")),
    "gender":            Field("string", "sex", values=("F", "M")),
    # Two different questions, and they gave two different answers. A patient
    # HAS every condition in clinical_groups - that is what the patient list's
    # Condition filter matches - and is MONITORED AS one of them (group_label).
    # Labelled "primary condition", group_label was the one the model reached
    # for on "patients with heart disease", and answered for 271 patients where
    # the list shows 547, missing the highest-risk one because their weekly plan
    # follows heart failure.
    "group_label":       Field("string", "condition monitored as"),
    "clinical_groups":   Field("string", "condition", array=True),
    "primary_diagnosis": Field("string", "principal diagnosis"),
    "monitoring_status": Field("string", "monitoring status"),
    "group_confidence":  Field("string", "how the condition was matched"),
    # The care team lives in care_actions, not on the worklist row, so this one
    # is resolved separately (_doctor_condition, _doctor_names) - but it is
    # asked about like any other field: "Dr Carter's High-risk patients",
    # "which doctor has the most High-risk patients".
    "doctor":            Field("string", "assigned doctor", stored=False),
    # Particular patients, by id - the ones an earlier answer listed. Without it
    # "which of those has changed most" could only look at one of them.
    "patient_id":        Field("string", "patient", groupable=False),
}
DOCTOR = "doctor"
NO_DOCTOR = "No doctor assigned"

FILTER_OPS = {
    "eq": "$eq", "ne": "$ne", "gt": "$gt", "gte": "$gte",
    "lt": "$lt", "lte": "$lte", "in": "$in",
}
METRICS = ("count", "avg", "min", "max")
# Two ceilings, for two shapes of answer. A ranked grouping ("top conditions")
# is bounded by TOP_N_LIMIT, which the caller may lower. A bucketed
# distribution ignores top_n - see the comment in run_cohort_query - so it
# needs its own ceiling to stop a pathological bucket_size returning hundreds
# of rows to the model.
MAX_GROUPS = 25


# ---------------------------------------------------------------------------
# Closed vocabularies
# ---------------------------------------------------------------------------
# A filter on a categorical field with a value that does not exist is the worst
# failure this layer has, because it does not look like a failure. Asking for
# monitoring_status = "declining" when the data says "deteriorating" returned
# "No patients match that filter" - a confident zero where the true answer was
# 750.
#
# So the valid values are resolved from the data, stated back to the model in
# the prompt, and enforced here. A wrong value is now an error that names the
# right ones, which is what lets the correction loop in chatbot_service fix it.
#
# primary_diagnosis is deliberately absent: it holds thousands of distinct coded
# strings, so there is no vocabulary to state and no useful way to validate one.

ENUMERABLE_FIELDS = ("current_band", "risk_band", "gender", "monitoring_status",
                     "group_label", "group_confidence", "clinical_groups")
# Above this a "closed" set is not closed enough to be worth stating or
# enforcing, and listing it would crowd the prompt.
VOCABULARY_LIMIT = 30
VOCABULARY_TTL_SECONDS = 600

_vocabulary_cache: dict = {}


def reset_vocabulary_cache() -> None:
    """
    Forget the cached vocabularies.

    The TTL means a batch that introduces a new condition is invisible to
    validation for up to ten minutes, which would reject a value that is now
    perfectly valid. An ETL that loads a batch should call this; tests call it
    so one case cannot poison the next.
    """
    _vocabulary_cache.clear()


def field_vocabulary(db, field: str):
    """
    The values a categorical field actually holds, or None if it has no usable
    vocabulary.

    Cached: `distinct` over the worklist is cheap but this is called for every
    filter on every turn, and the answer only changes when a batch loads.
    """
    if db is None or field not in ENUMERABLE_FIELDS:
        return None

    spec = FIELDS.get(field)
    if spec is not None and spec.values:
        return tuple(spec.values)   # declared statically; no need to ask the data

    cached = _vocabulary_cache.get(field)
    if cached and (time.time() - cached[0]) < VOCABULARY_TTL_SECONDS:
        return cached[1]

    try:
        values = [v for v in db["patient_worklist"].distinct(field) if v not in (None, "")]
    except Exception as exc:
        # A vocabulary we cannot read must not block the query. Losing the check
        # is worse than nothing, but far better than refusing to answer.
        print(f"[chatbot] could not read vocabulary for '{field}': {exc}")
        return None

    resolved = tuple(sorted(str(v) for v in values)) if len(values) <= VOCABULARY_LIMIT else None
    _vocabulary_cache[field] = (time.time(), resolved)
    return resolved


def _condition_names(values) -> list:
    """Condition keys as the screen names them: "cardiac_other (Heart disease)"."""
    return [f"{v} ({GROUP_LABELS[v]})" if v in GROUP_LABELS else str(v) for v in values]


def describe_vocabularies(db) -> str:
    """The catalog, as a line per field, for injection into the selection prompt."""
    lines = []
    for field in ENUMERABLE_FIELDS:
        values = field_vocabulary(db, field)
        if values:
            shown = _condition_names(values) if field == "clinical_groups" else values
            lines.append(f"- {field}: {', '.join(shown)}")
    try:
        doctors = _doctors_with_patients(db) if db is not None else []
    except Exception as exc:                                    # noqa: BLE001
        print(f"[chatbot] could not read the doctors for the vocabulary: {exc}")
        doctors = []
    if 0 < len(doctors) <= VOCABULARY_LIMIT:
        lines.append(f"- {DOCTOR}: {', '.join(doctors)}")
    return "\n".join(lines)


def _resolve_categorical(db, field: str, value):
    """
    Match a supplied value to the field's vocabulary, case-insensitively.

    Returns the stored spelling so "high" finds "High". Raises with the valid
    values when there is no match.
    """
    vocabulary = field_vocabulary(db, field)
    if not vocabulary:
        return value
    wanted = str(value).strip().lower()
    # A condition may be named as the screen shows it ("Heart disease") or by
    # its key ("cardiac_other"); clinical_groups stores keys, group_label names.
    names = {label.lower(): key for key, label in GROUP_LABELS.items()}
    if field == "clinical_groups" and wanted in names:
        wanted = names[wanted]
    elif field == "group_label" and wanted in GROUP_LABELS:
        wanted = GROUP_LABELS[wanted].lower()
    for known in vocabulary:
        if str(known).strip().lower() == wanted:
            return known
    shown = _condition_names(vocabulary) if field == "clinical_groups" else vocabulary
    raise ValueError(
        f"'{value}' is not a value of '{field}'. Valid values: {', '.join(shown)}.")


def _validate_filters(db, filters) -> tuple:
    """Turn a list of {field, op, value} into a Mongo match, rejecting anything unlisted."""
    conditions: list = []
    described = []
    for f in filters or []:
        if not isinstance(f, dict):
            raise ValueError("Each filter must be an object with field, op and value.")
        name, op, value = f.get("field"), f.get("op", "eq"), f.get("value")
        spec = FIELDS.get(name)
        if spec is None:
            raise ValueError(
                f"'{name}' is not a queryable field. Available: {', '.join(sorted(FIELDS))}.")
        if op not in FILTER_OPS:
            raise ValueError(f"'{op}' is not a valid comparison. Use one of {', '.join(FILTER_OPS)}.")
        if value is None:
            raise ValueError(f"Filter on '{name}' is missing a value.")

        if name == DOCTOR:
            condition, named = _doctor_condition(db, op, value)
            conditions.append(condition)
            described.append(f"{spec.label} {op} {named}")
            continue
        if name == "patient_id":
            if op not in ("eq", "ne", "in"):
                raise ValueError("A patient id takes eq, ne or in.")
            ids = _id_list(value)
            conditions.append({"patient_id": {"$nin" if op == "ne" else "$in": access.id_variants(ids)}})
            described.append(f"patient {'not ' if op == 'ne' else ''}{', '.join(ids)}")
            continue

        if spec.kind == "number":
            if op == "in":
                raise ValueError(f"'in' does not apply to the numeric field '{name}'.")
            value = float(value)
        else:
            if op in ("gt", "gte", "lt", "lte"):
                raise ValueError(f"'{name}' holds text, so it cannot be compared with '{op}'.")
            value = ([str(v).strip() for v in (value if isinstance(value, list) else str(value).split(","))]
                     if op == "in" else str(value))
            # Resolve against the field's real vocabulary before it reaches the
            # database, so a near-miss becomes a correctable error instead of an
            # empty result that reads like an answer.
            if op in ("eq", "ne"):
                value = _resolve_categorical(db, name, value)
            elif op == "in":
                value = [_resolve_categorical(db, name, v) for v in value]

        # Case-insensitive equality on closed vocabularies: the data stores
        # "High" but a question says "high", and refusing on that is absurd.
        if spec.kind == "string" and spec.values and op == "eq":
            conditions.append({name: {"$regex": f"^{value}$", "$options": "i"}})
        else:
            conditions.append({name: {FILTER_OPS[op]: value}})

        named = ([GROUP_LABELS.get(v, v) for v in value] if isinstance(value, list) else GROUP_LABELS.get(value, value)) \
            if name == "clinical_groups" else value
        described.append(f"{spec.label} {op} {named}")
    # Every filter must hold. Two on one field - "heart disease" and "diabetes" -
    # are both kept: merged into one dict, the second replaced the first, and
    # the answer was about diabetes alone.
    if len(conditions) > 1:
        return {"$and": conditions}, described
    return (conditions[0] if conditions else {}), described


def _names_a_diagnosis(filters) -> bool:
    return any(isinstance(f, dict) and f.get("field") == "primary_diagnosis"
               and f.get("op", "eq") in ("eq", "in") for f in filters or [])


def _id_list(value) -> list:
    """Patient ids however they arrive: a list, or text separated by commas."""
    items = value if isinstance(value, list) else str(value).split(",")
    return [str(v).strip() for v in items if str(v).strip()]


def _no_such_diagnosis(filters) -> None:
    """
    Raise when an empty answer came from naming a principal diagnosis by a
    short name. Diagnoses are stored as full ICD descriptions, so "heart attack"
    matches nothing and "no patients" would read as the truth; the error goes
    back to the model, which asks again by condition.
    """
    if not _names_a_diagnosis(filters):
        return
    raise ValueError(
        "No principal diagnosis is exactly that. Diagnoses are stored as full ICD "
        "descriptions (e.g. 'Acute myocardial infarction, unspecified'), so a short name finds "
        "nothing. For a kind of illness filter clinical_groups instead: "
        + ", ".join(sorted(set(GROUP_LABELS.values()))) + ".")


# ---------------------------------------------------------------------------
# The assigned doctor
# ---------------------------------------------------------------------------
# Who looks after a patient is in care_actions (assigned_doctor_id) and the
# doctor's name in the doctors registry - the same records the Patients and
# Staff pages read (api/hospital.care_teams). Both come through the caller's
# scoped view, so a doctor's name only ever brings back patients they may see.

_NO_DOCTOR_WORDS = {"none", "unassigned", "no doctor", "no doctor assigned", "nobody"}


def _name_words(name: str) -> list:
    return [w for w in re.sub(r"[.,]", " ", str(name).lower()).split() if w not in ("dr", "doctor")]


def _doctors_named(db, value) -> tuple:
    """([registry doctors], how they were found) for a name as someone typed it:
    "Dr Emily Carter", "emily carter", "Carter" - and a misspelling, when one
    registered name is close."""
    wanted = _name_words(value)
    if not wanted:
        raise ValueError("Say which doctor, by name.")
    roster = list(db[DOCTORS].find({}, {"_id": 0, "doctor_id": 1, "name": 1, "specialty": 1}))
    found = [d for d in roster if set(wanted) <= set(_name_words(d.get("name", "")))]
    if found:
        return found, None
    close = _closest_names(db, " ".join(wanted)) or []
    if len(close) == 1:
        return [d for d in roster if d.get("name") == close[0]], f"closest match to '{value}'"
    raise ValueError(f"No doctor is registered as '{value}'."
                     + (f" Did you mean {' or '.join(close)}?" if close else ""))


def _doctor_condition(db, op: str, value) -> tuple:
    """A filter on the assigned doctor, as a match on patient_id, and its description."""
    if op not in ("eq", "ne", "in"):
        raise ValueError("The assigned doctor takes eq, ne or in.")
    values = [str(v) for v in value] if isinstance(value, list) else [str(value)]
    if any(v.strip().lower() in _NO_DOCTOR_WORDS for v in values):
        assigned = db["care_actions"].distinct("patient_id",
                                               {"assigned_doctor_id": {"$nin": [None, ""]}})
        wanted_op = "$in" if op == "ne" else "$nin"   # "no doctor" is everyone not assigned
        return {"patient_id": {wanted_op: access.id_variants(assigned)}}, NO_DOCTOR.lower()
    doctors, how = [], []
    for v in values:
        found, note = _doctors_named(db, v)
        doctors += found
        if note:
            how.append(note)
    ids = sorted({d["doctor_id"] for d in doctors})
    pids = db["care_actions"].distinct("patient_id", {"assigned_doctor_id": {"$in": ids}})
    named = " or ".join(sorted({d.get("name", d["doctor_id"]) for d in doctors}))
    if how:
        named += f" ({'; '.join(how)})"
    return {"patient_id": {"$nin" if op == "ne" else "$in": access.id_variants(pids)}}, named


def _doctor_names(db, patient_ids) -> dict:
    """patient_id (as a string) -> the assigned doctor's name, or NO_DOCTOR."""
    from api import hospital
    own = hospital.owners(db, patient_ids)
    ids = {o["assigned_doctor_id"] for o in own.values() if o.get("assigned_doctor_id")}
    names = {d["doctor_id"]: d.get("name") or d["doctor_id"]
             for d in db[DOCTORS].find({"doctor_id": {"$in": sorted(ids)}},
                                       {"_id": 0, "doctor_id": 1, "name": 1})}
    out = {}
    for pid in patient_ids:
        doctor_id = (own.get(str(pid)) or {}).get("assigned_doctor_id")
        out[str(pid)] = names.get(doctor_id, doctor_id) if doctor_id else NO_DOCTOR
    return out


def _doctors_with_patients(db) -> list:
    """The names of the doctors looking after at least one of the caller's patients."""
    ids = [i for i in db["care_actions"].distinct("assigned_doctor_id") if i]
    return sorted(d.get("name") or d["doctor_id"]
                  for d in db[DOCTORS].find({"doctor_id": {"$in": ids}},
                                            {"_id": 0, "doctor_id": 1, "name": 1}))


def run_cohort_query(db, filters: Optional[list] = None,
                     group_by: Optional[str] = None,
                     bucket_size: Optional[float] = None,
                     metric: str = "count",
                     metric_field: Optional[str] = None,
                     top_n: Optional[int] = None) -> dict:
    """
    Answer an arbitrary cohort question from a validated spec.

    filters      list of {field, op, value}, ANDed together
    group_by     a field to break the answer down by; omit for a single figure
    bucket_size  width of the bucket when grouping a number (age by 10, say)
    metric       count, avg, min or max
    metric_field the numeric field to aggregate; required unless metric is count
    """
    if metric not in METRICS:
        raise ValueError(f"metric must be one of {', '.join(METRICS)}.")

    match, described = _validate_filters(db, filters)
    batch_date = _latest_batch_or_raise(db)
    match["batch_date"] = batch_date
    n = _capped_top_n(top_n)

    if metric != "count":
        spec = FIELDS.get(metric_field)
        if spec is None or not spec.aggregatable:
            raise ValueError(
                f"'{metric}' needs a numeric field to work on. Choose one of: "
                + ", ".join(k for k, v in FIELDS.items() if v.aggregatable))

    cohort_size = db["patient_worklist"].count_documents(match)
    filter_text = " and ".join(described) if described else "the current batch"
    if cohort_size == 0:
        _no_such_diagnosis(filters)
        return {"cohort_size": 0, "filter": filter_text, "results": [],
                "note": "No patients match that filter."}

    # A bare count is not a disclosure: "3 patients are above 90" says nothing
    # about who they are. A breakdown or an average over three people can, so
    # suppression applies to those and not to a plain headcount - otherwise the
    # chatbot refuses to answer "how many" for exactly the small groups a
    # coordinator most needs to know about.
    discloses_detail = bool(group_by) or metric != "count"
    if discloses_detail and cohort_size < MIN_COHORT_FOR_AGGREGATE:
        return {"cohort_size": cohort_size, "filter": filter_text, "results": [],
                "note": (f"Only {cohort_size} patient{'s' if cohort_size != 1 else ''} "
                         f"match{'' if cohort_size != 1 else 'es'} that filter — too few to "
                         f"summarise without effectively identifying them. Widen the filter.")}

    accumulator = ({"$sum": 1} if metric == "count"
                   else {f"${metric}": f"${metric_field}"})

    # ---- no group_by: one number for the whole cohort -----------------------
    if not group_by:
        if metric == "count":
            return {"cohort_size": cohort_size, "filter": filter_text, "metric": "count",
                    "value": cohort_size}
        row = list(db["patient_worklist"].aggregate([
            {"$match": match},
            {"$group": {"_id": None, "value": accumulator}},
        ], maxTimeMS=MAX_QUERY_MS))
        value = row[0]["value"] if row else None
        return {"cohort_size": cohort_size, "filter": filter_text,
                "metric": f"{metric} {FIELDS[metric_field].label}",
                "value": round(value, 1) if isinstance(value, (int, float)) else value}

    # ---- grouped ------------------------------------------------------------
    spec = FIELDS.get(group_by)
    if spec is None or not spec.groupable:
        raise ValueError(
            f"Cannot group by '{group_by}'. Available: "
            + ", ".join(k for k, v in FIELDS.items() if v.groupable))
    if group_by == DOCTOR:
        if bucket_size is not None:
            raise ValueError("The assigned doctor is not a number, so it cannot be bucketed.")
        return _cohort_by_doctor(db, match, cohort_size, filter_text, metric, metric_field, n)

    pipeline: list = [{"$match": match}]
    if spec.array:
        pipeline.append({"$unwind": f"${group_by}"})

    if bucket_size is not None:
        if spec.kind != "number":
            raise ValueError(f"'{group_by}' is not a number, so it cannot be bucketed.")
        size = float(bucket_size)
        if size <= 0:
            raise ValueError("bucket_size must be greater than zero.")
        key = {"$multiply": [{"$floor": {"$divide": [f"${group_by}", size]}}, size]}
    else:
        key = f"${group_by}"

    # A bucketed grouping is a distribution, and half a distribution is
    # misleading: cutting age bands at top_n=5 hides exactly the old patients
    # the question is usually about. Buckets are returned in full (up to
    # MAX_GROUPS) and ordered by bucket; only ranked groupings honour top_n.
    pipeline += [
        {"$group": {"_id": key, "value": accumulator, "count": {"$sum": 1}}},
        {"$sort": {"_id": 1} if bucket_size is not None else {"value": DESCENDING}},
        {"$limit": MAX_GROUPS if bucket_size is not None else min(n, MAX_GROUPS)},
    ]

    rows = list(db["patient_worklist"].aggregate(pipeline, maxTimeMS=MAX_QUERY_MS))
    results = []
    for r in rows:
        bucket = r["_id"]
        if bucket is None:
            continue
        if bucket_size is not None:
            size = float(bucket_size)
            name = f"{bucket:g}-{bucket + size - 1:g}"
        else:
            name = GROUP_LABELS.get(bucket, bucket) if spec.array else bucket
        entry = {spec.label: name, "patients": r["count"]}
        if metric != "count":
            value = r["value"]
            entry[f"{metric} {FIELDS[metric_field].label}"] = (
                round(value, 1) if isinstance(value, (int, float)) else value)
        else:
            entry["percent_of_cohort"] = round(r["count"] / cohort_size * 100, 1)
        results.append(entry)

    return {"cohort_size": cohort_size, "filter": filter_text,
            "grouped_by": spec.label, "results": results}


def _cohort_by_doctor(db, match: dict, cohort_size: int, filter_text: str,
                      metric: str, metric_field: Optional[str], n: int) -> dict:
    """run_cohort_query grouped by the assigned doctor. The doctor is not on the
    worklist row, so the grouping is done here rather than in the database."""
    projection = {"_id": 0, "patient_id": 1, **({metric_field: 1} if metric != "count" else {})}
    rows = list(db["patient_worklist"].find(match, projection))
    names = _doctor_names(db, [r["patient_id"] for r in rows])
    groups: dict = defaultdict(list)
    for r in rows:
        groups[names.get(str(r["patient_id"]), NO_DOCTOR)].append(r.get(metric_field))
    label = FIELDS[DOCTOR].label
    results = []
    for doctor, values in groups.items():
        entry = {label: doctor, "patients": len(values)}
        if metric == "count":
            entry["percent_of_cohort"] = round(len(values) / cohort_size * 100, 1)
            entry["_rank"] = len(values)
        else:
            numbers = [v for v in values if isinstance(v, (int, float))]
            value = ({"avg": lambda x: sum(x) / len(x), "min": min, "max": max}[metric](numbers)
                     if numbers else None)
            entry[f"{metric} {FIELDS[metric_field].label}"] = (
                round(value, 1) if value is not None else None)
            entry["_rank"] = value if value is not None else float("-inf")
        results.append(entry)
    results.sort(key=lambda e: e["_rank"], reverse=True)
    for e in results:
        e.pop("_rank")
    return {"cohort_size": cohort_size, "filter": filter_text, "grouped_by": label,
            "results": results[:min(n, MAX_GROUPS)]}


# ---------------------------------------------------------------------------
# Row-level listing
# ---------------------------------------------------------------------------
# run_cohort_query answers "what is true of this group". This answers "who is
# in it, and what does each of them look like" - a different shape of question
# that no amount of aggregation covers. "The primary diagnosis of the top 5
# patients" is one question, and before this existed the only ranked list
# available carried a score and nothing else.

DRIVER_FIELDS = {
    "driver_1": "top risk driver",
    "driver_2": "second risk driver",
    "driver_3": "third risk driver",
}
# Everything queryable is also displayable, plus the three driver sentences,
# which are explanations rather than columns and so are not in FIELDS.
LISTABLE_FIELDS = {**{name: spec.label for name, spec in FIELDS.items()}, **DRIVER_FIELDS}

LIST_PATIENTS_DEFAULT = 5
LIST_PATIENTS_MAX = 25
# More columns than this and the answer stops being readable in a chat bubble
# long before it stops being correct.
MAX_LIST_COLUMNS = 6
DEFAULT_LIST_FIELDS = ("current_score", "current_band")


def list_patients(db, filters: Optional[list] = None,
                  sort_by: str = "current_score",
                  order: str = "desc",
                  fields: Optional[list] = None,
                  limit: Optional[int] = None) -> dict:
    """
    List individual patients matching a filter, showing the requested columns.

    filters  list of {field, op, value}, ANDed - the same vocabulary as run_cohort_query
    sort_by  which field to rank by; "top patients" means current_score descending
    order    desc or asc
    fields   which columns to show for each patient, beyond patient_id
    limit    how many patients to return

    No small-cell suppression here, and deliberately so: this returns the same
    rows the worklist screen already shows, identified the same way. Suppression
    exists to stop an *aggregate* naming the people inside it; a list that is
    openly a list is not that failure mode.
    """
    match, described = _validate_filters(db, filters)
    match["batch_date"] = _latest_batch_or_raise(db)

    order = str(order or "desc").strip().lower()
    if order not in ("asc", "desc"):
        raise ValueError("order must be 'asc' or 'desc'.")

    sort_spec = FIELDS.get(sort_by)
    if sort_spec is None or sort_spec.array or not sort_spec.stored:
        raise ValueError(
            f"Cannot sort by '{sort_by}'. Available: "
            + ", ".join(k for k, v in FIELDS.items() if not v.array and v.stored))

    requested = list(fields) if fields else list(DEFAULT_LIST_FIELDS)
    # The field being sorted on belongs in the output - a "top 5 by risk" list
    # that omits the risk score is an unreadable answer.
    if sort_by not in requested:
        requested.insert(0, sort_by)

    chosen: list = []
    for name in requested:
        name = str(name)
        if name == "patient_id":
            continue  # always included
        if name not in LISTABLE_FIELDS:
            raise ValueError(
                f"'{name}' is not a field that can be shown. Available: "
                + ", ".join(sorted(LISTABLE_FIELDS)))
        if name not in chosen:
            chosen.append(name)
    chosen = chosen[:MAX_LIST_COLUMNS]

    resolved_limit = LIST_PATIENTS_DEFAULT if limit is None else int(limit)
    if resolved_limit <= 0:
        raise ValueError("limit must be a positive integer.")
    resolved_limit = min(resolved_limit, LIST_PATIENTS_MAX)

    count_matching = db["patient_worklist"].count_documents(match)
    filter_text = " and ".join(described) if described else "the current batch"
    if count_matching == 0:
        _no_such_diagnosis(filters)
        return {"count_matching": 0, "filter": filter_text, "patients": [],
                "note": "No patients match that filter."}

    projection = {"_id": 0, "patient_id": 1, "discharge_date": 1}
    for name in chosen:
        if name != DOCTOR:
            projection[name] = 1

    cursor = (db["patient_worklist"]
              .find(match, projection)
              .sort(sort_by, DESCENDING if order == "desc" else 1)
              .limit(resolved_limit))

    rows = list(cursor)
    doctors = _doctor_names(db, [r.get("patient_id") for r in rows]) if DOCTOR in chosen else {}
    # Each score shown comes with its date: the current one from the latest
    # weekly reading, the discharge one (and a change since it) from discharge.
    dates = (_current_score_dates(db, [r.get("patient_id") for r in rows])
             if {"current_score", "trend_delta"} & set(chosen) else {})
    with_discharge = bool({"discharge_score", "trend_delta"} & set(chosen))
    patients = []
    for row in rows:
        entry = {"patient_id": row.get("patient_id")}
        for name in chosen:
            value = doctors.get(str(row.get("patient_id"))) if name == DOCTOR else row.get(name)
            if isinstance(value, list):
                value = [GROUP_LABELS.get(v, v) for v in value] or None
            entry[LISTABLE_FIELDS[name]] = value
        if dates:
            entry["current score as of"] = dates.get(str(row.get("patient_id")))
        if with_discharge:
            entry["discharge date"] = row.get("discharge_date")
        patients.append(entry)

    return {
        "count_matching": count_matching,
        "returned": len(patients),
        "filter": filter_text,
        "sorted_by": f"{sort_spec.label}, {'highest' if order == 'desc' else 'lowest'} first",
        "patients": patients,
    }


# ---------------------------------------------------------------------------
# The clinician directory
# ---------------------------------------------------------------------------
# Questions about who is registered, what they cover, and how much is waiting
# for them. Doctors are staff rather than patients, so none of the small-cell
# suppression above applies - "one cardiologist is registered" discloses nothing
# that needs protecting.
#
# EMAIL IS DELIBERATELY NOT EXPOSED. It is in the directory because routing
# needs a unique key, not because the chatbot should read staff contact details
# aloud on request. Nothing here answers "what is Dr X's email", and that is a
# decision rather than an oversight.

# doctor_id is deliberately absent too. It is an internal routing key, and when
# it was returned the phrasing step led every bullet with it - a wall of
# "DR-dr-amir-haddad-a2c294" that tells a reader nothing they asked for.
DOCTOR_PUBLIC_FIELDS = {"_id": 0, "name": 1, "specialty": 1,
                        "clinical_groups": 1, "active": 1}

OPEN_ALERT_STATUSES = ("pending", "acknowledged")


def _group_label(key: str) -> str:
    return GROUP_LABELS.get(key, key.replace("_", " "))


def list_registered_doctors(db, name: Optional[str] = None,
                            specialty: Optional[str] = None,
                            clinical_group: Optional[str] = None,
                            include_inactive: bool = False) -> dict:
    """
    The clinician directory: who is registered and what they cover.

    Optionally narrowed by name, specialty or condition. Asking about one named
    clinician is the common case - without a name filter the model sent no
    filter at all, and the answer described five doctors when one was asked
    about.
    """
    query: dict = {} if include_inactive else {"active": True}
    described = []

    if name:
        query["name"] = {"$regex": re.escape(str(name).strip()), "$options": "i"}
        described.append(f"named like '{name}'")

    if specialty:
        query["specialty"] = {"$regex": f"^{str(specialty).strip()}$", "$options": "i"}
        described.append(f"in {specialty}")

    mapped_specialty = None
    key = None
    if clinical_group:
        key = _condition_key(clinical_group)
        if key not in SPECIALTY_FOR_GROUP:
            raise ValueError(
                f"'{clinical_group}' is not a condition this system tracks. Available: "
                + ", ".join(sorted(SPECIALTY_FOR_GROUP)))
        mapped_specialty = SPECIALTY_FOR_GROUP[key]
        # Matching only the claimed `clinical_groups` array contradicted the
        # routing rules: a psychiatrist who registered without ticking any
        # condition still receives every mental-health alert through the
        # specialty fallback, and this tool used to report that nobody covered
        # it while get_alert_routing named him. Both tiers are matched, and each
        # result says which one it came through.
        query["$or"] = [{"clinical_groups": key}, {"specialty": mapped_specialty}]
        described.append(f"covering {_group_label(key)}")

    doctors = list(db[DOCTORS].find(query, DOCTOR_PUBLIC_FIELDS).sort("name", 1))
    suggestions = _closest_names(db, name) if (name and not doctors) else None
    for d in doctors:
        claimed = d.get("clinical_groups") or []
        d["covers"] = [_group_label(g) for g in claimed]
        d.pop("clinical_groups", None)
        if key:
            d["reached_because"] = ("claims this condition" if key in claimed
                                    else f"{mapped_specialty} on call")
        # "Active status: True" on every line is bookkeeping, not an answer, and
        # the default query already excludes anyone inactive. The flag is only
        # worth stating when a deactivated clinician could be in the list.
        if not include_inactive:
            d.pop("active", None)

    return {
        "count": len(doctors),
        "filter": " and ".join(described) if described else "all registered clinicians",
        "doctors": doctors,
        "note": _empty_note(doctors, name, suggestions),
        "closest_matches": suggestions,
    }


def _empty_note(doctors: list, name: Optional[str], suggestions) -> Optional[str]:
    """
    Why an empty directory result is empty.

    The reason depends on what was searched for. Explaining the condition
    routing fallback after a failed NAME lookup answered a question nobody
    asked, and read as a non-sequitur next to the suggested spelling.
    """
    if doctors:
        return None
    if name:
        return ("No clinician is registered under that name."
                + (" The closest registered spelling is in closest_matches."
                   if suggestions else ""))
    return ("No clinician matches that. Alerts for a condition nobody covers fall "
            "back to the mapped specialty, then to internal medicine, and are held "
            "in an unrouted queue if neither is registered.")


def _closest_names(db, name: str, limit: int = 3) -> Optional[list]:
    """
    Names that look like the one asked for, when nothing matched exactly.

    Registered names are typed by hand and are misspelt in both directions - the
    directory holds "Dr. Partrick Jane", and someone searching for the person
    they can see on screen types "Patrick". A substring match makes that doctor
    invisible and the honest-looking answer, "no clinician matches", is wrong.
    """
    import difflib

    everyone = [d["name"] for d in db[DOCTORS].find({}, {"_id": 0, "name": 1})]
    needle = str(name).strip().lower()
    close = difflib.get_close_matches(needle, [n.lower() for n in everyone],
                                      n=limit, cutoff=0.6)
    # Also catch a single misspelt token against any word of a stored name.
    for stored in everyone:
        if stored.lower() in close:
            continue
        for word in stored.lower().replace(".", " ").split():
            if difflib.SequenceMatcher(None, needle, word).ratio() >= 0.8:
                close.append(stored.lower())
                break

    matched = [n for n in everyone if n.lower() in close]
    return matched[:limit] or None


def get_doctor_workload(db, doctor_name: Optional[str] = None) -> dict:
    """
    What is on each doctor's plate: the patients assigned to them on the current
    list, how many of those are High risk today, and the early-warning alerts
    waiting on them.

    Alerts are counted as alerts, not patients. A doctor with 40 open alerts may
    be looking at fewer than 40 people, because a patient can raise one alert
    per monitoring week - saying "40 patients" would overstate it.
    """
    match: dict = {"status": {"$in": list(OPEN_ALERT_STATUSES)}}
    wanted_ids, found_as = None, None
    if doctor_name:
        # The same name matching as the patient filters, misspellings included:
        # a regex on the name found nothing for "Emely Carter" and reported an
        # empty inbox as if that were the answer.
        doctors, found_as = _doctors_named(db, doctor_name)
        wanted_ids = {d["doctor_id"] for d in doctors}
        match["$or"] = [{"doctor_id": {"$in": sorted(wanted_ids)}},
                        {"doctor_name": {"$in": [d["name"] for d in doctors if d.get("name")]}}]

    rows = list(db[CLINICAL_ALERTS].aggregate([
        {"$match": match},
        {"$group": {
            "_id": {"doctor_id": "$doctor_id", "doctor_name": "$doctor_name"},
            "open_alerts": {"$sum": 1},
            "critical": {"$sum": {"$cond": [{"$eq": ["$severity", "critical"]}, 1, 0]}},
            "unacknowledged": {"$sum": {"$cond": [{"$eq": ["$status", "pending"]}, 1, 0]}},
            "patients": {"$addToSet": "$patient_id"},
        }},
    ], maxTimeMS=MAX_QUERY_MS))

    # The patient load, from the care team of everyone on the current list.
    load: dict = defaultdict(lambda: {"assigned_patients": 0, "high_risk_patients": 0})
    batch = get_latest_batch_date(db, "patient_worklist")
    if batch:
        from api import hospital
        current = list(db["patient_worklist"].find({"batch_date": batch},
                                                   {"_id": 0, "patient_id": 1, "current_band": 1}))
        own = hospital.owners(db, [r["patient_id"] for r in current])
        for r in current:
            doctor_id = (own.get(str(r["patient_id"])) or {}).get("assigned_doctor_id")
            if doctor_id and (wanted_ids is None or doctor_id in wanted_ids):
                load[doctor_id]["assigned_patients"] += 1
                load[doctor_id]["high_risk_patients"] += str(r.get("current_band")).lower() == "high"
    names = {d["doctor_id"]: d.get("name") or d["doctor_id"]
             for d in db[DOCTORS].find({"doctor_id": {"$in": sorted(load)}},
                                       {"_id": 0, "doctor_id": 1, "name": 1})}

    entries: dict = {}
    for doctor_id, counts in load.items():
        entries[doctor_id] = {"doctor": names.get(doctor_id, doctor_id), **counts,
                              "open_alerts": 0, "critical": 0, "awaiting_acknowledgement": 0,
                              "patients_with_open_alerts": 0}
    for r in rows:
        key = r["_id"].get("doctor_id") or r["_id"].get("doctor_name") or "unrouted"
        entry = entries.setdefault(key, {
            "doctor": r["_id"].get("doctor_name") or "unrouted",
            "assigned_patients": 0, "high_risk_patients": 0})
        entry.update(open_alerts=r["open_alerts"], critical=r["critical"],
                     awaiting_acknowledgement=r["unacknowledged"],
                     patients_with_open_alerts=len(r["patients"]))
    workload = sorted(entries.values(), key=lambda e: (e["critical"], e["open_alerts"],
                                                       e["high_risk_patients"],
                                                       e["assigned_patients"]), reverse=True)

    unrouted = db[CLINICAL_ALERTS].count_documents({"status": "unrouted"})
    responded = db[CLINICAL_ALERTS].count_documents({"status": "responded"})

    return {
        "workload": workload,
        "unrouted_alerts": unrouted,
        "responded_alerts": responded,
        "filter": (" or ".join(sorted({e["doctor"] for e in workload}) or [str(doctor_name)])
                   + (f" ({found_as})" if found_as else "")) if doctor_name else "all clinicians",
        "counts": ("assigned_patients and high_risk_patients are the doctor's patients on the "
                   "current list; open_alerts counts alerts, not patients - one patient can raise "
                   "an alert each week"),
        "note": (None if workload else
                 "No patients are assigned and no alerts are open for that. Either none have "
                 "been raised yet, or every one has already been answered."),
    }


def _condition_key(value) -> str:
    """A condition as a routing key, whether given as a key or as the screen
    names it: "heart_failure", "Heart failure", "heart disease"."""
    wanted = str(value).strip().lower()
    by_label = {label.lower(): key for key, label in GROUP_LABELS.items()}
    return by_label.get(wanted, wanted.replace(" ", "_"))


def get_alert_routing(db, clinical_group: Optional[str] = None) -> dict:
    """
    Who an alert for a given condition would reach, and why.

    Answers the routing rules rather than replaying them from the model's
    memory: the chain is condition -> claimed group -> mapped specialty ->
    internal medicine -> nobody, and only the database knows who is registered.
    """
    from api.doctor_service import route  # local: doctor_service imports this module

    groups = [_condition_key(clinical_group)] if clinical_group else sorted(SPECIALTY_FOR_GROUP)
    for g in groups:
        if g not in SPECIALTY_FOR_GROUP:
            raise ValueError(
                f"'{clinical_group}' is not a condition this system tracks. Available: "
                + ", ".join(sorted(SPECIALTY_FOR_GROUP)))

    roster = list(db[DOCTORS].find({"active": True}, DOCTOR_PUBLIC_FIELDS))
    results = []
    for g in groups:
        doctor, reason = route(db, g, roster=roster)
        results.append({
            "condition": _group_label(g),
            "routes_to": doctor["name"] if doctor else None,
            "specialty": (doctor["specialty"] if doctor
                          else SPECIALTY_FOR_GROUP.get(g, "Internal Medicine")),
            "because": reason,
        })

    unassigned = [r["condition"] for r in results if r["routes_to"] is None]
    return {
        "routing": results,
        "conditions_with_no_clinician": unassigned,
        "note": ("Alerts for these conditions are held in an unrouted queue until "
                 "someone is registered for them: " + ", ".join(unassigned))
        if unassigned else None,
    }

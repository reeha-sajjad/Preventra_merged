"""
Unit tests for the chatbot's predefined MongoDB query functions.
Uses mongomock so these run fully in isolation, no real MongoDB required.

Run with:
  pytest tests/units/test_chatbot_queries.py -v
"""

import sys
from pathlib import Path

import mongomock
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "api"))

from chatbot_queries import (
    LIST_PATIENTS_MAX,
    MAX_GROUPS,
    MAX_LIST_COLUMNS,
    TOP_N_LIMIT,
    count_patients,
    count_patients_by_risk_band,
    get_common_conditions,
    get_common_diagnoses,
    get_common_drivers,
    get_patient_details,
    get_patient_drivers,
    get_risk_trend_over_time,
    get_top_risk_patients,
    list_patients,
    list_patients_by_risk_threshold,
    run_cohort_query,
)

LATEST_BATCH = "2026-04-29"
PRIOR_BATCH = "2026-04-22"
OLDEST_BATCH = "2026-04-14"


@pytest.fixture
def db():
    client = mongomock.MongoClient()
    database = client["neuroshield"]
    docs = [
        # Latest batch — 2 low, 2 medium, 1 high
        {"patient_id": "PT-1", "risk_score": 10, "risk_band": "low", "batch_date": LATEST_BATCH,
         "driver_1": "d1a", "driver_2": "d2a", "driver_3": "d3a", "discharge_date": "2026-04-20"},
        {"patient_id": "PT-2", "risk_score": 25, "risk_band": "low", "batch_date": LATEST_BATCH,
         "driver_1": "d1b", "driver_2": "d2b", "driver_3": "d3b", "discharge_date": "2026-04-21"},
        {"patient_id": "PT-3", "risk_score": 55, "risk_band": "medium", "batch_date": LATEST_BATCH,
         "driver_1": "d1c", "driver_2": "d2c", "driver_3": "d3c", "discharge_date": "2026-04-22"},
        {"patient_id": "PT-4", "risk_score": 60, "risk_band": "medium", "batch_date": LATEST_BATCH,
         "driver_1": "d1d", "driver_2": "d2d", "driver_3": "d3d", "discharge_date": "2026-04-23"},
        # risk_band stored capitalized here, like real production data, to catch case-sensitivity bugs
        {"patient_id": "PT-5", "risk_score": 90, "risk_band": "High", "batch_date": LATEST_BATCH,
         "driver_1": "d1e", "driver_2": "d2e", "driver_3": "d3e", "discharge_date": "2026-04-24"},
        # patient_id stored as an int, like real production data — must still match a string lookup
        {"patient_id": 999888, "risk_score": 42, "risk_band": "medium", "batch_date": LATEST_BATCH,
         "driver_1": "d1f", "driver_2": "d2f", "driver_3": "d3f", "discharge_date": "2026-04-25"},
        # Prior batches, for trend-over-time
        {"patient_id": "PT-1", "risk_score": 8, "risk_band": "low", "batch_date": PRIOR_BATCH},
        {"patient_id": "PT-5", "risk_score": 70, "risk_band": "high", "batch_date": PRIOR_BATCH},
        {"patient_id": "PT-1", "risk_score": 5, "risk_band": "low", "batch_date": OLDEST_BATCH},
    ]
    # The worklist stores the discharge-time score as risk_score/risk_band and
    # the current, post-monitoring score as current_score/current_band. Every
    # query in chatbot_queries reads the current pair, because that is what the
    # dashboard shows. The fixture above only set the discharge pair, so each
    # filter matched nothing and the assertions had been failing silently.
    # Mirroring them keeps the existing expectations correct.
    for doc in docs:
        doc.setdefault("current_score", doc["risk_score"])
        doc.setdefault("current_band", doc["risk_band"])
    database["patient_worklist"].insert_many(docs)
    return database


@pytest.fixture
def empty_db():
    client = mongomock.MongoClient()
    return client["neuroshield"]


# ---------------------------------------------------------------------------
# count_patients_by_risk_band
# ---------------------------------------------------------------------------

def test_count_all_bands(db):
    result = count_patients_by_risk_band(db)
    assert result == {"counts": {"low": 2, "medium": 3, "high": 1}, "total": 6}


def test_count_single_band(db):
    assert count_patients_by_risk_band(db, "high") == {"risk_band": "high", "count": 1}


def test_count_single_band_case_insensitive(db):
    assert count_patients_by_risk_band(db, "HIGH") == {"risk_band": "high", "count": 1}


def test_count_invalid_band_raises(db):
    with pytest.raises(ValueError):
        count_patients_by_risk_band(db, "critical")


def test_count_no_data_raises(empty_db):
    with pytest.raises(LookupError):
        count_patients_by_risk_band(empty_db)


# ---------------------------------------------------------------------------
# list_patients_by_risk_threshold
# ---------------------------------------------------------------------------

def test_list_gt_threshold(db):
    result = list_patients_by_risk_threshold(db, "gt", 50)
    ids = [r["patient_id"] for r in result]
    assert ids == ["PT-5", "PT-4", "PT-3"]  # sorted descending by risk_score


def test_list_eq_threshold(db):
    result = list_patients_by_risk_threshold(db, "eq", 90)
    assert result == [{"patient_id": "PT-5", "current risk score": 90}]


def test_list_default_and_cap_limit(db):
    result = list_patients_by_risk_threshold(db, "gte", 0, limit=999)
    assert len(result) == 6  # hard cap doesn't matter here, fewer than 200 rows exist


def test_list_limit_hard_cap_enforced(db):
    result = list_patients_by_risk_threshold(db, "gte", 0, limit=500)
    assert len(result) <= 200


def test_list_invalid_operator_raises(db):
    with pytest.raises(ValueError):
        list_patients_by_risk_threshold(db, "startswith", 50)


def test_list_invalid_limit_raises(db):
    with pytest.raises(ValueError):
        list_patients_by_risk_threshold(db, "gt", 50, limit=0)


# ---------------------------------------------------------------------------
# get_top_risk_patients
# ---------------------------------------------------------------------------

def test_top_n(db):
    result = get_top_risk_patients(db, 3)
    assert [r["patient_id"] for r in result] == ["PT-5", "PT-4", "PT-3"]


def test_top_n_capped_at_100(db):
    result = get_top_risk_patients(db, 500)
    assert len(result) == 6  # all available patients returned, no error


def test_top_n_invalid_raises(db):
    with pytest.raises(ValueError):
        get_top_risk_patients(db, 0)


# ---------------------------------------------------------------------------
# get_patient_drivers
# ---------------------------------------------------------------------------

def test_get_patient_drivers_found(db):
    result = get_patient_drivers(db, "PT-5")
    assert result == {
        "patient_id": "PT-5",
        "current risk score": 90,
        "current band": "High",
        "current score as of": None,          # no weekly readings in this fixture
        "driver_1": "d1e",
        "driver_2": "d2e",
        "driver_3": "d3e",
    }


def test_get_patient_drivers_not_found_raises(db):
    with pytest.raises(LookupError):
        get_patient_drivers(db, "PT-DOES-NOT-EXIST")


def test_get_patient_drivers_matches_int_stored_id(db):
    """patient_id is stored as an int in production data; a string lookup must still match."""
    result = get_patient_drivers(db, "999888")
    assert result["patient_id"] == 999888
    assert result["current band"] == "medium"


# ---------------------------------------------------------------------------
# get_patient_details
# ---------------------------------------------------------------------------

def test_get_patient_details_found(db):
    result = get_patient_details(db, "PT-3")
    assert result["patient_id"] == "PT-3"
    assert result["risk score at discharge"] == 55
    assert result["discharge_date"] == "2026-04-22"
    assert "_id" not in result


def test_get_patient_details_not_found_raises(db):
    with pytest.raises(LookupError):
        get_patient_details(db, "PT-DOES-NOT-EXIST")


def test_get_patient_details_matches_int_stored_id(db):
    result = get_patient_details(db, "999888")
    assert result["patient_id"] == 999888
    assert result["discharge_date"] == "2026-04-25"


# ---------------------------------------------------------------------------
# get_risk_trend_over_time
# ---------------------------------------------------------------------------

def test_trend_by_day_all_data(db):
    result = get_risk_trend_over_time(db, group_by="day")
    periods = {r["period"]: r for r in result}
    assert periods[OLDEST_BATCH]["low"] == 1
    assert periods[PRIOR_BATCH]["low"] == 1
    assert periods[PRIOR_BATCH]["high"] == 1
    assert periods[LATEST_BATCH]["low"] == 2
    assert periods[LATEST_BATCH]["medium"] == 3
    assert periods[LATEST_BATCH]["high"] == 1


def test_trend_by_month(db):
    result = get_risk_trend_over_time(db, group_by="month")
    assert len(result) == 1  # all batches fall in April 2026
    assert result[0]["period"] == "2026-04"
    assert result[0]["low"] == 4
    assert result[0]["medium"] == 3
    assert result[0]["high"] == 2


def test_trend_date_range_filter(db):
    result = get_risk_trend_over_time(db, start_date=LATEST_BATCH, group_by="day")
    assert len(result) == 1
    assert result[0]["period"] == LATEST_BATCH


def test_trend_invalid_group_by_raises(db):
    with pytest.raises(ValueError):
        get_risk_trend_over_time(db, group_by="year")


# ---------------------------------------------------------------------------
# Cohort analytics
# ---------------------------------------------------------------------------

def _cohort_db():
    """
    Six patients in the latest batch, enough to clear MIN_COHORT_FOR_AGGREGATE,
    with overlapping conditions so membership counting can be checked.
    """
    client = mongomock.MongoClient()
    database = client["neuroshield"]
    rows = [
        ("C-1", 10, "low",    ["general"],                "Pneumonia",      "Length of stay: 3 days"),
        ("C-2", 30, "medium", ["heart_failure"],          "Heart failure",  "Length of stay: 9 days"),
        ("C-3", 45, "medium", ["heart_failure", "renal"], "Heart failure",  "Albumin, lowest value during stay: 2.1"),
        ("C-4", 85, "high",   ["heart_failure"],          "Heart failure",  "Length of stay: 20 days"),
        ("C-5", 90, "high",   ["renal"],                  "Kidney failure", "Albumin, lowest value during stay: 1.9"),
        ("C-6", 95, "high",   ["renal"],                  "Kidney failure", "Length of stay: 30 days"),
    ]
    database["patient_worklist"].insert_many([
        {"patient_id": pid, "batch_date": LATEST_BATCH,
         "risk_score": score, "risk_band": band,
         "current_score": score, "current_band": band,
         "clinical_groups": groups, "primary_diagnosis": dx,
         "driver_1": driver, "driver_2": "", "driver_3": ""}
        for pid, score, band, groups, dx, driver in rows
    ])
    return database


def test_count_patients_counts_every_match_not_a_page():
    """The whole point of this function: it must not inherit a list cap."""
    db = _cohort_db()
    assert count_patients(db)["count"] == 6
    assert count_patients(db, min_score=45)["count"] == 4
    assert count_patients(db, band="high")["count"] == 3
    assert count_patients(db, min_score=30, max_score=85)["count"] == 3


def test_count_patients_reports_share_of_batch():
    result = count_patients(_cohort_db(), band="high")
    assert result["cohort_total"] == 6
    assert result["percent_of_total"] == 50.0
    assert "high" in result["filter"]


def test_common_conditions_counts_membership_not_primary_group():
    """C-3 carries two conditions and must count toward both."""
    result = get_common_conditions(_cohort_db())
    counts = {r["condition"]: r["patients"] for r in result["results"]}
    assert counts["Heart failure"] == 3
    assert counts["Kidney disease"] == 3
    assert result["cohort_size"] == 6


def test_common_diagnoses_ranks_by_frequency():
    result = get_common_diagnoses(_cohort_db())
    assert result["results"][0] == {
        "diagnosis": "Heart failure", "patients": 3, "percent_of_cohort": 50.0}


def test_common_drivers_extracts_the_label_not_the_value():
    """A driver is a sentence; only the part before the colon is comparable."""
    result = get_common_drivers(_cohort_db())
    labels = {r["driver"]: r["patients"] for r in result["results"]}
    assert labels == {"Length of stay": 4, "Albumin, lowest value during stay": 2}


def test_common_drivers_counts_each_patient_once_per_label():
    """A factor in two of a patient's three slots is still one patient."""
    client = mongomock.MongoClient()
    db = client["neuroshield"]
    db["patient_worklist"].insert_many([
        {"patient_id": f"D-{i}", "batch_date": LATEST_BATCH,
         "current_score": 50, "current_band": "medium",
         "driver_1": "Length of stay: 9 days",
         "driver_2": "Length of stay: 9 days",
         "driver_3": "Prior admissions on record: 4"}
        for i in range(5)
    ])
    result = get_common_drivers(db)
    assert {r["driver"]: r["patients"] for r in result["results"]} == {
        "Length of stay": 5, "Prior admissions on record": 5}


def test_aggregates_are_suppressed_for_a_cohort_too_small_to_be_one():
    """Naming the diagnoses of two patients is disclosure, not statistics."""
    db = _cohort_db()
    for fn in (get_common_conditions,
               get_common_diagnoses,
               get_common_drivers):
        result = fn(db, min_score=90)
        assert result["results"] == []
        assert "too few" in result["note"]
        assert result["cohort_size"] == 2


def test_empty_cohort_says_so_rather_than_returning_nothing():
    result = get_common_conditions(_cohort_db(), min_score=99)
    assert result["cohort_size"] == 0
    assert "No patients" in result["note"]


@pytest.mark.parametrize("kwargs", [
    {"band": "critical"},
    {"min_score": -1},
    {"min_score": 101},
    {"max_score": 500},
])
def test_cohort_filter_rejects_out_of_range_arguments(kwargs):
    """Arguments arrive from an LLM, so the bounds are enforced here."""
    with pytest.raises(ValueError):
        count_patients(_cohort_db(), **kwargs)


def test_top_n_is_capped():
    result = get_common_conditions(_cohort_db(), top_n=9999)
    assert len(result["results"]) <= TOP_N_LIMIT


def test_top_n_must_be_positive():
    with pytest.raises(ValueError):
        get_common_conditions(_cohort_db(), top_n=0)


# ---------------------------------------------------------------------------
# run_cohort_query — the dynamic layer
# ---------------------------------------------------------------------------

def _query_db():
    client = mongomock.MongoClient()
    database = client["neuroshield"]
    rows = [
        ("Q-1", 10, "low",    35, "F", ["general"],       4.0),
        ("Q-2", 30, "medium", 45, "M", ["heart_failure"], 6.0),
        ("Q-3", 45, "medium", 55, "F", ["heart_failure"], 8.0),
        ("Q-4", 85, "high",   65, "M", ["heart_failure"], 10.0),
        ("Q-5", 90, "high",   75, "F", ["renal"],         12.0),
        ("Q-6", 95, "high",   85, "M", ["renal"],         14.0),
        # Enough rows that a filtered subset still clears
        # MIN_COHORT_FOR_AGGREGATE; below it, grouped results are suppressed
        # by design and the assertions would be testing the guardrail instead.
        ("Q-7", 60, "high",   58, "F", ["heart_failure"],  9.0),
        ("Q-8", 62, "high",   62, "M", ["renal"],         11.0),
        ("Q-9", 64, "high",   68, "F", ["general"],       13.0),
        ("Q-10", 66, "high",  72, "M", ["general"],       15.0),
    ]
    database["patient_worklist"].insert_many([
        {"patient_id": pid, "batch_date": LATEST_BATCH,
         "current_score": score, "current_band": band, "risk_score": score, "risk_band": band,
         "anchor_age": age, "gender": sex, "clinical_groups": groups, "los_days": los}
        for pid, score, band, age, sex, groups, los in rows
    ])
    return database


def test_query_counts_with_no_grouping():
    result = run_cohort_query(_query_db(),
                              filters=[{"field": "current_band", "op": "eq", "value": "high"}])
    assert result["value"] == 7


def test_query_matches_band_case_insensitively():
    """The data stores 'High'; a question says 'high'. Refusing on that is absurd."""
    db = _query_db()
    lower = run_cohort_query(db, filters=[{"field": "current_band", "op": "eq", "value": "high"}])
    upper = run_cohort_query(db, filters=[{"field": "current_band", "op": "eq", "value": "HIGH"}])
    assert lower["value"] == upper["value"] == 7


def test_query_aggregates_a_numeric_field():
    result = run_cohort_query(_query_db(), metric="avg", metric_field="anchor_age")
    assert result["value"] == 62.0


def test_query_groups_and_ranks():
    result = run_cohort_query(_query_db(), group_by="gender")
    assert {r["sex"]: r["patients"] for r in result["results"]} == {"F": 5, "M": 5}


def test_query_unwinds_array_fields_and_labels_them():
    result = run_cohort_query(_query_db(), group_by="clinical_groups")
    assert {r["condition"]: r["patients"] for r in result["results"]} == {
        "Heart failure": 4, "Kidney disease": 3, "General recovery": 3}


def test_query_buckets_numbers_into_bands():
    result = run_cohort_query(_query_db(), group_by="anchor_age", bucket_size=20)
    assert [r["age"] for r in result["results"]] == ["20-39", "40-59", "60-79", "80-99"]


def test_a_distribution_is_never_truncated_by_top_n():
    """Cutting age bands at top_n hides the oldest patients, who are the point."""
    result = run_cohort_query(_query_db(), group_by="anchor_age", bucket_size=20, top_n=2)
    assert len(result["results"]) == 4


def test_ranked_grouping_still_honours_top_n():
    result = run_cohort_query(_query_db(), group_by="clinical_groups", top_n=1)
    assert len(result["results"]) == 1


def test_query_combines_filter_group_and_metric():
    result = run_cohort_query(
        _query_db(),
        filters=[{"field": "anchor_age", "op": "gte", "value": 55}],
        group_by="gender", metric="avg", metric_field="los_days")
    by_sex = {r["sex"]: r["avg length of stay in days"] for r in result["results"]}
    assert set(by_sex) == {"F", "M"}


@pytest.mark.parametrize("kwargs, why", [
    ({"filters": [{"field": "password", "op": "eq", "value": "x"}]}, "field not on the allow-list"),
    ({"filters": [{"field": "current_score", "op": "$where", "value": "1"}]}, "smuggled operator"),
    ({"filters": [{"field": "gender", "op": "gt", "value": "M"}]}, "text compared with >"),
    ({"group_by": "_id"}, "grouping by an unlisted field"),
    ({"group_by": "driver_1"}, "grouping by unlisted raw text"),
    ({"metric": "avg", "metric_field": "primary_diagnosis"}, "averaging text"),
    ({"metric": "exfiltrate", "metric_field": "anchor_age"}, "unknown metric"),
    ({"group_by": "gender", "bucket_size": 10}, "bucketing a text field"),
    ({"group_by": "anchor_age", "bucket_size": -5}, "negative bucket"),
])
def test_query_refuses_anything_not_on_the_allow_list(kwargs, why):
    """The model supplies this spec, so every part of it is validated here."""
    with pytest.raises(ValueError):
        run_cohort_query(_query_db(), **kwargs)


def test_query_suppresses_a_cohort_too_small_to_aggregate():
    result = run_cohort_query(
        _query_db(), filters=[{"field": "current_score", "op": "gte", "value": 95}],
        group_by="gender")
    assert result["results"] == []
    assert "too few" in result["note"]


def test_query_caps_the_number_of_groups():
    """A high-cardinality field must not return thousands of rows to the model."""
    client = mongomock.MongoClient()
    db = client["neuroshield"]
    db["patient_worklist"].insert_many([
        {"patient_id": f"M-{i}", "batch_date": LATEST_BATCH, "current_score": 50,
         "current_band": "medium", "primary_diagnosis": f"Diagnosis {i}"}
        for i in range(200)
    ])
    result = run_cohort_query(db, group_by="primary_diagnosis", top_n=9999)
    # Two ceilings exist and the tighter one wins: TOP_N_LIMIT bounds a ranked
    # grouping, MAX_GROUPS bounds a bucketed distribution.
    assert len(result["results"]) == TOP_N_LIMIT
    assert TOP_N_LIMIT <= MAX_GROUPS


# ---------------------------------------------------------------------------
# list_patients — the row-level layer
# ---------------------------------------------------------------------------

def _list_db():
    client = mongomock.MongoClient()
    database = client["neuroshield"]
    rows = [
        # id,   score, band,     age, sex, diagnosis,              groups
        ("L-1",  95, "High",   82, "F", "Heart failure, acute",   ["heart_failure"]),
        ("L-2",  88, "High",   74, "M", "Sepsis, unspecified",    ["general"]),
        ("L-3",  71, "High",   66, "F", "Acute kidney failure",   ["renal"]),
        ("L-4",  52, "Medium", 58, "M", "Type 2 diabetes",        ["diabetes"]),
        ("L-5",  31, "Low",    44, "F", "Pneumonia, unspecified", ["general"]),
    ]
    database["patient_worklist"].insert_many([
        {"patient_id": pid, "batch_date": LATEST_BATCH,
         "current_score": score, "current_band": band,
         "risk_score": score, "risk_band": band,
         "anchor_age": age, "gender": sex,
         "primary_diagnosis": dx, "clinical_groups": groups,
         "driver_1": f"driver one for {pid}", "driver_2": f"driver two for {pid}",
         "driver_3": f"driver three for {pid}"}
        for pid, score, band, age, sex, dx, groups in rows
    ])
    return database


def test_list_patients_defaults_to_top_by_score():
    result = list_patients(_list_db())
    assert [p["patient_id"] for p in result["patients"]] == ["L-1", "L-2", "L-3", "L-4", "L-5"]
    assert result["count_matching"] == 5


def test_list_patients_returns_requested_detail():
    """The question this whole function exists for: the diagnosis of the top 3."""
    result = list_patients(_list_db(), fields=["primary_diagnosis"], limit=3)
    assert result["returned"] == 3
    assert result["patients"][0]["principal diagnosis"] == "Heart failure, acute"
    # The ranking field is present even though it was not asked for — a "top 3
    # by risk" list that hides the risk is not an answer.
    assert result["patients"][0]["current risk score"] == 95


def test_list_patients_ascending_order():
    result = list_patients(_list_db(), sort_by="anchor_age", order="asc", limit=2)
    assert [p["patient_id"] for p in result["patients"]] == ["L-5", "L-4"]


def test_list_patients_applies_filters():
    result = list_patients(
        _list_db(), filters=[{"field": "current_band", "op": "eq", "value": "high"}])
    assert result["count_matching"] == 3
    assert {p["patient_id"] for p in result["patients"]} == {"L-1", "L-2", "L-3"}


def test_list_patients_reports_total_beyond_the_page():
    """The cap must not be mistaken for the answer to 'how many'."""
    result = list_patients(_list_db(), limit=2)
    assert result["returned"] == 2
    assert result["count_matching"] == 5


def test_list_patients_can_show_drivers():
    result = list_patients(_list_db(), fields=["driver_1"], limit=1)
    assert result["patients"][0]["top risk driver"] == "driver one for L-1"


def test_list_patients_maps_condition_codes_to_labels():
    result = list_patients(_list_db(), fields=["clinical_groups"], limit=1)
    assert result["patients"][0]["condition"] != ["heart_failure"]


def test_list_patients_empty_result_explains_itself():
    result = list_patients(
        _list_db(), filters=[{"field": "current_score", "op": "gt", "value": 99}])
    assert result["patients"] == []
    assert "note" in result


def test_list_patients_caps_limit():
    result = list_patients(_list_db(), limit=9999)
    assert result["returned"] <= LIST_PATIENTS_MAX


def test_list_patients_caps_columns():
    result = list_patients(
        _list_db(),
        fields=["anchor_age", "gender", "primary_diagnosis", "clinical_groups",
                "los_days", "n_prior_adm", "weeks_tracked", "discharge_score"],
        limit=1)
    # patient_id is always present and does not count toward the column budget.
    assert len(result["patients"][0]) <= MAX_LIST_COLUMNS + 1


@pytest.mark.parametrize("kwargs, why", [
    ({"fields": ["ssn"]}, "field not on the whitelist"),
    ({"sort_by": "salary"}, "sort field not on the whitelist"),
    ({"sort_by": "clinical_groups"}, "cannot rank by an array field"),
    ({"order": "sideways"}, "unknown order"),
    ({"limit": 0}, "non-positive limit"),
    ({"filters": [{"field": "notes", "op": "eq", "value": "x"}]}, "filter field not queryable"),
])
def test_list_patients_rejects(kwargs, why):
    with pytest.raises(ValueError):
        list_patients(_list_db(), **kwargs)


def test_list_patients_no_data_raises():
    client = mongomock.MongoClient()
    with pytest.raises(LookupError):
        list_patients(client["neuroshield"])


# ---------------------------------------------------------------------------
# The clinician directory
# ---------------------------------------------------------------------------
# Doctors are staff, not patients, so the cohort suppression above does not
# apply. What does need pinning is the one thing the directory holds that the
# chatbot must never read out: the email address.

from chatbot_queries import (  # noqa: E402
    get_alert_routing,
    get_doctor_workload,
    list_registered_doctors,
)


def _directory_db():
    client = mongomock.MongoClient()
    database = client["neuroshield"]
    database["doctors"].insert_many([
        {"doctor_id": "DR-hart", "name": "Dr. Hart", "specialty": "Cardiology",
         "email": "hart@example.org", "clinical_groups": ["heart_failure"],
         "active": True, "registered_at": "2026-09-01 10:00:00"},
        {"doctor_id": "DR-gen", "name": "Dr. Gen", "specialty": "Internal Medicine",
         "email": "gen@example.org", "clinical_groups": ["general"],
         "active": True, "registered_at": "2026-09-01 10:00:00"},
        {"doctor_id": "DR-gone", "name": "Dr. Gone", "specialty": "Oncology",
         "email": "gone@example.org", "clinical_groups": ["oncology"],
         "active": False, "registered_at": "2026-08-01 10:00:00"},
    ])
    database["clinical_alerts"].insert_many([
        {"alert_id": "A1", "patient_id": "P1", "week_number": 3, "doctor_id": "DR-hart",
         "doctor_name": "Dr. Hart", "severity": "critical", "status": "pending"},
        {"alert_id": "A2", "patient_id": "P1", "week_number": 4, "doctor_id": "DR-hart",
         "doctor_name": "Dr. Hart", "severity": "high", "status": "acknowledged"},
        {"alert_id": "A3", "patient_id": "P2", "week_number": 4, "doctor_id": "DR-gen",
         "doctor_name": "Dr. Gen", "severity": "high", "status": "pending"},
        {"alert_id": "A4", "patient_id": "P3", "week_number": 4, "doctor_id": "DR-gen",
         "doctor_name": "Dr. Gen", "severity": "high", "status": "responded"},
        {"alert_id": "A5", "patient_id": "P4", "week_number": 4, "doctor_id": None,
         "doctor_name": None, "severity": "critical", "status": "unrouted"},
    ])
    return database


def test_directory_lists_active_clinicians():
    result = list_registered_doctors(_directory_db())
    assert result["count"] == 2
    assert {d["name"] for d in result["doctors"]} == {"Dr. Hart", "Dr. Gen"}


def test_directory_never_returns_an_email_address():
    """The chatbot must not read staff contact details out on request."""
    result = list_registered_doctors(_directory_db())
    assert "email" not in str(result)
    for doctor in result["doctors"]:
        assert "email" not in doctor


def test_directory_does_not_leak_the_internal_id():
    result = list_registered_doctors(_directory_db())
    assert all("doctor_id" not in d for d in result["doctors"])


def test_directory_translates_condition_keys_to_labels():
    result = list_registered_doctors(_directory_db(), clinical_group="heart_failure")
    assert result["doctors"][0]["covers"] == ["Heart failure"]


def test_directory_filters_by_name():
    result = list_registered_doctors(_directory_db(), name="hart")
    assert result["count"] == 1
    assert result["doctors"][0]["name"] == "Dr. Hart"


def test_directory_filters_by_specialty_case_insensitively():
    assert list_registered_doctors(_directory_db(), specialty="cardiology")["count"] == 1


def test_directory_excludes_deactivated_clinicians_by_default():
    assert list_registered_doctors(_directory_db())["count"] == 2
    assert list_registered_doctors(_directory_db(), include_inactive=True)["count"] == 3


def test_directory_explains_an_empty_result():
    result = list_registered_doctors(_directory_db(), specialty="Nephrology")
    assert result["count"] == 0
    assert "unrouted queue" in result["note"]


def test_directory_rejects_an_unknown_condition():
    with pytest.raises(ValueError):
        list_registered_doctors(_directory_db(), clinical_group="astrology")


def test_workload_counts_open_alerts_only():
    result = get_doctor_workload(_directory_db())
    by_name = {w["doctor"]: w for w in result["workload"]}
    assert by_name["Dr. Hart"]["open_alerts"] == 2       # pending + acknowledged
    assert by_name["Dr. Gen"]["open_alerts"] == 1        # the responded one is closed
    assert result["responded_alerts"] == 1


def test_workload_separates_critical_and_unacknowledged():
    hart = next(w for w in get_doctor_workload(_directory_db())["workload"]
                if w["doctor"] == "Dr. Hart")
    assert hart["critical"] == 1
    assert hart["awaiting_acknowledgement"] == 1


def test_workload_distinguishes_alerts_from_patients():
    """Dr. Hart has two alerts for the same patient across two weeks."""
    hart = next(w for w in get_doctor_workload(_directory_db())["workload"]
                if w["doctor"] == "Dr. Hart")
    assert hart["open_alerts"] == 2
    assert hart["patients_with_open_alerts"] == 1


def test_workload_surfaces_unrouted_alerts():
    assert get_doctor_workload(_directory_db())["unrouted_alerts"] == 1


def test_workload_filters_by_name():
    result = get_doctor_workload(_directory_db(), doctor_name="Hart")
    assert [w["doctor"] for w in result["workload"]] == ["Dr. Hart"]


def test_routing_names_the_clinician_and_the_reason():
    result = get_alert_routing(_directory_db(), clinical_group="heart_failure")
    row = result["routing"][0]
    assert row["routes_to"] == "Dr. Hart"
    assert "heart failure" in row["because"]


def test_routing_falls_back_and_says_so():
    result = get_alert_routing(_directory_db(), clinical_group="renal")
    row = result["routing"][0]
    assert row["routes_to"] == "Dr. Gen"
    assert "internal medicine" in row["because"]


def test_routing_covers_every_condition_when_unfiltered():
    result = get_alert_routing(_directory_db())
    assert len(result["routing"]) == 11


def test_routing_flags_conditions_nobody_covers():
    db = mongomock.MongoClient()["empty"]
    result = get_alert_routing(db, clinical_group="oncology")
    assert result["routing"][0]["routes_to"] is None
    assert result["conditions_with_no_clinician"] == ["Cancer"]
    assert "unrouted queue" in result["note"]


def test_routing_rejects_an_unknown_condition():
    with pytest.raises(ValueError):
        get_alert_routing(_directory_db(), clinical_group="astrology")


# ---------------------------------------------------------------------------
# Closed vocabularies
# ---------------------------------------------------------------------------
# A filter on a value that does not exist used to return "No patients match
# that filter" — a confident zero where the true answer was 750.

from chatbot_queries import (  # noqa: E402
    describe_vocabularies,
    field_vocabulary,
    reset_vocabulary_cache,
)


@pytest.fixture(autouse=True)
def _clear_vocabulary_cache():
    """The cache is module-level; without this one case poisons the next."""
    reset_vocabulary_cache()
    yield
    reset_vocabulary_cache()


def _vocab_db():
    client = mongomock.MongoClient()
    database = client["neuroshield"]
    database["patient_worklist"].insert_many([
        {"patient_id": f"V-{i}", "batch_date": LATEST_BATCH,
         "current_score": 50.0, "current_band": "Medium",
         "risk_score": 50.0, "risk_band": "Medium",
         "monitoring_status": status, "group_label": "Heart failure",
         "anchor_age": 70}
        for i, status in enumerate(["stable", "deteriorating", "improving",
                                    "stable", "deteriorating", "action_required"])
    ])
    return database


def test_vocabulary_is_read_from_the_data():
    assert field_vocabulary(_vocab_db(), "monitoring_status") == (
        "action_required", "deteriorating", "improving", "stable")


def test_declared_vocabularies_do_not_query_the_database():
    """current_band declares its values, so no distinct() is needed."""
    assert field_vocabulary(mongomock.MongoClient()["empty"], "current_band") == (
        "Low", "Medium", "High")


def test_fields_without_a_vocabulary_return_none():
    assert field_vocabulary(_vocab_db(), "primary_diagnosis") is None
    assert field_vocabulary(_vocab_db(), "anchor_age") is None


def test_a_wrong_value_is_rejected_with_the_valid_ones():
    with pytest.raises(ValueError) as exc:
        run_cohort_query(_vocab_db(),
                         filters=[{"field": "monitoring_status", "op": "eq",
                                   "value": "declining"}])
    message = str(exc.value)
    assert "declining" in message
    assert "deteriorating" in message


def test_a_wrong_value_no_longer_reads_as_an_answer():
    """The regression this exists for: a plausible typo returning a hard zero."""
    with pytest.raises(ValueError):
        run_cohort_query(_vocab_db(),
                         filters=[{"field": "monitoring_status", "op": "eq",
                                   "value": "worsening"}])


def test_a_right_value_in_the_wrong_case_still_matches():
    result = run_cohort_query(_vocab_db(),
                              filters=[{"field": "monitoring_status", "op": "eq",
                                        "value": "DETERIORATING"}])
    assert result["value"] == 2


def test_the_in_operator_validates_every_value():
    with pytest.raises(ValueError):
        run_cohort_query(_vocab_db(),
                         filters=[{"field": "monitoring_status", "op": "in",
                                   "value": ["stable", "declining"]}])


def test_free_text_fields_are_not_constrained():
    """primary_diagnosis has thousands of values; there is no set to enforce.
    The value reaches the database, and an empty answer comes back as advice to
    ask by condition, not as a list of every diagnosis."""
    with pytest.raises(ValueError) as caught:
        run_cohort_query(_vocab_db(), filters=[{"field": "primary_diagnosis", "op": "eq",
                                                "value": "Anything At All"}])
    assert "filter clinical_groups instead" in str(caught.value)
    assert "Valid values" not in str(caught.value)


def test_the_catalog_lists_every_enumerable_field():
    catalog = describe_vocabularies(_vocab_db())
    assert "monitoring_status: action_required, deteriorating" in catalog
    assert "current_band: Low, Medium, High" in catalog
    assert "primary_diagnosis" not in catalog


def test_a_high_cardinality_field_reports_no_vocabulary():
    """Above the limit a 'closed' set is not closed enough to state or enforce."""
    client = mongomock.MongoClient()
    database = client["neuroshield"]
    database["patient_worklist"].insert_many([
        {"patient_id": f"W-{i}", "batch_date": LATEST_BATCH,
         "group_label": f"Condition {i}"}
        for i in range(40)
    ])
    assert field_vocabulary(database, "group_label") is None


# ---------------------------------------------------------------------------
# Directory coverage and near-miss names
# ---------------------------------------------------------------------------

def test_coverage_includes_the_specialty_fallback():
    """
    A psychiatrist who ticked no conditions still receives every mental-health
    alert. The directory used to report that nobody covered it while
    get_alert_routing named him - two tools contradicting each other.
    """
    db = _directory_db()
    db["doctors"].insert_one({
        "doctor_id": "DR-psych", "name": "Dr. Mind", "specialty": "Psychiatry",
        "email": "mind@example.org", "clinical_groups": [], "active": True})
    result = list_registered_doctors(db, clinical_group="mental_health")
    assert [d["name"] for d in result["doctors"]] == ["Dr. Mind"]
    assert result["doctors"][0]["reached_because"] == "Psychiatry on call"


def test_coverage_says_when_the_condition_was_claimed():
    result = list_registered_doctors(_directory_db(), clinical_group="heart_failure")
    assert result["doctors"][0]["reached_because"] == "claims this condition"


def test_the_directory_and_routing_agree():
    """Whatever route() would pick must appear in the directory's coverage."""
    db = _directory_db()
    db["doctors"].insert_one({
        "doctor_id": "DR-psych", "name": "Dr. Mind", "specialty": "Psychiatry",
        "email": "mind@example.org", "clinical_groups": [], "active": True})
    for group in ("heart_failure", "mental_health", "general"):
        routed = get_alert_routing(db, clinical_group=group)["routing"][0]["routes_to"]
        covering = [d["name"] for d in
                    list_registered_doctors(db, clinical_group=group)["doctors"]]
        assert routed in covering, f"{group}: route() picked {routed}, not in {covering}"


def test_a_misspelt_name_suggests_the_real_one():
    """The directory holds 'Dr. Partrick Jane'; someone types 'Patrick'."""
    db = _directory_db()
    db["doctors"].insert_one({
        "doctor_id": "DR-pj", "name": "Dr. Partrick Jane", "specialty": "Psychiatry",
        "email": "pj@example.org", "clinical_groups": [], "active": True})
    result = list_registered_doctors(db, name="Patrick Jane")
    assert result["count"] == 0
    assert result["closest_matches"] == ["Dr. Partrick Jane"]


def test_an_exact_match_carries_no_suggestions():
    result = list_registered_doctors(_directory_db(), name="Hart")
    assert result["count"] == 1
    assert result["closest_matches"] is None


def test_a_name_nothing_resembles_suggests_nothing():
    result = list_registered_doctors(_directory_db(), name="Zzyzx")
    assert result["count"] == 0
    assert result["closest_matches"] is None


# ---------------------------------------------------------------------------
# Conditions mean what the patient list means, and the care team is known
# ---------------------------------------------------------------------------
# "Patients with heart disease" answered for the 271 MONITORED as heart disease
# while the list's Condition filter showed the 547 who HAVE it - and missed the
# highest-risk one, monitored as heart failure. And "who is this patient's
# doctor?" had no answer: the care team lives in care_actions.

@pytest.fixture
def conditions_db():
    from chatbot_queries import reset_vocabulary_cache
    reset_vocabulary_cache()
    database = mongomock.MongoClient()["neuroshield"]
    database["patient_worklist"].insert_many([
        {"patient_id": "HF-1", "batch_date": LATEST_BATCH, "current_score": 95.1, "current_band": "High",
         "group_label": "Heart failure", "clinical_groups": ["heart_failure", "cardiac_other"]},
        {"patient_id": "HD-1", "batch_date": LATEST_BATCH, "current_score": 79.5, "current_band": "High",
         "group_label": "Heart disease", "clinical_groups": ["cardiac_other"]},
        {"patient_id": "DM-1", "batch_date": LATEST_BATCH, "current_score": 40.0, "current_band": "High",
         "group_label": "Diabetes", "clinical_groups": ["diabetes"]},
    ])
    yield database
    reset_vocabulary_cache()


def test_a_condition_by_its_screen_name_matches_everyone_who_has_it(conditions_db):
    result = list_patients(conditions_db, filters=[{"field": "clinical_groups", "op": "eq",
                                                    "value": "Heart disease"}])
    assert result["count_matching"] == 2
    assert result["patients"][0]["patient_id"] == "HF-1"          # monitored as heart failure, still counted
    assert "Heart disease" in result["filter"] and "cardiac_other" not in result["filter"]


def test_monitored_as_is_still_its_own_question(conditions_db):
    result = list_patients(conditions_db, filters=[{"field": "group_label", "op": "eq", "value": "Heart disease"}])
    assert [p["patient_id"] for p in result["patients"]] == ["HD-1"]


def test_an_unknown_condition_names_the_real_ones(conditions_db):
    with pytest.raises(ValueError, match=r"cardiac_other \(Heart disease\)"):
        list_patients(conditions_db, filters=[{"field": "clinical_groups", "op": "eq", "value": "Gout"}])


def test_patient_details_carry_the_care_team(conditions_db):
    conditions_db["doctors"].insert_one({"doctor_id": "DR-1", "name": "Dr Emily Carter",
                                         "specialty": "Cardiology"})
    nurse = conditions_db.client["shared_identity"]["users"].insert_one(
        {"email": "grace@h.test", "name": "Grace Taylor", "role": "nurse"}).inserted_id
    conditions_db["care_actions"].insert_one({"patient_id": "HD-1", "assigned_doctor_id": "DR-1",
                                              "assigned_nurse_ids": [str(nurse)]})
    team = get_patient_details(conditions_db, "HD-1")["care_team"]
    assert team == {"doctor": "Dr Emily Carter (Cardiology)", "nurses": ["Grace Taylor"]}
    assert get_patient_details(conditions_db, "DM-1")["care_team"] == {
        "doctor": "no doctor assigned", "nurses": "no nurse assigned"}


# ---------------------------------------------------------------------------
# The assigned doctor, as a field like any other: filter, group and list by it.

@pytest.fixture
def care_team_db(conditions_db):
    conditions_db["patient_worklist"].insert_many([
        {"patient_id": f"P-{i}", "batch_date": LATEST_BATCH, "current_score": 50 + i,
         "current_band": "High", "clinical_groups": ["cardiac_other", "diabetes"]} for i in range(5)])
    conditions_db["doctors"].insert_many([
        {"doctor_id": "DR-1", "name": "Dr Emily Carter", "specialty": "Cardiology", "active": True},
        {"doctor_id": "DR-2", "name": "Dr Michael Turner", "specialty": "Internal Medicine", "active": True}])
    conditions_db["care_actions"].insert_many(
        [{"patient_id": "HF-1", "assigned_doctor_id": "DR-1"},
         {"patient_id": "HD-1", "assigned_doctor_id": "DR-1"}]
        + [{"patient_id": f"P-{i}", "assigned_doctor_id": "DR-2"} for i in range(5)])
    return conditions_db


def test_two_conditions_both_hold(care_team_db):
    """'Heart disease and diabetes' used to become diabetes alone: the second
    filter on the same field replaced the first."""
    both = run_cohort_query(care_team_db, filters=[
        {"field": "clinical_groups", "op": "eq", "value": "Heart disease"},
        {"field": "clinical_groups", "op": "eq", "value": "Diabetes"}])
    assert both["value"] == 5                  # P-0..4; not DM-1, which has no heart disease


def test_which_doctor_has_the_most_high_risk_patients(care_team_db):
    result = run_cohort_query(care_team_db, filters=[{"field": "current_band", "op": "eq", "value": "High"}],
                              group_by="doctor")
    assert [(r["assigned doctor"], r["patients"]) for r in result["results"]] == [
        ("Dr Michael Turner", 5), ("Dr Emily Carter", 2), ("No doctor assigned", 1)]


def test_a_doctor_by_name_finds_their_patients_misspelt_or_not(care_team_db):
    for name in ("Dr Emily Carter", "emily carter", "Carter", "Emely Carter"):
        result = list_patients(care_team_db, filters=[{"field": "doctor", "op": "eq", "value": name}],
                               fields=["current_score", "doctor"])
        assert [p["patient_id"] for p in result["patients"]] == ["HF-1", "HD-1"], name
        assert {p["assigned doctor"] for p in result["patients"]} == {"Dr Emily Carter"}
    assert "closest match" in list_patients(
        care_team_db, filters=[{"field": "doctor", "op": "eq", "value": "Emely Carter"}])["filter"]
    with pytest.raises(ValueError, match="No doctor is registered as 'Dr Zhivago'"):
        list_patients(care_team_db, filters=[{"field": "doctor", "op": "eq", "value": "Dr Zhivago"}])
    with pytest.raises(ValueError, match="Cannot sort by 'doctor'"):
        list_patients(care_team_db, sort_by="doctor")


def test_patients_with_no_doctor(care_team_db):
    result = list_patients(care_team_db, filters=[{"field": "doctor", "op": "eq", "value": "none"}])
    assert [p["patient_id"] for p in result["patients"]] == ["DM-1"]


def test_workload_counts_each_doctors_patients_and_matches_a_misspelling(care_team_db):
    result = get_doctor_workload(care_team_db, doctor_name="Emely Carter")
    assert [(w["doctor"], w["assigned_patients"], w["high_risk_patients"], w["open_alerts"])
            for w in result["workload"]] == [("Dr Emily Carter", 2, 2, 0)]
    assert result["filter"] == "Dr Emily Carter (closest match to 'Emely Carter')"


def test_routing_takes_a_condition_by_its_name():
    assert get_alert_routing(_directory_db(), clinical_group="Heart failure")["routing"][0]["routes_to"] \
        == "Dr. Hart"


def test_the_cohort_summaries_take_field_filters(care_team_db):
    """'Drivers for patients who went from Low to High' had only a score range
    to reach for, and the answer described everyone above 70."""
    care_team_db["patient_worklist"].update_many({"patient_id": {"$regex": "^P-"}},
                                                {"$set": {"risk_band": "Low", "driver_1": "Albumin: 2.1"}})
    moved = [{"field": "risk_band", "op": "eq", "value": "Low"},
             {"field": "current_band", "op": "eq", "value": "High"}]
    result = get_common_drivers(care_team_db, filters=moved)
    assert result["cohort_size"] == 5 and result["results"][0]["driver"] == "Albumin"
    assert "risk band at discharge eq Low" in result["filter"]
    assert count_patients(care_team_db, filters=[{"field": "doctor", "op": "eq", "value": "Carter"}])[
        "count"] == 2


def test_patient_details_name_every_condition(conditions_db):
    details = get_patient_details(conditions_db, "HF-1")
    assert details["conditions"] == ["Heart failure", "Heart disease"]
    assert details["monitored_as"] == "Heart failure" and "clinical_groups" not in details


def test_a_diagnosis_by_a_short_name_asks_again_by_condition(conditions_db):
    attack = [{"field": "primary_diagnosis", "op": "eq", "value": "heart attack"}]
    for ask in (run_cohort_query, list_patients, count_patients, get_common_drivers):
        with pytest.raises(ValueError, match="filter clinical_groups instead"):
            ask(conditions_db, filters=attack)



# ---------------------------------------------------------------------------
# Which score, and when; and follow-ups on the patients an answer listed.

@pytest.fixture
def scored_db(conditions_db):
    conditions_db["patient_worklist"].update_many({}, {"$set": {"discharge_date": "2011-01-27",
                                                               "discharge_score": 57.5, "trend_delta": 10.0}})
    conditions_db["patient_worklist"].update_one({"patient_id": "HF-1"}, {"$set": {"trend_delta": 38.5}})
    conditions_db["weekly_monitoring"].insert_many([
        {"patient_id": "HF-1", "week_number": 0, "week_date": "2011-01-27"},
        {"patient_id": "HF-1", "week_number": 4, "week_date": "2011-02-24"},
        {"patient_id": "HF-1", "week_number": 2, "week_date": "2011-02-10"}])
    return conditions_db


def test_every_score_says_which_it_is_and_when(scored_db):
    """"Risk score 96.0" said neither which score nor when."""
    top = get_top_risk_patients(scored_db, 1)[0]
    assert (top["current risk score"], top["current score as of"]) == (95.1, "2011-02-24")
    assert (top["risk score at discharge"], top["discharge date"]) == (57.5, "2011-01-27")
    listed = list_patients(scored_db, fields=["current_score", "trend_delta"], limit=1)["patients"][0]
    assert (listed["current score as of"], listed["discharge date"]) == ("2011-02-24", "2011-01-27")
    details = get_patient_details(scored_db, "HF-1")
    assert (details["current risk score"], details["risk score at discharge"]) == (95.1, 57.5)
    assert details["current score as of"] == "2011-02-24" and "risk_score" not in details


def test_a_follow_up_can_ask_about_the_patients_just_listed(scored_db):
    """"Which of those has changed most" could only look at one of them."""
    those = "HD-1, HF-1"                                   # as the model sends it: one string
    result = list_patients(scored_db, filters=[{"field": "patient_id", "op": "in", "value": those}],
                           sort_by="trend_delta", fields=["trend_delta"])
    assert [p["patient_id"] for p in result["patients"]] == ["HF-1", "HD-1"]
    assert result["count_matching"] == 2

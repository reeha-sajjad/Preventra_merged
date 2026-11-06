"""Who comes back (api/outcomes.py): the three Outcomes panels that compare
readmitted patients with those who were not - who they were, their conditions,
and their last weekly readings - and the Check-ins sent tile.

Same accounts as test_isolation.py. Every patient added here was discharged on
10 April 2026 and has a known 30-day outcome recorded by the care team. Any
group of 1-10 patients must be missing from the JSON itself, not just hidden by
the page.
"""
import datetime as dt

import mongomock
import pytest

from api import access, outcomes
from ingestion import scheduler
from models import mood_screen
from tests.units.test_isolation import A1, A2, LATEST, as_user, main, patient_row, world  # noqa: F401


@pytest.fixture
def clock(monkeypatch):
    monkeypatch.setattr(scheduler, "today", lambda: dt.date(2026, 6, 1))


def add(db, pid, hospital="hosp-a", readmitted=False, back="2026-04-20", **row):
    """A patient of `hospital`, discharged 10 April, whose outcome is known."""
    db["care_actions"].insert_one({"patient_id": pid, "hospital_id": hospital, "notes": [],
                                   "assigned_nurse_ids": []})
    db["patient_worklist"].insert_one({**patient_row(pid, LATEST, 30), **row})
    db["readmission_outcomes"].insert_one({
        "patient_id": pid, "discharge_date": "2026-04-10", "readmitted": readmitted,
        "readmit_date": back if readmitted else None, "discharge_score": 30.0, "discharge_band": "Low",
        "hospital_id": hospital})
    access.forget_hospital_lists()


def ids(n, first):
    return [f"MIMIC-{first + i}" for i in range(n)]


def detail(main, world, email="cm@a.test"):
    return as_user(main, world, email).get("/api/outcomes").json()["readmission_detail"]


def group(detail_, dimension, key):
    dim = next(d for d in detail_["who"] if d["key"] == dimension)
    return next(g for g in dim["groups"] if g["key"] == key)


# ------------------------------------------------------------ too few outcomes
def test_too_few_known_outcomes_say_how_many_are_needed(main, world, clock):
    for pid in ids(3, 3000):
        add(world["db"], pid)
    assert detail(main, world) == {"min_group": 11, "known": 3, "enough": False}


# ------------------------------------------------------------ panel 1
def test_rates_by_group_hide_small_groups_and_the_one_beside_them(main, world, clock):
    db = world["db"]
    for i, pid in enumerate(ids(13, 3000)):
        add(db, pid, readmitted=i < 4, anchor_age=70, gender="F")
    for i, pid in enumerate(ids(12, 3100)):
        add(db, pid, readmitted=i < 3, anchor_age=50, gender="F")
    for i, pid in enumerate(ids(3, 3200)):
        add(db, pid, readmitted=i < 1, anchor_age=90, gender="F")
    d = detail(main, world)
    assert d["enough"] and d["known"] == 28
    shown = group(d, "age", "65_74")
    assert (shown["known"], shown["readmitted"], shown["observed_rate"], shown["hidden"]) == (13, 4, 30.8, None)
    old = group(d, "age", "85_plus")
    assert old["hidden"] == "small" and all(old[k] is None for k in outcomes.BLOCK)
    # One group is hidden, so the smallest group left goes with it.
    middle = group(d, "age", "45_64")
    assert middle["hidden"] == "complement" and all(middle[k] is None for k in outcomes.BLOCK)
    assert group(d, "age", "under_45")["known"] == 0
    for dimension in ("sex", "prior", "insurance"):
        everyone = group(d, dimension, "female" if dimension == "sex" else "unknown")
        assert everyone["known"] == 28 and everyone["hidden"] is None
    assert group(d, "age", "85_plus") == {"key": "85_plus", "label": "85 and over", "known": None,
                                          "readmitted": None, "observed_rate": None, "expected_rate": None,
                                          "ratio": None, "hidden": "small"}


def test_insurance_and_destination_come_from_stored_model_inputs(main, world, clock):
    db = world["db"]
    for i, pid in enumerate(ids(23, 3000)):
        add(db, pid, readmitted=i % 4 == 0)
        if i < 12:
            db["model_inputs"].insert_one({"patient_id": pid, "current": True,
                                           "features": {"insurance": "Medicare", "disch_snf": 1,
                                                        "disch_home": 0, "disch_ama": 0}})
    d = detail(main, world)
    assert group(d, "insurance", "medicare")["known"] == 12
    assert group(d, "destination", "facility")["known"] == 12
    assert group(d, "insurance", "unknown")["known"] == 11
    assert group(d, "destination", "unknown")["known"] == 11
    assert all(g["hidden"] is None for dim in d["who"] for g in dim["groups"])


# ------------------------------------------------------------ panel 2
def test_disease_mix_compares_with_and_without_each_condition(main, world, clock):
    db = world["db"]
    for i, pid in enumerate(ids(12, 3000)):
        add(db, pid, readmitted=i < 5, clinical_groups=["heart_failure"])
    for i, pid in enumerate(ids(12, 3100)):
        add(db, pid, readmitted=i < 2, clinical_group="diabetes", clinical_groups=["diabetes"])
    for pid in ids(2, 3200):
        add(db, pid, readmitted=True, clinical_group="oncology", clinical_groups=["oncology"])
    mix = detail(main, world)["conditions"]
    rows = {c["key"]: c for c in mix["conditions"]}
    failure = rows["heart_failure"]
    assert (failure["with"]["known"], failure["without"]["known"], failure["hidden"]) == (12, 14, None)
    assert failure["with"]["readmitted"] == 5 and failure["with"]["observed_rate"] == 41.7
    cancer = rows["oncology"]
    assert cancer["hidden"] == "small"
    assert all(cancer[side][k] is None for side in ("with", "without") for k in outcomes.BLOCK)
    assert "general" not in rows and "renal" not in rows
    assert mix["conditions"][-1]["key"] == "oncology" and mix["unknown"] == 0


@pytest.mark.parametrize("known_groups, unknown", [(24, 3), (3, 24), (12, 11)])
def test_disease_mix_never_gives_away_the_discharges_with_no_diagnoses(main, world, clock, known_groups, unknown):
    db = world["db"]
    for pid in ids(known_groups, 3000):
        add(db, pid, clinical_groups=["heart_failure"])
    for pid in ids(unknown, 3100):
        add(db, pid, discharge_date="2026-03-01")        # the worklist row is for another discharge: no diagnoses
    d = detail(main, world)
    mix = d["conditions"]
    if unknown == 11:
        assert (mix["known"], mix["unknown"]) == (12, 11)
        assert mix["conditions"][0]["with"]["known"] == 12 and mix["conditions"][0]["hidden"] is None
        return
    assert (mix["known"], mix["unknown"]) == (None, None)
    assert all(c["hidden"] == "small" and c["with"]["known"] is None and c["without"]["known"] is None
               for c in mix["conditions"])
    assert d["overall"]["known"] == known_groups + unknown        # the total alone gives neither part away


# ------------------------------------------------------------ panel 3
def test_the_last_reading_is_the_one_before_the_readmission():
    day = dt.date(2026, 4, 10)
    weeks = [{"week_number": 1, "week_date": "2026-04-17", "observed": True},
             {"week_number": 2, "week_date": "2026-04-24", "observed": True}]
    last, quiet = outcomes._last_reading(weeks, day, dt.date(2026, 4, 22), True)
    assert last["week_number"] == 1 and quiet is False       # the week after the readmission is not counted
    weeks = [{"week_number": 1, "week_date": "2026-04-17", "observed": True},
             {"week_number": 2, "week_date": "2026-04-24", "observed": True},
             {"week_number": 3, "week_date": "2026-05-01", "observed": False},
             {"week_number": 5, "week_date": "2026-05-15", "observed": True}]
    last, quiet = outcomes._last_reading(weeks, day, dt.date(2026, 5, 10), False)
    assert last["week_number"] == 2 and quiet is True        # week 3 had nothing in it; week 5 is past day 30
    assert outcomes._last_reading([], day, dt.date(2026, 5, 10), False) == (None, False)


@pytest.mark.parametrize("last, quiet, sign, expected", [
    ({"clinical_group": "heart_failure", "monitoring": {"weight_change_kg": 1.2}}, False, "weight_gain", True),
    ({"clinical_group": "oncology", "monitoring": {"weight_change_kg": 1.2}}, False, "weight_gain", False),
    ({"clinical_group": "oncology", "monitoring": {"weight_change_kg": 2.5}}, False, "weight_gain", True),
    ({"monitoring": {}}, False, "weight_gain", None),
    ({"clinical_group": "general", "monitoring": {"spo2": 90}}, False, "low_oxygen", True),
    ({"clinical_group": "respiratory", "monitoring": {"spo2": 90}}, False, "low_oxygen", False),
    ({"monitoring": {"adherence_pct": 79}}, False, "missed_medicines", True),
    ({"monitoring": {"adherence_pct": 80}}, False, "missed_medicines", False),
    ({"monitoring": {"refill_status": "missed"}}, False, "missed_medicines", True),
    ({"monitoring": {}}, False, "missed_medicines", None),
    ({"monitoring": {"followup_status": "missed"}}, False, "missed_followup", True),
    ({"monitoring": {"followup_status": "attended"}}, False, "missed_followup", False),
    ({"monitoring": {}}, True, "no_contact", True),
    ({"monitoring": {}, "red_flags": ["spo2"]}, False, "red_flag", True),
    ({"monitoring": {}, "mood": {"phq2": 3}}, False, "low_mood", True),
    ({"monitoring": {}, "mood": {"phq2": 2, "phq8": None}}, False, "low_mood", False),
    ({"monitoring": {}, "mood": {"phq2": 3, "phq8": 4}}, False, "low_mood", True),
    ({"monitoring": {}}, False, "low_mood", None),
])
def test_each_warning_sign_uses_the_existing_threshold(last, quiet, sign, expected):
    assert outcomes._signs_at(last, quiet)[sign] is expected


def test_readings_before_readmission_compare_the_groups(main, world, clock):
    db = world["db"]

    def weeks(pid, *series):
        for number, day, kg in series:
            db["weekly_monitoring"].insert_one({
                "patient_id": pid, "week_number": number, "week_date": day, "observed": True,
                "monitoring": {"weight_change_kg": kg}, "red_flags": [], "clinical_group": "heart_failure",
                "source": "simulated"})
    for i, pid in enumerate(ids(23, 3000)):
        add(db, pid, readmitted=True, back="2026-04-20")
        if i < 12:
            weeks(pid, (1, "2026-04-17", 2.5), (2, "2026-04-24", 0))      # the second is after the readmission
    for pid in ids(12, 3100):
        add(db, pid)
        weeks(pid, (4, "2026-05-08", 0))
    lr = detail(main, world)["last_readings"]
    assert lr["readmitted"] == {"known": 23, "with_readings": 12, "no_readings": 11}
    assert lr["not_readmitted"] == {"known": 12, "with_readings": 12, "no_readings": 0}
    assert lr["hidden"] is None and lr["simulated"] == 24 and lr["undated"] == 0
    first = lr["signs"][0]
    assert first["key"] == "weight_gain" and first["ratio"] is None and first["hidden"] is None
    assert (first["readmitted"]["share"], first["not_readmitted"]["share"]) == (100.0, 0.0)
    mood = next(s for s in lr["signs"] if s["key"] == "low_mood")
    assert mood["readmitted"]["reported"] == 0 and mood["not_readmitted"]["reported"] == 0
    assert mood["hidden"] is None


def test_a_sign_reported_by_too_few_is_hidden_in_both_groups(main, world, clock):
    db = world["db"]
    for i, pid in enumerate(ids(12, 3000)):
        add(db, pid, readmitted=True)
        db["weekly_monitoring"].insert_one({
            "patient_id": pid, "week_number": 1, "week_date": "2026-04-17", "observed": True,
            "monitoring": {"weight_change_kg": 0}, "red_flags": [], "clinical_group": "heart_failure",
            "mood": {"phq2": 3, "phq8": 11} if i < 4 else None})
    for pid in ids(12, 3100):
        add(db, pid)
        db["weekly_monitoring"].insert_one({
            "patient_id": pid, "week_number": 4, "week_date": "2026-05-08", "observed": True,
            "monitoring": {"weight_change_kg": 0}, "red_flags": [], "clinical_group": "heart_failure",
            "mood": {"phq2": 0, "phq8": None}})
    lr = detail(main, world)["last_readings"]
    mood = next(s for s in lr["signs"] if s["key"] == "low_mood")
    assert mood["hidden"] == "small" and mood["ratio"] is None
    assert mood["readmitted"] == {"reported": None, "with": None, "share": None}
    assert mood["not_readmitted"] == {"reported": None, "with": None, "share": None}
    assert lr["signs"][-1]["key"] == "low_mood"                       # hidden rows go last


def panel_three(db, readmitted_read, readmitted_none, undated, not_read, not_none, first=3000):
    """Patients whose last readings are in weekly_monitoring: `readmitted_none`
    readmitted patients with no reading, `undated` readmitted with no date."""
    def weekly(pid, day):
        db["weekly_monitoring"].insert_one({
            "patient_id": pid, "week_number": 1, "week_date": day, "observed": True,
            "monitoring": {"weight_change_kg": 0}, "red_flags": [], "clinical_group": "heart_failure"})
    batches = ((readmitted_read, True, "2026-04-20", True), (readmitted_none, True, "2026-04-20", False),
               (undated, True, None, False), (not_read, False, None, True), (not_none, False, None, False))
    for n, (count, back, date, reading) in zip(range(5), batches):
        for pid in ids(count, first + n * 100):
            add(db, pid, readmitted=back, back=date)
            if reading:
                weekly(pid, "2026-04-17" if back else "2026-05-08")


def sizes_in(lr):
    """Every count the panel sends, and what the page's own totals give away."""
    sent = [n for side in ("readmitted", "not_readmitted") for n in lr[side].values()]
    sent += [lr["undated"], lr["simulated"]]
    return [n for n in sent if n is not None]


def small(n):
    return n is not None and 0 < n < outcomes.MIN_GROUP


@pytest.mark.parametrize("counts", [
    (12, 2, 0, 12, 0),        # a few readmitted with no reading
    (3, 12, 0, 12, 0),        # a few readmitted with a reading
    (12, 0, 3, 12, 0),        # a few readmissions with no date
    (12, 0, 0, 12, 4),        # a few who were not readmitted, with no reading
    (12, 2, 0, 12, 15),       # one small group beside a large one, so their sum is large
])
def test_panel_three_never_gives_away_a_small_group(main, world, clock, counts):
    panel_three(world["db"], *counts)
    d = detail(main, world)
    lr = d["last_readings"]
    assert lr["hidden"] == "small" and lr["signs"] == []
    assert lr["readmitted"] == {"known": None, "with_readings": None, "no_readings": None}
    assert lr["not_readmitted"] == {"known": None, "with_readings": None, "no_readings": None}
    assert lr["undated"] is None and lr["simulated"] is None
    # Nothing sent, and the page's own totals leave nothing to subtract from.
    dated = d["overall"]["readmitted"] - (lr["undated"] or 0)
    assert dated == counts[0] + counts[1] + counts[2] and not any(small(n) for n in sizes_in(lr))


def test_panel_three_sends_plain_counts_when_every_group_is_large(main, world, clock):
    panel_three(world["db"], 12, 11, 11, 12, 11)
    d = detail(main, world)
    lr = d["last_readings"]
    assert lr["readmitted"] == {"known": 23, "with_readings": 12, "no_readings": 11}
    assert lr["not_readmitted"] == {"known": 23, "with_readings": 12, "no_readings": 11}
    assert lr["undated"] == 11 and lr["hidden"] is None and lr["signs"]
    # Every part of the totals is large or nothing: no subtraction gives a small group.
    totals = (d["overall"]["readmitted"], d["overall"]["known"] - d["overall"]["readmitted"])
    assert totals[0] - lr["readmitted"]["with_readings"] - lr["undated"] == 11
    assert totals[1] - lr["not_readmitted"]["with_readings"] == 11


def test_a_sign_left_unreported_by_a_few_is_hidden(main, world, clock):
    db = world["db"]
    for i, pid in enumerate(ids(24, 3000)):
        add(db, pid, readmitted=i < 12, back="2026-04-20")
        # Everyone reports their weight; all but 3 of each group report their mood.
        db["weekly_monitoring"].insert_one({
            "patient_id": pid, "week_number": 1, "week_date": "2026-04-17" if i < 12 else "2026-05-08",
            "observed": True, "monitoring": {"weight_change_kg": 0}, "red_flags": [],
            "clinical_group": "heart_failure",
            "mood": {"phq2": 0, "phq8": None} if i % 12 < 9 else None})
    signs = {s["key"]: s for s in detail(main, world)["last_readings"]["signs"]}
    assert signs["low_mood"]["hidden"] == "small"
    assert signs["low_mood"]["readmitted"] == {"reported": None, "with": None, "share": None}
    assert signs["weight_gain"]["hidden"] is None and signs["weight_gain"]["readmitted"]["reported"] == 12


@pytest.mark.parametrize("low_readmitted, low_not, shown", [(2, 1, False), (7, 6, True), (11, 0, True), (0, 0, True)])
def test_a_sign_that_few_had_is_hidden_across_both_groups(main, world, clock, low_readmitted, low_not, shown):
    db = world["db"]
    for i, pid in enumerate(ids(24, 3000)):
        mine = i < 12
        low = i < low_readmitted if mine else i - 12 < low_not
        add(db, pid, readmitted=mine, back="2026-04-20")
        db["weekly_monitoring"].insert_one({           # everyone reported their oxygen
            "patient_id": pid, "week_number": 1, "week_date": "2026-04-17" if mine else "2026-05-08",
            "observed": True, "monitoring": {"spo2": 88 if low else 97}, "red_flags": [],
            "clinical_group": "heart_failure"})
    oxygen = next(s for s in detail(main, world)["last_readings"]["signs"] if s["key"] == "low_oxygen")
    if shown:
        assert oxygen["hidden"] is None and oxygen["readmitted"]["reported"] == 12
        assert oxygen["readmitted"]["with"] == low_readmitted and oxygen["not_readmitted"]["with"] == low_not
    else:
        assert oxygen["hidden"] == "small" and oxygen["ratio"] is None
        assert oxygen["readmitted"] == oxygen["not_readmitted"] == {"reported": None, "with": None, "share": None}


def test_an_ingested_readmission_reads_its_weeks_from_ingest_weeks():
    db = mongomock.MongoClient()["neuroshield"]
    left = dt.datetime(2026, 4, 10, 12)
    db["ingest_stays"].insert_many([
        {"hospital_id": "hosp-a", "encounter_key": "E-1", "patient_id": "HA-1", "readmitted_30d": True,
         "encounter": {"admit_datetime": dt.datetime(2026, 4, 1, 9), "discharge_datetime": left,
                       "age_years": 77, "payer_class": "MEDICAID", "discharge_disposition": "SNF"},
         "patient": {"sex": "M"},
         "diagnoses": [{"icd_code": "I50.9", "description": "Heart failure", "sequence": 1}],
         "discharge_vitals": {"weight_kg": 80.0}},
        # The stay the patient came back for.
        {"hospital_id": "hosp-a", "encounter_key": "E-2", "patient_id": "HA-1", "readmitted_30d": None,
         "encounter": {"admit_datetime": dt.datetime(2026, 4, 25, 9), "discharge_datetime": dt.datetime(2026, 5, 1, 9)}},
        # Another hospital's stay: same patient id and discharge date, never read.
        {"hospital_id": "hosp-b", "encounter_key": "E-1", "patient_id": "HA-1", "readmitted_30d": True,
         "encounter": {"admit_datetime": dt.datetime(2026, 4, 1, 9), "discharge_datetime": left, "age_years": 30}}])
    db["risk_registry"].insert_one({"hospital_id": "hosp-a", "encounter_key": "E-1", "patient_id": "HA-1",
                                    "risk_score": 55.0, "risk_band": "High", "n_prior_adm": 2,
                                    "scored_at": "2026-04-10"})
    db["ingest_weeks"].insert_many([
        {"hospital_id": "hosp-a", "encounter_key": "E-1", "week_number": 1,
         "week_date": dt.datetime(2026, 4, 17), "readings": {"weight_kg": 83.0, "spo2": 95}},
        {"hospital_id": "hosp-a", "encounter_key": "E-1", "week_number": 3,
         "week_date": dt.datetime(2026, 4, 27), "readings": {"weight_kg": 90.0}}])
    # The readmission's own series: scoring it removed the first stay's from here.
    db["weekly_monitoring"].insert_one({"patient_id": "HA-1", "week_number": 1, "week_date": "2026-04-27",
                                        "encounter_key": "E-2", "observed": True,
                                        "monitoring": {"weight_change_kg": 0}})
    user = {"_id": "x", "role": "hospital_admin", "hospital_id": "hosp-a"}
    view = access.ScopedDB(db, user, ["HA-1"])
    rows, _ = outcomes._known_outcomes(db, view, None, dt.date(2026, 6, 1))
    assert [(r[0], r[1], r[4], r[5]) for r in rows] == [("HA-1", "2026-04-10", True, "records")]
    fact = outcomes._facts(db, view, rows, None)[("HA-1", "2026-04-10")]
    assert fact["until"] == dt.date(2026, 4, 25)
    assert fact["last"]["week_number"] == 1 and fact["signs"]["weight_gain"] is True
    assert (fact["age"], fact["sex"], fact["insurance"], fact["prior"], fact["destination"]) == (
        "75_84", "male", "medicaid", "2", "facility")
    assert "heart_failure" in fact["groups"]


def test_insurance_and_destination_buckets():
    assert outcomes._insurance("No charge") == "other" and outcomes._insurance("UNKNOWN") == "unknown"
    assert outcomes._insurance("Self pay") == "other" and outcomes._insurance(None) == "unknown"
    flags = lambda ama, snf, home: {"disch_ama": ama, "disch_snf": snf, "disch_home": home}  # noqa: E731
    assert outcomes._destination(None, flags(1, 1, 1)) == "ama"
    assert outcomes._destination(None, flags(0, 1, 1)) == "facility"
    assert outcomes._destination(None, flags(0, 0, 1)) == "home"
    assert outcomes._destination(None, flags(0, 0, 0)) == "other"
    assert outcomes._destination(None, flags(None, None, None)) == "unknown"
    assert outcomes._destination("HOME_WITH_SERVICES", None) == "home"
    assert outcomes._destination("REHAB", flags(1, 0, 0)) == "facility"        # the hospital's word wins


def ingest_stay(db, n, readmitted, later=None):
    """A discharged stay (10 April) of patient HA-n, scored, and optionally a later admission."""
    db["ingest_stays"].insert_one({
        "hospital_id": "hosp-a", "encounter_key": f"E-{n}", "patient_id": f"HA-{n}", "readmitted_30d": readmitted,
        "encounter": {"admit_datetime": dt.datetime(2026, 4, 1, 9), "discharge_datetime": dt.datetime(2026, 4, 10, 12)},
        "diagnoses": [{"icd_code": "I50.9", "description": "Heart failure", "sequence": 1}]})
    db["risk_registry"].insert_one({"hospital_id": "hosp-a", "encounter_key": f"E-{n}", "patient_id": f"HA-{n}",
                                    "risk_score": 40.0, "risk_band": "Low", "n_prior_adm": 1, "scored_at": "2026-04-10"})
    if later:
        db["ingest_stays"].insert_one({
            "hospital_id": "hosp-a", "encounter_key": f"E-{n}b", "patient_id": f"HA-{n}", "readmitted_30d": None,
            "encounter": {"admit_datetime": later, "discharge_datetime": later + dt.timedelta(days=3)}})


def test_a_readmission_date_found_more_than_30_days_on_is_dropped():
    db = mongomock.MongoClient()["neuroshield"]
    ingest_stay(db, 1, True, later=dt.datetime(2026, 6, 1, 9))           # 52 days on: not this readmission
    ingest_stay(db, 2, True, later=dt.datetime(2026, 4, 25, 9))
    view = access.ScopedDB(db, {"_id": "x", "role": "hospital_admin", "hospital_id": "hosp-a"}, ["HA-1", "HA-2"])
    rows, _ = outcomes._known_outcomes(db, view, None, dt.date(2026, 7, 1))
    facts = outcomes._facts(db, view, rows, None)
    assert facts[("HA-1", "2026-04-10")]["until"] is None
    assert facts[("HA-2", "2026-04-10")]["until"] == dt.date(2026, 4, 25)


def test_a_care_team_outcome_keeps_the_records_earlier_admissions():
    db = mongomock.MongoClient()["neuroshield"]
    ingest_stay(db, 1, False)
    db["readmission_outcomes"].insert_one({"patient_id": "HA-1", "discharge_date": "2026-04-10", "readmitted": True,
                                           "readmit_date": "2026-04-20", "discharge_score": 40.0,
                                           "discharge_band": "Low", "hospital_id": "hosp-a"})
    view = access.ScopedDB(db, {"_id": "x", "role": "hospital_admin", "hospital_id": "hosp-a"}, ["HA-1"])
    (row,), _ = outcomes._known_outcomes(db, view, None, dt.date(2026, 7, 1))
    assert row[5] == "care_team" and row[4] is True and row[6]["n_prior_adm"] == 1
    assert row[6]["readmit_date"] == "2026-04-20"


# ------------------------------------------------------------ whose patients
def test_another_hospitals_outcomes_never_count(main, world, clock):
    db = world["db"]
    other = ids(12, 4000)
    for pid in other:
        add(db, pid, hospital="hosp-b", anchor_age=90, clinical_group="oncology", clinical_groups=["oncology"])
    for pid in ids(12, 3000):
        add(db, pid, anchor_age=50)
    response = as_user(main, world, "admin@a.test").get("/api/outcomes")
    d = response.json()["readmission_detail"]
    assert group(d, "age", "85_plus")["known"] == 0 and group(d, "age", "45_64")["known"] == 12
    assert "oncology" not in {c["key"] for c in d["conditions"]["conditions"]}
    assert not any(pid in response.text for pid in other)
    assert group(detail(main, world, "ops@team.test"), "age", "85_plus")["known"] == 12


# ------------------------------------------------------------ the Check-ins tile
def test_simulated_demo_checkins_are_not_counted_as_sent(main, world, clock):
    db = world["db"]
    now = dt.datetime.now(dt.timezone.utc)
    db["checkins"].insert_one({"patient_id": A1, "discharge_date": "2026-04-10", "week_number": 1,
                               "at": now, "by_role": "patient", "red_flags": []})
    positive = mood_screen.assess({"phq_interest": 2, "phq_down": 2}, [], 1, simulated=True)
    db["checkins"].insert_one({"patient_id": A2, "discharge_date": "2026-04-10", "week_number": 1,
                               "at": now, "by_role": "patient", "mood": positive, "red_flags": [],
                               "simulated": True})
    db["checkins"].insert_one({"patient_id": A2, "discharge_date": "2026-04-10", "week_number": 2,
                               "at": now, "by_role": "patient", "red_flags": ["spo2"], "simulated": True})
    page = as_user(main, world, "cm@a.test").get("/api/outcomes").json()
    contact, mental = page["contact"], page["mental_health"]
    assert (contact["checkins"], contact["checkin_patients"], contact["with_red_flags"],
            contact["checkins_simulated"]) == (1, 1, 0, 2)
    # The demo's mood screens still show, said to be simulated.
    assert (mental["screened"], mental["positive"], mental["simulated"]) == (1, 1, 1)


# ------------------------------------------------------------ who may see it
def test_the_mood_panel_is_counts_for_every_outcome_role_not_individual_screens():
    # The aggregate panel (counts, small cells hidden) stays on Outcomes for all its roles; a
    # patient's own screen is the care team's only (access.ACTIONS view_mood).
    assert {"superadmin", "hospital_admin"} <= set(access.ACTIONS["view_outcomes"])
    assert not {"superadmin", "hospital_admin"} & set(access.ACTIONS["view_mood"])


def test_reads_do_not_grow_with_patients(monkeypatch):
    calls = []
    real = mongomock.collection.Collection.find

    def counted(self, *args, **kwargs):
        calls.append(self.name)
        return real(self, *args, **kwargs)
    monkeypatch.setattr(mongomock.collection.Collection, "find", counted)

    def reads(n):
        db = mongomock.MongoClient()["neuroshield"]
        patients = ids(n, 5000)
        for i, pid in enumerate(patients):
            db["care_actions"].insert_one({"patient_id": pid, "hospital_id": "hosp-a"})
            db["patient_worklist"].insert_one({**patient_row(pid, LATEST, 30), "anchor_age": 60 + i})
            db["readmission_outcomes"].insert_one({
                "patient_id": pid, "discharge_date": "2026-04-10", "readmitted": i % 2 == 0,
                "readmit_date": "2026-04-20" if i % 2 == 0 else None, "discharge_score": 30.0,
                "discharge_band": "Low", "hospital_id": "hosp-a"})
            db["weekly_monitoring"].insert_one({"patient_id": pid, "week_number": 1, "week_date": "2026-04-17",
                                                "observed": True, "monitoring": {"weight_change_kg": 1.5}})
        view = access.ScopedDB(db, {"_id": "x", "role": "hospital_admin", "hospital_id": "hosp-a"}, patients)
        calls.clear()
        rows, _ = outcomes._known_outcomes(db, view, LATEST, dt.date(2026, 6, 1))
        facts = outcomes._facts(db, view, rows, LATEST)
        assert len(rows) == n and all(f["last"] for f in facts.values())
        return len(calls)
    assert reads(12) == reads(24)


def test_reads_do_not_grow_with_ingested_stays(monkeypatch):
    calls = []
    real = mongomock.collection.Collection.find

    def counted(self, *args, **kwargs):
        calls.append(self.name)
        return real(self, *args, **kwargs)
    monkeypatch.setattr(mongomock.collection.Collection, "find", counted)

    def reads(n):
        db = mongomock.MongoClient()["neuroshield"]
        for i in range(n):
            ingest_stay(db, i, i % 3 == 0, later=dt.datetime(2026, 4, 25, 9) if i % 3 == 0 else None)
            db["ingest_weeks"].insert_one({"hospital_id": "hosp-a", "encounter_key": f"E-{i}", "week_number": 1,
                                           "week_date": dt.datetime(2026, 4, 17), "readings": {"spo2": 95}})
        view = access.ScopedDB(db, {"_id": "x", "role": "hospital_admin", "hospital_id": "hosp-a"},
                               [f"HA-{i}" for i in range(n)])
        calls.clear()
        rows, _ = outcomes._known_outcomes(db, view, None, dt.date(2026, 7, 1))
        facts = outcomes._facts(db, view, rows, None)
        assert len(rows) == n and all(f["last"] for f in facts.values())
        return list(calls)
    small_run, big_run = reads(12), reads(36)
    assert "ingest_weeks" in small_run and small_run == big_run

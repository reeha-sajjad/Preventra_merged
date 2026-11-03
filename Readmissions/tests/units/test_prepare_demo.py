"""scripts/prepare_demo.py: a handful of old demo patients moved to around
today for a live demo, the same ones every run, and put back with --undo."""
import datetime as dt
import sys
from pathlib import Path

import mongomock
import pytest

from api import auth, checkins, outcomes
from models.monitoring_rules import contact_gap
from tests.units.test_isolation import main  # noqa: F401  - imports the service with a dead MONGO_URI

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import prepare_demo  # noqa: E402

TODAY = dt.date(2026, 10, 27)
HID = "demo-h"
# pid -> (group, band, linked to a patient login)
PEOPLE = {
    "MIMIC-1": ("heart_failure", "High", True),
    "MIMIC-2": ("respiratory", "Medium", False),
    "MIMIC-3": ("heart_failure", "High", False),
    "MIMIC-4": ("general", "Low", False),
    "MIMIC-5": ("renal", "High", False),
    "MIMIC-6": ("surgical_injury", "Low", False),
    "MIMIC-7": ("oncology", "High", False),
}


@pytest.fixture
def db(main, monkeypatch):  # noqa: F811
    db = mongomock.MongoClient()["neuroshield"]
    monkeypatch.setattr("api.main._worklist_driver", lambda pid, row: "")
    uid = str(auth.users(db).insert_one({"email": "patient1@demo-h.test", "role": "patient", "status": "active",
                                         "hospital_id": HID}).inserted_id)
    for i, (pid, (group, band, linked)) in enumerate(PEOPLE.items()):
        disch = dt.date(2014, 3, 1) + dt.timedelta(days=11 * i)
        db["patient_worklist"].insert_one({"patient_id": pid, "batch_date": "2026-04-29", "risk_score": 45.0,
                                           "risk_band": band, "clinical_group": group, "clinical_groups": [group],
                                           "discharge_date": disch.isoformat(),
                                           "admit_date": (disch - dt.timedelta(days=5)).isoformat()})
        db["care_actions"].insert_one({"patient_id": pid, "hospital_id": HID, "assigned_doctor_id": "DR-1",
                                       **({"patient_account_id": uid} if linked else {})})
        for wk in range(5):
            db["weekly_monitoring"].insert_one({
                "patient_id": pid, "week_number": wk, "days_after_discharge": 7 * wk,
                "week_date": (disch + dt.timedelta(days=7 * wk)).isoformat(), "risk_score": 45.0,
                "risk_band": band, "observed": True, "red_flags": [], "source": "simulated",
                "clinical_group": group, **({"discharge_baseline": {"dry_weight_kg": 80.0}} if wk == 0 else {})})
    db["doctors"].insert_one({"doctor_id": "DR-1", "name": "Dr Amina Rahman", "email": "cardiology@demo-h.test",
                              "specialty": "Cardiology", "clinical_groups": ["heart_failure"], "active": True,
                              "hospital_id": HID})
    # Another hospital's patient is never touched.
    db["patient_worklist"].insert_one({"patient_id": "MIMIC-9", "batch_date": "2026-04-29", "risk_band": "High",
                                       "discharge_date": "2014-01-01", "clinical_group": "heart_failure"})
    db["care_actions"].insert_one({"patient_id": "MIMIC-9", "hospital_id": "other"})
    db["weekly_monitoring"].insert_one({"patient_id": "MIMIC-9", "week_number": 0, "risk_band": "High",
                                        "week_date": "2014-01-01"})
    db["clinical_alerts"].insert_one({"alert_id": "OLD", "patient_id": "MIMIC-1", "created_at": "2014-03-08 10:00:00"})
    return db


def series(db, pid):
    return list(db["weekly_monitoring"].find({"patient_id": pid}, {"_id": 0}).sort("week_number", 1))


def test_the_same_patients_are_chosen_for_each_scene(db):
    assert prepare_demo.choose(db, HID) == [("checkin", "MIMIC-1"), ("quiet", "MIMIC-2"), ("never", "MIMIC-3"),
                                            ("active", "MIMIC-4"), ("active", "MIMIC-5"), ("outcome", "MIMIC-7")]


def test_a_preview_writes_nothing(db):
    before = series(db, "MIMIC-1")
    plan = prepare_demo.prepare(db, HID, TODAY)
    assert plan[0][:3] == ("checkin", "MIMIC-1", "2026-10-24")
    assert series(db, "MIMIC-1") == before and db[prepare_demo.STATE].count_documents({}) == 0


def test_prepared_patients_show_every_feature(db):
    prepare_demo.prepare(db, HID, TODAY, write=True)
    # Check-in: home 3 days, nothing after discharge, the form is open.
    assert [w["week_number"] for w in series(db, "MIMIC-1")] == [0]
    assert series(db, "MIMIC-1")[0]["week_date"] == "2026-10-24"
    user = auth.users(db).find_one({"email": "patient1@demo-h.test"})
    form = checkins._state(db, "MIMIC-1", TODAY)
    assert form["open"] and form["week_number"] == 1
    assert checkins.form(db, user, ["MIMIC-1"], today=TODAY)["open"]
    # Gone quiet after week 1; never heard from since a High discharge.
    quiet = contact_gap(series(db, "MIMIC-2"), TODAY)
    assert quiet["overdue"] and quiet["days_without_contact"] == 11
    never = contact_gap(series(db, "MIMIC-3"), TODAY)
    assert never["overdue"] and never["days_without_contact"] == 12
    # On track: readings every week to yesterday.
    active = series(db, "MIMIC-4")
    assert active[-1]["week_date"] == "2026-10-26" and not contact_gap(active, TODAY)["overdue"]
    # Outcome: the 30 days are over.
    assert outcomes.patient_outcome(db, {"role": "case_manager"}, "MIMIC-7", TODAY)["window_closed"]
    # The worklist agrees, and nobody else moved.
    statuses = {r["patient_id"]: r.get("monitoring_status") for r in db["patient_worklist"].find()}
    assert statuses["MIMIC-2"] == statuses["MIMIC-3"] == "action_required"
    assert db["patient_worklist"].find_one({"patient_id": "MIMIC-6"})["discharge_date"] == "2014-04-25"
    assert db["patient_worklist"].find_one({"patient_id": "MIMIC-9"})["discharge_date"] == "2014-01-01"


def test_prepared_patients_carry_simulated_mood_answers_that_are_never_scored(db):
    plan = prepare_demo.prepare(db, HID, TODAY, write=True)
    week4 = next(w for w in series(db, "MIMIC-4") if w["week_number"] == 4)
    assert week4["mood"]["phq8"] == 13 and week4["mood"]["simulated"]
    assert {"phq8_moderate", "phq8_rise"} <= set(week4["mood"]["flags"])
    assert week4["risk_score"] == 45.0 and "mood" not in week4.get("monitoring", {})
    assert [w["mood"]["phq8"] for w in series(db, "MIMIC-4") if w.get("mood") and w["mood"]["phq8"]] == [7, 13]
    assert any(w.get("mood") for w in series(db, "MIMIC-7"))
    assert not any(w.get("mood") for pid in ("MIMIC-5", "MIMIC-1", "MIMIC-2", "MIMIC-3") for w in series(db, pid))
    assert db["checkins"].count_documents({"simulated": True}) == 8
    assert db["clinical_alerts"].count_documents({}) == 0                  # the demo raises these itself
    sheet = prepare_demo.cheat_sheet(db, plan)
    assert "Mood answers (simulated) in weeks 1-4: week 4 PHQ-8 13, moderate, up 6" in sheet
    prepare_demo.undo(db, HID)
    assert db["weekly_monitoring"].count_documents({"mood": {"$exists": True}}) == 0
    assert db["checkins"].count_documents({}) == 0


def test_running_again_resets_the_demo_rather_than_moving_twice(db):
    prepare_demo.prepare(db, HID, TODAY, write=True)
    # The demo happens: a check-in, an alert, a checklist tick.
    db["weekly_monitoring"].insert_one({"patient_id": "MIMIC-1", "week_number": 1, "reported_by": "patient",
                                        "week_date": "2026-10-27"})
    db["checkins"].insert_one({"patient_id": "MIMIC-1", "discharge_date": "2026-10-24", "week_number": 1})
    db["discharge_plans"].insert_one({"patient_id": "MIMIC-3", "discharge_date": "2026-10-15",
                                      "custom_items": [{"id": "ci_00000001", "label": "Oxygen delivered"}]})
    db["clinical_alerts"].insert_one({"alert_id": "NEW", "patient_id": "MIMIC-1",
                                      "created_at": dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")})
    prepare_demo.prepare(db, HID, TODAY, write=True)
    assert [w["week_number"] for w in series(db, "MIMIC-1")] == [0]
    assert series(db, "MIMIC-1")[0]["week_date"] == "2026-10-24"
    assert db["checkins"].count_documents({"simulated": {"$ne": True}}) == 0
    assert db["discharge_plans"].count_documents({}) == 0
    assert db["clinical_alerts"].count_documents({}) == 0       # OLD is set aside while the demo runs
    prepare_demo.undo(db, HID)
    assert {a["alert_id"] for a in db["clinical_alerts"].find()} == {"OLD"}


def test_undo_puts_everything_back(db):
    before = {pid: series(db, pid) for pid in PEOPLE}
    rows = {r["patient_id"]: (r["admit_date"], r["discharge_date"]) for r in db["patient_worklist"].find(
        {"patient_id": {"$in": list(PEOPLE)}})}
    prepare_demo.prepare(db, HID, TODAY, write=True)
    assert sorted(prepare_demo.undo(db, HID)) == ["MIMIC-1", "MIMIC-2", "MIMIC-3", "MIMIC-4", "MIMIC-5", "MIMIC-7"]
    after = {pid: series(db, pid) for pid in PEOPLE}
    strip = lambda s: [{k: v for k, v in w.items() if k != "_id"} for w in s]  # noqa: E731
    assert {p: strip(s) for p, s in after.items()} == {p: strip(s) for p, s in before.items()}
    assert {r["patient_id"]: (r["admit_date"], r["discharge_date"]) for r in db["patient_worklist"].find(
        {"patient_id": {"$in": list(PEOPLE)}})} == rows
    assert db[prepare_demo.STATE].count_documents({}) == 0


def test_the_cheat_sheet_names_the_logins_to_use(db):
    sheet = prepare_demo.cheat_sheet(db, prepare_demo.prepare(db, HID, TODAY, write=True))
    assert "Sign in as patient1@demo-h.test" in sheet
    assert "Dr Amina Rahman - cardiology@demo-h.test" in sheet
    assert "MIMIC-2" in sheet and "Needs Attention" in sheet


# ------------------------------------------------------------ presenting logins
def add_login(db, email, role, hospital=HID, status="active"):
    return str(auth.users(db).insert_one({"email": email, "role": role, "status": status, "name": email.split("@")[0],
                                          "hospital_id": hospital}).inserted_id)


@pytest.mark.parametrize("setup, problem", [
    (lambda db: None, "casemanager@test.com: there is no account with this email."),
    (lambda db: add_login(db, "casemanager@test.com", "nurse"), "is a nurse account, not a case manager"),
    (lambda db: add_login(db, "casemanager@test.com", "case_manager", status="pending"), "approve it"),
    (lambda db: add_login(db, "casemanager@test.com", "case_manager", hospital="elsewhere"),
     "is in hospital 'elsewhere', not 'demo-h'"),
])
def test_the_presenting_logins_are_checked_first(db, setup, problem):
    setup(db)
    add_login(db, "doctor@test.com", "doctor")
    problems = prepare_demo.check_staff(db, HID, "casemanager@test.com", "doctor@test.com")
    assert len(problems) == 1 and problem in problems[0]


def test_a_doctor_registered_elsewhere_is_reported(db):
    add_login(db, "doctor@test.com", "doctor")
    db["doctors"].insert_one({"doctor_id": "DR-X", "email": "doctor@test.com", "hospital_id": "elsewhere",
                              "active": True})
    assert "registered for alerts in 'elsewhere'" in prepare_demo.check_staff(db, HID, None, "doctor@test.com")[0]


def test_the_presenting_doctor_gets_all_six_and_their_alerts_and_undo_gives_them_back(db):
    from api import access
    add_login(db, "casemanager@test.com", "case_manager")
    uid = add_login(db, "doctor@test.com", "doctor")
    assert prepare_demo.check_staff(db, HID, "casemanager@test.com", "doctor@test.com") == []
    db["clinical_alerts"].insert_one({"alert_id": "OPEN", "patient_id": "MIMIC-2", "status": "pending",
                                      "doctor_id": "DR-1", "created_at": "2014-03-20 10:00:00",
                                      "forecast": {"clinical_group": "respiratory"}})
    plan = prepare_demo.prepare(db, HID, TODAY, write=True, doctor="doctor@test.com")
    chosen = {p for _, p, _, _ in plan}
    registered = db["doctors"].find_one({"email": "doctor@test.com"})
    assert registered and registered["hospital_id"] == HID and registered["active"]
    doctor = {"_id": uid, "role": "doctor", "hospital_id": HID, "email": "doctor@test.com"}
    assert set(access.patient_scope(db, doctor)) == chosen                 # exactly the six demo patients
    assert db["clinical_alerts"].find_one({"alert_id": "OPEN"}) is None     # old alerts set aside for the demo
    sheet = prepare_demo.cheat_sheet(db, plan, "casemanager@test.com", "doctor@test.com")
    assert "Tab A  casemanager@test.com" in sheet and "Tab C  doctor@test.com" in sheet
    # Running again keeps the same doctor; undo gives the patients and alert back.
    prepare_demo.prepare(db, HID, TODAY, write=True, doctor="doctor@test.com")
    assert set(access.patient_scope(db, doctor)) == chosen
    prepare_demo.undo(db, HID)
    assert {c["assigned_doctor_id"] for c in db["care_actions"].find({"patient_id": {"$in": list(chosen)}})} == {"DR-1"}
    assert db["clinical_alerts"].find_one({"alert_id": "OPEN"})["doctor_id"] == "DR-1"     # back, untouched
    assert db["doctors"].find_one({"email": "doctor@test.com"}) is None    # the script registered them
    assert db[prepare_demo.STATE].count_documents({}) == 0


# ------------------------------------------------------------ --count: the more patients
BANDS = ("High", "Medium", "Low")
SCORES = {"High": 55.0, "Medium": 30.0, "Low": 10.0}
PLAN_GROUPS = ("heart_failure", "respiratory", "renal", "general")


def crowd_person(db, pid, band, group, groups, disch, linked=None):
    db["patient_worklist"].insert_one({"patient_id": pid, "batch_date": "2026-04-29", "risk_score": SCORES[band],
                                       "risk_band": band, "clinical_group": group, "clinical_groups": groups,
                                       "discharge_date": disch.isoformat(),
                                       "admit_date": (disch - dt.timedelta(days=5)).isoformat()})
    db["care_actions"].insert_one({"patient_id": pid, "hospital_id": HID, "assigned_doctor_id": "DR-1",
                                   **({"patient_account_id": linked} if linked else {})})


def crowd_weeks(db, pid, band, group, disch, observed=lambda wk: True):
    for wk in range(5):
        db["weekly_monitoring"].insert_one({
            "patient_id": pid, "week_number": wk, "days_after_discharge": 7 * wk,
            "week_date": (disch + dt.timedelta(days=7 * wk)).isoformat(), "risk_score": SCORES[band],
            "risk_band": band, "observed": observed(wk), "red_flags": [], "source": "simulated",
            "clinical_group": group})


@pytest.fixture
def crowd(db):
    """90 more MIMIC patients who can be brought to life, and the ones who cannot."""
    for i in range(90):
        pid, band, group = f"MIMIC-{9000 + i}", BANDS[i % 3], PLAN_GROUPS[i % 4]
        disch = dt.date(2014, 6, 1) + dt.timedelta(days=i)
        crowd_person(db, pid, band, group, [group] + (["diabetes"] if i % 2 else []), disch)
        crowd_weeks(db, pid, band, group, disch, lambda wk, i=i: not (wk == 3 and i % 5 == 0))
        for wk in (0, 4):                                    # an old alert on the series as it was
            alert = f"AL-{i}-{wk}"
            db["clinical_alerts"].insert_one({"alert_id": alert, "patient_id": pid, "week_number": wk,
                                              "created_at": "2014-06-08 10:00:00"})
            db["notifications"].insert_one({"alert_id": alert, "patient_id": pid, "created_at": "2014-06-08 10:00:00"})
    when = dt.date(2014, 9, 1)
    crowd_person(db, "MIMIC-9090", "Low", "general", ["general"], when, linked="a-login")        # has a login
    crowd_weeks(db, "MIMIC-9090", "Low", "general", when)
    crowd_person(db, "MIMIC-9091", "Low", "general", ["general"], when)                          # has a care-team outcome
    crowd_weeks(db, "MIMIC-9091", "Low", "general", when)
    db["readmission_outcomes"].insert_one({
        "patient_id": "MIMIC-9091", "discharge_date": when.isoformat(), "readmitted": False, "readmit_date": None,
        "discharge_score": 10.0, "discharge_band": "Low", "hospital_id": HID, "note": "",
        "recorded_by": {"id": "u", "name": "CM", "role": "case_manager"}, "recorded_at": dt.datetime(2014, 11, 1)})
    crowd_person(db, "MIMIC-9092", "Low", "general", ["general"], when)                          # a patient's own reading
    crowd_weeks(db, "MIMIC-9092", "Low", "general", when)
    db["weekly_monitoring"].insert_one({"patient_id": "MIMIC-9092", "week_number": 5, "reported_by": "patient",
                                        "week_date": "2014-10-06"})
    crowd_person(db, "MIMIC-9093", "Low", "general", ["general"], when)                          # no week-1 reading
    crowd_weeks(db, "MIMIC-9093", "Low", "general", when, lambda wk: wk != 1)
    crowd_person(db, "TYPED-1", "Low", "general", ["general"], when)                               # typed in, not MIMIC
    crowd_weeks(db, "TYPED-1", "Low", "general", when)
    return db


SCENE_IDS = {"MIMIC-1", "MIMIC-2", "MIMIC-3", "MIMIC-4", "MIMIC-5", "MIMIC-7"}
NOT_CHOSEN = {"MIMIC-9090", "MIMIC-9091", "MIMIC-9092", "MIMIC-9093", "TYPED-1"}
CM = {"_id": "cm", "role": "case_manager", "hospital_id": HID}


def set_up(db, count=60, doctor=None):
    scenes = prepare_demo.prepare(db, HID, TODAY, write=True, doctor=doctor)
    bulk, report = prepare_demo.prepare_bulk(db, HID, count, TODAY, True, {p for _, p, _, _ in scenes})
    return scenes, bulk, report


def page_of(db):
    from api import access
    access.forget_hospital_lists()
    return outcomes.page(db, access.ScopedDB(db, CM, access.patient_scope(db, CM)), CM, 30, TODAY)


def snapshot(db, drop=()):
    names = ("weekly_monitoring", "patient_worklist", "clinical_alerts", "notifications", "readmission_outcomes",
             "checkins", "demo_prep")
    order = lambda d: tuple(str(d.get(k)) for k in ("patient_id", "week_number", "alert_id", "discharge_date"))  # noqa: E731
    return {n: sorted(({k: v for k, v in d.items() if k not in drop} for d in db[n].find()), key=order)
            for n in names}


def dates(db):
    return ({r["patient_id"]: (r.get("admit_date"), r["discharge_date"]) for r in db["patient_worklist"].find()},
            sorted((w["patient_id"], w["week_number"], w["week_date"]) for w in db["weekly_monitoring"].find()))


def test_the_six_scenes_are_unchanged_with_more_patients(crowd):
    db = crowd
    assert prepare_demo.choose(db, HID) == [("checkin", "MIMIC-1"), ("quiet", "MIMIC-2"), ("never", "MIMIC-3"),
                                            ("active", "MIMIC-4"), ("active", "MIMIC-5"), ("outcome", "MIMIC-7")]
    set_up(db)
    assert [w["week_number"] for w in series(db, "MIMIC-1")] == [0]
    assert contact_gap(series(db, "MIMIC-2"), TODAY)["days_without_contact"] == 11
    assert outcomes.patient_outcome(db, {"role": "case_manager"}, "MIMIC-7", TODAY)["window_closed"]
    assert db["readmission_outcomes"].find_one({"patient_id": "MIMIC-7"}) is None


def test_more_patients_are_spread_over_bands_and_conditions_and_never_the_same_twice_differently(crowd):
    db = crowd
    taken = {p for _, p in prepare_demo.choose(db, HID)}
    plan, report = prepare_demo.choose_bulk(db, HID, 60, taken, TODAY)
    assert (plan, report) == prepare_demo.choose_bulk(db, HID, 60, taken, TODAY)
    ids = {e["patient_id"] for e in plan}
    assert len(plan) == 60 and not ids & taken and not ids & NOT_CHOSEN
    live, past = [e for e in plan if e["live"]], [e for e in plan if not e["live"]]
    assert len(live) == len(past) == 30
    assert {e["band"] for e in live} == {e["band"] for e in past} == set(BANDS)
    assert len({e["group"] for e in past}) >= 3
    assert min(e["days"] for e in live) >= 1 and max(e["days"] for e in live) <= 30
    assert min(e["days"] for e in past) == 31 and max(e["days"] for e in past) == 60
    assert report["by_band"] == {"High": 18, "Medium": 21, "Low": 21} and report["linked"] == 1
    assert report["kept_out"] == 2 and report["no_week1"] == 1 and report["found"] == 91


def test_outcomes_follow_the_score_with_a_top_up(monkeypatch):
    entry = lambda i, band, score: {"patient_id": f"P{i}", "band": band, "score": score}   # noqa: E731
    monkeypatch.setattr(prepare_demo, "_uniform", lambda pid, salt: 0.999)          # nobody drawn
    past = [entry(i, "Low", float(i % 5)) for i in range(30)]
    assert prepare_demo._draw_outcomes(past) == 11
    got = sorted((e["score"] for e in past if e["readmitted"]), reverse=True)
    assert len(got) == 11 and got == sorted((e["score"] for e in past), reverse=True)[:11]
    past = [entry(i, "High", 0.0) for i in range(5)] + [entry(10 + i, "Medium", float(i)) for i in range(20)]
    prepare_demo._draw_outcomes(past)
    assert {e["patient_id"] for e in past if e["readmitted"]} == (
        {f"P{i}" for i in range(5)} | {f"P{10 + i}" for i in range(14, 20)})
    monkeypatch.setattr(prepare_demo, "_uniform", lambda pid, salt: 0.0)            # everybody drawn
    past = [entry(i, "High", 100.0) for i in range(22)]
    assert prepare_demo._draw_outcomes(past) == 11 and sum(e["readmitted"] for e in past) == 11
    past = [entry(i, "Low", 0.0) for i in range(21)]
    monkeypatch.setattr(prepare_demo, "_uniform", lambda pid, salt: 0.999)
    assert prepare_demo._draw_outcomes(past) == 0 and not any(e["readmitted"] for e in past)


def test_a_readmission_comes_in_week_one_to_three_after_that_weeks_reading():
    for i in range(500):
        week, day = prepare_demo._readmit_day(f"MIMIC-{i}")
        assert week in (1, 2, 3) and 8 <= day <= 27 and day > 7 * week


def test_a_preview_of_more_patients_writes_nothing(crowd):
    db = crowd
    before = snapshot(db)
    scenes = prepare_demo.prepare(db, HID, TODAY)
    plan, report = prepare_demo.prepare_bulk(db, HID, 60, TODAY, False, {p for _, p, _, _ in scenes})
    assert len(plan) == 60 and snapshot(db) == before


def test_patients_still_in_their_30_days_have_readings_to_yesterday(crowd):
    db = crowd
    _, bulk, _ = set_up(db)
    live = [e for e in bulk if e["live"]]
    assert len(live) == 30
    for e in live:
        pid, weeks = e["patient_id"], series(db, e["patient_id"])
        assert db["patient_worklist"].find_one({"patient_id": pid})["discharge_date"] == e["target"].isoformat()
        assert [w["week_number"] for w in weeks] == list(range(e["keep"] + 1))
        assert dt.date.fromisoformat(weeks[-1]["week_date"]) <= TODAY - dt.timedelta(days=1)
        assert not contact_gap(weeks, TODAY)["overdue"]
        assert weeks[-1]["observed"] and not e["quiet"]               # the last reading is a reading
        assert db["readmission_outcomes"].find_one({"patient_id": pid}) is None


def test_patients_past_day_30_have_a_labelled_drawn_outcome_and_readings_before_it(crowd):
    db = crowd
    _, bulk, report = set_up(db)
    past = [e for e in bulk if not e["live"]]
    docs = {d["patient_id"]: d for d in db["readmission_outcomes"].find({"demo": True})}
    assert set(docs) == {e["patient_id"] for e in past} and len(docs) == 30
    back = [e for e in past if e["readmitted"]]
    assert len(back) >= 11 and len(past) - len(back) >= 11
    for e in past:
        pid, doc = e["patient_id"], docs[e["patient_id"]]
        assert doc["demo"] and doc["outcome_simulated"] and doc["note"] == prepare_demo.NOTES[doc["readmitted"]]
        assert doc["recorded_by"]["id"] == "scripts/prepare_demo.py" and doc["readmitted"] == e["readmitted"]
        assert doc["discharge_date"] == e["target"].isoformat() and doc["discharge_score"] == e["score"]
        weeks = series(db, pid)
        if e["readmitted"]:
            came = dt.date.fromisoformat(doc["readmit_date"])
            assert e["target"] + dt.timedelta(days=8) <= came <= e["target"] + dt.timedelta(days=27)
            assert all(dt.date.fromisoformat(w["week_date"]) < came for w in weeks)
            assert any(w["week_number"] >= 1 and w["observed"] for w in weeks)
        else:
            assert doc["readmit_date"] is None and len(weeks) == 5
        assert outcomes.patient_outcome(db, CM, pid, TODAY)["recorded"]["by"] == "Demo data (simulated)"
    assert db["checkins"].count_documents({}) == 8                       # the scenes' own mood check-ins only
    assert not any(w.get("mood") for e in bulk for w in series(db, e["patient_id"]))
    assert {c["assigned_doctor_id"] for c in db["care_actions"].find({"patient_id": {"$in": [e["patient_id"] for e in bulk]}})} == {"DR-1"}
    assert report["asked"] == 60


def test_the_outcomes_page_shows_all_three_panels_and_says_what_is_simulated(crowd):
    db = crowd
    _, bulk, report = set_up(db)
    page = page_of(db)
    assert page["readmissions"]["demo"] == 30 and page["readmissions"]["sources"]["care_team"] == 1
    detail = page["readmission_detail"]
    assert detail["enough"] and detail["demo"] == 30
    assert any(c["hidden"] is None and c["known"] > 0 for dim in detail["who"] for c in dim["groups"])
    assert [c["key"] for c in detail["conditions"]["conditions"] if c["hidden"] is None] == ["diabetes"]
    lr = detail["last_readings"]
    assert lr["hidden"] is None and lr["simulated"] == lr["readmitted"]["with_readings"] + lr["not_readmitted"]["with_readings"]
    for side in ("readmitted", "not_readmitted"):
        assert lr[side]["no_readings"] == 0 and lr[side]["with_readings"] >= 11
    assert lr["undated"] == 0
    assert page["contact"]["monitored_now"] == 5 + 30
    # The forecast made before writing agrees.
    forecast = prepare_demo.forecast_panels(bulk, 1)
    assert forecast["enough"] and forecast["readings"] and forecast["conditions"] == ["Diabetes"]
    assert (forecast["readmitted"], forecast["not_readmitted"]) == (lr["readmitted"]["known"], lr["not_readmitted"]["known"] - 1)
    checked = prepare_demo.panel_check(db, HID, TODAY)
    assert checked["enough"] and checked["readings"] and checked["conditions"] == forecast["conditions"]
    assert len(checked["who"]) == 5 and report["topped_up"] == 0


def test_a_draw_with_nobody_readmitted_is_topped_up_so_the_panels_still_show(crowd, monkeypatch):
    db = crowd
    monkeypatch.setattr(prepare_demo, "_uniform", lambda pid, salt: 0.999)
    _, bulk, report = set_up(db)
    past = [e for e in bulk if not e["live"]]
    assert report["topped_up"] == 11 and sum(e["readmitted"] for e in past) == 11
    assert {e["band"] for e in past if e["readmitted"]} == {"High", "Medium"}        # the likeliest came back
    assert page_of(db)["readmission_detail"]["last_readings"]["hidden"] is None


def test_alerts_about_weeks_set_aside_go_with_them_and_the_rest_stay(crowd):
    db = crowd
    _, bulk, _ = set_up(db)
    for e in (e for e in bulk if e["patient_id"].startswith("MIMIC-9")):
        i = int(e["patient_id"][6:]) - 9000
        gone4 = e["keep"] is not None and e["keep"] < 4
        for name in ("clinical_alerts", "notifications"):
            assert db[name].count_documents({"alert_id": f"AL-{i}-0"}) == 1
            assert db[name].count_documents({"alert_id": f"AL-{i}-4"}) == (0 if gone4 else 1)
    assert db["clinical_alerts"].count_documents({"created_at": {"$gte": "2026"}}) == 0


def test_running_again_gives_the_same_patients_whatever_the_last_demo_left(crowd):
    db = crowd
    _, bulk, _ = set_up(db)
    drop = ("_id", "recorded_at", "prepared_at", "prepared_local", "summary_refreshed")
    first = snapshot(db, drop)
    live = next(e["patient_id"] for e in bulk if e["live"])
    db["checkins"].insert_one({"patient_id": live, "discharge_date": "x", "week_number": 1})
    db["readmission_outcomes"].insert_one({"patient_id": live, "discharge_date": "x", "readmitted": False})
    set_up(db)
    assert snapshot(db, drop) == first
    assert db[prepare_demo.STATE].count_documents({}) == 66


def test_undo_puts_back_the_six_and_every_one_of_the_more(crowd):
    db = crowd
    before, rows_before = snapshot(db), dates(db)
    scenes, bulk, _ = set_up(db)
    with pytest.raises(RuntimeError, match="put the last run back first"):
        prepare_demo.prepare_bulk(db, HID, 60, TODAY, True, {p for _, p, _, _ in scenes})
    done = prepare_demo.undo(db, HID)
    assert sorted(done) == sorted(SCENE_IDS | {e["patient_id"] for e in bulk})
    after = snapshot(db)
    for name in ("weekly_monitoring", "clinical_alerts", "notifications"):
        assert after[name] == before[name]
    assert dates(db) == rows_before
    assert [d["patient_id"] for d in db["readmission_outcomes"].find()] == ["MIMIC-9091"]
    assert db[prepare_demo.STATE].count_documents({}) == 0
    assert prepare_demo.undo(db, HID) == []


def test_a_presenting_doctor_still_gets_exactly_the_six(crowd):
    from api import access
    db = crowd
    add_login(db, "casemanager@test.com", "case_manager")
    uid = add_login(db, "doctor@test.com", "doctor")
    scenes, bulk, _ = set_up(db, doctor="doctor@test.com")
    doctor = {"_id": uid, "role": "doctor", "hospital_id": HID, "email": "doctor@test.com"}
    assert set(access.patient_scope(db, doctor)) == {p for _, p, _, _ in scenes}


def test_the_sheet_says_what_was_done_and_what_to_watch(crowd):
    db = crowd
    scenes, bulk, report = set_up(db)
    sheet = prepare_demo.bulk_sheet(db, scenes, bulk, report, prepare_demo.panel_check(db, HID, TODAY), 1)
    for words in ("60 more:", "being monitored", "simulated 30-day outcome", "readmitted", "Demo data (simulated)",
                  "week-1 reading (2026-09-24)", "backfill_model_inputs"):
        assert words in sheet
    assert "last stay" not in sheet


def test_the_care_teams_word_replaces_a_simulated_one(crowd):
    db = crowd
    _, bulk, _ = set_up(db)
    pid = next(e["patient_id"] for e in bulk if not e["live"])
    outcomes.record(db, {"_id": "u", "role": "case_manager", "name": "CM"}, None, pid, False, today=TODAY)
    doc = db["readmission_outcomes"].find_one({"patient_id": pid})
    assert "demo" not in doc and "outcome_simulated" not in doc
    assert page_of(db)["readmissions"]["demo"] == 29


def run_cli(monkeypatch, capsys, db, *args):
    """prepare_demo.main() against the test database (never the .env one)."""
    import dotenv
    monkeypatch.setattr(dotenv, "load_dotenv", lambda *a, **k: None)
    monkeypatch.setattr("api.main.db", db)
    monkeypatch.setattr(sys, "argv", ["prepare_demo.py", "--hospital", HID, *args])
    capsys.readouterr()
    try:
        prepare_demo.main()
    except SystemExit as stop:
        return f"exit {stop.code}\n" + capsys.readouterr().out
    return capsys.readouterr().out


def test_the_command_line_previews_writes_warns_and_puts_back(crowd, monkeypatch, capsys):
    db = crowd
    assert run_cli(monkeypatch, capsys, db, "--count", "501").startswith("exit 1")
    assert db[prepare_demo.STATE].count_documents({}) == 0
    out = run_cli(monkeypatch, capsys, db, "--count", "20")
    assert "below 44" in out and "nothing written" in out and db[prepare_demo.STATE].count_documents({}) == 0
    out = run_cli(monkeypatch, capsys, db, "--count", "60")
    assert "60 more:" in out and "expected:" in out and "nothing written" in out
    assert db[prepare_demo.STATE].count_documents({}) == 0
    out = run_cli(monkeypatch, capsys, db, "--count", "60", "--yes")
    assert "ready for the demo." in out and "Who comes back now:" in out
    assert db[prepare_demo.STATE].count_documents({}) == 66
    out = run_cli(monkeypatch, capsys, db)
    assert "also has 60 more" in out and db[prepare_demo.STATE].count_documents({}) == 66
    out = run_cli(monkeypatch, capsys, db, "--undo")
    assert "would put back 66 patient(s) (60 of them from --count)" in out
    run_cli(monkeypatch, capsys, db, "--yes")                      # no --count: the six only, the rest put back
    assert db[prepare_demo.STATE].count_documents({}) == 6
    assert "put back 6 patient(s)" in run_cli(monkeypatch, capsys, db, "--undo", "--yes")


def test_a_live_patient_whose_last_whole_week_was_no_reading_is_given_another_day(crowd):
    db = crowd
    taken = {p for _, p in prepare_demo.choose(db, HID)}
    plan, _ = prepare_demo.choose_bulk(db, HID, 60, taken, TODAY)
    silent = [e for e in plan if e["live"] and e["patient_id"].startswith("MIMIC-9")
              and (int(e["patient_id"][6:]) - 9000) % 5 == 0]
    assert silent and all(not e["quiet"] and not 22 <= e["days"] <= 28 for e in silent)    # week 3 is silent
    weeks = {0: (0, True), 1: (7, True), 2: (14, False)}
    assert prepare_demo._quiet(weeks, 15) and not prepare_demo._quiet(weeks, 10)      # no reading at week 2 / 3 days ago
    assert prepare_demo._quiet({0: (0, True), 1: (7, True)}, 20)                      # the last reading is 13 days old
    only = {0: (0, False), 1: (7, True)}
    assert prepare_demo._quiet(only, 3)                                                # unavoidable: counted, not hidden
    live = [dict(e, quiet=i < 2) for i, e in enumerate(plan) if e["live"]]
    sheet = prepare_demo.bulk_sheet(db, [], live, {"asked": 60, "found": 91, "kept_out": 0, "linked": 0, "no_week1": 0,
                                                   "topped_up": 0, "by_band": {}, "by_group": {}}, {}, 0)
    assert "(2 of them have gone quiet)" in sheet


def test_a_patient_whose_week_one_was_not_in_the_first_week_home_is_not_chosen(crowd):
    db = crowd
    taken = {p for _, p in prepare_demo.choose(db, HID)}
    before = {e["patient_id"] for e in prepare_demo.choose_bulk(db, HID, 60, taken, TODAY)[0]}
    pid = sorted(before)[0]
    db["weekly_monitoring"].update_one({"patient_id": pid, "week_number": 1}, {"$set": {"days_after_discharge": 10}})
    disch = dt.date.fromisoformat(db["patient_worklist"].find_one({"patient_id": pid})["discharge_date"])
    db["weekly_monitoring"].update_one({"patient_id": pid, "week_number": 1},
                                       {"$set": {"week_date": (disch + dt.timedelta(days=10)).isoformat()}})
    plan, report = prepare_demo.choose_bulk(db, HID, 60, taken, TODAY)
    assert pid not in {e["patient_id"] for e in plan} and report["no_week1"] == 2


def test_the_risk_bell_markers_people_had_before_survive_and_those_made_since_go(crowd):
    db = crowd
    pid = "MIMIC-9001"
    mark = lambda at, week: db["risk_watch_seen"].insert_one(            # noqa: E731
        {"user_id": "u", "patient_id": pid, "week_number": week, "seen_at": at.isoformat()})
    mark(dt.datetime(2026, 9, 1, tzinfo=dt.timezone.utc), 1)
    _, bulk, _ = set_up(db)
    assert pid in {e["patient_id"] for e in bulk}
    mark(dt.datetime.now(dt.timezone.utc) + dt.timedelta(seconds=5), 2)
    prepare_demo.undo(db, HID)
    assert [m["week_number"] for m in db["risk_watch_seen"].find()] == [1]


def test_undo_refreshes_the_scenes_even_if_putting_back_the_rest_fails(crowd, monkeypatch):
    db = crowd
    set_up(db)
    refreshed = []
    monkeypatch.setattr(prepare_demo, "_refresh", lambda db, pids: refreshed.append(set(pids)))
    monkeypatch.setattr(prepare_demo, "_undo_bulk", lambda db, hospital: (_ for _ in ()).throw(RuntimeError("crash")))
    with pytest.raises(RuntimeError, match="crash"):
        prepare_demo.undo(db, HID)
    assert refreshed == [SCENE_IDS]

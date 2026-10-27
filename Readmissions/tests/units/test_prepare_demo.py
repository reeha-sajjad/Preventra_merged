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
        db["care_actions"].insert_one({"patient_id": pid, "hospital_id": HID,
                                       **({"patient_account_id": uid} if linked else {})})
        for wk in range(5):
            db["weekly_monitoring"].insert_one({
                "patient_id": pid, "week_number": wk, "days_after_discharge": 7 * wk,
                "week_date": (disch + dt.timedelta(days=7 * wk)).isoformat(), "risk_score": 45.0,
                "risk_band": band, "observed": True, "red_flags": [], "source": "simulated",
                "clinical_group": group, **({"discharge_baseline": {"dry_weight_kg": 80.0}} if wk == 0 else {})})
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


def test_running_again_resets_the_demo_rather_than_moving_twice(db):
    prepare_demo.prepare(db, HID, TODAY, write=True)
    # The demo happens: a check-in, an alert, a checklist tick.
    db["weekly_monitoring"].insert_one({"patient_id": "MIMIC-1", "week_number": 1, "reported_by": "patient",
                                        "week_date": "2026-10-27"})
    db["checkins"].insert_one({"patient_id": "MIMIC-1", "discharge_date": "2026-10-24", "week_number": 1})
    db["discharge_plans"].insert_one({"patient_id": "MIMIC-3", "discharge_date": "2026-10-15"})
    db["clinical_alerts"].insert_one({"alert_id": "NEW", "patient_id": "MIMIC-1",
                                      "created_at": dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")})
    prepare_demo.prepare(db, HID, TODAY, write=True)
    assert [w["week_number"] for w in series(db, "MIMIC-1")] == [0]
    assert series(db, "MIMIC-1")[0]["week_date"] == "2026-10-24"
    assert db["checkins"].count_documents({}) == 0 and db["discharge_plans"].count_documents({}) == 0
    assert {a["alert_id"] for a in db["clinical_alerts"].find()} == {"OLD"}
    assert db[prepare_demo.STATE].count_documents({}) == 6


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

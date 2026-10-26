"""The weekly check-in (api/checkins.py): a patient or their caretaker sends
this week's readings, scored like any feed's.

Same accounts as test_isolation.py: me@patient.test is A4, carer@home.test is
A4's caretaker. A4 is a heart failure patient discharged on 1 October.
"""
import datetime as dt

import pytest

from api import checkins
from ingestion import scheduler, worker
from tests.units.test_ingest import bundle, machine, token  # noqa: F401
from tests.units.test_isolation import A4, as_user, main, world  # noqa: F401


def on(monkeypatch, day):
    monkeypatch.setattr(scheduler, "today", lambda: dt.date(2026, 10, day) if isinstance(day, int) else day)


@pytest.fixture
def a4(world, monkeypatch):
    db = world["db"]
    db["weekly_monitoring"].delete_many({"patient_id": A4})
    db["weekly_monitoring"].insert_one({
        "patient_id": A4, "week_number": 0, "days_after_discharge": 0, "week_date": "2026-10-01",
        "risk_score": 32.0, "risk_band": "Medium", "observed": True, "red_flags": [],
        "clinical_group": "heart_failure", "group_label": "Heart failure", "source": "simulated",
        "model_version": "test", "discharge_baseline": {"dry_weight_kg": 80.0, "group": "heart_failure"}})
    on(monkeypatch, 9)                                  # day 8: week 2
    monkeypatch.setattr("api.main._worklist_driver", lambda pid, row: "")
    return db


def test_the_form_asks_what_the_plan_scores(main, world, a4):
    form = as_user(main, world, "me@patient.test").get("/api/my-home/checkin").json()
    assert form["open"] and form["week_number"] == 2 and form["closed_reason"] is None
    keys = [q["key"] for q in form["questions"]]
    # Heart failure's own signals lead; the pharmacy reports refills itself.
    assert keys[:3] == ["orthopnoea_pillows", "ankle_swelling", "walk_distance_pct"]
    assert "weight_kg" in keys and "refill_status" not in keys and "followup_status" in keys
    monitor = [q["key"] for q in form["questions"] if q["monitor"]]
    assert monitor == keys[-len(monitor):] and "spo2" in monitor          # devices last
    assert form["window_ends"] == "2026-10-31"


def test_no_discharge_weight_means_no_weight_question(main, world, a4):
    a4["weekly_monitoring"].update_one({"patient_id": A4, "week_number": 0}, {"$unset": {"discharge_baseline": ""}})
    keys = [q["key"] for q in as_user(main, world, "me@patient.test").get("/api/my-home/checkin").json()["questions"]]
    assert "weight_kg" not in keys


def test_a_check_in_is_scored_into_the_week_once(main, world, a4):
    me = as_user(main, world, "me@patient.test")
    r = me.post("/api/my-home/checkin", json={"answers": {
        "orthopnoea_pillows": 3, "ankle_swelling": "marked", "adherence_pct": 80, "weight_kg": 82.5}})
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["sent"] and not body["urgent"] and body["done_this_week"] and not body["open"]
    week = a4["weekly_monitoring"].find_one({"patient_id": A4, "week_number": 2})
    assert week["reported_by"] == "patient" and week["observed"] and week["source"] == "simulated"
    assert week["monitoring"]["weight_change_kg"] == 2.5 and week["days_after_discharge"] == 8
    assert week["risk_score"] > 32.0 and week["driver_1"]                  # marked swelling, 2.5 kg up
    assert week["week_date"] == "2026-10-09"
    log = a4[checkins.COLLECTION].find_one({"patient_id": A4})
    assert log["by_role"] == "patient" and log["week_number"] == 2 and log["hospital_id"] == "hosp-a"
    assert me.post("/api/my-home/checkin", json={"answers": {"ankle_swelling": "none"}}).status_code == 409
    # The worklist row moved with it.
    row = a4["patient_worklist"].find_one({"patient_id": A4, "batch_date": "2026-04-29"})
    assert row["current_score"] == week["risk_score"] and row["weeks_tracked"] == 2


def test_a_red_flag_gets_the_safety_advice_and_raises_an_alert(main, world, a4, monkeypatch):
    raised = []
    monkeypatch.setattr("api.scoring_service._alerts_for", lambda db, ids: raised.extend(ids))
    body = as_user(main, world, "me@patient.test").post(
        "/api/my-home/checkin", json={"answers": {"spo2": 87, "ankle_swelling": "none"}}).json()
    assert body["urgent"] and body["message"] == checkins.URGENT
    assert body["red_flags"] == ["oxygen saturation below 90%"]
    assert raised == [A4]
    week = a4["weekly_monitoring"].find_one({"patient_id": A4, "week_number": 2})
    assert week["red_flags"] == ["spo2"] and week["risk_band"] in ("Medium", "High")


def test_a_caretaker_checks_in_for_the_person_they_care_for(main, world, a4):
    carer = as_user(main, world, "carer@home.test")
    assert carer.get(f"/api/my-home/checkin?patient={A4}").json()["open"]
    r = carer.post("/api/my-home/checkin", json={"answers": {"new_confusion": "no"}, "patient_id": A4})
    assert r.status_code == 422                     # not asked of a heart failure patient
    r = carer.post("/api/my-home/checkin", json={"answers": {"ankle_swelling": "mild"}, "patient_id": A4})
    assert r.status_code == 201
    assert a4["weekly_monitoring"].find_one({"patient_id": A4, "week_number": 2})["reported_by"] == "caretaker"


@pytest.mark.parametrize("email", ["cm@a.test", "doc.a@a.test", "admin@b.test", "ops@team.test"])
def test_staff_do_not_send_check_ins(main, world, a4, email):
    client = as_user(main, world, email)
    assert client.get("/api/my-home/checkin").status_code == 403
    assert client.post("/api/my-home/checkin", json={"answers": {"ankle_swelling": "mild"}}).status_code == 403


@pytest.mark.parametrize("day, reason", [(dt.date(2026, 10, 1), "too_soon"), (dt.date(2026, 11, 1), "ended")])
def test_the_window_opens_the_day_after_discharge_and_ends_at_30_days(main, world, a4, monkeypatch, day, reason):
    on(monkeypatch, day)
    me = as_user(main, world, "me@patient.test")
    form = me.get("/api/my-home/checkin").json()
    assert not form["open"] and form["closed_reason"] == reason and form["questions"] == []
    assert me.post("/api/my-home/checkin", json={"answers": {"ankle_swelling": "mild"}}).status_code == 409


def test_day_30_is_still_open_and_counts_as_week_5(main, world, a4, monkeypatch):
    on(monkeypatch, 31)
    form = as_user(main, world, "me@patient.test").get("/api/my-home/checkin").json()
    assert form["open"] and form["week_number"] == 5 and form["next_opens"] is None


def test_without_monitoring_there_is_nothing_to_check_in_to(main, world, a4):
    a4["weekly_monitoring"].delete_many({"patient_id": A4})
    form = as_user(main, world, "me@patient.test").get("/api/my-home/checkin").json()
    assert not form["open"] and form["closed_reason"] == "not_started"


@pytest.mark.parametrize("answers", [
    {}, {"ankle_swelling": ""}, {"refill_status": "missed"}, {"ankle_swelling": "enormous"},
    {"orthopnoea_pillows": 40}, {"weight_kg": "heavy"},
])
def test_answers_are_checked_against_what_was_asked(main, world, a4, answers):
    r = as_user(main, world, "me@patient.test").post("/api/my-home/checkin", json={"answers": answers})
    assert r.status_code == 422
    assert a4[checkins.COLLECTION].count_documents({}) == 0


def test_the_trend_page_attributes_the_week_to_the_patient_app(main, world, a4):
    as_user(main, world, "me@patient.test").post("/api/my-home/checkin",
                                                 json={"answers": {"ankle_swelling": "mild"}})
    trend = as_user(main, world, "doc.a@a.test")
    a4["care_actions"].update_one({"patient_id": A4}, {"$set": {"assigned_doctor_id": "DR-A"}})
    weeks = trend.get(f"/api/patients/{A4}/trend").json()["weeks"]
    week2 = next(w for w in weeks if w["week_number"] == 2)
    assert [s["feed"] for s in week2["sources"]] == ["Preventra patient app"]


# ------------------------------------------------------------ an ingested patient
def test_an_ingested_patients_check_in_joins_the_ingested_week(main, world, token, monkeypatch):
    from tests.units.test_ingest_scoring import send
    db = world["db"]
    send(main, token, bundle(disch="2026-10-01T12:00"))
    assert worker.run(db, once=True) == 1
    pid = db["patient_worklist"].find_one({"source": "ingest"})["patient_id"]
    me_id = world["ids"]["me@patient.test"]
    db["care_actions"].update_many({"patient_account_id": me_id}, {"$unset": {"patient_account_id": ""}})
    db["care_actions"].update_one({"patient_id": pid}, {"$set": {"patient_account_id": me_id}})
    monkeypatch.setattr(scheduler, "today", lambda: dt.date(2026, 10, 9))

    r = as_user(main, world, "me@patient.test").post(
        "/api/my-home/checkin", json={"answers": {"orthopnoea_pillows": 2, "weight_kg": 86.0}})
    assert r.status_code == 201, r.text
    stored = db["ingest_weeks"].find_one({"patient_id": pid, "week_number": 2})
    assert stored["readings"] == {"orthopnoea_pillows": 2, "weight_kg": 86.0}
    assert stored["reported_by"] == "patient" and stored["source"] == "patient_app"
    week = db["weekly_monitoring"].find_one({"patient_id": pid, "week_number": 2})
    assert week["source"] == "ingest" and week["reported_by"] == "patient"
    assert week["monitoring"]["weight_change_kg"] == 1.8                    # against 84.2 at discharge

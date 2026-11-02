"""The mood screen through the real service (models/mood_screen.py, api/checkins.py).

Flag only: the patient answers it in the weekly check-in, it never touches a score,
it raises alerts through the forecast, and only the clinical care team reads any of
it back - not the patient, an insurer, a pharmacy or (merged app) a caretaker.

Same accounts as test_isolation.py: me@patient.test is A4, a heart failure patient
discharged on 1 October (tests/units/test_checkins.py sets that up).
"""
import datetime as dt

import pytest

from api import checkins, scoring_service
from ingestion import scheduler, worker
from models import mood_screen
from tests.units.test_checkins import a4, on  # noqa: F401
from tests.units.test_checkins import bundle, token  # noqa: F401
from tests.units.test_isolation import A1, A2, A3, A4, as_user, main, world  # noqa: F401

USUAL = {"ankle_swelling": "none"}
POSITIVE_PHQ2 = {"phq_interest": 2, "phq_down": 1}
# PHQ-8 13: moderate.
WEEK4 = {"phq_interest": 2, "phq_down": 2, "phq_sleep": 2, "phq_tired": 2, "phq_appetite": 1,
         "phq_failure": 2, "phq_concentration": 1, "phq_movement": 1, "gad_nervous": 2, "gad_worry": 2}
# PHQ-8 7: mild.
MILD = {"phq_interest": 2, "phq_down": 1, "phq_sleep": 1, "phq_tired": 1, "phq_appetite": 1,
        "phq_failure": 0, "phq_concentration": 1, "phq_movement": 0}


def send(main, world, mood, answers=None, email="me@patient.test", **more):
    return as_user(main, world, email).post(
        "/api/my-home/checkin", json={"answers": USUAL if answers is None else answers,
                                      "mood_answers": mood, **more})


def week2(db, pid=A4):
    return db["weekly_monitoring"].find_one({"patient_id": pid, "week_number": 2})


def mood_alert(db, pid=A4, week=2):
    return db["clinical_alerts"].find_one({"patient_id": pid, "week_number": week})


# ------------------------------------------------------------ the form
def test_the_patient_is_offered_the_questions_with_the_crisis_line(main, world, a4):
    form = as_user(main, world, "me@patient.test").get("/api/my-home/checkin").json()
    mood = form["mood"]
    assert mood["crisis"] == mood_screen.CRISIS and "988" in mood["crisis"]
    keys = [q["key"] for part in ("phq2", "phq8_more", "gad2") for q in mood[part]]
    assert keys and not any(w in k for k in keys for w in ("self", "harm", "dead"))
    assert [o["value"] for o in mood["options"]] == [0, 1, 2, 3] and mood["followup_from"] == 3


def test_the_questions_go_once_the_week_is_in(main, world, a4):
    assert send(main, world, {}).status_code == 201
    assert as_user(main, world, "me@patient.test").get("/api/my-home/checkin").json()["mood"] is None


# ------------------------------------------------------------ sending it
def test_the_answers_are_kept_beside_the_readings_never_in_them(main, world, a4):
    r = send(main, world, WEEK4)
    assert r.status_code == 201, r.text
    body = r.json()
    # What the patient is told is unchanged: no mood, no score, and not "urgent".
    assert body["sent"] and body["mood_sent"] and not body["urgent"] and body["mood"] is None
    assert body["message"] == "Thank you. Your care team will see this."
    week = week2(a4)
    assert week["mood"]["phq8"] == 13 and week["mood"]["phq2"] == 4 and week["mood"]["gad2"] == 4
    assert week["mood"]["flags"] == ["phq8_moderate", "gad2_positive"]
    assert not [k for k in week["monitoring"] if k.startswith(("phq", "gad", "mood"))]
    assert not [k for k in week["red_flags"] if k.startswith(("phq", "gad", "mood"))]
    log = a4[checkins.COLLECTION].find_one({"patient_id": A4})
    assert log["mood"]["phq8"] == 13 and "phq_down" not in log["answered"]
    assert a4["ingest_weeks"].count_documents({}) == 0


def test_a_check_in_without_mood_stores_none_and_says_so(main, world, a4):
    body = send(main, world, {}).json()
    assert body["mood_sent"] is False
    assert "mood" not in week2(a4) and "mood" not in a4[checkins.COLLECTION].find_one({"patient_id": A4})


def test_mood_never_changes_the_score(main, world, a4):
    me = as_user(main, world, "me@patient.test")
    answers = {"ankle_swelling": "mild", "orthopnoea_pillows": 2}
    send(main, world, WEEK4, answers)
    with_mood = week2(a4)
    a4["weekly_monitoring"].delete_many({"patient_id": A4, "week_number": 2})
    a4[checkins.COLLECTION].delete_many({})
    assert me.post("/api/my-home/checkin", json={"answers": answers}).status_code == 201
    plain = week2(a4)
    for field in ("risk_score", "risk_band", "driver_1", "driver_2", "driver_3", "red_flags", "monitoring",
                  "rule_adjustment"):
        assert plain[field] == with_mood[field], field
    assert "mood" not in plain


def test_the_rest_of_the_phq8_is_dropped_when_the_phq2_is_under_three(main, world, a4):
    extras = {k: 2 for k in mood_screen.PHQ8_MORE}
    assert send(main, world, {"phq_interest": 1, "phq_down": 1, **extras}).status_code == 201
    mood = week2(a4)["mood"]
    assert mood["phq2"] == 2 and mood["phq8"] is None and mood["flags"] == []
    assert set(mood["answers"]) == {"phq_interest", "phq_down"}


@pytest.mark.parametrize("mood", [
    {"phq_interest": 4}, {"phq_interest": "often"}, {"phq_interest": True}, {"phq_interest": 1.5},
    {"phq_self_harm": 3}, {"made_up": 1}, {"phq_interest": 1, "gad_worry": 9},
])
def test_bad_mood_answers_are_refused_and_nothing_is_written(main, world, a4, mood):
    assert send(main, world, mood).status_code == 422
    assert a4[checkins.COLLECTION].count_documents({}) == 0
    assert week2(a4) is None


def test_mood_alone_is_not_a_check_in(main, world, a4):
    r = send(main, world, WEEK4, answers={})
    assert r.status_code == 422 and r.json()["detail"] == "Answer at least one question"
    assert a4[checkins.COLLECTION].count_documents({}) == 0


def test_blank_mood_answers_are_no_answers(main, world, a4):
    assert send(main, world, {"phq_interest": "", "phq_down": None}).status_code == 201
    assert "mood" not in week2(a4)


def test_this_weeks_answers_are_compared_with_last_weeks(main, world, a4, monkeypatch):
    assert send(main, world, MILD).status_code == 201
    assert week2(a4)["mood"]["flags"] == []
    on(monkeypatch, 16)                                          # day 15: week 3
    assert send(main, world, WEEK4).status_code == 201
    mood = a4["weekly_monitoring"].find_one({"patient_id": A4, "week_number": 3})["mood"]
    assert mood["previous"] == {"week_number": 2, "phq8": 7} and mood["change"] == 6
    assert mood["flags"] == ["phq8_moderate", "phq8_rise", "gad2_positive"]


def test_low_mood_with_missed_doses_is_flagged(main, world, a4):
    send(main, world, POSITIVE_PHQ2, {"ankle_swelling": "none", "adherence_pct": 50})
    assert "low_mood_adherence" in week2(a4)["mood"]["flags"]
    assert week2(a4)["mood"]["adherence_pct"] == 50.0


# ------------------------------------------------------------ the alert
def test_a_moderate_screen_raises_an_alert_through_the_forecast(main, world, a4):
    assert send(main, world, WEEK4).status_code == 201
    alert = mood_alert(a4)
    assert alert and alert["severity"] in ("high", "critical")
    codes = {t["code"] for t in alert["forecast"]["triggers"] if t["stream"] == "mood"}
    assert "phq8_moderate" in codes
    assert alert["forecast"]["severity_without_mood"] == "none"


def test_an_anxiety_screen_alone_raises_no_alert(main, world, a4):
    send(main, world, {"phq_interest": 0, "phq_down": 0, "gad_nervous": 2, "gad_worry": 2})
    assert week2(a4)["mood"]["flags"] == ["gad2_positive"]
    assert mood_alert(a4) is None


# ------------------------------------------------------------ who reads it back
def doctor_for_a4(db):
    db["care_actions"].update_one({"patient_id": A4}, {"$set": {"assigned_doctor_id": "DR-A"}})


def opened(main, world, email, pid=A4):
    """A hospital admin (or insurer) opens the clinical layer with a reason first.
    An insurer's reasons are a payer's where the app has them (access.PAYER_REASONS)
    and the hospital's list otherwise, so the first one the account may give is used."""
    client = as_user(main, world, email)
    codes = [client.post(f"/api/patients/{pid}/open", json={"reason": reason}).status_code
             for reason in ("claim_review", "audit")]
    assert 200 in codes, codes
    return client


def mood_of(client, pid=A4, week=2):
    weeks = client.get(f"/api/patients/{pid}/trend").json()["weeks"]
    return next(w for w in weeks if w["week_number"] == week)["mood"]


def test_the_care_team_reads_the_week_card_and_the_patient_does_not(main, world, a4):
    send(main, world, WEEK4)
    doctor_for_a4(a4)
    card = mood_of(as_user(main, world, "doc.a@a.test"))
    assert [(line["key"], line["text"]) for line in card["lines"]] == [
        ("phq8", "13 · moderate"), ("gad2", "4 · positive screen")]
    assert {f["code"] for f in card["flags"]} == {"phq8_moderate", "gad2_positive"}
    assert mood_of(as_user(main, world, "cm@a.test")) is not None
    assert mood_of(opened(main, world, "admin@a.test")) is not None
    me = as_user(main, world, "me@patient.test")
    assert mood_of(me) is None
    assert all(w["mood"] is None for w in me.get(f"/api/patients/{A4}/trend").json()["weeks"])
    assert "phq8_moderate" not in me.get(f"/api/patients/{A4}/trend").text


def test_the_patient_does_not_read_it_in_the_forecast_or_the_alerts(main, world, a4):
    send(main, world, WEEK4)
    doctor_for_a4(a4)
    alert_id = mood_alert(a4)["alert_id"]
    doc = as_user(main, world, "doc.a@a.test")
    seen = doc.get(f"/api/patients/{A4}/forecast").json()["forecast"]
    assert seen["severity"] == "high" and any(t["stream"] == "mood" for t in seen["triggers"])
    assert alert_id in {a["alert_id"] for a in doc.get(f"/api/patients/{A4}/clinical-alerts").json()["alerts"]}

    me = as_user(main, world, "me@patient.test")
    forecast = me.get(f"/api/patients/{A4}/forecast").json()["forecast"]
    assert not any(t["stream"] == "mood" for t in forecast["triggers"])
    assert forecast["severity"] == "none" and "mood" not in forecast["streams"]
    assert "severity_without_mood" not in forecast
    alerts = me.get(f"/api/patients/{A4}/clinical-alerts").json()["alerts"]
    assert alert_id not in {a["alert_id"] for a in alerts}


def test_an_insurer_never_reads_it_even_after_giving_a_reason(main, world):
    db = world["db"]
    mood = mood_screen.assess(WEEK4, [], 2)
    db["weekly_monitoring"].insert_one({
        "patient_id": A1, "week_number": 2, "days_after_discharge": 14, "week_date": "2026-04-24",
        "risk_score": 40.0, "risk_band": "Low", "observed": True, "red_flags": [], "monitoring": {}, "mood": mood})
    trigger = mood_screen.triggers(mood, "heart_failure")[0]
    db["clinical_alerts"].insert_one({
        "alert_id": "CA-MOOD", "patient_id": A1, "week_number": 2, "status": "pending", "severity": "high",
        "doctor_id": "DR-A", "created_at": "2026-04-24 10:00:00",
        "forecast": {"status": "ok", "severity": "high", "severity_without_mood": "none", "as_of_week": 2,
                     "streams": ["mood"], "triggers": [trigger], "recommended_review_by": "within 48 hours"}})
    insurer = opened(main, world, "claims@acme.test", A1)

    assert mood_of(insurer, A1) is None
    assert "phq8_moderate" not in insurer.get(f"/api/patients/{A1}/trend").text
    assert "CA-MOOD" not in {a["alert_id"] for a in insurer.get(f"/api/patients/{A1}/clinical-alerts").json()["alerts"]}
    forecast = insurer.get(f"/api/patients/{A1}/forecast").json()["forecast"]
    assert not any(t["stream"] == "mood" for t in forecast["triggers"])
    admin = opened(main, world, "admin@a.test", A1)
    assert mood_of(admin, A1)["lines"][0]["key"] == "phq8"
    assert "CA-MOOD" in {a["alert_id"] for a in admin.get(f"/api/patients/{A1}/clinical-alerts").json()["alerts"]}
    seen = admin.get(f"/api/patients/{A1}/forecast").json()["forecast"]
    assert any(t["stream"] == "mood" for t in seen["triggers"]) and forecast["severity"] != "high"


def test_the_insurers_open_alert_count_leaves_out_a_mood_only_alert(main, world):
    db = world["db"]
    insurer, admin = as_user(main, world, "claims@acme.test"), as_user(main, world, "admin@a.test")
    before = (insurer.get("/api/overview").json()["open_alerts"], admin.get("/api/overview").json()["open_alerts"])
    base = {"patient_id": A1, "status": "pending", "severity": "high", "doctor_id": "DR-A",
            "created_at": "2026-04-24 10:00:00"}
    db["clinical_alerts"].insert_one({**base, "alert_id": "CA-MOOD", "week_number": 2,
                                      "forecast": {"severity": "high", "severity_without_mood": "none"}})
    after = (insurer.get("/api/overview").json()["open_alerts"], admin.get("/api/overview").json()["open_alerts"])
    assert after == (before[0], before[1] + 1)
    # An alert the vitals raised counts for both, and so does one written before this field existed.
    db["clinical_alerts"].insert_one({**base, "alert_id": "CA-VITALS", "week_number": 3,
                                      "forecast": {"severity": "high", "severity_without_mood": "high"}})
    db["clinical_alerts"].insert_one({**base, "alert_id": "CA-OLD", "week_number": 4,
                                      "forecast": {"severity": "high"}})
    final = (insurer.get("/api/overview").json()["open_alerts"], admin.get("/api/overview").json()["open_alerts"])
    assert final == (before[0] + 2, before[1] + 3)


def answered_with_referral(alert_id, pid, triggers, **fields):
    """An alert a doctor answered by choosing the behavioural health referral."""
    return {"alert_id": alert_id, "patient_id": pid, "status": "responded", "severity": "high",
            "doctor_id": "DR-A", "created_at": "2026-04-24 10:00:00",
            "forecast": {"severity": "high", "severity_without_mood": "high", "streams": ["vitals"],
                         "triggers": triggers},
            "response": {"doctor_id": "DR-A", "recommendation": "Low mood since discharge, refer",
                         "actions": ["behavioural_health", "contact_patient"],
                         "action_labels": ["Behavioural health referral", "Telephone the patient"],
                         "urgency": "routine", "responded_at": "2026-04-24 11:00:00"}, **fields}


@pytest.mark.parametrize("email, pid", [("me@patient.test", A4), ("claims@acme.test", A1)])
def test_an_alert_reply_that_chose_the_referral_loses_it_for_everyone_but_the_care_team(main, world, email, pid):
    db = world["db"]
    vitals = {"code": "spo2", "stream": "vitals", "severity": "high", "title": "Low oxygen"}
    mood = mood_screen.triggers(mood_screen.assess(WEEK4, [], 2), "heart_failure")[0]
    db["clinical_alerts"].insert_many([
        answered_with_referral("CA-VITALS", pid, [vitals], week_number=2),               # no mood trigger at all
        answered_with_referral("CA-MIXED", pid, [vitals, mood], week_number=3)])
    client = opened(main, world, email, pid) if email.startswith("claims") else as_user(main, world, email)
    alerts = {a["alert_id"]: a for a in client.get(f"/api/patients/{pid}/clinical-alerts").json()["alerts"]}
    assert {"CA-VITALS", "CA-MIXED"} <= set(alerts)
    for alert_id in ("CA-VITALS", "CA-MIXED"):
        response = alerts[alert_id]["response"]
        assert response["actions"] == ["contact_patient"] and response["action_labels"] == ["Telephone the patient"]
        assert response["recommendation"] == ""
    assert not any(t["stream"] == "mood" for t in alerts["CA-MIXED"]["forecast"]["triggers"])
    assert "behavioural" not in str(alerts).lower() and "mood" not in str(alerts).lower()

    # The care team still reads all of it.
    seen = {a["alert_id"]: a for a in as_user(main, world, "cm@a.test").get(
        f"/api/patients/{pid}/clinical-alerts").json()["alerts"]}
    for alert_id in ("CA-VITALS", "CA-MIXED"):
        assert seen[alert_id]["response"]["actions"] == ["behavioural_health", "contact_patient"]
        assert seen[alert_id]["response"]["recommendation"] == "Low mood since discharge, refer"
    assert any(t["stream"] == "mood" for t in seen["CA-MIXED"]["forecast"]["triggers"])
    # Nothing was changed in the database.
    assert db["clinical_alerts"].find_one({"alert_id": "CA-VITALS"})["response"]["actions"][0] == "behavioural_health"


# ------------------------------------------------------------ behavioural health follow-up
def ask(main, world, email, pid, **more):
    return as_user(main, world, email).post(f"/api/patients/{pid}/followup",
                                            json={"urgency": "routine", "note": "low mood", **more})


def test_a_doctor_asks_for_a_behavioural_health_referral(main, world):
    assert ask(main, world, "doc.a@a.test", A1, reason="made_up").status_code == 422
    r = ask(main, world, "doc.a@a.test", A1, reason="behavioural_health")
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["reason"] == "behavioural_health" and body["reason_label"] == "Behavioural health referral"
    items = as_user(main, world, "cm@a.test").get("/api/followups").json()["items"]
    assert items[0]["reason_label"] == "Behavioural health referral" and items[0]["note"] == "low mood"
    doc = as_user(main, world, "doc.a@a.test").get(f"/api/patients/{A1}/followup").json()
    assert doc["active"]["reason"] == "behavioural_health"
    assert doc["followup_reasons"] == [{"key": "behavioural_health", "label": "Behavioural health referral"}]
    # The reason is optional.
    assert ask(main, world, "doc.a@a.test", A2).json()["reason"] is None


def test_an_insurer_sees_the_request_without_why(main, world):
    ask(main, world, "doc.a@a.test", A1, reason="behavioural_health")
    insurer = opened(main, world, "claims@acme.test", A1)
    seen = insurer.get(f"/api/patients/{A1}/followup").json()
    assert seen["active"]["status"] == "open" and seen["followup_reasons"] == []
    assert seen["active"]["reason"] is None and seen["active"]["reason_label"] is None
    assert seen["active"]["note"] == "" and seen["active"]["notes"] == []
    assert "behavioural" not in insurer.get(f"/api/patients/{A1}/followup").text.lower()


def test_an_insurer_sees_a_finished_referral_without_its_notes_or_its_alert(main, world):
    body = reply(main, world, f"CA-{A1}", ["behavioural_health"]).json()["followup"]
    assert body["source"] == "clinical_alert" and body["alert_id"] == f"CA-{A1}"
    cm = as_user(main, world, "cm@a.test")
    cm.post(f"/api/followups/{body['id']}/take")
    done = cm.post(f"/api/followups/{body['id']}/done",
                   json={"outcome": "booked", "note": "Booked with the low mood clinic"})
    assert done.status_code == 200, done.text
    insurer = opened(main, world, "claims@acme.test", A1)
    seen = insurer.get(f"/api/patients/{A1}/followup").json()
    recent = seen["recent"]
    assert recent["status"] == "done" and recent["outcome"] == "booked"
    assert recent["outcome_note"] == "" and recent["note"] == "" and recent["notes"] == []
    assert recent["reason"] is None and recent["alert_id"] is None and recent["source"] is None
    assert "low mood" not in str(seen).lower() and "behavioural" not in str(seen).lower()
    assert f"CA-{A1}" not in str(seen)
    doctor = as_user(main, world, "doc.a@a.test").get(f"/api/patients/{A1}/followup").json()["recent"]
    assert doctor["outcome_note"] == "Booked with the low mood clinic" and doctor["alert_id"] == f"CA-{A1}"


def test_a_patient_reads_no_detail_of_a_finished_referral(main, world):
    now = dt.datetime.now(dt.timezone.utc)
    world["db"]["followups"].insert_one({
        "patient_id": A4, "status": "done", "urgency": "routine", "hospital_id": "hosp-a",
        "reason": "behavioural_health", "note": "low mood", "notes": [], "outcome": "booked",
        "outcome_note": "clinic booked for her low mood", "source": "clinical_alert", "alert_id": "CA-X",
        "requested_by": {"id": "r", "name": "Dr A", "role": "doctor"}, "requested_at": now,
        "done_by": {"id": "c", "name": "Cm", "role": "case_manager"}, "done_at": now})
    seen = as_user(main, world, "me@patient.test").get(f"/api/patients/{A4}/followup")
    assert seen.status_code == 200
    text = seen.text.lower()
    assert "low mood" not in text and "behavioural" not in text and "ca-x" not in text


def test_an_insurer_still_sees_the_note_on_an_ordinary_request(main, world):
    ask(main, world, "doc.a@a.test", A1)
    insurer = opened(main, world, "claims@acme.test", A1)
    assert insurer.get(f"/api/patients/{A1}/followup").json()["active"]["note"] == "low mood"


def reply(main, world, alert_id, actions):
    return as_user(main, world, "doc.a@a.test").post(f"/api/clinical-alerts/{alert_id}/respond", json={
        "doctor_id": "DR-A", "recommendation": "Refer", "actions": actions, "urgency": "routine"})


def test_choosing_the_referral_in_an_alert_reply_asks_for_it(main, world):
    body = reply(main, world, f"CA-{A1}", ["behavioural_health"]).json()
    assert body["status"] == "responded"
    followup = body["followup"]
    assert followup["reason"] == "behavioural_health" and followup["source"] == "clinical_alert"
    assert "Behavioural health referral" in followup["note"]


def test_the_referral_in_a_reply_goes_onto_a_request_that_is_already_open(main, world):
    first = ask(main, world, "doc.a@a.test", A1).json()
    assert first["reason"] is None
    followup = reply(main, world, f"CA-{A1}", ["behavioural_health"]).json()["followup"]
    assert followup["id"] == first["id"] and followup["reason"] == "behavioural_health"
    assert followup["notes"][-1]["text"].startswith("Alert reply:")


def test_a_reply_without_the_referral_leaves_the_reason_alone(main, world):
    ask(main, world, "doc.a@a.test", A1, reason="behavioural_health")
    followup = reply(main, world, f"CA-{A1}", ["contact_patient"]).json()["followup"]
    assert followup["reason"] == "behavioural_health"


# ------------------------------------------------------------ outcomes
@pytest.fixture
def clock(monkeypatch):
    monkeypatch.setattr(scheduler, "today", lambda: dt.date(2026, 6, 1))


def test_outcomes_count_screens_and_compare_readmission(main, world, clock):
    db = world["db"]
    now = dt.datetime.now(dt.timezone.utc)
    for pid, mood in ((A1, mood_screen.assess({**WEEK4, "phq_interest": 3, "phq_down": 1, "phq_sleep": 1,
                                                "phq_tired": 1, "phq_appetite": 1, "phq_failure": 1,
                                                "phq_concentration": 1, "phq_movement": 1}, [], 1)),
                      (A2, mood_screen.assess({"phq_interest": 0, "phq_down": 1}, [], 1))):
        db["checkins"].insert_one({"patient_id": pid, "discharge_date": "2026-04-10", "week_number": 1,
                                   "at": now, "by_role": "patient", "mood": mood})
    # An old screen is outside the period but still counts for the comparison.
    db["checkins"].insert_one({"patient_id": A4, "discharge_date": "2026-04-10", "week_number": 1,
                               "at": now - dt.timedelta(days=100), "by_role": "patient",
                               "mood": mood_screen.assess({"phq_interest": 3, "phq_down": 1}, [], 1)})
    # A check-in with no mood answers is not a screen.
    db["checkins"].insert_one({"patient_id": A3, "discharge_date": "2026-04-10", "week_number": 1,
                               "at": now, "by_role": "patient"})
    db["followups"].insert_one({"patient_id": A1, "status": "open", "requested_at": now,
                                "reason": "behavioural_health"})
    db["followups"].insert_one({"patient_id": A2, "status": "open", "requested_at": now})
    ops = as_user(main, world, "ops@team.test")
    assert ops.post(f"/api/patients/{A1}/outcome", json={"readmitted": True, "readmit_date": "2026-04-22"}).status_code == 200
    assert ops.post(f"/api/patients/{A2}/outcome", json={"readmitted": False}).status_code == 200

    mental = as_user(main, world, "cm@a.test").get("/api/outcomes?days=30").json()["mental_health"]
    assert (mental["screened"], mental["positive"], mental["moderate_or_worse"], mental["referred"]) == (2, 1, 1, 1)
    positive, negative = mental["by_screen"]["positive"], mental["by_screen"]["negative"]
    assert (positive["known"], positive["readmitted"]) == (1, 1)
    assert (negative["known"], negative["readmitted"]) == (1, 0)
    assert as_user(main, world, "cm@a.test").get("/api/outcomes?days=7").json()["mental_health"]["screened"] == 2


def test_a_referral_added_to_an_older_open_request_counts_in_its_period(main, world, clock):
    db = world["db"]
    first = ask(main, world, "doc.a@a.test", A1).json()
    db["followups"].update_one({"active_patient": A1}, {"$set": {
        "requested_at": dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=60)}})
    cm = as_user(main, world, "cm@a.test")
    assert cm.get("/api/outcomes?days=30").json()["mental_health"]["referred"] == 0
    followup = reply(main, world, f"CA-{A1}", ["behavioural_health"]).json()["followup"]
    assert followup["id"] == first["id"] and followup["reason"] == "behavioural_health"
    assert cm.get("/api/outcomes?days=30").json()["mental_health"]["referred"] == 1
    # A request asked for long ago with the reason, and never touched since, is not in this period.
    db["followups"].update_one({"active_patient": A1}, {"$set": {"history": [
        {"action": "requested", "at": dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=60),
         "reason": "behavioural_health"}]}})
    assert cm.get("/api/outcomes?days=30").json()["mental_health"]["referred"] == 0


def test_outcomes_with_no_screens_say_so(main, world, clock):
    mental = as_user(main, world, "cm@a.test").get("/api/outcomes").json()["mental_health"]
    assert (mental["screened"], mental["positive"], mental["referred"]) == (0, 0, 0)
    assert mental["by_screen"]["positive"]["known"] == 0 and mental["by_screen"]["negative"]["known"] == 0


# ------------------------------------------------------------ an ingested patient
def test_an_ingested_patients_mood_survives_rescoring(main, world, token, monkeypatch):
    from tests.units.test_ingest_scoring import send as ingest
    db = world["db"]
    ingest(main, token, bundle(disch="2026-10-01T12:00"))
    assert worker.run(db, once=True) == 1
    pid = db["patient_worklist"].find_one({"source": "ingest"})["patient_id"]
    me_id = world["ids"]["me@patient.test"]
    db["care_actions"].update_many({"patient_account_id": me_id}, {"$unset": {"patient_account_id": ""}})
    db["care_actions"].update_one({"patient_id": pid}, {"$set": {"patient_account_id": me_id}})
    monkeypatch.setattr(scheduler, "today", lambda: dt.date(2026, 10, 9))

    r = as_user(main, world, "me@patient.test").post(
        "/api/my-home/checkin", json={"answers": {"orthopnoea_pillows": 2}, "mood_answers": WEEK4})
    assert r.status_code == 201, r.text
    stored = db["ingest_weeks"].find_one({"patient_id": pid, "week_number": 2})
    assert stored["mood"]["phq8"] == 13 and "phq_down" not in stored["readings"]
    week = db["weekly_monitoring"].find_one({"patient_id": pid, "week_number": 2})
    assert week["mood"]["phq8"] == 13
    hospital = stored["hospital_id"]
    db["weekly_monitoring"].update_one({"_id": week["_id"]}, {"$unset": {"mood": ""}})
    scoring_service.score_weeks(db, hospital, [pid])
    assert db["weekly_monitoring"].find_one({"patient_id": pid, "week_number": 2})["mood"]["phq8"] == 13

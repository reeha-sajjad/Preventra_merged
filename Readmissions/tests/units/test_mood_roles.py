"""Who may read mood: the clinical care team only (api/access.py ACTIONS["view_mood"]
= doctor, nurse, case manager). The superadmin and hospital admins run the account
and the hospital; they do not treat, so a patient's mood screen - and the alerts,
follow-up reasons and referral action that come from it - are closed to them, on
the server. Twin of GLP1/Backend/tests/test_mood.py for the shared rule.
"""
import datetime as dt

import pytest

from ingestion import scheduler
from models import mood_screen
from tests.units.test_checkins import a4, bundle, token  # noqa: F401
from tests.units.test_mood_api import (  # noqa: F401
    A1, A4, WEEK4, answered_with_referral, ask, doctor_for_a4, mood_of, opened, reply, send)
from tests.units.test_isolation import as_user, main, world  # noqa: F401


def mood_trigger():
    return mood_screen.triggers(mood_screen.assess(WEEK4, [], 2), "heart_failure")[0]


def alert(alert_id, forecast, **fields):
    return {"alert_id": alert_id, "patient_id": A1, "week_number": 2, "status": "pending", "severity": "high",
            "doctor_id": "DR-A", "created_at": "2026-04-24 10:00:00", "forecast": forecast, **fields}


@pytest.fixture
def alerts(world):
    """A mood-only alert, one the vitals raised too, and an unrouted mood-only one."""
    vitals = {"code": "spo2", "stream": "vitals", "severity": "high", "title": "Low oxygen"}
    only = {"severity": "high", "severity_without_mood": "none", "streams": ["mood"], "triggers": [mood_trigger()]}
    mixed = {"severity": "high", "severity_without_mood": "high", "streams": ["vitals", "mood"],
             "triggers": [vitals, mood_trigger()]}
    world["db"]["clinical_alerts"].insert_many([
        alert("CA-MOODONLY", only), alert("CA-MIXED", mixed),
        alert("CA-UNROUTED-MOOD", only, status="unrouted", doctor_id=None)])


def ids(response, key="alerts"):
    return {a["alert_id"] for a in response.json()[key]}


def moods_in(response, alert_id):
    found = next(a for a in response.json()["alerts"] if a["alert_id"] == alert_id)
    return [t for t in found["forecast"]["triggers"] if t["stream"] == "mood"]


def test_the_week_card_is_for_the_care_team_not_the_superadmin_or_a_hospital_admin(main, world, a4):
    send(main, world, WEEK4)
    doctor_for_a4(a4)
    for email in ("ops@team.test", "admin@a.test"):
        client = opened(main, world, email, A4) if email == "admin@a.test" else as_user(main, world, email)
        assert mood_of(client, A4) is None, email
        assert "phq8_moderate" not in client.get(f"/api/patients/{A4}/trend").text, email
    for email in ("cm@a.test", "doc.a@a.test"):
        assert mood_of(as_user(main, world, email), A4) is not None, email


def test_the_inboxes_leave_out_what_mood_alone_raised_for_the_superadmin(main, world, alerts):
    ops = as_user(main, world, "ops@team.test")
    for response in (ops.get("/api/doctors/DR-A/alerts"), ops.get("/api/clinical-alerts/mine")):
        assert response.status_code == 200
        assert "CA-MOODONLY" not in ids(response) and "CA-MIXED" in ids(response)
        assert moods_in(response, "CA-MIXED") == []
        assert "mood" not in str(response.json()).lower()
    unrouted = ops.get("/api/clinical-alerts/unrouted")
    assert unrouted.status_code == 200 and "CA-UNROUTED-MOOD" not in ids(unrouted)
    for email in ("doc.a@a.test", "cm@a.test"):                          # the care team sees all of it
        client = as_user(main, world, email)
        assert {"CA-MOODONLY", "CA-MIXED"} <= ids(client.get("/api/clinical-alerts/mine"))
        assert len(moods_in(client.get("/api/clinical-alerts/mine"), "CA-MIXED")) == 1
    assert "CA-UNROUTED-MOOD" in ids(as_user(main, world, "cm@a.test").get("/api/clinical-alerts/unrouted"))


def test_the_referral_is_not_on_offer_to_the_superadmin(main, world):
    keys = lambda email: {a["key"] for a in as_user(main, world, email).get(  # noqa: E731
        "/api/clinical-alerts/actions").json()["actions"]}
    assert "behavioural_health" not in keys("ops@team.test")
    assert "behavioural_health" in keys("doc.a@a.test") and "contact_patient" in keys("ops@team.test")


def test_the_superadmin_cannot_choose_the_referral_or_act_on_a_mood_only_alert(main, world, alerts):
    ops = as_user(main, world, "ops@team.test")
    body = {"doctor_id": "DR-A", "recommendation": "Refer", "actions": ["behavioural_health"], "urgency": "routine"}
    assert ops.post("/api/clinical-alerts/CA-MIXED/respond", json=body).status_code == 403
    assert ops.post("/api/clinical-alerts/CA-MOODONLY/acknowledge", json={"doctor_id": "DR-A"}).status_code == 404
    assert ops.post("/api/clinical-alerts/CA-MOODONLY/dismiss", json={"doctor_id": "DR-A"}).status_code == 404
    assert ops.post("/api/clinical-alerts/CA-MOODONLY/respond",
                    json={**body, "actions": ["contact_patient"]}).status_code == 404
    # An alert the vitals raised too can be acknowledged, and what comes back has no mood in it.
    acked = ops.post("/api/clinical-alerts/CA-MIXED/acknowledge", json={"doctor_id": "DR-A"})
    assert acked.status_code == 200 and acked.json()["status"] == "acknowledged"
    assert not [t for t in acked.json()["forecast"]["triggers"] if t["stream"] == "mood"]
    # The care team's reply may choose it, and the alert still reads in full for them.
    assert reply(main, world, "CA-MIXED", ["behavioural_health"]).status_code == 200


def test_a_referral_asked_for_reaches_the_case_manager_but_not_the_admins_with_its_reason(main, world):
    assert ask(main, world, "doc.a@a.test", A1, reason="behavioural_health").status_code == 201
    cm = as_user(main, world, "cm@a.test").get("/api/followups").json()["items"][0]
    assert cm["reason_label"] == "Behavioural health referral" and cm["note"] == "low mood"
    for email in ("admin@a.test", "ops@team.test"):
        body = as_user(main, world, email).get("/api/followups").json()
        item = next(i for i in body["items"] if i["patient_id"] == A1)
        assert item["reason"] is None and item["reason_label"] is None
        assert item["note"] == "" and item["notes"] == [] and item["appointment"] is None, email
        assert "behavioural" not in str(body).lower() and "low mood" not in str(body).lower(), email


def test_an_admin_who_takes_and_finishes_a_referral_never_sees_why(main, world, monkeypatch):
    monkeypatch.setattr(scheduler, "today", lambda: dt.date(2026, 6, 1))
    fid = ask(main, world, "doc.a@a.test", A1, reason="behavioural_health").json()["id"]
    admin = as_user(main, world, "admin@a.test")
    taken = admin.post(f"/api/followups/{fid}/take")
    assert taken.status_code == 200 and taken.json()["status"] == "in_progress"
    assert taken.json()["reason"] is None and taken.json()["note"] == ""
    again = admin.post(f"/api/followups/{fid}/take")
    assert again.status_code == 409 and again.json()["detail"]["followup"]["note"] == ""
    done = admin.post(f"/api/followups/{fid}/done", json={
        "outcome": "booked", "note": "Booked at the low mood clinic",
        "appointment": {"date": "2026-06-10", "time": "10:30", "place": "Wellbeing clinic"}})
    assert done.status_code == 200, done.text
    body = done.json()
    assert body["outcome"] == "booked" and body["outcome_note"] == "" and body["notes"] == []
    assert body["reason"] is None and body["appointment"] is None
    cm_done = as_user(main, world, "cm@a.test").get("/api/followups", params={"status": "done"}).json()["items"][0]
    assert cm_done["outcome_note"] == "Booked at the low mood clinic" and cm_done["appointment"]["place"] == "Wellbeing clinic"
    assert as_user(main, world, "ops@team.test").get(
        "/api/followups", params={"status": "done"}).json()["items"][0]["appointment"] is None

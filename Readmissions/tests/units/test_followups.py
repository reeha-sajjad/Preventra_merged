"""Follow-up requests (api/followups.py), the clinical-alert replies that
create them, the nurse console and the care-team archive.

Over the accounts of test_isolation.py: hospital A has a case manager
(cm@a.test), a doctor (doc.a@a.test, registry DR-A, patients A1 and A2) and a
nurse (A1-A3); hospital B has only an admin.
"""
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from api import auth, followups
from api.hospital import CARE_TEAM_HISTORY
from tests.units.test_isolation import A1, A2, A3, A4, B1, as_user, main, world  # noqa: F401


@pytest.fixture
def cm2(main, world):
    """A second case manager in hospital A."""
    db = world["db"]
    auth.users(db).insert_one({"email": "cm2@a.test", "name": "Second CM", "role": "case_manager",
                               "password_hash": auth.hash_password("correct-horse"),
                               "status": "active", "hospital_id": "hosp-a"})
    token = auth.login(db, "cm2@a.test", "correct-horse")["token"]
    return TestClient(main.app, headers={"Authorization": f"Bearer {token}"},
                      raise_server_exceptions=False)


def ask(main, world, email="doc.a@a.test", pid=A1, urgency="urgent", note="breathless, please call"):
    return as_user(main, world, email).post(f"/api/patients/{pid}/followup",
                                            json={"urgency": urgency, "note": note})


def reply(main, world, email="doc.a@a.test", alert=f"CA-{A1}", doctor="DR-A",
          actions=("contact_patient",), urgency="routine", text="Please call this week"):
    return as_user(main, world, email).post(
        f"/api/clinical-alerts/{alert}/respond",
        json={"doctor_id": doctor, "recommendation": text, "actions": list(actions),
              "urgency": urgency})


# ------------------------------------------------------------------ asking
def test_a_doctor_requests_a_follow_up(main, world):
    r = ask(main, world)
    assert r.status_code == 201
    f = r.json()
    assert (f["patient_id"], f["status"], f["urgency"], f["note"]) == (A1, "open", "urgent",
                                                                       "breathless, please call")
    assert f["requested_by"]["email"] == "doc.a@a.test" and f["hospital_id"] == "hosp-a"
    assert f["risk_at_request"]["band"]
    page = as_user(main, world, "doc.a@a.test").get(f"/api/patients/{A1}/followup").json()
    assert page["active"]["id"] == f["id"] and page["can_request"] is False


def test_only_one_open_request_per_patient(main, world):
    first = ask(main, world).json()
    second = ask(main, world, "nurse@a.test", urgency="routine")
    assert second.status_code == 409
    assert second.json()["detail"]["code"] == "already_requested"
    noted = as_user(main, world, "nurse@a.test").post(
        f"/api/followups/{first['id']}/note", json={"text": "also missed her refill"})
    assert noted.status_code == 200
    assert [n["text"] for n in noted.json()["notes"]] == ["also missed her refill"]


def test_who_may_ask_and_who_may_see(main, world):
    assert ask(main, world, "cm@a.test").status_code == 403              # they arrange, not ask
    assert ask(main, world, "doc.a@a.test", pid=B1).status_code == 404   # not their patient
    assert ask(main, world, "doc.a@a.test", pid=A3).status_code == 404   # not assigned to them
    insurer = as_user(main, world, "claims@acme.test").get(f"/api/patients/{A1}/followup")
    assert insurer.status_code == 403
    patient = as_user(main, world, "me@patient.test").get(f"/api/patients/{A4}/followup")
    assert patient.status_code == 403
    assert as_user(main, world, "doc.a@a.test").get("/api/followups").status_code == 403


# ------------------------------------------------------- the case manager
def test_the_list_shows_what_booking_needs_and_nothing_clinical(main, world):
    ask(main, world)
    r = as_user(main, world, "cm@a.test").get("/api/followups")
    assert r.status_code == 200
    (item,) = r.json()["items"]
    assert item["patient_id"] == A1 and item["doctor"] == "Dr A"
    assert item["nurses"] == ["nurse@a.test"] and item["current_band"]
    assert "Heart failure" not in r.text and "driver" not in r.text
    assert as_user(main, world, "admin@b.test").get("/api/followups").json()["items"] == []
    assert as_user(main, world, "floating@x.test").get("/api/followups").json()["items"] == []
    assert as_user(main, world, "cm@a.test").get("/api/followups/badge").json()["open"] == 1


def test_taking_is_exclusive_and_the_admin_can_step_in(main, world, cm2):
    f = ask(main, world).json()
    cm = as_user(main, world, "cm@a.test")
    assert cm.post(f"/api/followups/{f['id']}/take").json()["status"] == "in_progress"
    assert cm.get("/api/followups/badge").json()["open"] == 0
    taken = cm2.post(f"/api/followups/{f['id']}/take")
    assert taken.status_code == 409 and taken.json()["detail"]["code"] == "already_taken"
    assert cm2.post(f"/api/followups/{f['id']}/done", json={"outcome": "booked"}).status_code == 409
    done = as_user(main, world, "admin@a.test").post(f"/api/followups/{f['id']}/done",
                                                     json={"outcome": "booked", "note": "Tue 10:30"})
    assert done.status_code == 200
    assert done.json()["done_by"]["email"] == "admin@a.test"
    assert done.json()["taken_by"]["email"] == "cm@a.test"


def test_marking_done_without_taking_takes_it_on_the_way(main, world):
    f = ask(main, world).json()
    done = as_user(main, world, "cm@a.test").post(f"/api/followups/{f['id']}/done",
                                                  json={"outcome": "unreachable"})
    assert done.status_code == 200
    body = done.json()
    assert body["status"] == "done" and body["taken_by"]["email"] == "cm@a.test"
    page = as_user(main, world, "doc.a@a.test").get(f"/api/patients/{A1}/followup").json()
    assert page["active"] is None and page["recent"]["outcome"] == "unreachable"
    assert ask(main, world).status_code == 201                           # a new one may follow
    history = [h["action"] for h in world["db"][followups.COLLECTION]
               .find_one({"_id": followups._object_id(f["id"])})["history"]]
    assert history == ["requested", "done"]


def test_outcome_and_note_are_checked(main, world):
    f = ask(main, world).json()
    cm = as_user(main, world, "cm@a.test")
    assert cm.post(f"/api/followups/{f['id']}/done", json={"outcome": "maybe"}).status_code == 422
    long_note = {"outcome": "booked", "note": "x" * 501}
    assert cm.post(f"/api/followups/{f['id']}/done", json=long_note).status_code == 422


def test_an_old_open_request_is_overdue(main, world):
    f = ask(main, world).json()
    world["db"][followups.COLLECTION].update_one(
        {"_id": followups._object_id(f["id"])},
        {"$set": {"requested_at": datetime.now(timezone.utc) - timedelta(days=8)}})
    cm = as_user(main, world, "cm@a.test")
    listing = cm.get("/api/followups").json()
    assert listing["items"][0]["overdue"] is True and listing["counts"]["overdue"] == 1
    assert cm.get("/api/followups/badge").json()["overdue"] == 1


# ------------------------------------------------------ from alert replies
@pytest.mark.parametrize("actions, urgency, expected", [
    (["contact_patient"], "urgent", "urgent"),
    (["schedule_review"], "routine", "routine"),
    (["arrange_labs"], "routine", "routine"),
    (["home_visit"], "immediate", "urgent"),
    (["increase_monitoring"], "routine", "routine"),
    (["escalate_ed", "contact_patient"], "immediate", "urgent"),
    (["escalate_ed"], "immediate", None),          # an emergency now, not a booking
    (["no_action"], "routine", None),
    (["adjust_medication"], "routine", None),       # the doctor's own action
    (["refer_specialist"], "routine", None),
])
def test_an_alert_reply_hands_the_right_requests_to_the_case_manager(main, world, actions,
                                                                      urgency, expected):
    r = reply(main, world, actions=actions, urgency=urgency)
    assert r.status_code == 200
    f = r.json()["followup"]
    if expected is None:
        assert f is None
        assert as_user(main, world, "cm@a.test").get("/api/followups").json()["items"] == []
    else:
        assert (f["urgency"], f["source"], f["merged"]) == (expected, "clinical_alert", False)
        assert f["note"].startswith("From an alert reply: Please call this week")
    # The reply itself is stored as before.
    alert = world["db"]["clinical_alerts"].find_one({"alert_id": f"CA-{A1}"})
    assert alert["status"] == "responded" and alert["response"]["actions"] == actions


def test_a_case_manager_answering_creates_no_request(main, world):
    r = reply(main, world, "cm@a.test")
    assert r.status_code == 200 and r.json()["followup"] is None
    response = r.json()["response"]
    # Filed against the doctor whose inbox it is, but says who typed it.
    assert response["answered_by"] == "Dr A" and response["responder"]["email"] == "cm@a.test"


def test_a_reply_joins_the_open_request_and_can_make_it_urgent(main, world):
    first = ask(main, world, "nurse@a.test", urgency="routine").json()
    f = reply(main, world, urgency="urgent").json()["followup"]
    assert f["id"] == first["id"] and f["merged"] is True and f["urgency"] == "urgent"
    assert f["notes"][0]["text"].startswith("From an alert reply")
    assert world["db"][followups.COLLECTION].count_documents({}) == 1


# ------------------------------------------------------------ nurse console
def test_the_nurse_console_shows_alerts_on_the_nurses_patients(main, world):
    world["db"]["clinical_alerts"].update_many({}, {"$set": {"status": "pending"}})
    r = as_user(main, world, "nurse@a.test").get("/api/clinical-alerts/mine")
    assert r.status_code == 200
    assert {a["patient_id"] for a in r.json()["alerts"]} == {A1, A2, A3}
    for email in ("claims@acme.test", "me@patient.test", "admin@a.test"):
        assert as_user(main, world, email).get("/api/clinical-alerts/mine").status_code == 403


def test_a_nurse_answers_in_their_own_name(main, world):
    r = reply(main, world, "nurse@a.test", alert=f"CA-{A3}", doctor="")
    assert r.status_code == 200
    response = r.json()["response"]
    assert (response["answered_as"], response["answered_by"]) == ("nurse", "nurse@a.test")
    note = world["db"]["care_actions"].find_one({"patient_id": A3})["notes"][-1]
    assert note["author"] == "nurse@a.test (Nurse)" and note["entered_by"] == "nurse@a.test"
    assert r.json()["followup"]["requested_by"]["role"] == "nurse"


# ------------------------------------------------------ care-team archive
def test_every_care_team_change_is_archived(main, world):
    cm = as_user(main, world, "cm@a.test")
    assert cm.post("/api/care-team", json={"patient_ids": [A3], "doctor_id": "DR-A"}).status_code == 200
    assert cm.post("/api/care-team", json={"patient_ids": [A3], "nurse_ids": []}).status_code == 200
    # The same doctor again is not a change, and is not archived.
    assert cm.post("/api/care-team", json={"patient_ids": [A3], "doctor_id": "DR-A"}).status_code == 200
    admin = as_user(main, world, "admin@a.test")
    assert admin.post(f"/api/patients/{A3}/assign",
                      json={"coordinator_name": "x", "doctor_id": ""}).status_code == 200

    changes = admin.get(f"/api/patients/{A3}/care-team/history").json()["changes"]
    assert [c["via"] for c in changes] == ["assign", "care_team", "care_team"]
    oldest = changes[-1]
    assert oldest["before"]["doctor_id"] is None and oldest["after"]["doctor"] == "Dr A"
    assert oldest["changed_by"]["email"] == "cm@a.test" and oldest["hospital_id"] == "hosp-a"
    assert changes[1]["before"]["nurses"] == ["nurse@a.test"] and changes[1]["after"]["nurse_ids"] == []
    assert changes[0]["after"]["doctor_id"] is None
    assert world["db"][CARE_TEAM_HISTORY].count_documents({}) == 3


def test_care_team_history_is_for_the_roles_that_assign(main, world):
    assert as_user(main, world, "doc.a@a.test").get(
        f"/api/patients/{A1}/care-team/history").status_code == 403
    assert as_user(main, world, "admin@b.test").get(
        f"/api/patients/{A1}/care-team/history").status_code == 404

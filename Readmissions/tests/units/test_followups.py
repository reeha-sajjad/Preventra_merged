"""Follow-up requests (api/followups.py), against the real service.

Same two hospitals and accounts as test_isolation.py. The same rules as GLP-1's
GLP1/Backend/tests/test_followups.py:

  * doctors and nurses ask, for their own patients only; one open request per
    patient;
  * case managers see their own hospital's requests, take one, mark it done;
  * a doctor's reply to a clinical alert that asks for a call, a review,
    bloods or a home visit becomes a request on its own - and the reply itself
    is saved exactly as before.
"""
import pytest

from api import followups
from tests.units.test_isolation import A1, A2, A3, A4, B1, as_user, main, world  # noqa: F401


def ask(main, world, email, pid, urgency="routine", note=None):
    return as_user(main, world, email).post(f"/api/patients/{pid}/followup",
                                            json={"urgency": urgency, "note": note})


def reply(main, world, email, alert_id, actions, urgency="routine", text="See the patient"):
    return as_user(main, world, email).post(f"/api/clinical-alerts/{alert_id}/respond", json={
        "doctor_id": "DR-A", "recommendation": text, "actions": actions, "urgency": urgency})


# ------------------------------------------------------------- asking
def test_a_doctor_asks_for_their_own_patient(main, world):
    r = ask(main, world, "doc.a@a.test", A1, "urgent", "Check fluid status")
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["status"] == "open" and body["urgency"] == "urgent" and body["hospital_id"] == "hosp-a"
    assert body["requested_by"]["email"] == "doc.a@a.test" and body["source"] == "patient_page"


def test_one_open_request_per_patient(main, world):
    assert ask(main, world, "doc.a@a.test", A1).status_code == 201
    second = ask(main, world, "nurse@a.test", A1)
    assert second.status_code == 409
    assert second.json()["detail"]["code"] == "already_requested"


def test_nobody_asks_for_someone_elses_patient(main, world):
    assert ask(main, world, "doc.a@a.test", A3).status_code == 404     # A3 has no doctor
    assert ask(main, world, "doc.a@a.test", B1).status_code == 404     # other hospital


@pytest.mark.parametrize("email", ["cm@a.test", "admin@a.test", "claims@acme.test"])
def test_only_doctors_and_nurses_ask(main, world, email):
    assert ask(main, world, email, A1).status_code == 403


def test_the_care_team_can_add_a_note(main, world):
    fid = ask(main, world, "doc.a@a.test", A1).json()["id"]
    r = as_user(main, world, "nurse@a.test").post(f"/api/followups/{fid}/note", json={"text": "Prefers mornings"})
    assert r.status_code == 200 and r.json()["notes"][0]["text"] == "Prefers mornings"


# --------------------------------------------------------- handling
def test_case_managers_see_their_own_hospitals_requests(main, world):
    ask(main, world, "doc.a@a.test", A1, "urgent")
    ask(main, world, "nurse@a.test", A3)
    items = as_user(main, world, "cm@a.test").get("/api/followups").json()["items"]
    assert [i["patient_id"] for i in items] == [A1, A3]                 # urgent first
    assert items[0]["doctors"] == ["Dr A"] and items[0]["risk_band"]
    assert as_user(main, world, "admin@b.test").get("/api/followups").json()["items"] == []
    assert as_user(main, world, "cm@a.test").get("/api/followups/waiting").json() == {"waiting": 2}


@pytest.mark.parametrize("email", ["doc.a@a.test", "nurse@a.test", "claims@acme.test", "me@patient.test"])
def test_only_handlers_see_the_list(main, world, email):
    assert as_user(main, world, email).get("/api/followups").status_code == 403


def test_take_then_done_then_ask_again(main, world):
    fid = ask(main, world, "doc.a@a.test", A1).json()["id"]
    cm = as_user(main, world, "cm@a.test")
    assert cm.post(f"/api/followups/{fid}/take").json()["status"] == "in_progress"
    assert cm.post(f"/api/followups/{fid}/take").status_code == 409     # already taken
    done = cm.post(f"/api/followups/{fid}/done", json={"outcome": "booked", "note": "Tue 10:30"}).json()
    assert done["status"] == "done" and done["outcome_label"] == "Booked"

    seen = as_user(main, world, "doc.a@a.test").get(f"/api/patients/{A1}/followup").json()
    assert seen["active"] is None and seen["recent"]["outcome"] == "booked" and seen["can_request"]
    assert ask(main, world, "doc.a@a.test", A1).status_code == 201       # a new one is allowed


def test_another_hospital_cannot_touch_a_request(main, world):
    fid = ask(main, world, "doc.a@a.test", A1).json()["id"]
    assert as_user(main, world, "admin@b.test").post(f"/api/followups/{fid}/take").status_code == 404
    assert as_user(main, world, "admin@b.test").post(
        f"/api/followups/{fid}/done", json={"outcome": "booked"}).status_code == 404


def test_only_the_listed_outcomes(main, world):
    fid = ask(main, world, "doc.a@a.test", A1).json()["id"]
    r = as_user(main, world, "cm@a.test").post(f"/api/followups/{fid}/done", json={"outcome": "lost"})
    assert r.status_code == 422


# ------------------------------------------------- from an alert reply
def test_an_alert_reply_asking_for_a_call_becomes_a_request(main, world):
    r = reply(main, world, "doc.a@a.test", f"CA-{A1}", ["contact_patient", "adjust_medication"], "urgent")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "responded"                                 # the reply is saved as before
    f = body["followup"]
    assert f["source"] == "clinical_alert" and f["alert_id"] == f"CA-{A1}" and f["urgency"] == "urgent"
    assert "Telephone the patient" in f["note"] and "Medication" not in f["note"]
    assert world["db"]["care_actions"].find_one({"patient_id": A1})["notes"][-1]["source"] == "clinical_alert"


@pytest.mark.parametrize("actions,urgency", [(["no_action"], "routine"), (["escalate_ed"], "immediate"),
                                             (["adjust_medication"], "urgent")])
def test_replies_that_need_no_booking_make_no_request(main, world, actions, urgency):
    body = reply(main, world, "doc.a@a.test", f"CA-{A1}", actions, urgency).json()
    assert body["followup"] is None
    assert world["db"][followups.COLLECTION].count_documents({}) == 0


def test_an_immediate_reply_with_a_booking_is_urgent(main, world):
    f = reply(main, world, "doc.a@a.test", f"CA-{A1}", ["escalate_ed", "schedule_review"], "immediate").json()["followup"]
    assert f["urgency"] == "urgent" and "emergency" not in f["note"].lower()


def test_a_reply_on_an_open_request_adds_to_it(main, world):
    first = ask(main, world, "doc.a@a.test", A1, "routine").json()
    f = reply(main, world, "doc.a@a.test", f"CA-{A1}", ["home_visit"], "urgent").json()["followup"]
    assert f["id"] == first["id"] and f["urgency"] == "urgent"
    assert f["notes"][-1]["text"].startswith("Alert reply:")
    assert world["db"][followups.COLLECTION].count_documents({}) == 1


def test_a_case_managers_reply_does_not_ask_themselves(main, world):
    body = reply(main, world, "cm@a.test", f"CA-{A1}", ["contact_patient"]).json()
    assert body["status"] == "responded" and body["followup"] is None
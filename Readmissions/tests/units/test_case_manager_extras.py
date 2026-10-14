"""What the case manager role adds on top of follow-ups (test_followups.py):

  * "Increase monitoring frequency" in an alert reply is a request too, and the
    superadmin answering in a doctor's inbox hands one over like the doctor;
  * the nurse console: a nurse sees and answers the alerts of their own
    patients, in their own name; everyone else covering an inbox is recorded
    beside the doctor it is filed under;
  * every care-team change is archived (care_team_history).

Same two hospitals and accounts as test_isolation.py; see docs/case_manager.md.
"""
from api import followups
from api.hospital import CARE_TEAM_HISTORY
from tests.units.test_isolation import A1, A3, as_user, main, world  # noqa: F401


def reply(main, world, email, alert_id, actions, doctor="DR-A", urgency="routine",
          text="Please check in"):
    return as_user(main, world, email).post(f"/api/clinical-alerts/{alert_id}/respond", json={
        "doctor_id": doctor, "recommendation": text, "actions": actions, "urgency": urgency})


# ------------------------------------------------------ alert replies
def test_increased_monitoring_is_a_request(main, world):
    r = reply(main, world, "doc.a@a.test", f"CA-{A1}", ["increase_monitoring"])
    assert r.status_code == 200, r.text
    f = r.json()["followup"]
    assert f and f["urgency"] == "routine" and f["source"] == "clinical_alert"
    assert "Increase monitoring frequency" in f["note"]


def test_the_superadmin_answering_for_a_doctor_hands_the_request_over(main, world):
    f = reply(main, world, "ops@team.test", f"CA-{A1}", ["contact_patient"], urgency="urgent").json()["followup"]
    assert f and f["urgency"] == "urgent" and f["requested_by"]["role"] == "superadmin"


# ------------------------------------------------------- nurse console
def test_the_nurse_console_shows_alerts_on_the_nurses_patients(main, world):
    world["db"]["clinical_alerts"].update_many({}, {"$set": {"status": "pending"}})
    r = as_user(main, world, "nurse@a.test").get("/api/clinical-alerts/mine")
    assert r.status_code == 200
    assert {a["patient_id"] for a in r.json()["alerts"]} == {"MIMIC-1101", "MIMIC-1102", A3}
    for email in ("claims@acme.test", "me@patient.test", "admin@a.test"):
        assert as_user(main, world, email).get("/api/clinical-alerts/mine").status_code == 403


def test_a_nurse_answers_in_their_own_name(main, world):
    r = reply(main, world, "nurse@a.test", f"CA-{A3}", ["contact_patient"], doctor="")
    assert r.status_code == 200, r.text
    response = r.json()["response"]
    assert (response["answered_as"], response["answered_by"]) == ("nurse", "nurse@a.test")
    note = world["db"]["care_actions"].find_one({"patient_id": A3})["notes"][-1]
    assert note["author"] == "nurse@a.test (Nurse)" and note["entered_by"] == "nurse@a.test"
    assert r.json()["followup"]["requested_by"]["role"] == "nurse"


def test_covering_a_doctors_inbox_is_recorded_beside_the_doctor(main, world):
    world["db"]["clinical_alerts"].update_many({}, {"$set": {"status": "pending"}})
    cm = as_user(main, world, "cm@a.test")
    assert cm.post(f"/api/clinical-alerts/CA-{A1}/acknowledge", json={"doctor_id": "DR-A"}).status_code == 200
    alert = world["db"]["clinical_alerts"].find_one({"alert_id": f"CA-{A1}"})
    assert alert["acknowledged_by"] == "DR-A"
    assert alert["acknowledged_by_account"]["email"] == "cm@a.test"
    response = reply(main, world, "cm@a.test", f"CA-{A1}", ["no_action"]).json()["response"]
    assert response["answered_by"] == "Dr A" and response["responder"]["email"] == "cm@a.test"


# --------------------------------------------------- care-team archive
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


def test_the_extras_leave_the_follow_up_rules_alone(main, world):
    """A case manager's reply still creates nothing (followups.ALERT_REPLY_ROLES)."""
    assert "case_manager" not in followups.ALERT_REPLY_ROLES
    f = reply(main, world, "cm@a.test", f"CA-{A1}", ["increase_monitoring"]).json()["followup"]
    assert f is None

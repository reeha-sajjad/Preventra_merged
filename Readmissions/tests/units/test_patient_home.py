"""A patient's own home page in Readmissions (api/patient_home.py) and asking
the case managers to get in touch (api/followups.patient_request). Same
accounts as test_isolation.py: me@patient.test is linked to A4, in hospital A.
"""
from api import followups
from tests.units.test_isolation import A1, A4, as_user, main, world  # noqa: F401


def me(main, world):
    return as_user(main, world, "me@patient.test")


def test_the_home_page_is_their_own_record_in_plain_words(main, world):
    body = me(main, world).get("/api/my-home").json()
    assert body["linked"] and body["patient_id"] == A4
    assert body["status"]["key"] in ("on_track", "watch", "support")
    assert not any(c.isdigit() for c in body["status"]["title"])        # never the score
    assert "risk_score" not in str(body) and "driver" not in str(body)
    assert body["stay"]["reason"] == "Heart failure" and body["stay"]["discharged"] == "2026-04-10"
    assert body["care_team"]["hospital"] and body["followup"]["can_ask"] and body["safety"]


def test_only_patients_have_a_home_page(main, world):
    for email in ("doc.a@a.test", "cm@a.test", "rx@bay.test", "ops@team.test", "claims@acme.test"):
        assert as_user(main, world, email).get("/api/my-home").status_code == 403, email


def test_asking_reaches_the_case_managers_without_the_patients_login(main, world):
    r = me(main, world).post("/api/my-home/ask", json={"reason": "symptoms", "note": "Ankles swollen since Monday"})
    assert r.status_code == 201 and r.json()["added_to_open"] is False
    doc = world["db"][followups.COLLECTION].find_one({"patient_id": A4})
    assert doc["requested_by"]["name"] == "The patient" and "email" not in doc["requested_by"]
    assert doc["source"] == "patient" and "email" not in doc["history"][0]["by"]
    listed = as_user(main, world, "cm@a.test").get("/api/followups").json()["items"]
    item = next(i for i in listed if i["patient_id"] == A4)
    assert item["patient_requests"][0]["reason_label"] == "I'm not feeling well since I got home"
    assert as_user(main, world, "cm@a.test").get("/api/followups/waiting").json()["waiting"] == 1
    assert me(main, world).get("/api/my-home").json()["followup"]["active"]["status"] == "open"


def test_the_patient_never_sees_the_care_teams_notes(main, world):
    me(main, world).post("/api/my-home/ask", json={"reason": "medicines"})
    fid = str(world["db"][followups.COLLECTION].find_one({"patient_id": A4})["_id"])
    cm = as_user(main, world, "cm@a.test")
    assert cm.post(f"/api/followups/{fid}/note", json={"text": "Daughter is the main carer"}).status_code == 200
    assert cm.post(f"/api/followups/{fid}/take").status_code == 200
    me(main, world).post("/api/my-home/ask", json={"reason": "refill"})          # while it is held
    assert [i["kind"] for i in cm.get("/api/followups/bell").json()["items"]] == ["followup_report"]
    for body in (me(main, world).get("/api/my-home").json(), me(main, world).get(f"/api/patients/{A4}/followup").json()):
        assert "Daughter" not in str(body)
    assert me(main, world).post(f"/api/followups/{fid}/note", json={"text": "hi"}).status_code == 403
    assert cm.post(f"/api/followups/{fid}/done", json={"outcome": "booked", "note": "Thu 10am clinic"}).status_code == 200
    recent = me(main, world).get("/api/my-home").json()["followup"]["recent"]
    assert recent["outcome_label"] == "Booked" and "Thu 10am" not in str(recent)


def test_asking_needs_a_reason_and_is_for_patients(main, world):
    assert me(main, world).post("/api/my-home/ask", json={"reason": "nonsense"}).status_code == 422
    assert me(main, world).post("/api/my-home/ask", json={"reason": "other", "note": ""}).status_code == 422
    assert as_user(main, world, "doc.a@a.test").post("/api/my-home/ask", json={"reason": "refill"}).status_code == 403
"""Caretakers in Readmissions (api/caretakers.py) - GLP-1's twin. Same accounts
as test_isolation.py: carer@home.test is linked to A4 (me@patient.test)."""
from api import access_log, auth, followups
from tests.units.test_isolation import A1, A2, A4, B1, as_user, main, world  # noqa: F401


def new_carer(world, email="new.carer@home.test"):
    return str(auth.users(world["db"]).insert_one({
        "email": email, "name": "Sam Carer", "role": "caretaker", "status": "active", "hospital_id": None,
        "password_hash": auth.hash_password("pw-123456"), "app_access": ["readmissions"]}).inserted_id)


def test_caretakers_sign_up_ready_but_see_nothing_until_linked(main, world):
    auth.signup(world["db"], "fam@home.test", "correct-horse", role="caretaker")
    account = auth.users(world["db"]).find_one({"email": "fam@home.test"})
    assert account["status"] == "active" and account["hospital_id"] is None
    from fastapi.testclient import TestClient
    tok = auth.issue_token(account)["token"]
    c = TestClient(main.app, headers={"Authorization": f"Bearer {tok}"})
    assert c.get("/api/my-home").json() == {"linked": False, "name": None, "viewer": "caretaker"}
    assert c.get("/api/patients?limit=50").json()["data"] == []


def test_the_patient_adds_and_removes_a_caretaker(main, world):
    uid = new_carer(world)
    me = as_user(main, world, "me@patient.test")
    assert me.post("/api/caretakers", json={"email": "nobody@home.test"}).status_code == 404
    body = me.post("/api/caretakers", json={"email": "new.carer@home.test"}).json()
    assert {c["email"] for c in body["caretakers"]} == {"carer@home.test", "new.carer@home.test"}
    assert me.delete(f"/api/caretakers/{uid}").status_code == 200
    assert uid not in (world["db"]["care_actions"].find_one({"patient_id": A4}).get("caretaker_ids") or [])


def test_staff_link_only_with_the_patients_agreement(main, world):
    new_carer(world)
    cm = as_user(main, world, "cm@a.test")
    assert cm.post("/api/caretakers", json={"email": "new.carer@home.test", "patient_id": A2}).status_code == 422
    assert cm.post("/api/caretakers", json={"email": "new.carer@home.test", "patient_id": A2,
                                            "patient_agreed": True}).status_code == 201
    assert cm.post("/api/caretakers", json={"email": "new.carer@home.test", "patient_id": B1,
                                            "patient_agreed": True}).status_code == 404     # not their hospital
    assert as_user(main, world, "doc.a@a.test").post("/api/caretakers", json={
        "email": "new.carer@home.test", "patient_id": A2, "patient_agreed": True}).status_code == 403


def test_a_caretaker_opens_the_clinical_record_of_their_people_and_it_is_logged(main, world):
    carer = as_user(main, world, "carer@home.test")
    home = carer.get("/api/my-home").json()
    assert home["linked"] and home["patient_id"] == A4 and home["caring_for"]["people"][0]["patient_id"] == A4
    entry = access_log.collection(world["db"]).find_one({"email": "carer@home.test"})
    assert entry["reason"] == "caretaker"
    assert carer.get(f"/api/patients/{A4}").status_code == 200
    assert carer.get(f"/api/patients/{A1}").status_code == 404              # not one of theirs
    assert carer.get(f"/api/patients/{A4}/care-actions").status_code == 403  # the care team's notes
    assert carer.post("/api/ai-insights", json={"patient_id": A4}).status_code in (403, 422)
    assert carer.get(f"/api/caretakers?patient={A4}").status_code == 403
    for path in ("/api/overview", "/api/followups", "/api/staff"):
        assert carer.get(path).status_code in (403, 404), path


def test_a_caretakers_request_is_signed_as_theirs(main, world):
    carer = as_user(main, world, "carer@home.test")
    assert carer.post("/api/my-home/ask", json={"reason": "support", "note": "Needs a stairlift"}).status_code == 201
    doc = world["db"][followups.COLLECTION].find_one({"patient_id": A4})
    assert doc["requested_by"]["name"] == "The caretaker" and doc["source"] == "caretaker"
    item = next(i for i in as_user(main, world, "cm@a.test").get("/api/followups").json()["items"]
                if i["patient_id"] == A4)
    assert item["patient_requests"][0]["by"] == "The caretaker"
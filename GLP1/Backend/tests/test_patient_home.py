"""A patient's own home page (core/patient_home.py) and asking their case
manager to get in touch (core/followups.patient_request). Same accounts as
test_isolation.py: me@patient.test is linked to patient 3, in hospital A.
"""
import pytest

from core.followups import COLLECTION
from tests.test_isolation import client, world  # noqa: F401


def me(world):
    return client(world, "me@patient.test")


@pytest.fixture
def clean(world):
    world["db"][COLLECTION].delete_many({})
    yield
    world["db"][COLLECTION].delete_many({})


def test_the_home_page_is_their_own_record_in_plain_words(world, clean):
    body = me(world).get("/api/my-home").json()
    assert body["linked"] and body["patient_idx"] == 3
    assert body["status"]["key"] in ("on_track", "watch", "support") and body["status"]["title"]
    assert "%" not in body["status"]["title"] + body["status"]["text"]          # never the number
    assert "dropout_prob" not in body and "shap" not in str(body).lower()
    assert body["medicine"]["label"] and body["medicine"]["schedule"]
    assert {p["key"] for p in body["profile"]} == {"age", "bmi", "hba1c"}
    assert all(f["title"] and f["text"] for f in body["factors"])
    assert body["care_team"]["hospital"] and body["followup"]["can_ask"]
    assert body["safety"]


def test_only_patients_have_a_home_page(world):
    for email in ("doc@a.test", "cm@a.test", "rx@bay.test", "ops@team.test"):
        assert client(world, email).get("/api/my-home").status_code == 403


def test_a_patient_with_no_record_linked_is_told_so(world):
    from bson import ObjectId
    users = world["store"]["shared_identity"].users
    uid = users.insert_one({"email": "new@patient.test", "role": "patient", "status": "active",
                            "hospital_id": "hosp-a", "app_access": ["glp1"]}).inserted_id
    import jwt, time
    from core.config import settings
    from fastapi.testclient import TestClient
    import main
    tok = jwt.encode({"sub": str(uid), "exp": int(time.time()) + 600}, settings.shared_secret_key, algorithm="HS256")
    c = TestClient(main.app, headers={"Authorization": f"Bearer {tok}"})
    assert c.get("/api/my-home").json() == {"linked": False, "name": None}
    assert c.post("/api/my-home/ask", json={"reason": "cost"}).status_code == 404
    users.delete_one({"_id": ObjectId(uid)})


def test_asking_reaches_the_case_manager_without_the_patients_login(world, clean):
    r = me(world).post("/api/my-home/ask", json={"reason": "side_effects", "note": "Nausea every morning"})
    assert r.status_code == 201 and r.json()["added_to_open"] is False
    doc = world["db"][COLLECTION].find_one({"patient_idx": 3})
    assert doc["requested_by"]["name"] == "The patient" and "email" not in doc["requested_by"]
    item = next(i for i in client(world, "cm@a.test").get("/api/notifications").json()["items"]
                if i["patient_idx"] == 3)
    assert item["problem_label"] == "Side effects from my medicine"
    listed = client(world, "cm@a.test").get("/api/followups").json()["items"]
    assert next(i for i in listed if i["patient_idx"] == 3)["patient_requests"][0]["note"] == "Nausea every morning"
    # The patient sees it waiting on their page.
    assert me(world).get("/api/my-home").json()["followup"]["active"]["status"] == "open"


def test_the_patient_never_sees_the_care_teams_notes(world, clean):
    me(world).post("/api/my-home/ask", json={"reason": "cost"})
    fid = str(world["db"][COLLECTION].find_one({"patient_idx": 3})["_id"])
    cm = client(world, "cm@a.test")
    assert cm.post(f"/api/followups/{fid}/note", json={"text": "Lives alone, call after 5"}).status_code == 200
    assert cm.post(f"/api/followups/{fid}/take").status_code == 200
    me(world).post("/api/my-home/ask", json={"reason": "refill"})        # while it is held
    bell = cm.get("/api/notifications").json()["items"]
    assert next(i for i in bell if i["followup_id"] == fid)["kinds"] == ["followup_report"]
    for body in (me(world).get("/api/my-home").json(), me(world).get("/api/patients/3/followup").json()):
        assert "Lives alone" not in str(body)
    assert me(world).post(f"/api/followups/{fid}/note", json={"text": "hi"}).status_code == 403
    done = cm.post(f"/api/followups/{fid}/done", json={"outcome": "booked", "note": "Booked Tue 3pm, see notes"})
    assert done.status_code == 200
    recent = me(world).get("/api/my-home").json()["followup"]["recent"]
    assert recent["outcome_label"] == "Booked" and "Tue 3pm" not in str(recent)


def test_asking_needs_a_reason(world, clean):
    assert me(world).post("/api/my-home/ask", json={"reason": "nonsense"}).status_code == 422
    assert me(world).post("/api/my-home/ask", json={"reason": "other", "note": ""}).status_code == 422
    assert client(world, "doc@a.test").post("/api/my-home/ask", json={"reason": "cost"}).status_code == 403
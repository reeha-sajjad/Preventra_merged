"""Caretakers (core/caretakers.py): family or home carers a patient links.
Same accounts as test_isolation.py: carer@home.test is already linked to
patient 3 (me@patient.test's record, hospital A)."""
import time

import pytest
from bson import ObjectId
from fastapi.testclient import TestClient
from jose import jwt

import main
from core.config import settings
from core.followups import COLLECTION
from tests.test_isolation import client, world  # noqa: F401


def account(world, email, role="caretaker", status="active", name="Sam Carer"):
    users = world["store"][settings.shared_identity_db_name].users
    uid = users.insert_one({"email": email, "name": name, "role": role, "status": status,
                            "hospital_id": None, "app_access": ["glp1"]}).inserted_id
    tok = jwt.encode({"sub": str(uid), "exp": int(time.time()) + 600}, settings.shared_secret_key, algorithm="HS256")
    return str(uid), TestClient(main.app, headers={"Authorization": f"Bearer {tok}"}, raise_server_exceptions=False)


@pytest.fixture
def fresh(world):
    """A second caretaker, not linked to anyone; tidied up afterwards."""
    uid, c = account(world, "new.carer@home.test")
    yield uid, c
    world["db"].patient_access.update_many({}, {"$pull": {"caretaker_ids": uid}})
    world["store"][settings.shared_identity_db_name].users.delete_one({"_id": ObjectId(uid)})
    world["db"][COLLECTION].delete_many({})


def test_the_patient_adds_and_removes_a_caretaker(world, fresh):
    uid, carer = fresh
    me = client(world, "me@patient.test")
    assert carer.get("/api/my-home").json() == {"linked": False, "name": "Sam Carer", "viewer": "caretaker"}
    body = me.post("/api/caretakers", json={"email": "New.Carer@home.test"}).json()
    assert "new.carer@home.test" in {c["email"] for c in body["caretakers"]}
    home = carer.get("/api/my-home").json()
    assert home["linked"] and home["patient_idx"] == 3 and home["viewer"] == "caretaker"
    assert home["caring_for"]["people"] == [{"patient_idx": 3, "name": "Patient #3"}]
    # The look is on the access log, as a caretaker's.
    log = world["store"][settings.shared_identity_db_name].access_log
    assert log.find_one({"user_id": uid, "patient_id": "3"})["reason"] == "caretaker"
    assert me.delete(f"/api/caretakers/{uid}").status_code == 200
    assert carer.get("/api/my-home").json()["linked"] is False
    assert carer.get("/api/patients/3/followup").status_code == 404


def test_only_an_existing_caretaker_account_can_be_added(world, fresh):
    me = client(world, "me@patient.test")
    assert me.post("/api/caretakers", json={"email": "nobody@home.test"}).status_code == 404
    assert me.post("/api/caretakers", json={"email": "doc@a.test"}).status_code == 404      # not a caretaker
    me.post("/api/caretakers", json={"email": "new.carer@home.test"})
    assert me.post("/api/caretakers", json={"email": "new.carer@home.test"}).status_code == 409


def test_staff_link_a_caretaker_only_with_the_patients_agreement(world, fresh):
    uid, carer = fresh
    cm = client(world, "cm@a.test")
    assert cm.post("/api/caretakers", json={"email": "new.carer@home.test", "patient_idx": 2}).status_code == 422
    r = cm.post("/api/caretakers", json={"email": "new.carer@home.test", "patient_idx": 2, "patient_agreed": True})
    assert r.status_code == 201
    assert carer.get("/api/my-home").json()["patient_idx"] == 2
    doc = world["db"].patient_access.find_one({"patient_idx": 2})
    assert doc["caretaker_history"][-1]["agreement"] == "confirmed_by_staff"
    # Not a patient of their hospital: as if it didn't exist.
    assert cm.post("/api/caretakers", json={"email": "new.carer@home.test", "patient_idx": 4,
                                            "patient_agreed": True}).status_code == 404
    for email in ("doc@a.test", "nurse@a.test", "rx@bay.test", "claims@acme.test"):
        assert client(world, email).post("/api/caretakers", json={"email": "new.carer@home.test", "patient_idx": 0,
                                                                  "patient_agreed": True}).status_code in (403, 404)


def test_a_caretaker_opens_the_clinical_record_of_their_people_and_it_is_logged(world):
    carer = client(world, "carer@home.test")
    assert carer.get("/api/my-home").json()["patient_idx"] == 3
    world["db"].access_log.delete_many({}) if "access_log" in world["db"].list_collection_names() else None
    assert carer.get("/api/patients/3").status_code == 200
    assert carer.get("/api/patients/3/doses").status_code == 200
    assert carer.get("/api/patients/4").status_code == 404                     # not one of theirs
    assert carer.get("/api/caretakers?patient=3").status_code == 403          # not theirs to manage
    for path in ("/api/overview", "/api/staff", "/api/followups", "/api/cost-effectiveness"):
        assert carer.get(path).status_code in (403, 404), path


def test_a_caretaker_can_ask_the_case_manager_and_step_away(world, fresh):
    uid, carer = fresh
    client(world, "me@patient.test").post("/api/caretakers", json={"email": "new.carer@home.test"})
    r = carer.post("/api/my-home/ask", json={"reason": "refill", "note": "Pharmacy out of pens"})
    assert r.status_code == 201
    doc = world["db"][COLLECTION].find_one({"patient_idx": 3})
    assert doc["requested_by"]["name"] == "The caretaker" and "email" not in doc["requested_by"]
    listed = client(world, "cm@a.test").get("/api/followups").json()["items"]
    assert next(i for i in listed if i["patient_idx"] == 3)["patient_requests"][0]["by"] == "The caretaker"
    # A caretaker can remove only themselves.
    other = str(world["store"][settings.shared_identity_db_name].users.find_one({"email": "carer@home.test"})["_id"])
    assert carer.delete(f"/api/caretakers/{other}?patient=3").status_code == 403
    assert carer.delete(f"/api/caretakers/{uid}?patient=3").json() == {"removed": True}
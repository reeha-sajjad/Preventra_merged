"""Follow-up requests (core/followups.py), over the same accounts as
test_isolation.py. Hospital A has a case manager (cm@a.test); hospital B has
only an admin (admin@b.test), who therefore gets the requests in their bell."""
import time

import pytest
from fastapi.testclient import TestClient
from jose import jwt

import main
from core import access
from core.config import settings
from core.followups import COLLECTION
from core.notifications import SEEN_COLLECTION
from tests.test_isolation import client, world  # noqa: F401


@pytest.fixture(autouse=True)
def fresh(world):
    world["db"][COLLECTION].delete_many({})
    world["db"][SEEN_COLLECTION].delete_many({})
    yield
    world["db"][COLLECTION].delete_many({})
    world["db"][SEEN_COLLECTION].delete_many({})


@pytest.fixture
def second_cm(world):
    """Another case manager in hospital A."""
    users = world["store"][settings.shared_identity_db_name].users
    uid = users.insert_one({"email": "cm2@a.test", "name": "Second CM", "role": "case_manager",
                            "status": "active", "hospital_id": "hosp-a", "app_access": ["glp1"]}).inserted_id
    token = jwt.encode({"sub": str(uid), "exp": int(time.time()) + 3600},
                       settings.shared_secret_key, algorithm="HS256")
    yield TestClient(main.app, headers={"Authorization": f"Bearer {token}"}, raise_server_exceptions=False)
    users.delete_one({"_id": uid})


def ask(world, email="doc@a.test", idx=0, urgency="urgent", note="side effects"):
    return client(world, email).post(f"/api/patients/{idx}/followup", json={"urgency": urgency, "note": note})


def bell(world, email):
    return client(world, email).get("/api/notifications").json()


# ------------------------------------------------------------------ asking
def test_a_doctor_requests_a_follow_up(world):
    r = ask(world)
    assert r.status_code == 201
    f = r.json()
    assert (f["patient_idx"], f["status"], f["urgency"], f["note"]) == (0, "open", "urgent", "side effects")
    assert f["requested_by"]["email"] == "doc@a.test" and f["hospital_id"] == "hosp-a"
    page = client(world, "doc@a.test").get("/api/patients/0/followup").json()
    assert page["active"]["id"] == f["id"] and page["can_request"] is False


def test_only_one_request_per_patient_at_a_time(world):
    assert ask(world).status_code == 201
    r = ask(world, "nurse@a.test", urgency="routine")
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "already_requested"
    assert r.json()["detail"]["followup"]["requested_by"]["email"] == "doc@a.test"
    assert world["db"][COLLECTION].count_documents({}) == 1


def test_the_nurse_adds_a_note_to_the_doctors_request(world):
    fid = ask(world).json()["id"]
    r = client(world, "nurse@a.test").post(f"/api/followups/{fid}/note", json={"text": "patient prefers mornings"})
    assert r.status_code == 200
    assert [n["text"] for n in r.json()["notes"]] == ["patient prefers mornings"]


@pytest.mark.parametrize("email", ["cm@a.test", "admin@a.test", "claims@acme.test", "me@patient.test"])
def test_only_doctors_and_nurses_request(world, email):
    assert ask(world, email, idx=0 if email != "me@patient.test" else 3).status_code == 403


def test_nobody_requests_for_someone_elses_patient(world):
    assert ask(world, idx=2).status_code == 404          # the nurse's patient, not the doctor's
    assert ask(world, idx=4).status_code == 404          # another hospital's
    assert world["db"][COLLECTION].count_documents({}) == 0


def test_bad_requests_are_refused(world):
    assert ask(world, urgency="tomorrow").status_code == 422
    assert ask(world, note="x" * 501).status_code == 422


# ---------------------------------------------------------- case manager side
def test_the_case_manager_sees_it_without_clinical_details(world):
    ask(world)
    body = client(world, "cm@a.test").get("/api/followups").json()
    assert body["counts"] == {"open": 1, "in_progress": 0, "overdue": 0}
    item = body["items"][0]
    assert item["patient_idx"] == 0 and item["doctors"] and item["nurses"]
    assert not access.CLINICAL_FIELDS & item.keys()


def test_other_hospitals_never_see_it(world):
    fid = ask(world).json()["id"]
    b = client(world, "admin@b.test")
    assert b.get("/api/followups").json()["items"] == []
    assert b.post(f"/api/followups/{fid}/take").status_code == 404
    assert b.post(f"/api/followups/{fid}/done", json={"outcome": "booked"}).status_code == 404
    assert client(world, "floating@x.test").get("/api/followups").json()["items"] == []


@pytest.mark.parametrize("email", ["doc@a.test", "nurse@a.test", "claims@acme.test", "me@patient.test"])
def test_only_case_managers_and_admins_handle(world, email):
    fid = ask(world).json()["id"]
    c = client(world, email)
    assert c.get("/api/followups").status_code == 403
    assert c.post(f"/api/followups/{fid}/take").status_code in (403, 404)
    assert c.post(f"/api/followups/{fid}/done", json={"outcome": "booked"}).status_code in (403, 404)


def test_two_case_managers_cannot_take_the_same_one(world, second_cm):
    fid = ask(world).json()["id"]
    r = client(world, "cm@a.test").post(f"/api/followups/{fid}/take")
    assert r.status_code == 200 and r.json()["status"] == "in_progress"
    again = second_cm.post(f"/api/followups/{fid}/take")
    assert again.status_code == 409 and again.json()["detail"]["code"] == "already_taken"
    assert second_cm.post(f"/api/followups/{fid}/done", json={"outcome": "booked"}).status_code == 409
    mine = {i["id"]: i["mine"] for i in second_cm.get("/api/followups").json()["items"]}
    assert mine == {fid: False}


def test_the_admin_can_step_in(world):
    fid = ask(world).json()["id"]
    client(world, "cm@a.test").post(f"/api/followups/{fid}/take")
    r = client(world, "admin@a.test").post(f"/api/followups/{fid}/done",
                                           json={"outcome": "unreachable", "note": "no answer twice"})
    assert r.status_code == 200
    assert (r.json()["outcome_label"], r.json()["done_by"]["email"]) == ("Couldn't reach patient", "admin@a.test")


def test_done_frees_the_patient_for_a_new_request(world):
    fid = ask(world).json()["id"]
    cm = client(world, "cm@a.test")
    r = cm.post(f"/api/followups/{fid}/done", json={"outcome": "booked", "note": "Tue clinic"})
    assert r.status_code == 200 and r.json()["taken_by"]["email"] == "cm@a.test"   # taken on the way
    assert cm.post(f"/api/followups/{fid}/done", json={"outcome": "booked"}).status_code == 409
    assert cm.get("/api/followups").json()["items"] == []
    assert [i["id"] for i in cm.get("/api/followups", params={"status": "done"}).json()["items"]] == [fid]
    page = client(world, "doc@a.test").get("/api/patients/0/followup").json()
    assert page["active"] is None and page["recent"]["outcome"] == "booked" and page["can_request"]
    assert ask(world, "nurse@a.test", urgency="routine").status_code == 201


def test_every_change_is_recorded(world):
    fid = ask(world).json()["id"]
    client(world, "nurse@a.test").post(f"/api/followups/{fid}/note", json={"text": "hi"})
    client(world, "cm@a.test").post(f"/api/followups/{fid}/take")
    client(world, "cm@a.test").post(f"/api/followups/{fid}/done", json={"outcome": "not_needed"})
    doc = world["db"][COLLECTION].find_one({})
    assert [(h["action"], h["by"]["email"]) for h in doc["history"]] == [
        ("requested", "doc@a.test"), ("note", "nurse@a.test"), ("taken", "cm@a.test"), ("done", "cm@a.test")]


def test_urgent_first_then_oldest(world):
    ask(world, idx=1, urgency="routine")
    ask(world, "nurse@a.test", idx=2, urgency="routine")
    ask(world, idx=0, urgency="urgent")
    order = [i["patient_idx"] for i in client(world, "cm@a.test").get("/api/followups").json()["items"]]
    assert order == [0, 1, 2]


def test_old_open_requests_are_flagged_overdue(world):
    from datetime import datetime, timedelta, timezone
    ask(world)
    world["db"][COLLECTION].update_one({}, {"$set": {"requested_at": datetime.now(timezone.utc) - timedelta(days=8)}})
    body = client(world, "cm@a.test").get("/api/followups").json()
    assert body["items"][0]["overdue"] and body["counts"]["overdue"] == 1


# ------------------------------------------------------------------ the bell
def test_new_requests_go_to_the_case_manager_not_the_admin(world):
    ask(world)
    cm = bell(world, "cm@a.test")
    assert cm["enabled"] and [i["kinds"] for i in cm["items"]] == [["followup_new"]]
    admin = bell(world, "admin@a.test")
    assert admin["enabled"] is False and admin["items"] == []


def test_a_hospital_without_a_case_manager_notifies_its_admin(world):
    world["db"].patient_access.update_one({"patient_idx": 5}, {"$set": {"assigned_doctor_id": "x"}})
    world["db"][COLLECTION].insert_one({                       # a request in hospital B
        "patient_idx": 5, "active_patient": 5, "hospital_id": "hosp-b", "status": "open",
        "urgency": "routine", "note": "", "notes": [], "history": [],
        "requested_by": {"id": "x", "name": "Dr B", "email": "", "role": "doctor"},
        "requested_at": __import__("datetime").datetime.now(__import__("datetime").timezone.utc)})
    b = bell(world, "admin@b.test")
    assert b["enabled"] and [i["patient_idx"] for i in b["items"]] == [5]
    world["db"].patient_access.update_one({"patient_idx": 5}, {"$unset": {"assigned_doctor_id": ""}})


def test_taking_a_request_clears_it_from_every_case_managers_bell(world, second_cm):
    fid = ask(world).json()["id"]
    assert second_cm.get("/api/notifications").json()["total"] == 1
    client(world, "cm@a.test").post(f"/api/followups/{fid}/take")
    assert bell(world, "cm@a.test")["total"] == 0
    assert second_cm.get("/api/notifications").json()["total"] == 0


def test_the_doctor_and_nurse_hear_the_outcome_until_they_open_the_patient(world):
    world["db"].patients.update_one({"patient_idx": 1}, {"$set": {"dropout_prob": 0.2}})
    client(world, "doc@a.test").get("/api/patients/1")
    client(world, "nurse@a.test").get("/api/patients/1")
    fid = ask(world, idx=1, urgency="routine").json()["id"]
    client(world, "cm@a.test").post(f"/api/followups/{fid}/done", json={"outcome": "booked"})
    for email in ("doc@a.test", "nurse@a.test"):
        item = next(i for i in bell(world, email)["items"] if i["patient_idx"] == 1)
        assert item["kinds"] == ["followup_done"] and item["followup"]["outcome_label"] == "Booked"
    client(world, "doc@a.test").get("/api/patients/1")
    assert 1 not in {i["patient_idx"] for i in bell(world, "doc@a.test")["items"]}
    assert 1 in {i["patient_idx"] for i in bell(world, "nurse@a.test")["items"]}


def test_insurers_patients_and_superadmin_have_no_bell(world):
    ask(world)
    for email in ("claims@acme.test", "me@patient.test", "ops@team.test"):
        body = bell(world, email)
        assert body["enabled"] is False and body["items"] == []
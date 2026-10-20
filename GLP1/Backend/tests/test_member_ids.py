"""Insurance member IDs (core/member_ids.py). Same accounts as test_isolation.py:
patients 0 (hospital A) and 4 (hospital B) are insured by "acme";
claims@acme.test works for acme."""
import pytest

from core.config import settings
from core.member_ids import MBI, normalise
from tests.test_isolation import client, world  # noqa: F401


@pytest.fixture
def insurers(world):
    ins = world["store"][settings.shared_identity_db_name].insurers
    ins.delete_many({})
    ins.insert_many([{"_id": "acme", "name": "Acme Health"},
                     {"_id": "medicare", "name": "Medicare", "member_id_format": "mbi"}])
    acc = world["db"].patient_access
    yield
    acc.update_many({}, {"$unset": {"insurer_member_id": "", "insurer_member_id_history": ""}})
    acc.update_one({"patient_idx": 2}, {"$unset": {"insurer_id": ""}})
    ins.delete_many({})


def test_the_mbi_format():
    assert MBI.match("1EG4TE5MK73") and normalise("1eg4-te5-mk73") == "1EG4TE5MK73"
    for bad in ("0EG4TE5MK73", "1SG4TE5MK73", "1EG4TE5MK7", "1EG4TE5MKZ3"):
        assert not MBI.match(bad), bad


def test_billing_staff_record_a_member_id_and_it_is_unique_per_insurer(world, insurers):
    cm = client(world, "cm@a.test")
    body = cm.post("/api/patients/0/insurance", json={"member_id": "acm 0042-77"}).json()
    assert body["member_id"] == "ACM004277" and body["insurer"]["name"] == "Acme Health"
    assert body["can_edit"] is False                     # on file now: it can't be changed
    # The same ID for another acme member is refused (patient 4 is hospital B's, so ask as superadmin).
    ops = client(world, "ops@team.test")
    assert ops.post("/api/patients/4/insurance", json={"member_id": "ACM004277"}).status_code == 409
    # ...but another insurer may use the same number.
    world["db"].patient_access.update_one({"patient_idx": 2}, {"$set": {"insurer_id": "medicare"}})
    assert cm.post("/api/patients/2/insurance", json={"member_id": "ACM004277"}).status_code == 422   # not an MBI
    assert cm.post("/api/patients/2/insurance", json={"member_id": "1EG4-TE5-MK73"}).status_code == 200
    # No insurer on file: nothing to attach it to.
    assert cm.post("/api/patients/1/insurance", json={"member_id": "ABC123"}).status_code == 409
    # Once on file it can't be changed or cleared, by anyone.
    for who in (cm, ops):
        assert who.post("/api/patients/0/insurance", json={"member_id": "ACM999"}).status_code == 409
        assert who.post("/api/patients/0/insurance", json={"member_id": ""}).status_code == 409
    assert world["db"].patient_access.find_one({"patient_idx": 0})["insurer_member_id"] == "ACM004277"
    assert len(world["db"].patient_access.find_one({"patient_idx": 0})["insurer_member_id_history"]) == 1


def test_who_sees_member_ids(world, insurers):
    client(world, "cm@a.test").post("/api/patients/0/insurance", json={"member_id": "ACM10001"})
    assert client(world, "claims@acme.test").get("/api/patients/0/insurance").json()["member_id"] == "ACM10001"
    assert client(world, "claims@acme.test").post("/api/patients/0/insurance", json={"member_id": "X"}).status_code == 403
    for email in ("doc@a.test", "nurse@a.test", "rx@bay.test", "carer@home.test"):
        assert client(world, email).get("/api/patients/0/insurance").status_code in (403, 404), email
    assert client(world, "admin@b.test").get("/api/patients/0/insurance").status_code == 404
    assert client(world, "me@patient.test").get("/api/patients/3/insurance").json()["insurer"] is None


def test_finding_a_patient_by_member_id(world, insurers):
    client(world, "ops@team.test").post("/api/patients/4/insurance", json={"member_id": "ACM555"})
    assert client(world, "claims@acme.test").get("/api/member-lookup?member_id=acm-555").json()["matches"] == [
        {"patient_idx": 4, "insurer": "Acme Health"}]
    assert client(world, "admin@b.test").get("/api/member-lookup?member_id=ACM555").json()["matches"][0]["patient_idx"] == 4
    assert client(world, "admin@a.test").get("/api/member-lookup?member_id=ACM555").status_code == 404   # not their hospital
    assert client(world, "doc@a.test").get("/api/member-lookup?member_id=ACM555").status_code == 403
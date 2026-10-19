"""Insurance member IDs in Readmissions (api/member_ids.py) - GLP-1's twin.
Same accounts as test_isolation.py: A1 (hospital A) and B1 (hospital B) are
insured by "acme"; claims@acme.test works for acme."""
from api import auth
from tests.units.test_isolation import A1, A2, A3, B1, as_user, main, world  # noqa: F401


def insurers(world):
    world["db"].client[auth.IDENTITY_DB]["insurers"].insert_many([
        {"_id": "acme", "name": "Acme Health"}, {"_id": "medicare", "name": "Medicare", "member_id_format": "mbi"}])


def test_member_ids_are_unique_per_insurer_and_medicare_is_checked(main, world):
    insurers(world)
    cm = as_user(main, world, "cm@a.test")
    assert cm.post(f"/api/patients/{A1}/insurance", json={"member_id": "acm-0042 77"}).json()["member_id"] == "ACM004277"
    assert as_user(main, world, "ops@team.test").post(f"/api/patients/{B1}/insurance",
                                                      json={"member_id": "ACM004277"}).status_code == 409
    world["db"]["care_actions"].update_one({"patient_id": A3}, {"$set": {"insurer_id": "medicare"}})
    assert cm.post(f"/api/patients/{A3}/insurance", json={"member_id": "ACM004277"}).status_code == 422
    assert cm.post(f"/api/patients/{A3}/insurance", json={"member_id": "1EG4-TE5-MK73"}).status_code == 200
    assert cm.post(f"/api/patients/{A2}/insurance", json={"member_id": "ABC123"}).status_code == 409   # no insurer


def test_who_sees_and_finds_member_ids(main, world):
    insurers(world)
    as_user(main, world, "ops@team.test").post(f"/api/patients/{B1}/insurance", json={"member_id": "ACM555"})
    claims = as_user(main, world, "claims@acme.test")
    assert claims.get(f"/api/patients/{B1}/insurance").json()["member_id"] == "ACM555"
    assert claims.get("/api/member-lookup?member_id=acm-555").json()["matches"] == [
        {"patient_id": B1, "insurer": "Acme Health"}]
    assert claims.post(f"/api/patients/{B1}/insurance", json={"member_id": "X12"}).status_code == 403
    assert as_user(main, world, "admin@a.test").get("/api/member-lookup?member_id=ACM555").status_code == 404
    for email in ("doc.a@a.test", "nurse@a.test", "rx@bay.test", "carer@home.test"):
        assert as_user(main, world, email).get(f"/api/patients/{A1}/insurance").status_code in (403, 404), email
        assert as_user(main, world, email).get("/api/member-lookup?member_id=ACM555").status_code == 403, email
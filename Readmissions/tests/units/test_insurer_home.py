"""An insurer's home page in Readmissions (api/insurer.py). claims@acme.test's
members are A1 (hospital A) and B1 (hospital B)."""
from api import auth
from tests.units.test_isolation import A1, B1, as_user, main, world  # noqa: F401


def test_the_insurer_home_is_its_own_members_by_hospital(main, world):
    world["db"].client[auth.IDENTITY_DB]["hospitals"].insert_many(
        [{"_id": "hosp-a", "name": "Hospital A"}, {"_id": "hosp-b", "name": "Hospital B"}])
    world["db"]["care_actions"].update_one({"patient_id": B1}, {"$set": {"insurer_member_id": "ACM777"}})
    body = as_user(main, world, "claims@acme.test").get("/api/insurer/home").json()
    assert body["stats"]["members"] == 2 and body["stats"]["hospitals"] == 2
    assert {h["hospital"] for h in body["by_hospital"]} == {"Hospital A", "Hospital B"}
    assert body["stats"]["expected_cost"] > 0 and body["stats"]["without_member_id"] == 1
    assert {m["patient_id"] for m in body["high_risk_members"]} <= {A1, B1}
    assert "primary_diagnosis" not in str(body) and "driver" not in str(body)
    for email in ("admin@a.test", "doc.a@a.test", "cm@a.test", "rx@bay.test", "me@patient.test"):
        assert as_user(main, world, email).get("/api/insurer/home").status_code == 403, email
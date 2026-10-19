"""An insurer's home page (core/insurer.py). claims@acme.test's members are
patients 0 (hospital A) and 4 (hospital B)."""
from core.config import settings
from tests.test_isolation import client, world  # noqa: F401


def test_the_insurer_home_is_its_own_members_by_hospital(world):
    ident = world["store"][settings.shared_identity_db_name]
    ident.hospitals.delete_many({})
    ident.hospitals.insert_many([{"_id": "hosp-a", "name": "Hospital A"}, {"_id": "hosp-b", "name": "Hospital B"}])
    world["db"].patient_access.update_one({"patient_idx": 4}, {"$set": {"insurer_member_id": "ACM777"}})
    before = world["db"].patients.find_one({"patient_idx": 4})["dropout_prob"]
    world["db"].patients.update_one({"patient_idx": 4}, {"$set": {"dropout_prob": 0.91}})
    body = client(world, "claims@acme.test").get("/api/insurer/home").json()
    assert body["stats"]["members"] == 2 and body["stats"]["hospitals"] == 2
    assert {h["hospital"] for h in body["by_hospital"]} == {"Hospital A", "Hospital B"}
    assert body["stats"]["annual_spend"] > 0 and body["stats"]["spend_at_risk"] >= 0
    top = body["high_risk_members"][0]
    assert top["patient_idx"] == 4 and top["member_id"] == "ACM777" and top["hospital"] == "Hospital B"
    assert {m["patient_idx"] for m in body["high_risk_members"]} <= {0, 4}           # nobody else's
    assert not {"driver_1", "BMXBMI", "LBXGH"} & set().union(*body["high_risk_members"])
    for email in ("admin@a.test", "doc@a.test", "cm@a.test", "rx@bay.test", "me@patient.test"):
        assert client(world, email).get("/api/insurer/home").status_code == 403, email
    world["db"].patient_access.update_one({"patient_idx": 4}, {"$unset": {"insurer_member_id": ""}})
    world["db"].patients.update_one({"patient_idx": 4}, {"$set": {"dropout_prob": before}})
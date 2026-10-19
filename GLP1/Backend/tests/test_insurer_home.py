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


def test_an_insurer_sees_the_hospital_and_case_manager_not_the_clinical_staff(world):
    c = client(world, "claims@acme.test")
    s = c.get("/api/patients/0/summary").json()
    assert s["viewer"] == "insurer" and "hospital" in s and "member_id" in s
    assert not {"doctors", "doctor", "nurses", "pharmacy"} & set(s)
    rows = c.get("/api/patients").json()["patients"]
    assert rows and all(not {"doctors", "nurses"} & set(r) for r in rows)
    # A hospital admin still sees the whole care team.
    assert "doctors" in client(world, "admin@a.test").get("/api/patients/0/summary").json()


def test_an_insurer_gives_a_payer_reason(world):
    c = client(world, "claims@acme.test")
    keys = [r["key"] for r in c.get("/api/patients/4/summary").json()["reasons"]]
    assert keys == ["claim_review", "prior_auth_review", "care_management", "quality_review"]
    assert c.post("/api/patients/4/open", json={"reason": "audit"}).status_code == 422     # the hospital's list
    assert c.post("/api/patients/4/open", json={"reason": "claim_review"}).status_code == 200
    detail = c.get("/api/patients/4").json()["patient"]
    assert detail["viewer"] == "insurer" and "doctors" not in detail
    admin = client(world, "admin@a.test")
    assert [r["key"] for r in admin.get("/api/patients/0/summary").json()["reasons"]][0] == "care_coordination"
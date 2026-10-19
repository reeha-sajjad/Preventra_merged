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


def test_an_insurer_sees_the_hospital_and_who_to_contact_not_the_clinical_staff(main, world):
    world["db"].client[auth.IDENTITY_DB]["hospitals"].insert_one({"_id": "hosp-a", "name": "Hospital A"})
    c = as_user(main, world, "claims@acme.test")
    s = c.get(f"/api/patients/{A1}/summary").json()
    assert s["viewer"] == "insurer" and s["hospital"] == "Hospital A" and "member_id" in s
    assert [p["email"] for p in s["case_managers"]] == ["cm@a.test"]
    assert not {"doctor", "nurses"} & set(s)
    rows = c.get("/api/patients").json()["data"]
    assert rows and all(not {"doctor", "nurses"} & set(r) for r in rows)
    assert "doctor" in as_user(main, world, "admin@a.test").get(f"/api/patients/{A1}/summary").json()


def test_an_insurer_gives_a_payer_reason(main, world):
    c = as_user(main, world, "claims@acme.test")
    keys = [r["key"] for r in c.get(f"/api/patients/{B1}/summary").json()["reasons"]]
    assert keys == ["claim_review", "prior_auth_review", "care_management", "quality_review"]
    assert c.post(f"/api/patients/{B1}/open", json={"reason": "audit"}).status_code == 422
    assert c.post(f"/api/patients/{B1}/open", json={"reason": "claim_review"}).status_code == 200
    log = world["db"].client[auth.IDENTITY_DB]["access_log"].find_one({"patient_id": B1})
    assert log is None or log["reason_label"] == "Claim review"
    admin = as_user(main, world, "admin@a.test")
    assert [r["key"] for r in admin.get(f"/api/patients/{A1}/summary").json()["reasons"]][0] == "care_coordination"
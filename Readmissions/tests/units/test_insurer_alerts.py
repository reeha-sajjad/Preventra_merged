"""Insurer alerts in Readmissions (api/insurer_alerts.py; GLP-1's twin).
claims@acme.test's members are A1 (hospital A, Low) and B1 (hospital B, High)."""
from api import insurer_alerts
from tests.units.test_isolation import A1, A2, B1, LATEST, as_user, main, world  # noqa: F401


def bell(main, world, email="claims@acme.test"):
    r = as_user(main, world, email).get("/api/followups/bell")
    assert r.status_code == 200
    return r.json()["items"]


def test_first_look_is_a_baseline_then_a_member_turning_high_rings(main, world):
    assert bell(main, world) == []                                         # baseline: B1 already High, quiet
    world["db"]["patient_worklist"].update_one({"patient_id": A1, "batch_date": LATEST},
                                               {"$set": {"current_band": "High", "current_score": 77}})
    items = bell(main, world)
    assert [(i["kind"], i["patient_id"], i["previous_band"]) for i in items] == [("turned_high", A1, "Low")]
    # Opening the member (with a reason) clears it.
    c = as_user(main, world, "claims@acme.test")
    reason = "billing"
    assert c.post(f"/api/patients/{A1}/open", json={"reason": reason}).status_code == 200
    assert c.get(f"/api/patients/{A1}").status_code == 200
    assert bell(main, world) == []


def test_mark_all_as_read_is_a_new_baseline(main, world):
    bell(main, world)
    world["db"]["patient_worklist"].update_one({"patient_id": A1, "batch_date": LATEST},
                                               {"$set": {"current_band": "High"}})
    assert len(bell(main, world)) == 1
    assert as_user(main, world, "claims@acme.test").post("/api/insurer/alerts/seen", json={}).json()["seen"] == 2
    assert bell(main, world) == []
    assert as_user(main, world, "cm@a.test").post("/api/insurer/alerts/seen", json={}).status_code == 403


def test_a_hospital_flags_a_member_and_the_insurer_replies(main, world):
    cm = as_user(main, world, "cm@a.test")
    assert cm.post(f"/api/patients/{A1}/insurer-flags", json={"kind": "nonsense", "note": "hello there"}).status_code == 422
    assert cm.post(f"/api/patients/{A1}/insurer-flags", json={"kind": "prior_auth", "note": ""}).status_code == 422
    sent = cm.post(f"/api/patients/{A1}/insurer-flags", json={"kind": "prior_auth", "note": "PA for home oxygen"}).json()
    assert sent["status"] == "sent" and sent["insurer_id"] == "acme"
    items = bell(main, world)
    assert items[0]["kind"] == "insurer_flag" and items[0]["kind_label"] == "Prior authorisation"
    claims = as_user(main, world, "claims@acme.test")
    assert claims.get("/api/insurer/flags").json()["flags"][0]["note"] == "PA for home oxygen"
    r = claims.post(f"/api/insurer/flags/{sent['id']}/reply", json={"status": "approved", "note": "Approved for 90 days"})
    assert r.json()["status_label"] == "Approved"
    assert cm.get(f"/api/patients/{A1}/insurer-flags").json()["flags"][0]["reply_note"] == "Approved for 90 days"
    assert all(i["kind"] != "insurer_flag" for i in bell(main, world))       # answered: off the bell
    assert claims.get("/api/insurer/flags").json()["flags"] == []            # approved is closed


def test_flags_go_only_where_they_belong(main, world):
    cm = as_user(main, world, "cm@a.test")
    assert cm.post(f"/api/patients/{A2}/insurer-flags", json={"kind": "billing_query", "note": "Which plan?"}).status_code == 409
    for email in ("doc.a@a.test", "nurse@a.test", "rx@bay.test", "me@patient.test", "claims@acme.test", "admin@b.test"):
        assert as_user(main, world, email).post(f"/api/patients/{A1}/insurer-flags",
                                                json={"kind": "billing_query", "note": "Which plan?"}).status_code in (403, 404), email
    sent = cm.post(f"/api/patients/{A1}/insurer-flags", json={"kind": "billing_query", "note": "Which plan?"}).json()
    assert as_user(main, world, "admin@b.test").get(f"/api/patients/{A1}/insurer-flags").status_code == 404
    assert as_user(main, world, "admin@a.test").post(f"/api/insurer/flags/{sent['id']}/reply",
                                                     json={"status": "approved"}).status_code == 403
    assert as_user(main, world, "doc.a@a.test").get(f"/api/patients/{A1}/insurer-flags").status_code == 403
    assert world["db"][insurer_alerts.FLAGS].count_documents({}) == 1
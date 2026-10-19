"""Insurer alerts (core/insurer_alerts.py). claims@acme.test's members are
patients 0 (hospital A) and 4 (hospital B)."""
import pytest

from core import insurer_alerts
from tests.test_isolation import client, world  # noqa: F401


@pytest.fixture
def clean(world):
    before = {d["patient_idx"]: d["dropout_prob"] for d in world["db"].patients.find(
        {"patient_idx": {"$in": [0, 4]}}, {"patient_idx": 1, "dropout_prob": 1})}
    for c in (insurer_alerts.WATCH, insurer_alerts.FLAGS):
        world["db"][c].delete_many({})
    yield
    for c in (insurer_alerts.WATCH, insurer_alerts.FLAGS):
        world["db"][c].delete_many({})
    for idx, p in before.items():
        world["db"].patients.update_one({"patient_idx": idx}, {"$set": {"dropout_prob": p}})


def bell(world, email="claims@acme.test"):
    return client(world, email).get("/api/notifications").json()


def test_first_look_is_a_baseline_then_a_member_turning_critical_rings(world, clean):
    world["db"].patients.update_one({"patient_idx": 0}, {"$set": {"dropout_prob": 0.40}})
    first = bell(world)
    assert first["enabled"] and first["total"] == 0                       # baseline, nothing rings
    world["db"].patients.update_one({"patient_idx": 0}, {"$set": {"dropout_prob": 0.88}})
    item = bell(world)["items"][0]
    assert item["kinds"] == ["turned_critical"] and item["patient_idx"] == 0 and item["previous_prob"] == 0.4
    # Opening the member clears it.
    client(world, "claims@acme.test").get("/api/patients/0/summary")
    assert bell(world)["total"] == 0


def test_a_hospital_flags_a_member_and_the_insurer_replies(world, clean):
    cm = client(world, "cm@a.test")
    assert cm.post("/api/patients/0/insurer-flags", json={"kind": "nonsense", "note": "hello there"}).status_code == 422
    assert cm.post("/api/patients/0/insurer-flags", json={"kind": "prior_auth", "note": ""}).status_code == 422
    sent = cm.post("/api/patients/0/insurer-flags", json={"kind": "prior_auth", "note": "PA for dose increase"}).json()
    assert sent["status"] == "sent" and sent["insurer_id"] == "acme"
    bell(world)                                                            # (the baseline)
    items = bell(world)["items"]
    assert items[0]["kinds"] == ["insurer_flag"] and items[0]["kind_label"] == "Prior authorisation"
    claims = client(world, "claims@acme.test")
    assert claims.get("/api/insurer/flags").json()["flags"][0]["note"] == "PA for dose increase"
    r = claims.post(f"/api/insurer/flags/{sent['id']}/reply", json={"status": "needs_info", "note": "Send the A1c"})
    assert r.json()["status_label"] == "Needs more information"
    assert cm.get("/api/patients/0/insurer-flags").json()["flags"][0]["reply_note"] == "Send the A1c"
    assert all("insurer_flag" not in i["kinds"] for i in bell(world)["items"])   # answered: off the bell


def test_flags_go_only_where_they_belong(world, clean):
    cm = client(world, "cm@a.test")
    assert cm.post("/api/patients/2/insurer-flags", json={"kind": "billing_query", "note": "Which plan?"}).status_code == 409
    for email in ("doc@a.test", "nurse@a.test", "rx@bay.test", "me@patient.test", "claims@acme.test"):
        assert client(world, email).post("/api/patients/0/insurer-flags",
                                         json={"kind": "billing_query", "note": "Which plan?"}).status_code in (403, 404)
    sent = cm.post("/api/patients/0/insurer-flags", json={"kind": "billing_query", "note": "Which plan?"}).json()
    assert client(world, "admin@b.test").get("/api/patients/0/insurer-flags").status_code == 404
    assert client(world, "admin@a.test").post(f"/api/insurer/flags/{sent['id']}/reply",
                                              json={"status": "approved"}).status_code == 403
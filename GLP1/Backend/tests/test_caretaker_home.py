"""A caretaker's people and bell, and the case manager's "no dose logged"
alert (core/caretaker_home.py). carer@home.test looks after patient 3
(hospital A); cm@a.test is hospital A's case manager."""
from datetime import datetime, timedelta, timezone

import pytest

from core import caretaker_home, followups
from tests.test_isolation import client, world  # noqa: F401


@pytest.fixture
def clean(world):
    for c in ("dose_log", followups.COLLECTION, caretaker_home.SEEN, "zoe_plans"):
        world["db"][c].delete_many({})
    yield
    for c in ("dose_log", followups.COLLECTION, caretaker_home.SEEN, "zoe_plans"):
        world["db"][c].delete_many({})


def dose(world, days_ago):
    world["db"].dose_log.insert_one({"patient_idx": 3, "given_at": datetime.now(timezone.utc) - timedelta(days=days_ago),
                                     "logged_by": {"id": "x", "name": "Zara", "role": "caretaker"}})


def carer(world):
    return client(world, "carer@home.test")


def test_my_people_shows_risk_medicine_and_doses(world, clean):
    body = carer(world).get("/api/my-people").json()
    assert [p["patient_idx"] for p in body["people"]] == [3]
    p = body["people"][0]
    assert p["band"] in ("Low", "Medium", "High", "Critical") and 0 <= p["dropout_prob"] <= 1
    assert p["last_dose"] is None
    # The patient's own page shows the caretaker the number too; the patient never.
    assert "risk" in carer(world).get("/api/my-home").json()
    assert "risk" not in client(world, "me@patient.test").get("/api/my-home").json()
    for email in ("doc@a.test", "cm@a.test", "me@patient.test", "rx@bay.test"):
        assert client(world, email).get("/api/my-people").status_code == 403, email


def test_the_caretaker_bell(world, clean):
    world["db"].patients.update_one({"patient_idx": 3}, {"$set": {"assigned_molecule": "TIRZEPATIDE"}})
    dose(world, 8)                                                          # weekly: overdue
    now = datetime.now(timezone.utc)
    world["db"][followups.COLLECTION].insert_one({
        "patient_idx": 3, "active_patient": 3, "status": "open", "urgency": "routine", "hospital_id": "hosp-a",
        "requested_by": {"id": "r", "name": "Bay counter", "role": "pharmacy"}, "requested_at": now,
        "pharmacy_reports": [{"problem": "missed_pickup", "at": now, "pharmacy": "Bay Pharmacy"},
                             {"problem": "cost", "at": now, "pharmacy": "Bay Pharmacy"}]})
    body = carer(world).get("/api/notifications").json()
    assert body["enabled"] and body["items"][0]["kinds"] == ["refill_problem", "dose_due"]
    assert body["items"][0]["refill_label"] == "Missed a refill pickup"
    assert "cost" not in str(body) and "afford" not in str(body)          # the patient's own business
    # Mark all as read: the refill report goes, the overdue dose stays while it is true.
    assert carer(world).post("/api/my-people/seen").status_code == 200
    assert carer(world).get("/api/notifications").json()["items"][0]["kinds"] == ["dose_due"]
    world["db"].dose_log.delete_many({}); dose(world, 0)
    assert carer(world).get("/api/notifications").json()["total"] == 0


def test_the_case_manager_hears_when_doses_stop_being_logged(world, clean):
    cm = client(world, "cm@a.test")
    dose(world, 3)
    assert all("dose_missed" not in i["kinds"] for i in cm.get("/api/notifications").json()["items"])
    world["db"].dose_log.delete_many({}); dose(world, 10)
    item = next(i for i in cm.get("/api/notifications").json()["items"] if "dose_missed" in i["kinds"])
    assert item["patient_idx"] == 3 and item["days_since_dose"] == 10 and item["last_dose_by"] == "Zara"
    world["db"].dose_log.delete_many({}); dose(world, 45)                   # long gone: not news
    assert all("dose_missed" not in i["kinds"] for i in cm.get("/api/notifications").json()["items"])
    # Another hospital's case manager never hears of it.
    assert all(i["patient_idx"] != 3 for i in client(world, "admin@b.test").get("/api/notifications").json()["items"])
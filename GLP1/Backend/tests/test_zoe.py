"""Zoe, the patient's reminder companion (core/zoe.py). me@patient.test is
linked to patient 3."""
from datetime import date, timedelta

import pytest

from core import zoe
from tests.test_isolation import client, world  # noqa: F401


def me(world):
    return client(world, "me@patient.test")


@pytest.fixture
def clean(world):
    world["db"][zoe.COLLECTION].delete_many({})
    world["db"]["medication_changes"].delete_many({"patient_idx": 3})
    yield
    world["db"][zoe.COLLECTION].delete_many({})
    world["db"]["medication_changes"].delete_many({"patient_idx": 3})


def drug_of_3(world):
    return str(world["db"].patients.find_one({"patient_idx": 3})["assigned_molecule"]).upper()


def test_the_schedule_follows_the_drug(world, clean):
    body = me(world).get("/api/zoe").json()
    freq = zoe.FREQUENCY[drug_of_3(world)]
    assert body["dose"] is None and body["medicine"]["needs_form"] == (freq == "either")
    c = me(world)
    if freq == "weekly":
        assert c.post("/api/zoe/dose", json={"time": "09:00"}).status_code == 422         # needs a day
        r = c.post("/api/zoe/dose", json={"time": "09:00", "day": 6})
        assert r.status_code == 200 and r.json()["dose"]["day"] == 6
    elif freq == "daily":
        r = c.post("/api/zoe/dose", json={"time": "08:30", "day": 2})
        assert r.status_code == 200 and r.json()["dose"]["day"] is None              # daily: no day
    else:
        assert c.post("/api/zoe/dose", json={"time": "09:00", "day": 0}).status_code == 422  # say which form
        r = c.post("/api/zoe/dose", json={"time": "09:00", "form": "daily"})
        assert r.status_code == 200 and r.json()["medicine"]["frequency"] == "daily"
    assert c.post("/api/zoe/dose", json={"time": "25:00", "day": 1, "form": "weekly"}).status_code == 422
    assert c.delete("/api/zoe/dose").json()["dose"] is None


def test_a_stopped_medicine_has_nothing_to_remind(world, clean):
    world["db"]["medication_changes"].insert_one({
        "patient_idx": 3, "from_drug": drug_of_3(world), "to_drug": "STOPPED", "reason": "side_effects",
        "effective_date": "2026-06-01", "recorded_by": {"name": "Dr"}, "recorded_at": __import__("datetime").datetime.now()})
    assert me(world).post("/api/zoe/dose", json={"time": "09:00", "day": 1, "form": "weekly"}).status_code == 409


def test_appointments_are_kept_soonest_first_and_old_ones_drop_off(world, clean):
    c = me(world)
    soon, later = (date.today() + timedelta(days=d) for d in (2, 20))
    assert c.post("/api/zoe/appointments", json={"kind": "blood_test", "date": later.isoformat()}).status_code == 201
    body = c.post("/api/zoe/appointments", json={"kind": "doctor", "date": soon.isoformat(), "time": "14:30",
                                                 "note": "Bring my pen"}).json()
    assert [a["kind_label"] for a in body["appointments"]] == ["Doctor visit", "Blood test"]
    # An appointment that has passed is not shown, and is dropped on the next save.
    world["db"][zoe.COLLECTION].update_one({}, {"$push": {"appointments": {
        "id": "old", "kind": "nurse", "date": (date.today() - timedelta(days=5)).isoformat()}}})
    assert "old" not in {a["id"] for a in c.get("/api/zoe").json()["appointments"]}
    aid = body["appointments"][0]["id"]
    assert len(c.delete(f"/api/zoe/appointments/{aid}").json()["appointments"]) == 1
    assert c.delete(f"/api/zoe/appointments/{aid}").status_code == 404


def test_appointments_need_a_real_kind_and_date(world, clean):
    c = me(world)
    tomorrow = (date.today() + timedelta(days=1)).isoformat()
    assert c.post("/api/zoe/appointments", json={"kind": "spa", "date": tomorrow}).status_code == 422
    assert c.post("/api/zoe/appointments", json={"kind": "doctor", "date": "soon"}).status_code == 422
    assert c.post("/api/zoe/appointments", json={"kind": "doctor", "date": "2020-01-01"}).status_code == 422
    assert c.post("/api/zoe/appointments", json={"kind": "doctor", "date": tomorrow, "note": "x" * 81}).status_code == 422


def test_zoe_is_the_patients_alone(world, clean):
    me(world).post("/api/zoe/appointments", json={"kind": "doctor", "date": (date.today() + timedelta(days=3)).isoformat()})
    for email in ("doc@a.test", "nurse@a.test", "cm@a.test", "admin@a.test", "ops@team.test", "rx@bay.test"):
        c = client(world, email)
        assert c.get("/api/zoe").status_code == 403, email
        assert c.post("/api/zoe/appointments", json={"kind": "doctor", "date": "2030-01-01"}).status_code == 403
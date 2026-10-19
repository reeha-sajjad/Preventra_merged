"""Shared Zoe and doses given (core/zoe.py, core/doses.py). carer@home.test is
linked to patient 3 (me@patient.test); doc@a.test is not on 3's care team, but
cm@a.test (hospital A) is."""
from datetime import date, timedelta

import pytest

from core import doses, zoe
from tests.test_isolation import client, world  # noqa: F401


@pytest.fixture
def clean(world):
    for c in (zoe.COLLECTION, doses.COLLECTION):
        world["db"][c].delete_many({})
    yield
    for c in (zoe.COLLECTION, doses.COLLECTION):
        world["db"][c].delete_many({})


def test_the_patient_and_their_caretaker_share_one_plan(world, clean):
    me, carer = client(world, "me@patient.test"), client(world, "carer@home.test")
    day = (date.today() + timedelta(days=4)).isoformat()
    assert carer.post("/api/zoe/appointments", json={"kind": "doctor", "date": day}).status_code == 201
    assert [a["date"] for a in me.get("/api/zoe").json()["appointments"]] == [day]
    aid = me.get("/api/zoe").json()["appointments"][0]["id"]
    assert me.delete(f"/api/zoe/appointments/{aid}").status_code == 200
    assert carer.get("/api/zoe").json()["appointments"] == []
    # Nobody else's plan: a caretaker naming a patient who didn't link them.
    assert carer.get("/api/zoe?patient=2").status_code == 404


def test_a_caretaker_ticks_doses_and_the_care_team_sees_them(world, clean):
    carer, me = client(world, "carer@home.test"), client(world, "me@patient.test")
    body = carer.post("/api/zoe/doses", json={"note": "Left thigh"}).json()
    assert len(body["entries"]) == 1 and body["entries"][0]["can_undo"] and body["last_4_weeks"] == 1
    assert carer.post("/api/zoe/doses", json={}).status_code == 409              # a double tap is one dose
    # The patient sees it but can't tick.
    seen = me.get("/api/zoe/doses").json()
    assert seen["entries"][0]["logged_by"] and seen["can_tick"] is False
    assert me.post("/api/zoe/doses", json={}).status_code == 403
    # The care team sees it on the patient's page (case manager: hospital A).
    staff = client(world, "cm@a.test").get("/api/patients/3/doses").json()
    assert staff["last_4_weeks"] == 1 and staff["entries"][0]["note"] == "Left thigh"
    # A hospital admin needs a reason first; someone outside the hospital gets nothing.
    assert client(world, "admin@a.test").get("/api/patients/3/doses").status_code == 403
    assert client(world, "admin@b.test").get("/api/patients/3/doses").status_code == 404
    assert carer.get("/api/patients/3/doses").status_code == 403                 # caretakers: summary only
    # Undo a mis-tap: only the person who recorded it.
    did = body["entries"][0]["id"]
    assert me.delete(f"/api/zoe/doses/{did}").status_code == 403
    assert carer.delete(f"/api/zoe/doses/{did}").json()["entries"] == []
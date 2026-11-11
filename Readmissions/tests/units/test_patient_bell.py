"""A patient's bell (api/patient_bell.py): today's medicines and a booked
appointment - never the care team's score alerts (main.get_alerts).
me@patient.test is linked to A4; carer@home.test looks after A4."""
from datetime import timedelta

from ingestion.scheduler import today
from scripts import load_patient_medicines
from tests.units.test_isolation import A4, as_user, main, world  # noqa: F401
from tests.units.test_meds_appointments import _booked


def score_alert(world):
    world["db"]["alerts"].insert_one({"alert_id": "AL-1", "patient_id": A4, "acknowledged": False,
                                      "old_score": 40, "old_band": "Medium", "new_score": 68, "new_band": "High"})


def test_patients_and_carers_get_no_score_alerts(main, world):
    score_alert(world)
    for email in ("me@patient.test", "carer@home.test"):
        assert as_user(main, world, email).get("/api/alerts").json() == [], email
    staff = as_user(main, world, "cm@a.test").get("/api/alerts").json()
    assert any(a["alert_id"] == "AL-1" for a in staff)                               # staff still do


def test_the_patient_is_reminded_of_todays_medicines_once_a_day(main, world):
    me = as_user(main, world, "me@patient.test")
    assert me.get("/api/followups/bell").json()["items"] == []                       # no list, nothing
    load_patient_medicines.apply(world["db"], load_patient_medicines.plan(world["db"], demo=True))
    items = me.get("/api/followups/bell").json()["items"]
    assert [i["kind"] for i in items] == ["meds_today"]
    assert items[0]["medicines"][0]["drug"] and "score" not in str(items).lower()
    assert me.post("/api/my-people/seen").status_code == 200                         # mark all as read
    assert me.get("/api/followups/bell").json()["items"] == []                       # until tomorrow


def test_the_patient_hears_when_an_appointment_is_booked(main, world):
    day = (today() + timedelta(days=9)).isoformat()
    _booked(main, world, {"date": day, "time": "10:30", "place": "Cardiology clinic", "with": "Dr Turner"})
    me = as_user(main, world, "me@patient.test")
    item = next(i for i in me.get("/api/followups/bell").json()["items"] if i["kind"] == "booked")
    assert item["appointment"]["date"] == day and item["appointment"]["place"] == "Cardiology clinic"
    me.post("/api/my-people/seen")
    assert all(i["kind"] != "booked" for i in me.get("/api/followups/bell").json()["items"])


def test_a_behavioural_health_booking_is_not_announced(main, world):
    day = (today() + timedelta(days=9)).isoformat()
    _booked(main, world, {"date": day, "time": "10:30", "place": "Clinic"}, reason="behavioural_health")
    assert as_user(main, world, "me@patient.test").get("/api/followups/bell").json()["items"] == []
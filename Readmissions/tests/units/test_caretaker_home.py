"""A caretaker's people and bell, and the case manager's "no medicines logged"
alert (api/caretaker_home.py; GLP-1's twin). carer@home.test looks after A4."""
from datetime import datetime, timedelta, timezone

from api import followups
from tests.units.test_isolation import A4, as_user, main, world  # noqa: F401


def tick(world, days_ago):
    world["db"]["medicines_given"].insert_one({
        "patient_id": A4, "given_at": datetime.now(timezone.utc) - timedelta(days=days_ago),
        "logged_by": {"id": "x", "name": "Zara", "role": "caretaker"}})


def test_my_people_shows_risk_and_medicines(main, world):
    carer = as_user(main, world, "carer@home.test")
    p = carer.get("/api/my-people").json()["people"][0]
    assert p["patient_id"] == A4 and p["band"] in ("High", "Medium", "Low") and p["risk_score"] > 0
    assert p["last_given"] is None and p["alerts"] == []
    assert "risk" in carer.get("/api/my-home").json()
    assert "risk" not in as_user(main, world, "me@patient.test").get("/api/my-home").json()
    for email in ("doc.a@a.test", "cm@a.test", "me@patient.test", "rx@bay.test"):
        assert as_user(main, world, email).get("/api/my-people").status_code == 403, email


def test_the_caretaker_bell(main, world):
    carer = as_user(main, world, "carer@home.test")
    tick(world, 1)                                                           # not logged today
    now = datetime.now(timezone.utc)
    world["db"][followups.COLLECTION].insert_one({
        "patient_id": A4, "active_patient": A4, "status": "open", "urgency": "routine", "hospital_id": "hosp-a",
        "requested_by": {"id": "r", "name": "Bay counter", "role": "pharmacy"}, "requested_at": now,
        "pharmacy_reports": [{"problem": "out_of_stock", "at": now}, {"problem": "side_effects", "at": now}]})
    items = carer.get("/api/followups/bell").json()["items"]
    assert [i["kind"] for i in items] == ["refill_problem", "dose_due"]
    assert items[0]["label"] == "Out of stock / supply problem" and "side" not in str(items).lower()
    assert carer.post("/api/my-people/seen").status_code == 200
    assert [i["kind"] for i in carer.get("/api/followups/bell").json()["items"]] == ["dose_due"]
    tick(world, 0)
    assert carer.get("/api/followups/bell").json()["items"] == []


def test_the_case_manager_hears_when_medicines_stop_being_logged(main, world):
    cm = as_user(main, world, "cm@a.test")
    tick(world, 1)
    assert all(i["kind"] != "dose_missed" for i in cm.get("/api/followups/bell").json()["items"])
    world["db"]["medicines_given"].delete_many({}); tick(world, 5)
    item = next(i for i in cm.get("/api/followups/bell").json()["items"] if i["kind"] == "dose_missed")
    assert item["patient_id"] == A4 and item["days"] == 5 and item["by"] == "Zara"
    assert all(i.get("patient_id") != A4 for i in as_user(main, world, "admin@b.test").get("/api/followups/bell").json()["items"])
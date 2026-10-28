"""The weekly trend says whether the patient is still being monitored, so the
chart can pulse its latest point only while that is true."""
from datetime import date, timedelta

from tests.units.test_isolation import A1, as_user, main, world  # noqa: F401

TODAY = date(2026, 6, 30)


def series(world, discharged):
    db = world["db"]
    db["weekly_monitoring"].delete_many({"patient_id": A1})
    db["weekly_monitoring"].insert_many([
        {"patient_id": A1, "week_number": 0, "risk_score": 40, "risk_band": "High",
         "week_date": discharged.isoformat(), "days_after_discharge": 0},
        {"patient_id": A1, "week_number": 1, "risk_score": 35, "risk_band": "Medium",
         "week_date": (discharged + timedelta(days=7)).isoformat(), "days_after_discharge": 7}])


def test_live_is_active_inside_the_window(main, world, monkeypatch):  # noqa: F811
    monkeypatch.setattr(main.hospital_clock, "today", lambda: TODAY)
    series(world, TODAY - timedelta(days=10))
    live = as_user(main, world, "doc.a@a.test").get(f"/api/patients/{A1}/trend").json()["live"]
    assert live == {"as_of": TODAY.isoformat(), "days_since_discharge": 10, "active": True}


def test_an_old_series_is_not_live(main, world, monkeypatch):  # noqa: F811
    monkeypatch.setattr(main.hospital_clock, "today", lambda: TODAY)
    series(world, TODAY - timedelta(days=90))
    live = as_user(main, world, "doc.a@a.test").get(f"/api/patients/{A1}/trend").json()["live"]
    assert live["active"] is False and live["days_since_discharge"] == 90


def test_an_admissions_series_is_never_live(main, world):  # noqa: F811
    world["db"]["weekly_monitoring"].delete_many({"patient_id": A1})
    body = as_user(main, world, "doc.a@a.test").get(f"/api/patients/{A1}/trend").json()
    assert body["series_kind"] == "admissions" and body["live"] == {"active": False}

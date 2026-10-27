"""The rising-risk watchlist (api/risk_watch.py): a doctor is told about their
own patients whose latest week got worse, and about nobody else's.

Same accounts and ownership as test_isolation.py: Dr A has A1 and A2, the nurse
A1-A3, hospital B is someone else's.
"""
import pytest

from tests.units.test_isolation import A1, A2, A3, A4, B1, as_user, main, world  # noqa: F401

SERIES = {
    A1: [(40, "High"), (47, "High")],                    # +7, ends High      -> urgent
    A2: [(50, "High"), (48, "High", ["spo2"])],          # red flag, fell 2   -> urgent
    A3: [(18, "Low"), (22, "Medium")],                   # band up only       -> rising
    A4: [(50, "High"), (51, "High")],                    # +1                 -> nothing
    B1: [(30, "Medium"), (60, "High")],                  # another hospital's
}


@pytest.fixture
def weeks(world, monkeypatch):
    # Inside these patients' monitoring window: the watchlist only reads
    # patients still being monitored.
    import datetime as dt
    from ingestion import scheduler
    monkeypatch.setattr(scheduler, "today", lambda: dt.date(2026, 4, 20))
    db = world["db"]
    db["weekly_monitoring"].delete_many({})
    for pid, series in SERIES.items():
        for wk, point in enumerate(series):
            db["weekly_monitoring"].insert_one({
                "patient_id": pid, "week_number": wk, "risk_score": point[0],
                "risk_band": point[1], "red_flags": point[2] if len(point) > 2 else [],
                "week_date": f"2026-04-{10 + wk:02d}"})
    return db


def watch(main, world, email):
    return as_user(main, world, email).get("/api/rising-risk").json()


def ids(body):
    return [p["patient_id"] for p in body["patients"]]


def test_a_doctor_sees_only_their_own_patients_most_serious_first(main, world, weeks):
    body = watch(main, world, "doc.a@a.test")
    assert ids(body) == [A2, A1]                         # the red flag before the bigger rise
    a2, a1 = body["patients"]
    assert (a1["severity"], a1["delta"], a1["previous_score"], a1["risk_score"]) == \
        ("urgent", 7.0, 40.0, 47.0)
    assert a2["severity"] == "urgent" and a2["red_flags"] == ["oxygen saturation below 90%"]
    assert a2["reasons"] == ["Red flag: oxygen saturation below 90%"]
    assert a1["primary_diagnosis"] == "Heart failure"
    assert (body["total"], body["urgent"], body["unseen"]) == (2, 2, 2)


def test_a_nurse_sees_their_patients_including_a_move_up_a_band(main, world, weeks):
    body = watch(main, world, "nurse@a.test")
    assert ids(body) == [A2, A1, A3]
    a3 = body["patients"][2]
    assert a3["severity"] == "rising" and a3["reasons"] == ["Moved from Low to Medium"]


def test_the_superadmin_sees_every_hospital(main, world, weeks):
    assert set(ids(watch(main, world, "ops@team.test"))) == {A1, A2, A3, B1}


@pytest.mark.parametrize("email", ["admin@a.test", "claims@acme.test", "me@patient.test"])
def test_roles_outside_the_care_team_are_refused(main, world, weeks, email):
    assert as_user(main, world, email).get("/api/rising-risk").status_code == 403


def test_seen_takes_a_patient_off_the_bell_until_a_newer_week_rises(main, world, weeks):
    doctor = as_user(main, world, "doc.a@a.test")
    assert doctor.post(f"/api/rising-risk/{A1}/seen", json={"week_number": 1}).status_code == 200
    body = watch(main, world, "doc.a@a.test")
    assert body["unseen"] == 1
    assert {p["patient_id"]: p["seen"] for p in body["patients"]} == {A1: True, A2: False}
    # someone else's "seen" is theirs alone
    assert watch(main, world, "nurse@a.test")["unseen"] == 3
    weeks["weekly_monitoring"].insert_one({"patient_id": A1, "week_number": 2, "risk_score": 60,
                                           "risk_band": "High", "red_flags": []})
    body = watch(main, world, "doc.a@a.test")
    assert {p["patient_id"]: p["seen"] for p in body["patients"]}[A1] is False


def test_silence_after_a_medium_week_is_on_the_list_but_not_after_a_low_one():
    from api.risk_watch import assess
    medium = [{"week": 0, "score": 25.0, "band": "Medium"}, {"week": 1, "score": 34.2, "band": "Medium"},
              {"week": 2, "score": 34.2, "band": "Medium", "observed": False}]
    entry = assess(medium)
    assert entry["severity"] == "rising" and entry["delta"] == 0.0
    assert entry["reasons"] == ["No readings for 1 week since week 1 while Medium - contact the patient"]
    low = [{**w, "band": "Low"} for w in medium]
    assert assess(low) is None


def test_a_patient_outside_the_scope_cannot_be_marked(main, world, weeks):
    r = as_user(main, world, "doc.a@a.test").post(f"/api/rising-risk/{B1}/seen",
                                                  json={"week_number": 1})
    assert r.status_code == 404
    assert as_user(main, world, "doc.a@a.test").post(
        f"/api/rising-risk/{A3}/seen", json={"week_number": 1}).status_code == 404


def test_check_ins_that_stop_are_on_the_list_by_the_calendar():
    from api.risk_watch import assess
    weeks = [{"week": 0, "score": 41.0, "band": "High", "date": "2026-10-01"},
             {"week": 1, "score": 44.0, "band": "High", "date": "2026-10-08", "days": 7}]
    assert assess(weeks, "2026-10-15") is None                  # 7 days, +3: nothing to say
    entry = assess(weeks, "2026-10-21")
    assert entry["severity"] == "urgent"
    assert entry["reasons"] == ["No readings for 13 days since week 1 while High - contact the patient"]


def test_a_high_discharge_never_heard_from_is_on_the_list(main, world, monkeypatch):
    from ingestion import scheduler
    import datetime as dt
    db = world["db"]
    db["weekly_monitoring"].delete_many({})
    db["weekly_monitoring"].insert_one({"patient_id": A1, "week_number": 0, "risk_score": 52,
                                        "risk_band": "High", "red_flags": [], "week_date": "2026-10-01"})
    monkeypatch.setattr(scheduler, "today", lambda: dt.date(2026, 10, 12))
    body = watch(main, world, "doc.a@a.test")
    assert ids(body) == [A1]
    assert body["patients"][0]["reasons"] == [
        "No readings for 11 days since discharge while High - contact the patient"]
    monkeypatch.setattr(scheduler, "today", lambda: dt.date(2026, 10, 8))     # one week: not yet
    assert ids(watch(main, world, "doc.a@a.test")) == []



def test_a_patient_whose_monitoring_ended_long_ago_is_not_news(main, world, weeks, monkeypatch):
    """Years-old weeks (the historical demo data) stay off the bell."""
    import datetime as dt
    from ingestion import scheduler
    monkeypatch.setattr(scheduler, "today", lambda: dt.date(2026, 10, 27))
    assert watch(main, world, "doc.a@a.test")["patients"] == []

"""Outcomes (api/outcomes.py): a readmission recorded on the patient's page,
and the page that counts what was done and what happened.

Same accounts as test_isolation.py. Hospital A's patients are A1-A4, B's B1-B2.
The worklist discharge date is 10 April 2026, so its 30 days end on 10 May.
"""
import datetime as dt

import pytest

from api import outcomes
from ingestion import scheduler
from tests.units.test_isolation import A1, A2, A3, A4, B1, B2, as_user, main, world  # noqa: F401


@pytest.fixture
def clock(monkeypatch):
    def set_to(day):
        monkeypatch.setattr(scheduler, "today", lambda: day)
    set_to(dt.date(2026, 6, 1))
    return set_to


# ------------------------------------------------------------ one patient
def test_a_readmission_is_recorded_and_can_be_taken_back(main, world, clock):
    cm = as_user(main, world, "cm@a.test")
    out = cm.get(f"/api/patients/{A1}/outcome").json()
    assert out["recorded"] is None and out["window_ends"] == "2026-05-10" and out["window_closed"]
    r = cm.post(f"/api/patients/{A1}/outcome", json={"readmitted": True, "readmit_date": "2026-04-22",
                                                     "note": "Admitted elsewhere, per daughter"})
    assert r.status_code == 200, r.text
    rec = r.json()["recorded"]
    assert rec["readmitted"] and rec["readmit_date"] == "2026-04-22" and rec["by"] and rec["note"]
    stored = world["db"][outcomes.COLLECTION].find_one({"patient_id": A1})
    assert stored["discharge_band"] == "Low" and stored["discharge_score"] == 40.0
    assert cm.delete(f"/api/patients/{A1}/outcome").json()["recorded"] is None


@pytest.mark.parametrize("body, status", [
    ({"readmitted": True}, 422),                                       # no date
    ({"readmitted": True, "readmit_date": "2026-04-10"}, 422),         # the discharge day itself
    ({"readmitted": True, "readmit_date": "2026-05-11"}, 422),         # day 31
    ({"readmitted": True, "readmit_date": "2026-05-10"}, 200),         # day 30
])
def test_a_readmission_must_fall_inside_the_30_days(main, world, clock, body, status):
    assert as_user(main, world, "cm@a.test").post(f"/api/patients/{A1}/outcome", json=body).status_code == status


def test_not_readmitted_waits_for_the_30_days(main, world, clock):
    cm = as_user(main, world, "cm@a.test")
    clock(dt.date(2026, 5, 1))
    assert cm.post(f"/api/patients/{A1}/outcome", json={"readmitted": False}).status_code == 409
    assert cm.post(f"/api/patients/{A1}/outcome", json={"readmitted": True,
                                                        "readmit_date": "2026-05-03"}).status_code == 422  # future
    clock(dt.date(2026, 5, 11))
    assert cm.post(f"/api/patients/{A1}/outcome", json={"readmitted": False}).json()["recorded"]["readmitted"] is False


@pytest.mark.parametrize("email, status", [("me@patient.test", 403), ("claims@acme.test", 403),
                                           ("admin@b.test", 404), ("rx@bay.test", 404)])
def test_who_records_an_outcome(main, world, clock, email, status):
    pid = A4 if email == "me@patient.test" else A1
    r = as_user(main, world, email).post(f"/api/patients/{pid}/outcome",
                                         json={"readmitted": True, "readmit_date": "2026-04-20"})
    assert r.status_code == status
    assert world["db"][outcomes.COLLECTION].count_documents({}) == 0


# ------------------------------------------------------------ the page
def test_the_page_is_for_the_roles_that_run_a_hospital(main, world, clock):
    for email in ("doc.a@a.test", "nurse@a.test", "claims@acme.test", "me@patient.test", "rx@bay.test",
                  "carer@home.test"):
        assert as_user(main, world, email).get("/api/outcomes").status_code == 403, email
    for email in ("ops@team.test", "admin@a.test", "cm@a.test"):
        assert as_user(main, world, email).get("/api/outcomes").status_code == 200, email
    assert as_user(main, world, "cm@a.test").get("/api/outcomes?days=12").status_code == 422


def test_observed_against_expected_counts_only_the_callers_patients(main, world, clock):
    db = world["db"]
    ops = as_user(main, world, "ops@team.test")
    # A1 left at 40% (Low) and came back; A2 at 50% (Low) did not; B1 is another hospital's.
    for pid, back in ((A1, True), (A2, False), (B1, True)):
        body = {"readmitted": back, **({"readmit_date": "2026-04-20"} if back else {})}
        assert ops.post(f"/api/patients/{pid}/outcome", json=body).status_code == 200
    page = as_user(main, world, "admin@a.test").get("/api/outcomes").json()
    overall = page["readmissions"]["overall"]
    assert (overall["known"], overall["readmitted"], overall["observed_rate"]) == (2, 1, 50.0)
    assert overall["expected_rate"] == 45.0 and overall["ratio"] == 1.11
    low = next(b for b in page["readmissions"]["bands"] if b["band"] == "Low")
    assert low["known"] == 2
    assert page["readmissions"]["past_30_days"] == 4                      # A1-A4, past 10 May
    assert page["readmissions"]["sources"] == {"records": 0, "care_team": 2}
    assert ops.get("/api/outcomes").json()["readmissions"]["overall"]["known"] == 3


def test_the_hospitals_own_records_count_and_the_care_team_wins(main, world, clock):
    db = world["db"]
    db["ingest_stays"].insert_one({"hospital_id": "hosp-a", "encounter_key": "E-9", "patient_id": A3,
                                   "readmitted_30d": False,
                                   "encounter": {"discharge_datetime": dt.datetime(2026, 4, 10, 12)}})
    db["risk_registry"].insert_one({"hospital_id": "hosp-a", "encounter_key": "E-9", "patient_id": A3,
                                    "risk_score": 62.0, "risk_band": "High", "scored_at": "2026-04-10"})
    cm = as_user(main, world, "cm@a.test")
    assert cm.get(f"/api/patients/{A3}/outcome").json()["from_records"]["readmitted"] is False
    page = cm.get("/api/outcomes").json()["readmissions"]
    high = next(b for b in page["bands"] if b["band"] == "High")
    assert (high["known"], high["readmitted"], high["expected_rate"]) == (1, 0, 62.0)
    # The care team knows of a readmission elsewhere: theirs counts.
    cm.post(f"/api/patients/{A3}/outcome", json={"readmitted": True, "readmit_date": "2026-04-30"})
    page = cm.get("/api/outcomes").json()["readmissions"]
    assert page["overall"]["known"] == 1 and page["overall"]["readmitted"] == 1
    assert page["sources"] == {"records": 0, "care_team": 1}


def test_acting_on_alerts_and_follow_ups(main, world, clock):
    db = world["db"]
    now = dt.datetime.now()
    stamp = lambda hours: (now - dt.timedelta(hours=hours)).strftime("%Y-%m-%d %H:%M:%S")  # noqa: E731
    db["clinical_alerts"].delete_many({})
    db["clinical_alerts"].insert_many([
        {"alert_id": "x1", "patient_id": A1, "status": "responded", "created_at": stamp(30),
         "acknowledged_at": stamp(28), "response": {"responded_at": stamp(24)}},
        {"alert_id": "x2", "patient_id": A2, "status": "acknowledged", "created_at": stamp(10),
         "acknowledged_at": stamp(6)},
        {"alert_id": "x3", "patient_id": A3, "status": "unrouted", "created_at": stamp(5)},
        {"alert_id": "x4", "patient_id": B1, "status": "pending", "created_at": stamp(5)},
        {"alert_id": "x5", "patient_id": A1, "status": "pending", "created_at": stamp(24 * 40)},  # too old
    ])
    utc = dt.datetime.now(dt.timezone.utc)
    db["followups"].insert_many([
        {"patient_id": A1, "status": "done", "outcome": "booked", "requested_at": utc - dt.timedelta(days=3),
         "done_at": utc - dt.timedelta(days=1)},
        {"patient_id": A2, "status": "done", "outcome": "unreachable", "requested_at": utc - dt.timedelta(days=2),
         "done_at": utc - dt.timedelta(days=1)},
        {"patient_id": A3, "status": "open", "requested_at": utc - dt.timedelta(days=9)},
        {"patient_id": B2, "status": "open", "requested_at": utc - dt.timedelta(days=9)},
    ])
    page = as_user(main, world, "cm@a.test").get("/api/outcomes?days=30").json()
    alerts = page["alerts"]
    assert (alerts["raised"], alerts["acknowledged"], alerts["answered"], alerts["unrouted"]) == (3, 2, 1, 1)
    assert alerts["median_hours_to_acknowledge"] == 3.0 and alerts["median_hours_to_answer"] == 6.0
    f = page["followups"]
    assert (f["requested"], f["done"], f["overdue"]) == (3, 2, 1)
    assert {o["key"]: o["count"] for o in f["outcomes"]} == {"booked": 1, "unreachable": 1, "not_needed": 0}
    assert f["median_days_to_done"] == 1.5


def test_staying_in_touch_and_discharge_planning(main, world, clock):
    db = world["db"]
    clock(dt.date(2026, 10, 15))
    db["weekly_monitoring"].delete_many({})
    db["weekly_monitoring"].insert_many([
        {"patient_id": A1, "week_number": 0, "risk_score": 50, "risk_band": "High", "week_date": "2026-10-01"},
        {"patient_id": A2, "week_number": 0, "risk_score": 10, "risk_band": "Low", "week_date": "2026-10-01"},
        {"patient_id": A3, "week_number": 0, "risk_score": 50, "risk_band": "High", "week_date": "2026-08-01"},
    ])
    db["checkins"].insert_many([
        {"patient_id": A2, "by_role": "patient", "at": dt.datetime.now(dt.timezone.utc), "red_flags": []},
        {"patient_id": A2, "by_role": "caretaker", "at": dt.datetime.now(dt.timezone.utc), "red_flags": ["spo2"]},
        {"patient_id": B1, "by_role": "patient", "at": dt.datetime.now(dt.timezone.utc), "red_flags": []},
    ])
    db["patient_worklist"].update_many({"patient_id": {"$in": [A1, A2]}}, {"$set": {"discharge_date": "2026-10-01"}})
    nurse = as_user(main, world, "nurse@a.test")
    nurse.post(f"/api/patients/{A1}/discharge-plan/checklist", json={"item": "followup_booked", "done": True})
    nurse.post(f"/api/patients/{A1}/discharge-plan/social", json={"answers": {"transport": "no"}})
    page = as_user(main, world, "cm@a.test").get("/api/outcomes?days=30").json()
    c = page["contact"]
    assert (c["monitored_now"], c["overdue_now"]) == (2, 1)               # A1 quiet 14 days at High
    assert (c["checkins"], c["checkin_patients"], c["by_caretaker"], c["with_red_flags"]) == (2, 1, 1, 1)
    d = page["discharge_planning"]
    assert (d["discharges"], d["with_plan"], d["followup_booked"], d["complete"], d["with_needs"]) == (2, 1, 1, 0, 1)
    assert d["needs"] == [{"key": "transport", "label": "Needs help with transport", "count": 1}]


def test_a_readmission_known_before_day_30_counts_in_the_denominator(main, world, clock):
    """Known outcomes can never outnumber the discharges they are out of."""
    clock(dt.date(2026, 4, 25))                                   # day 15 of A1-A4's 30
    cm = as_user(main, world, "cm@a.test")
    assert cm.post(f"/api/patients/{A1}/outcome",
                   json={"readmitted": True, "readmit_date": "2026-04-20"}).status_code == 200
    r = cm.get("/api/outcomes").json()["readmissions"]
    assert r["overall"]["known"] == 1 and r["past_30_days"] == 1


def test_a_sweep_over_historical_data_is_not_counted_as_alerts(main, world, clock):
    """Alerts about discharges years old (a forecast run over old data) were
    never live, so 'acting on alerts' leaves them out."""
    db = world["db"]
    db["clinical_alerts"].delete_many({})
    now = dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    recent = (dt.date.today() - dt.timedelta(days=10)).isoformat()
    db["clinical_alerts"].insert_many([
        {"alert_id": "live", "patient_id": A1, "status": "pending", "created_at": now,
         "patient": {"discharge_date": recent}},
        {"alert_id": "old", "patient_id": A2, "status": "pending", "created_at": now,
         "patient": {"discharge_date": "2012-05-01"}},
        {"alert_id": "undated", "patient_id": A3, "status": "pending", "created_at": now},
    ])
    assert as_user(main, world, "cm@a.test").get("/api/outcomes").json()["alerts"]["raised"] == 2

"""
Unit tests for the clinician loop: registry, routing, and the alert lifecycle.

mongomock throughout — no Atlas, no network. The things worth pinning here are
the ones that would be invisible until they hurt: an alert duplicated on every
nightly sweep, an alert routed to nobody and silently dropped, or a doctor's
reply that never reaches the coordinator who has to act on it.
"""

import sys
from pathlib import Path

import mongomock
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from api import doctor_service as ds


@pytest.fixture
def db():
    return mongomock.MongoClient()["neuroshield"]


@pytest.fixture
def cardiologist(db):
    return ds.register_doctor(db, "Dr. Hart", "Cardiology", "hart@example.org",
                              ["heart_failure"])


@pytest.fixture
def generalist(db):
    return ds.register_doctor(db, "Dr. Gen", "Internal Medicine", "gen@example.org",
                              ["general"])


def a_forecast(severity="critical", group="heart_failure", week=3, score=55.0):
    return {
        "severity": severity, "clinical_group": group, "as_of_week": week,
        "current_score": score, "current_band": "High", "projected_score": score + 8,
        "triggers": [{"code": "hf_fluid_overload", "severity": "critical",
                      "stream": "vitals", "title": "Weight gain",
                      "detail": "+2.4 kg.", "rationale": "Fluid, not tissue."}],
    }


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

def test_registering_returns_an_id(db):
    doctor = ds.register_doctor(db, "Dr. Hart", "Cardiology", "hart@example.org")
    assert doctor["doctor_id"].startswith("DR-")
    assert doctor["active"] is True


def test_re_registering_the_same_email_updates_rather_than_duplicates(db):
    first = ds.register_doctor(db, "Dr. Hart", "Cardiology", "hart@example.org")
    second = ds.register_doctor(db, "Dr. H. Hart", "Nephrology", "HART@example.org")
    assert first["doctor_id"] == second["doctor_id"]
    assert second["specialty"] == "Nephrology"
    assert len(ds.list_doctors(db)) == 1


@pytest.mark.parametrize("args", [
    ("", "Cardiology", "hart@example.org"),
    ("Dr. Hart", "Cardiology", "not-an-email"),
    ("Dr. Hart", "", "hart@example.org"),
])
def test_registration_rejects_incomplete_entries(db, args):
    with pytest.raises(ValueError):
        ds.register_doctor(db, *args)


def test_unknown_clinical_groups_are_dropped(db):
    doctor = ds.register_doctor(db, "Dr. Hart", "Cardiology", "hart@example.org",
                                ["heart_failure", "astrology"])
    assert doctor["clinical_groups"] == ["heart_failure"]


def test_deactivating_keeps_the_record(db, cardiologist):
    assert ds.deactivate_doctor(db, cardiologist["doctor_id"]) is True
    assert ds.list_doctors(db) == []
    assert ds.get_doctor(db, cardiologist["doctor_id"])["active"] is False


# ---------------------------------------------------------------------------
# Routing
# ---------------------------------------------------------------------------

def test_claimed_condition_wins(db, cardiologist, generalist):
    doctor, reason = ds.route(db, "heart_failure")
    assert doctor["doctor_id"] == cardiologist["doctor_id"]
    assert "heart failure" in reason


def test_specialty_mapping_is_the_fallback(db):
    onc = ds.register_doctor(db, "Dr. Onc", "Oncology", "onc@example.org")
    doctor, reason = ds.route(db, "oncology")
    assert doctor["doctor_id"] == onc["doctor_id"]
    assert "on call" in reason


def test_internal_medicine_catches_what_nobody_covers(db, generalist):
    doctor, reason = ds.route(db, "neuro_stroke")
    assert doctor["doctor_id"] == generalist["doctor_id"]
    assert "internal medicine" in reason


def test_an_explicit_assignment_beats_the_condition(db, cardiologist, generalist):
    doctor, reason = ds.route(db, "heart_failure",
                              assigned_doctor_id=generalist["doctor_id"])
    assert doctor["doctor_id"] == generalist["doctor_id"]
    assert reason == "assigned to this patient"


def test_an_inactive_doctor_is_not_routed_to(db, cardiologist):
    ds.deactivate_doctor(db, cardiologist["doctor_id"])
    doctor, _ = ds.route(db, "heart_failure")
    assert doctor is None


def test_routing_to_nobody_is_reported_not_hidden(db):
    doctor, reason = ds.route(db, "oncology")
    assert doctor is None
    assert "Oncology" in reason


def test_a_stale_assignment_falls_through_to_the_condition(db, cardiologist):
    """A doctor who has left must not black-hole their patients' alerts."""
    doctor, reason = ds.route(db, "heart_failure", assigned_doctor_id="DR-gone-999")
    assert doctor["doctor_id"] == cardiologist["doctor_id"]
    assert reason != "assigned to this patient"


# ---------------------------------------------------------------------------
# Alert creation and deduplication
# ---------------------------------------------------------------------------

def test_moderate_forecasts_do_not_reach_an_inbox(db, cardiologist):
    alert, created = ds.create_alert(db, "P1", a_forecast(severity="moderate"))
    assert alert is None and created is False
    assert db[ds.CLINICAL_ALERTS].count_documents({}) == 0


def test_creating_an_alert_notifies_the_doctor(db, cardiologist):
    alert, created = ds.create_alert(db, "P1", a_forecast())
    assert created is True
    assert alert["doctor_id"] == cardiologist["doctor_id"]
    assert alert["status"] == "pending"
    assert db[ds.NOTIFICATIONS].count_documents(
        {"doctor_id": cardiologist["doctor_id"], "read": False}) == 1


def test_the_same_week_never_creates_a_second_alert(db, cardiologist):
    ds.create_alert(db, "P1", a_forecast())
    alert, created = ds.create_alert(db, "P1", a_forecast())
    assert created is False
    assert db[ds.CLINICAL_ALERTS].count_documents({"patient_id": "P1"}) == 1


def test_a_new_week_does_create_one(db, cardiologist):
    ds.create_alert(db, "P1", a_forecast(week=3))
    _, created = ds.create_alert(db, "P1", a_forecast(week=4))
    assert created is True
    assert db[ds.CLINICAL_ALERTS].count_documents({"patient_id": "P1"}) == 2


def test_worsening_reopens_an_acknowledged_alert(db, cardiologist):
    alert, _ = ds.create_alert(db, "P1", a_forecast(severity="high"))
    ds.acknowledge(db, alert["alert_id"], cardiologist["doctor_id"])
    updated, _ = ds.create_alert(db, "P1", a_forecast(severity="critical"))
    assert updated["status"] == "pending"
    assert updated["severity"] == "critical"


def test_an_answered_alert_is_not_dragged_back(db, cardiologist):
    alert, _ = ds.create_alert(db, "P1", a_forecast(severity="high"))
    ds.respond(db, alert["alert_id"], cardiologist["doctor_id"], "Reviewed.")
    updated, _ = ds.create_alert(db, "P1", a_forecast(severity="critical"))
    assert updated["status"] == "responded"


def test_an_unrouted_alert_is_kept(db):
    alert, created = ds.create_alert(db, "P1", a_forecast(group="oncology"))
    assert created is True
    assert alert["status"] == "unrouted"
    assert alert["doctor_id"] is None
    assert len(ds.unrouted_alerts(db)) == 1


def test_the_alert_carries_the_patient_snapshot(db, cardiologist):
    alert, _ = ds.create_alert(db, "P1", a_forecast(),
                               {"group_label": "Heart failure", "anchor_age": 78})
    assert alert["patient"]["group_label"] == "Heart failure"
    assert alert["patient"]["anchor_age"] == 78
    assert alert["patient"]["current_score"] == 55.0


# ---------------------------------------------------------------------------
# The sweep
# ---------------------------------------------------------------------------

def _seed_weeks(db, patient_ids):
    db["weekly_monitoring"].insert_many(
        [{"patient_id": pid, "week_number": 3} for pid in patient_ids])


def test_a_sweep_raises_one_alert_per_affected_patient(db, cardiologist):
    _seed_weeks(db, ["P1", "P2", "P3"])
    severities = {"P1": "critical", "P2": "moderate", "P3": "high"}
    result = ds.scan_cohort(
        db, lambda pid: (a_forecast(severity=severities[pid]), {}, None))
    assert result["scanned"] == 3
    assert result["alerts_created"] == 2
    assert result["no_alert"] == 1
    assert db[ds.CLINICAL_ALERTS].count_documents({}) == 2


def test_re_running_a_sweep_creates_nothing_new(db, cardiologist):
    _seed_weeks(db, ["P1", "P2"])
    forecast_fn = lambda pid: (a_forecast(), {}, None)
    ds.scan_cohort(db, forecast_fn)
    second = ds.scan_cohort(db, forecast_fn)
    assert second["alerts_created"] == 0
    assert second["alerts_updated"] == 2
    assert db[ds.CLINICAL_ALERTS].count_documents({}) == 2


def test_one_failing_patient_does_not_stop_the_sweep(db, cardiologist):
    _seed_weeks(db, ["P1", "P2", "P3"])

    def forecast_fn(patient_id):
        if patient_id == "P2":
            raise RuntimeError("bad week document")
        return a_forecast(), {}, None

    result = ds.scan_cohort(db, forecast_fn)
    assert result["forecast_failed"] == 1
    assert result["alerts_created"] == 2


def test_a_sweep_counts_unrouted_alerts(db):
    _seed_weeks(db, ["P1"])
    result = ds.scan_cohort(db, lambda pid: (a_forecast(group="oncology"), {}, None))
    assert result["unrouted"] == 1


def test_limit_caps_the_sweep(db, cardiologist):
    _seed_weeks(db, [f"P{i}" for i in range(10)])
    result = ds.scan_cohort(db, lambda pid: (a_forecast(), {}, None), limit=4)
    assert result["scanned"] == 4


# ---------------------------------------------------------------------------
# Acknowledge, respond, dismiss
# ---------------------------------------------------------------------------

def test_acknowledging_marks_notifications_read(db, cardiologist):
    alert, _ = ds.create_alert(db, "P1", a_forecast())
    ds.acknowledge(db, alert["alert_id"], cardiologist["doctor_id"])
    assert db[ds.NOTIFICATIONS].count_documents({"read": False}) == 0


def test_responding_records_who_and_what(db, cardiologist):
    alert, _ = ds.create_alert(db, "P1", a_forecast())
    updated = ds.respond(db, alert["alert_id"], cardiologist["doctor_id"],
                         "Increase diuretic and review in 48 hours.",
                         actions=["adjust_medication", "schedule_review"],
                         urgency="urgent")
    assert updated["status"] == "responded"
    assert updated["response"]["doctor_name"] == "Dr. Hart"
    assert updated["response"]["specialty"] == "Cardiology"
    assert updated["response"]["action_labels"] == [
        "Medication change required", "Bring forward the clinic review"]


def test_the_recommendation_reaches_the_care_team(db, cardiologist):
    """
    The reply lands on care_actions as well as the alert. The coordinator who
    telephones the patient does not work out of the doctor's inbox.
    """
    alert, _ = ds.create_alert(db, "P1", a_forecast())
    ds.respond(db, alert["alert_id"], cardiologist["doctor_id"],
               "Increase diuretic.", actions=["contact_patient"], urgency="urgent")
    care = db["care_actions"].find_one({"patient_id": "P1"})
    note = care["notes"][0]
    assert "Increase diuretic." in note["text"]
    assert "Telephone the patient" in note["text"]
    assert note["author"] == "Dr. Hart (Cardiology)"
    assert note["alert_id"] == alert["alert_id"]
    assert note["urgency"] == "urgent"


def test_an_empty_recommendation_is_refused(db, cardiologist):
    alert, _ = ds.create_alert(db, "P1", a_forecast())
    with pytest.raises(ValueError):
        ds.respond(db, alert["alert_id"], cardiologist["doctor_id"], "   ")


def test_an_invalid_urgency_is_refused(db, cardiologist):
    alert, _ = ds.create_alert(db, "P1", a_forecast())
    with pytest.raises(ValueError):
        ds.respond(db, alert["alert_id"], cardiologist["doctor_id"], "Fine.",
                   urgency="whenever")


def test_unknown_actions_are_dropped_not_stored(db, cardiologist):
    alert, _ = ds.create_alert(db, "P1", a_forecast())
    updated = ds.respond(db, alert["alert_id"], cardiologist["doctor_id"], "Fine.",
                         actions=["contact_patient", "launch_helicopter"])
    assert updated["response"]["actions"] == ["contact_patient"]


def test_dismissing_records_the_reason(db, cardiologist):
    alert, _ = ds.create_alert(db, "P1", a_forecast())
    updated = ds.dismiss(db, alert["alert_id"], cardiologist["doctor_id"],
                         "Weight gain explained by a new steroid.")
    assert updated["status"] == "dismissed"
    assert "steroid" in updated["dismissal_reason"]
    assert updated["response"] is None


def test_dismissing_leaves_no_note_on_the_patient(db, cardiologist):
    """A dismissal is not clinical advice and must not read like it."""
    alert, _ = ds.create_alert(db, "P1", a_forecast())
    ds.dismiss(db, alert["alert_id"], cardiologist["doctor_id"], "Faulty meter.")
    assert db["care_actions"].count_documents({"patient_id": "P1"}) == 0


@pytest.mark.parametrize("call", [
    lambda db, d: ds.acknowledge(db, "AL-nope", d),
    lambda db, d: ds.respond(db, "AL-nope", d, "text"),
    lambda db, d: ds.dismiss(db, "AL-nope", d),
])
def test_acting_on_a_missing_alert_raises_lookup(db, cardiologist, call):
    with pytest.raises(LookupError):
        call(db, cardiologist["doctor_id"])


# ---------------------------------------------------------------------------
# Inbox
# ---------------------------------------------------------------------------

def test_the_inbox_puts_critical_first(db, cardiologist):
    ds.create_alert(db, "P1", a_forecast(severity="high", week=1))
    ds.create_alert(db, "P2", a_forecast(severity="critical", week=1))
    inbox = ds.inbox(db, cardiologist["doctor_id"], status="open")
    assert inbox[0]["severity"] == "critical"


def test_answered_alerts_leave_the_open_inbox(db, cardiologist):
    alert, _ = ds.create_alert(db, "P1", a_forecast())
    ds.respond(db, alert["alert_id"], cardiologist["doctor_id"], "Done.")
    assert ds.inbox(db, cardiologist["doctor_id"], status="open") == []
    assert len(ds.inbox(db, cardiologist["doctor_id"])) == 1


def test_unread_counts_separate_pending_from_acknowledged(db, cardiologist):
    first, _ = ds.create_alert(db, "P1", a_forecast(week=1))
    ds.create_alert(db, "P2", a_forecast(week=1, severity="high"))
    ds.acknowledge(db, first["alert_id"], cardiologist["doctor_id"])
    counts = ds.unread_count(db, cardiologist["doctor_id"])
    assert counts["open"] == 2
    assert counts["pending"] == 1
    assert counts["critical"] == 1


def test_a_doctor_sees_only_their_own_alerts(db, cardiologist, generalist):
    ds.create_alert(db, "P1", a_forecast(group="heart_failure"))
    ds.create_alert(db, "P2", a_forecast(group="general"))
    assert len(ds.inbox(db, cardiologist["doctor_id"])) == 1
    assert len(ds.inbox(db, generalist["doctor_id"])) == 1


def test_a_critical_alert_past_the_page_limit_still_surfaces():
    """
    The inbox is paged. Sorting after truncation meant a doctor with more open
    alerts than fit on a page could never see a critical one that happened to be
    inserted late.
    """
    db = mongomock.MongoClient()["ranking"]
    doctor = ds.register_doctor(db, "Dr. Hart", "Cardiology", "hart@example.org",
                                ["heart_failure"])
    for i in range(30):
        ds.create_alert(db, f"P{i}", a_forecast(severity="high", week=i))
    ds.create_alert(db, "P-LATE", a_forecast(severity="critical", week=99))

    page = ds.inbox(db, doctor["doctor_id"], status="open", limit=5)
    assert page[0]["patient_id"] == "P-LATE"
    assert page[0]["severity"] == "critical"


def test_patient_history_is_newest_first(db, cardiologist):
    for week in (1, 2, 3):
        ds.create_alert(db, "P1", a_forecast(week=week))
    history = ds.patient_alerts(db, "P1")
    assert [a["week_number"] for a in history][0] == 3


# ---------------------------------------------------------------------------
# Inside the patient's hospital, and following the care team
# ---------------------------------------------------------------------------

def _in_hospital(db, doctor, hospital):
    db[ds.DOCTORS].update_one({"doctor_id": doctor["doctor_id"]}, {"$set": {"hospital_id": hospital}})


def test_routing_stays_inside_the_patients_hospital(db, cardiologist, generalist):
    """A heart-failure alert for one hospital's patient went to an internist
    registered with no hospital at all."""
    _in_hospital(db, cardiologist, "other")
    doctor, reason = ds.route(db, "heart_failure", hospital_id="mine")
    assert doctor is None and "no active doctor" in reason
    _in_hospital(db, generalist, "mine")
    doctor, _ = ds.route(db, "heart_failure", hospital_id="mine")
    assert doctor["name"] == "Dr. Gen"


def test_a_sweep_routes_inside_each_patients_hospital(db, cardiologist, generalist):
    _in_hospital(db, cardiologist, "other")
    _in_hospital(db, generalist, "mine")
    _seed_weeks(db, ["P1"])
    ds.scan_cohort(db, lambda pid: (a_forecast(), {"hospital_id": "mine"}, None))
    assert db[ds.CLINICAL_ALERTS].find_one({"patient_id": "P1"})["doctor_name"] == "Dr. Gen"


def test_open_alerts_follow_a_newly_assigned_doctor(db, cardiologist, generalist):
    _in_hospital(db, cardiologist, "mine")
    _in_hospital(db, generalist, "mine")
    db["care_actions"].insert_one({"patient_id": "P1", "hospital_id": "mine"})
    alert, _ = ds.create_alert(db, "P1", a_forecast(), {"hospital_id": "mine"})
    assert alert["doctor_name"] == "Dr. Hart"
    db[ds.CLINICAL_ALERTS].update_one({"alert_id": alert["alert_id"]}, {"$set": {"status": "acknowledged"}})

    assert ds.reroute_open_alerts(db, ["P1"], generalist["doctor_id"]) == 1
    moved = db[ds.CLINICAL_ALERTS].find_one({"alert_id": alert["alert_id"]})
    assert (moved["doctor_name"], moved["status"]) == ("Dr. Gen", "pending")   # the new doctor has not seen it
    assert db[ds.NOTIFICATIONS].count_documents({"doctor_id": generalist["doctor_id"]}) == 1

    # an answered alert stays with whoever answered it
    db[ds.CLINICAL_ALERTS].update_one({"alert_id": alert["alert_id"]}, {"$set": {"status": "responded"}})
    assert ds.reroute_open_alerts(db, ["P1"], cardiologist["doctor_id"]) == 0


def test_new_weeks_raise_their_alerts_for_the_patients_own_doctor(db, cardiologist, generalist):
    """api/forecasting.raise_alerts, which runs after weekly readings are scored
    and every night - the scan used to wait for a superadmin's button."""
    from api import forecasting
    _in_hospital(db, cardiologist, "mine")
    _in_hospital(db, generalist, "mine")
    db["patient_worklist"].insert_one({"patient_id": "P1", "batch_date": "2026-10-01",
                                       "clinical_groups": ["mental_health"]})
    db["care_actions"].insert_one({"patient_id": "P1", "hospital_id": "mine",
                                   "assigned_doctor_id": cardiologist["doctor_id"]})
    db["weekly_monitoring"].insert_many([
        {"patient_id": "P1", "week_number": 0, "risk_score": 40.0, "monitoring": {}},
        {"patient_id": "P1", "week_number": 1, "risk_score": 42.0, "monitoring": {"spo2": 89}}])
    done = forecasting.raise_alerts(db, ["P1"])
    assert done["alerts_created"] == 1
    alert = db[ds.CLINICAL_ALERTS].find_one({"patient_id": "P1"})
    assert (alert["week_number"], alert["severity"], alert["doctor_name"]) == (1, "critical", "Dr. Hart")

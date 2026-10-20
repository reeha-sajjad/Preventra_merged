"""Weekly monitoring cards must not contradict themselves.

Every test here is a contradiction a reader of the cards caught: marked ankle
swelling beside a stable weight, an appointment missed after it was attended,
a refill due two weeks running, a COVID-19 exposure code putting a GI bleed on
the infection plan, a red flag on a Low week, and a silent fortnight that read
as "+0.0, +0.0, +36.1 in 7 days" while the patient was not being reached.
"""
import os

import mongomock
import pytest

from models.icd_groups import monitoring_groups
from models.monitoring_rules import contact_gap, red_flags, score_week
from scripts.audit_weekly_monitoring import CHECKS, check_patient

np = pytest.importorskip("numpy")
sim = pytest.importorskip("scripts.simulate_weekly_monitoring")


# ------------------------------------------------------------ grouping
@pytest.mark.parametrize("title", [
    "Contact with and (suspected) exposure to COVID-19",
    "Carrier of other specified bacterial diseases",
    "Need for immunization against influenza",
])
def test_exposure_contact_and_carrier_codes_are_not_an_infection(title):
    groups = monitoring_groups("K2901", "Acute gastritis with bleeding",
                               ["Chronic diastolic (congestive) heart failure", title])
    assert "sepsis_infection" not in groups and groups[0] == "heart_failure"


def test_a_real_covid_or_pneumonia_diagnosis_still_is():
    assert "sepsis_infection" in monitoring_groups("", "COVID-19", [])
    assert "sepsis_infection" in monitoring_groups("", "Pneumonia, unspecified organism", [])


# ------------------------------------------------------------ red-flag floor
def test_a_red_flag_week_is_never_banded_low():
    obs = {"spo2": 87}
    low, _, _ = score_week(3.0, obs, 0.0)
    floored, _, _ = score_week(3.0, obs, 0.0, flag_floor=20.0)
    assert low < 20 <= floored


def test_the_floor_leaves_unflagged_weeks_alone():
    obs = {"spo2": 97, "adherence_pct": 95}
    assert score_week(3.0, obs, 0.0) == score_week(3.0, obs, 0.0, flag_floor=20.0)


# ------------------------------------------------------------ contact gap
def _wk(n, band, observed=True, flags=None):
    return {"week_number": n, "risk_band": band, "observed": observed, "red_flags": flags or []}


def test_silence_after_a_medium_week_is_overdue():
    gap = contact_gap([_wk(0, "Medium"), _wk(1, "Medium"), _wk(2, "Medium", False), _wk(3, "Medium", False)])
    assert gap == {"weeks_without_contact": 2, "last_contact_week": 1,
                   "last_contact_band": "Medium", "overdue": True}


def test_silence_after_a_low_week_is_not_overdue_and_contact_clears_it():
    assert not contact_gap([_wk(0, "Low"), _wk(1, "Low"), _wk(2, "Low", False)])["overdue"]
    assert contact_gap([_wk(0, "High"), _wk(1, "High", False), _wk(2, "High")])["weeks_without_contact"] == 0


def test_a_red_flag_before_the_silence_makes_it_overdue_even_when_low():
    assert contact_gap([_wk(0, "Low"), _wk(1, "Low", flags=["spo2"]), _wk(2, "Low", False)])["overdue"]


# ------------------------------------------------------------ the simulator
GROUPS = ("heart_failure", "respiratory", "surgical_injury", "sepsis_infection", "general",
          "oncology", "mental_health")


def _series(seed, group, deteriorating, severity=1.2):
    rng = np.random.default_rng(seed)
    course = sim.PatientCourse(group, deteriorating, rng)
    docs = [{"week_number": 0, "risk_score": 20.0, "risk_band": "Medium",
             "simulated_trajectory": "deteriorating" if deteriorating else "recovering"}]
    for wk in range(1, 5):
        obs = sim.observe_week(wk, 4, deteriorating, severity, rng, group=group,
                               groups=[group], course=course)
        docs.append({"week_number": wk, "observed": True, "monitoring": obs,
                     "risk_score": 30.0, "risk_band": "Medium", "red_flags": red_flags(obs)})
    return docs


@pytest.mark.parametrize("group", GROUPS)
@pytest.mark.parametrize("deteriorating", [True, False])
def test_generated_series_pass_every_consistency_check(group, deteriorating):
    for seed in range(150):
        failed = check_patient(_series(seed, group, deteriorating)) & set(CHECKS)
        assert not failed, (seed, group, deteriorating, failed)


def test_the_appointment_is_booked_once_and_rebooked_until_attended():
    seen = set()
    for seed in range(300):
        status = [d["monitoring"]["followup_status"] for d in _series(seed, "general", True)[1:]]
        assert status[0] == "not due"                       # 14-day review: not in week 1
        assert status.count("attended") <= 1
        if "attended" in status:
            after = status[status.index("attended") + 1:]
            assert set(after) <= {"not due"}
        seen.add(tuple(status))
    assert len(seen) > 3                                    # and it does vary


def test_refills_fall_only_when_the_supply_runs_out():
    for seed in range(300):
        due = [d["week_number"] for d in _series(seed, "general", True)[1:]
               if d["monitoring"]["refill_status"] != "not due"]
        assert due in ([4], [2, 4])


def test_swelling_and_pillows_follow_the_weight_in_heart_failure():
    for seed in range(300):
        for d in _series(seed, "heart_failure", True)[1:]:
            m = d["monitoring"]
            if m["ankle_swelling"] == "marked":
                assert m["weight_change_kg"] >= 1.0, (seed, m)


# ------------------------------------------------------------ the trend page
@pytest.fixture
def service(monkeypatch):
    os.environ.setdefault("MONGO_URI", "mongodb://127.0.0.1:1/?serverSelectionTimeoutMS=100")
    from api import main
    monkeypatch.setattr(main, "db", mongomock.MongoClient()["neuroshield"])
    return main


def _doc(wk, score, band, observed=True, flags=None):
    return {"patient_id": "P1", "week_number": wk, "days_after_discharge": 7 * wk,
            "week_date": f"2020-06-{9 + 7 * wk:02d}", "risk_score": score, "risk_band": band,
            "observed": observed, "red_flags": flags or [], "source": "simulated",
            "scored_by": "model" if wk == 0 else "monitoring_rules", "clinical_group": "heart_failure",
            "driver_1": "Medication adherence: 65% (below the 80% threshold)",
            "driver_2": "", "driver_3": ""}


def test_a_silent_week_is_held_and_the_next_contact_is_measured_from_the_last(service):
    docs = [_doc(0, 25.1, "Medium"), _doc(1, 34.2, "Medium"), _doc(2, 34.2, "Medium", False),
            _doc(3, 34.2, "Medium", False), _doc(4, 70.3, "High", flags=["spo2"])]
    weeks = service._weekly_trend_response("P1", docs)["weeks"]
    w2, w3, w4 = weeks[2], weeks[3], weeks[4]
    assert w2["delta"] is None and w2["interval_label"] == "no readings this week - score held from week 1"
    assert w2["contact_overdue"] and w2["held_from_week"] == 1
    assert w4["delta"] == 36.1 and w4["delta_since_week"] == 1 and w4["days_since_prev"] == 21
    assert w4["interval_label"] == "21 days after the last readings (week 1)"
    assert weeks[1]["interval_label"] == "7 days later" and weeks[1]["delta_since_week"] is None


def test_a_patient_not_reached_after_a_medium_week_needs_action(service):
    docs = [_doc(0, 25.1, "Medium"), _doc(1, 34.2, "Medium"), _doc(2, 34.2, "Medium", False)]
    body = service._weekly_trend_response("P1", docs)
    assert body["monitoring_status"] == "action_required"
    assert body["recommended_action"].startswith("No readings for 1 week since week 1")
    assert body["contact"]["overdue"]


def test_a_skipped_real_check_in_reads_as_the_gap_it_was(service):
    """Ingested check-ins have no document for a week nobody sent."""
    docs = [_doc(0, 20.0, "Medium"), _doc(2, 31.0, "Medium")]
    w = service._weekly_trend_response("P1", docs)["weeks"][1]
    assert w["interval_label"] == "14 days after discharge - the first readings since"
    assert w["days_since_prev"] == 14 and w["delta"] == 11.0

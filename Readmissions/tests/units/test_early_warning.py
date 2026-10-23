"""
Unit tests for the early-warning forecast.

Pure functions over a list of weeks — no database, no network. What is under
test is the arithmetic of the projection and the condition-specific rules,
because those are what a clinician is being asked to trust.

Run with:
  .venv/bin/python -m pytest tests/units/test_early_warning.py -v
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from models.early_warning import (
    ACCELERATION_NOISE_BAND,
    DEFAULT_BANDS,
    VELOCITY_NOISE_BAND,
    band_for,
    forecast,
)


def wk(number, score, **observations):
    return {"week_number": number, "risk_score": score, "monitoring": observations}


def codes(result):
    return {t["code"] for t in result["triggers"]}


def steady(*scores, **observations):
    """A series with one observation set applied to the final week."""
    weeks = [wk(i, s) for i, s in enumerate(scores, start=1)]
    weeks[-1]["monitoring"] = observations
    return weeks


# ---------------------------------------------------------------------------
# Bands and projection arithmetic
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("score, expected", [
    (0, "Low"), (19.9, "Low"), (20, "Medium"), (39.9, "Medium"),
    (40, "High"), (100, "High"),
])
def test_band_edges(score, expected):
    assert band_for(score, DEFAULT_BANDS) == expected


def test_velocity_is_points_per_week():
    result = forecast(steady(10.0, 15.0, 20.0))
    assert result["velocity_per_week"] == pytest.approx(5.0)


def test_projection_extends_the_slope():
    result = forecast(steady(10.0, 15.0, 20.0), horizon_weeks=2)
    assert result["projected_score"] == pytest.approx(30.0)


def test_projection_is_clamped_to_100():
    result = forecast(steady(50.0, 70.0, 90.0), horizon_weeks=2)
    assert result["projected_score"] == 100.0


def test_gap_in_reporting_flattens_the_slope():
    """
    A patient who reported at weeks 1 and 5 has not been climbing for four weeks
    at the rate the two points suggest per-index. Using week_number as the x axis
    is what keeps that honest.
    """
    spread = forecast([wk(1, 10.0), wk(5, 30.0)])
    adjacent = forecast([wk(1, 10.0), wk(2, 30.0)])
    assert spread["velocity_per_week"] < adjacent["velocity_per_week"]


def test_flat_series_raises_nothing():
    result = forecast(steady(30.0, 30.2, 29.8))
    assert result["severity"] == "none"
    assert result["triggers"] == []


def test_falling_series_raises_nothing():
    assert forecast(steady(60.0, 50.0, 40.0))["severity"] == "none"


# ---------------------------------------------------------------------------
# Lead time — the reason this exists
# ---------------------------------------------------------------------------

def test_forecasts_a_crossing_before_it_happens():
    result = forecast(steady(24.0, 29.0, 34.0))
    crossing = result["band_crossing"]
    assert result["current_band"] == "Medium"
    assert crossing["to_band"] == "High"
    assert 1 <= crossing["lead_time_days"] <= 14


def test_no_crossing_when_already_in_the_band():
    result = forecast(steady(44.0, 48.0, 52.0))
    assert result["band_crossing"] is None
    assert "rising_trajectory" in codes(result)


def test_a_projection_alone_is_never_critical():
    """
    Critical means review today. A forecast carrying a week of lead time is by
    definition not that, however steep it is.
    """
    result = forecast(steady(24.0, 30.0, 36.0))
    assert result["band_crossing"] is not None
    assert result["severity"] != "critical"


def test_noise_below_the_band_is_not_a_trend():
    result = forecast(steady(30.0, 30.5, 31.0))
    assert result["velocity_per_week"] < VELOCITY_NOISE_BAND
    assert "rising_trajectory" not in codes(result)


# ---------------------------------------------------------------------------
# Acceleration
# ---------------------------------------------------------------------------

def test_acceleration_fires_on_a_curve_bending_up():
    result = forecast(steady(20.0, 22.0, 30.0, 45.0))
    assert result["acceleration"] > ACCELERATION_NOISE_BAND
    assert "accelerating" in codes(result)


def test_slowing_improvement_is_not_acceleration():
    """
    A patient improving more slowly than last week has a positive second
    derivative and is still getting better. Calling that "deterioration is
    accelerating" was the single largest source of false alerts.
    """
    result = forecast(steady(90.0, 60.0, 40.0, 35.0))
    assert result["acceleration"] > 0
    assert "accelerating" not in codes(result)
    assert result["severity"] == "none"


# ---------------------------------------------------------------------------
# Condition-specific rules
# ---------------------------------------------------------------------------

def test_weight_gain_is_critical_in_heart_failure():
    result = forecast(steady(30.0, 31.0, 32.0, weight_change_kg=2.4),
                      group="heart_failure")
    assert "hf_fluid_overload" in codes(result)
    assert result["severity"] == "critical"


def test_the_same_weight_gain_is_silent_after_surgery():
    result = forecast(steady(30.0, 31.0, 32.0, weight_change_kg=2.4),
                      group="surgical_injury")
    assert "hf_fluid_overload" not in codes(result)


def test_weight_loss_is_the_oncology_flag_and_the_direction_inverts():
    losing = forecast(steady(30.0, 31.0, 32.0, weight_change_kg=-2.5), group="oncology")
    assert "onc_weight_loss" in codes(losing)
    # The identical reading means nothing in heart failure, where gain is the risk.
    gaining = forecast(steady(30.0, 31.0, 32.0, weight_change_kg=-2.5),
                       group="heart_failure")
    assert gaining["triggers"] == []


def test_two_sub_threshold_weeks_still_flag_cumulative_gain():
    weeks = [wk(1, 30.0), wk(2, 31.0, weight_change_kg=1.2),
             wk(3, 32.0, weight_change_kg=1.3)]
    assert "hf_cumulative_gain" in codes(forecast(weeks, group="heart_failure"))


def test_fever_after_sepsis_is_critical():
    result = forecast(steady(30.0, 31.0, 32.0, temperature_c=38.4),
                      group="sepsis_infection")
    assert "sepsis_fever_return" in codes(result)
    assert result["severity"] == "critical"


def test_new_confusion_is_critical_after_sepsis():
    result = forecast(steady(30.0, 31.0, 32.0, new_confusion=True),
                      group="sepsis_infection")
    assert "sepsis_confusion" in codes(result)


def test_respiratory_desaturation_escalates_above_the_universal_rule():
    obs = {"spo2": 90}
    respiratory = forecast(steady(30.0, 31.0, 32.0, **obs), group="respiratory")
    general = forecast(steady(30.0, 31.0, 32.0, **obs), group="general")
    assert respiratory["severity"] == "critical"
    assert general["severity"] == "moderate"


def test_borderline_saturation_is_not_a_page():
    """92% is often this patient's baseline and no baseline is recorded."""
    result = forecast(steady(30.0, 30.2, 30.1, spo2=92), group="general")
    assert "hypoxia" in codes(result)
    assert result["severity"] == "moderate"


@pytest.mark.parametrize("group, observations, expected", [
    ("renal",         {"weight_change_kg": 2.5, "sbp": 165}, "renal_overload_hypertensive"),
    ("neuro_stroke",  {"sbp": 190},                          "stroke_hypertension"),
    ("diabetes",      {"adherence_pct": 40},                 "dm_adherence"),
    ("surgical_injury", {"wound_status": "infected"},        "surg_wound"),
    ("mental_health", {"followup_status": "missed"},         "mh_missed_contact"),
    ("cardiac_other", {"heart_rate": 40},                    "cardiac_rate"),
])
def test_each_group_has_its_own_red_flag(group, observations, expected):
    assert expected in codes(forecast(steady(30.0, 31.0, 32.0, **observations), group=group))


# ---------------------------------------------------------------------------
# Universal rules and the engagement pattern
# ---------------------------------------------------------------------------

def test_hypotension_is_critical_for_anyone():
    assert "hypotension" in codes(forecast(steady(30.0, 31.0, 32.0, sbp=84)))


def test_one_missed_thing_is_not_disengagement():
    result = forecast(steady(30.0, 30.1, 30.2, refill_status="missed",
                             adherence_pct=95, followup_status="attended"))
    assert "disengagement" not in codes(result)


def test_two_missed_things_together_are():
    result = forecast(steady(30.0, 30.1, 30.2, refill_status="missed",
                             adherence_pct=40, followup_status="attended"))
    assert "disengagement" in codes(result)


def test_adherence_collapse_needs_the_previous_week():
    weeks = [wk(1, 30.0, adherence_pct=95), wk(2, 30.5, adherence_pct=92),
             wk(3, 31.0, adherence_pct=50)]
    assert "adherence_collapse" in codes(forecast(weeks))


# ---------------------------------------------------------------------------
# Corroboration
# ---------------------------------------------------------------------------

def test_two_serious_streams_escalate():
    weeks = [wk(1, 24.0), wk(2, 29.0),
             wk(3, 34.0, adherence_pct=30, refill_status="missed")]
    result = forecast(weeks)
    assert result["corroborated"] is True
    assert result["severity"] == "critical"


def test_a_moderate_finding_does_not_corroborate():
    """
    Counting mild findings as corroboration turned any small trend plus any
    borderline vital into a critical alert, which is how an inbox becomes noise.
    """
    result = forecast(steady(30.0, 32.0, 34.0, spo2=92))
    assert {"trajectory", "vitals"} <= set(result["streams"])
    assert result["corroborated"] is False
    assert result["severity"] == "moderate"


# ---------------------------------------------------------------------------
# Honesty about what is known
# ---------------------------------------------------------------------------

def test_no_weeks_is_reported_not_guessed():
    result = forecast([])
    assert result["status"] == "no_data"
    assert result["severity"] == "none"


def test_one_week_cannot_produce_a_trend():
    result = forecast([wk(1, 30.0, spo2=96)])
    assert result["status"] == "insufficient_history"
    assert result["velocity_per_week"] == 0.0
    assert result["band_crossing"] is None
    assert result["confidence"] == "low"


def test_one_week_still_raises_a_red_flag():
    """A single alarming reading is worth acting on with no trend at all."""
    result = forecast([wk(1, 30.0, weight_change_kg=3.0)], group="heart_failure")
    assert result["status"] == "insufficient_history"
    assert "hf_fluid_overload" in codes(result)
    assert result["severity"] == "critical"


def test_confidence_is_separate_from_severity():
    result = forecast([wk(1, 30.0, sbp=80)])
    assert result["severity"] == "critical"
    assert result["confidence"] == "low"


def test_every_result_states_its_basis():
    for weeks in ([], [wk(1, 30.0)], steady(10.0, 20.0, 30.0)):
        assert "not a trained predictor" in forecast(weeks)["basis"].lower()


def test_every_trigger_carries_a_rationale():
    result = forecast(steady(24.0, 30.0, 36.0, weight_change_kg=2.5, sbp=80),
                      group="heart_failure")
    assert result["triggers"]
    for trigger in result["triggers"]:
        assert trigger["rationale"].strip()
        assert trigger["detail"].strip()


def test_triggers_are_ordered_worst_first():
    result = forecast(steady(24.0, 30.0, 36.0, weight_change_kg=2.5, spo2=92),
                      group="heart_failure")
    severities = [t["severity"] for t in result["triggers"]]
    assert severities == sorted(
        severities, key=["none", "moderate", "high", "critical"].index, reverse=True)


def test_unknown_group_falls_back_to_universal_rules_only():
    result = forecast(steady(30.0, 31.0, 32.0, sbp=84), group="not_a_real_group")
    assert "hypotension" in codes(result)


def test_missing_observations_are_not_treated_as_normal():
    """An unreported vital must produce no flag, not a reassuring one."""
    assert forecast(steady(30.0, 30.1, 30.2))["triggers"] == []


def test_a_red_flag_on_the_weekly_card_is_a_same_day_alert():
    """SpO2 89% was a red flag on the card ("assess today") and only moderate
    here, so the week it appeared raised no alert."""
    from models import early_warning as ew
    weeks = [{"week_number": 0, "risk_score": 40.0, "monitoring": {}},
             {"week_number": 1, "risk_score": 41.0, "monitoring": {"spo2": 89}}]
    result = ew.forecast(weeks, "mental_health")
    assert result["severity"] == "critical"
    assert result["triggers"][0]["code"] == "red_flag"
    assert "oxygen saturation below 90%" in result["triggers"][0]["title"]
    # already critical for the same reading: not listed twice
    weeks[1]["monitoring"] = {"spo2": 86}
    assert "red_flag" not in [t["code"] for t in ew.forecast(weeks, "mental_health")["triggers"]]

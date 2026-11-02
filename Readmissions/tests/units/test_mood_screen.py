"""The mental and behavioural health screen (models/mood_screen.py): flag only.

Pure functions, no database. The API side - who may send it, who may read it
back, and the alerts it raises - is tests/units/test_mood_api.py.
"""
import pandas as pd
import pytest

from api import discharge_plan, doctor_service
from features import weekly_rows
from models import early_warning, mood_screen
from models.mood_screen import GAD2, PHQ2, PHQ8_MORE, assess, band, card, clean, triggers

LOW = {"phq_interest": 0, "phq_down": 0}
POSITIVE_PHQ2 = {"phq_interest": 2, "phq_down": 1}


def phq8(total_of_extras: int, base=None) -> dict:
    """PHQ-2 positive (3) plus extras spread to reach the given extras total."""
    answers = dict(base or POSITIVE_PHQ2)
    left = total_of_extras
    for key in PHQ8_MORE:
        answers[key] = min(3, left)
        left -= answers[key]
    return answers


# ------------------------------------------------------------ clean
@pytest.mark.parametrize("answers", [
    {"phq_self_harm": 1},                       # never asked
    {"phq_interest": True},                     # a bool is not an answer
    {"phq_interest": "4"},
    {"phq_interest": 4},
    {"phq_interest": 2.0},
    {"phq_interest": [1]},
])
def test_clean_refuses_what_was_not_asked_or_is_not_an_answer(answers):
    with pytest.raises(ValueError):
        clean(answers)


def test_clean_names_what_is_wrong():
    with pytest.raises(ValueError, match="Not asked this week: a_key, b_key"):
        clean({"b_key": 1, "a_key": 1})
    with pytest.raises(ValueError, match="Choose one of the answers for: Little interest"):
        clean({"phq_interest": 9})


def test_clean_drops_blanks_and_reads_strings():
    assert clean({"phq_interest": "2", "phq_down": None, "gad_worry": ""}) == {"phq_interest": 2}


def test_the_rest_of_the_phq8_is_only_kept_when_the_phq2_is_positive():
    extras = {k: 1 for k in PHQ8_MORE}
    assert clean({**{"phq_interest": 1, "phq_down": 1}, **extras}) == {"phq_interest": 1, "phq_down": 1}
    assert clean({"phq_interest": 3, **extras}) == {"phq_interest": 3}          # PHQ-2 incomplete
    assert clean({**POSITIVE_PHQ2, **extras}) == {**POSITIVE_PHQ2, **extras}
    assert clean({"gad_nervous": 1, **extras}) == {"gad_nervous": 1}


def test_the_form_never_asks_about_self_harm():
    form = mood_screen.form()
    keys = [q["key"] for part in ("phq2", "phq8_more", "gad2") for q in form[part]]
    assert keys == list(PHQ2) + list(PHQ8_MORE) + list(GAD2)
    text = " ".join(q["label"] for part in ("phq2", "phq8_more", "gad2") for q in form[part]).lower()
    assert "dead" not in text and "hurting yourself" not in text
    assert form["crisis"] == mood_screen.CRISIS and "988" in form["crisis"]
    assert [o["value"] for o in form["options"]] == [0, 1, 2, 3] and form["followup_from"] == 3


# ------------------------------------------------------------ assess
def test_nothing_answered_is_nothing():
    assert assess({}, [], 2) is None


def test_a_partly_answered_phq2_has_no_total():
    mood = assess({"phq_interest": 3}, [], 2)
    assert mood["phq2"] is None and mood["phq8"] is None and mood["flags"] == []


def test_a_phq8_total_needs_every_item_and_names_its_band():
    mood = assess(phq8(6), [], 2)                      # 3 from the PHQ-2 and 6 from the rest
    assert mood["phq2"] == 3 and mood["phq8"] == 9 and mood["phq8_band"] == "mild"
    full = assess(phq8(10), [], 2)
    assert full["phq8"] == 13 and full["phq8_band"] == "moderate"
    partial = {**POSITIVE_PHQ2, "phq_sleep": 2}
    assert assess(partial, [], 2)["phq8"] is None


def test_a_negative_phq2_gives_no_phq8():
    assert assess({**LOW, "gad_nervous": 0, "gad_worry": 0}, [], 2)["phq8"] is None


@pytest.mark.parametrize("total, label", [
    (0, "minimal"), (4, "minimal"), (5, "mild"), (9, "mild"), (10, "moderate"), (14, "moderate"),
    (15, "moderately severe"), (19, "moderately severe"), (20, "severe"), (24, "severe")])
def test_band_edges(total, label):
    assert band(total) == label


def test_band_of_nothing_is_nothing():
    assert band(None) is None


def test_moderate_or_worse_flags_the_first_time_only():
    first = assess(phq8(10), [], 3)
    assert first["flags"] == ["phq8_moderate"]
    later = assess(phq8(10), [{"week_number": 3, "phq8": 13}], 4)
    assert "phq8_moderate" not in later["flags"]
    # Only earlier weeks count: re-scoring week 3 is not compared with week 4.
    again = assess(phq8(10), [{"week_number": 4, "phq8": 13}], 3)
    assert again["flags"] == ["phq8_moderate"]


def test_a_rise_of_five_needs_a_previous_phq8():
    assert assess(phq8(6), [], 3)["flags"] == []                         # 9: nothing to compare with
    base = [{"week_number": 1, "phq8": 4}]
    rose = assess(phq8(6), base, 3)                                      # 9, up 5
    assert rose["flags"] == ["phq8_rise"] and rose["change"] == 5
    assert rose["previous"] == {"week_number": 1, "phq8": 4}
    assert "phq8_rise" not in assess(phq8(5), base, 3)["flags"]          # 8, up 4
    # The latest earlier PHQ-8 is the one compared with, whatever came between.
    history = [{"week_number": 1, "phq8": 4}, {"week_number": 2, "phq2": 1, "phq8": None}]
    assert assess(phq8(6), history, 3)["previous"]["week_number"] == 1


def test_a_falling_phq8_is_a_change_but_not_a_flag():
    mood = assess(phq8(6), [{"week_number": 1, "phq8": 15}], 3)
    assert mood["change"] == -6 and "phq8_rise" not in mood["flags"]


def test_low_mood_with_missed_doses_needs_both():
    assert "low_mood_adherence" in assess(POSITIVE_PHQ2, [], 2, 50)["flags"]
    assert "low_mood_adherence" not in assess(POSITIVE_PHQ2, [], 2, 80)["flags"]
    assert "low_mood_adherence" not in assess(POSITIVE_PHQ2, [], 2, None)["flags"]
    assert "low_mood_adherence" not in assess(LOW, [], 2, 20)["flags"]


def test_anxiety_screen_flags_at_three():
    assert assess({"gad_nervous": 2, "gad_worry": 1}, [], 2)["flags"] == ["gad2_positive"]
    assert assess({"gad_nervous": 1, "gad_worry": 1}, [], 2)["flags"] == []
    assert assess({"gad_nervous": 3}, [], 2)["flags"] == []              # half answered is nothing


def test_simulated_is_marked():
    assert assess(LOW, [], 1, simulated=True)["simulated"] is True
    assert "simulated" not in assess(LOW, [], 1)


# ------------------------------------------------------------ triggers and the card
def test_triggers_use_co_occurrence_wording_and_never_critical():
    mood = assess(phq8(10), [{"week_number": 1, "phq8": 4}], 3, 50)
    found = {t["code"]: t for t in triggers(mood)}
    assert set(found) == {"phq8_moderate", "phq8_rise", "low_mood_adherence"}
    assert all(t["stream"] == "mood" and t["severity"] == "high" for t in found.values())
    assert found["phq8_rise"]["title"] == "Mood worse by 9 points since last time"
    assert found["phq8_rise"]["detail"] == "PHQ-8 13, up from 4 in week 1."
    assert found["low_mood_adherence"]["detail"] == "PHQ-2 3, PHQ-8 13; 50% of doses taken this week."
    assert not any(t["severity"] == "critical" for t in found.values())


def test_the_anxiety_flag_is_moderate_and_carries_no_group_note():
    mood = assess({"gad_nervous": 3, "gad_worry": 2}, [], 2)
    (trigger,) = triggers(mood, "heart_failure")
    assert trigger["code"] == "gad2_positive" and trigger["severity"] == "moderate"
    assert trigger["title"] == "Anxiety screen positive (GAD-2 5)"
    assert mood_screen.GROUP_NOTES["heart_failure"] not in trigger["rationale"]


def test_the_group_note_is_added_to_the_other_flags():
    mood = assess(phq8(10), [], 2)
    (plain,) = triggers(mood, "general")
    (with_group,) = triggers(mood, "heart_failure")
    assert mood_screen.GROUP_NOTES["heart_failure"] in with_group["rationale"]
    assert with_group["rationale"].startswith(plain["rationale"])
    assert triggers(None) == []


def test_the_card_reads_in_plain_words():
    mood = assess(phq8(10), [{"week_number": 1, "phq8": 7}], 3)
    lines = {line["key"]: line for line in card(mood)["lines"]}
    assert lines["phq8"]["text"] == "13 · moderate · up 6 since last time" and lines["phq8"]["positive"]
    assert card(None) is None
    down = card(assess(phq8(6), [{"week_number": 1, "phq8": 12}], 3))["lines"][0]["text"]
    assert down == "9 · mild · down 3 since last time"
    same = card(assess(phq8(6), [{"week_number": 1, "phq8": 9}], 3))["lines"][0]["text"]
    assert same == "9 · mild · same as last time"

    anxious = card(assess({"gad_nervous": 3, "gad_worry": 1}, [], 2))
    assert [(line["key"], line["text"], line["positive"]) for line in anxious["lines"]] == [
        ("gad2", "4 · positive screen", True)]
    calm = card(assess({**LOW, "gad_nervous": 0, "gad_worry": 1}, [], 2))
    assert [(line["key"], line["text"]) for line in calm["lines"]] == [
        ("phq2", "0 · negative screen"), ("gad2", "1 · negative screen")]
    assert card(assess({"phq_interest": 1}, [], 2))["lines"][0]["text"] == "not all answered"
    unfinished = card(assess(POSITIVE_PHQ2, [], 2))["lines"][0]["text"]
    assert unfinished == "3 · positive screen - the follow-up questions were not all answered"
    assert card(mood)["simulated"] is False
    assert card(assess(LOW, [], 1, simulated=True))["simulated"] is True


# ------------------------------------------------------------ the forecast
def week(n, score, mood=None, **monitoring):
    return {"week_number": n, "risk_score": score, "monitoring": monitoring, **({"mood": mood} if mood else {})}


def test_a_moderate_mood_screen_raises_the_forecast_to_high_without_touching_the_score():
    mood = assess(phq8(10), [], 2)
    result = early_warning.forecast([week(1, 30.0), week(2, 30.0, mood)], "heart_failure")
    assert result["severity"] == "high" and result["severity_without_mood"] == "none"
    assert result["current_score"] == 30.0 and result["projected_score"] == 30.0
    assert "mood" in result["streams"]
    codes = [t["code"] for t in result["triggers"] if t["stream"] == "mood"]
    assert codes == ["phq8_moderate"]


def test_mood_is_not_a_second_stream_that_makes_a_high_alert_critical():
    mood = assess(phq8(10), [], 3)
    rising = [week(1, 20.0), week(2, 28.0)]
    without = early_warning.forecast([*rising, week(3, 36.0)], "general")
    with_mood = early_warning.forecast([*rising, week(3, 36.0, mood)], "general")
    # A trajectory trigger at "high" alone; if mood counted as a second serious
    # stream the corroboration rule would escalate this to critical.
    assert [t["code"] for t in without["triggers"]] == ["projected_band_crossing"]
    assert without["severity"] == "high" and without["corroborated"] is False
    assert with_mood["severity"] == "high" and with_mood["corroborated"] is False
    assert with_mood["severity_without_mood"] == "high"
    assert {t["stream"] for t in with_mood["triggers"]} == {"trajectory", "mood"}


def test_the_anxiety_screen_alone_is_moderate_so_no_alert():
    mood = assess({"gad_nervous": 2, "gad_worry": 2}, [], 2)
    result = early_warning.forecast([week(1, 30.0), week(2, 30.0, mood)], "general")
    assert result["severity"] == "moderate" and result["severity_without_mood"] == "none"
    assert result["severity"] not in doctor_service.ALERTABLE_SEVERITIES


def test_without_mood_restores_the_forecast_a_non_clinical_role_may_read():
    mood = assess(phq8(10), [], 2)
    result = early_warning.forecast([week(1, 30.0), week(2, 30.0, mood)], "heart_failure")
    plain = early_warning.without_mood(result)
    assert plain["severity"] == "none" and plain["recommended_review_by"] == "no review needed"
    assert not any(t["stream"] == "mood" for t in plain["triggers"]) and "mood" not in plain["streams"]
    assert "severity_without_mood" not in plain
    assert result["severity"] == "high"                                   # the input is untouched
    assert early_warning.without_mood(None) is None and early_warning.without_mood({}) == {}
    # No mood in it: the same forecast, minus the bookkeeping key.
    quiet = early_warning.forecast([week(1, 30.0), week(2, 30.0)], "general")
    assert early_warning.without_mood(quiet) == {k: v for k, v in quiet.items() if k != "severity_without_mood"}


def test_without_mood_keeps_a_vitals_trigger_and_its_severity():
    mood = assess(phq8(10), [], 2)
    result = early_warning.forecast([week(1, 30.0), week(2, 30.0, mood, spo2=88)], "respiratory")
    plain = early_warning.without_mood(result)
    assert plain["triggers"] and all(t["stream"] != "mood" for t in plain["triggers"])
    assert plain["severity"] == result["severity_without_mood"]


# ------------------------------------------------------------ alerts, weekly rows, the plan
def alert_with(forecast=None, **fields):
    return {"alert_id": "x", "patient_id": "p", "severity": "high",
            **({"forecast": forecast} if forecast is not None else {}), **fields}


def test_a_mood_only_alert_is_the_care_teams_and_a_mixed_one_loses_its_mood():
    mood = assess(phq8(10), [], 2)
    only = early_warning.forecast([week(1, 30.0), week(2, 30.0, mood)], "heart_failure")
    assert doctor_service.without_mood(alert_with(only)) is None
    mixed = early_warning.forecast([week(1, 30.0), week(2, 30.0, mood, spo2=88)], "respiratory")
    stripped = doctor_service.without_mood(alert_with(mixed, severity=mixed["severity"]))
    assert stripped["severity"] == mixed["severity_without_mood"]
    assert all(t["stream"] != "mood" for t in stripped["forecast"]["triggers"])
    assert any(t["stream"] == "mood" for t in mixed["triggers"])          # the stored alert is untouched


def test_an_alert_without_a_forecast_or_mood_is_unchanged():
    bare = {"alert_id": "CA-1", "patient_id": "p", "status": "open"}
    assert doctor_service.without_mood(bare) is bare
    plain = alert_with({"severity": "high", "triggers": [{"stream": "vitals", "severity": "high"}]})
    assert doctor_service.without_mood(plain) is plain


def test_a_weekly_model_never_sees_the_mood_columns():
    mood = assess(phq8(10), [], 2)
    flat = weekly_rows.flatten([{"patient_id": "p", "week_number": 2, "risk_score": 30.0, "mood": mood,
                                 "monitoring": {"adherence_pct": 80}}])
    assert isinstance(flat, pd.DataFrame)
    assert "mood" not in flat.columns
    assert not [c for c in flat.columns if str(c).startswith(("phq", "gad", "mood"))]
    assert "adherence_pct" in flat.columns


def test_the_mental_health_plan_has_its_own_follow_up_item():
    assert "mental_health_followup" in discharge_plan.items_for("mental_health", "Low", {})
    assert "mental_health_followup" not in discharge_plan.items_for("general", "Low", {})
    assert discharge_plan.LABELS["mental_health_followup"] == "Mental health follow-up arranged"

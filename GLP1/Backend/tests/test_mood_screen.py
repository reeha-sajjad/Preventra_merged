"""The mood screen's questions and flag rules (core/mood_screen.py): pure, no
database. The shared parts mirror Readmissions/tests/units/test_mood_screen.py;
the ordering by date, "first time" and the paired events are GLP-1's own.
"""
from datetime import datetime, timedelta, timezone

import pytest

from core import mood_screen as ms

T0 = datetime(2026, 9, 1, 9, 0, tzinfo=timezone.utc)
FORTNIGHT = timedelta(days=14)

LOW = {"phq_interest": 2, "phq_down": 2}                       # PHQ-2 4: positive
REST_OF_PHQ8 = {"phq_sleep": 1, "phq_tired": 1, "phq_appetite": 1, "phq_failure": 1,
                "phq_concentration": 1, "phq_movement": 1}


def phq8_of(total: int) -> dict:
    """A positive PHQ-2 and the rest answered, adding up to `total` (from 4 to 22)."""
    keys = list(ms.PHQ2) + list(ms.PHQ8_MORE)
    answers = {k: 0 for k in keys}
    answers["phq_interest"], answers["phq_down"] = 2, 2
    left = total - 4
    for k in keys[2:]:
        step = min(3, left)
        answers[k] = step
        left -= step
    assert left == 0 and sum(answers.values()) == total
    return answers


def screen(answers, history=(), n=0, trouble=None, simulated=False):
    return ms.assess(answers, list(history), T0 + n * FORTNIGHT, trouble, simulated)


def stored(result, n):
    return {**result, "at": T0 + n * FORTNIGHT}


# ----------------------------------------------------------------- the form
def test_the_form_is_the_phq8_and_gad2_and_nothing_else():
    form = ms.form()
    assert [q["key"] for q in form["phq2"]] == list(ms.PHQ2)
    assert [q["key"] for q in form["phq8_more"]] == list(ms.PHQ8_MORE)
    assert [q["key"] for q in form["gad2"]] == list(ms.GAD2)
    assert len(ms.ITEMS) == 10 and len(ms.PHQ2) + len(ms.PHQ8_MORE) == 8 and len(ms.GAD2) == 2
    assert form["followup_from"] == 3
    assert [o["value"] for o in form["options"]] == [0, 1, 2, 3]
    assert [o["label"] for o in form["options"]] == [
        "Not at all (0–1 days)", "Several days (2–6 days)",
        "More than half the days (7–11 days)", "Nearly every day (12–14 days)"]
    assert form["safety"] == ms.SAFETY == (
        "If you feel unwell or unsafe, contact your care team, or call 911 in an emergency.")


# -------------------------------------------------------------------- clean
def test_clean_refuses_unknown_and_invalid_answers():
    with pytest.raises(ValueError):
        ms.clean({"phq_interest": 1, "something_else": 2})
    for bad in (4, -1, True, 1.5, "x", [1]):
        with pytest.raises(ValueError):
            ms.clean({"phq_interest": bad})


def test_clean_reads_strings_and_drops_blanks():
    assert ms.clean({"phq_interest": "2", "phq_down": "", "gad_worry": None, "gad_nervous": 0}) == {
        "phq_interest": 2, "gad_nervous": 0}


def test_the_rest_of_the_phq8_is_dropped_unless_the_phq2_is_positive():
    assert ms.clean({"phq_interest": 1, "phq_down": 1, **REST_OF_PHQ8}) == {"phq_interest": 1, "phq_down": 1}
    assert ms.clean({"phq_interest": 1, **REST_OF_PHQ8}) == {"phq_interest": 1}
    assert ms.clean({**LOW, **REST_OF_PHQ8}) == {**LOW, **REST_OF_PHQ8}


# ------------------------------------------------------- totals and bands
def test_a_total_exists_only_when_every_item_was_answered():
    part = screen({"phq_interest": 3})
    assert part["phq2"] is None and part["phq8"] is None and part["flags"] == []
    only_phq2 = screen(LOW)
    assert only_phq2["phq2"] == 4 and only_phq2["phq8"] is None          # follow-up unanswered
    assert screen({**LOW, **REST_OF_PHQ8})["phq8"] == 10
    assert screen({}) is None


@pytest.mark.parametrize("total, label", [(4, "minimal"), (5, "mild"), (9, "mild"), (10, "moderate"),
                                          (14, "moderate"), (15, "moderately severe"),
                                          (19, "moderately severe"), (20, "severe"), (24, "severe")])
def test_the_bands(total, label):
    assert ms.band(total) == label
    assert ms.band(None) is None


# -------------------------------------------------------------- the flags
def test_the_first_screen_at_10_or_more_flags():
    result = screen(phq8_of(11))
    assert result["flags"] == ["phq8_moderate"] and result["phq8_band"] == "moderate"
    assert result["previous"] is None and result["change"] is None


def test_staying_at_10_or_more_does_not_flag_again():
    first = stored(screen(phq8_of(11)), 0)
    second = screen(phq8_of(12), [first], n=1)
    assert second["flags"] == [] and second["change"] == 1
    assert second["previous"] == {"answered_on": first["at"].date().isoformat(), "phq8": 11}


def test_a_new_episode_flags_again_after_a_screen_below_10():
    first = stored(screen(phq8_of(11)), 0)
    well = stored(screen({"phq_interest": 0, "phq_down": 1}, [first], n=1), 1)       # PHQ-2 negative: no PHQ-8
    assert well["phq8"] is None
    again = screen(phq8_of(10), [first, well], n=2)
    assert again["flags"] == ["phq8_moderate"]
    assert again["previous"]["phq8"] == 11                                            # the last real PHQ-8


def test_an_unfinished_follow_up_counts_as_not_at_10():
    first = stored(screen(phq8_of(12)), 0)
    unfinished = stored(screen(LOW, [first], n=1), 1)                                 # PHQ-2 positive, rest missing
    assert unfinished["phq8"] is None
    assert screen(phq8_of(10), [first, unfinished], n=2)["flags"] == ["phq8_moderate"]


def test_a_rise_of_5_or_more_flags_and_a_fall_does_not():
    base = stored(screen(phq8_of(10)), 0)
    rise = screen(phq8_of(15), [base], n=1)
    assert rise["flags"] == ["phq8_rise"] and rise["change"] == 5
    assert screen(phq8_of(14), [base], n=1)["flags"] == []
    assert screen(phq8_of(6), [stored(screen(phq8_of(16)), 0)], n=1)["flags"] == []


def test_a_rise_from_below_10_that_crosses_it_flags_both():
    base = stored(screen(phq8_of(6)), 0)
    assert screen(phq8_of(12), [base], n=1)["flags"] == ["phq8_moderate", "phq8_rise"]


def test_low_mood_with_trouble_flags_and_each_alone_does_not():
    assert screen(LOW, trouble=["missed_pickup"])["flags"] == ["low_mood_trouble"]
    assert screen(LOW)["flags"] == []                                                 # low mood, no event
    quiet = screen({"phq_interest": 0, "phq_down": 1}, trouble=["missed_pickup"])
    assert quiet["flags"] == [] and quiet["trouble"] == []                             # event, mood fine


def test_the_trouble_is_kept_once_and_sorted():
    result = screen(LOW, trouble=["stopping_ask", "dose_gap", "stopping_ask"])
    assert result["trouble"] == ["dose_gap", "stopping_ask"]


def test_gad2_of_3_or_more_flags_the_anxiety_screen():
    assert screen({"gad_nervous": 2, "gad_worry": 1})["flags"] == ["gad2_positive"]
    assert screen({"gad_nervous": 1, "gad_worry": 1})["flags"] == []
    assert screen({"gad_nervous": 3})["gad2"] is None


def test_history_at_or_after_the_screen_is_ignored():
    later = stored(screen(phq8_of(20)), 3)
    same_time = stored(screen(phq8_of(20)), 1)
    result = screen(phq8_of(10), [later, same_time], n=1)
    assert result["previous"] is None and result["flags"] == ["phq8_moderate"]


def test_history_may_hold_naive_datetimes_and_iso_strings():
    first = {**screen(phq8_of(11)), "at": (T0).replace(tzinfo=None)}
    assert screen(phq8_of(12), [first], n=1)["previous"]["phq8"] == 11
    as_text = {**screen(phq8_of(11)), "at": T0.isoformat(), "answered_on": "2026-09-01"}
    again = screen(phq8_of(17), [as_text], n=1)
    assert again["previous"] == {"answered_on": "2026-09-01", "phq8": 11} and again["flags"] == ["phq8_rise"]


def test_a_simulated_screen_is_marked_and_a_real_one_is_not():
    assert screen(LOW, simulated=True)["simulated"] is True
    assert "simulated" not in screen(LOW)


# ------------------------------------------------- triggers and the card
def test_triggers_are_never_critical_and_carry_scores_only():
    result = screen(LOW, trouble=["missed_pickup", "dose_gap"])
    flags = ms.triggers(result)
    assert [f["code"] for f in flags] == ["low_mood_trouble"]
    assert {f["severity"] for f in flags} == {"high"}
    assert flags[0]["detail"] == "PHQ-2 4."
    everything = " ".join(str(v) for f in flags for v in f.values()).upper()
    assert not any(drug in everything for drug in ("SEMAGLUTIDE", "TIRZEPATIDE", "LIRAGLUTIDE", "DULAGLUTIDE"))
    both = ms.triggers(screen(phq8_of(12), trouble=["dose_gap"]))
    assert {f["code"] for f in both} == {"phq8_moderate", "low_mood_trouble"}
    assert next(f for f in both if f["code"] == "low_mood_trouble")["detail"] == "PHQ-2 4, PHQ-8 12."
    assert all(f["severity"] != "critical" for f in both)
    assert ms.triggers(None) == []


def test_only_three_of_the_flags_ring_the_bell():
    assert set(ms.BELL_FLAGS) == {"phq8_moderate", "phq8_rise", "low_mood_trouble"}
    assert "gad2_positive" in ms.SEVERITY and "gad2_positive" not in ms.BELL_FLAGS


def test_the_card_says_it_in_plain_lines_and_never_shows_the_items():
    base = stored(screen(phq8_of(10)), 0)
    now = {**screen(phq8_of(11), [base], n=1), "answered_on": "2026-09-15"}
    card = ms.card(now)
    assert card["lines"][0] == {"key": "phq8", "label": "Mood (PHQ-8)", "positive": True,
                                "text": "11 · moderate · up 1 since last time"}
    assert card["answered_on"] == "2026-09-15" and card["simulated"] is False
    assert "answers" not in card
    positive = ms.card(screen({"phq_interest": 3, "phq_down": 1, "gad_nervous": 1, "gad_worry": 3}))
    assert [l["text"] for l in positive["lines"]] == [
        "4 · positive screen - the follow-up questions were not all answered", "4 · positive screen"]
    assert ms.card(screen({"phq_interest": 0, "phq_down": 0}))["lines"][0]["text"] == "0 · negative screen"
    assert ms.card(None) is None

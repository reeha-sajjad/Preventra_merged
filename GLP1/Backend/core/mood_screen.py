"""
The mood screen the patient answers every 2 weeks. FLAG ONLY.

The patient answers the PHQ-2 (and, when it is positive, the rest of the PHQ-8)
and the GAD-2. Twin of Readmissions/models/mood_screen.py: the questions,
options, totals, bands and thresholds are the same and must be edited together
(the services deploy separately, so this is a copy, not an import). What differs:

  - Readmissions orders screens by week of a discharge; here they are ordered by
    `at`, because a GLP-1 patient has no discharge to count from.
  - "First time at 10 or more" means the first screen at 10 or more after one
    below 10 (or the first screen at all), so a new episode flags once and a
    patient who stays at 10 or more does not flag every 2 weeks. A rise of 5 or
    more is still caught inside an episode.
  - Low mood is paired with real events (core/mood.trouble_events), not with a
    weekly adherence percentage.

A screen never touches the dropout score or any model input. A flag says low mood
and trouble with treatment went together, not that one caused the other, and a
screen is not a diagnosis. The form always carries a plain safety line (`SAFETY`).

A skipped item is nothing, never a 0, and a total exists only when every item in
it was answered. The PHQ's two-week recall means the same low mood can show in two
consecutive screens; the "first time" and "change" rules are written with that in mind.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

STEM = "Over the last 2 weeks, how often have you been bothered by any of the following problems?"
SAFETY = "If you feel unwell or unsafe, contact your care team, or call 911 in an emergency."

OPTIONS = ((0, "Not at all"), (1, "Several days"),
           (2, "More than half the days"), (3, "Nearly every day"))

PHQ2 = {"phq_interest": "Little interest or pleasure in doing things",
        "phq_down": "Feeling down, depressed, or hopeless"}
PHQ8_MORE = {
    "phq_sleep": "Trouble falling or staying asleep, or sleeping too much",
    "phq_tired": "Feeling tired or having little energy",
    "phq_appetite": "Poor appetite or overeating",
    "phq_failure": "Feeling bad about yourself - or that you are a failure or have let yourself or your family down",
    "phq_concentration": "Trouble concentrating on things, such as reading the newspaper or watching television",
    "phq_movement": ("Moving or speaking so slowly that other people could have noticed? Or the opposite - "
                     "being so fidgety or restless that you have been moving around a lot more than usual"),
}
GAD2 = {"gad_nervous": "Feeling nervous, anxious, or on edge",
        "gad_worry": "Not being able to stop or control worrying"}
ITEMS = {**PHQ2, **PHQ8_MORE, **GAD2}

PHQ2_POSITIVE = 3      # a positive screen, and when the rest of the PHQ-8 is asked
PHQ8_MODERATE = 10
PHQ8_RISE = 5
GAD2_POSITIVE = 3

# PHQ-8 total -> band, as the upper edge of each.
BANDS = ((4, "minimal"), (9, "mild"), (14, "moderate"), (19, "moderately severe"), (24, "severe"))

# Never "critical": a mood screen is a reason to ask, not an emergency. The
# anxiety screen is shown but does not ring the bell on its own.
SEVERITY = {"phq8_moderate": "high", "phq8_rise": "high", "low_mood_trouble": "high",
            "gad2_positive": "moderate"}
BELL_FLAGS = ("phq8_moderate", "phq8_rise", "low_mood_trouble")


def form() -> dict:
    """The questions the patient is shown. Nothing here depends on a patient."""
    def rows(items):
        return [{"key": k, "label": v} for k, v in items.items()]
    return {
        "title": "How you have been feeling",
        "stem": STEM,
        "safety": SAFETY,
        "options": [{"value": v, "label": label} for v, label in OPTIONS],
        "phq2": rows(PHQ2),
        "phq8_more": rows(PHQ8_MORE),
        "gad2": rows(GAD2),
        "followup_from": PHQ2_POSITIVE,
    }


def _total(answers: dict, keys) -> Optional[int]:
    """The sum when every item was answered; otherwise nothing, never a part-sum."""
    if any(answers.get(k) is None for k in keys):
        return None
    return sum(int(answers[k]) for k in keys)


def clean(answers: dict) -> dict:
    """
    Check what the patient sent. Returns only answered items, as ints 0-3.
    Raises ValueError with a sentence the form can show.

    The rest of the PHQ-8 is only asked when the PHQ-2 is positive, so those
    answers are dropped (not refused) when it is not: a stale form is not a reason
    to lose the answers.
    """
    given = {k: v for k, v in (answers or {}).items() if v is not None and v != ""}
    unknown = sorted(k for k in given if k not in ITEMS)
    if unknown:
        raise ValueError("Not on these questions: " + ", ".join(unknown))
    out = {}
    for key, value in given.items():
        if isinstance(value, bool) or not isinstance(value, (int, str)):
            raise ValueError(f"Choose one of the answers for: {ITEMS[key]}")
        if isinstance(value, str):
            if value not in ("0", "1", "2", "3"):
                raise ValueError(f"Choose one of the answers for: {ITEMS[key]}")
            value = int(value)
        if value not in (0, 1, 2, 3):
            raise ValueError(f"Choose one of the answers for: {ITEMS[key]}")
        out[key] = value
    phq2 = _total(out, PHQ2)
    if phq2 is None or phq2 < PHQ2_POSITIVE:
        out = {k: v for k, v in out.items() if k not in PHQ8_MORE}
    return out


def band(phq8: Optional[int]) -> Optional[str]:
    if phq8 is None:
        return None
    for upper, label in BANDS:
        if phq8 <= upper:
            return label
    return BANDS[-1][1]


def _aware(at) -> datetime:
    """A datetime as timezone-aware UTC (mongomock hands back naive ones); an ISO string is accepted."""
    if isinstance(at, str):
        at = datetime.fromisoformat(at)
    return at if at.tzinfo else at.replace(tzinfo=timezone.utc)


def assess(answers: dict, history: list, at, trouble=None, simulated: bool = False) -> Optional[dict]:
    """
    Totals and flags for one screen's cleaned answers.

    history  earlier screens of this patient; anything not before `at` is
             ignored, so re-scoring a screen never compares it with its own future
    trouble  the real events found when it was answered (core/mood.trouble_events);
             low mood with any of them raises low_mood_trouble
    """
    if not answers:
        return None
    at = _aware(at)
    earlier = sorted((h for h in history or [] if h and h.get("at") is not None and _aware(h["at"]) < at),
                     key=lambda h: _aware(h["at"]))

    phq2 = _total(answers, PHQ2)
    phq8 = _total(answers, list(PHQ2) + list(PHQ8_MORE)) if phq2 is not None and phq2 >= PHQ2_POSITIVE else None
    gad2 = _total(answers, GAD2)

    prior = next((h for h in reversed(earlier) if h.get("phq8") is not None), None)
    previous = ({"answered_on": prior.get("answered_on") or _aware(prior["at"]).date().isoformat(),
                 "phq8": prior["phq8"]} if prior else None)
    change = phq8 - prior["phq8"] if phq8 is not None and prior else None
    last = earlier[-1] if earlier else None

    flags = []
    if phq8 is not None and phq8 >= PHQ8_MODERATE and not (last and (last.get("phq8") or 0) >= PHQ8_MODERATE):
        flags.append("phq8_moderate")
    if change is not None and change >= PHQ8_RISE:
        flags.append("phq8_rise")
    low_mood = (phq2 is not None and phq2 >= PHQ2_POSITIVE) or (phq8 is not None and phq8 >= PHQ8_MODERATE)
    events = sorted(set(trouble or []))
    if low_mood and events:
        flags.append("low_mood_trouble")
    if gad2 is not None and gad2 >= GAD2_POSITIVE:
        flags.append("gad2_positive")

    return {
        "answers": dict(answers),
        "phq2": phq2,
        "phq8": phq8,
        "gad2": gad2,
        "phq8_band": band(phq8),
        "previous": previous,
        "change": change,
        "trouble": events if low_mood else [],
        "flags": flags,
        **({"simulated": True} if simulated else {}),
    }


def triggers(mood: Optional[dict]) -> list:
    """
    The stored flags as plain flag objects for the care team. Wording is
    co-occurrence only: it never says the mood caused anything, and the
    low-mood flag carries scores, never the medicine.
    """
    if not mood:
        return []
    phq2, phq8, gad2 = mood.get("phq2"), mood.get("phq8"), mood.get("gad2")
    prev = mood.get("previous") or {}
    out = []
    for code in mood.get("flags") or []:
        if code not in SEVERITY:
            continue
        if code == "phq8_moderate":
            title = f"Mood screen moderate or worse (PHQ-8 {phq8})"
            detail = (f"PHQ-8 {phq8} ({mood.get('phq8_band')}). Flagged when a screen reaches 10 or more "
                      "after one below 10, or on the first screen.")
            rationale = ("A PHQ-8 of 10 or more is the usual cut-off for depression worth assessing. "
                         "Low mood often goes with missed doses and stopping treatment early; "
                         "ask how they are feeling and whether they would like support.")
        elif code == "phq8_rise":
            title = f"Mood worse by {mood.get('change')} points since last time"
            detail = f"PHQ-8 {phq8}, up from {prev.get('phq8')} on {prev.get('answered_on')}."
            rationale = "A rise of 5 or more points on the PHQ-8 is the usual threshold for a change that matters."
        elif code == "low_mood_trouble":
            title = "Low mood with trouble taking the medicine"
            detail = f"PHQ-2 {phq2}" + (f", PHQ-8 {phq8}" if phq8 is not None else "") + "."
            rationale = "Low mood and trouble keeping up with treatment often go together; ask about both."
        else:
            title = f"Anxiety screen positive (GAD-2 {gad2})"
            detail = f"GAD-2 {gad2} of 6."
            rationale = ("A GAD-2 of 3 or more is the usual cut-off for anxiety worth asking about. "
                         "Shown for the care team; on its own it does not ring the bell.")
        out.append({"code": code, "severity": SEVERITY[code],
                    "title": title, "detail": detail, "rationale": rationale})
    return out


def _screen_text(total: Optional[int], answered_any: bool, positive: bool, more: str = "") -> Optional[str]:
    if total is not None:
        return f"{total} · {'positive' if positive else 'negative'} screen{more}"
    return "not all answered" if answered_any else None


def card(mood: Optional[dict]) -> Optional[dict]:
    """What the staff card shows: a few plain lines and the flags. Never the item answers."""
    if not mood:
        return None
    answers = mood.get("answers") or {}
    phq2, phq8, gad2 = mood.get("phq2"), mood.get("phq8"), mood.get("gad2")
    lines = []
    if phq8 is not None:
        change = mood.get("change")
        suffix = ("" if change is None else
                  f" · up {change} since last time" if change > 0 else
                  f" · down {-change} since last time" if change < 0 else
                  " · same as last time")
        lines.append({"key": "phq8", "label": "Mood (PHQ-8)", "positive": phq8 >= PHQ8_MODERATE,
                      "text": f"{phq8} · {mood.get('phq8_band')}{suffix}"})
    else:
        text = _screen_text(
            phq2, any(k in answers for k in PHQ2), phq2 is not None and phq2 >= PHQ2_POSITIVE,
            " - the follow-up questions were not all answered" if phq2 is not None and phq2 >= PHQ2_POSITIVE else "")
        if text:
            lines.append({"key": "phq2", "label": "Mood (PHQ-2)",
                          "positive": phq2 is not None and phq2 >= PHQ2_POSITIVE, "text": text})
    text = _screen_text(gad2, any(k in answers for k in GAD2), gad2 is not None and gad2 >= GAD2_POSITIVE)
    if text:
        lines.append({"key": "gad2", "label": "Anxiety (GAD-2)",
                      "positive": gad2 is not None and gad2 >= GAD2_POSITIVE, "text": text})
    return {"lines": lines, "flags": triggers(mood), "simulated": bool(mood.get("simulated")),
            "answered_on": mood.get("answered_on")}

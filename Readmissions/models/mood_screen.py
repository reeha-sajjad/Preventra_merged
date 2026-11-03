"""
Mental and behavioural health screen in the weekly check-in. FLAG ONLY.

The patient answers the PHQ-2 (and, when it is positive, the rest of the PHQ-8)
and the GAD-2. The answers are stored as `mood` BESIDE `monitoring` on the week
- never inside it - and they never reach `score_week`, the risk score, the band
or any weekly model input: `features/weekly_rows.flatten` drops the column so a
trained or deployed weekly model, and the Model Studio export, are unchanged.

What the answers DO is raise flags. `triggers` turns them into forecast triggers
(models/early_warning.py adds them after the corroboration rule, so a mood screen
is never a second clinical stream that makes a vitals alert same-day, and is
never critical itself), and `card` is what the week card shows. A screen is not
a diagnosis, and a flag here says low mood and missed doses went together, not
that one caused the other.

The screen is the PHQ-8, the eight-question form suited to self-report at home.
The section always carries a plain safety line (`SAFETY`): contact the care
team if they feel unwell or unsafe, or call 911 in an emergency.

A skipped item is nothing, never a 0, and a total exists only when every item in
it was answered. The PHQ's two-week recall overlaps from one week to the next, so
the same low mood can show in two consecutive screens; the "first time" and
"change" rules below are written with that in mind.
"""

from __future__ import annotations

from typing import Optional

STREAM = "mood"

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
LOW_ADHERENCE = 80     # percent of doses taken this week

# PHQ-8 total -> band, as the upper edge of each.
BANDS = ((4, "minimal"), (9, "mild"), (14, "moderate"), (19, "moderately severe"), (24, "severe"))

# Never "critical": a mood screen is a reason to ask, not an emergency. The
# anxiety screen is shown but does not page anyone on its own.
SEVERITY = {"phq8_moderate": "high", "phq8_rise": "high", "low_mood_adherence": "high",
            "gad2_positive": "moderate"}

GROUP_NOTES = {
    "heart_failure": "In heart failure, low mood is a common reason patients stop weighing themselves and taking their water tablets.",
    "cardiac_other": "Low mood after a heart event is common and often goes with missed doses and missed cardiac rehab.",
    "renal": "In kidney disease, low mood often goes with missed doses and fluid advice that is harder to keep to.",
    "respiratory": "In chronic lung disease, low mood often goes with inhalers used less and breathlessness under-reported.",
    "diabetes": "In diabetes, low mood often goes with missed tablets or insulin and skipped sugar checks.",
    "neuro_stroke": "Low mood is common after a stroke and often goes with less rehab and fewer medicines taken.",
    "oncology": "Low mood is common during cancer treatment and often goes with missed appointments.",
    "mental_health": "This patient was admitted for a mental health condition: tell their mental health team as well.",
}


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
    to lose the check-in.
    """
    given = {k: v for k, v in (answers or {}).items() if v is not None and v != ""}
    unknown = sorted(k for k in given if k not in ITEMS)
    if unknown:
        raise ValueError("Not asked this week: " + ", ".join(unknown))
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


def assess(answers: dict, history: list, week_number: int,
           adherence_pct: Optional[float] = None, simulated: bool = False) -> Optional[dict]:
    """
    Totals and flags for one week's cleaned answers.

    history  earlier mood dicts of this discharge; anything not before
             `week_number` is ignored, so re-scoring a week never compares it
             with its own future
    """
    if not answers:
        return None
    earlier = sorted((h for h in history or []
                      if h and h.get("week_number") is not None and h["week_number"] < week_number),
                     key=lambda h: h["week_number"])

    phq2 = _total(answers, PHQ2)
    phq8 = _total(answers, list(PHQ2) + list(PHQ8_MORE)) if phq2 is not None and phq2 >= PHQ2_POSITIVE else None
    gad2 = _total(answers, GAD2)

    prior = next((h for h in reversed(earlier) if h.get("phq8") is not None), None)
    previous = {"week_number": prior["week_number"], "phq8": prior["phq8"]} if prior else None
    change = phq8 - prior["phq8"] if phq8 is not None and prior else None

    flags = []
    if phq8 is not None and phq8 >= PHQ8_MODERATE \
            and not any((h.get("phq8") or 0) >= PHQ8_MODERATE for h in earlier):
        flags.append("phq8_moderate")
    if change is not None and change >= PHQ8_RISE:
        flags.append("phq8_rise")
    low_mood = (phq2 is not None and phq2 >= PHQ2_POSITIVE) or (phq8 is not None and phq8 >= PHQ8_MODERATE)
    if low_mood and adherence_pct is not None and float(adherence_pct) < LOW_ADHERENCE:
        flags.append("low_mood_adherence")
    if gad2 is not None and gad2 >= GAD2_POSITIVE:
        flags.append("gad2_positive")

    return {
        "week_number": week_number,
        "answers": dict(answers),
        "phq2": phq2,
        "phq8": phq8,
        "gad2": gad2,
        "phq8_band": band(phq8),
        "previous": previous,
        "change": change,
        "adherence_pct": float(adherence_pct) if adherence_pct is not None else None,
        "flags": flags,
        **({"simulated": True} if simulated else {}),
    }


def triggers(mood: Optional[dict], group: str = "general") -> list:
    """
    The stored flags as forecast triggers (the shape early_warning._flag makes).
    Wording is co-occurrence only: it never says the mood caused anything.
    """
    if not mood:
        return []
    note = GROUP_NOTES.get(group)
    phq2, phq8, gad2 = mood.get("phq2"), mood.get("phq8"), mood.get("gad2")
    prev = mood.get("previous") or {}
    out = []
    for code in mood.get("flags") or []:
        if code not in SEVERITY:
            continue
        if code == "phq8_moderate":
            title = f"Mood screen moderate or worse (PHQ-8 {phq8})"
            detail = f"PHQ-8 {phq8} ({mood.get('phq8_band')}), the first time at 10 or more since discharge."
            rationale = ("A PHQ-8 of 10 or more is the usual cut-off for depression worth assessing. "
                         "Low mood after discharge often goes with missed doses and missed appointments; "
                         "ask how they are feeling and whether they would like support.")
        elif code == "phq8_rise":
            title = f"Mood worse by {mood.get('change')} points since last time"
            detail = f"PHQ-8 {phq8}, up from {prev.get('phq8')} in week {prev.get('week_number')}."
            rationale = "A rise of 5 or more points on the PHQ-8 is the usual threshold for a change that matters."
        elif code == "low_mood_adherence":
            title = "Low mood and missed doses in the same week"
            more = f", PHQ-8 {phq8}" if phq8 is not None else ""
            detail = f"PHQ-2 {phq2}{more}; {mood.get('adherence_pct'):g}% of doses taken this week."
            rationale = "Low mood and missed doses often go together; ask about both."
        else:
            title = f"Anxiety screen positive (GAD-2 {gad2})"
            detail = f"GAD-2 {gad2} of 6."
            rationale = ("A GAD-2 of 3 or more is the usual cut-off for anxiety worth asking about. "
                         "Shown for the care team; on its own it does not page anyone.")
        if note and code != "gad2_positive":
            rationale = f"{rationale} {note}"
        out.append({"code": code, "stream": STREAM, "severity": SEVERITY[code],
                    "title": title, "detail": detail, "rationale": rationale})
    return out


def _screen_text(total: Optional[int], answered_any: bool, positive: bool, more: str = "") -> Optional[str]:
    if total is not None:
        return f"{total} · {'positive' if positive else 'negative'} screen{more}"
    return "not all answered" if answered_any else None


def card(mood: Optional[dict], group: str = "general") -> Optional[dict]:
    """What the week card shows: a few plain lines and the flags."""
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
    return {"lines": lines, "flags": triggers(mood, group), "simulated": bool(mood.get("simulated"))}

"""
Early-warning forecast over the weekly monitoring series.

WHAT THIS IS, AND WHAT IT IS NOT
--------------------------------
This is NOT a trained model and does not claim to be one. No public dataset
carries weekly post-discharge observations against readmission outcomes at the
cadence this would need to be fitted - scripts/mimic_feasibility.py measures
that directly against MIMIC's `omr` table and prints the verdict. Fitting a
model to simulated weeks would only learn the simulator.

What this is instead, and what it can honestly claim:

  1. LINEAR PROJECTION of the patient's own observed score series. Arithmetic
     on real numbers. It says where the trend points, not what will happen.

  2. CONDITION-SPECIFIC RED FLAGS on the week's observations, using published
     clinical thresholds - a >2 kg weekly gain in heart failure, SpO2 below 92,
     a temperature over 38 after a sepsis admission. Each flag carries its own
     rationale so a clinician can disagree with it individually.

  3. AN ENGAGEMENT PATTERN read across the non-clinical signals, because a
     patient who has stopped collecting prescriptions and stopped attending
     follow-up is a well-described readmission precursor.

The value is the LEAD TIME. The existing alerting is reactive: it fires once a
score has already crossed into a higher band. This fires while the score is
still Medium and heading for High, or when the vitals show a condition-specific
pattern before the score has moved at all.

ALERTING THRESHOLDS ARE NOT SCORING WEIGHTS
-------------------------------------------
models/monitoring_rules.py answers "how much worse is this week". This module
answers a different question: "is this worth interrupting a clinician for".
Those are different jobs and deliberately have different numbers. A signal can
move the score a little every week without ever being worth a phone call, and a
single reading can be worth a phone call without moving the score much at all.
Keeping the two tables separate is what lets either be tuned without silently
breaking the other.

WHEN THERE IS NOT ENOUGH DATA
-----------------------------
A slope needs at least two points. With one week of monitoring the honest
answer is "not enough history yet", and that is what this returns - not a
projection drawn through a single observation.
"""

from __future__ import annotations

from typing import Optional

# Band edges. Mirrors docs/mimic/band_thresholds_mimic.json; passed in by the
# caller in production so the two can never drift.
DEFAULT_BANDS = {"high_score_threshold": 40.0, "low_score_threshold": 20.0}

# How many recent weeks the slope is fitted over. Three is a compromise: two is
# hostage to a single noisy reading, and more than three lets a patient who has
# already recovered keep dragging their own forecast down.
TREND_WINDOW = 3

# Weeks ahead to project. Two weeks is the useful horizon for this programme -
# far enough to act on, close enough that a straight line is still a defensible
# approximation of a trajectory.
DEFAULT_HORIZON_WEEKS = 2

# Below this the slope is noise, not a trend. Matches the band the weekly trend
# panel already treats as flat, so the two never disagree on screen.
VELOCITY_NOISE_BAND = 1.5

# A curve bending upward by at least this much per week is accelerating rather
# than merely rising. Set well above VELOCITY_NOISE_BAND: this is the difference
# between two slopes, so it carries the noise of both, and a lower bar fires on
# most patients whose weekly scores wobble at all.
ACCELERATION_NOISE_BAND = 5.0

SEVERITY_ORDER = ("none", "moderate", "high", "critical")

REVIEW_BY = {
    "critical": "same day",
    "high": "within 48 hours",
    "moderate": "at the next scheduled contact",
    "none": "no review needed",
}


def band_for(score: float, bands: Optional[dict] = None) -> str:
    b = bands or DEFAULT_BANDS
    if score >= b.get("high_score_threshold", 40.0):
        return "High"
    if score >= b.get("low_score_threshold", 20.0):
        return "Medium"
    return "Low"


def _escalate(severity: str, steps: int = 1) -> str:
    i = SEVERITY_ORDER.index(severity)
    return SEVERITY_ORDER[min(i + steps, len(SEVERITY_ORDER) - 1)]


def _worst(severities) -> str:
    worst = "none"
    for s in severities:
        if SEVERITY_ORDER.index(s) > SEVERITY_ORDER.index(worst):
            worst = s
    return worst


def _slope(points: list) -> float:
    """
    Least-squares slope in score-points per week.

    Weeks are used as the x axis rather than the index, so a gap in reporting
    flattens the slope instead of being silently treated as a week that
    happened. A patient who missed two weeks has not been deteriorating at the
    same rate through the silence.
    """
    n = len(points)
    if n < 2:
        return 0.0
    mean_x = sum(x for x, _ in points) / n
    mean_y = sum(y for _, y in points) / n
    num = sum((x - mean_x) * (y - mean_y) for x, y in points)
    den = sum((x - mean_x) ** 2 for x, _ in points)
    return 0.0 if den == 0 else num / den


def _flag(code, stream, severity, title, detail, rationale) -> dict:
    return {"code": code, "stream": stream, "severity": severity,
            "title": title, "detail": detail, "rationale": rationale}


def _red_flag_trigger(obs: dict, triggers: list) -> list:
    """
    A reading the weekly check calls a red flag - "assess today" on the
    patient's card (models/monitoring_rules.RED_FLAGS) - is a same-day alert
    here too. The two disagreed: a saturation of 89% was a red flag on the card
    and only "moderate" here, so no alert was raised for the week it appeared.
    Added only when nothing already makes the week critical, so the same
    reading is not listed twice.
    """
    from models.monitoring_rules import RED_FLAGS, red_flags
    flagged = red_flags(obs)
    if not flagged or any(t["severity"] == "critical" for t in triggers):
        return []
    labels = [RED_FLAGS[k][1] for k in flagged]
    return [_flag(
        "red_flag", "vitals", "critical",
        "Red flag on this week's readings: " + "; ".join(labels),
        "; ".join(f"{k.replace('_', ' ')} {obs.get(k)}" for k in flagged) + ".",
        "The weekly check marks this reading for assessment today, so the alert is same-day "
        "whatever the trend.")]


# ---------------------------------------------------------------------------
# Condition-specific red flags
# ---------------------------------------------------------------------------
# Each check reads the current week's observations, and where the pattern needs
# it, the previous week's too. Thresholds are the published clinical ones; the
# rationale on each flag is what a clinician would argue with.
#
# The direction of a signal is not universal, which is the whole reason these
# are keyed by condition. A 2 kg weekly weight GAIN is the classic heart-failure
# decompensation signal. The same 2 kg as a LOSS is the thing to chase in an
# oncology patient, and means very little in someone recovering from surgery.

def _universal_flags(obs: dict, prev: Optional[dict]) -> list:
    flags = []
    sbp = obs.get("sbp")
    spo2 = obs.get("spo2")
    hr = obs.get("heart_rate")
    adherence = obs.get("adherence_pct")
    refill = obs.get("refill_status")
    followup = obs.get("followup_status")

    if sbp is not None and sbp < 90:
        flags.append(_flag(
            "hypotension", "vitals", "critical",
            "Systolic blood pressure below 90",
            f"Systolic {sbp:g} mmHg.",
            "Hypotension after discharge can indicate sepsis, bleeding, dehydration "
            "or over-diuresis, and none of those wait for a scheduled appointment."))
    if spo2 is not None:
        if spo2 <= 88:
            flags.append(_flag(
                "severe_hypoxia", "vitals", "critical",
                "Oxygen saturation at or below 88%",
                f"SpO2 {spo2:g}%.",
                "Saturation this low is an emergency threshold in most oxygen-therapy "
                "guidance regardless of the admitting diagnosis."))
        elif spo2 <= 92:
            # Deliberately moderate rather than high. 92% is common and often
            # normal for this patient - plenty of older and chronically
            # short-of-breath people live there - and no baseline saturation is
            # recorded to compare against. The respiratory profile escalates its
            # own threshold below, where the diagnosis makes it meaningful.
            flags.append(_flag(
                "hypoxia", "vitals", "moderate",
                "Oxygen saturation at or below 92%",
                f"SpO2 {spo2:g}%.",
                "Below 92% is the usual trigger for clinical assessment, though it "
                "may be this patient's baseline - no pre-discharge saturation is "
                "recorded to compare against."))
    if hr is not None and hr >= 120:
        flags.append(_flag(
            "tachycardia", "vitals", "high",
            "Resting heart rate at or above 120",
            f"Heart rate {hr:g} bpm.",
            "Sustained tachycardia at rest is a non-specific but early marker of "
            "infection, dehydration, arrhythmia or decompensation."))

    # The disengagement pattern. Individually these are administrative; together
    # they describe a patient who has come off the programme, which is one of
    # the better-established readmission precursors.
    disengaged = [
        adherence is not None and adherence < 50,
        refill == "missed",
        followup == "missed",
    ]
    if sum(bool(x) for x in disengaged) >= 2:
        flags.append(_flag(
            "disengagement", "engagement", "high",
            "Patient has disengaged from the discharge plan",
            f"Adherence {adherence if adherence is not None else 'unknown'}%, "
            f"refill {refill or 'unknown'}, follow-up {followup or 'unknown'}.",
            "Missed medication, missed refills and missed follow-up occurring together "
            "describe a patient who is no longer on the plan, not three separate "
            "administrative lapses."))

    if prev and adherence is not None:
        before = prev.get("adherence_pct")
        if before is not None and before - adherence >= 30:
            flags.append(_flag(
                "adherence_collapse", "engagement", "high",
                "Sharp fall in medication adherence",
                f"Adherence fell from {before:g}% to {adherence:g}% in one week.",
                "A sudden drop is a different event from chronically poor adherence. "
                "Something changed this week - a side effect, a cost, a new confusion."))
    return flags


def _group_flags(group: str, obs: dict, prev: Optional[dict]) -> list:
    flags = []
    weight = obs.get("weight_change_kg")
    sbp = obs.get("sbp")
    spo2 = obs.get("spo2")
    temp = obs.get("temperature_c")
    adherence = obs.get("adherence_pct")
    followup = obs.get("followup_status")
    prev_weight = (prev or {}).get("weight_change_kg")

    if group == "heart_failure":
        if weight is not None and weight >= 2.0:
            flags.append(_flag(
                "hf_fluid_overload", "vitals", "critical",
                "Weight gain over 2 kg in one week",
                f"Weight change {weight:+.1f} kg.",
                "A gain this fast is fluid, not tissue. It is the earliest and most "
                "treatable decompensation signal in heart failure and typically "
                "precedes breathlessness by several days."))
        elif (weight is not None and prev_weight is not None
              and weight >= 1.0 and prev_weight >= 1.0):
            flags.append(_flag(
                "hf_cumulative_gain", "vitals", "high",
                "Steady weight gain across two consecutive weeks",
                f"{prev_weight:+.1f} kg then {weight:+.1f} kg.",
                "Neither week alone crosses the 2 kg threshold, but the cumulative "
                "gain does, and the direction has been consistent."))
        if obs.get("orthopnoea_pillows") is not None and obs["orthopnoea_pillows"] >= 3:
            flags.append(_flag(
                "hf_orthopnoea", "symptoms", "high",
                "Sleeping propped on three or more pillows",
                f"{obs['orthopnoea_pillows']:g} pillows.",
                "Orthopnoea is congestion the patient can feel, and usually appears "
                "after the weight has already moved."))
        if obs.get("ankle_swelling") in ("worse", "new"):
            flags.append(_flag(
                "hf_oedema", "symptoms", "moderate",
                f"Ankle swelling reported as {obs['ankle_swelling']}",
                f"Ankle swelling: {obs['ankle_swelling']}.",
                "Peripheral oedema corroborates a fluid trend but is a late and "
                "unreliable signal on its own."))

    elif group == "renal":
        if weight is not None and sbp is not None and weight >= 2.0 and sbp >= 160:
            flags.append(_flag(
                "renal_overload_hypertensive", "vitals", "critical",
                "Fluid gain with uncontrolled blood pressure",
                f"Weight {weight:+.1f} kg with systolic {sbp:g} mmHg.",
                "Volume overload and hypertension together after a renal admission "
                "suggest the patient is retaining fluid faster than it is cleared."))
        elif sbp is not None and sbp >= 180:
            flags.append(_flag(
                "renal_severe_hypertension", "vitals", "high",
                "Systolic blood pressure at or above 180",
                f"Systolic {sbp:g} mmHg.",
                "Sustained severe hypertension accelerates renal decline and raises "
                "immediate cardiovascular risk."))

    elif group == "respiratory":
        if spo2 is not None and spo2 <= 90:
            flags.append(_flag(
                "resp_desaturation", "vitals", "critical",
                "Oxygen saturation at or below 90% after a respiratory admission",
                f"SpO2 {spo2:g}%.",
                "In a patient admitted for lung disease this is the reading most "
                "likely to end in a return to hospital if left for a week."))
        if obs.get("rescue_inhaler_uses") is not None and obs["rescue_inhaler_uses"] >= 8:
            flags.append(_flag(
                "resp_rescue_overuse", "symptoms", "high",
                "Heavy reliance on rescue inhaler",
                f"{obs['rescue_inhaler_uses']:g} uses per day.",
                "Escalating reliever use is the standard marker of losing control, "
                "and it rises before saturation falls."))
        if obs.get("sputum_change") in ("purulent", "increased"):
            flags.append(_flag(
                "resp_sputum", "symptoms", "high",
                f"Sputum reported as {obs['sputum_change']}",
                f"Sputum change: {obs['sputum_change']}.",
                "A change in sputum colour or volume is a recognised exacerbation "
                "criterion and often the first thing the patient notices."))

    elif group == "sepsis_infection":
        if temp is not None and temp >= 38.0:
            flags.append(_flag(
                "sepsis_fever_return", "vitals", "critical",
                "Temperature at or above 38 °C after a sepsis admission",
                f"Temperature {temp:.1f} °C.",
                "Recurrent fever after treatment for sepsis is treated as relapse "
                "until proven otherwise."))
        if obs.get("new_confusion"):
            flags.append(_flag(
                "sepsis_confusion", "symptoms", "critical",
                "New confusion reported",
                "New confusion since last week.",
                "New confusion is part of every sepsis screening tool and can be the "
                "only sign in older patients, who often do not mount a fever."))
        if obs.get("antibiotic_course") in ("stopped early", "incomplete"):
            flags.append(_flag(
                "sepsis_course_incomplete", "engagement", "high",
                "Antibiotic course not completed",
                f"Antibiotic course: {obs['antibiotic_course']}.",
                "An unfinished course after a serious infection is a direct and "
                "avoidable route back to hospital."))

    elif group == "oncology":
        # The direction of the weight rule inverts here. This is the clearest
        # case for why these flags are keyed by condition at all.
        if weight is not None and weight <= -2.0:
            flags.append(_flag(
                "onc_weight_loss", "vitals", "high",
                "Weight loss over 2 kg in one week",
                f"Weight change {weight:+.1f} kg.",
                "Rapid loss during treatment indicates poor intake or progressive "
                "cachexia, both of which predict poor tolerance of the next cycle. "
                "Note the direction is the opposite of the heart-failure rule."))
        if temp is not None and temp >= 38.0:
            flags.append(_flag(
                "onc_neutropenic_fever", "vitals", "critical",
                "Fever during cancer treatment",
                f"Temperature {temp:.1f} °C.",
                "Fever in a patient who may be neutropenic is a medical emergency "
                "and is managed as such until the count is known."))

    elif group == "diabetes":
        if adherence is not None and adherence < 60:
            flags.append(_flag(
                "dm_adherence", "engagement", "high",
                "Medication adherence below 60%",
                f"Adherence {adherence:g}%.",
                "Glycaemic control degrades quickly off-regimen, and the resulting "
                "admissions are among the most preventable in this cohort."))

    elif group == "neuro_stroke":
        if sbp is not None and sbp >= 180:
            flags.append(_flag(
                "stroke_hypertension", "vitals", "critical",
                "Systolic blood pressure at or above 180 after a stroke",
                f"Systolic {sbp:g} mmHg.",
                "Blood pressure control is the single largest modifiable factor in "
                "preventing a second stroke, and this is well outside target."))
        if adherence is not None and adherence < 70:
            flags.append(_flag(
                "stroke_adherence", "engagement", "high",
                "Adherence below 70% on secondary-prevention medication",
                f"Adherence {adherence:g}%.",
                "Antiplatelet and anticoagulant cover is not partially effective. "
                "Gaps in it translate fairly directly into recurrence risk."))

    elif group == "surgical_injury":
        if obs.get("wound_status") in ("infected", "discharging", "dehiscence"):
            flags.append(_flag(
                "surg_wound", "symptoms", "critical",
                f"Wound reported as {obs['wound_status']}",
                f"Wound status: {obs['wound_status']}.",
                "Surgical site infection is the leading cause of readmission after "
                "an operation and worsens quickly without review."))
        if temp is not None and temp >= 38.0:
            flags.append(_flag(
                "surg_fever", "vitals", "high",
                "Temperature at or above 38 °C after surgery",
                f"Temperature {temp:.1f} °C.",
                "Post-operative fever needs a source identified rather than a week "
                "of watchful waiting."))
        if obs.get("pain_trend") == "worse":
            flags.append(_flag(
                "surg_pain", "symptoms", "moderate",
                "Pain reported as worsening",
                "Pain trend: worse.",
                "Pain that increases rather than settles is often the first report "
                "of a wound or hardware problem."))

    elif group == "mental_health":
        if followup == "missed":
            flags.append(_flag(
                "mh_missed_contact", "engagement", "high",
                "Missed follow-up contact",
                "Follow-up: missed.",
                "For this group contact is the intervention, not a check on it. A "
                "missed appointment is a clinical event."))
        if adherence is not None and adherence < 60:
            flags.append(_flag(
                "mh_adherence", "engagement", "high",
                "Medication adherence below 60%",
                f"Adherence {adherence:g}%.",
                "Relapse after stopping psychiatric medication typically follows "
                "weeks rather than days, which makes this the window to act in."))

    elif group == "cardiac_other":
        hr = obs.get("heart_rate")
        if hr is not None and (hr >= 120 or hr <= 45):
            flags.append(_flag(
                "cardiac_rate", "vitals", "high",
                "Heart rate outside the safe range",
                f"Heart rate {hr:g} bpm.",
                "Rate and rhythm control is the point of the discharge regimen, and "
                "both extremes carry their own risk."))
        if sbp is not None and sbp >= 180:
            flags.append(_flag(
                "cardiac_hypertension", "vitals", "high",
                "Systolic blood pressure at or above 180",
                f"Systolic {sbp:g} mmHg.",
                "Severe hypertension raises the immediate risk of a further "
                "cardiac event."))

    return flags


# ---------------------------------------------------------------------------
# The forecast
# ---------------------------------------------------------------------------

def forecast(weeks: list, group: str = "general",
             horizon_weeks: int = DEFAULT_HORIZON_WEEKS,
             bands: Optional[dict] = None) -> dict:
    """
    Project a patient's risk forward and check this week against condition-specific
    red flags.

    weeks         weekly monitoring documents, ascending by week_number. Each needs
                  `risk_score` and `week_number`; `monitoring` carries observations.
    group         the patient's clinical group from models/icd_groups
    horizon_weeks how far ahead to project
    bands         band thresholds; defaults to the product bands

    Returns a dict that is always safe to render. When there is too little history
    to project, `status` is "insufficient_history" and the red flags are still
    evaluated - a single alarming week is worth acting on even with no trend.
    """
    bands = bands or DEFAULT_BANDS
    horizon_weeks = max(1, int(horizon_weeks))

    observed = [w for w in weeks or [] if w.get("risk_score") is not None]
    observed.sort(key=lambda w: w.get("week_number", 0))

    if not observed:
        return {
            "status": "no_data",
            "severity": "none",
            "triggers": [],
            "confidence": "low",
            "confidence_reason": "No weekly monitoring has been recorded for this patient.",
            "basis": _BASIS,
        }

    current = observed[-1]
    previous = observed[-2] if len(observed) >= 2 else None
    obs = current.get("monitoring") or {}
    prev_obs = (previous or {}).get("monitoring") or {}

    current_score = float(current["risk_score"])
    current_week = int(current.get("week_number", len(observed) - 1))

    triggers = _universal_flags(obs, prev_obs) + _group_flags(group, obs, prev_obs)
    triggers += _red_flag_trigger(obs, triggers)

    # ---- trajectory --------------------------------------------------------
    window = observed[-TREND_WINDOW:]
    points = [(int(w.get("week_number", i)), float(w["risk_score"]))
              for i, w in enumerate(window)]
    has_trend = len(points) >= 2

    velocity = _slope(points) if has_trend else 0.0
    projected_score = max(0.0, min(100.0, current_score + velocity * horizon_weeks))
    projected_band = band_for(projected_score, bands)
    current_band = band_for(current_score, bands)

    # Acceleration: is the recent slope steeper than the one before it? Compared
    # over the same number of points so the two are like for like.
    acceleration = 0.0
    if len(observed) >= 4:
        recent = [(int(w.get("week_number", i)), float(w["risk_score"]))
                  for i, w in enumerate(observed[-3:])]
        earlier = [(int(w.get("week_number", i)), float(w["risk_score"]))
                   for i, w in enumerate(observed[-4:-1])]
        acceleration = _slope(recent) - _slope(earlier)

    band_crossing = None
    if has_trend and velocity > VELOCITY_NOISE_BAND:
        for label, edge in (("High", bands.get("high_score_threshold", 40.0)),
                            ("Medium", bands.get("low_score_threshold", 20.0))):
            if current_score < edge <= projected_score:
                weeks_to_edge = (edge - current_score) / velocity
                band_crossing = {
                    "to_band": label,
                    "from_band": current_band,
                    "crosses_at_week": current_week + max(1, round(weeks_to_edge)),
                    "lead_time_days": max(1, int(round(weeks_to_edge * 7))),
                }
                break

    if band_crossing:
        # Never critical on its own, whichever band it is heading for. "Critical"
        # means review the patient today, and a projection with a week of lead
        # time is by definition not that - the lead time is the entire product.
        # Corroboration by an observed red flag can still escalate it below.
        triggers.append(_flag(
            "projected_band_crossing", "trajectory",
            "high" if band_crossing["to_band"] == "High" else "moderate",
            f"On track to reach {band_crossing['to_band']} risk in about "
            f"{band_crossing['lead_time_days']} days",
            f"Rising {velocity:+.1f} points per week from {current_score:.1f}; "
            f"projected {projected_score:.1f} by week {current_week + horizon_weeks}.",
            "The score has not crossed yet. Acting now is the entire point of "
            "forecasting rather than waiting for the threshold."))
    elif has_trend and velocity > VELOCITY_NOISE_BAND:
        triggers.append(_flag(
            "rising_trajectory", "trajectory", "moderate",
            f"Risk rising {velocity:+.1f} points per week",
            f"{current_score:.1f} now, projected {projected_score:.1f} by week "
            f"{current_week + horizon_weeks}.",
            "A consistent rise inside the same band still describes a patient "
            "moving in the wrong direction."))

    # Acceleration only means something while the trend is going the wrong way.
    # A patient recovering less quickly this week than last has a positive
    # second derivative and is still getting better; calling that "deterioration
    # is accelerating" is simply wrong, and it was the single largest source of
    # alerts before this guard.
    if acceleration > ACCELERATION_NOISE_BAND and velocity > VELOCITY_NOISE_BAND:
        triggers.append(_flag(
            "accelerating", "trajectory", "high",
            "Deterioration is accelerating",
            f"Weekly rise steepened by {acceleration:+.1f} points compared with "
            f"the preceding weeks.",
            "A curve bending upward is a different clinical picture from a steady "
            "climb, and it tends to be the one that ends in an admission."))

    # ---- confidence --------------------------------------------------------
    # Stated separately from severity on purpose. A single alarming vital sign
    # can be critical on its first reading; that does not make the TREND
    # trustworthy, and conflating the two would overstate the projection.
    reported = len([k for k, v in obs.items() if v is not None])
    if len(observed) >= 3 and reported >= 5:
        confidence, reason = "high", (
            f"{len(observed)} weeks of history and {reported} signals reported this week.")
    elif len(observed) >= 2:
        confidence, reason = "moderate", (
            f"Only {len(observed)} weeks of history, so the slope is provisional.")
    else:
        confidence, reason = "low", (
            "One week of monitoring only. Red flags still apply, but no trend can "
            "be drawn through a single point.")

    # ---- severity ----------------------------------------------------------
    severity = _worst([t["severity"] for t in triggers])
    streams = {t["stream"] for t in triggers}

    # Independent streams agreeing is evidence, but only when each of them is
    # saying something substantial. Counting a moderate trigger as corroboration
    # meant any mild finding alongside a mild trend produced a critical alert,
    # which is how an inbox fills up with things nobody needs to see. Both
    # streams must carry a trigger of at least "high" to escalate.
    serious_streams = {t["stream"] for t in triggers
                       if SEVERITY_ORDER.index(t["severity"]) >= SEVERITY_ORDER.index("high")}
    corroborated = len(serious_streams) >= 2
    if corroborated:
        severity = _escalate(severity)

    triggers.sort(key=lambda t: SEVERITY_ORDER.index(t["severity"]), reverse=True)

    return {
        "status": "ok" if has_trend else "insufficient_history",
        "as_of_week": current_week,
        "weeks_observed": len(observed),
        "current_score": round(current_score, 1),
        "current_band": current_band,
        "horizon_weeks": horizon_weeks,
        "velocity_per_week": round(velocity, 2),
        "acceleration": round(acceleration, 2),
        "projected_score": round(projected_score, 1),
        "projected_band": projected_band,
        "band_crossing": band_crossing,
        "severity": severity,
        "corroborated": corroborated,
        "streams": sorted(streams),
        "triggers": triggers,
        "confidence": confidence,
        "confidence_reason": reason,
        "recommended_review_by": REVIEW_BY[severity],
        "clinical_group": group,
        "basis": _BASIS,
    }


_BASIS = (
    "Linear projection of this patient's own weekly scores, combined with "
    "condition-specific clinical thresholds. Not a trained predictor - no "
    "dataset exists at this cadence to fit one against."
)

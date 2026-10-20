"""
AI-written clinical decision support for a single patient's risk verdict, by
whichever model serves the "insights" feature (api/llm.py):

  1. Return-on-investment estimate for enrolling the patient in a post-discharge
     care-coordination intervention (cost avoided vs. cost of the intervention).
  2. Counterfactual explanation - which risk driver(s), if addressed, would most
     plausibly lower the score, and roughly by how much.

The model is asked to return both as a single structured JSON object (schema
enforced) so the API never has to parse free-form text. Only the risk score,
band, driver list and dates already computed by the model are given to it -
never the patient's identifier - and it is told not to invent clinical facts
beyond what is provided.
"""

from api import llm

# The panel is button-triggered and shows an "Estimating..." state, so a slow
# call is visible rather than silent - but it must still be bounded (the
# feature's timeout in api/llm.py). Transient failures (a 504 from the
# provider, an overload) are tried again twice: a single attempt failed the
# whole panel often enough to matter.
_RETRIES = 2

_INSIGHTS_SYSTEM_INSTRUCTION = (
    "You are a clinical operations analyst supporting a hospital readmission-risk dashboard. "
    "Given one patient's readmission risk score, risk band, top clinical risk drivers, and an "
    "ALREADY-COMPUTED return-on-investment breakdown, write the narrative around those numbers "
    "and a counterfactual explanation, as structured JSON.\n\n"
    "The ROI figures are supplied to you. They were computed from an itemised cost model in the "
    "application, not by you. Do NOT recompute them, do not substitute your own cost estimates, "
    "and do not state any monetary figure that does not appear in the input. Your job for the ROI "
    "is to explain in plain language what the numbers mean for this specific patient, referring to "
    "their actual risk drivers.\n\n"
    "THE VERDICT IS ALREADY DECIDED. The input carries a decision line stating whether "
    "intervening in this patient is worth it. Not every patient has a case: below a stated "
    "break-even risk the intervention costs more than the readmission it would be expected to "
    "avert, and the net benefit figure is then NEGATIVE. When that is what the input says, your "
    "rationale must say plainly that enrolling this patient is not the right use of the "
    "programme's resources and that routine monitoring is the correct action. Never present a "
    "negative net benefit as a saving, never argue for intervening against the stated decision, "
    "and never describe a falling risk as a reason to escalate.\n\n"
    "Counterfactual explanation: identify which one or two of the LISTED drivers are most "
    "modifiable by a care team, and describe qualitatively how the risk would likely shift if "
    "those drivers were addressed. Only reason about drivers actually provided; never invent "
    "clinical facts not present in the input. Refer to the patient as 'the patient' or 'they'; "
    "the input does not state a gender, so never assume one.\n\n"
    "Return roi_rationale and counterfactual_explanation each as an array of 2 to 3 short bullet "
    "points. Each bullet point is one complete plain sentence written for a care coordinator. Do "
    "not use markdown, do not prefix bullets with any symbol, and do not use dashes or hyphens as "
    "sentence punctuation; write plain sentences using only commas and periods."
)

_INSIGHTS_SCHEMA = {
    "type": "object",
    "properties": {
        "roi_rationale": {
            "type": "array", "items": {"type": "string"},
            "description": "2-3 short plain-text bullet points explaining the supplied ROI figures.",
        },
        "counterfactual_explanation": {
            "type": "array", "items": {"type": "string"},
            "description": "2-3 short plain-text bullet points: what change would lower this patient's risk.",
        },
    },
    "required": ["roi_rationale", "counterfactual_explanation"],
}


def _driver_lines(drivers: list) -> str:
    """
    Render driver bullets for a prompt, naming the feed each value arrived on.

    The feed is part of the finding: "adherence 62%" from a pharmacy dispensing
    record and the same number typed into an app support different actions, and
    without the attribution the model has no way to tell them apart.
    """
    lines = []
    for d in drivers:
        if not d.get("label"):
            continue
        source = d.get("source") or {}
        via = ""
        if source.get("feed"):
            channel = source.get("channel", "")
            via = f"  [received from {source['feed']}{', ' + channel if channel else ''}]"
        lines.append(f"- {d.get('label', '')}: {d.get('value', '')} — "
                     f"{d.get('explanation', '')}{via}")
    return "\n".join(lines) or "- No structured drivers available."


def generate_roi_and_counterfactual(
    patient_id: str, risk_score: float, risk_band: str, drivers: list,
    roi: dict | None = None,
) -> dict:
    """
    Write the narrative around an already-computed ROI breakdown.

    `roi` comes from api.roi_model.compute_roi. Every monetary figure is fixed
    before this call: The model receives the itemisation and is instructed to
    explain it, not to price anything. It returns only roi_rationale and
    counterfactual_explanation, and the caller merges those into the ROI dict.

    Raises on any failure — the caller (main.py) turns that into a user-facing
    error response.
    """
    driver_lines = _driver_lines(drivers)

    if roi:
        cost_lines = "\n".join(
            f"  - {i['item']} ({i['basis']}): ${i['amount']:,.0f}"
            for i in roi["readmission_cost_items"]
        )
        spend_lines = "\n".join(
            f"  - {i['item']} ({i['basis']}): ${i['amount']:,.0f}"
            for i in roi["intervention_cost_items"]
        )
        roi_block = (
            "\nCOMPUTED ROI BREAKDOWN — use these figures verbatim, do not recompute:\n"
            f"Cost of one readmission episode, ${roi['estimated_readmission_cost_usd']:,.0f}, made up of:\n"
            f"{cost_lines}\n"
            f"Cost of delivering the intervention to one patient, ${roi['estimated_intervention_cost_usd']:,.0f}, made up of:\n"
            f"{spend_lines}\n"
            f"Exposure at this patient's risk: ${roi['gross_exposure_usd']:,.0f}\n"
            f"Assumed intervention effectiveness: {roi['intervention_effectiveness']:.0%}\n"
            f"Expected cost avoided: ${roi['expected_cost_avoided_usd']:,.0f}\n"
            f"Net benefit after intervention cost: ${roi['net_roi_usd']:,.0f}\n"
            f"Return ratio: {roi['roi_ratio']}x\n"
        )
        case = roi.get("case") or {}
        if case:
            roi_block += (
                f"Break-even risk for this programme: {roi.get('break_even_risk_pct')}%\n"
                f"DECISION for this patient: {case.get('label')}\n"
                f"Why: {case.get('reason')}\n"
            )
    else:
        roi_block = ""

    prompt = (
        f"Readmission risk score: {risk_score}%\n"
        f"Risk band: {risk_band}\n"
        f"Top clinical risk drivers:\n{driver_lines}\n"
        f"{roi_block}\n"
        "Return the JSON now."
    )

    return llm.complete_json("insights", system=_INSIGHTS_SYSTEM_INSTRUCTION, prompt=prompt,
                             schema=_INSIGHTS_SCHEMA, retries=_RETRIES)


# The score series is a patient's ADMISSION history, not a weekly monitoring
# feed: consecutive points are typically months apart. The previous version of
# this instruction said "one week's re-score" and "the change from the previous
# week", so the model narrated week-over-week deterioration for admissions that
# were in fact years apart. The interval is now stated explicitly in the prompt
# and the model is told to reason about that elapsed time.
_WEEK_NARRATIVE_SYSTEM_INSTRUCTION = (
    "You are a clinical care coordinator assistant supporting a hospital readmission-risk "
    "dashboard.\n\n"
    "IMPORTANT CONTEXT ABOUT THE DATA. Each point in this patient's risk trend is a SEPARATE "
    "HOSPITAL ADMISSION, scored at that admission's discharge. It is NOT a weekly check-in and "
    "the points are NOT evenly spaced: consecutive admissions are often months or years apart. "
    "The prompt states how long after the previous discharge this admission began. You must "
    "reason about that actual elapsed time. Never describe the change as happening over a week, "
    "and never use the word 'week' unless the stated interval really is about a week.\n\n"
    "HOW TO READ THE INTERVAL. A return within about 30 days is a rapid readmission and suggests "
    "the previous discharge left something unresolved, so the score change is directly meaningful. "
    "A gap of many months or years means the two scores describe different illness episodes, so "
    "the change reflects how the patient's baseline health has shifted over that period rather "
    "than any short-term deterioration. Say which of these applies.\n\n"
    "Given one admission's score, risk band, the change from the previous admission, the elapsed "
    "time since the previous discharge, and that admission's top clinical risk drivers, explain "
    "why the score is where it is and what drove the change. Only reason about drivers actually "
    "provided; never invent clinical facts not present in the input. Refer to the patient as "
    "'the patient' or 'they'; the input does not state a gender, so never assume one.\n\n"
    "Return bullet_points as a JSON array of 2 to 4 short bullet points. Each bullet point is one "
    "complete plain sentence written for a care coordinator. Do not use markdown, do not prefix "
    "bullets with any symbol, and do not use dashes or hyphens as sentence punctuation; write "
    "plain sentences using only commas and periods."
)

# The SAME days_since_prev field means two opposite things depending on the
# series: in an admission history "7 days later" is a bounce-back readmission;
# in weekly monitoring it is simply the next scheduled check while the patient
# is at home. Narrating a monitoring week with the admission prompt produced
# "the patient returned rapidly just seven days after discharge" for a patient
# who had not been readmitted at all - so the two get separate instructions.
_WEEKLY_NARRATIVE_SYSTEM_INSTRUCTION = (
    "You are a clinical care coordinator assistant supporting a post-discharge monitoring "
    "dashboard.\n\n"
    "IMPORTANT CONTEXT ABOUT THE DATA. Each point is a SCHEDULED WEEKLY RE-SCORE during the "
    "30-day window after the patient went home. Week 0 is the discharge baseline; weeks 1 to 4 "
    "are follow-up checks driven by home vitals, medication adherence, pharmacy dispensing "
    "records and follow-up attendance. THE PATIENT IS AT HOME AND HAS NOT "
    "BEEN READMITTED. The 7-day interval is the monitoring schedule, not a gap between hospital "
    "stays. Never describe the patient as having returned, been readmitted, or come back to "
    "hospital. If the risk is rising, the point is that a readmission may be COMING and the team "
    "still has time to prevent it.\n\n"
    "SOME WEEKS HAVE NO CONTACT. When the prompt says the score was carried forward, say that "
    "the score is unchanged because nothing was received from the patient that week, and that "
    "contact should be chased. Do not invent a clinical reason for a flat carried-forward week.\n\n"
    "THE PATIENT'S CONDITION CHANGES WHAT THE NUMBERS MEAN. The prompt states a monitoring group "
    "and what that group is watched for. Reason within it. A 2 kg weekly weight gain is the "
    "classic early warning in heart failure and is close to meaningless after orthopaedic "
    "surgery; an oxygen saturation of 91% is alarming after a pulmonary embolism and is inside "
    "the normal target range in chronic lung disease. Do not describe a reading as concerning "
    "when the driver explanation says it is expected for this condition, and do not recommend "
    "an action that belongs to a different condition.\n\n"
    "WHERE EACH NUMBER CAME FROM. Each driver line names the feed it arrived on, such as a home "
    "device hub, a medication app, a named pharmacy or a clinic scheduling system. These differ "
    "in how much weight they carry: a pharmacy dispensing record and a clinic attendance record "
    "are external facts, while an adherence percentage from an app is the patient's own account. "
    "Where it matters to the action you recommend, say which feed the finding came from.\n\n"
    "Given the week number, the score, the risk band, the change from the previous week, whether "
    "anything was recorded, and that week's top clinical drivers, explain what the week suggests about "
    "the patient's trajectory and what the care team should do. Only reason about drivers actually "
    "provided; never invent clinical facts not present in the input. Refer to the patient as "
    "'the patient' or 'they'; the input does not state a gender, so never assume one.\n\n"
    "Return bullet_points as a JSON array of 2 to 4 short bullet points. Each bullet point is one "
    "complete plain sentence written for a care coordinator. Do not use markdown, do not prefix "
    "bullets with any symbol, and do not use dashes or hyphens as sentence punctuation; write "
    "plain sentences using only commas and periods."
)


_WEEK_NARRATIVE_SCHEMA = {
    "type": "object",
    "properties": {
        "bullet_points": {
            "type": "array", "items": {"type": "string"},
            "description": "2-4 short plain-text bullet points explaining this week's score.",
        },
    },
    "required": ["bullet_points"],
}


def generate_week_narrative(
    patient_id: str,
    week_number: int,
    risk_score: float,
    risk_band: str,
    delta,
    week_trend: str,
    drivers: list,
    days_since_prev=None,
    interval_label: str = "",
    admit_date: str = "",
    discharge_date: str = "",
    los_days=None,
    series_kind: str = "admissions",
    observed: bool = True,
    primary_diagnosis: str = "",
    clinical_group: str = "",
    group_focus: str = "",
) -> list:
    """
    Calls the model once to narrate a single ADMISSION in a patient's trend series —
    why the score is where it is and what drove the change since the previous
    admission — as a list of bullet points. Raises on failure; the caller turns
    that into a 503.

    `days_since_prev` is the days between the previous discharge and this
    admission. It is the field that keeps the narrative honest: without it the
    model has no way to know whether it is describing a 3-day bounce-back or a
    5-year gap, and it defaults to talking about weeks.
    """
    driver_lines = _driver_lines(drivers)

    weekly = series_kind == "weekly"
    # The admitting diagnosis anchors the whole narrative: the same rising
    # creatinine means something different after a GI bleed than after
    # chemotherapy. Without it the model can only talk about the numbers.
    dx_line = f"Admitting diagnosis: {primary_diagnosis}\n" if primary_diagnosis else ""
    if clinical_group:
        dx_line += (f"Monitoring group: {clinical_group.replace('_', ' ')}\n"
                    f"What this group is watched for: {group_focus}\n")

    if weekly:
        if delta is None:
            delta_text = ("This is week 0, the discharge baseline, with no prior week to compare "
                          "against.")
        else:
            draw = ("Monitoring data was received this week."
                    if observed else
                    "NOTHING was received from this patient this week, so the score is carried "
                    "forward unchanged from the last week that reported.")
            delta_text = (f"This is a scheduled weekly check {week_number} week(s) after "
                          f"discharge. The patient is at home. {draw}\n"
                          f"Change from last week: {delta:+.1f} points ({week_trend}).")
        prompt = (
            f"Post-discharge monitoring week {week_number} of 4.\n"
            f"{dx_line}"
            f"Readmission risk score this week: {risk_score}%\n"
            f"Risk band: {risk_band}\n"
            f"{delta_text}\n"
            f"Top clinical risk drivers this week:\n{driver_lines}\n\n"
            "Return the JSON now."
        )
        return llm.complete_json("insights", system=_WEEKLY_NARRATIVE_SYSTEM_INSTRUCTION, prompt=prompt,
                                 schema=_WEEK_NARRATIVE_SCHEMA, retries=_RETRIES).get("bullet_points", [])

    if delta is None:
        delta_text = ("This is the first scored admission on record, with no prior score to "
                      "compare against.")
    elif days_since_prev is None:
        delta_text = (f"Change from the previous admission: {delta:+.1f} points ({week_trend}). "
                      "The time between the two admissions is not recorded, so do not "
                      "characterise how quickly the change happened.")
    else:
        pace = ("This is a rapid readmission." if days_since_prev <= 30 else
                "This is not a rapid readmission; the two admissions are far apart.")
        delta_text = (
            f"Time since the previous discharge: {days_since_prev} days ({interval_label}). "
            f"{pace}\n"
            f"Change from the previous admission: {delta:+.1f} points ({week_trend})."
        )

    stay_bits = []
    if admit_date and discharge_date:
        stay_bits.append(f"admitted {admit_date}, discharged {discharge_date}")
    if los_days is not None:
        stay_bits.append(f"length of stay {los_days} days")
    stay_text = f"This admission: {'; '.join(stay_bits)}.\n" if stay_bits else ""

    prompt = (
        f"Admission number {week_number} in this patient's scored history.\n"
        f"{dx_line}"
        f"{stay_text}"
        f"Readmission risk score at this discharge: {risk_score}%\n"
        f"Risk band: {risk_band}\n"
        f"{delta_text}\n"
        f"Top clinical risk drivers for this admission:\n{driver_lines}\n\n"
        "Return the JSON now."
    )

    return llm.complete_json("insights", system=_WEEK_NARRATIVE_SYSTEM_INSTRUCTION, prompt=prompt,
                             schema=_WEEK_NARRATIVE_SCHEMA, retries=_RETRIES).get("bullet_points", [])

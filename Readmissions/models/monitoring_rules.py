"""
The post-discharge monitoring rule set.

Shared by two callers so they can never disagree:

  scripts/simulate_weekly_monitoring.py   generates simulated weekly panels
  api/main.py                             scores a real patient's self-logged
                                          vitals and medication adherence

Each rule maps one observed value to (points, phrasings). Points are added to
the patient's calibrated discharge score; positive raises risk, negative lowers
it.

The weights are deliberately ASYMMETRIC - the worst week adds about 27 points
while the best week removes about 6. Deterioration is strong evidence of
trouble; perfect adherence is only weak evidence of safety, since a patient can
take every pill and still decompensate. The trained model behaves the same way:
perturbing its lab inputs across their deteriorating range moved scores +10.2
points on average, while normalising them moved only -1.8.

This is NOT the gradient-boosted model scoring vitals. That model was trained on
discharge-time features and contains no vitals, adherence or pharmacy feature.
This layer is a transparent clinical guardrail over a calibrated ML baseline.

WHY EACH RULE HAS SEVERAL PHRASINGS
-----------------------------------
A rule fires for a whole bucket of values, so a single fixed sentence made every
patient in that bucket read identically - four weeks of cards saying the same
thing about adherence teaches a reader to stop reading them. Each branch now
carries two or three phrasings that say the same clinical thing from a different
angle, and `score_week` picks one from a caller-supplied `variant`. The variant
is derived from patient and week, never drawn at random, so a card does not
reword itself on refresh.

The POINTS are unaffected by the variant. Only the prose changes.

WHERE THE NUMBERS COME FROM
---------------------------
See SIGNAL_FEEDS. Each signal is attributed to a named upstream feed - a home
device hub, a medication app, a retail pharmacy, a clinic scheduling system -
because "adherence 62%" is a different fact depending on whether a patient typed
it or a pharmacy dispensing record produced it.

Those feed names are FICTIONAL and generated deterministically from the patient
id. No such integration exists yet; this is the shape the cards will have when
it does.

Explanations must not contain " (" - a driver renders as
"<label>: <value> (<explanation>)" and the API parses it back on the last " (".
See api/mimic_scoring._split_driver.
"""

from __future__ import annotations

import hashlib
from datetime import date, datetime, timedelta

import numpy as np

# How much of last week's adjustment persists into this one. A missed refill
# does not stop mattering because a new week started.
CARRY = 0.45

# Clamped so a long deteriorating run cannot saturate at 100 and lose all
# resolution between patients.
SCORE_FLOOR, SCORE_CEIL = 1.0, 96.0


# ---------------------------------------------------------------------------
# The monitoring rule set
# ---------------------------------------------------------------------------
# Each rule maps an observed value to (points, phrasings). Points are added to
# the discharge baseline; positive raises risk, negative lowers it. Phrasings
# are interchangeable descriptions of the SAME finding - see the module note.
#
# Explanations must not contain " (" - the driver string is rendered as
# "<label>: <value> (<explanation>)" and the API parses it back on the last
# " (". See api/mimic_scoring._split_driver.

# The wording below is shared by every condition, so it stays general. Fluid,
# congestion and diuretic language belongs to the heart failure and kidney
# profiles, which override these phrasings - a hip fracture patient losing a
# kilogram has not had a "diuresis".
def _rule_weight(kg):
    if kg >= 2.0:
        return 7.0, "gain_major", (
            "a gain of more than 2 kg in a week is faster than body tissue can be "
            "gained, so retained fluid is the usual explanation",
            "weight moving this fast is more likely fluid than tissue, and it is "
            "worth asking about swelling and breathlessness",
            "a rise this size in seven days is unlikely to be tissue, so it is worth "
            "a call to find out what is behind it",
        )
    if kg >= 1.0:
        return 3.0, "gain_mild", (
            "steady weight gain this week, worth watching to see whether it continues",
            "a rise of about a kilogram a week is the beginning of a trend rather "
            "than day-to-day variation",
            "not alarming on its own, but the direction is wrong for this stage of "
            "recovery and it is worth a check-in",
        )
    if kg <= -1.0:
        return -1.0, "loss", (
            "weight is coming down a little, which on its own is not a concern at "
            "this stage",
            "a modest fall in weight in the first weeks home is common as appetite "
            "and activity settle",
            "weight is below the discharge reading, worth noting but not a warning "
            "sign on its own",
        )
    return 0.0, "stable", (
        "weight is stable week on week",
        "no fluid signal either way - the reading is doing its job by being dull",
        "holding steady, which at this stage is the result you want",
    )


def _rule_adherence(pct):
    if pct < 50:
        return 8.0, "very_low", (
            "less than half of prescribed doses were covered, so the discharge "
            "regimen is effectively not being taken",
            "at this level the medicines are not doing clinical work, and the "
            "reason - cost, side effects, confusion over the new list - is worth "
            "asking about directly",
            "most of the protective effect of the discharge prescription has been "
            "lost, and this is the single most reversible thing on this card",
        )
    if pct < 80:
        return 4.5, "below_threshold", (
            "below the 80% threshold used for adequate adherence, so the regimen is "
            "only partly protective",
            "roughly one dose in four is being missed, which is the range where "
            "readmission risk starts to climb measurably",
            "partial cover like this often means one specific drug is being skipped "
            "rather than the whole list, which a pharmacist call can usually find",
        )
    if pct < 95:
        return -0.5, "adequate", (
            "adherence is adequate and above the 80% threshold",
            "dosing is good enough to count as covered, with the occasional miss "
            "that almost every patient has",
            "comfortably in the range where the discharge regimen is doing its job",
        )
    return -1.5, "excellent", (
        "the discharge regimen is being taken almost exactly as prescribed",
        "near-perfect dosing, which removes medication non-adherence as an "
        "explanation for anything else on this card",
        "this patient is doing their part of the plan, so any deterioration is "
        "unlikely to be about the tablets",
    )


def _rule_refill(status):
    points, phrasings = {
        "missed": (5.0, (
            "the prescription due this week was not collected, so there is a "
            "confirmed gap in treatment",
            "the dispensing record shows nothing collected, which means the supply "
            "at home has run out rather than merely being taken irregularly",
            "an uncollected prescription is one of the few hard signals here - it "
            "is a fact from the pharmacy, not a self-report",
        )),
        "late": (2.0, (
            "the refill was collected several days late, leaving a short gap in cover",
            "a few days without supply is usually logistics rather than intent, but "
            "the treatment gap is real either way",
            "collected in the end, though the delay means some doses were missed "
            "before the new pack arrived",
        )),
        "collected": (-0.8, (
            "the refill due this week was collected on time",
            "the pharmacy record confirms supply is in the patient's hands",
            "supply is continuous, so nothing on this card can be blamed on running "
            "out of tablets",
        )),
        "not due": (0.0, (
            "no refill was due this week",
            "the current pack still has days left, so the pharmacy feed has nothing "
            "to say this week",
            "nothing scheduled at the pharmacy for this week",
        )),
    }[status]
    return points, status.replace(" ", "_"), phrasings


def _rule_followup(status):
    points, phrasings = {
        "missed": (4.5, (
            "the booked post-discharge review was not attended, so nobody has "
            "reassessed this patient",
            "a missed early review means no clinician has laid eyes on this patient "
            "since discharge, and every other number here is self-reported",
            "the appointment that exists precisely to catch problems early did not "
            "happen, and rebooking it is the obvious first action",
        )),
        "attended": (-2.0, (
            "the post-discharge review was attended, which is consistently "
            "associated with lower readmission risk",
            "the early review happened, so the discharge plan has been checked by a "
            "clinician rather than assumed to be working",
            "attending the first follow-up is one of the strongest modifiable "
            "predictors in this whole panel, and it has been done",
        )),
        "not due": (0.0, (
            "no follow-up appointment fell in this week",
            "nothing was booked for this week, so attendance is not a signal either way",
            "the next review sits outside this week's window",
        )),
    }[status]
    return points, status.replace(" ", "_"), phrasings


def _rule_sbp(mmhg):
    if mmhg >= 180 or mmhg < 90:
        return 4.0, "extreme", (
            "outside the safe ambulatory range, indicating haemodynamic instability",
            "a reading this far from target needs to be confirmed and acted on the "
            "same day rather than at the next scheduled call",
            "blood pressure at this level is a reason to contact the patient now, "
            "whatever the rest of the panel says",
        )
    if mmhg >= 160:
        return 2.0, "high", (
            "above target, adding cardiovascular strain during recovery",
            "persistently high for someone in the first weeks after discharge, and "
            "usually a dose-titration question",
            "not an emergency, but it is extra load on a system that is still recovering",
        )
    if mmhg <= 139:
        return -0.5, "target", (
            "within the target range for recovery",
            "sitting where the discharge plan intended it to sit",
            "controlled, which suggests the antihypertensive part of the regimen is "
            "being taken and is correctly dosed",
        )
    return 0.0, "borderline", (
        "slightly above target but not alarming on its own",
        "marginally high - worth watching across the next two readings rather than "
        "acting on today",
        "a little above where it should be, though single home readings run high "
        "often enough that one value proves nothing",
    )


def _rule_hr(bpm):
    if bpm >= 110:
        return 3.5, "tachy_high", (
            "a sustained resting tachycardia, which signals physiological stress or "
            "infection",
            "a resting rate this high after discharge is the body compensating for "
            "something - infection, anaemia and dehydration are the usual three",
            "persistent tachycardia is rarely the primary problem, but it is a "
            "reliable sign that there is one",
        )
    if bpm >= 100:
        return 2.0, "tachy_mild", (
            "resting heart rate above 100 bpm, an early sign of strain",
            "mildly fast at rest, which often precedes a clearer picture by a few days",
            "above the normal resting range, and worth pairing with the weight and "
            "temperature before drawing a conclusion",
        )
    if bpm < 50:
        return 2.0, "brady", (
            "an unusually slow resting rate, which can follow over-treatment with "
            "rate-control drugs",
            "bradycardia this marked usually points at the beta-blocker or "
            "rate-control dose rather than at new disease",
            "slow enough to warrant a medication review, particularly if the patient "
            "reports dizziness",
        )
    return 0.0, "normal", (
        "resting heart rate is within the normal range",
        "an unremarkable resting rate, which is the useful kind of reading",
        "nothing in the pulse to suggest strain this week",
    )


def _rule_spo2(pct):
    if pct < 90:
        return 5.0, "very_low", (
            "below 90%, indicating impaired gas exchange that usually needs assessment",
            "a saturation this low at home is an assessment today, not a note for "
            "the next review",
            "gas exchange is impaired enough that the cause needs finding rather "
            "than monitoring",
        )
    if pct < 92:
        return 3.0, "low", (
            "below the 92% threshold, suggesting respiratory compromise",
            "under the threshold where a respiratory cause becomes the leading "
            "explanation for the rest of the panel",
            "low enough to matter, especially alongside any rise in weight or heart rate",
        )
    if pct >= 95:
        return -0.5, "normal", (
            "oxygen saturation is comfortably normal",
            "no respiratory signal - the lungs are keeping up",
            "saturation in the range that effectively rules out a respiratory driver "
            "this week",
        )
    return 0.0, "marginal", (
        "oxygen saturation is marginal but acceptable",
        "borderline rather than low, and home oximeters read a point or two under "
        "at the best of times",
        "acceptable for now, though it leaves little headroom if anything else worsens",
    )


# ---------------------------------------------------------------------------
# Group-specific rule replacements
# ---------------------------------------------------------------------------
# Used where a group needs a different SHAPE of rule, not merely a different
# weight - the direction of risk itself changes.

def _rule_weight_oncology(kg):
    """
    In cancer, weight LOSS is the risk signal, not gain.

    Cachexia and poor intake predict deterioration; a gain is usually fluid from
    steroids or simply appetite returning. Applying the heart-failure rule here
    would score recovery as decline.
    """
    if kg <= -2.0:
        return 6.0, "loss_major", (
            "losing more than 2 kg in a week during cancer treatment points at poor "
            "intake or cachexia, and is one of the strongest warning signs available",
            "unintended weight loss at this rate usually means the patient is not "
            "eating, and nutrition support is the intervention",
        )
    if kg <= -1.0:
        return 3.0, "loss_mild", (
            "steady weight loss suggests intake is not keeping up with what treatment "
            "is demanding",
            "a kilogram a week is a trend worth interrupting before it compounds",
        )
    if kg >= 2.0:
        return 1.5, "gain", (
            "a rise this fast is more likely steroid-related fluid than nutrition, so "
            "it is worth checking rather than celebrating",
            "weight up sharply during treatment is usually fluid, and the ankles and "
            "breathing are the things to ask about",
        )
    return 0.0, "stable", (
        "weight is holding, which during treatment is a good result in itself",
        "stable weight means intake is keeping up with demand",
    )


def _rule_spo2_respiratory(pct):
    """
    In chronic lung disease the general thresholds are simply wrong.

    A patient with advanced COPD often lives at 88-92%, and the accepted target
    range for oxygen therapy in COPD is 88-92% precisely because higher is not
    safer for them. Scoring 91% as "respiratory compromise" would flag their
    normal state every week and train the team to ignore the card.
    """
    if pct < 85:
        return 5.0, "very_low", (
            "below 85% is low even for chronic lung disease and needs assessment today",
            "this is below where a patient with chronic lung disease usually sits, and "
            "the drop matters more than the number",
        )
    if pct < 88:
        return 3.0, "low", (
            "below the 88-92% range targeted in chronic lung disease, so this is a "
            "genuine fall rather than their normal baseline",
            "under the range this patient is expected to live in, which suggests an "
            "exacerbation rather than a bad reading",
        )
    if pct >= 92:
        return -0.8, "normal", (
            "at or above the top of the 88-92% target range for chronic lung disease",
            "comfortably within range for someone with chronic lung disease",
        )
    return 0.0, "marginal", (
        "inside the 88-92% range that is the target in chronic lung disease, so this "
        "is where this patient is expected to sit rather than a warning",
        "normal for chronic lung disease - the same number would be a concern in "
        "someone without it",
    )


# key -> (display label, value formatter, rule)
SIGNAL_RULES = {
    "weight_change_kg": ("Weight change since discharge", lambda v: f"{v:+.1f} kg", _rule_weight),
    "adherence_pct":    ("Medication adherence",          lambda v: f"{v:.0f}%",    _rule_adherence),
    "refill_status":    ("Pharmacy refill",               lambda v: str(v),         _rule_refill),
    "followup_status":  ("Follow-up appointment",         lambda v: str(v),         _rule_followup),
    "sbp":              ("Systolic blood pressure",       lambda v: f"{v:.0f} mmHg", _rule_sbp),
    "heart_rate":       ("Resting heart rate",            lambda v: f"{v:.0f} bpm", _rule_hr),
    "spo2":             ("Oxygen saturation",             lambda v: f"{v:.0f}%",    _rule_spo2),
}


# ---------------------------------------------------------------------------
# Disease-specific signals
# ---------------------------------------------------------------------------
# The seven signals above apply to everybody. These do not: each belongs to one
# or two clinical groups, and asking any other patient for them wastes their
# time on a number nobody will act on.
#
# Every threshold below is published, not invented:
#
#   orthopnoea          needing more pillows to sleep is on the standard heart
#                       failure "zone chart" given to patients at discharge
#   Anthonisen criteria an exacerbation of chronic lung disease is defined by
#                       increased breathlessness, sputum VOLUME and sputum
#                       PURULENCE - two of three is a treatable exacerbation
#   rescue inhaler      more than twice a week is the accepted marker of poor
#                       control in inhaled-therapy guidelines
#   38.0 C              the threshold for neutropenic fever, which is an
#                       emergency during chemotherapy, and the usual trigger for
#                       assessing a post-operative wound
#   new confusion       part of every sepsis screening tool as a sign of
#                       deterioration, and often the first thing a family notices
#
# CAP: no disease-specific signal scores above +6.0, which keeps all of them
# below medication adherence at +8.0. Adherence and follow-up attendance have
# the strongest evidence base of anything here, and a first pass at
# disease-specific monitoring should not outrank them.

def _rule_orthopnoea(pillows):
    """Pillows needed to sleep. A rise is orthopnoea - fluid on the lungs lying flat."""
    if pillows >= 3:
        return 6.0, "severe", (
            "needing three or more pillows to sleep means fluid is collecting in the "
            "lungs when lying flat. This is one of the signs patients notice first and "
            "report last",
            "sleeping almost upright is orthopnoea, and in heart failure it usually "
            "arrives alongside the weight and before the breathlessness",
        )
    if pillows == 2:
        return 3.0, "moderate", (
            "needing a second pillow to sleep comfortably suggests fluid is beginning "
            "to accumulate",
            "an extra pillow is a small change worth asking about, because it often "
            "precedes the more obvious symptoms by several days",
        )
    return 0.0, "none", (
        "sleeping flat without difficulty, which argues against fluid overload",
        "no orthopnoea, which is a genuinely reassuring sign in heart failure",
    )


def _rule_ankle_swelling(level):
    points, phrasings = {
        "marked": (5.0, (
            "swelling up the shins is visible fluid retention, and alongside a rising "
            "weight it is the clearest evidence of congestion available at home",
            "oedema this far up the leg means several litres of retained fluid, not a "
            "long day on the feet",
        )),
        "mild": (2.5, (
            "ankle swelling that was not there at discharge is early fluid retention",
            "mild oedema on its own is common, but paired with any weight rise it is "
            "the same story told twice",
        )),
        "none": (-0.5, (
            "no ankle swelling, which is consistent with fluid staying under control",
            "the ankles are clear, which supports the weight reading rather than "
            "contradicting it",
        )),
    }[level]
    return points, level, phrasings


def _rule_walk_distance(pct):
    """Walking distance as a percentage of the patient's OWN usual distance."""
    if pct < 40:
        return 5.5, "collapsed", (
            "walking distance has more than halved against this patient's own usual "
            "range, which is a large functional loss in a short time",
            "a fall this steep in exercise tolerance is one of the earliest signs "
            "that recovery is going backwards, and it shows up before most readings do",
        )
    if pct < 70:
        return 3.0, "reduced", (
            "walking noticeably less far than usual, measured against their own "
            "baseline rather than a population figure",
            "a third of their usual distance has gone, which is worth asking about "
            "even if every other reading looks acceptable",
        )
    if pct >= 95:
        return -1.0, "restored", (
            "back to their usual walking distance, which is the most meaningful "
            "recovery signal a patient can report",
            "exercise tolerance is where it was before, which no single vital sign "
            "can tell you",
        )
    return 0.0, "near_usual", (
        "walking close to their usual distance",
        "a small reduction, within the range of an ordinary week",
    )


def _rule_rescue_inhaler(uses):
    """Rescue inhaler uses per week. More than twice weekly indicates poor control."""
    if uses >= 14:
        return 6.0, "very_high", (
            "reaching for the rescue inhaler daily or more means the maintenance "
            "treatment is not holding, and this is usually an exacerbation already "
            "under way",
            "at this frequency the reliever is doing the work the preventer should be, "
            "which is the definition of uncontrolled disease",
        )
    if uses > 2:
        return 3.5, "high", (
            "more than twice a week is the accepted threshold for poor control, and it "
            "is a prompt to review inhaler technique and the maintenance dose",
            "above the twice-weekly mark the treatment plan needs revisiting rather "
            "than the patient trying harder",
        )
    return -0.5, "controlled", (
        "rescue inhaler use is within the range considered well controlled",
        "the reliever is barely needed, which suggests the maintenance treatment is "
        "doing its job",
    )


def _rule_sputum(change):
    """Anthonisen criteria: increased volume and purulence define an exacerbation."""
    points, phrasings = {
        "both": (6.0, (
            "more sputum and a change in colour together are two of the three "
            "Anthonisen criteria, which is the definition of an exacerbation and the "
            "usual trigger for antibiotics and steroids",
            "increased volume plus purulence is the classic exacerbation picture, and "
            "treating it early is what keeps it out of hospital",
        )),
        "purulent": (4.0, (
            "a change to green or yellow sputum suggests bacterial infection and is one "
            "of the three criteria used to define an exacerbation",
            "colour change alone is worth a call, because it is often the first of the "
            "three criteria to appear",
        )),
        "volume": (2.5, (
            "producing more sputum than usual is one of the three exacerbation criteria",
            "an increase in volume without a colour change is early, and worth watching "
            "over the next few days",
        )),
        "none": (-0.5, (
            "no change in sputum, which argues against an exacerbation",
            "sputum unchanged, which is one of the three exacerbation criteria ruled out",
        )),
    }[change]
    return points, change, phrasings


def _rule_temperature(celsius):
    if celsius >= 38.0:
        return 6.0, "fever", (
            "a temperature of 38 degrees or more is the threshold that defines fever, "
            "and after surgery or a serious infection it needs assessing the same day "
            "rather than being watched",
            "at or above 38 degrees this is a fever, and in a patient recovering from "
            "surgery or infection that is an assessment today",
        )
    if celsius >= 37.5:
        return 3.0, "raised", (
            "a temperature above normal but below the fever threshold, which is worth "
            "repeating in a few hours rather than acting on immediately",
            "mildly raised. On its own it proves nothing, but alongside a rising pulse "
            "it is the beginning of a picture",
        )
    if celsius < 36.0:
        return 3.5, "low", (
            "an unusually low temperature can accompany serious infection just as fever "
            "does, particularly in older patients",
            "a low temperature in someone recovering from surgery or infection is not "
            "reassuring, it can be the other half of the same warning",
        )
    return -0.5, "normal", (
        "temperature is normal",
        "no fever, which is one of the most useful things to know at this stage of "
        "recovery",
    )


def _rule_wound(status):
    points, phrasings = {
        "opening": (6.0, (
            "a wound that is opening needs to be seen today. It will not close on its "
            "own and the risk of deeper infection rises quickly",
            "dehiscence is a same-day problem, whatever the rest of this card says",
        )),
        "discharge": (5.0, (
            "discharge from a surgical wound suggests infection, and it is treatable "
            "with antibiotics if caught now and a readmission if not",
            "a weeping wound is the most common reason a post-surgical patient comes "
            "back, and it is also the most preventable",
        )),
        "red": (3.0, (
            "redness spreading around a wound is early infection. It responds to oral "
            "antibiotics at this stage",
            "surrounding redness is worth a photograph and a call today rather than "
            "waiting for the booked review",
        )),
        "clean": (-1.0, (
            "the wound looks clean and dry, which is the single most reassuring thing "
            "in surgical recovery",
            "a clean wound removes the most common cause of readmission in this group",
        )),
    }[status]
    return points, status, phrasings


def _rule_pain_trend(trend):
    points, phrasings = {
        "worse": (4.0, (
            "pain increasing rather than settling is the wrong direction after surgery, "
            "and it usually points at the wound or at a clot",
            "post-operative pain should fall week on week. Rising pain is a symptom, not "
            "a tolerance problem",
        )),
        "unchanged": (1.5, (
            "pain that has not improved is slower progress than expected, though not "
            "alarming on its own",
            "a plateau in pain is worth mentioning at the review rather than acting on "
            "today",
        )),
        "improving": (-1.0, (
            "pain is settling, which is the expected trajectory and a good sign that the "
            "wound is healing underneath",
            "improving pain is the ordinary course of recovery and needs nothing",
        )),
    }[trend]
    return points, trend, phrasings


def _rule_antibiotic_course(status):
    points, phrasings = {
        "stopped_early": (6.0, (
            "stopping an antibiotic course early after a serious infection is the "
            "highest-priority item on this card. It is how a treated infection returns, "
            "and returns harder to treat",
            "an unfinished course leaves the infection partly treated, which is worse "
            "than untreated and should be chased today",
        )),
        "ongoing": (0.0, (
            "the antibiotic course is still running as prescribed",
            "still taking the course, which is what should be happening at this point",
        )),
        "completed": (-2.0, (
            "the full antibiotic course was completed, which is the single most "
            "important thing after treatment for a serious infection",
            "course finished as prescribed, which removes the most common route back to "
            "hospital in this group",
        )),
        "not_prescribed": (0.0, (
            "no antibiotic course was prescribed at discharge",
            "nothing to complete, so this is not a signal either way",
        )),
    }[status]
    return points, status, phrasings


def _rule_confusion(state):
    points, phrasings = {
        "yes": (6.0, (
            "new confusion after an infection is a recognised sign of deterioration and "
            "often the first thing a family notices. It needs assessing today",
            "delirium is part of every sepsis screening tool, and in an older patient it "
            "can be the only sign that the infection is back",
        )),
        "no": (-0.5, (
            "no new confusion reported, which is one of the more useful negatives here",
            "mentally unchanged, which argues against the infection returning",
        )),
    }[state]
    return points, state, phrasings


# Signals that belong to specific groups only. Same shape as SIGNAL_RULES.
DISEASE_SIGNALS = {
    "orthopnoea_pillows": ("Pillows needed to sleep", lambda v: f"{int(v)}", _rule_orthopnoea),
    "ankle_swelling":     ("Ankle swelling", lambda v: str(v), _rule_ankle_swelling),
    "walk_distance_pct":  ("Walking distance vs usual", lambda v: f"{v:.0f}% of usual",
                           _rule_walk_distance),
    "rescue_inhaler_uses": ("Rescue inhaler uses", lambda v: f"{int(v)} this week",
                            _rule_rescue_inhaler),
    "sputum_change":      ("Sputum change", lambda v: str(v), _rule_sputum),
    "temperature_c":      ("Temperature", lambda v: f"{v:.1f} C", _rule_temperature),
    "wound_status":       ("Wound", lambda v: str(v), _rule_wound),
    "pain_trend":         ("Pain since last week", lambda v: str(v), _rule_pain_trend),
    "antibiotic_course":  ("Antibiotic course", lambda v: str(v).replace("_", " "),
                           _rule_antibiotic_course),
    "new_confusion":      ("New confusion", lambda v: str(v), _rule_confusion),
}

# Which groups get which. Four groups on this pass - the ones where the shared
# seven are most obviously the wrong instrument. The remaining groups keep the
# plain seven until these four are shown to work.
GROUP_SIGNALS = {
    "heart_failure":    ("orthopnoea_pillows", "ankle_swelling", "walk_distance_pct"),
    "respiratory":      ("rescue_inhaler_uses", "sputum_change", "walk_distance_pct"),
    "surgical_injury":  ("temperature_c", "wound_status", "pain_trend", "walk_distance_pct"),
    "sepsis_infection": ("temperature_c", "antibiotic_course", "new_confusion"),
}

# Neutral value per disease signal, used when a patient did not report it. The
# neutral must score ZERO or near it: a blank must never read as good news.
DISEASE_NEUTRAL = {
    "orthopnoea_pillows": 1,
    "ankle_swelling": "none",
    "walk_distance_pct": 85.0,
    "rescue_inhaler_uses": 2,
    "sputum_change": "none",
    "temperature_c": 36.8,
    "wound_status": "clean",
    "pain_trend": "improving",
    "antibiotic_course": "not_prescribed",
    "new_confusion": "no",
}


def signals_for(group: str) -> dict:
    """The shared seven, plus whatever this group adds."""
    signals = dict(SIGNAL_RULES)
    for key in GROUP_SIGNALS.get(group, ()):
        signals[key] = DISEASE_SIGNALS[key]
    return signals

# Reverse map so the API can get from a stored driver string back to its signal.
LABEL_TO_SIGNAL = {label: key for key, (label, _, _) in SIGNAL_RULES.items()}
LABEL_TO_SIGNAL.update({label: key for key, (label, _, _) in DISEASE_SIGNALS.items()})


# ---------------------------------------------------------------------------
# Score normalisation
# ---------------------------------------------------------------------------
# Giving a group extra signals also gives it extra points to accumulate. Left
# alone, a heart failure patient with ten signals would out-score a general
# patient with seven for having more boxes to tick, and the worklist sorts by
# score - so heart failure would drift to the top of every list for a reason
# that is arithmetic rather than clinical.
#
# So each group's worst attainable week is scaled to the same ceiling, and its
# best attainable week to the same floor. Points still SUM within a group, which
# matters: three problems really is worse than one, and averaging would hide
# that. It is only the range that is equalised.
#
# The reference is the general group's own extremes, so an ungrouped patient's
# score is unchanged by this and every other group is expressed on the same
# scale as before.

# Values that exercise every branch of every rule, used to find each rule's
# extremes. A rule whose probes miss a branch would under-state its ceiling, so
# these are kept beside the rules rather than derived.
_PROBE_VALUES = {
    "weight_change_kg": (3.0, 1.5, 0.0, -1.5, -3.0),
    "adherence_pct": (10, 60, 88, 100),
    "refill_status": ("missed", "late", "collected", "not due"),
    "followup_status": ("missed", "attended", "not due"),
    "sbp": (190, 165, 145, 125, 80),
    "heart_rate": (120, 104, 75, 45),
    "spo2": (84, 91, 94, 99),
    "orthopnoea_pillows": (0, 1, 2, 3, 4),
    "ankle_swelling": ("none", "mild", "marked"),
    "walk_distance_pct": (10, 55, 80, 100),
    "rescue_inhaler_uses": (0, 2, 6, 20),
    "sputum_change": ("none", "volume", "purulent", "both"),
    "temperature_c": (35.5, 36.8, 37.7, 38.6),
    "wound_status": ("clean", "red", "discharge", "opening"),
    "pain_trend": ("improving", "unchanged", "worse"),
    "antibiotic_course": ("completed", "ongoing", "stopped_early", "not_prescribed"),
    "new_confusion": ("no", "yes"),
}

_extremes_cache: dict = {}


def group_extremes(group: str, weights=None) -> tuple:
    """(worst attainable adjustment, best attainable adjustment) for a group.

    `weights` are the weights the week is actually scored with - the merged
    weights of a patient with several conditions. They must be the same ones
    used for scoring, or the range is measured on one scale and the week on
    another: a patient whose merged weights exceed their plan's was scaled up
    past the general range.
    """
    profile = profile_for(group)
    weights = profile["weights"] if weights is None else weights
    cache_key = (group, tuple(sorted(weights.items())))
    if cache_key in _extremes_cache:
        return _extremes_cache[cache_key]

    overrides = profile["rules"]
    worst = best = 0.0
    for key, (_, _, default_rule) in signals_for(group).items():
        rule = overrides.get(key, default_rule)
        points = [rule(v)[0] for v in _PROBE_VALUES[key]]
        w = weights.get(key, 1.0)
        worst += max(points) * w
        best += min(points) * w

    _extremes_cache[cache_key] = (worst, best)
    return worst, best


def _normalise(raw_total: float, group: str, weights=None) -> float:
    """Scale a group's raw adjustment onto the general group's range."""
    ref_worst, ref_best = group_extremes("general")
    worst, best = group_extremes(group, weights)
    if raw_total > 0 and worst > 0:
        return raw_total / worst * ref_worst
    if raw_total < 0 and best < 0:
        return raw_total / abs(best) * abs(ref_best)
    return raw_total


# ---------------------------------------------------------------------------
# Group profiles: what each condition changes about monitoring
# ---------------------------------------------------------------------------
# A profile can change three things about a signal:
#
#   weights   scale the points. 1.0 leaves the rule as written; 0.15 says the
#             signal is nearly meaningless for this group; 1.6 says it is the
#             thing that will bring them back.
#   rules     replace the rule outright, for when the DIRECTION of risk changes
#             - weight loss is the danger in oncology, weight gain in heart
#             failure.
#   phrasing  override the wording for one (signal, branch) pair, so the card
#             explains the finding in terms of this patient's condition.
#
# It also declares which signals are worth collecting, which drives the weekly
# logging form: a patient with chronic lung disease should be sending oxygen
# saturations and can skip the scales, and the form should say so.
#
# THESE WEIGHTS ARE CLINICALLY-REASONED DEFAULTS, NOT LEARNED VALUES.
# MIMIC contains no post-discharge vitals, so there is nothing to fit them
# against - exactly as with the base rules. They are written down here, with
# their reasoning, so a clinician can argue with them rather than having to
# read the code to find them. Every one is a starting hypothesis.

_ALL_SIGNALS = tuple(SIGNAL_RULES.keys())

GROUP_PROFILES = {
    "heart_failure": {
        "focus": "Fluid balance. Weight is the earliest and most treatable signal.",
        "weights": {"weight_change_kg": 1.6, "spo2": 1.2, "adherence_pct": 1.15,
                    "refill_status": 1.2},
        "phrasing": {
            ("weight_change_kg", "gain_major"):
                "in heart failure this is the single most reliable early warning. Two "
                "kilograms is roughly two litres of fluid the heart is having to move, "
                "and it usually responds to a diuretic review rather than an admission",
            ("weight_change_kg", "gain_mild"):
                "a kilogram of fluid in a week is how a heart failure admission starts. "
                "Worth a call about diuretic dose and salt intake before it doubles",
            ("weight_change_kg", "loss"):
                "the diuretic is doing its job and the congestion is clearing, which is "
                "exactly the trajectory this discharge plan was aiming for",
            ("spo2", "low"):
                "in heart failure a falling saturation usually means fluid in the lungs "
                "rather than a chest infection, and it belongs alongside the weight",
        },
        "priority_signals": ("weight_change_kg", "adherence_pct", "sbp", "spo2"),
    },

    "renal": {
        "focus": "Fluid and blood pressure together, plus anything nephrotoxic.",
        "weights": {"weight_change_kg": 1.35, "sbp": 1.4, "adherence_pct": 1.15,
                    "spo2": 0.8},
        "phrasing": {
            ("weight_change_kg", "gain_major"):
                "in kidney disease a gain this fast means fluid is not being cleared, "
                "and it usually shows in the ankles and the blood pressure at the same time",
            ("sbp", "high"):
                "blood pressure control does more to protect remaining kidney function "
                "than almost anything else, so a run of readings this high needs the dose "
                "revisiting",
            ("sbp", "extreme"):
                "this far outside range is dangerous for kidneys already under strain, and "
                "is a same-day call",
        },
        "priority_signals": ("sbp", "weight_change_kg", "adherence_pct", "refill_status"),
    },

    "respiratory": {
        "focus": "Oxygen saturation against this patient's own baseline, and inhaler use.",
        "weights": {"spo2": 1.5, "heart_rate": 1.2, "adherence_pct": 1.2,
                    "weight_change_kg": 0.3, "sbp": 0.7},
        "rules": {"spo2": _rule_spo2_respiratory},
        "phrasing": {
            ('weight_change_kg', 'gain_major'):
                "a gain this size can mean fluid if the heart is also involved, but in chronic lung disease alone the oxygen saturation and walking distance are the better guide",
            ("heart_rate", "tachy_mild"):
                "a rising resting pulse in chronic lung disease often arrives a day or "
                "two before the breathlessness does, so it is worth taking seriously early",
            ("weight_change_kg", "gain_mild"):
                "weight counts for little in chronic lung disease. The oxygen "
                "saturation and how far they can walk matter far more",
        },
        "priority_signals": ("spo2", "adherence_pct", "heart_rate", "refill_status"),
    },

    "sepsis_infection": {
        "focus": "Finishing the antibiotic course, and any sign the infection is back.",
        "weights": {"heart_rate": 1.5, "spo2": 1.3, "adherence_pct": 1.4,
                    "weight_change_kg": 0.4, "followup_status": 1.2},
        # Adherence here is the whole discharge regimen. The antibiotic course has
        # its own signal, so this wording must not claim the course is unfinished:
        # it used to, on cards whose antibiotic line said "completed".
        "phrasing": {
            ('weight_change_kg', 'gain_major'):
                "weight counts for little after a serious infection. The temperature, the pulse and finishing the antibiotic course are what matter",
            ("adherence_pct", "below_threshold"):
                "after a serious infection, doses missed from the discharge regimen leave "
                "the patient less protected while they recover. The antibiotic course has "
                "its own line on this card",
            ("adherence_pct", "very_low"):
                "most of the discharge regimen is not being taken, which after a serious "
                "infection should be followed up today",
            ("heart_rate", "tachy_high"):
                "a fast resting pulse in the weeks after a serious infection is the "
                "classic first sign it has not cleared",
        },
        "priority_signals": ("heart_rate", "adherence_pct", "spo2", "followup_status"),
    },

    "oncology": {
        "focus": "Nutrition and weight LOSS, plus any sign of infection between cycles.",
        "weights": {"heart_rate": 1.2, "followup_status": 1.3, "adherence_pct": 1.1},
        "rules": {"weight_change_kg": _rule_weight_oncology},
        "phrasing": {
            ("followup_status", "missed"):
                "a missed appointment during cancer treatment can delay the next cycle, "
                "which has consequences beyond this monitoring window",
            ("heart_rate", "tachy_high"):
                "between chemotherapy cycles a fast pulse raises the question of "
                "neutropenic infection, which is urgent rather than routine",
        },
        "priority_signals": ("weight_change_kg", "heart_rate", "followup_status",
                             "adherence_pct"),
    },

    "diabetes": {
        "focus": "Medication adherence and blood pressure; weight matters slowly.",
        "weights": {"adherence_pct": 1.3, "sbp": 1.1, "weight_change_kg": 0.6},
        "phrasing": {
            ("adherence_pct", "very_low"):
                "insulin or oral therapy missed at this rate produces problems within "
                "days rather than months, and the reason is usually practical - cost, "
                "supply, or fear of hypoglycaemia",
        },
        "priority_signals": ("adherence_pct", "refill_status", "sbp"),
    },

    "cardiac_other": {
        "focus": "Rate and rhythm, blood pressure, and staying on the regimen.",
        "weights": {"heart_rate": 1.3, "sbp": 1.2, "adherence_pct": 1.2,
                    "weight_change_kg": 0.8},
        "phrasing": {
            ("heart_rate", "brady"):
                "after a cardiac admission an unusually slow pulse usually points at the "
                "beta-blocker dose, which is a straightforward thing to review",
        },
        "priority_signals": ("heart_rate", "sbp", "adherence_pct", "refill_status"),
    },

    "neuro_stroke": {
        "focus": "Blood pressure and anticoagulation - the two things that prevent a second event.",
        "weights": {"adherence_pct": 1.4, "sbp": 1.3, "refill_status": 1.3,
                    "weight_change_kg": 0.3, "followup_status": 1.2},
        "phrasing": {
            ('weight_change_kg', 'gain_major'):
                "weight counts for little after a stroke. Blood pressure and staying on the anticoagulant are what prevent a second event",
            ("adherence_pct", "below_threshold"):
                "after a stroke, missed doses of an anticoagulant or antiplatelet are "
                "directly a second-stroke risk, not a general adherence concern",
            ("refill_status", "missed"):
                "an uncollected anticoagulant prescription after a stroke leaves the "
                "patient unprotected, and the gap starts the day the pack runs out",
        },
        "priority_signals": ("adherence_pct", "sbp", "refill_status", "followup_status"),
    },

    "surgical_injury": {
        "focus": "The wound, temperature and mobility. Weight is noise this month.",
        "weights": {"weight_change_kg": 0.15, "followup_status": 1.5, "heart_rate": 1.2,
                    "spo2": 0.9, "adherence_pct": 0.9},
        "phrasing": {
            ("weight_change_kg", "gain_major"):
                "after surgery a gain this size is usually appetite returning rather than "
                "fluid, and on its own it is not a concern. The wound and the temperature "
                "are the signals that matter this week",
            ("weight_change_kg", "gain_mild"):
                "weight is not a useful signal during surgical recovery - this is normal "
                "post-operative variation",
            ("followup_status", "missed"):
                "the post-operative review is when the wound gets inspected and stitches "
                "come out. Missing it is how a wound infection goes unnoticed",
            ("heart_rate", "tachy_mild"):
                "a rising pulse after surgery raises the question of a wound infection or "
                "a clot, both of which are treatable early and serious late",
        },
        "priority_signals": ("followup_status", "heart_rate", "adherence_pct"),
    },

    "mental_health": {
        "focus": "Contact and attendance. Vitals say very little here.",
        "weights": {"followup_status": 1.8, "adherence_pct": 1.5, "refill_status": 1.5,
                    "weight_change_kg": 0.2, "sbp": 0.3, "heart_rate": 0.4, "spo2": 0.3},
        "phrasing": {
            ('weight_change_kg', 'gain_major'):
                "weight counts for little in mental health recovery, and a change this size is more often an effect of the medication than a warning about anything",
            ('weight_change_kg', 'gain_mild'):
                "weight gain is a common side effect of psychiatric medication rather than a risk signal, though it is worth mentioning because it is a common reason people stop taking them",
            ("followup_status", "missed"):
                "in mental health care a missed appointment is often the first sign of "
                "disengagement, and is the strongest predictor on this card. Direct "
                "contact matters more than any reading here",
            ("followup_status", "attended"):
                "the patient is still engaged with the service, which is the single most "
                "protective thing in this group",
            ("adherence_pct", "below_threshold"):
                "psychiatric medicines stopped part-way are a common route back to "
                "hospital, and side effects are usually the reason rather than intent",
            ("adherence_pct", "very_low"):
                "the regimen has effectively stopped. In mental health this needs a "
                "conversation rather than a reminder, since the reason is usually side "
                "effects or a decision the patient has made",
        },
        "priority_signals": ("followup_status", "adherence_pct", "refill_status"),
    },

    "general": {
        "focus": "The standard post-discharge panel.",
        "weights": {},
        "priority_signals": ("weight_change_kg", "adherence_pct", "sbp",
                             "refill_status", "followup_status"),
    },
}


def profile_for(group: str) -> dict:
    """The profile for a group, with defaults filled in for anything unset."""
    p = GROUP_PROFILES.get(group) or GROUP_PROFILES["general"]
    return {
        "focus": p.get("focus", ""),
        "weights": p.get("weights", {}),
        "rules": p.get("rules", {}),
        "phrasing": p.get("phrasing", {}),
        "priority_signals": p.get("priority_signals", _ALL_SIGNALS),
    }


def signal_plan(group: str) -> dict:
    """
    What the weekly logging form should ask this patient for.

    Returns the signals that matter most for their condition and the ones that
    can be collapsed, so somebody with chronic lung disease is asked for oxygen
    saturations first and not made to weigh themselves every week for a number
    nobody will act on.
    """
    p = profile_for(group)
    signals = signals_for(group)
    # Condition-specific signals lead: they are the reason this group has its own
    # plan at all, and they are the ones the patient will not think to report.
    extras = [s for s in GROUP_SIGNALS.get(group, ()) if s in signals]
    priority = extras + [s for s in p["priority_signals"]
                         if s in signals and s not in extras]
    weights = p["weights"]
    optional = [s for s in signals if s not in priority]
    return {
        "group": group,
        "label": GROUP_LABELS_LOCAL.get(group, group.replace("_", " ").title()),
        "focus": p["focus"],
        "priority": [{"key": s, "label": signals[s][0],
                      "condition_specific": s in extras} for s in priority],
        "optional": [{"key": s, "label": signals[s][0],
                      "condition_specific": s in extras} for s in optional],
        # A signal weighted below 0.5 is one this group is barely scored on, and
        # the form should say so rather than implying every reading counts equally.
        "low_value": [signals[s][0] for s in signals if weights.get(s, 1.0) < 0.5],
        "condition_specific": [{"key": s, "label": signals[s][0]} for s in extras],
    }


# Labels live in models/icd_groups, but importing it here would make the two
# modules circular - monitoring_rules is imported by the scoring path and
# icd_groups is not. A local copy keeps the dependency one-directional.
GROUP_LABELS_LOCAL = {
    "heart_failure": "Heart failure",
    "renal": "Kidney disease",
    "respiratory": "Chronic lung disease",
    "sepsis_infection": "Sepsis or serious infection",
    "oncology": "Cancer",
    "diabetes": "Diabetes",
    "cardiac_other": "Heart disease",
    "neuro_stroke": "Stroke or neurological injury",
    "surgical_injury": "Surgery or injury recovery",
    "mental_health": "Mental health",
    "general": "General recovery",
}


# ---------------------------------------------------------------------------
# Where each number came from
# ---------------------------------------------------------------------------
# A monitoring programme does not have one feed, it has several, and they differ
# in how much they can be trusted. A pharmacy dispensing record is an external
# fact; an adherence percentage typed into an app is the patient's own account
# of it. Showing the origin next to the value is what lets a coordinator weigh
# them differently.
#
# THE NAMES BELOW ARE FICTIONAL. No integration exists yet. They are assigned
# deterministically from the patient id so that one patient keeps the same
# pharmacy and the same device hub across every week, which is what makes the
# cards read like a real programme rather than a random draw.

_DEVICE_HUBS = [
    "VitalLink Home Hub",
    "CareRing Home Kit",
    "Medley Home Monitor",
    "Tempo Remote Care Kit",
]

_ADHERENCE_APPS = [
    "MedMinder app",
    "PillPath app",
    "DoseLog app",
]

# Symptoms the patient reports themselves. Deliberately a SEPARATE feed from the
# medication app: "sputum changed colour" and "took 90% of doses" are different
# kinds of claim, and attributing a symptom to the pill-tracking app would
# misrepresent where it came from.
_SYMPTOM_APPS = [
    "Preventra daily check-in",
    "DayCheck symptom diary",
    "WellTrack check-in",
]

_PHARMACIES = [
    "Riverside Community Pharmacy",
    "Oakwood Family Pharmacy",
    "Bay Street Pharmacy",
    "Northgate Dispensary",
    "MedPlus Pharmacy #214",
]

_CLINICS = [
    "Northgate Cardiology Clinic",
    "St Mary's Outpatient Clinic",
    "Lakeside Family Practice",
    "Central City Follow-Up Clinic",
]

# Which pool each signal draws from, and how that feed describes itself.
_SIGNAL_CHANNEL = {
    "weight_change_kg": ("device", "connected scale, reading pushed nightly"),
    "sbp":              ("device", "home blood-pressure cuff, reading pushed nightly"),
    "heart_rate":       ("device", "home blood-pressure cuff, reading pushed nightly"),
    "spo2":             ("device", "fingertip oximeter, reading pushed nightly"),
    "adherence_pct":    ("app", "doses the patient confirmed in the app"),
    "refill_status":    ("pharmacy", "dispensing record from the pharmacy claims feed"),
    "followup_status":  ("clinic", "attendance marked in the clinic scheduling system"),

    # Condition-specific. Split by how the reading is actually obtained: a
    # thermometer and a step counter are devices, everything else is the patient
    # telling you something.
    "temperature_c":       ("device", "connected thermometer reading"),
    "walk_distance_pct":   ("device", "step and distance count from the wearable"),
    "orthopnoea_pillows":  ("symptom", "reported by the patient in the check-in"),
    "ankle_swelling":      ("symptom", "reported by the patient in the check-in"),
    "sputum_change":       ("symptom", "reported by the patient in the check-in"),
    "rescue_inhaler_uses": ("symptom", "counted by the patient in the check-in"),
    "wound_status":        ("symptom", "photograph and description sent by the patient"),
    "pain_trend":          ("symptom", "reported by the patient in the check-in"),
    "antibiotic_course":   ("symptom", "confirmed by the patient in the check-in"),
    "new_confusion":       ("symptom", "reported by the patient or their caretaker"),
}

_POOLS = {
    "device": _DEVICE_HUBS,
    "app": _ADHERENCE_APPS,
    "symptom": _SYMPTOM_APPS,
    "pharmacy": _PHARMACIES,
    "clinic": _CLINICS,
}

# How a feed describes itself when it carries several signals at once. The
# per-signal channel above is wrong in that case - a hub that reports weight,
# blood pressure and saturation is not "a connected scale".
_KIND_CHANNEL = {
    "device": "home readings pushed nightly from the connected scale, cuff and oximeter",
    "app": "doses the patient confirmed in the app",
    "symptom": "symptoms the patient reported in their weekly check-in",
    "pharmacy": "dispensing record from the pharmacy claims feed",
    "clinic": "attendance marked in the clinic scheduling system",
}

# What a patient's own submission through the Preventra portal is attributed to.
_SELF_REPORTED = {
    "kind": "self",
    "feed": "Preventra patient app",
    "channel": "entered by the patient or their caretaker",
}

# A week where nothing arrived. The values on the card are last week's, still
# standing, and saying they came from a pharmacy or a device would be a lie
# about a week in which neither reported.
CARRIED_FORWARD_SOURCE = {
    "kind": "carried",
    "feed": "Carried forward",
    "channel": "nothing was received this week, so the previous readings still stand",
}

# The discharge baseline is not a monitoring feed at all - it is the model.
MODEL_SOURCE = {
    "kind": "model",
    "feed": "Preventra risk model",
    "channel": "discharge record in the hospital EHR, attributed with SHAP",
}


def _pick(pool: list, patient_id: str, salt: str) -> str:
    """Stable choice from a pool. Same patient, same feed, every week."""
    digest = hashlib.md5(f"{patient_id}|{salt}".encode("utf-8")).hexdigest()
    return pool[int(digest, 16) % len(pool)]


def source_for(signal_key: str, patient_id: str, origin: str = "programme") -> dict:
    """
    The named feed a signal arrived on, as {kind, feed, channel}.

    `origin` is the weekly document's own source field. Anything the patient
    submitted themselves is attributed to the portal regardless of signal,
    because that is what actually happened - a weight the patient typed in did
    not come off a connected scale.
    """
    if origin == "patient_reported":
        return dict(_SELF_REPORTED)

    kind, channel = _SIGNAL_CHANNEL.get(signal_key, ("app", "monitoring feed"))
    return {
        "kind": kind,
        "feed": _pick(_POOLS[kind], patient_id, kind),
        "channel": channel,
    }


def week_sources(patient_id: str, origin: str = "programme",
                 group: str = "general") -> list:
    """
    Every feed that reports into one monitoring week, deduplicated, each with
    the signals it carries. This is the footer of a weekly card: which systems
    were heard from, not just which values happened to make the top three.
    """
    if origin == "patient_reported":
        return [{**_SELF_REPORTED,
                 "signals": [label for label, _, _ in signals_for(group).values()]}]

    by_feed = {}
    for key, (label, _, _) in signals_for(group).items():
        src = source_for(key, patient_id, origin)
        entry = by_feed.setdefault(src["feed"], {**src, "signals": []})
        entry["signals"].append(label)

    for entry in by_feed.values():
        if len(entry["signals"]) > 1:
            entry["channel"] = _KIND_CHANNEL.get(entry["kind"], entry["channel"])
    return list(by_feed.values())


# ---------------------------------------------------------------------------
# Red flags
# ---------------------------------------------------------------------------
# Readings that need assessing the same day whatever the patient was admitted
# for. The group weights above describe which signals are INFORMATIVE for a
# condition - weight says little in mental health - but a saturation of 85% is
# an emergency in anyone. So a red flag is exempt from down-weighting, adds at
# least RED_FLAG_MIN_POINTS, and while one is present the score cannot fall
# below the previous week's: an attended appointment must never make a patient
# with a saturation of 85% look like they are recovering.
#
# Thresholds are standard early-warning cut-offs (NEWS2 red-score values and the
# usual sepsis screen). Like the weights, they are written here to be argued with.

RED_FLAGS = {
    "spo2":          (lambda v: v < 90, "oxygen saturation below 90%"),
    "sbp":           (lambda v: v < 90 or v >= 180, "systolic blood pressure below 90 or 180 and over"),
    "heart_rate":    (lambda v: v >= 130, "heart rate 130 or over"),
    "temperature_c": (lambda v: v >= 39.0, "temperature 39 C or over"),
    "new_confusion": (lambda v: v == "yes", "new confusion"),
}
RED_FLAG_MIN_POINTS = 10.0


def red_flags(obs: dict) -> list:
    """The red-flag readings in one week's observations, as signal keys."""
    out = []
    for key, (test, _) in RED_FLAGS.items():
        value = (obs or {}).get(key)
        if value is None:
            continue
        try:
            if test(value):
                out.append(key)
        except TypeError:
            continue
    return out


def weights_for(groups) -> dict:
    """Signal weights for a patient with SEVERAL conditions.

    Each group's weights say how informative a signal is for that condition, so
    a patient takes the strongest weight any of their conditions gives: bipolar
    disorder alongside liver failure must not mute the oxygen saturation the
    liver disease makes important. A group without an entry for a signal counts
    it at the ordinary weight of 1.0, and "general" - a diagnosis that matched no
    group - counts everything at 1.0.
    """
    groups = [g for g in (groups or []) if g] or ["general"]
    merged: dict = {}
    for key in set(SIGNAL_RULES) | set(DISEASE_SIGNALS):
        merged[key] = max(profile_for(g)["weights"].get(key, 1.0) for g in groups)
    return merged


def score_week(baseline: float, obs: dict, carry: float, variant: int = 0,
               group: str = "general", groups=None, prev_score=None,
               flag_floor=None) -> tuple:
    """
    Apply the monitoring rules to one week of observations.

    Returns (score, total_adjustment, [(points, driver_string), ...]) with the
    driver list already sorted by absolute contribution.

    `variant` selects between the interchangeable phrasings of each rule. Pass
    something stable - a week number, or a hash of patient and week - so the
    same card always reads the same way. Points do not depend on it.

    `group` is the patient's clinical group from models/icd_groups. It scales
    the points, can replace a rule outright where the direction of risk differs,
    and can override the wording so the card explains the finding in terms of
    the patient's condition. Passing "general" reproduces the ungrouped
    behaviour exactly.

    `groups` is EVERY condition the patient has (models/icd_groups
    .monitoring_groups), plan group first. Weights are merged across them - see
    weights_for. Without it, only `group` counts, as before.

    `prev_score` is last week's score. While a red flag is present the score
    cannot fall below it - see RED_FLAGS.

    `flag_floor` is where the Medium band starts. A red flag means "assess
    today", and a card that says that beside a Low band contradicts itself: a
    patient with a low discharge score plus the minimum red-flag points could
    still land under it. Pass it and a flagged week reads at least Medium.
    """
    profile = profile_for(group)
    weights = weights_for(groups) if groups else profile["weights"]
    overrides, phrasing_overrides = profile["rules"], profile["phrasing"]
    flagged = set(red_flags(obs))

    contributions, flag_contribs = [], []
    total = flag_total = 0.0
    for offset, (key, (label, fmt, default_rule)) in enumerate(signals_for(group).items()):
        if key not in obs:
            if key not in DISEASE_NEUTRAL:
                # A shared reading this week did not report. A real feed rarely
                # sends all seven (ingestion/schema.py Readings), so it adds
                # nothing and stays off the card rather than being invented.
                continue
            # A disease signal the caller did not supply. Scoring its neutral
            # value contributes nothing rather than treating silence as good
            # news - see DISEASE_NEUTRAL.
            obs = {**DISEASE_NEUTRAL, **obs}
        value = obs[key]
        rule = overrides.get(key, default_rule)
        points, branch, phrasings = rule(value)

        if key in flagged:
            # Never down-weighted, never normalised away, and big enough to
            # matter: see RED_FLAGS.
            points = round(max(points * max(weights.get(key, 1.0), 1.0), RED_FLAG_MIN_POINTS), 2)
            why = phrasings[(variant + offset) % len(phrasings)] if not isinstance(phrasings, str) else phrasings
            flag_total += points
            flag_contribs.append((points, f"{label}: {fmt(value)} (red flag - {why})"))
            continue

        # Scale, but never flip a sign: a weight of 0 silences a signal, it does
        # not turn a warning into reassurance.
        points = round(points * weights.get(key, 1.0), 2)

        why = phrasing_overrides.get((key, branch))
        # A plan's wording that plays a signal down ("weight is not a useful
        # signal during surgical recovery") is only true while the signal IS
        # played down. When another of the patient's conditions raised its
        # weight, the shared wording is used instead.
        if why is not None and weights.get(key, 1.0) > profile["weights"].get(key, 1.0):
            why = None
        if why is None:
            if isinstance(phrasings, str):
                phrasings = (phrasings,)
            # Offset per signal so one card does not use variant 0 for all seven
            # rules and then variant 1 for all seven the following week.
            why = phrasings[(variant + offset) % len(phrasings)]

        total += points
        contributions.append((points, f"{label}: {fmt(value)} ({why})"))

    # Equalise the range across groups, and scale the displayed contributions by
    # the same factor so the card's numbers still add up to the adjustment.
    normalised = _normalise(total, group, weights)
    if total and abs(total - normalised) > 1e-9:
        factor = normalised / total
        contributions = [(round(p * factor, 2), d) for p, d in contributions]

    score = float(np.clip(baseline + normalised + flag_total + carry, SCORE_FLOOR, SCORE_CEIL))
    if flagged and prev_score is not None:
        score = max(score, float(prev_score))
    if flagged and flag_floor is not None:
        score = max(score, float(flag_floor))
    contributions.sort(key=lambda c: abs(c[0]), reverse=True)
    # Red flags lead the card: they are what someone must act on today.
    flag_contribs.sort(key=lambda c: c[0], reverse=True)
    return round(score, 1), normalised + flag_total, flag_contribs + contributions


def variant_for(patient_id: str, week_number: int) -> int:
    """
    A stable phrasing variant for one patient-week.

    Derived, never drawn: a random pick would reword every card on every page
    refresh, which reads as instability in the data rather than as variety.
    """
    digest = hashlib.md5(f"{patient_id}|{week_number}".encode("utf-8")).hexdigest()
    return int(digest[:8], 16)


# Bands whose silence is a reason to act rather than to wait.
CONTACT_WATCH_BANDS = ("Medium", "High")
# Readings are weekly, so a week and two days' grace without any is late.
CONTACT_DUE_DAYS = 9
# The window the programme watches: the 30 days a readmission is counted in.
# Silence after it is the end of monitoring, not a missed contact.
MONITORING_DAYS = 30


def _as_date(value):
    """A stored date ("2026-10-02", a datetime or a date) as a date, or None."""
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return datetime.strptime(str(value or "")[:10], "%Y-%m-%d").date()
    except ValueError:
        return None


def _discharge_date(weeks: list):
    """Week 0's date, or a later week's date less its days after discharge."""
    for w in weeks or []:
        when = _as_date(w.get("week_date"))
        if when is None:
            continue
        if int(w.get("week_number", 0) or 0) == 0:
            return when
        days = w.get("days_after_discharge")
        if days is not None:
            return when - timedelta(days=int(days))
    return None


def contact_gap(weeks: list, today=None) -> dict:
    """
    Whether the latest monitoring weeks went without contact, and whether that
    silence needs someone to act.

    `weeks` are one patient's weekly documents, oldest first, each with
    `week_number`, `observed`, `risk_band` and `red_flags`, and - for the check
    against the calendar - `week_date` (and `days_after_discharge`). Week 0 is
    the discharge and always counts as contact.

    A week without contact holds the last score: nothing new was measured, so
    the number is not moved either way. But holding it is not the same as
    reassurance. A patient whose last readings put them at Medium or High, or
    raised a red flag, and who then cannot be reached, is overdue for contact -
    the warning signs are open and nobody knows how they have gone since. The
    trend page, the worklist status and the "needs attention" list all read
    this one answer.

    Silence shows up two ways. The simulator writes a week with nothing in it
    (`observed: False`). Real check-ins have no document for a week nobody
    reported, so with `today` (the hospital's date) the last contact is also
    measured against the calendar: more than CONTACT_DUE_DAYS without readings,
    while the patient is inside the MONITORING_DAYS window, is silence too.
    A discharge with no readings since counts from the discharge itself.

    Returns {"weeks_without_contact": n, "last_contact_week": k,
    "last_contact_band": band, "last_contact_date": "YYYY-MM-DD" or None,
    "days_without_contact": d or None, "overdue": bool}. n is 0 when the
    latest week was observed and on time; d is set only when the calendar
    says the contact is late.
    """
    silent, last = 0, None
    for w in reversed(weeks or []):
        if int(w.get("week_number", 0) or 0) > 0 and w.get("observed") is False:
            silent += 1
            continue
        last = w
        break
    band = (last or {}).get("risk_band")
    open_signs = band in CONTACT_WATCH_BANDS or bool((last or {}).get("red_flags"))

    last_date = _as_date((last or {}).get("week_date"))
    late_days = None
    now = _as_date(today)
    if now is not None and last_date is not None:
        discharged = _discharge_date(weeks)
        inside = discharged is not None and (now - discharged).days <= MONITORING_DAYS
        elapsed = (now - last_date).days
        if inside and elapsed > CONTACT_DUE_DAYS:
            late_days = elapsed
    return {"weeks_without_contact": max(silent, (late_days or 0) // 7),
            "last_contact_week": int((last or {}).get("week_number", 0) or 0),
            "last_contact_band": band,
            "last_contact_date": last_date.isoformat() if last_date else None,
            "days_without_contact": late_days,
            "overdue": (silent > 0 or late_days is not None) and open_signs}


def contact_gap_text(gap: dict) -> str:
    """How long a patient has gone without readings, for an overdue contact:
    "No readings for 12 days since week 2" - in days when the calendar showed
    it, in weeks when stored silent weeks did."""
    since = gap["last_contact_week"]
    if gap.get("days_without_contact") is not None:
        n, unit = gap["days_without_contact"], "day"
    else:
        n, unit = gap["weeks_without_contact"], "week"
    return (f"No readings for {n} {unit}{'s' if n != 1 else ''} since "
            + ("discharge" if since == 0 else f"week {since}"))

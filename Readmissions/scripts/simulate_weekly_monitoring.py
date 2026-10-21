#!/usr/bin/env python3
"""
Weekly post-discharge monitoring for the Preventra dashboard.

WHAT THIS IS
------------
A post-discharge monitoring programme does not re-draw a full inpatient lab
panel every week. What it actually records is:

    vitals              weight, blood pressure, heart rate, oxygen saturation
    medication adherence proportion of prescribed days actually covered
    pharmacy activity   was the refill due this week collected
    follow-up           was the post-discharge appointment attended

Those are the things a care coordinator can see, act on, and change. So those
are what the weekly cards show and what moves the weekly score.

HOW THE SCORE IS BUILT
----------------------
    week 0  the REAL calibrated model's discharge probability over 94 features,
            explained with REAL SHAP attribution. Unchanged.

    week n  that discharge baseline, adjusted by the monitored signals for the
            week through the explicit, bounded rules in SIGNAL_RULES below,
            plus a carry term so an unresolved problem keeps compounding
            instead of resetting every Monday.

This is deliberately NOT the gradient-boosted model scoring vitals. The model
was trained on discharge-time features and contains no vitals, adherence or
pharmacy feature - it cannot score them, and pretending otherwise would be the
dishonest option. The weekly layer is therefore a transparent clinical
guardrail on top of a calibrated ML baseline, which is the standard shape for
a monitoring programme that does not yet have post-discharge outcome data of
its own to train on.

Direction of every rule: deterioration in vitals or adherence pushes risk UP,
sustained good control pushes it DOWN. Weights come from well-established
associations - a >2 kg weekly weight gain as a fluid-overload signal, the 80%
PDC threshold for adequate adherence, attended early follow-up reducing
readmission - and are recorded here rather than buried in code so a clinician
can argue with them.

The weights are deliberately ASYMMETRIC: the worst week can add about 27 points
while the best week removes about 6. Two reasons. Clinically, deterioration is
strong evidence of trouble whereas perfect adherence is only weak evidence of
safety - a patient can take every pill and still decompensate. Empirically, the
trained model behaves the same way: perturbing its lab inputs across their full
deteriorating range moved scores +10.2 points on average, while normalising the
same inputs moved them only -1.8. Fixed discharge-time features set a risk floor
that good behaviour cannot go below, and these rules preserve that floor.

WHAT IS REAL AND WHAT IS SIMULATED
----------------------------------
    REAL        the discharge score and its SHAP drivers, from the trained model
    REAL        the patient's discharge-time feature vector
    SIMULATED   the weekly vitals, adherence, pharmacy and follow-up records
    SIMULATED   which patients deteriorate

Every document carries source="simulated" and the UI labels it. Replace this
with real programme data when the feed exists; the document shape and the API
do not change.

HOW TRAJECTORIES ARE CHOSEN
---------------------------
Not from the admission's `readmit_30d` label. The worklist shows each patient's
MOST RECENT admission, and a last admission has nothing after it, so its label
is censored to 0 by construction.

Deterioration is instead drawn from the model's own calibrated discharge
probability, which is precisely what calibration licenses: a patient scored at
40% should go on to deteriorate 40% of the time. The cohort then reproduces the
real ~20% event rate AND puts the deterioration where the model expects it.

Usage
-----
    python scripts/simulate_weekly_monitoring.py --dry-run
    python scripts/simulate_weekly_monitoring.py
    python scripts/simulate_weekly_monitoring.py --weeks 4 --limit 0
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import timedelta

import numpy as np
import pandas as pd
from dotenv import load_dotenv

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from models.mimic_drivers import (inner_estimator, load_bundle,  # noqa: E402
                                  preprocess_for_shap, top_drivers)
# The rule set lives in models/monitoring_rules so this script and the API's
# patient self-logging endpoint score identical observations identically.
from models.monitoring_rules import (CARRY, SCORE_CEIL,  # noqa: E402,F401
                                     SCORE_FLOOR, SIGNAL_RULES, red_flags, score_week,
                                     variant_for)
# The patient's clinical group decides which signals matter for them, so it is
# resolved once per patient here and carried on every weekly document.
from models.icd_groups import classify, monitoring_groups  # noqa: E402
from models.monitoring_rules import GROUP_SIGNALS  # noqa: E402
from models.discharge_baseline import derive_baseline  # noqa: E402

_RESULTS = "data/mimic/model/results"
DEFAULT_BUNDLE = f"{_RESULTS}/phase1_model.joblib"
DEFAULT_MATRIX = f"{_RESULTS}/phase1_matrix.parquet"
COLLECTION = "weekly_monitoring"
MODEL_VERSION = "mimic-hgb-calibrated-v1"
SOURCE = "simulated"

# Fraction of weeks where the patient records nothing at all - no home reading,
# no contact. A real programme has these, and the score is carried forward and
# flagged rather than invented.
NO_CONTACT_RATE = 0.12

# ---------------------------------------------------------------------------
# Things that happen on a schedule
# ---------------------------------------------------------------------------
# An appointment, a pharmacy refill and an antibiotic course are EVENTS, not
# readings: each happens once (or on a fixed cycle) and then stays happened.
# Drawing them afresh every week produced cards nobody could believe - an
# appointment "missed" in week 1 and "attended" in week 3 and "missed" again in
# week 4, a refill collected two weeks running from a 30-day supply. A
# PatientCourse carries them from week to week so each card follows from the
# one before it.

# A 7-day post-discharge review is the standard for heart failure, other
# cardiac and kidney admissions; everyone else is seen at about 14 days.
EARLY_FOLLOWUP_GROUPS = {"heart_failure", "cardiac_other", "renal"}
# Most discharge prescriptions cover 30 days, so the first refill falls in
# week 4; a minority are written for 14 days and are refilled in weeks 2 and 4.
SHORT_SUPPLY_RATE = 0.30
# About one infection admission in six goes home without antibiotics.
NO_ANTIBIOTIC_RATE = 0.17


def followup_due_week(group: str) -> int:
    return 1 if group in EARLY_FOLLOWUP_GROUPS else 2


class PatientCourse:
    """One patient's 30 days: the follow-up appointment, the pharmacy supply
    and the antibiotic course, which carry from one week to the next."""

    def __init__(self, group: str, deteriorating: bool, rng: np.random.Generator):
        self.group = group
        self.deteriorating = deteriorating
        self.followup_due = followup_due_week(group)
        self.followup_done = False
        self.refill_weeks = (2, 4) if rng.random() < SHORT_SUPPLY_RATE else (4,)
        self.antibiotics = "not_prescribed" if rng.random() < NO_ANTIBIOTIC_RATE else "ongoing"
        # Each patient's own level for every reading, kept for all four weeks.
        # Week-to-week noise on its own made the same patient look like four
        # different people: a deteriorating heart failure patient on four pillows
        # with 13% of usual walking one week and two pillows at 55% the next,
        # while the weight kept rising. Most of the spread is now between
        # patients, and a little is left for the week.
        self.offset = {"fluid": rng.normal(0, 0.5), "sbp": rng.normal(0, 8),
                       "hr": rng.normal(0, 6), "spo2": rng.normal(0, 1.0),
                       "congestion": rng.normal(0, 0.3), "walk": rng.normal(0, 5)}

    def followup(self, week: int, drift: float, rng: np.random.Generator) -> str:
        """Not due before the booked week; then attended, or missed and still
        outstanding until a rebooked visit is attended; not due once seen."""
        if self.followup_done or week < self.followup_due:
            return "not due"
        p_attend = (float(np.clip(0.75 - 0.35 * drift, 0.25, 0.75)) if self.deteriorating
                    else 0.88)
        if rng.random() < p_attend:
            self.followup_done = True
            return "attended"
        return "missed"

    def refill(self, week: int, drift: float, rng: np.random.Generator) -> str:
        """Due only in a week the supply runs out."""
        if week not in self.refill_weeks:
            return "not due"
        missed, late = ((0.08 + 0.40 * drift, 0.12 + 0.15 * drift) if self.deteriorating
                        else (0.04, 0.10))
        r = rng.random()
        return "missed" if r < missed else "late" if r < missed + late else "collected"

    def antibiotic(self, week: int, drift: float, rng: np.random.Generator) -> str:
        """Ongoing for the first week or two, then completed - or stopped early,
        which stays stopped."""
        if self.antibiotics in ("not_prescribed", "stopped_early"):
            return self.antibiotics
        if self.deteriorating and week >= 2 and rng.random() < 0.10 + 0.40 * drift:
            self.antibiotics = "stopped_early"
            return self.antibiotics
        if week >= 3:
            self.antibiotics = "completed"
        return self.antibiotics


def _walk_distance(drift: float, spo2: float, congestion: float, rng, offset: float = 0.0) -> int:
    """Exercise tolerance falls with the same deterioration the vitals show:
    a lower saturation and more retained fluid both cut how far someone walks."""
    mean = 96 - 45 * drift - 2.5 * max(0.0, 94 - spo2) - 6 * max(0.0, congestion - 1.0) + offset
    return round(float(np.clip(rng.normal(mean, 4), 5, 130)))


def _level(x: float, cuts: tuple, names: tuple) -> str:
    """The category whose cut `x` has passed: names[i] once x >= cuts[i-1]."""
    for cut, name in zip(reversed(cuts), reversed(names[1:])):
        if x >= cut:
            return name
    return names[0]


def observe_disease_signals(group: str, drift: float, rng: np.random.Generator,
                            fluid: float = 0.0, spo2: float = 96.0,
                            course: "PatientCourse | None" = None, week: int = 1,
                            deteriorating: bool = False) -> dict:
    """
    The condition-specific readings for one week, derived from the SAME state
    the shared vitals came from, so a card cannot contradict itself.

    `fluid` is the weight gained since discharge and `spo2` this week's
    saturation. Ankle swelling and pillows follow the fluid - marked swelling
    with a stable weight was possible when they were drawn separately, and it
    describes litres of retained fluid that the scales say are not there.
    Inhaler use follows the saturation; sputum, the wound and the temperature
    follow the deterioration; new confusion follows a fever or a severe decline.

    Only the signals this group is actually asked for are produced - see
    GROUP_SIGNALS. Generating readings nobody scores would put numbers on a card
    that mean nothing.
    """
    keys = GROUP_SIGNALS.get(group, ())
    if not keys:
        return {}
    out = {}
    off = course.offset if course is not None else {"congestion": 0.0, "walk": 0.0}
    congestion = fluid + off["congestion"] + rng.normal(0, 0.15)
    if "orthopnoea_pillows" in keys:
        out["orthopnoea_pillows"] = int(np.clip(
            1 + (congestion >= 1.8) + (congestion >= 3.2) + (spo2 < 92), 0, 4))
    if "ankle_swelling" in keys:
        out["ankle_swelling"] = _level(congestion, (1.2, 2.6), ("none", "mild", "marked"))
    if "walk_distance_pct" in keys:
        out["walk_distance_pct"] = _walk_distance(
            drift, spo2, congestion if "ankle_swelling" in keys else 0.0, rng, off["walk"])
    if "rescue_inhaler_uses" in keys:
        out["rescue_inhaler_uses"] = int(np.clip(
            rng.normal(1.5 + 3.5 * max(0.0, 95 - spo2) + 6 * drift, 1.5), 0, 40))
    if "sputum_change" in keys:
        out["sputum_change"] = _level(drift + rng.normal(0, 0.15), (0.35, 0.65, 0.95),
                                      ("none", "volume", "purulent", "both"))
    wound = None
    if "wound_status" in keys:
        wound = _level(drift + rng.normal(0, 0.15), (0.35, 0.65, 0.95),
                       ("clean", "red", "discharge", "opening"))
        out["wound_status"] = wound
    if "temperature_c" in keys:
        infected_wound = 0.6 if wound in ("discharge", "opening") else 0.0
        out["temperature_c"] = round(float(np.clip(
            rng.normal(36.8 + 1.5 * drift + infected_wound, 0.3 if deteriorating else 0.25),
            35.0, 41.0)), 1)
    if "pain_trend" in keys:
        p = drift + rng.normal(0, 0.15)
        out["pain_trend"] = ("worse" if wound in ("discharge", "opening") or p >= 0.7
                             else "unchanged" if p >= 0.3 else "improving")
    if "antibiotic_course" in keys:
        course = course or PatientCourse(group, deteriorating, rng)
        out["antibiotic_course"] = course.antibiotic(week, drift, rng)
    if "new_confusion" in keys:
        fever = out.get("temperature_c", 36.8)
        out["new_confusion"] = ("yes" if deteriorating and (
            (fever >= 38.8 and rng.random() < 0.5) or (drift > 0.9 and rng.random() < 0.35))
            else "no")
    return out


# How strongly a condition shows up in the VITALS as it worsens. Mental health
# deteriorates through contact, adherence and refills - its vitals stay near
# normal - so a patient whose only condition is psychiatric must not drift into
# low saturations or a racing pulse. Every other condition, and any diagnosis
# that matched no group, keeps the full vital drift. A patient takes the
# strongest relevance among their conditions.
VITAL_RELEVANCE = {"mental_health": 0.1}


def vital_relevance(groups) -> float:
    return max((VITAL_RELEVANCE.get(g, 1.0) for g in (groups or ["general"])), default=1.0)


def observe_week(week: int, n_weeks: int, deteriorating: bool,
                 severity: float, rng: np.random.Generator,
                 group: str = "general", groups=None,
                 course: "PatientCourse | None" = None) -> dict:
    """
    Generate one week of monitoring observations.

    `severity` scales how fast a deteriorating patient declines, so the cohort
    contains slow drifts as well as sharp falls rather than one uniform curve.
    `course` carries the patient's appointment, pharmacy supply and antibiotics
    from week to week; pass the same one for every week of a patient. Without
    it the week is generated as if it were the patient's only one.
    """
    course = course or PatientCourse(group, deteriorating, rng)
    # 0 at discharge, 1 at the end of the window - how far along the trajectory.
    t = week / max(n_weeks, 1)

    off = course.offset
    if deteriorating:
        drift = severity * t
        # Vitals follow the patient's conditions; adherence, refills and
        # follow-up deteriorate for everyone.
        vd = drift * vital_relevance(groups or [group])
        fluid = rng.normal(0.9 + 2.4 * vd + off["fluid"], 0.4)
        adherence = float(np.clip(rng.normal(88 - 45 * drift, 9), 5, 100))
        sbp = rng.normal(138 + 26 * vd + off["sbp"], 7)
        hr = rng.normal(82 + 26 * vd + off["hr"], 5)
        spo2 = rng.normal(96 - 6.0 * vd + off["spo2"], 0.9)
    else:
        drift = 0.0
        fluid = rng.normal(-0.2 - 0.3 * t + off["fluid"] * 0.8, 0.35)
        adherence = float(np.clip(rng.normal(90 + 7 * t, 7), 30, 100))
        sbp = rng.normal(132 - 4 * t + off["sbp"], 6)
        hr = rng.normal(78 - 3 * t + off["hr"], 5)
        spo2 = rng.normal(96.5 + 0.6 * t + off["spo2"] * 0.8, 0.7)

    refill = course.refill(week, drift, rng)
    if refill == "missed":
        # The supply ran out before the refill, so the last days went untaken.
        adherence = max(5.0, adherence - 15)
    follow = course.followup(week, drift, rng)

    # In cancer the danger runs the other way: a declining patient LOSES weight
    # (poor intake, cachexia) and a recovering one holds or regains it, which is
    # what the oncology weight rule scores.
    weight = -fluid if group == "oncology" else fluid
    spo2_reading = int(np.clip(spo2, 82, 100))

    obs = {
        "weight_change_kg": round(float(weight), 1),
        "adherence_pct": round(adherence),
        "refill_status": refill,
        "followup_status": follow,
        "sbp": int(np.clip(sbp, 78, 215)),
        "heart_rate": int(np.clip(hr, 44, 145)),
        "spo2": spo2_reading,
    }
    obs.update(observe_disease_signals(group, drift, rng, fluid=float(fluid),
                                       spo2=float(spo2_reading), course=course, week=week,
                                       deteriorating=deteriorating))
    return obs


def choose_trajectories(proba0: np.ndarray, readmit: np.ndarray,
                        seed: int) -> np.ndarray:
    """
    Draw who deteriorates from the model's calibrated discharge probability.

    A genuine positive label, where one exists, still wins outright.
    """
    rng = np.random.default_rng(seed + 1)
    drawn = rng.random(len(proba0)) < proba0
    return np.asarray(drawn | (readmit == 1))


def _load_bands(fallback: float) -> dict:
    import json
    try:
        b = json.load(open("docs/mimic/band_thresholds_mimic.json"))
        return {"high": b["high_score_threshold"], "low": b["low_score_threshold"]}
    except FileNotFoundError:
        return {"high": 40.0, "low": 20.0}


def _band(score: float, t: dict) -> str:
    if score >= t["high"]:
        return "High"
    if score >= t["low"]:
        return "Medium"
    return "Low"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--bundle", default=DEFAULT_BUNDLE)
    ap.add_argument("--matrix", default=DEFAULT_MATRIX)
    ap.add_argument("--weeks", type=int, default=4)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--audit", action="store_true",
                    help="check the generated series for contradictions (scripts/audit_weekly_monitoring.py); "
                         "writes nothing")
    a = ap.parse_args()

    print("=" * 74)
    print("WEEKLY POST-DISCHARGE MONITORING")
    print("  week 0 : real calibrated model + real SHAP drivers")
    print("  week n : vitals, adherence, pharmacy and follow-up rules,")
    print("           weighted and worded for the patient's clinical group")
    print("=" * 74)

    b = load_bundle(a.bundle)
    model, FEATURES = b["model"], b["features"]
    hgb = inner_estimator(model)
    thresholds = _load_bands(float(b["threshold"]))
    print(f"  bands     : High>={thresholds['high']}%  Medium>={thresholds['low']}%")

    load_dotenv()
    from api.db_utils import get_db_name, get_mongo_client
    uri = os.environ.get("MONGO_URI", "mongodb://localhost:27017")

    # Atlas intermittently fails the TLS handshake on a single shard node and
    # pymongo surfaces it as AutoReconnect before it can fail over. It clears on
    # a retry, so a run that takes minutes of compute should not die on it.
    def _with_retry(fn, what, attempts=6):
        import time
        from pymongo.errors import AutoReconnect, ServerSelectionTimeoutError
        for n in range(1, attempts + 1):
            try:
                return fn()
            except (AutoReconnect, ServerSelectionTimeoutError) as exc:
                if n == attempts:
                    raise
                wait = min(2 ** n, 20)
                print(f"  [{what}] connection attempt {n} failed, retrying in {wait}s "
                      f"- {str(exc)[:70]}")
                time.sleep(wait)

    db = get_mongo_client(uri)[get_db_name()]
    wl = _with_retry(lambda: list(db["patient_worklist"].find(
        {"source": "mimic"},
        {"_id": 0, "patient_id": 1, "discharge_date": 1, "primary_diagnosis": 1,
         "primary_icd_code": 1, "secondary_diagnoses": 1})), "read worklist")
    if not wl:
        sys.exit("patient_worklist has no mimic patients - run load_mimic_to_mongo.py first")
    wl = pd.DataFrame(wl)
    if a.limit:
        wl = wl.head(a.limit)
    wl["subject_id"] = wl.patient_id.str.replace("MIMIC-", "", regex=False).astype("int64")
    print(f"  cohort    : {len(wl):,} patients from patient_worklist")

    groups = [classify(r.get("primary_icd_code", ""), r.get("primary_diagnosis", ""),
                       r.get("secondary_diagnoses") or [])
              for r in wl.to_dict("records")]
    from collections import Counter
    tally = Counter(g["group"] for g in groups)
    print("  groups    : " + ", ".join(f"{k} {v:,}" for k, v in tally.most_common()))
    conf = Counter(g["confidence"] for g in groups)
    print(f"              matched on principal code: {conf.get('high', 0):,}  "
          f"on a title: {conf.get('moderate', 0):,}  "
          f"defaulted to general: {conf.get('default', 0):,}")

    M = pd.read_parquet(a.matrix)
    M = M[M.subject_id.isin(set(wl.subject_id))]
    latest = (M.sort_values(["subject_id", "n_prior_adm"])
                .groupby("subject_id", as_index=False).tail(1)
                .set_index("subject_id")
                .loc[wl.subject_id].reset_index())

    # ---- week 0: the real model, unchanged --------------------------------
    print("\nscoring the discharge baseline with the real model ...")
    proba0 = model.predict_proba(latest[FEATURES])[:, 1]
    baseline = np.round(proba0 * 100, 1)

    print("computing discharge SHAP drivers ...")
    import shap
    explainer = shap.TreeExplainer(hgb)
    Xt, names = preprocess_for_shap(hgb, latest[FEATURES], FEATURES)
    sv = np.array(explainer.shap_values(Xt))
    raw0 = latest[FEATURES].reindex(columns=names)
    disch_drivers = [top_drivers(sv[i], names, raw0.iloc[i].values, top_n=3)
                     for i in range(len(latest))]

    readmit = latest.readmit_30d.to_numpy() if "readmit_30d" in latest else np.zeros(len(latest))
    deteriorating = choose_trajectories(proba0, readmit, a.seed)
    print(f"  trajectories: {deteriorating.sum():,} deteriorating "
          f"({deteriorating.mean():.1%}), {(~deteriorating).sum():,} recovering")

    # ---- weeks 1..n: monitoring observations ------------------------------
    print(f"\nsimulating {a.weeks} weeks of monitoring ...")
    disch = pd.to_datetime(wl.discharge_date, errors="coerce").to_numpy()
    sev_rng = np.random.default_rng(a.seed + 7)
    severity = sev_rng.uniform(0.55, 1.35, size=len(latest))

    docs = []
    for i in range(len(latest)):
        pid = wl.patient_id.iloc[i]
        d0 = disch[i]
        base = float(baseline[i])
        rng = np.random.default_rng(a.seed + i * 97)
        grp = groups[i]
        row = wl.iloc[i]
        conditions = monitoring_groups(row.get("primary_icd_code", "") or "",
                                       row.get("primary_diagnosis", "") or "",
                                       row.get("secondary_diagnoses") or [])
        # The patient's own reference point. Every relative reading below is
        # measured against this rather than against a population number.
        baseline_record = derive_baseline(pid, grp["group"])

        docs.append({
            "patient_id": pid, "week_number": 0, "days_after_discharge": 0,
            "week_date": (pd.Timestamp(d0).strftime("%Y-%m-%d")
                          if pd.notna(d0) else ""),
            "risk_score": base, "risk_band": _band(base, thresholds),
            "observed": True,
            "driver_1": disch_drivers[i][0],
            "driver_2": disch_drivers[i][1],
            "driver_3": disch_drivers[i][2],
            "model_version": MODEL_VERSION, "source": SOURCE,
            "scored_by": "model",
            "clinical_group": grp["group"],
            "group_label": grp["label"],
            "group_evidence": grp["evidence"],
            "group_confidence": grp["confidence"],
            "discharge_baseline": baseline_record,
            "simulated_trajectory": "deteriorating" if deteriorating[i] else "recovering",
        })

        prev_adj, prev_score = 0.0, base
        prev_obs, prev_drv = None, None
        # The appointment, the pharmacy supply and any antibiotic course carry
        # from week to week - see PatientCourse.
        course = PatientCourse(grp["group"], bool(deteriorating[i]), rng)
        last_contact = 0
        for wk in range(1, a.weeks + 1):
            # The first week always has its readings: there is nothing earlier to
            # carry forward, so a "no contact" first week used to be scored on new
            # readings while being marked as not reported. The draw is kept so
            # every later week's data is unchanged.
            contacted = rng.random() > NO_CONTACT_RATE or prev_obs is None
            if contacted:
                obs = observe_week(wk, a.weeks, bool(deteriorating[i]),
                                   float(severity[i]), rng, group=grp["group"],
                                   groups=conditions, course=course)
                # Phrasing varies per patient-week so four consecutive cards do
                # not repeat one sentence; the POINTS are variant-independent,
                # so the scores this writes are identical either way.
                score, adj, contribs = score_week(base, obs, CARRY * prev_adj,
                                                  variant=variant_for(pid, wk),
                                                  group=grp["group"], groups=conditions,
                                                  prev_score=prev_score,
                                                  flag_floor=thresholds["low"])
                drivers = [c[1] for c in contribs[:3]]
                prev_obs, prev_drv, prev_adj = obs, drivers, adj
                last_contact = wk
            else:
                # No contact this week: hold the last score and say so, rather
                # than inventing readings the programme never took. The readings
                # kept here are the LAST ones taken, shown as such (week
                # `last_contact_week`), and the appointment and pharmacy clock
                # does not advance - nobody heard how they went.
                obs, drivers, score = prev_obs, prev_drv, prev_score

            wdate = (pd.Timestamp(d0) + timedelta(days=7 * wk)) if pd.notna(d0) else None
            prev_score = score
            docs.append({
                "patient_id": pid, "week_number": wk, "days_after_discharge": 7 * wk,
                "week_date": wdate.strftime("%Y-%m-%d") if wdate is not None else "",
                "risk_score": score, "risk_band": _band(score, thresholds),
                "observed": bool(contacted),
                "last_contact_week": last_contact,
                "driver_1": drivers[0], "driver_2": drivers[1], "driver_3": drivers[2],
                "monitoring": obs,
                # Readings that need assessing today whatever else improved;
                # they make the week "Action required" (api/main.py). A week
                # nobody reported raises none: last week's flag was last week's.
                "red_flags": red_flags(obs) if contacted else [],
                "monitoring_groups": conditions,
                "model_version": MODEL_VERSION, "source": SOURCE,
                "scored_by": "monitoring_rules",
                "clinical_group": grp["group"],
                "group_label": grp["label"],
                "group_evidence": grp["evidence"],
                "group_confidence": grp["confidence"],
                "simulated_trajectory": "deteriorating" if deteriorating[i] else "recovering",
            })

    # ---- preview -----------------------------------------------------------
    df = pd.DataFrame([{k: d[k] for k in ("patient_id", "week_number", "risk_score",
                                          "risk_band", "observed")} for d in docs])
    print(f"\n  {len(docs):,} documents  ({len(latest):,} patients x {a.weeks + 1} weeks)")
    print("\n  mean score by week:")
    for wk, g in df.groupby("week_number"):
        det = [d for d in docs if d["week_number"] == wk
               and d["simulated_trajectory"] == "deteriorating"]
        rec = [d for d in docs if d["week_number"] == wk
               and d["simulated_trajectory"] == "recovering"]
        print(f"    week {wk}: all {g.risk_score.mean():5.1f}%   "
              f"deteriorating {np.mean([d['risk_score'] for d in det]):5.1f}%   "
              f"recovering {np.mean([d['risk_score'] for d in rec]):5.1f}%")

    sample = next(d for d in docs if d["week_number"] == 2 and d["monitoring"])
    print(f"\n  sample week-2 card for {sample['patient_id']} "
          f"({sample['risk_score']}% {sample['risk_band']}):")
    for k in ("driver_1", "driver_2", "driver_3"):
        print(f"    {sample[k]}")

    if a.audit:
        from scripts.audit_weekly_monitoring import audit, report
        print()
        report(audit(docs))
        print("\n  AUDIT - nothing written")
        return
    if a.dry_run:
        print("\n  DRY RUN - nothing written")
        return

    print(f"\nwriting to MongoDB.{COLLECTION} ...")
    _with_retry(lambda: db[COLLECTION].delete_many({"source": SOURCE}), "clear")
    for i in range(0, len(docs), 2000):
        batch = docs[i:i + 2000]
        _with_retry(lambda b=batch: db[COLLECTION].insert_many(b), f"insert {i}")
        print(f"    {min(i + 2000, len(docs)):>7,} / {len(docs):,}")
    _with_retry(lambda: db[COLLECTION].create_index(
        [("patient_id", 1), ("week_number", 1)]), "index")
    print(f"  {COLLECTION} <- {len(docs):,} docs")
    print("\ndone")


if __name__ == "__main__":
    main()

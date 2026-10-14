"""Weekly monitoring follows the diagnosis the patient was discharged with
(models/icd_groups, models/monitoring_rules and the weekly simulator).

Each test pins a mismatch found in an audit of the stored weekly series:
signals a group says are noise scored in full, chronic-lung oxygen targets
applied to acute respiratory failure, infections and overdoses on the wrong
plan, heart failure wording on other patients' cards, and cancer patients
whose simulated weight moved the opposite way to the rule scoring it.
"""
import re

import pytest

from models.icd_groups import classify, classify_all, monitoring_groups
from models.monitoring_rules import (GROUP_PROFILES, group_extremes, score_week,
                                     weights_for)

WEEK = {"weight_change_kg": 1.2, "adherence_pct": 90, "refill_status": "collected",
        "followup_status": "attended", "sbp": 130, "heart_rate": 80, "spo2": 96}


def plan(code="", title="", secondary=None):
    return classify(code, title, secondary or [])["group"]


# --------------------------------------------- which signals get weighed
def test_an_ordinary_secondary_diagnosis_does_not_undo_the_plan():
    """A hip fracture with hypertension is still a surgical patient: weight
    stays near-silent, as the surgical profile and its wording say."""
    groups = monitoring_groups("S72001A", "Fracture of right femur",
                               ["Essential (primary) hypertension"])
    assert groups == ["surgical_injury"]
    assert weights_for(groups)["weight_change_kg"] == 0.15


def test_an_unmatched_reason_for_admission_still_restores_the_vitals():
    groups = monitoring_groups("07044", "Chronic hepatitis C with hepatic coma",
                               ["Bipolar I disorder, most recent episode depressed"])
    assert groups == ["mental_health", "general"]
    assert weights_for(groups)["spo2"] == 1.0


@pytest.mark.parametrize("group", sorted(GROUP_PROFILES))
@pytest.mark.parametrize("kg", [1.2, 3.0])
def test_a_card_that_calls_weight_noise_does_not_score_it_as_a_warning(group, kg):
    """The wording and the points must agree: where a group's card says weight
    is not a useful signal, the gain adds almost nothing."""
    _, _, contribs = score_week(30.0, {**WEEK, "weight_change_kg": kg}, 0,
                                group=group, groups=[group])
    weight = next((p, d) for p, d in contribs if d.startswith("Weight change"))
    if re.search(r"not (a useful|a meaningful|the) signal|weight is not", weight[1]):
        assert weight[0] < 1.0


def test_wording_that_plays_a_signal_down_is_dropped_when_another_condition_raises_it():
    """Surgical plan, but heart failure makes weight matter: the card must not
    call the gain normal post-operative variation while scoring it in full."""
    _, _, contribs = score_week(30.0, WEEK, 0, group="surgical_injury",
                                groups=["surgical_injury", "heart_failure"])
    weight = next(d for _, d in contribs if d.startswith("Weight change"))
    assert "not a useful signal" not in weight
    _, _, alone = score_week(30.0, WEEK, 0, group="surgical_injury", groups=["surgical_injury"])
    assert "not a useful signal" in next(d for _, d in alone if d.startswith("Weight change"))


def test_merged_weights_stay_inside_the_general_range():
    worst_general = group_extremes("general")[0]
    bad = {"weight_change_kg": 3.0, "adherence_pct": 10, "refill_status": "missed",
           "followup_status": "missed", "sbp": 165, "heart_rate": 120, "spo2": 91}
    for groups in (["mental_health", "general"], ["surgical_injury", "general"],
                   ["respiratory", "diabetes"]):
        _, adjustment, _ = score_week(30.0, bad, 0, group=groups[0], groups=groups)
        assert adjustment <= worst_general + 1e-6


# ------------------------------------------------- diagnosis -> plan group
def test_acute_respiratory_failure_is_not_chronic_lung_disease():
    assert plan("A419", "Sepsis, unspecified organism",
                ["Acute respiratory failure with hypoxia"]) == "sepsis_infection"
    assert plan("I214", "Non-ST elevation myocardial infarction",
                ["Chronic respiratory failure with hypoxia"]) == "respiratory"
    assert plan("J441", "Chronic obstructive pulmonary disease with exacerbation") == "respiratory"


@pytest.mark.parametrize("code, title", [
    ("J189", "Pneumonia, unspecified organism"), ("486", "Pneumonia, organism unspecified"),
    ("N390", "Urinary tract infection, site not specified"), ("5990", ""),
    ("L03115", "Cellulitis of right lower limb"), ("6826", ""), ("U071", "COVID-19"),
    ("A0472", "Enterocolitis due to Clostridium difficile"), ("A419", "Sepsis")])
def test_serious_infections_are_on_the_infection_plan(code, title):
    result = classify(code, title, [])
    assert result["group"] == "sepsis_infection" and result["confidence"] == "high"


def test_an_infection_named_only_in_a_title_is_recognised():
    assert plan("", "Pneumonia, organism unspecified") == "sepsis_infection"
    assert plan("", "Personal history of pneumonia (recurrent)") == "general"


@pytest.mark.parametrize("code, expected", [
    ("T391X1A", "general"),          # accidental paracetamol poisoning
    ("96501", "general"),            # heroin poisoning, ICD-9
    ("T391X2A", "mental_health"),    # intentional self-harm
    ("T50902A", "mental_health"),
    ("S72001A", "surgical_injury"),  # fracture
    ("T8141XA", "surgical_injury"),  # infection following a procedure
    ("99859", "surgical_injury"),    # post-operative infection, ICD-9
    ("82021", "surgical_injury")])
def test_a_poisoning_is_not_a_wound(code, expected):
    assert plan(code) == expected


def test_acute_kidney_injury_counts_only_as_the_reason_for_admission():
    assert plan("N179", "Acute kidney failure, unspecified") == "renal"
    assert plan("5849", "Acute kidney failure, unspecified") == "renal"
    # As a secondary it is common in sepsis and must not take the plan over.
    assert plan("A419", "Sepsis", ["Acute kidney failure, unspecified"]) == "sepsis_infection"


@pytest.mark.parametrize("code", ["C189", "C187", "C179", "C080", "C833", "C824", "C61"])
def test_cancer_codes_match_on_the_code(code):
    result = classify(code, "", [])
    assert result["group"] == "oncology" and result["confidence"] == "high"


def test_mental_health_keywords_do_not_catch_drug_side_effects():
    secondary = ["Adverse effect of other drugs, medicaments and biological substances, initial encounter"]
    assert [m["group"] for m in classify_all("K8590", "Acute pancreatitis", secondary)] == []
    assert plan("", "", ["Opioid abuse, uncomplicated"]) == "mental_health"
    assert plan("", "", ["Suicide and self-inflicted poisoning by tranquilizers"]) == "mental_health"


# ----------------------------------------------------------- card wording
HEART_FAILURE_WORDS = re.compile(r"diures|decompensat|congestion", re.I)


@pytest.mark.parametrize("group", sorted(set(GROUP_PROFILES) - {"heart_failure", "renal"}))
@pytest.mark.parametrize("kg", [3.0, 1.2, -1.5])
def test_only_fluid_conditions_get_fluid_wording(group, kg):
    for variant in range(3):
        _, _, contribs = score_week(30.0, {**WEEK, "weight_change_kg": kg}, 0,
                                    variant=variant, group=group)
        weight = next(d for _, d in contribs if d.startswith("Weight change"))
        assert not HEART_FAILURE_WORDS.search(weight)


@pytest.mark.parametrize("pct", [30, 70])
def test_infection_adherence_wording_does_not_contradict_the_antibiotic_line(pct):
    obs = {**WEEK, "adherence_pct": pct, "antibiotic_course": "completed",
           "temperature_c": 36.8, "new_confusion": "no"}
    _, _, contribs = score_week(30.0, obs, 0, group="sepsis_infection")
    adherence = next(d for _, d in contribs if d.startswith("Medication adherence"))
    assert not re.search(r"unfinished|partly taken course", adherence)


@pytest.mark.parametrize("variant", range(3))
def test_surgical_temperature_wording_does_not_assume_sepsis(variant):
    for temp in (38.6, 35.5, 36.8):
        obs = {**WEEK, "temperature_c": temp, "wound_status": "clean",
               "pain_trend": "improving", "walk_distance_pct": 90}
        _, _, contribs = score_week(30.0, obs, 0, variant=variant, group="surgical_injury")
        text = next(d for _, d in contribs if d.startswith("Temperature"))
        assert "septic" not in text and "sepsis" not in text


# ------------------------------------------------------ simulated readings
def test_a_declining_cancer_patient_loses_weight():
    np = pytest.importorskip("numpy")
    sim = pytest.importorskip("scripts.simulate_weekly_monitoring")
    declining = [sim.observe_week(4, 4, True, 1.0, np.random.default_rng(s),
                                  group="oncology")["weight_change_kg"] for s in range(200)]
    recovering = [sim.observe_week(4, 4, False, 1.0, np.random.default_rng(s),
                                   group="oncology")["weight_change_kg"] for s in range(200)]
    assert np.mean(declining) < -1.5
    assert np.mean([w <= -1.0 for w in recovering]) < 0.05

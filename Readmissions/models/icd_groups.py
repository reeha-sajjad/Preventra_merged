"""
Clinical grouping of a patient from their diagnosis codes.

WHY THIS EXISTS
---------------
Post-discharge monitoring applied one rule set to everybody: a 2 kg weekly
weight gain added the same points whether the patient had heart failure or a
new knee. That is wrong in both directions - two kilos in a week is THE warning
sign in heart failure and close to meaningless after orthopaedic surgery.

This module turns a patient's diagnoses into a small label that says which
signals matter for them. models/monitoring_rules.py then uses that label to
choose weights, wording, and which readings are worth asking for at all.

WHERE THE GROUPS COME FROM
--------------------------
Not invented. Each group was measured across 534,227 MIMIC-IV admissions in
notebooks/mimic/solved-mimic-icd-feature-enginering.ipynb, against a 19.02% base
30-day readmission rate:

    oncology          8.4% of admissions   27.7% readmitted   1.46x
    heart_failure    15.1%                 23.6%              1.24x
    mental_health    13.7%                 23.6%              1.24x
    renal             8.7%                 23.0%              1.21x
    sepsis_infection  1.6%                 22.9%              1.20x
    respiratory      11.6%                 19.2%              1.01x
    diabetes          8.7%                 16.9%              0.89x
    surgical_injury   8.4%                 14.4%              0.76x
    cardiac_other     5.3%                 13.8%              0.72x
    neuro_stroke      1.9%                 12.2%              0.64x
    general          16.7%                 10.0%              0.53x

A 2.8x spread across groups, and none of them is negligibly small.

Those figures predate three changes, so they describe the original
definitions: sepsis_infection now also takes the serious infections its label
names (pneumonia, urinary and skin infections and others), renal takes a
principal diagnosis of acute kidney injury, and poisonings no longer count as
surgical_injury.

Note respiratory sits at 1.01x - almost exactly the base rate. It still earns
its own rules, but for a different reason: the SIGNALS differ. An oxygen
saturation of 91% is alarming after a pulmonary embolism and near-normal in
advanced COPD. The group is not there to say "these patients are riskier".

ORDERING
--------
First match wins, and the order is deliberate: the group that most determines
the monitoring plan is tested first. A patient with heart failure and diabetes
is monitored as heart failure, because that is what will bring them back.

WHAT THIS CAN AND CANNOT SEE
----------------------------
The loader keeps ONE code - the principal diagnosis - plus three secondary
diagnosis TITLES with no codes (N_SECONDARY = 3 in models/mimic_diagnoses.py).
So:

    principal code      matched by regex, exactly. Confidence: high.
    any title           matched by keyword. Approximate, because a title is
                        prose. Confidence: moderate.

The notebook grouped on every code of the admission, which is strictly better.
Until the loader keeps the codes for secondaries, a patient whose heart failure
was coded fifth will be grouped on whatever the classifier can see, and may
land in "general". `classify` therefore returns its evidence, and the UI shows
it, so a grouping is never silently wrong.
"""

from __future__ import annotations

import re
from typing import Optional

# ---------------------------------------------------------------------------
# Code patterns
# ---------------------------------------------------------------------------
# ICD-9 and ICD-10 patterns per group. The chronic-disease patterns are the
# Quan et al. (2005) Charlson crosswalk already used by the Phase-1 pipeline,
# so a patient grouped here is grouped on the same definition the model's
# charlson_score was built from. Sepsis, injury and mental health are not
# Charlson conditions and use chapter-level patterns.

GROUP_PATTERNS = [
    ("heart_failure", "Heart failure",
     r"^(39891|402(01|11|91)|404(01|03|11|13|91|93)|425[456789]|428)",
     r"^(I099|I110|I130|I132|I255|I420|I42[56789]|I43|I50|P290)"),

    # Acute kidney injury (584 / N17) is matched on the PRINCIPAL code only, and
    # deliberately has no title keyword: it is a common secondary diagnosis in
    # sepsis and heart failure, and as a keyword it would take over their plans.
    ("renal", "Kidney disease",
     r"^(40301|40311|40391|40402|40403|40412|40413|40492|40493|584|58[26]|5830[0-7]|585|586|5880|V420|V451|V56)",
     r"^(I120|I131|N03[2-7]|N05[2-7]|N17|N1[89]|N250|Z49[0-2]|Z940|Z992)"),

    ("respiratory", "Chronic lung disease",
     r"^(4168|4169|49[0-6]|50[0-5]|5064|5081|5088)",
     r"^(I27[89]|J4[0-7]|J6[0-7]|J684|J70[13])"),

    # Sepsis plus the serious infections the group's label promises and its
    # signals (temperature, antibiotic course, new confusion) are built for:
    # pneumonia and aspiration, lung abscess and empyema, pyelonephritis and
    # urinary infection, cellulitis and skin abscess, osteomyelitis,
    # endocarditis, C. difficile, peritonitis, meningitis, COVID-19.
    ("sepsis_infection", "Sepsis or serious infection",
     r"^(038|9959[12]|48[0-6]|5070|510|513|590|5990|68[12]|730[0-2]|421|00845|567|32[0-2])",
     r"^(A40|A41|R652|J1[2-8]|J690|J85|J86|N10|N390|L0[23]|M86|I33|A047|K65|G0[0-3]|U071)"),

    ("oncology", "Cancer",
     r"^(1[4-9][0-9]|20[0-8]|2386)",
     r"^(C[01][0-9]|C2[0-6]|C3[0-4]|C3[789]|C4[01]|C43|C4[5-9]|C5[0-8]|C6[0-9]|C7[0-9]|C8[0-58]|C9[0-7])"),

    ("diabetes", "Diabetes",
     r"^(250)",
     r"^(E1[0-4])"),

    ("cardiac_other", "Heart disease",
     r"^(410|412|42731|44[01]|443[123456789]|4471)",
     r"^(I21|I22|I252|I48|I70|I71|I73[189]|I771)"),

    ("neuro_stroke", "Stroke or neurological injury",
     r"^(36234|43[0-8]|3341|342|343|344[0-6]|3449)",
     r"^(G4[56]|H340|I6[0-9]|G041|G114|G80[12]|G8[12]|G83[0-49])"),

    # Injuries (800-959, S00-T35), other external causes (T66-T77, T79) and
    # complications of care (996-999, T80-T98). Poisoning and toxic effects
    # (960-995, T36-T65) and allergic reactions (T78) are left out: an overdose
    # is not a wound to inspect or a walk to measure.
    ("surgical_injury", "Surgery or injury recovery",
     r"^(8[0-9][0-9]|9[0-5][0-9]|99[6-9])",
     r"^(S|T[0-2][0-9]|T3[0-5]|T6[6-9]|T7[0-79]|T[89][0-9])"),

    # ICD-10 poisonings coded as intentional self-harm (sixth character 2) are
    # a mental health admission, whatever the substance.
    ("mental_health", "Mental health",
     r"^(29[0-9]|3[01][0-9])",
     r"^(F[0-9]|T(3[6-9]|[45][0-9]|6[0-5])[0-9A-Z]{2}2)"),
]

# Keyword fallbacks, matched against diagnosis TITLES. Used when the principal
# code does not match a group, so that a heart failure recorded only as a
# secondary title is still seen. Deliberately narrow: a loose keyword produces
# a confidently wrong group, which is worse than "general".
GROUP_KEYWORDS = {
    "heart_failure":    ("heart failure",),
    "renal":            ("kidney disease", "renal failure", "end stage renal",
                         "renal insufficiency", "nephropathy", "dialysis"),
    # "chronic respiratory failure", not "respiratory failure": the acute kind
    # comes with sepsis, pneumonia and COVID, and this group's oxygen rule
    # assumes a patient who lives at 88-92%.
    "respiratory":      ("chronic obstructive", "copd", "emphysema", "asthma",
                         "chronic airway obstruction", "chronic respiratory failure"),
    "sepsis_infection": ("sepsis", "septicemia", "septic shock", "septicaemia",
                         "pneumonia", "pneumonitis due to inhalation", "empyema",
                         "abscess of lung", "pyelonephritis", "urinary tract infection",
                         "cellulitis", "osteomyelitis", "endocarditis", "clostridium difficile",
                         "clostridioides difficile", "peritonitis", "meningitis", "covid-19"),
    "oncology":         ("malignant neoplasm", "carcinoma", "lymphoma", "leukemia",
                         "leukaemia", "metastatic", "metastasis", "myeloma"),
    "diabetes":         ("diabetes", "diabetic"),
    "cardiac_other":    ("myocardial infarction", "atrial fibrillation",
                         "coronary atherosclerosis", "atherosclerotic heart disease",
                         "peripheral vascular", "aortic aneurysm"),
    "neuro_stroke":     ("cerebral infarction", "cerebrovascular", "hemiplegia",
                         "subarachnoid hemorrhage", "intracerebral hemorrhage",
                         "stroke"),
    "surgical_injury":  ("fracture", "postoperative", "post-operative",
                         "complication of", "wound", "amputation"),
    # Not "substance" on its own: it matched "Adverse effect of other drugs ...
    # and biological substances", an E-code on a large share of admissions.
    "mental_health":    ("depress", "anxiety", "schizophren", "bipolar", "psychosis",
                         "psychotic", "suicidal", "suicide", "self-harm", "self-inflicted",
                         "alcohol dependence", "alcohol abuse", "opioid dependence",
                         "opioid abuse", "cocaine abuse", "cannabis abuse", "drug abuse",
                         "drug dependence", "substance abuse", "substance use",
                         "substance dependence"),
}

GROUP_LABELS = {key: label for key, label, _, _ in GROUP_PATTERNS}
GROUP_LABELS["general"] = "General recovery"

_COMPILED = [(key, re.compile(p9), re.compile(p10)) for key, _, p9, p10 in GROUP_PATTERNS]


def infer_version(code: str) -> int:
    """
    ICD edition for a bare code.

    patient_worklist stores the code with no version field, so it has to be
    inferred. Numeric codes are ICD-9; letter-led are ICD-10, except the ICD-9
    E-codes for external causes, which are always E8xx or E9xx.
    """
    c = (code or "").strip().upper()
    if not c:
        return 10
    if c[0].isdigit():
        return 9
    if c[0] == "E" and re.match(r"^E[89]\d", c):
        return 9
    if c[0] == "V" and re.match(r"^V\d{2}", c) and len(c) <= 5:
        return 9
    return 10


def _match_code(code: str) -> Optional[str]:
    c = (code or "").strip().upper().replace(".", "")
    if not c:
        return None
    version = infer_version(c)
    for key, p9, p10 in _COMPILED:
        if (p9 if version == 9 else p10).match(c):
            return key
    return None


# A diagnosis title can NAME a condition the patient does not currently have.
# "Personal history of malignant neoplasm of breast" is a resolved cancer;
# "Hypertensive heart disease without heart failure" is an explicit negative.
# Substring matching cannot tell those from the real thing, and grouping on
# them puts a patient on a monitoring plan for a condition they do not have.

# Z85/Z86 and V10-V15 titles all open this way, and the whole title is then
# historical - including any condition named later in it.
_HISTORICAL_TITLE = re.compile(r"\b(personal|family) history of\b", re.I)

# Applied to the clause running up to the keyword, so that a negation later in
# the title cannot cancel a condition stated before it: "Diabetes mellitus
# without mention of complication" is still diabetes.
_CLAUSE_NEGATION = re.compile(
    r"\b(without|history of|screening for|no evidence of|ruled out|at risk (for|of))\b", re.I)


def _negated(text: str, start: int) -> bool:
    """Is the keyword at `start` inside a clause that negates it?"""
    clause_start = max(text.rfind(",", 0, start), text.rfind(";", 0, start)) + 1
    return bool(_CLAUSE_NEGATION.search(text[clause_start:start]))


def _match_title(title: str) -> Optional[str]:
    t = (title or "").lower()
    if not t:
        return None
    if _HISTORICAL_TITLE.search(t):
        return None
    # Same priority order as the code patterns, so code and title matching can
    # never disagree about which group outranks which.
    for key, _, _, _ in GROUP_PATTERNS:
        for word in GROUP_KEYWORDS.get(key, ()):
            # Every occurrence, not just the first: one negated mention does not
            # rule out a genuine one later in the same title.
            pos = t.find(word)
            while pos != -1:
                if not _negated(t, pos):
                    return key
                pos = t.find(word, pos + 1)
    return None


def _candidates(primary_code: str, primary_diagnosis: str,
                secondary_diagnoses: list) -> list:
    """
    Every (rank, tiebreak, key, confidence, matched_on, evidence) a patient's
    recorded diagnoses support. Shared by classify() and classify_all() so the
    single group and the membership list can never disagree.
    """
    rank = {k: i for i, (k, _, _, _) in enumerate(GROUP_PATTERNS)}
    out = []

    key = _match_code(primary_code)
    if key:
        out.append((rank[key], 0, key, "high", "principal_code",
                    f"principal diagnosis {primary_code}"
                    + (f" - {primary_diagnosis}" if primary_diagnosis else "")))

    key = _match_title(primary_diagnosis)
    if key:
        out.append((rank[key], 1, key, "moderate", "principal_title",
                    f"principal diagnosis - {primary_diagnosis}"))

    for title in secondary_diagnoses or []:
        key = _match_title(title)
        if key:
            out.append((rank[key], 2, key, "moderate", "secondary_title",
                        f"secondary diagnosis - {title}"))
    return out


def classify(primary_code: str = "", primary_diagnosis: str = "",
             secondary_diagnoses: Optional[list] = None) -> dict:
    """
    Assign THE monitoring group - the one plan this patient is followed under.

    Returns {group, label, confidence, matched_on, evidence}. `evidence` is
    rendered in the UI: a grouping that silently changes someone's score is
    exactly the kind of hidden logic this project has avoided elsewhere.

    Resolution: the HIGHEST-PRIORITY group found anywhere wins - principal code,
    principal title, or any secondary title. The principal code only breaks a
    tie between two candidates of equal priority.

    That deliberately matches the notebook, which grouped on every code of the
    admission. The alternative - "principal code always wins" - would monitor a
    patient admitted with pneumonia and severe heart failure as a lung patient,
    and their weight would stop being the signal that matters. The priority
    order already encodes which condition should own the monitoring plan, so it
    is applied consistently rather than overridden by position.

    A patient usually has SEVERAL of these conditions. This function answers
    "which one do we monitor them for"; classify_all answers "which do they
    have", and that is the one to filter on.
    """
    candidates = _candidates(primary_code, primary_diagnosis, secondary_diagnoses)
    if not candidates:
        return {
            "group": "general",
            "label": GROUP_LABELS["general"],
            "confidence": "default",
            "matched_on": "none",
            "evidence": "no condition matched a specific monitoring group",
        }

    _, _, key, confidence, matched_on, evidence = min(candidates, key=lambda c: (c[0], c[1]))
    return {
        "group": key,
        "label": GROUP_LABELS[key],
        "confidence": confidence,
        "matched_on": matched_on,
        "evidence": evidence,
    }


def classify_all(primary_code: str = "", primary_diagnosis: str = "",
                 secondary_diagnoses: Optional[list] = None) -> list:
    """
    EVERY group the patient's recorded diagnoses support, best evidence first.

    WHY THIS EXISTS SEPARATELY FROM classify()
    ------------------------------------------
    classify() has to pick one group, because a patient can only be on one
    monitoring plan. Filtering is a different question. A cardiologist asking
    for "heart failure" wants every patient who HAS heart failure, not only
    those whose plan is named after it - and a patient with heart failure plus
    diabetes belongs in both lists. Returning one group made those patients
    invisible under the lower-priority condition.

    Each entry is {group, label, confidence, matched_on, evidence}, ordered by
    the same priority as classify(), so the first entry is always the group
    classify() would return. One entry per group: a condition named by both the
    principal code and a secondary title is one condition, and the strongest
    evidence for it is kept.

    Returns [] - not ["general"] - when nothing matched. "General recovery" is
    the absence of a specific condition, so it is not a membership.
    """
    best: dict = {}
    for cand in _candidates(primary_code, primary_diagnosis, secondary_diagnoses):
        rank, tiebreak, key = cand[0], cand[1], cand[2]
        if key not in best or (rank, tiebreak) < (best[key][0], best[key][1]):
            best[key] = cand
    return [{"group": k, "label": GROUP_LABELS[k], "confidence": c,
             "matched_on": m, "evidence": e}
            for _, _, k, c, m, e in sorted(best.values(), key=lambda x: (x[0], x[1]))]


def monitoring_groups(primary_code: str = "", primary_diagnosis: str = "",
                      secondary_diagnoses: Optional[list] = None) -> list:
    """
    The conditions that shape a patient's weekly monitoring: every group their
    diagnoses match, plan group first, plus "general" when the REASON FOR
    ADMISSION - the principal diagnosis - matched no group.

    The "general" entry matters. A patient admitted with liver failure whose
    only matched diagnosis is bipolar disorder would otherwise be monitored as
    a mental health patient alone, with their vitals muted - the liver disease
    is real even though no group names it, and it makes vitals matter.

    An unmatched SECONDARY diagnosis does not add it. Almost every patient has
    one - hypertension, hyperlipidaemia, obesity - and when they counted,
    "general" joined 97% of patients and its ordinary weight of 1.0 overrode
    every group's down-weighting: weight was scored in full after a hip
    fracture while the card said it was noise. Extreme readings in a muted
    signal are still caught by the red flags in models/monitoring_rules.
    """
    keys = [m["group"] for m in classify_all(primary_code, primary_diagnosis, secondary_diagnoses)]
    unmatched = bool((primary_code or primary_diagnosis)
                     and not (_match_code(primary_code) or _match_title(primary_diagnosis)))
    if unmatched or not keys:
        keys.append("general")
    return keys

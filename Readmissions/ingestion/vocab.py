"""
The canonical vocabulary every ingested record is translated into, and how
that vocabulary maps onto the values the built-in model was trained on.

Two steps, on purpose:

    hospital code  --(the hospital's mapping, api/ingest.py)-->  canonical value
    canonical value --(the tables below, fixed)-->                model value

The canonical layer is the contract with hospitals: short, documented values
(EMERGENCY, HOME, MEDICARE ...) that do not change when a model is retrained.
The model layer is whatever vocabulary a model happened to be trained on - the
built-in model learned MIMIC-IV's strings ("EW EMER.", "Medicare"), so they
appear here and nowhere else.
"""
from __future__ import annotations

import re

# ------------------------------------------------------------------ encounters
ADMISSION_TYPES = ("EMERGENCY", "DIRECT_EMERGENCY", "URGENT", "ELECTIVE", "SAME_DAY_SURGERY",
                   "OBSERVATION")
DISPOSITIONS = ("HOME", "HOME_WITH_SERVICES", "SNF", "REHAB", "LTAC", "AMA", "TRANSFER",
                "HOSPICE", "DIED", "OTHER")
PAYERS = ("MEDICARE", "MEDICAID", "PRIVATE", "SELF_PAY", "OTHER")
SEXES = ("F", "M", "OTHER", "UNKNOWN")
MARITAL = ("SINGLE", "MARRIED", "DIVORCED", "WIDOWED", "UNKNOWN")
RACES = ("WHITE", "BLACK", "ASIAN", "HISPANIC", "AMERICAN_INDIAN", "PACIFIC_ISLANDER",
         "OTHER", "UNKNOWN")

# Which canonical field each vocabulary belongs to, for validation and mapping.
VOCABULARIES = {
    "admission_type": ADMISSION_TYPES,
    "discharge_disposition": DISPOSITIONS,
    "payer_class": PAYERS,
    "sex": SEXES,
    "marital_status": MARITAL,
    "race_ethnicity": RACES,
}

# Outcomes and exclusions, exactly as the Phase 1 notebook defined them.
TERMINAL_DISPOSITIONS = {"DIED", "HOSPICE"}
SNF_DISPOSITIONS = {"SNF", "REHAB", "LTAC"}
READMIT_WINDOW_DAYS = 30

# ------------------------------------------------- canonical -> built-in model
MODEL_ADMISSION_TYPE = {
    "EMERGENCY": "EW EMER.", "DIRECT_EMERGENCY": "DIRECT EMER.", "URGENT": "URGENT",
    "ELECTIVE": "ELECTIVE", "SAME_DAY_SURGERY": "SURGICAL SAME DAY ADMISSION",
    "OBSERVATION": "OBSERVATION ADMIT",
}
MODEL_INSURANCE = {"MEDICARE": "Medicare", "MEDICAID": "Medicaid", "PRIVATE": "Private",
                   "SELF_PAY": "No charge", "OTHER": "Other"}
MODEL_MARITAL = {"SINGLE": "SINGLE", "MARRIED": "MARRIED", "DIVORCED": "DIVORCED",
                 "WIDOWED": "WIDOWED"}
MODEL_GENDER = {"F": "F", "M": "M"}
MODEL_RACE = {"WHITE": "WHITE", "BLACK": "BLACK/AFRICAN AMERICAN", "ASIAN": "ASIAN",
              "HISPANIC": "HISPANIC OR LATINO", "AMERICAN_INDIAN": "AMERICAN INDIAN/ALASKA NATIVE",
              "PACIFIC_ISLANDER": "NATIVE HAWAIIAN OR OTHER PACIFIC ISLANDER", "OTHER": "OTHER",
              "UNKNOWN": "UNKNOWN"}

# ------------------------------------------------------------------------ labs
# The 12 tests the model reads. Each: LOINC codes that name it, a pattern for
# lab names (the Phase 1 notebook's, applied to a lowercase name), the unit the
# model was trained in, and conversions from other units into it.
LABS = {
    "albumin":     {"loinc": ("1751-7",), "name": r"^albumin$", "unit": "g/dL",
                    "convert": {"g/l": 0.1}},
    "bicarbonate": {"loinc": ("1963-8", "2028-9"), "name": r"^bicarbonate$|carbon dioxide|^co2",
                    "unit": "mEq/L", "convert": {"mmol/l": 1.0}},
    "bun":         {"loinc": ("3094-0",), "name": r"urea nitrogen|^bun$", "unit": "mg/dL",
                    "convert": {"mmol/l": 2.8}},
    "creatinine":  {"loinc": ("2160-0",), "name": r"^creatinine$", "unit": "mg/dL",
                    "convert": {"umol/l": 1 / 88.4, "µmol/l": 1 / 88.4}},
    "glucose":     {"loinc": ("2345-7",), "name": r"^glucose$", "unit": "mg/dL",
                    "convert": {"mmol/l": 18.0}},
    "hba1c":       {"loinc": ("4548-4", "17856-6"), "name": r"hemoglobin a1c|hba1c|% *hemoglobin a1c",
                    "unit": "%", "convert": {"mmol/mol": None}},     # affine, see convert_lab
    "hemoglobin":  {"loinc": ("718-7",), "name": r"^hemoglobin$|^haemoglobin$", "unit": "g/dL",
                    "convert": {"g/l": 0.1}},
    "inr":         {"loinc": ("6301-6", "34714-6"), "name": r"^inr\(pt\)$|international normalized|^inr$",
                    "unit": "ratio", "convert": {}},
    "platelets":   {"loinc": ("777-3",), "name": r"platelet count|^platelets$", "unit": "K/uL",
                    "convert": {"10*9/l": 1.0, "10^9/l": 1.0, "x10^9/l": 1.0, "10e9/l": 1.0}},
    "potassium":   {"loinc": ("2823-3",), "name": r"^potassium$", "unit": "mEq/L",
                    "convert": {"mmol/l": 1.0}},
    "sodium":      {"loinc": ("2951-2",), "name": r"^sodium$", "unit": "mEq/L",
                    "convert": {"mmol/l": 1.0}},
    "wbc":         {"loinc": ("6690-2",), "name": r"white blood cells|^wbc$", "unit": "K/uL",
                    "convert": {"10*9/l": 1.0, "10^9/l": 1.0, "x10^9/l": 1.0, "10e9/l": 1.0}},
}
LAB_KEYS = tuple(LABS)
_LOINC_TO_LAB = {code: key for key, spec in LABS.items() for code in spec["loinc"]}

# Medication classes the model flags, as the Phase 1 notebook matched them.
HIGH_RISK_DRUGS = {
    "insulin":       r"insulin",
    "anticoagulant": r"warfarin|heparin|apixaban|rivaroxaban|enoxaparin|dabigatran",
    "opioid":        r"morphine|oxycodone|hydromorphone|fentanyl|hydrocodone|tramadol",
    "diuretic":      r"furosemide|bumetanide|torsemide|spironolactone|hydrochlorothiazide",
    "antipsychotic": r"haloperidol|quetiapine|olanzapine|risperidone",
}


def lab_key(loinc_code=None, test_name=None):
    """Which of the 12 tests a result is, or None. LOINC first; the name only
    when no LOINC code was sent."""
    if loinc_code and str(loinc_code).strip() in _LOINC_TO_LAB:
        return _LOINC_TO_LAB[str(loinc_code).strip()]
    if loinc_code:
        return None
    name = str(test_name or "").strip().lower()
    for key, spec in LABS.items():
        if name and re.search(spec["name"], name):
            return key
    return None


def _unit(text) -> str:
    return re.sub(r"\s+", "", str(text or "")).lower()


def convert_lab(key: str, value, unit) -> tuple:
    """(value in the model's unit, problem or None). An unknown unit is a
    problem, not a guess: a creatinine in umol/L read as mg/dL is 88 times off."""
    if value is None:
        return None, "no value"
    spec = LABS[key]
    u = _unit(unit)
    if u in ("", _unit(spec["unit"])) or (key == "inr" and u in ("", "ratio", "inr")):
        return float(value), None
    if key == "hba1c" and u == "mmol/mol":
        return round(float(value) * 0.0915 + 2.15, 2), None      # IFCC -> NGSP
    factor = spec["convert"].get(u)
    if factor is None:
        return None, f"unit '{unit}' for {key} is not one we can convert to {spec['unit']}"
    return float(value) * factor, None

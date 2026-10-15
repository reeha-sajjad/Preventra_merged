"""
MIMIC-IV tables -> the canonical tables of features/discharge_features.py.

Two uses: the parity test, which runs the Phase 1 notebook's own cells and this
path on the same records and requires the same 94 inputs; and the dry run in
docs/pilot_ingestion_build.md (B7), which pushes MIMIC records through the
exact path a hospital's data will take.

Everything maps losslessly for the model except race: MIMIC records 33 race
values and the canonical vocabulary has 8, so "ASIAN - CHINESE" arrives as
"ASIAN". A hospital will not send MIMIC's categories either.
"""
from __future__ import annotations

import pandas as pd

ADMISSION_TYPE = {
    "EW EMER.": "EMERGENCY", "DIRECT EMER.": "DIRECT_EMERGENCY", "URGENT": "URGENT",
    "ELECTIVE": "ELECTIVE", "SURGICAL SAME DAY ADMISSION": "SAME_DAY_SURGERY",
    "EU OBSERVATION": "OBSERVATION", "OBSERVATION ADMIT": "OBSERVATION",
    "AMBULATORY OBSERVATION": "OBSERVATION", "DIRECT OBSERVATION": "OBSERVATION",
}
DISPOSITION = {
    "HOME": "HOME", "HOME HEALTH CARE": "HOME_WITH_SERVICES", "SKILLED NURSING FACILITY": "SNF",
    "REHAB": "REHAB", "CHRONIC/LONG TERM ACUTE CARE": "LTAC", "AGAINST ADVICE": "AMA",
    "ACUTE HOSPITAL": "TRANSFER", "HOSPICE": "HOSPICE", "DIED": "DIED",
}
INSURANCE = {"Medicare": "MEDICARE", "Medicaid": "MEDICAID", "Private": "PRIVATE",
             "No charge": "SELF_PAY", "Other": "OTHER"}
MARITAL = {"SINGLE": "SINGLE", "MARRIED": "MARRIED", "DIVORCED": "DIVORCED", "WIDOWED": "WIDOWED"}

# The notebook's own lab patterns (cell 13), so MIMIC items resolve exactly as
# they did when the model was trained.
NOTEBOOK_LAB_PATTERNS = {
    "hba1c":      r"hemoglobin a1c|hba1c|% *hemoglobin a1c",
    "glucose":    r"^glucose$",
    "creatinine": r"^creatinine$",
    "bun":        r"urea nitrogen",
    "sodium":     r"^sodium$",
    "potassium":  r"^potassium$",
    "hemoglobin": r"^hemoglobin$",
    "wbc":        r"white blood cells",
    "platelets":  r"platelet count",
    "albumin":    r"^albumin$",
    "bicarbonate": r"^bicarbonate$",
    "inr":        r"^inr\(pt\)$|international normalized",
}


def race(value) -> str:
    v = str(value or "").upper()
    if v.startswith("WHITE"):
        return "WHITE"
    if v.startswith("BLACK"):
        return "BLACK"
    if v.startswith("ASIAN"):
        return "ASIAN"
    if v.startswith("HISPANIC"):
        return "HISPANIC"
    if v == "AMERICAN INDIAN/ALASKA NATIVE":
        return "AMERICAN_INDIAN"
    if v == "NATIVE HAWAIIAN OR OTHER PACIFIC ISLANDER":
        return "PACIFIC_ISLANDER"
    if v in ("", "NAN", "UNKNOWN", "UNABLE TO OBTAIN", "PATIENT DECLINED TO ANSWER"):
        return "UNKNOWN"
    return "OTHER"


def lab_items(d_labitems: pd.DataFrame) -> dict:
    """itemid -> test, exactly as the notebook resolves it (blood preferred)."""
    dlab = d_labitems.copy()
    dlab["label_l"] = dlab["label"].astype(str).str.lower()
    item_map = {}
    for name, pattern in NOTEBOOK_LAB_PATTERNS.items():
        m = dlab[dlab["label_l"].str.contains(pattern, regex=True, na=False)]
        if "fluid" in m.columns:
            blood = m[m["fluid"].eq("Blood")]
            m = blood if len(blood) else m
        for iid in m["itemid"].tolist():
            item_map[int(iid)] = name
    return item_map


def to_canonical(admissions, patients, diagnoses_icd, procedures_icd, drgcodes,
                 prescriptions, labevents, d_labitems) -> dict:
    """The canonical tables, keyed by subject_id (patient_key) and hadm_id
    (encounter_key). Labs come back already prepared (test, value, abnormal)."""
    adm = admissions
    ages = patients.set_index("subject_id")["anchor_age"]
    encounters = pd.DataFrame({
        "patient_key": adm["subject_id"].astype(str),
        "encounter_key": adm["hadm_id"].astype(str),
        "admit_datetime": pd.to_datetime(adm["admittime"]),
        "discharge_datetime": pd.to_datetime(adm["dischtime"]),
        "age_years": adm["subject_id"].map(ages).values,
        "admission_type": adm["admission_type"].map(ADMISSION_TYPE).values,
        "discharge_disposition": adm["discharge_location"].map(DISPOSITION).fillna("OTHER").values,
        "died_in_hospital": adm["hospital_expire_flag"].eq(1).values,
        "payer_class": adm["insurance"].map(INSURANCE).values,
        "ed_arrival_datetime": pd.to_datetime(adm["edregtime"]),
        "ed_departure_datetime": pd.to_datetime(adm["edouttime"]),
        "marital_status": adm["marital_status"].map(MARITAL).values,
        "race_ethnicity": adm["race"].map(race).values,
    })
    people = pd.DataFrame({"patient_key": patients["subject_id"].astype(str),
                           "sex": patients["gender"].values})
    diagnoses = pd.DataFrame({"encounter_key": diagnoses_icd["hadm_id"].astype(str),
                              "icd_code": diagnoses_icd["icd_code"].astype(str),
                              "icd_version": diagnoses_icd["icd_version"].values,
                              "sequence": diagnoses_icd.get("seq_num")})
    procedures = pd.DataFrame({"encounter_key": procedures_icd["hadm_id"].astype(str),
                               "procedure_code": procedures_icd["icd_code"].astype(str),
                               "code_system": "ICD"})
    drg = pd.DataFrame({"encounter_key": drgcodes["hadm_id"].astype(str),
                        "drg_system": drgcodes["drg_type"].map(lambda t: "APR-DRG" if t == "APR" else t),
                        "drg_code": drgcodes.get("drg_code"),
                        "severity_of_illness": drgcodes["drg_severity"].values,
                        "risk_of_mortality": drgcodes["drg_mortality"].values})
    meds = pd.DataFrame({"encounter_key": prescriptions["hadm_id"].astype(str),
                         "drug_name": prescriptions["drug"].astype("string")})
    items = lab_items(d_labitems)
    lev = labevents[labevents["itemid"].isin(items) & labevents["valuenum"].notna()
                    & labevents["hadm_id"].notna()]
    labs = pd.DataFrame({"encounter_key": lev["hadm_id"].astype("int64").astype(str),
                         "lab": lev["itemid"].map(items).values,
                         "value": lev["valuenum"].astype(float).values,
                         "result_datetime": pd.to_datetime(lev["charttime"]).values,
                         "abnormal": lev["flag"].eq("abnormal").astype(int).values})
    return {"encounters": encounters, "patients": people, "diagnoses": diagnoses,
            "procedures": procedures, "drg": drg, "medications_": meds, "lab_rows": labs}

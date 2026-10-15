"""
The 94 discharge-time inputs, built from a hospital's own tables.

A port of the Phase 1 notebook (notebooks/mimic/phase1_discharge_risk_mimic.ipynb,
cells 5, 7, 9, 11, 13-14 and 16) into plain functions over the canonical
tables of docs/pilot_ingestion_build.md. The same code builds a hospital's
Model Studio training file and scores its live discharges, so a model always
sees inputs made the way it learned them. tests/units/test_discharge_features.py
runs the notebook's own cells on the same records and requires identical output.

Canonical tables (pandas DataFrames, one per table):

    encounters   patient_key, encounter_key, admit_datetime, discharge_datetime,
                 age_years, admission_type, discharge_disposition,
                 died_in_hospital, payer_class, ed_arrival_datetime,
                 ed_departure_datetime, [observation_stay, planned_admission,
                 marital_status, race_ethnicity]
    patients     patient_key, sex, [marital_status, race_ethnicity]
    diagnoses    encounter_key, icd_code, icd_version, sequence
    procedures   encounter_key, procedure_code
    drg          encounter_key, drg_system, severity_of_illness, risk_of_mortality
    medications  encounter_key, drug_name
    labs         encounter_key, lab (one of ingestion.vocab.LAB_KEYS), value (in
                 the model's unit), result_datetime, abnormal (0/1) - see
                 prepare_labs for turning results as sent into this

Categorical values are canonical (ingestion/vocab.py); they become the model's
vocabulary only in assemble().

Missing values stay missing. The model learned a direction for NaN, and an
imputed value would invent a measurement nobody took.
"""
from __future__ import annotations

import json
import os
from typing import Optional

import numpy as np
import pandas as pd

from ingestion import vocab

# Quan et al. (2005) Charlson mappings, copied from the Phase 1 notebook.
#
# Known issue, kept on purpose: the malignancy ICD-10 pattern "C[0-2][0-6]"
# misses C07-C09 and C17-C19 (colon cancer among them). The model was trained
# on exactly these definitions, so they must match it; fixing the pattern
# means retraining the model at the same time.
CHARLSON = {
    "myocardial_infarction":   (1, r"^(410|412)",                         r"^(I21|I22|I252)"),
    "congestive_heart_failure":(1, r"^(39891|402(01|11|91)|404(01|03|11|13|91|93)|425[456789]|428)",
                                   r"^(I099|I110|I130|I132|I255|I420|I42[56789]|I43|I50|P290)"),
    "peripheral_vascular":     (1, r"^(0930|437[34]|440|441|443[123456789]|4471|5571|5579|V434)",
                                   r"^(I70|I71|I73[189]|I771|I79[02]|K55[189]|Z95[89])"),
    "cerebrovascular":         (1, r"^(36234|43[0-8])",                   r"^(G4[56]|H340|I6[0-9])"),
    "dementia":                (1, r"^(290|2941|3312)",                   r"^(F0[0-3]|F051|G30|G311)"),
    "chronic_pulmonary":       (1, r"^(4168|4169|49[0-6]|50[0-5]|5064|5081|5088)",
                                   r"^(I27[89]|J4[0-7]|J6[0-7]|J684|J70[13])"),
    "rheumatic":               (1, r"^(4465|710[0-4]|714[0128]|725)",     r"^(M0[56]|M315|M3[234]|M35[13]|M360)"),
    "peptic_ulcer":            (1, r"^(53[1-4])",                         r"^(K2[5-8])"),
    "mild_liver":              (1, r"^(070[23][23]|070[45]4|0706|0709|57[01]|5733|5734|5738|5739|V427)",
                                   r"^(B18|K70[0-39]|K71[3-57]|K7[34]|K76[02-49]|Z944)"),
    "diabetes_uncomplicated":  (1, r"^(250[0-3]|2508|2509)",              r"^(E1[0-4][01689])"),
    "diabetes_complicated":    (2, r"^(250[4-7])",                        r"^(E1[0-4][2-57])"),
    "hemiplegia":              (2, r"^(3341|342|343|344[0-6]|3449)",      r"^(G041|G114|G80[12]|G8[12]|G83[0-49])"),
    "renal_disease":           (2, r"^(40301|40311|40391|40402|40403|40412|40413|40492|40493|58[26]|5830[0-7]|585|586|5880|V420|V451|V56)",
                                   r"^(I120|I131|N03[2-7]|N05[2-7]|N1[89]|N250|Z49[0-2]|Z940|Z992)"),
    "malignancy":              (2, r"^(1[4-9][0-9]|20[0-8]|2386)",        r"^(C[0-2][0-6]|C3[0-4]|C3[789]|C4[01]|C43|C4[5-9]|C5[0-8]|C6[0-9]|C7[0-6]|C8[1-5]|C88|C9[0-7])"),
    "severe_liver":            (3, r"^(456[012]|572[2-8])",               r"^(I85[09]|I864|I982|K704|K711|K721|K729|K76[5-7])"),
    "metastatic_cancer":       (6, r"^(19[6-9])",                          r"^(C7[789]|C80)"),
    "hiv_aids":                (6, r"^(04[2-4])",                          r"^(B2[0-4])"),
}

ID_COLUMNS = ["patient_key", "encounter_key", "admit_datetime", "discharge_datetime"]
OUTCOME = "readmit_30d"


def feature_names() -> list:
    """The 94 inputs, in the order the built-in model was trained on."""
    from api.mimic_scoring import SPEC_PATH
    with open(SPEC_PATH) as fh:
        return json.load(fh)["features"]


def _keys(df: pd.DataFrame, column: str = "encounter_key") -> pd.Series:
    return df[column].astype(str)


def _bool(series: Optional[pd.Series], index) -> pd.Series:
    if series is None:
        return pd.Series(False, index=index)
    return series.map(lambda v: str(v).strip().lower() in ("1", "true", "yes", "y", "t")
                      if pd.notna(v) else False).astype(bool)


# ------------------------------------------------------------------ cohort
def cohort(encounters: pd.DataFrame, as_of: Optional[pd.Timestamp] = None) -> pd.DataFrame:
    """
    Every stay in time order per patient, with the history inputs, the
    exclusion flag and the outcome (cell 5).

    The outcome is computed on the FULL sequence before anything is excluded,
    so a readmission after an excluded stay still counts. With `as_of` (the
    newest date the data covers), a stay whose 30 days run past it and has no
    readmission yet has an unknown outcome (NaN), not a "no".
    """
    adm = encounters.copy()
    adm["patient_key"], adm["encounter_key"] = _keys(adm, "patient_key"), _keys(adm)
    for col in ("admit_datetime", "discharge_datetime", "ed_arrival_datetime", "ed_departure_datetime"):
        adm[col] = pd.to_datetime(adm[col], errors="coerce") if col in adm else pd.NaT
    adm = adm.sort_values(["patient_key", "admit_datetime"], kind="mergesort").reset_index(drop=True)

    g = adm.groupby("patient_key", sort=False)
    adm["n_prior_adm"] = g.cumcount()
    adm["days_since_prev"] = (adm["admit_datetime"] - g["discharge_datetime"].shift(1)).dt.total_seconds() / 86400
    next_admit = g["admit_datetime"].shift(-1)
    next_type = g["admission_type"].shift(-1)
    planned = _bool(adm.get("planned_admission"), adm.index)
    next_planned = planned.groupby(adm["patient_key"]).shift(-1, fill_value=False).astype(bool)
    days_to_next = (next_admit - adm["discharge_datetime"]).dt.total_seconds() / 86400

    died = _bool(adm.get("died_in_hospital"), adm.index)
    terminal = adm["discharge_disposition"].isin(vocab.TERMINAL_DISPOSITIONS)
    observation = adm["admission_type"].eq("OBSERVATION") | _bool(adm.get("observation_stay"), adm.index)
    adm["excluded"] = np.select([died, terminal, observation],
                                ["died in hospital", "hospice or died", "observation stay"], "")

    within = days_to_next.between(0, vocab.READMIT_WINDOW_DAYS)
    unplanned = ~(next_type.eq("ELECTIVE") | next_planned)
    readmit = (within & unplanned).astype(float)
    if as_of is not None:
        open_window = adm["discharge_datetime"] + pd.Timedelta(days=vocab.READMIT_WINDOW_DAYS) > as_of
        readmit = readmit.where(~(open_window & readmit.eq(0)))
    adm[OUTCOME] = readmit
    return adm


# --------------------------------------------------------------- diagnoses
def charlson(diagnoses: pd.DataFrame, encounter_keys) -> pd.DataFrame:
    """17 Charlson conditions, the weighted score and the number of coded
    diagnoses, per stay (cell 7)."""
    keys = pd.Index(pd.Series(list(encounter_keys), dtype=str).unique(), name="encounter_key")
    dx = diagnoses.copy()
    dx["encounter_key"] = _keys(dx)
    dx = dx[dx["encounter_key"].isin(keys)]
    dx["icd_code"] = dx["icd_code"].astype(str).str.upper().str.replace(".", "", regex=False).str.strip()
    is9 = pd.to_numeric(dx["icd_version"], errors="coerce").eq(9)

    cci = pd.DataFrame(index=keys)
    for cond, (_, p9, p10) in CHARLSON.items():
        hit = np.where(is9, dx["icd_code"].str.match(p9), dx["icd_code"].str.match(p10))
        hit = pd.Series(hit, index=dx.index).fillna(False).astype(bool)
        cci[cond] = cci.index.isin(dx.loc[hit, "encounter_key"].unique()).astype(int)
    # Charlson hierarchy: the severe form supersedes the mild one.
    cci.loc[cci["diabetes_complicated"].eq(1), "diabetes_uncomplicated"] = 0
    cci.loc[cci["severe_liver"].eq(1), "mild_liver"] = 0
    cci.loc[cci["metastatic_cancer"].eq(1), "malignancy"] = 0
    cci["charlson_score"] = sum(cci[c] * w for c, (w, _, _) in CHARLSON.items())
    cci["n_diagnoses"] = dx.groupby("encounter_key").size().reindex(keys).fillna(0).astype(int)
    return cci


# ------------------------------------------------- severity and procedures
def severity(drg: pd.DataFrame, procedures: pd.DataFrame, encounter_keys) -> pd.DataFrame:
    """APR-DRG severity and mortality (APR-DRG only) and the number of
    procedures, per stay (cell 9)."""
    keys = pd.Index(pd.Series(list(encounter_keys), dtype=str).unique(), name="encounter_key")
    out = pd.DataFrame(index=keys)
    d = drg.copy()
    if len(d):
        d["encounter_key"] = _keys(d)
        d = d[d["encounter_key"].isin(keys)
              & d["drg_system"].astype(str).str.upper().str.replace("-", "").str.startswith("APR")]
        agg = (d.assign(drg_severity=pd.to_numeric(d["severity_of_illness"], errors="coerce"),
                        drg_mortality=pd.to_numeric(d["risk_of_mortality"], errors="coerce"))
               .groupby("encounter_key")[["drg_severity", "drg_mortality"]].max())
        out = out.join(agg)
    for col in ("drg_severity", "drg_mortality"):
        if col not in out:
            out[col] = np.nan
    p = procedures.copy()
    counts = (_keys(p).value_counts() if len(p) else pd.Series(dtype=float))
    out["n_procedures"] = counts.reindex(keys).astype(float)
    return out


# ------------------------------------------------------------- medications
def medications(meds: pd.DataFrame, encounter_keys) -> pd.DataFrame:
    """Orders, distinct drugs and the five high-risk drug classes, per stay
    (cell 11). One row per ORDER: counting doses given would inflate both."""
    keys = pd.Index(pd.Series(list(encounter_keys), dtype=str).unique(), name="encounter_key")
    out = pd.DataFrame(index=keys)
    m = meds.copy()
    if not len(m):
        for col in ["n_drug_orders", "n_distinct_drugs"] + [f"med_{k}" for k in vocab.HIGH_RISK_DRUGS]:
            out[col] = np.nan
        return out
    m["encounter_key"] = _keys(m)
    m = m[m["encounter_key"].isin(keys)]
    m["drug_l"] = m["drug_name"].astype("string").str.lower()
    g = m.groupby("encounter_key")
    agg = g.agg(n_drug_orders=("drug_name", "size"), n_distinct_drugs=("drug_name", "nunique"))
    for name, pattern in vocab.HIGH_RISK_DRUGS.items():
        agg[f"med_{name}"] = (m.assign(f=m["drug_l"].str.contains(pattern, na=False, regex=True))
                              .groupby("encounter_key")["f"].max().astype(int))
    return out.join(agg)


# -------------------------------------------------------------------- labs
def prepare_labs(raw: pd.DataFrame) -> tuple:
    """
    Results as a hospital sends them -> the canonical labs table.

    Each result is matched to one of the 12 tests (LOINC first, then the name),
    converted to the unit the model was trained in, and flagged abnormal from
    the lab's own flag (H, L, A) or, without one, from its reference range.
    Returns (labs, problems): results for other tests are left out silently;
    results that cannot be converted are left out and reported.
    """
    problems = []
    rows = []
    for rec in raw.to_dict("records"):
        key = vocab.lab_key(rec.get("loinc_code"), rec.get("test_name"))
        if key is None:
            continue
        value = pd.to_numeric(rec.get("value"), errors="coerce")
        if pd.isna(value):
            continue
        converted, problem = vocab.convert_lab(key, float(value), rec.get("unit"))
        if problem:
            problems.append({"encounter_key": str(rec.get("encounter_key")), "problem": problem})
            continue
        flag = str(rec.get("abnormal_flag") or "").strip().upper()
        if flag:
            abnormal = int(flag in ("H", "L", "A", "HH", "LL", "ABNORMAL"))
        else:
            low = pd.to_numeric(rec.get("reference_low"), errors="coerce")
            high = pd.to_numeric(rec.get("reference_high"), errors="coerce")
            abnormal = int((pd.notna(low) and value < low) or (pd.notna(high) and value > high))
        rows.append({"encounter_key": str(rec.get("encounter_key")), "lab": key, "value": converted,
                     "result_datetime": rec.get("result_datetime"), "abnormal": abnormal})
    cols = ["encounter_key", "lab", "value", "result_datetime", "abnormal"]
    return pd.DataFrame(rows, columns=cols), problems


def labs(lab_rows: pd.DataFrame, encounter_keys) -> pd.DataFrame:
    """Last, lowest, highest value and number of abnormal results for each of
    the 12 tests, per stay (cells 13-14)."""
    keys = pd.Index(pd.Series(list(encounter_keys), dtype=str).unique(), name="encounter_key")
    out = pd.DataFrame(index=keys)
    lab_cols = [f"lab_{lab}_{stat}" for stat in ("last", "min", "max", "n_abn")
                for lab in vocab.LAB_KEYS]
    if len(lab_rows):
        r = lab_rows.copy()
        r["encounter_key"] = _keys(r)
        r["value"] = pd.to_numeric(r["value"], errors="coerce")
        r = r[r["encounter_key"].isin(keys) & r["value"].notna()]
        r["result_datetime"] = pd.to_datetime(r["result_datetime"], errors="coerce")
        r = r.sort_values("result_datetime", kind="mergesort")
        agg = r.groupby(["encounter_key", "lab"]).agg(
            last=("value", "last"), min=("value", "min"), max=("value", "max"),
            n_abn=("abnormal", "sum"))
        if len(agg):
            wide = agg.unstack("lab")
            wide.columns = [f"lab_{lab}_{stat}" for stat, lab in wide.columns]
            out = out.join(wide)
    for col in lab_cols:
        if col not in out:
            out[col] = np.nan
    return out


# ---------------------------------------------------------------- assemble
def assemble(encounters: pd.DataFrame, patients: pd.DataFrame, diagnoses: pd.DataFrame,
             procedures: pd.DataFrame, drg: pd.DataFrame, medications_: pd.DataFrame,
             lab_rows: pd.DataFrame, as_of: Optional[pd.Timestamp] = None,
             include_excluded: bool = False) -> pd.DataFrame:
    """
    One row per stay: the identifiers, the outcome and the 94 inputs in the
    model's order and vocabulary (cell 16). Stays the notebook excluded (died,
    hospice, observation) are dropped unless `include_excluded`, which keeps
    them with their `excluded` reason so a caller can say why one was not scored.
    """
    adm = cohort(encounters, as_of=as_of)
    keys = adm["encounter_key"]

    people = patients.copy()
    people["patient_key"] = _keys(people, "patient_key")
    people = people.drop_duplicates("patient_key", keep="last").set_index("patient_key")
    X = adm.join(people[[c for c in ("sex", "marital_status", "race_ethnicity") if c in people]],
                 on="patient_key", rsuffix="_patient")
    for col in ("marital_status", "race_ethnicity"):
        own, theirs = (X[col] if col in X else None), X.get(f"{col}_patient")
        X[col] = own.where(own.notna(), theirs) if own is not None and theirs is not None \
            else (own if own is not None else theirs)

    X = (X.join(charlson(diagnoses, keys), on="encounter_key")
          .join(severity(drg, procedures, keys), on="encounter_key")
          .join(medications(medications_, keys), on="encounter_key")
          .join(labs(lab_rows, keys), on="encounter_key"))

    # Categoricals in the model's vocabulary; anything else is "UNKNOWN".
    X["gender"] = X.get("sex").map(vocab.MODEL_GENDER) if "sex" in X else np.nan
    X["insurance"] = X["payer_class"].map(vocab.MODEL_INSURANCE) if "payer_class" in X else np.nan
    X["admission_type"] = X["admission_type"].map(vocab.MODEL_ADMISSION_TYPE)
    X["marital_status"] = X["marital_status"].map(vocab.MODEL_MARITAL) if X["marital_status"] is not None else np.nan
    X["race"] = X["race_ethnicity"].map(vocab.MODEL_RACE) if X["race_ethnicity"] is not None else np.nan
    X["anchor_age"] = pd.to_numeric(X["age_years"], errors="coerce")

    X["los_days"] = (X["discharge_datetime"] - X["admit_datetime"]).dt.total_seconds() / 86400
    X["ed_visit"] = X["ed_arrival_datetime"].notna().astype(int)
    X["ed_hours"] = (X["ed_departure_datetime"] - X["ed_arrival_datetime"]).dt.total_seconds() / 3600
    X["is_emergency"] = X["admission_type"].str.contains("EMER|URGENT", na=False).astype(int)
    for col in ("n_procedures", "n_drug_orders", "n_distinct_drugs"):
        X[col] = X[col].fillna(0)
    X["prior_adm_flag"] = (X["n_prior_adm"] > 0).astype(int)
    X["readmit_history"] = (X["days_since_prev"].notna() & (X["days_since_prev"] <= 30)).astype(int)
    for col in [c for c in X.columns if c.startswith("med_")]:
        X[col] = X[col].fillna(0)
    X["disch_home"] = X["discharge_disposition"].eq("HOME").astype(int)
    X["disch_snf"] = X["discharge_disposition"].isin(vocab.SNF_DISPOSITIONS).astype(int)
    X["disch_ama"] = X["discharge_disposition"].eq("AMA").astype(int)
    for col in ("gender", "insurance", "admission_type", "marital_status", "race"):
        X[col] = X[col].fillna("UNKNOWN").astype(object)

    if not include_excluded:
        X = X[X["excluded"].eq("")]
    cols = ID_COLUMNS + [OUTCOME] + (["excluded"] if include_excluded else []) + feature_names()
    return X[cols].reset_index(drop=True)

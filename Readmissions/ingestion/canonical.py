"""
Stored stays (ingest_stays) -> the canonical tables features/discharge_features.py
builds the 94 inputs from.

Used by the worker to score new discharges and by
scripts/export_discharge_training_csv.py to build a Model Studio training file,
so both see a patient's stays assembled the same way.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from features import discharge_features

ENCOUNTER_FIELDS = ["admit_datetime", "discharge_datetime", "age_years", "admission_type",
                    "discharge_disposition", "died_in_hospital", "payer_class", "ed_arrival_datetime",
                    "ed_departure_datetime", "observation_stay", "planned_admission"]


def tables(stays: list) -> dict:
    """The canonical tables for these stays. `patient_key` is the stay's
    hospital-prefixed patient_id, so stays from two hospitals never merge."""
    enc_rows, people, dx, procs, drgs, meds, raw_labs = [], {}, [], [], [], [], []
    for s in stays:
        key, pid = str(s["encounter_key"]), str(s["patient_id"])
        e = s["encounter"]
        enc_rows.append({"patient_key": pid, "encounter_key": key,
                         **{f: e.get(f) for f in ENCOUNTER_FIELDS}})
        people[pid] = s.get("patient") or {}
        dx += [{"encounter_key": key, **d} for d in s.get("diagnoses") or []]
        procs += [{"encounter_key": key, **p} for p in s.get("procedures") or []]
        if s.get("drg"):
            drgs.append({"encounter_key": key, **s["drg"]})
        meds += [{"encounter_key": key, **m} for m in s.get("medications") or []]
        raw_labs += [{"encounter_key": key, **lab} for lab in s.get("labs") or []]

    def frame(rows, cols):
        return pd.DataFrame(rows, columns=cols) if not rows else pd.DataFrame(rows)

    patients = pd.DataFrame([{"patient_key": pid, **{k: p.get(k) for k in
                                                     ("sex", "marital_status", "race_ethnicity")}}
                             for pid, p in people.items()],
                            columns=["patient_key", "sex", "marital_status", "race_ethnicity"])
    lab_rows, _ = discharge_features.prepare_labs(
        frame(raw_labs, ["encounter_key", "test_name", "loinc_code", "value", "unit",
                         "result_datetime", "abnormal_flag", "reference_low", "reference_high"]))
    return {
        "encounters": frame(enc_rows, ["patient_key", "encounter_key"] + ENCOUNTER_FIELDS),
        "patients": patients,
        "diagnoses": frame(dx, ["encounter_key", "icd_code", "icd_version", "sequence"]),
        "procedures": frame(procs, ["encounter_key", "procedure_code", "code_system"]),
        "drg": frame(drgs, ["encounter_key", "drg_system", "drg_code", "severity_of_illness",
                            "risk_of_mortality"]),
        "medications_": frame(meds, ["encounter_key", "drug_name"]),
        "lab_rows": lab_rows,
    }


EXTRA_PREFIX = "x_"
# The inputs that come from a stay's place in the patient's history.
HISTORY_FEATURES = ("n_prior_adm", "days_since_prev", "prior_adm_flag", "readmit_history")


def training_table(stays: list, as_of=None) -> pd.DataFrame:
    """A hospital's scorable discharges with their outcome as far as known, in
    the columns live scoring produces: what Model Studio trains on (the Data
    page's download and scripts/export_discharge_training_csv.py)."""
    cols = ["patient_key", "discharge_datetime", discharge_features.OUTCOME] + discharge_features.feature_names()
    if not stays:
        return pd.DataFrame(columns=cols)
    feats = features(stays, as_of=pd.Timestamp(as_of) if as_of else pd.Timestamp.now(), include_excluded=False)
    extras = [c for c in feats.columns if c.startswith(EXTRA_PREFIX)]
    return feats[cols + extras].reset_index(drop=True)


def features(stays: list, as_of=None, include_excluded: bool = True) -> pd.DataFrame:
    """One row per stay: identifiers, outcome, the 94 inputs (and, by default,
    the reason a stay is excluded, so a caller can say why it was not scored),
    then the hospital's own extra columns as `x_<name>` (ingestion/profile.py).
    The extras feed only that hospital's Model Studio model; the built-in model
    reads its 94 inputs by name and never sees them."""
    out = discharge_features.assemble(**tables(stays), as_of=as_of, include_excluded=include_excluded)
    keys = out["encounter_key"].astype(str)

    # Without both dates a stay cannot be placed in the patient's history: how
    # many admissions came before it, the gap since the last, and whether the
    # next one fell within 30 days are unknown, not zero.
    undated = {str(s["encounter_key"]) for s in stays
               if not ((s.get("encounter") or {}).get("admit_datetime")
                       and (s.get("encounter") or {}).get("discharge_datetime"))}
    if undated:
        out.loc[keys.isin(undated), list(HISTORY_FEATURES) + [discharge_features.OUTCOME]] = np.nan
    # The hospital's own record of the outcome, when its file has one, is the
    # outcome: it saw readmissions to other hospitals that its dates cannot show.
    reported = {str(s["encounter_key"]): s["reported_outcome"] for s in stays
                if s.get("reported_outcome") is not None}
    if reported:
        has = keys.isin(reported)
        out.loc[has, discharge_features.OUTCOME] = keys[has].map(reported).astype(float)

    extras = {str(s["encounter_key"]): s.get("extras") or {} for s in stays}
    names = sorted({k for e in extras.values() for k in e})
    for name in names:
        values = out["encounter_key"].astype(str).map(lambda key: extras.get(key, {}).get(name))
        numbers = pd.to_numeric(values, errors="coerce")
        # Numbers stay numbers only when every value given is one; a column
        # that mixes "3" and "high" is a category.
        out[EXTRA_PREFIX + name] = numbers if numbers.notna().sum() == values.notna().sum() else values
    return out

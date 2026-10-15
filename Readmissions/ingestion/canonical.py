"""
Stored stays (ingest_stays) -> the canonical tables features/discharge_features.py
builds the 94 inputs from.

Used by the worker to score new discharges and by
scripts/export_discharge_training_csv.py to build a Model Studio training file,
so both see a patient's stays assembled the same way.
"""
from __future__ import annotations

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


def features(stays: list, as_of=None, include_excluded: bool = True) -> pd.DataFrame:
    """One row per stay: identifiers, outcome, the 94 inputs (and, by default,
    the reason a stay is excluded, so a caller can say why it was not scored)."""
    return discharge_features.assemble(**tables(stays), as_of=as_of,
                                       include_excluded=include_excluded)

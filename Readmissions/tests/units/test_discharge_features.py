"""features/discharge_features.py against the Phase 1 notebook itself.

The parity test writes a small MIMIC-format hospital to disk, runs the
notebook's own cells (5, 7, 9, 11, 13, 14, 16) over it, runs the same records
through ingestion/mimic_adapter.py and assemble(), and requires the same 94
inputs and outcome for every stay. The records are chosen to hit each rule:
a readmission after an excluded stay, an elective return, deaths, hospice and
observation stays, the Charlson hierarchy, ICD-9 and ICD-10 codes with and
without dots, duplicate drug orders, a urine test the blood one must win over,
missing values.
"""
import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from features import discharge_features as df_
from ingestion import mimic_adapter, vocab

NOTEBOOK = Path(__file__).resolve().parents[2] / "notebooks/mimic/phase1_discharge_risk_mimic.ipynb"

ADM_COLS = ["subject_id", "hadm_id", "admittime", "dischtime", "admission_type",
            "admission_location", "discharge_location", "insurance", "marital_status", "race",
            "edregtime", "edouttime", "hospital_expire_flag"]


def _hospital() -> dict:
    A = [  # subject, hadm, admit, discharge, type, discharge location, insurance, marital, race, ED in, ED out, died
        (101, 1, "2150-01-01 08:00", "2150-01-05 12:00", "EW EMER.", "HOME", "Medicare", "MARRIED", "WHITE",
         "2150-01-01 05:00", "2150-01-01 07:30", 0),
        (101, 2, "2150-01-15 10:00", "2150-01-20 09:00", "URGENT", "SKILLED NURSING FACILITY", "Medicare",
         "MARRIED", "WHITE", None, None, 0),
        (101, 3, "2150-02-05 09:00", "2150-02-07 15:00", "ELECTIVE", "HOME HEALTH CARE", "Medicare",
         "MARRIED", "WHITE", None, None, 0),
        (101, 4, "2150-03-01 09:00", "2150-03-02 10:00", "EU OBSERVATION", "HOME", "Medicare", "MARRIED",
         "WHITE", "2150-03-01 06:00", "2150-03-01 08:00", 0),
        (101, 5, "2150-03-10 09:00", "2150-03-15 11:00", "EW EMER.", "AGAINST ADVICE", "Medicare",
         "MARRIED", "WHITE", "2150-03-10 02:00", "2150-03-10 08:30", 0),
        (102, 6, "2151-06-01 09:00", "2151-06-03 14:00", "DIRECT EMER.", "HOME", "Private", "SINGLE",
         "BLACK/AFRICAN AMERICAN", None, None, 0),
        (102, 7, "2151-06-20 09:00", "2151-06-25 14:00", "EW EMER.", "DIED", "Private", "SINGLE",
         "BLACK/AFRICAN AMERICAN", "2151-06-20 01:00", "2151-06-20 08:00", 1),
        (103, 8, "2152-01-01 09:00", "2152-01-10 14:00", "SURGICAL SAME DAY ADMISSION", "HOSPICE",
         "No charge", "WIDOWED", "ASIAN - CHINESE", None, None, 0),
        (103, 9, "2152-02-01 09:00", "2152-02-04 14:00", "ELECTIVE", "REHAB", "No charge", "WIDOWED",
         "ASIAN - CHINESE", None, None, 0),
        (103, 10, "2152-02-20 09:00", "2152-02-22 14:00", "URGENT", "CHRONIC/LONG TERM ACUTE CARE",
         "Other", None, "UNKNOWN", None, None, 0),
    ]
    admissions = pd.DataFrame([dict(zip(ADM_COLS[:4] + ADM_COLS[4:5] + ADM_COLS[6:], (*a[:5], *a[5:])))
                               for a in A])
    admissions["admission_location"] = "EMERGENCY ROOM"
    admissions = admissions[ADM_COLS]
    patients = pd.DataFrame({"subject_id": [101, 102, 103], "gender": ["F", "M", "F"],
                             "anchor_age": [70, 55, 91], "anchor_year": [2150, 2151, 2152],
                             "anchor_year_group": ["2008 - 2010"] * 3, "dod": [None] * 3})
    dx = [(1, "I5023", 10), (1, "E1122", 10), (1, "E119", 10), (1, "I252", 10), (1, "Z9581", 10),
          (2, "4280", 9), (2, "25000", 9), (2, "5859", 9), (3, "J449", 10),
          (4, "I509", 10), (5, "C787", 10), (5, "C189", 10), (5, "C509", 10), (5, "B20", 10),
          (6, "F0390", 10), (6, "K7030", 10), (6, "K7040", 10), (7, "I219", 10),
          (9, "49390", 9), (9, "I25.2", 10), (9, "4011", 9)]
    diagnoses = pd.DataFrame([{"subject_id": 0, "hadm_id": h, "seq_num": i + 1, "icd_code": c,
                               "icd_version": v} for i, (h, c, v) in enumerate(dx)])
    procedures = pd.DataFrame([{"subject_id": 0, "hadm_id": h, "seq_num": 1, "chartdate": "2150-01-02",
                                "icd_code": c, "icd_version": 10}
                               for h, c in [(1, "02HV33Z"), (1, "5A1955Z"), (2, "0DJ08ZZ"),
                                            (5, "3E033GC"), (5, "0W9930Z"), (5, "B2111ZZ")]])
    drg = pd.DataFrame([{"subject_id": 0, "hadm_id": h, "drg_type": t, "drg_code": "1",
                         "description": "x", "drg_severity": s, "drg_mortality": m}
                        for h, t, s, m in [(1, "APR", 3, 2), (1, "HCFA", None, None), (2, "APR", 2, 1),
                                           (5, "APR", 3, 3), (5, "APR", 4, 2), (9, "APR", 1, 1)]])
    rx = [(1, "Insulin Glargine"), (1, "Heparin"), (1, "Furosemide"), (1, "Furosemide"),
          (1, "Acetaminophen"), (2, "Warfarin"), (2, "Quetiapine"), (5, "Morphine Sulfate"),
          (5, "HYDROmorphone (Dilaudid)"), (6, "Spironolactone"), (9, "Acetaminophen"), (7, "Heparin")]
    prescriptions = pd.DataFrame([{"subject_id": 0, "hadm_id": h, "drug": d} for h, d in rx])
    items = [(50912, "Creatinine", "Blood"), (51082, "Creatinine", "Urine"), (50931, "Glucose", "Blood"),
             (51478, "Glucose", "Urine"), (50983, "Sodium", "Blood"), (50971, "Potassium", "Blood"),
             (51006, "Urea Nitrogen", "Blood"), (51222, "Hemoglobin", "Blood"),
             (51301, "White Blood Cells", "Blood"), (51265, "Platelet Count", "Blood"),
             (50862, "Albumin", "Blood"), (50882, "Bicarbonate", "Blood"), (51237, "INR(PT)", "Blood"),
             (50852, "% Hemoglobin A1c", "Blood"), (50868, "Anion Gap", "Blood")]
    d_labitems = pd.DataFrame([{"itemid": i, "label": l, "fluid": f, "category": "Chemistry"}
                               for i, l, f in items])
    lev = [(1, 50912, "2150-01-02 06:00", 1.1, None), (1, 50912, "2150-01-03 06:00", 1.8, "abnormal"),
           (1, 50912, "2150-01-04 06:00", 1.4, None), (1, 50931, "2150-01-02 06:00", 210, "abnormal"),
           (1, 50931, "2150-01-04 06:00", 140, None), (1, 50983, "2150-01-02 06:00", 131, "abnormal"),
           (1, 51082, "2150-01-02 07:00", 80, None), (1, 51478, "2150-01-02 07:00", 300, "abnormal"),
           (1, 50852, "2150-01-02 06:00", 8.1, "abnormal"), (2, 51222, "2150-01-16 06:00", 9.5, "abnormal"),
           (2, 51222, "2150-01-18 06:00", 10.1, "abnormal"), (2, 50971, "2150-01-16 06:00", 5.6, "abnormal"),
           (2, 50971, "2150-01-17 06:00", None, None), (5, 51301, "2150-03-11 06:00", 14, "abnormal"),
           (5, 51265, "2150-03-11 06:00", 120, "abnormal"), (5, 51237, "2150-03-11 06:00", 1.6, "abnormal"),
           (5, 50862, "2150-03-11 06:00", 2.9, "abnormal"), (5, 50882, "2150-03-11 06:00", 20, "abnormal"),
           (5, 51006, "2150-03-11 06:00", 40, "abnormal"), (6, 50983, "2151-06-02 06:00", 140, None),
           (9, 50931, "2152-02-02 06:00", 95, None), (9, 50868, "2152-02-02 06:00", 12, None)]
    labevents = pd.DataFrame([{"labevent_id": i, "subject_id": 0, "hadm_id": h, "itemid": it,
                               "charttime": t, "valuenum": v, "flag": f}
                              for i, (h, it, t, v, f) in enumerate(lev)])
    return {"admissions": admissions, "patients": patients, "diagnoses_icd": diagnoses,
            "procedures_icd": procedures, "drgcodes": drg, "prescriptions": prescriptions,
            "labevents": labevents, "d_labitems": d_labitems}


def _run_notebook(tables: dict, tmp_path) -> pd.DataFrame:
    """The notebook's own cells over the tables; returns the matrix it saves."""
    for name, frame in tables.items():
        frame.to_csv(tmp_path / f"{name}.csv", index=False)
    saved = {}
    import gc
    import time
    ns = {"pd": pd, "np": np, "gc": gc, "time": time,
          "tbl": lambda name: str(tmp_path / f"{name}.csv"),
          "report": lambda *a, **k: None, "load_cache": lambda name: None,
          "save_cache": lambda frame, name: saved.__setitem__(name, frame.copy()),
          "downcast": lambda frame: frame, "CHUNK_ROWS": 1000, "COMPACT_EVERY": 8}
    cells = json.loads(NOTEBOOK.read_text())["cells"]
    for i in (5, 7, 9, 11, 13, 14, 16):
        exec("".join(cells[i]["source"]), ns)                     # noqa: S102 - our own notebook
    return saved["phase1_matrix"]


@pytest.fixture(scope="module")
def both(tmp_path_factory):
    tables = _hospital()
    notebook = _run_notebook(tables, tmp_path_factory.mktemp("mimic"))
    ours = df_.assemble(**mimic_adapter.to_canonical(**tables))
    return notebook.set_index(notebook["hadm_id"].astype(str)), ours.set_index("encounter_key")


def test_the_same_stays_are_kept(both):
    notebook, ours = both
    assert sorted(notebook.index) == sorted(ours.index) == ["1", "10", "2", "3", "5", "6", "9"]


def test_the_outcome_matches_the_notebook(both):
    notebook, ours = both
    assert notebook["readmit_30d"].to_dict() == ours["readmit_30d"].astype(int).to_dict()
    # 1 readmitted at 10 days; 2 returned electively; 3's return was an
    # (excluded) observation stay, which still counts; 6 died on its return.
    assert ours.loc[["1", "2", "3", "6"], "readmit_30d"].tolist() == [1, 0, 1, 1]


@pytest.mark.parametrize("feature", df_.feature_names())
def test_every_input_matches_the_notebook(both, feature):
    notebook, ours = both
    theirs, mine = notebook[feature], ours.loc[notebook.index, feature]
    if feature == "race":           # 33 MIMIC values -> 8 canonical ones, by design
        theirs = theirs.astype(str).map(mimic_adapter.race).map(vocab.MODEL_RACE)
    if theirs.dtype.name == "category" or mine.dtype == object:
        assert theirs.astype(str).tolist() == mine.astype(str).tolist(), feature
    else:
        assert np.allclose(theirs.astype(float), mine.astype(float), equal_nan=True), \
            (feature, theirs.tolist(), mine.tolist())


# ------------------------------------------------ beyond what MIMIC exercises
def _one_patient(**over):
    enc = {"patient_key": "P1", "encounter_key": "E1", "admit_datetime": "2026-09-01",
           "discharge_datetime": "2026-09-05", "age_years": 70, "admission_type": "EMERGENCY",
           "discharge_disposition": "HOME", "died_in_hospital": False, "payer_class": "MEDICARE",
           "ed_arrival_datetime": None, "ed_departure_datetime": None}
    return pd.DataFrame([{**enc, **over}])


def test_an_open_follow_up_window_is_an_unknown_outcome():
    enc = _one_patient()
    known = df_.cohort(enc, as_of=pd.Timestamp("2026-11-01"))[df_.OUTCOME]
    unknown = df_.cohort(enc, as_of=pd.Timestamp("2026-09-20"))[df_.OUTCOME]
    assert known.tolist() == [0.0] and unknown.isna().all()


def test_a_planned_return_is_not_a_readmission():
    enc = pd.concat([_one_patient(),
                     _one_patient(encounter_key="E2", admit_datetime="2026-09-12",
                                  discharge_datetime="2026-09-13", admission_type="EMERGENCY",
                                  planned_admission=True)])
    assert df_.cohort(enc)[df_.OUTCOME].tolist()[0] == 0.0


def test_lab_results_are_matched_converted_and_flagged():
    raw = pd.DataFrame([
        {"encounter_key": "E1", "loinc_code": "2160-0", "test_name": "Creat", "value": 159.12,
         "unit": "umol/L", "abnormal_flag": "H"},
        {"encounter_key": "E1", "loinc_code": None, "test_name": "Glucose", "value": 10, "unit": "mmol/L",
         "abnormal_flag": None, "reference_low": 3.9, "reference_high": 7.8},
        {"encounter_key": "E1", "loinc_code": "4548-4", "test_name": "HbA1c", "value": 64,
         "unit": "mmol/mol", "abnormal_flag": "N"},
        {"encounter_key": "E1", "loinc_code": "2951-2", "test_name": "Na", "value": 140, "unit": "furlongs"},
        {"encounter_key": "E1", "loinc_code": "9999-9", "test_name": "Ferritin", "value": 50, "unit": "ng/mL"},
    ])
    labs, problems = df_.prepare_labs(raw)
    got = {r["lab"]: (round(r["value"], 2), r["abnormal"]) for r in labs.to_dict("records")}
    assert got == {"creatinine": (1.8, 1), "glucose": (180.0, 1), "hba1c": (8.01, 0)}
    assert len(problems) == 1 and "furlongs" in problems[0]["problem"]

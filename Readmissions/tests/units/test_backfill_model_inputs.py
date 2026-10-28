"""Storing the model's 94 inputs for the MIMIC patients already on the worklist
(scripts/backfill_model_inputs.py). Mongomock and hand-built frames only: the
script is never pointed at a real database from here.
"""
import sys
from pathlib import Path

import bson
import mongomock
import numpy as np
import pandas as pd
import pytest

from api import mimic_scoring

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import backfill_model_inputs as backfill  # noqa: E402

BATCH = "2026-04-29"
STATE = mimic_scoring._load()
FEATURES = STATE["features"]
CATEGORICALS = STATE["categoricals"]


def matrix_row(subject, hadm, prior, seed):
    row = {"subject_id": subject, "hadm_id": hadm, "readmit_30d": 0}
    for i, f in enumerate(FEATURES):
        if f in CATEGORICALS:
            row[f] = CATEGORICALS[f][seed % len(CATEGORICALS[f])]
        else:
            row[f] = float(seed * 100 + i)
    row["n_prior_adm"] = prior
    row["anchor_age"] = 60 + seed
    row[[f for f in FEATURES if f not in CATEGORICALS and f not in ("n_prior_adm", "anchor_age")][0]] = np.nan
    return row


@pytest.fixture
def frames():
    matrix = pd.DataFrame([
        matrix_row(1, 11, 0, 1), matrix_row(1, 12, 1, 2),          # MIMIC-1: the earlier stay is on the list
        matrix_row(2, 21, 0, 3), matrix_row(2, 22, 1, 4),          # MIMIC-2: moved in time by the demo prep
        matrix_row(3, 31, 0, 5), matrix_row(3, 32, 2, 6),          # MIMIC-3: no date matches
    ])
    for col, levels in CATEGORICALS.items():
        matrix[col] = pd.Categorical(matrix[col], categories=levels)
    matrix["n_prior_adm"] = matrix["n_prior_adm"].astype("int64")
    timeline = pd.DataFrame({
        "hadm_id": [11, 12, 21, 22, 31, 32],
        "disch_real": pd.to_datetime(["2026-04-10", "2026-05-20", "2026-03-01", "2026-04-02",
                                      "2026-02-01", "2026-03-15"])})
    return matrix, timeline


@pytest.fixture
def db():
    db = mongomock.MongoClient()["neuroshield"]
    for pid, disch in (("MIMIC-1", "2026-04-10"), ("MIMIC-2", "2026-04-05"), ("MIMIC-3", "2026-04-10"),
                       ("MIMIC-9", "2026-04-10")):
        db["patient_worklist"].insert_one({"patient_id": pid, "batch_date": BATCH, "discharge_date": disch,
                                           "source": "mimic", "risk_score": 40.0})
        db["care_actions"].insert_one({"patient_id": pid, "hospital_id": "hosp-a"})
    db["patient_worklist"].insert_one({"patient_id": "HA-P-1", "batch_date": BATCH, "source": "ingest"})
    db["demo_prep"].insert_one({"patient_id": "MIMIC-2", "shift_days": 3})   # stored 5 April = 2 April + 3
    return db


def planned(db, frames):
    docs, report = backfill.plan(db, *frames, FEATURES)
    return {d["patient_id"]: d for d in docs}, report


def test_the_worklist_admission_is_matched_by_its_discharge_date(db, frames):
    docs, report = planned(db, frames)
    assert docs["MIMIC-1"]["hadm_id"] == 11 and docs["MIMIC-1"]["matched_on"] == "discharge_date"
    assert docs["MIMIC-1"]["discharge_date"] == "2026-04-10"
    assert docs["MIMIC-1"]["features"]["anchor_age"] == 61
    assert "HA-P-1" not in docs and report["worklist_patients"] == 4


def test_a_demo_patient_moved_in_time_is_matched_by_the_original_date(db, frames):
    docs, _ = planned(db, frames)
    assert docs["MIMIC-2"]["hadm_id"] == 22 and docs["MIMIC-2"]["matched_on"] == "discharge_date"


def test_without_a_date_match_the_latest_admission_is_used(db, frames):
    docs, report = planned(db, frames)
    assert docs["MIMIC-3"]["hadm_id"] == 32 and docs["MIMIC-3"]["matched_on"] == "latest"
    assert (report["matched_on_discharge_date"], report["matched_on_latest"]) == (2, 1)


def test_a_patient_missing_from_the_matrix_is_reported(db, frames):
    docs, report = planned(db, frames)
    assert "MIMIC-9" not in docs and report["not_in_matrix"] == ["MIMIC-9"]


def test_values_are_stored_safely(db, frames):
    docs, _ = planned(db, frames)
    doc = docs["MIMIC-1"]
    bson.encode(doc)                                                   # nothing numpy or NaN-typed
    stored = doc["features"]
    assert set(stored) == set(FEATURES)
    assert stored["gender"] == CATEGORICALS["gender"][1 % len(CATEGORICALS["gender"])] and isinstance(stored["gender"], str)
    assert isinstance(stored["n_prior_adm"], int) and not isinstance(stored["n_prior_adm"], bool)
    assert isinstance(stored["anchor_age"], int)
    nan_col = [f for f in FEATURES if f not in CATEGORICALS and f not in ("n_prior_adm", "anchor_age")][0]
    assert stored[nan_col] is None
    other = next(f for f in FEATURES if f not in CATEGORICALS and f not in ("n_prior_adm", "anchor_age", nan_col))
    assert type(stored[other]) is float and stored[other] == frames[0].iloc[0][other]
    assert doc["hospital_id"] == "hosp-a" and doc["current"] is True and doc["source"] == "mimic_matrix"
    assert len(doc["feature_set"]) == 12


def test_a_preview_writes_nothing(db, frames):
    before = {n: list(db[n].find({}, {"_id": 0})) for n in db.list_collection_names()}
    backfill.plan(db, *frames, FEATURES)
    assert {n: list(db[n].find({}, {"_id": 0})) for n in db.list_collection_names()} == before
    assert "model_inputs" not in db.list_collection_names()


def test_writing_twice_changes_nothing_and_keeps_one_current_row(db, frames):
    docs, _ = backfill.plan(db, *frames, FEATURES)
    first = backfill.write(db, docs, batch=2)
    assert first == {"inserted": 3, "updated": 0, "unchanged": 0}
    stored = list(db["model_inputs"].find({}, {"_id": 0}))
    docs, _ = backfill.plan(db, *frames, FEATURES)
    again = backfill.write(db, docs, batch=2)
    assert again == {"inserted": 0, "updated": 0, "unchanged": 3}
    assert list(db["model_inputs"].find({}, {"_id": 0})) == stored
    # The worklist moves to another admission: the old row stays, but stops being current.
    db["patient_worklist"].update_one({"patient_id": "MIMIC-1"}, {"$set": {"discharge_date": "2026-05-20"}})
    docs, _ = backfill.plan(db, *frames, FEATURES)
    backfill.write(db, docs)
    rows = list(db["model_inputs"].find({"patient_id": "MIMIC-1"}))
    assert sorted((r["hadm_id"], r["current"]) for r in rows) == [(11, False), (12, True)]
    assert db["model_inputs"].count_documents({"patient_id": "MIMIC-1", "current": True}) == 1
    # Only this collection was touched.
    assert db["patient_worklist"].count_documents({}) == 5


def test_stored_inputs_reproduce_the_score():
    path = ROOT / backfill.DEFAULT_MATRIX
    if not path.exists():
        pytest.skip("the Phase 1 matrix is not on this machine")
    matrix = pd.read_parquet(path, columns=["subject_id", "hadm_id"] + FEATURES)
    rows = matrix.iloc[[0, len(matrix) // 2, len(matrix) - 1]]
    expected = (STATE["model"].predict_proba(rows[FEATURES])[:, 1] * 100).round(1)
    for (_, row), want in zip(rows.iterrows(), expected):
        got = mimic_scoring.score_features(mimic_scoring.features_for_storage(row[FEATURES]))
        assert got["risk_score"] == float(want)
        assert got["driver_1_raw"] and got["driver_2_raw"] and got["driver_3_raw"]

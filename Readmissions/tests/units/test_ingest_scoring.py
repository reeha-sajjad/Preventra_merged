"""Ingested discharges and weekly check-ins, scored by the worker and read back
through the dashboard's own routes (ingestion/worker.py, api/scoring_service.py).

Same two hospitals and accounts as test_isolation.py; the ingest credential
belongs to hosp-a.
"""
import json
import sys
from pathlib import Path

import mongomock
import mongomock.gridfs
import numpy as np
import pandas as pd
import pytest

from api import model_studio, scoring_service, studio_llm
from api.studio_templates import STANDARD_PIPELINE
from ingestion import worker
from tests.units.test_ingest import bundle, machine, token  # noqa: F401
from tests.units.test_isolation import LATEST, as_user, main, world  # noqa: F401

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import export_discharge_training_csv as discharge_export  # noqa: E402
import export_weekly_training_csv as weekly_export  # noqa: E402
from features.discharge_features import feature_names  # noqa: E402

mongomock.gridfs.enable_gridfs_integration()
PID = "HA-P-1"


@pytest.fixture
def studio(world, tmp_path, monkeypatch):
    monkeypatch.setattr(model_studio, "WORK_ROOT", str(tmp_path / "studio"))
    monkeypatch.setattr(studio_llm, "available", lambda: False)
    return world


def send(main, token, *bundles):
    r = machine(main, token).post("/ingest/v1/encounters", json={"bundles": list(bundles)})
    assert r.status_code == 202 and not r.json()["rejected"], r.text
    return r.json()["batch_id"]


def send_weeks(main, token, *weeks):
    r = machine(main, token).post("/ingest/v1/weeks", json={"weeks": list(weeks)})
    assert r.status_code == 202 and not r.json()["rejected"], r.text


def week(date, **readings):
    return {"patient_key": "P-1", "encounter_key": "E-1", "week_date": date,
            "source": "nurse_call", "readings": readings}


def died(enc, admit, disch):
    b = bundle(enc, admit=admit, disch=disch)
    b["encounter"].update(died_in_hospital=True, discharge_disposition="DIED")
    return b


# ---------------------------------------------------------- the built-in model
def test_a_discharge_lands_on_the_current_worklist(main, world, token):
    db = world["db"]
    batch = send(main, token, bundle())
    assert worker.run(db, once=True) == 1

    row = db["patient_worklist"].find_one({"patient_id": PID})
    # The dashboard reads only the newest batch_date, so ingested rows join it.
    assert row["batch_date"] == LATEST
    assert db["patient_worklist"].count_documents({"batch_date": LATEST}) == 7
    assert row["model_version"] == scoring_service.BUILTIN_VERSION and 0 <= row["risk_score"] <= 100
    assert row["driver_1"] and row["clinical_group"] == "heart_failure"
    assert row["monitoring_status"] == "insufficient_data" and row["band_rank"] > 0
    assert db["executive_summary"].find_one({"batch_date": LATEST})["total_patients"] == 7

    assert db["risk_registry"].count_documents({"patient_id": PID, "stage": "final"}) == 1
    assert db["care_actions"].find_one({"patient_id": PID})["hospital_id"] == "hosp-a"
    week0 = db["weekly_monitoring"].find_one({"patient_id": PID, "week_number": 0})
    assert week0["discharge_baseline"]["dry_weight_kg"] == 84.2 and week0["risk_score"] == row["risk_score"]

    status = machine(main, token).get(f"/ingest/v1/batches/{batch}").json()
    assert (status["status"], status["scored"]) == ("done", 1)

    def listed(email):
        rows = as_user(main, world, email).get("/api/patients", params={"limit": 100}).json()["data"]
        return {r["id"] for r in rows}
    assert PID in listed("admin@a.test") and PID in listed("cm@a.test")
    assert PID not in listed("admin@b.test")


def test_the_same_job_twice_writes_the_same_things(main, world, token):
    db = world["db"]
    send(main, token, bundle())
    worker.run(db, once=True)
    before = {c: db[c].count_documents({"patient_id": PID})
              for c in ("patient_worklist", "risk_registry", "care_actions", "weekly_monitoring")}
    scoring_service.score_discharges(db, "hosp-a", ["E-1"])
    after = {c: db[c].count_documents({"patient_id": PID}) for c in before}
    assert before == after == {"patient_worklist": 1, "risk_registry": 1, "care_actions": 1,
                               "weekly_monitoring": 1}


def test_the_latest_stay_is_the_row_and_earlier_ones_are_inputs_and_outcomes(main, world, token):
    db = world["db"]
    send(main, token, bundle("E-1"), bundle("E-2", admit="2026-04-20T08:00", disch="2026-04-24T12:00"))
    worker.run(db, once=True)
    row = db["patient_worklist"].find_one({"patient_id": PID})
    assert (row["encounter_key"], row["n_prior_adm"]) == ("E-2", 1)
    assert db["risk_registry"].count_documents({"patient_id": PID}) == 2
    # An unplanned return 15 days later is E-1's readmission.
    assert db["ingest_stays"].find_one({"encounter_key": "E-1"})["readmitted_30d"] is True

    send(main, token, bundle("E-1"))                    # a late resend of the older stay
    worker.run(db, once=True)
    assert db["patient_worklist"].find_one({"patient_id": PID})["encounter_key"] == "E-2"


def test_a_death_takes_the_patient_off_the_list(main, world, token):
    db = world["db"]
    send(main, token, bundle("E-1"))
    worker.run(db, once=True)
    batch = send(main, token, died("E-2", "2026-04-20T08:00", "2026-04-24T12:00"))
    worker.run(db, once=True)
    assert db["patient_worklist"].count_documents({"patient_id": PID}) == 0
    assert db["weekly_monitoring"].count_documents({"patient_id": PID}) == 0
    assert db["risk_registry"].count_documents({"patient_id": PID}) == 1     # history is kept
    reasons = machine(main, token).get(f"/ingest/v1/batches/{batch}").json()["not_scored"]
    assert reasons == [{"encounter_key": "E-2", "reason": "not scored: died in hospital"}]


def test_a_death_in_the_same_batch_as_an_earlier_stay(main, world, token):
    db = world["db"]
    send(main, token, bundle("E-1"), died("E-2", "2026-04-20T08:00", "2026-04-24T12:00"))
    worker.run(db, once=True)
    assert db["patient_worklist"].count_documents({"patient_id": PID}) == 0
    assert db["risk_registry"].count_documents({"patient_id": PID}) == 1


def test_a_job_that_keeps_failing_is_reported(main, world, token, monkeypatch):
    db = world["db"]

    def broken(*_):
        raise RuntimeError("scoring is down")
    monkeypatch.setattr(scoring_service, "score_discharges", broken)
    batch = send(main, token, bundle())
    assert worker.run(db, once=True) == worker.MAX_ATTEMPTS
    status = machine(main, token).get(f"/ingest/v1/batches/{batch}").json()
    assert (status["status"], status["failed"]) == ("failed", 1)
    assert status["errors"] == ["RuntimeError: scoring is down"]


# ----------------------------------------------------------------- weeks
def test_a_weekly_check_in_is_scored_and_a_red_flag_surfaces(main, world, token):
    db = world["db"]
    send(main, token, bundle())
    send_weeks(main, token, week("2026-04-12", weight_kg=87.4, spo2=87, refill_status="late"))
    worker.run(db, once=True)

    wk = db["weekly_monitoring"].find_one({"patient_id": PID, "week_number": 1})
    base = db["weekly_monitoring"].find_one({"patient_id": PID, "week_number": 0})["risk_score"]
    assert wk["scored_by"] == "monitoring_rules" and wk["red_flags"] == ["spo2"]
    assert wk["monitoring"]["weight_change_kg"] == pytest.approx(3.2)
    assert wk["risk_score"] > base and wk["driver_1"]
    assert db["patient_worklist"].find_one({"patient_id": PID})["monitoring_status"] == "action_required"

    watch = as_user(main, world, "cm@a.test").get("/api/rising-risk").json()
    flagged = {i["patient_id"]: i for i in watch["patients"]}
    assert flagged[PID]["red_flags"] and flagged[PID]["severity"] == "urgent"
    trend = as_user(main, world, "cm@a.test").get(f"/api/patients/{PID}/trend")
    assert trend.status_code == 200, trend.text


def test_a_week_sent_before_its_discharge_was_scored_waits_for_it(main, world, token):
    db = world["db"]
    send(main, token, bundle())
    send_weeks(main, token, week("2026-04-12", sbp=150))
    job = worker.claim(db)                              # the discharge job, held by "another worker"
    assert job["kind"] == "discharge"
    worker.run(db, once=True)                           # runs the week job first: nothing to follow yet
    assert db["weekly_monitoring"].count_documents({"patient_id": PID}) == 0
    worker.handle(db, job)                              # the discharge, and the waiting week with it
    assert db["weekly_monitoring"].count_documents({"patient_id": PID}) == 2


# ----------------------------------------------------- a hospital's own model
def _train(tmp_path, name, frame, features, categorical=()):
    wd = tmp_path / name
    wd.mkdir()
    data = frame.copy()
    data["__label__"] = data.pop("outcome")
    data.to_csv(wd / "train.csv", index=False)
    (wd / "pipeline_code.py").write_text(STANDARD_PIPELINE)
    cfg = {"label_column": "outcome", "feature_columns": features,
           "numeric_columns": [f for f in features if f not in categorical],
           "categorical_columns": list(categorical), "algorithm_preference": "logistic_regression"}
    result = model_studio.run_sandbox("train", str(wd), {"config": cfg, "train_file": "train.csv",
                                                         "target_field": "__label__", "group_field": None},
                                      180, lambda m: None)
    assert result["ok"], result["error"]
    reference = json.loads((wd / "train_result.json").read_text())["reference_values"]
    return {**cfg, "reference_values": reference}, (wd / "model.joblib").read_bytes()


def _deploy(db, model_id, target, config, blob, bands):
    model_studio._registry(db).insert_one({
        "model_id": model_id, "name": f"Hospital A {target} model", "target": target,
        "hospital_id": "hosp-a", "algorithm": "logistic_regression", "config": config,
        "code": STANDARD_PIPELINE, "artifact_id": model_studio._gridfs(db).put(blob) if blob else None,
        "bands": bands, "data": {"history": False, "id_column": None, "time_column": None},
        "can_score": True, "created_at": "2026-04-30 00:00:00"})
    model_studio.deploy_model(db, model_id, model_studio.system_actor("hosp-a"))


def test_a_deployed_discharge_model_scores_and_explains(main, studio, token, tmp_path):
    db = studio["db"]
    rng = np.random.default_rng(3)
    n = 800
    frame = pd.DataFrame({"lab_creatinine_last": rng.gamma(4, 0.3, n).round(2),
                          "n_prior_adm": rng.poisson(0.6, n), "anchor_age": rng.integers(30, 90, n),
                          "admission_type": rng.choice(["EW EMER.", "ELECTIVE", "URGENT"], n)})
    logit = 1.8 * (frame["lab_creatinine_last"] - 1.2) + 0.2 * frame["n_prior_adm"] - 1.5
    frame["outcome"] = (rng.random(n) < 1 / (1 + np.exp(-logit))).astype(int)
    config, blob = _train(tmp_path, "discharge", frame,
                          ["lab_creatinine_last", "n_prior_adm", "anchor_age", "admission_type"],
                          categorical=("admission_type",))
    _deploy(db, "m-dis", "discharge", config, blob, {"high_score_threshold": 30.0, "low_score_threshold": 15.0})

    sick = bundle()
    sick["labs"][0]["value"] = 3.5
    send(main, token, sick)
    worker.run(db, once=True)
    row = db["patient_worklist"].find_one({"patient_id": PID})
    assert row["model_version"] == "studio:m-dis"
    expected = "High" if row["risk_score"] >= 30 else "Medium" if row["risk_score"] >= 15 else "Low"
    assert row["risk_band"] == expected
    # Creatinine 3.5 against a typical ~1.1 is what raised this patient's risk.
    assert row["driver_1"].startswith("Creatinine, last value before discharge: 3.5 (raises")
    for driver in (row["driver_1"], row["driver_2"], row["driver_3"]):
        assert not driver or (driver.count(" (") == 1 and driver.endswith(")"))

    # The weeks after it are banded by the same model's thresholds.
    send_weeks(main, token, week("2026-04-12", sbp=150))
    worker.run(db, once=True)
    wk = db["weekly_monitoring"].find_one({"patient_id": PID, "week_number": 1})
    expected = "High" if wk["risk_score"] >= 30 else "Medium" if wk["risk_score"] >= 15 else "Low"
    assert wk["risk_band"] == expected


def test_a_model_whose_inputs_are_missing_falls_back_to_the_built_in(main, studio, token):
    db = studio["db"]
    _deploy(db, "m-odd", "discharge", {"feature_columns": ["ejection_fraction", "bnp", "nyha_class"]},
            None, None)
    batch = send(main, token, bundle())
    worker.run(db, once=True)
    assert db["patient_worklist"].find_one({"patient_id": PID})["model_version"] == \
        scoring_service.BUILTIN_VERSION
    job = db["ingest_jobs"].find_one({"kind": "discharge"})
    assert "0 of 3" in job["result"]["note"]
    assert machine(main, token).get(f"/ingest/v1/batches/{batch}").json()["status"] == "done"


def test_a_deployed_weekly_model_scores_the_weeks(main, studio, token, tmp_path):
    db = studio["db"]
    rng = np.random.default_rng(4)
    n = 600
    frame = pd.DataFrame({"weight_change_kg": rng.normal(0.5, 1.5, n).round(1),
                          "sbp": rng.normal(130, 18, n).round()})
    logit = 1.2 * frame["weight_change_kg"] - 1.0
    frame["outcome"] = (rng.random(n) < 1 / (1 + np.exp(-logit))).astype(int)
    config, blob = _train(tmp_path, "weekly", frame, ["weight_change_kg", "sbp"])
    _deploy(db, "m-wk", "weekly", config, blob, {"high_score_threshold": 50.0, "low_score_threshold": 25.0})

    send(main, token, bundle())
    send_weeks(main, token, week("2026-04-12", weight_kg=88.2, sbp=140))
    worker.run(db, once=True)
    wk = db["weekly_monitoring"].find_one({"patient_id": PID, "week_number": 1})
    assert wk["scored_by"] == wk["model_version"] == "studio:m-wk"
    assert wk["risk_score"] > 50 and wk["risk_band"] == "High"        # 4 kg up, by this model
    assert wk["driver_1"]                               # the rules' readings stay as the card lines


def test_a_weekly_model_missing_its_inputs_leaves_the_rules_scores(main, studio, token):
    db = studio["db"]
    _deploy(db, "m-wk-odd", "weekly", {"feature_columns": ["six_minute_walk", "bnp"]}, None, None)
    send(main, token, bundle())
    send_weeks(main, token, week("2026-04-12", sbp=150))
    worker.run(db, once=True)
    assert db["weekly_monitoring"].find_one({"patient_id": PID, "week_number": 1})["scored_by"] == \
        "monitoring_rules"
    assert "0 of 2" in db["ingest_jobs"].find_one({"kind": "week"})["result"]["note"]


# ---------------------------------------------------------- training exports
def test_the_discharge_export_has_the_outcome_and_the_94_inputs(main, world, token):
    db = world["db"]
    other = bundle("E-9", pat="P-2")
    other["encounter"].update(died_in_hospital=True, discharge_disposition="DIED")
    send(main, token, bundle("E-1"), bundle("E-2", admit="2026-04-20T08:00", disch="2026-04-24T12:00"), other)
    worker.run(db, once=True)

    table = discharge_export.table(db, "hosp-a", as_of="2026-10-01")
    assert list(table.columns) == ["patient_key", "discharge_datetime", "readmit_30d"] + feature_names()
    assert len(feature_names()) == 94
    assert table.set_index(table["patient_key"] + "/" + table["n_prior_adm"].astype(str))[
        "readmit_30d"].to_dict() == {"HA-P-1/0": 1.0, "HA-P-1/1": 0.0}        # the death is left out
    # While E-2's 30 days are still running its outcome is unknown, not "no".
    assert discharge_export.table(db, "hosp-a", as_of="2026-04-30")["readmit_30d"].isna().sum() == 1
    assert discharge_export.table(db, "hosp-b").empty


def test_the_weekly_export_keeps_the_weeks_before_a_readmission(main, world, token):
    db = world["db"]
    send(main, token, bundle("E-1"))
    send_weeks(main, token, week("2026-04-08", weight_kg=85.0, sbp=135),
               week("2026-04-15", weight_kg=87.9, spo2=91))
    send(main, token, bundle("E-2", admit="2026-04-20T08:00", disch="2026-04-24T12:00"))
    worker.run(db, once=True)
    # The dashboard now follows the new discharge; E-1's weeks are gone from it ...
    assert {d["encounter_key"] for d in db["weekly_monitoring"].find({"patient_id": PID})} == {"E-2"}

    # ... but they are the weeks that ended in a readmission, so the export keeps them.
    table = weekly_export.hospital_table(db, "hosp-a", as_of="2026-10-01")
    assert table["week_number"].tolist() == [1, 2]
    assert table["readmitted_within_7d"].tolist() == [0.0, 1.0]       # back on 20 April
    assert table["weight_change_kg"].tolist() == pytest.approx([0.8, 3.7])
    assert {"discharge_score", "age_years", "sex"} <= set(table.columns)
    assert not {"risk_score", "risk_band", "driver_1", "encounter_key", "hospital_id"} & set(table.columns)
    assert weekly_export.hospital_table(db, "hosp-b").empty


def test_a_failure_leaves_values_out_of_the_log(main, world, token, monkeypatch, capsys):
    def broken(*_):
        raise ValueError("could not convert string to float: '3.5 mg/dL'")
    monkeypatch.setattr(scoring_service, "score_discharges", broken)
    send(main, token, bundle())
    worker.run(world["db"], once=True)
    logged = capsys.readouterr()
    assert "ValueError" in logged.out and "3.5" not in logged.out + logged.err
    assert "3.5 mg/dL" in world["db"]["ingest_jobs"].find_one()["error"]

"""
Model Studio: the parts between the LLM and the decision.

Gemini is switched off throughout, so a full run exercises the real sandbox
runner with the Preventra standard pipeline - the same path a run takes when
generated code fails. The data is synthetic (scripts/make_studio_demo_data.py)
with a known risk function, so "did it find the signal" has an answer.
"""

import os
import sys
import time
from pathlib import Path

import mongomock
import mongomock.gridfs
import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

from api import model_studio as ms  # noqa: E402
from api import studio_guard, studio_llm  # noqa: E402
from api.studio_templates import STANDARD_PIPELINE  # noqa: E402
import make_studio_demo_data as demo  # noqa: E402

mongomock.gridfs.enable_gridfs_integration()

ADMIN = {"_id": "u1", "email": "admin@test", "role": "superadmin"}
HOSP_A = {"_id": "u2", "email": "a@test", "role": "hospital_admin", "hospital_id": "H-A"}
HOSP_B = {"_id": "u3", "email": "b@test", "role": "hospital_admin", "hospital_id": "H-B"}


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setattr(ms, "WORK_ROOT", str(tmp_path / "studio"))
    monkeypatch.setattr(studio_llm, "available", lambda: False)
    return mongomock.MongoClient()["studio_test"]


def _csv(tmp_path, frame, name="data.csv") -> str:
    path = str(tmp_path / name)
    frame.to_csv(path, index=False)
    return path


def _wait(job_id, user, statuses, timeout=240):
    end = time.time() + timeout
    while time.time() < end:
        job = ms.public_job(ms.get_job(job_id, user))
        if job["status"] in statuses:
            return job
        time.sleep(0.5)
    raise AssertionError(f"job stuck in {job['status']} at {job['current_step']}")


# ------------------------------------------------------------------ the guard
def test_standard_pipeline_passes_the_guard():
    assert studio_guard.check_code(STANDARD_PIPELINE) == []


@pytest.mark.parametrize("snippet", [
    "import os",
    "import subprocess",
    "from urllib import request",
    "import requests",
    "import joblib",
    "from sklearn.datasets import fetch_openml",
    "x = open('/etc/passwd').read()",
    "import pandas as pd\nx = pd.read_csv('/proc/1/environ')",
    "y = eval('1+1')",
    "z = (1).__class__.__bases__",
    "g = getattr(object, 'x')",
    "import numpy as np\nnp.save('x', [1])",
])
def test_guard_refuses_escape_routes(snippet):
    code = snippet + "\n" + STANDARD_PIPELINE
    assert studio_guard.check_code(code), snippet


def test_guard_requires_the_three_functions():
    problems = studio_guard.check_code("import pandas as pd\ndef preprocess(df, c):\n    return df\n")
    assert any("transform" in p for p in problems) and any("train" in p for p in problems)


def test_sections_split_the_three_stages():
    parts = studio_guard.sections(STANDARD_PIPELINE)
    assert parts["preprocess"].startswith("def preprocess")
    assert parts["train"].startswith("def train")
    assert "import pandas" in parts["helpers"]


# ------------------------------------------------------------------ the plan
def test_profile_never_shows_identifier_or_pii_values():
    frame = pd.DataFrame({"patient_id": [f"P{i}" for i in range(200)],
                          "first_name": ["Ann", "Bob"] * 100,
                          "sex": ["F", "M"] * 100, "age": np.arange(200)})
    profile = ms.profile_dataframe(frame)["columns"]
    assert profile["patient_id"]["kind"] == "identifier-like"
    assert "values" not in profile["patient_id"]
    assert "values" not in profile["first_name"]  # name-like column, values withheld
    assert profile["sex"]["values"] == {"F": 100, "M": 100}


def test_heuristic_plan_on_demo_discharges():
    frame = demo.discharges(600, np.random.default_rng(1))
    plan = ms.heuristic_plan(ms.profile_dataframe(frame), "discharge")
    plan, problems = ms.normalise_plan(plan, list(frame.columns), "discharge")
    assert problems == []
    assert plan["label_column"] == "readmit_30d"
    assert plan["id_column"] == "patient_id"
    assert plan["time_column"] == "discharge_date"
    assert "readmission_date" in {d["column"] for d in plan["drop_columns"]}


def test_weekly_plan_without_patient_id_is_refused():
    plan = {"label_column": "y", "numeric_columns": ["x"]}
    _, problems = ms.normalise_plan(plan, ["y", "x"], "weekly")
    assert any("patient identifier" in p for p in problems)


def test_unassigned_columns_are_dropped_visibly():
    plan, _ = ms.normalise_plan({"label_column": "y", "numeric_columns": ["a"]},
                                ["y", "a", "forgotten"], "discharge")
    assert {"column": "forgotten", "reason": "not assigned a role"} in plan["drop_columns"]


@pytest.mark.parametrize("values,positive,expected", [
    ([0, 1, 1, None], None, [0, 1, 1, None]),
    (["Yes", "no", "YES"], None, [1, 0, 1]),
    (["NO", "<30", ">30"], "<30", [0, 1, 0]),
])
def test_label_mapping(values, positive, expected):
    out = ms.label_series(pd.Series(values), positive).tolist()
    assert [None if pd.isna(v) else int(v) for v in out] == expected


def test_ambiguous_label_is_refused():
    with pytest.raises(ValueError, match="positive value"):
        ms.label_series(pd.Series(["red", "blue"]), None)


def test_association_scan_flags_the_leaky_column():
    frame = demo.discharges(3000, np.random.default_rng(2))
    y = frame["readmit_30d"].astype(float)
    scan = ms.association_scan(frame, y, ["readmission_date", "age", "sodium_last"])
    flagged = {r["column"]: r["flag"] for r in scan}
    assert flagged["readmission_date"] == "very likely leakage"
    assert flagged["age"] is None


# ------------------------------------------------------------------ the split
def test_split_keeps_patients_together_and_tests_the_newest():
    frame = demo.weekly(400, np.random.default_rng(3))
    split = ms.make_split(frame, "patient_id", "week_date")
    sides = pd.DataFrame({"pid": frame.patient_id, "side": split}).groupby("pid").side.nunique()
    assert sides.max() == 1
    dates = pd.to_datetime(frame.week_date)
    assert dates[split == "test"].min() >= dates[split == "train"].min()
    shares = pd.Series(split).value_counts(normalize=True)
    assert 0.6 < shares["train"] < 0.8 and 0.12 < shares["test"] < 0.28


# ------------------------------------------------------------------ the rules
def _evaluation(auroc=0.75, ci=(0.72, 0.78), positives=300, ratio=1.0, slope=1.0,
                slope_ci=None, brier=0.15, comparisons=(), subgroups=()):
    return {"metrics": {"n_positive": positives, "auroc": auroc, "auroc_ci": list(ci),
                        "calibration_ratio": ratio, "calibration_slope": slope,
                        "calibration_slope_ci": list(slope_ci or (slope, slope)), "brier": brier},
            "comparisons": [{"kind": "baseline", "brier": 0.17}, *comparisons],
            "subgroups": list(subgroups)}


def test_a_good_model_is_recommended():
    assert ms.decide(_evaluation(), [], "gemini")["decision"] == "deploy"


def test_implausible_auroc_is_treated_as_leakage():
    verdict = ms.decide(_evaluation(auroc=0.99, ci=(0.98, 0.995)), [], "gemini")
    assert verdict["decision"] == "do_not_deploy"
    assert any("leaked" in c["detail"] for c in verdict["checks"])


def test_very_high_auroc_on_a_score_outcome_is_a_warning_not_a_leak():
    easy = ms.decide(_evaluation(auroc=0.989, ci=(0.986, 0.992)), [], "gemini", score_outcome=True)
    perfect = ms.decide(_evaluation(auroc=1.0, ci=(1.0, 1.0)), [], "gemini", score_outcome=True)
    assert easy["decision"] == "caution"
    assert perfect["decision"] == "do_not_deploy"


def test_leakage_warning_only_names_columns_that_were_kept():
    scan = [{"column": "dropped_leak", "auroc": 1.0, "flag": "very likely leakage"},
            {"column": "kept_leak", "auroc": 0.99, "flag": "very likely leakage"}]
    verdict = ms.decide(_evaluation(), scan, "gemini", features=["kept_leak", "age"])
    detail = next(c for c in verdict["checks"] if c["name"] == "Leakage")["detail"]
    assert "kept_leak" in detail and "dropped_leak" not in detail


def test_worse_than_the_active_model_is_not_deployed():
    worse = {"kind": "active", "name": "Current", "delta_auroc":
             {"estimate": -0.04, "ci_low": -0.06, "ci_high": -0.02}}
    assert ms.decide(_evaluation(comparisons=[worse]), [], "gemini")["decision"] == "do_not_deploy"


def test_no_clear_gain_and_miscalibration_mean_caution():
    level = {"kind": "active", "name": "Current", "delta_auroc":
             {"estimate": 0.005, "ci_low": -0.01, "ci_high": 0.02}}
    verdict = ms.decide(_evaluation(comparisons=[level], ratio=1.6), [], "gemini")
    assert verdict["decision"] == "caution"
    assert {c["status"] for c in verdict["checks"]} >= {"warn"}


def test_a_noisy_slope_is_not_a_calibration_warning():
    # 1.4 looks miscalibrated, but the interval includes 1: not enough evidence.
    noisy = ms.decide(_evaluation(slope=1.4, slope_ci=(0.9, 1.9)), [], "gemini")
    firm = ms.decide(_evaluation(slope=1.4, slope_ci=(1.15, 1.7)), [], "gemini")
    assert next(c for c in noisy["checks"] if c["name"] == "Calibration")["status"] == "pass"
    assert next(c for c in firm["checks"] if c["name"] == "Calibration")["status"] == "warn"


def test_patient_bootstrap_widens_intervals_for_repeated_rows():
    rng = np.random.default_rng(1)
    n_patients = 600
    risk = rng.normal(0, 1, n_patients)
    y_pat = (rng.random(n_patients) < 1 / (1 + np.exp(-(risk - 1.4)))).astype(int)
    rows = np.repeat(np.arange(n_patients), 4)          # four weeks per patient
    p = 1 / (1 + np.exp(-(np.repeat(risk, 4) + rng.normal(0, 0.8, len(rows)) - 1.4)))
    y = y_pat[rows]
    naive = ms.compute_metrics(y, p, None, None, 0.2)
    clustered = ms.compute_metrics(y, p, None, None, 0.2, groups=rows)
    width = lambda m: m["auroc_ci"][1] - m["auroc_ci"][0]
    assert width(clustered) > 1.4 * width(naive)
    assert clustered["n_units"] == n_patients and clustered["unit"] == "patients"
    assert clustered["n_positive_units"] == int(y_pat.sum())


def test_too_few_events_is_not_deployed():
    assert ms.decide(_evaluation(positives=12), [], "gemini")["decision"] == "do_not_deploy"


def test_subgroup_gap_is_a_warning():
    gap = [{"column": "sex", "level": "F", "auroc": 0.62}]
    assert ms.decide(_evaluation(subgroups=gap), [], "gemini")["decision"] == "caution"


def test_calibration_line_recovers_a_perfect_model():
    rng = np.random.default_rng(0)
    p = rng.uniform(0.02, 0.6, 20000)
    y = (rng.random(20000) < p).astype(int)
    slope, intercept = ms._calibration_line(y, p)
    assert abs(slope - 1) < 0.08 and abs(intercept) < 0.15


# ------------------------------------------------------------------ end to end
def test_discharge_run_save_deploy_score(db, tmp_path):
    frame = demo.discharges(3000, np.random.default_rng(4))
    job = ms.create_job(db, ADMIN, _csv(tmp_path, frame), "discharge_demo.csv",
                        target="discharge", mode="new", base_model_id=None,
                        algorithm="auto", name="Demo discharge")
    job = _wait(job["job_id"], ADMIN, {"awaiting_confirmation", "failed"})
    assert job["status"] == "awaiting_confirmation", job["error"]
    assert job["plan"]["label_column"] == "readmit_30d"

    ms.confirm_job(db, job["job_id"], ADMIN, {})
    job = _wait(job["job_id"], ADMIN, {"completed", "failed"})
    assert job["status"] == "completed", job["error"]
    assert job["code_origin"] == "standard"
    m = job["evaluation"]["metrics"]
    assert m["auroc"] > 0.68, m  # the planted signal is found
    assert job["evaluation"]["split"]["test"]["rows"] > 400
    assert job["verdict"]["decision"] in ("deploy", "caution", "do_not_deploy")
    assert job["explanation"]["bullets"]
    # The built-in model needs MIMIC columns this file does not have.
    builtin = next(c for c in job["evaluation"]["comparisons"] if c["kind"] == "active")
    assert "Not comparable" in builtin["note"]

    saved = ms.save_job_model(db, job["job_id"], ADMIN)
    assert saved["model_id"].startswith("MDL-")
    # Bands: Medium from the training readmission rate, High from double it.
    train = job["evaluation"]["split"]["train"]
    assert saved["bands"]["low_score_threshold"] == pytest.approx(
        100 * train["positives"] / train["rows"], abs=0.06)
    assert saved["bands"]["high_score_threshold"] == pytest.approx(
        2 * saved["bands"]["low_score_threshold"], abs=0.11)
    assert ms.get_model(db, saved["model_id"], ADMIN)["metrics"]["auroc"] == m["auroc"]

    ms.deploy_model(db, saved["model_id"], ADMIN)
    assert ms.active_model_id(db, ADMIN, "discharge") == saved["model_id"]
    with pytest.raises(ValueError, match="deployed"):
        ms.delete_model(db, saved["model_id"], ADMIN)

    new_patients = demo.discharges(200, np.random.default_rng(5)).drop(columns=["readmit_30d"])
    scored = ms.score_file(db, ADMIN, "discharge", _csv(tmp_path, new_patients, "new.csv"))
    assert len(scored["rows"]) == 200
    assert scored["rows"][0]["id"].startswith("P")
    assert set(scored["summary"]) == {"High", "Medium", "Low"}
    assert 0 <= scored["rows"][0]["score"] <= 100

    ms.deploy_model(db, ms.BUILTIN_MIMIC, ADMIN)
    ms.delete_model(db, saved["model_id"], ADMIN)
    with pytest.raises(LookupError):
        ms.get_model(db, saved["model_id"], ADMIN)


def test_weekly_retrain_compares_with_its_base(db, tmp_path):
    rng = np.random.default_rng(6)
    first = demo.weekly(900, rng)
    job = ms.create_job(db, ADMIN, _csv(tmp_path, first, "w1.csv"), "w1.csv",
                        target="weekly", mode="new", base_model_id=None,
                        algorithm="logistic_regression", name="Weekly v1")
    job = _wait(job["job_id"], ADMIN, {"awaiting_confirmation", "failed"})
    plan = job["plan"]
    assert plan["id_column"] == "patient_id"
    # The person drops the leaky flag before training, as the scan suggests.
    edits = {"categorical_columns": [c for c in plan["categorical_columns"]
                                     if c != "readmit_visit_flag"],
             "drop_columns": plan["drop_columns"] + [{"column": "readmit_visit_flag",
                                                      "reason": "leakage"}]}
    ms.confirm_job(db, job["job_id"], ADMIN, edits)
    job = _wait(job["job_id"], ADMIN, {"completed", "failed"})
    assert job["status"] == "completed", job["error"]
    assert job["train_report"]["report"]["algorithm"] == "logistic_regression"
    v1 = ms.save_job_model(db, job["job_id"], ADMIN)

    second = demo.weekly(900, rng)
    job2 = ms.create_job(db, ADMIN, _csv(tmp_path, second, "w2.csv"), "w2.csv",
                         target="weekly", mode="retrain", base_model_id=v1["model_id"],
                         algorithm="auto", name="Weekly v2")
    job2 = _wait(job2["job_id"], ADMIN, {"awaiting_confirmation", "failed"})
    ms.confirm_job(db, job2["job_id"], ADMIN, edits)
    job2 = _wait(job2["job_id"], ADMIN, {"completed", "failed"})
    assert job2["status"] == "completed", job2["error"]
    base_row = next(c for c in job2["evaluation"]["comparisons"] if c["kind"] == "base")
    assert "delta_auroc" in base_row, base_row
    # The rules-based weekly scorer cannot be compared or score files.
    assert not any(c.get("model_id") == ms.BUILTIN_RULES
                   for c in job2["evaluation"]["comparisons"])
    with pytest.raises(ValueError, match="rules-based"):
        ms.score_file(db, ADMIN, "weekly", _csv(tmp_path, second.head(5), "s.csv"))


def test_hospitals_see_only_their_own_models_and_runs(db, tmp_path):
    frame = demo.discharges(1500, np.random.default_rng(8))
    job = ms.create_job(db, HOSP_A, _csv(tmp_path, frame), "a.csv", target="discharge",
                        mode="new", base_model_id=None, algorithm="logistic_regression",
                        name="A's model")
    with pytest.raises(LookupError):
        ms.get_job(job["job_id"], HOSP_B)
    job = _wait(job["job_id"], HOSP_A, {"awaiting_confirmation", "failed"})
    ms.confirm_job(db, job["job_id"], HOSP_A, {})
    job = _wait(job["job_id"], HOSP_A, {"completed", "failed"})
    saved = ms.save_job_model(db, job["job_id"], HOSP_A)
    ms.deploy_model(db, saved["model_id"], HOSP_A)

    assert saved["model_id"] in {m["model_id"] for m in ms.list_models(db, HOSP_A)}
    assert saved["model_id"] not in {m["model_id"] for m in ms.list_models(db, HOSP_B)}
    with pytest.raises(LookupError):
        ms.get_model(db, saved["model_id"], HOSP_B)
    # Deploying for hospital A leaves hospital B on the built-in model.
    assert ms.active_model_id(db, HOSP_A, "discharge") == saved["model_id"]
    assert ms.active_model_id(db, HOSP_B, "discharge") == ms.BUILTIN_MIMIC


def test_generated_code_that_fails_falls_back_to_the_standard_pipeline(db, tmp_path,
                                                                        monkeypatch):
    """Gemini 'on', but every pipeline it writes is broken: three attempts, then
    the standard pipeline, and the attempts are recorded."""
    monkeypatch.setattr(studio_llm, "available", lambda: True)
    monkeypatch.setattr(studio_llm, "analyze_dataset", lambda *a, **k: (_ for _ in ()).throw(
        RuntimeError("no network in tests")))
    broken = STANDARD_PIPELINE.replace("return model, report", "return model")
    calls = []
    monkeypatch.setattr(studio_llm, "generate_pipeline",
                        lambda *a, **k: calls.append(k.get("error")) or broken)
    monkeypatch.setattr(studio_llm, "explain_verdict", lambda s, **k: ["explained"])

    frame = demo.discharges(1500, np.random.default_rng(9))
    job = ms.create_job(db, ADMIN, _csv(tmp_path, frame), "d.csv", target="discharge",
                        mode="new", base_model_id=None, algorithm="logistic_regression",
                        name="Fallback")
    job = _wait(job["job_id"], ADMIN, {"awaiting_confirmation", "failed"})
    assert job["plan_source"] == "heuristic"  # analysis failed over to the rules
    ms.confirm_job(db, job["job_id"], ADMIN, {})
    job = _wait(job["job_id"], ADMIN, {"completed", "failed"})
    assert job["status"] == "completed", job["error"]
    assert job["code_origin"] == "standard"
    assert len(job["code_attempts"]) == ms.CODE_ATTEMPTS
    assert calls[0] is None and "must return (estimator, report_dict)" in calls[1]
    assert job["explanation"]["bullets"] == ["explained"]


def test_sandbox_does_not_see_the_api_environment(db, tmp_path, monkeypatch):
    """A pipeline that tries to find a secret in its environment finds nothing,
    because the runner starts with a scrubbed environment."""
    monkeypatch.setenv("SHARED_SECRET_KEY", "must-not-leak")
    probe = STANDARD_PIPELINE.replace(
        'def preprocess(df, config):',
        'def preprocess(df, config):\n    import os as _o\n'
        '    assert "SHARED_SECRET_KEY" not in _o.environ, "leaked"\n'
        '    assert "MONGO_URI" not in _o.environ, "leaked"', 1)
    wd = tmp_path / "probe"
    wd.mkdir()
    frame = demo.discharges(400, np.random.default_rng(10))
    frame["__label__"] = frame.pop("readmit_30d")
    frame.to_csv(wd / "train.csv", index=False)
    (wd / "pipeline_code.py").write_text(probe)
    cfg = {"label_column": "readmit_30d", "feature_columns": ["age", "charlson_index"],
           "numeric_columns": ["age", "charlson_index"], "categorical_columns": [],
           "algorithm_preference": "logistic_regression"}
    result = ms.run_sandbox("train", str(wd), {"config": cfg, "train_file": "train.csv",
                                               "target_field": "__label__",
                                               "group_field": None}, 120, lambda m: None)
    assert result["ok"], result["error"]


# ------------------------------------------------------------------ patient history
def _weekly_scored(n_patients: int, seed: int) -> pd.DataFrame:
    """Weekly rows with a rules-like score and NEXT week's score, as the weekly
    export produces: the outcome is 'next week's score >= 40'."""
    frame = demo.weekly(n_patients, np.random.default_rng(seed))
    logit = (0.6 * frame["weight_change_kg"].clip(lower=0) - 0.05 * (frame["adherence_pct"] - 85)
             + 0.8 * (frame["followup_status"] == "missed") - 1.3)
    frame["risk_score"] = (100 / (1 + np.exp(-logit))).round(1)
    frame["next_week_risk_score"] = frame.groupby("patient_id")["risk_score"].shift(-1)
    return frame


def _history_run_dir(tmp_path, code: str, frame: pd.DataFrame, history: bool):
    wd = tmp_path / "hist"
    wd.mkdir()
    data = frame.copy()
    data["__label__"] = (data["risk_score"] >= 40).astype(int)
    data["__group__"] = data["patient_id"]
    data["__order__"] = data["week_number"].astype(float)
    data.to_csv(wd / "train.csv", index=False)
    (wd / "pipeline_code.py").write_text(code)
    cfg = {"label_column": "risk_score", "feature_columns": ["weight_change_kg", "adherence_pct"],
           "numeric_columns": ["weight_change_kg", "adherence_pct"], "categorical_columns": [],
           "algorithm_preference": "logistic_regression", "history": history,
           "history_group_column": "__group__", "history_order_column": "__order__"}
    return str(wd), {"config": cfg, "train_file": "train.csv", "target_field": "__label__",
                     "group_field": "__group__"}


@pytest.mark.parametrize("cheat,history", [
    # uses every week of the patient, including later ones (invisible to the static check)
    ('out["w_mean"] = df.groupby(config["history_group_column"])["weight_change_kg"]'
     '.transform("mean")', True),
    # a statistic of the whole dataset
    ('out["w_centred"] = df["weight_change_kg"] - df["weight_change_kg"].mean()', False),
])
def test_lookahead_check_fails_features_that_peek(tmp_path, cheat, history):
    code = STANDARD_PIPELINE.replace("    return out\n\n\ndef _columns",
                                     f"    {cheat}\n    return out\n\n\ndef _columns", 1)
    assert studio_guard.check_code(code) == []  # passes the static check...
    wd, spec = _history_run_dir(tmp_path, code, _weekly_scored(200, 1), history)
    result = ms.run_sandbox("train", wd, spec, 120, lambda m: None)
    assert not result["ok"]                      # ...but not the runtime one
    assert "Look-ahead check failed" in result["error"]
    assert ("w_mean" if history else "w_centred") in result["error"]


def test_earlier_row_features_pass_the_lookahead_check(tmp_path):
    logs = []
    wd, spec = _history_run_dir(tmp_path, STANDARD_PIPELINE, _weekly_scored(200, 2), True)
    result = ms.run_sandbox("train", wd, spec, 120, logs.append)
    assert result["ok"], result["error"]
    assert any("Look-ahead check passed" in m for m in logs)
    import json
    names = json.loads((tmp_path / "hist" / "train_result.json").read_text())["feature_names"]
    assert "weight_change_kg_change" in names and "rows_before" in names
    assert "__group__" not in names and "__order__" not in names


def test_boolean_numeric_columns_become_floats_with_history(tmp_path):
    """True/False in a numeric column used to stay boolean, and 'change since
    last week' then mixed booleans with numbers and broke the encoders."""
    frame = _weekly_scored(200, 5)
    frame["observed"] = np.random.default_rng(5).random(len(frame)) < 0.8   # booleans
    wd, spec = _history_run_dir(tmp_path, STANDARD_PIPELINE, frame, True)
    spec["config"]["numeric_columns"] = spec["config"]["feature_columns"] = [
        "weight_change_kg", "adherence_pct", "observed"]
    result = ms.run_sandbox("train", wd, spec, 120, lambda m: None)
    assert result["ok"], result["error"]


def test_threshold_outcome():
    out = ms.label_series(pd.Series([12.0, 40.0, 63.5, None, "n/a"], name="s"), None, 40)
    assert [None if pd.isna(v) else int(v) for v in out] == [0, 1, 1, None, None]
    with pytest.raises(ValueError, match="no numeric values"):
        ms.label_series(pd.Series(["a", "b"], name="s"), None, 40)


def test_order_values_sorts_dates_and_keeps_numbers():
    dates = ms.order_values(pd.Series(["2025-01-08", "2025-01-01", "bad"]))
    assert dates[1] < dates[0] and np.isnan(dates[2])
    assert list(ms.order_values(pd.Series([3, 1, 2]))) == [3.0, 1.0, 2.0]


def test_weekly_history_model_scores_each_patients_latest_week(db, tmp_path):
    frame = _weekly_scored(900, 3)
    job = ms.create_job(db, ADMIN, _csv(tmp_path, frame, "weekly.csv"), "weekly.csv",
                        target="weekly", mode="new", base_model_id=None,
                        algorithm="logistic_regression", name="Next-week High")
    job = _wait(job["job_id"], ADMIN, {"awaiting_confirmation", "failed"})
    plan = job["plan"]
    assert plan["id_column"] == "patient_id" and plan["time_column"] == "week_date"
    leaks = {"readmitted_30d", "readmit_visit_flag", "next_week_risk_score"}
    edits = {"label_column": "next_week_risk_score", "label_threshold": 40,
             "numeric_columns": [c for c in plan["numeric_columns"] if c not in leaks],
             "categorical_columns": [c for c in plan["categorical_columns"] if c not in leaks],
             "drop_columns": [{"column": c, "reason": "leakage"}
                              for c in ("readmitted_30d", "readmit_visit_flag")]}
    ms.confirm_job(db, job["job_id"], ADMIN, edits)
    job = _wait(job["job_id"], ADMIN, {"completed", "failed"})
    assert job["status"] == "completed", job["error"]
    assert job["config"]["history"] is True
    assert any(m["message"].startswith("Look-ahead check passed") for m in job["logs"])
    assert "risk_score_change" in job["train_report"]["feature_names"]

    saved = ms.save_job_model(db, job["job_id"], ADMIN)
    assert saved["data"]["label_threshold"] == 40 and saved["data"]["history"] is True
    ms.deploy_model(db, saved["model_id"], ADMIN)

    new = _weekly_scored(50, 4).drop(columns=["next_week_risk_score"])
    scored = ms.score_file(db, ADMIN, "weekly", _csv(tmp_path, new, "new.csv"))
    assert scored["history"] is True and scored["rows_in_file"] == len(new)
    assert len(scored["rows"]) == new["patient_id"].nunique()      # one row per patient
    latest = new.sort_values("week_number").groupby("patient_id").tail(1)
    by_id = {r["id"]: r for r in scored["rows"]}
    sample = latest.iloc[0]
    assert by_id[sample["patient_id"]]["as_of"] == str(sample["week_date"])
    assert by_id[sample["patient_id"]]["history_rows"] == 3          # weeks 1-3 before week 4

    with pytest.raises(ValueError, match="patient id column"):
        ms.score_file(db, ADMIN, "weekly",
                      _csv(tmp_path, new.drop(columns=["patient_id"]), "noid.csv"))


def test_week_numbers_order_rows_but_do_not_pick_the_test_patients():
    """Every patient starts at week 0, so a week number cannot say who is newest;
    the split must fall back to random patients rather than id order."""
    frame = demo.weekly(500, np.random.default_rng(11))
    split = ms.make_split(frame, "patient_id", "week_number")
    test_ids = sorted(frame.loc[split == "test", "patient_id"].unique())
    all_ids = sorted(frame["patient_id"].unique())
    assert test_ids != all_ids[-len(test_ids):]          # not simply the last ids
    sides = pd.DataFrame({"pid": frame.patient_id, "side": split}).groupby("pid").side.nunique()
    assert sides.max() == 1

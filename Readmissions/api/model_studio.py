"""
Model Studio: train, evaluate, save and deploy readmission models from a
hospital's own CSV.

THE FLOW (one job per upload)
-----------------------------
    profile    column statistics, computed here                    (no LLM)
    analyze    Gemini proposes label / id / time / feature roles    (sees the profile only)
    confirm    a person reviews and edits that plan                 (waits for the user)
    generate   Gemini writes preprocess / transform / train         (checked by studio_guard)
    train      the code runs in the sandbox runner                  (separate process)
    evaluate   held-out test metrics, comparisons, subgroups        (our code, not the model's)
    recommend  fixed rules decide deploy / caution / do not deploy  (Gemini only explains)

WHAT IS TRUSTED
---------------
Generated code, and every model pickled by it, only ever runs inside
api/studio_runner.py in a child process with the API's secrets removed from
its environment and with time and memory limits. This process reads back
numbers - scores and a JSON report - and never unpickles a generated model.

The train/validation/test split is made here, before the generated code sees
any data, and the test labels never leave this process. A generated pipeline
cannot grade its own work.

WHAT "DEPLOY" MEANS
-------------------
Each hospital has one active model per target (risk at discharge, weekly
monitoring). Until something else is deployed that is the built-in model: the
Phase 1 MIMIC model for discharge, the monitoring rules for weekly. The active
model scores new patient files through score_file(). The worklist's stored
scores are not rewritten: they were produced from inputs the worklist does not
keep, so re-scoring them would need the ingestion pipeline to carry raw
features first.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from datetime import datetime, timedelta
from typing import Optional

import numpy as np
import pandas as pd

from api import studio_guard, studio_llm
from api.studio_templates import ALGORITHMS, BUILTIN_MIMIC_ADAPTER, STANDARD_PIPELINE

# ------------------------------------------------------------------ settings
WORK_ROOT = os.environ.get("STUDIO_WORKDIR",
                           os.path.join(tempfile.gettempdir(), "preventra_studio"))
MAX_UPLOAD_MB = int(os.environ.get("STUDIO_MAX_UPLOAD_MB", "200"))
MAX_ROWS = int(os.environ.get("STUDIO_MAX_ROWS", "300000"))
TRAIN_TIMEOUT_S = int(os.environ.get("STUDIO_TRAIN_TIMEOUT_S", "1500"))
PREDICT_TIMEOUT_S = int(os.environ.get("STUDIO_PREDICT_TIMEOUT_S", "600"))
# Address-space cap for the sandbox. 0 disables it (some platforms count
# virtual memory reservations against it aggressively).
MEMORY_LIMIT_MB = int(os.environ.get("STUDIO_MEMORY_LIMIT_MB", "6144"))
CODE_ATTEMPTS = 3            # the first try plus two corrections
JOB_TTL = timedelta(hours=24)

MODELS = "studio_models"
DEPLOYMENTS = "studio_deployments"
JOBS = "studio_jobs"
BUCKET = "studio_artifacts"

TARGETS = ("discharge", "weekly")
STEPS = ("profile", "analyze", "confirm", "generate", "train", "evaluate", "recommend")
RUNNER = os.path.join(os.path.dirname(os.path.abspath(__file__)), "studio_runner.py")
_BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_RESULTS = os.path.join(_BASE, "data", "mimic", "model", "results")

LABEL_FIELD, GROUP_FIELD = "__label__", "__group__"

# ------------------------------------------------------------------ built-ins
BUILTIN_MIMIC = "builtin-mimic-phase1"
BUILTIN_RULES = "builtin-weekly-rules"


def _mimic_card() -> dict:
    try:
        with open(os.path.join(_RESULTS, "model_card_phase1.json")) as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {}


def _mimic_spec() -> dict:
    with open(os.path.join(_RESULTS, "mimic_feature_spec.json")) as fh:
        return json.load(fh)


def _builtins() -> dict:
    card = _mimic_card()
    m = card.get("metrics", {})
    return {
        BUILTIN_MIMIC: {
            "model_id": BUILTIN_MIMIC, "builtin": True, "target": "discharge",
            "name": "Preventra Phase 1 (MIMIC-IV)",
            "algorithm": card.get("architecture", "HistGradientBoosting + isotonic calibration"),
            "description": "The production discharge model: trained on MIMIC-IV, 94 features "
                           "(demographics, comorbidities, labs, medications, utilisation).",
            "created_at": card.get("trained_at"),
            "metrics": {"auroc": m.get("auc_roc"), "auprc": m.get("auc_pr"),
                        "brier": m.get("brier"), "prevalence": card.get("prevalence"),
                        "n_test": None},
            "data": {"rows": (card.get("cohort") or {}).get("index_stays"),
                     "label_column": "readmit_30d", "id_column": "subject_id"},
            "can_score": True, "can_retrain": True,
        },
        BUILTIN_RULES: {
            "model_id": BUILTIN_RULES, "builtin": True, "target": "weekly",
            "name": "Preventra monitoring rules",
            "algorithm": "Rules: discharge score adjusted by condition-specific weekly signals",
            "description": "The production weekly scorer. Clinical rules rather than a trained "
                           "model, so it is not retrained here; deploy a trained weekly model "
                           "to replace it for scoring files.",
            "metrics": {}, "data": {}, "can_score": False, "can_retrain": False,
        },
    }


# ======================================================================== jobs
_jobs: dict = {}
_jobs_lock = threading.Lock()
_sandbox_slot = threading.Semaphore(int(os.environ.get("STUDIO_CONCURRENT_RUNS", "1")))


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _hospital_key(hospital_id) -> str:
    return hospital_id or "global"


def _visible(user: dict, hospital_id) -> bool:
    from api import access
    if user["role"] == "superadmin" and not user.get("acting_hospital_id"):
        return True
    return hospital_id == access.hospital_of(user)


def _log(job: dict, message: str, step: Optional[str] = None) -> None:
    with _jobs_lock:
        job["logs"].append({"t": _now(), "step": step or job.get("current_step"),
                            "message": str(message)[:2000]})
        job["logs"] = job["logs"][-500:]


def _step(job: dict, name: str, status: str, detail: Optional[str] = None) -> None:
    with _jobs_lock:
        s = job["steps"][name]
        s["status"] = status
        if status == "running":
            s["started_at"] = _now()
            job["current_step"] = name
        elif status in ("done", "failed", "skipped", "waiting"):
            s["finished_at"] = _now()
        if detail is not None:
            s["detail"] = detail


def _fail(job: dict, step: str, message: str) -> None:
    _step(job, step, "failed", message)
    _log(job, f"Stopped: {message}", step)
    with _jobs_lock:
        job["status"] = "failed"
        job["error"] = message


def _expire_old_jobs() -> None:
    cutoff = datetime.now() - JOB_TTL
    with _jobs_lock:
        old = [jid for jid, j in _jobs.items()
               if datetime.strptime(j["created_at"], "%Y-%m-%d %H:%M:%S") < cutoff]
        for jid in old:
            shutil.rmtree(_jobs[jid]["workdir"], ignore_errors=True)
            del _jobs[jid]


PUBLIC_JOB_KEYS = ("job_id", "name", "target", "mode", "base_model_id", "algorithm_preference",
                   "filename", "status", "error", "current_step", "steps", "logs", "created_at",
                   "created_by", "profile", "plan", "plan_source", "config", "association",
                   "code", "code_sections", "code_origin", "code_attempts", "train_report",
                   "evaluation", "verdict", "explanation", "saved_model_id", "warnings",
                   "plan_problems")


def public_job(job: dict) -> dict:
    with _jobs_lock:
        return json.loads(json.dumps({k: job.get(k) for k in PUBLIC_JOB_KEYS}, default=str))


def get_job(job_id: str, user: dict) -> dict:
    job = _jobs.get(job_id)
    if not job or not _visible(user, job["hospital_id"]):
        raise LookupError("No such training run.")
    return job


def list_jobs(user: dict) -> list:
    with _jobs_lock:
        mine = [j for j in _jobs.values() if _visible(user, j["hospital_id"])]
    mine.sort(key=lambda j: j["created_at"], reverse=True)
    return [{k: j.get(k) for k in ("job_id", "name", "target", "status", "current_step",
                                   "created_at", "filename", "saved_model_id")}
            for j in mine[:20]]


def create_job(db, user: dict, upload_path: str, filename: str, *, target: str, mode: str,
               base_model_id: Optional[str], algorithm: str, name: str) -> dict:
    from api import access

    if target not in TARGETS:
        raise ValueError("target must be 'discharge' or 'weekly'.")
    if algorithm not in ALGORITHMS:
        raise ValueError(f"algorithm must be one of {', '.join(ALGORITHMS)}.")
    if mode not in ("new", "retrain"):
        raise ValueError("mode must be 'new' or 'retrain'.")
    base = None
    if mode == "retrain":
        if not base_model_id:
            raise ValueError("Choose the model to retrain.")
        base = get_model(db, base_model_id, user)
        if base["target"] != target:
            raise ValueError("The model to retrain was trained for the other target.")
        if not base.get("can_retrain", True):
            raise ValueError(f"{base['name']} cannot be retrained here.")

    _expire_old_jobs()
    job_id = "JOB-" + uuid.uuid4().hex[:10].upper()
    workdir = os.path.join(WORK_ROOT, job_id)
    os.makedirs(workdir, exist_ok=True)
    shutil.move(upload_path, os.path.join(workdir, "upload.csv"))

    job = {
        "job_id": job_id, "workdir": workdir, "hospital_id": access.hospital_of(user),
        "created_by": user.get("email"), "created_at": _now(),
        "name": (name or "").strip() or f"{target.title()} model {datetime.now():%Y-%m-%d %H:%M}",
        "target": target, "mode": mode, "base_model_id": base_model_id if base else None,
        "algorithm_preference": algorithm, "filename": filename,
        "status": "running", "error": None, "current_step": "profile",
        "steps": {s: {"status": "pending", "detail": None} for s in STEPS},
        "logs": [], "warnings": [],
    }
    with _jobs_lock:
        _jobs[job_id] = job
    threading.Thread(target=_phase_one, args=(db, job, base), daemon=True).start()
    return public_job(job)


# ============================================================ 1. profiling
# Names that END in an identifier token: patient_id, subjectid, mrn, hadm_id.
# Anchored at the end so patient_age or visit_count are not mistaken for ids.
_ID_HINT = re.compile(r"(^|_)((patient|subject|member|person|hadm|encounter|visit|record|"
                      r"account|admission|stay)_?id|id|mrn|ssn|nhs_?number)$", re.I)
_PII_HINT = re.compile(r"name|address|phone|email|street|zip|postcode|birth|dob|ssn|mrn", re.I)


def _read_csv(path: str) -> pd.DataFrame:
    size_mb = os.path.getsize(path) / 1e6
    if size_mb > MAX_UPLOAD_MB:
        raise ValueError(f"The file is {size_mb:.0f} MB; the limit is {MAX_UPLOAD_MB} MB.")
    try:
        df = pd.read_csv(path, low_memory=False)
    except UnicodeDecodeError:
        df = pd.read_csv(path, low_memory=False, encoding="latin-1")
    df.columns = [str(c).strip() for c in df.columns]
    if df.columns.duplicated().any():
        raise ValueError("The file has duplicate column names.")
    return df


def _is_date(s: pd.Series) -> bool:
    if pd.api.types.is_datetime64_any_dtype(s):
        return True
    if pd.api.types.is_numeric_dtype(s):
        return False
    sample = s.dropna().astype(str).str.strip()
    sample = sample[sample != ""].head(300)
    if sample.empty or sample.str.len().median() < 6:
        return False
    parsed = pd.to_datetime(sample, errors="coerce", format="mixed")
    return parsed.notna().mean() >= 0.9


def profile_dataframe(df: pd.DataFrame) -> dict:
    """
    What Gemini is allowed to see. Column statistics only: the values of a
    column are shown only when it has few distinct values, short ones, and a
    name that does not suggest personal details - never identifiers, free text
    or anything resembling a name, address or date of birth.
    """
    n = len(df)
    columns = {}
    for col in df.columns:
        s = df[col]
        non_null = s.dropna()
        n_unique = int(non_null.nunique())
        entry = {"missing_pct": round(float(s.isna().mean() * 100), 1),
                 "n_unique": n_unique}
        if pd.api.types.is_bool_dtype(s) or (pd.api.types.is_numeric_dtype(s) and n_unique <= 2):
            entry["kind"] = "binary"
        elif pd.api.types.is_numeric_dtype(s):
            entry["kind"] = "numeric"
            if not non_null.empty:
                entry["stats"] = {k: round(float(v), 4) for k, v in {
                    "min": non_null.min(), "p25": non_null.quantile(.25),
                    "median": non_null.median(), "p75": non_null.quantile(.75),
                    "max": non_null.max(), "mean": non_null.mean()}.items()}
        elif _is_date(s):
            entry["kind"] = "date"
        else:
            lengths = non_null.astype(str).str.len()
            mean_len = float(lengths.mean()) if not lengths.empty else 0.0
            entry["kind"] = ("text" if mean_len > 40 else
                             "identifier-like" if n and n_unique / max(len(non_null), 1) > 0.9
                             else "categorical")
            entry["mean_length"] = round(mean_len, 1)
        # An id-named column is an identifier even when it repeats - weekly data
        # has one row per patient per week, so patient_id is only 25% unique.
        if _ID_HINT.search(col) and n_unique >= min(20, max(len(non_null), 1)):
            entry["kind"] = "identifier-like"
        if (n_unique <= 20 and entry["kind"] in ("binary", "categorical")
                and not _PII_HINT.search(col)):
            top = non_null.astype(str).value_counts().head(20)
            if top.index.str.len().max() <= 40:
                entry["values"] = {str(k): int(v) for k, v in top.items()}
        columns[col] = entry
    return {"n_rows": int(n), "n_columns": int(df.shape[1]), "columns": columns}


# ===================================================== 2. analysis / the plan
_LABEL_HINT = re.compile(r"readmi|label|target|outcome|^y$|event", re.I)
_TIME_HINT = re.compile(r"disch|date|time|admit|week_date", re.I)
_SENSITIVE_HINT = re.compile(r"gender|sex|^age|anchor_age|race|ethnic|insur|payer", re.I)


def heuristic_plan(profile: dict, target: str) -> dict:
    """A conservative plan from column names and types, used when Gemini is
    unavailable or its answer does not validate."""
    cols = profile["columns"]
    label = None
    for col, c in cols.items():
        if _LABEL_HINT.search(col) and c["n_unique"] <= 3:
            label = col
            if "readmi" in col.lower():
                break
    ids = [c for c, p in cols.items() if p["kind"] == "identifier-like"]
    id_col = next((c for c in ids if re.search(r"patient|subject|mrn|member|person", c, re.I)),
                  ids[0] if ids else None)
    dates = [c for c, p in cols.items() if p["kind"] == "date"]
    time_col = next((c for c in dates if re.search(r"disch", c, re.I)),
                    next((c for c in dates if _TIME_HINT.search(c)), None))
    drop, numeric, categorical = [], [], []
    for col, p in cols.items():
        if col in (label, id_col, time_col):
            continue
        if (p["kind"] in ("identifier-like", "text", "date") or p["n_unique"] <= 1
                or (p["kind"] == "categorical" and p["n_unique"] > 200)):
            reason = {"identifier-like": "identifier", "text": "free text", "date": "date",
                      "categorical": "too many distinct values"}.get(p["kind"], "constant")
            drop.append({"column": col, "reason": reason})
        elif p["kind"] in ("numeric", "binary"):
            numeric.append(col)
        else:
            categorical.append(col)
    return {
        "summary": f"{profile['n_rows']:,} rows and {profile['n_columns']} columns. Roles were "
                   "assigned from column names and types (no AI analysis).",
        "label_column": label, "positive_value": None, "id_column": id_col,
        "time_column": time_col, "numeric_columns": numeric, "categorical_columns": categorical,
        "drop_columns": drop, "leakage_suspects": [],
        "sensitive_columns": [c for c in cols if _SENSITIVE_HINT.search(c)][:6],
        "data_issues": [], "recommended_algorithms": ["gradient_boosting"],
    }


def normalise_plan(plan: dict, columns: list, target: str) -> tuple:
    """
    Make a plan internally consistent against the real columns: unknown names
    removed, every column in exactly one role. Returns (plan, problems) -
    problems are the things a person must fix before training can start.
    """
    known = set(columns)

    def one(name):
        return name if isinstance(name, str) and name in known else None

    def many(names):
        out = []
        for n in names or []:
            n = n.get("column") if isinstance(n, dict) else n
            if isinstance(n, str) and n in known and n not in out:
                out.append(n)
        return out

    label, id_col, time_col = one(plan.get("label_column")), one(plan.get("id_column")), \
        one(plan.get("time_column"))
    reserved = {c for c in (label, id_col, time_col) if c}
    drop_entries = [d for d in (plan.get("drop_columns") or [])
                    if isinstance(d, dict) and d.get("column") in known]
    drop = [d["column"] for d in drop_entries if d["column"] not in reserved]
    numeric = [c for c in many(plan.get("numeric_columns")) if c not in reserved and c not in drop]
    categorical = [c for c in many(plan.get("categorical_columns"))
                   if c not in reserved and c not in drop and c not in numeric]
    assigned = reserved | set(drop) | set(numeric) | set(categorical)
    for col in columns:  # anything the plan forgot is dropped, visibly, not silently used
        if col not in assigned:
            drop.append(col)
            drop_entries.append({"column": col, "reason": "not assigned a role"})

    out = {
        **plan,
        "label_column": label, "id_column": id_col, "time_column": time_col,
        "numeric_columns": numeric, "categorical_columns": categorical,
        "drop_columns": [d for d in drop_entries if d["column"] in drop],
        "sensitive_columns": [c for c in many(plan.get("sensitive_columns"))
                              if c in numeric + categorical][:8],
        "leakage_suspects": [d for d in (plan.get("leakage_suspects") or [])
                             if isinstance(d, dict) and d.get("column") in known],
        "positive_value": (None if plan.get("positive_value") in (None, "", "null")
                           else str(plan["positive_value"])),
    }
    problems = []
    if not label:
        problems.append("Choose the outcome (label) column.")
    if target == "weekly" and not id_col:
        problems.append("Weekly data needs a patient identifier column, so each patient's "
                        "weeks stay together when the data is split.")
    if not numeric + categorical:
        problems.append("No feature columns are selected.")
    return out, problems


_YES = {"1", "1.0", "true", "yes", "y", "readmitted", "<30", "t"}
_NO = {"0", "0.0", "false", "no", "n", "not readmitted", "none", "f", ">30"}


def label_series(s: pd.Series, positive_value: Optional[str]) -> pd.Series:
    """The outcome as 1 / 0 / NaN. Ambiguous coding is refused, not guessed."""
    text = s.astype("object").where(s.isna(), s.astype(str).str.strip().str.lower())
    if positive_value is not None:
        pos = str(positive_value).strip().lower()
        return text.map(lambda v: np.nan if pd.isna(v) else float(v == pos))
    values = set(text.dropna().unique())
    if values <= _YES | _NO:
        return text.map(lambda v: np.nan if pd.isna(v) else float(v in _YES))
    raise ValueError(f"The outcome column has values {sorted(map(str, values))[:8]}; say which "
                     "one means 'readmitted' (positive value).")


def association_scan(df: pd.DataFrame, y: pd.Series, features: list) -> list:
    """
    How well each column predicts the outcome on its own. A single column
    with an AUROC near 1 almost always means the outcome leaked into it - a
    readmission date, a follow-up flag set because of the readmission - and
    is worth a person's look before anything trains on it.
    """
    from sklearn.metrics import roc_auc_score

    mask = y.notna()
    sample = df.loc[mask].sample(min(int(mask.sum()), 50000), random_state=0)
    ys = y.loc[sample.index].astype(int)
    if ys.nunique() < 2:
        return []
    out = []
    for col in features:
        s = sample[col]
        try:
            if pd.api.types.is_numeric_dtype(s):
                x = s.fillna(s.median() if s.notna().any() else 0)
            else:
                keys = s.astype(str).fillna("∅")
                x = keys.map(ys.groupby(keys).mean())
            if x.nunique() < 2:
                continue
            auc = float(roc_auc_score(ys, x))
            auc = max(auc, 1 - auc)
        except Exception:
            continue
        level = "very likely leakage" if auc >= 0.95 else "strong" if auc >= 0.85 else None
        out.append({"column": col, "auroc": round(auc, 3), "flag": level})
    out.sort(key=lambda r: -r["auroc"])
    return out[:12]


def _phase_one(db, job: dict, base: Optional[dict]) -> None:
    try:
        _step(job, "profile", "running")
        df = _read_csv(os.path.join(job["workdir"], "upload.csv"))
        if len(df) < 100:
            return _fail(job, "profile", f"The file has {len(df)} rows; at least 100 are needed.")
        profile = profile_dataframe(df)
        with _jobs_lock:
            job["profile"] = profile
        _log(job, f"Read {len(df):,} rows and {df.shape[1]} columns.", "profile")
        _step(job, "profile", "done", f"{len(df):,} rows · {df.shape[1]} columns")

        _step(job, "analyze", "running")
        base_ctx = _base_context(base) if base else None
        plan, source = None, "heuristic"
        if studio_llm.available():
            try:
                _log(job, "Gemini is reading the column profile (no patient rows are sent).")
                plan = studio_llm.analyze_dataset(profile, job["target"], base_ctx,
                                                  notify=lambda m: _log(job, m, "analyze"))
                source = "gemini"
            except Exception as exc:
                _log(job, f"Gemini analysis failed ({_short(exc)}); using name/type rules.")
        else:
            _log(job, "No GEMINI_API_KEY is configured; using name/type rules.")
        if not isinstance(plan, dict):
            plan = heuristic_plan(profile, job["target"])
        if base_ctx:  # a retrain keeps the base model's choices where the data allows
            for key in ("label_column", "id_column", "time_column", "positive_value"):
                if base_ctx.get(key) in df.columns or key == "positive_value":
                    plan.setdefault(key, base_ctx.get(key))
        plan, problems = normalise_plan(plan, list(df.columns), job["target"])
        assoc = _association_for(df, plan)
        with _jobs_lock:
            job["plan"], job["plan_source"], job["association"] = plan, source, assoc
            job["plan_problems"] = problems
        strong = [a["column"] for a in assoc if a["flag"]]
        if strong:
            _log(job, "Columns that predict the outcome suspiciously well on their own: "
                      + ", ".join(strong))
        _step(job, "analyze", "done", "Plan proposed by " + ("Gemini" if source == "gemini"
                                                               else "name/type rules"))
        _step(job, "confirm", "waiting", "Review the plan and continue")
        with _jobs_lock:
            job["status"] = "awaiting_confirmation"
    except Exception as exc:
        _fail(job, job.get("current_step") or "profile", _short(exc))


def _association_for(df: pd.DataFrame, plan: dict) -> list:
    if not plan.get("label_column"):
        return []
    try:
        y = label_series(df[plan["label_column"]], plan.get("positive_value"))
    except ValueError:
        return []
    return association_scan(df, y, plan["numeric_columns"] + plan["categorical_columns"])


def _short(exc) -> str:
    return f"{type(exc).__name__}: {str(exc)[:300]}"


# ============================================================ 3. confirmation
def confirm_job(db, job_id: str, user: dict, edits: dict) -> dict:
    job = get_job(job_id, user)
    if job["status"] != "awaiting_confirmation":
        raise ValueError("This run is not waiting for confirmation.")
    df = _read_csv(os.path.join(job["workdir"], "upload.csv"))
    plan = {**job["plan"], **{k: v for k, v in (edits or {}).items() if k in (
        "label_column", "positive_value", "id_column", "time_column", "numeric_columns",
        "categorical_columns", "drop_columns", "sensitive_columns")}}
    plan, problems = normalise_plan(plan, list(df.columns), job["target"])
    if problems:
        raise ValueError(" ".join(problems))
    y = label_series(df[plan["label_column"]], plan.get("positive_value"))  # raises if ambiguous
    if y.dropna().nunique() < 2:
        raise ValueError("The outcome column has only one value after mapping.")
    with _jobs_lock:
        job["plan"] = plan
        job["status"] = "running"
    _step(job, "confirm", "done", f"Confirmed by {user.get('email')}")
    _log(job, f"Plan confirmed: outcome '{plan['label_column']}', "
              f"{len(plan['numeric_columns']) + len(plan['categorical_columns'])} features.",
         "confirm")
    threading.Thread(target=_phase_two, args=(db, job, user), daemon=True).start()
    return public_job(job)


# ============================================================ split + files
def make_split(df: pd.DataFrame, group_col: Optional[str], time_col: Optional[str],
               fractions=(0.7, 0.1, 0.2)) -> np.ndarray:
    """
    'train' / 'valid' / 'test' per row. Whole patients go to one side, so a
    patient's other stays or weeks can never sit in training while one of them
    is being tested. With a time column the newest patients are the test set,
    which is how the model will actually be used: trained on the past, scoring
    what comes next.
    """
    groups = (df[group_col].astype(str) if group_col
              else pd.Series(np.arange(len(df)).astype(str), index=df.index))
    sizes = groups.value_counts()
    if time_col:
        t = pd.to_datetime(df[time_col], errors="coerce", format="mixed")
        first = t.groupby(groups).min()
        order = first.sort_values(na_position="first").index.tolist()
        order += [g for g in sizes.index if g not in set(order)]
    else:
        order = list(sizes.index)
        np.random.RandomState(0).shuffle(order)
    cum = np.cumsum([sizes[g] for g in order]) / len(df)
    cut_train, cut_valid = fractions[0], fractions[0] + fractions[1]
    which = {g: ("train" if c <= cut_train else "valid" if c <= cut_valid else "test")
             for g, c in zip(order, cum)}
    return groups.map(which).values


def _cap_rows(df: pd.DataFrame, group_col: Optional[str]) -> pd.DataFrame:
    if len(df) <= MAX_ROWS:
        return df
    if not group_col:
        return df.sample(MAX_ROWS, random_state=0)
    groups = df[group_col].astype(str)
    order = groups.drop_duplicates().sample(frac=1, random_state=0)
    keep, total = set(), 0
    sizes = groups.value_counts()
    for g in order:
        if total + sizes[g] > MAX_ROWS:
            break
        keep.add(g)
        total += sizes[g]
    return df[groups.isin(keep)]


def _write_split(job: dict, df: pd.DataFrame, plan: dict, y: pd.Series) -> dict:
    features = plan["numeric_columns"] + plan["categorical_columns"]
    split = make_split(df, plan.get("id_column"), plan.get("time_column"))
    wd = job["workdir"]
    data = df[features].copy()
    data[LABEL_FIELD] = y.values
    data[GROUP_FIELD] = (df[plan["id_column"]].astype(str).values if plan.get("id_column")
                         else np.arange(len(df)).astype(str))
    out = {}
    for part in ("train", "valid", "test"):
        rows = data[split == part]
        cols = features + [LABEL_FIELD, GROUP_FIELD] if part == "train" else features
        rows[cols].to_csv(os.path.join(wd, f"{part}.csv"), index=False)
        out[part] = {"rows": int(len(rows)), "positives": int(rows[LABEL_FIELD].sum()),
                     "labels": rows[LABEL_FIELD].astype(int).values,
                     "groups": rows[GROUP_FIELD].values}
    # The raw test rows, for subgroup analysis afterwards.
    out["test_frame"] = df.loc[split == "test"].reset_index(drop=True)
    return out


def _runner_config(job: dict, plan: dict, y_train: np.ndarray) -> dict:
    return {
        "label_column": plan["label_column"],
        "feature_columns": plan["numeric_columns"] + plan["categorical_columns"],
        "numeric_columns": plan["numeric_columns"],
        "categorical_columns": plan["categorical_columns"],
        "algorithm_preference": job["algorithm_preference"],
        "target": job["target"],
        "n_rows": int(len(y_train)),
        "n_positive": int(np.sum(y_train)),
    }


def _dtypes(config: dict) -> dict:
    # Categorical columns are read as text, so codes such as "0042" keep their
    # leading zeros and a column of mostly numbers with the odd "N/A" stays whole.
    return {c: "str" for c in config.get("categorical_columns", [])}


# ============================================================ sandbox runs
def run_sandbox(mode: str, workdir: str, job_spec: dict, timeout_s: int, log) -> dict:
    """
    Run studio_runner in a child process. The environment is built from
    scratch - PATH and thread counts only - so no API key, database URI or
    signing secret is visible to code the LLM wrote.
    """
    # The runner applies these to itself before importing anything, with hard
    # limits equal to soft ones so the code it then loads cannot raise them.
    job_spec = {**job_spec, "limits": {"cpu_seconds": timeout_s * 4,
                                       "memory_mb": MEMORY_LIMIT_MB,
                                       "file_mb": 2048}}
    with open(os.path.join(workdir, "job.json"), "w") as fh:
        json.dump(job_spec, fh)
    env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": workdir,
           "LANG": "C.UTF-8", "PYTHONHASHSEED": "0", "MPLBACKEND": "Agg",
           "OMP_NUM_THREADS": "2", "OPENBLAS_NUM_THREADS": "2", "MKL_NUM_THREADS": "2"}
    error = None
    with _sandbox_slot:
        proc = subprocess.Popen([sys.executable, "-I", RUNNER, mode, workdir], cwd=workdir,
                                env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                text=True, start_new_session=True)
        timer = threading.Timer(timeout_s, lambda: _kill(proc))
        timer.start()
        tail = []
        try:
            for line in proc.stdout:
                line = line.rstrip()
                try:
                    event = json.loads(line)
                except ValueError:
                    tail = (tail + [line])[-40:]
                    continue
                if event.get("kind") == "log":
                    log(event.get("message", ""))
                    if event.get("missing_inputs"):
                        log("Columns missing from the file (scored as unknown): "
                            + ", ".join(event["missing_inputs"][:20]))
                elif event.get("kind") == "error":
                    error = f"{event.get('message')}\n{event.get('traceback', '')}"
            proc.wait()
        finally:
            timed_out = not timer.is_alive() and proc.returncode not in (0, 1)
            timer.cancel()
    if timed_out:
        error = f"Stopped after the {timeout_s}s time limit."
    elif proc.returncode != 0 and not error:
        error = (f"The runner exited with code {proc.returncode} (out of memory or killed)."
                 + ("\n" + "\n".join(tail[-15:]) if tail else ""))
    return {"ok": proc.returncode == 0 and not error, "error": error}


def _kill(proc) -> None:
    try:
        os.killpg(proc.pid, 9)
    except (ProcessLookupError, PermissionError):
        pass


# ============================================================ 4-5. code + train
def _phase_two(db, job: dict, user: dict) -> None:
    try:
        plan = job["plan"]
        df = _read_csv(os.path.join(job["workdir"], "upload.csv"))
        y_all = label_series(df[plan["label_column"]], plan.get("positive_value"))
        df = df[y_all.notna()].reset_index(drop=True)
        y_all = y_all[y_all.notna()].reset_index(drop=True)
        if len(df) > MAX_ROWS:
            df = _cap_rows(df, plan.get("id_column"))
            y_all = y_all.loc[df.index].reset_index(drop=True)
            df = df.reset_index(drop=True)
            _log(job, f"Large file: trained on a sample of {len(df):,} rows (whole patients).")
        split = _write_split(job, df, plan, y_all)
        with _jobs_lock:
            job["_split"] = split
        tr, va, te = split["train"], split["valid"], split["test"]
        _log(job, f"Split by {'patient' if plan.get('id_column') else 'row'}"
                  f"{' and time (newest patients tested)' if plan.get('time_column') else ''}: "
                  f"train {tr['rows']:,} ({tr['positives']:,} readmitted), validation "
                  f"{va['rows']:,}, test {te['rows']:,} ({te['positives']:,} readmitted).",
             "generate")
        if tr["positives"] < 20 or te["positives"] < 5:
            return _fail(job, "generate", "Too few readmissions to train and test a model "
                         f"(train {tr['positives']}, test {te['positives']}).")
        config = _runner_config(job, plan, tr["labels"])
        with _jobs_lock:
            job["config"] = config

        base = get_model(db, job["base_model_id"], user) if job.get("base_model_id") else None
        ok = _generate_and_train(job, config, base)
        if not ok:
            return
        _evaluate(db, job, user, base)
        _recommend(job)
        with _jobs_lock:
            job["status"] = "completed"
        _persist_job(db, job)
    except Exception as exc:
        _fail(job, job.get("current_step") or "generate", _short(exc))
        _persist_job(db, job)


def _generate_and_train(job: dict, config: dict, base: Optional[dict]) -> bool:
    wd = job["workdir"]
    attempts, code, error = [], None, None
    base_code = base.get("code") if base and not base.get("builtin") else None
    base_card = _base_context(base) if base else None
    use_llm = studio_llm.available()

    for attempt in range(1, CODE_ATTEMPTS + 1 if use_llm else 1):
        _step(job, "generate", "running")
        try:
            _log(job, f"Gemini is writing the pipeline (attempt {attempt}).", "generate")
            code = studio_llm.generate_pipeline(
                job["profile"], config, job["target"], base_code=base_code,
                base_card=base_card, previous_code=code if error else None, error=error,
                notify=lambda m: _log(job, m, "generate"))
        except Exception as exc:
            attempts.append({"attempt": attempt, "stage": "generate", "error": _short(exc)})
            _log(job, f"Gemini could not write the code: {_short(exc)}", "generate")
            break
        problems = studio_guard.check_code(code)
        if problems:
            error = "The code was refused before running:\n" + "\n".join(problems)
            attempts.append({"attempt": attempt, "stage": "check", "error": error})
            _log(job, error, "generate")
            continue
        _step(job, "generate", "done", f"Gemini, attempt {attempt}")
        result = _train_code(job, code, config)
        if result["ok"]:
            attempts.append({"attempt": attempt, "stage": "train", "error": None})
            return _accept_code(job, code, "gemini", attempts)
        error = result["error"]
        attempts.append({"attempt": attempt, "stage": "train", "error": error[-1500:]})
        _log(job, f"The generated code failed: {error.splitlines()[0] if error else ''}", "train")
        _step(job, "train", "failed", "Generated code failed; retrying")

    # Every run ends with a model: the tested standard pipeline takes over.
    reason = ("no GEMINI_API_KEY is configured" if not use_llm
              else f"the generated code failed {len(attempts)} time(s)")
    _log(job, f"Using the Preventra standard pipeline because {reason}.", "generate")
    _step(job, "generate", "done", "Preventra standard pipeline")
    result = _train_code(job, STANDARD_PIPELINE, config)
    if result["ok"]:
        return _accept_code(job, STANDARD_PIPELINE, "standard", attempts)
    _fail(job, "train", "Training failed: " + (result["error"] or "")[:600])
    return False


def _train_code(job: dict, code: str, config: dict) -> dict:
    wd = job["workdir"]
    with open(os.path.join(wd, "pipeline_code.py"), "w") as fh:
        fh.write(code)
    for stale in ("model.joblib", "train_result.json"):
        if os.path.exists(os.path.join(wd, stale)):
            os.remove(os.path.join(wd, stale))
    shutil.rmtree(os.path.join(wd, "__pycache__"), ignore_errors=True)
    _step(job, "train", "running")
    return run_sandbox("train", wd, {
        "config": config, "train_file": "train.csv", "target_field": LABEL_FIELD,
        "group_field": GROUP_FIELD if job["plan"].get("id_column") else None,
        "dtypes": _dtypes(config)}, TRAIN_TIMEOUT_S, lambda m: _log(job, m, "train"))


def _accept_code(job: dict, code: str, origin: str, attempts: list) -> bool:
    with open(os.path.join(job["workdir"], "train_result.json")) as fh:
        result = json.load(fh)
    with _jobs_lock:
        job["code"], job["code_origin"] = code, origin
        job["code_sections"] = studio_guard.sections(code)
        job["code_attempts"] = attempts
        job["train_report"] = result
    algo = (result.get("report") or {}).get("algorithm", "model")
    _step(job, "train", "done", f"{algo} · {result.get('n_features')} features")
    return True


# ============================================================ 6. evaluation
def _predict(workdir: str, code: str, config: dict, inputs: list, log,
             model_file: Optional[str] = None, compat: bool = False) -> dict:
    """Score files with a model in its own sandbox directory."""
    os.makedirs(workdir, exist_ok=True)
    with open(os.path.join(workdir, "pipeline_code.py"), "w") as fh:
        fh.write(code)
    shutil.rmtree(os.path.join(workdir, "__pycache__"), ignore_errors=True)
    spec = {"config": config, "inputs": inputs, "dtypes": _dtypes(config),
            "sklearn_compat": compat}
    if model_file:
        spec["model_file"] = model_file
    return run_sandbox("predict", workdir, spec, PREDICT_TIMEOUT_S, log)


def _scores(path: str) -> np.ndarray:
    return pd.read_csv(path)["score"].astype(float).values


def _evaluate(db, job: dict, user: dict, base: Optional[dict]) -> None:
    _step(job, "evaluate", "running")
    wd, split, config = job["workdir"], job["_split"], job["config"]
    log = lambda m: _log(job, m, "evaluate")  # noqa: E731
    test_labeled = os.path.join(wd, "test_labeled.csv")
    pd.read_csv(os.path.join(wd, "test.csv"), low_memory=False, dtype=_dtypes(config)).assign(
        **{LABEL_FIELD: split["test"]["labels"]}).to_csv(test_labeled, index=False)
    result = _predict(wd, job["code"], config, [
        {"name": "validation", "path": os.path.join(wd, "valid.csv"),
         "out": os.path.join(wd, "pred_valid.csv")},
        {"name": "test", "path": test_labeled, "out": os.path.join(wd, "pred_test.csv"),
         "importance_label_field": LABEL_FIELD,
         "importance_out": os.path.join(wd, "importance.json")},
    ], log)
    if not result["ok"]:
        raise RuntimeError("Scoring the held-out data failed: " + (result["error"] or "")[:400])

    y_te, p_te = split["test"]["labels"], _scores(os.path.join(wd, "pred_test.csv"))
    y_va, p_va = split["valid"]["labels"], _scores(os.path.join(wd, "pred_valid.csv"))
    test_groups = split["test"]["groups"] if job["plan"].get("id_column") else None
    with _jobs_lock:
        job["_test_groups"] = test_groups
    metrics = compute_metrics(y_te, p_te, y_va, p_va, prevalence_train=float(
        np.mean(split["train"]["labels"])), groups=test_groups)
    importance = []
    if os.path.exists(os.path.join(wd, "importance.json")):
        with open(os.path.join(wd, "importance.json")) as fh:
            importance = json.load(fh)

    comparisons = [{"model_id": None, "name": "No model (everyone gets the average risk)",
                    "kind": "baseline", **_constant_baseline(y_te, split["train"]["labels"])}]
    seen = set()
    active = active_model(db, user, job["target"])
    for kind, comp in (("base", base), ("active", active)):
        if not comp or comp["model_id"] in seen or not comp.get("can_score", True):
            continue
        seen.add(comp["model_id"])
        comparisons.append(_compare(db, job, comp, kind, y_te, p_te))

    with _jobs_lock:
        job["evaluation"] = {
            "metrics": metrics, "comparisons": comparisons, "importance": importance,
            "subgroups": subgroup_metrics(split["test_frame"], y_te, p_te,
                                          job["plan"].get("sensitive_columns", []),
                                          groups=test_groups),
            "split": {k: {"rows": split[k]["rows"], "positives": split[k]["positives"]}
                      for k in ("train", "valid", "test")},
            "split_method": ("patient and time" if job["plan"].get("time_column") and
                             job["plan"].get("id_column") else
                             "time" if job["plan"].get("time_column") else
                             "patient" if job["plan"].get("id_column") else "random rows"),
        }
    _step(job, "evaluate", "done", f"AUROC {metrics['auroc']:.3f} on "
                                   f"{split['test']['rows']:,} held-out rows")


def _compare(db, job, comp, kind, y_te, p_te) -> dict:
    name = comp["name"]
    row = {"model_id": comp["model_id"], "name": name, "kind": kind}
    try:
        features = comp_features(comp)
        have = [c for c in features if c in job["_split"]["test_frame"].columns]
        coverage = len(have) / max(len(features), 1)
        row["feature_coverage"] = round(coverage, 2)
        if coverage < 0.6:
            row["note"] = (f"Not comparable: this file has {len(have)} of the "
                           f"{len(features)} columns it needs.")
            return row
        out = os.path.join(job["workdir"], f"cmp_{comp['model_id']}")
        os.makedirs(out, exist_ok=True)
        # The comparison model needs its own raw columns, which the test file
        # may not hold as features; score the raw test rows instead.
        raw_path = os.path.join(out, "raw_test.csv")
        job["_split"]["test_frame"].to_csv(raw_path, index=False)
        result = _score_with(db, comp, [{"name": f"test ({name})", "path": raw_path,
                                         "out": os.path.join(out, "pred.csv")}], out,
                             lambda m: _log(job, m, "evaluate"))
        if not result["ok"]:
            row["note"] = "Could not be scored on this data: " + (result["error"] or "")[:200]
            return row
        p = _scores(os.path.join(out, "pred.csv"))
        row.update(_basic_metrics(y_te, p))
        row["delta_auroc"] = paired_delta(y_te, p_te, p, job["_test_groups"])
    except Exception as exc:
        row["note"] = f"Could not be compared: {_short(exc)}"
    return row


def comp_features(model: dict) -> list:
    if model["model_id"] == BUILTIN_MIMIC:
        return _mimic_spec()["features"]
    return model.get("config", {}).get("feature_columns", [])


# ------------------------------------------------------------- metric maths
def _basic_metrics(y, p) -> dict:
    from sklearn.metrics import brier_score_loss, roc_auc_score
    return {"auroc": round(float(roc_auc_score(y, p)), 4),
            "brier": round(float(brier_score_loss(y, p)), 4)}


def _constant_baseline(y_test, y_train) -> dict:
    from sklearn.metrics import brier_score_loss
    p = np.full(len(y_test), float(np.mean(y_train)))
    return {"auroc": 0.5, "brier": round(float(brier_score_loss(y_test, p)), 4)}


def _bootstrap_indices(n: int, reps: int = 300, groups=None):
    """
    Resampled row indices for 95% intervals. With patient ids, whole patients
    are resampled (a cluster bootstrap): a patient's weeks or stays are
    correlated, and resampling them as independent rows makes every interval
    look about as many times narrower as there are rows per patient.
    """
    rng = np.random.RandomState(0)
    if groups is None:
        for _ in range(reps):
            yield rng.randint(0, n, n)
        return
    codes, _ = pd.factorize(pd.Series(groups).astype(str))
    order = np.argsort(codes, kind="stable")
    members = np.split(order, np.cumsum(np.bincount(codes))[:-1])
    for _ in range(reps):
        pick = rng.randint(0, len(members), len(members))
        yield np.concatenate([members[i] for i in pick])


def paired_delta(y, p_new, p_old, groups=None) -> dict:
    """AUROC(new) - AUROC(old) on the same rows, with a 95% bootstrap interval."""
    from sklearn.metrics import roc_auc_score
    deltas = []
    for idx in _bootstrap_indices(len(y), groups=groups):
        if y[idx].min() == y[idx].max():
            continue
        deltas.append(roc_auc_score(y[idx], p_new[idx]) - roc_auc_score(y[idx], p_old[idx]))
    point = float(roc_auc_score(y, p_new) - roc_auc_score(y, p_old))
    lo, hi = np.percentile(deltas, [2.5, 97.5]) if deltas else (point, point)
    return {"estimate": round(point, 4), "ci_low": round(float(lo), 4),
            "ci_high": round(float(hi), 4)}


def _calibration_line(y, p) -> tuple:
    """Slope and intercept of y on logit(p). A slope of 1 and intercept of 0 is
    perfect calibration; a slope below 1 means the scores are too extreme."""
    p = np.clip(p, 1e-6, 1 - 1e-6)
    x = np.log(p / (1 - p))
    X = np.column_stack([np.ones_like(x), x])
    beta = np.array([0.0, 1.0])
    for _ in range(50):  # Newton-Raphson for a two-parameter logistic fit
        mu = 1 / (1 + np.exp(-(X @ beta)))
        w = mu * (1 - mu)
        hess = X.T @ (X * w[:, None]) + 1e-9 * np.eye(2)
        step = np.linalg.solve(hess, X.T @ (y - mu))
        beta += step
        if np.abs(step).max() < 1e-8:
            break
    return float(beta[1]), float(beta[0])


def compute_metrics(y, p, y_valid, p_valid, prevalence_train: float, groups=None) -> dict:
    from sklearn.metrics import (average_precision_score, brier_score_loss, f1_score,
                                 precision_recall_curve, roc_auc_score, roc_curve)

    y, p = np.asarray(y).astype(int), np.asarray(p).astype(float)
    auroc = float(roc_auc_score(y, p))
    boot, slopes = [], []
    for i in _bootstrap_indices(len(y), groups=groups):
        if y[i].min() != y[i].max():
            boot.append(roc_auc_score(y[i], p[i]))
            slopes.append(_calibration_line(y[i], p[i])[0])
    ci = np.percentile(boot, [2.5, 97.5]) if boot else (auroc, auroc)
    slope_ci = np.percentile(slopes, [2.5, 97.5]) if slopes else (np.nan, np.nan)
    if groups is not None:
        g = pd.Series(np.asarray(groups).astype(str))
        units = int(g.nunique())
        positive_units = int(g[y == 1].nunique())
    else:
        units, positive_units = int(len(y)), int(y.sum())

    # The operating threshold is chosen on the validation rows, never the test
    # rows, so the precision and recall below are honest.
    if y_valid is not None and len(y_valid) and np.sum(y_valid) >= 10:
        prec, rec, thr = precision_recall_curve(y_valid, p_valid)
        f1 = 2 * prec * rec / np.clip(prec + rec, 1e-12, None)
        threshold = float(thr[int(np.argmax(f1[:-1]))]) if len(thr) else 0.5
        threshold_basis = "best F1 on the validation rows"
    else:
        threshold = float(np.quantile(p, 1 - min(max(prevalence_train, 0.01), 0.99)))
        threshold_basis = "flags the same share of patients as the training readmission rate"
    pred = (p >= threshold).astype(int)
    tp, fp = int(((pred == 1) & (y == 1)).sum()), int(((pred == 1) & (y == 0)).sum())
    fn, tn = int(((pred == 0) & (y == 1)).sum()), int(((pred == 0) & (y == 0)).sum())

    slope, intercept = _calibration_line(y, p)
    bins = pd.qcut(pd.Series(p), q=10, duplicates="drop")
    calib = (pd.DataFrame({"p": p, "y": y, "bin": bins}).groupby("bin", observed=True)
             .agg(predicted=("p", "mean"), observed=("y", "mean"), n=("y", "size"))
             .reset_index(drop=True))
    fpr, tpr, _ = roc_curve(y, p)
    take = np.unique(np.linspace(0, len(fpr) - 1, min(len(fpr), 60)).astype(int))
    hist, edges = np.histogram(p, bins=20, range=(0, 1))

    return {
        "n_test": int(len(y)), "n_positive": int(y.sum()),
        # Patients when ids are known: the unit the intervals and the
        # "enough readmissions" check are counted in.
        "n_units": units, "n_positive_units": positive_units,
        "unit": "patients" if groups is not None else "rows",
        "prevalence": round(float(y.mean()), 4),
        "auroc": round(auroc, 4), "auroc_ci": [round(float(ci[0]), 4), round(float(ci[1]), 4)],
        "auprc": round(float(average_precision_score(y, p)), 4),
        "brier": round(float(brier_score_loss(y, p)), 4),
        "mean_predicted": round(float(p.mean()), 4),
        "calibration_slope": round(slope, 3), "calibration_intercept": round(intercept, 3),
        "calibration_slope_ci": [round(float(slope_ci[0]), 3), round(float(slope_ci[1]), 3)],
        "calibration_ratio": round(float(p.mean() / max(y.mean(), 1e-9)), 3),
        "threshold": round(threshold, 4), "threshold_basis": threshold_basis,
        "precision": round(tp / max(tp + fp, 1), 4), "recall": round(tp / max(tp + fn, 1), 4),
        "specificity": round(tn / max(tn + fp, 1), 4),
        "f1": round(float(f1_score(y, pred, zero_division=0)), 4),
        "flagged_pct": round(float(pred.mean() * 100), 1),
        "confusion": {"tp": tp, "fp": fp, "fn": fn, "tn": tn},
        "calibration_bins": [{"predicted": round(float(r.predicted), 4),
                              "observed": round(float(r.observed), 4), "n": int(r.n)}
                             for r in calib.itertuples()],
        "roc_curve": [{"fpr": round(float(fpr[i]), 4), "tpr": round(float(tpr[i]), 4)}
                      for i in take],
        "score_histogram": [{"from": round(float(edges[i]), 2), "to": round(float(edges[i + 1]), 2),
                             "n": int(hist[i])} for i in range(len(hist))],
    }


def subgroup_metrics(frame: pd.DataFrame, y, p, columns: list, groups=None) -> list:
    """AUROC per level of each demographic column, where there are enough
    readmitted and not-readmitted patients in the level to say anything.
    Counted in patients when ids are known, so four weekly rows of one patient
    do not pass for four readmissions."""
    from sklearn.metrics import roc_auc_score
    ids = pd.Series(np.asarray(groups).astype(str)) if groups is not None else None
    out = []
    for col in columns:
        if col not in frame.columns:
            continue
        s = frame[col]
        if pd.api.types.is_numeric_dtype(s) and s.nunique() > 12:
            levels = pd.cut(s, bins=[-np.inf, 44, 64, 79, np.inf],
                            labels=["<45", "45-64", "65-79", "80+"]) if re.search(
                "age", col, re.I) else pd.qcut(s, 4, duplicates="drop")
            levels = levels.astype(str)
        else:
            levels = s.astype(str)
        top = levels.value_counts().head(8).index
        for level in top:
            mask = (levels == level).values
            yy, pp = y[mask], p[mask]
            if ids is not None:
                n, pos = int(ids[mask].nunique()), int(ids[mask & (y == 1)].nunique())
            else:
                n, pos = int(mask.sum()), int(yy.sum())
            entry = {"column": col, "level": str(level), "n": n, "positives": pos,
                     "unit": "patients" if ids is not None else "rows"}
            if pos >= 20 and (n - pos) >= 20:
                entry["auroc"] = round(float(roc_auc_score(yy, pp)), 4)
            out.append(entry)
    return out


# ============================================================ 7. recommendation
def decide(evaluation: dict, association: list, code_origin: str) -> dict:
    """
    Deploy / deploy with caution / do not deploy, by fixed rules - the same
    rules for every model, so two runs can be compared and the decision can be
    audited. Gemini explains this verdict; it does not make it.
    """
    m = evaluation["metrics"]
    checks = []

    def check(name, status, detail):
        checks.append({"name": name, "status": status, "detail": detail})

    pos = m.get("n_positive_units", m["n_positive"])
    what = ("readmitted patients" if m.get("unit") == "patients" else "readmissions")
    extra = (f" ({m['n_positive']} rows)" if m.get("unit") == "patients"
             and m["n_positive"] != pos else "")
    if pos < 30:
        check("Enough readmissions to judge", "fail",
              f"Only {pos} {what} in the test set{extra}; any result is mostly noise.")
    elif pos < 100:
        check("Enough readmissions to judge", "warn",
              f"{pos} {what} in the test set{extra}. Usable, but 100+ gives a firm answer.")
    else:
        check("Enough readmissions to judge", "pass", f"{pos} {what} in the test set{extra}.")

    auc, lo = m["auroc"], m["auroc_ci"][0]
    if auc > 0.97:
        check("Discrimination", "fail", f"AUROC {auc:.3f} is implausibly high for readmission; "
                                        "the outcome has almost certainly leaked into a feature.")
    elif auc < 0.6:
        check("Discrimination", "fail", f"AUROC {auc:.3f}: barely better than chance.")
    elif auc < 0.65:
        check("Discrimination", "warn", f"AUROC {auc:.3f} (95% CI {lo:.3f}-{m['auroc_ci'][1]:.3f}) "
                                        "is weak for clinical use.")
    else:
        check("Discrimination", "pass", f"AUROC {auc:.3f} (95% CI {lo:.3f}-{m['auroc_ci'][1]:.3f}).")

    ratio, slope = m["calibration_ratio"], m["calibration_slope"]
    s_lo, s_hi = (m.get("calibration_slope_ci") or [slope, slope])
    # A slope outside the band only counts when its interval excludes 1: on a
    # few hundred patients the estimate swings widely by chance alone.
    slope_off = not (0.75 <= slope <= 1.3) and not (s_lo <= 1.0 <= s_hi)
    if not (0.8 <= ratio <= 1.25) or slope_off:
        check("Calibration", "warn",
              f"Average predicted risk is {ratio:.2f}x the observed rate and the calibration "
              f"slope is {slope:.2f} (95% CI {s_lo:.2f}-{s_hi:.2f}; ideal 1.0): scores should "
              "not be read as exact probabilities until recalibrated.")
    else:
        check("Calibration", "pass", f"Predicted risk matches the observed rate (ratio "
                                     f"{ratio:.2f}, slope {slope:.2f}, 95% CI "
                                     f"{s_lo:.2f}-{s_hi:.2f}).")

    baseline = next((c for c in evaluation["comparisons"] if c["kind"] == "baseline"), None)
    if baseline and m["brier"] >= baseline["brier"]:
        check("Better than no model", "fail", "The Brier score is no better than giving every "
                                              "patient the average risk.")

    for comp in evaluation["comparisons"]:
        if comp["kind"] == "baseline":
            continue
        d = comp.get("delta_auroc")
        label = ("the model it retrains" if comp["kind"] == "base" else "the active model")
        if not d:
            check(f"Compared with {label}", "info", comp.get("note") or "Not compared.")
        elif d["ci_high"] < 0:
            check(f"Compared with {label}", "fail",
                  f"Worse than {comp['name']} on the same patients (AUROC {d['estimate']:+.3f}, "
                  f"95% CI {d['ci_low']:+.3f} to {d['ci_high']:+.3f}).")
        elif d["ci_low"] > 0:
            check(f"Compared with {label}", "pass",
                  f"Better than {comp['name']} on the same patients (AUROC {d['estimate']:+.3f}, "
                  f"95% CI {d['ci_low']:+.3f} to {d['ci_high']:+.3f}).")
        else:
            check(f"Compared with {label}", "warn",
                  f"No clear difference from {comp['name']} (AUROC {d['estimate']:+.3f}, 95% CI "
                  f"{d['ci_low']:+.3f} to {d['ci_high']:+.3f}); switching gains little.")

    gaps = [s for s in evaluation["subgroups"] if s.get("auroc") is not None
            and s["auroc"] < auc - 0.08]
    if gaps:
        worst = min(gaps, key=lambda s: s["auroc"])
        check("Fairness across groups", "warn",
              f"Weaker for {len(gaps)} group(s); lowest is {worst['column']} = {worst['level']} "
              f"(AUROC {worst['auroc']:.3f} vs {auc:.3f} overall).")
    elif evaluation["subgroups"]:
        check("Fairness across groups", "pass", "No demographic group is more than 0.08 AUROC "
                                                "below the overall result.")

    leaks = [a["column"] for a in association if a.get("flag") == "very likely leakage"]
    if leaks:
        check("Leakage", "warn", "These columns predict the outcome almost perfectly on their own "
                                 f"and were kept: {', '.join(leaks)}.")

    if code_origin == "standard":
        check("Pipeline", "info", "Trained with the Preventra standard pipeline, not "
                                  "Gemini-written code.")

    statuses = {c["status"] for c in checks}
    decision = ("do_not_deploy" if "fail" in statuses else
                "caution" if "warn" in statuses else "deploy")
    headline = {"deploy": "Recommended for deployment",
                "caution": "Deployable with caution",
                "do_not_deploy": "Not recommended for deployment"}[decision]
    return {"decision": decision, "headline": headline, "checks": checks}


def _recommend(job: dict) -> None:
    _step(job, "recommend", "running")
    verdict = decide(job["evaluation"], job.get("association") or [], job["code_origin"])
    m = job["evaluation"]["metrics"]
    unit = ("patient-weeks (several rows per patient)" if job["target"] == "weekly"
            else "hospital stays")
    summary = {
        "verdict": verdict["headline"], "checks": verdict["checks"],
        "row_unit": unit,
        "test_rows": m["n_test"], "test_rows_readmitted": m["n_positive"],
        "test_patients": m.get("n_units"), "test_patients_readmitted": m.get("n_positive_units"),
        "auroc": m["auroc"], "auroc_95ci": m["auroc_ci"], "auprc": m["auprc"],
        "brier": m["brier"], "precision_at_threshold": m["precision"],
        "recall_at_threshold": m["recall"], "flagged_pct": m["flagged_pct"],
        "algorithm": (job.get("train_report") or {}).get("report", {}).get("algorithm"),
        "comparisons": [{k: c.get(k) for k in ("name", "auroc", "brier", "delta_auroc", "note")}
                        for c in job["evaluation"]["comparisons"]],
    }
    explanation, source = None, "rules"
    if studio_llm.available():
        try:
            explanation, source = studio_llm.explain_verdict(
                summary, notify=lambda m: _log(job, m, "recommend")), "gemini"
        except Exception as exc:
            _log(job, f"Gemini could not write the explanation ({_short(exc)}).", "recommend")
    if not explanation:
        explanation = [f"{c['name']}: {c['detail']}" for c in verdict["checks"]]
    with _jobs_lock:
        job["verdict"] = verdict
        job["explanation"] = {"bullets": explanation, "source": source}
    _step(job, "recommend", "done", verdict["headline"])


def _persist_job(db, job: dict) -> None:
    try:
        doc = public_job(job)
        doc["hospital_id"] = job["hospital_id"]
        doc.pop("logs", None)
        db[JOBS].replace_one({"job_id": job["job_id"]}, doc, upsert=True)
    except Exception as exc:
        print(f"[studio] could not persist job {job['job_id']}: {exc}")


# ============================================================ registry
def _gridfs(db):
    import gridfs
    return gridfs.GridFS(getattr(db, "_db", db), collection=BUCKET)


def _registry(db):
    return getattr(db, "_db", db)[MODELS]


def _base_context(model: dict) -> dict:
    if model["model_id"] == BUILTIN_MIMIC:
        spec = _mimic_spec()
        return {"name": model["name"], "algorithm": model["algorithm"],
                "label_column": "readmit_30d", "id_column": "subject_id", "time_column": None,
                "positive_value": None, "feature_columns": spec["features"],
                "metrics": model.get("metrics")}
    cfg = model.get("config", {})
    return {"name": model["name"], "algorithm": model.get("algorithm"),
            "label_column": model.get("data", {}).get("label_column"),
            "id_column": model.get("data", {}).get("id_column"),
            "time_column": model.get("data", {}).get("time_column"),
            "positive_value": model.get("data", {}).get("positive_value"),
            "feature_columns": cfg.get("feature_columns"),
            "metrics": model.get("metrics")}


def save_job_model(db, job_id: str, user: dict, name: Optional[str] = None) -> dict:
    job = get_job(job_id, user)
    if job["status"] != "completed":
        raise ValueError("Only a finished run can be saved.")
    if job.get("saved_model_id"):
        return get_model(db, job["saved_model_id"], user)
    with open(os.path.join(job["workdir"], "model.joblib"), "rb") as fh:
        artifact_id = _gridfs(db).put(fh.read(), filename=f"{job_id}.joblib")
    ev, plan = job["evaluation"], job["plan"]
    m = ev["metrics"]
    base_rate = 100 * ev["split"]["train"]["positives"] / max(ev["split"]["train"]["rows"], 1)
    model_id = "MDL-" + uuid.uuid4().hex[:10].upper()
    doc = {
        "model_id": model_id, "name": (name or job["name"]).strip(), "target": job["target"],
        "hospital_id": job["hospital_id"], "created_by": user.get("email"), "created_at": _now(),
        "job_id": job_id, "mode": job["mode"], "base_model_id": job.get("base_model_id"),
        "algorithm": (job["train_report"].get("report") or {}).get("algorithm"),
        "algorithm_preference": job["algorithm_preference"],
        "data": {"filename": job["filename"], "rows": job["profile"]["n_rows"],
                 "label_column": plan["label_column"], "positive_value": plan.get("positive_value"),
                 "id_column": plan.get("id_column"), "time_column": plan.get("time_column"),
                 "split": ev["split"], "split_method": ev["split_method"]},
        "config": job["config"],
        "metrics": {k: m[k] for k in ("auroc", "auroc_ci", "auprc", "brier", "precision", "recall",
                                      "specificity", "f1", "threshold", "prevalence", "n_test",
                                      "n_positive", "n_units", "n_positive_units", "unit",
                                      "calibration_slope", "calibration_slope_ci",
                                      "calibration_ratio")},
        # Medium from this population's average readmission rate, High from
        # twice it - the rule behind the production bands (20 / 40 at a 19.6%
        # base rate). "Above average" and "double the average" read the same
        # way at every hospital, whatever its case mix.
        "bands": {"high_score_threshold": round(2 * base_rate, 1),
                  "low_score_threshold": round(base_rate, 1)},
        "evaluation": ev, "verdict": job["verdict"], "explanation": job["explanation"],
        "code": job["code"], "code_origin": job["code_origin"],
        "train_report": job["train_report"], "artifact_id": artifact_id,
        "can_score": True, "can_retrain": True,
    }
    _registry(db).insert_one(dict(doc))
    with _jobs_lock:
        job["saved_model_id"] = model_id
    _persist_job(db, job)
    return _public_model(doc)


def _public_model(doc: dict) -> dict:
    doc = {k: v for k, v in doc.items() if k not in ("_id", "artifact_id")}
    return json.loads(json.dumps(doc, default=str))


def list_models(db, user: dict, target: Optional[str] = None) -> list:
    query = {} if (user["role"] == "superadmin" and not user.get("acting_hospital_id")) else \
        {"hospital_id": _user_hospital(user)}
    if target:
        query["target"] = target
    saved = [_public_model(d) for d in _registry(db).find(
        query, {"evaluation": 0, "code": 0, "train_report": 0}).sort("created_at", -1)]
    builtins = [b for b in _builtins().values() if not target or b["target"] == target]
    active = {t: active_model_id(db, user, t) for t in TARGETS}
    for m in builtins + saved:
        m["active"] = active.get(m["target"]) == m["model_id"]
    return builtins + saved


def _user_hospital(user: dict):
    from api import access
    return access.hospital_of(user)


def get_model(db, model_id: str, user: dict) -> dict:
    if model_id in _builtins():
        return dict(_builtins()[model_id])
    doc = _registry(db).find_one({"model_id": model_id})
    if not doc or not _visible(user, doc.get("hospital_id")):
        raise LookupError("No such model.")
    out = _public_model(doc)
    if out.get("code"):
        out["code_sections"] = studio_guard.sections(out["code"])
    return out


def active_model_id(db, user: dict, target: str) -> str:
    hospital = _user_hospital(user)
    store = getattr(db, "_db", db)[DEPLOYMENTS]
    doc = (store.find_one({"_id": f"{_hospital_key(hospital)}:{target}"})
           or (store.find_one({"_id": f"global:{target}"}) if hospital else None))
    if doc:
        return doc["model_id"]
    return BUILTIN_MIMIC if target == "discharge" else BUILTIN_RULES


def active_model(db, user: dict, target: str) -> Optional[dict]:
    try:
        return get_model(db, active_model_id(db, user, target), user)
    except LookupError:  # deployed model deleted out from under the record
        return get_model(db, BUILTIN_MIMIC if target == "discharge" else BUILTIN_RULES, user)


def deploy_model(db, model_id: str, user: dict) -> dict:
    model = get_model(db, model_id, user)
    hospital = _user_hospital(user)
    key = f"{_hospital_key(hospital)}:{model['target']}"
    store = getattr(db, "_db", db)[DEPLOYMENTS]
    previous = store.find_one({"_id": key})
    entry = {"model_id": model_id, "deployed_by": user.get("email"), "deployed_at": _now()}
    history = (previous or {}).get("history", [])
    if previous:
        history.append({k: previous.get(k) for k in ("model_id", "deployed_by", "deployed_at")})
    store.replace_one({"_id": key}, {"_id": key, "hospital_id": hospital,
                                     "target": model["target"], **entry,
                                     "history": history[-20:]}, upsert=True)
    return {"target": model["target"], "model_id": model_id, "name": model["name"], **entry}


def delete_model(db, model_id: str, user: dict) -> None:
    if model_id in _builtins():
        raise ValueError("Built-in models cannot be deleted.")
    model = get_model(db, model_id, user)
    store = getattr(db, "_db", db)[DEPLOYMENTS]
    if store.find_one({"model_id": model_id}):
        raise ValueError("This model is deployed. Deploy another model first, then delete it.")
    doc = _registry(db).find_one({"model_id": model_id})
    if doc and doc.get("artifact_id"):
        _gridfs(db).delete(doc["artifact_id"])
    _registry(db).delete_one({"model_id": model_id})
    return model


def overview(db, user: dict) -> dict:
    return {
        "llm_available": studio_llm.available(),
        "algorithms": ALGORITHMS,
        "limits": {"max_upload_mb": MAX_UPLOAD_MB, "max_rows": MAX_ROWS},
        "active": {t: active_model(db, user, t) for t in TARGETS},
        "jobs": list_jobs(user),
    }


# ============================================================ scoring
def _materialise(db, model: dict, workdir: str) -> dict:
    """Code, model file and config for scoring with a model; keyword args for _predict."""
    if model["model_id"] == BUILTIN_MIMIC:
        spec = _mimic_spec()
        return {"code": BUILTIN_MIMIC_ADAPTER, "compat": True,
                "model_file": os.path.join(_RESULTS, "phase1_model.joblib"),
                "config": {"feature_columns": spec["features"],
                           "categoricals": spec["categoricals"], "categorical_columns": []}}
    if not model.get("can_score", True):
        raise ValueError(f"{model['name']} cannot score files here.")
    doc = _registry(db).find_one({"model_id": model["model_id"]})
    path = os.path.join(workdir, "model.joblib")
    with open(path, "wb") as fh:
        fh.write(_gridfs(db).get(doc["artifact_id"]).read())
    return {"code": doc["code"], "compat": False, "model_file": path, "config": doc["config"]}


def _score_with(db, model: dict, inputs: list, workdir: str, log) -> dict:
    m = _materialise(db, model, workdir)
    return _predict(workdir, m["code"], m["config"], inputs, log,
                    model_file=m["model_file"], compat=m["compat"])


def score_file(db, user: dict, target: str, upload_path: str) -> dict:
    model = active_model(db, user, target)
    if not model.get("can_score", True):
        raise ValueError(f"The active {target} model ({model['name']}) is rules-based and "
                         "scores inside weekly monitoring. Deploy a trained weekly model to "
                         "score files here.")
    df = _read_csv(upload_path)
    if len(df) > 100000:
        raise ValueError("Score at most 100,000 rows at a time.")
    features = comp_features(model)
    present = [c for c in features if c in df.columns]
    if not present:
        raise ValueError("None of the columns this model needs are in the file. It expects: "
                         + ", ".join(features[:15]) + ("…" if len(features) > 15 else ""))
    workdir = tempfile.mkdtemp(prefix="score_", dir=_ensure_root())
    try:
        inp = os.path.join(workdir, "input.csv")
        df.to_csv(inp, index=False)
        logs = []
        result = _score_with(db, model, [{"name": "upload", "path": inp,
                                          "out": os.path.join(workdir, "pred.csv")}],
                             workdir, logs.append)
        if not result["ok"]:
            raise RuntimeError("Scoring failed: " + (result["error"] or "")[:400])
        scores = _scores(os.path.join(workdir, "pred.csv")) * 100
    finally:
        shutil.rmtree(workdir, ignore_errors=True)

    bands = model.get("bands") or _mimic_bands()
    band = np.where(scores >= bands["high_score_threshold"], "High",
                    np.where(scores >= bands["low_score_threshold"], "Medium", "Low"))
    id_col = (model.get("data") or {}).get("id_column")
    ids = df[id_col].astype(str).values if id_col and id_col in df.columns else None
    rows = [{"row": i + 1, "id": None if ids is None else ids[i],
             "score": round(float(scores[i]), 1), "band": str(band[i])} for i in range(len(df))]
    return {
        "model": {k: model.get(k) for k in ("model_id", "name", "target", "algorithm")},
        "bands": bands, "rows": rows, "id_column": id_col if ids is not None else None,
        "summary": {b: int((band == b).sum()) for b in ("High", "Medium", "Low")},
        "missing_columns": [c for c in features if c not in df.columns],
    }


def _mimic_bands() -> dict:
    try:
        with open(os.path.join(_BASE, "docs", "mimic", "band_thresholds_mimic.json")) as fh:
            b = json.load(fh)
        return {"high_score_threshold": b["high_score_threshold"],
                "low_score_threshold": b["low_score_threshold"]}
    except (OSError, ValueError, KeyError):
        return {"high_score_threshold": 40.0, "low_score_threshold": 20.0}


def _ensure_root() -> str:
    os.makedirs(WORK_ROOT, exist_ok=True)
    return WORK_ROOT


def save_upload(fileobj, filename: str) -> str:
    """Stream an upload to disk, refusing anything over the size limit."""
    if not filename.lower().endswith(".csv"):
        raise ValueError("Only .csv files are accepted.")
    fd, path = tempfile.mkstemp(suffix=".csv", dir=_ensure_root())
    limit, total = MAX_UPLOAD_MB * 1024 * 1024, 0
    with os.fdopen(fd, "wb") as out:
        while True:
            chunk = fileobj.read(1024 * 1024)
            if not chunk:
                break
            total += len(chunk)
            if total > limit:
                out.close()
                os.remove(path)
                raise ValueError(f"The file is larger than {MAX_UPLOAD_MB} MB.")
            out.write(chunk)
    return path


def discard_job(job_id: str, user: dict) -> None:
    job = get_job(job_id, user)
    with _jobs_lock:
        _jobs.pop(job_id, None)
    shutil.rmtree(job["workdir"], ignore_errors=True)


def job_code(job_id: str, user: dict) -> str:
    return get_job(job_id, user).get("code") or ""


def model_code(db, model_id: str, user: dict) -> str:
    get_model(db, model_id, user)
    doc = _registry(db).find_one({"model_id": model_id}, {"code": 1})
    return (doc or {}).get("code") or ""


__all__ = ["create_job", "confirm_job", "get_job", "public_job", "list_jobs", "discard_job",
           "save_job_model", "list_models", "get_model", "deploy_model", "delete_model",
           "overview", "score_file", "save_upload", "job_code", "model_code"]

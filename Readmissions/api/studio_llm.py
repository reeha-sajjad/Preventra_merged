"""
Gemini's part in Model Studio: reading a dataset's profile, writing its
pipeline code, and explaining the verdict.

What Gemini is shown is the column profile only - names, types, missing rates,
numeric ranges and the values of low-cardinality columns - never a patient
row. The profile is built in api/model_studio.profile_dataframe, which is the
one place that decides what leaves the building.

What Gemini decides is advisory everywhere except the code, and the code is
checked (api/studio_guard) and sandboxed (api/studio_runner) before it runs.
The split, the metrics and the deploy recommendation are computed by our own
code; Gemini only puts the recommendation into words.
"""

from __future__ import annotations

import json
import os
import re
import time

from api.studio_templates import ALGORITHMS, STANDARD_PIPELINE

# Code writing benefits from a stronger model than the chatbot's routing; both
# are overridable. If the studio model is unavailable on this key the chatbot's
# model is used instead.
STUDIO_MODEL = os.environ.get("STUDIO_GEMINI_MODEL", "gemini-flash-latest")
FALLBACK_MODEL = os.environ.get("GEMINI_MODEL", "gemini-flash-lite-latest")
_TIMEOUT_MS = int(os.environ.get("STUDIO_GEMINI_TIMEOUT_MS", "180000"))

TARGETS = {
    "discharge": (
        "RISK AT DISCHARGE. One row per hospital stay, described at the moment of "
        "discharge. The outcome is whether the patient was readmitted within 30 days. "
        "Anything recorded after discharge (the readmission itself, later visits, "
        "follow-up results, length of a later stay) is leakage and must not be a feature."),
    "weekly": (
        "WEEKLY POST-DISCHARGE MONITORING. One row per patient per monitoring week after "
        "discharge (signals such as weight change, medication adherence, refills, "
        "follow-up attendance, vital signs). The outcome is readmission within the "
        "monitoring window. Rows of the same patient are not independent, so a patient "
        "identifier is required and splits must keep each patient's weeks together. "
        "Signals from weeks after the row's own week are leakage."),
}


def available() -> bool:
    return bool(os.environ.get("GEMINI_API_KEY"))


def _generate(prompt: str, system: str, json_mode: bool, notify=None) -> str:
    from google import genai
    from google.genai import types

    client = genai.Client(api_key=os.environ["GEMINI_API_KEY"],
                          http_options=types.HttpOptions(timeout=_TIMEOUT_MS))
    config = types.GenerateContentConfig(
        system_instruction=system, temperature=0.2,
        response_mime_type="application/json" if json_mode else "text/plain")
    # Overload (503) and rate limits (429) are routine and pass within seconds,
    # so each model gets a few tries with backoff; a missing or still-overloaded
    # model hands over to the other one. Anything else is a real error.
    last = None
    for model in dict.fromkeys((STUDIO_MODEL, FALLBACK_MODEL)):
        for wait in (0, 4, 10):
            time.sleep(wait)
            try:
                response = client.models.generate_content(model=model, contents=prompt,
                                                          config=config)
                return response.text or ""
            except Exception as exc:
                last, text = exc, str(exc)
                if "NOT_FOUND" in text or "404" in text:
                    break  # this model name is not available on the key; try the other
                if not any(code in text for code in ("503", "UNAVAILABLE", "429",
                                                     "RESOURCE_EXHAUSTED", "500", "INTERNAL")):
                    raise
                if notify:
                    notify(f"Gemini ({model}) is busy ({text[:40].strip()}…); retrying.")
    raise last


def _json(text: str):
    text = text.strip()
    fenced = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    if fenced:
        text = fenced.group(1)
    return json.loads(text)


# ---------------------------------------------------------------- 1. analysis
_ANALYSIS_SYSTEM = (
    "You are a senior clinical data scientist preparing a hospital dataset for a "
    "30-day readmission risk model. You see a column profile, never the rows. "
    "Decide each column's role conservatively: when a column could leak the outcome, "
    "say so. Answer with JSON only, exactly in the requested shape, using column "
    "names verbatim from the profile.")

_ANALYSIS_SHAPE = """{
  "summary": "2-3 sentences: what this dataset appears to be and how usable it is",
  "label_column": "the outcome column (readmitted or not)",
  "positive_value": "the label value that means readmitted, as text, or null if 1/true",
  "id_column": "patient identifier column, or null",
  "time_column": "date/time column usable to split oldest-to-newest, or null",
  "numeric_columns": ["feature columns to treat as numbers"],
  "categorical_columns": ["feature columns to treat as categories"],
  "drop_columns": [{"column": "...", "reason": "identifier / free text / leakage / constant / ..."}],
  "leakage_suspects": [{"column": "...", "reason": "why it may encode the outcome"}],
  "sensitive_columns": ["demographic columns to check performance fairness on, e.g. sex, age, race, insurance"],
  "data_issues": ["short findings: heavy missingness, odd ranges, too few events, ..."],
  "recommended_algorithms": ["logistic_regression | random_forest | gradient_boosting"]
}"""


def analyze_dataset(profile: dict, target: str, base: dict | None = None, notify=None) -> dict:
    prompt = (
        f"TASK TYPE: {TARGETS[target]}\n\n"
        f"COLUMN PROFILE (JSON):\n{json.dumps(profile, default=str)}\n\n")
    if base:
        prompt += ("This is a RETRAIN of an existing model. Its previous configuration was:\n"
                   f"{json.dumps(base, default=str)}\n"
                   "Keep the same label, identifier and features where the new data has them.\n\n")
    prompt += ("Every column in the profile must appear in exactly one of: label_column, "
               "id_column, time_column, numeric_columns, categorical_columns, drop_columns. "
               f"Return JSON in this shape:\n{_ANALYSIS_SHAPE}")
    return _json(_generate(prompt, _ANALYSIS_SYSTEM, json_mode=True, notify=notify))


# --------------------------------------------------------- 2. pipeline code
_CODE_SYSTEM = (
    "You write production-quality scikit-learn pipelines for clinical risk models. "
    "You return exactly one Python code block containing a complete module and nothing else.")

_CONTRACT = """THE CONTRACT - the module MUST define these three top-level functions:

def preprocess(df: pd.DataFrame, config: dict) -> pd.DataFrame
    Clean raw columns (types, units, invalid or sentinel values to NaN, text tidy-up).
    Must return the SAME number of rows in the SAME order. Stateless: it must not
    compute any statistic from df (no means, medians, value counts, quantiles),
    because it runs again, row-for-row, when new patients are scored.

def transform(df: pd.DataFrame, config: dict) -> pd.DataFrame
    Feature engineering from the cleaned columns: ratios, flags, buckets, date parts,
    clinically meaningful combinations. Same rules: same rows, same order, stateless,
    and the label column must never appear in the output. Numeric features must be
    numeric dtype; categorical features object dtype.

def train(X: pd.DataFrame, y: pd.Series, groups, config: dict) -> tuple
    Model selection and fitting on the transformed features. y is 0/1 (1 = readmitted).
    groups is a pd.Series of patient ids or None; when it is not None, every
    cross-validation must keep a patient's rows in one fold (StratifiedGroupKFold).
    Compare the candidate algorithms by cross-validated ROC AUC, refit the best on all
    of X, and wrap it in CalibratedClassifierCV (isotonic with >= 1000 positives, else
    sigmoid) so its output reads as a probability. When groups is not None, give
    CalibratedClassifierCV cv=list(StratifiedGroupKFold(...).split(X, y, groups)) so
    calibration is also fitted on patients it was not trained on. Everything learned from data
    (imputation, scaling, encoding, rare-category grouping) must live inside the
    returned estimator (Pipeline + ColumnTransformer). Return (estimator, report)
    where report is a JSON-serialisable dict with at least "algorithm", "cv_auroc"
    and "notes".

RULES
- Allowed imports: pandas, numpy, sklearn (not sklearn.datasets), scipy, math, re,
  datetime, warnings, collections, itertools, functools, typing, statistics.
  Nothing else: no os, sys, pathlib, pickle, joblib, requests, subprocess.
- No file or network access of any kind: no open(), no pd.read_*, no .to_csv/.to_*,
  no np.load/np.save. The runner passes data in and saves the model itself.
- No eval/exec/getattr/setattr/globals, no dunder attribute access except __init__.
- Must handle missing values and categories never seen in training.
- Must finish in under 10 minutes on the stated row count with 2 CPU cores: cap
  cross-validation work (e.g. sample at most 60,000 rows for model selection,
  3 folds, modest n_estimators).
- Use random_state=0 everywhere for reproducibility.
- Write clear docstrings: a reviewer reads this code before it is deployed.

config contains: label_column, feature_columns (the raw input columns you receive),
numeric_columns, categorical_columns, algorithm_preference, target, n_rows, n_positive.
"""


def generate_pipeline(profile: dict, config: dict, target: str, *,
                      base_code: str | None = None, base_card: dict | None = None,
                      previous_code: str | None = None, error: str | None = None,
                      notify=None) -> str:
    preference = config.get("algorithm_preference", "auto")
    prompt = (
        f"TASK TYPE: {TARGETS[target]}\n\n"
        f"COLUMN PROFILE (JSON, of the raw input columns you will receive):\n"
        f"{json.dumps({k: v for k, v in profile.get('columns', {}).items() if k in config['feature_columns']}, default=str)}\n\n"
        f"CONFIG:\n{json.dumps({k: config[k] for k in config if k != 'categoricals'}, default=str)}\n\n"
        f"ALGORITHM PREFERENCE: {preference} - {ALGORITHMS.get(preference, preference)}. "
        + ("Compare at least two suitable candidates and keep the best."
           if preference == "auto" else "Use this algorithm family only; tune it sensibly.")
        + f"\n\n{_CONTRACT}\n"
        f"REFERENCE IMPLEMENTATION (follows the contract; improve on it for THIS data - "
        f"engineer features the profile suggests, choose encodings per column):\n"
        f"```python\n{STANDARD_PIPELINE}\n```\n")
    if base_code:
        prompt += ("\nThis is a RETRAIN of an existing model. Start from its code and keep its "
                   "feature contract where the new columns allow; improve what its metrics "
                   f"suggest.\nIts model card: {json.dumps(base_card or {}, default=str)}\n"
                   f"Its code:\n```python\n{base_code}\n```\n")
    if previous_code and error:
        prompt += ("\nYOUR PREVIOUS ATTEMPT FAILED. Fix the cause and return the whole corrected "
                   f"module.\nError:\n{error[-3000:]}\n\nPrevious code:\n```python\n"
                   f"{previous_code}\n```\n")
    text = _generate(prompt, _CODE_SYSTEM, json_mode=False, notify=notify)
    block = re.search(r"```(?:python)?\s*(.*?)```", text, re.S)
    return (block.group(1) if block else text).strip() + "\n"


# ------------------------------------------------------ 3. verdict in words
_VERDICT_SYSTEM = (
    "You explain machine-learning evaluation results to hospital administrators and "
    "clinicians who are not data scientists. Be precise and brief. Use only the numbers "
    "provided; never invent a figure. Answer with JSON only.")


def explain_verdict(summary: dict, notify=None) -> list:
    prompt = (
        "A readmission risk model was trained and evaluated on a held-out test set. The "
        "deployment recommendation below was decided by fixed rules; explain it, do not "
        "change it.\n\n"
        f"{json.dumps(summary, default=str)}\n\n"
        'Return {"bullets": ["4 to 6 short plain-English sentences: what the model achieves, '
        "how it compares with the alternatives, the main risks or caveats, and what to do "
        'next. Count rows in the given row_unit; do not call them patients unless they '
        'are."]}')
    data = _json(_generate(prompt, _VERDICT_SYSTEM, json_mode=True, notify=notify))
    bullets = data.get("bullets") if isinstance(data, dict) else data
    return [str(b) for b in (bullets or [])][:6]

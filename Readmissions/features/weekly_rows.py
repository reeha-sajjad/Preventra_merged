"""
Weekly monitoring rows, flattened the same way for training and for scoring.

scripts/export_weekly_training_csv.py writes a Model Studio training file with
flatten(); the ingestion worker builds the rows a deployed weekly model scores
with the same function, so a weekly model never sees a column made differently
from the one it learned.
"""
from __future__ import annotations

import math
from typing import Optional

import pandas as pd

from models.discharge_baseline import relative_observations

# How a row was produced, not anything about the patient.
BOOKKEEPING = ("model_version", "scored_by", "source", "pinned_by", "group_evidence", "group_label",
               "hospital_id", "encounter_key", "rule_adjustment", "responder", "received_at")
# A week's own scoring output, and free text. Left out of a weekly model's
# rows: once a weekly model is deployed, a stored week's risk_score is that
# model's score, so training on it would teach the next model its
# predecessor's answers.
WEEK_OUTPUTS = ("risk_score", "risk_band", "driver_1", "driver_2", "driver_3", "observed", "note",
                "group_confidence")


def flatten(docs: list) -> pd.DataFrame:
    """weekly_monitoring documents -> one row each, nested fields as columns:

        monitoring.*          -> weight_change_kg, adherence_pct, refill_status, ...
        discharge_baseline.*  -> baseline_dry_weight_kg, baseline_spo2, ... (week 0 row)
        red_flags (a list)    -> n_red_flags and red_flags ("; "-joined)
    """
    df = pd.DataFrame(docs)

    def nested(col, prefix=""):
        values = df[col] if col in df.columns else pd.Series([None] * len(df))
        part = pd.json_normalize(values.apply(lambda v: v if isinstance(v, dict) else {}).tolist())
        return part.rename(columns=lambda c: f"{prefix}{c}")

    signals = nested("monitoring")
    baseline = nested("discharge_baseline", "baseline_").drop(
        columns=["baseline_source", "baseline_group"], errors="ignore")
    baseline = baseline.rename(columns=lambda c: c.replace("baseline_baseline_", "baseline_"))
    flags = df.get("red_flags", pd.Series([[]] * len(df))).apply(
        lambda v: v if isinstance(v, list) else [])
    groups = df.get("monitoring_groups", pd.Series([[]] * len(df))).apply(
        lambda v: ",".join(map(str, v)) if isinstance(v, list) else "")

    flat = df.drop(columns=["monitoring", "discharge_baseline", "red_flags", "monitoring_groups",
                            *BOOKKEEPING], errors="ignore")
    flat = pd.concat([flat.reset_index(drop=True), signals, baseline], axis=1)
    flat["n_red_flags"] = flags.apply(len).values
    flat["red_flags"] = flags.apply(lambda v: "; ".join(map(str, v))).values
    flat["monitoring_groups"] = groups.values
    return flat


def week_number(discharge_date, week_date) -> int:
    """Which monitoring week a reading belongs to: days since discharge, in
    whole weeks rounded up, so days 1-7 are week 1. Never 0 - week 0 is the
    discharge itself."""
    days = (pd.Timestamp(week_date) - pd.Timestamp(discharge_date).normalize()).days
    return max(1, math.ceil(days / 7))


def to_observations(readings: dict, baseline: Optional[dict]) -> dict:
    """A week's readings as sent -> the observation dict the monitoring rules
    score. Absolute weight and walking distance become changes against the
    patient's discharge baseline (models/discharge_baseline.relative_observations);
    without a baseline they are left out rather than compared with a
    population figure."""
    raw = {k: v for k, v in (readings or {}).items() if v is not None}
    if "weight_kg" in raw:                       # the request schema's name for it
        raw["current_weight_kg"] = raw.pop("weight_kg")
    obs = relative_observations(raw, baseline)
    for absolute in ("current_weight_kg", "walk_metres", "spo2_drop"):
        obs.pop(absolute, None)
    return obs


def model_rows(docs: list, stay: dict) -> pd.DataFrame:
    """One stay's series (week 0 first, as weekly_monitoring documents) -> the
    rows a weekly model trains on and scores: the flattened weeks without each
    week's own score, plus the discharge context - the discharge score, age
    and sex. scripts/export_weekly_training_csv.py --hospital and the worker
    (api/scoring_service) both build rows here, so they never differ."""
    week0 = next((d for d in docs if d.get("week_number") == 0), {})
    flat = flatten(docs)
    flat = flat.drop(columns=[c for c in WEEK_OUTPUTS if c in flat.columns])
    flat["discharge_score"] = week0.get("risk_score")
    flat["age_years"] = (stay.get("encounter") or {}).get("age_years")
    flat["sex"] = (stay.get("patient") or {}).get("sex")
    return flat


def readmitted_within(week_dates, readmit_at, days: int = 7, as_of=None) -> pd.Series:
    """For each week: 1 if the patient was readmitted in the `days` after it,
    0 if not, unknown (NaN) when those days run past `as_of` with no
    readmission yet, or when the patient was already back in hospital."""
    dates = pd.to_datetime(pd.Series(list(week_dates)), errors="coerce")
    end = dates + pd.Timedelta(days=days)
    if readmit_at is None or pd.isna(readmit_at):
        out = pd.Series(0.0, index=dates.index)
    else:
        at = pd.Timestamp(readmit_at)
        out = ((at > dates) & (at <= end)).astype(float)
        out = out.where(at > dates)                      # already readmitted: not a monitoring week
    if as_of is not None:
        out = out.where(~(out.eq(0) & (end > pd.Timestamp(as_of))))
    return out

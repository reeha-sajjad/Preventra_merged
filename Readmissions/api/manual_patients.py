"""
Score a patient who is not on the list yet - typed in, or uploaded as a CSV -
and, for the roles allowed to, add them to it.

Two different acts, kept apart on purpose:

    score(record)   calculates a risk score and drivers and writes NOTHING. The
                    clinical roles may do it; it is how a team answers "what
                    would this patient score?" before deciding anything.
    add(...)        files the patient on the hospital's list, scored by the
                    server (never by a number the browser sends), with the same
                    rows a discharge from the ingest pipeline gets, so they
                    behave like every other patient afterwards.

These patients are external: the id is generated here (MAN-XXXXXXXX) and any
reference the hospital uses for them is kept beside it, never as the id.
"""
from __future__ import annotations

import csv
import io
import math
import secrets
from datetime import datetime
from typing import Optional

from api import access, mimic_scoring
from ingestion.scheduler import today as hospital_today
from models.icd_groups import classify, classify_all

MAX_ROWS = 500
MAX_BYTES = 1_000_000
SOURCE = "manual"

# Not part of the model's inputs: who the patient is, what they were treated
# for, and the vitals they went home with (the baseline weekly readings are
# measured against). charlson_score is never accepted - it is derived.
IDENTITY_FIELDS = [
    ("external_ref", "text", "Your own reference for this patient (kept, never used as the id)"),
    ("admit_date", "date", "Admission date, YYYY-MM-DD"),
    ("discharge_date", "date", "Discharge date, YYYY-MM-DD (needed to add the patient)"),
    ("primary_icd_code", "text", "Principal diagnosis ICD code, e.g. I50.9"),
    ("primary_diagnosis", "text", "Principal diagnosis in words (needed to add, unless a code is given)"),
    ("secondary_diagnoses", "list", "Other diagnoses, separated by ;"),
    ("weight_kg", "number", "Weight at discharge, kg"),
    ("spo2", "number", "Oxygen saturation at discharge, %"),
    ("sbp", "number", "Systolic blood pressure at discharge, mmHg"),
    ("heart_rate", "number", "Heart rate at discharge, beats per minute"),
]
IDENTITY_NAMES = [n for n, _, _ in IDENTITY_FIELDS]
VITAL_FIELDS = {"weight_kg": "dry_weight_kg", "spo2": "baseline_spo2",
                "sbp": "baseline_sbp", "heart_rate": "baseline_hr"}
RANGES = {"anchor_age": (0, 120), "spo2": (50, 100), "sbp": (40, 300), "heart_rate": (20, 300),
          "weight_kg": (1, 500), "los_days": (0, 1000)}
NON_NEGATIVE = ("n_prior_adm", "n_procedures", "n_diagnoses", "n_drug_orders", "n_distinct_drugs",
                "ed_hours", "days_since_prev", "drg_severity", "drg_mortality")
TRUE, FALSE = {"1", "true", "yes", "y"}, {"0", "false", "no", "n"}


class ScoringUnavailable(Exception):
    """The model could not be loaded or run."""


# --------------------------------------------------------------- the columns
def model_fields() -> list:
    """(name, kind) of every model input a person can supply, in form order."""
    return ([(c, "category") for c in mimic_scoring.ENTERABLE_CATEGORICAL]
            + [(n, "number") for n in mimic_scoring.ENTERABLE_NUMERIC]
            + [(f, "flag") for f in mimic_scoring.ENTERABLE_FLAGS]
            + [(c, "flag") for c in mimic_scoring.CHARLSON_FLAGS]
            + [(lab, "number") for lab in mimic_scoring.ENTERABLE_LABS])


def categories() -> dict:
    """Valid values of each categorical input. Empty when the model cannot be
    read - scoring reports that itself, and a category is then not checked."""
    try:
        return mimic_scoring.manual_entry_schema().get("categorical", {})
    except Exception:
        return {}


def template() -> dict:
    cats = categories()
    columns = [{"name": n, "kind": k, "help": h} for n, k, h in IDENTITY_FIELDS]
    for name, kind in model_fields():
        col = {"name": name, "kind": kind,
               "help": {"number": "A number", "flag": "1 or 0 (yes or no)",
                        "category": "One of the listed options"}[kind]}
        if kind == "category":
            col["options"] = cats.get(name, [])
        columns.append(col)
    return {
        "columns": columns,
        "example": {"external_ref": "EXT-001", "admit_date": "2026-01-10", "discharge_date": "2026-01-15",
                    "primary_icd_code": "I50.9", "primary_diagnosis": "Heart failure, unspecified",
                    "secondary_diagnoses": "Type 2 diabetes; Hypertension", "weight_kg": "82",
                    "spo2": "94", "sbp": "118", "heart_rate": "76", "anchor_age": "71",
                    "los_days": "5", "n_prior_adm": "2", "congestive_heart_failure": "1"},
        "notes": [f"One patient per row, 1 to {MAX_ROWS} rows. Leave a cell empty when it is unknown - "
                  "that is scored as unknown, not as zero.",
                  "Dates are YYYY-MM-DD and cannot be in the future. Separate several secondary diagnoses with ;",
                  "To add a patient to the list a row needs a discharge date and a diagnosis (code or words)."],
    }


# -------------------------------------------------------------- validation
def _date(value) -> Optional[datetime]:
    try:
        return datetime.strptime(str(value)[:10], "%Y-%m-%d") if len(str(value)) == 10 else None
    except ValueError:
        return None


def check(record: dict, cats: Optional[dict] = None) -> list:
    """Why this record cannot be scored, in words a person can act on."""
    cats = categories() if cats is None else cats
    errors = []
    dates = {}
    for field in ("admit_date", "discharge_date"):
        value = record.get(field)
        if value in (None, ""):
            continue
        when = _date(value)
        if when is None:
            errors.append(f"{field} must be a date written YYYY-MM-DD")
        elif when.date() > hospital_today():
            errors.append(f"{field} cannot be in the future")
        else:
            dates[field] = when
    if len(dates) == 2 and dates["admit_date"] > dates["discharge_date"]:
        errors.append("admit_date is after discharge_date")
    for name, kind in model_fields():
        value = record.get(name)
        if value is None or value == "":
            continue
        if kind == "category" and cats.get(name) and value not in cats[name]:
            errors.append(f"{name} must be one of: {', '.join(map(str, cats[name]))}")
    for name, value in record.items():
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            continue
        if not math.isfinite(value):
            errors.append(f"{name} must be a finite number")
        elif name in RANGES and not RANGES[name][0] <= value <= RANGES[name][1]:
            errors.append(f"{name} must be between {RANGES[name][0]} and {RANGES[name][1]}")
        elif name in NON_NEGATIVE and value < 0:
            errors.append(f"{name} cannot be negative")
    return errors


def add_errors(record: dict) -> list:
    """What a record still needs before it can be ADDED to the list."""
    errors = []
    if not record.get("discharge_date"):
        errors.append("a discharge date is needed to add the patient")
    if not (record.get("primary_icd_code") or record.get("primary_diagnosis")):
        errors.append("a diagnosis (code or words) is needed to add the patient")
    return errors


# --------------------------------------------------------------------- CSV
def _cell(name: str, kind: str, raw: str, cats: dict):
    """(value, error) for one non-empty cell."""
    text = raw.strip()
    if kind == "number":
        try:
            number = float(text)
        except ValueError:
            return None, f"{name} must be a number"
        return (number, None) if math.isfinite(number) else (None, f"{name} must be a number")
    if kind == "flag":
        if text.lower() in TRUE:
            return True, None
        if text.lower() in FALSE:
            return False, None
        return None, f"{name} must be 1 or 0 (or yes / no)"
    if kind == "category":
        options = cats.get(name) or []
        if not options:
            return text, None
        match = next((o for o in options if str(o).lower() == text.lower()), None)
        return (match, None) if match is not None else (
            None, f"{name} must be one of: {', '.join(map(str, options))}")
    if kind == "list":
        return [p.strip() for p in text.split(";") if p.strip()], None
    return text, None


def parse_csv(content: bytes, max_rows: int = MAX_ROWS, max_bytes: int = MAX_BYTES) -> dict:
    """Read an uploaded CSV into records. Raises ValueError for a file that
    cannot be used at all; a bad row is reported on the row, not fatal."""
    if len(content) > max_bytes:
        raise ValueError(f"The file is larger than {max_bytes // 1_000_000} MB")
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise ValueError("The file is not a CSV text file (expected UTF-8)")
    try:
        reader = csv.DictReader(io.StringIO(text))
        rows = list(reader)
        header = [h.strip() for h in (reader.fieldnames or [])]
    except csv.Error:
        raise ValueError("The file could not be read as a CSV")
    if not header or len(header) < 2:
        raise ValueError("The file has no header row of column names")
    if not rows:
        raise ValueError("The file has no patient rows")
    if len(rows) > max_rows:
        raise ValueError(f"The file has {len(rows)} rows; at most {max_rows} at a time")

    cats = categories()
    kinds = {n: k for n, k, _ in IDENTITY_FIELDS}
    kinds.update(dict(model_fields()))
    unknown = [h for h in header if h and h not in kinds]
    out = []
    for number, raw_row in enumerate(rows, start=1):
        row = {(k or "").strip(): (v or "") for k, v in raw_row.items() if k is not None}
        record, errors = {}, []
        for name, kind in kinds.items():
            raw = row.get(name, "")
            if raw.strip() == "":
                continue
            value, problem = _cell(name, kind, raw, cats)
            if problem:
                errors.append(problem)
            elif value not in (None, []):
                record[name] = value
        if not record and not errors:
            continue                                  # a blank line in the file
        out.append({"row": number, "record": record, "errors": errors})
    if not out:
        raise ValueError("The file has no patient rows")
    return {"rows": out, "unknown_columns": unknown}


# ----------------------------------------------------------------- scoring
def _model_inputs(record: dict) -> dict:
    """Only what the model reads: identity fields are not inputs, and a flag
    typed as a boolean reaches the model as the 1 / 0 it was trained on."""
    skip, boxes = set(IDENTITY_NAMES), set(mimic_scoring.CHARLSON_FLAGS)
    return {k: (int(v) if isinstance(v, bool) and k not in boxes else v)
            for k, v in record.items() if k not in skip and v is not None and v != ""}


def score(record: dict) -> dict:
    """Risk score, band and top drivers for one record. Writes nothing."""
    try:
        scored = mimic_scoring.score_manual_entry(_model_inputs(record))
    except FileNotFoundError as exc:
        raise ScoringUnavailable(f"Model artefacts missing: {exc}")
    except Exception as exc:
        raise ScoringUnavailable(f"Scoring unavailable: {exc}")
    raw = [scored.get(f"driver_{i}_raw", "") for i in (1, 2, 3)]
    drivers = ([mimic_scoring._split_driver(d) for d in raw if d]
               or list(scored.get("drivers") or []))
    return {"risk_score": scored["risk_score"], "risk_band": scored["risk_band"],
            "drivers": drivers, "driver_1": raw[0], "driver_2": raw[1], "driver_3": raw[2]}


# ------------------------------------------------------------------- adding
def _code(value) -> str:
    return str(value or "").replace(".", "").strip().upper()


def _new_id(db) -> str:
    while True:
        pid = f"MAN-{secrets.token_hex(4).upper()}"
        if not (db["care_actions"].find_one({"patient_id": pid}, {"_id": 1})
                or db["patient_worklist"].find_one({"patient_id": pid}, {"_id": 1})):
            return pid


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def add(db, user: dict, record: dict, hospital_id: str, scored: Optional[dict] = None) -> dict:
    """File one patient on the hospital's list, scored here, and return
    {patient_id, risk_score, risk_band}. The rows written are the ones a
    discharge from the ingest pipeline gets (api/scoring_service._write_discharge),
    so the patient shows up, trends and is counted like any other. `scored` is
    a result already calculated by score(record); with it, nothing here can
    reach the model."""
    from api import scoring_service as ss
    from api.worklist_summary import refresh

    problems = add_errors(record) + check(record)
    if problems:
        raise ValueError("; ".join(problems))
    result = scored if scored is not None else score(record)
    pid, batch, now = _new_id(db), ss._batch(db), _now()

    code = _code(record.get("primary_icd_code"))
    title = (record.get("primary_diagnosis") or "").strip() or code
    secondary = [s for s in (record.get("secondary_diagnoses") or []) if s]
    grp = classify(code, title, secondary, [])
    matches = classify_all(code, title, secondary, [])
    score_value, band = round(float(result["risk_score"]), 1), result["risk_band"]
    discharge = str(record["discharge_date"])[:10]

    inputs = _model_inputs(record)
    age = record.get("anchor_age")
    common = {"patient_id": pid, "risk_score": score_value, "risk_band": band,
              "driver_1": result["driver_1"], "driver_2": result["driver_2"], "driver_3": result["driver_3"],
              "admit_date": str(record.get("admit_date") or "")[:10], "discharge_date": discharge,
              "primary_diagnosis": title, "primary_icd_code": code,
              "n_diagnoses_coded": (1 if code else 0) + len(secondary),
              "los_days": record.get("los_days"), "n_prior_adm": record.get("n_prior_adm"),
              "model_version": ss.BUILTIN_VERSION, "hospital_id": hospital_id,
              "encounter_key": pid, "source": SOURCE}

    db["patient_worklist"].update_one(
        {"patient_id": pid, "batch_date": batch},
        {"$set": {**common, "batch_date": batch, "secondary_diagnoses": secondary,
                  "anchor_age": int(round(age)) if age is not None else None,
                  "gender": record.get("gender"),
                  "clinical_group": grp["group"], "group_label": grp["label"],
                  "group_evidence": grp["evidence"], "group_confidence": grp["confidence"],
                  "clinical_groups": [m["group"] for m in matches] or ["general"],
                  "group_matches": matches, "stage": "final",
                  "raw_inputs": {**inputs, "discharge_date": discharge},
                  "external_ref": (record.get("external_ref") or "").strip() or None,
                  "added_by": str(user.get("email") or user.get("_id") or ""),
                  "scored_at": now}},
        upsert=True)
    db["risk_registry"].update_one(
        {"hospital_id": hospital_id, "encounter_key": pid, "model_version": ss.BUILTIN_VERSION,
         "stage": "final"},
        {"$set": {**common, "batch_date": discharge, "stage": "final", "scored_at": now}}, upsert=True)
    made = db["care_actions"].update_one(
        {"patient_id": pid},
        {"$setOnInsert": {"patient_id": pid, "hospital_id": hospital_id, "notes": [],
                          "coordinator_name": None, "assigned_at": None, "assigned_nurse_ids": []}},
        upsert=True)
    if made.upserted_id is not None:
        access.forget_hospital_lists()

    # Vitals typed in are the patient's own baseline. With none, say it is
    # recorded-but-empty, so the trend page does not invent a simulated one.
    vitals = {to: record[frm] for frm, to in VITAL_FIELDS.items() if record.get(frm) is not None}
    db["weekly_monitoring"].update_one(
        {"patient_id": pid, "week_number": 0},
        {"$set": {"patient_id": pid, "week_number": 0, "days_after_discharge": 0, "week_date": discharge,
                  "risk_score": score_value, "risk_band": band, "observed": True,
                  "driver_1": result["driver_1"], "driver_2": result["driver_2"],
                  "driver_3": result["driver_3"], "model_version": ss.BUILTIN_VERSION, "source": SOURCE,
                  "scored_by": "model", "clinical_group": grp["group"], "group_label": grp["label"],
                  "group_evidence": grp["evidence"], "group_confidence": grp["confidence"],
                  "discharge_baseline": {**vitals, "source": "recorded", "group": grp["group"]},
                  "red_flags": [], "hospital_id": hospital_id, "encounter_key": pid}},
        upsert=True)

    ss._write_summary(db, batch)
    refresh(db, batch, [pid])
    return {"patient_id": pid, "risk_score": score_value, "risk_band": band}

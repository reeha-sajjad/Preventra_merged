"""
Score ingested discharges and weekly check-ins, and write them where the
dashboard already reads them. Called by the ingestion worker
(ingestion/worker.py) for each queued job.

Discharges
----------
The 94 inputs are built from ALL of the patient's stored stays
(ingestion/canonical.py), because earlier admissions are inputs. They are
scored by the model deployed for the hospital in Model Studio, or by the
built-in model until it has one. A Model Studio model runs only in the
sandbox runner (model_studio._score_with), exactly as the Score tab does; this
process never loads a generated model. If the deployed model needs inputs the
row does not have, the built-in model scores it and the job says why.

Then, per stay:

    patient_worklist    the patient's row - their LATEST discharge - in the
                        current batch (the dashboard reads only the newest
                        batch_date, so a new date would hide everyone else)
    risk_registry       every scored discharge, by model and stage
    care_actions        ownership by the hospital, so its staff see the patient
    weekly_monitoring   week 0: the discharge score, its drivers and the
                        discharge vitals as the patient's baseline

Weekly check-ins
----------------
Scored by the monitoring rules (models/monitoring_rules.py) against the
discharge score, or by a deployed weekly model. The rules always run: their
ranked readings are the card's lines and their red flags still apply.
"""
from __future__ import annotations

import os
import shutil
import tempfile
from datetime import datetime
from typing import Optional

import numpy as np
import pandas as pd
from pymongo import UpdateOne

from api import model_studio
from api.db_utils import get_latest_batch_date
from api.worklist_summary import refresh as refresh_summaries
from features.weekly_rows import model_rows, to_observations
from ingestion import canonical, schema
from models.icd_groups import classify, classify_all, monitoring_groups
from models.monitoring_rules import CARRY, red_flags, score_week, variant_for

STAYS, WEEKS = "ingest_stays", "ingest_weeks"
BUILTIN_VERSION = "mimic-hgb-calibrated-v1"
SOURCE = "ingest"
MIN_COVERAGE = 0.6        # share of a model's inputs a row must have, as in Model Studio


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _day(value) -> str:
    return pd.Timestamp(value).strftime("%Y-%m-%d") if value is not None and not pd.isna(value) else ""


def _number(value, shape):
    """A feature value for the dashboard, or None when it is unknown (a stay
    without dates has no length of stay)."""
    return None if value is None or pd.isna(value) else shape(float(value))


def _band(score: float, bands: dict) -> str:
    if score >= bands["high_score_threshold"]:
        return "High"
    if score >= bands["low_score_threshold"]:
        return "Medium"
    return "Low"


def _batch(db) -> str:
    """The batch new rows join: the current one (decision 3 of the build plan)."""
    return get_latest_batch_date(db, "patient_worklist") or datetime.now().strftime("%Y-%m-%d")


# ------------------------------------------------------------------ models
def _builtin_scores(rows: pd.DataFrame) -> list:
    """The built-in model with real SHAP drivers, one row at a time - the same
    call as manual entry (api/mimic_scoring.score_frame)."""
    from api import mimic_scoring
    state = mimic_scoring._load()
    out = []
    for _, row in rows.iterrows():
        frame = pd.DataFrame([row[state["features"]].to_dict()])
        for col in state["features"]:
            levels = state["categoricals"].get(col)
            frame[col] = (pd.Categorical(frame[col].astype(object), categories=levels) if levels
                          else pd.to_numeric(frame[col], errors="coerce"))
        scored = mimic_scoring.score_frame(frame)
        out.append({"score": scored["risk_score"],
                    "drivers": [scored.get(f"driver_{i}_raw", "") for i in (1, 2, 3)]})
    return out


def _studio_scores(db, model: dict, frame: pd.DataFrame, targets: pd.Series) -> list:
    """A deployed Model Studio model, in the sandbox runner. `frame` holds every
    stay of the affected patients (a history model needs the earlier ones);
    `targets` marks the rows to score and explain."""
    from models.studio_drivers import format_studio_driver
    framed = model_studio.with_context(model, frame).reset_index(drop=True)
    framed[model_studio.EXPLAIN_FIELD] = targets.values.astype(int)
    workdir = tempfile.mkdtemp(prefix="ingest_", dir=model_studio._ensure_root())
    try:
        inp = os.path.join(workdir, "input.csv")
        framed.to_csv(inp, index=False)
        item = {"name": "ingest", "path": inp, "out": os.path.join(workdir, "pred.csv"),
                "explain_out": os.path.join(workdir, "explain.json")}
        result = model_studio._score_with(db, model, [item], workdir, lambda m: None)
        if not result["ok"]:
            raise RuntimeError("Model Studio model could not score: " + (result["error"] or "")[:300])
        scores = model_studio._scores(item["out"]) * 100
        explained = model_studio.read_explanations(item["explain_out"])
    finally:
        shutil.rmtree(workdir, ignore_errors=True)
    out = []
    for pos in np.flatnonzero(targets.values):
        lines = [format_studio_driver(e["feature"], e["value"], e["delta"], e.get("lowers", False))
                 for e in explained.get(int(pos), [])]
        out.append({"score": round(float(scores[pos]), 1), "drivers": (lines + ["", "", ""])[:3]})
    return out


def deployed_fingerprint(db, hospital_id: str) -> str:
    """Which discharge and weekly models are deployed for the hospital now.
    Every worklist row records the fingerprint it was scored under
    (`scored_for`); a row whose fingerprint differs was scored before the
    latest deployment and is due a re-score. Recorded even when the deployed
    model could not be used and the built-in one stood in, so such a row is
    not re-scored every night for nothing."""
    discharge = model_studio.active_model_for(db, hospital_id, "discharge")
    weekly = model_studio.active_model_for(db, hospital_id, "weekly")
    return (f"{discharge['model_id'] if discharge else model_studio.BUILTIN_MIMIC}|"
            f"{weekly['model_id'] if weekly else model_studio.BUILTIN_RULES}")


def _ingested(hospital_id: str) -> dict:
    return {"hospital_id": hospital_id, "source": SOURCE}


def waiting_for_a_model(db, hospital_id: str) -> list:
    """The latest discharge of each current patient that was stored but never
    scored - no model could read it yet. Without this they were stranded: a
    re-score only looked at patients already on the worklist, so deploying the
    model that could score them changed nothing. Past discharges sent as
    history, and stays that are never scored (died in hospital, hospice,
    observation), are not waiting."""
    on_list = set(db["patient_worklist"].distinct("patient_id", _ingested(hospital_id)))
    latest: dict = {}
    for s in db[STAYS].find({"hospital_id": hospital_id, "history": {"$ne": True}},
                            {"_id": 0, "patient_id": 1, "encounter_key": 1, "received_at": 1,
                             "encounter.discharge_datetime": 1, "encounter.died_in_hospital": 1,
                             "encounter.discharge_disposition": 1, "encounter.admission_type": 1,
                             "encounter.observation_stay": 1}):
        enc = s.get("encounter") or {}
        if (s.get("patient_id") in on_list or enc.get("died_in_hospital") or enc.get("observation_stay")
                or enc.get("discharge_disposition") in ("DIED", "HOSPICE")
                or enc.get("admission_type") == "OBSERVATION"):
            continue
        when = enc.get("discharge_datetime") or s.get("received_at") or datetime.min
        if s["patient_id"] not in latest or when >= latest[s["patient_id"]][0]:
            latest[s["patient_id"]] = (when, str(s["encounter_key"]))
    return [key for _, key in latest.values()]


def due_for_rescore(db, hospital_id: str) -> list:
    """The latest discharge of every ingested patient of this hospital whose
    worklist row was scored under other models than the ones deployed now, and
    of every current patient still waiting for a model that can read them."""
    fingerprint = deployed_fingerprint(db, hospital_id)
    rows = db["patient_worklist"].find({**_ingested(hospital_id), "scored_for": {"$ne": fingerprint}},
                                       {"encounter_key": 1})
    keys = [str(r["encounter_key"]) for r in rows if r.get("encounter_key")]
    return list(dict.fromkeys(keys + waiting_for_a_model(db, hospital_id)))


def rescore_status(db, hospital_id: str) -> dict:
    """For the Data page: what is deployed, and how many patients wait for it."""
    models = {}
    for target in ("discharge", "weekly"):
        m = model_studio.active_model_for(db, hospital_id, target)
        models[target] = {"model_id": m["model_id"], "name": m.get("name"), "builtin": bool(m.get("builtin"))} \
            if m else None
    fingerprint = deployed_fingerprint(db, hospital_id)
    return {"models": models,
            "patients": db["patient_worklist"].count_documents(_ingested(hospital_id)),
            "due": db["patient_worklist"].count_documents({**_ingested(hospital_id),
                                                           "scored_for": {"$ne": fingerprint}}),
            "waiting": len(waiting_for_a_model(db, hospital_id))}


def _choose_model(db, hospital_id: str, rows: pd.DataFrame, source_columns=()) -> tuple:
    """(model, note, inputs). The hospital's deployed discharge model when the
    stays carry enough of its inputs; the built-in model otherwise, with the
    reason.

    `inputs` says which form the model reads. "features": Preventra's own
    columns (the 94 inputs and the x_ columns) - a model trained from stored
    discharges. "source": the discharge rows exactly as the hospital sent them -
    a model trained in Model Studio on the hospital's own export, whose inputs
    are that export's columns and raw values (ingestion/profile.SOURCE_TABLES)."""
    model = model_studio.active_model_for(db, hospital_id, "discharge")
    if not model or model["model_id"] == model_studio.BUILTIN_MIMIC:
        return None, None, None
    wanted = model_studio.comp_features(model)
    need = MIN_COVERAGE * max(len(wanted), 1)
    have = [c for c in wanted if c in rows.columns]
    if len(have) >= need:
        return model, None, "features"
    as_sent = [c for c in wanted if c in set(source_columns)]
    if len(as_sent) >= need:
        return model, None, "source"
    best = max(len(have), len(as_sent))
    return None, (f"{model['name']} needs inputs these discharges do not carry "
                  f"({best} of {len(wanted)}); scored by the built-in model instead"), None


# -------------------------------------------------------------- outcomes
def link_outcomes(db, hospital_id: str, feats: pd.DataFrame) -> None:
    """Record each stay's 30-day outcome as far as the data now shows: a later
    unplanned admission within 30 days, no return once 30 days have passed, or
    unknown. Kept for retraining and the accuracy check (build plan C5)."""
    ops = [UpdateOne({"hospital_id": hospital_id, "encounter_key": key},
                     {"$set": {"readmitted_30d": None if pd.isna(value) else bool(value),
                               "outcome_checked_at": _now()}})
           for key, value in feats.set_index("encounter_key")[canonical.discharge_features.OUTCOME].items()]
    if ops:
        db[STAYS].bulk_write(ops, ordered=False)


# ------------------------------------------------------------ discharges
def _diagnoses(stay: dict) -> tuple:
    dx = sorted(stay.get("diagnoses") or [], key=lambda d: d.get("sequence") or 999)
    principal = dx[0] if dx else {}
    code = str(principal.get("icd_code") or "").replace(".", "").upper()
    title = principal.get("description") or code
    secondary = dx[1:]
    return (code, title, [d.get("description") or d.get("icd_code") for d in secondary[:3]],
            [str(d.get("icd_code") or "").replace(".", "").upper() for d in secondary], len(dx))


def _baseline(stay: dict, group: str) -> Optional[dict]:
    vitals = stay.get("discharge_vitals") or {}
    fields = {"dry_weight_kg": vitals.get("weight_kg"), "baseline_spo2": vitals.get("spo2"),
              "baseline_sbp": vitals.get("sbp"), "baseline_hr": vitals.get("heart_rate"),
              "usual_walk_metres": vitals.get("walk_metres"), "baseline_pain": vitals.get("pain")}
    fields = {k: v for k, v in fields.items() if v is not None}
    return {**fields, "source": "recorded", "group": group} if fields else None


def score_discharges(db, hospital_id: str, encounter_keys: list) -> dict:
    keys = [str(k) for k in encounter_keys]
    targets = {s["encounter_key"]: s for s in db[STAYS].find(
        {"hospital_id": hospital_id, "encounter_key": {"$in": keys}}, {"_id": 0})}
    not_scored = [{"encounter_key": k, "reason": "no stored discharge with this key"}
                  for k in keys if k not in targets]
    if not targets:
        return {"scored": 0, "not_scored": not_scored}
    patients = list({s["patient_id"] for s in targets.values()})
    stays = list(db[STAYS].find({"hospital_id": hospital_id, "patient_id": {"$in": patients}}, {"_id": 0}))
    feats = canonical.features(stays, as_of=pd.Timestamp.now(), include_excluded=True)
    link_outcomes(db, hospital_id, feats)
    retired = _retire_ended(db, hospital_id, feats)

    wanted = feats["encounter_key"].isin(list(targets))
    for _, row in feats[wanted & feats["excluded"].ne("")].iterrows():
        not_scored.append({"encounter_key": row["encounter_key"], "reason": f"not scored: {row['excluded']}"})
    scorable = wanted & feats["excluded"].eq("")
    if not scorable.any():
        return {"scored": 0, "not_scored": not_scored}

    fingerprint = deployed_fingerprint(db, hospital_id)
    scoring_keys = list(feats.loc[scorable, "encounter_key"])
    sent = {k: dict(targets[k].get("source") or []) for k in scoring_keys}
    model, note, inputs = _choose_model(db, hospital_id, feats,
                                        {c for row in sent.values() for c in row})
    if model is None:
        # The built-in model reads only what it was trained on: a stay without
        # those fields (or a child's) waits for the hospital's own model.
        lacking = {k: schema.missing_for_builtin(targets[k]) for k in feats.loc[scorable, "encounter_key"]}
        for key, missing in lacking.items():
            if missing:
                not_scored.append({"encounter_key": key, "reason": (
                    "the built-in model needs " + ", ".join(m.replace("_", " ") for m in missing)
                    + "; train a model on your own data to score it")})
        scorable = scorable & ~feats["encounter_key"].isin([k for k, m in lacking.items() if m])
        if not scorable.any():
            return {"scored": 0, "not_scored": not_scored, **({"note": note} if note else {})}
        results = _builtin_scores(feats[scorable])
        version, bands = BUILTIN_VERSION, model_studio._mimic_bands()
    elif inputs == "source":
        # The rows as sent, in the order the stays are written below. Only the
        # discharges being scored: a model trained on a flat export reads one
        # row per discharge and no earlier admissions.
        rows = pd.DataFrame([sent[k] for k in scoring_keys])
        results = _studio_scores(db, model, rows, pd.Series([True] * len(rows)))
        version = f"studio:{model['model_id']}"
        bands = model.get("bands") or model_studio._mimic_bands()
    else:
        results = _studio_scores(db, model, feats.drop(columns=["excluded"]), scorable)
        version = f"studio:{model['model_id']}"
        bands = model.get("bands") or model_studio._mimic_bands()

    batch = _batch(db)
    # The worklist row is the latest discharge that could be scored.
    # A patient whose newest stay ended in death or hospice has no row at all.
    latest = set(feats[feats["excluded"].eq("") & ~feats["patient_key"].isin(retired)]
                 .sort_values("discharge_datetime").groupby("patient_key").tail(1)["encounter_key"])
    touched = []
    for (_, row), result in zip(feats[scorable].iterrows(), results):
        stay = targets[row["encounter_key"]]
        _write_discharge(db, hospital_id, stay, row, result, version, bands, batch,
                         is_latest=row["encounter_key"] in latest, scored_for=fingerprint)
        touched.append(stay["patient_id"])
    _write_summary(db, batch)
    refresh_summaries(db, batch, touched)
    # Weeks that arrived before their discharge was scored are scored now.
    pending = [p for p in set(touched) if db[WEEKS].count_documents({"patient_id": p}, limit=1)]
    if pending:
        score_weeks(db, hospital_id, pending)
    return {"scored": len(results), "not_scored": not_scored, "model_version": version,
            **({"note": note} if note else {})}


def _write_discharge(db, hospital_id: str, stay: dict, row: pd.Series, result: dict, version: str,
                     bands: dict, batch: str, is_latest: bool, scored_for: str = "") -> None:
    pid, enc = stay["patient_id"], stay["encounter"]
    code, title, sec_titles, sec_codes, n_dx = _diagnoses(stay)
    grp = classify(code, title, sec_titles, sec_codes)
    matches = classify_all(code, title, sec_titles, sec_codes)
    score = round(float(result["score"]), 1)
    band = _band(score, bands)
    drivers = (list(result["drivers"]) + ["", "", ""])[:3]
    common = {"patient_id": pid, "risk_score": score, "risk_band": band,
              "driver_1": drivers[0], "driver_2": drivers[1], "driver_3": drivers[2],
              "admit_date": _day(enc["admit_datetime"]), "discharge_date": _day(enc["discharge_datetime"]),
              "primary_diagnosis": title, "primary_icd_code": code, "n_diagnoses_coded": n_dx,
              "los_days": _number(row["los_days"], lambda v: round(max(v, 0.0), 1)),
              "n_prior_adm": _number(row["n_prior_adm"], int), "model_version": version,
              # Dates taken as the day the patient was sent (api/ingest._assume_dates).
              "dates_assumed": bool(stay.get("dates_assumed")),
              "hospital_id": hospital_id, "encounter_key": stay["encounter_key"], "source": SOURCE}

    db["risk_registry"].update_one(
        {"hospital_id": hospital_id, "encounter_key": stay["encounter_key"], "model_version": version,
         "stage": stay.get("stage", "final")},
        {"$set": {**common, "batch_date": common["discharge_date"], "stage": stay.get("stage", "final"),
                  "scored_at": _now()}}, upsert=True)
    made = db["care_actions"].update_one(
        {"patient_id": pid},
        {"$setOnInsert": {"patient_id": pid, "hospital_id": hospital_id, "notes": [],
                          "coordinator_name": None, "assigned_nurse_ids": []}}, upsert=True)
    if made.upserted_id is not None:
        # A new patient of the hospital: its kept patient list is out of date.
        from api.access import forget_hospital_lists
        forget_hospital_lists()
    if not is_latest:
        return

    current = db["patient_worklist"].find_one({"patient_id": pid, "batch_date": batch},
                                              {"discharge_date": 1, "encounter_key": 1})
    if current and current.get("encounter_key") != stay["encounter_key"] \
            and str(current.get("discharge_date") or "") > common["discharge_date"]:
        return                                  # an older discharge never replaces a newer one
    db["patient_worklist"].update_one(
        {"patient_id": pid, "batch_date": batch},
        {"$set": {**common, "batch_date": batch, "secondary_diagnoses": sec_titles,
                  "anchor_age": int(enc["age_years"]) if enc.get("age_years") is not None else None,
                  "gender": (stay.get("patient") or {}).get("sex"),
                  "clinical_group": grp["group"], "group_label": grp["label"],
                  "group_evidence": grp["evidence"], "group_confidence": grp["confidence"],
                  "clinical_groups": [m["group"] for m in matches] or ["general"],
                  "group_matches": matches, "stage": stay.get("stage", "final"), "raw_inputs": {},
                  "scored_for": scored_for, "scored_at": _now()}},
        upsert=True)

    # A new discharge starts a new monitoring series.
    db["weekly_monitoring"].delete_many({"patient_id": pid, "source": SOURCE,
                                         "encounter_key": {"$ne": stay["encounter_key"]}})
    db["weekly_monitoring"].update_one(
        {"patient_id": pid, "week_number": 0},
        {"$set": {"patient_id": pid, "week_number": 0, "days_after_discharge": 0,
                  "week_date": common["discharge_date"], "risk_score": score, "risk_band": band,
                  "observed": True, "driver_1": drivers[0], "driver_2": drivers[1],
                  "driver_3": drivers[2], "model_version": version, "source": SOURCE,
                  "scored_by": "model", "clinical_group": grp["group"], "group_label": grp["label"],
                  "group_evidence": grp["evidence"], "group_confidence": grp["confidence"],
                  "discharge_baseline": _baseline(stay, grp["group"]), "red_flags": [],
                  "hospital_id": hospital_id, "encounter_key": stay["encounter_key"]}},
        upsert=True)


ENDED = ("died in hospital", "hospice or died")


def _retire_ended(db, hospital_id: str, feats: pd.DataFrame) -> set:
    """A patient whose newest stay ended in death or hospice leaves the
    worklist and their monitoring series: a readmission-risk list must not ask
    anyone to call them. Their scored discharges stay in risk_registry.
    Returns those patients."""
    newest = feats.sort_values("discharge_datetime").groupby("patient_key").tail(1)
    ended = set(newest.loc[newest["excluded"].isin(ENDED), "patient_key"])
    for pid in ended:
        db["patient_worklist"].delete_many({"patient_id": pid, "source": SOURCE})
        db["weekly_monitoring"].delete_many({"patient_id": pid, "source": SOURCE})
    return ended


def _write_summary(db, batch: str) -> None:
    """The band counts the executive summary shows, for the current batch."""
    counts = {r["_id"]: r["n"] for r in db["patient_worklist"].aggregate([
        {"$match": {"batch_date": batch}}, {"$group": {"_id": "$risk_band", "n": {"$sum": 1}}}])}
    total = sum(counts.values())
    db["executive_summary"].update_one(
        {"batch_date": batch},
        {"$set": {"batch_date": batch, "total_patients": total, "high_count": counts.get("High", 0),
                  "medium_count": counts.get("Medium", 0), "low_count": counts.get("Low", 0),
                  "pct_high": round(counts.get("High", 0) / max(total, 1) * 100, 2)},
         "$setOnInsert": {"wow_change": "N/A"}}, upsert=True)


# ---------------------------------------------------------------- weeks
def _bands_of(db, hospital_id: str, version: Optional[str]) -> dict:
    """The bands of the model that scored the discharge, so week 0 and the
    weeks after it mean the same thing by High."""
    if version and version.startswith("studio:"):
        try:
            model = model_studio.get_model(db, version.split(":", 1)[1],
                                           model_studio.system_actor(hospital_id))
            return model.get("bands") or model_studio._mimic_bands()
        except LookupError:
            pass
    return model_studio._mimic_bands()


def week0_for(db, hospital_id: str, stay: dict) -> Optional[dict]:
    """Week 0 of a stay's series: from weekly_monitoring while it is the
    patient's current series, rebuilt from risk_registry once a newer discharge
    has replaced it there (the training export needs those older series - they
    are the ones that ended in a readmission). None when never scored."""
    doc = db["weekly_monitoring"].find_one({"patient_id": stay["patient_id"], "week_number": 0,
                                            "encounter_key": stay["encounter_key"]}, {"_id": 0})
    if doc:
        return doc
    reg = db["risk_registry"].find_one({"hospital_id": hospital_id, "encounter_key": stay["encounter_key"]},
                                       {"_id": 0}, sort=[("scored_at", -1)])
    if not reg:
        return None
    code, title, sec_titles, sec_codes, _ = _diagnoses(stay)
    grp = classify(code, title, sec_titles, sec_codes)
    return {"patient_id": stay["patient_id"], "week_number": 0, "days_after_discharge": 0,
            "week_date": reg.get("discharge_date"), "risk_score": reg["risk_score"],
            "risk_band": reg["risk_band"], "model_version": reg.get("model_version"),
            "clinical_group": grp["group"], "group_label": grp["label"],
            "group_evidence": grp["evidence"], "group_confidence": grp["confidence"],
            "discharge_baseline": _baseline(stay, grp["group"]), "red_flags": [],
            "hospital_id": hospital_id, "encounter_key": stay["encounter_key"], "source": SOURCE}


def scored_weeks(db, hospital_id: str, stay: dict, week0: dict) -> list:
    """The weekly check-ins that follow `stay`, scored by the monitoring rules
    against its discharge score, as weekly_monitoring documents (not written)."""
    weeks = list(db[WEEKS].find({"hospital_id": hospital_id, "encounter_key": stay["encounter_key"]},
                                {"_id": 0}).sort("week_number", 1))
    if not weeks:
        return []
    pid = stay["patient_id"]
    code, title, sec_titles, sec_codes, _ = _diagnoses(stay)
    groups = monitoring_groups(code, title, sec_titles, sec_codes)
    group = week0.get("clinical_group") or (groups[0] if groups else "general")
    base = float(week0["risk_score"])
    bands = _bands_of(db, hospital_id, week0.get("model_version"))
    prev_adj, prev_score, docs = 0.0, base, []
    discharge = pd.Timestamp(stay["encounter"]["discharge_datetime"]).normalize()
    for wk in weeks:
        n = int(wk["week_number"])
        obs = to_observations(wk.get("readings") or {}, week0.get("discharge_baseline"))
        score, adj, contribs = score_week(base, obs, CARRY * prev_adj, variant=variant_for(pid, n),
                                          group=group, groups=groups, prev_score=prev_score,
                                          flag_floor=bands["low_score_threshold"])
        lines = ([d for _, d in contribs[:3]] + ["", "", ""])[:3]
        docs.append({"patient_id": pid, "week_number": n,
                     "days_after_discharge": int((pd.Timestamp(wk["week_date"]) - discharge).days),
                     "week_date": _day(wk["week_date"]), "risk_score": score,
                     "risk_band": _band(score, bands), "observed": True,
                     "driver_1": lines[0], "driver_2": lines[1], "driver_3": lines[2],
                     "monitoring": obs, "red_flags": red_flags(obs), "rule_adjustment": round(adj, 2),
                     "scored_by": "monitoring_rules", "model_version": week0.get("model_version"),
                     "source": SOURCE, "clinical_group": group,
                     "group_label": week0.get("group_label"), "group_evidence": week0.get("group_evidence"),
                     "group_confidence": week0.get("group_confidence"), "hospital_id": hospital_id,
                     "encounter_key": stay["encounter_key"],
                     # A patient's or caretaker's own check-in (api/checkins.py).
                     **({"reported_by": wk["reported_by"]} if wk.get("reported_by") else {}),
                     **({"note": wk["note"]} if wk.get("note") else {})})
        prev_adj, prev_score = adj, score
    return docs


def score_weeks(db, hospital_id: str, patient_ids: list) -> dict:
    scored, not_scored, notes, written = 0, [], set(), []
    batch = _batch(db)
    weekly_model = model_studio.active_model_for(db, hospital_id, "weekly")
    if weekly_model and not weekly_model.get("can_score", True):
        weekly_model = None                             # the built-in rules-based one
    for pid in dict.fromkeys(str(p) for p in patient_ids):
        stays = list(db[STAYS].find({"hospital_id": hospital_id, "patient_id": pid},
                                    {"_id": 0}).sort("encounter.discharge_datetime", -1).limit(1))
        if not stays:
            not_scored.append({"patient_id": pid, "reason": "no stored discharge for this patient"})
            continue
        stay = stays[0]
        week0 = db["weekly_monitoring"].find_one({"patient_id": pid, "week_number": 0,
                                                  "encounter_key": stay["encounter_key"]}, {"_id": 0})
        if not week0:
            not_scored.append({"patient_id": pid,
                               "reason": "the discharge these weeks follow has not been scored"})
            continue
        docs = scored_weeks(db, hospital_id, stay, week0)
        if not docs:
            continue
        if weekly_model:
            note = _score_weeks_with_model(db, weekly_model, stay, week0, docs)
            if note:
                notes.add(note)
        for d in docs:
            db["weekly_monitoring"].update_one({"patient_id": pid, "week_number": d["week_number"]},
                                               {"$set": d}, upsert=True)
        scored += len(docs)
        written.append(pid)
    refresh_summaries(db, batch, [str(p) for p in patient_ids])
    return {"scored": scored, "not_scored": not_scored, "alerts": _alerts_for(db, written),
            **({"note": "; ".join(sorted(notes))} if notes else {})}


def _alerts_for(db, patient_ids: list) -> Optional[dict]:
    """
    Raise the alerts these new weeks call for, as soon as they are scored. The
    forecast scan used to run only when a superadmin pressed it, so a red flag
    that arrived by upload sat on the patient's card with nobody told. A failed
    check is logged and the nightly sweep tries again; it never fails scoring.
    """
    if not patient_ids:
        return None
    try:
        from api import forecasting
        done = forecasting.raise_alerts(db, patient_ids)
        return {"created": done["alerts_created"], "updated": done["alerts_updated"],
                "unrouted": done["unrouted"]}
    except Exception as exc:                                       # noqa: BLE001
        print(f"[scoring] alert check failed: {type(exc).__name__}")
        return None


def _score_weeks_with_model(db, model: dict, stay: dict, week0: dict, docs: list) -> Optional[str]:
    """A deployed weekly model scores the weeks from the rows it was trained on
    (features/weekly_rows.model_rows). The rules' readings stay as the card's
    lines; the score and band are the model's. When the rows lack most of the
    model's inputs, the rules' scores stand and the reason is returned."""
    frame = model_rows([week0] + docs, stay)
    wanted = model_studio.comp_features(model)
    have = [c for c in wanted if c in frame.columns]
    if len(have) < MIN_COVERAGE * max(len(wanted), 1):
        return (f"{model['name']} needs inputs these weeks do not carry ({len(have)} of {len(wanted)}); "
                "scored by the monitoring rules instead")
    targets = pd.Series([False] + [True] * len(docs))
    results = _studio_scores(db, model, frame, targets)
    bands = model.get("bands") or model_studio._mimic_bands()
    for d, r in zip(docs, results):
        d["risk_score"] = r["score"]
        d["risk_band"] = _band(r["score"], bands)
        d["scored_by"] = f"studio:{model['model_id']}"
        d["model_version"] = f"studio:{model['model_id']}"
    return None

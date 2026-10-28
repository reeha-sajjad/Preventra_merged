"""
Re-run the models on one patient already on the list, with no typing.

Two targets:

    discharge   the discharge score is calculated again from the inputs the
                server holds for the patient, and the weekly scores that follow
                are recomputed from it (the weekly rules start from the
                discharge score, so a changed one moves every week after it)
    weekly      the weekly scores are recomputed from the discharge score and
                the readings already stored; the discharge score is untouched

Both are previews until `commit` is true. The server always scores - a number
sent by the browser is never read - and every saved re-evaluation is written
to `rescore_log`.

Where the inputs come from, by how the patient came onto the list:

    ingest   the hospital's feed: the hospital's deployed model, through the
             same scoring the worker runs (api/scoring_service)
    stored   `model_inputs`: the 94 inputs of a loaded MIMIC patient
             (scripts/backfill_model_inputs.py)
    typed    the inputs a person entered by hand (Score a patient, or an edit)

Access (patient in scope, role, reason) is checked in api/main.py.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional

from api import access, mimic_scoring, model_studio, scoring_service
from api.db_utils import get_latest_batch_date
from api.worklist_summary import refresh

SCORE_JUMP_ALERT_THRESHOLD = 15.0
_BAND_RANK = {"Low": 1, "Medium": 2, "High": 3}

BUILTIN_REASON = ("Scored by the built-in model: the hospital's deployed model reads "
                  "the hospital's own discharge records.")
NO_INPUTS = ("This patient's model inputs have not been loaded yet, so the discharge score can't be "
             "re-evaluated. The weekly trend still can.")
NO_WEEK0 = "No discharge score to build the weekly trend from"
FEED = "This patient's discharge comes from the hospital's feed."


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


# --------------------------------------------------------------- context
def _context(db, pid) -> dict:
    """What is held about the patient: the list row, hospital, source, the
    latest ingested stay, stored model inputs and week 0. LookupError when the
    patient is not on the current list."""
    variants = access.id_variants([str(pid)])
    batch = get_latest_batch_date(db, "patient_worklist")
    row = db["patient_worklist"].find_one({"patient_id": {"$in": variants}, "batch_date": batch})
    if not row:
        raise LookupError("Patient not found")
    care = db["care_actions"].find_one({"patient_id": {"$in": variants}}, {"hospital_id": 1}) or {}
    hospital = care.get("hospital_id") or row.get("hospital_id")
    source = row.get("source") or "mimic"
    stay = None
    if source == "ingest":
        found = list(db["ingest_stays"].find({"hospital_id": hospital, "patient_id": {"$in": variants}},
                                             {"_id": 0})
                     .sort("encounter.discharge_datetime", -1).limit(1))
        stay = found[0] if found else None
    return {"pid": row["patient_id"], "variants": variants, "batch": batch, "row": row,
            "hospital_id": hospital, "source": source, "stay": stay,
            "inputs_doc": db["model_inputs"].find_one({"patient_id": {"$in": variants}, "current": True}),
            "week0": db["weekly_monitoring"].find_one({"patient_id": {"$in": variants}, "week_number": 0})}


def discharge_inputs(ctx: dict) -> tuple:
    """(kind, values): which inputs the discharge score is calculated from."""
    row = ctx["row"]
    raw = row.get("raw_inputs") or {}
    if ctx["source"] == "ingest":
        return "ingest", None
    if row.get("inputs_source") == "typed" and raw:
        return "typed", raw
    if ctx["inputs_doc"]:
        return "stored_features", ctx["inputs_doc"].get("features") or {}
    if raw:
        return "manual", raw
    return None, None


def _model_of(db, ctx: dict) -> dict:
    if ctx["source"] == "ingest":
        m = model_studio.active_model_for(db, ctx["hospital_id"], "discharge")
        if m:
            return {"model_id": m["model_id"], "name": m.get("name"), "builtin": bool(m.get("builtin"))}
    return {"model_id": model_studio.BUILTIN_MIMIC, "name": "Built-in model", "builtin": True}


def options(db, pid) -> dict:
    ctx = _context(db, pid)
    row, kind = ctx["row"], discharge_inputs(ctx)[0]
    model = _model_of(db, ctx)
    possible = bool(ctx["stay"]) if kind == "ingest" else kind is not None
    reason = None
    if not possible:
        reason = FEED if kind == "ingest" else NO_INPUTS
    elif model["builtin"]:
        reason = BUILTIN_REASON
    weeks = list(db["weekly_monitoring"].find({"patient_id": {"$in": ctx["variants"]},
                                               "week_number": {"$gt": 0}}, {"observed": 1}))
    return {"source": ctx["source"], "hospital_id": ctx["hospital_id"],
            "current": {"risk_score": row.get("risk_score"), "risk_band": row.get("risk_band"),
                        "current_score": row.get("current_score"), "current_band": row.get("current_band"),
                        "model_version": row.get("model_version"), "scored_at": row.get("scored_at")},
            "discharge": {"possible": possible, "inputs": kind, "model": model, "reason": reason},
            "weekly": {"possible": bool(ctx["week0"]), "weeks": len(weeks),
                       "observed_weeks": sum(1 for w in weeks if w.get("observed", True) is not False),
                       "reason": None if ctx["week0"] else NO_WEEK0}}


# ----------------------------------------------------------------- helpers
def _drivers(raw: list) -> list:
    return [mimic_scoring._split_driver(d) for d in raw if d]


def _row_drivers(row: dict) -> list:
    return [row.get(f"driver_{i}") or "" for i in (1, 2, 3)]


def _log(db, ctx: dict, user: dict, target: str, old: dict, new: dict, weeks: list, inputs) -> None:
    db["rescore_log"].insert_one({
        "patient_id": ctx["pid"], "hospital_id": ctx["hospital_id"], "target": target,
        "by": user.get("email"), "user_id": str(user.get("_id") or ""), "role": user.get("role"),
        "at": datetime.now(timezone.utc), "old": old, "new": new,
        "weeks_changed": sum(1 for w in weeks if w.get("changed")), "inputs": inputs})


def _diff_stored(db, ctx: dict, new_base: Optional[tuple], docs: list) -> list:
    """The weeks of an ingested patient as they would become: stored value
    against the preview's. new_base is (score, band) for week 0 or None."""
    stored = {int(d["week_number"]): d for d in db["weekly_monitoring"].find(
        {"patient_id": {"$in": ctx["variants"]}})}
    out = []

    def line(n, old, score, band):
        old_score, old_band = (old or {}).get("risk_score"), (old or {}).get("risk_band")
        return {"week_number": n, "old_score": old_score, "new_score": score, "old_band": old_band,
                "new_band": band, "rescored": True,
                "changed": old is None or old_score is None or abs(score - old_score) > 1e-9 or band != old_band}

    if new_base is not None and 0 in stored:
        out.append(line(0, stored[0], new_base[0], new_base[1]))
    for d in docs:
        out.append(line(int(d["week_number"]), stored.get(int(d["week_number"])), d["risk_score"], d["risk_band"]))
    return out


def score_jump_alert(db, pid, old_score: float, old_band: str, new_score: float, new_band: str) -> bool:
    """A 15-point rise, or a move to a higher band, raises an alert."""
    score_delta = new_score - old_score
    band_jumped_up = _BAND_RANK.get(new_band, 0) > _BAND_RANK.get(old_band, 0)
    if score_delta >= SCORE_JUMP_ALERT_THRESHOLD or band_jumped_up:
        db["alerts"].insert_one({
            "patient_id":    pid,
            "old_score":     old_score,
            "new_score":     new_score,
            "old_band":      old_band,
            "new_band":      new_band,
            "delta":         round(score_delta, 1),
            "triggered_at":  datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "acknowledged":  False,
        })
        return True
    return False


def registry_discharge_date(db, ctx: dict):
    """The discharge date the patient's registry rows are keyed on. The demo
    prep (scripts/prepare_demo.py) moves the dates on the list and the weekly
    rows but not the registry, so the list's date is moved back by the shift."""
    date = ctx["row"].get("discharge_date")
    shifted = db["demo_prep"].find_one({"kind": {"$ne": "doctor"}, "patient_id": {"$in": ctx["variants"]}})
    days = int((shifted or {}).get("shift_days") or 0)
    if not days:
        return date
    try:
        return (datetime.strptime(str(date)[:10], "%Y-%m-%d") - timedelta(days=days)).strftime("%Y-%m-%d")
    except ValueError:
        return date


def _commit_builtin(db, ctx: dict, scored: dict) -> bool:
    """Save a built-in-model score for the patient and carry it through: the
    list row, the registry, week 0 and every week after it. Returns whether a
    score-jump alert was raised."""
    row, pid, batch = ctx["row"], ctx["pid"], ctx["batch"]
    score, band = float(scored["risk_score"]), scored["risk_band"]
    raw = [scored.get(f"driver_{i}_raw", "") for i in (1, 2, 3)]
    old_score, old_band = float(row.get("risk_score") or 0), row.get("risk_band", "Low")
    now = _now()
    version = scoring_service.BUILTIN_VERSION
    db["patient_worklist"].update_one(
        {"_id": row["_id"]},
        {"$set": {"risk_score": score, "risk_band": band, "driver_1": raw[0], "driver_2": raw[1],
                  "driver_3": raw[2], "model_version": version, "scored_at": now,
                  "previous": {"risk_score": row.get("risk_score"), "risk_band": row.get("risk_band"),
                               "model_version": row.get("model_version"), "scored_at": row.get("scored_at")}}})
    # One registry row per admission per model.
    original = registry_discharge_date(db, ctx)
    registered = {"patient_id": pid, "risk_score": score, "risk_band": band, "driver_1": raw[0],
                  "driver_2": raw[1], "driver_3": raw[2], "model_version": version,
                  "discharge_date": original, "admit_date": row.get("admit_date"),
                  "primary_diagnosis": row.get("primary_diagnosis"),
                  "primary_icd_code": row.get("primary_icd_code"), "source": ctx["source"],
                  "batch_date": original, "scored_at": now}
    if ctx["hospital_id"]:
        registered["hospital_id"] = ctx["hospital_id"]
    db["risk_registry"].update_one(
        {"patient_id": pid, "discharge_date": original, "model_version": version},
        {"$set": registered}, upsert=True)

    if ctx["week0"] is None:
        group = row.get("clinical_group") or "general"
        db["weekly_monitoring"].insert_one({
            "patient_id": pid, "week_number": 0, "days_after_discharge": 0,
            "week_date": row.get("discharge_date"), "risk_score": score, "risk_band": band,
            "observed": True, "driver_1": raw[0], "driver_2": raw[1], "driver_3": raw[2],
            "model_version": version, "source": ctx["source"], "scored_by": "model",
            "clinical_group": group, "group_label": row.get("group_label"),
            "group_evidence": row.get("group_evidence"), "group_confidence": row.get("group_confidence"),
            "discharge_baseline": {"source": "recorded", "group": group}, "red_flags": [],
            "hospital_id": ctx["hospital_id"], "encounter_key": row.get("encounter_key") or pid})
    else:
        scoring_service.rescore_series(db, pid, base=score, write=True, week0_drivers=raw,
                                       model_version=version)
    scoring_service._write_summary(db, batch)
    refresh(db, batch, [pid])
    scoring_service._alerts_for(db, [pid])
    return score_jump_alert(db, pid, old_score, old_band, score, band)


def _series_preview(db, pid, score: float) -> list:
    try:
        return scoring_service.rescore_series(db, pid, base=score, write=False)["weeks"]
    except LookupError:
        return []


# ------------------------------------------------------------------- run
def run(db, user: dict, pid, target: str, commit: bool = False) -> dict:
    """Preview (or, with commit, save) a re-evaluation. ValueError carries the
    reason when it cannot be done."""
    ctx = _context(db, pid)
    row, hosp = ctx["row"], ctx["hospital_id"]
    cur = {"score": row.get("risk_score"), "band": row.get("risk_band"), "drivers": _drivers(_row_drivers(row))}
    alert = False

    if target == "discharge":
        if ctx["source"] == "ingest":
            stay = ctx["stay"]
            if not stay:
                raise ValueError(FEED)
            new = scoring_service.preview_discharge(db, hosp, stay["encounter_key"])
            weeks = []
            if ctx["week0"]:
                weeks = _diff_stored(db, ctx, (new["score"], new["band"]),
                                     scoring_service.preview_weeks(db, hosp, ctx["pid"], base_override=new["score"],
                                                   bands=new["bands"]))
            prop = {"score": new["score"], "band": new["band"], "drivers": _drivers(new["drivers"]),
                    "model_version": new["model_version"], "model_name": new["model_name"], "note": new["note"]}
            if commit:
                res = scoring_service.score_discharges(db, hosp, [stay["encounter_key"]])
                if not res.get("scored"):
                    why = [n["reason"] for n in res.get("not_scored", [])]
                    raise ValueError(why[0] if why else "this discharge could not be scored")
            inputs = "ingest"
        else:
            kind, values = discharge_inputs(ctx)
            if kind is None:
                raise ValueError(NO_INPUTS)
            if kind == "stored_features":
                scored = mimic_scoring.score_features(values)
            else:
                scored = mimic_scoring.score_manual_entry(
                    {k: v for k, v in values.items() if k != "discharge_date" and v is not None})
            weeks = _series_preview(db, ctx["pid"], scored["risk_score"]) if ctx["week0"] else []
            prop = {"score": scored["risk_score"], "band": scored["risk_band"],
                    "drivers": _drivers([scored.get(f"driver_{i}_raw", "") for i in (1, 2, 3)]),
                    "model_version": scoring_service.BUILTIN_VERSION, "model_name": "Built-in model",
                    "note": BUILTIN_REASON}
            if commit:
                alert = _commit_builtin(db, ctx, scored)
            inputs = kind
    elif target == "weekly":
        if not ctx["week0"]:
            raise ValueError(NO_WEEK0)
        if ctx["source"] == "ingest":
            if not ctx["stay"]:
                raise ValueError(FEED)
            weeks = _diff_stored(db, ctx, None, scoring_service.preview_weeks(db, hosp, ctx["pid"]))
            if commit:
                scoring_service.score_weeks(db, hosp, [ctx["pid"]])
        else:
            weeks = scoring_service.rescore_series(db, ctx["pid"], write=commit)["weeks"]
            if commit:
                refresh(db, ctx["batch"], [ctx["pid"]])
                scoring_service._alerts_for(db, [ctx["pid"]])
        prop = {**cur, "model_version": row.get("model_version"), "model_name": "Monitoring rules",
                "note": "The weekly scores are recalculated from the discharge score and the readings already stored."}
        inputs = "stored_readings"
    else:
        raise ValueError("unknown target")

    changed = (any(w.get("changed") for w in weeks)
               or (target == "discharge" and (prop["score"] != cur["score"] or prop["band"] != cur["band"])))
    if commit:
        after = db["patient_worklist"].find_one({"_id": row["_id"]}) or row
        _log(db, ctx, user, target,
             {"score": cur["score"], "band": cur["band"], "model_version": row.get("model_version")},
             {"score": after.get("risk_score"), "band": after.get("risk_band"),
              "model_version": after.get("model_version")}, weeks, inputs)
    return {"target": target, "current": cur, "proposed": prop, "weeks": weeks, "changed": bool(changed),
            "committed": bool(commit), "alert_created": alert}


# ------------------------------------------------------------ hand edits
def edit_inputs(db, pid) -> dict:
    """The inputs the Update Patient form is filled with, and whether they can
    be edited by hand."""
    ctx = _context(db, pid)
    if ctx["source"] == "ingest":
        return {"inputs": {}, "editable": False, "reason": FEED}
    if ctx["inputs_doc"] and not (ctx["row"].get("inputs_source") == "typed" and ctx["row"].get("raw_inputs")):
        return {"inputs": mimic_scoring.manual_from_features(ctx["inputs_doc"].get("features") or {}),
                "editable": True, "reason": None}
    return {"inputs": dict(ctx["row"].get("raw_inputs") or {}), "editable": True, "reason": None}


def _edited(ctx: dict, manual: dict) -> dict:
    if ctx["source"] == "ingest":
        raise ValueError(FEED + " It can't be edited by hand; send a corrected record instead.")
    manual = {k: v for k, v in (manual or {}).items() if v is not None and v != ""}
    if ctx["inputs_doc"] and not (ctx["row"].get("inputs_source") == "typed" and ctx["row"].get("raw_inputs")):
        return mimic_scoring.score_features(
            mimic_scoring.overlay_manual(ctx["inputs_doc"].get("features") or {}, manual))
    return mimic_scoring.score_manual_entry({k: v for k, v in manual.items() if k != "discharge_date"})


def preview_edit(db, user: dict, pid, manual: dict) -> dict:
    """The score the edited inputs give, and the weeks after it. Writes nothing."""
    ctx = _context(db, pid)
    scored = _edited(ctx, manual)
    weeks = _series_preview(db, ctx["pid"], scored["risk_score"]) if ctx["week0"] else []
    return {**scored, "weeks": weeks}


def commit_edit(db, user: dict, pid, manual: dict) -> dict:
    """Save edited inputs, score them here and carry the new score through the
    weeks. The score comes from the server, never from the request."""
    ctx = _context(db, pid)
    scored = _edited(ctx, manual)
    row = ctx["row"]
    manual = {k: v for k, v in (manual or {}).items() if v is not None and v != ""}
    old = {"score": row.get("risk_score"), "band": row.get("risk_band"), "model_version": row.get("model_version")}
    weeks = _series_preview(db, ctx["pid"], scored["risk_score"]) if ctx["week0"] else []
    doc = ctx["inputs_doc"]
    if doc and not (row.get("inputs_source") == "typed" and row.get("raw_inputs")):
        features = mimic_scoring.features_for_storage(
            mimic_scoring.overlay_manual(doc.get("features") or {}, manual))
        change = {"features": features, "edited_by": user.get("email"), "edited_at": _now()}
        if "loaded_features" not in doc:
            change["loaded_features"] = doc.get("features")
        db["model_inputs"].update_one({"_id": doc["_id"]}, {"$set": change})
        inputs = "stored_features"
    else:
        extra = {"inputs_source": "typed"} if ctx["source"] == "mimic" else {}
        db["patient_worklist"].update_one({"_id": row["_id"]}, {"$set": {"raw_inputs": manual, **extra}})
        inputs = "typed"
    alert = _commit_builtin(db, ctx, scored)
    _log(db, ctx, user, "edit", old,
         {"score": scored["risk_score"], "band": scored["risk_band"],
          "model_version": scoring_service.BUILTIN_VERSION}, weeks, inputs)
    return {"old_score": float(row.get("risk_score") or 0), "new_score": scored["risk_score"],
            "batch_date": ctx["batch"], "alert_created": alert}

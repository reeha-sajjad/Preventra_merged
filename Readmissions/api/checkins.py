"""
The weekly check-in: a patient, or a caretaker they linked, sends this week's
readings from their own home page, and they are scored exactly as a hospital's
feed would be.

Until now every weekly reading arrived through ingestion (api/ingest.py) from a
system the hospital runs. Without one - no connected devices, no nurse phoning
round - nothing reached the monitoring rules at all, and the silence rule
(models/monitoring_rules.contact_gap) was the only thing that ever fired. This
is the cheapest feed there is: the patient themselves.

What is asked comes from the patient's monitoring plan (models/monitoring_rules
.signal_plan): heart failure asks about pillows and ankles, a wound about the
wound. Only what the rules score is asked, so every answer counts. The pharmacy
refill is not asked - the pharmacy's own feed reports it. Weight is asked only
when there is a discharge weight to compare it with (models/discharge_baseline):
a weight on its own says nothing.

When the check-in is open:

    from the day after discharge to the end of the 30-day window
    (monitoring_rules.MONITORING_DAYS), once per monitoring week - days 1-7
    are week 1 (features/weekly_rows.week_number), as for an ingested week

How it is scored - two paths, one outcome:

    an ingested patient   the readings join that week in ingest_weeks and the
                          worker's own scoring runs (api/scoring_service
                          .score_weeks), so a device reading the same week and
                          the patient's answers are one week, scored once
    anyone else           the same rules, applied here against the patient's
                          series in weekly_monitoring (week 0 is the discharge)

Either way the week is marked with who reported it (`reported_by`), the trend
page attributes it to the patient app rather than to a device, the worklist
summary is refreshed and the alert check runs at once - a red flag a patient
types in reaches their doctor the same minute. The patient is never shown the
score; a red flag gets the safety advice instead.

Every submission is kept in `checkins` (who, when, which week, what was asked),
which is what the Outcomes page counts.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import HTTPException
from pydantic import ValidationError
from pymongo.errors import DuplicateKeyError

from api import access
from api.db_utils import get_latest_batch_date
from features.weekly_rows import to_observations, week_number
from ingestion import schema
from ingestion import scheduler
from models.monitoring_rules import (CARRY, MONITORING_DAYS, RED_FLAGS, red_flags, score_week,
                                     signals_for, variant_for)

COLLECTION = "checkins"
WEEKLY = "weekly_monitoring"

# The phone-number advice, shown when an answer is a red flag (the same words
# as the patient's home page, api/patient_home.SAFETY).
URGENT = ("Some of what you told us needs looking at today, and your care team has been alerted. "
          "If you have chest pain, trouble breathing, sudden confusion, or feel much worse than when you "
          "left hospital, call 911 or go to the emergency department now.")

# One question per monitoring signal, in the patient's words. `field` is the
# reading it fills (ingestion/schema.Readings). A choice whose reading is a
# number carries the number as its value. `monitor` marks a reading that needs a
# home device, shown under "If you have a home monitor".
QUESTIONS = {
    "weight_change_kg": {
        "field": "weight_kg", "kind": "number", "unit": "kg", "min": 20, "max": 400, "step": 0.1,
        "label": "Your weight today",
        "hint": "In the morning, after using the toilet and before breakfast."},
    "adherence_pct": {
        "field": "adherence_pct", "kind": "choice",
        "label": "How many of your medicine doses did you take this week?",
        "options": [(100, "All of them"), (80, "Most of them"), (50, "About half"), (20, "Few or none")]},
    "followup_status": {
        "field": "followup_status", "kind": "choice",
        "label": "Your follow-up appointment",
        "options": [("attended", "I went to it"), ("missed", "I missed it"),
                    ("not due", "It hasn't happened yet")]},
    "orthopnoea_pillows": {
        "field": "orthopnoea_pillows", "kind": "number", "min": 0, "max": 10, "step": 1, "unit": "pillows",
        "label": "How many pillows do you need to sleep without getting breathless?"},
    "ankle_swelling": {
        "field": "ankle_swelling", "kind": "choice",
        "label": "Are your ankles or legs swollen?",
        "options": [("none", "No"), ("mild", "A little"), ("marked", "A lot - shoes or socks feel tight")]},
    "walk_distance_pct": {
        "field": "walk_distance_pct", "kind": "choice",
        "label": "How far can you walk, compared with before you went into hospital?",
        "options": [(110, "Further"), (100, "About the same"), (70, "Less far"), (40, "Much less far")]},
    "rescue_inhaler_uses": {
        "field": "rescue_inhaler_uses", "kind": "number", "min": 0, "max": 200, "step": 1, "unit": "times",
        "label": "How many times did you use your reliever (rescue) inhaler this week?"},
    "sputum_change": {
        "field": "sputum_change", "kind": "choice",
        "label": "Has the phlegm you cough up changed?",
        "options": [("none", "No change"), ("volume", "There's more of it"),
                    ("purulent", "It's changed colour (yellow or green)"), ("both", "Both")]},
    "wound_status": {
        "field": "wound_status", "kind": "choice",
        "label": "How does your wound look?",
        "options": [("clean", "Clean and healing"), ("red", "Red or warm around it"),
                    ("discharge", "Leaking fluid or pus"), ("opening", "Coming apart")]},
    "pain_trend": {
        "field": "pain_trend", "kind": "choice",
        "label": "Your pain since last week",
        "options": [("improving", "Getting better"), ("unchanged", "About the same"), ("worse", "Getting worse")]},
    "antibiotic_course": {
        "field": "antibiotic_course", "kind": "choice",
        "label": "Your antibiotics",
        "options": [("ongoing", "Still taking them"), ("completed", "Finished the course"),
                    ("stopped_early", "Stopped before the end"), ("not_prescribed", "I wasn't given any")]},
    "new_confusion": {
        "field": "new_confusion", "kind": "choice",
        "label": "Has anyone noticed new confusion or unusual drowsiness?",
        "options": [("no", "No"), ("yes", "Yes")]},
    "temperature_c": {
        "field": "temperature_c", "kind": "number", "unit": "°C", "min": 30, "max": 44, "step": 0.1,
        "label": "Your temperature", "monitor": True},
    "sbp": {
        "field": "sbp", "kind": "number", "unit": "mmHg", "min": 50, "max": 260, "step": 1,
        "label": "Blood pressure - the top number", "monitor": True},
    "heart_rate": {
        "field": "heart_rate", "kind": "number", "unit": "beats a minute", "min": 20, "max": 250, "step": 1,
        "label": "Pulse at rest", "monitor": True},
    "spo2": {
        "field": "spo2", "kind": "number", "unit": "%", "min": 50, "max": 100, "step": 1,
        "label": "Oxygen level, from a finger monitor", "monitor": True},
}
# Reported by the pharmacy's own feed, never asked.
NOT_ASKED = ("refill_status",)
REPORTERS = ("patient", "caretaker")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def ensure_indexes(db) -> None:
    db[COLLECTION].create_index([("patient_id", 1), ("discharge_date", 1), ("week_number", 1)], unique=True)


# ------------------------------------------------------------ the series
def _series(db, patient_id: str) -> list:
    return list(db[WEEKLY].find({"patient_id": {"$in": access.id_variants([patient_id])}},
                                {"_id": 0}).sort("week_number", 1))


def _row(db, patient_id: str) -> dict:
    batch = get_latest_batch_date(db, "patient_worklist")
    return db["patient_worklist"].find_one(
        {"patient_id": {"$in": access.id_variants([patient_id])}, "batch_date": batch},
        {"_id": 0, "discharge_date": 1, "clinical_group": 1, "clinical_groups": 1, "hospital_id": 1}) or {}


def _stay(db, patient_id: str) -> Optional[dict]:
    """The patient's latest ingested discharge, when they came in by ingestion."""
    found = list(db["ingest_stays"].find({"patient_id": {"$in": access.id_variants([patient_id])}},
                                         {"_id": 0, "hospital_id": 1, "encounter_key": 1, "patient_key": 1,
                                          "patient_id": 1, "encounter.discharge_datetime": 1})
                 .sort("encounter.discharge_datetime", -1).limit(1))
    return found[0] if found else None


def _discharged(week0: Optional[dict], row: dict):
    from models.monitoring_rules import _as_date
    return _as_date((week0 or {}).get("week_date")) or _as_date(row.get("discharge_date"))


def _plan(db, patient_id: str):
    """(week0, plan group, every group, discharge baseline, discharge date)."""
    docs = _series(db, patient_id)
    week0 = next((d for d in docs if int(d.get("week_number", -1)) == 0), None)
    row = _row(db, patient_id)
    group = (week0 or {}).get("clinical_group") or row.get("clinical_group") or "general"
    groups = row.get("clinical_groups") or [group]
    if group in groups:
        groups = [group] + [g for g in groups if g != group]
    baseline = next((d.get("discharge_baseline") for d in docs if d.get("discharge_baseline")), None)
    return docs, week0, group, groups, baseline, _discharged(week0, row)


def questions_for(group: str, baseline: Optional[dict]) -> list:
    """What this patient is asked: their plan's signals, condition-specific
    first (signal_plan's order), home-monitor readings last."""
    from models.monitoring_rules import signal_plan
    plan = signal_plan(group)
    order = [s["key"] for s in plan["priority"]] + [s["key"] for s in plan["optional"]]
    asked = [k for k in order if k in signals_for(group) and k in QUESTIONS and k not in NOT_ASKED]
    if not (baseline or {}).get("dry_weight_kg"):
        asked = [k for k in asked if k != "weight_change_kg"]       # nothing to compare a weight with
    asked.sort(key=lambda k: bool(QUESTIONS[k].get("monitor")))     # stable: plan order within each
    out = []
    for key in asked:
        q = QUESTIONS[key]
        out.append({"key": q["field"], "signal": key, "label": q["label"], "kind": q["kind"],
                    "monitor": bool(q.get("monitor")), "hint": q.get("hint"), "unit": q.get("unit"),
                    "min": q.get("min"), "max": q.get("max"), "step": q.get("step"),
                    "options": [{"value": v, "label": label} for v, label in q.get("options", [])]})
    return out


# ------------------------------------------------------------ the form
def form(db, user: dict, scope: Optional[list], patient_id: Optional[str] = None,
         today=None) -> dict:
    """Whether this week's check-in is open, and what it asks."""
    from api.followups import own_patient
    pid = own_patient(user, scope, patient_id)
    return _state(db, pid, today or scheduler.today())


def _state(db, pid: str, today) -> dict:
    docs, week0, group, groups, baseline, discharged = _plan(db, pid)
    base = {"patient_id": pid, "open": False, "closed_reason": None, "questions": [],
            "week_number": None, "done_this_week": False, "last": _last(db, pid)}
    if not week0 or discharged is None:
        return {**base, "closed_reason": "not_started"}
    days = (today - discharged).days
    ends = discharged + timedelta(days=MONITORING_DAYS)
    base.update({"discharge_date": discharged.isoformat(), "window_ends": ends.isoformat(),
                 "days_since_discharge": days})
    if days < 1:
        return {**base, "closed_reason": "too_soon"}
    if days > MONITORING_DAYS:
        return {**base, "closed_reason": "ended"}
    n = week_number(discharged, today)
    done = db[COLLECTION].find_one({"patient_id": pid, "discharge_date": discharged.isoformat(),
                                    "week_number": n}, {"_id": 1})
    next_opens = discharged + timedelta(days=7 * n + 1)
    return {**base, "open": not done, "week_number": n, "done_this_week": bool(done),
            "next_opens": next_opens.isoformat() if next_opens <= ends else None,
            "questions": [] if done else questions_for(group, baseline)}


def _last(db, pid: str) -> Optional[dict]:
    doc = db[COLLECTION].find_one({"patient_id": pid}, sort=[("at", -1)])
    if not doc:
        return None
    at = doc["at"] if doc["at"].tzinfo else doc["at"].replace(tzinfo=timezone.utc)
    return {"at": at.isoformat(), "week_number": doc["week_number"], "by": doc["by_role"]}


# ------------------------------------------------------------ sending
def _readings(answers: dict, questions: list) -> dict:
    """The answers as Readings, checked against what was asked. Anything not
    asked is refused rather than ignored; a blank answer is no answer."""
    asked = {q["key"]: q for q in questions}
    unknown = sorted(set(answers) - set(asked))
    if unknown:
        raise HTTPException(status_code=422, detail=f"Not asked this week: {', '.join(unknown)}")
    out = {}
    for key, value in answers.items():
        if value is None or value == "":
            continue
        q = asked[key]
        if q["kind"] == "choice":
            allowed = {str(o["value"]): o["value"] for o in q["options"]}
            if str(value) not in allowed:
                raise HTTPException(status_code=422, detail=f"Choose one of the answers for: {q['label']}")
            out[key] = allowed[str(value)]
        else:
            try:
                number = float(value)
            except (TypeError, ValueError):
                raise HTTPException(status_code=422, detail=f"Enter a number for: {q['label']}")
            if not q["min"] <= number <= q["max"]:
                raise HTTPException(status_code=422,
                                    detail=f"{q['label']}: enter a number from {q['min']} to {q['max']}")
            out[key] = int(number) if q["step"] == 1 else number
    if not out:
        raise HTTPException(status_code=422, detail="Answer at least one question")
    try:
        return schema.Readings(**out).model_dump(exclude_none=True)
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=[e["msg"] for e in exc.errors()])


def submit(db, user: dict, scope: Optional[list], answers: dict, patient_id: Optional[str] = None,
           today=None) -> dict:
    """Send this week's check-in. 409 once this week's is in."""
    from api.followups import own_patient
    if user["role"] not in REPORTERS:
        raise HTTPException(status_code=403, detail="Check-ins are sent by patients and their caretakers")
    pid = own_patient(user, scope, patient_id)
    today = today or scheduler.today()
    state = _state(db, pid, today)
    if state["done_this_week"]:
        raise HTTPException(status_code=409, detail="This week's check-in is already in")
    if not state["open"]:
        raise HTTPException(status_code=409, detail={
            "not_started": "Your care team hasn't started your check-ins yet",
            "too_soon": "Your check-ins start the day after you go home",
            "ended": "Your 30 days of check-ins have finished"}[state["closed_reason"]])
    readings = _readings(answers or {}, state["questions"])
    ensure_indexes(db)
    n, discharged = state["week_number"], state["discharge_date"]
    record = {"patient_id": pid, "discharge_date": discharged, "week_number": n,
              "week_date": today.isoformat(), "at": _now(), "by_role": user["role"],
              "by_user": str(user["_id"]), "answered": sorted(readings)}
    try:
        db[COLLECTION].insert_one(record)
    except DuplicateKeyError:                                      # a second tab, same week
        raise HTTPException(status_code=409, detail="This week's check-in is already in")

    stay = _stay(db, pid)
    week0 = next((d for d in _series(db, pid) if int(d.get("week_number", -1)) == 0), {})
    if stay and stay.get("encounter_key") == week0.get("encounter_key"):
        flags = _through_ingest(db, stay, pid, n, today, readings, user["role"])
    else:
        flags = _score_here(db, pid, n, today, readings, user["role"])
    db[COLLECTION].update_one({"_id": record["_id"]}, {"$set": {"red_flags": flags,
                                                                "hospital_id": _hospital(db, pid)}})
    return {**_state(db, pid, today), "sent": True, "urgent": bool(flags),
            "message": URGENT if flags else "Thank you. Your care team will see this.",
            "red_flags": [RED_FLAGS[k][1] for k in flags if k in RED_FLAGS]}


def _hospital(db, pid: str) -> Optional[str]:
    own = db["care_actions"].find_one({"patient_id": {"$in": access.id_variants([pid])}}, {"hospital_id": 1})
    return (own or {}).get("hospital_id")


def _through_ingest(db, stay: dict, pid: str, n: int, today, readings: dict, role: str) -> list:
    """An ingested patient: the answers join the week in ingest_weeks, and the
    worker's own scoring writes the week, refreshes the worklist and alerts."""
    from api import scoring_service
    hospital = stay["hospital_id"]
    when = datetime(today.year, today.month, today.day)
    db[scoring_service.WEEKS].update_one(
        {"hospital_id": hospital, "encounter_key": stay["encounter_key"], "week_number": n},
        {"$set": {**{f"readings.{k}": v for k, v in readings.items()}, "patient_key": stay.get("patient_key"),
                  "patient_id": stay["patient_id"], "week_date": when, "received_at": datetime.utcnow(),
                  "reported_by": role},
         "$setOnInsert": {"source": "patient_app"}},
        upsert=True)
    scoring_service.score_weeks(db, hospital, [stay["patient_id"]])
    doc = db[WEEKLY].find_one({"patient_id": stay["patient_id"], "week_number": n}, {"red_flags": 1})
    return list((doc or {}).get("red_flags") or [])


def _score_here(db, pid: str, n: int, today, readings: dict, role: str) -> list:
    """Anyone else: the monitoring rules against the patient's own series, as
    api/scoring_service.scored_weeks applies them to an ingested week."""
    from api import scoring_service
    from api.worklist_summary import refresh as refresh_summaries
    docs, week0, group, groups, baseline, discharged = _plan(db, pid)
    stored_pid = week0["patient_id"]
    existing = next((d for d in docs if int(d.get("week_number", -1)) == n), None)
    prior = [d for d in docs if 0 < int(d.get("week_number", 0)) < n and d.get("observed", True)]
    prev = prior[-1] if prior else week0
    bands = scoring_service._bands_of(db, _hospital(db, pid) or "", week0.get("model_version"))
    obs = to_observations(readings, baseline)
    if existing and existing.get("observed", True):
        obs = {**(existing.get("monitoring") or {}), **obs}         # the same week from another feed
    score, adj, contribs = score_week(
        float(week0.get("risk_score", 0) or 0), obs, CARRY * float(prev.get("rule_adjustment") or 0),
        variant=variant_for(str(pid), n), group=group, groups=groups,
        prev_score=prev.get("risk_score"), flag_floor=bands["low_score_threshold"])
    lines = ([d for _, d in contribs[:3]] + ["", "", ""])[:3]
    flags = red_flags(obs)
    db[WEEKLY].update_one(
        {"patient_id": stored_pid, "week_number": n},
        {"$set": {"patient_id": stored_pid, "week_number": n,
                  "days_after_discharge": (today - discharged).days, "week_date": today.isoformat(),
                  "risk_score": score, "risk_band": scoring_service._band(score, bands), "observed": True,
                  "driver_1": lines[0], "driver_2": lines[1], "driver_3": lines[2],
                  "monitoring": obs, "red_flags": flags, "rule_adjustment": round(adj, 2),
                  "scored_by": "monitoring_rules", "model_version": week0.get("model_version"),
                  # The series' own source, so whatever replaces the series
                  # (a new discharge, a regenerated simulation) replaces this too.
                  "source": week0.get("source"), "reported_by": role,
                  "clinical_group": group, "group_label": week0.get("group_label"),
                  "group_evidence": week0.get("group_evidence"),
                  "group_confidence": week0.get("group_confidence"),
                  **({"hospital_id": week0["hospital_id"]} if week0.get("hospital_id") else {})}},
        upsert=True)
    batch = get_latest_batch_date(db, "patient_worklist")
    if batch:
        refresh_summaries(db, batch, [pid])
    scoring_service._alerts_for(db, [stored_pid])
    return flags

"""
Outcomes: did the care team act on what Preventra told them, and did fewer
patients come back?

Two halves, kept apart because the first is available on day one and the
second only once 30 days have passed:

ACTING ON IT - from records the app already keeps, over a chosen period
    clinical alerts   raised, acknowledged, answered; hours to acknowledge and
                      to answer (api/doctor_service.py)
    follow-ups        asked for, done, how they ended - booked, unreachable,
                      not needed - and days to done (api/followups.py)
    staying in touch  patients inside the 30-day window now, how many have gone
                      quiet after warning signs (monitoring_rules.contact_gap,
                      by the calendar), and the weekly check-ins patients and
                      caretakers sent (api/checkins.py)
    discharge plans   checklists complete, follow-ups booked, social needs
                      found (api/discharge_plan.py)

READMISSIONS - observed against expected, by the band each patient left with.
The expected rate is the mean discharge score: the model is calibrated
(docs: calibration ratio 1.01 on MIMIC), so a band's scores ARE its expected
readmission rate. A 30-day outcome is known from:

    the hospital's records   ingest_stays.readmitted_30d, which the worker sets
                             from a later admission within 30 days, or "no" once
                             30 days pass without one (scoring_service.link_outcomes)
    the care team            a "Readmitted" / "Not readmitted" recorded on the
                             patient's page (this module), which wins for the same
                             discharge - they may know about another hospital

A readmission to another hospital is invisible unless someone records it, so
the observed rate can only be too LOW. The page says so.

Scoped like everything else: counts over the caller's patients only (the
ScopedDB view), and only for the roles that run a hospital's care
(access.ACTIONS view_outcomes). No dollar figures, so a case manager sees it.
"""
from __future__ import annotations

import statistics
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import HTTPException

from api import access, discharge_plan
from api.db_utils import get_latest_batch_date
from ingestion import scheduler
from models.monitoring_rules import MONITORING_DAYS, _as_date, contact_gap

COLLECTION = "readmission_outcomes"
PERIODS = (7, 30, 90)
BANDS = ("Low", "Medium", "High")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(at) -> Optional[str]:
    if at is None:
        return None
    return (at if at.tzinfo else at.replace(tzinfo=timezone.utc)).isoformat()


def _who(user: dict) -> dict:
    return {"id": str(user["_id"]), "name": user.get("name") or user.get("email", ""), "role": user["role"]}


def ensure_indexes(db) -> None:
    db[COLLECTION].create_index([("patient_id", 1), ("discharge_date", 1)], unique=True)


# ============================================================ one patient
def _current(db, patient_id: str) -> dict:
    batch = get_latest_batch_date(db, "patient_worklist")
    row = db["patient_worklist"].find_one(
        {"patient_id": {"$in": access.id_variants([patient_id])}, "batch_date": batch},
        {"_id": 0, "discharge_date": 1, "risk_score": 1, "risk_band": 1})
    if not row:
        raise HTTPException(status_code=404, detail="Patient not found")
    return row


def _from_records(db, patient_id: str, discharge: str) -> Optional[dict]:
    """What the hospital's own records say about this discharge, if it came in
    by ingestion."""
    for stay in db["ingest_stays"].find({"patient_id": {"$in": access.id_variants([patient_id])}},
                                        {"_id": 0, "readmitted_30d": 1, "outcome_checked_at": 1,
                                         "encounter.discharge_datetime": 1}):
        when = _as_date((stay.get("encounter") or {}).get("discharge_datetime"))
        if when and when.isoformat() == discharge and stay.get("readmitted_30d") is not None:
            return {"readmitted": bool(stay["readmitted_30d"]), "checked_at": stay.get("outcome_checked_at")}
    return None


def patient_outcome(db, user: dict, patient_id: str, today=None) -> dict:
    """The 30-day outcome of the patient's current discharge."""
    patient_id = str(patient_id)
    row = _current(db, patient_id)
    discharge = str(row.get("discharge_date") or "")
    start = _as_date(discharge)
    today = today or scheduler.today()
    doc = db[COLLECTION].find_one({"patient_id": patient_id, "discharge_date": discharge}, {"_id": 0})
    ends = (start + timedelta(days=MONITORING_DAYS)) if start else None
    return {
        "patient_id": patient_id, "discharge_date": discharge,
        "window_ends": ends.isoformat() if ends else None,
        "window_closed": bool(ends and today > ends),
        "recorded": ({"readmitted": doc["readmitted"], "readmit_date": doc.get("readmit_date"),
                      "note": doc.get("note") or "", "by": (doc.get("recorded_by") or {}).get("name"),
                      "at": _iso(doc.get("recorded_at"))} if doc else None),
        "from_records": _from_records(db, patient_id, discharge),
        "can_record": user["role"] in access.ACTIONS["edit_patients"],
    }


def record(db, user: dict, scope: Optional[list], patient_id: str, readmitted: bool,
           readmit_date: Optional[str] = None, note: Optional[str] = None, today=None) -> dict:
    patient_id = str(patient_id)
    access.require_patient(scope, patient_id)
    access.require(user, "edit_patients")
    row = _current(db, patient_id)
    discharge = str(row.get("discharge_date") or "")
    start = _as_date(discharge)
    if start is None:
        raise HTTPException(status_code=409, detail="This patient has no discharge date to count 30 days from")
    today = today or scheduler.today()
    ends = start + timedelta(days=MONITORING_DAYS)
    note = (note or "").strip()
    if len(note) > 300:
        raise HTTPException(status_code=422, detail="Keep the note under 300 characters")
    if readmitted:
        when = _as_date(readmit_date)
        if when is None:
            raise HTTPException(status_code=422, detail="Give the date they were readmitted")
        if not start < when <= ends:
            raise HTTPException(status_code=422, detail=(
                f"A 30-day readmission is between {start.isoformat()} and {ends.isoformat()}"))
        if when > today:
            raise HTTPException(status_code=422, detail="That date is in the future")
        readmit_date = when.isoformat()
    else:
        if today <= ends:
            raise HTTPException(status_code=409, detail=(
                f"The 30 days are not over until {ends.isoformat()}; record a readmission, or come back then"))
        readmit_date = None
    ensure_indexes(db)
    own = db["care_actions"].find_one({"patient_id": {"$in": access.id_variants([patient_id])}},
                                      {"hospital_id": 1}) or {}
    db[COLLECTION].update_one(
        {"patient_id": patient_id, "discharge_date": discharge},
        {"$set": {"readmitted": bool(readmitted), "readmit_date": readmit_date, "note": note,
                  "discharge_score": float(row.get("risk_score") or 0),
                  "discharge_band": str(row.get("risk_band") or "").title() or None,
                  "hospital_id": own.get("hospital_id"), "recorded_by": _who(user), "recorded_at": _now()}},
        upsert=True)
    return patient_outcome(db, user, patient_id, today)


def clear(db, user: dict, scope: Optional[list], patient_id: str) -> dict:
    """Take back an outcome recorded by mistake."""
    patient_id = str(patient_id)
    access.require_patient(scope, patient_id)
    access.require(user, "edit_patients")
    row = _current(db, patient_id)
    db[COLLECTION].delete_one({"patient_id": patient_id, "discharge_date": str(row.get("discharge_date") or "")})
    return patient_outcome(db, user, patient_id)


# ============================================================ the page
def _median(values: list) -> Optional[float]:
    values = [v for v in values if v is not None and v >= 0]
    return round(statistics.median(values), 1) if values else None


def _stamp(value) -> Optional[datetime]:
    """Alert times are stored as local "YYYY-MM-DD HH:MM:SS" strings, follow-up
    times as datetimes. Both, as naive datetimes, for differences."""
    if isinstance(value, datetime):
        return value.replace(tzinfo=None) if value.tzinfo else value
    try:
        return datetime.strptime(str(value or "")[:19], "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None


def _hours(a, b) -> Optional[float]:
    start, end = _stamp(a), _stamp(b)
    return (end - start).total_seconds() / 3600 if start and end else None


def _alerts(view, since: datetime) -> dict:
    docs = list(view["clinical_alerts"].find(
        {"created_at": {"$gte": since.strftime("%Y-%m-%d %H:%M:%S")}},
        {"_id": 0, "status": 1, "created_at": 1, "acknowledged_at": 1, "response.responded_at": 1}))
    answered = [d for d in docs if d.get("status") == "responded"]
    acked = [d for d in docs if d.get("acknowledged_at") or d.get("status") == "responded"]
    return {
        "raised": len(docs),
        "acknowledged": len(acked),
        "answered": len(answered),
        "dismissed": sum(1 for d in docs if d.get("status") == "dismissed"),
        "unrouted": sum(1 for d in docs if d.get("status") == "unrouted"),
        "open": sum(1 for d in docs if d.get("status") in ("pending", "unrouted", "acknowledged", "open")),
        "median_hours_to_acknowledge": _median([_hours(d["created_at"], d["acknowledged_at"])
                                                for d in docs if d.get("acknowledged_at")]),
        "median_hours_to_answer": _median([_hours(d["created_at"], (d.get("response") or {}).get("responded_at"))
                                           for d in answered]),
    }


def _followups(view, since: datetime) -> dict:
    from api.followups import OUTCOMES, OVERDUE_DAYS
    docs = list(view["followups"].find({"requested_at": {"$gte": since}},
                                       {"_id": 0, "status": 1, "outcome": 1, "requested_at": 1, "done_at": 1}))
    done = [d for d in docs if d.get("status") == "done"]
    now = _now().replace(tzinfo=None)
    return {
        "requested": len(docs), "done": len(done),
        "outcomes": [{"key": k, "label": label, "count": sum(1 for d in done if d.get("outcome") == k)}
                     for k, label in OUTCOMES.items()],
        "median_days_to_done": _median([(_hours(d["requested_at"], d["done_at"]) or 0) / 24 for d in done
                                        if d.get("done_at")]),
        "overdue": sum(1 for d in docs if d.get("status") != "done" and _stamp(d["requested_at"])
                       and now - _stamp(d["requested_at"]) > timedelta(days=OVERDUE_DAYS)),
    }


def _contact(db, view, since: datetime, today) -> dict:
    from api.worklist_summary import in_window
    ids = in_window(db, today)
    monitored = overdue = 0
    if ids:
        for doc in view["weekly_monitoring"].aggregate([
                {"$match": {"patient_id": {"$in": ids}}},
                {"$sort": {"week_number": 1}},
                {"$group": {"_id": "$patient_id", "weeks": {"$push": {
                    "week_number": "$week_number", "observed": {"$ifNull": ["$observed", True]},
                    "risk_band": "$risk_band", "red_flags": {"$ifNull": ["$red_flags", []]},
                    "week_date": {"$ifNull": ["$week_date", None]},
                    "days_after_discharge": {"$ifNull": ["$days_after_discharge", None]}}}}}]):
            week0 = next((w for w in doc["weeks"] if w.get("week_number") == 0), None)
            start = _as_date((week0 or {}).get("week_date"))
            if start is None or (today - start).days > MONITORING_DAYS:
                continue
            monitored += 1
            overdue += contact_gap(doc["weeks"], today)["overdue"]
    sent = list(view["checkins"].find({"at": {"$gte": since}}, {"_id": 0, "patient_id": 1, "by_role": 1,
                                                                "red_flags": 1}))
    return {"monitored_now": monitored, "overdue_now": overdue,
            "checkins": len(sent), "checkin_patients": len({c["patient_id"] for c in sent}),
            "by_caretaker": sum(1 for c in sent if c.get("by_role") == "caretaker"),
            "with_red_flags": sum(1 for c in sent if c.get("red_flags"))}


def _discharges(view, since_day: str, batch: Optional[str]) -> list:
    if not batch:
        return []
    return list(view["patient_worklist"].find(
        {"batch_date": batch, "discharge_date": {"$gte": since_day}},
        {"_id": 0, "patient_id": 1, "discharge_date": 1, "risk_band": 1, "risk_score": 1, "clinical_group": 1}))


def _known_outcomes(db, view, batch: Optional[str], today) -> tuple:
    """Every discharge of the caller's patients with a known 30-day outcome:
    [(patient_id, discharge_date, discharge score, band, readmitted, source)],
    and how many discharges are past their 30 days at all."""
    known = {}
    closed_before = (today - timedelta(days=MONITORING_DAYS)).isoformat()
    scope = view.scope
    # The hospital's records, scored by risk_registry.
    stays = list(db["ingest_stays"].find({**access.scope_query(scope), "readmitted_30d": {"$ne": None}},
                                         {"_id": 0, "patient_id": 1, "hospital_id": 1, "encounter_key": 1,
                                          "readmitted_30d": 1, "encounter.discharge_datetime": 1}))
    scores = {}
    if stays:
        for r in db["risk_registry"].find({"encounter_key": {"$in": [s["encounter_key"] for s in stays]}},
                                          {"_id": 0, "hospital_id": 1, "encounter_key": 1, "risk_score": 1,
                                           "risk_band": 1, "scored_at": 1}).sort("scored_at", 1):
            scores[(r.get("hospital_id"), r["encounter_key"])] = r            # the latest wins
    for s in stays:
        reg = scores.get((s.get("hospital_id"), s["encounter_key"]))
        when = _as_date((s.get("encounter") or {}).get("discharge_datetime"))
        if not reg or when is None:
            continue
        known[(str(s["patient_id"]), when.isoformat())] = (
            float(reg["risk_score"]), str(reg.get("risk_band") or "").title(), bool(s["readmitted_30d"]), "records")
    # The care team's own record wins for the same discharge.
    for d in db[COLLECTION].find(access.scope_query(scope), {"_id": 0}):
        known[(str(d["patient_id"]), d["discharge_date"])] = (
            float(d.get("discharge_score") or 0), d.get("discharge_band") or "", bool(d["readmitted"]), "care_team")
    # Every discharge whose outcome could be known by now: the current ones on
    # the worklist past their 30 days, and any with a known outcome - a
    # readmission is known the day it happens, before the 30 days are up.
    closed = {(str(r["patient_id"]), str(r.get("discharge_date")))
              for r in (view["patient_worklist"].find(
                  {"batch_date": batch, "discharge_date": {"$lte": closed_before, "$gt": ""}},
                  {"_id": 0, "patient_id": 1, "discharge_date": 1}) if batch else [])}
    closed |= set(known)
    rows = [(pid, day, *values) for (pid, day), values in known.items()]
    return rows, len(closed)


def _observed_vs_expected(rows: list) -> dict:
    def block(items):
        n = len(items)
        readmitted = sum(1 for r in items if r[4])
        return {"known": n, "readmitted": readmitted,
                "observed_rate": round(100 * readmitted / n, 1) if n else None,
                "expected_rate": round(sum(r[2] for r in items) / n, 1) if n else None,
                "ratio": (round((readmitted / n) / (sum(r[2] for r in items) / n / 100), 2)
                          if n and sum(r[2] for r in items) else None)}
    return {"overall": block(rows),
            "bands": [{"band": b, **block([r for r in rows if r[3] == b])} for b in BANDS]}


def page(db, view, user: dict, days: int = 30, today=None) -> dict:
    access.require(user, "view_outcomes")
    if days not in PERIODS:
        raise HTTPException(status_code=422, detail=f"Choose a period of {', '.join(map(str, PERIODS))} days")
    today = today or scheduler.today()
    since = _now() - timedelta(days=days)
    batch = get_latest_batch_date(db, "patient_worklist")

    discharged = _discharges(view, (today - timedelta(days=days)).isoformat(), batch)
    rows, closed = _known_outcomes(db, view, batch, today)
    plans = discharge_plan.complete_for(db, [(r[0], r[1]) for r in rows])
    with_plan = [r for r in rows if (r[0], r[1]) in plans]
    by_checklist = {
        "complete": _observed_vs_expected([r for r in with_plan if plans[(r[0], r[1])]])["overall"],
        "incomplete": _observed_vs_expected([r for r in with_plan if not plans[(r[0], r[1])]])["overall"],
    }
    return {
        "days": days, "periods": list(PERIODS), "as_of": today.isoformat(),
        # Alert times are the server's local clock (doctor_service._now).
        "alerts": _alerts(view, datetime.now() - timedelta(days=days)),
        "followups": _followups(view, since),
        "contact": _contact(db, view, since, today),
        "discharge_planning": discharge_plan.summary(db, discharged),
        "readmissions": {
            **_observed_vs_expected(rows),
            "past_30_days": closed,
            "by_checklist": by_checklist,
            "sources": {"records": sum(1 for r in rows if r[5] == "records"),
                        "care_team": sum(1 for r in rows if r[5] == "care_team")},
        },
    }

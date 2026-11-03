"""
Outcomes: did the care team act on what Preventra told them, and did fewer
patients come back?

Two halves, kept apart because the first is available on day one and the
second only once 30 days have passed:

ACTING ON IT - from records the app already keeps, over a chosen period
    clinical alerts   raised, acknowledged, answered; hours to acknowledge and
                      to answer (api/doctor_service.py) - only alerts raised
                      during the patient's 30 days (plus a week), not a sweep
                      over historical data
    follow-ups        asked for, done, how they ended - booked, unreachable,
                      not needed - and days to done (api/followups.py)
    staying in touch  patients inside the 30-day window now, how many have gone
                      quiet after warning signs (monitoring_rules.contact_gap,
                      by the calendar), and the weekly check-ins patients and
                      caretakers sent (api/checkins.py)
    discharge plans   checklists complete, follow-ups booked, social needs
                      found (api/discharge_plan.py)
    mental health     patients who answered the weekly mood questions, how many
                      screened positive, and how many were referred
                      (models/mood_screen.py). Screens, not diagnoses; and where
                      it is compared with readmission it is co-occurrence, not
                      cause.

READMISSIONS - observed against expected, by the band each patient left with.
The expected rate is the mean discharge score: the model is calibrated
(docs: calibration ratio 1.01 on MIMIC), so a band's scores ARE its expected
readmission rate. A 30-day outcome is known from:

    the hospital's records   ingest_stays.readmitted_30d, which the worker sets
                             from a later admission within 30 days, or "no" once
                             30 days pass without one (scoring_service.link_outcomes)
    the care team            a "Readmitted" / "Not readmitted" recorded on the
                             patient's page (this module), which wins for the same
                             discharge - they may know about another hospital.
                             Marked `demo` when scripts/prepare_demo.py --count
                             simulated it; the page says how many

A readmission to another hospital is invisible unless someone records it, so
the observed rate can only be too LOW. The page says so.

WHO COMES BACK - every known outcome again, readmitted against not readmitted:
by who the patient was (age, sex, insurance, earlier admissions, where they went),
by condition (with and without each monitoring group), and by the warning signs
at their last weekly reading before the readmission or day 30. Descriptive: a
difference says two things went together, never that one caused the other. Any
group of 1-10 patients is withheld on the server (MIN_GROUP).

Scoped like everything else: counts over the caller's patients only (the
ScopedDB view), and only for the roles that run a hospital's care
(access.ACTIONS view_outcomes). No dollar figures, so a case manager sees it.
"""
from __future__ import annotations

import math
import statistics
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import HTTPException

from api import access, discharge_plan
from api.db_utils import get_latest_batch_date
from ingestion import scheduler
from models.monitoring_rules import (
    CONTACT_DUE_DAYS, MONITORING_DAYS, SIGNAL_RULES, _as_date, contact_gap, profile_for, red_flags)

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
                  "hospital_id": own.get("hospital_id"), "recorded_by": _who(user), "recorded_at": _now()},
         # The care team's word replaces a demo script's simulated one.
         "$unset": {"demo": "", "outcome_simulated": ""}},
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


def _during_monitoring(alert: dict) -> bool:
    """Raised while the patient was being monitored. A sweep that forecasts
    historical data raises alerts about discharges years old - nobody could
    have acted on those, so they are not counted. Without a discharge date on
    the alert, it counts."""
    discharged = _as_date((alert.get("patient") or {}).get("discharge_date"))
    raised = _as_date(alert.get("created_at"))
    return discharged is None or raised is None or (raised - discharged).days <= MONITORING_DAYS + 7


def _alerts(view, since: datetime) -> dict:
    docs = [d for d in view["clinical_alerts"].find(
        {"created_at": {"$gte": since.strftime("%Y-%m-%d %H:%M:%S")}},
        {"_id": 0, "status": 1, "created_at": 1, "acknowledged_at": 1, "response.responded_at": 1,
         "patient.discharge_date": 1}) if _during_monitoring(d)]
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
    docs = list(view["checkins"].find({"at": {"$gte": since}}, {"_id": 0, "patient_id": 1, "by_role": 1,
                                                                "red_flags": 1, "simulated": 1}))
    # A record a script wrote for the demo (scripts/prepare_demo.py) is not a patient sending anything.
    sent = [c for c in docs if not c.get("simulated")]
    return {"monitored_now": monitored, "overdue_now": overdue,
            "checkins": len(sent), "checkin_patients": len({c["patient_id"] for c in sent}),
            "by_caretaker": sum(1 for c in sent if c.get("by_role") == "caretaker"),
            "with_red_flags": sum(1 for c in sent if c.get("red_flags")),
            "checkins_simulated": len(docs) - len(sent)}


def _mental_health(view, since: datetime, rows: list) -> dict:
    """The mood screens patients answered in their weekly check-ins (flag only:
    nothing here changes a risk score). `by_screen` compares readmission for
    discharges whose patient screened positive with those who screened and did
    not - every known outcome, whatever the period, because a period of a few
    weeks holds too few to compare."""
    from models.mood_screen import PHQ2_POSITIVE, PHQ8_MODERATE
    screens = [c for c in view["checkins"].find({"mood": {"$exists": True}},
                                                {"_id": 0, "patient_id": 1, "discharge_date": 1, "at": 1, "mood": 1,
                                                 "simulated": 1})
               if (c.get("mood") or {}).get("phq2") is not None]
    cutoff = _stamp(since)
    recent = [c for c in screens if _stamp(c.get("at")) and _stamp(c["at"]) >= cutoff]
    positive = {str(c["patient_id"]) for c in recent if c["mood"]["phq2"] >= PHQ2_POSITIVE}
    moderate = {str(c["patient_id"]) for c in recent if (c["mood"].get("phq8") or 0) >= PHQ8_MODERATE}
    # Referred in the period: asked for then, or added to a request that was already
    # open by a doctor's alert reply (a history entry carrying the reason).
    def referred_since(f):
        return any(_stamp(e.get("at")) and _stamp(e["at"]) >= cutoff for e in
                   [{"at": f.get("requested_at")}] + [h for h in f.get("history") or [] if h.get("reason")])
    referred = {str(f["patient_id"]) for f in view["followups"].find(
        {"reason": "behavioural_health"}, {"_id": 0, "patient_id": 1, "requested_at": 1, "history": 1})
        if referred_since(f)}
    screen = {}
    for c in screens:
        key = (str(c["patient_id"]), str(c.get("discharge_date")))
        screen[key] = screen.get(key, False) or c["mood"]["phq2"] >= PHQ2_POSITIVE
    # Demo answers (scripts/prepare_demo.py) still count here, but are said to be simulated.
    def is_sim(c):
        return bool(c.get("simulated") or c["mood"].get("simulated"))
    sim_discharges = {(str(c["patient_id"]), str(c.get("discharge_date"))) for c in screens if is_sim(c)}
    return {
        "screened": len({str(c["patient_id"]) for c in recent}),
        "simulated": len({str(c["patient_id"]) for c in recent if is_sim(c)}),
        "simulated_compared": sum(1 for r in rows if (r[0], r[1]) in sim_discharges),
        "positive": len(positive), "moderate_or_worse": len(moderate), "referred": len(referred),
        "by_screen": {
            "positive": _observed_vs_expected([r for r in rows if screen.get((r[0], r[1])) is True])["overall"],
            "negative": _observed_vs_expected([r for r in rows if screen.get((r[0], r[1])) is False])["overall"]},
    }


def _discharges(view, since_day: str, batch: Optional[str]) -> list:
    if not batch:
        return []
    return list(view["patient_worklist"].find(
        {"batch_date": batch, "discharge_date": {"$gte": since_day}},
        {"_id": 0, "patient_id": 1, "discharge_date": 1, "risk_band": 1, "risk_score": 1, "clinical_group": 1}))


def _known_outcomes(db, view, batch: Optional[str], today) -> tuple:
    """Every discharge of the caller's patients with a known 30-day outcome:
    [(patient_id, discharge_date, discharge score, band, readmitted, source, extra)],
    and how many discharges are past their 30 days at all. `extra` carries what
    the who-comes-back panels need: the readmission date if known, the
    hospital, and the earlier admissions."""
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
                                           "risk_band": 1, "scored_at": 1, "n_prior_adm": 1}).sort("scored_at", 1):
            scores[(r.get("hospital_id"), r["encounter_key"])] = r            # the latest wins
    for s in stays:
        reg = scores.get((s.get("hospital_id"), s["encounter_key"]))
        when = _as_date((s.get("encounter") or {}).get("discharge_datetime"))
        if not reg or when is None:
            continue
        known[(str(s["patient_id"]), when.isoformat())] = (
            float(reg["risk_score"]), str(reg.get("risk_band") or "").title(), bool(s["readmitted_30d"]), "records",
            {"readmit_date": None, "hospital_id": s.get("hospital_id"), "n_prior_adm": reg.get("n_prior_adm"),
             "demo": False})
    # The care team's own record wins for the same discharge.
    for d in db[COLLECTION].find(access.scope_query(scope), {"_id": 0}):
        key = (str(d["patient_id"]), d["discharge_date"])
        earlier = known[key][-1]["n_prior_adm"] if key in known else None      # the records still know this
        known[key] = (
            float(d.get("discharge_score") or 0), d.get("discharge_band") or "", bool(d["readmitted"]), "care_team",
            {"readmit_date": d.get("readmit_date"), "hospital_id": d.get("hospital_id"), "n_prior_adm": earlier,
             "demo": bool(d.get("demo"))})
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


# ============================================================ who comes back
MIN_GROUP = 11   # CMS-style small-cell rule: a count of 1-10 patients is never sent, so never shown
BLOCK = ("known", "readmitted", "observed_rate", "expected_rate", "ratio")
# The first band is "Under 45", not "18-44": a typed patient may be younger than 18
# (manual_patients.RANGES allows 0-120), and MIMIC's censored 91 and the ingest cap of 90
# both land in "85 and over".
AGE_BANDS = ((44, "under_45", "Under 45"), (64, "45_64", "45-64"), (74, "65_74", "65-74"),
             (84, "75_84", "75-84"), (None, "85_plus", "85 and over"))
DIMENSIONS = (
    ("age", "Age", [(k, label) for _, k, label in AGE_BANDS] + [("unknown", "Not recorded")]),
    ("sex", "Sex", [("female", "Female"), ("male", "Male"), ("unknown", "Other or not recorded")]),
    ("insurance", "Insurance", [("medicare", "Medicare"), ("medicaid", "Medicaid"), ("private", "Private"),
                                ("other", "Other or self-pay"), ("unknown", "Not recorded")]),
    ("prior", "Admissions before this one", [("0", "None"), ("1", "1"), ("2", "2"), ("3_plus", "3 or more"),
                                             ("unknown", "Not recorded")]),
    ("destination", "Discharged to", [("home", "Home"), ("facility", "Nursing or rehab facility"),
                                      ("ama", "Left against medical advice"), ("other", "Somewhere else"),
                                      ("unknown", "Not recorded")]),
)
# key, label, what counts - each an existing threshold (models/monitoring_rules.py, models/mood_screen.py)
SIGNS = (
    ("weight_gain", "Weight gain", "1 kg or more above the discharge weight (2 kg in cancer care)"),
    ("missed_medicines", "Missed medicines", "under 80% of doses taken, or a refill not collected"),
    ("low_oxygen", "Low oxygen", "under 92% (under 88% in chronic lung disease)"),
    ("missed_followup", "Missed follow-up", "the booked review was not attended"),
    ("no_contact", "No recent contact", f"no readings for over {CONTACT_DUE_DAYS} days"),
    ("red_flag", "A red-flag answer", "a reading that needs assessing the same day"),
    ("low_mood", "Low mood", "PHQ-2 of 3 or more, or PHQ-8 of 10 or more"),
)


def _number(value) -> Optional[float]:
    """A stored value as a number, or None when it is blank or not one."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(number) else number


def _first(*values):
    """The first value that was actually recorded."""
    return next((v for v in values if v is not None and v != "" and not (isinstance(v, float) and math.isnan(v))),
                None)


def _age_band(value) -> str:
    age = _number(value)
    if age is None or age < 0:
        return "unknown"
    return next(key for upper, key, _ in AGE_BANDS if upper is None or age <= upper)


def _sex(value) -> str:
    text = str(value or "").strip().upper()
    return "female" if text in ("F", "FEMALE") else "male" if text in ("M", "MALE") else "unknown"


def _insurance(value) -> str:
    text = str(value or "").strip().upper().replace(" ", "_")
    if text in ("MEDICARE", "MEDICAID", "PRIVATE"):
        return text.lower()
    return "other" if text in ("OTHER", "SELF_PAY", "NO_CHARGE") else "unknown"


def _prior(value) -> str:
    count = _number(value)
    if count is None or count < 0 or math.isinf(count):
        return "unknown"
    return str(int(count)) if count < 3 else "3_plus"


def _destination(disposition, flags: Optional[dict]) -> str:
    """Where the patient went: the hospital's own word for it, else the three
    yes/no inputs the model was given (home, nursing or rehab facility, left
    against advice)."""
    from ingestion.vocab import SNF_DISPOSITIONS
    if disposition:
        text = str(disposition).upper()
        return ("home" if text in ("HOME", "HOME_WITH_SERVICES") else "facility" if text in SNF_DISPOSITIONS
                else "ama" if text == "AMA" else "other")
    ama, snf, home = (_number((flags or {}).get(k)) for k in ("disch_ama", "disch_snf", "disch_home"))
    if ama is None and snf is None and home is None:
        return "unknown"
    return "ama" if ama else "facility" if snf else "home" if home else "other"


def _signs_at(last: dict, quiet: bool) -> dict:
    """Which warning signs a weekly reading showed: True, False, or None when
    that sign was not reported - a blank is never counted as a no. Each uses
    the threshold the monitoring rules and the mood screen already use."""
    from models.mood_screen import LOW_ADHERENCE, PHQ2_POSITIVE, PHQ8_MODERATE
    obs = last.get("monitoring") or {}
    rules = profile_for(last.get("clinical_group") or "general")["rules"]
    kg, spo2 = _number(obs.get("weight_change_kg")), _number(obs.get("spo2"))
    adherence, refill = _number(obs.get("adherence_pct")), obs.get("refill_status")
    followup = obs.get("followup_status")
    mood = last.get("mood") or {}
    phq2, phq8 = _number(mood.get("phq2")), _number(mood.get("phq8"))
    weight_rule = rules.get("weight_change_kg", SIGNAL_RULES["weight_change_kg"][2])
    oxygen_rule = rules.get("spo2", SIGNAL_RULES["spo2"][2])
    return {
        "weight_gain": None if kg is None else kg > 0 and weight_rule(kg)[0] > 0,
        "missed_medicines": (None if adherence is None and refill is None
                             else (adherence is not None and adherence < LOW_ADHERENCE) or refill == "missed"),
        "low_oxygen": None if spo2 is None else oxygen_rule(spo2)[1] in ("low", "very_low"),
        "missed_followup": None if followup is None else followup == "missed",
        "no_contact": bool(quiet),
        "red_flag": bool(last.get("red_flags")),
        "low_mood": None if phq2 is None else phq2 >= PHQ2_POSITIVE or (phq8 or 0) >= PHQ8_MODERATE,
    }


def _last_reading(weeks: list, discharged, until, readmitted: bool) -> tuple:
    """(the last reading that was taken, whether contact had gone quiet by `until`).
    Readings after the discharge and before the day the patient came back, or up
    to day 30 for a patient who did not; a week with nothing in it is not a reading."""
    inside = []
    for w in weeks or []:
        day = _as_date(w.get("week_date"))
        if day is None or int(w.get("week_number") or 0) <= 0 or day <= discharged:
            continue
        if day < until if readmitted else day <= until:
            inside.append((day, int(w["week_number"]), w))
    inside = [w for _, _, w in sorted(inside, key=lambda t: (t[0], t[1]))]
    seen = [w for w in inside if w.get("observed") is not False]
    if not seen:
        return None, False
    # The discharge itself counts as the first contact, so the calendar check has a start.
    gap = contact_gap([{"week_number": 0, "week_date": discharged.isoformat(), "observed": True}] + inside, until)
    return seen[-1], gap["weeks_without_contact"] > 0


def _facts(db, view, rows: list, batch: Optional[str]) -> dict:
    """What is known about each discharge in `rows`, by (patient, day): who the
    patient was, their conditions, and the last reading before the readmission
    or day 30. The only part of this module that reads more than the rows - at
    most five reads, whatever the number of patients.

    ingest_stays and ingest_weeks are not scoped by patient, so they are asked
    only for the caller's patient ids AND the caller's hospital."""
    from api import scoring_service
    from features.weekly_rows import to_observations
    from models.icd_groups import classify, classify_all

    pids = sorted({str(r[0]) for r in rows})
    ids = access.id_variants(pids)
    own = access.hospital_of(view.user)
    hospitals = [own] if own else sorted({r[6]["hospital_id"] for r in rows if r[6].get("hospital_id")}, key=str)
    by_patient: dict = {}
    for s in (db["ingest_stays"].find(
            {"hospital_id": {"$in": hospitals}, "patient_id": {"$in": ids}},
            {"_id": 0, "patient_id": 1, "hospital_id": 1, "encounter_key": 1, "encounter.admit_datetime": 1,
             "encounter.discharge_datetime": 1, "encounter.age_years": 1, "encounter.payer_class": 1,
             "encounter.discharge_disposition": 1, "patient.sex": 1, "diagnoses": 1, "discharge_vitals": 1})
            if hospitals else []):
        by_patient.setdefault(str(s["patient_id"]), []).append(s)
    current = {str(w["patient_id"]): w for w in (view["patient_worklist"].find(
        {"batch_date": batch, "patient_id": {"$in": ids}},
        {"_id": 0, "patient_id": 1, "discharge_date": 1, "anchor_age": 1, "gender": 1, "n_prior_adm": 1,
         "clinical_group": 1, "clinical_groups": 1, "raw_inputs": 1}) if batch else [])}
    stored = {str(m["patient_id"]): m.get("features") or {} for m in view["model_inputs"].find(
        {"patient_id": {"$in": ids}, "current": True},
        {"_id": 0, "patient_id": 1, "features.insurance": 1, "features.disch_home": 1, "features.disch_snf": 1,
         "features.disch_ama": 1, "features.anchor_age": 1, "features.gender": 1, "features.n_prior_adm": 1})}

    facts, stays = {}, {}
    for pid, day, _score, _band, readmitted, _source, extra in rows:
        pid, start = str(pid), _as_date(day)
        stay = next((s for s in by_patient.get(pid, [])
                     if start and _as_date((s.get("encounter") or {}).get("discharge_datetime")) == start
                     and (not extra.get("hospital_id") or s.get("hospital_id") == extra["hospital_id"])), None)
        enc = (stay or {}).get("encounter") or {}
        row = current.get(pid) if str((current.get(pid) or {}).get("discharge_date")) == day else None
        typed = (row or {}).get("raw_inputs") or {}
        model = stored.get(pid, {}) if row else {}
        row = row or {}
        if stay and stay.get("diagnoses"):
            code, title, sec_titles, sec_codes, _ = scoring_service._diagnoses(stay)
            groups = [m["group"] for m in classify_all(code, title, sec_titles, sec_codes)]
            plan = classify(code, title, sec_titles, sec_codes)["group"]
        elif row:
            groups = [g for g in (row.get("clinical_groups") or [row.get("clinical_group")]) if g and g != "general"]
            plan = row.get("clinical_group") or "general"
        else:
            groups, plan = None, "general"
        # When a patient came back: the care team's date, else the hospital's next admission within 30 days.
        back = _as_date(extra.get("readmit_date")) if readmitted else None
        if readmitted and back is None and stay:
            left = _stamp(enc.get("discharge_datetime"))
            later = [_stamp((o.get("encounter") or {}).get("admit_datetime")) for o in by_patient[pid]
                     if o is not stay and o.get("hospital_id") == stay.get("hospital_id")]
            later = [a for a in later if a and left and a > left]
            back = min(later).date() if later else None
            if back and back > start + timedelta(days=MONITORING_DAYS):
                back = None
        until = back if readmitted else (start + timedelta(days=MONITORING_DAYS) if start else None)
        flags = typed if any(typed.get(k) is not None for k in ("disch_home", "disch_snf", "disch_ama")) else model
        facts[(pid, day)] = {
            "age": _age_band(_first(enc.get("age_years"), row.get("anchor_age"), typed.get("anchor_age"),
                                    model.get("anchor_age"))),
            "sex": _sex(_first(((stay or {}).get("patient") or {}).get("sex"), row.get("gender"), typed.get("gender"),
                               model.get("gender"))),
            "insurance": _insurance(_first(enc.get("payer_class"), typed.get("insurance"), model.get("insurance"))),
            "prior": _prior(_first(extra.get("n_prior_adm"), row.get("n_prior_adm"), typed.get("n_prior_adm"),
                                   model.get("n_prior_adm"))),
            "destination": _destination(enc.get("discharge_disposition"), flags),
            "groups": groups, "plan": plan, "start": start, "until": until, "readmitted": bool(readmitted),
            "last": None, "signs": None, "simulated": False}
        stays[(pid, day)] = stay

    # The weeks: an ingested discharge's come from ingest_weeks (scoring a readmission deletes its
    # series from weekly_monitoring), everyone else's from weekly_monitoring.
    dated = [k for k, f in facts.items() if f["start"] and f["until"]]
    keys = sorted({stays[k]["encounter_key"] for k in dated if stays[k]})
    ingested: dict = {}
    for w in (db["ingest_weeks"].find(
            {"hospital_id": {"$in": hospitals}, "encounter_key": {"$in": keys}},
            {"_id": 0, "hospital_id": 1, "encounter_key": 1, "week_number": 1, "week_date": 1, "readings": 1,
             "mood": 1}) if keys else []):
        ingested.setdefault((w.get("hospital_id"), w["encounter_key"]), []).append(w)
    plain = sorted({k[0] for k in dated if not stays[k]})
    weekly: dict = {}
    for w in (view["weekly_monitoring"].find(
            {"patient_id": {"$in": access.id_variants(plain)}, "week_number": {"$gt": 0}},
            {"_id": 0, "patient_id": 1, "week_number": 1, "week_date": 1, "observed": 1, "monitoring": 1,
             "red_flags": 1, "mood": 1, "source": 1, "clinical_group": 1}) if plain else []):
        weekly.setdefault(str(w["patient_id"]), []).append(w)

    for key in dated:
        f, stay = facts[key], stays[key]
        if stay:
            baseline = scoring_service._baseline(stay, f["plan"])
            weeks = []
            for w in ingested.get((stay.get("hospital_id"), stay["encounter_key"]), []):
                obs = to_observations(w.get("readings") or {}, baseline)
                weeks.append({"week_number": w["week_number"], "week_date": w.get("week_date"), "observed": True,
                              "monitoring": obs, "red_flags": red_flags(obs), "mood": w.get("mood"),
                              "source": "ingest", "clinical_group": f["plan"]})
        else:
            weeks = [{**w, "clinical_group": w.get("clinical_group") or f["plan"]} for w in weekly.get(key[0], [])]
        last, quiet = _last_reading(weeks, f["start"], f["until"], f["readmitted"])
        if last:
            f["last"], f["signs"] = last, _signs_at(last, quiet)
            f["simulated"] = last.get("source") == "simulated" or bool((last.get("mood") or {}).get("simulated"))
    return facts


def _cell(rows: list) -> dict:
    """Observed against expected for one group, or nothing at all when the
    group is 1-10 patients."""
    block = _observed_vs_expected(rows)["overall"]
    if 0 < block["known"] < MIN_GROUP:
        return {**block, **{k: None for k in BLOCK}, "hidden": "small"}
    return {**block, "hidden": None}


def _blank(cell: dict, why: str) -> dict:
    return {**cell, **{k: None for k in BLOCK}, "hidden": why}


def _who_came_back(rows: list, facts: dict) -> list:
    """Panel 1: the readmission rate for each group of each dimension. When exactly
    one group is hidden, the smallest group left is hidden too: the totals are on
    the page, so a single hidden group could be worked out from them."""
    out = []
    for key, label, groups in DIMENSIONS:
        cells = [{"key": g, "label": name, **_cell([r for r in rows if facts[(r[0], r[1])][key] == g])}
                 for g, name in groups]
        shown = [c for c in cells if c["hidden"] is None and c["known"] > 0]
        if sum(1 for c in cells if c["hidden"] == "small") == 1 and shown:
            smallest = min(shown, key=lambda c: c["known"])
            cells[cells.index(smallest)] = _blank(smallest, "complement")
        out.append({"key": key, "label": label, "groups": cells})
    return out


def _disease_mix(rows: list, facts: dict) -> dict:
    """Panel 2: the readmission rate with and without each monitoring group. A
    patient with several conditions counts under each. If either side is 1-10
    patients, both are hidden - the other could be worked out from the total.
    The same goes for the discharges with no diagnoses on record: if they, or
    the ones with, number 1-10, nothing is shown, because either could be
    worked out from the other and the total."""
    from models.icd_groups import GROUP_LABELS
    known = [r for r in rows if facts[(r[0], r[1])]["groups"] is not None]
    conditions = []
    for group, label in GROUP_LABELS.items():
        if group == "general":
            continue
        has = [group in facts[(r[0], r[1])]["groups"] for r in known]
        if not any(has):
            continue
        with_ = _cell([r for r, yes in zip(known, has) if yes])
        without = _cell([r for r, yes in zip(known, has) if not yes])
        hidden = "small" if with_["hidden"] or without["hidden"] else None
        if hidden:
            with_, without = _blank(with_, hidden), _blank(without, hidden)
        conditions.append({"key": group, "label": label, "with": with_, "without": without, "hidden": hidden})
    if any(0 < n < MIN_GROUP for n in (len(known), len(rows) - len(known))):
        conditions = [{**c, "with": _blank(c["with"], "small"), "without": _blank(c["without"], "small"),
                       "hidden": "small"} for c in conditions]
        return {"known": None, "unknown": None, "conditions": conditions}
    conditions.sort(key=lambda c: (c["hidden"] is not None, -(c["with"]["known"] or 0)))
    return {"known": len(known), "unknown": len(rows) - len(known), "conditions": conditions}


def _readings_before(rows: list, facts: dict) -> dict:
    """Panel 3: how often each warning sign showed at the last reading before the
    readmission (or before day 30), readmitted against not. Each share is of the
    patients whose reading reported that sign. A sign reported by 1-10 patients
    in either group, or left unreported by 1-10, is hidden in both."""
    sides, undated = {True: [], False: []}, 0
    for r in rows:
        f = facts[(r[0], r[1])]
        if f["until"] is None:
            undated += 1 if r[4] else 0         # a readmission nobody dated cannot be split into before and after
            continue
        sides[bool(r[4])].append(f)
    read = {k: [f for f in side if f["last"]] for k, side in sides.items()}
    none = {k: len(sides[k]) - len(read[k]) for k in sides}
    # Every count here is a part of a total that is already on the page (the overall
    # readmitted and not-readmitted), so a part of 1-10 would be worked out from the
    # others. If any is, none of them is sent, and nor is the comparison.
    withheld = any(0 < n < MIN_GROUP for n in (len(read[True]), len(read[False]), none[True], none[False], undated))

    def sent(n):
        return None if withheld else n
    summary = {name: {"known": sent(len(sides[k])), "with_readings": sent(len(read[k])), "no_readings": sent(none[k])}
               for k, name in ((True, "readmitted"), (False, "not_readmitted"))}
    hidden = "small" if withheld or min(len(read[True]), len(read[False])) < MIN_GROUP else None
    signs = []
    for key, label, detail in ([] if hidden else SIGNS):
        cells, small = {}, False
        for k, name in ((True, "readmitted"), (False, "not_readmitted")):
            values = [f["signs"][key] for f in read[k] if f["signs"][key] is not None]
            cells[name] = {"reported": len(values), "with": sum(values),
                           "share": round(100 * sum(values) / len(values), 1) if values else None}
            # Those who did not report it are a group too: the readings less the reports.
            small = small or 0 < len(values) < MIN_GROUP or 0 < len(read[k]) - len(values) < MIN_GROUP
        # Those who had it, and those who did not, are each a group across both sides.
        yes = cells["readmitted"]["with"] + cells["not_readmitted"]["with"]
        no = (cells["readmitted"]["reported"] - cells["readmitted"]["with"]
              + cells["not_readmitted"]["reported"] - cells["not_readmitted"]["with"])
        small = small or 0 < yes < MIN_GROUP or 0 < no < MIN_GROUP
        if small:
            cells = {name: {"reported": None, "with": None, "share": None} for name in cells}
        a, b = cells["readmitted"]["share"], cells["not_readmitted"]["share"]
        signs.append({"key": key, "label": label, "detail": detail, **cells,
                      "ratio": round(a / b, 2) if a is not None and b else None,
                      "hidden": "small" if small else None})

    def order(sign):
        a, b = sign["readmitted"]["share"], sign["not_readmitted"]["share"]
        if sign["hidden"] or a is None or b is None:
            return (2, 0)
        if b == 0:
            return (0, -math.inf) if a > 0 else (1, 0)     # only among the readmitted comes first
        return (1, -(a / b))
    signs.sort(key=order)
    fake = sum(1 for k in read for f in read[k] if f["simulated"])
    real = len(read[True]) + len(read[False]) - fake
    return {**summary, "undated": sent(undated),
            "simulated": None if withheld or any(0 < n < MIN_GROUP for n in (fake, real)) else fake,
            "hidden": hidden, "signs": signs}


def _readmission_detail(db, view, rows: list, batch: Optional[str]) -> dict:
    """The three who-comes-back panels. Below MIN_GROUP known outcomes nothing is
    compared at all, and nothing more is read."""
    if len(rows) < MIN_GROUP:
        return {"min_group": MIN_GROUP, "known": len(rows), "enough": False}
    facts = _facts(db, view, rows, batch)
    return {"min_group": MIN_GROUP, "known": len(rows), "enough": True,
            "overall": _observed_vs_expected(rows)["overall"],
            "demo": sum(1 for r in rows if r[6].get("demo")),
            "who": _who_came_back(rows, facts), "conditions": _disease_mix(rows, facts),
            "last_readings": _readings_before(rows, facts)}


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
        "mental_health": _mental_health(view, since, rows),
        "readmission_detail": _readmission_detail(db, view, rows, batch),
        "readmissions": {
            **_observed_vs_expected(rows),
            "past_30_days": closed,
            "by_checklist": by_checklist,
            "sources": {"records": sum(1 for r in rows if r[5] == "records"),
                        "care_team": sum(1 for r in rows if r[5] == "care_team" and not r[6].get("demo"))},
            "demo": sum(1 for r in rows if r[6].get("demo")),
        },
    }

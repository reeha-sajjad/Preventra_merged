"""
The clinician loop: registry, alert routing, and the doctor's response.

THE FLOW
--------
    weekly monitoring  ->  models/early_warning.forecast()
                       ->  create_alert()        one alert per patient per week
                       ->  route()               to a registered doctor
                       ->  doctor's inbox        acknowledge, then respond
                       ->  recommendation        back onto the patient record

WHY A SEPARATE COLLECTION FROM `alerts`
---------------------------------------
`alerts` already exists and means something specific: a coordinator changed a
patient's score by hand and it jumped a band. That is a record of an edit.
These are forecasts addressed to a named clinician, with an acknowledgement and
a reply. Same word, different object, different lifecycle - merging them would
mean one collection whose documents only half make sense whichever way you read
them.

IDENTITY IS A DIRECTORY, NOT AUTHENTICATION
-------------------------------------------
Sign-in was removed from this build. `register_doctor` creates a directory
entry and the console picks one; nothing verifies that the person clicking is
the doctor they selected. That is fine for a demonstration and is NOT fine for
real patient data. Everything here is written so that adding real authentication
later means checking the caller against `doctor_id` at the endpoint - the data
model already carries who did what and when.
"""

from __future__ import annotations

import re
import uuid
from datetime import datetime
from typing import Optional

DOCTORS = "doctors"
CLINICAL_ALERTS = "clinical_alerts"
NOTIFICATIONS = "notifications"

# Which specialty a condition routes to when no doctor has claimed the group
# explicitly. Deliberately coarse: this decides who gets paged, and a mapping
# fine enough to be clever is also fine enough to send a heart-failure alert
# somewhere nobody is reading.
SPECIALTY_FOR_GROUP = {
    "heart_failure":    "Cardiology",
    "cardiac_other":    "Cardiology",
    "renal":            "Nephrology",
    "respiratory":      "Pulmonology",
    "diabetes":         "Endocrinology",
    "oncology":         "Oncology",
    "neuro_stroke":     "Neurology",
    "sepsis_infection": "Infectious Disease",
    "surgical_injury":  "Surgery",
    "mental_health":    "Psychiatry",
    "general":          "Internal Medicine",
}

# The structured half of a doctor's reply. Free text says why; these say what,
# so the response can be counted, filtered and handed to a coordinator as a task
# rather than read as prose by whoever happens to open it.
RECOMMENDED_ACTIONS = {
    "contact_patient":     "Telephone the patient",
    "schedule_review":     "Bring forward the clinic review",
    "adjust_medication":   "Medication change required",
    "arrange_labs":        "Arrange bloods or investigations",
    "home_visit":          "Arrange a home visit",
    "increase_monitoring": "Increase monitoring frequency",
    "escalate_ed":         "Send to the emergency department",
    "refer_specialist":    "Refer to another specialty",
    "no_action":           "No action needed at this time",
}

URGENCIES = ("routine", "urgent", "immediate")

# Only these severities are worth a clinician's inbox. A "moderate" forecast is
# real information and belongs on the patient's page, but paging a consultant
# for every patient drifting a few points a week is how an alerting system gets
# switched off within a fortnight.
ALERTABLE_SEVERITIES = ("high", "critical")

_STATUS_OPEN = ("pending", "acknowledged")


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (name or "").strip().lower()).strip("-") or "doctor"


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

def register_doctor(db, name: str, specialty: str, email: str,
                    clinical_groups: Optional[list] = None) -> dict:
    """
    Add a clinician to the directory.

    Email is the natural key - re-registering the same address updates the
    existing entry rather than creating a second doctor who will then receive
    half the alerts.
    """
    name = (name or "").strip()
    email = (email or "").strip().lower()
    specialty = (specialty or "").strip()
    if not name:
        raise ValueError("A doctor's name is required.")
    if not email or "@" not in email:
        raise ValueError("A valid email address is required.")
    if not specialty:
        raise ValueError("A specialty is required so alerts can be routed.")

    groups = [g for g in (clinical_groups or []) if g in SPECIALTY_FOR_GROUP]

    existing = db[DOCTORS].find_one({"email": email})
    if existing:
        db[DOCTORS].update_one(
            {"_id": existing["_id"]},
            {"$set": {"name": name, "specialty": specialty,
                      "clinical_groups": groups, "active": True,
                      "updated_at": _now()}})
        return get_doctor(db, existing["doctor_id"])

    doctor = {
        "doctor_id": f"DR-{_slug(name)[:20]}-{uuid.uuid4().hex[:6]}",
        "name": name,
        "specialty": specialty,
        "email": email,
        "clinical_groups": groups,
        "active": True,
        "registered_at": _now(),
    }
    db[DOCTORS].insert_one(dict(doctor))
    return doctor


def list_doctors(db, active_only: bool = True) -> list:
    query = {"active": True} if active_only else {}
    return list(db[DOCTORS].find(query, {"_id": 0}).sort("name", 1))


def get_doctor(db, doctor_id: str) -> Optional[dict]:
    return db[DOCTORS].find_one({"doctor_id": doctor_id}, {"_id": 0})


def deactivate_doctor(db, doctor_id: str) -> bool:
    """
    Take a clinician out of the routing pool without deleting them.

    Deleting would orphan every alert they have already answered, and the reply
    on a patient's record has to keep saying who wrote it.
    """
    result = db[DOCTORS].update_one(
        {"doctor_id": doctor_id}, {"$set": {"active": False, "updated_at": _now()}})
    return result.matched_count > 0


# ---------------------------------------------------------------------------
# Routing
# ---------------------------------------------------------------------------

def route(db, clinical_group: str, assigned_doctor_id: Optional[str] = None,
          roster: Optional[list] = None, hospital_id: Optional[str] = None) -> tuple:
    """
    Choose the clinician for an alert. Returns (doctor|None, reason).

    Most specific wins: a doctor named on the patient, then one who has claimed
    the condition, then the specialty it maps to, then general medicine.

    `roster` is the list of active doctors, preloaded. A cohort sweep passes it
    so routing a thousand alerts does not become four thousand round trips for
    a directory that fits in memory.

    Returning None is a real outcome, not a failure. An alert nobody is
    responsible for must stay visible in an unrouted queue; silently dropping it
    is the worst thing this function could do.
    """
    if roster is None:
        roster = list_doctors(db, active_only=True)
    if hospital_id:
        # Only the patient's own hospital's clinicians. Across the whole
        # registry, a heart-failure alert for one hospital's patient went to an
        # internist registered with no hospital at all.
        roster = [d for d in roster if d.get("hospital_id") == hospital_id]

    group = clinical_group or "general"
    specialty = SPECIALTY_FOR_GROUP.get(group, "Internal Medicine")

    def first(predicate):
        return next((d for d in roster if predicate(d)), None)

    doctor = (assigned_doctor_id
              and first(lambda d: d.get("doctor_id") == assigned_doctor_id))
    if doctor:
        return doctor, "assigned to this patient"

    doctor = first(lambda d: group in (d.get("clinical_groups") or []))
    if doctor:
        return doctor, f"covers {group.replace('_', ' ')}"

    doctor = first(lambda d: d.get("specialty") == specialty)
    if doctor:
        return doctor, f"{specialty} on call"

    doctor = first(lambda d: d.get("specialty") == "Internal Medicine")
    if doctor:
        return doctor, "no specialist registered, routed to internal medicine"

    return None, f"no active doctor covers {specialty}"


# ---------------------------------------------------------------------------
# Alerts
# ---------------------------------------------------------------------------


def _alert_document(patient_id: str, forecast_result: dict, meta: dict,
                    doctor: Optional[dict], reason: str) -> dict:
    """
    The shape of a clinical alert.

    Shared by the single-patient path and the bulk sweep so the two can never
    produce subtly different documents - the sweep writes almost all of them,
    and a field only the single path sets would be missing from the ones that
    actually reach a doctor.
    """
    return {
        "alert_id": f"AL-{uuid.uuid4().hex[:12]}",
        "patient_id": patient_id,
        "week_number": forecast_result.get("as_of_week"),
        "created_at": _now(),
        "severity": forecast_result.get("severity"),
        "status": "pending" if doctor else "unrouted",
        "doctor_id": doctor["doctor_id"] if doctor else None,
        "doctor_name": doctor["name"] if doctor else None,
        "routing_reason": reason,
        "forecast": forecast_result,
        # Denormalised so an inbox renders without a join per row. These are
        # facts about the patient at the moment the alert fired, which is also
        # what the doctor needs to see even if the worklist moves on.
        "patient": {
            "group_label": meta.get("group_label"),
            "clinical_group": forecast_result.get("clinical_group"),
            "primary_diagnosis": meta.get("primary_diagnosis"),
            "current_score": forecast_result.get("current_score"),
            "current_band": forecast_result.get("current_band"),
            "anchor_age": meta.get("anchor_age"),
            "gender": meta.get("gender"),
            "discharge_date": meta.get("discharge_date"),
        },
        "response": None,
    }


def _notification_document(doctor: Optional[dict], patient_id: str, severity: str,
                           alert_id: str, escalation: bool = False) -> dict:
    """
    One notification record.

    In-app only. There is no mail or SMS transport wired up, and writing one
    that silently no-ops would be worse than not having it - a clinician would
    believe they had been paged. `channel` and `delivered` exist so a real
    transport can be added here and the audit trail already has a place to say
    whether it worked.
    """
    return {
        "notification_id": f"NT-{uuid.uuid4().hex[:12]}",
        "doctor_id": doctor["doctor_id"] if doctor else None,
        "alert_id": alert_id,
        "patient_id": patient_id,
        "severity": severity,
        "kind": "escalation" if escalation else "new_alert",
        "channel": "in_app",
        "delivered": True,
        "read": False,
        "created_at": _now(),
    }


def _escalation_update(existing: dict, forecast_result: dict) -> tuple:
    """
    What to change on an alert that already exists for this patient-week.

    Returns (update, escalated). A repeat scan of the same week must not insert
    a second copy, and must not drag an answered alert back into the inbox -
    only a genuine worsening reopens one the doctor has already seen.
    """
    severity = forecast_result.get("severity")
    got_worse = (ALERTABLE_SEVERITIES.index(severity)
                 > ALERTABLE_SEVERITIES.index(existing.get("severity", "high")))
    update = {"forecast": forecast_result, "severity": severity, "updated_at": _now()}
    if got_worse and existing.get("status") in _STATUS_OPEN:
        update["status"] = "pending"
        update["escalated_at"] = _now()
    return update, got_worse


def create_alert(db, patient_id: str, forecast_result: dict,
                 patient_meta: Optional[dict] = None,
                 assigned_doctor_id: Optional[str] = None,
                 roster: Optional[list] = None) -> tuple:
    """
    Raise a clinical alert from a forecast. Returns (alert|None, created).

    `created` is False both when the alert already existed for that week and
    when the forecast was not worth alerting on - the caller distinguishes those
    by whether the alert itself is None.
    """
    if forecast_result.get("severity") not in ALERTABLE_SEVERITIES:
        return None, False

    week = forecast_result.get("as_of_week")
    doctor, reason = route(db, forecast_result.get("clinical_group", "general"),
                           assigned_doctor_id, roster=roster,
                           hospital_id=(patient_meta or {}).get("hospital_id"))

    existing = db[CLINICAL_ALERTS].find_one({"patient_id": patient_id, "week_number": week})
    if existing:
        update, escalated = _escalation_update(existing, forecast_result)
        db[CLINICAL_ALERTS].update_one({"_id": existing["_id"]}, {"$set": update})
        if escalated:
            db[NOTIFICATIONS].insert_one(_notification_document(
                doctor, patient_id, forecast_result["severity"],
                existing["alert_id"], escalation=True))
        return db[CLINICAL_ALERTS].find_one({"_id": existing["_id"]}, {"_id": 0}), False

    alert = _alert_document(patient_id, forecast_result, patient_meta or {}, doctor, reason)
    db[CLINICAL_ALERTS].insert_one(dict(alert))
    db[NOTIFICATIONS].insert_one(_notification_document(
        doctor, patient_id, forecast_result["severity"], alert["alert_id"]))
    return alert, True


def scan_cohort(db, forecast_fn, limit: Optional[int] = None,
                progress=None, patient_ids: Optional[list] = None) -> dict:
    """
    Run the forecast across every monitored patient and raise what it finds.

    `forecast_fn(patient_id) -> (forecast, patient_meta, assigned_doctor_id)` is
    injected rather than imported so this module stays free of the query layer
    and can be tested without a worklist.

    Writes are batched. Forecasting the cohort is pure computation once the
    caller has loaded the data, but writing an alert and a notification per
    patient one at a time is thousands of round trips - on a small Atlas tier
    that is the difference between a sweep that finishes and one that times out.
    """
    from pymongo import InsertOne, UpdateOne

    if patient_ids is None:
        patient_ids = db["weekly_monitoring"].distinct("patient_id")
    if limit:
        patient_ids = patient_ids[:limit]

    # Loaded once for the whole sweep. Routing reads it for every alert and it
    # cannot change underneath a single run in any way that matters.
    roster = list_doctors(db, active_only=True)

    if progress:
        progress(step="forecasting", scanned=0, total=len(patient_ids))

    candidates = []
    failed = 0
    for i, patient_id in enumerate(patient_ids):
        try:
            result, meta, assigned = forecast_fn(patient_id)
        except Exception as exc:  # one bad patient must not stop the sweep
            print(f"[doctor_service] forecast failed for {patient_id}: {exc}")
            failed += 1
            continue
        if result.get("severity") in ALERTABLE_SEVERITIES:
            candidates.append((patient_id, result, meta, assigned))
        if progress and i % 250 == 0:
            progress(step="forecasting", scanned=i, total=len(patient_ids))

    if progress:
        progress(step="writing", scanned=len(patient_ids), total=len(patient_ids),
                 candidates=len(candidates))

    # One query for every alert this sweep might collide with, rather than a
    # find_one per candidate.
    existing = {}
    if candidates:
        for doc in db[CLINICAL_ALERTS].find(
                {"patient_id": {"$in": [c[0] for c in candidates]}},
                {"alert_id": 1, "patient_id": 1, "week_number": 1,
                 "severity": 1, "status": 1}):
            existing[(doc["patient_id"], doc.get("week_number"))] = doc

    alert_ops, notifications = [], []
    created = updated = unrouted = 0
    by_severity = {"critical": 0, "high": 0}

    for patient_id, result, meta, assigned in candidates:
        severity = result["severity"]
        by_severity[severity] = by_severity.get(severity, 0) + 1
        prior = existing.get((patient_id, result.get("as_of_week")))

        if prior:
            update, escalated = _escalation_update(prior, result)
            alert_ops.append(UpdateOne({"alert_id": prior["alert_id"]}, {"$set": update}))
            if escalated:
                doctor, _ = route(db, result.get("clinical_group", "general"),
                                  assigned, roster=roster, hospital_id=meta.get("hospital_id"))
                notifications.append(_notification_document(
                    doctor, patient_id, severity, prior["alert_id"], escalation=True))
            updated += 1
            continue

        doctor, reason = route(db, result.get("clinical_group", "general"),
                               assigned, roster=roster, hospital_id=meta.get("hospital_id"))
        alert = _alert_document(patient_id, result, meta, doctor, reason)
        alert_ops.append(InsertOne(alert))
        notifications.append(_notification_document(
            doctor, patient_id, severity, alert["alert_id"]))
        created += 1
        if doctor is None:
            unrouted += 1

    if alert_ops:
        db[CLINICAL_ALERTS].bulk_write(alert_ops, ordered=False)
    if notifications:
        db[NOTIFICATIONS].insert_many(notifications, ordered=False)

    return {
        "scanned": len(patient_ids),
        "alerts_created": created,
        "alerts_updated": updated,
        "no_alert": len(patient_ids) - len(candidates) - failed,
        "forecast_failed": failed,
        "unrouted": unrouted,
        "by_severity": by_severity,
        "completed_at": _now(),
    }


def reroute_open_alerts(db, patient_ids: list, doctor_id: Optional[str]) -> int:
    """
    Move a patient's open alerts to the doctor now assigned to them, and tell
    that doctor. Without this an alert stayed with whoever it reached first -
    in the demo data an internist with no hospital, still unread weeks after
    the patient was given a doctor of their own. A cleared assignment routes by
    the condition rules again, inside the patient's hospital. Returns how many
    alerts moved.
    """
    from api import access
    ids = access.id_variants(list(patient_ids))
    alerts = list(db[CLINICAL_ALERTS].find(
        {"patient_id": {"$in": ids}, "status": {"$in": list(_STATUS_OPEN) + ["unrouted"]}},
        {"_id": 1, "alert_id": 1, "patient_id": 1, "doctor_id": 1, "severity": 1,
         "forecast.clinical_group": 1}))
    if not alerts:
        return 0
    hospitals = {str(c["patient_id"]): c.get("hospital_id") for c in db["care_actions"].find(
        {"patient_id": {"$in": ids}}, {"_id": 0, "patient_id": 1, "hospital_id": 1})}
    roster = list_doctors(db, active_only=True)
    moved = 0
    for a in alerts:
        doctor, reason = route(db, (a.get("forecast") or {}).get("clinical_group", "general"),
                               doctor_id or None, roster=roster,
                               hospital_id=hospitals.get(str(a["patient_id"])))
        if (doctor or {}).get("doctor_id") == a.get("doctor_id"):
            continue
        db[CLINICAL_ALERTS].update_one({"_id": a["_id"]}, {"$set": {
            "doctor_id": doctor["doctor_id"] if doctor else None,
            "doctor_name": doctor["name"] if doctor else None,
            "routing_reason": reason,
            # Whoever acknowledged it is no longer the one responsible.
            "status": "pending" if doctor else "unrouted",
            "rerouted_at": _now(), "updated_at": _now()}})
        if doctor:
            db[NOTIFICATIONS].insert_one(_notification_document(
                doctor, a["patient_id"], a.get("severity", "high"), a["alert_id"]))
        moved += 1
    return moved


# ---------------------------------------------------------------------------
# Inbox and reply
# ---------------------------------------------------------------------------

# Severity as a number, so the database can order by it. Sorting on the string
# happens to work today because "critical" < "high" alphabetically, which is a
# coincidence and not something to build an inbox on.
_SEVERITY_RANK = {"$switch": {"branches": [
    {"case": {"$eq": ["$severity", "critical"]}, "then": 2},
    {"case": {"$eq": ["$severity", "high"]}, "then": 1},
], "default": 0}}


def _ranked(db, query: dict, limit: int) -> list:
    """
    Most severe first, then newest, ordered BEFORE the limit is applied.

    Sorting a page that has already been truncated is the bug this replaces: a
    doctor with 165 open alerts was shown the first 50 in insertion order and
    then those 50 were sorted, so a critical alert sitting at position 51 was
    never seen. The ordering has to happen in the query.
    """
    return list(db[CLINICAL_ALERTS].aggregate([
        {"$match": query},
        {"$addFields": {"_rank": _SEVERITY_RANK}},
        {"$sort": {"_rank": -1, "created_at": -1}},
        {"$limit": limit},
        {"$project": {"_id": 0, "_rank": 0}},
    ]))


def inbox(db, doctor_id: str, status: Optional[str] = None, limit: int = 50) -> list:
    """Alerts for one doctor, most severe first, then newest."""
    query: dict = {"doctor_id": doctor_id}
    if status == "open":
        query["status"] = {"$in": list(_STATUS_OPEN)}
    elif status:
        query["status"] = status
    return _ranked(db, query, limit)


def alerts_for_patients(db, status: Optional[str] = "open", limit: int = 50) -> list:
    """Alerts about the caller's patients, whichever doctor they went to, most
    severe first. `db` is the caller's scoped view, so a nurse sees the alerts
    of the patients they are assigned to and nobody else's: the nurse console."""
    query: dict = {}
    if status == "open":
        query["status"] = {"$in": list(_STATUS_OPEN)}
    elif status:
        query["status"] = status
    return _ranked(db, query, limit)


def unrouted_alerts(db, limit: int = 50) -> list:
    """Alerts nobody is responsible for. Must never be silently dropped."""
    return _ranked(db, {"status": "unrouted"}, limit)


def patient_alerts(db, patient_id: str, limit: int = 20) -> list:
    """
    One patient's alert history, most recent monitoring week first.

    Deliberately NOT severity-ranked like the inbox. An inbox is a queue and the
    worst thing goes first; a patient's record is a timeline, and reordering it
    by severity would put last month's crisis above this week's reading.
    """
    from api.chatbot_queries import _patient_id_filter
    return list(db[CLINICAL_ALERTS]
                .find(_patient_id_filter(patient_id), {"_id": 0})
                .sort([("week_number", -1), ("created_at", -1)])
                .limit(limit))


def unread_count(db, doctor_id: str) -> dict:
    open_alerts = list(db[CLINICAL_ALERTS].find(
        {"doctor_id": doctor_id, "status": {"$in": list(_STATUS_OPEN)}},
        {"_id": 0, "severity": 1, "status": 1}))
    return {
        "doctor_id": doctor_id,
        "open": len(open_alerts),
        "pending": sum(1 for a in open_alerts if a.get("status") == "pending"),
        "critical": sum(1 for a in open_alerts if a.get("severity") == "critical"),
        "unread_notifications": db[NOTIFICATIONS].count_documents(
            {"doctor_id": doctor_id, "read": False}),
    }


def acknowledge(db, alert_id: str, doctor_id: str, responder: Optional[dict] = None) -> dict:
    alert = db[CLINICAL_ALERTS].find_one({"alert_id": alert_id})
    if not alert:
        raise LookupError(f"No alert with id '{alert_id}'.")
    if alert.get("status") == "responded":
        return db[CLINICAL_ALERTS].find_one({"alert_id": alert_id}, {"_id": 0})

    db[CLINICAL_ALERTS].update_one(
        {"alert_id": alert_id},
        {"$set": {"status": "acknowledged", "acknowledged_at": _now(),
                  "acknowledged_by": doctor_id, "acknowledged_by_account": responder}})
    db[NOTIFICATIONS].update_many({"alert_id": alert_id}, {"$set": {"read": True}})
    return db[CLINICAL_ALERTS].find_one({"alert_id": alert_id}, {"_id": 0})


def respond(db, alert_id: str, doctor_id: str, recommendation: str,
            actions: Optional[list] = None, urgency: str = "routine",
            responder: Optional[dict] = None) -> dict:
    """
    Record the clinician's reply and push it onto the patient's care record.

    The reply lands in two places on purpose. On the alert it closes the loop
    and is auditable. On `care_actions` it reaches the coordinator who is
    actually going to telephone the patient, who does not work out of the
    doctor's inbox.
    """
    recommendation = (recommendation or "").strip()
    if not recommendation:
        raise ValueError("A recommendation is required.")
    if urgency not in URGENCIES:
        raise ValueError(f"urgency must be one of {', '.join(URGENCIES)}.")

    actions = [a for a in (actions or []) if a in RECOMMENDED_ACTIONS]

    alert = db[CLINICAL_ALERTS].find_one({"alert_id": alert_id})
    if not alert:
        raise LookupError(f"No alert with id '{alert_id}'.")

    doctor = get_doctor(db, doctor_id)
    # A nurse answers in their own name, from their own console. Anyone else
    # covering a doctor's inbox answers as that doctor (the console says so),
    # and `responder` keeps who actually typed it.
    by_nurse = (responder or {}).get("role") == "nurse"
    response = {
        "doctor_id": doctor_id,
        "doctor_name": doctor["name"] if doctor else doctor_id,
        "specialty": doctor["specialty"] if doctor else None,
        "answered_by": (responder or {}).get("name") if by_nurse
                       else (doctor["name"] if doctor else doctor_id),
        "answered_as": "nurse" if by_nurse else "doctor",
        "responder": responder,
        "recommendation": recommendation,
        "actions": actions,
        "action_labels": [RECOMMENDED_ACTIONS[a] for a in actions],
        "urgency": urgency,
        "responded_at": _now(),
    }

    db[CLINICAL_ALERTS].update_one(
        {"alert_id": alert_id},
        {"$set": {"status": "responded", "response": response}})
    db[NOTIFICATIONS].update_many({"alert_id": alert_id}, {"$set": {"read": True}})

    from api.chatbot_queries import _patient_id_filter
    note_text = recommendation
    if response["action_labels"]:
        note_text += "\nActions: " + "; ".join(response["action_labels"])
    db["care_actions"].update_one(
        _patient_id_filter(alert["patient_id"]),
        {"$push": {"notes": {
            "text": note_text,
            "author": (f"{response['answered_by']} (Nurse)" if by_nurse
                       else f"{response['doctor_name']}"
                       + (f" ({response['specialty']})" if response["specialty"] else "")),
            "entered_by": (responder or {}).get("email"),
            "source": "clinical_alert",
            "alert_id": alert_id,
            "urgency": urgency,
            "created_at": response["responded_at"],
        }},
         "$setOnInsert": {"patient_id": alert["patient_id"],
                          "coordinator_name": None, "assigned_at": None}},
        upsert=True)

    return db[CLINICAL_ALERTS].find_one({"alert_id": alert_id}, {"_id": 0})


def dismiss(db, alert_id: str, doctor_id: str, reason: str = "",
            responder: Optional[dict] = None) -> dict:
    """
    Close an alert without a clinical recommendation.

    A forecast can be right about the numbers and wrong about the patient - the
    weight gain was a new medication, the low saturation was a faulty meter. The
    reason is recorded because a pile of dismissals with the same cause is how
    a threshold gets found to be wrong.
    """
    alert = db[CLINICAL_ALERTS].find_one({"alert_id": alert_id})
    if not alert:
        raise LookupError(f"No alert with id '{alert_id}'.")
    db[CLINICAL_ALERTS].update_one(
        {"alert_id": alert_id},
        {"$set": {"status": "dismissed", "dismissed_at": _now(),
                  "dismissed_by": doctor_id, "dismissed_by_account": responder,
                  "dismissal_reason": reason.strip()}})
    db[NOTIFICATIONS].update_many({"alert_id": alert_id}, {"$set": {"read": True}})
    return db[CLINICAL_ALERTS].find_one({"alert_id": alert_id}, {"_id": 0})

"""
Follow-up requests: a doctor or nurse asks the hospital to follow a patient up,
and a case manager makes it happen. The Readmissions twin of GLP-1's
GLP1/Backend/core/followups.py - same states, same rules, same words - so a
case manager works the same way in both apps.

Preventra passes the request along and records that it was dealt with; the
booking itself happens in the hospital's own system. So there are no dates,
slots or rooms here, only:

    open          asked for, nobody has taken it yet
    in_progress   one case manager has taken it ("I'll take it"), so no two
                  of them phone the same patient
    done          dealt with: booked | unreachable | not_needed, with a note

A request starts one of two ways:
  * from the patient's page ("Request follow-up"), by a doctor or nurse;
  * from a doctor's reply to a clinical alert (doctor_service.respond), when
    the reply asks for something a case manager arranges - phoning the
    patient, bringing the review forward, bloods, a home visit. The alert reply
    itself is untouched; this only adds the case manager's side
    (from_alert below, called by the route after the reply is saved).

Who does what (api/access.py has the role lists):

    doctor, nurse          request one for their own patients, add a note,
                           see its status and the outcome
    case_manager           sees every request in their hospital, takes it,
                           marks it done
    hospital_admin         sees the same list and can step in (take, mark done)
    superadmin             every hospital's, or the one picked

Rules:
  * One request at a time per patient, enforced by a unique index on
    `active_patient`, which only an unfinished request carries.
  * After OVERDUE_DAYS an open request is flagged. A finished one leaves the
    list, and its outcome shows on the patient's page for SHOW_DONE_DAYS.
    Nothing is deleted: every change is in the request's `history`.
  * The list shows what booking needs - patient number, risk, care team, who
    asked, how soon and their note - and none of the clinical layer
    (45 CFR 164.514(d), minimum necessary).

Stored in neuroshield.followups.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional

from bson import ObjectId
from bson.errors import InvalidId
from fastapi import HTTPException
from pymongo import DESCENDING
from pymongo.errors import DuplicateKeyError

from api import access

COLLECTION = "followups"
URGENCY = {"urgent": "Urgent - within days", "routine": "Routine - within weeks"}
OUTCOMES = {"booked": "Booked", "unreachable": "Couldn't reach patient", "not_needed": "Not needed"}
REQUEST_ROLES = ("doctor", "nurse")
HANDLE_ROLES = ("superadmin", "hospital_admin", "case_manager")
OVERDUE_DAYS = 7
SHOW_DONE_DAYS = 30
MAX_NOTE = 500

# The alert-reply actions (doctor_service.RECOMMENDED_ACTIONS) that a case
# manager arranges. "Send to the emergency department" is not one: that is
# now, not a booking. "Medication change" and "refer" are the doctor's own.
BOOKING_ACTIONS = ("contact_patient", "schedule_review", "arrange_labs", "home_visit")

_indexed = set()


def _col(db):
    return db[COLLECTION]


def ensure_indexes(db) -> None:
    key = id(db)
    if key not in _indexed:
        _col(db).create_index("active_patient", unique=True, sparse=True)
        _col(db).create_index([("hospital_id", 1), ("status", 1)])
        _indexed.add(key)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _aware(at: Optional[datetime]) -> Optional[datetime]:
    if at is None:
        return None
    return at if at.tzinfo else at.replace(tzinfo=timezone.utc)


def _clean_note(text: Optional[str]) -> str:
    text = (text or "").strip()
    if len(text) > MAX_NOTE:
        raise HTTPException(status_code=422, detail=f"Keep the note under {MAX_NOTE} characters")
    return text


def _who(user: dict) -> dict:
    return {"id": str(user["_id"]), "name": user.get("name") or user.get("email", ""),
            "email": user.get("email", ""), "role": user["role"]}


def _object_id(followup_id: str) -> ObjectId:
    try:
        return ObjectId(followup_id)
    except (InvalidId, TypeError):
        raise HTTPException(status_code=404, detail="Follow-up request not found")


def _hospital_of_patient(db, patient_id: str) -> Optional[str]:
    doc = db["care_actions"].find_one({"patient_id": {"$in": access.id_variants([patient_id])}},
                                      {"hospital_id": 1})
    return (doc or {}).get("hospital_id")


def _risk(db, patient_ids) -> dict:
    """patient_id -> {score, band} from the latest batch each patient is in."""
    out: dict = {}
    for row in db["patient_worklist"].find(
            {"patient_id": {"$in": access.id_variants(patient_ids)}},
            {"_id": 0, "patient_id": 1, "batch_date": 1, "current_score": 1, "risk_score": 1,
             "current_band": 1, "risk_band": 1}).sort("batch_date", DESCENDING):
        pid = str(row["patient_id"])
        if pid in out:
            continue
        try:
            score = float(row.get("current_score", row.get("risk_score", 0)) or 0)
        except (TypeError, ValueError):
            score = 0.0
        out[pid] = {"score": score, "band": row.get("current_band") or row.get("risk_band") or "Low"}
    return out


def public(doc: dict, risk: Optional[dict] = None) -> dict:
    """A request as the screens show it."""
    now = _now()
    asked = _aware(doc["requested_at"])
    out = {
        "id": str(doc["_id"]), "patient_id": doc["patient_id"], "hospital_id": doc.get("hospital_id"),
        "status": doc["status"], "urgency": doc["urgency"], "urgency_label": URGENCY[doc["urgency"]],
        "note": doc.get("note", ""), "requested_by": doc["requested_by"], "requested_at": asked.isoformat(),
        "source": doc.get("source", "patient_page"), "alert_id": doc.get("alert_id"),
        "notes": [{**n, "at": _aware(n["at"]).isoformat()} for n in doc.get("notes", [])],
        "taken_by": doc.get("taken_by"),
        "outcome": doc.get("outcome"), "outcome_label": OUTCOMES.get(doc.get("outcome")),
        "outcome_note": doc.get("outcome_note", ""), "done_by": doc.get("done_by"),
        "done_at": _aware(doc["done_at"]).isoformat() if doc.get("done_at") else None,
        "risk_at_request": doc.get("risk_at_request"),
        "overdue": doc["status"] != "done" and now - asked > timedelta(days=OVERDUE_DAYS),
        "days_open": (now - asked).days,
    }
    if risk is not None:
        out["risk_score"], out["risk_band"] = risk["score"], risk["band"]
    return out


# ------------------------------------------------------------ care team side
def _insert(db, user: dict, patient_id: str, urgency: str, note: str,
            source: str = "patient_page", alert_id: Optional[str] = None) -> dict:
    ensure_indexes(db)
    who, now = _who(user), _now()
    risk = _risk(db, [patient_id]).get(patient_id)
    doc = {"patient_id": patient_id, "active_patient": patient_id,
           "hospital_id": _hospital_of_patient(db, patient_id), "status": "open", "urgency": urgency,
           "note": note, "requested_by": who, "requested_at": now, "notes": [],
           "source": source, "alert_id": alert_id,
           "risk_at_request": risk["score"] if risk else None,
           "history": [{"action": "requested", "by": who, "at": now, "urgency": urgency,
                        "source": source}]}
    doc["_id"] = _col(db).insert_one(doc).inserted_id
    return doc


def request(db, user: dict, scope: Optional[list], patient_id: str,
            urgency: str, note: Optional[str]) -> dict:
    if user["role"] not in REQUEST_ROLES:
        raise HTTPException(status_code=403, detail="Doctors and nurses request follow-ups")
    patient_id = str(patient_id)
    access.require_patient(scope, patient_id)
    if urgency not in URGENCY:
        raise HTTPException(status_code=422, detail="Choose urgent or routine")
    note = _clean_note(note)
    try:
        return public(_insert(db, user, patient_id, urgency, note))
    except DuplicateKeyError:
        existing = _col(db).find_one({"active_patient": patient_id})
        raise HTTPException(status_code=409, detail={
            "code": "already_requested",
            "message": f"{existing['requested_by']['name']} already requested a follow-up for this patient",
            "followup": public(existing)})


def from_alert(db, user: dict, alert: dict) -> Optional[dict]:
    """After a doctor or nurse replies to a clinical alert: if the reply asks for
    something a case manager arranges, put it in their list. If a request is
    already open for the patient, the reply is added to it as a note instead.
    Returns the request, or None when the reply needs no follow-up."""
    response = alert.get("response") or {}
    if user["role"] not in REQUEST_ROLES:
        return None
    wanted = [a for a in response.get("actions") or [] if a in BOOKING_ACTIONS]
    if not wanted:
        return None
    patient_id = str(alert["patient_id"])
    # "immediate" is the emergency department, not a booking; anything sooner
    # than routine is urgent here.
    urgency = "routine" if response.get("urgency", "routine") == "routine" else "urgent"
    labels = [l for a, l in zip(response.get("actions") or [], response.get("action_labels") or [])
              if a in BOOKING_ACTIONS]
    note = _clean_note(("; ".join(labels) + ". " + (response.get("recommendation") or ""))[:MAX_NOTE])
    try:
        return public(_insert(db, user, patient_id, urgency, note,
                              source="clinical_alert", alert_id=alert.get("alert_id")))
    except DuplicateKeyError:
        existing = _col(db).find_one({"active_patient": patient_id})
        who, now = _who(user), _now()
        _col(db).update_one({"_id": existing["_id"]}, {
            "$push": {"notes": {"by": who, "at": now, "text": f"Alert reply: {note}"},
                      "history": {"action": "note", "by": who, "at": now,
                                  "alert_id": alert.get("alert_id")}},
            # A reply that is more urgent than the open request makes it urgent.
            **({"$set": {"urgency": "urgent"}} if urgency == "urgent" else {})})
        return public(_col(db).find_one({"_id": existing["_id"]}))


def for_patient(db, user: dict, scope: Optional[list], patient_id: str) -> dict:
    """The patient's open request, and the last finished one if recent."""
    patient_id = str(patient_id)
    access.require_patient(scope, patient_id)
    active = _col(db).find_one({"active_patient": patient_id})
    since = _now() - timedelta(days=SHOW_DONE_DAYS)
    recent = list(_col(db).find({"patient_id": patient_id, "status": "done", "done_at": {"$gte": since}})
                  .sort("done_at", DESCENDING).limit(1))
    return {"active": public(active) if active else None,
            "recent": public(recent[0]) if recent else None,
            "can_request": user["role"] in REQUEST_ROLES and active is None,
            "can_handle": user["role"] in HANDLE_ROLES}


def add_note(db, user: dict, scope: Optional[list], followup_id: str, text: str) -> dict:
    doc = _load(db, user, scope, followup_id)
    if doc["status"] == "done":
        raise HTTPException(status_code=409, detail="This request is already done")
    text = _clean_note(text)
    if not text:
        raise HTTPException(status_code=422, detail="Write a note")
    who, now = _who(user), _now()
    _col(db).update_one({"_id": doc["_id"]}, {
        "$push": {"notes": {"by": who, "at": now, "text": text},
                  "history": {"action": "note", "by": who, "at": now}}})
    return public(_col(db).find_one({"_id": doc["_id"]}))


# ------------------------------------------------------- case manager side
def _load(db, user: dict, scope: Optional[list], followup_id: str) -> dict:
    """A request the caller may see: their own patient's (care team), or their
    hospital's (case manager, admin). 404 otherwise, as for patients."""
    doc = _col(db).find_one({"_id": _object_id(followup_id)})
    if not doc:
        raise HTTPException(status_code=404, detail="Follow-up request not found")
    if user["role"] in HANDLE_ROLES:
        hospital = access.hospital_of(user)
        if hospital and doc.get("hospital_id") != hospital:
            raise HTTPException(status_code=404, detail="Follow-up request not found")
        if not hospital and user["role"] != "superadmin":
            raise HTTPException(status_code=404, detail="Follow-up request not found")
    else:
        access.require_patient(scope, doc["patient_id"])
    return doc


def _require_handler(user: dict) -> None:
    if user["role"] not in HANDLE_ROLES:
        raise HTTPException(status_code=403, detail="Case managers handle follow-up requests")


def take(db, user: dict, scope: Optional[list], followup_id: str) -> dict:
    _require_handler(user)
    doc = _load(db, user, scope, followup_id)
    who, now = _who(user), _now()
    # Only an open request can be taken - atomically, so two people can't both.
    result = _col(db).update_one({"_id": doc["_id"], "status": "open"}, {
        "$set": {"status": "in_progress", "taken_by": who, "taken_at": now},
        "$push": {"history": {"action": "taken", "by": who, "at": now}}})
    if result.modified_count == 0:
        current = _col(db).find_one({"_id": doc["_id"]})
        holder = (current.get("taken_by") or {}).get("name", "someone")
        raise HTTPException(status_code=409, detail={
            "code": "already_taken" if current["status"] == "in_progress" else "already_done",
            "message": f"{holder} is already handling this" if current["status"] == "in_progress"
                       else "This request is already done",
            "followup": public(current)})
    return public(_col(db).find_one({"_id": doc["_id"]}))


def complete(db, user: dict, scope: Optional[list], followup_id: str,
             outcome: str, note: Optional[str]) -> dict:
    """Done. A case manager may finish one they took, or an untaken one (which
    takes it on the way); one another case manager took is theirs to finish -
    unless the caller is the hospital admin or superadmin, stepping in."""
    _require_handler(user)
    if outcome not in OUTCOMES:
        raise HTTPException(status_code=422, detail="Choose booked, unreachable or not needed")
    doc = _load(db, user, scope, followup_id)
    if doc["status"] == "done":
        raise HTTPException(status_code=409, detail={"code": "already_done",
                                                     "message": "This request is already done",
                                                     "followup": public(doc)})
    holder = (doc.get("taken_by") or {}).get("id")
    if doc["status"] == "in_progress" and holder != str(user["_id"]) and user["role"] == "case_manager":
        raise HTTPException(status_code=409, detail={
            "code": "already_taken", "message": f"{doc['taken_by']['name']} is handling this",
            "followup": public(doc)})
    who, now = _who(user), _now()
    taken = {} if doc.get("taken_by") else {"taken_by": who, "taken_at": now}
    result = _col(db).update_one({"_id": doc["_id"], "status": doc["status"]}, {
        "$set": {"status": "done", "outcome": outcome, "outcome_note": _clean_note(note),
                 "done_by": who, "done_at": now, **taken},
        "$unset": {"active_patient": ""},
        "$push": {"history": {"action": "done", "by": who, "at": now, "outcome": outcome}}})
    if result.modified_count == 0:
        raise HTTPException(status_code=409, detail="Someone else changed this request; reload and try again")
    return public(_col(db).find_one({"_id": doc["_id"]}))


def listing(db, user: dict, status: str = "active") -> dict:
    """The hospital's requests: the active ones (urgent and oldest first), or
    the recently finished ones."""
    _require_handler(user)
    query: dict = {}
    hospital = access.hospital_of(user)
    if hospital:
        query["hospital_id"] = hospital
    elif user["role"] != "superadmin":
        return {"items": [], "counts": {"open": 0, "in_progress": 0, "overdue": 0}}
    if status == "done":
        query.update({"status": "done", "done_at": {"$gte": _now() - timedelta(days=SHOW_DONE_DAYS)}})
        docs = list(_col(db).find(query).sort("done_at", DESCENDING).limit(200))
    else:
        query["status"] = {"$in": ["open", "in_progress"]}
        docs = list(_col(db).find(query))
        docs.sort(key=lambda d: (d["urgency"] != "urgent", _aware(d["requested_at"])))
    ids = [d["patient_id"] for d in docs]
    risk = _risk(db, ids)
    from api.hospital import care_teams                       # avoid an import cycle
    teams = care_teams(db, ids)
    items = []
    for d in docs:
        item = public(d, risk.get(d["patient_id"]))
        team = teams.get(d["patient_id"], {})
        item["doctors"] = [team["doctor"]["name"]] if team.get("doctor") else []
        item["nurses"] = [n["name"] for n in team.get("nurses", [])]
        item["mine"] = (d.get("taken_by") or {}).get("id") == str(user["_id"])
        items.append(item)
    active = [i for i in items if i["status"] != "done"]
    return {"items": items, "counts": {
        "open": sum(i["status"] == "open" for i in active),
        "in_progress": sum(i["status"] == "in_progress" for i in active),
        "overdue": sum(i["overdue"] for i in active)}}


def waiting(db, user: dict) -> int:
    """How many requests nobody has taken yet, for the menu badge."""
    if user["role"] not in HANDLE_ROLES:
        return 0
    hospital = access.hospital_of(user)
    if not hospital and user["role"] != "superadmin":
        return 0
    return _col(db).count_documents({**({"hospital_id": hospital} if hospital else {}), "status": "open"})
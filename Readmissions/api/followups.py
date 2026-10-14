"""
Follow-up requests: a doctor or nurse asks the hospital to follow a patient
up, and a case manager makes it happen.

Preventra passes the request along and records that it was dealt with; the
booking itself happens in the hospital's own system. So there are no dates,
slots or rooms here, only:

    open          asked for, nobody has taken it yet
    in_progress   one case manager has taken it ("I'll take it"), so no two
                  of them phone the same patient
    done          dealt with: booked | unreachable | not_needed, with a note

Who does what (api/access.py has the role lists):

    doctor, nurse          request one for their own patients, add a note,
                           see its status and the outcome
    case_manager           sees every request in their hospital, takes it,
                           marks it done - the person a request is for
    hospital_admin         sees the same list and can step in (take, mark done)
    superadmin             sees every hospital's, or the picked one's

Where requests come from:

    the patient page       a doctor or nurse presses "Request follow-up"
    an alert reply         a doctor or nurse answers a clinical alert and ticks
                           something a case manager arranges (COORDINATOR_ACTIONS)
                           - see from_alert_reply

Rules:
  * One request at a time per patient. A second person sees the open one and
    can add a note to it instead. Enforced by the database (a unique index on
    `active_patient`, which only an unfinished request carries), so two clicks
    at the same moment cannot make two.
  * An open request stays until someone deals with it; after OVERDUE_DAYS it
    is flagged. A finished one leaves the list at once, and its outcome shows
    on the patient's page for SHOW_DONE_DAYS. Nothing is deleted: every change
    is in the request's `history`.
  * The list shows what booking needs - patient number, current risk, care
    team, who asked, how soon and their note - and none of the clinical layer
    (45 CFR 164.514(d), minimum necessary).

Mirrors GLP1/Backend/core/followups.py. Stored in the product database as
`followups`.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional

from bson import ObjectId
from bson.errors import InvalidId
from fastapi import HTTPException
from pymongo.errors import DuplicateKeyError

from api import access
from api.db_utils import get_latest_batch_date

COLLECTION = "followups"
URGENCY = {"urgent": "Urgent - within days", "routine": "Routine - within weeks"}
OUTCOMES = {"booked": "Booked", "unreachable": "Couldn't reach patient", "not_needed": "Not needed"}
REQUEST_ROLES = ("doctor", "nurse")
HANDLE_ROLES = ("superadmin", "hospital_admin", "case_manager")
OVERDUE_DAYS = 7
SHOW_DONE_DAYS = 30
MAX_NOTE = 500

# Alert-reply actions that are a case manager's to arrange. Sending someone to
# the emergency department is an emergency now, not a booking; a medication
# change, a referral and "no action" are the clinician's own.
COORDINATOR_ACTIONS = ("contact_patient", "schedule_review", "arrange_labs", "home_visit",
                       "increase_monitoring")

BAND_RANK = {"Low": 1, "Medium": 2, "High": 3}

# The indexes are created once per database. The client is kept in the value
# so its id cannot be reused by another database while this one is cached.
_indexed: dict = {}


def ensure_indexes(db) -> None:
    key = (id(db.client), db.name)
    if key in _indexed:
        return
    db[COLLECTION].create_index("active_patient", unique=True, sparse=True)
    db[COLLECTION].create_index([("hospital_id", 1), ("status", 1)])
    db[COLLECTION].create_index([("patient_id", 1), ("done_at", -1)])
    _indexed[key] = db.client


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


def who(user: dict) -> dict:
    """The person acting, as stored on the request: id, name, email, role."""
    return {"id": str(user["_id"]), "name": user.get("name") or user.get("email", ""),
            "email": user.get("email", ""), "role": user["role"]}


def _object_id(followup_id: str) -> ObjectId:
    try:
        return ObjectId(followup_id)
    except (InvalidId, TypeError):
        raise HTTPException(status_code=404, detail="Follow-up request not found")


def _hospital_of_patient(db, patient_id) -> Optional[str]:
    doc = db["care_actions"].find_one({"patient_id": {"$in": access.id_variants([patient_id])}},
                                      {"hospital_id": 1})
    return (doc or {}).get("hospital_id")


def _risk(db, patient_ids) -> dict:
    """patient_id -> {"score", "band"}: the latest weekly figure, or the
    discharge one for a patient with no weekly series."""
    ids = [str(p) for p in patient_ids]
    if not ids:
        return {}
    latest = get_latest_batch_date(db, "patient_worklist")
    rows = db["patient_worklist"].find(
        {"patient_id": {"$in": access.id_variants(ids)}, **({"batch_date": latest} if latest else {})},
        {"_id": 0, "patient_id": 1, "current_score": 1, "current_band": 1,
         "risk_score": 1, "risk_band": 1})
    out = {}
    for r in rows:
        score = r.get("current_score", r.get("risk_score"))
        out[str(r["patient_id"])] = {
            "score": None if score is None else round(float(score), 1),
            "band": r.get("current_band") or r.get("risk_band"),
        }
    return out


def public(doc: dict, risk: Optional[dict] = None) -> dict:
    """A request as the screens show it."""
    now = _now()
    asked = _aware(doc["requested_at"])
    out = {
        "id": str(doc["_id"]), "patient_id": str(doc["patient_id"]),
        "hospital_id": doc.get("hospital_id"), "status": doc["status"],
        "urgency": doc["urgency"], "urgency_label": URGENCY[doc["urgency"]],
        "note": doc.get("note", ""), "source": doc.get("source", "patient_page"),
        "requested_by": doc["requested_by"], "requested_at": asked.isoformat(),
        "notes": [{**n, "at": _aware(n["at"]).isoformat()} for n in doc.get("notes", [])],
        "taken_by": doc.get("taken_by"),
        "outcome": doc.get("outcome"), "outcome_label": OUTCOMES.get(doc.get("outcome")),
        "outcome_note": doc.get("outcome_note", ""), "done_by": doc.get("done_by"),
        "done_at": _aware(doc["done_at"]).isoformat() if doc.get("done_at") else None,
        "risk_at_request": doc.get("risk_at_request"),
        "overdue": doc["status"] != "done" and now - asked > timedelta(days=OVERDUE_DAYS),
        "days_open": (now - asked).days,
    }
    if risk:
        out["current_score"], out["current_band"] = risk.get("score"), risk.get("band")
        before = (doc.get("risk_at_request") or {}).get("band")
        out["risk_went_down"] = (doc["status"] != "done" and before in BAND_RANK
                                 and risk.get("band") in BAND_RANK
                                 and BAND_RANK[risk["band"]] < BAND_RANK[before])
    return out


def _insert(db, by: dict, patient_id: str, urgency: str, note: str,
            source: str = "patient_page", alert_id: Optional[str] = None) -> dict:
    """Write a new open request. DuplicateKeyError if one is already open."""
    ensure_indexes(db)
    now = _now()
    doc = {"patient_id": str(patient_id), "active_patient": str(patient_id),
           "hospital_id": _hospital_of_patient(db, patient_id), "status": "open",
           "urgency": urgency, "note": note, "source": source,
           "requested_by": by, "requested_at": now, "notes": [],
           "risk_at_request": _risk(db, [patient_id]).get(str(patient_id)),
           "history": [{"action": "requested", "by": by, "at": now, "urgency": urgency,
                        **({"alert_id": alert_id} if alert_id else {})}]}
    if alert_id:
        doc["alert_id"] = alert_id
    result = db[COLLECTION].insert_one(doc)
    doc["_id"] = result.inserted_id
    return doc


# ------------------------------------------------------------ care team side
def request(db, user: dict, scope: Optional[list], patient_id: str,
            urgency: str, note: Optional[str]) -> dict:
    access.require_patient(scope, patient_id)
    if user["role"] not in REQUEST_ROLES:
        raise HTTPException(status_code=403, detail="Doctors and nurses request follow-ups")
    if urgency not in URGENCY:
        raise HTTPException(status_code=422, detail="Choose urgent or routine")
    try:
        doc = _insert(db, who(user), patient_id, urgency, _clean_note(note))
    except DuplicateKeyError:
        existing = db[COLLECTION].find_one({"active_patient": str(patient_id)})
        raise HTTPException(status_code=409, detail={
            "code": "already_requested",
            "message": f"{existing['requested_by']['name']} already requested a follow-up for this patient",
            "followup": public(existing)})
    return public(doc)


def for_patient(db, user: dict, scope: Optional[list], patient_id: str) -> dict:
    """The patient's open request, and the last finished one if recent."""
    access.require_patient(scope, patient_id)
    if user["role"] not in REQUEST_ROLES + HANDLE_ROLES:
        raise HTTPException(status_code=403, detail="Follow-ups are for the care team and case managers")
    col = db[COLLECTION]
    active = col.find_one({"active_patient": str(patient_id)})
    since = _now() - timedelta(days=SHOW_DONE_DAYS)
    recent = list(col.find({"patient_id": str(patient_id), "status": "done", "done_at": {"$gte": since}})
                  .sort("done_at", -1).limit(1))
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
    by, now = who(user), _now()
    db[COLLECTION].update_one({"_id": doc["_id"]}, {
        "$push": {"notes": {"by": by, "at": now, "text": text},
                  "history": {"action": "note", "by": by, "at": now}}})
    return public(db[COLLECTION].find_one({"_id": doc["_id"]}))


# ---------------------------------------------------------- from alert replies
def _merge_into(db, existing: dict, by: dict, text: str, urgent: bool) -> dict:
    """Add an alert reply to the request already open for the patient, and make
    it urgent if the reply was."""
    now = _now()
    update: dict = {"$push": {"notes": {"by": by, "at": now, "text": text},
                              "history": {"action": "note", "by": by, "at": now}}}
    if urgent and existing["urgency"] != "urgent":
        update["$set"] = {"urgency": "urgent"}
        update["$push"]["history"] = {"$each": [
            {"action": "note", "by": by, "at": now},
            {"action": "made_urgent", "by": by, "at": now}]}
    db[COLLECTION].update_one({"_id": existing["_id"]}, update)
    return db[COLLECTION].find_one({"_id": existing["_id"]})


def from_alert_reply(db, user: dict, alert: dict, actions: list, urgency: str,
                     recommendation: str, action_labels: dict) -> Optional[dict]:
    """
    The request a clinical-alert reply hands to the case managers, or None.

        nothing a case manager arranges was ticked   no request
        the reply is from a case manager             no request - they would be
                                                     asking themselves
        a request is already open for the patient    the reply is added to it as
                                                     a note, and makes it urgent
                                                     if the reply was urgent
        otherwise                                    a new request; "urgent" and
                                                     "immediate" replies are urgent

    The doctor's reply itself is stored by doctor_service.respond exactly as
    before; this only adds the request on top.
    """
    if user["role"] == "case_manager":
        return None
    picked = [a for a in (actions or []) if a in COORDINATOR_ACTIONS]
    if not picked:
        return None
    urgent = urgency in ("urgent", "immediate")
    text = (recommendation or "").strip()
    text = (f"From an alert reply: {text}\nTo arrange: "
            + "; ".join(action_labels.get(a, a) for a in picked))
    if len(text) > MAX_NOTE:
        text = text[:MAX_NOTE - 1] + "…"

    by, patient_id = who(user), str(alert["patient_id"])
    existing = db[COLLECTION].find_one({"active_patient": patient_id})
    if existing:
        return {**public(_merge_into(db, existing, by, text, urgent)), "merged": True}
    try:
        doc = _insert(db, by, patient_id, "urgent" if urgent else "routine", text,
                      source="clinical_alert", alert_id=alert.get("alert_id"))
    except DuplicateKeyError:             # opened by someone else a moment ago
        existing = db[COLLECTION].find_one({"active_patient": patient_id})
        return {**public(_merge_into(db, existing, by, text, urgent)), "merged": True}
    return {**public(doc), "merged": False}


# ------------------------------------------------------- case manager side
def _load(db, user: dict, scope: Optional[list], followup_id: str) -> dict:
    """A request the caller may see: their own patient's (care team), or their
    hospital's (case manager, admin). 404 otherwise, as for patients."""
    doc = db[COLLECTION].find_one({"_id": _object_id(followup_id)})
    missing = HTTPException(status_code=404, detail="Follow-up request not found")
    if not doc:
        raise missing
    if user["role"] in HANDLE_ROLES:
        hospital = access.hospital_of(user)
        if hospital and doc.get("hospital_id") != hospital:
            raise missing
        if not hospital and user["role"] != "superadmin":
            raise missing
    elif user["role"] in REQUEST_ROLES:
        access.require_patient(scope, doc["patient_id"])
    else:
        raise missing
    return doc


def _require_handler(user: dict) -> None:
    if user["role"] not in HANDLE_ROLES:
        raise HTTPException(status_code=403, detail="Case managers handle follow-up requests")


def take(db, user: dict, scope: Optional[list], followup_id: str) -> dict:
    _require_handler(user)
    doc = _load(db, user, scope, followup_id)
    by, now = who(user), _now()
    # Only an open request can be taken - atomically, so two people can't both.
    result = db[COLLECTION].update_one({"_id": doc["_id"], "status": "open"}, {
        "$set": {"status": "in_progress", "taken_by": by, "taken_at": now},
        "$push": {"history": {"action": "taken", "by": by, "at": now}}})
    if result.modified_count == 0:
        current = db[COLLECTION].find_one({"_id": doc["_id"]})
        holder = (current.get("taken_by") or {}).get("name", "someone")
        in_progress = current["status"] == "in_progress"
        raise HTTPException(status_code=409, detail={
            "code": "already_taken" if in_progress else "already_done",
            "message": f"{holder} is already handling this" if in_progress
                       else "This request is already done",
            "followup": public(current)})
    return public(db[COLLECTION].find_one({"_id": doc["_id"]}))


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
        raise HTTPException(status_code=409, detail={
            "code": "already_done", "message": "This request is already done",
            "followup": public(doc)})
    holder = (doc.get("taken_by") or {}).get("id")
    if (doc["status"] == "in_progress" and holder != str(user["_id"])
            and user["role"] == "case_manager"):
        raise HTTPException(status_code=409, detail={
            "code": "already_taken", "message": f"{doc['taken_by']['name']} is handling this",
            "followup": public(doc)})
    by, now = who(user), _now()
    taken = {} if doc.get("taken_by") else {"taken_by": by, "taken_at": now}
    result = db[COLLECTION].update_one({"_id": doc["_id"], "status": doc["status"]}, {
        "$set": {"status": "done", "outcome": outcome, "outcome_note": _clean_note(note),
                 "done_by": by, "done_at": now, **taken},
        "$unset": {"active_patient": ""},
        "$push": {"history": {"action": "done", "by": by, "at": now, "outcome": outcome}}})
    if result.modified_count == 0:
        raise HTTPException(status_code=409,
                            detail="Someone else changed this request; reload and try again")
    return public(db[COLLECTION].find_one({"_id": doc["_id"]}))


def _hospital_query(user: dict) -> Optional[dict]:
    """The caller's hospital as a filter; {} for the superadmin looking at every
    hospital; None for an account with no hospital, who sees nothing."""
    hospital = access.hospital_of(user)
    if hospital:
        return {"hospital_id": hospital}
    return {} if user["role"] == "superadmin" else None


def listing(db, user: dict, status: str = "active") -> dict:
    """The hospital's requests: the active ones (urgent and oldest first), or
    the recently finished ones."""
    _require_handler(user)
    empty = {"items": [], "counts": {"open": 0, "in_progress": 0, "overdue": 0}}
    query = _hospital_query(user)
    if query is None:
        return empty
    col = db[COLLECTION]
    if status == "done":
        query.update({"status": "done", "done_at": {"$gte": _now() - timedelta(days=SHOW_DONE_DAYS)}})
        docs = list(col.find(query).sort("done_at", -1).limit(200))
    else:
        query["status"] = {"$in": ["open", "in_progress"]}
        docs = list(col.find(query))
        docs.sort(key=lambda d: (d["urgency"] != "urgent", _aware(d["requested_at"])))
    pids = [d["patient_id"] for d in docs]
    risk = _risk(db, pids)
    from api.hospital import care_teams              # avoid an import cycle
    teams = care_teams(db, pids)
    me = str(user["_id"])
    items = []
    for d in docs:
        item = public(d, risk.get(str(d["patient_id"])))
        team = teams.get(str(d["patient_id"]), {})
        item["doctor"] = (team.get("doctor") or {}).get("name")
        item["nurses"] = [n["name"] for n in team.get("nurses", [])]
        item["mine"] = (d.get("taken_by") or {}).get("id") == me
        items.append(item)
    active = [i for i in items if i["status"] != "done"]
    return {"items": items, "counts": {
        "open": sum(i["status"] == "open" for i in active),
        "in_progress": sum(i["status"] == "in_progress" for i in active),
        "overdue": sum(i["overdue"] for i in active)}}


def badge(db, user: dict) -> dict:
    """The menu badge: requests nobody has taken yet, in the caller's hospital."""
    _require_handler(user)
    query = _hospital_query(user)
    if query is None:
        return {"open": 0, "overdue": 0}
    cutoff = _now() - timedelta(days=OVERDUE_DAYS)
    col = db[COLLECTION]
    return {"open": col.count_documents({**query, "status": "open"}),
            "overdue": col.count_documents({**query, "status": {"$in": ["open", "in_progress"]},
                                            "requested_at": {"$lt": cutoff}})}

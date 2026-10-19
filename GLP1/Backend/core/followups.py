"""
Follow-up requests: a doctor or hospital nurse asks the hospital to follow a
patient up, and a case manager makes it happen.

Preventra passes the request along and records that it was dealt with; the
booking itself happens in the hospital's own system. So there are no dates,
slots or rooms here, only:

    open          asked for, nobody has taken it yet
    in_progress   one case manager has taken it ("I'll take it"), so no two
                  of them phone the same patient
    done          dealt with: booked | unreachable | not_needed, with a note

Who does what (core/access.py has the role lists):

    doctor, nurse          request one for their own patients, add a note,
                           see its status and the outcome
    case_manager           sees every request in their hospital, takes it,
                           marks it done - the person a request is for; a new
                           request rings the bell of the patient's main case
                           manager (core/hospital.py), or everyone's if none
    hospital_admin         sees the same list and can step in (take, mark done);
                           notified only if the hospital has no case manager
    superadmin             sees every hospital's, or the picked one's
    pharmacy               reports a refill problem for a patient who fills
                           there (report); it becomes a request - or a note on
                           the open one - for the patient's case manager. Sees
                           only the status of its own reports, never the notes.
                           A report on a request someone already holds rings
                           that person's bell; when the request is done, the
                           pharmacy's bell says so.
    patient                asks their case manager to get in touch, with a
                           reason (patient_request) - the same way: a new
                           request, or added to the open one. Sees whether
                           someone is on it and how it ended, never the notes.

Rules:
  * One request at a time per patient. A second person sees the open one and
    can add a note to it instead. Enforced by the database (a unique index on
    `active_patient`, which only an unfinished request carries), so two clicks
    at the same moment cannot make two.
  * An open request stays until someone deals with it; after OVERDUE_DAYS it
    is flagged. A finished one leaves the list at once, and its outcome shows
    on the patient's page for SHOW_DONE_DAYS. Nothing is deleted: every change
    is in the request's `history`.
  * The list shows what booking needs - patient number, risk, care team, who
    asked, how soon and their note - and none of the clinical layer
    (45 CFR 164.514(d), minimum necessary).

Stored in glp1_analytics.followups.
"""

from datetime import datetime, timedelta, timezone
from typing import Optional

from bson import ObjectId
from bson.errors import InvalidId
from fastapi import HTTPException
from pymongo.errors import DuplicateKeyError

from core.access import CARE_TEAM_ROLES, hospital_of, overview_only, require_patient, scope_query
from core.mongo import get_db, get_shared_identity_db

COLLECTION = "followups"
URGENCY = {"urgent": "Urgent - within days", "routine": "Routine - within weeks"}
OUTCOMES = {"booked": "Booked", "unreachable": "Couldn't reach patient", "not_needed": "Not needed"}
REQUEST_ROLES = CARE_TEAM_ROLES                                   # doctor, nurse
# What a pharmacy can report about a patient who fills there.
PHARMACY_PROBLEMS = {
    "missed_pickup":  "Missed a refill pickup",
    "prior_auth":     "Prior authorisation needed",
    "out_of_stock":   "Out of stock / supply problem",
    "side_effects":   "Mentioned side effects at the counter",
    "cost":           "Said they can't afford it",
    "other":          "Something else",
}
# Why a patient asks their case manager to get in touch (patient_request).
PATIENT_REASONS = {
    "side_effects": "Side effects from my medicine",
    "cost":         "Paying for my medicine",
    "refill":       "Trouble getting my refill",
    "questions":    "Questions about my treatment",
    "stopping":     "Thinking about stopping my medicine",
    "appointment":  "I'd like an appointment",
    "other":        "Something else",
}
HANDLE_ROLES = ("superadmin", "hospital_admin", "case_manager")
OVERDUE_DAYS = 7
SHOW_DONE_DAYS = 30
MAX_NOTE = 500

_indexed: set = set()          # databases that have the indexes (one per test world, too)


def _collection():
    return get_db()[COLLECTION]


async def ensure_indexes() -> None:
    key = id(get_db())
    if key not in _indexed:
        await _collection().create_index("active_patient", unique=True, sparse=True)
        await _collection().create_index([("hospital_id", 1), ("status", 1)])
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


async def _who(user: dict) -> dict:
    """The person acting, as stored on the request: id, name, role."""
    account = None
    try:
        account = await get_shared_identity_db().users.find_one({"_id": ObjectId(user["id"])}, {"name": 1})
    except (InvalidId, TypeError):
        pass
    who = {"id": user["id"], "name": (account or {}).get("name") or user.get("email", ""),
           "email": user.get("email", ""), "role": user["role"]}
    if user.get("pharmacy_id"):
        org = await get_shared_identity_db().pharmacies.find_one({"_id": user["pharmacy_id"]}, {"name": 1})
        who["pharmacy"] = (org or {}).get("name") or user["pharmacy_id"]
    return who


def _object_id(followup_id: str) -> ObjectId:
    try:
        return ObjectId(followup_id)
    except (InvalidId, TypeError):
        raise HTTPException(status_code=404, detail="Follow-up request not found")


def public(doc: dict, risk: Optional[float] = None) -> dict:
    """A request as the screens show it."""
    now = _now()
    asked = _aware(doc["requested_at"])
    out = {
        "id": str(doc["_id"]), "patient_idx": doc["patient_idx"], "hospital_id": doc.get("hospital_id"),
        "status": doc["status"], "urgency": doc["urgency"], "urgency_label": URGENCY[doc["urgency"]],
        "note": doc.get("note", ""), "requested_by": doc["requested_by"], "requested_at": asked.isoformat(),
        "notes": [{**n, "at": _aware(n["at"]).isoformat()} for n in doc.get("notes", [])],
        "taken_by": doc.get("taken_by"),
        "outcome": doc.get("outcome"), "outcome_label": OUTCOMES.get(doc.get("outcome")),
        "outcome_note": doc.get("outcome_note", ""), "done_by": doc.get("done_by"),
        "done_at": _aware(doc["done_at"]).isoformat() if doc.get("done_at") else None,
        "risk_at_request": doc.get("risk_at_request"),
        # Reported by a pharmacy: which problems, so the case manager sees why.
        "pharmacy_reports": [{**p, "at": _aware(p["at"]).isoformat(),
                              "problem_label": PHARMACY_PROBLEMS.get(p["problem"], p["problem"])}
                             for p in doc.get("pharmacy_reports", [])],
        # Asked for by the patient: why.
        "patient_requests": [{"reason": p["reason"], "note": p.get("note", ""), "at": _aware(p["at"]).isoformat(),
                              "reason_label": PATIENT_REASONS.get(p["reason"], p["reason"]),
                              "by": CARETAKER_NAME if p.get("by") == "caretaker" else PATIENT_NAME}
                             for p in doc.get("patient_requests", [])],
        "overdue": doc["status"] != "done" and now - asked > timedelta(days=OVERDUE_DAYS),
        "days_open": (now - asked).days,
    }
    if risk is not None:
        out["dropout_prob"] = risk
        before = doc.get("risk_at_request")
        out["risk_went_down"] = (doc["status"] != "done" and before is not None
                                 and before >= 0.50 and risk < 0.50)
    return out


async def _risk(idxs) -> dict:
    docs = await get_db().patients.find({"patient_idx": {"$in": [int(i) for i in idxs]}},
                                        {"_id": 0, "patient_idx": 1, "dropout_prob": 1}).to_list(length=None)
    return {int(d["patient_idx"]): float(d.get("dropout_prob") or 0) for d in docs}


# ------------------------------------------------------------ care team side
async def request(user: dict, scope: Optional[list], patient_idx: int,
                  urgency: str, note: Optional[str]) -> dict:
    if user["role"] not in REQUEST_ROLES:
        raise HTTPException(status_code=403, detail="Doctors and hospital nurses request follow-ups")
    require_patient(scope, patient_idx)
    if urgency not in URGENCY:
        raise HTTPException(status_code=422, detail="Choose urgent or routine")
    await ensure_indexes()
    access = await get_db().patient_access.find_one({"patient_idx": int(patient_idx)}, {"hospital_id": 1})
    who, now = await _who(user), _now()
    doc = {"patient_idx": int(patient_idx), "active_patient": int(patient_idx),
           "hospital_id": (access or {}).get("hospital_id"), "status": "open", "urgency": urgency,
           "note": _clean_note(note), "requested_by": who, "requested_at": now, "notes": [],
           "risk_at_request": (await _risk([patient_idx])).get(int(patient_idx)),
           "history": [{"action": "requested", "by": who, "at": now, "urgency": urgency}]}
    try:
        result = await _collection().insert_one(doc)
    except DuplicateKeyError:
        existing = await _collection().find_one({"active_patient": int(patient_idx)})
        raise HTTPException(status_code=409, detail={
            "code": "already_requested",
            "message": f"{existing['requested_by']['name']} already requested a follow-up for this patient",
            "followup": public(existing)})
    doc["_id"] = result.inserted_id
    return public(doc)


async def for_patient(user: dict, scope: Optional[list], patient_idx: int) -> dict:
    """The patient's open request, and the last finished one if recent."""
    require_patient(scope, patient_idx)
    col = _collection()
    active = await col.find_one({"active_patient": int(patient_idx)})
    since = _now() - timedelta(days=SHOW_DONE_DAYS)
    recent = await col.find({"patient_idx": int(patient_idx), "status": "done", "done_at": {"$gte": since}}) \
        .sort("done_at", -1).limit(1).to_list(length=1)
    if user["role"] in ("patient", "caretaker"):
        # The patient or their caretaker: whether someone is on it, never what
        # the care team wrote.
        return {"active": patient_view(active) if active else None,
                "recent": patient_view(recent[0]) if recent else None,
                "can_request": False, "can_handle": False, "can_ask": True,
                "reasons": [{"key": k, "label": v} for k, v in PATIENT_REASONS.items()]}
    if overview_only(user):
        # A pharmacy: whether someone is on it, never what the care team wrote.
        return {"active": pharmacy_view(active, user) if active else None,
                "recent": pharmacy_view(recent[0], user) if recent else None,
                "can_request": False, "can_handle": False, "can_report": True,
                "problems": [{"key": k, "label": v} for k, v in PHARMACY_PROBLEMS.items()]}
    return {"active": public(active) if active else None,
            "recent": public(recent[0]) if recent else None,
            "can_request": user["role"] in REQUEST_ROLES and active is None,
            "can_handle": user["role"] in HANDLE_ROLES}


async def add_note(user: dict, scope: Optional[list], followup_id: str, text: str) -> dict:
    if user["role"] == "pharmacy":
        raise HTTPException(status_code=403, detail="Pharmacies report a problem instead")
    if user["role"] in ("patient", "caretaker"):
        raise HTTPException(status_code=403, detail="Ask your case manager from your home page instead")
    doc = await _load(user, scope, followup_id)
    if doc["status"] == "done":
        raise HTTPException(status_code=409, detail="This request is already done")
    text = _clean_note(text)
    if not text:
        raise HTTPException(status_code=422, detail="Write a note")
    who, now = await _who(user), _now()
    await _collection().update_one({"_id": doc["_id"]}, {
        "$push": {"notes": {"by": who, "at": now, "text": text},
                  "history": {"action": "note", "by": who, "at": now}}})
    return public(await _collection().find_one({"_id": doc["_id"]}))


# --------------------------------------------------------------- pharmacy side
def pharmacy_view(doc: dict, user: dict) -> dict:
    """A request as a pharmacy may see it: who is on it and how it ended, and
    its own reports - not the care team's notes or the doctor's request."""
    mine = [p for p in doc.get("pharmacy_reports", []) if p.get("pharmacy_id") == user.get("pharmacy_id")]
    return {"id": str(doc["_id"]), "patient_idx": doc["patient_idx"], "status": doc["status"],
            "urgency": doc["urgency"], "requested_at": _aware(doc["requested_at"]).isoformat(),
            "by_us": bool(mine),
            "reports": [{"problem": p["problem"], "problem_label": PHARMACY_PROBLEMS.get(p["problem"], p["problem"]),
                         "at": _aware(p["at"]).isoformat(), "note": p.get("note", "")} for p in mine],
            "handled_by": (doc.get("taken_by") or {}).get("name"),
            "outcome_label": OUTCOMES.get(doc.get("outcome")),
            "done_at": _aware(doc["done_at"]).isoformat() if doc.get("done_at") else None}


async def report(user: dict, scope: Optional[list], patient_idx: int, problem: str,
                 urgency: str, note: Optional[str]) -> dict:
    """A pharmacy reports a refill problem. A new request for the patient's case
    manager - or, if one is already open, added to it, so there is still only
    one request per patient and nobody phones them twice."""
    if user["role"] != "pharmacy":
        raise HTTPException(status_code=403, detail="Pharmacies report refill problems")
    require_patient(scope, patient_idx)
    if problem not in PHARMACY_PROBLEMS:
        raise HTTPException(status_code=422, detail="Choose what the problem is")
    if urgency not in URGENCY:
        raise HTTPException(status_code=422, detail="Choose urgent or routine")
    note = _clean_note(note)
    if problem == "other" and len(note) < 5:
        raise HTTPException(status_code=422, detail="Say briefly what the problem is")
    await ensure_indexes()
    who, now = await _who(user), _now()
    entry = {"pharmacy_id": user.get("pharmacy_id"), "pharmacy": who.get("pharmacy"), "by": who,
             "problem": problem, "note": note, "at": now}
    text = f"{who.get('pharmacy') or who['name']}: {PHARMACY_PROBLEMS[problem]}" + (f" - {note}" if note else "")
    existing = await _collection().find_one({"active_patient": int(patient_idx)})
    if existing:
        updates = {"urgency": "urgent"} if urgency == "urgent" else {}  # never urgent -> routine
        holder = (existing.get("taken_by") or {}).get("id")
        if existing["status"] == "in_progress" and holder:
            # Someone is already on it: tell them, since a new request would not
            # reach their bell (it only shows untaken ones). See reported_for_bell.
            updates["report_alert"] = {"for": holder, "by": who.get("pharmacy") or who["name"],
                                       "problem": problem, "label": PHARMACY_PROBLEMS[problem], "at": now}
        await _collection().update_one({"_id": existing["_id"]}, {
            "$push": {"pharmacy_reports": entry, "notes": {"by": who, "at": now, "text": text},
                      "history": {"action": "pharmacy_report", "by": who, "at": now, "problem": problem}},
            **({"$set": updates} if updates else {})})
        return {"added_to_open": True, "followup": pharmacy_view(
            await _collection().find_one({"_id": existing["_id"]}), user)}
    access = await get_db().patient_access.find_one({"patient_idx": int(patient_idx)}, {"hospital_id": 1})
    doc = {"patient_idx": int(patient_idx), "active_patient": int(patient_idx),
           "hospital_id": (access or {}).get("hospital_id"), "status": "open", "urgency": urgency,
           # The request's own note says what was reported; who reported it is
           # requested_by (shown as the pharmacy's name).
           "note": PHARMACY_PROBLEMS[problem] + (f" - {note}" if note else ""),
           "requested_by": who, "requested_at": now, "notes": [],
           "pharmacy_reports": [entry],
           "risk_at_request": (await _risk([patient_idx])).get(int(patient_idx)),
           "history": [{"action": "requested", "by": who, "at": now, "urgency": urgency, "problem": problem}]}
    try:
        result = await _collection().insert_one(doc)
    except DuplicateKeyError:                           # opened at the same moment: add to it
        return await report(user, scope, patient_idx, problem, urgency, note)
    doc["_id"] = result.inserted_id
    return {"added_to_open": False, "followup": pharmacy_view(doc, user)}


# ---------------------------------------------------------------- patient side
PATIENT_NAME = "The patient"        # how a patient's own request is signed on staff screens
CARETAKER_NAME = "The caretaker"    # ... and their caretaker's


def patient_view(doc: dict) -> dict:
    """A request as the patient sees it: whether someone is on it, who, and how
    it ended, and what they themselves asked - not the care team's notes, the
    doctor's request or a pharmacy's report."""
    return {"id": str(doc["_id"]), "patient_idx": doc["patient_idx"], "status": doc["status"],
            "requested_at": _aware(doc["requested_at"]).isoformat(),
            "by_you": bool(doc.get("patient_requests")),
            "asked": [{"reason": p["reason"], "reason_label": PATIENT_REASONS.get(p["reason"], p["reason"]),
                       "at": _aware(p["at"]).isoformat()} for p in doc.get("patient_requests", [])],
            "handled_by": (doc.get("taken_by") or {}).get("name"),
            "outcome_label": OUTCOMES.get(doc.get("outcome")),
            "done_at": _aware(doc["done_at"]).isoformat() if doc.get("done_at") else None}


def own_patient(user: dict, scope: Optional[list], patient_idx: Optional[int] = None) -> int:
    """The patient a patient or caretaker account is acting for: a patient's
    only record, or the one a caretaker picked from those who linked them."""
    if user["role"] not in ("patient", "caretaker"):
        raise HTTPException(status_code=403, detail="For patients and their caretakers")
    if not scope:
        raise HTTPException(status_code=404, detail="No record is linked to your account yet")
    if user["role"] == "patient" or patient_idx is None:
        if user["role"] == "caretaker" and len(scope) > 1:
            raise HTTPException(status_code=422, detail="Choose which person you're asking about")
        return int(scope[0])
    require_patient(scope, patient_idx)
    return int(patient_idx)


async def patient_request(user: dict, scope: Optional[list], reason: str, note: Optional[str],
                          patient_idx: Optional[int] = None) -> dict:
    """A patient - or their caretaker - asks the case manager to get in touch.
    One request per patient, as always: a new one, or added to the one already
    open (and the case manager holding it hears of it, as for a pharmacy
    report)."""
    patient_idx = own_patient(user, scope, patient_idx)
    signed = CARETAKER_NAME if user["role"] == "caretaker" else PATIENT_NAME
    if reason not in PATIENT_REASONS:
        raise HTTPException(status_code=422, detail="Choose what it's about")
    note = _clean_note(note)
    if reason == "other" and len(note) < 5:
        raise HTTPException(status_code=422, detail="Say briefly what it's about")
    await ensure_indexes()
    who, now = {**(await _who(user)), "name": signed}, _now()
    who.pop("email", None)                         # staff screens never need the patient's login
    entry = {"reason": reason, "note": note, "at": now, "by": user["role"]}
    label = PATIENT_REASONS[reason] + (f" - {note}" if note else "")
    existing = await _collection().find_one({"active_patient": patient_idx})
    if existing:
        updates = {}
        holder = (existing.get("taken_by") or {}).get("id")
        if existing["status"] == "in_progress" and holder:
            updates["report_alert"] = {"for": holder, "by": signed, "problem": reason,
                                       "label": PATIENT_REASONS[reason], "at": now}
        await _collection().update_one({"_id": existing["_id"]}, {
            "$push": {"patient_requests": entry, "notes": {"by": who, "at": now, "text": f"{signed}: {label}"},
                      "history": {"action": "patient_request", "by": who, "at": now, "reason": reason}},
            **({"$set": updates} if updates else {})})
        return {"added_to_open": True, "followup": patient_view(
            await _collection().find_one({"_id": existing["_id"]}))}
    access = await get_db().patient_access.find_one({"patient_idx": patient_idx}, {"hospital_id": 1})
    if not (access or {}).get("hospital_id"):
        raise HTTPException(status_code=409, detail="Your record isn't with a hospital yet, so there is no case manager to ask")
    doc = {"patient_idx": patient_idx, "active_patient": patient_idx, "hospital_id": access["hospital_id"],
           "status": "open", "urgency": "routine", "note": label, "requested_by": who, "requested_at": now,
           "notes": [], "patient_requests": [entry],
           "risk_at_request": (await _risk([patient_idx])).get(patient_idx),
           "history": [{"action": "requested", "by": who, "at": now, "urgency": "routine", "reason": reason}]}
    try:
        result = await _collection().insert_one(doc)
    except DuplicateKeyError:                           # opened at the same moment: add to it
        return await patient_request(user, scope, reason, note, patient_idx)
    doc["_id"] = result.inserted_id
    return {"added_to_open": False, "followup": patient_view(doc)}


async def pharmacy_reports(user: dict) -> list:
    """This pharmacy's reports: still open, or finished in the last SHOW_DONE_DAYS."""
    since = _now() - timedelta(days=SHOW_DONE_DAYS)
    docs = await _collection().find({"pharmacy_reports.pharmacy_id": user.get("pharmacy_id") or "__none__",
                                     "$or": [{"status": {"$ne": "done"}}, {"done_at": {"$gte": since}}]}) \
        .sort("requested_at", -1).limit(200).to_list(length=200)
    return [pharmacy_view(d, user) for d in docs]


# ------------------------------------------------------- case manager side
async def _load(user: dict, scope: Optional[list], followup_id: str) -> dict:
    """A request the caller may see: their own patient's (care team), or their
    hospital's (case manager, admin). 404 otherwise, as for patients."""
    doc = await _collection().find_one({"_id": _object_id(followup_id)})
    if not doc:
        raise HTTPException(status_code=404, detail="Follow-up request not found")
    if user["role"] in HANDLE_ROLES:
        hospital = hospital_of(user)
        if hospital and doc.get("hospital_id") != hospital:
            raise HTTPException(status_code=404, detail="Follow-up request not found")
        if not hospital and user["role"] != "superadmin":
            raise HTTPException(status_code=404, detail="Follow-up request not found")
    else:
        require_patient(scope, doc["patient_idx"])
    return doc


def _require_handler(user: dict) -> None:
    if user["role"] not in HANDLE_ROLES:
        raise HTTPException(status_code=403, detail="Case managers handle follow-up requests")


async def take(user: dict, scope: Optional[list], followup_id: str) -> dict:
    _require_handler(user)
    doc = await _load(user, scope, followup_id)
    who, now = await _who(user), _now()
    # Only an open request can be taken - atomically, so two people can't both.
    result = await _collection().update_one({"_id": doc["_id"], "status": "open"}, {
        "$set": {"status": "in_progress", "taken_by": who, "taken_at": now},
        "$push": {"history": {"action": "taken", "by": who, "at": now}}})
    if result.modified_count == 0:
        current = await _collection().find_one({"_id": doc["_id"]})
        holder = (current.get("taken_by") or {}).get("name", "someone")
        raise HTTPException(status_code=409, detail={
            "code": "already_taken" if current["status"] == "in_progress" else "already_done",
            "message": f"{holder} is already handling this" if current["status"] == "in_progress"
                       else "This request is already done",
            "followup": public(current)})
    return public(await _collection().find_one({"_id": doc["_id"]}))


async def complete(user: dict, scope: Optional[list], followup_id: str,
                   outcome: str, note: Optional[str]) -> dict:
    """Done. A case manager may finish one they took, or an untaken one (which
    takes it on the way); one another case manager took is theirs to finish -
    unless the caller is the hospital admin or superadmin, stepping in."""
    _require_handler(user)
    if outcome not in OUTCOMES:
        raise HTTPException(status_code=422, detail="Choose booked, unreachable or not needed")
    doc = await _load(user, scope, followup_id)
    if doc["status"] == "done":
        raise HTTPException(status_code=409, detail={"code": "already_done", "message": "This request is already done",
                                                     "followup": public(doc)})
    holder = (doc.get("taken_by") or {}).get("id")
    if doc["status"] == "in_progress" and holder != user["id"] and user["role"] == "case_manager":
        raise HTTPException(status_code=409, detail={
            "code": "already_taken", "message": f"{doc['taken_by']['name']} is handling this",
            "followup": public(doc)})
    who, now = await _who(user), _now()
    taken = {} if doc.get("taken_by") else {"taken_by": who, "taken_at": now}
    # The pharmacies that reported on it hear it is done (pharmacy_done_for_bell).
    told = sorted({p["pharmacy_id"] for p in doc.get("pharmacy_reports", []) if p.get("pharmacy_id")})
    result = await _collection().update_one({"_id": doc["_id"], "status": doc["status"]}, {
        "$set": {"status": "done", "outcome": outcome, "outcome_note": _clean_note(note),
                 "done_by": who, "done_at": now, **taken, **({"pharmacy_alert": told} if told else {})},
        "$unset": {"active_patient": "", "handover_alert": "", "report_alert": ""},
        "$push": {"history": {"action": "done", "by": who, "at": now, "outcome": outcome}}})
    if result.modified_count == 0:
        raise HTTPException(status_code=409, detail="Someone else changed this request; reload and try again")
    return public(await _collection().find_one({"_id": doc["_id"]}))


async def handlers(user: dict) -> list:
    """The active case managers of the caller's hospital - who a request can be
    handed to."""
    _require_handler(user)
    hospital = hospital_of(user)
    if not hospital:
        return []
    docs = await get_shared_identity_db().users.find(
        {"role": "case_manager", "status": "active", "hospital_id": hospital},
        {"name": 1, "email": 1}).to_list(length=None)
    return sorted(({"id": str(d["_id"]), "name": d.get("name") or d.get("email", ""),
                    "email": d.get("email", "")} for d in docs), key=lambda p: p["name"].lower())


def _may_reassign(user: dict, doc: dict) -> None:
    """A taken request can be handed over or put back by whoever holds it, or
    by the hospital admin or superadmin (someone is off sick, on leave)."""
    holder = (doc.get("taken_by") or {}).get("id")
    if user["role"] == "case_manager" and holder != user["id"]:
        raise HTTPException(status_code=409, detail={
            "code": "not_yours", "message": f"{(doc.get('taken_by') or {}).get('name', 'Someone')} is handling this",
            "followup": public(doc)})


async def release(user: dict, scope: Optional[list], followup_id: str) -> dict:
    """Put a taken request back in the queue, for any case manager to take."""
    _require_handler(user)
    doc = await _load(user, scope, followup_id)
    if doc["status"] != "in_progress":
        raise HTTPException(status_code=409, detail="Only a request someone has taken can be put back")
    _may_reassign(user, doc)
    who, now = await _who(user), _now()
    result = await _collection().update_one({"_id": doc["_id"], "status": "in_progress"}, {
        "$set": {"status": "open"},
        "$unset": {"taken_by": "", "taken_at": "", "handover_alert": "", "report_alert": ""},
        "$push": {"history": {"action": "released", "by": who, "at": now,
                              "from": doc.get("taken_by")}}})
    if result.modified_count == 0:
        raise HTTPException(status_code=409, detail="Someone else changed this request; reload and try again")
    return public(await _collection().find_one({"_id": doc["_id"]}))


async def hand_over(user: dict, scope: Optional[list], followup_id: str, to_user_id: str) -> dict:
    """Give a request to another case manager of the same hospital. Works on an
    open request too (an admin sharing out the work)."""
    _require_handler(user)
    doc = await _load(user, scope, followup_id)
    if doc["status"] == "done":
        raise HTTPException(status_code=409, detail="This request is already done")
    if doc["status"] == "in_progress":
        _may_reassign(user, doc)
    elif user["role"] == "case_manager":
        raise HTTPException(status_code=409, detail="Take the request first, or let a colleague take it")
    target = next((h for h in await handlers(user) if h["id"] == to_user_id), None)
    if target is None or doc.get("hospital_id") != hospital_of(user):
        raise HTTPException(status_code=422, detail="Choose a case manager of this hospital")
    if (doc.get("taken_by") or {}).get("id") == to_user_id:
        raise HTTPException(status_code=409, detail=f"{target['name']} already has this request")
    who, now = await _who(user), _now()
    to = {"id": target["id"], "name": target["name"], "email": target["email"], "role": "case_manager"}
    result = await _collection().update_one({"_id": doc["_id"], "status": doc["status"]}, {
        "$set": {"status": "in_progress", "taken_by": to, "taken_at": now,
                 # The bell of the person it was given to, until they open
                 # Follow-ups or act on it (see handed_for_bell).
                 "handover_alert": {"for": to["id"], "by": who["name"], "at": now}},
        "$unset": {"report_alert": ""},
        "$push": {"history": {"action": "handed_over", "by": who, "at": now,
                              "from": doc.get("taken_by"), "to": to}}})
    if result.modified_count == 0:
        raise HTTPException(status_code=409, detail="Someone else changed this request; reload and try again")
    return public(await _collection().find_one({"_id": doc["_id"]}))


async def listing(user: dict, status: str = "active") -> dict:
    """The hospital's requests: the active ones (urgent and oldest first), or
    the recently finished ones."""
    _require_handler(user)
    query: dict = {}
    hospital = hospital_of(user)
    if hospital:
        query["hospital_id"] = hospital
    elif user["role"] != "superadmin":
        return {"items": [], "counts": {"open": 0, "in_progress": 0, "overdue": 0}}
    if status == "done":
        query.update({"status": "done", "done_at": {"$gte": _now() - timedelta(days=SHOW_DONE_DAYS)}})
        docs = await _collection().find(query).sort("done_at", -1).limit(200).to_list(length=200)
    else:
        query["status"] = {"$in": ["open", "in_progress"]}
        docs = await _collection().find(query).to_list(length=None)
        docs.sort(key=lambda d: (d["urgency"] != "urgent", _aware(d["requested_at"])))
    risk = await _risk([d["patient_idx"] for d in docs])
    from core.hospital import care_teams                         # avoid an import cycle
    teams = await care_teams([d["patient_idx"] for d in docs])
    items = []
    for d in docs:
        item = public(d, risk.get(d["patient_idx"]))
        team = teams.get(d["patient_idx"], {})
        item["doctors"] = [p["name"] for p in team.get("doctors", [])]
        item["nurses"] = [p["name"] for p in team.get("nurses", [])]
        item["mine"] = (d.get("taken_by") or {}).get("id") == user["id"]
        # The patient's main case manager, so the team sees whose patient it is.
        item["case_manager"] = team.get("case_manager")
        item["my_patient"] = (team.get("case_manager") or {}).get("id") == user["id"]
        items.append(item)
    active = [i for i in items if i["status"] != "done"]
    return {"items": items, "counts": {
        "open": sum(i["status"] == "open" for i in active),
        "in_progress": sum(i["status"] == "in_progress" for i in active),
        "overdue": sum(i["overdue"] for i in active)}}


# ------------------------------------------------------------- for the bell
async def case_managers_of(idxs) -> dict:
    """patient_idx -> the account id of their main case manager (or None)."""
    docs = await get_db().patient_access.find({"patient_idx": {"$in": [int(i) for i in idxs]}},
                                              {"_id": 0, "patient_idx": 1, "case_manager_id": 1}).to_list(length=None)
    return {int(d["patient_idx"]): d.get("case_manager_id") or None for d in docs}


async def has_case_manager(hospital_id: Optional[str]) -> bool:
    if not hospital_id:
        return False
    return await get_shared_identity_db().users.count_documents(
        {"role": "case_manager", "status": "active", "hospital_id": hospital_id}, limit=1) > 0


async def notifies(user: dict) -> bool:
    """Does this person have a bell? Case managers always; a hospital admin only
    when the hospital has no case manager; a pharmacy placed in one."""
    if user["role"] == "pharmacy":
        return bool(user.get("pharmacy_id"))
    if user["role"] == "case_manager":
        return bool(user.get("hospital_id"))
    if user["role"] == "hospital_admin":
        return not await has_case_manager(user.get("hospital_id"))
    return False


async def open_for_bell(user: dict) -> list:
    """New requests nobody has taken yet, in the caller's hospital.

    A patient with a main case manager rings only that person's bell, so two
    case managers don't both act on it; the rest of the team still sees it in
    the Follow-ups list, and can take it if that person is away. A patient
    with none (or whose case manager has left) rings everyone's, as before."""
    docs = await _collection().find({"hospital_id": user.get("hospital_id"), "status": "open"}).to_list(length=None)
    if user["role"] == "case_manager" and docs:
        owner = await case_managers_of([d["patient_idx"] for d in docs])
        team = {str(u["_id"]) for u in await get_shared_identity_db().users.find(
            {"role": "case_manager", "status": "active", "hospital_id": user.get("hospital_id")},
            {"_id": 1}).to_list(length=None)}
        docs = [d for d in docs
                if owner.get(int(d["patient_idx"])) == user["id"] or owner.get(int(d["patient_idx"])) not in team]
    risk = await _risk([d["patient_idx"] for d in docs])
    return [public(d, risk.get(d["patient_idx"])) for d in docs]


async def handed_for_bell(user: dict) -> list:
    """Requests a colleague (or the admin) handed to the caller that they have
    not seen yet: they leave the bell when the caller opens Follow-ups, or the
    request is finished or put back."""
    docs = await _collection().find({"hospital_id": user.get("hospital_id"), "status": "in_progress",
                                     "handover_alert.for": user["id"]}).to_list(length=None)
    risk = await _risk([d["patient_idx"] for d in docs])
    return [{**public(d, risk.get(d["patient_idx"])),
             "handed_by": d["handover_alert"]["by"],
             "handed_at": _aware(d["handover_alert"]["at"]).isoformat()} for d in docs]


async def reported_for_bell(user: dict) -> list:
    """Requests the caller holds that a pharmacy has reported on since they
    last opened Follow-ups. They leave the bell the same way as handed ones."""
    docs = await _collection().find({"hospital_id": user.get("hospital_id"), "status": "in_progress",
                                     "report_alert.for": user["id"]}).to_list(length=None)
    risk = await _risk([d["patient_idx"] for d in docs])
    return [{**public(d, risk.get(d["patient_idx"])),
             "reported_by": d["report_alert"]["by"],
             "reported_problem": d["report_alert"].get("label")
                                 or PHARMACY_PROBLEMS.get(d["report_alert"]["problem"], d["report_alert"]["problem"]),
             "reported_at": _aware(d["report_alert"]["at"]).isoformat()} for d in docs]


async def seen_handed(user: dict) -> int:
    """The caller has opened Follow-ups: what was handed to them, and pharmacy
    reports on what they hold, leave their bell. (Not done by the list itself -
    the menu badge loads that too.)"""
    _require_handler(user)
    handed = await _collection().update_many({"handover_alert.for": user["id"]},
                                             {"$unset": {"handover_alert": ""}})
    reported = await _collection().update_many({"report_alert.for": user["id"]},
                                               {"$unset": {"report_alert": ""}})
    return handed.modified_count + reported.modified_count


async def pharmacy_done_for_bell(user: dict) -> list:
    """A pharmacy's bell: its reports whose request was finished since it last
    opened its home page. Status and outcome only, as on that page."""
    if not user.get("pharmacy_id"):
        return []
    docs = await _collection().find({"status": "done", "pharmacy_alert": user["pharmacy_id"]}) \
        .sort("done_at", -1).limit(200).to_list(length=200)
    risk = await _risk([d["patient_idx"] for d in docs])
    return [{**pharmacy_view(d, user), "dropout_prob": risk.get(d["patient_idx"], 0.0)} for d in docs]


async def seen_pharmacy(user: dict) -> int:
    """The pharmacy has opened its home page: finished reports leave its bell."""
    if user["role"] != "pharmacy" or not user.get("pharmacy_id"):
        raise HTTPException(status_code=403, detail="For pharmacies")
    result = await _collection().update_many({"pharmacy_alert": user["pharmacy_id"]},
                                             {"$pull": {"pharmacy_alert": user["pharmacy_id"]}})
    return result.modified_count


async def done_since(scope: Optional[list]) -> dict:
    """patient_idx -> their latest request finished in the last SHOW_DONE_DAYS."""
    since = _now() - timedelta(days=SHOW_DONE_DAYS)
    docs = await _collection().find({**scope_query(scope), "status": "done", "done_at": {"$gte": since}}) \
        .sort("done_at", 1).to_list(length=None)
    return {d["patient_idx"]: d for d in docs}
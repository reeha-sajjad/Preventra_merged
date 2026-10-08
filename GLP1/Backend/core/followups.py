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
                           marks it done - the person a request is for
    hospital_admin         sees the same list and can step in (take, mark done);
                           notified only if the hospital has no case manager
    superadmin             sees every hospital's, or the picked one's

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

from core.access import CARE_TEAM_ROLES, hospital_of, require_patient, scope_query
from core.mongo import get_db, get_shared_identity_db

COLLECTION = "followups"
URGENCY = {"urgent": "Urgent - within days", "routine": "Routine - within weeks"}
OUTCOMES = {"booked": "Booked", "unreachable": "Couldn't reach patient", "not_needed": "Not needed"}
REQUEST_ROLES = CARE_TEAM_ROLES                                   # doctor, nurse
HANDLE_ROLES = ("superadmin", "hospital_admin", "case_manager")
OVERDUE_DAYS = 7
SHOW_DONE_DAYS = 30
MAX_NOTE = 500

_indexed = False


def _collection():
    return get_db()[COLLECTION]


async def ensure_indexes() -> None:
    global _indexed
    if not _indexed:
        await _collection().create_index("active_patient", unique=True, sparse=True)
        await _collection().create_index([("hospital_id", 1), ("status", 1)])
        _indexed = True


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
    return {"id": user["id"], "name": (account or {}).get("name") or user.get("email", ""),
            "email": user.get("email", ""), "role": user["role"]}


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
    return {"active": public(active) if active else None,
            "recent": public(recent[0]) if recent else None,
            "can_request": user["role"] in REQUEST_ROLES and active is None,
            "can_handle": user["role"] in HANDLE_ROLES}


async def add_note(user: dict, scope: Optional[list], followup_id: str, text: str) -> dict:
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
    result = await _collection().update_one({"_id": doc["_id"], "status": doc["status"]}, {
        "$set": {"status": "done", "outcome": outcome, "outcome_note": _clean_note(note),
                 "done_by": who, "done_at": now, **taken},
        "$unset": {"active_patient": ""},
        "$push": {"history": {"action": "done", "by": who, "at": now, "outcome": outcome}}})
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
        items.append(item)
    active = [i for i in items if i["status"] != "done"]
    return {"items": items, "counts": {
        "open": sum(i["status"] == "open" for i in active),
        "in_progress": sum(i["status"] == "in_progress" for i in active),
        "overdue": sum(i["overdue"] for i in active)}}


# ------------------------------------------------------------- for the bell
async def has_case_manager(hospital_id: Optional[str]) -> bool:
    if not hospital_id:
        return False
    return await get_shared_identity_db().users.count_documents(
        {"role": "case_manager", "status": "active", "hospital_id": hospital_id}, limit=1) > 0


async def notifies(user: dict) -> bool:
    """Does this person get new requests in their bell? Case managers always;
    a hospital admin only when the hospital has no case manager."""
    if user["role"] == "case_manager":
        return bool(user.get("hospital_id"))
    if user["role"] == "hospital_admin":
        return not await has_case_manager(user.get("hospital_id"))
    return False


async def open_for_bell(user: dict) -> list:
    """New requests nobody has taken yet, in the caller's hospital."""
    docs = await _collection().find({"hospital_id": user.get("hospital_id"), "status": "open"}).to_list(length=None)
    risk = await _risk([d["patient_idx"] for d in docs])
    return [public(d, risk.get(d["patient_idx"])) for d in docs]


async def done_since(scope: Optional[list]) -> dict:
    """patient_idx -> their latest request finished in the last SHOW_DONE_DAYS."""
    since = _now() - timedelta(days=SHOW_DONE_DAYS)
    docs = await _collection().find({**scope_query(scope), "status": "done", "done_at": {"$gte": since}}) \
        .sort("done_at", 1).to_list(length=None)
    return {d["patient_idx"]: d for d in docs}
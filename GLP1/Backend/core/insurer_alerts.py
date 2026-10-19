"""
An insurer's alerts - two kinds, and only these, so the bell stays about the
predictions and about billing. Mirrors Readmissions/api/insurer_alerts.py.

1. A member turned critical (dropout risk 75% or more).
   The first time an insurer account asks, the risk of every member is taken
   as a baseline and nothing rings - otherwise an insurer with hundreds of
   critical members would get hundreds of alerts on day one. After that, a
   member rings when their risk crosses into critical, or when a new member
   arrives already critical. Opening the member (or "Mark all as read") takes
   a new baseline for them. GLP-1 has one score per patient, so this fires
   only when the scores change (a retrained model or new data).

2. A hospital flagged a member for billing.
   A hospital admin or case manager sends their insurer a flag about one
   patient - a billing question, prior authorisation or coverage check - with
   a short note. It reaches the insurer by member ID (core/member_ids.py). The
   insurer replies: received, approved or needs information, with a note; the
   hospital sees the reply on the patient's Insurance card.

What travels in a flag is what billing needs (45 CFR 164.506(c), payment;
164.502(b), minimum necessary): the member ID, the patient number, the hospital,
the kind of question and the note. The form says not to put clinical detail in
the note.

Stored in glp1_analytics.insurer_watch (one baseline per insurer account) and
glp1_analytics.insurer_flags. The dataset is never written to.
"""
from datetime import datetime, timezone
from typing import Iterable, Optional

from bson import ObjectId
from bson.errors import InvalidId
from fastapi import HTTPException

from core.access import require_patient, scope_query
from core.mongo import get_db, get_shared_identity_db

CRITICAL = 0.75
WATCH = "insurer_watch"
FLAGS = "insurer_flags"
KINDS = {"billing_query": "Billing question", "prior_auth": "Prior authorisation",
         "coverage_check": "Coverage check"}
STATUSES = {"sent": "Waiting for the insurer", "received": "Received", "approved": "Approved",
            "needs_info": "Needs more information"}
REPLY_STATUSES = ("received", "approved", "needs_info")
FLAG_ROLES = ("superadmin", "hospital_admin", "case_manager")
MAX_NOTE = 500


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _aware(at: datetime) -> datetime:
    return at if at.tzinfo else at.replace(tzinfo=timezone.utc)


async def _who(user: dict) -> dict:
    try:
        account = await get_shared_identity_db().users.find_one({"_id": ObjectId(user["id"])}, {"name": 1})
    except (InvalidId, TypeError):
        account = None
    return {"id": user["id"], "role": user["role"], "name": (account or {}).get("name") or user.get("email", "")}


# ------------------------------------------------------------ turned critical
async def _risk(scope: Optional[list]) -> dict:
    rows = await get_db().patients.find(scope_query(scope), {"_id": 0, "patient_idx": 1, "dropout_prob": 1}) \
        .to_list(length=None)
    return {str(int(r["patient_idx"])): float(r.get("dropout_prob") or 0) for r in rows}


async def turned_critical(user: dict, scope: Optional[list]) -> list:
    """Members who became critical since this insurer account's baseline."""
    now = await _risk(scope)
    col = get_db()[WATCH]
    doc = await col.find_one({"user_id": user["id"]})
    if doc is None:                                     # first look: the baseline, no alerts
        await col.update_one({"user_id": user["id"]}, {"$set": {"risk": now, "at": _now()}}, upsert=True)
        return []
    before = doc.get("risk") or {}
    out = []
    for idx, prob in now.items():
        was = before.get(idx)
        if prob >= CRITICAL and (was is None or was < CRITICAL):
            out.append({"patient_idx": int(idx), "dropout_prob": round(prob, 4),
                        "previous_prob": round(was, 4) if was is not None else None})
    return sorted(out, key=lambda i: -i["dropout_prob"])


async def mark_seen(user: dict, scope: Optional[list], patient_idxs: Optional[Iterable[int]] = None) -> int:
    """Take a new baseline: for these members, or (None) for all of them."""
    now = await _risk(scope)
    col = get_db()[WATCH]
    if patient_idxs is None:
        await col.update_one({"user_id": user["id"]}, {"$set": {"risk": now, "at": _now()}}, upsert=True)
        return len(now)
    sets = {f"risk.{int(i)}": now[str(int(i))] for i in patient_idxs if str(int(i)) in now}
    if sets:
        await col.update_one({"user_id": user["id"]}, {"$set": sets}, upsert=True)
    return len(sets)


# ----------------------------------------------------------- billing flags
def _public(doc: dict, names: Optional[dict] = None) -> dict:
    names = names or {}
    return {"id": str(doc["_id"]), "patient_idx": doc["patient_idx"], "member_id": doc.get("member_id"),
            "insurer_id": doc["insurer_id"], "insurer": names.get(doc["insurer_id"], doc["insurer_id"]),
            "hospital_id": doc.get("hospital_id"), "hospital": names.get(doc.get("hospital_id"), doc.get("hospital_id")),
            "kind": doc["kind"], "kind_label": KINDS[doc["kind"]], "note": doc.get("note", ""),
            "status": doc["status"], "status_label": STATUSES[doc["status"]],
            "reply_note": doc.get("reply_note", ""), "sent_by": doc["sent_by"]["name"],
            "replied_by": (doc.get("replied_by") or {}).get("name"),
            "sent_at": _aware(doc["sent_at"]).isoformat(),
            "replied_at": _aware(doc["replied_at"]).isoformat() if doc.get("replied_at") else None}


async def _names(docs: list) -> dict:
    ident = get_shared_identity_db()
    ins = {i["_id"]: i.get("name", i["_id"]) for i in await ident.insurers.find(
        {"_id": {"$in": list({d["insurer_id"] for d in docs})}}).to_list(length=None)}
    hos = {h["_id"]: h.get("name", h["_id"]) for h in await ident.hospitals.find(
        {"_id": {"$in": list({d.get("hospital_id") for d in docs if d.get("hospital_id")})}}).to_list(length=None)}
    return {**ins, **hos}


def _clean(note: Optional[str]) -> str:
    note = (note or "").strip()
    if len(note) > MAX_NOTE:
        raise HTTPException(status_code=422, detail=f"Keep the note under {MAX_NOTE} characters")
    return note


async def send_flag(user: dict, scope: Optional[list], patient_idx: int, kind: str, note: Optional[str]) -> dict:
    """A hospital flags a patient to their insurer."""
    require_patient(scope, patient_idx)
    if user["role"] not in FLAG_ROLES:
        raise HTTPException(status_code=403, detail="Hospital admins and case managers send flags to insurers")
    if kind not in KINDS:
        raise HTTPException(status_code=422, detail="Choose what the flag is about")
    note = _clean(note)
    if len(note) < 5:
        raise HTTPException(status_code=422, detail="Say briefly what the insurer needs to look at")
    acc = await get_db().patient_access.find_one({"patient_idx": int(patient_idx)},
                                                 {"insurer_id": 1, "insurer_member_id": 1, "hospital_id": 1}) or {}
    if not acc.get("insurer_id"):
        raise HTTPException(status_code=409, detail="This patient has no insurer on file")
    who, now = await _who(user), _now()
    doc = {"patient_idx": int(patient_idx), "insurer_id": acc["insurer_id"], "member_id": acc.get("insurer_member_id"),
           "hospital_id": acc.get("hospital_id"), "kind": kind, "note": note, "status": "sent",
           "sent_by": who, "sent_at": now, "history": [{"action": "sent", "by": who, "at": now}]}
    doc["_id"] = (await get_db()[FLAGS].insert_one(doc)).inserted_id
    return _public(doc, await _names([doc]))


async def flags_for_patient(user: dict, scope: Optional[list], patient_idx: int) -> list:
    """The flags about one patient - the hospital's billing staff, or the insurer."""
    require_patient(scope, patient_idx)
    if user["role"] not in FLAG_ROLES + ("insurer",):
        raise HTTPException(status_code=403, detail="For billing staff and the insurer")
    query = {"patient_idx": int(patient_idx)}
    if user["role"] == "insurer":
        query["insurer_id"] = user.get("insurer_id") or "__none__"
    docs = await get_db()[FLAGS].find(query).sort("sent_at", -1).limit(20).to_list(length=20)
    names = await _names(docs)
    return [_public(d, names) for d in docs]


async def insurer_flags(user: dict, status: str = "open") -> list:
    """An insurer's flags from hospitals: open (not finished) or all recent."""
    if user["role"] != "insurer":
        raise HTTPException(status_code=403, detail="For insurers")
    query = {"insurer_id": user.get("insurer_id") or "__none__"}
    if status == "open":
        query["status"] = {"$in": ["sent", "received", "needs_info"]}
    docs = await get_db()[FLAGS].find(query).sort("sent_at", -1).limit(200).to_list(length=200)
    names = await _names(docs)
    return [_public(d, names) for d in docs]


async def reply(user: dict, flag_id: str, status: str, note: Optional[str]) -> dict:
    """The insurer answers a flag."""
    if user["role"] != "insurer":
        raise HTTPException(status_code=403, detail="Insurers reply to flags")
    if status not in REPLY_STATUSES:
        raise HTTPException(status_code=422, detail="Choose received, approved or needs information")
    try:
        oid = ObjectId(flag_id)
    except (InvalidId, TypeError):
        raise HTTPException(status_code=404, detail="Flag not found")
    col = get_db()[FLAGS]
    doc = await col.find_one({"_id": oid, "insurer_id": user.get("insurer_id") or "__none__"})
    if not doc:
        raise HTTPException(status_code=404, detail="Flag not found")
    who, now = await _who(user), _now()
    await col.update_one({"_id": oid}, {"$set": {"status": status, "reply_note": _clean(note), "replied_by": who,
                                                 "replied_at": now},
                                        "$push": {"history": {"action": status, "by": who, "at": now}}})
    doc = await col.find_one({"_id": oid})
    return _public(doc, await _names([doc]))


# ------------------------------------------------------------------ the bell
async def bell_items(user: dict, scope: Optional[list]) -> list:
    """For core/notifications.py: new flags first, then members turned critical."""
    flags = [f for f in await insurer_flags(user, "open") if f["status"] == "sent"]
    items = [{"patient_idx": f["patient_idx"], "kinds": ["insurer_flag"], "flag_id": f["id"],
              "member_id": f["member_id"], "kind_label": f["kind_label"], "hospital": f["hospital"],
              "requested_by": f["sent_by"], "dropout_prob": None, "urgent": True} for f in flags]
    critical = await turned_critical(user, scope)
    acc = {a["patient_idx"]: a.get("insurer_member_id") for a in await get_db().patient_access.find(
        {"patient_idx": {"$in": [c["patient_idx"] for c in critical]}},
        {"_id": 0, "patient_idx": 1, "insurer_member_id": 1}).to_list(length=None)}
    items += [{"patient_idx": c["patient_idx"], "kinds": ["turned_critical"], "member_id": acc.get(c["patient_idx"]),
               "dropout_prob": c["dropout_prob"], "previous_prob": c["previous_prob"], "urgent": True}
              for c in critical]
    return items
"""
An insurer's alerts in Readmissions - two kinds, and only these, so the bell
stays about the predictions and about billing. The twin of GLP-1's
GLP1/Backend/core/insurer_alerts.py - same rules, same words.

1. A member turned high risk (readmission band "High", the same line the
   insurer's home page lists).
   The first time an insurer account asks, every member's band is taken as a
   baseline and nothing rings - otherwise an insurer with hundreds of high-risk
   members would get hundreds of alerts on day one. After that, a member rings
   when their latest band moves into High, or when a new member arrives
   already High. Opening the member (or "Mark all as read") takes a new
   baseline for them. Readmissions rescoring weekly makes this the alert that
   actually moves.

2. A hospital flagged a member for billing.
   A hospital admin or case manager sends their insurer a flag about one
   patient - a billing question, prior authorisation or coverage check - with
   a short note. It reaches the insurer by member ID (api/member_ids.py). The
   insurer replies: received, approved or needs information, with a note; the
   hospital sees the reply on the patient's Insurance card.

What travels in a flag is what billing needs (45 CFR 164.506(c), payment;
164.502(b), minimum necessary): the member ID, the patient ID, the hospital,
the kind of question and the note. The form says not to put clinical detail in
the note.

Stored in neuroshield.insurer_watch (one baseline per insurer account) and
neuroshield.insurer_flags. The worklist and the dataset are only read.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Iterable, Optional

from bson import ObjectId
from bson.errors import InvalidId
from fastapi import HTTPException
from pymongo import DESCENDING

from api import access, auth
from api.db_utils import get_latest_batch_date

HIGH = "High"
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


def _who(user: dict) -> dict:
    return {"id": str(user["_id"]), "role": user["role"], "name": user.get("name") or user.get("email", "")}


def _key(patient_id) -> str:
    # Mongo field names cannot hold dots; patient ids here never do, but be safe.
    return str(patient_id).replace(".", "_")


# ------------------------------------------------------------ turned high risk
def _risk(db, scope: Optional[list]) -> dict:
    """{patient_id: (band, score)} for the caller's members, latest batch."""
    ids = [str(p) for p in (scope or [])]
    if not ids:
        return {}
    batch = get_latest_batch_date(db, "patient_worklist")
    out = {}
    for r in db["patient_worklist"].find({"patient_id": {"$in": access.id_variants(ids)}, "batch_date": batch},
                                         {"_id": 0, "patient_id": 1, "current_band": 1, "risk_band": 1,
                                          "current_score": 1, "risk_score": 1}):
        try:
            score = float(r.get("current_score", r.get("risk_score", 0)) or 0)
        except (TypeError, ValueError):
            score = 0.0
        out[str(r["patient_id"])] = (str(r.get("current_band") or r.get("risk_band") or "").title(), round(score, 1))
    return out


def turned_high(db, user: dict, scope: Optional[list]) -> list:
    """Members whose band became High since this insurer account's baseline."""
    now = _risk(db, scope)
    col = db[WATCH]
    doc = col.find_one({"user_id": str(user["_id"])})
    if doc is None:                                     # first look: the baseline, no alerts
        col.update_one({"user_id": str(user["_id"])},
                       {"$set": {"band": {_key(p): b for p, (b, _) in now.items()}, "at": _now()}}, upsert=True)
        return []
    before = doc.get("band") or {}
    out = []
    for pid, (band, score) in now.items():
        was = before.get(_key(pid))
        if band == HIGH and was != HIGH:
            out.append({"patient_id": pid, "risk_score": score, "previous_band": was})
    return sorted(out, key=lambda i: -i["risk_score"])


def mark_seen(db, user: dict, scope: Optional[list], patient_ids: Optional[Iterable[str]] = None) -> int:
    """Take a new baseline: for these members, or (None) for all of them."""
    now = _risk(db, scope)
    col = db[WATCH]
    if patient_ids is None:
        col.update_one({"user_id": str(user["_id"])},
                       {"$set": {"band": {_key(p): b for p, (b, _) in now.items()}, "at": _now()}}, upsert=True)
        return len(now)
    sets = {f"band.{_key(p)}": now[str(p)][0] for p in patient_ids if str(p) in now}
    if sets:
        col.update_one({"user_id": str(user["_id"])}, {"$set": sets}, upsert=True)
    return len(sets)


# ----------------------------------------------------------- billing flags
def _names(db, docs: list) -> dict:
    ident = db.client[auth.IDENTITY_DB]
    ins = {i["_id"]: i.get("name", i["_id"]) for i in ident["insurers"].find(
        {"_id": {"$in": list({d["insurer_id"] for d in docs})}})}
    hos = {h["_id"]: h.get("name", h["_id"]) for h in ident["hospitals"].find(
        {"_id": {"$in": list({d.get("hospital_id") for d in docs if d.get("hospital_id")})}})}
    return {**ins, **hos}


def _public(doc: dict, names: dict) -> dict:
    return {"id": str(doc["_id"]), "patient_id": doc["patient_id"], "member_id": doc.get("member_id"),
            "insurer_id": doc["insurer_id"], "insurer": names.get(doc["insurer_id"], doc["insurer_id"]),
            "hospital_id": doc.get("hospital_id"), "hospital": names.get(doc.get("hospital_id"), doc.get("hospital_id")),
            "kind": doc["kind"], "kind_label": KINDS[doc["kind"]], "note": doc.get("note", ""),
            "status": doc["status"], "status_label": STATUSES[doc["status"]],
            "reply_note": doc.get("reply_note", ""), "sent_by": doc["sent_by"]["name"],
            "replied_by": (doc.get("replied_by") or {}).get("name"),
            "sent_at": _aware(doc["sent_at"]).isoformat(),
            "replied_at": _aware(doc["replied_at"]).isoformat() if doc.get("replied_at") else None}


def _clean(note: Optional[str]) -> str:
    note = (note or "").strip()
    if len(note) > MAX_NOTE:
        raise HTTPException(status_code=422, detail=f"Keep the note under {MAX_NOTE} characters")
    return note


def send_flag(db, user: dict, scope: Optional[list], patient_id: str, kind: str, note: Optional[str]) -> dict:
    """A hospital flags a patient to their insurer."""
    access.require_patient(scope, str(patient_id))
    if user["role"] not in FLAG_ROLES:
        raise HTTPException(status_code=403, detail="Hospital admins and case managers send flags to insurers")
    if kind not in KINDS:
        raise HTTPException(status_code=422, detail="Choose what the flag is about")
    note = _clean(note)
    if len(note) < 5:
        raise HTTPException(status_code=422, detail="Say briefly what the insurer needs to look at")
    own = db["care_actions"].find_one({"patient_id": {"$in": access.id_variants([str(patient_id)])}},
                                      {"patient_id": 1, "insurer_id": 1, "insurer_member_id": 1, "hospital_id": 1}) or {}
    if not own.get("insurer_id"):
        raise HTTPException(status_code=409, detail="This patient has no insurer on file")
    who, now = _who(user), _now()
    doc = {"patient_id": str(own["patient_id"]), "insurer_id": own["insurer_id"],
           "member_id": own.get("insurer_member_id"), "hospital_id": own.get("hospital_id"), "kind": kind,
           "note": note, "status": "sent", "sent_by": who, "sent_at": now,
           "history": [{"action": "sent", "by": who, "at": now}]}
    doc["_id"] = db[FLAGS].insert_one(doc).inserted_id
    return _public(doc, _names(db, [doc]))


def flags_for_patient(db, user: dict, scope: Optional[list], patient_id: str) -> list:
    """The flags about one patient - the hospital's billing staff, or the insurer."""
    access.require_patient(scope, str(patient_id))
    if user["role"] not in FLAG_ROLES + ("insurer",):
        raise HTTPException(status_code=403, detail="For billing staff and the insurer")
    query: dict = {"patient_id": {"$in": access.id_variants([str(patient_id)])}}
    if user["role"] == "insurer":
        query["insurer_id"] = user.get("insurer_id") or "__none__"
    docs = list(db[FLAGS].find(query).sort("sent_at", DESCENDING).limit(20))
    names = _names(db, docs)
    return [_public(d, names) for d in docs]


def insurer_flags(db, user: dict, status: str = "open") -> list:
    """An insurer's flags from hospitals: open (not finished) or all recent."""
    if user["role"] != "insurer":
        raise HTTPException(status_code=403, detail="For insurers")
    query: dict = {"insurer_id": user.get("insurer_id") or "__none__"}
    if status == "open":
        query["status"] = {"$in": ["sent", "received", "needs_info"]}
    docs = list(db[FLAGS].find(query).sort("sent_at", DESCENDING).limit(200))
    names = _names(db, docs)
    return [_public(d, names) for d in docs]


def reply(db, user: dict, flag_id: str, status: str, note: Optional[str]) -> dict:
    """The insurer answers a flag."""
    if user["role"] != "insurer":
        raise HTTPException(status_code=403, detail="Insurers reply to flags")
    if status not in REPLY_STATUSES:
        raise HTTPException(status_code=422, detail="Choose received, approved or needs information")
    try:
        oid = ObjectId(flag_id)
    except (InvalidId, TypeError):
        raise HTTPException(status_code=404, detail="Flag not found")
    col = db[FLAGS]
    doc = col.find_one({"_id": oid, "insurer_id": user.get("insurer_id") or "__none__"})
    if not doc:
        raise HTTPException(status_code=404, detail="Flag not found")
    who, now = _who(user), _now()
    col.update_one({"_id": oid}, {"$set": {"status": status, "reply_note": _clean(note), "replied_by": who,
                                           "replied_at": now},
                                  "$push": {"history": {"action": status, "by": who, "at": now}}})
    doc = col.find_one({"_id": oid})
    return _public(doc, _names(db, [doc]))


# ------------------------------------------------------------------ the bell
def bell(db, user: dict, scope: Optional[list]) -> list:
    """For the bell: new flags first, then members who turned high risk."""
    if user["role"] != "insurer" or not user.get("insurer_id"):
        return []
    items = [{"kind": "insurer_flag", "flag_id": f["id"], "patient_id": f["patient_id"], "member_id": f["member_id"],
              "kind_label": f["kind_label"], "hospital": f["hospital"], "by": f["sent_by"], "at": f["sent_at"]}
             for f in insurer_flags(db, user, "open") if f["status"] == "sent"]
    high = turned_high(db, user, scope)
    own = {str(o["patient_id"]): o.get("insurer_member_id") for o in db["care_actions"].find(
        {"patient_id": {"$in": access.id_variants([h["patient_id"] for h in high])}},
        {"_id": 0, "patient_id": 1, "insurer_member_id": 1})} if high else {}
    items += [{"kind": "turned_high", "patient_id": h["patient_id"], "member_id": own.get(h["patient_id"]),
               "risk_score": h["risk_score"], "previous_band": h["previous_band"]} for h in high]
    return items
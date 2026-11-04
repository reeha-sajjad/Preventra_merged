"""
"Who has looked at my record": the patient's own view of the access log.

Every first look at a patient's clinical details per sign-in is already logged
(api/access_log.py; api/main._require_detail, api/emergency.py, the pharmacy and
caretaker pages). This reads those entries back for one patient, in words a
patient understands: who (a name, or the organisation), in what role, why, and
when. HIPAA gives a patient the right to an accounting of disclosures of their
record (45 CFR 164.528); this shows more than the rule requires - their own care
team's looks too - because "who has seen my record" is the question a patient
actually asks.

Only the patient sees it, on their own home page. A caretaker does not: it is
the patient's record, not theirs.

Repeated looks by the same person for the same reason on the same day are shown
once ("3 times"), newest first.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from bson import ObjectId
from bson.errors import InvalidId

from api import access_log, auth

SHOW = 30
SCAN = 500
ROLE_WORDS = {
    "doctor": "Doctor", "nurse": "Nurse", "case_manager": "Case manager",
    "hospital_admin": "Hospital administrator", "insurer": "Your insurer", "pharmacy": "Your pharmacy",
    "caretaker": "Your caretaker", "superadmin": "Preventra support team",
}


def _oid(value) -> Optional[ObjectId]:
    try:
        return ObjectId(str(value))
    except (InvalidId, TypeError):
        return None


def _names(db, collection: str, ids: set) -> dict:
    if not ids:
        return {}
    return {d["_id"]: d.get("name") or d["_id"]
            for d in db.client[auth.IDENTITY_DB][collection].find({"_id": {"$in": list(ids)}}, {"name": 1})}


def viewed_by(db, patient_id: str) -> dict:
    rows = list(access_log.collection(db).find(
        {"app": access_log.APP, "patient_id": str(patient_id)},
        {"_id": 0, "user_id": 1, "role": 1, "reason_label": 1, "reason": 1, "at": 1})
        .sort("at", -1).limit(SCAN))
    people = {str(u["_id"]): u for u in auth.users(db).find(
        {"_id": {"$in": [o for o in (_oid(r.get("user_id")) for r in rows) if o]}},
        {"name": 1, "role": 1, "hospital_id": 1, "insurer_id": 1, "pharmacy_id": 1})}
    hospitals = _names(db, "hospitals", {p.get("hospital_id") for p in people.values() if p.get("hospital_id")})
    insurers = _names(db, "insurers", {p.get("insurer_id") for p in people.values() if p.get("insurer_id")})
    pharmacies = _names(db, "pharmacies", {p.get("pharmacy_id") for p in people.values() if p.get("pharmacy_id")})

    out, seen = [], {}
    for r in rows:
        at = r.get("at")
        if isinstance(at, datetime) and at.tzinfo is None:
            at = at.replace(tzinfo=timezone.utc)
        day = at.date().isoformat() if isinstance(at, datetime) else str(at)[:10]
        key = (r.get("user_id"), r.get("reason"), day)
        if key in seen:
            seen[key]["times"] += 1
            continue
        p = people.get(str(r.get("user_id")), {})
        role = r.get("role") or p.get("role")
        if role == "insurer":
            who = insurers.get(p.get("insurer_id")) or "Your insurer"
        elif role == "pharmacy":
            who = pharmacies.get(p.get("pharmacy_id")) or "Your pharmacy"
        elif role == "superadmin":
            who = "Preventra support team"
        else:
            who = p.get("name") or ROLE_WORDS.get(role, "Someone at your hospital")
        where = hospitals.get(p.get("hospital_id")) if role in ("doctor", "nurse", "case_manager",
                                                                 "hospital_admin") else None
        entry = {"who": who, "role": ROLE_WORDS.get(role, role), "where": where,
                 "why": r.get("reason_label") or r.get("reason"),
                 "emergency": r.get("reason") == "emergency_access",
                 "at": at.isoformat() if isinstance(at, datetime) else at, "times": 1}
        seen[key] = entry
        out.append(entry)
    return {"entries": out[:SHOW], "total": len(rows)}

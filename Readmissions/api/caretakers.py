"""
Caretakers: the family members or home carers who help a patient, linked to
that patient's record so they can see the patient's plain-words home page
(api/patient_home.py) and help with their care. The twin of GLP-1's
GLP1/Backend/core/caretakers.py - same rules, same words.

Who links, and who can see the list (a patient's caretakers are on their
ownership record, care_actions.caretaker_ids):

    patient                     adds a caretaker by email, from their own page,
                                and removes any of them at any time
    hospital_admin,             link a caretaker to a patient of their hospital,
    case_manager, superadmin    confirming the patient agreed (recorded), and
                                remove one
    caretaker                   can remove themselves (stop caring for someone)

HIPAA lets a provider share what is relevant to someone's involvement in a
patient's care when the patient agrees or, given the chance, does not object
(45 CFR 164.510(b)). The patient's own action - or staff confirming the patient
agreed - is that agreement, and the patient can always see and undo it.

Only an existing, active caretaker account can be linked: the system does not
verify that a person owns the email they sign up with, so a waiting invite
could be claimed by whoever signed up first with the address. The patient sees
the caretaker's name and email once linked. Every link and removal is kept in
care_actions.caretaker_history. Linking here links the patient's Readmissions
record; GLP-1 keeps its own.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from bson import ObjectId
from bson.errors import InvalidId
from fastapi import HTTPException

from api import access, auth

MAX_CARETAKERS = 10
STAFF_ROLES = access.ACTIONS["assign"]            # superadmin, hospital_admin, case_manager


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _oids(ids) -> list:
    out = []
    for i in ids:
        try:
            out.append(ObjectId(i))
        except (InvalidId, TypeError):
            pass
    return out


def _people(db, ids) -> dict:
    return {str(u["_id"]): {"id": str(u["_id"]), "name": u.get("name") or u.get("email", ""),
                            "email": u.get("email", "")}
            for u in auth.users(db).find({"_id": {"$in": _oids(ids)}}, {"name": 1, "email": 1})}


def _actor(user: dict) -> dict:
    return {"id": str(user["_id"]), "role": user["role"],
            "name": user.get("name") or ("The patient" if user["role"] == "patient" else user.get("email", ""))}


def _record(db, patient_id: str) -> dict:
    return db["care_actions"].find_one({"patient_id": {"$in": access.id_variants([patient_id])}},
                                       {"patient_id": 1, "caretaker_ids": 1, "caretaker_history": 1}) or {}


def _patient_of(user: dict, scope: Optional[list], patient_id: Optional[str]) -> str:
    if user["role"] == "patient":
        if not scope:
            raise HTTPException(status_code=404, detail="No record is linked to your account yet")
        return str(scope[0])
    if user["role"] in STAFF_ROLES or user["role"] == "caretaker":
        if not patient_id:
            raise HTTPException(status_code=422, detail="Name the patient")
        access.require_patient(scope, str(patient_id))
        return str(patient_id)
    raise HTTPException(status_code=403, detail="Patients and hospital staff manage caretakers")


def listing(db, user: dict, scope: Optional[list], patient_id: Optional[str] = None) -> dict:
    if user["role"] == "caretaker":
        raise HTTPException(status_code=403, detail="Patients and hospital staff manage caretakers")
    pid = _patient_of(user, scope, patient_id)
    doc = _record(db, pid)
    ids = doc.get("caretaker_ids") or []
    people = _people(db, ids)
    added = {}
    for h in doc.get("caretaker_history") or []:
        if h["action"] == "linked":
            at = h["at"]
            added[h["caretaker_id"]] = {"by": h["by"], "at": at.isoformat() if hasattr(at, "isoformat") else at}
    return {"patient_id": pid,
            "caretakers": [{**people[i], "added": added.get(i)} for i in ids if i in people],
            "can_add": len(ids) < MAX_CARETAKERS,
            "needs_agreement": user["role"] != "patient"}


def link(db, user: dict, scope: Optional[list], email: str, patient_id: Optional[str] = None,
         patient_agreed: bool = False) -> dict:
    if user["role"] == "caretaker":
        raise HTTPException(status_code=403, detail="Patients and hospital staff manage caretakers")
    pid = _patient_of(user, scope, patient_id)
    if user["role"] != "patient" and not patient_agreed:
        raise HTTPException(status_code=422, detail="Confirm the patient agreed to this caretaker seeing their summary")
    email = (email or "").strip().lower()
    account = auth.users(db).find_one({"email": email})
    if not account or account.get("role") != "caretaker" or account.get("status") != "active":
        raise HTTPException(status_code=404, detail=(
            "There's no caretaker account with that email. Ask them to sign up as a caretaker first, "
            "using this email, then add them here."))
    cid = str(account["_id"])
    doc = _record(db, pid)
    if not doc:
        raise HTTPException(status_code=404, detail="Patient not found")
    ids = doc.get("caretaker_ids") or []
    if cid in ids:
        raise HTTPException(status_code=409, detail=f"{account.get('name') or email} is already linked")
    if len(ids) >= MAX_CARETAKERS:
        raise HTTPException(status_code=409, detail=f"A patient can have up to {MAX_CARETAKERS} caretakers")
    entry = {"action": "linked", "caretaker_id": cid, "by": _actor(user), "at": _now(),
             "agreement": "patient" if user["role"] == "patient" else "confirmed_by_staff"}
    db["care_actions"].update_one({"_id": doc["_id"]}, {
        "$addToSet": {"caretaker_ids": cid}, "$push": {"caretaker_history": entry}})
    return listing(db, user, scope, pid)


def unlink(db, user: dict, scope: Optional[list], caretaker_id: str, patient_id: Optional[str] = None) -> dict:
    pid = _patient_of(user, scope, patient_id)
    if user["role"] == "caretaker" and caretaker_id != str(user["_id"]):
        raise HTTPException(status_code=403, detail="You can only remove yourself")
    doc = _record(db, pid)
    result = db["care_actions"].update_one(
        {"_id": doc.get("_id"), "caretaker_ids": caretaker_id},
        {"$pull": {"caretaker_ids": caretaker_id},
         "$push": {"caretaker_history": {"action": "removed", "caretaker_id": caretaker_id,
                                         "by": _actor(user), "at": _now()}}})
    if result.modified_count == 0:
        raise HTTPException(status_code=404, detail="That caretaker isn't linked to this patient")
    if user["role"] == "caretaker":
        return {"removed": True}
    return listing(db, user, scope, pid)

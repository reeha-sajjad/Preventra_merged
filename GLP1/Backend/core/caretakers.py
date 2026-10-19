"""
Caretakers: the family members or home carers who help a patient, linked to
that patient's record so they can see the patient's plain-words home page
(core/patient_home.py) and help with their care. Mirrors
Readmissions/api/caretakers.py.

Who links, and who can see the list (a patient's caretakers are on their
ownership record, patient_access.caretaker_ids):

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
patient_access.caretaker_history.
"""
from datetime import datetime, timezone
from typing import Optional

from bson import ObjectId
from bson.errors import InvalidId
from fastapi import HTTPException

from core.access import ASSIGN_ROLES, require_patient
from core.mongo import get_db, get_shared_identity_db

MAX_CARETAKERS = 10


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


async def _people(ids) -> dict:
    return {str(u["_id"]): {"id": str(u["_id"]), "name": u.get("name") or u.get("email", ""),
                            "email": u.get("email", "")}
            for u in await get_shared_identity_db().users.find(
                {"_id": {"$in": _oids(ids)}}, {"name": 1, "email": 1}).to_list(length=None)}


async def _actor(user: dict) -> dict:
    account = None
    try:
        account = await get_shared_identity_db().users.find_one({"_id": ObjectId(user["id"])}, {"name": 1})
    except (InvalidId, TypeError):
        pass
    return {"id": user["id"], "role": user["role"],
            "name": (account or {}).get("name") or ("The patient" if user["role"] == "patient" else user.get("email", ""))}


def _patient_of(user: dict, scope: Optional[list], patient_idx: Optional[int]) -> int:
    """Whose caretakers: a patient's own record; for staff, the patient named
    (within their scope - 404 otherwise, as for patients)."""
    if user["role"] == "patient":
        if not scope:
            raise HTTPException(status_code=404, detail="No record is linked to your account yet")
        return int(scope[0])
    if user["role"] in ASSIGN_ROLES or user["role"] == "caretaker":
        if patient_idx is None:
            raise HTTPException(status_code=422, detail="Name the patient")
        require_patient(scope, patient_idx)
        return int(patient_idx)
    raise HTTPException(status_code=403, detail="Patients and hospital staff manage caretakers")


async def listing(user: dict, scope: Optional[list], patient_idx: Optional[int] = None) -> dict:
    if user["role"] == "caretaker":
        raise HTTPException(status_code=403, detail="Patients and hospital staff manage caretakers")
    idx = _patient_of(user, scope, patient_idx)
    doc = await get_db().patient_access.find_one({"patient_idx": idx}, {"caretaker_ids": 1, "caretaker_history": 1})
    ids = (doc or {}).get("caretaker_ids") or []
    people = await _people(ids)
    added = {}
    for h in (doc or {}).get("caretaker_history") or []:
        if h["action"] == "linked":
            added[h["caretaker_id"]] = {"by": h["by"], "at": h["at"].isoformat() if hasattr(h["at"], "isoformat") else h["at"]}
    return {"patient_idx": idx,
            "caretakers": [{**people[i], "added": added.get(i)} for i in ids if i in people],
            "can_add": len(ids) < MAX_CARETAKERS,
            "needs_agreement": user["role"] != "patient"}


async def link(user: dict, scope: Optional[list], email: str, patient_idx: Optional[int] = None,
               patient_agreed: bool = False) -> dict:
    if user["role"] == "caretaker":
        raise HTTPException(status_code=403, detail="Patients and hospital staff manage caretakers")
    idx = _patient_of(user, scope, patient_idx)
    if user["role"] != "patient" and not patient_agreed:
        raise HTTPException(status_code=422, detail="Confirm the patient agreed to this caretaker seeing their summary")
    email = (email or "").strip().lower()
    account = await get_shared_identity_db().users.find_one({"email": email})
    if not account or account.get("role") != "caretaker" or account.get("status") != "active":
        raise HTTPException(status_code=404, detail=(
            "There's no caretaker account with that email. Ask them to sign up as a caretaker first, "
            "using this email, then add them here."))
    cid = str(account["_id"])
    doc = await get_db().patient_access.find_one({"patient_idx": idx}, {"caretaker_ids": 1}) or {}
    ids = doc.get("caretaker_ids") or []
    if cid in ids:
        raise HTTPException(status_code=409, detail=f"{account.get('name') or email} is already linked")
    if len(ids) >= MAX_CARETAKERS:
        raise HTTPException(status_code=409, detail=f"A patient can have up to {MAX_CARETAKERS} caretakers")
    entry = {"action": "linked", "caretaker_id": cid, "by": await _actor(user), "at": _now(),
             "agreement": "patient" if user["role"] == "patient" else "confirmed_by_staff"}
    await get_db().patient_access.update_one({"patient_idx": idx}, {
        "$addToSet": {"caretaker_ids": cid}, "$push": {"caretaker_history": entry}})
    return await listing(user, scope, idx)


async def unlink(user: dict, scope: Optional[list], caretaker_id: str, patient_idx: Optional[int] = None) -> dict:
    idx = _patient_of(user, scope, patient_idx)
    if user["role"] == "caretaker" and caretaker_id != user["id"]:
        raise HTTPException(status_code=403, detail="You can only remove yourself")
    result = await get_db().patient_access.update_one(
        {"patient_idx": idx, "caretaker_ids": caretaker_id},
        {"$pull": {"caretaker_ids": caretaker_id},
         "$push": {"caretaker_history": {"action": "removed", "caretaker_id": caretaker_id,
                                         "by": await _actor(user), "at": _now()}}})
    if result.modified_count == 0:
        raise HTTPException(status_code=404, detail="That caretaker isn't linked to this patient")
    if user["role"] == "caretaker":
        return {"removed": True}
    return await listing(user, scope, idx)
"""
Insurance member IDs: the ID printed on a patient's insurance card, which the
hospital bills with and the insurer knows them by. The twin of GLP-1's
GLP1/Backend/core/member_ids.py - same rules, same words.

There is no national format. HIPAA's health plan identifier was rescinded in
2019 and a national patient identifier has never been funded, so each insurer
issues its own member IDs. What makes one unique is the pair: insurer + member
ID. The database refuses the same member ID twice for one insurer.

Original Medicare is the exception with a fixed format - the Medicare
Beneficiary Identifier (MBI): 11 characters, no dashes stored, and never the
letters S, L, O, I, B or Z. An insurer whose record says member_id_format =
"mbi" has its IDs checked against it.

A member ID is one of HIPAA's 18 identifiers (45 CFR 164.514(b)(2)(i)(I)), so:

    superadmin, hospital_admin,   see it, set it, find a patient by it
    case_manager
    insurer                       sees it for its own members, finds by it
    patient                       sees their own
    everyone else                 not at all

Stored on the patient's ownership record (care_actions.insurer_member_id), with
every change in insurer_member_id_history. Each app keeps its own copy, as it
does the insurer itself.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Optional

from fastapi import HTTPException

from api import access, auth

SEE_ROLES = ("superadmin", "hospital_admin", "case_manager", "insurer", "patient")
EDIT_ROLES = ("superadmin", "hospital_admin", "case_manager")
FIND_ROLES = ("superadmin", "hospital_admin", "case_manager", "insurer")

MBI_LETTERS = "ACDEFGHJKMNPQRTUVWXY"                        # A-Z without S L O I B Z
_L, _A = f"[{MBI_LETTERS}]", f"[0-9{MBI_LETTERS}]"
MBI = re.compile(f"^[1-9]{_L}{_A}[0-9]{_L}{_A}[0-9]{_L}{_L}[0-9][0-9]$")
GENERIC = re.compile(r"^[A-Z0-9]{3,20}$")

_indexed: set = set()


def normalise(raw: Optional[str]) -> str:
    return re.sub(r"[\s\-]", "", (raw or "")).upper()


def _ensure_index(db) -> None:
    if id(db) in _indexed:
        return
    try:
        db["care_actions"].create_index(
            [("insurer_id", 1), ("insurer_member_id", 1)], unique=True, name="insurer_member_unique",
            partialFilterExpression={"insurer_member_id": {"$type": "string"}})
    except Exception:
        pass
    _indexed.add(id(db))


def _insurer(db, insurer_id: Optional[str]) -> Optional[dict]:
    if not insurer_id:
        return None
    doc = db.client[auth.IDENTITY_DB]["insurers"].find_one({"_id": insurer_id})
    return {"id": insurer_id, "name": (doc or {}).get("name") or insurer_id,
            "format": (doc or {}).get("member_id_format")}


def check(member_id: str, fmt: Optional[str]) -> None:
    if fmt == "mbi":
        if not MBI.match(member_id):
            raise HTTPException(status_code=422, detail=(
                "That isn't a valid Medicare number (MBI): 11 characters, like 1EG4TE5MK73. "
                "Dashes are fine; the letters S, L, O, I, B and Z are never used."))
    elif not GENERIC.match(member_id):
        raise HTTPException(status_code=422, detail="A member ID is 3 to 20 letters and numbers, as on the card")


def _record(db, patient_id: str) -> dict:
    return db["care_actions"].find_one({"patient_id": {"$in": access.id_variants([patient_id])}},
                                       {"patient_id": 1, "insurer_id": 1, "insurer_member_id": 1}) or {}


def insurance(db, user: dict, scope: Optional[list], patient_id: str) -> dict:
    # 404 first for a patient outside the caller's scope, as on every route.
    access.require_patient(scope, str(patient_id))
    if user["role"] not in SEE_ROLES:
        raise HTTPException(status_code=403, detail="Member IDs are for billing staff, the insurer and the patient")
    doc = _record(db, str(patient_id))
    ins = _insurer(db, doc.get("insurer_id"))
    return {"patient_id": str(patient_id),
            "insurer": {"id": ins["id"], "name": ins["name"]} if ins else None,
            "member_id": doc.get("insurer_member_id"),
            "format": (ins or {}).get("format"),
            "can_edit": user["role"] in EDIT_ROLES and ins is not None}


def set_member_id(db, user: dict, scope: Optional[list], patient_id: str, raw: Optional[str]) -> dict:
    access.require_patient(scope, str(patient_id))
    if user["role"] not in EDIT_ROLES:
        raise HTTPException(status_code=403, detail="Hospital admins and case managers record member IDs")
    _ensure_index(db)
    doc = _record(db, str(patient_id))
    ins = _insurer(db, doc.get("insurer_id"))
    if not doc or not ins:
        raise HTTPException(status_code=409, detail="Set the patient's insurer first")
    member_id = normalise(raw)
    if member_id:
        check(member_id, ins["format"])
        if db["care_actions"].find_one({"insurer_id": ins["id"], "insurer_member_id": member_id,
                                        "_id": {"$ne": doc["_id"]}}, {"_id": 1}):
            raise HTTPException(status_code=409, detail=f"{ins['name']} member ID {member_id} is already on another patient")
    change = {"from": doc.get("insurer_member_id"), "to": member_id or None, "insurer_id": ins["id"],
              "by": {"id": str(user["_id"]), "role": user["role"]}, "at": datetime.now(timezone.utc)}
    update = {"$set": {"insurer_member_id": member_id}} if member_id else {"$unset": {"insurer_member_id": ""}}
    try:
        db["care_actions"].update_one({"_id": doc["_id"]}, {**update, "$push": {"insurer_member_id_history": change}})
    except Exception as exc:
        if "duplicate" in str(exc).lower() or "E11000" in str(exc):
            raise HTTPException(status_code=409, detail=f"{ins['name']} member ID {member_id} is already on another patient")
        raise
    return insurance(db, user, scope, patient_id)


def find(db, user: dict, scope: Optional[list], raw: str, insurer_id: Optional[str] = None) -> dict:
    if user["role"] not in FIND_ROLES:
        raise HTTPException(status_code=403, detail="Finding by member ID is for billing staff and insurers")
    member_id = normalise(raw)
    if not member_id:
        raise HTTPException(status_code=422, detail="Enter a member ID")
    query: dict = {"insurer_member_id": member_id}
    if user["role"] == "insurer":
        query["insurer_id"] = user.get("insurer_id") or "__none__"
    elif insurer_id:
        query["insurer_id"] = insurer_id
    if scope is not None:
        query["patient_id"] = {"$in": access.id_variants(list(scope))}
    docs = list(db["care_actions"].find(query, {"_id": 0, "patient_id": 1, "insurer_id": 1}).limit(5))
    if not docs:
        raise HTTPException(status_code=404, detail=f"No patient of yours has member ID {member_id}")
    names = {i["_id"]: i.get("name", i["_id"]) for i in db.client[auth.IDENTITY_DB]["insurers"].find(
        {"_id": {"$in": [d.get("insurer_id") for d in docs]}})}
    return {"member_id": member_id,
            "matches": [{"patient_id": str(d["patient_id"]), "insurer": names.get(d.get("insurer_id"), d.get("insurer_id"))}
                        for d in docs]}
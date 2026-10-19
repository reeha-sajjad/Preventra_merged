"""
Insurance member IDs: the ID printed on a patient's insurance card, which the
hospital bills with and the insurer knows them by. Mirrors
Readmissions/api/member_ids.py.

There is no national format. HIPAA's health plan identifier was rescinded in
2019 and a national patient identifier has never been funded, so each insurer
issues its own member IDs. What makes one unique is the pair: insurer + member
ID. The database refuses the same member ID twice for one insurer.

Original Medicare is the exception with a fixed format - the Medicare
Beneficiary Identifier (MBI): 11 characters, no dashes stored, and never the
letters S, L, O, I, B or Z (CMS, "Understanding the MBI"). An insurer whose
record says member_id_format = "mbi" has its IDs checked against it. Medicare
Advantage plans issue their own IDs, so they are not checked.

A member ID is one of HIPAA's 18 identifiers (45 CFR 164.514(b)(2)(i)(I),
"health plan beneficiary numbers"), so it goes only to those who need it:

    superadmin, hospital_admin,   see it, set it, find a patient by it -
    case_manager                  billing and coordination
    insurer                       sees it for its own members, finds by it
    patient                       sees their own
    everyone else                 not at all (doctors, nurses, pharmacies,
                                  caretakers)

Stored on the patient's ownership record (patient_access.insurer_member_id),
with every change in insurer_member_id_history. Never in Talha's dataset.
"""
import re
from datetime import datetime, timezone
from typing import Optional

from fastapi import HTTPException

from core.access import require_patient
from core.mongo import get_db, get_shared_identity_db

SEE_ROLES = ("superadmin", "hospital_admin", "case_manager", "insurer", "patient")
EDIT_ROLES = ("superadmin", "hospital_admin", "case_manager")
FIND_ROLES = ("superadmin", "hospital_admin", "case_manager", "insurer")

MBI_LETTERS = "ACDEFGHJKMNPQRTUVWXY"                        # A-Z without S L O I B Z
_L, _A = f"[{MBI_LETTERS}]", f"[0-9{MBI_LETTERS}]"
MBI = re.compile(f"^[1-9]{_L}{_A}[0-9]{_L}{_A}[0-9]{_L}{_L}[0-9][0-9]$")
GENERIC = re.compile(r"^[A-Z0-9]{3,20}$")

_indexed: set = set()


def normalise(raw: Optional[str]) -> str:
    """As stored: capitals, no spaces or dashes (cards print them for reading)."""
    return re.sub(r"[\s\-]", "", (raw or "")).upper()


async def _ensure_index() -> None:
    db = get_db()
    if id(db) in _indexed:
        return
    try:
        await db.patient_access.create_index(
            [("insurer_id", 1), ("insurer_member_id", 1)], unique=True, name="insurer_member_unique",
            partialFilterExpression={"insurer_member_id": {"$type": "string"}})
    except Exception:                                   # an older server, or a test double
        pass
    _indexed.add(id(db))


async def _insurer(insurer_id: Optional[str]) -> Optional[dict]:
    if not insurer_id:
        return None
    doc = await get_shared_identity_db().insurers.find_one({"_id": insurer_id})
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


async def insurance(user: dict, scope: Optional[list], patient_idx: int) -> dict:
    """The patient's insurer and member ID, for those allowed to see it."""
    # 404 first for a patient outside the caller's scope, as on every route.
    require_patient(scope, patient_idx)
    if user["role"] not in SEE_ROLES:
        raise HTTPException(status_code=403, detail="Member IDs are for billing staff, the insurer and the patient")
    doc = await get_db().patient_access.find_one({"patient_idx": int(patient_idx)},
                                                 {"insurer_id": 1, "insurer_member_id": 1}) or {}
    ins = await _insurer(doc.get("insurer_id"))
    return {"patient_idx": int(patient_idx),
            "insurer": {"id": ins["id"], "name": ins["name"]} if ins else None,
            "member_id": doc.get("insurer_member_id"),
            "format": (ins or {}).get("format"),
            "can_edit": user["role"] in EDIT_ROLES and ins is not None}


async def set_member_id(user: dict, scope: Optional[list], patient_idx: int, raw: Optional[str]) -> dict:
    """Record (or clear, with an empty value) a patient's member ID."""
    require_patient(scope, patient_idx)
    if user["role"] not in EDIT_ROLES:
        raise HTTPException(status_code=403, detail="Hospital admins and case managers record member IDs")
    await _ensure_index()
    col = get_db().patient_access
    doc = await col.find_one({"patient_idx": int(patient_idx)}, {"insurer_id": 1, "insurer_member_id": 1}) or {}
    ins = await _insurer(doc.get("insurer_id"))
    if not ins:
        raise HTTPException(status_code=409, detail="Set the patient's insurer first")
    member_id = normalise(raw)
    if member_id:
        check(member_id, ins["format"])
        clash = await col.find_one({"insurer_id": ins["id"], "insurer_member_id": member_id,
                                    "patient_idx": {"$ne": int(patient_idx)}}, {"_id": 1})
        if clash:
            raise HTTPException(status_code=409, detail=f"{ins['name']} member ID {member_id} is already on another patient")
    change = {"from": doc.get("insurer_member_id"), "to": member_id or None, "insurer_id": ins["id"],
              "by": {"id": user["id"], "role": user["role"]}, "at": datetime.now(timezone.utc)}
    update = ({"$set": {"insurer_member_id": member_id}} if member_id else {"$unset": {"insurer_member_id": ""}})
    try:
        await col.update_one({"patient_idx": int(patient_idx)},
                             {**update, "$push": {"insurer_member_id_history": change}})
    except Exception as exc:                            # the unique index, if two saves raced
        if "duplicate" in str(exc).lower() or "E11000" in str(exc):
            raise HTTPException(status_code=409, detail=f"{ins['name']} member ID {member_id} is already on another patient")
        raise
    return await insurance(user, scope, patient_idx)


async def find(user: dict, scope: Optional[list], raw: str, insurer_id: Optional[str] = None) -> dict:
    """The patient with this member ID, among the caller's patients. An insurer
    searches its own members; hospital staff can narrow by insurer."""
    if user["role"] not in FIND_ROLES:
        raise HTTPException(status_code=403, detail="Finding by member ID is for billing staff and insurers")
    member_id = normalise(raw)
    if not member_id:
        raise HTTPException(status_code=422, detail="Enter a member ID")
    query = {"insurer_member_id": member_id}
    if user["role"] == "insurer":
        query["insurer_id"] = user.get("insurer_id") or "__none__"
    elif insurer_id:
        query["insurer_id"] = insurer_id
    if scope is not None:
        query["patient_idx"] = {"$in": [int(i) for i in scope]}
    docs = await get_db().patient_access.find(query, {"_id": 0, "patient_idx": 1, "insurer_id": 1}).to_list(length=5)
    if not docs:
        raise HTTPException(status_code=404, detail=f"No patient of yours has member ID {member_id}")
    names = {i["_id"]: i.get("name", i["_id"]) for i in await get_shared_identity_db().insurers.find(
        {"_id": {"$in": [d.get("insurer_id") for d in docs]}}).to_list(length=None)}
    return {"member_id": member_id,
            "matches": [{"patient_idx": int(d["patient_idx"]), "insurer": names.get(d.get("insurer_id"), d.get("insurer_id"))}
                        for d in docs]}
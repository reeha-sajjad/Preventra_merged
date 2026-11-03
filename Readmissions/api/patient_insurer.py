"""
Who pays for a patient: setting and changing a patient's insurer.

The insurer is what puts a patient on an insurer's Members page
(api/access.patient_scope: insurer_id on the patient's care_actions record).
Until now only scripts/seed_hospital.py ever set it, so a patient added in the
app (Add patient, the CSV upload) never reached any insurer. This is the
in-app way, for the same billing staff who record member IDs
(api/member_ids.EDIT_ROLES: superadmin, hospital admin, case manager).

    insurers_for_picker   the insurers to choose from
    set_insurer           set or change one patient's insurer, optionally with
                          the member ID from the new card
    history               every change to a patient's insurer, newest first
    check_new / file_new  the same, for patients being added (api/main.py
                          _add_manual_records): checked before anything is
                          written, filed right after the patient is created

Changing the insurer is allowed - people switch plans - and:
    * the old insurer loses the patient on its next request (the scope is read
      live from care_actions);
    * the old member ID belonged to the old insurer, so it is cleared and kept
      in insurer_member_id_history;
    * every change is kept in insurer_history: from, to, who, when.

The model's "insurance" input on the add-patient form (Medicare, Medicaid,
Private...) is a risk factor, not this. This is the organisation that pays.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from fastapi import HTTPException

from api import access, auth, member_ids

SET_ROLES = member_ids.EDIT_ROLES
HISTORY_LIMIT = 20


def _insurers(db):
    return db.client[auth.IDENTITY_DB]["insurers"]


def _require_role(user: dict) -> None:
    if user["role"] not in SET_ROLES:
        raise HTTPException(status_code=403, detail="Hospital admins and case managers set a patient's insurer")


def _by(user: dict) -> dict:
    return {"id": str(user.get("_id", "")), "role": user.get("role", ""),
            "name": user.get("name") or user.get("email", "")}


def resolve(db, value: Optional[str]) -> Optional[dict]:
    """An insurer by its id, or by its name (any capitalisation)."""
    text = (value or "").strip()
    if not text:
        return None
    doc = _insurers(db).find_one({"_id": text})
    if doc is None:
        for candidate in _insurers(db).find({}, {"name": 1, "member_id_format": 1}):
            if str(candidate.get("name", "")).strip().lower() == text.lower():
                doc = candidate
                break
    if doc is None:
        return None
    return {"id": doc["_id"], "name": doc.get("name") or doc["_id"], "format": doc.get("member_id_format")}


def insurers_for_picker(db, user: dict) -> list:
    _require_role(user)
    return [{"id": d["_id"], "name": d.get("name") or d["_id"], "format": d.get("member_id_format")}
            for d in _insurers(db).find({}, {"name": 1, "member_id_format": 1}).sort("name", 1)]


def _member_id_problem(db, ins: dict, raw: Optional[str], exclude_doc_id=None) -> tuple:
    """(normalised member ID or "", error message or None)."""
    member_id = member_ids.normalise(raw)
    if not member_id:
        return "", None
    try:
        member_ids.check(member_id, ins["format"])
    except HTTPException as exc:
        return member_id, str(exc.detail)
    query = {"insurer_id": ins["id"], "insurer_member_id": member_id}
    if exclude_doc_id is not None:
        query["_id"] = {"$ne": exclude_doc_id}
    if db["care_actions"].find_one(query, {"_id": 1}):
        return member_id, f"{ins['name']} member ID {member_id} is already on another patient"
    return member_id, None


def _record(db, patient_id: str) -> dict:
    return db["care_actions"].find_one(
        {"patient_id": {"$in": access.id_variants([patient_id])}},
        {"patient_id": 1, "hospital_id": 1, "insurer_id": 1, "insurer_member_id": 1}) or {}


def set_insurer(db, user: dict, scope: Optional[list], patient_id: str, insurer: str,
                member_id: Optional[str] = None) -> dict:
    """Set or change a patient's insurer. Returns the patient's insurance card
    (api/member_ids.insurance) as it now stands."""
    access.require_patient(scope, str(patient_id))
    _require_role(user)
    doc = _record(db, str(patient_id))
    if not doc or not doc.get("hospital_id"):
        raise HTTPException(status_code=409, detail="This patient is not in any hospital yet")
    ins = resolve(db, insurer)
    if ins is None:
        raise HTTPException(status_code=422, detail="Choose an insurer from the list")

    old_insurer, old_member = doc.get("insurer_id"), doc.get("insurer_member_id")
    if ins["id"] == old_insurer:
        # Same insurer: nothing to change, except a member ID not on file yet.
        if member_ids.normalise(member_id) and not old_member:
            return member_ids.set_member_id(db, user, scope, patient_id, member_id)
        return member_ids.insurance(db, user, scope, patient_id)

    member_ids._ensure_index(db)
    new_member, problem = _member_id_problem(db, ins, member_id, exclude_doc_id=doc["_id"])
    if problem:
        raise HTTPException(status_code=409 if "already on another" in problem else 422, detail=problem)

    now = datetime.now(timezone.utc)
    by = _by(user)
    change = {"from": old_insurer, "to": ins["id"], "member_id": new_member or None,
              "previous_member_id": old_member, "by": by, "at": now}
    update: dict = {"$set": {"insurer_id": ins["id"]}, "$push": {"insurer_history": change}}
    if new_member:
        update["$set"]["insurer_member_id"] = new_member
    elif old_member:
        update["$unset"] = {"insurer_member_id": ""}
    if old_member or new_member:
        # One $push per field: the member-ID log gets the old ID leaving and
        # the new one arriving, in that order.
        entries = []
        if old_member:
            entries.append({"from": old_member, "to": None, "insurer_id": old_insurer, "by": by, "at": now,
                            "why": "insurer changed"})
        if new_member:
            entries.append({"from": None, "to": new_member, "insurer_id": ins["id"], "by": by, "at": now})
        update["$push"]["insurer_member_id_history"] = {"$each": entries}
    try:
        db["care_actions"].update_one({"_id": doc["_id"]}, update)
    except Exception as exc:
        if "duplicate" in str(exc).lower() or "E11000" in str(exc):
            raise HTTPException(status_code=409,
                                detail=f"{ins['name']} member ID {new_member} is already on another patient")
        raise
    return member_ids.insurance(db, user, scope, patient_id)


def history(db, user: dict, scope: Optional[list], patient_id: str) -> list:
    """Every change to the patient's insurer, newest first, with insurer names."""
    access.require_patient(scope, str(patient_id))
    _require_role(user)
    doc = db["care_actions"].find_one({"patient_id": {"$in": access.id_variants([str(patient_id)])}},
                                      {"insurer_history": 1}) or {}
    rows = list(reversed(doc.get("insurer_history") or []))[:HISTORY_LIMIT]
    ids = {r.get(k) for r in rows for k in ("from", "to") if r.get(k)}
    names = {d["_id"]: d.get("name") or d["_id"] for d in _insurers(db).find({"_id": {"$in": list(ids)}})}
    out = []
    for r in rows:
        at = r.get("at")
        out.append({"from": names.get(r.get("from"), r.get("from")) if r.get("from") else None,
                    "to": names.get(r.get("to"), r.get("to")),
                    "by": (r.get("by") or {}).get("name"), "by_role": (r.get("by") or {}).get("role"),
                    "at": at.isoformat() if hasattr(at, "isoformat") else at,
                    "member_id_cleared": bool(r.get("previous_member_id"))})
    return out


# ------------------------------------------------- patients being added
def check_new(db, records: list) -> dict:
    """{row number: [problems]} for the insurer fields of records about to be
    added (rows count from 1). Nothing is written."""
    problems: dict = {}
    seen: dict = {}
    for row, record in enumerate(records, start=1):
        raw_ins, raw_member = record.get("insurer"), record.get("insurer_member_id")
        errs = []
        if not (raw_ins or "").strip():
            if member_ids.normalise(raw_member):
                errs.append("a member ID needs an insurer")
        else:
            ins = resolve(db, raw_ins)
            if ins is None:
                errs.append(f"no insurer called '{str(raw_ins).strip()}'")
            else:
                member_id, problem = _member_id_problem(db, ins, raw_member)
                if problem:
                    errs.append(problem)
                elif member_id:
                    key = (ins["id"], member_id)
                    if key in seen:
                        errs.append(f"{ins['name']} member ID {member_id} is also on row {seen[key]}")
                    else:
                        seen[key] = row
        if errs:
            problems[row] = errs
    return problems


def file_new(db, user: dict, patient_id: str, record: dict) -> None:
    """Put a just-added patient with their insurer. check_new has passed."""
    ins = resolve(db, record.get("insurer"))
    if ins is None:
        return
    member_id = member_ids.normalise(record.get("insurer_member_id"))
    now, by = datetime.now(timezone.utc), _by(user)
    update: dict = {"$set": {"insurer_id": ins["id"]},
                    "$push": {"insurer_history": {"from": None, "to": ins["id"], "member_id": member_id or None,
                                                  "previous_member_id": None, "by": by, "at": now,
                                                  "via": "added"}}}
    if member_id:
        member_ids._ensure_index(db)
        update["$set"]["insurer_member_id"] = member_id
        update["$push"]["insurer_member_id_history"] = {"from": None, "to": member_id, "insurer_id": ins["id"],
                                                        "by": by, "at": now}
    db["care_actions"].update_one({"patient_id": str(patient_id)}, update)

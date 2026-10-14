"""
Medication changes: a doctor records that a patient's GLP-1 drug was switched,
or stopped - from what, to what, why, from when.

What this is NOT: a prescription, and not a change to the risk score. The
prescription lives in the hospital's own system. The risk score is the model's
(core/model.py), worked out from the patient's data; it is never overwritten by
hand. The patient's original record (`patients.assigned_molecule`, the drug the
score was calculated on) is not touched either - changes are kept beside it, in
glp1_analytics.medication_changes, so the dataset stays exactly as delivered.
The patient's page shows both: the drug the score is based on, and the drug the
patient is on now.

Who: a doctor, for a patient on their list (care team, or emergency access).
Nurses and case managers see the history; hospital admins and insurers see it
once they have opened the patient's clinical details with a reason, because the
drug is part of the clinical layer (core/access.py CLINICAL_FIELDS).

Nothing is deleted. A wrong entry is corrected by recording another change.
"""
from datetime import date, datetime, timezone
from typing import Optional

from bson import ObjectId
from bson.errors import InvalidId
from fastapi import HTTPException

from core.access import patient_hospital, require_patient
from core.mongo import get_db, get_shared_identity_db

COLLECTION = "medication_changes"
DRUGS = {
    "SEMAGLUTIDE": "Semaglutide",
    "TIRZEPATIDE": "Tirzepatide",
    "LIRAGLUTIDE": "Liraglutide",
    "DULAGLUTIDE": "Dulaglutide",
    "STOPPED":     "Stopped GLP-1 therapy",
}
REASONS = {
    "side_effects":  "Side effects",
    "not_effective": "Not working well enough",
    "cost":          "Cost or insurance coverage",
    "supply":        "Supply shortage",
    "preference":    "Patient preference",
    "other":         "Other",
}
RECORD_ROLES = ("doctor",)
MAX_NOTE = 500

SCORE_NOTE = ("The risk score comes from the model and was calculated while the patient was on "
              "{drug}. Recording a change does not alter it; the score updates when the model "
              "next scores this patient on new refill and adherence data.")


def _col():
    return get_db()[COLLECTION]


def _label(drug: Optional[str]) -> str:
    return DRUGS.get(str(drug or "").upper(), str(drug or "Unknown").title())


def _public(doc: dict) -> dict:
    at = doc["recorded_at"]
    at = at if at.tzinfo else at.replace(tzinfo=timezone.utc)
    return {"id": str(doc["_id"]), "from_drug": doc["from_drug"], "from_label": _label(doc["from_drug"]),
            "to_drug": doc["to_drug"], "to_label": _label(doc["to_drug"]),
            "reason": doc["reason"], "reason_label": REASONS[doc["reason"]], "note": doc.get("note", ""),
            "effective_date": doc["effective_date"], "recorded_by": doc["recorded_by"],
            "recorded_at": at.isoformat()}


async def _original(patient_idx: int) -> Optional[str]:
    doc = await get_db().patients.find_one({"patient_idx": int(patient_idx)},
                                           {"_id": 0, "assigned_molecule": 1})
    return str((doc or {}).get("assigned_molecule") or "").upper() or None


async def history(user: dict, patient_idx: int) -> dict:
    """Original drug, current drug, and every change, newest first. The caller
    has already passed require_detail for this patient."""
    idx = int(patient_idx)
    original = await _original(idx)
    docs = await _col().find({"patient_idx": idx}).sort([("recorded_at", -1)]).to_list(length=200)
    changes = [_public(d) for d in docs]
    current = changes[0]["to_drug"] if changes else original
    return {"patient_idx": idx, "original_drug": original, "original_label": _label(original),
            "current_drug": current, "current_label": _label(current),
            "changes": changes, "can_record": user["role"] in RECORD_ROLES,
            "drugs": [{"key": k, "label": v} for k, v in DRUGS.items()],
            "reasons": [{"key": k, "label": v} for k, v in REASONS.items()],
            "score_note": SCORE_NOTE.format(drug=_label(original))}


async def record(user: dict, scope: Optional[list], patient_idx: int, to_drug: str,
                 reason: str, note: Optional[str], effective_date: Optional[str]) -> dict:
    if user["role"] not in RECORD_ROLES:
        raise HTTPException(status_code=403, detail="Only doctors record medication changes")
    idx = int(patient_idx)
    require_patient(scope, idx)
    to_drug = (to_drug or "").upper()
    if to_drug not in DRUGS:
        raise HTTPException(status_code=422, detail="Choose a drug from the list")
    if reason not in REASONS:
        raise HTTPException(status_code=422, detail="Choose a reason from the list")
    note = (note or "").strip()
    if len(note) > MAX_NOTE:
        raise HTTPException(status_code=422, detail=f"Keep the note under {MAX_NOTE} characters")
    if reason == "other" and not note:
        raise HTTPException(status_code=422, detail="Say what the reason was")
    try:
        when = date.fromisoformat(effective_date) if effective_date else date.today()
    except ValueError:
        raise HTTPException(status_code=422, detail="The date must look like 2026-10-14")
    if when > date.today():
        raise HTTPException(status_code=422, detail="Record a change once it has happened, not in advance")

    current = (await history(user, idx))["current_drug"]
    if current and to_drug == current:
        raise HTTPException(status_code=409, detail=f"The patient is already on {_label(to_drug)}")

    account = None
    try:
        account = await get_shared_identity_db().users.find_one({"_id": ObjectId(user["id"])}, {"name": 1})
    except (InvalidId, TypeError):
        pass
    doc = {"patient_idx": idx, "hospital_id": await patient_hospital(idx),
           "from_drug": current, "to_drug": to_drug, "reason": reason, "note": note,
           "effective_date": when.isoformat(),
           "recorded_by": {"id": user["id"], "email": user.get("email", ""),
                           "name": (account or {}).get("name") or user.get("email", ""), "role": user["role"]},
           "recorded_at": datetime.now(timezone.utc)}
    doc["_id"] = (await _col().insert_one(doc)).inserted_id
    return _public(doc)
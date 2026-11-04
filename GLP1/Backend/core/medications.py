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
    "dose_change":   "Dose change (stepping up or down)",
    "side_effects":  "Side effects",
    "not_effective": "Not working well enough",
    "cost":          "Cost or insurance coverage",
    "supply":        "Supply shortage",
    "preference":    "Patient preference",
    "other":         "Other",
}
RECORD_ROLES = ("doctor",)
# The doses each drug comes in, from its label, and how often it is taken. A
# doctor picks one when recording a switch or a dose change; until then the
# dose is "not recorded" - the dataset never had one, and none is assumed.
DOSES = {
    "SEMAGLUTIDE": {"every": "weekly", "options": ["0.25 mg", "0.5 mg", "1 mg", "1.7 mg", "2 mg", "2.4 mg"]},
    "TIRZEPATIDE": {"every": "weekly", "options": ["2.5 mg", "5 mg", "7.5 mg", "10 mg", "12.5 mg", "15 mg"]},
    "DULAGLUTIDE": {"every": "weekly", "options": ["0.75 mg", "1.5 mg", "3 mg", "4.5 mg"]},
    "LIRAGLUTIDE": {"every": "daily", "options": ["0.6 mg", "1.2 mg", "1.8 mg", "2.4 mg", "3 mg"]},
}
EVERY = {"weekly": "once a week", "daily": "once a day"}


def dose_label(drug: Optional[str], dose: Optional[str]) -> Optional[str]:
    """"0.5 mg once a week" - or None when no dose is recorded."""
    spec = DOSES.get(str(drug or "").upper())
    if not dose or not spec:
        return None
    return f"{dose} {EVERY[spec['every']]}"
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
            "dose": doc.get("dose"), "dose_label": dose_label(doc["to_drug"], doc.get("dose")),
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
    # The dose of the drug the patient is on now: the latest change to that
    # drug that gave one. A switch to another drug starts with no dose.
    dose = None
    for c in changes:
        if c["to_drug"] != current:
            break
        if c.get("dose"):
            dose = c["dose"]
            break
    spec = DOSES.get(str(current or "").upper())
    return {"patient_idx": idx, "original_drug": original, "original_label": _label(original),
            "current_drug": current, "current_label": _label(current),
            "current_dose": dose, "current_dose_label": dose_label(current, dose),
            "current_every": spec["every"] if spec else None,
            "dose_options": {k: v["options"] for k, v in DOSES.items()},
            "changes": changes, "can_record": user["role"] in RECORD_ROLES,
            "drugs": [{"key": k, "label": v} for k, v in DRUGS.items()],
            "reasons": [{"key": k, "label": v} for k, v in REASONS.items()],
            "score_note": SCORE_NOTE.format(drug=_label(original))}


async def record(user: dict, scope: Optional[list], patient_idx: int, to_drug: str,
                 reason: str, note: Optional[str], effective_date: Optional[str],
                 dose: Optional[str] = None) -> dict:
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

    dose = (dose or "").strip() or None
    if dose and dose not in DOSES.get(to_drug, {}).get("options", []):
        raise HTTPException(status_code=422, detail=f"Choose a {_label(to_drug)} dose from the list")
    now_on = await history(user, idx)
    current = now_on["current_drug"]
    if current and to_drug == current:
        # The same drug again is a dose change, and needs a new dose.
        if not dose or dose == now_on["current_dose"]:
            raise HTTPException(status_code=409, detail=f"The patient is already on {_label(to_drug)}"
                                + (f" {dose}" if dose else "") + " - choose a different drug or dose")
    elif reason == "dose_change":
        raise HTTPException(status_code=422, detail="A dose change keeps the same drug")

    account = None
    try:
        account = await get_shared_identity_db().users.find_one({"_id": ObjectId(user["id"])}, {"name": 1})
    except (InvalidId, TypeError):
        pass
    doc = {"patient_idx": idx, "hospital_id": await patient_hospital(idx),
           "from_drug": current, "to_drug": to_drug, "reason": reason, "note": note, "dose": dose,
           "effective_date": when.isoformat(),
           "recorded_by": {"id": user["id"], "email": user.get("email", ""),
                           "name": (account or {}).get("name") or user.get("email", ""), "role": user["role"]},
           "recorded_at": datetime.now(timezone.utc)}
    doc["_id"] = (await _col().insert_one(doc)).inserted_id
    return _public(doc)
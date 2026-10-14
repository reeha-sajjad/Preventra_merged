"""
Emergency access ("break the glass") and medication changes - see
core/emergency.py and core/medications.py.
"""
from typing import Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from core import emergency, medications
from core.access import actor, on_care_team, require_detail, scope_of

router = APIRouter()


# ------------------------------------------------------------ emergency access
class LookupRequest(BaseModel):
    patient_idx: int


class GrantRequest(BaseModel):
    patient_idx: int
    reason: str
    note: str


@router.post("/emergency-access/lookup")
async def emergency_lookup(body: LookupRequest, user: dict = Depends(actor),
                           scope: Optional[list] = Depends(scope_of)):
    """Is this exact patient number in my hospital, and already on my list?"""
    return await emergency.lookup(user, scope, body.patient_idx)


@router.post("/emergency-access", status_code=201)
async def emergency_grant(body: GrantRequest, user: dict = Depends(actor),
                          scope: Optional[list] = Depends(scope_of)):
    """Open a patient who is not on my care team, for 24 hours, with a reason."""
    return await emergency.grant(user, scope, body.patient_idx, body.reason, body.note)


@router.get("/emergency-access/mine")
async def emergency_mine(user: dict = Depends(actor)):
    return {"grants": await emergency.mine(user)}


@router.get("/patients/{patient_idx}/emergency")
async def patient_emergency(patient_idx: int, user: dict = Depends(actor),
                            scope: Optional[list] = Depends(scope_of)):
    """The caller's emergency grant for this patient, when that - not the care
    team - is how they reach them. For the banner on the patient's page."""
    from core.access import require_patient
    require_patient(scope, patient_idx)
    grant = await emergency.for_patient(user, patient_idx)
    if grant and await on_care_team(user, patient_idx):
        grant = None
    return {"grant": grant}


# ---------------------------------------------------------- medication changes
class MedicationChange(BaseModel):
    to_drug: str
    reason: str
    note: Optional[str] = None
    effective_date: Optional[str] = None


@router.get("/patients/{patient_idx}/medications")
async def patient_medications(patient_idx: int, user: dict = Depends(actor),
                              scope: Optional[list] = Depends(scope_of)):
    await require_detail(user, scope, patient_idx)       # the drug is clinical
    return await medications.history(user, patient_idx)


@router.post("/patients/{patient_idx}/medications", status_code=201)
async def record_medication_change(patient_idx: int, body: MedicationChange,
                                   user: dict = Depends(actor),
                                   scope: Optional[list] = Depends(scope_of)):
    return await medications.record(user, scope, patient_idx, body.to_drug, body.reason,
                                    body.note, body.effective_date)
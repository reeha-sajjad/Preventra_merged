"""
Insurance member IDs - see core/member_ids.py.
"""
from typing import Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from core import member_ids
from core.access import actor, scope_of

router = APIRouter()


@router.get("/patients/{patient_idx}/insurance")
async def get_insurance(patient_idx: int, user: dict = Depends(actor), scope: Optional[list] = Depends(scope_of)):
    """The patient's insurer and member ID - billing staff, the insurer, the patient."""
    return await member_ids.insurance(user, scope, patient_idx)


class MemberIdRequest(BaseModel):
    member_id: Optional[str] = None             # empty clears it


@router.post("/patients/{patient_idx}/insurance")
async def set_insurance(patient_idx: int, body: MemberIdRequest, user: dict = Depends(actor),
                        scope: Optional[list] = Depends(scope_of)):
    return await member_ids.set_member_id(user, scope, patient_idx, body.member_id)


@router.get("/member-lookup")
async def member_lookup(member_id: str, insurer_id: Optional[str] = None, user: dict = Depends(actor),
                        scope: Optional[list] = Depends(scope_of)):
    """Find a patient by the member ID on their insurance card."""
    return await member_ids.find(user, scope, member_id, insurer_id)
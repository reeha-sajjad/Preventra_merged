"""
The hospital pages: Overview, Staff and care-team assignment - see
core/hospital.py. Overview layer only, so no reason is asked for any of it.
"""

from typing import List, Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from core import hospital
from core.access import ASSIGN_ROLES, OVERVIEW_ROLES, STAFF_ROLES, actor, require_role, scope_of

router = APIRouter()


@router.get("/me")
async def get_me(user: dict = Depends(actor), scope: Optional[list] = Depends(scope_of)):
    """Your own account, for Settings. Name, role, hospital, how many patients."""
    return await hospital.me(user, scope)


@router.get("/overview")
async def get_overview(user: dict = Depends(require_role(*OVERVIEW_ROLES)),
                       scope: Optional[list] = Depends(scope_of)):
    """How is my hospital doing? Not for doctors and nurses, who land on their
    own patient list instead."""
    return await hospital.overview(user, scope)


@router.get("/staff")
async def get_staff(user: dict = Depends(require_role(*STAFF_ROLES)),
                    scope: Optional[list] = Depends(scope_of)):
    """Who looks after whom: doctors and nurses with their patient counts."""
    return await hospital.staff(user, scope)


class CareTeamRequest(BaseModel):
    """Set a list, or add and remove people - see core/hospital.assign."""
    patient_ids: List[int]
    doctor_ids: Optional[List[str]] = None
    add_doctor_ids: Optional[List[str]] = None
    remove_doctor_ids: Optional[List[str]] = None
    nurse_ids: Optional[List[str]] = None
    add_nurse_ids: Optional[List[str]] = None
    remove_nurse_ids: Optional[List[str]] = None
    doctor_id: Optional[str] = None          # older one-doctor form


@router.post("/care-team")
async def set_care_team(req: CareTeamRequest, user: dict = Depends(require_role(*ASSIGN_ROLES)),
                        scope: Optional[list] = Depends(scope_of)):
    """Change the doctors and nurses of one or more patients, effective at once."""
    return await hospital.assign(user, scope, req.patient_ids, doctor_id=req.doctor_id,
                                 doctor_ids_=req.doctor_ids, add_doctor_ids=req.add_doctor_ids,
                                 remove_doctor_ids=req.remove_doctor_ids, nurse_ids=req.nurse_ids,
                                 add_nurse_ids=req.add_nurse_ids, remove_nurse_ids=req.remove_nurse_ids)
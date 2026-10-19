"""
A patient's own home page, and asking their case manager to get in touch -
see core/patient_home.py and core/followups.patient_request.
"""

from typing import Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from core import followups, patient_home
from core.access import actor, scope_of

router = APIRouter()


@router.get("/my-home")
async def my_home(user: dict = Depends(actor), scope: Optional[list] = Depends(scope_of)):
    """The caller's own record in plain words. Patients only."""
    return await patient_home.home(user, scope)


class AskRequest(BaseModel):
    reason: str
    note: Optional[str] = None


@router.post("/my-home/ask", status_code=201)
async def ask_case_manager(body: AskRequest, user: dict = Depends(actor),
                           scope: Optional[list] = Depends(scope_of)):
    """The patient asks their case manager to get in touch, saying why."""
    return await followups.patient_request(user, scope, body.reason, body.note)
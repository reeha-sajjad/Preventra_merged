"""
Follow-up requests - see core/followups.py.
"""

from typing import Literal, Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from core import followups
from core.access import actor, scope_of

router = APIRouter()


class FollowUpRequest(BaseModel):
    urgency: Literal["urgent", "routine"]
    note: Optional[str] = None


class NoteRequest(BaseModel):
    text: str


class DoneRequest(BaseModel):
    outcome: Literal["booked", "unreachable", "not_needed"]
    note: Optional[str] = None


@router.get("/patients/{patient_idx}/followup")
async def get_patient_followup(patient_idx: int, user: dict = Depends(actor),
                               scope: Optional[list] = Depends(scope_of)):
    """This patient's open request, and the last finished one if recent."""
    return await followups.for_patient(user, scope, patient_idx)


@router.post("/patients/{patient_idx}/followup", status_code=201)
async def request_followup(patient_idx: int, body: FollowUpRequest, user: dict = Depends(actor),
                           scope: Optional[list] = Depends(scope_of)):
    """A doctor or hospital nurse asks for a follow-up. 409 if one is open."""
    return await followups.request(user, scope, patient_idx, body.urgency, body.note)


@router.get("/followups")
async def list_followups(status: Literal["active", "done"] = "active", user: dict = Depends(actor)):
    """The hospital's follow-up requests - case managers, hospital admins."""
    return await followups.listing(user, status)


@router.post("/followups/{followup_id}/note")
async def add_followup_note(followup_id: str, body: NoteRequest, user: dict = Depends(actor),
                            scope: Optional[list] = Depends(scope_of)):
    return await followups.add_note(user, scope, followup_id, body.text)


@router.post("/followups/{followup_id}/take")
async def take_followup(followup_id: str, user: dict = Depends(actor),
                        scope: Optional[list] = Depends(scope_of)):
    """"I'll take it" - only one person can."""
    return await followups.take(user, scope, followup_id)


@router.post("/followups/{followup_id}/done")
async def finish_followup(followup_id: str, body: DoneRequest, user: dict = Depends(actor),
                          scope: Optional[list] = Depends(scope_of)):
    return await followups.complete(user, scope, followup_id, body.outcome, body.note)
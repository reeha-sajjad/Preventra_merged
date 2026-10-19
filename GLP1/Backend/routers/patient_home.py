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
async def my_home(patient: Optional[int] = None, user: dict = Depends(actor),
                  scope: Optional[list] = Depends(scope_of)):
    """The caller's own record in plain words - or, for a caretaker, that of a
    patient who linked them (`patient` picks which). Patients and caretakers."""
    return await patient_home.home(user, scope, patient)


class AskRequest(BaseModel):
    reason: str
    note: Optional[str] = None
    patient_idx: Optional[int] = None          # a caretaker: which of their people


@router.post("/my-home/ask", status_code=201)
async def ask_case_manager(body: AskRequest, user: dict = Depends(actor),
                           scope: Optional[list] = Depends(scope_of)):
    """The patient or their caretaker asks the case manager to get in touch."""
    return await followups.patient_request(user, scope, body.reason, body.note, body.patient_idx)


# -------------------------------------------------- caretakers (core/caretakers.py)
from core import caretakers  # noqa: E402


@router.get("/caretakers")
async def list_caretakers(patient: Optional[int] = None, user: dict = Depends(actor),
                          scope: Optional[list] = Depends(scope_of)):
    """A patient's caretakers: the patient's own, or a patient's in staff scope."""
    return await caretakers.listing(user, scope, patient)


class LinkCaretaker(BaseModel):
    email: str
    patient_idx: Optional[int] = None          # staff only
    patient_agreed: bool = False               # staff only: confirmed with the patient


@router.post("/caretakers", status_code=201)
async def link_caretaker(body: LinkCaretaker, user: dict = Depends(actor),
                         scope: Optional[list] = Depends(scope_of)):
    return await caretakers.link(user, scope, body.email, body.patient_idx, body.patient_agreed)


@router.delete("/caretakers/{caretaker_id}")
async def unlink_caretaker(caretaker_id: str, patient: Optional[int] = None, user: dict = Depends(actor),
                           scope: Optional[list] = Depends(scope_of)):
    return await caretakers.unlink(user, scope, caretaker_id, patient)


# --------------------------------------------------------------- Zoe (core/zoe.py)
from typing import Literal  # noqa: E402

from core import zoe  # noqa: E402


@router.get("/zoe")
async def zoe_plan(user: dict = Depends(actor), scope: Optional[list] = Depends(scope_of)):
    """The patient's own reminders: their dose schedule and appointments."""
    return await zoe.plan(user, scope)


class DoseRequest(BaseModel):
    time: str
    day: Optional[int] = None                       # 0 = Monday; weekly medicines only
    form: Optional[Literal["weekly", "daily"]] = None   # semaglutide only


@router.post("/zoe/dose")
async def zoe_set_dose(body: DoseRequest, user: dict = Depends(actor),
                       scope: Optional[list] = Depends(scope_of)):
    return await zoe.set_dose(user, scope, body.time, body.day, body.form)


@router.delete("/zoe/dose")
async def zoe_clear_dose(user: dict = Depends(actor), scope: Optional[list] = Depends(scope_of)):
    return await zoe.clear_dose(user, scope)


class AppointmentRequest(BaseModel):
    kind: str
    date: str
    time: Optional[str] = None
    note: Optional[str] = None


@router.post("/zoe/appointments", status_code=201)
async def zoe_add_appointment(body: AppointmentRequest, user: dict = Depends(actor),
                              scope: Optional[list] = Depends(scope_of)):
    return await zoe.add_appointment(user, scope, body.kind, body.date, body.time, body.note)


@router.delete("/zoe/appointments/{appointment_id}")
async def zoe_remove_appointment(appointment_id: str, user: dict = Depends(actor),
                                 scope: Optional[list] = Depends(scope_of)):
    return await zoe.remove_appointment(user, scope, appointment_id)
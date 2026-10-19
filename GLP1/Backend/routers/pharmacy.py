"""
Pharmacies: their home page and refill-problem reports - see core/pharmacy.py
and core/followups.report.
"""

from typing import Literal, Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from core import followups, pharmacy
from core.access import actor, scope_of

router = APIRouter()


@router.get("/pharmacy/home")
async def pharmacy_home(user: dict = Depends(actor), scope: Optional[list] = Depends(scope_of)):
    """The patients who fill at the caller's pharmacy, and its reports."""
    return await pharmacy.home(user, scope)


class ReportRequest(BaseModel):
    problem: str
    urgency: Literal["urgent", "routine"] = "routine"
    note: Optional[str] = None


@router.post("/patients/{patient_idx}/pharmacy-report", status_code=201)
async def pharmacy_report(patient_idx: int, body: ReportRequest, user: dict = Depends(actor),
                          scope: Optional[list] = Depends(scope_of)):
    """A pharmacy reports a refill problem; the patient's case manager gets it."""
    return await followups.report(user, scope, patient_idx, body.problem, body.urgency, body.note)


@router.post("/pharmacy/seen")
async def pharmacy_seen(user: dict = Depends(actor)):
    """The pharmacy home page is open: finished reports leave the bell."""
    return {"seen": await followups.seen_pharmacy(user)}
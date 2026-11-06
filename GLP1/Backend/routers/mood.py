"""
The mood screen - see core/mood.py. The patient answers on their own home page;
doctors, nurses and case managers read it on the patient's page. Its own
endpoints, so mood is never part of the patient record the chatbot reads.
"""

from typing import Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from core import mood
from core.access import actor, scope_of

router = APIRouter()


class MoodAnswers(BaseModel):
    answers: dict = Field(default_factory=dict)


@router.get("/my-home/mood")
async def my_mood(user: dict = Depends(actor), scope: Optional[list] = Depends(scope_of)):
    """The patient's card: whether the questions are due, and the form if so."""
    return await mood.patient_state(user, scope)


@router.post("/my-home/mood", status_code=201)
async def answer_mood(body: MoodAnswers, user: dict = Depends(actor),
                      scope: Optional[list] = Depends(scope_of)):
    """The patient answers. One set every 2 weeks (409 before that)."""
    return await mood.submit(user, scope, body.answers)


@router.get("/patients/{patient_idx}/mood")
async def patient_mood(patient_idx: int, user: dict = Depends(actor),
                       scope: Optional[list] = Depends(scope_of)):
    """A patient's screens - doctors, nurses and case managers only."""
    return await mood.for_staff(user, scope, patient_idx)

"""
The bell for doctors and hospital nurses - see core/notifications.py.
"""

from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from core import notifications
from core.access import CARE_TEAM_ROLES, actor, require_patient, require_role, scope_of

router = APIRouter()


@router.get("/notifications")
async def get_notifications(limit: int = 20,
                            user: dict = Depends(actor),
                            scope: Optional[list] = Depends(scope_of)):
    """What needs the caller's attention, most serious first. Doctors and nurses:
    their own patients. Case managers: new follow-up requests (a hospital admin
    too, if the hospital has none). Everyone else: `enabled` is false."""
    return await notifications.bell(user, scope, limit=limit)


class SeenRequest(BaseModel):
    """Dismiss some alerts, or every alert in the bell (`all`)."""
    patient_ids: Optional[List[int]] = None
    all: bool = False


@router.post("/notifications/seen")
async def mark_notifications_seen(body: SeenRequest,
                                  user: dict = Depends(require_role(*CARE_TEAM_ROLES)),
                                  scope: Optional[list] = Depends(scope_of)):
    if body.all:
        # Everything in the bell, not only the first page shown.
        ids = [i["patient_idx"] for i in await notifications.alerts(user, scope)]
    elif body.patient_ids:
        ids = body.patient_ids
        for idx in ids:
            require_patient(scope, idx)          # 404 for anyone not theirs
    else:
        raise HTTPException(status_code=422, detail="Name the patients, or send all: true")
    return {"seen": await notifications.mark_seen(user, ids)}
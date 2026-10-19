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


# --------------------------------------------------- insurer home (core/insurer.py)
from core import insurer  # noqa: E402


@router.get("/insurer/home")
async def insurer_home(user: dict = Depends(actor), scope: Optional[list] = Depends(scope_of)):
    """An insurer's members by hospital, their drug spend, and who is most at risk."""
    return await insurer.home(user, scope)


# ------------------------------------------- insurer alerts (core/insurer_alerts.py)
from typing import List  # noqa: E402

from core import insurer_alerts  # noqa: E402


class FlagRequest(BaseModel):
    kind: str
    note: str


@router.post("/patients/{patient_idx}/insurer-flags", status_code=201)
async def send_insurer_flag(patient_idx: int, body: FlagRequest, user: dict = Depends(actor),
                            scope: Optional[list] = Depends(scope_of)):
    """The hospital flags a patient to their insurer (billing question, prior auth, coverage)."""
    return await insurer_alerts.send_flag(user, scope, patient_idx, body.kind, body.note)


@router.get("/patients/{patient_idx}/insurer-flags")
async def list_patient_flags(patient_idx: int, user: dict = Depends(actor), scope: Optional[list] = Depends(scope_of)):
    return {"flags": await insurer_alerts.flags_for_patient(user, scope, patient_idx),
            "kinds": [{"key": k, "label": v} for k, v in insurer_alerts.KINDS.items()],
            "can_send": user["role"] in insurer_alerts.FLAG_ROLES}


@router.get("/insurer/flags")
async def list_insurer_flags(status: str = "open", user: dict = Depends(actor)):
    """An insurer's flags from hospitals."""
    return {"flags": await insurer_alerts.insurer_flags(user, status),
            "statuses": [{"key": k, "label": insurer_alerts.STATUSES[k]} for k in insurer_alerts.REPLY_STATUSES]}


class ReplyRequest(BaseModel):
    status: str
    note: Optional[str] = None


@router.post("/insurer/flags/{flag_id}/reply")
async def reply_insurer_flag(flag_id: str, body: ReplyRequest, user: dict = Depends(actor)):
    return await insurer_alerts.reply(user, flag_id, body.status, body.note)


class SeenRequest(BaseModel):
    patient_ids: Optional[List[int]] = None             # none: everyone


@router.post("/insurer/alerts/seen")
async def insurer_alerts_seen(body: SeenRequest, user: dict = Depends(actor), scope: Optional[list] = Depends(scope_of)):
    """'Mark all as read' on the insurer's bell: a new baseline."""
    if user["role"] != "insurer":
        from fastapi import HTTPException
        raise HTTPException(status_code=403, detail="For insurers")
    return {"seen": await insurer_alerts.mark_seen(user, scope, body.patient_ids)}
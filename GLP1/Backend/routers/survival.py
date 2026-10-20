from typing import Optional

from fastapi import APIRouter, Depends

from core import scoped
from core.access import scope_of
from core.mongo import get_db

router = APIRouter()


@router.get("/survival")
async def get_survival(scope: Optional[list] = Depends(scope_of)):
    """Kaplan-Meier curves per segment, fitted on the caller's own patients
    (core/scoped.py) - for the superadmin, every patient, which gives the same
    curves as the ones the migration stored."""
    segments = await get_db().segment_profiles.find({}, {"_id": 0, "cluster": 1, "label": 1, "color": 1}) \
        .to_list(length=None)
    return await scoped.survival(scope, segments)
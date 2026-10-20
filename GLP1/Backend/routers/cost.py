"""
Cost-Effectiveness Studio — live read from cost_effectiveness collection,
plus static benchmark constants from the notebook.
"""

from typing import Optional

from fastapi import APIRouter, Depends

from core import scoped
from core.access import require_cost_view, scope_of

router = APIRouter()

_BENCHMARKS = {
    "glp1": {
        "SEMAGLUTIDE": {"weight_loss_pct": 14.9, "hba1c_reduction": 1.6, "annual_cost": 13000},
        "TIRZEPATIDE": {"weight_loss_pct": 20.9, "hba1c_reduction": 2.1, "annual_cost": 16000},
        "LIRAGLUTIDE": {"weight_loss_pct": 8.0,  "hba1c_reduction": 1.1, "annual_cost": 7800},
        "DULAGLUTIDE": {"weight_loss_pct": 4.5,  "hba1c_reduction": 1.4, "annual_cost": 7200},
    },
    "comparators": {
        "insulin_glargine": {"weight_loss_pct": -1.5, "hba1c_reduction": 1.5, "annual_cost": 3500},
        "sglt2_inhibitor":  {"weight_loss_pct":  3.0, "hba1c_reduction": 0.8, "annual_cost": 5800},
    },
    "icer_threshold": 50000,
}


@router.get("/cost-effectiveness", dependencies=[Depends(require_cost_view)])
async def get_cost_effectiveness(scope: Optional[list] = Depends(scope_of)):
    # The caller's own patients' counts and spend (core/scoped.py).
    return {"cea": await scoped.cost_table(scope), "benchmarks": _BENCHMARKS}
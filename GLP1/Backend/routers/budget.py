"""
Budget Impact Simulator — fetches segment-level CEA from Mongo once,
then runs the same ROI math the dashboard expects.
"""

from math import ceil
from typing import Optional

from fastapi import APIRouter, Depends

from core import scoped
from core.access import require_cost_view, scope_of
from schemas.budget import BudgetRequest, BudgetResponse, SegmentImpact

router = APIRouter()


@router.post("/budget-impact", dependencies=[Depends(require_cost_view)])
async def budget_impact(req: BudgetRequest, mine: Optional[list] = Depends(scope_of)) -> BudgetResponse:
    reduction   = req.dropout_reduction_pct / 100
    scope       = req.population_scope_pct / 100
    interv_cost = req.intervention_cost_per_patient

    # The caller's own patients per segment, and their adherence (core/scoped.py).
    cea = await scoped.cost_table(mine)

    segments_out = []
    total_net = total_waste = total_interv = 0

    for seg in cea:
        i           = int(seg["cluster"])
        n_scope     = int(seg["n"] * scope)
        annual_cost = float(seg.get("annual_cost") or 0)
        adh         = float(seg.get("adherence_rate") or 0)
        dropout_rate = 1 - adh

        baseline_wasted = annual_cost * dropout_rate * n_scope
        new_dropout     = dropout_rate * (1 - reduction)
        new_wasted      = annual_cost * new_dropout * n_scope
        waste_recovered = baseline_wasted - new_wasted
        i_cost          = interv_cost * n_scope
        net_saving      = waste_recovered - i_cost

        total_net    += net_saving
        total_waste  += waste_recovered
        total_interv += i_cost

        segments_out.append(SegmentImpact(
            cluster=i,
            label=seg.get("label") or seg.get("segment") or f"Segment {i}",
            n_in_scope=n_scope,
            baseline_dropout_rate=round(dropout_rate, 4),
            new_dropout_rate=round(new_dropout, 4),
            baseline_wasted_spend=round(baseline_wasted),
            waste_recovered=round(waste_recovered),
            intervention_cost=round(i_cost),
            net_saving=round(net_saving),
            roi_positive=net_saving > 0,
        ))

    monthly_saving = total_waste / 12
    break_even = (
        ceil(total_interv / monthly_saving)
        if monthly_saving > 0 and total_net > 0
        else None
    )

    return BudgetResponse(
        total_net_saving=round(total_net),
        total_waste_recovered=round(total_waste),
        total_intervention_cost=round(total_interv),
        break_even_month=break_even,
        segments=segments_out,
    )
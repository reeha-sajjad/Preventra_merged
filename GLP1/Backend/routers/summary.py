"""
Executive Summary endpoint — live aggregation over the patients collection.

Combines:
  - patients collection ($group by cluster for adherence + counts)
  - cost_effectiveness collection (annual cost + total spend per segment)
"""

import asyncio
from typing import Optional

from fastapi import APIRouter, Depends

import core.model as model
from core.mongo import get_db
from core.access import COST_VIEW_ROLES, actor, scope_of, scope_query

router = APIRouter()


@router.get("/summary")
async def get_summary(scope: Optional[list] = Depends(scope_of), user: dict = Depends(actor)):
    db = get_db()
    mine = scope_query(scope)          # {} for the superadmin: every patient

    seg_agg, dropout_30, dropout_60, dropout_90, dropout_180, cea_docs, total_patients = await asyncio.gather(
        db.patients.aggregate([
            {"$match": mine},
            {"$group": {
                "_id": "$cluster",
                "adherence": {"$avg": "$is_adherent"},
                "n": {"$sum": 1},
            }},
            {"$sort": {"_id": 1}},
        ]).to_list(length=None),
        _dropouts_by_day(db, 30, mine),
        _dropouts_by_day(db, 60, mine),
        _dropouts_by_day(db, 90, mine),
        _dropouts_by_day(db, 180, mine),
        db.cost_effectiveness.find({}, {"_id": 0}).to_list(length=None),
        db.patients.count_documents(mine),
    )

    cea_by_cluster = {d["cluster"]: d for d in cea_docs}
    seg_by_cluster = {int(r["_id"]): r for r in seg_agg}

    overall_adherence = sum(r["adherence"] * r["n"] for r in seg_agg) / max(total_patients, 1)
    # Per-patient segment economics times the caller's OWN patient count in
    # each segment. The stored `n` is every patient; using it would give a
    # doctor their own adherence but the whole population's spend. For the
    # superadmin the two counts are the same, so the figures do not change.
    my_n = {int(r["_id"]): int(r["n"]) for r in seg_agg}
    avg_annual_cost = sum(d.get("annual_cost", 0) * my_n.get(int(d["cluster"]), 0)
                          for d in cea_docs) / max(total_patients, 1)
    wasted_spend = sum(
        (d.get("wasted_spend_per_pt") or 0) * my_n.get(int(d["cluster"]), 0)
        for d in cea_docs
    )

    adherence_by_segment = []
    for i in range(4):
        seg = seg_by_cluster.get(i)
        if seg is None:
            continue
        adherence_by_segment.append({
            "cluster":   i,
            "segment":   model.SEGMENT_SHORT[i],
            "adherence": round(float(seg["adherence"] or 0), 4),
            "n":         int(seg["n"]),
            "color":     model.SEGMENT_COLORS[i],
        })

    dropout_by_window = []
    for window, counts in [("By Day 30", dropout_30), ("By Day 60", dropout_60),
                           ("By Day 90", dropout_90), ("By Day 180", dropout_180)]:
        dropout_by_window.append({
            "window": window,
            "seg0":   counts.get(0, 0),
            "seg1":   counts.get(1, 0),
            "seg2":   counts.get(2, 0),
            "seg3":   counts.get(3, 0),
        })

    costs = user["role"] in COST_VIEW_ROLES
    return {
        "kpis": {
            "total_patients":      total_patients,
            "adherence_rate":      round(overall_adherence, 4),
            # With no patients there is no dropout; 1 - 0 would report 100%.
            "dropout_rate":        round(1 - overall_adherence, 4) if total_patients else 0.0,
            # Drug spend is the payer's: only the cost roles get it (the
            # screens hide it too, but the server is the control).
            "avg_annual_cost":     round(avg_annual_cost) if costs else None,
            "wasted_spend_annual": round(wasted_spend) if costs else None,
        },
        "adherence_by_segment": adherence_by_segment,
        "dropout_by_window":    dropout_by_window,
    }


async def _dropouts_by_day(db, day: int, mine: dict) -> dict[int, int]:
    rows = await db.patients.aggregate([
        {"$match": {"event_occurred": 1, "time_to_dropout": {"$lte": day}, **mine}},
        {"$group": {"_id": "$cluster", "n": {"$sum": 1}}},
    ]).to_list(length=None)
    return {int(r["_id"]): int(r["n"]) for r in rows}
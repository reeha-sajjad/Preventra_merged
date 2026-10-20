"""
Segment Explorer endpoints — live Mongo queries.

GET /segments        → segment_profiles collection, with the caller's own
                       counts and averages (core/scoped.py)
GET /segments/{id}   → profile + live distribution stats, the caller's patients only
"""
from typing import Optional


import numpy as np
from fastapi import APIRouter, Depends, HTTPException

from core import scoped
from core.access import scope_of, scope_query
from core.mongo import get_db

router = APIRouter()

_DIST_COLS = [("BMXBMI", "bmi"), ("RIDAGEYR", "age"), ("LBXGH", "hba1c")]


@router.get("/segments")
async def get_segments(scope: Optional[list] = Depends(scope_of)):
    db = get_db()
    docs = await db.segment_profiles.find({}, {"_id": 0}).sort("cluster", 1).to_list(length=None)
    return {"segments": await _own(docs, scope)}


async def _own(docs: list, scope: Optional[list]) -> list:
    """The stored profiles hold every patient's figures; swap in the caller's."""
    stats = await scoped.cluster_stats(scope)
    cost = {int(c["cluster"]): c for c in await scoped.cost_table(scope, stats)}
    out = []
    for d in docs:
        s = stats.get(int(d["cluster"]))
        if not s:
            out.append({**d, "n": 0})
            continue
        out.append({**d, **{k: v for k, v in s.items() if v is not None},
                    "wasted_per_pt": round((cost.get(int(d["cluster"])) or {}).get("wasted_spend_per_pt") or 0)})
    return out


@router.get("/segments/{cluster_id}")
async def get_segment(cluster_id: int, scope: Optional[list] = Depends(scope_of)):
    if cluster_id not in range(4):
        raise HTTPException(status_code=404, detail=f"Segment {cluster_id} not found. Valid: 0–3")

    db = get_db()
    profile = await db.segment_profiles.find_one({"cluster": cluster_id}, {"_id": 0})
    if profile is None:
        raise HTTPException(status_code=404, detail=f"Segment {cluster_id} data not available")

    distributions = await _get_distributions(db, cluster_id, scope)
    return {"cluster": cluster_id, "profile": (await _own([profile], scope))[0], "distributions": distributions}


async def _get_distributions(db, cluster_id: int, scope: Optional[list] = None):
    projection = {"_id": 0, **{col: 1 for col, _ in _DIST_COLS}}
    docs = await db.patients.find({"cluster": cluster_id, **scope_query(scope)}, projection).to_list(length=None)
    if not docs:
        return None

    dist = {}
    for col, key in _DIST_COLS:
        vals = np.array([d[col] for d in docs if d.get(col) is not None], dtype=float)
        if vals.size == 0:
            continue
        dist[key] = {
            "mean": round(float(vals.mean()), 2),
            "std":  round(float(vals.std(ddof=1)), 2) if vals.size > 1 else 0.0,
            "min":  round(float(vals.min()), 2),
            "p25":  round(float(np.percentile(vals, 25)), 2),
            "p75":  round(float(np.percentile(vals, 75)), 2),
            "max":  round(float(vals.max()), 2),
        }
    return dist or None
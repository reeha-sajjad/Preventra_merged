"""
The analytics pages, worked out from the caller's own patients.

The segment, cost, budget and survival tables were computed once, offline, for
every patient in the dataset. Read as they are, an insurer or a hospital would
see everyone's numbers - other insurers' members and other hospitals'
patients. So every page reads them through here instead:

    cluster_stats(scope)    per segment: how many of the caller's patients,
                            how many adherent, and the segment's averages
    cost_table(scope)       the per-segment cost table with the caller's own
                            count and adherence (so total spend and wasted
                            spend are theirs); per-patient drug cost and the
                            trial-based effect sizes stay the segment's
    survival(scope)         Kaplan-Meier curves on the caller's patients
                            (a few milliseconds for the whole dataset), with
                            the day 30/60/90/180 dropout checkpoints

scope is core.access.scope_of: None (the superadmin) means every patient, and
then the figures are the same as the stored tables.
"""
from typing import Optional

import numpy as np

from core.access import scope_query
from core.mongo import get_db

SEGMENTS = range(4)
CHECKPOINT_DAYS = (30, 60, 90, 180)
TIMELINE = list(range(0, 181, 5))

# Segment averages, as the segment_profiles collection names them.
_PROFILE_AVERAGES = {"age": "$RIDAGEYR", "bmi": "$BMXBMI", "hba1c": "$LBXGH", "oop_cost": "$avg_oop_cost",
                     "cost_pressure": "$income_cost_pressure", "bio_friction": "$bio_friction",
                     "refill_score": "$system_refill_score", "comorbidity": "$comorbidity_score"}
_ROUND = {"age": 1, "bmi": 1, "hba1c": 2, "oop_cost": 2, "cost_pressure": 1, "bio_friction": 3,
          "refill_score": 4, "comorbidity": 2}


async def cluster_stats(scope: Optional[list]) -> dict:
    """cluster -> {"n", "adherence", and the profile averages}, for the caller's patients."""
    rows = await get_db().patients.aggregate([
        {"$match": scope_query(scope)},
        {"$group": {"_id": "$cluster", "n": {"$sum": 1}, "adherence": {"$avg": "$is_adherent"},
                    **{k: {"$avg": v} for k, v in _PROFILE_AVERAGES.items()}}},
    ]).to_list(length=None)
    out = {}
    for r in rows:
        if r["_id"] is None:
            continue
        out[int(r["_id"])] = {"n": int(r["n"]), "adherence": round(float(r.get("adherence") or 0), 4),
                              **{k: (round(float(r[k]), _ROUND[k]) if r.get(k) is not None else None)
                                 for k in _PROFILE_AVERAGES}}
    return out


async def cost_table(scope: Optional[list], stats: Optional[dict] = None) -> list:
    """The cost_effectiveness rows with the caller's own n and adherence."""
    stats = stats if stats is not None else await cluster_stats(scope)
    rows = await get_db().cost_effectiveness.find({}, {"_id": 0}).sort("cluster", 1).to_list(length=None)
    out = []
    for d in rows:
        s = stats.get(int(d["cluster"])) or {"n": 0, "adherence": 0.0}
        annual = float(d.get("annual_cost") or 0)
        adherence = s["adherence"] if s["n"] else float(d.get("adherence_rate") or 0)
        out.append({**d, "n": s["n"], "adherence_rate": round(adherence, 4),
                    "wasted_spend_per_pt": round(annual * (1 - adherence), 2),
                    "total_annual_spend": round(annual * s["n"], 2)})
    return out


def _kaplan_meier(times: np.ndarray, events: np.ndarray, at: list) -> list:
    """Survival probability at each day in `at` (product-limit estimator)."""
    order = np.argsort(times, kind="stable")
    times, events = times[order], events[order]
    event_days = np.unique(times[events == 1])
    surv, s, i = {}, 1.0, 0
    n = len(times)
    for day in event_days:
        at_risk = n - np.searchsorted(times, day, side="left")
        died = int(np.sum((times == day) & (events == 1)))
        s *= 1 - died / at_risk if at_risk else 1
        surv[float(day)] = s
    keys = np.array(sorted(surv))
    vals = [surv[k] for k in keys]
    out = []
    for t in at:
        j = np.searchsorted(keys, t, side="right") - 1
        out.append(1.0 if j < 0 else vals[j])
    return out


def log_rank(groups: list) -> Optional[dict]:
    """The k-group log-rank test: do the segments' curves differ? groups is a
    list of (times, events) arrays. None when fewer than two groups have data."""
    groups = [(t, e) for t, e in groups if len(t)]
    if len(groups) < 2:
        return None
    times = np.concatenate([t for t, _ in groups])
    events = np.concatenate([e for _, e in groups])
    k = len(groups)
    o_minus_e = np.zeros(k)
    var = np.zeros((k, k))
    for day in np.unique(times[events == 1]):
        at_risk = np.array([np.sum(t >= day) for t, _ in groups], dtype=float)
        died = np.array([np.sum((t == day) & (e == 1)) for t, e in groups], dtype=float)
        n, d = at_risk.sum(), died.sum()
        if n <= 1:
            continue
        o_minus_e += died - d * at_risk / n
        frac = at_risk / n
        var += d * (n - d) / (n - 1) * (np.diag(frac) - np.outer(frac, frac))
    # Drop one group: the k-1 differences are independent.
    stat = float(o_minus_e[:-1] @ np.linalg.pinv(var[:-1, :-1]) @ o_minus_e[:-1])
    from scipy.stats import chi2                      # comes with scikit-learn
    p = float(chi2.sf(stat, k - 1))
    return {"test_statistic": round(stat, 2), "p_value": p, "significant": p < 0.05}


async def survival(scope: Optional[list], segments: list) -> dict:
    """The Survival Analysis page's data for the caller's patients. `segments`
    gives each segment's label and colour (segment_profiles)."""
    rows = await get_db().patients.find(
        {**scope_query(scope), "time_to_dropout": {"$ne": None}},
        {"_id": 0, "cluster": 1, "time_to_dropout": 1, "event_occurred": 1, "is_adherent": 1}).to_list(length=None)
    meta = {int(s["cluster"]): s for s in segments}
    curves, checkpoints, medians, groups = [], [], [], []
    for c in SEGMENTS:
        sub = [r for r in rows if r.get("cluster") == c]
        m = meta.get(c, {})
        if not sub:
            # Kept so each segment stays at its own index, as the page expects.
            curves.append({"cluster": c, "label": m.get("label"), "color": m.get("color"), "adherence": None,
                           "n": 0, "data": []})
            checkpoints.append({"cluster": c, "segment": m.get("label"), "n": 0,
                                **{f"day{d}": None for d in CHECKPOINT_DAYS}})
            medians.append(None)
            continue
        t = np.array([float(r["time_to_dropout"]) for r in sub])
        e = np.array([int(r.get("event_occurred") or 0) for r in sub])
        groups.append((t, e))
        line = _kaplan_meier(t, e, TIMELINE)
        cps = _kaplan_meier(t, e, list(CHECKPOINT_DAYS))
        adh = float(np.mean([float(r.get("is_adherent") or 0) for r in sub]))
        curves.append({"cluster": c, "label": m.get("label"), "color": m.get("color"), "adherence": round(adh, 4),
                       "n": len(sub), "data": [{"day": d, "survival": round(v, 4)} for d, v in zip(TIMELINE, line)]})
        # The checkpoints are the share who have DROPPED OUT by that day.
        checkpoints.append({"cluster": c, "segment": m.get("label"), "n": len(sub),
                            **{f"day{d}": round(1 - v, 4) for d, v in zip(CHECKPOINT_DAYS, cps)}})
        medians.append(next((p["day"] for p in curves[-1]["data"] if p["survival"] <= 0.5), 999))
    return {"curves": curves, "checkpoints": checkpoints, "median_survival": medians,
            "logrank": log_rank(groups), "n_patients": len(rows)}
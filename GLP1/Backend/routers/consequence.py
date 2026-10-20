"""
Consequence Model endpoints.

Phase 1: downstream cost (Markov-projected dropout-side spend).
Phase 2: metabolic rebound risk + per-cluster trajectory + dropout-timing sensitivity.
Phase 3: payer ROI synthesizer — combines adherence + downstream cost + drug cost.
"""

from collections import Counter, defaultdict

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from core import scoped
from core.mongo import get_db
from core.access import require_cost_view, scope_of, scope_query
from schemas.consequence import (
    DownstreamCostCluster,
    DownstreamCostResponse,
    PayerROICluster,
    PayerROIHorizon,
    PayerROIResponse,
    PayerROIYearlyPoint,
    ReboundCluster,
    ReboundRiskResponse,
    ReboundSensitivityCluster,
    ReboundSensitivityScenario,
    ReboundTrajectoryCluster,
    ReboundTrajectoryPoint,
    ReboundTrajectoryScenario,
)

_DEFAULT_INTERVENTION_COST = 500.0
_DEFAULT_ADHERENCE_UPLIFT = 0.15   # from docs/evidence/parameter_registry.csv → dropout_reduction_relative_assumed
_DISCOUNT_RATE = 0.03              # from docs/evidence/parameter_registry.csv → discount_rate_annual
_PRIMARY_HORIZONS = (1, 3, 5, 10)
_YEARLY_HORIZONS = tuple(range(1, 11))


def _annuity_factor(years: int, discount_rate: float = _DISCOUNT_RATE) -> float:
    """Discounted annuity for $1/year over `years` years (year-0 undiscounted)."""
    if years <= 0:
        return 0.0
    return sum(1.0 / ((1.0 + discount_rate) ** i) for i in range(years))

router = APIRouter(prefix="/consequence", tags=["consequence"])


_CLUSTER_LABELS = {
    0: "Low Urgency Dropout Risk",
    1: "Financial Barrier Dropout Risk",
    2: "Low Friction Strong Adherer",
    3: "Moderate Risk Moderate Adherer",
}


@router.get("/downstream-cost", response_model=DownstreamCostResponse,
            dependencies=[Depends(require_cost_view)])
async def get_downstream_cost(scope: Optional[list] = Depends(scope_of)) -> DownstreamCostResponse:
    """Aggregate the per-patient Markov projections into a per-cluster view."""
    db = get_db()
    # The caller's patients only: an insurer's view adds up its own members.
    docs = await db.progression_cost.find(scope_query(scope), {"_id": 0}).to_list(length=None)
    # No rows because the caller has no patients (a new hospital, an insurer
    # with no members yet) is an answer of zero, not a missing collection.
    if not docs and scope != []:
        raise HTTPException(
            status_code=503,
            detail="progression_cost collection is empty. Run scripts/migrate_csv_to_mongo.py.",
        )

    by_cluster_acc: dict[int, dict] = {}
    driver_counter: Counter = Counter()
    pop_total_5yr = 0.0
    pop_total_10yr = 0.0

    for d in docs:
        cluster = int(d["cluster"])
        c5 = float(d.get("expected_downstream_cost_5yr") or 0)
        c10 = float(d.get("expected_downstream_cost_10yr") or 0)
        esrd5 = float(d.get("esrd_probability_5yr") or 0)
        cv5 = float(d.get("cv_event_probability_5yr") or 0)
        share_esrd = float(d.get("cost_share_esrd_5yr") or 0)
        share_cv = float(d.get("cost_share_cv_5yr") or 0)
        share_t2d = float(d.get("cost_share_uncontrolled_t2d_5yr") or 0)

        acc = by_cluster_acc.setdefault(
            cluster,
            {"n": 0, "sum5": 0.0, "sum10": 0.0, "sum_esrd": 0.0, "sum_cv": 0.0,
             "sum_c_esrd": 0.0, "sum_c_cv": 0.0, "sum_c_t2d": 0.0},
        )
        acc["n"] += 1
        acc["sum5"] += c5
        acc["sum10"] += c10
        acc["sum_esrd"] += esrd5
        acc["sum_cv"] += cv5
        acc["sum_c_esrd"] += c5 * share_esrd
        acc["sum_c_cv"]   += c5 * share_cv
        acc["sum_c_t2d"]  += c5 * share_t2d

        pop_total_5yr += c5
        pop_total_10yr += c10

        driver = d.get("primary_cost_driver")
        if driver:
            driver_counter[driver] += 1

    total_drivers = sum(driver_counter.values()) or 1
    driver_dist = {k: v / total_drivers for k, v in driver_counter.items()}

    by_cluster = []
    for cluster, acc in sorted(by_cluster_acc.items()):
        n = acc["n"]
        by_cluster.append(
            DownstreamCostCluster(
                cluster_id=cluster,
                cluster_label=_CLUSTER_LABELS.get(cluster),
                n_patients=n,
                avg_downstream_cost_5yr=round(acc["sum5"] / n, 2),
                avg_downstream_cost_10yr=round(acc["sum10"] / n, 2),
                esrd_probability_5yr=round(acc["sum_esrd"] / n, 5),
                cv_event_probability_5yr=round(acc["sum_cv"] / n, 5),
                total_population_cost_5yr=round(acc["sum5"], 2),
                cost_by_driver_5yr={
                    "ESRD":             round(acc["sum_c_esrd"] / n, 2),
                    "CV_event":         round(acc["sum_c_cv"] / n, 2),
                    "Uncontrolled_T2D": round(acc["sum_c_t2d"] / n, 2),
                },
            )
        )

    return DownstreamCostResponse(
        by_cluster=by_cluster,
        population_total_5yr=round(pop_total_5yr, 2),
        population_total_10yr=round(pop_total_10yr, 2),
        primary_cost_driver_distribution={
            k: round(v, 4) for k, v in driver_dist.items()
        },
        n_patients_total=sum(c.n_patients for c in by_cluster),
    )


@router.get("/rebound-risk", response_model=ReboundRiskResponse,
            dependencies=[Depends(require_cost_view)])
async def get_rebound_risk(scope: Optional[list] = Depends(scope_of)) -> ReboundRiskResponse:
    """Per-cluster rebound severity + trajectory + dropout-timing sensitivity.

    Reads three Mongo collections:
        rebound_risk          per-patient projection (aggregated here)
        rebound_trajectory    cluster x scenario x month points (line chart)
        rebound_sensitivity   cluster x scenario summary (sensitivity panel)
    """
    db = get_db()
    patient_docs = await db.rebound_risk.find(scope_query(scope), {"_id": 0}).to_list(length=None)
    if not patient_docs and scope != []:            # as in get_downstream_cost
        raise HTTPException(
            status_code=503,
            detail="rebound_risk collection is empty. Run scripts/migrate_csv_to_mongo.py.",
        )

    traj_docs = await db.rebound_trajectory.find({}, {"_id": 0}).to_list(length=None)
    sens_docs = await db.rebound_sensitivity.find({}, {"_id": 0}).to_list(length=None)

    # ── aggregate per-cluster summary from per-patient rows ───────────────
    cluster_acc: dict[int, dict] = defaultdict(lambda: {
        "n": 0,
        "sum_hba1c_drop": 0.0,
        "sum_hba1c_6":    0.0,
        "sum_hba1c_12":   0.0,
        "sum_bmi_drop":   0.0,
        "sum_bmi_12":     0.0,
        "sum_severity":   0.0,
        "sum_p_t2d":      0.0,
        "n_p_t2d":        0,
        "sum_p_unc":      0.0,
        "n_p_unc":        0,
        "status":         Counter(),
    })

    for d in patient_docs:
        c = int(d["cluster"])
        a = cluster_acc[c]
        a["n"] += 1
        a["sum_hba1c_drop"] += float(d.get("hba1c_at_dropout") or 0)
        a["sum_hba1c_6"]    += float(d.get("expected_hba1c_6mo") or 0)
        a["sum_hba1c_12"]   += float(d.get("expected_hba1c_12mo") or 0)
        a["sum_bmi_drop"]   += float(d.get("bmi_at_dropout") or 0)
        a["sum_bmi_12"]     += float(d.get("expected_bmi_12mo") or 0)
        a["sum_severity"]   += float(d.get("rebound_severity_score") or 0)
        p_t2d = d.get("p_new_t2d_12mo")
        if p_t2d is not None:
            a["sum_p_t2d"] += float(p_t2d)
            a["n_p_t2d"] += 1
        p_unc = d.get("p_uncontrolled_12mo")
        if p_unc is not None:
            a["sum_p_unc"] += float(p_unc)
            a["n_p_unc"] += 1
        status = d.get("dm_status_at_dropout")
        if status:
            a["status"][status] += 1

    by_cluster = []
    for c, a in sorted(cluster_acc.items()):
        n = a["n"]
        total_status = sum(a["status"].values()) or 1
        by_cluster.append(ReboundCluster(
            cluster_id=c,
            cluster_label=_CLUSTER_LABELS.get(c),
            n_patients=n,
            avg_hba1c_at_dropout=round(a["sum_hba1c_drop"] / n, 3),
            avg_expected_hba1c_6mo=round(a["sum_hba1c_6"] / n, 3),
            avg_expected_hba1c_12mo=round(a["sum_hba1c_12"] / n, 3),
            avg_bmi_at_dropout=round(a["sum_bmi_drop"] / n, 2),
            avg_expected_bmi_12mo=round(a["sum_bmi_12"] / n, 2),
            avg_severity_score=round(a["sum_severity"] / n, 4),
            p_new_t2d_12mo_mean=round(a["sum_p_t2d"] / a["n_p_t2d"], 4) if a["n_p_t2d"] else None,
            p_uncontrolled_12mo_mean=round(a["sum_p_unc"] / a["n_p_unc"], 4) if a["n_p_unc"] else None,
            dm_status_distribution={
                k: round(v / total_status, 4) for k, v in a["status"].items()
            },
        ))

    # ── trajectory: group by (cluster, scenario) ──────────────────────────
    traj_by_cluster: dict[int, dict[str, dict]] = defaultdict(dict)
    for row in traj_docs:
        c = int(row["cluster"])
        s = str(row["scenario"])
        if s not in traj_by_cluster[c]:
            traj_by_cluster[c][s] = {
                "dropout_day": float(row["dropout_day"]),
                "points": [],
            }
        traj_by_cluster[c][s]["points"].append(ReboundTrajectoryPoint(
            month=float(row["month"]),
            avg_hba1c=float(row["avg_hba1c"]),
            avg_bmi=float(row["avg_bmi"]),
        ))
    trajectory = []
    for c in sorted(traj_by_cluster):
        scenarios = []
        for s_name in ("early", "median", "late"):
            if s_name not in traj_by_cluster[c]:
                continue
            payload = traj_by_cluster[c][s_name]
            scenarios.append(ReboundTrajectoryScenario(
                scenario=s_name,
                dropout_day=payload["dropout_day"],
                points=sorted(payload["points"], key=lambda p: p.month),
            ))
        trajectory.append(ReboundTrajectoryCluster(cluster_id=c, scenarios=scenarios))

    # ── sensitivity: group by cluster ─────────────────────────────────────
    sens_by_cluster: dict[int, list[ReboundSensitivityScenario]] = defaultdict(list)
    for row in sens_docs:
        c = int(row["cluster"])
        sens_by_cluster[c].append(ReboundSensitivityScenario(
            scenario=str(row["scenario"]),
            dropout_day=float(row["dropout_day"]),
            avg_hba1c_at_dropout=float(row["avg_hba1c_at_dropout"]),
            avg_expected_hba1c_12mo=float(row["avg_expected_hba1c_12mo"]),
            avg_severity_score=float(row["avg_severity_score"]),
            p_new_t2d_12mo_mean=row.get("p_new_t2d_12mo_mean"),
            p_uncontrolled_12mo_mean=row.get("p_uncontrolled_12mo_mean"),
        ))
    sensitivity = []
    for c in sorted(sens_by_cluster):
        ordering = {"early": 0, "median": 1, "late": 2}
        ordered = sorted(sens_by_cluster[c], key=lambda s: ordering.get(s.scenario, 9))
        sensitivity.append(ReboundSensitivityCluster(cluster_id=c, scenarios=ordered))

    # ── population-level T2D incidence: weighted across pre-DM patients ────
    pop_t2d_sum = sum(a["sum_p_t2d"] for a in cluster_acc.values())
    pop_t2d_n = sum(a["n_p_t2d"] for a in cluster_acc.values())
    pop_t2d_incidence = (pop_t2d_sum / pop_t2d_n) if pop_t2d_n > 0 else 0.0

    return ReboundRiskResponse(
        by_cluster=by_cluster,
        trajectory_by_cluster=trajectory,
        sensitivity=sensitivity,
        population_t2d_incidence_12mo=round(pop_t2d_incidence, 4),
        n_patients_total=sum(c.n_patients for c in by_cluster),
    )


@router.get("/payer-scenarios", dependencies=[Depends(require_cost_view)])
async def get_payer_scenarios() -> dict:
    """List available payer_type scenarios in the payer_roi collection."""
    db = get_db()
    scenarios = await db.payer_roi.distinct("payer_type")
    ordering = {"current": 0, "medicare_2028": 1, "post_generic": 2}
    scenarios.sort(key=lambda s: (ordering.get(s, 99), s))
    return {"scenarios": scenarios, "default": "current"}


@router.get("/payer-roi", response_model=PayerROIResponse, dependencies=[Depends(require_cost_view)])
async def get_payer_roi(
    scope: Optional[list] = Depends(scope_of),
    intervention_cost: float = Query(
        _DEFAULT_INTERVENTION_COST,
        ge=0,
        le=100_000,
        description="Per-patient intervention program cost (drives the dashboard slider).",
    ),
    payer_type: str = Query(
        "current",
        description="Pricing scenario: 'current', 'medicare_2028', 'post_generic', or any file stem present in docs/evidence/overrides/.",
    ),
    adherence_uplift: float = Query(
        _DEFAULT_ADHERENCE_UPLIFT,
        ge=0.0,
        le=0.5,
        description=(
            "Absolute increase in adherence rate the program is assumed to deliver "
            "(e.g. 0.15 = +15 percentage points). Drives the intervention-only "
            "ROI metric. Capped at 1.0 per cluster."
        ),
    ),
) -> PayerROIResponse:
    """Per-cluster ROI at 1/3/5/10-year horizons under a chosen payer_type.

    Two ROI variants are returned side-by-side:

    * **Drug economics ROI** (legacy): (α·(C_d−C_a) − drug_cost − intervention) / drug_cost.
      Answers "does GLP-1 pay off vs. no drug at all?" — naturally negative at
      WAC pricing because complication-avoidance doesn't clear the drug cost.

    * **Intervention ROI** (added): asks the adherence-program business-case
      question — "for every $1 of program spend, how much medical cost is
      avoided?" Two sub-variants:
        - medical: (Δα·(C_d−C_a) − intervention) / intervention — treats drug
          as an already-committed formulary decision. Standard payer framing.
        - net:     also subtracts Δα·D·(annuity − drop_days/365) — the extra
          drug spend a payer incurs when adherence rises. CFO-view.
    """
    db = get_db()
    docs = await db.payer_roi.find(
        {"payer_type": payer_type}, {"_id": 0}
    ).sort("cluster", 1).to_list(length=None)
    if not docs:
        # Fallback to 'current' if the requested scenario isn't populated
        docs = await db.payer_roi.find({"payer_type": "current"}, {"_id": 0}).sort("cluster", 1).to_list(length=None)
        if not docs:
            raise HTTPException(
                status_code=503,
                detail=f"payer_roi collection has no docs for payer_type='{payer_type}' (or fallback 'current'). Run scripts/migrate_csv_to_mongo.py.",
            )
    # Per-patient economics stay the segment's; the patient counts (which weight
    # the population ROI and the totals) are the caller's own (core/scoped.py).
    own = await scoped.cluster_stats(scope)
    docs = [{**d, "n_patients": (own.get(int(d["cluster"])) or {}).get("n", 0)} for d in docs]

    by_cluster: list[PayerROICluster] = []
    for d in docs:
        cluster = int(d["cluster"])
        alpha_base = float(d["adherence_probability"])
        alpha_with = min(1.0, alpha_base + adherence_uplift)
        delta_alpha = alpha_with - alpha_base
        annual_drug = float(d["avg_annual_drug_cost"])
        drop_days = float(d["avg_time_to_dropout_days"])

        horizons_out: list[PayerROIHorizon] = []
        for h in _PRIMARY_HORIZONS:
            gross = float(d.get(f"gross_benefit_{h}yr") or 0)
            drug = float(d.get(f"expected_drug_cost_{h}yr") or 0)
            net = gross - drug - intervention_cost
            roi = (net / drug) if drug > 0 else 0.0

            c_d = float(d.get(f"downstream_dropout_{h}yr") or 0)
            c_a = float(d.get(f"downstream_adherent_{h}yr") or 0)
            delta_benefit = delta_alpha * (c_d - c_a)
            delta_drug = delta_alpha * annual_drug * (_annuity_factor(h) - drop_days / 365.0)
            iroi_med = (delta_benefit - intervention_cost) / intervention_cost if intervention_cost > 0 else 0.0
            iroi_net = (delta_benefit - delta_drug - intervention_cost) / intervention_cost if intervention_cost > 0 else 0.0

            horizons_out.append(PayerROIHorizon(
                horizon_years=h,
                expected_drug_cost=round(drug, 2),
                gross_benefit=round(gross, 2),
                intervention_cost=round(intervention_cost, 2),
                net_benefit=round(net, 2),
                roi=round(roi, 4),
                delta_benefit_medical=round(delta_benefit, 2),
                delta_drug_cost=round(delta_drug, 2),
                intervention_roi_medical=round(iroi_med, 4),
                intervention_roi_net=round(iroi_net, 4),
            ))

        # Full yearly ROI series 1..10 for the trajectory chart + time-to-positive lookup.
        yearly_roi: list[tuple[int, float]] = []
        yearly_series_out: list[PayerROIYearlyPoint] = []
        for h in _YEARLY_HORIZONS:
            gross = float(d.get(f"gross_benefit_{h}yr") or 0)
            drug = float(d.get(f"expected_drug_cost_{h}yr") or 0)
            net = gross - drug - intervention_cost
            r = (net / drug) if drug > 0 else 0.0
            yearly_roi.append((h, r))

            c_d = float(d.get(f"downstream_dropout_{h}yr") or 0)
            c_a = float(d.get(f"downstream_adherent_{h}yr") or 0)
            db_med = delta_alpha * (c_d - c_a)
            dd = delta_alpha * annual_drug * (_annuity_factor(h) - drop_days / 365.0)
            r_med = (db_med - intervention_cost) / intervention_cost if intervention_cost > 0 else 0.0
            r_net = (db_med - dd - intervention_cost) / intervention_cost if intervention_cost > 0 else 0.0
            yearly_series_out.append(PayerROIYearlyPoint(
                year=h,
                roi=round(r, 4),
                intervention_roi_medical=round(r_med, 4),
                intervention_roi_net=round(r_net, 4),
            ))
        t_pos = _time_to_positive(yearly_roi)

        # intervention threshold at 5-yr = gross(5) − drug(5) (pre-intervention net)
        thresh_5 = float(d.get("gross_benefit_5yr") or 0) - float(d.get("expected_drug_cost_5yr") or 0)

        by_cluster.append(PayerROICluster(
            cluster_id=cluster,
            cluster_label=_CLUSTER_LABELS.get(cluster),
            n_patients=int(d["n_patients"]),
            adherence_probability=round(alpha_base, 4),
            adherence_with_program=round(alpha_with, 4),
            effective_adherence_uplift=round(delta_alpha, 4),
            avg_annual_drug_cost=round(annual_drug, 2),
            avg_time_to_dropout_days=round(drop_days, 2),
            horizons=horizons_out,
            yearly_roi_series=yearly_series_out,
            break_even_adherence_rate=(
                round(float(d["break_even_adherence_rate"]), 4)
                if d.get("break_even_adherence_rate") is not None
                else None
            ),
            intervention_cost_threshold_5yr=round(thresh_5, 2),
            time_to_positive_roi_years=t_pos,
        ))

    pop = {h: _population_roi_from_docs(docs, h, intervention_cost) for h in _PRIMARY_HORIZONS}
    pop_iroi_med = {
        h: _population_intervention_roi_from_docs(docs, h, intervention_cost, adherence_uplift, net_of_drug=False)
        for h in (1, 5, 10)
    }
    pop_iroi_net = {
        h: _population_intervention_roi_from_docs(docs, h, intervention_cost, adherence_uplift, net_of_drug=True)
        for h in (1, 5, 10)
    }

    return PayerROIResponse(
        by_cluster=by_cluster,
        population_roi_1yr=round(pop[1], 4),
        population_roi_3yr=round(pop[3], 4),
        population_roi_5yr=round(pop[5], 4),
        population_roi_10yr=round(pop[10], 4),
        population_intervention_roi_medical_1yr=round(pop_iroi_med[1], 4),
        population_intervention_roi_medical_5yr=round(pop_iroi_med[5], 4),
        population_intervention_roi_medical_10yr=round(pop_iroi_med[10], 4),
        population_intervention_roi_net_1yr=round(pop_iroi_net[1], 4),
        population_intervention_roi_net_5yr=round(pop_iroi_net[5], 4),
        population_intervention_roi_net_10yr=round(pop_iroi_net[10], 4),
        intervention_cost_per_patient=intervention_cost,
        adherence_uplift_applied=round(adherence_uplift, 4),
        n_patients_total=sum(c.n_patients for c in by_cluster),
    )


def _time_to_positive(yearly: list[tuple[int, float]]) -> float | None:
    """Linearly interpolate the year where ROI crosses 0."""
    if not yearly:
        return None
    if yearly[0][1] >= 0:
        return float(yearly[0][0])
    for (y1, r1), (y2, r2) in zip(yearly, yearly[1:]):
        if r1 < 0 <= r2:
            span = r2 - r1
            if span == 0:
                return float(y2)
            frac = -r1 / span
            return round(y1 + frac * (y2 - y1), 3)
    return None


def _population_roi_from_docs(docs: list[dict], horizon: int, intervention_cost: float) -> float:
    total_net = 0.0
    total_drug = 0.0
    for d in docs:
        n = int(d["n_patients"])
        gross = float(d.get(f"gross_benefit_{horizon}yr") or 0)
        drug = float(d.get(f"expected_drug_cost_{horizon}yr") or 0)
        total_net += n * (gross - drug - intervention_cost)
        total_drug += n * drug
    return (total_net / total_drug) if total_drug > 0 else 0.0


def _population_intervention_roi_from_docs(
    docs: list[dict],
    horizon: int,
    intervention_cost: float,
    adherence_uplift: float,
    net_of_drug: bool,
) -> float:
    """Patient-count-weighted intervention ROI at `horizon` years.

    Aggregates Δbenefit (and optionally Δdrug) per cluster, weights by n_patients,
    and divides by total intervention spend. If `net_of_drug=False`, returns the
    "medical savings only" variant.
    """
    if intervention_cost <= 0:
        return 0.0
    ann = _annuity_factor(horizon)
    total_net_delta = 0.0
    total_intervention_spend = 0.0
    for d in docs:
        n = int(d["n_patients"])
        alpha_base = float(d["adherence_probability"])
        delta_alpha = min(1.0, alpha_base + adherence_uplift) - alpha_base
        c_d = float(d.get(f"downstream_dropout_{horizon}yr") or 0)
        c_a = float(d.get(f"downstream_adherent_{horizon}yr") or 0)
        db_med = delta_alpha * (c_d - c_a)
        dd = 0.0
        if net_of_drug:
            annual_drug = float(d["avg_annual_drug_cost"])
            drop_days = float(d["avg_time_to_dropout_days"])
            dd = delta_alpha * annual_drug * (ann - drop_days / 365.0)
        total_net_delta += n * (db_med - dd - intervention_cost)
        total_intervention_spend += n * intervention_cost
    return total_net_delta / total_intervention_spend if total_intervention_spend > 0 else 0.0
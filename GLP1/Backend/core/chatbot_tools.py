"""
Tool registry, snapshot builder, and system prompt for the chatbot.

Each tool is a thin async wrapper around an existing FastAPI router handler.
Handlers are called directly (as Python coroutines) — no HTTP hop, no double
serialization. Pydantic responses are dumped via `.model_dump(mode="json")`
before being handed back to the LLM.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Optional

from fastapi import HTTPException
from google.genai import types

from routers import (
    budget       as budget_router,
    consequence  as consequence_router,
    cost         as cost_router,
    info         as info_router,
    patients     as patients_router,
    segments     as segments_router,
    shap         as shap_router,
    summary      as summary_router,
    survival     as survival_router,
)
from schemas.budget import BudgetRequest
from core.access import COST_VIEW_ROLES

logger = logging.getLogger("chatbot.tools")


@dataclass
class ToolContext:
    """Who is asking, and which patients they may see. Every tool runs as this
    user: the router functions it calls apply the same scope as the UI does,
    so the chatbot can never answer from patients its user cannot open."""
    user: dict
    scope: Optional[list]


# The tools behind the cost and ROI screens; refused for roles without them.
COST_TOOLS = {"get_cost_effectiveness", "simulate_budget", "get_downstream_cost",
              "get_rebound_risk", "get_payer_scenarios", "get_payer_roi"}


# ─────────────────────────── Tool handlers ────────────────────────────
#
# Each handler receives already-validated kwargs and returns a dict/list.
# They call the router functions directly to keep the code paths identical
# to what the UI hits.


async def _get_summary(ctx: ToolContext, **_: Any) -> Any:
    return await summary_router.get_summary(scope=ctx.scope, user=ctx.user)


async def _get_segments(ctx: ToolContext, **_: Any) -> Any:
    return await segments_router.get_segments()


async def _get_segment_detail(ctx: ToolContext, *, cluster_id: int) -> Any:
    return await segments_router.get_segment(int(cluster_id))


async def _search_patients(
    ctx: ToolContext,
    *,
    segment: int | None = None,
    molecule: str | None = None,
    min_risk: float | None = None,
    prediction: str | None = None,
    financial_only: bool = False,
    page: int = 0,
    page_size: int = 10,
    sort_by: str = "dropout_prob",
    sort_dir: str = "desc",
    search: str | None = None,
) -> Any:
    page_size = max(1, min(int(page_size), 50))
    result = await patients_router.get_patients(
        page=int(page),
        page_size=page_size,
        segment=segment,
        molecule=molecule,
        min_risk=min_risk,
        prediction=prediction,
        financial_only=bool(financial_only),
        sort_by=sort_by,
        sort_dir=sort_dir,
        search=search,
        # Called directly, not through FastAPI, so every parameter must be
        # given: an omitted one would be its Query(...) default object.
        doctor=None,
        nurse=None,
        unassigned=None,
        case_manager=None,
        user=ctx.user,
        scope=ctx.scope,
    )
    return result


async def _get_patient(ctx: ToolContext, *, patient_idx: int) -> Any:
    return await patients_router.get_patient(int(patient_idx), user=ctx.user, scope=ctx.scope)


async def _get_survival(ctx: ToolContext, **_: Any) -> Any:
    return survival_router.get_survival()


async def _get_cost_effectiveness(ctx: ToolContext, **_: Any) -> Any:
    return await cost_router.get_cost_effectiveness()


async def _simulate_budget(
    ctx: ToolContext,
    *,
    dropout_reduction_pct: float,
    population_scope_pct: float = 100.0,
    intervention_cost_per_patient: float = 500.0,
) -> Any:
    req = BudgetRequest(
        dropout_reduction_pct=float(dropout_reduction_pct),
        population_scope_pct=float(population_scope_pct),
        intervention_cost_per_patient=float(intervention_cost_per_patient),
    )
    return await budget_router.budget_impact(req)


async def _get_downstream_cost(ctx: ToolContext, **_: Any) -> Any:
    return await consequence_router.get_downstream_cost(scope=ctx.scope)


async def _get_rebound_risk(ctx: ToolContext, **_: Any) -> Any:
    return await consequence_router.get_rebound_risk(scope=ctx.scope)


async def _get_payer_scenarios(ctx: ToolContext, **_: Any) -> Any:
    return await consequence_router.get_payer_scenarios()


async def _get_payer_roi(
    ctx: ToolContext,
    *,
    intervention_cost: float = 500.0,
    payer_type: str = "current",
    adherence_uplift: float = 0.15,
) -> Any:
    return await consequence_router.get_payer_roi(
        intervention_cost=float(intervention_cost),
        payer_type=str(payer_type),
        adherence_uplift=float(adherence_uplift),
    )


async def _get_global_shap(ctx: ToolContext, **_: Any) -> Any:
    return shap_router.get_global_shap()


async def _get_model_info(ctx: Optional[ToolContext] = None, **_: Any) -> Any:
    return info_router.get_model_info()


TOOL_HANDLERS: dict[str, Callable[..., Awaitable[Any]]] = {
    "get_summary":             _get_summary,
    "get_segments":            _get_segments,
    "get_segment_detail":      _get_segment_detail,
    "search_patients":         _search_patients,
    "get_patient":             _get_patient,
    "get_survival":            _get_survival,
    "get_cost_effectiveness":  _get_cost_effectiveness,
    "simulate_budget":         _simulate_budget,
    "get_downstream_cost":     _get_downstream_cost,
    "get_rebound_risk":        _get_rebound_risk,
    "get_payer_scenarios":     _get_payer_scenarios,
    "get_payer_roi":           _get_payer_roi,
    "get_global_shap":         _get_global_shap,
    "get_model_info":          _get_model_info,
}


# ─────────────────────────── Tool schemas ─────────────────────────────

def _decl(name: str, description: str, properties: dict | None = None, required: list[str] | None = None) -> types.FunctionDeclaration:
    parameters = None
    if properties:
        parameters = types.Schema(
            type=types.Type.OBJECT,
            properties={k: types.Schema(**v) for k, v in properties.items()},
            required=required or [],
        )
    else:
        parameters = types.Schema(type=types.Type.OBJECT, properties={})
    return types.FunctionDeclaration(name=name, description=description, parameters=parameters)


_TOOL_DECLARATIONS: list[types.FunctionDeclaration] = [
    _decl(
        "get_summary",
        "Overall executive KPIs: total patients, adherence rate, dropout rate, average annual cost, wasted spend, adherence by segment, dropout counts by 30/60/90/180-day windows.",
    ),
    _decl(
        "get_segments",
        "List all 4 K-Means patient behavior clusters with their profiles (adherence rate, average age/BMI/HbA1c, cost pressure, comorbidity score, wasted spend per patient).",
    ),
    _decl(
        "get_segment_detail",
        "Detailed profile + BMI/age/HbA1c distributions (mean, std, quartiles) for one cluster.",
        properties={
            "cluster_id": {
                "type": types.Type.INTEGER,
                "description": "Cluster id 0-3. 0=Low Urgency Dropout Risk, 1=Financial Barrier Dropout Risk, 2=Low Friction Strong Adherer, 3=Moderate Risk Moderate Adherer.",
            },
        },
        required=["cluster_id"],
    ),
    _decl(
        "search_patients",
        "Filter/search the patient roster. Returns a paginated list plus counts of high-risk and financial-barrier patients matching the filter.",
        properties={
            "segment":        {"type": types.Type.INTEGER, "description": "Cluster id 0-3."},
            "molecule":       {"type": types.Type.STRING,  "description": "GLP-1 molecule name (e.g. SEMAGLUTIDE, TIRZEPATIDE, LIRAGLUTIDE, DULAGLUTIDE)."},
            "min_risk":       {"type": types.Type.NUMBER,  "description": "Minimum dropout probability, 0.0-1.0. Use 0.75 for high-risk only."},
            "prediction":     {"type": types.Type.STRING,  "description": "'Dropout Risk' or 'Adherent'."},
            "financial_only": {"type": types.Type.BOOLEAN, "description": "If true, only patients whose top driver is financial (out-of-pocket, income, cost)."},
            "page":           {"type": types.Type.INTEGER, "description": "Zero-based page index."},
            "page_size":      {"type": types.Type.INTEGER, "description": "Rows per page, max 50."},
        },
    ),
    _decl(
        "get_patient",
        "Full detail for a single patient by patient_idx: demographics, clinical baseline, dropout probability, top 3 SHAP drivers with directions, and segment-level survival checkpoints.",
        properties={
            "patient_idx": {"type": types.Type.INTEGER, "description": "The patient's integer index."},
        },
        required=["patient_idx"],
    ),
    _decl(
        "get_survival",
        "Kaplan-Meier survival curves (adherence probability over days) per cluster, checkpoints at day 30/60/90/180, log-rank test.",
    ),
    _decl(
        "get_cost_effectiveness",
        "Per-cluster cost-effectiveness metrics (annual cost, weight loss %, HbA1c reduction, cost per HbA1c point, wasted spend per patient) plus GLP-1/comparator benchmarks and ICER threshold.",
    ),
    _decl(
        "simulate_budget",
        "Budget-impact simulator. Given an assumed dropout reduction (%), population scope (%), and intervention cost per patient ($), returns segment-by-segment net savings, waste recovered, intervention cost, and break-even month.",
        properties={
            "dropout_reduction_pct":         {"type": types.Type.NUMBER, "description": "% reduction in dropout rate the intervention delivers, 1-100."},
            "population_scope_pct":          {"type": types.Type.NUMBER, "description": "% of patients enrolled in the intervention, 1-100. Default 100."},
            "intervention_cost_per_patient": {"type": types.Type.NUMBER, "description": "Program cost per enrolled patient in dollars. Default 500."},
        },
        required=["dropout_reduction_pct"],
    ),
    _decl(
        "get_downstream_cost",
        "Markov-projected 5-year and 10-year downstream medical cost per cluster if patients drop out (ESRD, CV event, uncontrolled T2D shares), plus population totals.",
    ),
    _decl(
        "get_rebound_risk",
        "Metabolic rebound after dropout: per-cluster HbA1c and BMI trajectories at 6/12 months, severity score, T2D incidence probability, and dropout-timing sensitivity.",
    ),
    _decl(
        "get_payer_scenarios",
        "List available payer_type scenarios for get_payer_roi (e.g. 'current', 'medicare_2028', 'post_generic').",
    ),
    _decl(
        "get_payer_roi",
        "Per-cluster payer ROI at 1/3/5/10-year horizons under a chosen payer_type. Returns both drug-economics ROI and intervention ROI (medical + net variants), break-even adherence rates, and time-to-positive ROI.",
        properties={
            "intervention_cost": {"type": types.Type.NUMBER, "description": "Per-patient intervention program cost in dollars. Default 500."},
            "payer_type":        {"type": types.Type.STRING, "description": "Pricing scenario: 'current', 'medicare_2028', 'post_generic'. Call get_payer_scenarios for the full list. Default 'current'."},
            "adherence_uplift":  {"type": types.Type.NUMBER, "description": "Absolute increase in adherence rate the program delivers, 0.0-0.5. E.g. 0.15 = +15pp. Default 0.15."},
        },
    ),
    _decl(
        "get_global_shap",
        "Population-level SHAP feature importance — the top predictors of dropout across the whole cohort, ranked.",
    ),
    _decl(
        "get_model_info",
        "Model card: name, hyperparameters, accuracy, precision, recall, F1, AUC-ROC, threshold, train/test sizes, feature count.",
    ),
]


TOOL_SET = types.Tool(function_declarations=_TOOL_DECLARATIONS)


# ─────────────────────────── Dispatch ─────────────────────────────────

def _serialize(obj: Any) -> Any:
    if hasattr(obj, "model_dump"):
        return obj.model_dump(mode="json")
    if isinstance(obj, dict):
        return obj
    return {"result": obj}


async def dispatch_tool(name: str, args: dict, ctx: ToolContext) -> tuple[Any, str | None]:
    handler = TOOL_HANDLERS.get(name)
    if handler is None:
        return {"error": f"Unknown tool '{name}'"}, f"Unknown tool '{name}'"
    if name in COST_TOOLS and ctx.user["role"] not in COST_VIEW_ROLES:
        msg = "Cost and ROI figures are available to insurers only."
        return {"error": msg}, msg
    try:
        result = await handler(ctx, **(args or {}))
        return _serialize(result), None
    except HTTPException as exc:
        # The same refusals the screens get - not found, or a hospital admin or
        # insurer who has not opened this patient with a reason yet.
        detail = exc.detail
        msg = detail.get("message", str(detail)) if isinstance(detail, dict) else str(detail)
        if isinstance(detail, dict) and detail.get("code") == "reason_required":
            msg += ". Open the patient from the Patients page and give a reason first, then ask again."
        return {"error": msg}, msg
    except Exception as exc:  # noqa: BLE001
        logger.exception("Tool '%s' raised", name)
        return {"error": f"{type(exc).__name__}: {exc}"}, str(exc)


# ─────────────────────────── Snapshot ─────────────────────────────────

_SNAPSHOT_TTL_SECONDS = 60.0
# One snapshot per account. The headline numbers are the caller's own, so a
# snapshot shared between users would hand one hospital another's figures.
_snapshot_cache: dict[str, dict[str, Any]] = {}


async def build_snapshot(ctx: ToolContext) -> str:
    """Compact Markdown of headline KPIs + segments + model — injected into the
    system prompt so the LLM can answer aggregate questions with zero tool calls.
    Cached for 60s per account to keep response latency low."""
    now = time.monotonic()
    key = f'{ctx.user["id"]}:{ctx.user.get("acting_hospital_id") or ""}'
    cached = _snapshot_cache.get(key)
    if cached and now - cached["ts"] < _SNAPSHOT_TTL_SECONDS:
        return cached["text"]

    try:
        summary, segments, model_info = await asyncio.gather(
            summary_router.get_summary(scope=ctx.scope, user=ctx.user),
            segments_router.get_segments(),
            _get_model_info(),
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("Snapshot build failed: %s", exc)
        return "(No live snapshot available — use tools to fetch data.)"

    kpis = summary.get("kpis", {})
    seg_rows = summary.get("adherence_by_segment", []) or []
    seg_profiles = {int(s.get("cluster", -1)): s for s in (segments.get("segments") or [])}

    lines: list[str] = ["## Current dashboard snapshot", ""]
    lines.append(f"- Total patients: **{kpis.get('total_patients', '?'):,}**")
    lines.append(f"- Overall adherence: **{kpis.get('adherence_rate', 0) * 100:.1f}%**")
    lines.append(f"- Overall dropout: **{kpis.get('dropout_rate', 0) * 100:.1f}%**")
    # Only the cost roles get spend in the summary (None for everyone else).
    if kpis.get("avg_annual_cost") is not None:
        lines.append(f"- Avg annual cost / patient: **${kpis['avg_annual_cost']:,}**")
        lines.append(f"- Annual wasted spend: **${kpis['wasted_spend_annual']:,}**")
    lines.append("")
    lines.append("### Segments (adherence, n, profile)")
    for row in seg_rows:
        cid = int(row.get("cluster", -1))
        profile = seg_profiles.get(cid, {})
        label = profile.get("label") or profile.get("segment") or row.get("segment") or f"Segment {cid}"
        adh = float(row.get("adherence", 0)) * 100
        n = int(row.get("n", 0))
        parts = [f"- **Cluster {cid} — {label}**: adherence {adh:.1f}%, n={n:,}"]
        if profile:
            fragments = []
            if profile.get("age") is not None:
                fragments.append(f"age {profile['age']:.0f}")
            if profile.get("bmi") is not None:
                fragments.append(f"BMI {profile['bmi']:.1f}")
            if profile.get("hba1c") is not None:
                fragments.append(f"HbA1c {profile['hba1c']:.1f}")
            if profile.get("oop_cost") is not None:
                fragments.append(f"OOP ${profile['oop_cost']:.0f}")
            if profile.get("wasted_per_pt") is not None:
                fragments.append(f"wasted/pt ${profile['wasted_per_pt']:.0f}")
            if fragments:
                parts.append("  · " + ", ".join(fragments))
        lines.append("".join(parts))

    lines.append("")
    lines.append("### Model")
    lines.append(
        f"- {model_info.get('name', 'Model')} — AUC {model_info.get('auc_roc', 0):.3f}, "
        f"accuracy {model_info.get('accuracy', 0):.3f}, "
        f"threshold {model_info.get('threshold', 0):.2f}, "
        f"{model_info.get('feature_count', 0)} features."
    )

    text = "\n".join(lines)
    _snapshot_cache[key] = {"ts": now, "text": text}
    return text


# ─────────────────────────── System prompt ────────────────────────────

_BASE_SYSTEM_PROMPT = """You are the analytics assistant embedded in the GLP-1 Adherence & Cost Intelligence Platform.

The platform predicts GLP-1 therapy adherence dropout risk across ~7,500 simulated patients and quantifies the cost and ROI of intervention programs. Users see dashboards for executive KPIs, patient risk, segment profiles, survival curves, cost-effectiveness, budget impact, downstream cost, rebound risk, and payer ROI. All patient data is simulated from public sources (NHANES, MEPS, FAERS, ClinicalTrials, CMS) — no real PHI is involved.

## Answering strategy
- The "Current dashboard snapshot" section below gives you the headline KPIs, per-segment profiles, and model performance. Prefer answering from the snapshot when it is sufficient — do not call a tool if the answer is already there.
- Call tools for anything the snapshot cannot answer: individual patients, custom budget/ROI scenarios, per-cluster deep dives, survival curves, SHAP details, downstream cost, rebound risk.
- Never fabricate patient IDs, cluster labels, or numeric values. If a tool returns an error, tell the user in plain language and suggest a next step.
- Cite numbers with units and horizon (e.g. "$12,500 per patient over 5 years", "72% adherence", "cluster 1 (Financial Barrier)").
- Keep replies concise. Prefer 1–4 short paragraphs or a compact list over long walls of text. Use bold sparingly for emphasis.

## Cluster reference
- 0 = Low Urgency Dropout Risk
- 1 = Financial Barrier Dropout Risk
- 2 = Low Friction Strong Adherer
- 3 = Moderate Risk Moderate Adherer

## Safety
- You are a decision-support assistant, not a clinician. Never give personal medical advice — if asked about a specific treatment decision for a real person, defer to a licensed provider.
- All data shown is simulated and for research/demo purposes.
"""

_ROLE_ADDENDUM = {
    "insurer": (
        "\n## Audience: payer/insurer\n"
        "Lead with dollar terms: wasted spend, ROI, break-even, cost per outcome. "
        "Frame recommendations in terms of medical-cost avoidance and intervention ROI."
    ),
    "case_manager": (
        "\n## Audience: case manager / clinician\n"
        "Lead with patient and clinical framing: risk drivers, adherence patterns, rebound risk. "
        "Include cost only when it directly affects a care decision (e.g. financial-barrier patients)."
    ),
}


async def build_system_instruction(role_context: str | None, ctx: ToolContext) -> str:
    snapshot = await build_snapshot(ctx)
    parts = [_BASE_SYSTEM_PROMPT]
    if role_context and role_context in _ROLE_ADDENDUM:
        parts.append(_ROLE_ADDENDUM[role_context])
    parts.append("\n" + snapshot)
    return "\n".join(parts)
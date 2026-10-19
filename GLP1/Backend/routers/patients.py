"""
Patient list + detail endpoints — live MongoDB queries.

Filter/sort/paginate all happen server-side via Mongo. The merged
patient documents (created by the migration script) already contain
both the clinical fields and the SHAP driver columns.
"""

import asyncio
from typing import Optional

from fastapi import APIRouter, HTTPException, Query, Depends
from pydantic import BaseModel

import core.model as model
from core import access_log, hospital
from core.mongo import get_db
from core.access import (reasons_for, ASSIGN_ROLES, DISPENSING_REASON, actor, detail_access, needs_reason, overview_only, patient_hospital,
                         redact, require_detail, require_patient, scope_of, scope_query)
from schemas.patient_views import PatientPharmacyView

router = APIRouter()

_FINANCIAL_REGEX = "financial|out-of-pocket|cost|income"
_HIGH_RISK_THRESHOLD = 0.75


def _build_match(
    segment: Optional[int],
    molecule: Optional[str],
    min_risk: Optional[float],
    prediction: Optional[str],
    financial_only: bool,
    search: Optional[str],
) -> dict:
    match: dict = {}
    if segment is not None:
        match["cluster"] = segment
    if molecule:
        match["assigned_molecule"] = molecule.upper()
    if min_risk is not None:
        match["dropout_prob"] = {"$gte": min_risk}
    if prediction:
        match["prediction"] = prediction
    if financial_only:
        match["driver_1"] = {"$regex": _FINANCIAL_REGEX, "$options": "i"}
    if search:
        clauses = [{"driver_1": {"$regex": search, "$options": "i"}}]
        try:
            clauses.append({"patient_idx": int(search)})
        except ValueError:
            pass
        match["$or"] = clauses
    return match


def _shape_patient(doc: dict) -> dict:
    cluster = int(doc.get("cluster") or 0)
    return {
        "patient_idx":          int(doc.get("patient_idx", 0)),
        "dropout_prob":         round(float(doc.get("dropout_prob") or doc.get("dropout_proba") or 0.5), 4),
        "prediction":           str(doc.get("prediction") or "Unknown"),
        "cluster":              cluster,
        "segment":              model.SEGMENT_SHORT[cluster] if cluster < 4 else "Unknown",
        "assigned_molecule":    str(doc.get("assigned_molecule") or "UNKNOWN"),
        "avg_oop_cost":         round(float(doc.get("avg_oop_cost") or 0.0), 2),
        "driver_1":             str(doc.get("driver_1") or ""),
        "driver_1_direction":   str(doc.get("driver_1_direction") or ""),
        "driver_1_shap":        doc.get("driver_1_shap"),
        "driver_2":             doc.get("driver_2"),
        "driver_2_direction":   doc.get("driver_2_direction"),
        "driver_2_shap":        doc.get("driver_2_shap"),
        "driver_3":             doc.get("driver_3"),
        "driver_3_direction":   doc.get("driver_3_direction"),
        "driver_3_shap":        doc.get("driver_3_shap"),
        "BMXBMI":               doc.get("BMXBMI"),
        "RIDAGEYR":             int(doc["RIDAGEYR"]) if doc.get("RIDAGEYR") is not None else None,
        "LBXGH":                doc.get("LBXGH"),
        "comorbidity_score":    int(doc["comorbidity_score"]) if doc.get("comorbidity_score") is not None else None,
        "bio_friction":         doc.get("bio_friction"),
        "income_cost_pressure": doc.get("income_cost_pressure"),
        "system_refill_score":  doc.get("system_refill_score"),
        "drug_generation":      int(doc["drug_generation"]) if doc.get("drug_generation") is not None else None,
        "time_to_dropout":      int(doc["time_to_dropout"]) if doc.get("time_to_dropout") is not None else None,
    }


@router.get("/patients")
async def get_patients(
    page:           int   = Query(0, ge=0),
    page_size:      int   = Query(20, ge=1, le=10000),
    segment:        Optional[int]   = Query(None),
    molecule:       Optional[str]   = Query(None),
    min_risk:       Optional[float] = Query(None, ge=0.0, le=1.0),
    prediction:     Optional[str]   = Query(None),
    financial_only: bool            = Query(False),
    sort_by:        str             = Query("dropout_prob"),
    sort_dir:       str             = Query("desc"),
    search:         Optional[str]   = Query(None),
    doctor:         Optional[str]   = Query(None, description="doctor account id"),
    nurse:          Optional[str]   = Query(None, description="nurse account id"),
    unassigned:     Optional[str]   = Query(None, description="doctor | nurse | case_manager"),
    case_manager:   Optional[str]   = Query(None, description="case manager account id, or 'me'"),
    user:           dict            = Depends(actor),
    scope:          Optional[list]  = Depends(scope_of),
):
    db = get_db()
    # A hospital admin or insurer sees the overview layer (core/access.py).
    # Filtering or sorting on the clinical fields would hand the clinical layer
    # back one row at a time, so for them those are ignored; search matches
    # the patient number only.
    redacted = needs_reason(user)
    if redacted:
        segment = molecule = None
        financial_only = False
        if search and not search.strip().isdigit():
            search = None
        if sort_by not in ("dropout_prob", "patient_idx"):
            sort_by = "dropout_prob"
    # Only the caller's patients: a top-level condition, so the counts below
    # (which extend `match`) are limited the same way as the page.
    match = {**_build_match(segment, molecule, min_risk, prediction, financial_only, search),
             **scope_query(scope)}
    if case_manager == "me":
        case_manager = user["id"]               # "My caseload"
    care = await hospital.care_filter(scope, doctor=doctor, nurse=nurse, unassigned=unassigned,
                                      case_manager=case_manager)
    if care is not None:
        match["$and"] = match.get("$and", []) + [{"patient_idx": {"$in": care}}]
    direction = -1 if sort_dir.lower() == "desc" else 1

    high_risk_filter = {**match, "dropout_prob": {"$gte": _HIGH_RISK_THRESHOLD}}
    if "dropout_prob" in match and isinstance(match["dropout_prob"], dict):
        merged = {**match["dropout_prob"], "$gte": max(match["dropout_prob"].get("$gte", 0), _HIGH_RISK_THRESHOLD)}
        high_risk_filter = {**match, "dropout_prob": merged}

    financial_filter = {**match, "driver_1": {"$regex": _FINANCIAL_REGEX, "$options": "i"}}

    total, high_risk, financial_cnt, page_docs = await asyncio.gather(
        db.patients.count_documents(match),
        db.patients.count_documents(high_risk_filter),
        db.patients.count_documents(financial_filter),
        db.patients.find(match, {"_id": 0})
                   .sort(sort_by, direction)
                   .skip(page * page_size)
                   .limit(page_size)
                   .to_list(length=page_size),
    )

    rows = [_shape_patient(d) for d in page_docs]
    # Who looks after each patient, and who pays - the overview layer, on
    # every row for every role. Pharmacy is clinical and goes with the rest.
    teams = await hospital.care_teams([r["patient_idx"] for r in rows])
    if user["role"] == "insurer":
        teams = await hospital.for_insurer(teams)
    for r in rows:
        r.update(teams.get(r["patient_idx"], {}))
    if redacted:
        rows = [redact(r) for r in rows]
    return {
        "total":     total,
        "page":      page,
        "page_size": page_size,
        "patients":  rows,
        "summary":   {"high_risk_count": high_risk, "financial_barrier_count": financial_cnt},
        "redacted":  redacted,
    }


@router.get("/patients/{patient_idx}/summary")
async def get_patient_summary(patient_idx: int, user: dict = Depends(actor),
                              scope: Optional[list] = Depends(scope_of)):
    """The overview layer of one patient, and whether the caller may open the
    clinical layer yet. What a hospital admin sees before giving a reason."""
    require_patient(scope, patient_idx)
    doc = await get_db().patients.find_one({"patient_idx": patient_idx},
                                           {"_id": 0, "patient_idx": 1, "dropout_prob": 1,
                                            "dropout_proba": 1, "prediction": 1})
    if doc is None:
        raise HTTPException(status_code=404, detail=f"Patient {patient_idx} not found")
    team = (await hospital.care_teams([patient_idx]))[patient_idx]
    state = await detail_access(user, patient_idx)
    extra = {}
    if user["role"] == "insurer":
        # Looking at a member clears "turned critical" for them from the bell.
        from core import insurer_alerts                   # avoid an import cycle
        await insurer_alerts.mark_seen(user, scope, [patient_idx])
        # The hospital and case manager, not the clinical staff; and the member ID.
        team = (await hospital.for_insurer({patient_idx: team}))[patient_idx]
    if user["role"] == "pharmacy":
        # A pharmacy: the dispensing facts it already has, and the look is logged.
        from core import pharmacy                         # avoid an import cycle
        facts = await get_db().patients.find_one({"patient_idx": patient_idx},
                                                 {"_id": 0, "assigned_molecule": 1, "system_refill_score": 1})
        extra = {"drug": pharmacy.drug_label((facts or {}).get("assigned_molecule")),
                 "refill_band": pharmacy.refill_band((facts or {}).get("system_refill_score"))}
        if not await access_log.has_opened(user, patient_idx):
            await access_log.record(user, patient_idx, await patient_hospital(patient_idx), DISPENSING_REASON)
    return {**extra, **redact({
        "patient_idx": patient_idx,
        "dropout_prob": round(float(doc.get("dropout_prob") or doc.get("dropout_proba") or 0.5), 4),
        "prediction": str(doc.get("prediction") or "Unknown"),
        **team,
        "detail_access": state,
        "reasons": ([{"key": k, "label": v} for k, v in reasons_for(user).items()]
                    if state == "reason_required" else []),
        "can_assign": user["role"] in ASSIGN_ROLES,
    })}


class OpenPatientRequest(BaseModel):
    reason: str


@router.post("/patients/{patient_idx}/open")
async def open_patient(patient_idx: int, req: OpenPatientRequest, user: dict = Depends(actor),
                       scope: Optional[list] = Depends(scope_of)):
    """A hospital admin or insurer opens one patient's clinical layer, giving a
    reason; logged once per patient per sign-in. Nobody else is asked."""
    require_patient(scope, patient_idx)
    if not needs_reason(user):
        return {"patient_idx": patient_idx, "detail_access": "open"}
    if overview_only(user):
        raise HTTPException(status_code=403, detail="This account sees the overview of a patient only")
    if req.reason not in reasons_for(user):
        raise HTTPException(status_code=422, detail="Choose one of the listed reasons")
    if not await access_log.has_opened(user, patient_idx):
        await access_log.record(user, patient_idx, await patient_hospital(patient_idx), req.reason)
    return {"patient_idx": patient_idx, "detail_access": "granted"}


@router.get("/patients/{patient_idx}")
async def get_patient(patient_idx: int, user: dict = Depends(actor),
                      scope: Optional[list] = Depends(scope_of)):
    await require_detail(user, scope, patient_idx)
    db = get_db()
    doc = await db.patients.find_one({"patient_idx": patient_idx}, {"_id": 0})
    if doc is None:
        raise HTTPException(status_code=404, detail=f"Patient {patient_idx} not found")

    team = (await hospital.care_teams([patient_idx]))[patient_idx]
    if user["role"] == "insurer":
        team = (await hospital.for_insurer({patient_idx: team}))[patient_idx]
    patient = {**_shape_patient(doc), **team}

    shap_drivers = None
    if doc.get("driver_1") is not None:
        shap_drivers = []
        for rank, (feat_col, dir_col, shap_col) in enumerate([
            ("driver_1", "driver_1_direction", "driver_1_shap"),
            ("driver_2", "driver_2_direction", "driver_2_shap"),
            ("driver_3", "driver_3_direction", "driver_3_shap"),
        ], start=1):
            feat = doc.get(feat_col)
            if feat is None:
                break
            shap_drivers.append({
                "rank":       rank,
                "feature":    str(feat),
                "direction":  str(doc.get(dir_col) or ""),
                "shap_value": round(float(doc.get(shap_col) or 0.0), 4),
            })

    cluster  = int(doc.get("cluster") or 0)
    checkpts = (model.survival_cache or {}).get("checkpoints", [])
    seg_surv = next((c for c in checkpts if c.get("cluster") == cluster), None)

    return {
        "patient":          patient,
        "shap_drivers":     shap_drivers,
        "segment_survival": seg_surv,
    }
    
@router.get("/patients/{patient_idx}/pharmacy-view", response_model=PatientPharmacyView)
async def get_patient_pharmacy_view(patient_idx: int, user: dict = Depends(actor),
                                    scope: Optional[list] = Depends(scope_of)):
    await require_detail(user, scope, patient_idx)
    db = get_db()
    doc = await db.patients.find_one({"patient_idx": patient_idx}, {"_id": 0})
    if doc is None:
        raise HTTPException(status_code=404, detail=f"Patient {patient_idx} not found")

    return PatientPharmacyView(
        patient_idx=int(doc.get("patient_idx", 0)),
        assigned_molecule=str(doc.get("assigned_molecule") or "UNKNOWN"),
        drug_generation=doc.get("drug_generation"),
        system_refill_score=doc.get("system_refill_score"),
    )
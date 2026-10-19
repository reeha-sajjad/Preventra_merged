"""
An insurer's home page in Readmissions: its members across hospitals, the
readmission cost they carry, and who is most likely to come back - so the
payer's own care management can step in. GLP-1's twin is
GLP1/Backend/core/insurer.py (dropout and drug spend).

Only the insurer's own members (api/access.patient_scope: insurer_id on the
ownership record), and only the overview layer: risk, hospital, member ID -
never diagnoses or risk drivers, which still need a reason, one patient at a
time.

The money uses the same figures as the patient page's ROI panel
(api/roi_model.py), so they agree:
    expected readmission cost   each member's current risk x the cost of a
                                readmission (READMISSION_COST_TOTAL)
    avoidable with follow-up    that x INTERVENTION_EFFECTIVENESS
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Optional

from fastapi import HTTPException

from api import access, auth
from api.db_utils import get_latest_batch_date
from api.roi_model import INTERVENTION_EFFECTIVENESS, READMISSION_COST_TOTAL

TOP_MEMBERS = 25


def _score(row: dict) -> float:
    try:
        return float(row.get("current_score", row.get("risk_score", 0)) or 0)
    except (TypeError, ValueError):
        return 0.0


def home(db, user: dict, scope: Optional[list]) -> dict:
    if user["role"] != "insurer":
        raise HTTPException(status_code=403, detail="This page is for insurers")
    ids = [str(p) for p in (scope or [])]
    batch = get_latest_batch_date(db, "patient_worklist")
    rows = list(db["patient_worklist"].find(
        {"patient_id": {"$in": access.id_variants(ids)}, "batch_date": batch},
        {"_id": 0, "patient_id": 1, "current_score": 1, "risk_score": 1, "current_band": 1, "risk_band": 1,
         "discharge_date": 1, "monitoring_status": 1})) if ids else []
    own = {str(r["patient_id"]): r for r in db["care_actions"].find(
        {"patient_id": {"$in": access.id_variants(ids)}},
        {"_id": 0, "patient_id": 1, "hospital_id": 1, "insurer_member_id": 1})} if ids else {}
    ident = db.client[auth.IDENTITY_DB]
    hospitals = {h["_id"]: h.get("name", h["_id"]) for h in ident["hospitals"].find(
        {"_id": {"$in": list({o.get("hospital_id") for o in own.values() if o.get("hospital_id")})}}, {"name": 1})}
    insurer = ident["insurers"].find_one({"_id": user.get("insurer_id")}, {"name": 1}) or {}

    by_hospital: dict = {}
    for r in rows:
        hid = (own.get(str(r["patient_id"])) or {}).get("hospital_id")
        h = by_hospital.setdefault(hid, {"hospital_id": hid,
                                         "hospital": hospitals.get(hid, hid) if hid else "No hospital on file",
                                         "members": 0, "high_risk": 0, "expected_cost": 0.0})
        h["members"] += 1
        h["high_risk"] += str(r.get("current_band") or r.get("risk_band") or "").title() == "High"
        h["expected_cost"] += _score(r) / 100.0 * READMISSION_COST_TOTAL
    for h in by_hospital.values():
        h["avoidable"] = round(h["expected_cost"] * INTERVENTION_EFFECTIVENESS)
        h["expected_cost"] = round(h["expected_cost"])

    top = sorted((r for r in rows if str(r.get("current_band") or r.get("risk_band") or "").title() == "High"),
                 key=lambda r: -_score(r))[:TOP_MEMBERS]
    members = [{"patient_id": str(r["patient_id"]),
                "member_id": (own.get(str(r["patient_id"])) or {}).get("insurer_member_id"),
                "hospital": hospitals.get((own.get(str(r["patient_id"])) or {}).get("hospital_id"),
                                          (own.get(str(r["patient_id"])) or {}).get("hospital_id")),
                "risk_score": round(_score(r), 1), "discharge_date": r.get("discharge_date") or None}
               for r in top]
    # Risk bands now and the change since the previous weekly batch, members
    # whose risk is rising since discharge, and recent discharges - what the
    # hospital overview shows, for the insurer's own members only.
    def band(r):
        return str(r.get("current_band") or r.get("risk_band") or "").title()
    bands = {b: sum(1 for r in rows if band(r) == b) for b in ("High", "Medium", "Low")}
    earlier = sorted(d for d in db["patient_worklist"].distinct("batch_date") if batch and d < batch)
    high_delta = 0
    if earlier and ids:
        before = db["patient_worklist"].count_documents(
            {"patient_id": {"$in": access.id_variants(ids)}, "batch_date": earlier[-1],
             "$or": [{"current_band": {"$in": ["High", "high", "HIGH"]}},
                     {"current_band": {"$exists": False}, "risk_band": {"$in": ["High", "high", "HIGH"]}}]})
        high_delta = bands["High"] - before
    try:
        cutoff = (datetime.strptime(batch, "%Y-%m-%d") - timedelta(days=30)).strftime("%Y-%m-%d")
    except (TypeError, ValueError):
        cutoff = ""

    return {
        "insurer": {"id": user.get("insurer_id"), "name": insurer.get("name") or user.get("insurer_id")},
        "batch_date": batch,
        "bands": bands,
        "high_delta": high_delta,
        "rising": sum(1 for r in rows if r.get("monitoring_status") in ("action_required", "deteriorating")),
        "discharged_30d": sum(1 for r in rows if cutoff and str(r.get("discharge_date") or "") >= cutoff),
        "stats": {"members": len(rows), "hospitals": len([h for h in by_hospital if h]),
                  "high_risk": sum(h["high_risk"] for h in by_hospital.values()),
                  "expected_cost": sum(h["expected_cost"] for h in by_hospital.values()),
                  "avoidable": sum(h["avoidable"] for h in by_hospital.values()),
                  "without_member_id": sum(1 for i in ids if not (own.get(i) or {}).get("insurer_member_id")),
                  "readmission_cost": READMISSION_COST_TOTAL},
        "by_hospital": sorted(by_hospital.values(), key=lambda h: -h["members"]),
        "high_risk_members": members,
    }
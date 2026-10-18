"""
A pharmacy's home page: the patients who fill at this pharmacy (their scope -
core/access.py), what a pharmacy needs about each, and its refill-problem
reports (core/followups.report).

What a pharmacy sees is the overview layer plus the dispensing facts a pharmacy
already has - which GLP-1 drug and how reliably it is refilled - and who to
contact at the hospital: the patient's case manager. Never diagnoses, vitals,
risk drivers, notes or costs (45 CFR 164.502(b), minimum necessary). Opening
one patient's page is logged, once per sign-in, under DISPENSING_REASON
(routers/patients.get_patient_summary), as every other role's look is.
"""
from typing import Optional

from bson import ObjectId
from bson.errors import InvalidId
from fastapi import HTTPException

from core import followups
from core.access import scope_query
from core.mongo import get_db, get_shared_identity_db

CRITICAL = 0.75
HIGH = 0.50
# system_refill_score: drug-level refill average minus provider risk, 0.92 to
# 1.34 in the data (docs/DATA_AND_MODEL_DOCUMENTATION.md). Higher is more
# reliable. Shown as a band, relative to all patients, not as a raw number.
REFILL_LOW = 1.05
REFILL_GOOD = 1.20
DRUGS = {"SEMAGLUTIDE": "Semaglutide", "TIRZEPATIDE": "Tirzepatide", "LIRAGLUTIDE": "Liraglutide",
         "DULAGLUTIDE": "Dulaglutide"}


def refill_band(score) -> Optional[str]:
    if score is None:
        return None
    s = float(score)
    return "low" if s < REFILL_LOW else "good" if s >= REFILL_GOOD else "typical"


def drug_label(molecule) -> str:
    return DRUGS.get(str(molecule or "").upper(), str(molecule or "Unknown").title())


async def pharmacy_of(user: dict) -> Optional[dict]:
    if not user.get("pharmacy_id"):
        return None
    org = await get_shared_identity_db().pharmacies.find_one({"_id": user["pharmacy_id"]}, {"name": 1})
    return {"id": user["pharmacy_id"], "name": (org or {}).get("name") or user["pharmacy_id"]}


async def home(user: dict, scope: Optional[list]) -> dict:
    if user["role"] != "pharmacy":
        raise HTTPException(status_code=403, detail="This page is for pharmacies")
    db = get_db()
    rows = await db.patients.find(scope_query(scope), {
        "_id": 0, "patient_idx": 1, "dropout_prob": 1, "prediction": 1, "assigned_molecule": 1,
        "drug_generation": 1, "system_refill_score": 1}).to_list(length=None)
    idxs = [int(r["patient_idx"]) for r in rows]
    access = {int(a["patient_idx"]): a for a in await db.patient_access.find(
        {"patient_idx": {"$in": idxs}}, {"_id": 0, "patient_idx": 1, "hospital_id": 1,
                                         "case_manager_id": 1}).to_list(length=None)}
    ident = get_shared_identity_db()
    hospitals = {h["_id"]: h.get("name", h["_id"]) for h in await ident.hospitals.find(
        {"_id": {"$in": list({a.get("hospital_id") for a in access.values() if a.get("hospital_id")})}},
        {"name": 1}).to_list(length=None)}
    cm_ids = []
    for a in access.values():
        try:
            if a.get("case_manager_id"):
                cm_ids.append(ObjectId(a["case_manager_id"]))
        except (InvalidId, TypeError):
            pass
    managers = {str(u["_id"]): u.get("name") or u.get("email", "") for u in await ident.users.find(
        {"_id": {"$in": cm_ids}, "status": "active"}, {"name": 1, "email": 1}).to_list(length=None)}

    reports = await followups.pharmacy_reports(user)
    open_for = {}
    for doc in await db[followups.COLLECTION].find({"active_patient": {"$in": idxs}}).to_list(length=None):
        open_for[int(doc["patient_idx"])] = followups.pharmacy_view(doc, user)

    patients = []
    for r in rows:
        idx = int(r["patient_idx"])
        a = access.get(idx, {})
        patients.append({
            "patient_idx": idx, "dropout_prob": round(float(r.get("dropout_prob") or 0), 4),
            "prediction": r.get("prediction") or "Unknown",
            "drug": drug_label(r.get("assigned_molecule")), "drug_generation": r.get("drug_generation"),
            "refill_band": refill_band(r.get("system_refill_score")),
            "hospital": hospitals.get(a.get("hospital_id"), a.get("hospital_id")),
            "case_manager": managers.get(str(a.get("case_manager_id") or "")),
            "open_followup": open_for.get(idx),
        })
    patients.sort(key=lambda p: (-p["dropout_prob"], p["patient_idx"]))
    return {
        "pharmacy": await pharmacy_of(user),
        "stats": {
            "patients": len(patients),
            "critical": sum(p["dropout_prob"] >= CRITICAL for p in patients),
            "high": sum(HIGH <= p["dropout_prob"] < CRITICAL for p in patients),
            "low_refill": sum(p["refill_band"] == "low" for p in patients),
            "open_reports": sum(r["status"] != "done" for r in reports),
        },
        "patients": patients,
        "reports": reports,
        "problems": [{"key": k, "label": v} for k, v in followups.PHARMACY_PROBLEMS.items()],
    }
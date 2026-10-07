"""
Who may see which GLP-1 patients, and which screens - decided here only.

Mirrors Readmissions/api/access.py. Ownership lives in `patient_access`, one
record per patient_idx, which scripts/migrate_csv_to_mongo.py never drops, so a
data reload does not orphan anyone:

    hospital_id         the hospital the patient belongs to
    insurer_id          who pays for them
    assigned_doctor_ids shared_identity user ids of their doctors
    assigned_doctor_id  the first of them - the field records had before a
                        patient could have several doctors, still read (see
                        doctor_ids) and kept in step on every change
    assigned_nurse_ids  shared_identity user ids of their nurses
    patient_account_id  the patient's own login, if they have one
    pharmacy            where they collect their prescription

    superadmin                    every patient
    hospital_admin, case_manager  their hospital
    doctor, nurse                 their hospital's patients assigned to them
    insurer                       their members, in any hospital
    patient                       their own record
    anyone without a placement    nobody

The superadmin can look at one hospital at a time through the hospital picker
(the X-Hospital-Id header), and then sees what that hospital's admin sees.

Two layers of detail. Hospital admins and insurers get the overview layer of
their patients - adherence risk, care team, insurer - and open the clinical
layer (vitals, drug, pharmacy, risk drivers) one patient at a time with a
reason, which is logged (core/access_log.py). The superadmin, doctors and
nurses are never asked but are logged too: a doctor's or nurse's first look at
each of their patients per sign-in is recorded as "Treatment (care team)".

Model-wide outputs (survival curves, feature importance, segment profiles, model
info) describe the model rather than any patient and are not filtered. Cost and
ROI screens are for the roles that own the budget.
"""

from typing import Optional

from fastapi import Depends, HTTPException, Request

from core import access_log
from core.mongo import get_db
from core.security import current_user

COST_VIEW_ROLES = ("superadmin", "hospital_admin", "insurer")

# The hospital pages. Doctors and nurses work from their own patient list.
OVERVIEW_ROLES = ("superadmin", "hospital_admin", "case_manager", "insurer")
STAFF_ROLES = ("superadmin", "hospital_admin", "case_manager")
ASSIGN_ROLES = ("superadmin", "hospital_admin", "case_manager")

# ------------------------------------------------ overview and clinical layer
REASON_ROLES = ("hospital_admin", "insurer")

# Must match REASONS in Readmissions/api/access.py: one access log, one list.
REASONS = {
    "care_coordination": "Care coordination",
    "incident_review":   "Complaint / incident review",
    "audit":             "Audit",
    "billing":           "Billing query",
}
SUPERADMIN_REASON = "superadmin"

# Roles that are never asked for a reason but whose first look at each patient
# per sign-in is still logged, for the audit trail (45 CFR 164.312(b)).
# Doctors and nurses open only their own patients (patient_scope), so treatment
# is the reason (45 CFR 164.506); a case manager coordinates care across the
# hospital. Like SUPERADMIN_REASON these are not in REASONS: nobody picks them,
# so the list both apps share stays as it is.
# NOT YET IN READMISSIONS: it logs only the superadmin and the reason roles.
CARE_TEAM_ROLES = ("doctor", "nurse")
TREATMENT_REASON = "treatment"
COORDINATION_REASON = "case_management"
AUTOMATIC_REASONS = {"superadmin": SUPERADMIN_REASON, "doctor": TREATMENT_REASON,
                     "nurse": TREATMENT_REASON, "case_manager": COORDINATION_REASON}
LOGGED_REASONS = {SUPERADMIN_REASON: "Superadmin (not asked)",
                  TREATMENT_REASON: "Treatment (care team)",
                  COORDINATION_REASON: "Care coordination (case manager)"}

REASON_REQUIRED = {"code": "reason_required",
                   "message": "Give a reason to open this patient's clinical details"}

# Fields of a patient row that belong to the clinical layer. The overview layer
# is what is left: patient_idx, dropout risk and prediction, care team, insurer.
CLINICAL_FIELDS = frozenset({
    "driver_1", "driver_1_direction", "driver_1_shap",
    "driver_2", "driver_2_direction", "driver_2_shap",
    "driver_3", "driver_3_direction", "driver_3_shap",
    "BMXBMI", "RIDAGEYR", "LBXGH", "comorbidity_score", "bio_friction",
    "income_cost_pressure", "system_refill_score", "drug_generation", "time_to_dropout",
    "assigned_molecule", "avg_oop_cost", "pharmacy", "cluster", "segment",
})


def doctor_ids(record: dict) -> list:
    """A patient_access record's doctors: the list, plus the single doctor an
    older record (or a script that fills in `assigned_doctor_id`) holds."""
    ids = [str(d) for d in (record.get("assigned_doctor_ids") or []) if d]
    legacy = record.get("assigned_doctor_id")
    if legacy and str(legacy) not in ids:
        ids.insert(0, str(legacy))
    return ids


def doctor_query(doctor_id: str) -> dict:
    """patient_access records that list this doctor, in either field."""
    return {"$or": [{"assigned_doctor_ids": doctor_id}, {"assigned_doctor_id": doctor_id}]}


def needs_reason(user: dict) -> bool:
    return user["role"] in REASON_ROLES


def redact(row: dict) -> dict:
    return {k: v for k, v in row.items() if k not in CLINICAL_FIELDS}


def acting_as(user: dict, hospital_id: Optional[str]) -> dict:
    """The superadmin looking at one hospital. Nobody else can use the header."""
    hospital_id = (hospital_id or "").strip()
    if user.get("role") == "superadmin" and hospital_id:
        return {**user, "acting_hospital_id": hospital_id}
    return user


def hospital_of(user: dict) -> Optional[str]:
    """Their own hospital, or the one the superadmin picked; None for a
    superadmin looking at every hospital."""
    return user.get("acting_hospital_id") or user.get("hospital_id")


async def actor(request: Request, user: dict = Depends(current_user)) -> dict:
    """FastAPI dependency: the signed-in user as the rest of GLP-1 treats them."""
    return acting_as(user, request.headers.get("x-hospital-id"))


async def patient_scope(user: dict) -> Optional[list]:
    """The patient_idx values this user may see, or None for no restriction."""
    role, hospital, uid = user["role"], user.get("hospital_id"), user["id"]
    if role == "superadmin":
        if not user.get("acting_hospital_id"):
            return None
        query = {"hospital_id": user["acting_hospital_id"]}
    elif role in ("hospital_admin", "case_manager"):
        query = {"hospital_id": hospital} if hospital else None
    elif role == "doctor":
        query = {"hospital_id": hospital, **doctor_query(uid)} if hospital else None
    elif role == "nurse":
        query = {"hospital_id": hospital, "assigned_nurse_ids": uid} if hospital else None
    elif role == "insurer":
        query = {"insurer_id": user["insurer_id"]} if user.get("insurer_id") else None
    elif role == "patient":
        query = {"patient_account_id": uid}
    else:
        query = None
    if query is None:
        return []
    docs = await get_db().patient_access.find(query, {"_id": 0, "patient_idx": 1}).to_list(length=None)
    return [int(d["patient_idx"]) for d in docs]


async def scope_of(user: dict = Depends(actor)) -> Optional[list]:
    """FastAPI dependency: the caller's scope, worked out once per request."""
    return await patient_scope(user)


def scope_query(scope: Optional[list], field: str = "patient_idx") -> dict:
    return {} if scope is None else {field: {"$in": list(scope)}}


def require_patient(scope: Optional[list], patient_idx: int) -> None:
    # 404, not 403: the same as for a patient that does not exist, so nobody
    # can find out which patients another hospital has.
    if scope is not None and int(patient_idx) not in set(scope):
        raise HTTPException(status_code=404, detail=f"Patient {patient_idx} not found")


async def patient_hospital(patient_idx: int) -> Optional[str]:
    doc = await get_db().patient_access.find_one({"patient_idx": int(patient_idx)},
                                                 {"_id": 0, "hospital_id": 1})
    return (doc or {}).get("hospital_id")


async def detail_access(user: dict, patient_idx: int) -> str:
    """open | granted | reason_required, for a patient already in scope."""
    if not needs_reason(user):
        return "open"
    granted = await access_log.has_opened(user, patient_idx)
    return "granted" if granted else "reason_required"


async def require_detail(user: dict, scope: Optional[list], patient_idx: int) -> None:
    """The clinical layer of one patient: 404 outside the caller's patients; a
    hospital admin or insurer then needs a reason given this sign-in. The
    superadmin's, a doctor's, a nurse's and a case manager's first look each
    sign-in is logged without asking."""
    require_patient(scope, patient_idx)
    automatic = AUTOMATIC_REASONS.get(user["role"])
    if automatic:
        if not await access_log.has_opened(user, patient_idx):
            await access_log.record(user, patient_idx, await patient_hospital(patient_idx),
                                    automatic)
        if user["role"] in CARE_TEAM_ROLES:
            # Opening a patient clears them from the caller's bell.
            from core import notifications                  # avoid an import cycle
            await notifications.mark_seen(user, [patient_idx])
        return
    if await detail_access(user, patient_idx) == "reason_required":
        raise HTTPException(status_code=403, detail=REASON_REQUIRED)


def require_cost_view(user: dict = Depends(current_user)) -> dict:
    """FastAPI dependency for the cost and ROI screens."""
    if user["role"] not in COST_VIEW_ROLES:
        raise HTTPException(status_code=403,
                            detail="Cost and ROI views are for hospital administrators and insurers")
    return user


def require_role(*roles: str):
    """FastAPI dependency factory: 403 unless the caller has one of `roles`."""
    async def check(user: dict = Depends(actor)) -> dict:
        if user["role"] not in roles:
            raise HTTPException(status_code=403, detail=f"A {user['role']} cannot use this page")
        return user
    return check
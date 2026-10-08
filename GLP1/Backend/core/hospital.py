"""
The hospital pages: Overview, Staff, and who looks after each patient.

Mirrors Readmissions/api/hospital.py. All of it is the overview layer (see
core/access.py) - counts, names and assignments - so no reason is asked, and
all of it is limited to the caller's patients by the same scope as every other
route. Doctors and nurses are assigned by their shared_identity account id.
"""

from datetime import datetime, timezone
from typing import Optional

from bson import ObjectId
from bson.errors import InvalidId
from fastapi import HTTPException

from pymongo import UpdateOne

from core.access import (COST_VIEW_ROLES, doctor_ids, doctor_query, hospital_of,
                         require_patient, scope_query)
from core.mongo import get_db, get_shared_identity_db

HIGH_RISK = 0.75
MAX_BATCH_ASSIGN = 500
MAX_TEAM = 20            # doctors, and nurses, on one patient
_ACCESS_FIELDS = {"_id": 0, "patient_idx": 1, "hospital_id": 1, "insurer_id": 1,
                  "assigned_doctor_id": 1, "assigned_doctor_ids": 1, "assigned_nurse_ids": 1}


def _object_ids(ids) -> list:
    out = []
    for i in ids:
        try:
            out.append(ObjectId(str(i)))
        except (InvalidId, TypeError):
            continue
    return out


def _person(account: dict) -> dict:
    return {"id": str(account["_id"]), "name": account.get("name") or account.get("email", ""),
            "email": account.get("email", "")}


async def _hospital_name(hospital_id: Optional[str]) -> Optional[dict]:
    if not hospital_id:
        return None
    h = await get_shared_identity_db().hospitals.find_one({"_id": hospital_id})
    return {"id": hospital_id, "name": h["name"] if h else hospital_id}


# ------------------------------------------------------------ care teams
async def owners(idxs) -> dict:
    """patient_idx -> its patient_access record."""
    query = {} if idxs is None else {"patient_idx": {"$in": [int(i) for i in idxs]}}
    docs = await get_db().patient_access.find(query, {**_ACCESS_FIELDS, "pharmacy": 1}).to_list(length=None)
    return {int(d["patient_idx"]): d for d in docs}


async def care_teams(idxs) -> dict:
    """patient_idx -> {"doctors", "nurses", "insurer", "pharmacy", "hospital_id"},
    with names. Three queries however many patients - the list loads all of
    them. "doctor" is the first doctor, for callers that show only one."""
    own = await owners(idxs)
    people_ids = {d for o in own.values() for d in doctor_ids(o)}
    people_ids |= {n for o in own.values() for n in (o.get("assigned_nurse_ids") or [])}
    insurer_ids = {o["insurer_id"] for o in own.values() if o.get("insurer_id")}
    ident = get_shared_identity_db()
    people = {str(u["_id"]): _person(u) for u in await ident.users.find(
        {"_id": {"$in": _object_ids(people_ids)}}, {"email": 1, "name": 1}).to_list(length=None)}
    insurers = {i["_id"]: {"id": i["_id"], "name": i.get("name", i["_id"])}
                for i in await ident.insurers.find({"_id": {"$in": list(insurer_ids)}}).to_list(length=None)}
    out = {}
    for idx in idxs:
        o = own.get(int(idx), {})
        doctors = [people[d] for d in doctor_ids(o) if d in people]
        out[int(idx)] = {
            "hospital_id": o.get("hospital_id"),
            "doctors": doctors,
            "doctor": doctors[0] if doctors else None,
            "nurses": [people[n] for n in (o.get("assigned_nurse_ids") or []) if n in people],
            "insurer": insurers.get(o.get("insurer_id")) if o.get("insurer_id") else None,
            "pharmacy": o.get("pharmacy"),
        }
    return out


async def care_filter(scope: Optional[list], doctor: Optional[str] = None,
                      nurse: Optional[str] = None, unassigned: Optional[str] = None) -> Optional[list]:
    """patient_idx values matching a care-team filter, within the scope; None
    when no filter was asked for."""
    if not (doctor or nurse or unassigned):
        return None
    if unassigned in ("doctor", "nurse"):
        # A patient with no patient_access record has nobody either, so this
        # is worked out as "in scope, minus those who have one".
        has_one = ({"$or": [{"assigned_doctor_id": {"$nin": [None, ""]}},
                            {"assigned_doctor_ids.0": {"$exists": True}}]}
                   if unassigned == "doctor" else {"assigned_nurse_ids.0": {"$exists": True}})
        has = {int(d["patient_idx"]) for d in await get_db().patient_access.find(
            {**scope_query(scope), **has_one}, {"_id": 0, "patient_idx": 1}).to_list(length=None)}
        everyone = scope if scope is not None else [
            int(d["patient_idx"]) for d in await get_db().patients.find(
                {}, {"_id": 0, "patient_idx": 1}).to_list(length=None)]
        return [i for i in everyone if int(i) not in has]
    query = dict(scope_query(scope))
    if doctor:
        query.update(doctor_query(doctor))
    if nurse:
        query["assigned_nurse_ids"] = nurse
    docs = await get_db().patient_access.find(query, {"_id": 0, "patient_idx": 1}).to_list(length=None)
    return [int(d["patient_idx"]) for d in docs]


async def assign(user: dict, scope: Optional[list], patient_idxs: list,
                 doctor_id: Optional[str] = None, doctor_ids_: Optional[list] = None,
                 add_doctor_ids: Optional[list] = None, remove_doctor_ids: Optional[list] = None,
                 nurse_ids: Optional[list] = None, add_nurse_ids: Optional[list] = None,
                 remove_nurse_ids: Optional[list] = None) -> dict:
    """Change the doctors and nurses of one or more patients.

    For each of doctors and nurses, either set the whole list (`doctor_ids`,
    `nurse_ids`; an empty list clears it) or add and remove people from
    whoever each patient already has (`add_*`, `remove_*`). `doctor_id` is the
    older one-doctor form: "x" sets the list to [x], "" clears it.

    Everyone added must be an active doctor or nurse of the patients'
    hospital. Anyone can be removed - including someone who has since left.
    Nothing is written unless every patient checks out."""
    idxs = list(dict.fromkeys(int(i) for i in (patient_idxs or [])))
    if not idxs:
        raise HTTPException(status_code=422, detail="Choose at least one patient")
    if len(idxs) > MAX_BATCH_ASSIGN:
        raise HTTPException(status_code=422, detail=f"At most {MAX_BATCH_ASSIGN} patients at a time")

    clean = lambda ids: list(dict.fromkeys(str(i).strip() for i in (ids or []) if str(i).strip()))
    if doctor_id is not None:
        if doctor_ids_ is not None:
            raise HTTPException(status_code=422, detail="Send doctor_id or doctor_ids, not both")
        doctor_ids_ = [doctor_id] if doctor_id.strip() else []
    changes = {}
    for kind, replace, add, remove in (("doctor", doctor_ids_, add_doctor_ids, remove_doctor_ids),
                                       ("nurse", nurse_ids, add_nurse_ids, remove_nurse_ids)):
        add, remove = clean(add), clean(remove)
        if replace is not None and (add or remove):
            raise HTTPException(status_code=422,
                                detail=f"Either set the {kind}s or add and remove them, not both")
        if set(add) & set(remove):
            raise HTTPException(status_code=422, detail=f"The same {kind} cannot be added and removed")
        if replace is not None:
            replace = clean(replace)
            if len(replace) > MAX_TEAM:
                raise HTTPException(status_code=422, detail=f"At most {MAX_TEAM} {kind}s per patient")
            changes[kind] = ("set", replace, [])
        elif add or remove:
            changes[kind] = ("edit", add, remove)
    if not changes:
        raise HTTPException(status_code=422, detail="Nothing to change")
    for idx in idxs:
        require_patient(scope, idx)

    own = await owners(idxs)
    hospitals = set()
    for idx in idxs:
        hospital = own.get(idx, {}).get("hospital_id")
        if not hospital:
            raise HTTPException(status_code=409, detail=f"Patient {idx} is not in any hospital yet")
        hospitals.add(hospital)
    if len(hospitals) > 1:
        raise HTTPException(status_code=409, detail="Assign one hospital's patients at a time")
    hospital = hospitals.pop()

    # Everyone being given a patient must work in that patient's hospital.
    users = get_shared_identity_db().users
    for kind, (mode, wanted, _) in changes.items():
        if not wanted:
            continue
        found = {str(u["_id"]) for u in await users.find(
            {"_id": {"$in": _object_ids(wanted)}, "role": kind, "status": "active",
             "hospital_id": hospital}, {"_id": 1}).to_list(length=None)}
        if any(p not in found for p in wanted):
            raise HTTPException(status_code=404, detail=f"{kind.title()} not found in this hospital")

    # Work out every patient's new team first; write only if all are valid.
    stamp = {"care_team_updated_at": datetime.now(timezone.utc),
             "care_team_updated_by": user.get("email", "")}
    ops = []
    for idx in idxs:
        record = own[idx]
        fields = dict(stamp)
        for kind, (mode, wanted, removed) in changes.items():
            before = doctor_ids(record) if kind == "doctor" else \
                [str(n) for n in (record.get("assigned_nurse_ids") or [])]
            after = wanted if mode == "set" else \
                [p for p in before if p not in removed] + [p for p in wanted if p not in before]
            if len(after) > MAX_TEAM:
                raise HTTPException(status_code=422,
                                    detail=f"Patient {idx} would have more than {MAX_TEAM} {kind}s")
            # When each person joined, for their "new patient" notification
            # (core/notifications.py). Only for people newly added.
            for person in after:
                if person not in before:
                    fields[f"care_team_added_at.{person}"] = stamp["care_team_updated_at"]
            if kind == "doctor":
                fields["assigned_doctor_ids"] = after
                fields["assigned_doctor_id"] = after[0] if after else None
            else:
                fields["assigned_nurse_ids"] = after
        ops.append(UpdateOne({"patient_idx": idx}, {"$set": fields}))
    result = await get_db().patient_access.bulk_write(ops, ordered=False)
    return {"updated": result.matched_count, "patient_ids": idxs}


# --------------------------------------------------------------- my account
async def me(user: dict, scope: Optional[list]) -> dict:
    """The signed-in person's own account, for Settings: who they are, where
    they work, and how many patients they can see. Their own details only."""
    ident = get_shared_identity_db()
    ids = _object_ids([user["id"]])
    account = (await ident.users.find_one({"_id": ids[0]}, {"name": 1, "created_at": 1}) if ids else None) or {}
    insurer = None
    if user.get("insurer_id"):
        doc = await ident.insurers.find_one({"_id": user["insurer_id"]})
        insurer = {"id": user["insurer_id"], "name": (doc or {}).get("name", user["insurer_id"])}
    created = account.get("created_at")
    return {"id": user["id"], "name": account.get("name") or "", "email": user.get("email", ""),
            "role": user["role"], "hospital": await _hospital_name(user.get("hospital_id")),
            "insurer": insurer, "patients": None if scope is None else len(scope),
            "member_since": created.isoformat() if hasattr(created, "isoformat") else None}


# --------------------------------------------------------------- overview
async def overview(user: dict, scope: Optional[list]) -> dict:
    """How is my hospital doing - or, for an insurer, how are my members."""
    db = get_db()
    mine = scope_query(scope)
    rows = await db.patients.find(mine, {"_id": 0, "patient_idx": 1, "is_adherent": 1,
                                         "dropout_prob": 1, "assigned_molecule": 1,
                                         "cluster": 1}).to_list(length=None)
    total = len(rows)
    adherent = sum(1 for r in rows if int(r.get("is_adherent") or 0) == 1)
    high_risk = sum(1 for r in rows if float(r.get("dropout_prob") or 0) >= HIGH_RISK)

    drugs: dict = {}
    per_cluster: dict = {}
    for r in rows:
        drug = str(r.get("assigned_molecule") or "UNKNOWN")
        drugs[drug] = drugs.get(drug, 0) + 1
        c = int(r.get("cluster") or 0)
        per_cluster[c] = per_cluster.get(c, 0) + 1
    drug_mix = sorted(({"drug": k, "count": v} for k, v in drugs.items()), key=lambda d: -d["count"])

    own = await owners([r["patient_idx"] for r in rows])
    no_doctor = sum(1 for r in rows if not doctor_ids(own.get(int(r["patient_idx"]), {})))
    no_nurse = sum(1 for r in rows if not own.get(int(r["patient_idx"]), {}).get("assigned_nurse_ids"))
    mix: dict = {}
    for r in rows:
        key = own.get(int(r["patient_idx"]), {}).get("insurer_id")
        mix[key] = mix.get(key, 0) + 1
    names = {i["_id"]: i.get("name", i["_id"]) for i in await get_shared_identity_db().insurers.find(
        {"_id": {"$in": [k for k in mix if k]}}).to_list(length=None)}
    insurer_mix = sorted(({"id": k, "name": names.get(k, k) if k else "No insurer on file",
                           "count": n} for k, n in mix.items()), key=lambda x: -x["count"])

    # Drug spend is a cost figure, so only the roles with the cost screens get
    # it. Per-patient segment economics times this caller's own counts, as the
    # summary does.
    spend = None
    if user["role"] in COST_VIEW_ROLES:
        cea = await db.cost_effectiveness.find({}, {"_id": 0}).to_list(length=None)
        spend = {
            "annual": round(sum(float(d.get("annual_cost") or 0) * per_cluster.get(int(d["cluster"]), 0)
                                for d in cea)),
            "wasted": round(sum(float(d.get("wasted_spend_per_pt") or 0) * per_cluster.get(int(d["cluster"]), 0)
                                for d in cea)),
        }

    hospital = hospital_of(user)
    staff = None
    if user["role"] != "insurer":
        q = {"status": "active"}
        if hospital:
            q["hospital_id"] = hospital
        users = get_shared_identity_db().users
        staff = {"doctors": await users.count_documents({**q, "role": "doctor"}),
                 "nurses": await users.count_documents({**q, "role": "nurse"})}

    return {
        "hospital": await _hospital_name(hospital),
        "total_patients": total,
        "adherent": adherent,
        "non_adherent": total - adherent,
        "high_risk": high_risk,
        "high_risk_threshold": HIGH_RISK,
        "drug_mix": drug_mix,
        "drug_spend": spend,
        "no_doctor": no_doctor,
        "no_nurse": no_nurse,
        "insurer_mix": insurer_mix,
        "staff": staff,
    }


# ------------------------------------------------------------------ staff
async def staff(user: dict, scope: Optional[list]) -> dict:
    """Doctors and nurses with their patient and non-adherent counts."""
    db = get_db()
    hospital = hospital_of(user)
    q = {"status": "active"}
    if hospital:
        q["hospital_id"] = hospital

    rows = await db.patients.find(scope_query(scope), {"_id": 0, "patient_idx": 1, "is_adherent": 1,
                                                       "dropout_prob": 1}).to_list(length=None)
    non_adherent = {int(r["patient_idx"]) for r in rows if int(r.get("is_adherent") or 0) == 0}
    high = {int(r["patient_idx"]) for r in rows if float(r.get("dropout_prob") or 0) >= HIGH_RISK}
    everyone = {int(r["patient_idx"]) for r in rows}

    by_doctor: dict = {}
    by_nurse: dict = {}
    for o in (await owners([r["patient_idx"] for r in rows])).values():
        idx = int(o["patient_idx"])
        for d in doctor_ids(o):
            by_doctor.setdefault(d, set()).add(idx)
        for n in o.get("assigned_nurse_ids") or []:
            by_nurse.setdefault(str(n), set()).add(idx)

    def counts(idxs: set) -> dict:
        return {"patients": len(idxs), "non_adherent": len(idxs & non_adherent),
                "high_risk": len(idxs & high)}

    ident = get_shared_identity_db()
    accounts = await ident.users.find({**q, "role": {"$in": ["doctor", "nurse"]}}).sort(
        "email", 1).to_list(length=None)
    # Hospital names for the superadmin's all-hospitals view (and its search).
    names = {h["_id"]: h.get("name", h["_id"]) for h in await ident.hospitals.find(
        {"_id": {"$in": list({a.get("hospital_id") for a in accounts if a.get("hospital_id")})}},
        {"name": 1}).to_list(length=None)}

    def row(a: dict, assigned: dict) -> dict:
        return {**_person(a), "hospital_id": a.get("hospital_id"),
                "hospital_name": names.get(a.get("hospital_id")),
                **counts(assigned.get(str(a["_id"]), set()))}

    doctors = [row(a, by_doctor) for a in accounts if a.get("role") == "doctor"]
    nurses = [row(a, by_nurse) for a in accounts if a.get("role") == "nurse"]
    with_doctor = {i for s in by_doctor.values() for i in s}
    with_nurse = {i for s in by_nurse.values() for i in s}
    return {
        "hospital": await _hospital_name(hospital),
        "doctors": doctors,
        "nurses": nurses,
        "no_doctor": len(everyone - with_doctor),
        "no_nurse": len(everyone - with_nurse),
    }
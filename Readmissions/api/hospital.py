"""The hospital pages: Overview, Staff, and who looks after each patient.

Everything here is the overview layer (see api/access.py): counts, names and
assignments, never diagnoses, drivers or notes. So none of it needs a reason,
and all of it is limited to the caller's patients through the same scope as
every other route.

Doctors are assigned by their `doctors` registry id, which alert routing also
reads; a doctor account becomes assignable once it is in the registry (the
Staff page offers to add it). Nurses are assigned by account id.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Optional

from bson import ObjectId
from bson.errors import InvalidId
from fastapi import HTTPException

from api import access, auth, doctor_service

OPEN_ALERT_STATUSES = list(doctor_service._STATUS_OPEN)
HIGH = "High"
MAX_BATCH_ASSIGN = 500


def _identity(db):
    return db.client[auth.IDENTITY_DB]


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


def _band(row: dict) -> str:
    return str(row.get("current_band") or row.get("risk_band") or "").capitalize()


# ------------------------------------------------------------ care teams
def owners(db, patient_ids) -> dict:
    """patient_id (as a string) -> its ownership record."""
    rows = db["care_actions"].find(
        {"patient_id": {"$in": access.id_variants(list(patient_ids))}},
        {"_id": 0, "patient_id": 1, "hospital_id": 1, "insurer_id": 1,
         "assigned_doctor_id": 1, "assigned_nurse_ids": 1})
    return {str(r["patient_id"]): r for r in rows}


def care_teams(db, patient_ids) -> dict:
    """patient_id -> {"doctor", "nurses", "insurer", "hospital_id"}, with names.

    Four queries for any number of patients, so a page of 25 rows or an export
    of 5,000 costs the same round trips."""
    own = owners(db, patient_ids)
    doctor_ids = {o["assigned_doctor_id"] for o in own.values() if o.get("assigned_doctor_id")}
    nurse_ids = {n for o in own.values() for n in (o.get("assigned_nurse_ids") or [])}
    insurer_ids = {o["insurer_id"] for o in own.values() if o.get("insurer_id")}

    doctors = {d["doctor_id"]: {"id": d["doctor_id"], "name": d.get("name", d["doctor_id"]),
                                "specialty": d.get("specialty", "")}
               for d in db["doctors"].find({"doctor_id": {"$in": list(doctor_ids)}},
                                           {"_id": 0, "doctor_id": 1, "name": 1, "specialty": 1})}
    nurses = {str(u["_id"]): _person(u)
              for u in auth.users(db).find({"_id": {"$in": _object_ids(nurse_ids)}},
                                           {"email": 1, "name": 1})}
    insurers = {i["_id"]: {"id": i["_id"], "name": i.get("name", i["_id"])}
                for i in _identity(db)["insurers"].find({"_id": {"$in": list(insurer_ids)}})}

    out = {}
    for pid in patient_ids:
        o = own.get(str(pid), {})
        out[str(pid)] = {
            "hospital_id": o.get("hospital_id"),
            "doctor": doctors.get(o.get("assigned_doctor_id")) if o.get("assigned_doctor_id") else None,
            "nurses": [nurses[n] for n in (o.get("assigned_nurse_ids") or []) if n in nurses],
            "insurer": insurers.get(o.get("insurer_id")) if o.get("insurer_id") else None,
        }
    return out


def care_filter(db, scope: Optional[list], doctor: Optional[str] = None,
                nurse: Optional[str] = None, unassigned: Optional[str] = None) -> Optional[list]:
    """The patient ids matching a care-team filter, within the scope, or None
    when no filter was asked for. Drives the Staff page's drill-down and the
    Overview's "no doctor assigned" link."""
    if not (doctor or nurse or unassigned):
        return None
    query: dict = dict(access.scope_query(scope))
    if doctor:
        query["assigned_doctor_id"] = doctor
    if nurse:
        query["assigned_nurse_ids"] = nurse
    if unassigned == "doctor":
        query["$or"] = [{"assigned_doctor_id": None}, {"assigned_doctor_id": ""},
                        {"assigned_doctor_id": {"$exists": False}}]
    elif unassigned == "nurse":
        query["$or"] = [{"assigned_nurse_ids": {"$size": 0}},
                        {"assigned_nurse_ids": {"$exists": False}}, {"assigned_nurse_ids": None}]
    return [r["patient_id"] for r in db["care_actions"].find(query, {"patient_id": 1})]


def assign(db, user: dict, scope: Optional[list], patient_ids: list,
           doctor_id: Optional[str] = None, nurse_ids: Optional[list] = None,
           add_nurse_ids: Optional[list] = None) -> dict:
    """Set the doctor and nurses of one or more patients.

    doctor_id      None leaves it, "" clears it, anything else must be an active
                   registry doctor of each patient's hospital
    nurse_ids      replaces the nurses; each must be an active nurse account of
                   each patient's hospital
    add_nurse_ids  adds nurses, keeping the ones already there

    All or nothing: every patient and every person is checked before anything
    is written, so a batch with one bad row changes nobody.
    """
    patient_ids = [str(p) for p in dict.fromkeys(patient_ids or [])]
    if not patient_ids:
        raise HTTPException(status_code=422, detail="Choose at least one patient")
    if len(patient_ids) > MAX_BATCH_ASSIGN:
        raise HTTPException(status_code=422, detail=f"At most {MAX_BATCH_ASSIGN} patients at a time")
    if doctor_id is None and nurse_ids is None and not add_nurse_ids:
        raise HTTPException(status_code=422, detail="Nothing to change")
    for pid in patient_ids:
        access.require_patient(scope, pid)

    own = owners(db, patient_ids)
    hospitals = set()
    for pid in patient_ids:
        hospital = own.get(pid, {}).get("hospital_id")
        if not hospital:
            raise HTTPException(status_code=409, detail=f"Patient {pid} is not in any hospital yet")
        hospitals.add(hospital)
    # Checked against the patients' hospital, not the caller's: the superadmin
    # looking at every hospital can assign in any of them, but never a doctor
    # from one hospital to a patient of another.
    if len(hospitals) > 1:
        raise HTTPException(status_code=409, detail="Assign one hospital's patients at a time")
    hospital = hospitals.pop()

    doctor_id = doctor_id.strip() if isinstance(doctor_id, str) else doctor_id
    if doctor_id:
        if not db["doctors"].find_one({"doctor_id": doctor_id, "hospital_id": hospital,
                                       "active": True}, {"_id": 1}):
            raise HTTPException(status_code=404, detail="Doctor not found in this hospital")

    wanted = [str(n) for n in (nurse_ids or []) + (add_nurse_ids or [])]
    if wanted:
        found = {str(u["_id"]) for u in auth.users(db).find(
            {"_id": {"$in": _object_ids(wanted)}, "role": "nurse", "status": "active",
             "hospital_id": hospital}, {"_id": 1})}
        missing = [n for n in wanted if n not in found]
        if missing:
            raise HTTPException(status_code=404, detail="Nurse not found in this hospital")

    update: dict = {}
    if doctor_id is not None:
        update.setdefault("$set", {})["assigned_doctor_id"] = doctor_id or None
    if nurse_ids is not None:
        update.setdefault("$set", {})["assigned_nurse_ids"] = list(dict.fromkeys(nurse_ids))
    elif add_nurse_ids:
        update["$addToSet"] = {"assigned_nurse_ids": {"$each": list(add_nurse_ids)}}
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    update.setdefault("$set", {})["care_team_updated_at"] = now
    update["$set"]["care_team_updated_by"] = user.get("email", "")

    result = db["care_actions"].update_many(
        {"patient_id": {"$in": access.id_variants(patient_ids)}}, update)
    return {"updated": result.matched_count, "patient_ids": patient_ids}


# --------------------------------------------------------------- overview
def _latest_rows(view, batch_date: str) -> list:
    return list(view["patient_worklist"].find(
        {"batch_date": batch_date},
        {"_id": 0, "patient_id": 1, "risk_band": 1, "current_band": 1,
         "monitoring_status": 1, "discharge_date": 1}))


def _hospital_name(db, hospital_id: Optional[str]) -> Optional[dict]:
    if not hospital_id:
        return None
    h = _identity(db)["hospitals"].find_one({"_id": hospital_id})
    return {"id": hospital_id, "name": h["name"] if h else hospital_id}


def me(db, user: dict, scope: Optional[list]) -> dict:
    """The signed-in person's own account, for Settings: who they are, where
    they work, and how many patients they can see. Their own details only, and
    the same shape as GLP-1's /api/me, so the two Settings pages match."""
    insurer = None
    if user.get("insurer_id"):
        doc = _identity(db)["insurers"].find_one({"_id": user["insurer_id"]})
        insurer = {"id": user["insurer_id"], "name": (doc or {}).get("name", user["insurer_id"])}
    return {"id": str(user["_id"]), "name": user.get("name") or "", "email": user.get("email", ""),
            "role": user["role"], "hospital": _hospital_name(db, user.get("hospital_id")),
            "insurer": insurer,
            "patients": None if scope is None else len({str(p) for p in scope})}


def overview(db, view, user: dict, batch_dates: list) -> dict:
    """How is my hospital doing - or, for an insurer, how are my members."""
    if not batch_dates:
        return {"batch_date": None, "total_patients": 0}
    latest = batch_dates[-1]
    rows = _latest_rows(view, latest)
    pids = [str(r["patient_id"]) for r in rows]

    bands = {"High": 0, "Medium": 0, "Low": 0}
    for r in rows:
        b = _band(r)
        if b in bands:
            bands[b] += 1
    high_before = None
    if len(batch_dates) > 1:
        high_before = sum(1 for r in _latest_rows(view, batch_dates[-2]) if _band(r) == HIGH)

    try:
        cutoff = (datetime.strptime(latest, "%Y-%m-%d") - timedelta(days=30)).strftime("%Y-%m-%d")
    except ValueError:
        cutoff = ""
    own = owners(db, pids)
    no_doctor = sum(1 for p in pids if not own.get(p, {}).get("assigned_doctor_id"))
    no_nurse = sum(1 for p in pids if not own.get(p, {}).get("assigned_nurse_ids"))

    mix: dict = {}
    for p in pids:
        key = own.get(p, {}).get("insurer_id")
        mix[key] = mix.get(key, 0) + 1
    names = {i["_id"]: i.get("name", i["_id"])
             for i in _identity(db)["insurers"].find({"_id": {"$in": [k for k in mix if k]}})}
    insurer_mix = sorted(({"id": k, "name": names.get(k, k) if k else "No insurer on file",
                           "count": n} for k, n in mix.items()), key=lambda x: -x["count"])

    hospital = access.hospital_of(user)
    staff = None
    if user["role"] != "insurer":
        doctor_q = access.doctor_query(user)
        people = {"role": "nurse", "status": "active"}
        if hospital:
            people["hospital_id"] = hospital
        staff = {"doctors": db["doctors"].count_documents({**doctor_q, "active": True}),
                 "nurses": auth.users(db).count_documents(people)}

    return {
        "batch_date": latest,
        "hospital": _hospital_name(db, hospital),
        "total_patients": len(rows),
        "bands": bands,
        "high_delta": (bands["High"] - high_before) if high_before is not None else 0,
        "needs_attention": sum(1 for r in rows if r.get("monitoring_status")
                               in ("action_required", "deteriorating")),
        "discharged_30d": sum(1 for r in rows if cutoff and str(r.get("discharge_date") or "") >= cutoff),
        "admissions_on_record": view["risk_registry"].count_documents({}),
        "open_alerts": (view["alerts"].count_documents({"acknowledged": False})
                        + view["clinical_alerts"].count_documents({"status": {"$in": OPEN_ALERT_STATUSES}})),
        "no_doctor": no_doctor,
        "no_nurse": no_nurse,
        "insurer_mix": insurer_mix,
        "staff": staff,
    }


# ------------------------------------------------------------------ staff
def staff(db, view, user: dict, batch_dates: list) -> dict:
    """Who looks after whom: each doctor and nurse with their patient counts."""
    hospital = access.hospital_of(user)
    people_q = {"status": "active"}
    if hospital:
        people_q["hospital_id"] = hospital

    high = set()
    if batch_dates:
        high = {str(r["patient_id"]) for r in _latest_rows(view, batch_dates[-1]) if _band(r) == HIGH}

    own = list(view["care_actions"].find({}, {"_id": 0, "patient_id": 1, "assigned_doctor_id": 1,
                                             "assigned_nurse_ids": 1}))
    by_doctor: dict = {}
    by_nurse: dict = {}
    for o in own:
        pid = str(o["patient_id"])
        if o.get("assigned_doctor_id"):
            by_doctor.setdefault(o["assigned_doctor_id"], set()).add(pid)
        for n in o.get("assigned_nurse_ids") or []:
            by_nurse.setdefault(str(n), set()).add(pid)

    alerts: dict = {}
    for a in view["clinical_alerts"].find({"status": {"$in": OPEN_ALERT_STATUSES}},
                                          {"_id": 0, "doctor_id": 1}):
        if a.get("doctor_id"):
            alerts[a["doctor_id"]] = alerts.get(a["doctor_id"], 0) + 1

    def counts(pids: set) -> dict:
        return {"patients": len(pids), "high_risk": len(pids & high)}

    registry = {d["email"].lower(): d for d in view["doctors"].find({}, {"_id": 0})
                if d.get("email")}
    doctors = []
    for acc in auth.users(db).find({**people_q, "role": "doctor"}).sort("email", 1):
        reg = registry.pop((acc.get("email") or "").lower(), None)
        entry = {**_person(acc), "hospital_id": acc.get("hospital_id"), "has_account": True,
                 "doctor_id": reg["doctor_id"] if reg else None,
                 "specialty": reg.get("specialty", "") if reg else "",
                 "registered": bool(reg and reg.get("active", True))}
        pids = by_doctor.get(entry["doctor_id"], set()) if reg else set()
        doctors.append({**entry, **counts(pids),
                        "open_alerts": alerts.get(entry["doctor_id"], 0) if reg else 0})
    # Registry entries with no login yet (registered before doctors had
    # accounts). Still assignable and still routed to, so still listed.
    for reg in registry.values():
        if not reg.get("active", True):
            continue
        pids = by_doctor.get(reg["doctor_id"], set())
        doctors.append({"id": None, "name": reg.get("name", ""), "email": reg.get("email", ""),
                        "hospital_id": reg.get("hospital_id"), "has_account": False,
                        "doctor_id": reg["doctor_id"], "specialty": reg.get("specialty", ""),
                        "registered": True, **counts(pids),
                        "open_alerts": alerts.get(reg["doctor_id"], 0)})

    nurses = []
    for acc in auth.users(db).find({**people_q, "role": "nurse"}).sort("email", 1):
        nurses.append({**_person(acc), "hospital_id": acc.get("hospital_id"),
                       **counts(by_nurse.get(str(acc["_id"]), set()))})

    assigned_doctor = {pid for pids in by_doctor.values() for pid in pids}
    assigned_nurse = {pid for pids in by_nurse.values() for pid in pids}
    all_pids = {str(o["patient_id"]) for o in own}
    return {
        "hospital": _hospital_name(db, hospital),
        "doctors": doctors,
        "nurses": nurses,
        "no_doctor": len(all_pids - assigned_doctor),
        "no_nurse": len(all_pids - assigned_nurse),
        "specialties": sorted(set(doctor_service.SPECIALTY_FOR_GROUP.values())),
    }
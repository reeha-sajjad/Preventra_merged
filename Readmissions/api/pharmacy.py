"""
A pharmacy's home page in Readmissions: the discharged patients who fill at
this pharmacy (their scope - api/access.py), what a pharmacy needs about each,
and its refill-problem reports (api/followups.report). The twin of GLP-1's
GLP1/Backend/core/pharmacy.py.

What a pharmacy sees is the overview layer - readmission risk, discharge date,
hospital - plus the one dispensing fact Readmissions already records, which the
pharmacy itself supplies: whether each week's refill was collected
(weekly_monitoring.monitoring.refill_status, from the pharmacy feed). Never
diagnoses, drivers, vitals, notes or costs (45 CFR 164.502(b), minimum
necessary). Opening one patient's page is logged under DISPENSING_REASON
(api/main.get_patient_summary).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import HTTPException
from pymongo import DESCENDING

from api import access, auth, followups

RECENT_DAYS = 30            # how long a finished follow-up shows beside the patient

HIGH = "High"
RECENT_WEEKS = 4            # "missed refills" counts the last four monitoring weeks
REFILL_LABELS = {"missed": "Missed", "late": "Late", "collected": "Collected", "not due": "Not due"}


def refill_history(db, patient_ids: list) -> dict:
    """patient_id -> {"latest": status, "week": n, "missed_recent": count} from
    the weekly monitoring the pharmacy feed fills in. Weeks with no contact are
    carried forward, not new facts, so only observed weeks count."""
    out: dict = {}
    rows = db["weekly_monitoring"].find(
        {"patient_id": {"$in": access.id_variants(patient_ids)}, "week_number": {"$gt": 0},
         "observed": {"$ne": False}, "monitoring.refill_status": {"$exists": True}},
        {"_id": 0, "patient_id": 1, "week_number": 1, "monitoring.refill_status": 1}
    ).sort([("patient_id", 1), ("week_number", DESCENDING)])
    for r in rows:
        pid = str(r["patient_id"])
        status = (r.get("monitoring") or {}).get("refill_status")
        entry = out.setdefault(pid, {"latest": status, "week": r.get("week_number"), "missed_recent": 0,
                                     "_seen": 0})
        if entry["_seen"] < RECENT_WEEKS:
            entry["missed_recent"] += status == "missed"
            entry["_seen"] += 1
    for entry in out.values():
        entry.pop("_seen", None)
    return out


def home(db, user: dict, scope: Optional[list]) -> dict:
    if user["role"] != "pharmacy":
        raise HTTPException(status_code=403, detail="This page is for pharmacies")
    ids = [str(p) for p in (scope or [])]
    risk = followups._risk(db, ids)
    discharge = {}
    for row in db["patient_worklist"].find({"patient_id": {"$in": access.id_variants(ids)}},
                                           {"_id": 0, "patient_id": 1, "batch_date": 1, "discharge_date": 1}
                                           ).sort("batch_date", DESCENDING):
        discharge.setdefault(str(row["patient_id"]), row.get("discharge_date"))
    owners = {str(o["patient_id"]): o for o in db["care_actions"].find(
        {"patient_id": {"$in": access.id_variants(ids)}}, {"_id": 0, "patient_id": 1, "hospital_id": 1})}
    identity = db.client[auth.IDENTITY_DB]
    hospitals = {h["_id"]: h.get("name", h["_id"]) for h in identity["hospitals"].find(
        {"_id": {"$in": list({o.get("hospital_id") for o in owners.values() if o.get("hospital_id")})}},
        {"name": 1})}
    refills = refill_history(db, ids)
    open_for = {str(d["patient_id"]): followups.pharmacy_view(d, user)
                for d in db[followups.COLLECTION].find({"active_patient": {"$in": ids}})}
    reports = followups.pharmacy_reports(db, user)
    # The latest finished follow-up of the last RECENT_DAYS, so the pharmacy sees
    # beside each patient that it was dealt with (the outcome only, never notes)
    # and does not report the same thing again. GLP-1 does the same.
    since = datetime.now(timezone.utc) - timedelta(days=RECENT_DAYS)
    last_done = {}
    for d in db[followups.COLLECTION].find({"patient_id": {"$in": access.id_variants(ids)}, "status": "done",
                                            "done_at": {"$gte": since}},
                                           {"patient_id": 1, "outcome": 1, "done_at": 1}).sort("done_at", 1):
        at = d["done_at"] if d["done_at"].tzinfo else d["done_at"].replace(tzinfo=timezone.utc)
        last_done[str(d["patient_id"])] = {"outcome_label": followups.OUTCOMES.get(d.get("outcome"), "Done"),
                                           "done_at": at.isoformat()}

    patients = []
    for pid in ids:
        if pid not in risk:
            continue                     # no worklist row yet: nothing to show
        refill = refills.get(pid, {})
        patients.append({
            "id": pid, "risk_score": round(risk[pid]["score"], 1), "risk_band": risk[pid]["band"],
            "discharge_date": discharge.get(pid),
            "hospital": hospitals.get((owners.get(pid) or {}).get("hospital_id")),
            "refill_latest": refill.get("latest"), "refill_week": refill.get("week"),
            "missed_recent": refill.get("missed_recent", 0),
            "open_followup": open_for.get(pid),
            "last_done": None if pid in open_for else last_done.get(pid),
        })
    patients.sort(key=lambda p: (-p["missed_recent"], -p["risk_score"], p["id"]))
    return {
        "pharmacy": ({"id": user["pharmacy_id"], "name": followups.pharmacy_name(db, user)}
                     if user.get("pharmacy_id") else None),
        "stats": {
            "patients": len(patients),
            "high": sum(str(p["risk_band"]).capitalize() == HIGH for p in patients),
            "missed": sum(p["refill_latest"] == "missed" for p in patients),
            "open_reports": sum(r["status"] != "done" for r in reports),
        },
        "patients": patients,
        "reports": reports,
        "refill_labels": REFILL_LABELS,
        "problems": [{"key": k, "label": v} for k, v in followups.PHARMACY_PROBLEMS.items()],
    }


def refill_facts(db, patient_id: str) -> dict:
    """For the patient page: the latest refill and missed ones lately."""
    r = refill_history(db, [patient_id]).get(str(patient_id), {})
    return {"refill_latest": r.get("latest"), "refill_week": r.get("week"),
            "missed_recent": r.get("missed_recent", 0)}
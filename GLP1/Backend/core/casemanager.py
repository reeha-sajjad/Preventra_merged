"""
"Today": the case manager's home page. What their hospital looks like on GLP-1
therapy, and what needs doing - follow-ups, gaps in care teams, and high-risk
patients nobody is following up.

Everything is limited to the caller's hospital (their patient scope, which for
a case manager is the whole hospital - core/access.py). Counts and patient
numbers, risk and care team: the same layers a case manager already sees on the
patient list. Nothing here writes; actions happen on the pages it links to.

`mine`: the same page for the caller's own caseload only - the patients they
are the main case manager of (core/hospital.py). Follow-ups then show their
patients' requests and those of patients with no case manager yet.
"""
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import HTTPException

from core import followups
from core.access import doctor_ids, hospital_of, scope_query
from core.hospital import _hospital_name, care_filter, care_teams
from core.mongo import get_db

CRITICAL = 0.75
HIGH = 0.50
SHOWN = 8


def _aware(at: datetime) -> datetime:
    return at if at.tzinfo else at.replace(tzinfo=timezone.utc)


async def today(user: dict, scope: Optional[list], mine: bool = False) -> dict:
    if user["role"] != "case_manager":
        raise HTTPException(status_code=403, detail="This page is for case managers")
    db = get_db()
    hospital = hospital_of(user)
    out = {"hospital": await _hospital_name(hospital) if hospital else None}

    caseload = await care_filter(scope, case_manager=user["id"])
    out["caseload"] = {"patients": len(caseload), "mine": mine}
    if mine:
        scope = caseload                  # everything below: my patients only
    rows = await db.patients.find(scope_query(scope), {
        "_id": 0, "patient_idx": 1, "dropout_prob": 1, "prediction": 1, "is_adherent": 1,
        "driver_1": 1, "assigned_molecule": 1}).to_list(length=None)
    risk = {int(r["patient_idx"]): float(r.get("dropout_prob") or 0) for r in rows}
    total = len(rows)
    out["stats"] = {
        "patients": total,
        "critical": sum(p >= CRITICAL for p in risk.values()),
        "high": sum(HIGH <= p < CRITICAL for p in risk.values()),
        "predicted_dropout": sum(r.get("prediction") == "Dropout Risk" for r in rows),
        "adherent": sum(int(r.get("is_adherent") or 0) == 1 for r in rows),
    }

    # ---- follow-ups: the hospital's queue, and the caller's own
    col = get_db()[followups.COLLECTION]
    base = {"hospital_id": hospital} if hospital else {"hospital_id": "__none__"}
    active = await col.find({**base, "status": {"$in": ["open", "in_progress"]}}).to_list(length=None)
    if mine:
        # My patients' requests, mine in hand, and those of patients nobody owns.
        owner = await followups.case_managers_of([f["patient_idx"] for f in active])
        active = [f for f in active if owner.get(int(f["patient_idx"])) in (user["id"], None)
                  or (f.get("taken_by") or {}).get("id") == user["id"]]
    now = datetime.now(timezone.utc)
    overdue = [f for f in active if now - _aware(f["requested_at"]) > timedelta(days=followups.OVERDUE_DAYS)]
    out["followups"] = {
        "waiting": sum(f["status"] == "open" for f in active),
        "urgent_waiting": sum(f["status"] == "open" and f["urgency"] == "urgent" for f in active),
        "mine": sum((f.get("taken_by") or {}).get("id") == user["id"] for f in active),
        "in_progress": sum(f["status"] == "in_progress" for f in active),
        "overdue": len(overdue),
    }
    # The next few to pick up: untaken, urgent first, then the longest waiting.
    waiting = sorted((f for f in active if f["status"] == "open"),
                     key=lambda f: (f["urgency"] != "urgent", _aware(f["requested_at"])))
    # Risk for the shown requests' patients outside the rows above (in "mine",
    # patients nobody owns yet).
    missing = [int(f["patient_idx"]) for f in waiting[:5] if int(f["patient_idx"]) not in risk]
    if missing:
        risk.update(await followups._risk(missing))
    out["followups"]["next"] = [
        {"id": str(f["_id"]), "patient_idx": f["patient_idx"], "urgency": f["urgency"],
         "requested_by": f["requested_by"]["name"], "requested_at": _aware(f["requested_at"]).isoformat(),
         "note": f.get("note", ""), "dropout_prob": risk.get(int(f["patient_idx"])),
         "overdue": now - _aware(f["requested_at"]) > timedelta(days=followups.OVERDUE_DAYS)}
        for f in waiting[:5]]
    since = now - timedelta(days=followups.SHOW_DONE_DAYS)
    recent_done = await col.find({**base, "status": "done", "done_at": {"$gte": since}},
                                 {"patient_idx": 1}).to_list(length=None)
    followed = {int(f["patient_idx"]) for f in active} | {int(f["patient_idx"]) for f in recent_done}

    # ---- care-team gaps
    access = {int(d["patient_idx"]): d for d in await db.patient_access.find(
        scope_query(scope), {"_id": 0, "patient_idx": 1, "assigned_doctor_id": 1,
                             "assigned_doctor_ids": 1, "assigned_nurse_ids": 1,
                             "case_manager_id": 1}).to_list(length=None)}
    by_idx_set = {int(r["patient_idx"]) for r in rows}
    no_doctor = [i for i in by_idx_set if not doctor_ids(access.get(i, {}))]
    no_nurse = [i for i in by_idx_set if not (access.get(i, {}).get("assigned_nurse_ids") or [])]
    gaps = sorted(set(no_doctor) | set(no_nurse), key=lambda i: (-risk[i], i))
    no_cm = sum(1 for i in risk if i in by_idx_set and not access.get(i, {}).get("case_manager_id"))
    out["gaps"] = {"no_doctor": len(no_doctor), "no_nurse": len(no_nurse), "total": len(gaps),
                   "no_case_manager": no_cm,
                   "patients": [{"patient_idx": i, "dropout_prob": risk[i],
                                 "missing": [m for m, ids in (("doctor", no_doctor), ("nurse", no_nurse))
                                             if i in ids]}
                                for i in gaps[:SHOWN]]}

    # ---- high risk, and nobody is following them up
    by_idx = {int(r["patient_idx"]): r for r in rows}
    unattended = sorted((i for i in by_idx_set if risk[i] >= CRITICAL and i not in followed),
                        key=lambda i: (-risk[i], i))
    teams = await care_teams(unattended[:SHOWN])
    out["unattended"] = {"total": len(unattended), "patients": [
        {"patient_idx": i, "dropout_prob": risk[i], "main_reason": by_idx[i].get("driver_1") or "",
         "drug": by_idx[i].get("assigned_molecule"),
         "doctors": [d["name"] for d in teams.get(i, {}).get("doctors", [])],
         "nurses": [n["name"] for n in teams.get(i, {}).get("nurses", [])]}
        for i in unattended[:SHOWN]]}
    return out
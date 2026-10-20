"""
A caretaker's "My people" page and bell, and the case manager's "no medicines
logged" alert. The twin of GLP-1's GLP1/Backend/core/caretaker_home.py - same
rules, same words, with Readmissions' daily "medicines given" tick
(api/medicines_given.py) in place of GLP-1's weekly dose.

My people (caretaker): one card per patient who linked them - name, current
readmission risk (score and band: a carer helps manage the recovery, so they see
the care team's number, not only the patient's words), why they were in
hospital, when they went home, when medicines were last logged and whether
today's are, and anything that needs the carer.

The caretaker's bell:
    refill_problem   the pharmacy reported a missed refill pickup or an
                     out-of-stock medicine and the case manager's request is
                     still open. Only these two: cost and side effects are the
                     patient's own business (45 CFR 164.510(b)).
    dose_due         today's medicines are not logged yet (for someone whose
                     carer logs them). Leaves when they are.
    booked           the case manager booked something (a follow-up closed as
                     Booked) in the last BOOKED_DAYS.

The case manager's bell (dose_missed): a patient with a caretaker whose
medicines were last logged MISSED_AFTER_DAYS to STALE_DAYS ago.

Reads the worklist, care_actions, medicines_given and follow-ups; writes only
caretaker_seen. The dataset is never written to.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import HTTPException
from pymongo import ASCENDING

from api import access, followups
from api.db_utils import get_latest_batch_date
from api.patient_home import _patient_names, _tidy, status_of

REFILL_PROBLEMS = ("missed_pickup", "out_of_stock")
BOOKED_DAYS = 14
MISSED_AFTER_DAYS = 3          # a daily tick, three days without one
STALE_DAYS = 30
SEEN = "caretaker_seen"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _aware(at: datetime) -> datetime:
    return at if at.tzinfo else at.replace(tzinfo=timezone.utc)


def _last_ticks(db, ids: list) -> dict:
    out = {}
    for d in db["medicines_given"].find({"patient_id": {"$in": access.id_variants(ids)}, "undone": {"$exists": False}},
                                        {"patient_id": 1, "given_at": 1, "logged_by": 1}).sort("given_at", ASCENDING):
        out[str(d["patient_id"])] = {"at": _aware(d["given_at"]), "by": d["logged_by"]["name"]}
    return out


def _refill_problems(db, ids: list) -> dict:
    out = {}
    for f in db[followups.COLLECTION].find({"active_patient": {"$in": access.id_variants(ids)},
                                            "pharmacy_reports.problem": {"$in": list(REFILL_PROBLEMS)}}):
        reports = [r for r in f.get("pharmacy_reports", []) if r.get("problem") in REFILL_PROBLEMS]
        last = max(reports, key=lambda r: _aware(r["at"]))
        out[str(f["patient_id"])] = {"at": _aware(last["at"]), "label": followups.PHARMACY_PROBLEMS[last["problem"]]}
    return out


def _booked(db, ids: list) -> dict:
    since = _now() - timedelta(days=BOOKED_DAYS)
    out = {}
    for f in db[followups.COLLECTION].find({"patient_id": {"$in": access.id_variants(ids)}, "status": "done",
                                            "outcome": "booked", "done_at": {"$gte": since}}).sort("done_at", ASCENDING):
        out[str(f["patient_id"])] = {"at": _aware(f["done_at"])}
    return out


def people(db, user: dict, scope: Optional[list]) -> dict:
    """A caretaker's people, most in need first."""
    if user["role"] != "caretaker":
        raise HTTPException(status_code=403, detail="This page is for caretakers")
    ids = [str(i) for i in (scope or [])]
    if not ids:
        return {"people": []}
    batch = get_latest_batch_date(db, "patient_worklist")
    rows = {str(r["patient_id"]): r for r in db["patient_worklist"].find(
        {"patient_id": {"$in": access.id_variants(ids)}, "batch_date": batch},
        {"_id": 0, "patient_id": 1, "current_score": 1, "risk_score": 1, "current_band": 1, "risk_band": 1,
         "primary_diagnosis": 1, "discharge_date": 1})}
    names = _patient_names(db, ids)
    last, refills, booked = _last_ticks(db, ids), _refill_problems(db, ids), _booked(db, ids)
    today = _now().date()
    out = []
    for pid in ids:
        r = rows.get(pid, {})
        band = str(r.get("current_band") or r.get("risk_band") or "").title() or None
        try:
            score = float(r.get("current_score", r.get("risk_score", 0)) or 0)
        except (TypeError, ValueError):
            score = 0.0
        tick = last.get(pid)
        logged_today = bool(tick and tick["at"].date() == today)
        alerts = []
        if pid in refills:
            alerts.append({"kind": "refill_problem", "text": f"Pharmacy: {refills[pid]['label']}"})
        if tick and not logged_today:
            alerts.append({"kind": "dose_due", "text": "Today's medicines not logged yet"})
        if pid in booked:
            alerts.append({"kind": "booked", "text": "Case manager booked an appointment"})
        out.append({"patient_id": pid, "name": names[pid], "risk_score": round(score, 1), "band": band,
                    "status": status_of(band)["title"], "reason": _tidy(r.get("primary_diagnosis")),
                    "discharged": r.get("discharge_date") or None,
                    "last_given": {"at": tick["at"].isoformat(), "by": tick["by"]} if tick else None,
                    "logged_today": logged_today, "alerts": alerts})
    out.sort(key=lambda p: (-len(p["alerts"]), -p["risk_score"]))
    return {"people": out}


def bell(db, user: dict, scope: Optional[list]) -> list:
    """The caretaker's bell items (kind per item, as the Readmissions bell expects)."""
    data = people(db, user, scope)
    seen = db[SEEN].find_one({"user_id": str(user["_id"])}) or {}
    seen_at = _aware(seen["at"]) if seen.get("at") else None
    ids = [p["patient_id"] for p in data["people"]]
    refills, booked = _refill_problems(db, ids), _booked(db, ids)
    items = []
    for p in data["people"]:
        pid, r, b = p["patient_id"], refills.get(p["patient_id"]), booked.get(p["patient_id"])
        base = {"patient_id": pid, "patient_name": p["name"]}
        if r and (seen_at is None or r["at"] > seen_at):
            items.append({**base, "kind": "refill_problem", "label": r["label"]})
        if p["last_given"] and not p["logged_today"]:
            items.append({**base, "kind": "dose_due"})
        if b and (seen_at is None or b["at"] > seen_at):
            items.append({**base, "kind": "booked"})
    return items


def mark_seen(db, user: dict) -> dict:
    if user["role"] != "caretaker":
        raise HTTPException(status_code=403, detail="For caretakers")
    db[SEEN].update_one({"user_id": str(user["_id"])}, {"$set": {"at": _now()}}, upsert=True)
    return {"ok": True}


def dose_missed_items(db, scope: Optional[list]) -> list:
    """For a case manager's bell: patients with a caretaker whose medicines were
    last logged MISSED_AFTER_DAYS to STALE_DAYS ago."""
    query: dict = {"caretaker_ids.0": {"$exists": True}}
    if scope is not None:
        query["patient_id"] = {"$in": access.id_variants([str(i) for i in scope])}
    ids = [str(o["patient_id"]) for o in db["care_actions"].find(query, {"patient_id": 1})]
    if not ids:
        return []
    now = _now()
    items = []
    for pid, t in _last_ticks(db, ids).items():
        days = (now - t["at"]).days
        if MISSED_AFTER_DAYS <= days <= STALE_DAYS:
            items.append({"kind": "dose_missed", "patient_id": pid, "days": days, "by": t["by"]})
    return sorted(items, key=lambda i: -i["days"])
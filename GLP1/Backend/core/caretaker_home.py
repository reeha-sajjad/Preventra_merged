"""
A caretaker's "My people" page and bell, and the case manager's "no dose
logged" alert. Readmissions has the same in api/caretaker_home.py.

My people (caretaker): one card per patient who linked them - the patient's
name, their risk of stopping (the percentage and its band: a caretaker helps
manage the treatment, so they see the care team's number, not only the words
the patient sees), the medicine and how often it is taken, the last dose they
or another carer logged and when the next is due, and anything that needs them.

The caretaker's bell:
    refill_problem   the pharmacy reported a missed refill pickup or an
                     out-of-stock medicine, and the case manager's request is
                     still open. Only these two: cost and side effects are the
                     patient's own business (45 CFR 164.510(b) - what is
                     relevant to the carer's part in the care).
    dose_due         a dose is due today or overdue (from the dose log and how
                     often the medicine is taken). Leaves when a dose is logged.
    booked           the case manager booked something for the patient (a
                     follow-up closed as Booked) in the last BOOKED_DAYS.
"Mark all as read" hides refill_problem and booked up to that moment; dose_due
stays while it is true.

The case manager's bell (dose_missed): a patient with a caretaker whose last
logged dose was MISSED_AFTER_DAYS to STALE_DAYS ago - the carer may have
stopped logging, or the patient stopped. One alert while it lasts, never one
per tick.

Reads glp1_analytics (patients, patient_access, dose_log, follow-ups); writes
only caretaker_seen. The dataset is never written to.
"""
from datetime import datetime, timedelta, timezone
from typing import Optional

from core import followups, zoe
from core.mongo import get_db
from core.patient_home import SCHEDULE, _patient_names, status_of

CRITICAL, HIGH, MEDIUM = 0.75, 0.50, 0.25
REFILL_PROBLEMS = ("missed_pickup", "out_of_stock")
BOOKED_DAYS = 14
MISSED_AFTER_DAYS = 9          # a weekly dose, two days late
STALE_DAYS = 30                # after that it is not news any more
EVERY = {"weekly": timedelta(days=7), "daily": timedelta(days=1)}
SEEN = "caretaker_seen"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _aware(at: datetime) -> datetime:
    return at if at.tzinfo else at.replace(tzinfo=timezone.utc)


def band(prob: float) -> str:
    return "Critical" if prob >= CRITICAL else "High" if prob >= HIGH else "Medium" if prob >= MEDIUM else "Low"


async def _last_doses(idxs: list) -> dict:
    out = {}
    async for d in get_db()["dose_log"].find({"patient_idx": {"$in": idxs}, "undone": {"$exists": False}},
                                             {"patient_idx": 1, "given_at": 1, "logged_by": 1}).sort("given_at", 1):
        out[int(d["patient_idx"])] = {"at": _aware(d["given_at"]), "by": d["logged_by"]["name"]}
    return out


async def _refill_problems(idxs: list) -> dict:
    """patient_idx -> the latest refill-type pharmacy report on an open request."""
    out = {}
    async for f in get_db()[followups.COLLECTION].find({"active_patient": {"$in": idxs},
                                                        "pharmacy_reports.problem": {"$in": list(REFILL_PROBLEMS)}}):
        reports = [r for r in f.get("pharmacy_reports", []) if r.get("problem") in REFILL_PROBLEMS]
        last = max(reports, key=lambda r: _aware(r["at"]))
        out[int(f["patient_idx"])] = {"at": _aware(last["at"]), "problem": last["problem"],
                                      "label": followups.PHARMACY_PROBLEMS[last["problem"]],
                                      "pharmacy": last.get("pharmacy") or (last.get("by") or {}).get("pharmacy")}
    return out


async def _booked(idxs: list) -> dict:
    out = {}
    since = _now() - timedelta(days=BOOKED_DAYS)
    async for f in get_db()[followups.COLLECTION].find({"patient_idx": {"$in": idxs}, "status": "done",
                                                        "outcome": "booked", "done_at": {"$gte": since}}).sort("done_at", 1):
        out[int(f["patient_idx"])] = {"at": _aware(f["done_at"])}
    return out


def _due(last: Optional[dict], frequency: Optional[str]) -> Optional[dict]:
    """When the next dose is due, and whether it is due now or overdue."""
    if frequency not in EVERY or not last:
        return None
    due = last["at"] + EVERY[frequency]
    today = _now().date()
    state = "overdue" if due.date() < today else "today" if due.date() == today else "later"
    return {"at": due.isoformat(), "state": state}


async def people(user: dict, scope: Optional[list]) -> dict:
    """A caretaker's people, most in need first."""
    if user["role"] != "caretaker":
        from fastapi import HTTPException
        raise HTTPException(status_code=403, detail="This page is for caretakers")
    idxs = [int(i) for i in (scope or [])]
    if not idxs:
        return {"people": []}
    rows = {int(r["patient_idx"]): r for r in await get_db().patients.find(
        {"patient_idx": {"$in": idxs}}, {"_id": 0, "patient_idx": 1, "dropout_prob": 1,
                                         "assigned_molecule": 1}).to_list(length=None)}
    names = await _patient_names(idxs)
    last, refills, booked = await _last_doses(idxs), await _refill_problems(idxs), await _booked(idxs)
    out = []
    for idx in idxs:
        r = rows.get(idx, {})
        prob = float(r.get("dropout_prob") or 0)
        frequency = await zoe.frequency(user, idx)
        drug = str(r.get("assigned_molecule") or "").upper()
        due = _due(last.get(idx), frequency)
        alerts = []
        if idx in refills:
            alerts.append({"kind": "refill_problem", "text": f"Pharmacy: {refills[idx]['label']}"})
        if due and due["state"] in ("today", "overdue"):
            alerts.append({"kind": "dose_due", "text": "Dose overdue" if due["state"] == "overdue" else "Dose due today"})
        if idx in booked:
            alerts.append({"kind": "booked", "text": "Case manager booked an appointment"})
        out.append({"patient_idx": idx, "name": names[idx], "dropout_prob": round(prob, 4), "band": band(prob),
                    "status": status_of(prob)["title"],
                    "drug": drug.title() if drug else None, "schedule": SCHEDULE.get(drug),
                    "last_dose": ({"at": last[idx]["at"].isoformat(), "by": last[idx]["by"]} if idx in last else None),
                    "next_due": due, "alerts": alerts})
    out.sort(key=lambda p: (-len(p["alerts"]), -p["dropout_prob"]))
    return {"people": out}


async def bell_items(user: dict, scope: Optional[list]) -> list:
    """For core/notifications.py: the caretaker's bell."""
    data = await people(user, scope)
    seen = await get_db()[SEEN].find_one({"user_id": user["id"]}) or {}
    seen_at = _aware(seen["at"]) if seen.get("at") else None
    idxs = [p["patient_idx"] for p in data["people"]]
    refills, booked = await _refill_problems(idxs), await _booked(idxs)
    items = []
    for p in data["people"]:
        idx, kinds = p["patient_idx"], []
        r = refills.get(idx)
        if r and (seen_at is None or r["at"] > seen_at):
            kinds.append("refill_problem")
        if p["next_due"] and p["next_due"]["state"] in ("today", "overdue"):
            kinds.append("dose_due")
        b = booked.get(idx)
        if b and (seen_at is None or b["at"] > seen_at):
            kinds.append("booked")
        if kinds:
            items.append({"patient_idx": idx, "patient_name": p["name"], "kinds": kinds,
                          "dropout_prob": p["dropout_prob"], "urgent": "refill_problem" in kinds,
                          "refill_label": r["label"] if r else None,
                          "dose_state": p["next_due"]["state"] if p["next_due"] else None})
    return items


async def mark_seen(user: dict) -> dict:
    if user["role"] != "caretaker":
        from fastapi import HTTPException
        raise HTTPException(status_code=403, detail="For caretakers")
    await get_db()[SEEN].update_one({"user_id": user["id"]}, {"$set": {"at": _now()}}, upsert=True)
    return {"ok": True}


async def dose_missed_items(scope: Optional[list]) -> list:
    """For a case manager's bell: patients with a caretaker whose last logged
    dose was MISSED_AFTER_DAYS to STALE_DAYS ago."""
    query = {"caretaker_ids.0": {"$exists": True}}
    if scope is not None:
        query["patient_idx"] = {"$in": [int(i) for i in scope]}
    idxs = [int(a["patient_idx"]) for a in await get_db().patient_access.find(
        query, {"_id": 0, "patient_idx": 1}).to_list(length=None)]
    if not idxs:
        return []
    last = await _last_doses(idxs)
    now = _now()
    items = []
    for idx, d in last.items():
        days = (now - d["at"]).days
        if MISSED_AFTER_DAYS <= days <= STALE_DAYS:
            items.append({"patient_idx": idx, "kinds": ["dose_missed"], "days_since_dose": days,
                          "last_dose_by": d["by"], "dropout_prob": None, "urgent": False})
    probs = {int(r["patient_idx"]): float(r.get("dropout_prob") or 0) for r in await get_db().patients.find(
        {"patient_idx": {"$in": [i["patient_idx"] for i in items]}}, {"patient_idx": 1, "dropout_prob": 1}
    ).to_list(length=None)}
    for i in items:
        i["dropout_prob"] = probs.get(i["patient_idx"])
    return sorted(items, key=lambda i: -i["days_since_dose"])
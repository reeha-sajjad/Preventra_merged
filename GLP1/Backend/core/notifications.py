"""
The bell for doctors and hospital nurses: which of their own patients need a look.

Three reasons, worked out on every call from the patients the caller may see
(core/access.patient_scope - exactly as for the patient list), so nothing here
can mention anyone else's patient:

    new         added to the caller's care team since they last looked at the
                patient (core/hospital.assign stamps care_team_added_at.<id>)
    unreviewed  critical risk (75% or more) and never looked at by the caller
    risk_up     risk 10 points or more higher, or in a higher band, than when
                the caller last looked. GLP-1 has one score per patient, so this
                fires only after the scores change (a retrained model or new
                data); until then it stays quiet.

"Looked at" is stored per person and patient in SEEN_COLLECTION, with the risk
at that moment: opening the patient (core/access.require_detail) or dismissing
the alert updates it, so a patient leaves the bell and comes back only if their
risk rises again. Readmissions' bell (api/risk_watch.py) works the same way on
its weekly scores.

Only counts and patient numbers leave this module - no names, and nothing is
sent outside the app (no email or text), so no patient detail travels anywhere
the access controls do not reach.
"""

from datetime import datetime, timezone
from typing import Iterable, Optional

from pymongo import UpdateOne

from core.access import CARE_TEAM_ROLES, scope_query
from core.mongo import get_db

SEEN_COLLECTION = "notification_seen"
CRITICAL = 0.75
RISE = 0.10                     # 10 percentage points
BANDS = (0.25, 0.50, 0.75)      # the same bands as the Patients page
KIND_ORDER = {"risk_up": 0, "new": 1, "unreviewed": 2}
MAX_ITEMS = 50


def band(prob: float) -> int:
    return sum(prob >= b for b in BANDS)


def _naive(at: Optional[datetime]) -> Optional[datetime]:
    """Compare times the same way whether or not the driver attached a timezone."""
    if at is None:
        return None
    return at.astimezone(timezone.utc).replace(tzinfo=None) if at.tzinfo else at


def _assess(prob: float, seen: Optional[dict], added_at: Optional[datetime]) -> list:
    kinds = []
    seen_at = _naive((seen or {}).get("at"))
    added_at = _naive(added_at)
    if added_at and (seen_at is None or seen_at < added_at):
        kinds.append("new")
    if seen is None and prob >= CRITICAL:
        kinds.append("unreviewed")
    if seen is not None:
        before = float(seen.get("dropout_prob") or 0)
        if prob - before >= RISE or band(prob) > band(before):
            kinds.append("risk_up")
    return kinds


async def alerts(user: dict, scope: Optional[list]) -> list:
    """Every alert the caller has, most serious first."""
    if user["role"] not in CARE_TEAM_ROLES:
        return []
    db = get_db()
    mine = scope_query(scope)
    patients = await db.patients.find(mine, {"_id": 0, "patient_idx": 1, "dropout_prob": 1,
                                             "driver_1": 1}).to_list(length=None)
    added = {d["patient_idx"]: (d.get("care_team_added_at") or {}).get(user["id"])
             for d in await db.patient_access.find(
                 mine, {"_id": 0, "patient_idx": 1, "care_team_added_at": 1}).to_list(length=None)}
    seen = {d["patient_idx"]: d for d in await db[SEEN_COLLECTION].find(
        {"user_id": user["id"]}, {"_id": 0}).to_list(length=None)}

    items = []
    for p in patients:
        idx, prob = int(p["patient_idx"]), float(p.get("dropout_prob") or 0)
        kinds = _assess(prob, seen.get(idx), added.get(idx))
        if not kinds:
            continue
        before = seen.get(idx, {}).get("dropout_prob")
        items.append({"patient_idx": idx, "dropout_prob": prob, "main_reason": p.get("driver_1"),
                      "kinds": kinds, "previous_prob": before if "risk_up" in kinds else None,
                      "urgent": prob >= CRITICAL})
    items.sort(key=lambda i: (min(KIND_ORDER[k] for k in i["kinds"]), -i["dropout_prob"]))
    return items


async def bell(user: dict, scope: Optional[list], limit: int = 20) -> dict:
    """The first `limit` alerts, the total, and how many of each kind."""
    items = await alerts(user, scope)
    counts = {k: sum(k in i["kinds"] for i in items) for k in KIND_ORDER}
    return {"total": len(items), "counts": counts,
            "items": items[:max(1, min(int(limit), MAX_ITEMS))]}


async def mark_seen(user: dict, patient_idxs: Iterable[int]) -> int:
    """The caller has looked at these patients: remember when, and their risk then."""
    idxs = sorted({int(i) for i in patient_idxs})
    if not idxs:
        return 0
    db = get_db()
    risk = {d["patient_idx"]: float(d.get("dropout_prob") or 0) for d in await db.patients.find(
        {"patient_idx": {"$in": idxs}}, {"_id": 0, "patient_idx": 1, "dropout_prob": 1}).to_list(length=None)}
    now = datetime.now(timezone.utc)
    ops = [UpdateOne({"user_id": user["id"], "patient_idx": i},
                     {"$set": {"dropout_prob": risk.get(i, 0.0), "at": now}}, upsert=True)
           for i in idxs]
    await db[SEEN_COLLECTION].bulk_write(ops, ordered=False)
    return len(ops)
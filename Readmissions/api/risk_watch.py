"""
Which of the caller's patients got worse in their latest monitoring week.

This is what a doctor's bell and the "Needs attention" panel show: their own
patients - access.patient_scope decides whose, exactly as for the patient list -
whose latest weekly score

    carries a red flag       saturation below 90% and the rest of
                             models/monitoring_rules.RED_FLAGS
    rose RISE_POINTS or more the trend panel's noise band: a smaller move is
                             not distinguishable from week-to-week noise
    moved up a risk band     Low -> Medium, Medium -> High

or whose latest weeks went without contact after a Medium or High week (or a
red flag) - the score is only held then, so no rise can fire, and that is when
someone should be calling (models/monitoring_rules.contact_gap). Silence is
counted by the calendar too: more than a week and two days without readings,
inside the 30-day window - including a Medium or High discharge never heard
from since.

A red flag, a rise that ends in High, or silence after High is "urgent";
anything else "rising".
Red flags first, then other urgent rises, then the rest; biggest rise first.

Computed from weekly_monitoring on every call rather than stored as alerts:
the weekly series is regenerated as a whole (scripts/simulate_weekly_monitoring
.py), and a stored alert would then describe weeks that no longer exist. What
IS stored is who has seen which week (SEEN_COLLECTION), so a patient leaves the
bell once opened and comes back only when a newer week rises again.
"""
from __future__ import annotations

from datetime import datetime, timezone

from models.monitoring_rules import RED_FLAGS, contact_gap, contact_gap_text

RISE_POINTS = 5.0
BAND_RANK = {"Low": 1, "Medium": 2, "High": 3}
SEEN_COLLECTION = "risk_watch_seen"


def _float(value, default=0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def assess(weeks: list, today=None):
    """One patient's weeks, oldest first -> the watchlist entry, or None.
    `today` (the hospital's date) also counts silence by the calendar."""
    if not weeks:
        return None
    latest = weeks[-1]
    prev = weeks[-2] if len(weeks) > 1 else latest
    # Silence after warning signs: the score is only held, so a rise rule can
    # never fire on it - and that is exactly when someone should be calling.
    gap = contact_gap([{"week_number": w.get("week"), "observed": w.get("observed", True),
                        "risk_band": w.get("band"), "red_flags": w.get("flags"),
                        "week_date": w.get("date"), "days_after_discharge": w.get("days")}
                       for w in weeks], today)
    if gap["overdue"]:
        band = gap["last_contact_band"] or "flagged"
        score = _float(latest.get("score"))
        return {
            "severity": "urgent" if band == "High" else "rising",
            "week_number": latest.get("week"),
            "week_date": latest.get("date") or "",
            "risk_score": score, "risk_band": latest.get("band") or "Low",
            "previous_score": score, "previous_band": prev.get("band") or "Low",
            "delta": 0.0, "red_flags": [],
            "reasons": [contact_gap_text(gap) + f" while {band} - contact the patient"],
        }
    if len(weeks) < 2:
        return None
    score, before = _float(latest.get("score")), _float(prev.get("score"))
    delta = round(score - before, 1)
    band, band_before = latest.get("band") or "Low", prev.get("band") or "Low"
    flags = [RED_FLAGS[k][1] for k in (latest.get("flags") or []) if k in RED_FLAGS]
    band_up = BAND_RANK.get(band, 0) > BAND_RANK.get(band_before, 0)
    rose = delta >= RISE_POINTS
    if not (flags or rose or band_up):
        return None

    reasons = [f"Red flag: {f}" for f in flags]
    if rose:
        reasons.append(f"Risk up {delta:.1f} points in a week")
    if band_up:
        reasons.append(f"Moved from {band_before} to {band}")
    return {
        "severity": "urgent" if flags or (rose and band == "High") else "rising",
        "week_number": latest.get("week"),
        "week_date": latest.get("date") or "",
        "risk_score": score, "risk_band": band,
        "previous_score": before, "previous_band": band_before,
        "delta": delta, "red_flags": flags, "reasons": reasons,
    }


def watchlist(view, db, user: dict, limit: int = 50) -> dict:
    """The caller's patients whose latest week needs a look, most serious first."""
    series = view["weekly_monitoring"].aggregate([
        {"$sort": {"week_number": 1}},
        # Defaults spelled out: weeks written before red flags existed have no
        # red_flags field, and a missing field must read as "none", not break
        # the row.
        {"$group": {"_id": "$patient_id", "weeks": {"$push": {
            "week": {"$ifNull": ["$week_number", 0]},
            "score": {"$ifNull": ["$risk_score", 0]},
            "band": {"$ifNull": ["$risk_band", "Low"]},
            "flags": {"$ifNull": ["$red_flags", []]},
            "observed": {"$ifNull": ["$observed", True]},
            "date": {"$ifNull": ["$week_date", ""]},
            "days": {"$ifNull": ["$days_after_discharge", None]}}}}},
    ])
    from ingestion.scheduler import today as hospital_today
    today = hospital_today()
    items = []
    for doc in series:
        weeks = sorted(doc["weeks"], key=lambda w: w.get("week") or 0)
        entry = assess(weeks, today)
        if entry:
            items.append({"patient_id": str(doc["_id"]), **entry})

    uid = str(user["_id"])
    ids = [i["patient_id"] for i in items]
    seen = {(s["patient_id"], s["week_number"]) for s in db[SEEN_COLLECTION].find(
        {"user_id": uid, "patient_id": {"$in": ids}}, {"patient_id": 1, "week_number": 1})}
    for i in items:
        i["seen"] = (i["patient_id"], i["week_number"]) in seen

    # Red flags first - they need assessing today - then other urgent rises,
    # then the rest; the biggest rise first within each.
    items.sort(key=lambda i: (i["severity"] != "urgent", not i["red_flags"], -i["delta"],
                              i["patient_id"]))
    shown = items[:max(1, limit)]

    # What the patient is being treated for, so the list is readable without
    # opening every record.
    diagnoses = {}
    if shown:
        for row in view["patient_worklist"].find(
                {"patient_id": {"$in": [i["patient_id"] for i in shown]}},
                {"_id": 0, "patient_id": 1, "primary_diagnosis": 1, "batch_date": 1}):
            key = str(row["patient_id"])
            if row.get("batch_date", "") >= diagnoses.get(key, ("", ""))[0]:
                diagnoses[key] = (row.get("batch_date", ""), row.get("primary_diagnosis") or "")
    for i in shown:
        i["primary_diagnosis"] = diagnoses.get(i["patient_id"], ("", ""))[1]

    return {
        "total": len(items),
        "urgent": sum(1 for i in items if i["severity"] == "urgent"),
        "unseen": sum(1 for i in items if not i["seen"]),
        "rise_points": RISE_POINTS,
        "patients": shown,
    }


def mark_seen(db, user: dict, patient_id: str, week_number: int) -> None:
    db[SEEN_COLLECTION].update_one(
        {"user_id": str(user["_id"]), "patient_id": str(patient_id), "week_number": week_number},
        {"$set": {"seen_at": datetime.now(timezone.utc).isoformat()}}, upsert=True)

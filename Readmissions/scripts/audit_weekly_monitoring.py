#!/usr/bin/env python3
"""
Check weekly monitoring series for cards that contradict themselves.

Every check here is something a clinician reading the cards caught, or would:
marked ankle swelling beside a stable weight, an appointment "missed" after it
was attended, a refill due two weeks running from a 30-day supply, a red flag
on a week banded Low. They are facts about the DATA, so they are checked on the
data - both the simulator's output before it is written and what is already
stored - rather than trusted to the generator.

    .venv/bin/python scripts/audit_weekly_monitoring.py            # stored series, read-only
    .venv/bin/python scripts/simulate_weekly_monitoring.py --audit  # a fresh run, not written

Exits 1 when any check finds a problem, so it can gate a regeneration.
"""
from __future__ import annotations

import os
import sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# name -> what a failing patient looks like on screen
CHECKS = {
    "swelling_vs_weight":
        "marked ankle swelling with under 1 kg gained, or no swelling with 3 kg or more",
    "pillows_vs_congestion":
        "3+ pillows to sleep with no weight gain and no swelling",
    "followup_sequence":
        "an appointment attended twice, missed after it was attended, or dropped after a miss",
    "refill_schedule":
        "a refill due in week 1, or due two weeks running",
    "antibiotic_sequence":
        "an antibiotic course that restarts after it was completed or stopped",
    "red_flag_low_band":
        "a red flag (assess today) on a week banded Low",
    "recovering_red_flag":
        "a red flag in a patient simulated as recovering",
    "silent_week_red_flag":
        "a week with no contact that still raises a red flag",
    "confusion_without_cause":
        "new confusion in a recovering patient without a fever",
}
# Reported, not failed: true of a real programme too, and explained on the card.
INFO = {
    "rise_after_silence":
        "a rise of 20+ points at the first contact after one or more silent weeks",
}


def _series(docs) -> dict:
    by_patient = defaultdict(list)
    for d in docs:
        by_patient[str(d.get("patient_id"))].append(d)
    for weeks in by_patient.values():
        weeks.sort(key=lambda d: int(d.get("week_number", 0) or 0))
    return by_patient


def check_patient(weeks: list) -> set:
    """The names of the checks one patient's series fails (or notes)."""
    found = set()
    traj = (weeks[0] if weeks else {}).get("simulated_trajectory")
    seen = [w for w in weeks if int(w.get("week_number", 0) or 0) > 0 and w.get("observed", True)]

    for w in weeks:
        if int(w.get("week_number", 0) or 0) == 0:
            continue
        if w.get("red_flags") and w.get("risk_band") == "Low":
            found.add("red_flag_low_band")
        if w.get("observed") is False and w.get("red_flags"):
            found.add("silent_week_red_flag")
        if traj == "recovering" and w.get("observed", True) and w.get("red_flags"):
            found.add("recovering_red_flag")

    for w in seen:
        m = w.get("monitoring") or {}
        kg = m.get("weight_change_kg")
        swell = m.get("ankle_swelling")
        if kg is not None and swell is not None:
            if (swell == "marked" and kg < 1.0) or (swell == "none" and kg >= 3.0):
                found.add("swelling_vs_weight")
        if (m.get("orthopnoea_pillows") or 0) >= 3 and (kg or 0) < 0.5 and swell in (None, "none"):
            found.add("pillows_vs_congestion")
        if traj == "recovering" and m.get("new_confusion") == "yes" and (m.get("temperature_c") or 0) < 38.0:
            found.add("confusion_without_cause")

    follow = [(w.get("monitoring") or {}).get("followup_status") for w in seen]
    follow = [f for f in follow if f]
    if follow.count("attended") > 1:
        found.add("followup_sequence")
    for a, b in zip(follow, follow[1:]):
        if (a == "attended" and b != "not due") or (a == "missed" and b == "not due"):
            found.add("followup_sequence")

    due = [int(w["week_number"]) for w in seen
           if (w.get("monitoring") or {}).get("refill_status") in ("collected", "late", "missed")]
    if 1 in due or any(b - a == 1 for a, b in zip(due, due[1:])):
        found.add("refill_schedule")

    course = [(w.get("monitoring") or {}).get("antibiotic_course") for w in seen]
    course = [c for c in course if c]
    for a, b in zip(course, course[1:]):
        if (a in ("completed", "stopped_early") and b == "ongoing") or (a == "stopped_early" and b == "completed"):
            found.add("antibiotic_sequence")

    for prev, cur in zip(weeks, weeks[1:]):
        if (cur.get("observed", True) and prev.get("observed") is False
                and float(cur.get("risk_score", 0)) - float(prev.get("risk_score", 0)) >= 20):
            found.add("rise_after_silence")
    return found


def audit(docs) -> dict:
    """{check name: [patient ids]} over every check and note."""
    hits = {name: [] for name in {**CHECKS, **INFO}}
    for pid, weeks in _series(docs).items():
        for name in check_patient(weeks):
            hits[name].append(pid)
    hits["_patients"] = len(_series(docs))
    return hits


def report(hits: dict) -> int:
    """Print the audit; return how many checks failed."""
    print(f"  weekly monitoring audit - {hits.get('_patients', 0):,} patients")
    failed = 0
    for name, what in CHECKS.items():
        ids = hits.get(name, [])
        failed += bool(ids)
        mark = "FAIL" if ids else " ok "
        print(f"   [{mark}] {len(ids):5,}  {what}" + (f"   e.g. {', '.join(ids[:3])}" if ids else ""))
    for name, what in INFO.items():
        ids = hits.get(name, [])
        print(f"   [info] {len(ids):5,}  {what}" + (f"   e.g. {', '.join(ids[:3])}" if ids else ""))
    return failed


def main() -> None:
    from dotenv import load_dotenv
    from api.db_utils import get_db_name, get_mongo_client
    load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env"))
    db = get_mongo_client(os.environ.get("MONGO_URI", "mongodb://localhost:27017"))[get_db_name()]
    docs = list(db["weekly_monitoring"].find(
        {}, {"_id": 0, "patient_id": 1, "week_number": 1, "risk_score": 1, "risk_band": 1,
             "observed": 1, "monitoring": 1, "red_flags": 1, "simulated_trajectory": 1}))
    sys.exit(1 if report(audit(docs)) else 0)


if __name__ == "__main__":
    main()

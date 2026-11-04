"""
Give every patient a case manager: share each hospital's patients among the
case manager accounts it already has.

WHY THIS EXISTS
---------------
A patient's case manager (care_actions.assigned_case_manager_id, set on the
Care team dialog - api/hospital.assign) is who the patient and their caretaker
are told to ask. Patients loaded from MIMIC never had one, so their pages said
"Your hospital's case managers". This fills that in once, for the patients
already on the list. New patients get theirs on the Care team dialog.

It only changes who is NAMED. A case manager still sees every patient in their
hospital (api/access.py), assigned or not.

What it does, per hospital:
  * finds the hospital's active case manager accounts (User Management);
  * gives each patient with no case manager yet the one with the fewest
    patients so far - so the load stays even, and a re-run after new patients
    arrive only fills the gaps;
  * never changes a patient who already has one;
  * records every assignment in the care team history, as "script".

    .venv/bin/python scripts/assign_case_managers.py                 # dry run: shows what it would do
    .venv/bin/python scripts/assign_case_managers.py --apply
    .venv/bin/python scripts/assign_case_managers.py --hospital demo-hospital --apply
"""
from __future__ import annotations

import argparse
import os
import sys
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pymongo import UpdateOne  # noqa: E402

from api import access, auth, hospital  # noqa: E402

SCRIPT_USER = {"_id": "script", "email": "scripts/assign_case_managers.py",
               "name": "assign_case_managers script", "role": "superadmin"}


def plan(db, hospital_id: str | None = None) -> dict:
    """{hospital_id: {"case_managers": [...], "assign": [(patient_id, account_id)], "already": n}}"""
    query = {"role": "case_manager", "status": "active", "hospital_id": {"$nin": [None, ""]}}
    if hospital_id:
        query["hospital_id"] = hospital_id
    by_hospital: dict = {}
    for acc in auth.users(db).find(query, {"name": 1, "email": 1, "hospital_id": 1}).sort("email", 1):
        by_hospital.setdefault(acc["hospital_id"], []).append(acc)
    out = {}
    for hid, accounts in by_hospital.items():
        ids = [str(a["_id"]) for a in accounts]
        rows = list(db["care_actions"].find({"hospital_id": hid},
                                            {"_id": 0, "patient_id": 1, "assigned_case_manager_id": 1}))
        load = Counter({i: 0 for i in ids})
        for r in rows:
            if str(r.get("assigned_case_manager_id") or "") in load:
                load[str(r["assigned_case_manager_id"])] += 1
        todo = sorted((str(r["patient_id"]) for r in rows if not r.get("assigned_case_manager_id")))
        assign = []
        for pid in todo:
            who = min(ids, key=lambda i: (load[i], ids.index(i)))   # fewest so far; ties by email order
            load[who] += 1
            assign.append((pid, who))
        out[hid] = {"case_managers": [{"id": str(a["_id"]), "name": a.get("name") or a.get("email"),
                                       "patients_after": load[str(a["_id"])]} for a in accounts],
                    "assign": assign, "already": len(rows) - len(todo)}
    return out


def apply(db, planned: dict) -> int:
    done = 0
    for hid, p in planned.items():
        if not p["assign"]:
            continue
        # {"$in": [None, ""]} also matches a record with no such field at all.
        ops = [UpdateOne({"patient_id": {"$in": access.id_variants([pid])},
                          "assigned_case_manager_id": {"$in": [None, ""]}},
                         {"$set": {"assigned_case_manager_id": who}}) for pid, who in p["assign"]]
        db["care_actions"].bulk_write(ops, ordered=False)
        changes = [(pid, hid, {"case_manager_id": None}, {"case_manager_id": who}) for pid, who in p["assign"]]
        done += hospital.archive_care_team_changes(db, changes, SCRIPT_USER, via="script")
    return done


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--hospital", help="only this hospital id (default: every hospital)")
    parser.add_argument("--apply", action="store_true", help="write the assignments (default: dry run)")
    args = parser.parse_args()

    from dotenv import load_dotenv
    from api.db_utils import get_db_name, get_mongo_client
    load_dotenv()
    db = get_mongo_client(os.environ.get("MONGO_URI", "mongodb://localhost:27017"))[get_db_name()]

    planned = plan(db, args.hospital)
    if not planned:
        print("No hospital has an active case manager account. Add them in User Management first.")
        return
    tag = "" if args.apply else "[dry run] "
    for hid, p in planned.items():
        print(f"{tag}{hid}: {len(p['assign'])} patients to assign, {p['already']} already have one")
        for cm in p["case_managers"]:
            print(f"    {cm['name']:<30} {cm['patients_after']:>6} patients after")
    if args.apply:
        print(f"Assigned {apply(db, planned)} patients.")
    else:
        print("Nothing written. Run again with --apply to assign.")


if __name__ == "__main__":
    main()

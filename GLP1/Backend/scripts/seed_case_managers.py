"""
Give a hospital's GLP-1 patients a main case manager, for a demo.

WHAT IT DOES
------------
Creates (or reuses) four case manager logins in the hospital you name and gives
every GLP-1 patient there ONE main case manager - their point of contact:

    casemanager@<hospital-id>.test          Clinical & side effects
    casemanager.refills1@<hospital-id>.test Pharmacy & refills (half)
    casemanager.refills2@<hospital-id>.test Pharmacy & refills (other half)
    casemanager.cost@<hospital-id>.test     Cost & coverage

Patients are split by their main dropout reason (the model's top driver), so
each case manager gets the patients whose problem they handle. Refill problems
are by far the most common, so two people share them.

This split is ONLY for the demo. The app itself has no rule about it: it stores
one case manager per patient, and in a real hospital the admin or a case
manager sets it from the patient's page or the Patients list (Care team), the
way each hospital organises itself - by doctor team, by problem, by ward.

WHAT IT WRITES - and nothing else
---------------------------------
  shared_identity.users           the case manager accounts it creates
  glp1_analytics.patient_access   `case_manager_id`, only on records that have none

WHAT IT NEVER CHANGES
---------------------
The patients collection and every table built from the GLP-1 dataset (only
read, for each patient's main reason). Readmissions. Any doctor, nurse, insurer
or pharmacy. A case manager someone has already set. An existing login's
password, name or role. Re-running is safe: it only fills patients with no
case manager yet (new arrivals).

The hospital must exist and its patients be placed in it first
(Readmissions/scripts/seed_hospital.py).

Run from GLP1/Backend, with its .env (MONGODB_URI, SHARED_IDENTITY_DB_NAME).
Needs bcrypt:  pip install bcrypt

    python -m scripts.seed_case_managers --hospital "City Hospital" --dry-run
    python -m scripts.seed_case_managers --hospital "City Hospital"
"""
from __future__ import annotations

import argparse
import getpass
import re
import sys
from collections import Counter

from pymongo import MongoClient, UpdateOne

from core.config import settings
from scripts.demo_patient_logins import MIN_PASSWORD_LENGTH, find_hospital, hash_password, now

CREATED_BY = "GLP1/Backend/scripts/seed_case_managers.py"

# Which main reasons each team handles (matched against the patient's driver_1,
# e.g. "Provider & pharmacy refill reliability"). Anything else is clinical.
REFILLS = re.compile(r"pharmacy|refill|provider", re.I)
COST = re.compile(r"financ|cost|income|pocket|insur|afford", re.I)

TEAM = [
    # (key, email prefix, name, focus)
    ("clinical", "casemanager", "Hina Qureshi", "Clinical & side effects"),
    ("refills1", "casemanager.refills1", "Usman Tariq", "Pharmacy & refills"),
    ("refills2", "casemanager.refills2", "Mariam Javed", "Pharmacy & refills"),
    ("cost", "casemanager.cost", "Saad Rehman", "Cost & coverage"),
]


def team_for(patient_idx: int, reason: str) -> str:
    """The demo split: by main reason; refills shared by patient number."""
    if REFILLS.search(reason or ""):
        return "refills1" if int(patient_idx) % 2 == 0 else "refills2"
    if COST.search(reason or ""):
        return "cost"
    return "clinical"


def run(client: MongoClient, hospital: str, password: str, dry: bool) -> dict:
    identity = client[settings.shared_identity_db_name]
    glp1 = client[settings.mongodb_db_name]
    users, access = identity["users"], glp1["patient_access"]

    found = find_hospital(identity["hospitals"], hospital)
    if not found:
        names = ", ".join(f'"{h.get("name", h["_id"])}"' for h in identity["hospitals"].find()) or "none"
        sys.exit(f"  No hospital called \"{hospital}\". Hospitals: {names}. Nothing changed.")
    hid = found["_id"]

    placed = list(access.find({"hospital_id": hid}, {"_id": 0, "patient_idx": 1, "case_manager_id": 1}))
    if not placed:
        sys.exit(f"  No GLP-1 patients are placed in {hid} yet - run "
                 "Readmissions/scripts/seed_hospital.py first. Nothing changed.")

    # ---- the accounts
    password_hash = None if dry else hash_password(password)
    ids, report = {}, []
    for key, prefix, name, focus in TEAM:
        email = f"{prefix}@{hid}.test"
        account = users.find_one({"email": email})
        if account and (account.get("role") != "case_manager" or account.get("hospital_id") != hid):
            print(f"  {email} exists as a {account.get('role')} of {account.get('hospital_id')}; skipped")
            continue
        if account:
            ids[key] = str(account["_id"])
            report.append((email, account.get("name") or name, focus, "existing"))
            continue
        if dry:
            ids[key] = f"(new:{email})"
        else:
            ids[key] = str(users.insert_one({
                "email": email, "password_hash": password_hash, "name": name,
                "role": "case_manager", "status": "active", "hospital_id": hid, "insurer_id": None,
                "must_change_password": False, "app_access": ["glp1", "readmissions"],
                "created_at": now(), "created_by": CREATED_BY}).inserted_id)
        report.append((email, name, focus, "new"))
    if not ids:
        sys.exit("  No case manager logins to use; nothing changed.")

    # ---- the split: patients with no case manager yet. Read-only on patients.
    todo = [int(a["patient_idx"]) for a in placed if not a.get("case_manager_id")]
    reasons = {int(p["patient_idx"]): p.get("driver_1") or "" for p in glp1["patients"].find(
        {"patient_idx": {"$in": todo}}, {"_id": 0, "patient_idx": 1, "driver_1": 1})}
    ops, counts = [], Counter()
    for idx in todo:
        key = team_for(idx, reasons.get(idx, ""))
        if key not in ids:                       # that login was skipped: any colleague
            key = next(iter(ids))
        counts[key] += 1
        # Only a record still without one - never overwrite a choice made since.
        ops.append(UpdateOne({"patient_idx": idx, "hospital_id": hid,
                              "$or": [{"case_manager_id": {"$exists": False}}, {"case_manager_id": None},
                                      {"case_manager_id": ""}]},
                             {"$set": {"case_manager_id": ids[key]}}))
    written = 0
    if ops and not dry:
        written = access.bulk_write(ops, ordered=False).modified_count
    return {"hospital": hid, "accounts": report, "counts": counts, "keys": ids,
            "already": len(placed) - len(todo), "written": written if not dry else len(ops)}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.strip().split("\n\n")[0])
    parser.add_argument("--hospital", required=True, help="hospital name or id (must exist)")
    parser.add_argument("--dry-run", action="store_true", help="report without writing anything")
    args = parser.parse_args()
    dry = args.dry_run
    tag = "[dry run] " if dry else ""

    password = "dry-run-password" if dry else getpass.getpass(
        "Password for NEW case manager logins (min 8; existing ones keep theirs): ")
    if len(password) < MIN_PASSWORD_LENGTH:
        sys.exit("  Password too short; nothing changed.")

    out = run(MongoClient(settings.mongodb_uri), args.hospital, password, dry)
    by_email = {f"{prefix}@{out['hospital']}.test": key for key, prefix, _, _ in TEAM}
    width = max(len(r[0]) for r in out["accounts"])
    print(f"\n  {tag}Case managers in {out['hospital']} - sign in at the Portal, open GLP-1:")
    print(f"    {'email'.ljust(width)}  {'name':<15}{'handles':<25}new patients")
    for email, name, focus, state in out["accounts"]:
        note = "  (login already existed)" if state == "existing" else ""
        print(f"    {email.ljust(width)}  {name:<15}{focus:<25}{out['counts'].get(by_email[email], 0):>6,}{note}")
    print(f"\n  {tag}{sum(out['counts'].values()):,} patients given a case manager; "
          f"{out['already']:,} already had one and were left alone.")
    print(f"  {'Dry run: nothing written.' if dry else 'Done. New logins use the password you typed.'}")


if __name__ == "__main__":
    main()
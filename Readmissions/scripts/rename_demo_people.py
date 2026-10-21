"""
Give the demo staff accounts generic English names - nothing is seeded again.

WHAT IT DOES
------------
For every demo account (an email ending in .test) that is a doctor, hospital
nurse, case manager or hospital admin, it sets a new display name from a fixed
list - doctors "Dr Emily Carter", "Dr James Wilson", ...; nurses "Olivia
Brown", ...; case managers "Sarah Mitchell", ...; hospital admins "Daniel
Hughes", .... Each role is numbered in sign-up order, so re-running gives the
same names and changes nothing more.

It changes only names:
    shared_identity.users.name            the account's display name (both apps)
    neuroshield.doctors.name              Readmissions' doctor registry, matched
                                          by email, so the doctor pages agree
    shared_identity.hospitals.name        only with --hospital-name, for one
                                          hospital (its id stays the same)
Emails, passwords, roles, hospitals and every patient link stay as they are,
and the dataset is never touched. Names already written into past follow-ups
and access-log entries keep the old name - those are records of who did what.

    .venv/bin/python scripts/rename_demo_people.py --dry-run
    .venv/bin/python scripts/rename_demo_people.py
    .venv/bin/python scripts/rename_demo_people.py --hospital-name "Riverside General Hospital"

Add --all to include accounts whose email does not end in .test.
"""
from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv

from api import auth
from api.db_utils import get_db_name, get_mongo_client

NAMES = {
    "doctor": ["Dr Emily Carter", "Dr James Wilson", "Dr Laura Bennett", "Dr Michael Turner", "Dr Rachel Adams",
               "Dr David Foster", "Dr Hannah Cooper", "Dr Andrew Collins", "Dr Sophie Ward", "Dr Thomas Reed",
               "Dr Claire Morgan", "Dr Peter Hayes"],
    "nurse": ["Olivia Brown", "Grace Taylor", "Lucy Evans", "Megan Clarke", "Chloe Walker", "Amy Robinson",
              "Ella Wright", "Jessica Hall", "Katie Green", "Rebecca Lewis", "Holly Scott", "Natalie King"],
    "case_manager": ["Sarah Mitchell", "Jennifer Brooks", "Karen Ellis", "Lisa Parker", "Anna Fisher",
                     "Helen Murray", "Julia Price", "Victoria Lane"],
    "hospital_admin": ["Daniel Hughes", "Mark Stevens", "Paul Richardson", "Simon Barker", "Richard Gray",
                       "Steven Cole"],
}


def plan(users, include_all: bool) -> list:
    """(account, old name, new name) for every account to rename."""
    out = []
    for role, pool in NAMES.items():
        query = {"role": role}
        if not include_all:
            query["email"] = {"$regex": r"\.test$"}
        accounts = list(users.find(query, {"email": 1, "name": 1, "created_at": 1}).sort([("created_at", 1), ("email", 1)]))
        for i, account in enumerate(accounts):
            base = pool[i % len(pool)]
            new = base if i < len(pool) else f"{base} {i // len(pool) + 1}"
            if account.get("name") != new:
                out.append((account, account.get("name") or "", new))
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.strip().split("\n\n")[0])
    parser.add_argument("--hospital-name", help="also rename the hospital (needs --hospital-id if there are several)")
    parser.add_argument("--hospital-id", help="which hospital to rename (default: the only one, or the one with most staff)")
    parser.add_argument("--all", action="store_true", help="include accounts whose email does not end in .test")
    parser.add_argument("--dry-run", action="store_true", help="show the changes without writing anything")
    args = parser.parse_args()
    dry, tag = args.dry_run, "[dry run] " if args.dry_run else ""

    load_dotenv()
    client = get_mongo_client(os.environ.get("MONGO_URI", "mongodb://localhost:27017"))
    db = client[get_db_name()]
    users = auth.users(db)
    identity = client[auth.IDENTITY_DB]

    changes = plan(users, args.all)
    print(f"\n  {tag}{len(changes)} account(s) to rename:")
    for account, old, new in changes:
        print(f"    {account['email']:<44} {old or '(no name)':<26} -> {new}")
        if not dry:
            users.update_one({"_id": account["_id"]}, {"$set": {"name": new, "updated_at": auth._now()}})
            db["doctors"].update_many({"email": account["email"]}, {"$set": {"name": new}})

    if args.hospital_name:
        hid = args.hospital_id
        if not hid:
            hospitals = [h["_id"] for h in identity["hospitals"].find({}, {"_id": 1})]
            if len(hospitals) == 1:
                hid = hospitals[0]
            else:
                counts = {}
                for u in users.find({"hospital_id": {"$in": hospitals}}, {"hospital_id": 1}):
                    counts[u["hospital_id"]] = counts.get(u["hospital_id"], 0) + 1
                hid = max(counts, key=counts.get) if counts else None
        doc = identity["hospitals"].find_one({"_id": hid}) if hid else None
        if not doc:
            sys.exit("  No such hospital; pass --hospital-id. Names above were " + ("not " if dry else "") + "changed.")
        print(f"\n  {tag}hospital {hid}: {doc.get('name')!r} -> {args.hospital_name!r} (its id stays {hid})")
        if not dry:
            identity["hospitals"].update_one({"_id": hid}, {"$set": {"name": args.hospital_name.strip()}})

    print(f"\n  {'Dry run: nothing written.' if dry else 'Done. Signed-in people see the new names after a refresh.'}")


if __name__ == "__main__":
    main()
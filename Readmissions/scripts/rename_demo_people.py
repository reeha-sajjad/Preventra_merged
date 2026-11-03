"""
Give the demo staff generic English names - nothing is seeded again.

WHAT IT DOES
------------
For every demo account (an email ending in .test) that is a doctor, hospital
nurse, case manager or hospital admin, it sets a new display name from a fixed
list - doctors "Dr Emily Carter", "Dr James Wilson", ...; nurses "Olivia
Brown", ...; case managers "Sarah Mitchell", ...; hospital admins "Daniel
Hughes", .... Each role is numbered in sign-up order, so re-running gives the
same names and changes nothing more.

Doctors registered for alerts with no account (the Readmissions doctor
registry) get the next doctor names on the list, in the order they registered.
A registry doctor whose email belongs to a real (non-.test) account is left
alone unless --all is given.

Names are also copied into records - an alert keeps the name of the doctor it
was sent to, a follow-up the name of whoever asked for it. Wherever a name field
(name, doctor_name, answered_by, author, coordinator_name) holds exactly an old
name (or an old name followed by " (Nurse)" and the like), it is replaced by the
new one, so the screens agree:

    shared_identity.users.name            the account's display name (both apps)
    neuroshield.doctors.name              Readmissions' doctor registry
    copies in neuroshield                 clinical_alerts, notifications,
                                          care_actions, followups,
                                          discharge_plans, readmission_outcomes,
                                          insurer_alerts, medicines_given,
                                          checkins, rescore_log
    shared_identity.hospitals.name        only with --hospital-name, for one
                                          hospital (its id stays the same)

Only whole name fields are changed - free text such as a note's wording is not.
Emails, passwords, roles, ids, hospitals, patients and the dataset are never
touched. Every run that writes keeps its old -> new list in
shared_identity.demo_renames, and --undo puts the last run back.

    .venv/bin/python scripts/rename_demo_people.py --dry-run
    .venv/bin/python scripts/rename_demo_people.py
    .venv/bin/python scripts/rename_demo_people.py --undo
    .venv/bin/python scripts/rename_demo_people.py --hospital-name "Riverside General Hospital"

Add --all to include accounts whose email does not end in .test.
"""
from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
from pymongo import UpdateOne

from api import auth
from api.db_utils import get_db_name, get_mongo_client

NAMES = {
    "doctor": ["Dr Emily Carter", "Dr James Wilson", "Dr Laura Bennett", "Dr Michael Turner", "Dr Rachel Adams",
               "Dr David Foster", "Dr Hannah Cooper", "Dr Andrew Collins", "Dr Sophie Ward", "Dr Thomas Reed",
               "Dr Claire Morgan", "Dr Peter Hayes", "Dr Robert Hughes", "Dr Alice Palmer", "Dr George Harris",
               "Dr Charlotte Webb", "Dr William Baker", "Dr Emma Fletcher", "Dr Oliver Grant", "Dr Lucy Shaw",
               "Dr Henry Lawson", "Dr Kate Spencer", "Dr Edward Mills", "Dr Ruth Chapman"],
    "nurse": ["Olivia Brown", "Grace Taylor", "Lucy Evans", "Megan Clarke", "Chloe Walker", "Amy Robinson",
              "Ella Wright", "Jessica Hall", "Katie Green", "Rebecca Lewis", "Holly Scott", "Natalie King",
              "Emily Hughes", "Sophie Turner", "Hannah Bell", "Laura Cook"],
    "case_manager": ["Sarah Mitchell", "Jennifer Brooks", "Karen Ellis", "Lisa Parker", "Anna Fisher",
                     "Helen Murray", "Julia Price", "Victoria Lane", "Rachel Hunt", "Emma Knight"],
    "hospital_admin": ["Daniel Hughes", "Mark Stevens", "Paul Richardson", "Simon Barker", "Richard Gray",
                       "Steven Cole", "Andrew Dixon", "Peter Fox"],
}

# Where a staff name is copied into a record, and the fields that hold one
# ({id, name, role} "who" records, an alert's doctor, a note's author). The
# access log keeps ids and emails, not names, so it needs nothing.
COPIES = ("clinical_alerts", "notifications", "care_actions", "followups", "discharge_plans",
          "readmission_outcomes", "insurer_alerts", "medicines_given", "checkins", "rescore_log")
NAME_KEYS = {"name", "doctor_name", "answered_by", "author", "coordinator_name"}
RENAMES = "demo_renames"


def _name_for(pool: list, i: int) -> str:
    base = pool[i % len(pool)]
    return base if i < len(pool) else f"{base} {i // len(pool) + 1}"


def plan(users, include_all: bool) -> list:
    """(account, old name, new name) for every account to rename."""
    out = []
    for role, pool in NAMES.items():
        query = {"role": role}
        if not include_all:
            query["email"] = {"$regex": r"\.test$"}
        accounts = list(users.find(query, {"email": 1, "name": 1, "created_at": 1}).sort([("created_at", 1), ("email", 1)]))
        for i, account in enumerate(accounts):
            new = _name_for(pool, i)
            if account.get("name") != new:
                out.append((account, account.get("name") or "", new))
    return out


def plan_registry(db, users, include_all: bool) -> list:
    """(registry doctor, old name, new name) for every doctor in the registry.
    One with a demo account takes that account's name; one with no account takes
    the next doctor name; one belonging to a real account is left alone."""
    doctor_accounts = list(users.find({"role": "doctor"} if include_all
                                      else {"role": "doctor", "email": {"$regex": r"\.test$"}},
                                      {"email": 1, "created_at": 1}).sort([("created_at", 1), ("email", 1)]))
    named = {str(a.get("email", "")).lower(): _name_for(NAMES["doctor"], i) for i, a in enumerate(doctor_accounts)}
    accounts = {str(u.get("email", "")).lower() for u in users.find({}, {"email": 1})}
    used, nxt, out = set(named.values()), len(doctor_accounts), []
    for doc in db["doctors"].find({}, {"_id": 1, "email": 1, "name": 1, "registered_at": 1, "doctor_id": 1}).sort(
            [("registered_at", 1), ("doctor_id", 1)]):
        email = str(doc.get("email") or "").lower()
        if email in named:
            new = named[email]
        elif email and email in accounts:
            continue                          # a real person's account: not ours to rename
        else:
            new = _name_for(NAMES["doctor"], nxt)
            while new in used:
                nxt += 1
                new = _name_for(NAMES["doctor"], nxt)
            nxt += 1
        used.add(new)
        if doc.get("name") != new:
            out.append((doc, doc.get("name") or "", new))
    return out


def _swap(value, mapping: dict, key: str = ""):
    """The value with every old name in a name field replaced; (value, changed)."""
    if isinstance(value, str):
        if key not in NAME_KEYS:
            return value, False
        if value in mapping:
            return mapping[value], True
        for old, new in mapping.items():
            if value.startswith(old + " ("):           # "Olivia Brown (Nurse)"
                return new + value[len(old):], True
        return value, False
    if isinstance(value, list):
        changed, out = False, []
        for v in value:
            v2, c = _swap(v, mapping, key)
            out.append(v2)
            changed = changed or c
        return out, changed
    if isinstance(value, dict):
        changed, out = False, {}
        for k, v in value.items():
            v2, c = _swap(v, mapping, k)
            out[k] = v2
            changed = changed or c
        return out, changed
    return value, False


def rename_copies(collection, mapping: dict, dry: bool) -> int:
    """Replace old names in every stored field of `collection`; how many records changed."""
    if not mapping:
        return 0
    ops = []
    for doc in collection.find({}):
        sets = {}
        for key, value in doc.items():
            if key == "_id":
                continue
            new, changed = _swap(value, mapping, key)
            if changed:
                sets[key] = new
        if sets:
            ops.append(UpdateOne({"_id": doc["_id"]}, {"$set": sets}))
    if ops and not dry:
        for i in range(0, len(ops), 500):
            collection.bulk_write(ops[i:i + 500], ordered=False)
    return len(ops)


def rename(db, include_all: bool = False, dry: bool = False) -> dict:
    """Rename demo staff, registry doctors and every copy of their names."""
    users, identity = auth.users(db), db.client[auth.IDENTITY_DB]
    accounts, registry = plan(users, include_all), plan_registry(db, users, include_all)
    mapping = {}
    for _, old, new in accounts + registry:
        if old and old != new:
            mapping.setdefault(old, new)
    report = {"accounts": [(a["email"], old, new) for a, old, new in accounts],
              "registry": [(d.get("email") or d.get("doctor_id") or "", old, new) for d, old, new in registry],
              "copies": {}}
    if not dry and (accounts or registry):
        identity[RENAMES].insert_one({
            "at": datetime.now(timezone.utc),
            "accounts": [{"_id": a["_id"], "old": old, "new": new} for a, old, new in accounts],
            "registry": [{"_id": d["_id"], "old": old, "new": new} for d, old, new in registry],
            "mapping": [{"old": o, "new": n} for o, n in mapping.items()]})
    for a, _, new in accounts:
        if not dry:
            users.update_one({"_id": a["_id"]}, {"$set": {"name": new, "updated_at": auth._now()}})
    for d, _, new in registry:
        if not dry:
            db["doctors"].update_one({"_id": d["_id"]}, {"$set": {"name": new}})
    for name in COPIES:
        report["copies"][name] = rename_copies(db[name], mapping, dry)
    return report


def undo(db, dry: bool = False) -> dict:
    """Put the last run back: account and registry names, and every copy."""
    identity = db.client[auth.IDENTITY_DB]
    last = identity[RENAMES].find_one(sort=[("at", -1)])
    if not last:
        return {}
    users = auth.users(db)
    back = {m["new"]: m["old"] for m in last.get("mapping", [])}
    report = {"accounts": len(last.get("accounts", [])), "registry": len(last.get("registry", [])), "copies": {}}
    if not dry:
        for a in last.get("accounts", []):
            users.update_one({"_id": a["_id"]}, {"$set": {"name": a["old"], "updated_at": auth._now()}})
        for d in last.get("registry", []):
            db["doctors"].update_one({"_id": d["_id"]}, {"$set": {"name": d["old"]}})
    for name in COPIES:
        report["copies"][name] = rename_copies(db[name], back, dry)
    if not dry:
        identity[RENAMES].delete_one({"_id": last["_id"]})
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.strip().split("\n\n")[0])
    parser.add_argument("--hospital-name", help="also rename the hospital (needs --hospital-id if there are several)")
    parser.add_argument("--hospital-id", help="which hospital to rename (default: the only one, or the one with most staff)")
    parser.add_argument("--all", action="store_true", help="include accounts whose email does not end in .test")
    parser.add_argument("--undo", action="store_true", help="put the last run's names back")
    parser.add_argument("--dry-run", action="store_true", help="show the changes without writing anything")
    args = parser.parse_args()
    dry, tag = args.dry_run, "[dry run] " if args.dry_run else ""

    load_dotenv()
    client = get_mongo_client(os.environ.get("MONGO_URI", "mongodb://localhost:27017"))
    db = client[get_db_name()]
    users = auth.users(db)
    identity = client[auth.IDENTITY_DB]

    if args.undo:
        report = undo(db, dry)
        if not report:
            print("\n  Nothing to put back: no rename on record.")
            return
        print(f"\n  {tag}putting back {report['accounts']} account name(s), {report['registry']} registry doctor(s)")
        for name, n in report["copies"].items():
            if n:
                print(f"    {name:<22} {n} record(s)")
        print(f"\n  {'Dry run: nothing written.' if dry else 'Done.'}")
        return

    report = rename(db, args.all, dry)
    print(f"\n  {tag}{len(report['accounts'])} account(s) to rename:")
    for email, old, new in report["accounts"]:
        print(f"    {email:<44} {old or '(no name)':<26} -> {new}")
    print(f"\n  {tag}{len(report['registry'])} registry doctor(s) to rename:")
    for who, old, new in report["registry"]:
        print(f"    {who:<44} {old or '(no name)':<26} -> {new}")
    print(f"\n  {tag}records holding an old name:")
    for name, n in report["copies"].items():
        print(f"    {name:<22} {n}")

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

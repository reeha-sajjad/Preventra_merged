"""
Give every patient in both apps a pharmacy, so pharmacy staff can be placed.

WHY THIS EXISTS
---------------
The pharmacy role (api/access.py) sees the patients who fill at its pharmacy,
matched by `pharmacy_id` on each patient's ownership record. Patients loaded
before the role existed carry only a pharmacy NAME (GLP-1) or none at all
(Readmissions), so without this script a pharmacy login sees nobody.

What it writes:

  shared_identity.pharmacies     one organisation per pharmacy, if missing
  glp1_analytics.patient_access  `pharmacy_id`, from the pharmacy name already
                                 on the GLP-1 record (scripts/seed_hospital.py)
  neuroshield.care_actions       `pharmacy_id`, the pharmacy the weekly
                                 monitoring cards already name for that patient
                                 (models/monitoring_rules.source_for), so the
                                 refill line and the pharmacy login agree

With --with-demo-logins it also creates one active login per pharmacy,
pharmacy@<pharmacy-id>.test, with the password you type.

The pharmacy names are fictional, like the rest of the demo data: the two apps
were built separately and name different pharmacies, so a demo pharmacy has
patients in one app or the other. A real deployment links each patient to their
actual pharmacy from dispensing data.

WHAT IT NEVER CHANGES
---------------------
Patient data in either app (the GLP-1 patients collection, the Readmissions
worklist and weekly monitoring). Any pharmacy already set on a record. Any
existing login. Re-running it only fills gaps, e.g. after new patients arrive.

    .venv/bin/python scripts/seed_pharmacies.py --dry-run
    .venv/bin/python scripts/seed_pharmacies.py --with-demo-logins

NOTE: if scripts/migrate_user_roles.py installed the users validator on your
cluster, re-run it first so the database accepts the new "pharmacy" role.
"""
from __future__ import annotations

import argparse
import getpass
import os
import sys
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
from pymongo import ASCENDING, UpdateOne

from api import auth
from api.db_utils import get_db_name, get_mongo_client
from models import monitoring_rules

CREATED_BY = "scripts/seed_pharmacies.py"


def _missing(field: str) -> dict:
    return {"$or": [{field: {"$exists": False}}, {field: None}, {field: ""}]}


def readmissions_pharmacy(patient_id) -> str:
    """The pharmacy the weekly monitoring cards name for this patient."""
    return monitoring_rules.source_for("refill_status", str(patient_id))["feed"]


def _bulk(collection, ops, dry: bool) -> int:
    if dry or not ops:
        return len(ops)
    changed = 0
    for i in range(0, len(ops), 1000):
        changed += collection.bulk_write(ops[i:i + 1000], ordered=False).modified_count
    return changed


def run(client, readm_db: str, glp1_db: str, password: str = "", dry: bool = False,
        with_logins: bool = False) -> dict:
    readm, glp1 = client[readm_db], client[glp1_db]
    identity = client[auth.IDENTITY_DB]

    # ---- which pharmacies, and who fills where (read-only so far)
    glp1_names = {d["patient_idx"]: d["pharmacy"] for d in glp1["patient_access"].find(
        {"pharmacy": {"$nin": [None, ""]}, **_missing("pharmacy_id")}, {"patient_idx": 1, "pharmacy": 1})}
    readm_ids = [d["patient_id"] for d in readm["care_actions"].find(
        _missing("pharmacy_id"), {"patient_id": 1})]
    readm_names = {pid: readmissions_pharmacy(pid) for pid in readm_ids}
    names = sorted(set(glp1_names.values()) | set(readm_names.values())
                   | set(monitoring_rules._PHARMACIES))

    # ---- organisations
    created = []
    for name in names:
        org = auth.slugify_org(name)
        if not identity["pharmacies"].find_one({"_id": org}):
            created.append(org)
            if not dry:
                identity["pharmacies"].insert_one({"_id": org, "name": name, "created_at": auth._now(),
                                                   "created_by": CREATED_BY})

    # ---- link the patients, only where no pharmacy is set
    if not dry:
        glp1["patient_access"].create_index([("pharmacy_id", ASCENDING)])
        readm["care_actions"].create_index([("pharmacy_id", ASCENDING)])
    glp1_ops = [UpdateOne({"patient_idx": idx, **_missing("pharmacy_id")},
                          {"$set": {"pharmacy_id": auth.slugify_org(name)}}) for idx, name in glp1_names.items()]
    readm_ops = [UpdateOne({"patient_id": pid, **_missing("pharmacy_id")},
                           {"$set": {"pharmacy_id": auth.slugify_org(name)}}) for pid, name in readm_names.items()]
    glp1_written = _bulk(glp1["patient_access"], glp1_ops, dry)
    readm_written = _bulk(readm["care_actions"], readm_ops, dry)

    # ---- demo logins
    logins = []
    if with_logins:
        users = auth.users(readm)
        password_hash = None if dry else auth.hash_password(password)
        for name in names:
            org = auth.slugify_org(name)
            email = f"pharmacy@{org}.test"
            existing = users.find_one({"email": email})
            if existing:
                logins.append((email, name, "existing"))
                continue
            if not dry:
                users.insert_one({"email": email, "password_hash": password_hash, "name": f"{name} counter",
                                  "role": "pharmacy", "status": "active", "hospital_id": None,
                                  "insurer_id": None, "pharmacy_id": org, "must_change_password": False,
                                  "app_access": list(auth.DEFAULT_APP_ACCESS),
                                  "created_at": auth._now(), "created_by": CREATED_BY})
            logins.append((email, name, "new"))

    per_pharmacy = Counter()
    for name in glp1_names.values():
        per_pharmacy[(name, "glp1")] += 1
    for name in readm_names.values():
        per_pharmacy[(name, "readmissions")] += 1
    return {"pharmacies": names, "created": created, "glp1": glp1_written, "readmissions": readm_written,
            "per_pharmacy": per_pharmacy, "logins": logins}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.strip().split("\n\n")[0])
    parser.add_argument("--with-demo-logins", action="store_true",
                        help="also create one pharmacy login per pharmacy")
    parser.add_argument("--glp1-db", default=os.environ.get("GLP1_DB", "glp1_analytics"))
    parser.add_argument("--dry-run", action="store_true", help="report without writing anything")
    args = parser.parse_args()
    dry, tag = args.dry_run, "[dry run] " if args.dry_run else ""

    load_dotenv()
    client = get_mongo_client(os.environ.get("MONGO_URI", "mongodb://localhost:27017"))
    password = ""
    if args.with_demo_logins:
        password = "dry-run-password" if dry else getpass.getpass("Password for the pharmacy logins (min 8): ")
        if len(password) < auth.MIN_PASSWORD_LENGTH:
            sys.exit("  Password too short; nothing changed.")

    out = run(client, get_db_name(), args.glp1_db, password, dry, args.with_demo_logins)
    print(f"\n  {tag}{len(out['pharmacies'])} pharmacies ({len(out['created'])} new):")
    for name in out["pharmacies"]:
        g, r = out["per_pharmacy"].get((name, "glp1"), 0), out["per_pharmacy"].get((name, "readmissions"), 0)
        print(f"    {name:<32} GLP-1 {g:>6,}   Readmissions {r:>6,}   ({auth.slugify_org(name)})")
    print(f"\n  {tag}linked {out['glp1']:,} GLP-1 and {out['readmissions']:,} Readmissions patients "
          "(records that already had a pharmacy were left alone)")
    if out["logins"]:
        print(f"\n  {tag}pharmacy logins - sign in at the Portal:")
        for email, name, state in out["logins"]:
            print(f"    {email:<48} {name}{'  (already existed)' if state == 'existing' else ''}")
    print(f"\n  {'Dry run: nothing written.' if dry else 'Done.'}")


if __name__ == "__main__":
    main()
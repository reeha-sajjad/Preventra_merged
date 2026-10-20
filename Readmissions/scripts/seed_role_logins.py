"""
Demo logins for the pharmacy and caretaker roles, in both apps, that you can
sign in with straight away.

WHAT IT DOES
------------
1. Pharmacy: makes sure the login pharmacy@<pharmacy-id>.test exists for one
   pharmacy (by default the one the most patients fill at), is active, and has
   the password you type. If it already exists, its password is reset to that.
   It sees the patients whose record names that pharmacy (pharmacy_id, set by
   scripts/seed_pharmacies.py - run that first if no patient has one).
2. Caretaker: makes sure caretaker@demo.test exists, is active, and has the
   password you type, and links it to one patient in each app - the patient a
   demo patient login already opens (scripts/demo_patient_logins.py), so the
   patient's and the caretaker's pages show the same person; otherwise the
   first patient in the hospital. Pick others with --readmissions-patient and
   --glp1-patient.

It writes only logins (shared_identity.users) and the caretaker link on the
ownership records (care_actions.caretaker_ids, glp1_analytics.patient_access.
caretaker_ids, with a history entry). The dataset is never touched. Re-running
is safe: nothing is duplicated, and the passwords are set again.

    .venv/bin/python scripts/seed_role_logins.py --dry-run
    .venv/bin/python scripts/seed_role_logins.py
    .venv/bin/python scripts/seed_role_logins.py --pharmacy "CityCare Pharmacy" --glp1-patient 727

NOTE: if scripts/migrate_user_roles.py installed the users validator on your
cluster, re-run it first so the database accepts the "pharmacy" and
"caretaker" roles.
"""
from __future__ import annotations

import argparse
import getpass
import os
import sys
from collections import Counter
from typing import Optional

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv

from api import auth
from api.db_utils import get_db_name, get_mongo_client

CREATED_BY = "scripts/seed_role_logins.py"
CARETAKER_EMAIL = "caretaker@demo.test"


def _upsert_login(users, email: str, role: str, name: str, password_hash: Optional[str], dry: bool,
                  pharmacy_id: Optional[str] = None) -> tuple:
    """Create the login, or make an existing one active with this password.
    Returns (account id or None on a dry run, "new" | "reset")."""
    fields = {"role": role, "status": "active", "hospital_id": None, "insurer_id": None,
              "pharmacy_id": pharmacy_id, "must_change_password": False}
    existing = users.find_one({"email": email})
    if existing:
        if existing.get("role") not in (role,):
            sys.exit(f"  {email} already exists as a {existing.get('role')} login; nothing changed.")
        if not dry:
            users.update_one({"_id": existing["_id"]},
                             {"$set": {**fields, "password_hash": password_hash, "updated_at": auth._now()}})
        return str(existing["_id"]), "reset"
    if dry:
        return None, "new"
    uid = users.insert_one({"email": email, "password_hash": password_hash, "name": name, **fields,
                            "app_access": list(auth.DEFAULT_APP_ACCESS), "created_at": auth._now(),
                            "created_by": CREATED_BY}).inserted_id
    return str(uid), "new"


def _pick(col, key: str, chosen, cast=str) -> Optional[dict]:
    """The record to link the caretaker to: the one asked for, else the one a
    demo patient login opens, else the first one in a hospital."""
    if chosen not in (None, ""):
        doc = col.find_one({key: cast(chosen)})
        if not doc:
            sys.exit(f"  No record {chosen!r} in {col.name}; nothing changed.")
        return doc
    return (col.find_one({"patient_account_id": {"$nin": [None, ""]}}, sort=[(key, 1)])
            or col.find_one({"hospital_id": {"$nin": [None, ""]}}, sort=[(key, 1)]))


def _link(col, doc: Optional[dict], caretaker_id: Optional[str], dry: bool) -> str:
    if not doc:
        return "no patient record found"
    if caretaker_id and caretaker_id in (doc.get("caretaker_ids") or []):
        return "already linked"
    if not dry:
        col.update_one({"_id": doc["_id"]}, {
            "$addToSet": {"caretaker_ids": caretaker_id},
            "$push": {"caretaker_history": {"action": "linked", "caretaker_id": caretaker_id,
                                            "by": {"id": None, "role": "script", "name": CREATED_BY},
                                            "at": auth._now(), "agreement": "demo_seed"}}})
    return "linked"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.strip().split("\n\n")[0])
    parser.add_argument("--pharmacy", help="pharmacy name or id (default: the one most patients fill at)")
    parser.add_argument("--readmissions-patient", help="Readmissions patient id for the caretaker, e.g. MIMIC-1101")
    parser.add_argument("--glp1-patient", help="GLP-1 patient number for the caretaker, e.g. 727")
    parser.add_argument("--glp1-db", default=os.environ.get("GLP1_DB", "glp1_analytics"))
    parser.add_argument("--dry-run", action="store_true", help="report without writing anything")
    args = parser.parse_args()
    dry, tag = args.dry_run, "[dry run] " if args.dry_run else ""

    load_dotenv()
    client = get_mongo_client(os.environ.get("MONGO_URI", "mongodb://localhost:27017"))
    readm, glp1, identity = client[get_db_name()], client[args.glp1_db], client[auth.IDENTITY_DB]
    users = auth.users(readm)

    # ---- which pharmacy
    if args.pharmacy:
        org = auth.slugify_org(args.pharmacy)
    else:
        counts = Counter(d["pharmacy_id"] for col in (readm["care_actions"], glp1["patient_access"])
                         for d in col.find({"pharmacy_id": {"$nin": [None, ""]}}, {"pharmacy_id": 1}))
        if not counts:
            sys.exit("  No patient has a pharmacy yet. Run scripts/seed_pharmacies.py first; nothing changed.")
        org = counts.most_common(1)[0][0]
    pharmacy = identity["pharmacies"].find_one({"_id": org})
    if not pharmacy:
        sys.exit(f"  No pharmacy {org!r}. Run scripts/seed_pharmacies.py first; nothing changed.")
    n_patients = (readm["care_actions"].count_documents({"pharmacy_id": org})
                  + glp1["patient_access"].count_documents({"pharmacy_id": org}))

    password = "dry-run-password" if dry else getpass.getpass("Password for both demo logins (min 8): ")
    if len(password) < auth.MIN_PASSWORD_LENGTH:
        sys.exit("  Password too short; nothing changed.")
    password_hash = None if dry else auth.hash_password(password)

    # ---- logins
    rx_email = f"pharmacy@{org}.test"
    _, rx_state = _upsert_login(users, rx_email, "pharmacy", f"{pharmacy.get('name', org)} counter",
                                password_hash, dry, pharmacy_id=org)
    carer_id, carer_state = _upsert_login(users, CARETAKER_EMAIL, "caretaker", "Demo Caretaker",
                                          password_hash, dry)

    # ---- link the caretaker to one patient in each app
    r_doc = _pick(readm["care_actions"], "patient_id", args.readmissions_patient)
    g_doc = _pick(glp1["patient_access"], "patient_idx", args.glp1_patient, int)
    r_state = _link(readm["care_actions"], r_doc, carer_id, dry)
    g_state = _link(glp1["patient_access"], g_doc, carer_id, dry)

    print(f"\n  {tag}Sign in at the Portal with the password you typed:")
    print(f"    {rx_email:<40} pharmacy  ({'new' if rx_state == 'new' else 'password reset'}) - "
          f"{pharmacy.get('name', org)}, {n_patients:,} patients across both apps")
    print(f"    {CARETAKER_EMAIL:<40} caretaker ({'new' if carer_state == 'new' else 'password reset'})")
    print(f"      Readmissions patient {r_doc['patient_id'] if r_doc else '-':<14} {r_state}")
    print(f"      GLP-1 patient        #{g_doc['patient_idx'] if g_doc else '-':<13} {g_state}")
    print(f"\n  {'Dry run: nothing written.' if dry else 'Done.'}")


if __name__ == "__main__":
    main()
    
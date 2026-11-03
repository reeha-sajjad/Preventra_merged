"""
Give every patient in both apps an owner: a hospital, an insurer, and (with
--with-demo-accounts) a doctor and nurses to look after them.

WHY THIS EXISTS
---------------
Access control shows each account only the patients it owns (api/access.py for
Readmissions, GLP1/Backend/core/access.py for GLP-1). Patients loaded before
that had no owner, so without this script every account except the superadmin
would see an empty app. Run it BEFORE deploying the access-control backends.

What it writes, for ONE hospital (the demo shows one):

  shared_identity.hospitals     the hospital, if it does not exist
  shared_identity.insurers      Medicare, Medicaid, Private, Other
  neuroshield.care_actions      per Readmissions patient: hospital_id, insurer_id
                                (from the patient's MIMIC insurance, most common
                                across their admissions)
  glp1_analytics.patient_access per GLP-1 patient: hospital_id, insurer_id
                                (65+ -> Medicare, else a fixed split by id), pharmacy

With --with-demo-accounts it also creates, for that hospital, a hospital admin,
four doctors (in the Readmissions doctor registry too, matched by email), two
nurses, a case manager, two patient logins linked to real records, and one
login per insurer - all with the password you type - and assigns every patient
a doctor by specialty and a nurse round-robin.

Idempotent and non-destructive: an owner, insurer or assignment already set is
never changed, so re-running after new patients arrive only fills the gaps.
Re-run it after scripts/load_mimic_to_mongo.py, which rebuilds the worklist.

    .venv/bin/python scripts/seed_hospital.py --hospital "Demo Hospital" --dry-run
    .venv/bin/python scripts/seed_hospital.py --hospital "Demo Hospital" --with-demo-accounts

Needs pyarrow to read the MIMIC matrix (pip install pyarrow, or the full
requirements.txt environment).
"""
from __future__ import annotations

import argparse
import getpass
import hashlib
import os
import sys
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
from pymongo import ASCENDING, UpdateOne

from api import auth
from api.db_utils import get_db_name, get_mongo_client

MATRIX = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                      "data", "mimic", "model", "results", "phase1_matrix.parquet")

# MIMIC insurance value -> insurer organisation. "UNKNOWN" and "No charge" have
# no insurer to show, so those patients get none.
INSURERS = {"Medicare": "medicare", "Medicaid": "medicaid", "Private": "private", "Other": "other"}

PHARMACIES = ["Main Street Pharmacy", "CityCare Pharmacy", "Riverside Drugs",
              "Northgate Pharmacy", "Hospital Outpatient Pharmacy"]

# Demo doctors, by specialty. `groups` are the Readmissions clinical groups they
# take (anything unmatched goes to internal medicine); `glp1` says who shares
# the GLP-1 patients.
DOCTORS = [
    {"key": "cardiology", "name": "Dr Emily Carter", "specialty": "Cardiology",
     "groups": ["heart_failure", "cardiac_other"], "glp1": False},
    {"key": "pulmonology", "name": "Dr James Wilson", "specialty": "Pulmonology",
     "groups": ["respiratory"], "glp1": False},
    {"key": "endocrinology", "name": "Dr Laura Bennett", "specialty": "Endocrinology",
     "groups": ["diabetes"], "glp1": True},
    {"key": "medicine", "name": "Dr Michael Turner", "specialty": "Internal Medicine",
     "groups": ["general"], "glp1": True},
]


def stable_bucket(value, n: int) -> int:
    """The same bucket for the same value on every run and every machine."""
    return int(hashlib.sha256(str(value).encode()).hexdigest(), 16) % n


def glp1_insurer(idx: int, age) -> str:
    try:
        if float(age) >= 65:
            return "medicare"
    except (TypeError, ValueError):
        pass
    bucket = stable_bucket(f"ins-{idx}", 10)
    return "private" if bucket < 6 else "medicaid" if bucket < 9 else "other"


def mimic_insurance() -> dict:
    """patient_id -> insurer id, from the most common insurance on each
    patient's admissions."""
    import pandas as pd
    m = pd.read_parquet(MATRIX, columns=["subject_id", "insurance"])
    out = {}
    for subject, values in m.groupby("subject_id")["insurance"]:
        top = Counter(values.dropna()).most_common(1)
        if top and top[0][0] in INSURERS:
            out[f"MIMIC-{subject}"] = INSURERS[top[0][0]]
    return out


def ensure_org(collection, org_id: str, name: str, dry: bool) -> bool:
    if collection.find_one({"_id": org_id}):
        return False
    if not dry:
        collection.insert_one({"_id": org_id, "name": name, "created_at": auth._now(),
                               "created_by": "scripts/seed_hospital.py"})
    return True


def ensure_account(users, email: str, role: str, password: str, name: str,
                   hospital_id=None, insurer_id=None, dry=False) -> dict:
    """An active demo account; an existing one is left exactly as it is."""
    existing = users.find_one({"email": email})
    if existing:
        return existing
    doc = {"email": email, "password_hash": auth.hash_password(password), "name": name,
           "role": role, "status": "active", "hospital_id": hospital_id,
           "insurer_id": insurer_id, "must_change_password": False,
           "app_access": list(auth.DEFAULT_APP_ACCESS),
           "created_at": auth._now(), "created_by": "scripts/seed_hospital.py"}
    if not dry:
        doc["_id"] = users.insert_one(doc).inserted_id
    else:
        doc["_id"] = f"(new:{email})"
    return doc


def missing(field: str) -> dict:
    """Matches a record where `field` is absent or empty, so a seed only fills
    gaps and never overwrites an owner or assignment someone has set."""
    return {"$or": [{field: {"$exists": False}}, {field: None}, {field: []}]}


def bulk(collection, ops, dry: bool) -> int:
    if dry or not ops:
        return len(ops)
    changed = 0
    for i in range(0, len(ops), 1000):
        changed += collection.bulk_write(ops[i:i + 1000], ordered=False).modified_count
    return changed


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--hospital", default="Demo Hospital", help="hospital name (created if missing)")
    parser.add_argument("--with-demo-accounts", action="store_true",
                        help="also create demo staff, patient and insurer logins and assign patients")
    parser.add_argument("--glp1-db", default=os.environ.get("GLP1_DB", "glp1_analytics"))
    parser.add_argument("--dry-run", action="store_true", help="report without writing anything")
    args = parser.parse_args()
    dry = args.dry_run

    load_dotenv()
    client = get_mongo_client(os.environ.get("MONGO_URI", "mongodb://localhost:27017"))
    readm, glp1 = client[get_db_name()], client[args.glp1_db]
    identity = client[auth.IDENTITY_DB]
    hospital_id = auth.slugify_org(args.hospital)
    tag = "[dry run] " if dry else ""

    # ---- indexes, first --------------------------------------------------
    # Before any write: every update below finds its record by patient id, and
    # without the index each of those tens of thousands of updates would scan
    # the whole collection. The other fields are what the access filters query.
    if not dry:
        for field in ("patient_id", "hospital_id", "insurer_id", "assigned_doctor_id",
                      "assigned_nurse_ids", "patient_account_id"):
            readm["care_actions"].create_index([(field, ASCENDING)])
        for field in ("patient_idx", "hospital_id", "insurer_id", "assigned_doctor_id",
                      "assigned_nurse_ids", "patient_account_id"):
            glp1["patient_access"].create_index([(field, ASCENDING)], unique=(field == "patient_idx"))
        readm["doctors"].create_index([("hospital_id", ASCENDING)])

    # ---- organisations ---------------------------------------------------
    made = ensure_org(identity["hospitals"], hospital_id, args.hospital, dry)
    print(f"  {tag}hospital {hospital_id}: {'created' if made else 'already there'}")
    for name, org in INSURERS.items():
        ensure_org(identity["insurers"], org, name, dry)
    print(f"  {tag}insurers: {', '.join(INSURERS.values())}")

    # ---- demo accounts ---------------------------------------------------
    staff = {}
    if args.with_demo_accounts:
        password = "dry-run-password" if dry else getpass.getpass("Demo account password (min 8): ")
        if len(password) < auth.MIN_PASSWORD_LENGTH:
            sys.exit("  Password too short; nothing changed.")
        users = auth.users(readm)
        domain = f"{hospital_id}.test"
        staff["admin"] = ensure_account(users, f"admin@{domain}", "hospital_admin", password,
                                        "Daniel Hughes", hospital_id, dry=dry)
        staff["case_manager"] = ensure_account(users, f"casemanager@{domain}", "case_manager",
                                               password, "Sarah Mitchell", hospital_id, dry=dry)
        staff["nurses"] = [ensure_account(users, f"nurse{i}@{domain}", "nurse", password,
                                          ("Olivia Brown", "Grace Taylor")[i - 1], hospital_id, dry=dry) for i in (1, 2)]
        staff["doctors"] = {}
        for d in DOCTORS:
            email = f"{d['key']}@{domain}"
            staff["doctors"][d["key"]] = ensure_account(users, email, "doctor", password,
                                                        d["name"], hospital_id, dry=dry)
            if not dry and not readm["doctors"].find_one({"email": email}):
                readm["doctors"].insert_one({
                    "doctor_id": f"DR-{d['key']}-{hospital_id}"[:40], "name": d["name"],
                    "specialty": d["specialty"], "email": email, "clinical_groups": d["groups"],
                    "active": True, "hospital_id": hospital_id, "registered_at": auth._now()})
        staff["insurers"] = [ensure_account(users, f"claims@{org}.test", "insurer", password,
                                            f"{name} claims", insurer_id=org, dry=dry)
                             for name, org in INSURERS.items()]
        staff["patients"] = [ensure_account(users, f"patient{i}@{domain}", "patient", password,
                                            f"Demo Patient {i}", hospital_id, dry=dry) for i in (1, 2)]
        print(f"  {tag}demo accounts: admin, case manager, 2 nurses, {len(DOCTORS)} doctors, "
              f"{len(INSURERS)} insurers, 2 patients  (@{domain}, @<insurer>.test)")

    # ---- Readmissions patients ------------------------------------------
    patient_ids = sorted({str(p) for p in readm["patient_worklist"].distinct("patient_id")})
    groups = {str(r["patient_id"]): r.get("clinical_group") for r in
              readm["patient_worklist"].find({}, {"patient_id": 1, "clinical_group": 1})}
    insurance = mimic_insurance()
    doctor_ids = {d["key"]: (readm["doctors"].find_one({"email": f"{d['key']}@{hospital_id}.test"})
                             or {}).get("doctor_id") for d in DOCTORS} if staff else {}
    by_group = {g: d["key"] for d in DOCTORS for g in d["groups"]}

    ops = []
    for n, pid in enumerate(patient_ids):
        key = {"patient_id": pid}
        ops.append(UpdateOne(key, {"$setOnInsert": {"notes": [], "coordinator_name": None,
                                                    "assigned_at": None}}, upsert=True))
        ops.append(UpdateOne({**key, **missing("hospital_id")},
                             {"$set": {"hospital_id": hospital_id}}))
        if pid in insurance:
            ops.append(UpdateOne({**key, **missing("insurer_id")},
                                 {"$set": {"insurer_id": insurance[pid]}}))
        if staff:
            doc_id = doctor_ids.get(by_group.get(groups.get(pid), "medicine"))
            if doc_id:
                ops.append(UpdateOne({**key, **missing("assigned_doctor_id")},
                                     {"$set": {"assigned_doctor_id": doc_id}}))
            nurse = staff["nurses"][n % 2]["_id"]
            ops.append(UpdateOne({**key, **missing("assigned_nurse_ids")},
                                 {"$set": {"assigned_nurse_ids": [str(nurse)]}}))
    if staff and patient_ids:
        for i, account in enumerate(staff["patients"]):
            if i < len(patient_ids):
                ops.append(UpdateOne({"patient_id": patient_ids[i],
                                      **missing("patient_account_id")},
                                     {"$set": {"patient_account_id": str(account["_id"])}}))
    changed = bulk(readm["care_actions"], ops, dry)
    print(f"  {tag}Readmissions: {len(patient_ids):,} patients, {len(insurance):,} with insurance "
          f"on file, {changed:,} field updates")

    # ---- GLP-1 patients -------------------------------------------------
    glp1_docs = list(glp1["patients"].find({}, {"patient_idx": 1, "RIDAGEYR": 1}))
    glp1_doctors = [staff["doctors"][d["key"]]["_id"] for d in DOCTORS if d["glp1"]] if staff else []
    ops = []
    for doc in glp1_docs:
        idx = int(doc["patient_idx"])
        key = {"patient_idx": idx}
        ops.append(UpdateOne(key, {"$setOnInsert": {"created_by": "scripts/seed_hospital.py"}},
                             upsert=True))
        ops.append(UpdateOne({**key, **missing("hospital_id")},
                             {"$set": {"hospital_id": hospital_id}}))
        ops.append(UpdateOne({**key, **missing("insurer_id")},
                             {"$set": {"insurer_id": glp1_insurer(idx, doc.get("RIDAGEYR"))}}))
        ops.append(UpdateOne({**key, **missing("pharmacy")},
                             {"$set": {"pharmacy": PHARMACIES[stable_bucket(f"ph-{idx}", len(PHARMACIES))]}}))
        if staff:
            ops.append(UpdateOne({**key, **missing("assigned_doctor_id")},
                                 {"$set": {"assigned_doctor_id": str(glp1_doctors[idx % len(glp1_doctors)])}}))
            ops.append(UpdateOne({**key, **missing("assigned_nurse_ids")},
                                 {"$set": {"assigned_nurse_ids": [str(staff["nurses"][idx % 2]["_id"])]}}))
    if staff and glp1_docs:
        for i, account in enumerate(staff["patients"]):
            if i < len(glp1_docs):
                ops.append(UpdateOne({"patient_idx": int(glp1_docs[i]["patient_idx"]),
                                      **missing("patient_account_id")},
                                     {"$set": {"patient_account_id": str(account["_id"])}}))
    changed = bulk(glp1["patient_access"], ops, dry)
    print(f"  {tag}GLP-1: {len(glp1_docs):,} patients, {changed:,} field updates")

    print(f"  {'Dry run: nothing written.' if dry else 'Done.'}")


if __name__ == "__main__":
    main()

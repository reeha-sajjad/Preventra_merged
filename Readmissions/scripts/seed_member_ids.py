"""
Give demo patients an insurance member ID, in both apps, so billing and the
insurer screens have something to show.

WHAT IT DOES
------------
1. Every insurer whose id or name is "medicare" is marked member_id_format =
   "mbi", so its member IDs are checked against the Medicare Beneficiary
   Identifier format (api/member_ids.py).
2. Every patient who has an insurer but no member ID gets one:
     Medicare   a made-up MBI in the official format (11 characters, never the
                letters S, L, O, I, B or Z)
     others     the insurer's first three letters and nine digits, e.g.
                MED004218733 for Medicaid
   Each is unique within its insurer, as the apps require. Existing member IDs
   are left alone, and re-running links nobody twice.

It writes only to the ownership records - glp1_analytics.patient_access and
neuroshield.care_actions (insurer_member_id) - and to the insurer records. The
dataset itself is never touched. These IDs are made up for the demo; real ones
come from the patient's insurance card.

    .venv/bin/python scripts/seed_member_ids.py --dry-run
    .venv/bin/python scripts/seed_member_ids.py
"""
from __future__ import annotations

import argparse
import os
import random
import sys
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
from pymongo import UpdateOne

from api import auth, member_ids
from api.db_utils import get_db_name, get_mongo_client

DIGITS = "0123456789"


def fake_mbi(rng: random.Random) -> str:
    L, A = member_ids.MBI_LETTERS, DIGITS + member_ids.MBI_LETTERS
    pick = rng.choice
    return (pick("123456789") + pick(L) + pick(A) + pick(DIGITS) + pick(L) + pick(A)
            + pick(DIGITS) + pick(L) + pick(L) + pick(DIGITS) + pick(DIGITS))


def fake_member_id(rng: random.Random, insurer_id: str) -> str:
    prefix = "".join(c for c in insurer_id.upper() if c.isalnum())[:3] or "MEM"
    return prefix + "".join(rng.choice(DIGITS) for _ in range(9))


def _assign(collection, key: str, formats: dict, rng: random.Random, dry: bool) -> Counter:
    taken = {(d.get("insurer_id"), d["insurer_member_id"]) for d in collection.find(
        {"insurer_member_id": {"$type": "string"}}, {"insurer_id": 1, "insurer_member_id": 1})}
    ops, made = [], Counter()
    for d in collection.find({"insurer_id": {"$nin": [None, ""]},
                              "$or": [{"insurer_member_id": {"$exists": False}}, {"insurer_member_id": None}]},
                             {key: 1, "insurer_id": 1}).sort(key, 1):
        ins = d["insurer_id"]
        while True:
            mid = fake_mbi(rng) if formats.get(ins) == "mbi" else fake_member_id(rng, ins)
            if (ins, mid) not in taken:
                break
        taken.add((ins, mid))
        made[ins] += 1
        ops.append(UpdateOne({"_id": d["_id"], "insurer_member_id": {"$exists": False}},
                             {"$set": {"insurer_member_id": mid}}))
    if not dry:
        for i in range(0, len(ops), 1000):
            collection.bulk_write(ops[i:i + 1000], ordered=False)
    return made


def run(client, readm_db: str, glp1_db: str, dry: bool = False, seed: int = 7) -> dict:
    insurers = client[auth.IDENTITY_DB]["insurers"]
    # Original Medicare only: an insurer called exactly "Medicare" (Medicare
    # Advantage plans issue their own IDs).
    medicare = [i["_id"] for i in insurers.find()
                if str(i["_id"]).lower() == "medicare" or str(i.get("name", "")).strip().lower() == "medicare"]
    if not dry and medicare:
        insurers.update_many({"_id": {"$in": medicare}}, {"$set": {"member_id_format": "mbi"}})
    formats = {i: "mbi" for i in medicare}
    rng = random.Random(seed)
    return {"medicare": medicare,
            "glp1": _assign(client[glp1_db]["patient_access"], "patient_idx", formats, rng, dry),
            "readmissions": _assign(client[readm_db]["care_actions"], "patient_id", formats, rng, dry)}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.strip().split("\n\n")[0])
    parser.add_argument("--glp1-db", default=os.environ.get("GLP1_DB", "glp1_analytics"))
    parser.add_argument("--dry-run", action="store_true", help="report without writing anything")
    args = parser.parse_args()
    load_dotenv()
    client = get_mongo_client(os.environ.get("MONGO_URI", "mongodb://localhost:27017"))
    out = run(client, get_db_name(), args.glp1_db, args.dry_run)
    tag = "[dry run] " if args.dry_run else ""
    print(f"\n  {tag}Medicare (checked as MBI): {', '.join(out['medicare']) or 'none found'}")
    for app in ("glp1", "readmissions"):
        made = out[app]
        print(f"  {tag}{'GLP-1' if app == 'glp1' else 'Readmissions'}: {sum(made.values()):,} member IDs "
              f"({', '.join(f'{k} {v:,}' for k, v in sorted(made.items())) or 'none needed'})")
    print(f"\n  {'Dry run: nothing written.' if args.dry_run else 'Done.'}")


if __name__ == "__main__":
    main()
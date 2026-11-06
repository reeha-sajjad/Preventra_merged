"""
Add a few simulated mood screens to GLP-1 demo patients, so a demo can show the
Mood card, the bell and the Today list.

WHAT IT DOES
------------
Takes the demo patient logins that scripts/demo_patient_logins.py made in the
hospital you name (glp1.patient1@..., glp1.patient2@..., ...) and gives the first
five of them two earlier mood screens each, one scene per patient:

    1  moderate_first   mood screen moderate or worse, the first time
    2  rise             mood worse by 5 or more points since last time
    3  low_mood_event   low mood with trouble taking the medicine (a simulated
                        event, kept on the screen itself)
    4  gad2_only        the anxiety screen only - shown, but rings no bell
    5  unflagged        nothing flagged

Every screen is labelled "Demo data (simulated)" (`simulated`, `demo`,
`recorded_by`) and the Mood card and bell say so. Fewer than five demo logins:
the first scenes run, and the rest are listed as skipped.

WHAT IT WRITES - and nothing else
---------------------------------
  glp1_analytics.mood_screens      the simulated screens
  glp1_analytics.demo_mood_state   one record per run, written FIRST, so --undo
                                   can always find what a run made

WHAT IT NEVER CHANGES
---------------------
The patients collection and every table built from the GLP-1 dataset,
patient_access, the accounts, the follow-up list, pharmacy reports and dose
ticks. The low-mood scene carries its event on the screen only: no pharmacy
report or follow-up is made, so no simulated item appears in a real work queue.
A real screen a patient answered is never touched: --undo removes only what is
marked `simulated` and belongs to a run recorded here.

Re-running is safe: the earlier run's screens are removed and the scenes made
again, so there is always exactly one set. --undo removes it. --dry-run works out
and prints everything and writes nothing (with --undo, what would go).

Run from GLP1/Backend, with its .env (MONGODB_URI, SHARED_IDENTITY_DB_NAME).

    python -m scripts.demo_mood --hospital "Your Hospital Name" --dry-run
    python -m scripts.demo_mood --hospital "Your Hospital Name"
    python -m scripts.demo_mood --hospital "Your Hospital Name" --undo
"""
from __future__ import annotations

import argparse
import re
import sys
from datetime import datetime, timedelta, timezone
from typing import Optional
from uuid import uuid4

from pymongo import MongoClient

from core.config import settings
from core.mood import COLLECTION, INDEXES
from core.mood_screen import GAD2, PHQ2, PHQ8_MORE, assess
from scripts.demo_patient_logins import find_hospital

STATE = "demo_mood_state"
CREATED_BY = "GLP1/Backend/scripts/demo_mood.py"
LABEL = "Demo data (simulated)"


def phq(*values: int) -> dict:
    """PHQ-8 answers in the form's order (the two PHQ-2 items first)."""
    return dict(zip([*PHQ2, *PHQ8_MORE], values))


def gad(nervous: int, worry: int) -> dict:
    return dict(zip(GAD2, (nervous, worry)))


# scene -> its screens as (days ago, answers, simulated trouble), and the flags
# the latest should carry.
SCENES = (
    ("moderate_first", [(30, {**phq(1, 0), **gad(1, 0)}, None),
                        (16, {**phq(2, 2, 2, 2, 1, 1, 1, 0), **gad(1, 1)}, None)], ["phq8_moderate"]),
    ("rise", [(30, phq(2, 1, 2, 2, 1, 1, 1, 0), None),
              (16, phq(3, 2, 2, 3, 2, 2, 1, 1), None)], ["phq8_rise"]),
    ("low_mood_event", [(16, phq(2, 2, 1, 1, 1, 0, 1, 0), ["missed_pickup"])], ["low_mood_trouble"]),
    ("gad2_only", [(16, {**phq(0, 1), **gad(2, 2)}, None)], ["gad2_positive"]),
    ("unflagged", [(30, {**phq(0, 0), **gad(0, 1)}, None),
                   (16, {**phq(1, 0), **gad(1, 0)}, None)], []),
)


def demo_patients(identity, access, hid: str) -> list:
    """(N, email, patient_idx) of the hospital's demo logins that are linked to a
    record, in N order."""
    out = []
    for account in identity["users"].find({"role": "patient", "hospital_id": hid,
                                           "email": {"$regex": r"^glp1\.patient\d+@"}}):
        found = access.find_one({"patient_account_id": str(account["_id"])}, {"patient_idx": 1})
        if found:
            out.append((int(re.match(r"glp1\.patient(\d+)@", account["email"]).group(1)),
                        account["email"], int(found["patient_idx"])))
    return sorted(out)


def screens_of(scene: str, steps: list, idx: int, rid: str, now: datetime) -> list:
    """The scene's documents, oldest first, each assessed against those before it."""
    docs = []
    for k, (days, answers, trouble) in enumerate(steps, 1):
        at = now - timedelta(days=days)
        docs.append({"patient_idx": idx, "at": at, "answered_on": at.date().isoformat(),
                     "by_role": "script", "by_user": CREATED_BY, "recorded_by": LABEL,
                     "demo": True, "demo_run": rid, "previous_id": f"demo:{rid}:{scene}:{k}",
                     **assess(answers, docs, at, trouble, simulated=True),
                     **({"trouble_simulated": True} if trouble else {})})
    return docs


def remove_run(glp1, rid: str, dry: bool) -> int:
    """Delete a run's simulated screens (never a real one) and its state record."""
    query = {"demo_run": rid, "simulated": True}
    count = glp1[COLLECTION].count_documents(query)
    if not dry:
        glp1[COLLECTION].delete_many(query)
        glp1[STATE].delete_one({"run_id": rid})
    return count


def run(client: MongoClient, hospital: str, dry: bool, undo: bool = False,
        now: Optional[datetime] = None) -> list:
    """Make (or, with `undo`, remove) the simulated screens.
    Returns one row per scene made - (scene, email, patient_idx, latest flags) -
    or, for `undo`, (run_id, screens removed) per earlier run."""
    identity, glp1 = client[settings.shared_identity_db_name], client[settings.mongodb_db_name]
    found = find_hospital(identity["hospitals"], hospital)
    if not found:
        names = ", ".join(f'"{h.get("name", h["_id"])}"' for h in identity["hospitals"].find()) or "none"
        sys.exit(f"  No hospital called \"{hospital}\". Hospitals: {names}. Nothing changed.")
    hid = found["_id"]
    now = now or datetime.now(timezone.utc)

    if undo:
        return [(s["run_id"], remove_run(glp1, s["run_id"], dry)) for s in glp1[STATE].find({"hospital_id": hid})]

    patients = demo_patients(identity, glp1["patient_access"], hid)
    if not patients:
        sys.exit(f"  No GLP-1 demo patient logins in {hid} - run scripts/demo_patient_logins.py first. "
                 "Nothing changed.")
    rid = uuid4().hex[:12]
    plan = [(scene, email, idx, screens_of(scene, steps, idx, rid, now))
            for (scene, steps, _), (_, email, idx) in zip(SCENES, patients)]

    if not dry:
        # The record of this run first, so that whatever happens next can be undone.
        glp1[STATE].insert_one({"run_id": rid, "hospital_id": hid, "started_at": now, "status": "started",
                                "patients": [idx for _, _, idx, _ in plan], "created_by": CREATED_BY})
        for earlier in glp1[STATE].find({"hospital_id": hid, "run_id": {"$ne": rid}}):
            remove_run(glp1, earlier["run_id"], dry=False)
        for keys, options in INDEXES:
            glp1[COLLECTION].create_index(keys, **options)
        glp1[COLLECTION].insert_many([d for _, _, _, docs in plan for d in docs])
        glp1[STATE].update_one({"run_id": rid}, {"$set": {"status": "done"}})
    return [(scene, email, idx, docs[-1]["flags"]) for scene, email, idx, docs in plan]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.strip().split("\n\n")[0])
    parser.add_argument("--hospital", required=True, help="hospital name or id (must exist)")
    parser.add_argument("--dry-run", action="store_true", help="report without writing anything")
    parser.add_argument("--undo", action="store_true", help="remove the simulated screens")
    args = parser.parse_args()
    dry = args.dry_run
    tag = "[dry run] " if dry else ""

    rows = run(MongoClient(settings.mongodb_uri), args.hospital, dry, args.undo)
    if args.undo:
        print(f"\n  {tag}{'Would remove' if dry else 'Removed'} the simulated mood screens of {len(rows)} run(s):")
        for rid, count in rows:
            print(f"    run {rid}: {count} screens")
    else:
        width = max([len(r[1]) for r in rows] + [5])
        print(f"\n  {tag}{len(rows)} patients with simulated mood screens ({LABEL}):")
        print(f"    {'email'.ljust(width)}  {'patient':<9}{'scene':<17}latest flags")
        for scene, email, idx, flags in rows:
            print(f"    {email.ljust(width)}  #{idx:<8}{scene:<17}{', '.join(flags) or 'none'}")
        skipped = [s[0] for s in SCENES[len(rows):]]
        if skipped:
            print(f"\n  Skipped (not enough demo logins): {', '.join(skipped)}")
    print(f"\n  {'Dry run: nothing written.' if dry else 'Done.'}")


if __name__ == "__main__":
    main()

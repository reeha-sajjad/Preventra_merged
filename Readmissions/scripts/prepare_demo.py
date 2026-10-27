#!/usr/bin/env python3
"""
Get the demo hospital ready for a live demo in one command - and again before
the next one.

The demo patients (MIMIC) went home years ago, so on their own they never show
what lives inside the 30 days after discharge: the weekly check-in, the
"gone quiet" warning, the 30-day outcome. This moves a handful of them to
around today, and changes nothing else:

    checkin   a patient login's record, home 3 days, readings after discharge
              set aside - sign in as that patient and do this week's check-in
    quiet     a Medium/High patient, home 18 days, readings to week 1 and none
              since - "No readings for 11 days since week 1"
    never     a High patient, home 12 days, nothing since discharge
    active    two patients with a month of readings to yesterday - on track
    outcome   a High patient, home 40 days - record whether they came back

Their old alerts (from the series as it was) are set aside too, so the
clinician inbox shows only what the demo raises. With --doctor, all six are
assigned to that doctor (registered for alerts if they are not yet), so the
red-flag alert and the patients reach the login you present with; --case-manager checks the case manager login you will use. Both
accounts must already be active in the demo hospital - the script says what to
fix in User Management if not, and writes nothing.

Their original dates, doctors, and the readings set aside, are kept in `demo_prep`.
Running it again first puts the last run back - clearing the check-ins,
alerts, follow-ups, checklist ticks and outcomes made on these patients since -
so every demo starts the same, wherever the last one ended. --undo puts it all
back for good.

    .venv/bin/python scripts/prepare_demo.py                  # what it would do
    .venv/bin/python scripts/prepare_demo.py --yes            # do it (before each demo)
    .venv/bin/python scripts/prepare_demo.py --case-manager casemanager@test.com --doctor doctor@test.com --yes
    .venv/bin/python scripts/prepare_demo.py --undo --yes     # put everything back

Default hospital: your-hospital-name (the production demo hospital). Writes to
whichever database MONGO_URI / MONGO_DB point at.
"""
from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

STATE = "demo_prep"
WEEKLY = "weekly_monitoring"
# scene -> (days since discharge, the last week kept: None keeps them all)
SCENES = {"checkin": (3, 0), "quiet": (18, 1), "never": (12, 0), "active": (29, None), "outcome": (40, None)}
ORDER = ("checkin", "quiet", "never", "active", "active", "outcome")
# What a demo leaves on these patients, cleared when it is put back.
LEFTOVERS = ("checkins", "discharge_plans", "readmission_outcomes", "risk_watch_seen")


def _as_date(value):
    from models.monitoring_rules import _as_date as as_date
    return as_date(value)


def _shift(value, days: int):
    when = _as_date(value)
    return (when + timedelta(days=days)).isoformat() if when else value


def _variants(pid):
    from api.access import id_variants
    return id_variants([pid])


# ------------------------------------------------------------ choosing
def choose(db, hospital: str) -> list:
    """[(scene, patient_id)] - the same patients every time for the same data."""
    from api.db_utils import get_latest_batch_date
    batch = get_latest_batch_date(db, "patient_worklist")
    own = {str(o["patient_id"]): o for o in db["care_actions"].find(
        {"hospital_id": hospital}, {"patient_id": 1, "patient_account_id": 1})}
    rows = {str(r["patient_id"]): r for r in db["patient_worklist"].find(
        {"batch_date": batch, "patient_id": {"$in": [p for o in own.values() for p in _variants(o["patient_id"])]}},
        {"_id": 0, "patient_id": 1, "risk_band": 1, "clinical_group": 1, "discharge_date": 1})}
    series = {}
    for doc in db[WEEKLY].find({"patient_id": {"$in": [r["patient_id"] for r in rows.values()]}},
                               {"_id": 0, "patient_id": 1, "week_number": 1, "risk_band": 1, "observed": 1,
                                "discharge_baseline": 1}):
        series.setdefault(str(doc["patient_id"]), {})[int(doc.get("week_number", 0))] = doc
    ready = sorted(p for p in rows if 0 in series.get(p, {}) and _as_date(rows[p].get("discharge_date")))
    taken, picks = set(), []

    def pick(scene, candidates, key=lambda p: p):
        found = sorted((p for p in candidates if p not in taken), key=key)
        if found:
            taken.add(found[0])
            picks.append((scene, found[0]))

    band = lambda p: str(rows[p].get("risk_band") or "").title()  # noqa: E731
    weeks = lambda p: series.get(p, {})  # noqa: E731
    for scene in ORDER:
        if scene == "checkin":
            # A patient login's record; heart failure and High make the best
            # check-in (its own questions), and a discharge weight adds weight.
            pick(scene, [p for p in ready if own.get(p, {}).get("patient_account_id")],
                 key=lambda p: (rows[p].get("clinical_group") != "heart_failure", band(p) != "High",
                                not (weeks(p)[0].get("discharge_baseline") or {}).get("dry_weight_kg"), p))
        elif scene == "quiet":
            pick(scene, [p for p in ready if weeks(p).get(1, {}).get("observed", True) is not False
                         and str(weeks(p).get(1, {}).get("risk_band") or "").title() in ("Medium", "High")])
        elif scene == "never":
            # Nothing heard since a High discharge: week 0 is what the silence rule reads.
            pick(scene, [p for p in ready if str(weeks(p)[0].get("risk_band") or "").title() == "High"])
        elif scene == "outcome":
            pick(scene, [p for p in ready if band(p) == "High"])
        elif scene == "active":
            pick(scene, [p for p in ready if len(weeks(p)) >= 5
                         and all(w.get("observed", True) is not False for w in weeks(p).values())])
    return picks


# ------------------------------------------------------------ the people presenting
def check_staff(db, hospital: str, case_manager: str = None, doctor: str = None) -> list:
    """What is wrong with the logins the demo will use, in words; [] when fine."""
    from api import auth
    problems = []
    for email, role in ((case_manager, "case_manager"), (doctor, "doctor")):
        if not email:
            continue
        user = auth.users(db).find_one({"email": email.strip().lower()})
        if not user:
            problems.append(f"{email}: there is no account with this email.")
        elif user.get("role") != role:
            problems.append(f"{email} is a {user.get('role')} account, not a {role.replace('_', ' ')}.")
        elif user.get("status") != "active":
            problems.append(f"{email} is {user.get('status')}: approve it in User Management.")
        elif user.get("hospital_id") != hospital:
            problems.append(f"{email} is in hospital '{user.get('hospital_id') or 'none'}', not '{hospital}': "
                            f"set their hospital in User Management.")
    if doctor and not problems:
        listed = db["doctors"].find_one({"email": doctor.strip().lower()})
        if listed and (listed.get("hospital_id") != hospital or not listed.get("active", True)):
            problems.append(f"{doctor} is registered for alerts in '{listed.get('hospital_id')}'"
                            f"{'' if listed.get('active', True) else ' (inactive)'}: fix it on the Staff page.")
    return problems


def _demo_doctor(db, hospital: str, email: str) -> str:
    """The registry id of the presenting doctor, registering them if needed
    (as the Staff page's Register would). Recorded so undo can take it away."""
    from api import auth
    email = email.strip().lower()
    listed = db["doctors"].find_one({"email": email})
    if listed:
        return listed["doctor_id"]
    user = auth.users(db).find_one({"email": email}, {"name": 1})
    doctor_id = f"DR-demo-{email.split('@')[0]}"[:40]
    db["doctors"].insert_one({"doctor_id": doctor_id, "name": (user or {}).get("name") or email,
                              "specialty": "Internal Medicine", "email": email, "clinical_groups": [],
                              "active": True, "hospital_id": hospital,
                              "registered_at": datetime.now(timezone.utc), "registered_by": "scripts/prepare_demo.py"})
    db[STATE].insert_one({"hospital_id": hospital, "kind": "doctor", "doctor_id": doctor_id})
    return doctor_id


# ------------------------------------------------------------ doing and undoing
def prepare(db, hospital: str, today=None, write: bool = False, doctor: str = None) -> list:
    """Move the chosen patients to around today, and with `doctor` (an email)
    give them all to that doctor. Returns the plan:
    [(scene, patient_id, new discharge date, shift in days)]."""
    from ingestion import scheduler
    today = today or scheduler.today()
    if write and db[STATE].count_documents({"hospital_id": hospital}):
        undo(db, hospital)
    doctor_id = _demo_doctor(db, hospital, doctor) if (write and doctor) else None
    plan = []
    for scene, pid in choose(db, hospital):
        days, keep = SCENES[scene]
        row = db["patient_worklist"].find_one({"patient_id": {"$in": _variants(pid)}},
                                              {"discharge_date": 1}, sort=[("batch_date", -1)])
        target = today - timedelta(days=days)
        shift = (target - _as_date(row["discharge_date"])).days
        plan.append((scene, pid, target.isoformat(), shift))
        if not write:
            continue
        set_aside = list(db[WEEKLY].find({"patient_id": {"$in": _variants(pid)}, "week_number": {"$gt": keep}})
                         if keep is not None else [])
        if set_aside:
            db[WEEKLY].delete_many({"_id": {"$in": [d["_id"] for d in set_aside]}})
        alerts = list(db["clinical_alerts"].find({"patient_id": {"$in": _variants(pid)}}))
        notes = list(db["notifications"].find({"patient_id": {"$in": _variants(pid)}}))
        for name, docs in (("clinical_alerts", alerts), ("notifications", notes)):
            if docs:
                db[name].delete_many({"_id": {"$in": [d["_id"] for d in docs]}})
        _move(db, pid, shift)
        before = (db["care_actions"].find_one({"patient_id": {"$in": _variants(pid)}},
                                              {"assigned_doctor_id": 1}) or {}).get("assigned_doctor_id")
        if doctor_id:
            db["care_actions"].update_one({"patient_id": {"$in": _variants(pid)}},
                                          {"$set": {"assigned_doctor_id": doctor_id}})
        db[STATE].insert_one({"hospital_id": hospital, "patient_id": pid, "scene": scene, "shift_days": shift,
                              "discharge_date": target.isoformat(), "set_aside": set_aside,
                              "alerts_aside": alerts, "notifications_aside": notes,
                              **({"doctor_before": before} if doctor_id else {}),
                              "prepared_at": datetime.now(timezone.utc),
                              # Alerts are stamped on the server's local clock (doctor_service._now).
                              "prepared_local": datetime.now().strftime("%Y-%m-%d %H:%M:%S")})
    if write:
        _refresh(db, [p for _, p, _, _ in plan])
    return plan


def undo(db, hospital: str) -> list:
    """Put back every patient the last run moved, and clear what the demo left."""
    done = []
    for st in list(db[STATE].find({"hospital_id": hospital, "kind": {"$ne": "doctor"}})):
        pid, ids = st["patient_id"], _variants(st["patient_id"])
        since, since_local = st["prepared_at"], st.get("prepared_local", "9999")
        db[WEEKLY].delete_many({"patient_id": {"$in": ids}, "reported_by": {"$exists": True}})
        for name in LEFTOVERS:
            db[name].delete_many({"patient_id": {"$in": ids}})
        for name in ("clinical_alerts", "notifications"):
            db[name].delete_many({"patient_id": {"$in": ids}, "created_at": {"$gte": since_local}})
        db["followups"].delete_many({"patient_id": {"$in": ids}, "requested_at": {"$gte": since}})
        _move(db, pid, -int(st["shift_days"]))
        if st.get("set_aside"):
            db[WEEKLY].insert_many(st["set_aside"])
        for name, key in (("clinical_alerts", "alerts_aside"), ("notifications", "notifications_aside")):
            if st.get(key):
                db[name].insert_many(st[key])
        if "doctor_before" in st:
            db["care_actions"].update_one({"patient_id": {"$in": ids}},
                                          {"$set": {"assigned_doctor_id": st["doctor_before"]}})
        db[STATE].delete_one({"_id": st["_id"]})
        done.append(pid)
    # A doctor the script registered goes again, unless someone has since
    # given them a patient of their own.
    for st in list(db[STATE].find({"hospital_id": hospital, "kind": "doctor"})):
        if not db["care_actions"].count_documents({"assigned_doctor_id": st["doctor_id"]}):
            db["doctors"].delete_one({"doctor_id": st["doctor_id"], "registered_by": "scripts/prepare_demo.py"})
        db[STATE].delete_one({"_id": st["_id"]})
    _refresh(db, done)
    return done


def _move(db, pid: str, days: int) -> None:
    ids = _variants(pid)
    for row in db["patient_worklist"].find({"patient_id": {"$in": ids}}, {"admit_date": 1, "discharge_date": 1}):
        db["patient_worklist"].update_one({"_id": row["_id"]}, {"$set": {
            "admit_date": _shift(row.get("admit_date"), days),
            "discharge_date": _shift(row.get("discharge_date"), days)}})
    for doc in db[WEEKLY].find({"patient_id": {"$in": ids}}, {"week_date": 1}):
        db[WEEKLY].update_one({"_id": doc["_id"]}, {"$set": {"week_date": _shift(doc.get("week_date"), days)}})


def _refresh(db, pids: list) -> None:
    from api.db_utils import get_latest_batch_date
    from api.worklist_summary import refresh
    batch = get_latest_batch_date(db, "patient_worklist")
    if pids and batch:
        refresh(db, batch, pids)


# ------------------------------------------------------------ the cheat sheet
SAY = {
    "checkin": "Sign in as {login} -> do this week's check-in (try oxygen 88 for the red flag)",
    "quiet": "Patients -> Needs Attention: 'No readings for 11 days since week 1'",
    "never": "Patients -> Needs Attention: 'No readings for 12 days since discharge'",
    "active": "On track: readings every week to yesterday",
    "outcome": "Open it -> 30-day outcome -> record a readmission or 'Not readmitted'",
}


def cheat_sheet(db, plan: list, case_manager: str = None, doctor: str = None) -> str:
    from bson import ObjectId
    from bson.errors import InvalidId
    from api import auth
    lines = []
    if case_manager or doctor:
        lines.append("  Who presents:")
        if case_manager:
            lines.append(f"    Tab A  {case_manager:<34} case manager - sees every patient in the hospital")
        lines.append(f"    Tab B  {'(the patient login below)':<34} sends the check-in")
        if doctor:
            lines.append(f"    Tab C  {doctor:<34} doctor - assigned all six patients, gets the alert")
        lines.append("")
    for scene, pid, discharged, _ in plan:
        login = ""
        own = db["care_actions"].find_one({"patient_id": {"$in": _variants(pid)}},
                                          {"patient_account_id": 1, "assigned_doctor_id": 1}) or {}
        if scene == "checkin":
            try:
                user = auth.users(db).find_one({"_id": ObjectId(own.get("patient_account_id"))}, {"email": 1})
            except (InvalidId, TypeError):
                user = None
            login = (user or {}).get("email") or "(the patient linked to it)"
        lines.append(f"  {scene:<8} {pid:<16} home {discharged}   {SAY[scene].format(login=login)}")
        if scene == "checkin" and not doctor:
            # Who the red-flag alert goes to: sign in as them to show it arrive.
            assigned = db["doctors"].find_one({"doctor_id": own.get("assigned_doctor_id")}, {"email": 1, "name": 1})
            if assigned:
                lines.append(f"  {'':<8} {'':<16} {'':<16}   Their doctor, who gets the alert: "
                             f"{assigned.get('name', '')} - {assigned.get('email', '')}")
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.strip().split("\n\n")[0])
    ap.add_argument("--hospital", default="your-hospital-name")
    ap.add_argument("--yes", action="store_true", help="write; without it, only show what would change")
    ap.add_argument("--undo", action="store_true", help="put back what the last run moved")
    ap.add_argument("--case-manager", help="the case manager login you will present with (checked)")
    ap.add_argument("--doctor", help="the doctor login you will present with; gets all six patients")
    a = ap.parse_args()
    from dotenv import load_dotenv
    load_dotenv()
    from api.main import db  # noqa: E402  - the same database the API uses
    print(f"database: {db.name}   hospital: {a.hospital}")
    if a.undo:
        if not a.yes:
            n = db[STATE].count_documents({"hospital_id": a.hospital, "kind": {"$ne": "doctor"}})
            print(f"would put back {n} patient(s); add --yes")
            return
        print(f"put back {len(undo(db, a.hospital))} patient(s)")
        return
    problems = check_staff(db, a.hospital, a.case_manager, a.doctor)
    if problems:
        print("Fix these first - nothing was written:")
        print("\n".join(f"  - {p}" for p in problems))
        sys.exit(1)
    if not a.yes and db[STATE].count_documents({"hospital_id": a.hospital}):
        print("(a demo is set up now; --yes puts those patients back first, then sets them up fresh)")
    plan = prepare(db, a.hospital, write=a.yes, doctor=a.doctor)
    if not plan:
        print("no demo patients found in this hospital")
        return
    print(cheat_sheet(db, plan, a.case_manager, a.doctor))
    print("\nready for the demo." if a.yes else "\nnothing written - add --yes to do it.")


if __name__ == "__main__":
    main()

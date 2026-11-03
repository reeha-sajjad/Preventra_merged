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

The first "active" patient and the "outcome" patient also get four weeks of
simulated mood answers (marked simulated everywhere they show), so the week card,
the forecast and Outcomes -> Mental health have something to show; the check-in
patient answers the mood questions live.

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

With --count N, N more demo patients move too (up to 500; 200 fills the Outcomes
page): half still inside their 30 days, on track (the last reading under a week old), and half
past day 30 with a SIMULATED outcome. Each outcome is drawn from the patient's own
discharge risk score (High mostly readmitted, Low rarely, at least 11 of each so the
Who comes back panels can show), a readmission falls in week 1, 2 or 3 and always
after a week-1 reading that stays. The patients are spread over risk bands and
conditions, and each outcome is labelled "Recorded by Demo data (simulated)" on the
patient's page; Outcomes says how many are simulated. An alert about a week set
aside goes with it; the rest stay. These ride the same put-back: a re-run or --undo
returns them - so repeat --count N on every run that should keep them.

    .venv/bin/python scripts/prepare_demo.py --count 200 --yes   # the six, plus 200 more
"""
from __future__ import annotations

import argparse
import hashlib
import os
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

STATE = "demo_prep"
WEEKLY = "weekly_monitoring"
# scene -> (days since discharge, the last week kept: None keeps them all)
SCENES = {"checkin": (3, 0), "quiet": (18, 1), "never": (12, 0), "active": (29, None), "outcome": (40, None)}
ORDER = ("checkin", "quiet", "never", "active", "active", "outcome")
# Simulated mood answers (models/mood_screen.py) for the first patient of each of these
# scenes: week -> answers. Week 4 is PHQ-8 13, up 6 from week 3 (7).
DEMO_MOOD = {1: {"phq_interest": 1, "phq_down": 0, "gad_nervous": 1, "gad_worry": 0},
             2: {"phq_interest": 1, "phq_down": 1, "gad_nervous": 1, "gad_worry": 1},
             3: {"phq_interest": 2, "phq_down": 1, "phq_sleep": 1, "phq_tired": 1, "phq_appetite": 1,
                 "phq_failure": 0, "phq_concentration": 1, "phq_movement": 0, "gad_nervous": 2, "gad_worry": 1},
             4: {"phq_interest": 2, "phq_down": 2, "phq_sleep": 2, "phq_tired": 2, "phq_appetite": 1,
                 "phq_failure": 2, "phq_concentration": 1, "phq_movement": 1, "gad_nervous": 2, "gad_worry": 2}}
MOOD_SCENES = ("active", "outcome")   # the first of each
# What a demo leaves on these patients, cleared when it is put back.
LEFTOVERS = ("checkins", "discharge_plans", "readmission_outcomes", "risk_watch_seen")

# --count: the more patients. Share of each discharge band (the Outcomes page needs all three).
MAX_COUNT = 500
BULK_MIX = (("High", 0.30), ("Medium", 0.35), ("Low", 0.35))
DEMO_BY = {"id": "scripts/prepare_demo.py", "name": "Demo data (simulated)", "role": "script"}
# A patient with any of these is a real person's record: left alone.
KEPT_OUT = ("readmission_outcomes", "checkins", "discharge_plans")
NOTES = {True: "Demo data: a simulated 30-day outcome drawn from this patient's discharge risk score; "
                "the day is chosen at random from week 1 on.",
         False: "Demo data: a simulated 30-day outcome drawn from this patient's discharge risk score."}


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
    plan, mooded = [], set()
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
        if scene in MOOD_SCENES and scene not in mooded:
            _add_mood(db, hospital, pid, target)
            mooded.add(scene)
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


def _add_mood(db, hospital: str, pid: str, target) -> None:
    """Simulated mood answers on the weeks this patient has, with check-in records
    for Outcomes -> Mental health (labelled simulated, never counted as check-ins sent).
    No alert is raised: the demo's alerts come from what the presenter does."""
    from models.mood_screen import assess
    history = []
    for doc in db[WEEKLY].find({"patient_id": {"$in": _variants(pid)}, "week_number": {"$in": list(DEMO_MOOD)},
                                "observed": {"$ne": False}}).sort("week_number", 1):
        n = int(doc["week_number"])
        mood = assess(DEMO_MOOD[n], history, n, (doc.get("monitoring") or {}).get("adherence_pct"), simulated=True)
        history.append(mood)
        db[WEEKLY].update_one({"_id": doc["_id"]}, {"$set": {"mood": mood}})
        day = _as_date(doc.get("week_date"))
        if day is None:
            continue
        db["checkins"].insert_one({
            "patient_id": str(pid), "discharge_date": target.isoformat(), "week_number": n,
            "week_date": day.isoformat(), "at": datetime(day.year, day.month, day.day, 12, tzinfo=timezone.utc),
            "by_role": "patient", "by_user": "scripts/prepare_demo.py", "answered": [], "mood": mood,
            "red_flags": [], "hospital_id": hospital, "simulated": True})


def undo(db, hospital: str) -> list:
    """Put back every patient the last run moved, and clear what the demo left."""
    done = []
    for st in list(db[STATE].find({"hospital_id": hospital, "kind": {"$ne": "doctor"}, "scene": {"$ne": "bulk"}})):
        pid, ids = st["patient_id"], _variants(st["patient_id"])
        since, since_local = st["prepared_at"], st.get("prepared_local", "9999")
        db[WEEKLY].delete_many({"patient_id": {"$in": ids}, "reported_by": {"$exists": True}})
        db[WEEKLY].update_many({"patient_id": {"$in": ids}, "mood.simulated": True}, {"$unset": {"mood": ""}})
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
    _refresh(db, done)              # the scenes are back for good, whatever happens to the rest
    done += _undo_bulk(db, hospital)
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


# ------------------------------------------------------------ the more patients (--count)
def _rank(pid, salt: str = "bulk") -> str:
    """A fixed shuffle: the same patients in the same order every run (never hash(), which varies)."""
    return hashlib.sha256(f"{salt}:{pid}".encode()).hexdigest()


def _uniform(pid, salt: str) -> float:
    return int(_rank(pid, salt)[:12], 16) / 16 ** 12


def _readmit_day(pid) -> tuple:
    """(week, day) a readmitted patient came back: week 1-3, day 8-27 - after that
    week's reading (day 7 x week), which stays, and inside the 30 days."""
    week = 1 + int(_rank(pid, "week"), 16) % 3
    return week, 7 * week + 1 + int(_rank(pid, "day"), 16) % 6


def _quiet(weeks: dict, days: int) -> bool:
    """Would a patient home `days` days, with readings only up to the last whole week, look gone quiet:
    that last week was no reading, or it is more than the calendar allows ago."""
    from models.monitoring_rules import CONTACT_DUE_DAYS
    offset, observed = weeks[max(w for w in weeks if w <= (days - 1) // 7)]
    return not observed or days - offset > CONTACT_DUE_DAYS


def _draw_outcomes(past: list) -> int:
    """Sets `readmitted` on each patient (a dict) past their 30 days: readmitted when a
    fixed draw falls under the patient's own discharge score. A group too small for the
    panels to show is topped up, the likeliest first (High before Medium before Low, then
    the higher score): a fixed number of each, so the page is never empty. Returns how
    many were turned over to get there."""
    from api import outcomes
    for e in past:
        e["readmitted"] = _uniform(e["patient_id"], "outcome") < e["score"] / 100
    need, turned = outcomes.MIN_GROUP, 0
    if len(past) >= 2 * need:
        while sum(1 for e in past if e["readmitted"]) < need:
            nxt = min((e for e in past if not e["readmitted"]),
                      key=lambda e: (e["band"] != "High", e["band"] != "Medium", -e["score"], _rank(e["patient_id"])))
            nxt["readmitted"], turned = True, turned + 1
        while sum(1 for e in past if not e["readmitted"]) < need:
            nxt = min((e for e in past if e["readmitted"]), key=lambda e: (e["score"], _rank(e["patient_id"])))
            nxt["readmitted"], turned = False, turned + 1
    return turned


def choose_bulk(db, hospital: str, count: int, taken, today) -> tuple:
    """(plan, report): `count` more MIMIC patients of this hospital, spread over risk
    bands and conditions, the same ones every time. Half live (inside the 30 days, home
    1-30 days), half past day 30 (home 31-60) with a drawn outcome. Plan entries are
    dicts. A patient with a login, a caretaker, a care-team record or a reading of their
    own is left alone, and so is one with no week-1 reading (a readmission needs a
    reading before it to be compared). Reads only."""
    from api.db_utils import get_latest_batch_date
    report = {"asked": count, "found": 0, "linked": 0, "kept_out": 0, "no_week1": 0, "topped_up": 0,
              "by_band": {}, "by_group": {}}
    batch = get_latest_batch_date(db, "patient_worklist")
    if count <= 0 or not batch:
        return [], report
    pids, linked = set(), 0
    for o in db["care_actions"].find({"hospital_id": hospital},
                                     {"patient_id": 1, "patient_account_id": 1, "caretaker_ids": 1}):
        pid = str(o["patient_id"])
        if not pid.startswith("MIMIC-") or pid in taken:
            continue
        if o.get("patient_account_id") or o.get("caretaker_ids"):
            linked += 1
        else:
            pids.add(pid)
    pids = sorted(pids)
    report["linked"] = linked
    # Patients this script already moved: their dates, and the weeks it set aside, as they were.
    prepared = {str(s["patient_id"]): s for s in db[STATE].find(
        {"hospital_id": hospital, "kind": {"$ne": "doctor"}, "patient_id": {"$in": pids}})}
    busy = set()
    for name in KEPT_OUT:
        busy.update(str(p) for p in db[name].distinct("patient_id", {"patient_id": {"$in": pids}}))
    busy.update(str(p) for p in db[WEEKLY].distinct(
        "patient_id", {"patient_id": {"$in": pids}, "reported_by": {"$exists": True}}))
    busy -= set(prepared)
    rows = {str(r["patient_id"]): r for r in db["patient_worklist"].find(
        {"batch_date": batch, "patient_id": {"$in": pids}},
        {"_id": 0, "patient_id": 1, "risk_band": 1, "risk_score": 1, "clinical_group": 1, "clinical_groups": 1,
         "discharge_date": 1})}
    # Each patient's weeks 0-4 as {week: (days after the original discharge, whether it was a reading)}.
    weeks: dict = {}
    for w in db[WEEKLY].find({"patient_id": {"$in": pids}, "week_number": {"$in": list(range(5))}},
                             {"_id": 0, "patient_id": 1, "week_number": 1, "observed": 1, "week_date": 1,
                              "reported_by": 1}):
        pid, row = str(w["patient_id"]), rows.get(str(w["patient_id"]))
        start = _as_date(row.get("discharge_date")) if row else None
        if start and not w.get("reported_by") and _as_date(w.get("week_date")):
            weeks.setdefault(pid, {})[int(w["week_number"])] = (
                (_as_date(w["week_date"]) - start).days, w.get("observed", True) is not False)
    for pid, st in prepared.items():             # the weeks it set aside still have their original dates
        start = _as_date(rows[pid].get("discharge_date")) if pid in rows else None
        for w in st.get("set_aside") or []:
            if start and 0 <= int(w.get("week_number", -1)) <= 4 and not w.get("reported_by") \
                    and _as_date(w.get("week_date")):
                original = start - timedelta(days=int(st.get("shift_days") or 0))
                weeks.setdefault(pid, {}).setdefault(int(w["week_number"]), (
                    (_as_date(w["week_date"]) - original).days, w.get("observed", True) is not False))
    pool = []
    for pid in pids:
        if pid in busy:
            report["kept_out"] += 1
            continue
        seen, row = weeks.get(pid, {}), rows.get(pid)
        # Week 1 is a reading, taken in the first week home: a readmission after it has something to compare.
        if row and _as_date(row.get("discharge_date")) and 0 in seen and 1 in seen and seen[1][1] \
                and 1 <= seen[1][0] <= 7:
            pool.append(pid)
        else:
            report["no_week1"] += 1
    pool.sort(key=_rank)
    report["found"] = len(pool)
    band = {p: str(rows[p].get("risk_band") or "").title() for p in pool}
    group = {p: str(rows[p].get("clinical_group") or "general") for p in pool}
    groups = {p: [g for g in (rows[p].get("clinical_groups") or [rows[p].get("clinical_group")])
                  if g and g != "general"] for p in pool}

    # Each band its share, taking turns between that band's conditions.
    picked = []
    for name, share in BULK_MIX:
        lists = {}
        for p in pool:
            if band[p] == name:
                lists.setdefault(group[p], []).append(p)
        queues, quota, got = [lists[g] for g in sorted(lists)], round(count * share), 0
        while got < quota and any(queues):
            for q in queues:
                if q and got < quota:
                    picked.append(q.pop(0))
                    got += 1
    chosen = set(picked)
    picked += [p for p in pool if p not in chosen]          # a band ran short: whoever is next
    picked = picked[:count]
    order = {name: i for i, (name, _) in enumerate(BULK_MIX)}
    picked.sort(key=lambda p: (order.get(band[p], len(order)), group[p], _rank(p)))

    plan = []
    for half, first in ((picked[0::2], 1), (picked[1::2], 31)):
        half = sorted(half, key=lambda p: _rank(p, "days"))
        for j, pid in enumerate(half):
            days = first + (j * 30) // len(half)
            quiet = False
            if first == 1:                      # still being monitored: not shown as gone quiet
                near = sorted(range(1, 31), key=lambda d: (abs(d - days), d))
                days = next((d for d in near if not _quiet(weeks[pid], d)), days)
                quiet = _quiet(weeks[pid], days)
            target = today - timedelta(days=days)
            shift = (target - _as_date(rows[pid]["discharge_date"])).days
            if pid in prepared:                              # the date as it was before this script moved it
                shift += int(prepared[pid].get("shift_days") or 0)
            plan.append({"patient_id": pid, "band": band[pid], "group": group[pid], "groups": groups[pid],
                         "score": float(rows[pid].get("risk_score") or 0), "days": days, "target": target,
                         "shift": shift, "live": first == 1, "quiet": quiet, "readmitted": None, "keep": (days - 1) // 7,
                         "readmit_date": None})
    past = [e for e in plan if not e["live"]]
    report["topped_up"] = _draw_outcomes(past)
    for e in past:
        if e["readmitted"]:
            week, day = _readmit_day(e["patient_id"])
            e["keep"], e["readmit_date"] = week, e["target"] + timedelta(days=day)
        else:
            e["keep"] = None
    for e in plan:
        report["by_band"][e["band"]] = report["by_band"].get(e["band"], 0) + 1
        report["by_group"][e["group"]] = report["by_group"].get(e["group"], 0) + 1
    return plan, report


def existing_outcomes(db, hospital: str) -> int:
    """Known 30-day outcomes in this hospital that are not demo data: they join the Outcomes panels too."""
    return (db["readmission_outcomes"].count_documents({"hospital_id": hospital, "demo": {"$ne": True}})
            + db["ingest_stays"].count_documents({"hospital_id": hospital, "readmitted_30d": {"$ne": None}}))


def forecast_panels(plan: list, existing: int = 0) -> dict:
    """What the three Who comes back panels will show, from the plan alone (exact for the
    demo patients; `existing` other outcomes may change it). Same keys as panel_check."""
    from api import outcomes
    from models.icd_groups import GROUP_LABELS
    past = [e for e in plan if not e["live"]]
    back = sum(1 for e in past if e["readmitted"])
    known, need = len(past) + existing, outcomes.MIN_GROUP
    held = {}
    for e in past:
        for g in e["groups"]:
            held[g] = held.get(g, 0) + 1
    return {"checked": False, "known": known, "enough": known >= need,
            "who": [label for _, label, _ in outcomes.DIMENSIONS] if known >= need else [],
            "conditions": [label for g, label in sorted(GROUP_LABELS.items(), key=lambda kv: -held.get(kv[0], 0))
                           if g != "general" and need <= held.get(g, 0) <= known - need],
            "readings": back >= need and len(past) - back >= need, "readmitted": back,
            "not_readmitted": len(past) - back}


def panel_check(db, hospital: str, today) -> dict:
    """What the three panels show now, as the hospital's case manager sees them (read-only)."""
    from api import access, outcomes
    access.forget_hospital_lists()
    user = {"_id": "scripts/prepare_demo.py", "role": "case_manager", "hospital_id": hospital}
    view = access.ScopedDB(db, user, access.patient_scope(db, user))
    detail = outcomes.page(db, view, user, 30, today)["readmission_detail"]
    if not detail["enough"]:
        return {"checked": True, "known": detail["known"], "enough": False, "who": [], "conditions": [],
                "readings": False}
    lr = detail["last_readings"]
    return {"checked": True, "known": detail["known"], "enough": True,
            "who": [d["label"] for d in detail["who"]
                    if any(c["hidden"] is None and c["known"] > 0 for c in d["groups"])],
            "conditions": [c["label"] for c in detail["conditions"]["conditions"] if c["hidden"] is None],
            "readings": lr["hidden"] is None, "readmitted": lr["readmitted"]["with_readings"],
            "not_readmitted": lr["not_readmitted"]["with_readings"]}


def prepare_bulk(db, hospital: str, count: int, today=None, write: bool = False, taken=()) -> tuple:
    """Move the `choose_bulk` patients to around today and give those past day 30 their
    drawn outcome. Returns (plan, report). The put-back record is written first, so a
    crash part-way is put right by --undo or by running again."""
    from pymongo import UpdateOne
    from api import outcomes
    from ingestion import scheduler
    today = today or scheduler.today()
    if write and db[STATE].count_documents({"hospital_id": hospital, "scene": "bulk"}):
        raise RuntimeError("put the last run back first")
    plan, report = choose_bulk(db, hospital, count, set(taken), today)
    if not write or not plan:
        return plan, report
    pids = [e["patient_id"] for e in plan]
    weekly, rows, alerts, notes = {}, {}, {}, {}
    for w in db[WEEKLY].find({"patient_id": {"$in": pids}}):
        weekly.setdefault(str(w["patient_id"]), []).append(w)
    for r in db["patient_worklist"].find({"patient_id": {"$in": pids}}, {"admit_date": 1, "discharge_date": 1,
                                                                         "patient_id": 1}):
        rows.setdefault(str(r["patient_id"]), []).append(r)
    with_end = [e["patient_id"] for e in plan if e["keep"] is not None]
    for a in db["clinical_alerts"].find({"patient_id": {"$in": with_end}}):
        alerts.setdefault(str(a["patient_id"]), []).append(a)
    # An alert about a week that is set aside goes with it (the nightly sweep would only raise it
    # again); the others stay, and nothing else the patient has is touched.
    keep_of = {e["patient_id"]: e["keep"] for e in plan}
    aside_alerts = {pid: [a for a in found if a.get("week_number") is not None and int(a["week_number"]) > keep_of[pid]]
                    for pid, found in alerts.items()}
    for n in db["notifications"].find({"alert_id": {"$in": [a["alert_id"] for found in aside_alerts.values()
                                                           for a in found if a.get("alert_id")]}}):
        notes.setdefault(n["alert_id"], []).append(n)
    now, local = datetime.now(timezone.utc), datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    states, gone = [], {WEEKLY: [], "clinical_alerts": [], "notifications": []}
    moves = {"patient_worklist": [], WEEKLY: []}
    for e in plan:
        pid, keep = e["patient_id"], e["keep"]
        mine = weekly.get(pid, [])
        aside = [w for w in mine if keep is not None and int(w.get("week_number", 0)) > keep]
        left = [w for w in mine if w not in aside]
        old = aside_alerts.get(pid, [])
        old_notes = [n for a in old for n in notes.get(a.get("alert_id"), [])]
        gone[WEEKLY] += [w["_id"] for w in aside]
        gone["clinical_alerts"] += [a["_id"] for a in old]
        gone["notifications"] += [n["_id"] for n in old_notes]
        states.append({
            "hospital_id": hospital, "patient_id": pid, "scene": "bulk", "shift_days": e["shift"],
            "discharge_date": e["target"].isoformat(), "days_since_discharge": e["days"],
            "rows_before": [{"_id": r["_id"], **{k: r[k] for k in ("admit_date", "discharge_date") if k in r}}
                            for r in rows.get(pid, [])],
            "weeks_before": [{"_id": w["_id"], "week_date": w.get("week_date")} for w in left],
            "set_aside": aside, "alerts_aside": old, "notifications_aside": old_notes,
            "outcome": ({"readmitted": e["readmitted"],
                         "readmit_date": e["readmit_date"].isoformat() if e["readmit_date"] else None}
                        if not e["live"] else None),
            "prepared_at": now, "prepared_local": local})
        for r in rows.get(pid, []):
            dates = {k: _shift(r[k], e["shift"]) for k in ("admit_date", "discharge_date") if k in r}
            if dates:
                moves["patient_worklist"].append(UpdateOne({"_id": r["_id"]}, {"$set": dates}))
        for w in left:
            moves[WEEKLY].append(UpdateOne({"_id": w["_id"]}, {"$set": {"week_date": _shift(w.get("week_date"),
                                                                                             e["shift"])}}))
    db[STATE].insert_many(states)
    for name, ids in gone.items():
        if ids:
            db[name].delete_many({"_id": {"$in": ids}})
    for name, ops in moves.items():
        for i in range(0, len(ops), 1000):
            db[name].bulk_write(ops[i:i + 1000], ordered=False)
    past = [e for e in plan if not e["live"]]
    if past:
        outcomes.ensure_indexes(db)
        ops = [UpdateOne({"patient_id": e["patient_id"], "discharge_date": e["target"].isoformat()}, {"$set": {
            "readmitted": e["readmitted"], "readmit_date": e["readmit_date"].isoformat() if e["readmit_date"] else None,
            "note": NOTES[e["readmitted"]], "discharge_score": e["score"], "discharge_band": e["band"], "hospital_id": hospital,
            "recorded_by": DEMO_BY, "recorded_at": now, "demo": True, "outcome_simulated": True}}, upsert=True)
            for e in past]
        for i in range(0, len(ops), 1000):
            db["readmission_outcomes"].bulk_write(ops[i:i + 1000], ordered=False)
    _refresh(db, pids)
    return plan, report


def _undo_bulk(db, hospital: str) -> list:
    """Put back every patient --count moved. Batched, and every step sets the value it was
    (or deletes), so it can be repeated after a crash."""
    from pymongo import ReplaceOne, UpdateOne
    from api.access import id_variants
    states = list(db[STATE].find({"hospital_id": hospital, "scene": "bulk"}))
    if not states:
        return []
    pids = [st["patient_id"] for st in states]
    ids = id_variants(pids)
    since = min(st["prepared_at"] for st in states)
    since_local = min(st.get("prepared_local", "9999") for st in states)
    db[WEEKLY].delete_many({"patient_id": {"$in": ids}, "reported_by": {"$exists": True}})
    db[WEEKLY].update_many({"patient_id": {"$in": ids}, "mood.simulated": True}, {"$unset": {"mood": ""}})
    for name in LEFTOVERS:
        if name != "risk_watch_seen":
            db[name].delete_many({"patient_id": {"$in": ids}})
    # Which weeks someone opened in the risk bell is not kept in demo_prep, so only what was marked since
    # the run goes (risk_watch.mark_seen stamps seen_at as a UTC ISO string).
    started = (since if since.tzinfo else since.replace(tzinfo=timezone.utc)).astimezone(timezone.utc).isoformat()
    db["risk_watch_seen"].delete_many({"patient_id": {"$in": ids}, "seen_at": {"$gte": started}})
    for name in ("clinical_alerts", "notifications"):
        db[name].delete_many({"patient_id": {"$in": ids}, "created_at": {"$gte": since_local}})
    db["followups"].delete_many({"patient_id": {"$in": ids}, "requested_at": {"$gte": since}})
    restore = {"patient_worklist": [], WEEKLY: []}
    for st in states:
        restore["patient_worklist"] += [
            UpdateOne({"_id": r["_id"]}, {"$set": {k: v for k, v in r.items() if k != "_id"}})
            for r in st.get("rows_before") or [] if len(r) > 1]
        restore[WEEKLY] += [UpdateOne({"_id": w["_id"]}, {"$set": {"week_date": w["week_date"]}})
                            for w in st.get("weeks_before") or []]
    for name, ops in restore.items():
        for i in range(0, len(ops), 1000):
            db[name].bulk_write(ops[i:i + 1000], ordered=False)
    for name, key in ((WEEKLY, "set_aside"), ("clinical_alerts", "alerts_aside"),
                      ("notifications", "notifications_aside")):
        ops = [ReplaceOne({"_id": d["_id"]}, d, upsert=True) for st in states for d in st.get(key) or []]
        for i in range(0, len(ops), 1000):
            db[name].bulk_write(ops[i:i + 1000], ordered=False)
    db[STATE].delete_many({"_id": {"$in": [st["_id"] for st in states]}})
    return pids


# ------------------------------------------------------------ the cheat sheet
SAY = {
    "checkin": "Sign in as {login} -> do this week's check-in (try oxygen 88 for the red flag; "
               "'More than half the days' to both mood questions opens the rest of the PHQ-8)",
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
        moods = list(db[WEEKLY].find({"patient_id": {"$in": _variants(pid)}, "mood.simulated": True},
                                     {"week_number": 1, "mood": 1}).sort("week_number", 1))
        if moods:
            last = moods[-1]["mood"]
            phq8 = (f" PHQ-8 {last['phq8']}, {last['phq8_band']}"
                    f"{', up ' + str(last['change']) if (last.get('change') or 0) > 0 else ''}"
                    if last.get("phq8") is not None else "")
            lines.append(f"  {'':<8} {'':<16} {'':<16}   Mood answers (simulated) in weeks "
                         f"{moods[0]['week_number']}-{moods[-1]['week_number']}: week {moods[-1]['week_number']}{phq8}"
                         f" - week cards, the forecast, Outcomes -> Mental health")
    return "\n".join(lines)


def bulk_sheet(db, scene_plan: list, plan: list, report: dict, panels: dict, existing: int = 0) -> str:
    """What --count did (or would do), in words, for the person about to present."""
    if not plan and not report["asked"]:
        return ""
    live = [e for e in plan if e["live"]]
    past = [e for e in plan if not e["live"]]
    back = sum(1 for e in past if e["readmitted"])
    head = f"  {report['asked']} more: {len(live)} being monitored (home 1-30 days, last reading under a week old)"
    if past:
        head += (f", {len(past)} past day 30 with a simulated 30-day outcome ({back} readmitted in weeks 1-3, "
                 f"{len(past) - back} not; expected {round(sum(e['score'] for e in past) / len(past))}% from their scores)")
    quiet = sum(1 for e in live if e["quiet"])
    if quiet:
        head += f" ({quiet} of them have gone quiet)"
    lines = [head]
    if len(plan) < report["asked"]:
        lines.append(f"    only {len(plan)} could be found: {report['found']} had a week-1 reading and no record "
                     f"of their own ({report['kept_out']} have care-team or patient records, {report['linked']} a login "
                     f"or caretaker, {report['no_week1']} no observed week-1 reading)")
    if plan:
        lines.append("    bands: " + ", ".join(f"{n} {b or 'unscored'}" for b, n in sorted(report["by_band"].items())))
        groups = sorted(report["by_group"].items(), key=lambda kv: (-kv[1], kv[0]))
        lines.append("    conditions: " + ", ".join(f"{n} {g.replace('_', ' ')}" for g, n in groups))
    if report["topped_up"]:
        lines.append(f"    {report['topped_up']} outcome(s) turned over so the panels have 11 readmitted and 11 not")
    if past:
        lines.append("    labelled 'Recorded by Demo data (simulated)' on each patient's page; "
                     "Outcomes says how many are simulated")
        status = ("now" if panels.get("checked") else "expected") + ":"
        if not panels.get("enough"):
            lines.append(f"    Who comes back {status} not enough known outcomes ({panels.get('known', 0)}) to compare")
        else:
            lines.append(f"    Who comes back {status} by who - {len(panels['who'])} of 5 tables; disease mix - "
                         f"{len(panels['conditions'])} condition(s) ({', '.join(panels['conditions']) or 'none'}); readings "
                         f"before readmission - {'shown' if panels['readings'] else 'WITHHELD'}")
        if existing:
            lines.append(f"    note: {existing} other known outcome(s) in this hospital count in these panels too"
                         f"{'' if panels.get('checked') else '; check again after --yes'}")
        have = db["model_inputs"].count_documents(
            {"patient_id": {"$in": [e["patient_id"] for e in past]}, "current": True})
        if have < len(past):
            lines.append("    Age, sex, insurance and destination in Who comes back need "
                         "scripts/backfill_model_inputs.py --yes")
    outcome = next((d for scene, _, d, _ in scene_plan if scene == "outcome"), None)
    if outcome and past:
        day = (datetime.strptime(outcome, "%Y-%m-%d") + timedelta(days=7)).strftime("%Y-%m-%d")
        lines.append(f"    If you record a readmission for the outcome scene, choose a date after its week-1 reading "
                     f"({day}) - a readmitted patient with no reading before it is a group of 1, and Readings "
                     "before readmission is then withheld")
    if plan:
        lines.append("    Needs Attention and doctors' bells now hold more patients - search for the scene ids")
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.strip().split("\n\n")[0])
    ap.add_argument("--hospital", default="your-hospital-name")
    ap.add_argument("--yes", action="store_true", help="write; without it, only show what would change")
    ap.add_argument("--undo", action="store_true", help="put back what the last run moved")
    ap.add_argument("--case-manager", help="the case manager login you will present with (checked)")
    ap.add_argument("--doctor", help="the doctor login you will present with; gets all six patients")
    ap.add_argument("--count", type=int, default=0,
                    help=f"also move this many more demo patients, half with a simulated 30-day outcome "
                         f"(0-{MAX_COUNT}; 200 fills Outcomes)")
    a = ap.parse_args()
    if not 0 <= a.count <= MAX_COUNT:
        print(f"--count must be between 0 and {MAX_COUNT}")
        sys.exit(1)
    from dotenv import load_dotenv
    load_dotenv()
    from api.main import db  # noqa: E402  - the same database the API uses
    from ingestion import scheduler
    print(f"database: {db.name}   hospital: {a.hospital}")
    bulk_now = db[STATE].count_documents({"hospital_id": a.hospital, "scene": "bulk"})
    if a.undo:
        if not a.yes:
            n = db[STATE].count_documents({"hospital_id": a.hospital, "kind": {"$ne": "doctor"}})
            print(f"would put back {n} patient(s) ({bulk_now} of them from --count); add --yes")
            return
        print(f"put back {len(undo(db, a.hospital))} patient(s)")
        return
    problems = check_staff(db, a.hospital, a.case_manager, a.doctor)
    if problems:
        print("Fix these first - nothing was written:")
        print("\n".join(f"  - {p}" for p in problems))
        sys.exit(1)
    if 0 < a.count < 44:
        print("(below 44 the past half cannot hold 11 readmitted and 11 not; the Who comes back panels may not show)")
    if not a.yes and db[STATE].count_documents({"hospital_id": a.hospital}):
        print("(a demo is set up now; --yes puts those patients back first, then sets them up fresh)")
    if a.count == 0 and bulk_now:
        print(f"(the demo set up now also has {bulk_now} more; --yes without --count puts them back - "
              "add --count N to keep them)")
    today = scheduler.today()
    plan = prepare(db, a.hospital, today, write=a.yes, doctor=a.doctor)
    bulk, report = prepare_bulk(db, a.hospital, a.count, today, a.yes, {p for _, p, _, _ in plan})
    if not plan and not bulk:
        print("no demo patients found in this hospital")
        return
    existing = existing_outcomes(db, a.hospital) if bulk else 0
    panels = (panel_check(db, a.hospital, today) if a.yes else forecast_panels(bulk, existing)) if bulk else {}
    print(cheat_sheet(db, plan, a.case_manager, a.doctor))
    if bulk or report["asked"]:
        print("\n" + bulk_sheet(db, plan, bulk, report, panels, existing))
    print("\nready for the demo." if a.yes else "\nnothing written - add --yes to do it.")


if __name__ == "__main__":
    main()

"""
The patient's mood screen: storage, the real events a low mood is paired with,
and what the care team sees of it. FLAG ONLY - the questions and flag rules are
core/mood_screen.py, and nothing here touches the dropout score.

The patient answers on their own home page, at most once every INTERVAL_DAYS
(a later answer than that is "due"); a caretaker never does, and the server
refuses them. The answers go to the care team and nobody else:

    doctor, nurse, case_manager   the Mood card on the patient's page, the bell
                                  and (case managers) the Today list - see
                                  core/access.MOOD_ROLES
    everyone else                 nothing - not the superadmin, hospital admins,
                                  insurers, pharmacies or caretakers, and not
                                  the patient, who sees only when they last
                                  answered and when the next questions are due

It has its own endpoints (routers/mood.py) and is never added to the patient
record (`GET /api/patients/{idx}`), the home page, the summary or any chatbot tool.

A low mood flags with a real event in the last EVENT_DAYS, found once when the
patient answers and kept on the screen as `trouble` codes (never returned):

    stopping_ask    a "thinking about stopping" ask by the patient or a caretaker
    missed_pickup   a pharmacy report of a missed refill pickup
    dose_gap        a linked caretaker and no caretaker dose tick for DOSE_GAP_DAYS
                    (counted from the link if nothing was ever ticked; never for
                    a patient whose medicine was stopped)

Stored in glp1_analytics.mood_screens, one document per screen. Nothing here
reads or writes anything else except the follow-up, dose, medicine and
caretaker records it looks at for those events.
"""

from datetime import date, datetime, timedelta, timezone
from typing import Optional

from fastapi import HTTPException
from pymongo.errors import DuplicateKeyError

from core import access, followups, mood_screen
from core.mongo import get_db, get_shared_identity_db

COLLECTION = "mood_screens"
INTERVAL_DAYS = 14             # how often the patient is asked
BELL_DAYS = 28                 # a screen older than this no longer rings a bell
EVENT_DAYS = 30                # how far back a stopping ask or missed pickup counts
DOSE_GAP_DAYS = 14             # no caretaker dose tick for this long
SHOWN = 12                     # screens on the staff card
# One screen per (patient, the screen before it), so two answers sent at the same
# moment cannot both go in; and the staff card's lookup.
INDEXES = (([("patient_idx", 1), ("previous_id", 1)], {"unique": True}),
           ([("patient_idx", 1), ("at", -1)], {}))
PATIENT_ONLY = "Questions about mood are answered by the patient themselves"
STAFF_ONLY = "Mood screens are for the patient's doctors, nurses and case managers"

_indexed: set = set()          # databases that have the indexes (one per test world, too)


def _col():
    return get_db()[COLLECTION]


async def ensure_indexes() -> None:
    key = id(get_db())
    if key not in _indexed:
        for keys, options in INDEXES:
            await _col().create_index(keys, **options)
        _indexed.add(key)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _aware(at: datetime) -> datetime:
    return at if at.tzinfo else at.replace(tzinfo=timezone.utc)


async def _screens(idx: int) -> list:
    """Every screen of a patient, oldest first."""
    docs = await _col().find({"patient_idx": int(idx)}).to_list(length=None)
    return sorted(docs, key=lambda d: _aware(d["at"]))


def _answered(screens: list) -> list:
    """The screens the patient answered, not simulated demo ones."""
    return [s for s in screens if not s.get("simulated")]


def _answered_on(doc: dict) -> date:
    return date.fromisoformat(doc["answered_on"]) if doc.get("answered_on") else _aware(doc["at"]).date()


def _due(last: Optional[dict], today: date) -> dict:
    """When they last answered, when the next questions are due, and whether that is now."""
    if not last:
        return {"last_answered": None, "next_due": None, "due": True}
    on = _answered_on(last)
    nxt = on + timedelta(days=INTERVAL_DAYS)
    return {"last_answered": on.isoformat(), "next_due": nxt.isoformat(), "due": today >= nxt}


# ------------------------------------------------------------------ the events
async def trouble_events(idx: int, now: datetime) -> list:
    """Real trouble with the treatment in the last EVENT_DAYS, as codes - the
    "trouble" a low mood is paired with. Filtered in Python, not with array
    operators, so it behaves the same on every driver."""
    idx, since, found = int(idx), now - timedelta(days=EVENT_DAYS), set()
    async for doc in get_db()[followups.COLLECTION].find(
            {"patient_idx": idx, "$or": [{"patient_requests.reason": "stopping"},
                                         {"pharmacy_reports.problem": "missed_pickup"}]}):
        if any(p.get("reason") == "stopping" and _aware(p["at"]) >= since for p in doc.get("patient_requests", [])):
            found.add("stopping_ask")
        if any(p.get("problem") == "missed_pickup" and _aware(p["at"]) >= since for p in doc.get("pharmacy_reports", [])):
            found.add("missed_pickup")
    if await _dose_gap(idx, now):
        found.add("dose_gap")
    return sorted(found)


async def _dose_gap(idx: int, now: datetime) -> bool:
    """A caretaker is linked, yet nobody has ticked a dose for DOSE_GAP_DAYS."""
    from core import medications                      # avoid an import cycle
    access_doc = await get_db().patient_access.find_one(
        {"patient_idx": idx}, {"caretaker_ids": 1, "caretaker_history": 1}) or {}
    carers = set(access_doc.get("caretaker_ids") or [])
    if not carers:
        return False
    if (await medications.history({"role": ""}, idx))["current_drug"] == "STOPPED":
        return False
    linked = [_aware(h["at"]) for h in access_doc.get("caretaker_history") or []
              if h.get("action") == "linked" and h.get("caretaker_id") in carers and h.get("at")]
    if not linked:
        return False                                  # link date unknown: do not guess
    ticks = [_aware(d["given_at"]) for d in await get_db()["dose_log"].find({"patient_idx": idx}).to_list(length=None)
             if "undone" not in d]
    start = max(max(ticks), min(linked)) if ticks else min(linked)
    return now - start >= timedelta(days=DOSE_GAP_DAYS)


# ----------------------------------------------------------- the patient's side
async def patient_state(user: dict, scope: Optional[list]) -> dict:
    """The patient's card: whether questions are due, and when they last
    answered. Never a score or a flag."""
    if user["role"] != "patient":
        raise HTTPException(status_code=403, detail=PATIENT_ONLY)
    idx = followups.own_patient(user, scope)
    own = _answered(await _screens(idx))
    state = _due(own[-1] if own else None, _now().date())
    return {**state, "interval_days": INTERVAL_DAYS,
            "form": mood_screen.form() if state["due"] else None, "safety": mood_screen.SAFETY}


async def submit(user: dict, scope: Optional[list], answers: dict) -> dict:
    """The patient answers. One set per INTERVAL_DAYS; the flags are worked out
    here and kept for the care team, and not sent back."""
    if user["role"] != "patient":
        raise HTTPException(status_code=403, detail=PATIENT_ONLY)
    idx = followups.own_patient(user, scope)
    try:
        cleaned = mood_screen.clean(answers)
    except ValueError as err:
        raise HTTPException(status_code=422, detail=str(err))
    if not cleaned:
        raise HTTPException(status_code=422, detail="Answer at least one question")
    await ensure_indexes()
    now = _now()
    screens = await _screens(idx)
    # What the patient answered themselves: a demo screen is not their last answer.
    # The race guard keys on that, so "none" stands for no real answer yet (demo
    # screens carry "demo:..." ids and never clash with it).
    own = _answered(screens)
    last = own[-1] if own else None
    state = _due(last, now.date())
    if not state["due"]:
        raise HTTPException(status_code=409, detail=(
            f"You answered these on {state['last_answered']}. The next questions are due on {state['next_due']}."))
    # Scored against their own answers only: a demo screen is neither a baseline nor a rise.
    result = mood_screen.assess(cleaned, own, now, await trouble_events(idx, now))
    doc = {"patient_idx": idx, "at": now, "answered_on": now.date().isoformat(), "by_role": "patient",
           "by_user": user["id"], "previous_id": str(last["_id"]) if last else "none", **result}
    try:
        await _col().insert_one(doc)
    except DuplicateKeyError:
        raise HTTPException(status_code=409, detail="Your answers are already in")
    return {"due": False, "last_answered": now.date().isoformat(),
            "next_due": (now.date() + timedelta(days=INTERVAL_DAYS)).isoformat(),
            "interval_days": INTERVAL_DAYS, "form": None, "safety": mood_screen.SAFETY,
            "sent": True, "message": "Thank you. Your care team will see this."}


# ------------------------------------------------------------- the staff's side
async def for_staff(user: dict, scope: Optional[list], idx: int) -> dict:
    """A patient's screens for the Mood card, newest first: totals, bands,
    change and flags - not the answers item by item."""
    if not access.sees_mood(user):
        raise HTTPException(status_code=403, detail=STAFF_ONLY)
    await access.require_detail(user, scope, idx)
    if user["role"] == "case_manager":
        # Opening the card clears the case manager's mood item (a doctor's or
        # nurse's is cleared by require_detail).
        from core import notifications                # avoid an import cycle
        await notifications.mark_seen(user, [idx])
    screens = await _screens(idx)
    own = _answered(screens)                          # due dates as the patient's own card has them
    state = _due(own[-1] if own else None, _now().date())
    return {"patient_idx": int(idx), "screens": [{**mood_screen.card(s), "id": str(s["_id"])} for s in reversed(screens[-SHOWN:])],
            **state, "interval_days": INTERVAL_DAYS}


# ------------------------------------------------------- bells and Today list
async def flagged(idxs, now: Optional[datetime] = None) -> dict:
    """patient_idx -> the latest screen, if it is recent and carries a flag that
    rings the bell. Only a patient's latest screen counts: a better screen after
    a flagged one clears it."""
    idxs = [int(i) for i in idxs]
    if not idxs:
        return {}
    since = (now or _now()) - timedelta(days=BELL_DAYS)
    docs = await _col().find({"patient_idx": {"$in": idxs}, "at": {"$gte": since}}).to_list(length=None)
    latest = {}
    for d in sorted(docs, key=lambda d: _aware(d["at"])):
        latest[int(d["patient_idx"])] = d
    out = {}
    for idx, d in latest.items():
        bell = [t for t in mood_screen.triggers(d) if t["code"] in mood_screen.BELL_FLAGS]
        if bell:
            out[idx] = {"at": _aware(d["at"]), "answered_on": d.get("answered_on"),
                        "codes": [t["code"] for t in bell], "title": bell[0]["title"],
                        "titles": [t["title"] for t in bell], "simulated": bool(d.get("simulated"))}
    return out


async def case_manager_items(user: dict, scope: Optional[list]) -> list:
    """A case manager's bell: flagged patients they have not looked at since the
    screen. A patient's main case manager rings, or every case manager of the
    hospital when they have none or have left (as followups.open_for_bell)."""
    hospital = user.get("hospital_id")
    marked = await flagged(scope or [])
    if not marked:
        return []
    owner = await followups.case_managers_of(marked)
    team = {str(u["_id"]) for u in await get_shared_identity_db().users.find(
        {"role": "case_manager", "status": "active", "hospital_id": hospital}, {"_id": 1}).to_list(length=None)}
    seen = {int(d["patient_idx"]): _aware(d["at"]) for d in await get_db()["notification_seen"].find(
        {"user_id": user["id"], "patient_idx": {"$in": list(marked)}}).to_list(length=None)}
    mine = [i for i in marked if (owner.get(i) == user["id"] or owner.get(i) not in team)
            and not (i in seen and seen[i] >= marked[i]["at"])]
    risk = await followups._risk(mine)
    return [{"patient_idx": i, "dropout_prob": risk.get(i, 0.0), "kinds": ["mood"], "urgent": False,
             "mood": {"title": marked[i]["title"], "answered_on": marked[i]["answered_on"],
                      "simulated": marked[i]["simulated"]}}
            for i in sorted(mine, key=lambda i: marked[i]["at"], reverse=True)]


async def today_list(idxs, risk: dict) -> dict:
    """The case manager's Today list: patients in the page's scope whose latest
    screen is flagged, newest first. Not filtered by what they have seen."""
    marked = await flagged(idxs)
    rows = sorted(marked.items(), key=lambda kv: kv[1]["at"], reverse=True)
    return {"total": len(rows),
            "patients": [{"patient_idx": i, "dropout_prob": risk.get(i), "answered_on": m["answered_on"],
                          "title": m["title"], "titles": m["titles"], "simulated": m["simulated"]}
                         for i, m in rows[:8]]}

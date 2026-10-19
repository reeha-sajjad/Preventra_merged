"""
Zoe: a patient's reminder companion on their home page (core/patient_home.py).

Zoe is a set of fixed rules, not an AI model. Nothing a patient enters here is
sent to any outside service, and nothing is sent to the patient outside the app
(no text, email or push) - reminders show when they open their home page. That
keeps every detail inside the same access controls as the rest of their record.

What is kept here, per patient, shared by the patient and the caretakers they
linked (core/caretakers.py) - so whoever gives the injection sees the same
schedule. The care team does not see it, and nobody else can read or change it:

    dose          when they take their GLP-1 medicine: a weekday and time for
                  a weekly injection, or a time for a daily one. How often comes
                  from the drug (FREQUENCY); semaglutide comes as a weekly
                  injection or a daily tablet, so the patient says which.
    appointments  visits they want reminding of - a kind, a date, an optional
                  time and a short note. Up to MAX_APPOINTMENTS ahead; ones more
                  than a day past drop off.

Zoe never gives medical advice: it does not suggest doses, and for a missed dose
or side effects it points to the care team or pharmacist.

The frontend works out "today" and "tomorrow" in the patient's own time zone;
this module stores the plan and checks it.

Stored in glp1_analytics.zoe_plans, one document per patient (patient_idx).
Plans saved before caretakers existed were keyed by the patient's account and
also carry patient_idx, so they are found the same way. Talha's dataset is
never written to.
"""
from datetime import date, datetime, timedelta, timezone
from typing import Optional
import re
import uuid

from fastapi import HTTPException

from core import medications
from core.mongo import get_db

COLLECTION = "zoe_plans"
MAX_APPOINTMENTS = 20
MAX_NOTE = 80

# How often each drug is taken (prescribing information).
FREQUENCY = {"TIRZEPATIDE": "weekly", "DULAGLUTIDE": "weekly", "LIRAGLUTIDE": "daily", "SEMAGLUTIDE": "either"}
FORMS = {"weekly": "Weekly injection", "daily": "Daily tablet"}         # semaglutide only
DAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")
KINDS = {"doctor": "Doctor visit", "blood_test": "Blood test", "pharmacy": "Pharmacy pickup",
         "nurse": "Nurse check-in", "other": "Other appointment"}
TIME = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")

MISSED_DOSE = ("Missed a dose? Check the leaflet that came with your medicine or ask your pharmacist what to do - "
               "don't take two doses to catch up.")


def _col():
    return get_db()[COLLECTION]


def _require_patient(user: dict, scope: Optional[list], patient_idx: Optional[int] = None) -> int:
    """The patient whose reminders these are: a patient's own, or - for a
    caretaker - the one they picked from those who linked them."""
    if user["role"] not in ("patient", "caretaker"):
        raise HTTPException(status_code=403, detail="Zoe is for patients and their caretakers")
    from core.followups import own_patient                  # avoid an import cycle
    return own_patient(user, scope, patient_idx)


def _key(idx: int) -> dict:
    return {"patient_idx": int(idx)}


async def _drug(user: dict, idx: int) -> dict:
    meds = await medications.history(user, idx)
    drug = str(meds["current_drug"] or "").upper()
    return {"drug": drug, "label": meds["current_label"], "stopped": drug == "STOPPED",
            "frequency": FREQUENCY.get(drug)}


def _upcoming(appointments: list) -> list:
    """Today and later (and yesterday's, so an evening visit isn't gone by
    morning), soonest first."""
    cutoff = (date.today() - timedelta(days=1)).isoformat()
    return sorted((a for a in appointments if a["date"] >= cutoff), key=lambda a: (a["date"], a.get("time") or ""))


def _public(doc: Optional[dict], drug: dict) -> dict:
    doc = doc or {}
    dose = doc.get("dose")
    frequency = drug["frequency"]
    if frequency == "either":
        frequency = (dose or {}).get("form")                    # None until the patient says which
    return {
        "medicine": {**drug, "frequency": frequency, "needs_form": drug["frequency"] == "either",
                     "forms": [{"key": k, "label": v} for k, v in FORMS.items()]},
        "dose": dose,
        "appointments": [{**a, "kind_label": KINDS[a["kind"]]} for a in _upcoming(doc.get("appointments", []))],
        "kinds": [{"key": k, "label": v} for k, v in KINDS.items()],
        "days": list(DAYS),
        "missed_dose": MISSED_DOSE,
    }


async def plan(user: dict, scope: Optional[list], patient_idx: Optional[int] = None) -> dict:
    idx = _require_patient(user, scope, patient_idx)
    return {**_public(await _col().find_one(_key(idx)), await _drug(user, idx)), "patient_idx": idx}


async def set_dose(user: dict, scope: Optional[list], time: str, day: Optional[int],
                   form: Optional[str], patient_idx: Optional[int] = None) -> dict:
    """When the patient takes their medicine. Weekly: a weekday (0 = Monday) and
    time. Daily: a time."""
    idx = _require_patient(user, scope, patient_idx)
    drug = await _drug(user, idx)
    if drug["stopped"] or not drug["frequency"]:
        raise HTTPException(status_code=409, detail="There's no GLP-1 medicine on your record to remind you about")
    frequency = drug["frequency"]
    if frequency == "either":
        if form not in FORMS:
            raise HTTPException(status_code=422, detail="Say whether you take a weekly injection or a daily tablet")
        frequency = form
    if not TIME.match(time or ""):
        raise HTTPException(status_code=422, detail="Choose a time, like 09:00")
    if frequency == "weekly" and day not in range(7):
        raise HTTPException(status_code=422, detail="Choose the day of the week you take it")
    dose = {"time": time, "day": day if frequency == "weekly" else None,
            "form": form if drug["frequency"] == "either" else None,
            "drug": drug["drug"], "updated_at": datetime.now(timezone.utc), "updated_by": user["role"]}
    await _col().update_one(_key(idx), {"$set": {"dose": dose, "patient_idx": idx}}, upsert=True)
    return await plan(user, scope, idx)


async def clear_dose(user: dict, scope: Optional[list], patient_idx: Optional[int] = None) -> dict:
    idx = _require_patient(user, scope, patient_idx)
    await _col().update_one(_key(idx), {"$unset": {"dose": ""}})
    return await plan(user, scope, idx)


async def add_appointment(user: dict, scope: Optional[list], kind: str, on: str,
                          time: Optional[str], note: Optional[str], patient_idx: Optional[int] = None) -> dict:
    idx = _require_patient(user, scope, patient_idx)
    if kind not in KINDS:
        raise HTTPException(status_code=422, detail="Choose what kind of appointment it is")
    try:
        day = date.fromisoformat(on)
    except (TypeError, ValueError):
        raise HTTPException(status_code=422, detail="Choose the date")
    if day < date.today() - timedelta(days=1) or day > date.today() + timedelta(days=366):
        raise HTTPException(status_code=422, detail="Choose a date from today up to a year ahead")
    if time and not TIME.match(time):
        raise HTTPException(status_code=422, detail="Choose a time, like 14:30")
    note = (note or "").strip()
    if len(note) > MAX_NOTE:
        raise HTTPException(status_code=422, detail=f"Keep the note under {MAX_NOTE} characters")
    doc = await _col().find_one(_key(idx)) or {}
    upcoming = _upcoming(doc.get("appointments", []))
    if len(upcoming) >= MAX_APPOINTMENTS:
        raise HTTPException(status_code=409, detail=f"You can keep up to {MAX_APPOINTMENTS} upcoming appointments")
    entry = {"id": uuid.uuid4().hex[:12], "kind": kind, "date": day.isoformat(), "time": time or None,
             "note": note, "added_at": datetime.now(timezone.utc)}
    # Past ones are dropped as the list is saved, so it never grows without end.
    await _col().update_one(_key(idx), {"$set": {"appointments": upcoming + [entry], "patient_idx": idx}},
                            upsert=True)
    return await plan(user, scope, idx)


async def remove_appointment(user: dict, scope: Optional[list], appointment_id: str,
                             patient_idx: Optional[int] = None) -> dict:
    idx = _require_patient(user, scope, patient_idx)
    result = await _col().update_one(_key(idx), {"$pull": {"appointments": {"id": appointment_id}}})
    if result.modified_count == 0:
        raise HTTPException(status_code=404, detail="Appointment not found")
    return await plan(user, scope, idx)


async def frequency(user: dict, idx: int) -> Optional[str]:
    """weekly | daily | None - how often this patient takes their medicine, as
    far as Zoe knows (the drug, and for semaglutide the form they chose)."""
    drug = await _drug(user, idx)
    if drug["frequency"] != "either":
        return drug["frequency"]
    doc = await _col().find_one(_key(idx), {"dose": 1}) or {}
    return (doc.get("dose") or {}).get("form")
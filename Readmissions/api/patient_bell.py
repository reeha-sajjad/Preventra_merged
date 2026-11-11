"""
A patient's bell: reminders, never scores.

The patient's home page reads their recovery in words and never shows the
risk score (api/patient_home.py), so the bell must not either. It used to show
the care team's "score went up" alerts; those are for staff and are no longer
sent to a patient or a caretaker (main.get_alerts).

What a patient's bell holds:
    meds_today   today's medicines, from the list on their home page
                 (api/patient_medicines.py). Once a day: "Mark all as read"
                 clears it until tomorrow.
    booked       a case manager booked an appointment for them in the last
                 caretaker_home.BOOKED_DAYS, with the date, time and place.
                 Never a behavioural health referral's (that is left out of
                 followups.next_appointment for patients too).

"Mark all as read" is the same call a caretaker uses (POST /api/my-people/seen,
caretaker_home.mark_seen): one time per account in `caretaker_seen`.

Reads the medicine list, follow-ups and caretaker_seen; writes nothing.
"""
from __future__ import annotations

from typing import Optional

from api import followups, patient_medicines
from api.caretaker_home import SEEN, _aware, _booked, _now


def bell(db, user: dict, scope: Optional[list]) -> list:
    """The patient's own reminders (kind per item, as the Readmissions bell expects)."""
    if user.get("role") != "patient" or not scope:
        return []
    pid = str(scope[0])
    seen = db[SEEN].find_one({"user_id": str(user["_id"])}) or {}
    seen_at = _aware(seen["at"]) if seen.get("at") else None
    items = []

    meds = patient_medicines.for_home(db, pid)["medicines"]
    if meds and not (seen_at and seen_at.date() == _now().date()):
        items.append({"kind": "meds_today", "patient_id": pid,
                      "medicines": [{k: m.get(k) for k in ("drug", "dose", "how_often", "instructions")}
                                    for m in meds]})

    booked = _booked(db, [pid]).get(pid)
    appointment = followups.next_appointment(db, pid)
    if booked and appointment and (seen_at is None or booked["at"] > seen_at):
        items.append({"kind": "booked", "patient_id": pid, "appointment": appointment})
    return items
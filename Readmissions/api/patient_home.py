"""
A patient's own home page in Readmissions: their record in plain words. The
twin of GLP-1's GLP1/Backend/core/patient_home.py - same idea, same words where
they apply - for someone home from a hospital stay. (Zoe, GLP-1's reminder
companion, is not here yet.)

What a patient sees, all about themselves (api/access.patient_scope gives a
patient account exactly one record, via care_actions.patient_account_id):

    status      how their recovery looks, in words - never the score, which is
                a model's estimate for the care team, not a verdict to read alone
    stay        when they were admitted and discharged, the main reason for the
                stay and the conditions on file, as the hospital recorded them
    recovery    how many weeks of check-ins since discharge, and whether their
                latest prescription was collected (the pharmacy feed)
    care_team   their hospital, doctor, nurses and pharmacy - names and roles
    followup    whether a case manager has been asked to get in touch, who is on
                it and how it ended (followups.patient_view - never the notes)

A patient has a right of access to their own record (45 CFR 164.524), so the
diagnosis is shown. Left out: the care team's notes, the model's risk drivers
(lab values and features worded for clinicians), the score itself and costs.

Nothing is written here; the data is read as delivered.
"""
from __future__ import annotations

from typing import Optional

from bson import ObjectId
from bson.errors import InvalidId
from fastapi import HTTPException

from api import access, access_log, auth, followups, pharmacy
from api.db_utils import get_latest_batch_date
from api.hospital import care_teams

# The worklist's current band, in words. Three levels, as in GLP-1.
STATUS = {
    "Low": ("on_track", "Your recovery is on track",
            "Things are going the way we'd hope since you left hospital. Keep following your discharge plan."),
    "Medium": ("watch", "A few things to keep an eye on",
               "Most things look good. Keep taking your medicines and going to your appointments - your care team "
               "can help with anything that's hard."),
    "High": ("support", "Your care team is giving you extra support",
             "Your care team is keeping a closer eye on your recovery, to help you stay well at home. You may hear "
             "from them."),
}

# How the latest prescription pickup reads (the pharmacy feed's statuses).
REFILL = {
    "collected": "Your latest prescription was collected.",
    "late": "Your latest prescription was collected late.",
    "missed": "Your latest prescription hasn't been collected yet. If something is stopping you, your case manager can help.",
}

SAFETY = ("If you have chest pain, trouble breathing, sudden confusion, or feel much worse than when you left "
          "hospital, call 911 or go to the emergency department.")


def status_of(band: Optional[str]) -> dict:
    key, title, text = STATUS.get(str(band or "").title(), STATUS["Medium"])
    return {"key": key, "title": title, "text": text}


def _name(db, user: dict) -> Optional[str]:
    return user.get("name") or None


def _pharmacy_name(db, patient_id: str) -> Optional[str]:
    own = db["care_actions"].find_one({"patient_id": {"$in": access.id_variants([patient_id])}},
                                      {"pharmacy_id": 1})
    if not (own or {}).get("pharmacy_id"):
        return None
    org = db.client[auth.IDENTITY_DB]["pharmacies"].find_one({"_id": own["pharmacy_id"]}, {"name": 1})
    return (org or {}).get("name") or own["pharmacy_id"]


def _tidy(text: Optional[str]) -> Optional[str]:
    """Diagnosis text arrives as recorded (often upper case); show it as a sentence."""
    text = (text or "").strip()
    if not text:
        return None
    return text[0].upper() + text[1:].lower() if text.isupper() else text


def _patient_names(db, ids) -> dict:
    """patient_id -> the patient's own account name, where they have one - how
    a caretaker caring for several people tells them apart."""
    own = {str(r["patient_id"]): r.get("patient_account_id")
           for r in db["care_actions"].find({"patient_id": {"$in": access.id_variants(list(ids))}},
                                            {"patient_id": 1, "patient_account_id": 1})}
    oids = []
    for a in own.values():
        try:
            oids.append(ObjectId(a))
        except (InvalidId, TypeError):
            pass
    names = {str(u["_id"]): u.get("name") for u in auth.users(db).find({"_id": {"$in": oids}}, {"name": 1})}
    return {str(i): names.get(own.get(str(i))) or f"Patient {i}" for i in ids}


def home(db, user: dict, scope: Optional[list], patient_id: Optional[str] = None, session: str = "") -> dict:
    """The patient's home page - for the patient, or for a caretaker they
    linked (api/caretakers.py), who picks which of their people to look at."""
    if user["role"] not in ("patient", "caretaker"):
        raise HTTPException(status_code=403, detail="This page is for patients and their caretakers")
    if not scope:
        return {"linked": False, "name": _name(db, user), "viewer": user["role"]}
    caring_for = None
    if user["role"] == "caretaker":
        pid = followups.own_patient(user, scope, patient_id or str(scope[0]))
        names = _patient_names(db, scope)
        caring_for = {"patient_name": names[pid],
                      "people": [{"patient_id": str(i), "name": names[str(i)]} for i in scope]}
        # A disclosure to someone involved in the patient's care: on the record.
        if not access_log.has_opened(db, user, access_log.APP, pid, session):
            own = db["care_actions"].find_one({"patient_id": {"$in": access.id_variants([pid])}}, {"hospital_id": 1})
            access_log.record(db, user, access_log.APP, pid, (own or {}).get("hospital_id"),
                              access.CARETAKER_REASON, session)
    else:
        pid = str(scope[0])
    batch = get_latest_batch_date(db, "patient_worklist")
    row = db["patient_worklist"].find_one({"patient_id": {"$in": access.id_variants([pid])}, "batch_date": batch},
                                          {"_id": 0}) or {}
    team = care_teams(db, [pid]).get(pid, {})
    hosp = None
    if team.get("hospital_id"):
        h = db.client[auth.IDENTITY_DB]["hospitals"].find_one({"_id": team["hospital_id"]}, {"name": 1})
        hosp = (h or {}).get("name") or team["hospital_id"]
    from models.icd_groups import GROUP_LABELS                 # same labels as the staff pages
    refill = pharmacy.refill_facts(db, pid)
    follow = followups.for_patient(db, user, scope, pid)
    doctor = team.get("doctor")
    return {
        "linked": True, "name": _name(db, user), "patient_id": pid,
        "status": status_of(row.get("current_band") or row.get("risk_band")),
        "stay": {"admitted": row.get("admit_date") or None, "discharged": row.get("discharge_date") or None,
                 "reason": _tidy(row.get("primary_diagnosis")),
                 "conditions": [GROUP_LABELS.get(k, k) for k in (row.get("clinical_groups") or [])]},
        "recovery": {"weeks": int(row.get("weeks_tracked") or 0),
                     "refill": REFILL.get(refill.get("refill_latest"))},
        "care_team": {"hospital": hosp,
                      "doctor": {"name": doctor["name"], "specialty": doctor.get("specialty") or None} if doctor else None,
                      "nurses": [{"name": n["name"]} for n in team.get("nurses", [])],
                      "pharmacy": _pharmacy_name(db, pid)},
        "followup": {"active": follow["active"], "recent": follow["recent"], "reasons": follow["reasons"],
                     "can_ask": bool(team.get("hospital_id"))},
        "safety": SAFETY,
        "viewer": user["role"], "caring_for": caring_for,
    }
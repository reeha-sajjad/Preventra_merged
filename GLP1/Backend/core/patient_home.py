"""
A patient's own home page: their record in plain words.

What a patient sees, all about themselves (core/access.patient_scope gives a
patient account exactly one record, via patient_access.patient_account_id):

    status      how things look, in words - never the percentage, which is a
                model's estimate for the care team, not a verdict to read alone
    medicine    the GLP-1 drug they are on now (core/medications.py, so a switch
                or stop the doctor recorded shows) and how often it is taken
    profile     age, BMI and HbA1c with the usual ranges, and conditions on file
    factors     the two or three things the model weighed most for them,
                worded for a patient: what helps, and what to watch
    care_team   their hospital, doctors, nurses, main case manager, pharmacy -
                names and roles only
    followup    whether their case manager has been asked to get in touch, who
                is on it, how it ended (core/followups.patient_view - never the
                care team's notes)

A patient has a right of access to their own record (45 CFR 164.524), so
nothing here is redacted the way it is for staff who are not on the care team
(core/access.redact). What is left out is what is not "their record" in a useful
sense: other people's notes about coordinating their care, the model's internal
numbers (SHAP values, segment, cluster) and costs.

Nothing is written here; the dataset is read as delivered.
"""
from typing import Optional

from bson import ObjectId
from bson.errors import InvalidId
from fastapi import HTTPException

from core import access_log, followups, hospital, medications
from core.access import CARETAKER_REASON, patient_hospital
from core.mongo import get_db, get_shared_identity_db

# How the model's dropout estimate is put into words. Three levels, so a small
# change in the estimate rarely changes what the patient reads.
STATUS = (
    (0.40, "on_track", "You're on track",
     "Your treatment is going the way we'd hope. Keep taking your medicine as prescribed."),
    (0.75, "watch", "A few things to keep an eye on",
     "Most things look good. The points below are worth keeping in mind - your care team can help with any of them."),
    (1.01, "support", "Your care team is giving you extra support",
     "Your care team is keeping a closer eye on how things are going, to help you stay on your treatment. "
     "You may hear from your case manager."),
)

# How each drug is usually taken (prescribing information). Semaglutide comes
# as a weekly injection or a daily tablet; the record does not say which.
SCHEDULE = {
    "TIRZEPATIDE": "Once a week, by injection",
    "DULAGLUTIDE": "Once a week, by injection",
    "SEMAGLUTIDE": "Once a week by injection, or a daily tablet - as your doctor prescribed",
    "LIRAGLUTIDE": "Once a day, by injection",
}

# The model's risk factors (the driver labels in the data), worded for the
# patient: what it means when it is working against them, and when for them.
FACTORS = {
    "Financial pressure relative to income": (
        "Paying for your medicine",
        "Cost is one of the main reasons people stop - and stopping early throws away what you have already "
        "paid for. Don't skip or stretch doses to save money. Tell your case manager this week: savings and "
        "copay programmes can lower what you pay.",
        "Paying for your medicine is manageable for you, so cost is no reason to miss a dose."),
    "Out-of-pocket medication cost": (
        "What you pay for your medicine",
        "You pay more out of pocket than most. Don't let that make you skip a refill - ask your case manager "
        "now about copay and savings programmes before your next one is due.",
        "What you pay out of pocket is low, so there's nothing in the way of taking every dose."),
    "Provider & pharmacy refill reliability": (
        "Getting your refills on time",
        "Late refills are where treatment breaks down. Pick up every refill on the day it is due, and if your "
        "pharmacy can't supply it, tell your care team straight away rather than waiting.",
        "Your refills have been on time - keep it that way. A missed refill is the easiest way to fall off "
        "treatment."),
    "Side effect intensity (GI friction)": (
        "Stomach side effects",
        "Nausea and an upset stomach are common at first and usually settle within weeks. Do not stop on your "
        "own - call your care team, who can adjust things so you can keep going.",
        "Side effects are unlikely to be a problem with your medicine, so keep taking it exactly as prescribed."),
    "Blood sugar control (HbA1c)": (
        "Your blood sugar",
        "Your blood sugar only comes down if you take every dose. Missed doses undo the progress, so stay on "
        "schedule.",
        "Your blood sugar results show the treatment working - keep every dose on schedule to hold on to it."),
    "Body weight / BMI severity": (
        "Your weight",
        "People who stop early usually regain most of the weight they lost. Progress often slows after a few "
        "months - that is normal, not a reason to stop. Keep going, and talk to your care team if it feels "
        "stuck.",
        "Your weight goals depend on staying on treatment - keep going even when progress slows."),
    "Overall disease burden": (
        "Your other health conditions",
        "With several conditions to manage, this medicine matters more, not less. Keep it on your schedule "
        "alongside the others, and ask your care team to help you plan.",
        "This treatment helps your other conditions too - one more reason not to miss a dose."),
}

SAFETY = ("If you have severe stomach pain that won't go away, trouble breathing, or swelling of your face or "
          "throat, call 911 or go to the emergency department.")


def status_of(prob: float) -> dict:
    for cut, key, title, text in STATUS:
        if prob < cut:
            return {"key": key, "title": title, "text": text}
    last = STATUS[-1]
    return {"key": last[1], "title": last[2], "text": last[3]}


def _bmi(value) -> Optional[dict]:
    if value is None:
        return None
    v = float(value)
    band = ("Below the healthy range" if v < 18.5 else "Healthy range" if v < 25
            else "Above the healthy range" if v < 30 else "In the obesity range")
    return {"key": "bmi", "label": "BMI", "value": f"{v:.1f}", "note": band}


def _hba1c(value) -> Optional[dict]:
    if value is None:
        return None
    v = float(value)
    band = ("Normal range" if v < 5.7 else "Prediabetes range" if v < 6.5 else "Diabetes range")
    return {"key": "hba1c", "label": "HbA1c (average blood sugar)", "value": f"{v:.1f}%", "note": band}


def factors_of(doc: dict) -> list:
    out = []
    for n in (1, 2, 3):
        feature = doc.get(f"driver_{n}")
        if not feature:
            continue
        helping = "increases" not in str(doc.get(f"driver_{n}_direction") or "")
        title, against, works_for = FACTORS.get(str(feature), (str(feature), "Your care team can tell you more.",
                                                               "This is working in your favour."))
        out.append({"title": title, "helping": helping, "text": works_for if helping else against})
    return out


async def _name(user: dict) -> Optional[str]:
    try:
        account = await get_shared_identity_db().users.find_one({"_id": ObjectId(user["id"])}, {"name": 1})
    except (InvalidId, TypeError):
        return None
    return (account or {}).get("name")


async def _pharmacy(idx: int, team: dict) -> Optional[str]:
    access = await get_db().patient_access.find_one({"patient_idx": idx}, {"pharmacy_id": 1, "pharmacy": 1})
    if (access or {}).get("pharmacy_id"):
        org = await get_shared_identity_db().pharmacies.find_one({"_id": access["pharmacy_id"]}, {"name": 1})
        if org:
            return org.get("name")
    return (access or {}).get("pharmacy") or team.get("pharmacy")


async def _patient_names(idxs) -> dict:
    """patient_idx -> the patient's own account name, where they have one - how
    a caretaker caring for several people tells them apart."""
    access = await get_db().patient_access.find({"patient_idx": {"$in": [int(i) for i in idxs]}},
                                                {"_id": 0, "patient_idx": 1, "patient_account_id": 1}).to_list(length=None)
    accounts = {a["patient_idx"]: a.get("patient_account_id") for a in access}
    oids = []
    for a in accounts.values():
        try:
            oids.append(ObjectId(a))
        except (InvalidId, TypeError):
            pass
    names = {str(u["_id"]): u.get("name") for u in await get_shared_identity_db().users.find(
        {"_id": {"$in": oids}}, {"name": 1}).to_list(length=None)}
    return {int(i): names.get(accounts.get(int(i))) or f"Patient #{int(i)}" for i in idxs}


async def home(user: dict, scope: Optional[list], patient_idx: Optional[int] = None) -> dict:
    """The patient's home page - for the patient, or for a caretaker they
    linked (core/caretakers.py), who picks which of their people to look at."""
    if user["role"] not in ("patient", "caretaker"):
        raise HTTPException(status_code=403, detail="This page is for patients and their caretakers")
    name = await _name(user)
    if not scope:
        return {"linked": False, "name": name, "viewer": user["role"]}
    caring_for = None
    if user["role"] == "caretaker":
        idx = followups.own_patient(user, scope, patient_idx if patient_idx is not None else int(scope[0]))
        names = await _patient_names(scope)
        caring_for = {"patient_name": names[idx],
                      "people": [{"patient_idx": int(i), "name": names[int(i)]} for i in scope]}
        # A disclosure to someone involved in the patient's care: on the record.
        if not await access_log.has_opened(user, idx):
            await access_log.record(user, idx, await patient_hospital(idx), CARETAKER_REASON)
    else:
        idx = int(scope[0])
    doc = await get_db().patients.find_one({"patient_idx": idx}, {"_id": 0}) or {}
    prob = float(doc.get("dropout_prob") or doc.get("dropout_proba") or 0.5)
    team = (await hospital.care_teams([idx]))[idx]
    hosp = None
    if team.get("hospital_id"):
        h = await get_shared_identity_db().hospitals.find_one({"_id": team["hospital_id"]}, {"name": 1})
        hosp = (h or {}).get("name") or team["hospital_id"]
    meds = await medications.history(user, idx)
    current = meds["current_drug"]
    last_change = meds["changes"][0] if meds["changes"] else None
    conditions = [label for field, label in (("has_dyslipidemia", "High cholesterol"),
                                             ("has_dysglycemia", "High blood sugar")) if doc.get(field)]
    profile = [p for p in (
        {"key": "age", "label": "Age", "value": str(int(doc["RIDAGEYR"])), "note": None} if doc.get("RIDAGEYR") else None,
        _bmi(doc.get("BMXBMI")), _hba1c(doc.get("LBXGH"))) if p]
    follow = await followups.for_patient(user, scope, idx)
    person = lambda p: {"name": p["name"]} if p else None              # noqa: E731 - names only
    return {
        "linked": True, "name": name, "patient_idx": idx,
        "status": status_of(prob),
        "medicine": {"drug": current, "label": meds["current_label"], "stopped": current == "STOPPED",
                     "schedule": SCHEDULE.get(str(current or "").upper()),
                     "changed_on": last_change["effective_date"] if last_change else None,
                     "change_reason": last_change["reason_label"] if last_change else None},
        "profile": profile, "conditions": conditions,
        "factors": factors_of(doc),
        "care_team": {"hospital": hosp,
                      "doctors": [person(d) for d in team.get("doctors", [])],
                      "nurses": [person(n) for n in team.get("nurses", [])],
                      "case_manager": person(team.get("case_manager")),
                      "pharmacy": await _pharmacy(idx, team)},
        "followup": {"active": follow["active"], "recent": follow["recent"], "reasons": follow["reasons"],
                     "can_ask": bool(team.get("hospital_id"))},
        "viewer": user["role"], "caring_for": caring_for,
        # A caretaker helps manage the treatment, so they see the care team's
        # estimate too; the patient reads it in words only (status).
        **({"risk": {"prob": round(prob, 4), "band": ("Critical" if prob >= 0.75 else "High" if prob >= 0.5
                                                      else "Medium" if prob >= 0.25 else "Low")}}
           if user["role"] == "caretaker" else {}),
        "safety": SAFETY,
        "data_as_of": "May 2026",
    }
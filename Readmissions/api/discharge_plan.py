"""
The discharge plan: a checklist of what should be done before a patient goes
home, and the social needs that decide how well they will manage once there.
One record per discharge, in `discharge_plans` - a new discharge starts a new
plan, and the old one stays with the discharge it was about.

THE CHECKLIST
-------------
The interventions with the best evidence for preventing a readmission are
unglamorous and happen at discharge (Project RED; Project BOOST): a follow-up
appointment booked before the patient leaves, medicines reconciled, the plan
explained and explained back. The items depend on the patient:

    followup_booked     every patient. Within 7 days for heart failure, other
                        cardiac and renal patients, 14 for everyone else - the
                        same schedule the weekly simulator follows
                        (scripts/simulate_weekly_monitoring.EARLY_FOLLOWUP_GROUPS)
                        - and within 7 for a Medium or High patient who lives
                        alone or has nobody to help at home
    meds_reconciled     every patient
    warning_signs       every patient, with what to watch for their condition
    checkin_explained   every patient: the weekly check-in (api/checkins.py) is
                        only as good as the patient knowing to send it
    teach_back          Medium and High
    contact_named       High, or living alone. Ticks itself when a caretaker is
                        linked (api/caretakers.py)
    pharmacy_on_file    every patient. Ticks itself when a pharmacy is on file
    social_screened     every patient. Ticks itself once the social needs below
                        are recorded

An item that ticks itself can still be ticked by hand - a contact person named
on paper is a contact person - but not unticked while the fact behind it holds.

SOCIAL NEEDS
------------
Six questions asked at discharge. An answer that is a need comes with the
action it calls for. These do NOT change the risk score: no published weight
for them exists that this project could defend, so they change what the care
team does - the follow-up date above, and the actions - and the Outcomes page
counts them.

Who sees it: the care team's clinical layer (main._require_detail), so a
hospital admin or insurer gives a reason first and a pharmacy never does.
Not a caretaker: what a patient can afford or eat is theirs to share, as the
caretaker's bell already treats cost (api/caretaker_home.py). Changes are for
the roles that edit patients (access.ACTIONS edit_patients).
THE PATIENT'S OWN ITEMS AND QUESTIONS
-------------------------------------
The doctor or case manager can add items to one patient's checklist ("oxygen
delivered at home") and questions to that patient's weekly check-in
(api/checkins.py), as yes/no or a short answer. They belong to this patient's
plan for this discharge only: no hospital-wide template, and a new discharge
starts clean. Ticks of their own items sit in the same `checklist` map as the
built-in ones, so they count towards "plan complete" on the Outcomes page. A
check-in answer is kept with the question's wording at the time (api/checkins
.py), so removing a question never orphans the answers already given. Adding
and removing is access.ACTIONS customise_plan; ticking stays with edit_patients.
"""
from __future__ import annotations

import secrets
from datetime import datetime, timezone
from typing import Optional

from fastapi import HTTPException

from api import access
from api.db_utils import get_latest_batch_date

COLLECTION = "discharge_plans"

# What the care team may add for one patient.
MAX_CUSTOM_ITEMS = 10
MAX_CHECKIN_QUESTIONS = 5
MIN_LABEL = 3
MAX_LABEL = 150
QUESTION_KINDS = ("yes_no", "text")

# The 7-day follow-up groups - the simulator's EARLY_FOLLOWUP_GROUPS
# (scripts/simulate_weekly_monitoring.py). Twin copies: edit together.
EARLY_FOLLOWUP_GROUPS = ("heart_failure", "cardiac_other", "renal")

# What a patient should watch for and call about, by monitoring plan.
WARNING_SIGNS = {
    "heart_failure": "Weigh every morning; call if up 2 kg in 3 days, more breathless, or needing more pillows.",
    "respiratory": "Call if more breathless, using the reliever inhaler more, or the phlegm changes colour.",
    "surgical_injury": "Call if the wound is red, leaking or opening, or with a temperature.",
    "sepsis_infection": "Call with a temperature, new confusion, or if the antibiotics can't be finished.",
    "renal": "Call if passing much less urine, with new swelling, or if weight goes up quickly.",
    "diabetes": "Call with repeated high or low sugar readings, or if unable to eat or drink.",
    "mental_health": "Knows the crisis line and who to call if things get worse; keeps the follow-up.",
    "oncology": "Call at once with a temperature of 38 °C or more during treatment.",
    "cardiac_other": "Call with chest pain, palpitations, fainting, or new breathlessness.",
    "neuro_stroke": "Call 911 at once for new weakness, facial droop, or trouble speaking.",
}
DEFAULT_WARNING = "Call if feeling much worse than when they left hospital."

# question key -> (label, options [(value, label)], the answer that is a need, the action)
SOCIAL = {
    "lives_alone": (
        "Lives alone", [("yes", "Yes"), ("no", "No")], "yes",
        "Lives alone: phone within two days of going home, and name a contact person."),
    "help_at_home": (
        "Someone to help at home with daily tasks", [("yes", "Yes"), ("no", "No")], "no",
        "No help at home: consider a home-care or community nursing referral."),
    "transport": (
        "Can get to appointments", [("yes", "Yes"), ("no", "Needs help with transport")], "no",
        "Transport: arrange it for the follow-up, or offer a video appointment."),
    "medicine_cost": (
        "Can afford their medicines", [("yes", "Yes"), ("no", "Worried about the cost")], "no",
        "Medicine cost: refer to the pharmacy or social work before the first refill is due - a medicine "
        "the patient cannot afford is a common reason doses stop."),
    "food": (
        "Has enough food", [("yes", "Yes"), ("no", "Worried about having enough")], "no",
        "Food: refer to social work or a local food programme."),
    "housing": (
        "Housing", [("stable", "Stable"), ("unstable", "Unstable or at risk")], "unstable",
        "Housing: refer to social work, and confirm where the patient can be reached for follow-up."),
}
UNKNOWN = "unknown"          # not asked, or the patient preferred not to say

LABELS = {
    "followup_booked": "Follow-up appointment booked within {days} days",
    "meds_reconciled": "Medicines reviewed and reconciled with the patient",
    "warning_signs": "Knows the warning signs and who to call",
    "checkin_explained": "Weekly check-in explained to the patient or their caretaker",
    "teach_back": "Explained the plan back in their own words (teach-back)",
    "contact_named": "Caretaker or contact person named",
    "pharmacy_on_file": "Pharmacy on file",
    "social_screened": "Social needs asked about",
}
AUTOMATIC = ("contact_named", "pharmacy_on_file", "social_screened")
# Can only tick itself: the answers ARE the fact.
AUTOMATIC_ONLY = ("social_screened",)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(at) -> Optional[str]:
    if at is None:
        return None
    return (at if at.tzinfo else at.replace(tzinfo=timezone.utc)).isoformat()


def _who(user: dict) -> dict:
    return {"id": str(user["_id"]), "name": user.get("name") or user.get("email", ""), "role": user["role"]}


def ensure_indexes(db) -> None:
    db[COLLECTION].create_index([("patient_id", 1), ("discharge_date", 1)], unique=True)


# ------------------------------------------------------------ the rules
# A need in words. The questions are worded as the good case ("Can afford
# their medicines"), so a count under the question's own label read as the
# opposite of what it counted.
NEED_LABELS = {
    "lives_alone": "Lives alone",
    "help_at_home": "No help at home",
    "transport": "Needs help with transport",
    "medicine_cost": "Worried about medicine cost",
    "food": "Worried about having enough food",
    "housing": "Unstable housing",
}


def needs(social: dict) -> list:
    """The answers that are needs, each with its action."""
    out = []
    for key, (_, _, need, action) in SOCIAL.items():
        if (social or {}).get(key) == need:
            out.append({"key": key, "label": NEED_LABELS[key], "action": action})
    return out


def followup_days(group: str, band: str, social: dict) -> tuple:
    """(days, why) - how soon the first follow-up should be."""
    if group in EARLY_FOLLOWUP_GROUPS:
        return 7, "the usual for this condition"
    alone = (social or {}).get("lives_alone") == "yes" or (social or {}).get("help_at_home") == "no"
    if alone and band in ("Medium", "High"):
        return 7, "lives alone or has no help at home, at " + band + " risk"
    return 14, None


def items_for(group: str, band: str, social: dict) -> list:
    """Which checklist items this patient gets, in order."""
    keys = ["followup_booked", "meds_reconciled", "warning_signs", "checkin_explained"]
    if band in ("Medium", "High"):
        keys.append("teach_back")
    if band == "High" or (social or {}).get("lives_alone") == "yes":
        keys.append("contact_named")
    keys += ["pharmacy_on_file", "social_screened"]
    return keys


# ------------------------------------------------------------ reading
def _discharge(db, patient_id: str) -> dict:
    batch = get_latest_batch_date(db, "patient_worklist")
    row = db["patient_worklist"].find_one(
        {"patient_id": {"$in": access.id_variants([patient_id])}, "batch_date": batch},
        {"_id": 0, "discharge_date": 1, "risk_band": 1, "clinical_group": 1})
    if not row:
        raise HTTPException(status_code=404, detail="Patient not found")
    return row


def _facts(db, patient_id: str) -> dict:
    own = db["care_actions"].find_one({"patient_id": {"$in": access.id_variants([patient_id])}},
                                      {"caretaker_ids": 1, "pharmacy_id": 1}) or {}
    return {"contact_named": bool(own.get("caretaker_ids")), "pharmacy_on_file": bool(own.get("pharmacy_id"))}


def plan(db, user: dict, patient_id: str) -> dict:
    """The patient's plan for their current discharge."""
    patient_id = str(patient_id)
    row = _discharge(db, patient_id)
    discharge = str(row.get("discharge_date") or "")
    doc = db[COLLECTION].find_one({"patient_id": patient_id, "discharge_date": discharge}) or {}
    return _shape(patient_id, row, doc, _facts(db, patient_id),
                  user["role"] in access.ACTIONS["edit_patients"],
                  user["role"] in access.ACTIONS["customise_plan"])


def _shape(patient_id: str, row: dict, doc: dict, facts: dict, can_edit: bool,
           can_customise: bool = False) -> dict:
    band = str(row.get("risk_band") or "Low").title()
    group = row.get("clinical_group") or "general"
    social = doc.get("social") or {}
    days, why = followup_days(group, band, social)
    ticks = doc.get("checklist") or {}
    facts = {**facts, "social_screened": bool(social)}
    items = []
    for key in items_for(group, band, social):
        tick = ticks.get(key) or {}
        auto = bool(facts.get(key))
        items.append({
            "key": key, "label": LABELS[key].format(days=days),
            "hint": (WARNING_SIGNS.get(group, DEFAULT_WARNING) if key == "warning_signs"
                     else (f"Within {days} days: {why}." if key == "followup_booked" and why else None)),
            "done": auto or bool(tick.get("done")), "auto": auto,
            "by": (tick.get("by") or {}).get("name") if tick.get("done") else None,
            "at": _iso(tick.get("at")) if tick.get("done") else None,
            "can_toggle": can_edit and key not in AUTOMATIC_ONLY and not auto,
            "custom": False, "can_remove": False,
        })
    for c in doc.get("custom_items") or []:
        tick = ticks.get(c["id"]) or {}
        done = bool(tick.get("done"))
        items.append({
            "key": c["id"], "label": c["label"], "hint": None, "done": done, "auto": False,
            "by": (tick.get("by") or {}).get("name") if done else None,
            "at": _iso(tick.get("at")) if done else None,
            "can_toggle": can_edit, "custom": True,
            "added_by": (c.get("by") or {}).get("name"), "can_remove": can_customise,
        })
    return {
        "patient_id": patient_id, "discharge_date": row.get("discharge_date"), "band": band,
        "group": group, "followup_days": days,
        "items": items, "done": sum(1 for i in items if i["done"]), "total": len(items),
        "social": {
            "answers": {k: social.get(k, UNKNOWN) for k in SOCIAL},
            "recorded_by": (doc.get("social_by") or {}).get("name"), "recorded_at": _iso(doc.get("social_at")),
            "needs": needs(social),
            "questions": [{"key": k, "label": label,
                           "options": [{"value": v, "label": t} for v, t in opts]
                           + [{"value": UNKNOWN, "label": "Not asked / prefers not to say"}]}
                          for k, (label, opts, _, _) in SOCIAL.items()],
        },
        "checkin_questions": [{"id": q["id"], "label": q["label"], "kind": q["kind"],
                               "added_by": (q.get("by") or {}).get("name"), "at": _iso(q.get("at"))}
                              for q in doc.get("checkin_questions") or []],
        "can_edit": can_edit, "can_customise": can_customise,
        "limits": {"items": MAX_CUSTOM_ITEMS, "questions": MAX_CHECKIN_QUESTIONS, "label": MAX_LABEL},
    }


# ------------------------------------------------------------ changing
def _editable(db, user: dict, scope: Optional[list], patient_id: str) -> dict:
    access.require_patient(scope, patient_id)
    access.require(user, "edit_patients")
    return _discharge(db, patient_id)


def tick(db, user: dict, scope: Optional[list], patient_id: str, item: str, done: bool) -> dict:
    patient_id = str(patient_id)
    row = _editable(db, user, scope, patient_id)
    discharge = str(row.get("discharge_date") or "")
    own = _own_doc(db, patient_id, row)
    if item not in LABELS and item not in {c["id"] for c in own.get("custom_items") or []}:
        raise HTTPException(status_code=422, detail="Not a checklist item")
    if item in AUTOMATIC_ONLY:
        raise HTTPException(status_code=422, detail="This one ticks itself when the social needs are recorded")
    if not done and _facts(db, patient_id).get(item):
        raise HTTPException(status_code=409, detail="This is on file, so it stays ticked")
    ensure_indexes(db)
    db[COLLECTION].update_one(
        {"patient_id": patient_id, "discharge_date": discharge},
        {"$set": {f"checklist.{item}": {"done": bool(done), "by": _who(user), "at": _now()},
                  "hospital_id": _hospital(db, patient_id), "updated_at": _now()},
         "$setOnInsert": {"created_at": _now()}},
        upsert=True)
    return plan(db, user, patient_id)


def record_social(db, user: dict, scope: Optional[list], patient_id: str, answers: dict) -> dict:
    patient_id = str(patient_id)
    row = _editable(db, user, scope, patient_id)
    clean = {}
    for key, value in (answers or {}).items():
        if key not in SOCIAL:
            raise HTTPException(status_code=422, detail=f"Not one of the questions: {key}")
        allowed = {v for v, _ in SOCIAL[key][1]} | {UNKNOWN}
        if value not in allowed:
            raise HTTPException(status_code=422, detail=f"Choose one of the answers for: {SOCIAL[key][0]}")
        if value != UNKNOWN:
            clean[key] = value
    if not clean:
        raise HTTPException(status_code=422, detail="Answer at least one question")
    ensure_indexes(db)
    discharge = str(row.get("discharge_date") or "")
    db[COLLECTION].update_one(
        {"patient_id": patient_id, "discharge_date": discharge},
        {"$set": {"social": clean, "social_by": _who(user), "social_at": _now(),
                  "hospital_id": _hospital(db, patient_id), "updated_at": _now()},
         "$setOnInsert": {"created_at": _now()}},
        upsert=True)
    return plan(db, user, patient_id)


# ------------------------------------------------------------ the patient's own
def _label(text) -> str:
    label = " ".join(str(text or "").split())
    if len(label) < MIN_LABEL:
        raise HTTPException(status_code=422, detail="Write a few words")
    if len(label) > MAX_LABEL:
        raise HTTPException(status_code=422, detail=f"Keep it under {MAX_LABEL} characters")
    return label


def _customisable(db, user: dict, scope: Optional[list], patient_id: str) -> dict:
    access.require_patient(scope, patient_id)
    access.require(user, "customise_plan")
    return _discharge(db, patient_id)


def _own_doc(db, patient_id: str, row: dict) -> dict:
    return db[COLLECTION].find_one({"patient_id": patient_id,
                                    "discharge_date": str(row.get("discharge_date") or "")}) or {}


def _stamped(db, patient_id: str) -> dict:
    return {"hospital_id": _hospital(db, patient_id), "updated_at": _now()}


def add_item(db, user: dict, scope: Optional[list], patient_id: str, label: str) -> dict:
    patient_id = str(patient_id)
    row = _customisable(db, user, scope, patient_id)
    label = _label(label)
    doc = _own_doc(db, patient_id, row)
    have = doc.get("custom_items") or []
    if len(have) >= MAX_CUSTOM_ITEMS:
        raise HTTPException(status_code=409,
                            detail=f"This patient already has {MAX_CUSTOM_ITEMS} items of their own")
    band = str(row.get("risk_band") or "Low").title()
    days, _ = followup_days(row.get("clinical_group") or "general", band, doc.get("social") or {})
    taken = {c["label"].lower() for c in have} | {t.format(days=days).lower() for t in LABELS.values()}
    if label.lower() in taken:
        raise HTTPException(status_code=409, detail="That item is already on the checklist")
    ensure_indexes(db)
    item = {"id": "ci_" + secrets.token_hex(4), "label": label, "by": _who(user), "at": _now()}
    db[COLLECTION].update_one(
        {"patient_id": patient_id, "discharge_date": str(row.get("discharge_date") or "")},
        {"$push": {"custom_items": item}, "$set": _stamped(db, patient_id),
         "$setOnInsert": {"created_at": _now()}},
        upsert=True)
    return plan(db, user, patient_id)


def remove_item(db, user: dict, scope: Optional[list], patient_id: str, item_id: str) -> dict:
    patient_id = str(patient_id)
    row = _customisable(db, user, scope, patient_id)
    doc = _own_doc(db, patient_id, row)
    if item_id not in {c["id"] for c in doc.get("custom_items") or []}:
        raise HTTPException(status_code=404, detail="No such item")
    db[COLLECTION].update_one(
        {"patient_id": patient_id, "discharge_date": str(row.get("discharge_date") or "")},
        {"$pull": {"custom_items": {"id": item_id}}, "$unset": {f"checklist.{item_id}": ""},
         "$set": _stamped(db, patient_id)})
    return plan(db, user, patient_id)


def add_question(db, user: dict, scope: Optional[list], patient_id: str, label: str, kind: str) -> dict:
    patient_id = str(patient_id)
    row = _customisable(db, user, scope, patient_id)
    if kind not in QUESTION_KINDS:
        raise HTTPException(status_code=422, detail="Choose yes/no or a short answer")
    label = _label(label)
    doc = _own_doc(db, patient_id, row)
    have = doc.get("checkin_questions") or []
    if len(have) >= MAX_CHECKIN_QUESTIONS:
        raise HTTPException(status_code=409,
                            detail=f"This patient already has {MAX_CHECKIN_QUESTIONS} questions of their own")
    if label.lower() in {q["label"].lower() for q in have}:
        raise HTTPException(status_code=409, detail="That question is already asked")
    ensure_indexes(db)
    question = {"id": "cq_" + secrets.token_hex(4), "label": label, "kind": kind,
                "by": _who(user), "at": _now()}
    db[COLLECTION].update_one(
        {"patient_id": patient_id, "discharge_date": str(row.get("discharge_date") or "")},
        {"$push": {"checkin_questions": question}, "$set": _stamped(db, patient_id),
         "$setOnInsert": {"created_at": _now()}},
        upsert=True)
    return plan(db, user, patient_id)


def remove_question(db, user: dict, scope: Optional[list], patient_id: str, question_id: str) -> dict:
    patient_id = str(patient_id)
    row = _customisable(db, user, scope, patient_id)
    doc = _own_doc(db, patient_id, row)
    if question_id not in {q["id"] for q in doc.get("checkin_questions") or []}:
        raise HTTPException(status_code=404, detail="No such question")
    db[COLLECTION].update_one(
        {"patient_id": patient_id, "discharge_date": str(row.get("discharge_date") or "")},
        {"$pull": {"checkin_questions": {"id": question_id}}, "$set": _stamped(db, patient_id)})
    return plan(db, user, patient_id)


def checkin_questions(db, patient_id: str) -> list:
    """The questions the care team added for this patient's current discharge,
    for the weekly check-in form (api/checkins.py). The discharge date is the
    worklist row's, as plan() reads it, not the check-in's."""
    try:
        row = _discharge(db, str(patient_id))
    except HTTPException:
        return []
    doc = db[COLLECTION].find_one(
        {"patient_id": {"$in": access.id_variants([str(patient_id)])},
         "discharge_date": str(row.get("discharge_date") or "")}, {"checkin_questions": 1}) or {}
    return list(doc.get("checkin_questions") or [])


def _hospital(db, patient_id: str) -> Optional[str]:
    own = db["care_actions"].find_one({"patient_id": {"$in": access.id_variants([patient_id])}},
                                      {"hospital_id": 1})
    return (own or {}).get("hospital_id")


# ------------------------------------------------------------ for the Outcomes page
def summary(db, rows: list) -> dict:
    """Checklist completion and social needs over these worklist rows (one per
    discharge): how many have a plan, a follow-up booked, every item done, and
    which needs were found."""
    if not rows:
        return {"discharges": 0, "with_plan": 0, "followup_booked": 0, "complete": 0,
                "screened": 0, "with_needs": 0, "needs": []}
    keys = {(str(r["patient_id"]), str(r.get("discharge_date") or "")) for r in rows}
    docs = {(d["patient_id"], d["discharge_date"]): d for d in db[COLLECTION].find(
        {"patient_id": {"$in": sorted({k[0] for k in keys})}})}
    owners = {str(o["patient_id"]): o for o in db["care_actions"].find(
        {"patient_id": {"$in": access.id_variants(sorted({k[0] for k in keys}))}},
        {"patient_id": 1, "caretaker_ids": 1, "pharmacy_id": 1})}
    with_plan = booked = complete = screened = with_needs = 0
    by_need = {k: 0 for k in SOCIAL}
    for r in rows:
        pid = str(r["patient_id"])
        doc = docs.get((pid, str(r.get("discharge_date") or "")))
        if not doc:
            continue
        with_plan += 1
        own = owners.get(pid) or {}
        shaped = _shape(pid, r, doc, {"contact_named": bool(own.get("caretaker_ids")),
                                      "pharmacy_on_file": bool(own.get("pharmacy_id"))}, False)
        booked += any(i["key"] == "followup_booked" and i["done"] for i in shaped["items"])
        complete += shaped["done"] == shaped["total"]
        found = shaped["social"]["needs"]
        screened += bool(doc.get("social"))
        with_needs += bool(found)
        for n in found:
            by_need[n["key"]] += 1
    return {"discharges": len(rows), "with_plan": with_plan, "followup_booked": booked, "complete": complete,
            "screened": screened, "with_needs": with_needs,
            "needs": [{"key": k, "label": NEED_LABELS[k], "count": c} for k, c in by_need.items() if c]}


def complete_for(db, keys: list) -> dict:
    """(patient_id, discharge_date) -> whether that discharge's checklist was
    complete, for the discharges that have a plan at all."""
    out = {}
    if not keys:
        return out
    rows = {k: {"patient_id": k[0], "discharge_date": k[1]} for k in keys}
    pids = sorted({k[0] for k in keys})
    worklist = {}
    for r in db["patient_worklist"].find({"patient_id": {"$in": access.id_variants(pids)}},
                                         {"_id": 0, "patient_id": 1, "discharge_date": 1, "risk_band": 1,
                                          "clinical_group": 1}):
        worklist[(str(r["patient_id"]), str(r.get("discharge_date") or ""))] = r
    owners = {str(o["patient_id"]): o for o in db["care_actions"].find(
        {"patient_id": {"$in": access.id_variants(pids)}}, {"patient_id": 1, "caretaker_ids": 1, "pharmacy_id": 1})}
    for doc in db[COLLECTION].find({"patient_id": {"$in": pids}}):
        key = (doc["patient_id"], doc["discharge_date"])
        if key not in rows:
            continue
        own = owners.get(doc["patient_id"]) or {}
        shaped = _shape(doc["patient_id"], worklist.get(key, rows[key]), doc,
                        {"contact_named": bool(own.get("caretaker_ids")),
                         "pharmacy_on_file": bool(own.get("pharmacy_id"))}, False)
        out[key] = shaped["done"] == shaped["total"]
    return out

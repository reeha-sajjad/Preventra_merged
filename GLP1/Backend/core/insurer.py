"""
An insurer's home page: its members across hospitals, what they cost, and who
is most likely to stop their GLP-1 - so the payer's own care management can
step in. Readmissions has the same page (Readmissions/api/insurer.py) for
readmission risk.

Everything is the insurer's own members (core/access.patient_scope: insurer_id
on the ownership record), and only the overview layer: risk, hospital, member
ID, drug - never diagnoses or risk drivers, which still need a reason, one
patient at a time (core/access.require_detail).

The money uses the same per-patient segment economics as the rest of the app
(the cost_effectiveness collection, one row per segment):
    annual drug spend      annual_cost of each member's segment
    spend at risk          wasted_spend_per_pt of each member's segment - what
                           is spent on members who stop before it does them good
so the figures here add up to the ones on the overview and cost pages.
"""
from typing import Optional

from fastapi import HTTPException

from core import scoped
from core.access import scope_query
from core.mongo import get_db, get_shared_identity_db
from core.pharmacy import drug_label

HIGH_RISK = 0.75                       # the same "critical" line as everywhere
TOP_MEMBERS = 25


async def home(user: dict, scope: Optional[list]) -> dict:
    if user["role"] != "insurer":
        raise HTTPException(status_code=403, detail="This page is for insurers")
    db = get_db()
    mine = scope_query(scope)
    rows = await db.patients.find(mine, {"_id": 0, "patient_idx": 1, "dropout_prob": 1, "cluster": 1,
                                         "assigned_molecule": 1, "is_adherent": 1}).to_list(length=None)
    idxs = [int(r["patient_idx"]) for r in rows]
    access = {int(a["patient_idx"]): a for a in await db.patient_access.find(
        {"patient_idx": {"$in": idxs}}, {"_id": 0, "patient_idx": 1, "hospital_id": 1,
                                         "insurer_member_id": 1}).to_list(length=None)}
    cea = {int(d["cluster"]): d for d in await scoped.cost_table(scope)}
    ident = get_shared_identity_db()
    hospital_ids = {a.get("hospital_id") for a in access.values() if a.get("hospital_id")}
    hospitals = {h["_id"]: h.get("name", h["_id"]) for h in await ident.hospitals.find(
        {"_id": {"$in": list(hospital_ids)}}, {"name": 1}).to_list(length=None)}
    insurer = await ident.insurers.find_one({"_id": user.get("insurer_id")}, {"name": 1}) or {}

    def money(r, field):
        return float((cea.get(int(r.get("cluster") or 0)) or {}).get(field) or 0)

    by_hospital: dict = {}
    for r in rows:
        hid = (access.get(int(r["patient_idx"])) or {}).get("hospital_id")
        h = by_hospital.setdefault(hid, {"hospital_id": hid, "hospital": hospitals.get(hid, hid) if hid else "No hospital on file",
                                         "members": 0, "high_risk": 0, "annual_spend": 0.0, "spend_at_risk": 0.0})
        h["members"] += 1
        h["high_risk"] += float(r.get("dropout_prob") or 0) >= HIGH_RISK
        h["annual_spend"] += money(r, "annual_cost")
        h["spend_at_risk"] += money(r, "wasted_spend_per_pt")
    for h in by_hospital.values():
        h["annual_spend"], h["spend_at_risk"] = round(h["annual_spend"]), round(h["spend_at_risk"])

    top = sorted((r for r in rows if float(r.get("dropout_prob") or 0) >= HIGH_RISK),
                 key=lambda r: -float(r.get("dropout_prob") or 0))[:TOP_MEMBERS]
    members = []
    for r in top:
        a = access.get(int(r["patient_idx"])) or {}
        members.append({"patient_idx": int(r["patient_idx"]), "member_id": a.get("insurer_member_id"),
                        "hospital": hospitals.get(a.get("hospital_id"), a.get("hospital_id")),
                        "dropout_prob": round(float(r.get("dropout_prob") or 0), 4),
                        "drug": drug_label(r.get("assigned_molecule"))})

    # Where the spend at risk sits, by behaviour segment (core/scoped.py: the
    # members' own adherence), and the drug mix - formulary questions.
    profiles = {int(p["cluster"]): p for p in await db.segment_profiles.find(
        {}, {"_id": 0, "cluster": 1, "label": 1, "color": 1}).to_list(length=None)}
    by_segment = []
    for c, d in sorted(cea.items()):
        n = sum(1 for r in rows if int(r.get("cluster") or 0) == c)
        if not n:
            continue
        by_segment.append({"cluster": c, "label": (profiles.get(c) or {}).get("label") or d.get("segment"),
                           "color": (profiles.get(c) or {}).get("color"), "members": n,
                           "adherence": d.get("adherence_rate"),
                           "spend_at_risk": round(float(d.get("wasted_spend_per_pt") or 0) * n)})
    drugs: dict = {}
    for r in rows:
        name = drug_label(r.get("assigned_molecule"))
        drugs[name] = drugs.get(name, 0) + 1

    total = len(rows)
    adherent = sum(1 for r in rows if int(r.get("is_adherent") or 0) == 1)
    return {
        "insurer": {"id": user.get("insurer_id"), "name": insurer.get("name") or user.get("insurer_id")},
        "stats": {"members": total, "hospitals": len([h for h in by_hospital if h]),
                  "adherent": adherent,
                  "high_risk": sum(h["high_risk"] for h in by_hospital.values()),
                  "annual_spend": sum(h["annual_spend"] for h in by_hospital.values()),
                  "spend_at_risk": sum(h["spend_at_risk"] for h in by_hospital.values()),
                  "without_member_id": sum(1 for i in idxs if not (access.get(i) or {}).get("insurer_member_id"))},
        "by_hospital": sorted(by_hospital.values(), key=lambda h: -h["members"]),
        "high_risk_members": members,
        "by_segment": by_segment,
        "drug_mix": sorted(({"drug": k, "members": v} for k, v in drugs.items()), key=lambda d: -d["members"]),
        "high_risk_line": HIGH_RISK,
    }
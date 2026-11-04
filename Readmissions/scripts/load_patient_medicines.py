"""
Give every patient a medicine list with doses (api/patient_medicines.py).

Two sources, in this order:

  1. MIMIC-IV (--prescriptions and --admissions, the hosp module's
     prescriptions.csv[.gz] and admissions.csv[.gz]). For each MIMIC patient on
     the list, their LAST admission, and the orders still running when they
     went home: started before discharge and not stopped more than a day
     before it. Only medicines taken at home (by mouth, injection under the
     skin, inhaled, on the skin, drops) - IV drips stay in hospital. These are
     in-hospital orders, the nearest thing MIMIC has to a discharge list; the
     screens say "from the hospital's orders at discharge".

  2. Demo (--demo). Every patient still without a list gets typical medicines
     for their conditions (patient_worklist.clinical_groups). Marked
     source "demo", and every screen says they're made up.

It never touches a list that already exists (a doctor may have changed it),
never touches the risk model's inputs, and writes nothing without --apply.

    .venv/bin/python scripts/load_patient_medicines.py --demo                     # dry run
    .venv/bin/python scripts/load_patient_medicines.py --demo --apply
    .venv/bin/python scripts/load_patient_medicines.py \\
        --prescriptions ~/mimic-iv/hosp/prescriptions.csv.gz \\
        --admissions ~/mimic-iv/hosp/admissions.csv.gz --demo --apply
"""
from __future__ import annotations

import argparse
import hashlib
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api import patient_medicines  # noqa: E402

# MIMIC route -> how the screens say it. Anything not here is hospital-only.
HOME_ROUTES = {"PO": "By mouth", "PO/NG": "By mouth", "NG": None, "ORAL": "By mouth", "SL": "Under the tongue",
               "SC": "Injection", "SUBCUT": "Injection", "IH": "Inhaled", "INHALATION": "Inhaled",
               "NEB": "Inhaled", "TP": "On the skin", "TD": "On the skin", "TOPICAL": "On the skin",
               "OU": "Eye or ear drops", "OS": "Eye or ear drops", "OD": "Eye or ear drops",
               "BOTH EYES": "Eye or ear drops", "LEFT EYE": "Eye or ear drops", "RIGHT EYE": "Eye or ear drops"}
TIMES = {1: "Once a day", 2: "Twice a day", 3: "Three times a day", 4: "Four times a day"}
MAX_PER_PATIENT = 8

# Typical home medicines by condition, for the demo. Plain adult doses, the
# kind a discharge letter lists - illustrative, never advice.
DEMO = {
    "heart_failure": [("Furosemide", "40 mg", "By mouth", "Once a day", "In the morning"),
                      ("Lisinopril", "10 mg", "By mouth", "Once a day", None),
                      ("Metoprolol succinate", "50 mg", "By mouth", "Once a day", None),
                      ("Spironolactone", "25 mg", "By mouth", "Once a day", None)],
    "cardiac_other": [("Aspirin", "81 mg", "By mouth", "Once a day", None),
                      ("Atorvastatin", "40 mg", "By mouth", "At bedtime", None),
                      ("Apixaban", "5 mg", "By mouth", "Twice a day", None)],
    "neuro_stroke": [("Aspirin", "81 mg", "By mouth", "Once a day", None),
                     ("Clopidogrel", "75 mg", "By mouth", "Once a day", None),
                     ("Atorvastatin", "80 mg", "By mouth", "At bedtime", None)],
    "diabetes": [("Metformin", "1000 mg", "By mouth", "Twice a day", "With meals"),
                 ("Insulin glargine", "20 units", "Injection", "At bedtime", None)],
    "renal": [("Sevelamer", "800 mg", "By mouth", "Three times a day", "With meals"),
              ("Calcitriol", "0.25 mcg", "By mouth", "Once a day", None)],
    "respiratory": [("Tiotropium inhaler", "18 mcg", "Inhaled", "Once a day", None),
                    ("Albuterol inhaler", "2 puffs", "Inhaled", "Every 4-6 hours as needed", "For breathlessness"),
                    ("Prednisone", "40 mg", "By mouth", "Once a day", "For 5 days")],
    "sepsis_infection": [("Amoxicillin-clavulanate", "875 mg", "By mouth", "Twice a day", "For 7 days")],
    "oncology": [("Ondansetron", "8 mg", "By mouth", "Every 4-6 hours as needed", "For nausea")],
    "surgical_injury": [("Acetaminophen", "1000 mg", "By mouth", "Every 4-6 hours as needed", "For pain, no more than 3 g a day"),
                        ("Enoxaparin", "40 mg", "Injection", "Once a day", "For 14 days")],
    "mental_health": [("Sertraline", "50 mg", "By mouth", "Once a day", None)],
    "general": [("Acetaminophen", "650 mg", "By mouth", "Every 4-6 hours as needed", "For pain or fever")],
}


def _id(*parts) -> str:
    return hashlib.sha1("|".join(map(str, parts)).encode()).hexdigest()[:12]


def demo_items(patient_id: str, groups: list) -> list:
    out, seen = [], set()
    for g in list(groups or []) + ["general"]:
        if g == "general" and out:
            break                                   # a pain reliever only for someone with nothing else
        for drug, dose, route, often, note in DEMO.get(g, []):
            if drug in seen:
                continue
            seen.add(drug)
            out.append({"id": _id(patient_id, drug), "drug": drug, "dose": dose, "route": route,
                        "how_often": often, "instructions": note, "source": "demo"})
    return out[:MAX_PER_PATIENT]


def _dose(value, unit) -> str | None:
    v, u = str(value or "").strip(), str(unit or "").strip()
    if not v or v.lower() == "nan":
        return None
    return f"{v} {u}".strip() if u.lower() != "nan" else v


def mimic_items(prescriptions_path: str, admissions_path: str, subject_ids: set) -> dict:
    """{patient_id: [items]} from orders running at each patient's last discharge."""
    import pandas as pd
    adm = pd.read_csv(admissions_path, usecols=["subject_id", "hadm_id", "dischtime"],
                      parse_dates=["dischtime"])
    adm = adm[adm.subject_id.isin(subject_ids)].sort_values("dischtime").groupby("subject_id").tail(1)
    last = dict(zip(adm.hadm_id, zip(adm.subject_id, adm.dischtime)))
    out: dict = {}
    cols = ["subject_id", "hadm_id", "starttime", "stoptime", "drug_type", "drug", "dose_val_rx",
            "dose_unit_rx", "doses_per_24_hrs", "route"]
    # Dose as written ("25", "25-50"), never re-read as a number ("25.0").
    for chunk in pd.read_csv(prescriptions_path, usecols=cols, parse_dates=["starttime", "stoptime"],
                             dtype={"dose_val_rx": str, "dose_unit_rx": str, "route": str, "drug": str},
                             chunksize=500_000):
        chunk = chunk[chunk.hadm_id.isin(last.keys()) & (chunk.drug_type == "MAIN")]
        for r in chunk.itertuples(index=False):
            sid, disch = last[r.hadm_id]
            route = HOME_ROUTES.get(str(r.route).strip().upper())
            if not route or pd.isna(r.starttime) or r.starttime > disch:
                continue
            if pd.notna(r.stoptime) and (disch - r.stoptime).total_seconds() > 86400:
                continue
            dose = _dose(r.dose_val_rx, r.dose_unit_rx)
            if not dose:
                continue
            pid = f"MIMIC-{sid}"
            items = out.setdefault(pid, {})
            name = str(r.drug).strip()
            if name.lower() in items or len(items) >= MAX_PER_PATIENT:
                continue
            per_day = r.doses_per_24_hrs
            often = TIMES.get(int(per_day)) if pd.notna(per_day) and float(per_day).is_integer() else None
            items[name.lower()] = {"id": _id(pid, name), "drug": name, "dose": dose, "route": route,
                                   "how_often": often or "As directed", "instructions": None, "source": "mimic"}
    return {pid: list(v.values()) for pid, v in out.items()}


def plan(db, prescriptions=None, admissions=None, demo=False) -> dict:
    rows = {str(r["patient_id"]): r.get("clinical_groups") or [] for r in
            db["patient_worklist"].find({}, {"_id": 0, "patient_id": 1, "clinical_groups": 1})}
    have = {str(d["patient_id"]) for d in db[patient_medicines.COLLECTION].find({}, {"patient_id": 1})}
    todo = [p for p in rows if p not in have]
    lists: dict = {}
    if prescriptions and admissions:
        sids = {int(p.split("-", 1)[1]) for p in todo if p.startswith("MIMIC-") and p.split("-", 1)[1].isdigit()}
        lists.update(mimic_items(prescriptions, admissions, sids))
    if demo:
        for p in todo:
            if p not in lists:
                lists[p] = demo_items(p, rows[p])
    return {"lists": {p: lists[p] for p in todo if lists.get(p)}, "already": len(have & set(rows)),
            "patients": len(rows)}


def apply(db, planned: dict) -> int:
    now = datetime.now(timezone.utc)
    n = 0
    for pid, items in planned["lists"].items():
        stamped = [{**i, "added_at": now, "changed_at": now,
                    "changed_by": {"id": "script", "name": f"{i['source']} loader", "role": "script"}}
                   for i in items]
        res = db[patient_medicines.COLLECTION].update_one(
            {"patient_id": pid}, {"$setOnInsert": {"patient_id": pid, "items": stamped, "changes": []}},
            upsert=True)
        n += bool(res.upserted_id)
    db[patient_medicines.COLLECTION].create_index("patient_id", unique=True)
    return n


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--prescriptions", help="MIMIC-IV hosp/prescriptions.csv[.gz]")
    parser.add_argument("--admissions", help="MIMIC-IV hosp/admissions.csv[.gz]")
    parser.add_argument("--demo", action="store_true", help="fill everyone else with demo medicines")
    parser.add_argument("--apply", action="store_true", help="write (default: dry run)")
    args = parser.parse_args()
    if bool(args.prescriptions) != bool(args.admissions):
        parser.error("--prescriptions and --admissions go together")
    if not (args.prescriptions or args.demo):
        parser.error("give the MIMIC files, --demo, or both")

    from dotenv import load_dotenv
    from api.db_utils import get_db_name, get_mongo_client
    load_dotenv()
    db = get_mongo_client(os.environ.get("MONGO_URI", "mongodb://localhost:27017"))[get_db_name()]
    planned = plan(db, args.prescriptions, args.admissions, args.demo)
    by_source: dict = {}
    for items in planned["lists"].values():
        by_source[items[0]["source"]] = by_source.get(items[0]["source"], 0) + 1
    tag = "" if args.apply else "[dry run] "
    print(f"{tag}{planned['patients']:,} patients on the list, {planned['already']:,} already have medicines")
    for src, n in sorted(by_source.items()):
        print(f"{tag}  {n:,} would get a {src} list")
    sample = next(iter(planned["lists"].items()), None)
    if sample:
        print(f"{tag}  e.g. {sample[0]}: " + "; ".join(f"{i['drug']} {i['dose']} {i['how_often'].lower()}"
                                                       for i in sample[1]))
    if args.apply:
        print(f"Wrote {apply(db, planned):,} medicine lists.")
    else:
        print("Nothing written. Run again with --apply.")


if __name__ == "__main__":
    main()

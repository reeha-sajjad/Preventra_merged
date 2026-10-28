#!/usr/bin/env python3
"""
Store the model's 94 inputs for the MIMIC patients already on the worklist, so
"Re-evaluate" can score them again with one click.

The loader (scripts/load_mimic_to_mongo.py) scores each patient from the
Phase 1 matrix but keeps only the score, not the inputs. The matrix is not in
the API's image, so this script copies each worklist patient's inputs into the
`model_inputs` collection, one document per (patient_id, hadm_id):

    {patient_id, hadm_id, discharge_date, features: {94 inputs}, feature_set,
     model_version, source: "mimic_matrix", current, matched_on, hospital_id,
     loaded_at}

Which admission is the patient's? The one whose discharge date is the worklist
row's `discharge_date` (moved back by the `shift_days` that scripts/prepare_demo.py
recorded for a demo patient); failing that, the latest admission - what the
loader keeps. `current` marks the admission the worklist row stands for.

PREVIEW BY DEFAULT: nothing is written without --yes. It reads the worklist, the
matrix and the timeline, reports how patients were matched and whether the
stored inputs reproduce each patient's stored score, and stops.

    python scripts/backfill_model_inputs.py                  # preview
    python scripts/backfill_model_inputs.py --yes            # write

Writes to whichever database MONGO_URI / MONGO_DB point at, and only to the
`model_inputs` collection. Running it twice changes nothing. A patient whose
inputs a person has edited (api/patient_rescore.py) keeps those edits.
"""
from __future__ import annotations

import argparse
import hashlib
import os
import sys
from datetime import datetime, timedelta, timezone

import pandas as pd
from dotenv import load_dotenv
from pymongo import ASCENDING, UpdateMany, UpdateOne

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_RESULTS = "data/mimic/model/results"
DEFAULT_MATRIX = f"{_RESULTS}/phase1_matrix.parquet"
DEFAULT_TIMELINE = f"{_RESULTS}/phase1_timeline.parquet"
COLLECTION = "model_inputs"
MODEL_VERSION = "mimic-hgb-calibrated-v1"
SCORE_TOLERANCE = 0.05


def _subject(pid) -> int | None:
    text = str(pid)
    return int(text[6:]) if text.startswith("MIMIC-") and text[6:].isdigit() else None


def plan(db, matrix: pd.DataFrame, timeline: pd.DataFrame, features: list, model=None) -> tuple:
    """(docs, report): the documents to store and how each patient was matched.
    Reads only."""
    from api.db_utils import get_latest_batch_date
    batch = get_latest_batch_date(db, "patient_worklist")
    rows = list(db["patient_worklist"].find(
        {"batch_date": batch, "$or": [{"source": "mimic"}, {"patient_id": {"$regex": "^MIMIC-"}}]},
        {"_id": 0, "patient_id": 1, "discharge_date": 1, "risk_score": 1}))
    pids = [str(r["patient_id"]) for r in rows]
    shift = {str(s["patient_id"]): int(s.get("shift_days") or 0)
             for s in db["demo_prep"].find({"kind": {"$ne": "doctor"}, "patient_id": {"$in": pids}})}
    hospital = {str(c["patient_id"]): c.get("hospital_id")
                for c in db["care_actions"].find({"patient_id": {"$in": pids}}, {"patient_id": 1, "hospital_id": 1})}

    subjects = {p: _subject(p) for p in pids}
    m = matrix[matrix["subject_id"].isin([s for s in subjects.values() if s is not None])]
    m = m.merge(timeline[["hadm_id", "disch_real"]], on="hadm_id", how="left")
    m["_disch"] = pd.to_datetime(m["disch_real"]).dt.strftime("%Y-%m-%d")
    by_subject = {s: g for s, g in m.groupby("subject_id")}

    fingerprint = hashlib.sha1(",".join(features).encode()).hexdigest()[:12]
    loaded_at = datetime.now(timezone.utc).isoformat()
    report = {"worklist_patients": len(rows), "matched_on_discharge_date": 0, "matched_on_latest": 0,
              "not_in_matrix": [], "score_check": None}
    docs, chosen, old_scores = [], [], []
    from api.mimic_scoring import features_for_storage
    for r in rows:
        pid = str(r["patient_id"])
        sub = by_subject.get(subjects[pid])
        if sub is None:
            report["not_in_matrix"].append(pid)
            continue
        matched = "latest"
        pick = None
        try:
            target = (datetime.strptime(str(r.get("discharge_date"))[:10], "%Y-%m-%d")
                      - timedelta(days=shift.get(pid, 0))).strftime("%Y-%m-%d")
            hit = sub[sub["_disch"] == target]
            if len(hit):
                pick, matched = hit.iloc[-1], "discharge_date"
        except ValueError:
            pass
        if pick is None:
            pick = sub.sort_values("n_prior_adm", na_position="first").iloc[-1]
        report["matched_on_discharge_date" if matched == "discharge_date" else "matched_on_latest"] += 1
        docs.append({"patient_id": pid, "hadm_id": int(pick["hadm_id"]),
                     "discharge_date": None if pd.isna(pick["_disch"]) else pick["_disch"],
                     "features": features_for_storage(pick[features]), "feature_set": fingerprint,
                     "model_version": MODEL_VERSION, "source": "mimic_matrix", "current": True,
                     "matched_on": matched, "hospital_id": hospital.get(pid) or None,
                     "loaded_at": loaded_at})
        chosen.append(pick)
        old_scores.append(r.get("risk_score"))

    if model is not None and chosen:
        frame = pd.DataFrame([c[features] for c in chosen]).reset_index(drop=True)
        for col in features:
            if col in matrix.columns and str(matrix[col].dtype) == "category":
                frame[col] = pd.Categorical(frame[col].astype(object), categories=matrix[col].cat.categories)
            else:
                frame[col] = pd.to_numeric(frame[col], errors="coerce")
        new = (model.predict_proba(frame[features])[:, 1] * 100).round(1)
        bad = [{"patient_id": d["patient_id"], "old": o, "new": float(n)}
               for d, o, n in zip(docs, old_scores, new)
               if o is None or abs(float(o) - float(n)) > SCORE_TOLERANCE]
        report["score_check"] = {"checked": len(docs), "matches": len(docs) - len(bad),
                                 "mismatches": len(bad), "first_mismatches": bad[:20]}
    return docs, report


def write(db, docs: list, batch: int = 1000) -> dict:
    """Store the documents: idempotent, and only `model_inputs` is touched.
    Returns {inserted, updated, unchanged}."""
    coll = db[COLLECTION]
    coll.create_index([("patient_id", ASCENDING), ("hadm_id", ASCENDING)], unique=True)
    coll.create_index([("patient_id", ASCENDING), ("current", ASCENDING)])
    out = {"inserted": 0, "updated": 0, "unchanged": 0}
    for i in range(0, len(docs), batch):
        chunk = docs[i:i + batch]
        pids = [d["patient_id"] for d in chunk]
        hadms = [d["hadm_id"] for d in chunk]
        coll.bulk_write([UpdateMany({"patient_id": {"$in": pids}, "hadm_id": {"$nin": hadms}, "current": True},
                                    {"$set": {"current": False}})], ordered=False)
        # Inputs a person has corrected are theirs: keep them.
        edited = {(e["patient_id"], e["hadm_id"]) for e in coll.find(
            {"patient_id": {"$in": pids}, "edited_at": {"$exists": True}}, {"patient_id": 1, "hadm_id": 1})}
        ops = []
        for d in chunk:
            key = {"patient_id": d["patient_id"], "hadm_id": d["hadm_id"]}
            if (d["patient_id"], d["hadm_id"]) in edited:
                ops.append(UpdateOne(key, {"$set": {"current": True}}))
                continue
            rest = {k: v for k, v in d.items() if k != "loaded_at"}
            ops.append(UpdateOne(key, {"$set": rest, "$setOnInsert": {"loaded_at": d["loaded_at"]}},
                                 upsert=True))
        res = coll.bulk_write(ops, ordered=False)
        out["inserted"] += res.upserted_count
        out["updated"] += res.modified_count
        out["unchanged"] += len(ops) - res.upserted_count - res.modified_count
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--matrix", default=DEFAULT_MATRIX)
    ap.add_argument("--timeline", default=DEFAULT_TIMELINE)
    ap.add_argument("--limit", type=int, default=0, help="only the first N worklist patients (0 = all)")
    ap.add_argument("--batch", type=int, default=1000)
    ap.add_argument("--yes", action="store_true", help="write to the database (otherwise preview only)")
    ap.add_argument("--no-score-check", action="store_true",
                    help="skip scoring the stored inputs against the worklist scores")
    a = ap.parse_args()

    load_dotenv()
    from api import mimic_scoring
    from api.db_utils import get_db_name, get_mongo_client
    db = get_mongo_client(os.environ.get("MONGO_URI", "mongodb://localhost:27017"))[get_db_name()]
    state = mimic_scoring._load()
    features = state["features"]
    matrix = pd.read_parquet(a.matrix, columns=["subject_id", "hadm_id"] + features)
    timeline = pd.read_parquet(a.timeline, columns=["hadm_id", "disch_real"])

    docs, report = plan(db, matrix, timeline, features, None if a.no_score_check else state["model"])
    if a.limit:
        docs = docs[:a.limit]
    print(f"  database            : {db.name}  (collection: {COLLECTION})")
    print(f"  worklist patients   : {report['worklist_patients']:,}")
    print(f"  matched by date     : {report['matched_on_discharge_date']:,}")
    print(f"  matched by latest   : {report['matched_on_latest']:,}")
    print(f"  not in the matrix   : {len(report['not_in_matrix']):,}")
    check = report["score_check"]
    if check:
        print(f"  score check         : {check['matches']:,} of {check['checked']:,} reproduce the stored score")
        for bad in check["first_mismatches"]:
            print(f"      {bad['patient_id']}: stored {bad['old']} -> now {bad['new']}")
    if docs:
        print(f"  a document's keys   : {sorted(docs[0])}")
    if not a.yes:
        print("Preview only. Re-run with --yes to write.")
        return
    counts = write(db, docs, a.batch)
    print(f"  written             : {counts['inserted']:,} new, {counts['updated']:,} updated, "
          f"{counts['unchanged']:,} unchanged")


if __name__ == "__main__":
    main()

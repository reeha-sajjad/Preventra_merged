"""
The Data page's upload: any CSVs, read for a person to confirm, then stored the
way every other way in stores them (api/ingest.take_files).

    POST   /api/ingest/drafts                 files + what they are about -> how they were read
    GET    /api/ingest/drafts/{id}            the draft, and how far its import has got
    POST   /api/ingest/drafts/{id}/confirm    the person's corrections -> saved and imported
    DELETE /api/ingest/drafts/{id}            forget it
    POST   /api/ingest/train                  a Model Studio run on this hospital's stored discharges

A draft keeps the uploaded files on the API's disk for a day, so a person can
correct the reading without sending them again. Confirming saves the reading as
the hospital's profile - the next file shaped the same way reads the same way,
whichever way it arrives - and imports in the background: a file of a hundred
thousand discharges takes minutes, and the page follows its progress.

Training starts from what is stored, never from the uploaded file, so the model
learns from exactly the columns live scoring will give it.
"""
from __future__ import annotations

import os
import secrets
import shutil
import tempfile
import threading
from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from pydantic import BaseModel

from api import access, data_reader, ingest
from ingestion import canonical, vocab
from ingestion import profile as data_profile

router = APIRouter(prefix="/api/ingest", tags=["ingest upload"])

DRAFTS = "ingest_drafts"
DRAFT_ROOT = os.environ.get("INGEST_DRAFT_DIR") or os.path.join(tempfile.gettempdir(), "preventra_drafts")
DRAFT_HOURS = 24
MAX_FILE_MB = 100
MIN_TRAINING_OUTCOMES = 50


def _expire(db) -> None:
    """Drafts older than a day go, with their files."""
    cutoff = datetime.utcnow() - timedelta(hours=DRAFT_HOURS)
    for old in db[DRAFTS].find({"created_at": {"$lt": cutoff}}, {"folder": 1}):
        shutil.rmtree(old.get("folder") or "", ignore_errors=True)
        db[DRAFTS].delete_one({"_id": old["_id"]})


def _save(upload: UploadFile, folder: str, taken: set) -> str:
    name = os.path.basename(upload.filename or "file.csv")
    if not name.lower().endswith(".csv"):
        raise HTTPException(status_code=422, detail=f"{name}: only CSV files can be read")
    while name in taken:
        name = "copy_" + name
    taken.add(name)
    path, total = os.path.join(folder, name), 0
    with open(path, "wb") as out:
        while chunk := upload.file.read(1024 * 1024):
            total += len(chunk)
            if total > MAX_FILE_MB * 1024 * 1024:
                raise HTTPException(status_code=413, detail=f"{name} is larger than {MAX_FILE_MB} MB")
            out.write(chunk)
    return name


def _frames(draft: dict) -> dict:
    return {f["filename"]: data_reader.read_frame(f["path"]) for f in draft["files"]}


def _report(proposal: dict, frames: dict) -> dict:
    """The reading as the page shows it."""
    return {"proposal": proposal, "summary": data_reader.describe(proposal),
            "gaps": data_reader.gaps(proposal, frames), "problems": data_reader.required_missing(proposal),
            "incomplete": data_reader.complete_for_builtin(proposal), "ai": proposal.get("source") == "ai",
            "ai_error": proposal.get("ai_error"),
            "columns": {name: {c: data_reader.column_summary(f[c]) for c in f.columns}
                        for name, f in frames.items()},
            "tables": {t: {"required": data_profile.STORE_REQUIRED.get(t, data_profile.required(t)),
                           "fields": data_profile.fields(t),
                           "extras_allowed": t in data_profile.EXTRA_TABLES} for t in data_profile.TABLES},
            "vocabularies": {k: list(v) for k, v in vocab.VOCABULARIES.items()}}


def _public(draft: dict) -> dict:
    return {"draft_id": draft["_id"], "kind": draft["kind"], "status": draft.get("status", "review"),
            "files": [{"filename": f["filename"], "rows": f["rows"]} for f in draft["files"]],
            "result": draft.get("result"), "error": draft.get("error")}


@router.post("/drafts", status_code=201)
def create_draft(request: Request, kind: str = Form("discharges"), files: list[UploadFile] = File(...),
                 hospital_id: Optional[str] = None):
    hospital = ingest._admin_hospital(request, hospital_id)
    if kind not in data_reader.KINDS:
        raise HTTPException(status_code=422, detail=f"kind must be one of {', '.join(data_reader.KINDS)}")
    db = ingest._db()
    _expire(db)
    draft_id = "d_" + secrets.token_hex(6)
    folder = os.path.join(DRAFT_ROOT, draft_id)
    os.makedirs(folder, exist_ok=True)
    try:
        taken: set = set()
        names = [_save(f, folder, taken) for f in files]
        draft = {"files": [{"filename": n, "path": os.path.join(folder, n)} for n in names]}
        frames = _frames(draft)
    except HTTPException:
        shutil.rmtree(folder, ignore_errors=True)
        raise
    except Exception as exc:                                             # noqa: BLE001
        shutil.rmtree(folder, ignore_errors=True)
        raise HTTPException(status_code=422, detail=f"A file could not be read as CSV: {str(exc)[:200]}")
    proposal = data_reader.refine(data_reader.propose(frames, kind, ingest.profile_of(db, hospital)), frames)
    db[DRAFTS].insert_one({
        "_id": draft_id, "hospital_id": hospital, "kind": kind, "folder": folder, "status": "review",
        "files": [{**f, "rows": int(len(frames[f["filename"]]))} for f in draft["files"]],
        "proposal": proposal, "created_by": request.state.user.get("email"), "created_at": datetime.utcnow()})
    return {"draft_id": draft_id, "kind": kind, **_report(proposal, frames)}


def _draft(db, hospital: str, draft_id: str) -> dict:
    found = db[DRAFTS].find_one({"_id": draft_id, "hospital_id": hospital})
    if not found:
        raise HTTPException(status_code=404, detail="This upload is no longer here; upload the files again")
    return found


@router.get("/drafts/{draft_id}")
def get_draft(draft_id: str, request: Request, hospital_id: Optional[str] = None):
    hospital = ingest._admin_hospital(request, hospital_id)
    return _public(_draft(ingest._db(), hospital, draft_id))


@router.delete("/drafts/{draft_id}")
def delete_draft(draft_id: str, request: Request, hospital_id: Optional[str] = None):
    hospital = ingest._admin_hospital(request, hospital_id)
    db = ingest._db()
    draft = _draft(db, hospital, draft_id)
    shutil.rmtree(draft.get("folder") or "", ignore_errors=True)
    db[DRAFTS].delete_one({"_id": draft_id})
    return {"draft_id": draft_id, "deleted": True}


class FileIn(BaseModel):
    filename: str
    table: Optional[str] = None
    columns: dict[str, Optional[str]] = {}
    drop: list[str] = []


class OutcomeIn(BaseModel):
    column: str
    positive: list[str]


class ConfirmIn(BaseModel):
    files: list[FileIn]
    outcome: Optional[OutcomeIn] = None
    codes: dict[str, dict[str, Optional[str]]] = {}
    dayfirst: Optional[bool] = None
    units: Optional[dict] = None
    patient_prefix: Optional[str] = None
    history_only: bool = False


def _validate(body: ConfirmIn, draft: dict, frames: dict) -> list:
    problems = []
    main_table = "encounters" if draft["kind"] == "discharges" else "weeks"
    names = {f["filename"] for f in draft["files"]}
    mains = [f for f in body.files if f.table == main_table]
    if len(mains) != 1:
        problems.append(f"Choose exactly one file of {'discharges' if main_table == 'encounters' else 'weekly check-ins'}")
    for f in body.files:
        if f.filename not in names:
            problems.append(f"{f.filename}: not part of this upload")
            continue
        if f.table is None:
            continue
        if f.table not in data_profile.TABLES:
            problems.append(f"{f.filename}: no table '{f.table}'")
            continue
        allowed = set(data_profile.fields(f.table))
        chosen = [v for v in f.columns.values() if v]
        problems += [f"{f.filename}: '{v}' is not one of our fields for {f.table}" for v in chosen if v not in allowed]
        problems += [f"{f.filename}: {v} is chosen for more than one column"
                     for v in sorted({v for v in chosen if chosen.count(v) > 1})]
        need = data_profile.STORE_REQUIRED.get(f.table, data_profile.required(f.table))
        if f.table in ("diagnoses", "labs", "medications", "procedures", "drg"):
            need = ["encounter_key"]
        problems += [f"{f.filename}: choose the column for {n.replace('_', ' ')}" for n in need if n not in chosen]
    if body.outcome:
        main = next((f for f in mains), None)
        if not main or body.outcome.column not in frames.get(main.filename, {}):
            problems.append("The outcome column is not in the discharges file")
        elif not body.outcome.positive:
            problems.append("Choose which outcome values mean readmitted within 30 days")
    for field, codes in body.codes.items():
        if field not in vocab.VOCABULARIES:
            problems.append(f"codes: no coded field '{field}'")
            continue
        problems += [f"codes: '{v}' is not one of our {field.replace('_', ' ')} values"
                     for v in codes.values() if v and v not in vocab.VOCABULARIES[field]]
    return problems


@router.post("/drafts/{draft_id}/confirm", status_code=202)
def confirm_draft(draft_id: str, body: ConfirmIn, request: Request, hospital_id: Optional[str] = None):
    """Save the reading as the hospital's profile, then import in the
    background; GET the draft to follow it."""
    hospital = ingest._admin_hospital(request, hospital_id)
    db = ingest._db()
    draft = _draft(db, hospital, draft_id)
    if draft.get("status") not in ("review", "failed"):
        raise HTTPException(status_code=409, detail="This upload is already being imported")
    prefix = ingest.hospital_prefix(db, hospital)
    if not prefix:
        prefix = (body.patient_prefix or "").strip().upper()
        if not ingest.PREFIX_RE.match(prefix):
            raise HTTPException(status_code=422, detail=["Choose a patient id prefix: 2 to 10 capital letters or "
                                                         "digits, the start of every patient id (CGH-P1931)"])
    frames = _frames(draft)
    problems = _validate(body, draft, frames)
    if problems:
        raise HTTPException(status_code=422, detail=problems)

    set_, unset = {"patient_prefix": prefix, "updated_by": request.state.user.get("email"),
                   "updated_at": datetime.utcnow()}, {}
    for f in body.files:
        if not f.table:
            continue
        set_[f"columns.{f.table}"] = ingest.store_columns(f.columns)
        if f.table in data_profile.EXTRA_TABLES:
            set_[f"drop.{f.table}"] = list(f.drop)
            unset[f"extras.{f.table}"] = ""            # keep everything not dropped, from now on
    if body.outcome:
        set_["outcome"] = {"table": "encounters", "column": body.outcome.column, "positive": body.outcome.positive}
    elif draft["kind"] == "discharges":
        unset["outcome"] = ""
    if body.dayfirst is not None:
        set_["dayfirst"] = body.dayfirst
    if body.units:
        set_["units"] = body.units
    for field, codes in body.codes.items():
        for value, ours in codes.items():
            if ours:
                set_[f"{field}.{ingest._store_key(value)}"] = ours
    change = {"$set": set_, **({"$unset": unset} if unset else {})}
    db[ingest.MAPPINGS].update_one({"_id": hospital}, change, upsert=True)

    db[DRAFTS].update_one({"_id": draft_id}, {"$set": {"status": "importing", "error": None,
                                                     "confirmed_at": datetime.utcnow()}})
    client = {"client_id": "upload", "hospital_id": hospital, "patient_prefix": prefix, "source": "upload",
              "by": request.state.user.get("email")}
    tables = {f.filename: f.table for f in body.files if f.table}
    threading.Thread(target=_import, args=(db, draft, frames, tables, client, body.history_only),
                     daemon=True).start()
    return _public(db[DRAFTS].find_one({"_id": draft_id}))


def _import(db, draft: dict, frames: dict, tables: dict, client: dict, history_only: bool) -> None:
    """The import itself, off the request. The outcome lands on the draft."""
    try:
        by_table: dict = {}
        for name, table in tables.items():
            rows = [{k: v for k, v in r.items() if v != ""} for r in frames[name].to_dict("records")]
            by_table.setdefault(table, []).extend(rows)
        taken = ingest.take_files(db, client, by_table, history_only=history_only, chunked=True)
        batches = [b for kind in taken.values() for b in kind]
        rejected = [r for b in batches for r in b["rejected"]]
        result = {"accepted": sum(b["accepted"] for b in batches), "rejected": len(rejected),
                  "rejected_examples": rejected[:50],
                  "warnings": sum(len(b["warnings"]) for b in batches),
                  "warning_examples": [w for b in batches for w in b["warnings"]][:20],
                  "batch_ids": [b["batch_id"] for b in batches], "history_only": history_only}
        db[DRAFTS].update_one({"_id": draft["_id"]}, {"$set": {"status": "done", "result": result}})
        shutil.rmtree(draft.get("folder") or "", ignore_errors=True)
    except Exception as exc:                                             # noqa: BLE001
        db[DRAFTS].update_one({"_id": draft["_id"]}, {"$set": {
            "status": "failed", "error": f"{type(exc).__name__}: {str(exc)[:300]}"}})


# ------------------------------------------------------------------- training
class TrainIn(BaseModel):
    name: Optional[str] = None


@router.get("/training")
def training_status(request: Request, hospital_id: Optional[str] = None):
    """How much this hospital's stored history could teach a model now."""
    hospital = ingest._admin_hospital(request, hospital_id)
    table = canonical.training_table(list(ingest._db()[ingest.STAYS].find({"hospital_id": hospital}, {"_id": 0})))
    known = table[canonical.discharge_features.OUTCOME].dropna()
    return {"discharges": int(len(table)), "outcome_known": int(len(known)),
            "readmitted": int(known.sum()), "minimum": MIN_TRAINING_OUTCOMES,
            "ready": int(len(known)) >= MIN_TRAINING_OUTCOMES and 0 < int(known.sum()) < len(known)}


@router.post("/train", status_code=201)
def train(request: Request, payload: Optional[TrainIn] = None, hospital_id: Optional[str] = None):
    """A Model Studio run on everything stored for the hospital, with the
    outcome, patient and date columns ready for it."""
    from api import model_studio
    hospital = ingest._admin_hospital(request, hospital_id)
    user = request.state.user
    access.require(user, "manage_models")
    db = ingest._db()
    table = canonical.training_table(list(db[ingest.STAYS].find({"hospital_id": hospital}, {"_id": 0})))
    outcome = canonical.discharge_features.OUTCOME
    known = table[outcome].dropna()
    if len(known) < MIN_TRAINING_OUTCOMES or known.sum() == 0 or known.sum() == len(known):
        raise HTTPException(status_code=422, detail=(
            f"Not enough to train on yet: {len(known)} discharges have a known outcome, "
            f"{int(known.sum())} of them readmitted. At least {MIN_TRAINING_OUTCOMES}, with both outcomes, are needed."))
    # Columns no stay has a value for (a flat export has no lab rows) would
    # only crowd the plan. The patient is named patient_id so Model Studio
    # takes it as the identifier, never as a feature.
    keep = [c for c in table.columns if c in ("patient_key", "discharge_datetime", outcome) or table[c].notna().any()]
    table = table[keep].rename(columns={"patient_key": "patient_id"})
    fd, path = tempfile.mkstemp(suffix=".csv", dir=model_studio._ensure_root())
    os.close(fd)
    table.to_csv(path, index=False)
    name = (payload.name if payload and payload.name else "") or f"Discharges {datetime.now():%Y-%m-%d}"
    try:
        job = model_studio.create_job(db, user, path, f"{hospital}_discharges.csv", target="discharge",
                                      mode="new", base_model_id=None, algorithm="auto", name=name)
    finally:
        if os.path.exists(path):
            os.remove(path)
    return {"job_id": job["job_id"], "rows": int(len(table)), "outcome_known": int(len(known))}

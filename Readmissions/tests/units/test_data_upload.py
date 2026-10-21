"""The plug-and-play upload (api/data_reader.py, api/data_upload.py): any CSVs
are read, a person confirms, they are stored the way every other way in stores
them, and a model can be trained on them.

Uses the first rows of the UCI diabetes file in data/diabetic - one flat row
per discharge, no dates, codes as words, the outcome in a column - and small
made-up files for the rest. Same two hospitals and accounts as test_isolation.py.
"""
import io
import time

import pandas as pd
import pytest

from api import data_reader, data_upload, ingest, llm, llm_providers, scoring_service
from ingestion import worker
from tests.units.test_ingest import bundle, machine, token  # noqa: F401
from tests.units.test_ingest_scoring import studio  # noqa: F401
from tests.units.test_isolation import as_user, main, world  # noqa: F401

DIABETIC = "data/diabetic/diabetic_data.csv"


@pytest.fixture
def flat():
    """400 discharges, as UCI exported them."""
    frame = pd.read_csv(DIABETIC, nrows=400, dtype=str, keep_default_na=False)
    return frame.to_csv(index=False)


@pytest.fixture(autouse=True)
def drafts(tmp_path, monkeypatch):
    monkeypatch.setattr(data_upload, "DRAFT_ROOT", str(tmp_path / "drafts"))
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)                  # the rules alone, unless a test says so
    llm.invalidate()


def upload(client, kind="discharges", **files):
    return client.post("/api/ingest/drafts", data={"kind": kind},
                       files=[("files", (name, io.BytesIO(text.encode()), "text/csv")) for name, text in files.items()])


def confirm(client, draft, **over):
    p = draft["proposal"]
    body = {"files": [{"filename": f["filename"], "table": f["table"], "columns": f["columns"], "drop": []}
                      for f in p["files"]],
            "outcome": {"column": p["outcome"]["column"], "positive": p["outcome"]["positive"]} if p["outcome"] else None,
            "codes": p["codes"], "dayfirst": p["dayfirst"], **over}
    return client.post(f"/api/ingest/drafts/{draft['draft_id']}/confirm", json=body)


def wait(client, draft_id):
    for _ in range(200):
        state = client.get(f"/api/ingest/drafts/{draft_id}").json()
        if state["status"] in ("done", "failed"):
            return state
        time.sleep(0.05)
    raise AssertionError("the import did not finish")


# ------------------------------------------------------------------ reading
def test_a_flat_export_is_read_without_any_setup(main, world, flat):
    admin = as_user(main, world, "admin@a.test")
    r = upload(admin, **{"diabetic_data.csv": flat})
    assert r.status_code == 201, r.text
    draft = r.json()
    p = draft["proposal"]
    cols = {c: f for c, f in p["files"][0]["columns"].items() if f}
    assert cols["encounter_id"] == "encounter_key" and cols["patient_nbr"] == "patient_key"
    assert cols["diag_1"] == "diagnosis_code_1" and cols["age"] == "age_years" and cols["gender"] == "sex"
    assert p["outcome"]["column"] == "readmitted" and p["outcome"]["positive"] == ["<30"]
    assert p["codes"]["sex"]["Female"] == "F" and p["codes"]["race_ethnicity"]["Caucasian"] == "WHITE"
    assert draft["problems"] == [] and "admit_datetime" in draft["incomplete"]
    assert any("dates" in g["what"] for g in draft["gaps"])
    assert any("no admission and discharge dates" in line for line in draft["summary"])
    assert draft["columns"]["diabetic_data.csv"]["readmitted"]["values"]["<30"] > 0
    assert not draft["ai"]


def test_several_files_are_told_apart_and_joined(main, world):
    visits = "VisitNo,MRN,Ward\nV1,M1,A\nV2,M1,B\nV3,M2,A\n"
    dx = "Visit Number,Code,Rank\nV1,I5023,1\nV2,J441,1\nV3,N179,1\n"
    people = "PatientMRN,Sex\nM1,F\nM2,M\n"
    other = "x,y\n1,2\n3,4\n"
    r = upload(as_user(main, world, "admin@a.test"), **{"visits.csv": visits, "dx_export.csv": dx,
                                                        "people.csv": people, "notes.csv": other}).json()
    tables = {f["filename"]: f["table"] for f in r["proposal"]["files"]}
    assert tables == {"visits.csv": "encounters", "dx_export.csv": "diagnoses", "people.csv": "patients",
                      "notes.csv": None}
    by = {f["filename"]: f["columns"] for f in r["proposal"]["files"]}
    assert by["dx_export.csv"]["Visit Number"] == "encounter_key" and by["people.csv"]["PatientMRN"] == "patient_key"
    assert any("notes.csv" in p for p in r["problems"])


# --------------------------------------------------------------- confirming
def test_confirming_stores_the_files_and_remembers_how_they_read(main, world, flat):
    db = world["db"]
    admin = as_user(main, world, "admin@a.test")
    draft = upload(admin, **{"diabetic_data.csv": flat}).json()
    assert confirm(admin, draft).status_code == 422                      # no prefix yet: asked for one
    started = confirm(admin, draft, patient_prefix="DIA", history_only=True)
    assert started.status_code == 202 and started.json()["status"] == "importing"
    done = wait(admin, draft["draft_id"])
    assert done["status"] == "done", done
    assert done["result"]["accepted"] == 400 and done["result"]["rejected"] == 0

    stay = db[ingest.STAYS].find_one({"encounter_key": "149190"})
    assert stay["patient_id"] == "DIA-55629189" and stay["history"] and not stay["complete"]
    assert stay["patient"]["sex"] == "F" and stay["patient"]["race_ethnicity"] == "WHITE"
    assert stay["encounter"]["age_years"] == 15 and stay["reported_outcome"] is False
    assert [d["icd_code"] for d in stay["diagnoses"]] == ["276", "25001", "255"]
    assert "readmitted" not in stay["extras"] and stay["extras"]["insulin"] == "Up"

    saved = ingest.profile_of(db, "hosp-a")
    assert saved["outcome"] == {"table": "encounters", "column": "readmitted", "positive": ["<30"]}
    assert saved["sex"]["Female"] == "F" and saved["patient_prefix"] == "DIA"
    # The next file shaped the same way reads the way this one was confirmed.
    again = upload(admin, **{"december.csv": flat}).json()
    assert again["proposal"]["files"][0]["columns"]["patient_nbr"] == "patient_key"


def test_a_model_can_be_trained_on_what_was_stored(main, studio, flat):
    db = studio["db"]
    admin = as_user(main, studio, "admin@a.test")
    draft = upload(admin, **{"diabetic_data.csv": flat}).json()
    confirm(admin, draft, patient_prefix="DIA", history_only=True)
    wait(admin, draft["draft_id"])
    ready = admin.get("/api/ingest/training").json()
    assert ready["discharges"] == 400 and ready["outcome_known"] == 400 and ready["ready"]
    r = admin.post("/api/ingest/train", json={"name": "Diabetes first model"})
    assert r.status_code == 201, r.text
    job = admin.get(f"/api/studio/jobs/{r.json()['job_id']}").json()
    assert job["name"] == "Diabetes first model"
    csv = pd.read_csv(f"{job.get('workdir') or ''}/upload.csv") if job.get("workdir") else None
    if csv is not None:
        assert "patient_id" in csv.columns and "readmit_30d" in csv.columns and "x_insulin" in csv.columns
    assert db  # the run itself is Model Studio's (test_model_studio.py)


def test_too_little_history_says_so(main, world):
    r = as_user(main, world, "admin@a.test").post("/api/ingest/train", json={})
    assert r.status_code == 422 and "Not enough to train on yet" in r.json()["detail"]


def test_incomplete_discharges_wait_for_the_hospitals_own_model(main, world, flat):
    db = world["db"]
    admin = as_user(main, world, "admin@a.test")
    small = "\n".join(flat.splitlines()[:6]) + "\n"
    draft = upload(admin, **{"diabetic_data.csv": small}).json()
    # These five were all NO or >30: nothing marks a readmission, so no outcome.
    assert confirm(admin, draft, patient_prefix="DIA").status_code == 422
    assert confirm(admin, draft, patient_prefix="DIA", outcome=None).status_code == 202
    wait(admin, draft["draft_id"])
    worker.run(db, once=True)
    assert db["patient_worklist"].count_documents({"patient_id": {"$regex": "^DIA-"}}) == 0
    batch = admin.get("/api/ingest/batches").json()["batches"][0]
    assert batch["scored"] == 0 and "train a model on your own data" in batch["not_scored"][0]["reason"]


def test_weekly_check_ins_find_their_discharge_without_its_key(main, world, token):
    db = world["db"]
    machine(main, token).post("/ingest/v1/encounters", json={"bundles": [bundle()]})
    admin = as_user(main, world, "admin@a.test")
    weeks = "Patient,Visit date,Weight,SpO2\nP-1,2026-04-12,86.7,94\nP-9,2026-04-12,70,97\n"
    draft = upload(admin, kind="weekly", **{"home_readings.csv": weeks}).json()
    cols = draft["proposal"]["files"][0]["columns"]
    assert cols == {"Patient": "patient_key", "Visit date": "week_date", "Weight": "weight_kg", "SpO2": "spo2"}
    confirm(admin, draft)
    done = wait(admin, draft["draft_id"])
    assert done["result"]["accepted"] == 1 and done["result"]["rejected"] == 1
    week = db[ingest.WEEKS].find_one()
    assert week["encounter_key"] == "E-1" and week["week_number"] == 1 and week["readings"]["weight_kg"] == 86.7


def test_the_ai_model_improves_the_reading_and_cannot_break_it(main, world, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "env-key")
    llm.invalidate()
    answer = {"files": [{"filename": "visits.csv", "table": "encounters", "columns": {
                "Ref": "encounter_key", "Person": "patient_key", "When out": "discharge_datetime",
                "Bogus": "not_a_field", "Ward": "sex"}}],
              "outcome": {"column": "Back", "positive": ["Y", "Maybe"]},
              "codes": {"sex": {"Woman": "F", "Man": "nonsense"}},
              "summary": ["One row per discharge."]}

    class Fake:
        def complete(self, model, system, messages, **kw):
            import json
            return llm_providers.Reply(text=json.dumps(answer))
    monkeypatch.setattr(llm_providers, "make", lambda *a, **k: Fake())
    visits = "Ref,Person,When out,Bogus,Ward,Gender,Back\nR1,P1,2026-04-01,x,A,Woman,Y\nR2,P1,2026-05-01,y,B,Man,N\n"
    r = upload(as_user(main, world, "admin@a.test"), **{"visits.csv": visits}).json()
    cols = r["proposal"]["files"][0]["columns"]
    assert r["ai"] and cols["When out"] == "discharge_datetime" and cols["Bogus"] is None
    assert cols["Ward"] is None or cols["Ward"] != "sex" or cols["Gender"] != "sex"     # one column per field
    assert r["proposal"]["outcome"]["positive"] == ["Y"]                                 # "Maybe" is not in the file
    assert r["proposal"]["codes"]["sex"]["Woman"] == "F" and r["proposal"]["codes"]["sex"]["Man"] != "nonsense"
    assert r["summary"] == ["One row per discharge."]


def test_column_names_with_dots_are_stored_safely(main, world):
    admin = as_user(main, world, "admin@a.test")
    visits = "Visit.ID,Pat.No,Adm.Date\nV1,P1,2026-04-01\n"
    draft = upload(admin, **{"v.csv": visits}).json()
    cols = draft["proposal"]["files"][0]["columns"]
    cols["Visit.ID"], cols["Pat.No"], cols["Adm.Date"] = "encounter_key", "patient_key", "admit_datetime"
    r = confirm(admin, draft, patient_prefix="DOT")
    assert r.status_code == 202, r.text
    wait(admin, draft["draft_id"])
    assert ingest.profile_of(world["db"], "hosp-a")["columns"]["encounters"]["Adm.Date"] == "admit_datetime"


def test_dates_written_day_first_are_noticed():
    frames = {"a.csv": pd.DataFrame({"d": ["13/10/2026", "01/02/2026"]})}
    assert data_reader.dayfirst(frames) is True
    assert data_reader.dayfirst({"a.csv": pd.DataFrame({"d": ["10/13/2026"]})}) is False
    assert scoring_service  # imported for the scoring path above


def test_dropped_and_personal_columns_are_not_kept(main, world):
    db = world["db"]
    admin = as_user(main, world, "admin@a.test")
    visits = ("VisitNo,MRN,Patient Name,Ward,Bed\n"
              "V1,M1,Ann Lee,A,12\nV2,M1,Ann Lee,B,3\nV3,M2,Bo Chan,A,7\n")
    draft = upload(admin, **{"visits.csv": visits}).json()
    summary = draft["columns"]["visits.csv"]["Patient Name"]
    assert summary["personal"] and "values" not in summary                   # never shown, to a person or a model
    body_files = [{**f, "drop": ["Bed"]} for f in draft["proposal"]["files"]]
    r = client_confirm(admin, draft, files=body_files, patient_prefix="WRD", outcome=None)
    assert r.status_code == 202, r.text
    wait(admin, draft["draft_id"])
    stay = db[ingest.STAYS].find_one({"encounter_key": "V1"})
    assert stay["extras"] == {"ward": "A"}
    assert ingest.profile_of(db, "hosp-a")["drop"]["encounters"] == ["Bed"]
    # The next upload of the same shape starts with Bed dropped again.
    again = upload(admin, **{"visits_may.csv": visits}).json()
    assert again["proposal"]["files"][0]["drop"] == ["Bed"]


def client_confirm(client, draft, files, **over):
    p = draft["proposal"]
    body = {"files": files, "outcome": None, "codes": p["codes"], "dayfirst": p["dayfirst"], **over}
    return client.post(f"/api/ingest/drafts/{draft['draft_id']}/confirm", json=body)


def test_the_mapping_tab_keeps_every_column_but_the_dropped_ones(main, world):
    db = world["db"]
    admin = as_user(main, world, "admin@a.test")
    db[ingest.MAPPINGS].update_one({"_id": "hosp-a"}, {"$set": {"extras": {"encounters": ["ward"]}}}, upsert=True)
    r = admin.put("/api/ingest/profile", json={"drop": {"encounters": ["bed"]}})
    assert r.status_code == 200, r.text
    profile = r.json()["profile"]
    assert profile["drop"] == {"encounters": ["bed"]} and "encounters" not in profile["extras"]
    both = admin.put("/api/ingest/profile", json={"drop": {"encounters": []}, "extras": {"encounters": []}})
    assert both.status_code == 422


# ---------------------------------------------------------------------------
# Train in Model Studio on the export, upload current patients, see them scored
# ---------------------------------------------------------------------------
# The workflow a hospital expects: a model trained on its own export (the raw
# columns and values), then this week's patients uploaded on the Data page and
# scored by it - including the ones uploaded before any model existed.
RAW_FEATURES = ["time_in_hospital", "num_lab_procedures", "num_medications", "number_inpatient",
                "number_emergency", "age", "gender", "insulin"]
RAW_CATEGORICAL = ["age", "gender", "insulin"]


def _raw_model(tmp_path):
    from tests.units.test_ingest_scoring import _train
    frame = pd.read_csv(DIABETIC, nrows=2000)
    frame["outcome"] = (frame["readmitted"] == "<30").astype(int)
    return _train(tmp_path, "raw", frame[RAW_FEATURES + ["outcome"]], RAW_FEATURES, RAW_CATEGORICAL)


def _register(db, model_id, config, blob):
    from api import model_studio
    from api.studio_templates import STANDARD_PIPELINE
    model_studio._registry(db).insert_one({
        "model_id": model_id, "name": "Trained on the export", "target": "discharge",
        "hospital_id": "hosp-a", "algorithm": "logistic_regression", "config": config,
        "code": STANDARD_PIPELINE, "artifact_id": model_studio._gridfs(db).put(blob),
        "bands": {"high_score_threshold": 20.0, "low_score_threshold": 10.0},
        "data": {"history": False, "id_column": None, "time_column": None},
        "can_score": True, "created_at": "2026-10-21 00:00:00"})


def _current(admin, flat, rows=30):
    small = "\n".join(flat.splitlines()[:rows + 1]) + "\n"
    draft = upload(admin, **{"this_week.csv": small}).json()
    # Current patients: what happens to them next is not known yet, so no outcome.
    started = confirm(admin, draft, patient_prefix="DIA", history_only=False, outcome=None)
    assert started.status_code == 202, started.text
    return wait(admin, draft["draft_id"])


def test_current_patients_keep_their_row_as_sent_and_history_does_not(main, world, flat):
    db = world["db"]
    admin = as_user(main, world, "admin@a.test")
    _current(admin, flat, rows=5)
    stay = db[ingest.STAYS].find_one({"encounter_key": "149190"})
    sent = dict(stay["source"])
    assert sent["age"] == "[10-20)" and sent["gender"] == "Female" and sent["diag_1"] == "276"
    assert stay["dates_assumed"] and stay["encounter"]["discharge_datetime"].date() == pd.Timestamp.now().date()
    assert (stay["encounter"]["discharge_datetime"] - stay["encounter"]["admit_datetime"]).days == 3   # time_in_hospital

    draft = upload(admin, **{"history.csv": flat}).json()
    confirm(admin, draft, patient_prefix="DIA", history_only=True)
    wait(admin, draft["draft_id"])
    old = db[ingest.STAYS].find_one({"encounter_key": "64410"})
    assert old["history"] and not old.get("source") and not old.get("dates_assumed")
    assert old["encounter"].get("discharge_datetime") is None


def test_a_model_trained_on_the_export_scores_current_patients_as_sent(main, studio, flat, tmp_path):
    db = studio["db"]
    config, blob = _raw_model(tmp_path)
    _register(db, "m-raw", config, blob)
    from api import model_studio
    model_studio.deploy_model(db, "m-raw", model_studio.system_actor("hosp-a"))
    admin = as_user(main, studio, "admin@a.test")
    _current(admin, flat)
    worker.run(db, once=True)
    rows = list(db["patient_worklist"].find({"patient_id": {"$regex": "^DIA-"}}))
    assert len(rows) >= 25
    assert {r["model_version"] for r in rows} == {"studio:m-raw"}
    assert all(r["dates_assumed"] and r["discharge_date"] == pd.Timestamp.now().strftime("%Y-%m-%d") for r in rows)
    assert all(0 <= r["risk_score"] <= 100 and r["driver_1"] for r in rows)


def test_patients_waiting_for_a_model_are_scored_when_one_is_deployed(main, studio, flat, tmp_path):
    db = studio["db"]
    admin = as_user(main, studio, "admin@a.test")
    _current(admin, flat)
    worker.run(db, once=True)
    assert db["patient_worklist"].count_documents({"patient_id": {"$regex": "^DIA-"}}) == 0
    waiting = admin.get("/api/ingest/scoring").json()["waiting"]
    assert waiting >= 25

    config, blob = _raw_model(tmp_path)
    _register(db, "m-raw", config, blob)
    deployed = admin.post("/api/studio/models/m-raw/deploy")
    assert deployed.status_code == 200, deployed.text
    assert deployed.json()["rescore"]["queued"] == waiting      # queued by the deploy itself
    worker.run(db, once=True)
    assert db["patient_worklist"].count_documents({"patient_id": {"$regex": "^DIA-"},
                                                   "model_version": "studio:m-raw"}) == waiting
    assert admin.get("/api/ingest/scoring").json()["waiting"] == 0


def test_the_row_as_sent_leaves_out_the_outcome_and_anything_personal():
    from ingestion import profile
    rows = [{"encounter_id": "1", "patient_nbr": "9", "age": "[70-80)", "readmitted": "<30",
             "patient_name": "Ann Smith", "phone": "0300", "insulin": "Up"}]
    prof = {"outcome": {"table": "encounters", "column": "readmitted", "positive": ["<30"]}}
    sent = dict(profile.apply("encounters", rows, prof, keep_source=True)[0]["source"])
    assert sent == {"encounter_id": "1", "patient_nbr": "9", "age": "[70-80)", "insulin": "Up"}
    assert "source" not in profile.apply("encounters", rows, prof)[0]          # history keeps none

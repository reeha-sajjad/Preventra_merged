"""Continuous ingestion: each hospital's own columns (ingestion/profile.py),
uploads from the Data page, history-only loads, re-scoring after a new model
is deployed, the worker's clock (ingestion/scheduler.py) and pulls from a
hospital's own database (ingestion/pull.py, api/ingest_connections.py).

Same two hospitals and accounts as test_isolation.py.
"""
import io
import sqlite3
from datetime import datetime

import numpy as np
import pandas as pd
import pytest

from api import ingest, ingest_connections, scoring_service
from ingestion import agent, pull, scheduler, worker
from ingestion import profile as data_profile
from tests.units.test_ingest import bundle, machine, token  # noqa: F401
from tests.units.test_ingest_scoring import PID, _deploy, _train, studio  # noqa: F401
from tests.units.test_isolation import as_user, main, world  # noqa: F401

# A hospital that exports in its own words: day-first dates, a date of birth
# instead of an age, pounds, its own codes, and two columns of its own.
ENCOUNTERS = (
    "VISIT_ID,MRN,ADM_DT,DISCH_DT,DOB,ADM_TYPE,DISCH_DISP,GENDER,WEIGHT_LB,WARD,FRAILTY,NOTES\n"
    "V1,M1,05/04/2026 08:00,09/04/2026 12:00,15/06/1950,ER,HOME,F,185,Cardiology,4,x\n"
    "V2,M2,10/04/2026 09:30,12/04/2026 10:00,01/01/1961,ER,HOME,M,,General,2,y\n")
DIAGNOSES = "VISIT_ID,DX_CODE,DX_SEQ,ICD_VER\nV1,I5023,1,10\nV2,J441,1,10\n"
PROFILE = {"columns": {"encounters": {"DISCH_DISP": "discharge_disposition", "WEIGHT_LB": "discharge_weight_kg"},
                       "diagnoses": {"DX_CODE": "icd_code", "DX_SEQ": "sequence", "ICD_VER": "icd_version"}},
           "extras": {"encounters": ["WARD", "FRAILTY"]}, "units": {"weight": "lb"}, "dayfirst": True,
           "patient_prefix": "CGH"}


def files(**tables):
    return {name: (f"{name}.csv", io.BytesIO(text.encode()), "text/csv") for name, text in tables.items()}


@pytest.fixture
def admin(main, world):
    client = as_user(main, world, "admin@a.test")
    assert client.put("/api/ingest/mapping", json={"admission_type": {"ER": "EMERGENCY"}}).status_code == 200
    return client


# ------------------------------------------------------------ the profile
def test_their_column_names_are_suggested_and_checked():
    headers = ["VISIT_ID", "MRN", "ADM_DT", "DISCH_DT", "DOB", "ADM_TYPE", "DISCH_DISP", "GENDER", "WEIGHT_LB", "WARD"]
    got = data_profile.suggest("encounters", headers)
    assert (got["VISIT_ID"], got["MRN"], got["ADM_DT"], got["DOB"], got["GENDER"]) == \
        ("encounter_key", "patient_key", "admit_datetime", "date_of_birth", "sex")
    assert got["DISCH_DISP"] is None
    checked = data_profile.check("encounters", headers, {"extras": {"encounters": ["WARD"]}})
    assert checked["missing"] == []                               # the keys are there: it can be stored
    assert checked["incomplete"] == ["discharge_disposition"]     # the age comes from the date of birth
    assert checked["extras"] == ["WARD"] and "WEIGHT_LB" in checked["dropped"]
    assert data_profile.detect_table("2026-10-19_LABS_export.csv") == "labs"
    assert data_profile.detect_table("admissions.csv") == "encounters"


def test_dates_units_and_extras_are_read_the_hospitals_way():
    rows = [{"VISIT_ID": "V1", "ADM_DT": "05/04/2026 08:00", "DOB": "15/06/1950", "WEIGHT_LB": "185",
             "WARD": "Cardiology", "FRAILTY": "4", "NOTES": "never kept"}]
    out = data_profile.apply("encounters", rows, PROFILE)[0]
    assert out["admit_datetime"] == "2026-04-05T08:00:00" and out["date_of_birth"] == "1950-06-15"
    assert out["discharge_weight_kg"] == 83.91
    assert out["extras"] == {"ward": "Cardiology", "frailty": "4"} and "NOTES" not in str(out)
    assert data_profile.age_at("1950-06-15", "2026-04-05") == 75
    # Month first unless the hospital says otherwise.
    assert data_profile.apply("encounters", [{"ADM_DT": "05/04/2026"}], {})[0]["admit_datetime"] == "2026-05-04"


def test_a_profile_is_checked_before_it_is_saved(admin, world, main):
    bad = admin.put("/api/ingest/profile", json={"columns": {"encounters": {"A": "nonsense", "B": "sex", "C": "sex"},
                                                             "nowhere": {}},
                                                 "extras": {"labs": ["X"]}, "units": {"weight": "stone"},
                                                 "patient_prefix": "no good"})
    assert bad.status_code == 422
    detail = " ".join(bad.json()["detail"])
    for words in ("'nonsense' is not one of our fields", "'sex' is mapped from more than one column",
                  "no table 'nowhere'", "only encounters and patients", "weight must be one of",
                  "2 to 10 capital letters"):
        assert words in detail
    assert as_user(main, world, "cm@a.test").get("/api/ingest/profile").status_code == 403
    saved = admin.put("/api/ingest/profile", json=PROFILE).json()["profile"]
    assert saved["patient_prefix"] == "CGH" and saved["units"]["weight"] == "lb" and saved["dayfirst"]
    assert as_user(main, world, "admin@b.test").get("/api/ingest/profile").json()["profile"]["patient_prefix"] is None
    # The codes live on the same document: saving them leaves the profile as it was.
    admin.put("/api/ingest/mapping", json={"payer_class": {"MCR": "MEDICARE"}})
    after = admin.get("/api/ingest/profile").json()["profile"]
    assert after["columns"] == saved["columns"] and after["patient_prefix"] == "CGH" and after["dayfirst"]


# ----------------------------------------------------------------- upload
def test_an_upload_in_the_hospitals_own_format_is_read_through_its_profile(admin, world):
    db = world["db"]
    assert admin.post("/api/ingest/upload", files=files(encounters=ENCOUNTERS)).status_code == 422  # no prefix yet
    admin.put("/api/ingest/profile", json=PROFILE)
    preview = admin.post("/api/ingest/preview", files={"file": ("visits_encounters.csv", io.BytesIO(ENCOUNTERS.encode()))})
    assert preview.status_code == 200 and preview.json()["missing"] == [] and preview.json()["incomplete"] == []
    assert preview.json()["rows"] == 2
    assert preview.json()["read_as"][0]["discharge_weight_kg"] == 83.91 and preview.json()["dropped"] == ["NOTES"]

    r = admin.post("/api/ingest/upload", files=files(encounters=ENCOUNTERS, diagnoses=DIAGNOSES))
    assert r.status_code == 202, r.text
    batch = r.json()["encounters"][0]
    assert batch["accepted"] == 2 and not batch["rejected"], batch
    stay = db[ingest.STAYS].find_one({"encounter_key": "V1"})
    assert stay["patient_id"] == "CGH-M1" and stay["encounter"]["age_years"] == 75
    assert stay["encounter"]["admission_type"] == "EMERGENCY" and stay["discharge_vitals"]["weight_kg"] == 83.91
    assert stay["extras"] == {"ward": "Cardiology", "frailty": "4"} and stay["diagnoses"][0]["icd_code"] == "I5023"
    recorded = db[ingest.BATCHES].find_one({"_id": batch["batch_id"]})
    assert (recorded["source"], recorded["by"]) == ("upload", "admin@a.test")

    worker.run(db, once=True)
    row = db["patient_worklist"].find_one({"patient_id": "CGH-M1"})
    assert row and row["scored_for"] == scoring_service.deployed_fingerprint(db, "hosp-a")
    activity = admin.get("/api/ingest/batches").json()["batches"]
    assert activity[0]["source"] == "upload" and activity[0]["status"] == "done" and activity[0]["scored"] == 2


def test_history_is_stored_for_training_but_kept_off_the_worklist(admin, world):
    db = world["db"]
    admin.put("/api/ingest/profile", json=PROFILE)
    r = admin.post("/api/ingest/upload", files=files(encounters=ENCOUNTERS, diagnoses=DIAGNOSES),
                   data={"history_only": "true"})
    assert r.json()["encounters"][0]["history_only"] is True
    assert db[ingest.JOBS].count_documents({}) == 0 and db[ingest.STAYS].count_documents({"history": True}) == 2
    worker.run(db, once=True)
    assert not db["patient_worklist"].find_one({"patient_id": "CGH-M1"})

    csv = admin.get("/api/ingest/export/discharges.csv")
    assert csv.status_code == 200 and csv.headers["content-disposition"].endswith('_discharges_trainable.csv"')
    frame = pd.read_csv(io.StringIO(csv.text))
    assert len(frame) == 2 and {"x_ward", "x_frailty", "readmit_30d", "patient_key"} <= set(frame.columns)
    assert sorted(frame["x_frailty"]) == [2, 4]                              # numbers stay numbers
    assert db["ingest_exports"].find_one({"hospital_id": "hosp-a"})["by"] == "admin@a.test"


# ------------------------------------------------------------- re-scoring
def test_patients_scored_before_a_new_deployment_are_re_scored(main, world, token, admin, monkeypatch):
    db = world["db"]
    machine(main, token).post("/ingest/v1/encounters", json={"bundles": [bundle()]})
    worker.run(db, once=True)
    assert scoring_service.due_for_rescore(db, "hosp-a") == []
    assert admin.post("/api/ingest/rescore").json() == {"queued": 0}

    monkeypatch.setattr(scoring_service, "deployed_fingerprint", lambda db, hospital: "m-new|builtin-weekly-rules")
    assert scoring_service.due_for_rescore(db, "hosp-a") == ["E-1"]
    assert admin.get("/api/ingest/scoring").json()["due"] == 1
    assert as_user(main, world, "admin@b.test").get("/api/ingest/scoring").json()["due"] == 0
    queued = admin.post("/api/ingest/rescore").json()
    assert queued["queued"] == 1
    worker.run(db, once=True)
    assert db["patient_worklist"].find_one({"patient_id": PID})["scored_for"] == "m-new|builtin-weekly-rules"
    status = admin.get("/api/ingest/scoring").json()
    assert status["due"] == 0 and status["last_rescore"]["status"] == "done"
    assert status["last_rescore"]["by"] == "admin@a.test"


def test_a_deployed_model_that_reads_a_hospital_column_takes_over_on_re_score(main, studio, token, tmp_path):
    db = studio["db"]
    frail = bundle()
    frail["extras"] = {"frailty": "8"}
    machine(main, token).post("/ingest/v1/encounters", json={"bundles": [frail]})
    worker.run(db, once=True)
    assert db["patient_worklist"].find_one({"patient_id": PID})["model_version"] == scoring_service.BUILTIN_VERSION

    rng = np.random.default_rng(5)
    n = 600
    frame = pd.DataFrame({"x_frailty": rng.integers(0, 10, n), "anchor_age": rng.integers(30, 90, n)})
    frame["outcome"] = (rng.random(n) < 1 / (1 + np.exp(-(0.6 * (frame["x_frailty"] - 5))))).astype(int)
    config, blob = _train(tmp_path, "frailty", frame, ["x_frailty", "anchor_age"])
    _deploy(db, "m-frail", "discharge", config, blob, {"high_score_threshold": 40.0, "low_score_threshold": 20.0})

    assert scoring_service.due_for_rescore(db, "hosp-a") == ["E-1"]
    ingest.queue_rescore(db, "hosp-a", "test")
    worker.run(db, once=True)
    row = db["patient_worklist"].find_one({"patient_id": PID})
    assert row["model_version"] == "studio:m-frail"
    assert "frailty" in row["driver_1"].lower()          # the hospital's own column explains the score
    assert scoring_service.due_for_rescore(db, "hosp-a") == []


# ------------------------------------------------------------------ clock
def test_the_nightly_time_is_the_hospitals_local_time(monkeypatch):
    monkeypatch.setenv("NIGHTLY_TZ", "Asia/Karachi")                        # UTC+5
    assert scheduler.next_run("02:00", datetime(2026, 10, 19, 20, 0)) == datetime(2026, 10, 19, 21, 0)
    assert scheduler.next_run("02:00", datetime(2026, 10, 19, 21, 30)) == datetime(2026, 10, 20, 21, 0)


def test_a_blank_or_wrong_nightly_setting_falls_back_to_the_default(monkeypatch):
    # A variable left empty on the host must not take the Data page's scoring card down.
    for value in ("", "  ", "2am", "25:00"):
        monkeypatch.setenv("NIGHTLY_AT", value)
        assert scheduler.nightly_at() == "02:00"
    monkeypatch.setenv("NIGHTLY_TZ", "")
    assert str(scheduler.zone()) == "UTC"


def test_a_due_run_starts_once_however_many_workers_look(world, monkeypatch):
    db = world["db"]
    past = datetime(2026, 1, 1)
    db[scheduler.SCHEDULE].insert_one({"_id": scheduler.NIGHTLY, "next_run_at": past})
    db["patient_worklist"].update_one({}, {"$set": {"source": "ingest", "hospital_id": "hosp-a",
                                                    "encounter_key": "E-9", "scored_for": "old"}})
    db[scheduler.CONNECTIONS].insert_one({"_id": "dc_1", "hospital_id": "hosp-a", "enabled": True,
                                          "next_run_at": past, "schedule_at": "01:30"})
    first = scheduler.tick(db)
    assert first == {"rescored": {"hosp-a": 1}, "pulls": ["dc_1"]}
    assert scheduler.tick(db) == {"rescored": {}, "pulls": []}
    assert db[ingest.JOBS].count_documents({"kind": "pull"}) == 1
    assert db[scheduler.CONNECTIONS].find_one({"_id": "dc_1"})["next_run_at"] > datetime.utcnow()


# ------------------------------------------------------------------- pull
@pytest.fixture
def hospital_db(tmp_path):
    path = tmp_path / "his.sqlite"
    con = sqlite3.connect(path)
    con.executescript("""
        CREATE TABLE visits (VISIT_ID TEXT, MRN TEXT, ADM_DT TEXT, DISCH_DT TEXT, AGE INTEGER,
                             ADM_TYPE TEXT, DISCH_DISP TEXT, GENDER TEXT, UPDATED TEXT, WARD TEXT);
        INSERT INTO visits VALUES ('V1','M1','2026-04-01 08:00','2026-04-05 12:00',71,'EMERGENCY','HOME','F',
                                   '2026-04-05 13:00','Cardiology');
        INSERT INTO visits VALUES ('V0','M0','2025-01-01 08:00','2025-01-03 12:00',60,'EMERGENCY','HOME','M',
                                   '2025-01-03 13:00','General');
        CREATE TABLE dx (VISIT_ID TEXT, CODE TEXT, SEQ INTEGER, VER INTEGER);
        INSERT INTO dx VALUES ('V1','I5023',1,10);
    """)
    con.commit()
    con.close()
    return f"sqlite:///{path}"


QUERIES = {"encounters": "SELECT * FROM visits WHERE UPDATED >= :since",
           "diagnoses": "SELECT VISIT_ID, CODE AS icd_code, SEQ AS sequence, VER AS icd_version FROM dx"}


def test_a_pull_reads_only_what_changed_and_only_reads(hospital_db):
    rows = pull.fetch(hospital_db, QUERIES, datetime(2026, 1, 1))
    assert [r["VISIT_ID"] for r in rows["encounters"]] == ["V1"] and rows["encounters"][0]["AGE"] == "71"
    assert rows["diagnoses"] == [{"VISIT_ID": "V1", "icd_code": "I5023", "sequence": "1", "icd_version": "10"}]
    assert pull.check_query("DELETE FROM visits") and pull.check_query("SELECT 1; DROP TABLE dx")
    assert pull.check_query("  with x as (select 1) select * from x;") is None
    with pytest.raises(pull.PullError, match="no such table"):
        pull.fetch(hospital_db, {"labs": "SELECT * FROM labs"}, datetime(2026, 1, 1))
    report = pull.probe(hospital_db, {**QUERIES, "labs": "SELECT * FROM labs"}, datetime(2026, 1, 1))
    assert report["tables"]["encounters"]["ok"] and "VISIT_ID" in report["tables"]["encounters"]["columns"]
    assert not report["tables"]["labs"]["ok"] and report["tables"]["diagnoses"]["ok"]


def test_a_connection_is_set_up_tested_and_pulled_by_the_worker(main, world, admin, hospital_db, monkeypatch):
    db = world["db"]
    body = {"name": "HIS", "dialect": "mssql", "host": "his-db", "database": "HIS", "username": "ro",
            "password": "s3cret", "queries": QUERIES, "first_since": "2026-01-01"}
    monkeypatch.delenv("INGEST_SECRET_KEY", raising=False)
    assert admin.post("/api/ingest/connections", json=body).status_code == 503        # no key, no stored password
    monkeypatch.setenv("INGEST_SECRET_KEY", "test-key")
    assert admin.post("/api/ingest/connections", json={**body, "queries": {"labs": "DROP TABLE x"}}).status_code == 422

    seen = {}

    def fake_url(doc, password):
        seen["password"] = password
        return hospital_db
    monkeypatch.setattr(ingest_connections, "_url", fake_url)

    made = admin.post("/api/ingest/connections", json=body)
    assert made.status_code == 201 and "password" not in made.json() and made.json()["has_password"]
    conn_id = made.json()["connection_id"]
    assert "s3cret" not in str(db[ingest_connections.CONNECTIONS].find_one({"_id": conn_id}))
    assert as_user(main, world, "admin@b.test").post(f"/api/ingest/connections/{conn_id}/run").status_code == 404
    assert as_user(main, world, "admin@b.test").get("/api/ingest/connections").json()["connections"] == []

    tested = admin.post("/api/ingest/connections/test", json={"connection_id": conn_id}).json()
    assert tested["connected"] and seen["password"] == "s3cret"
    assert "DISCH_DISP" in tested["tables"]["encounters"]["mapping"]["mapping"]

    admin.put("/api/ingest/profile", json={"patient_prefix": "CGH", "extras": {"encounters": ["WARD"]},
                                          "columns": {"encounters": {"DISCH_DISP": "discharge_disposition"}}})
    assert admin.post(f"/api/ingest/connections/{conn_id}/run").json()["status"] == "queued"
    worker.run(db, once=True)

    conn = db[ingest_connections.CONNECTIONS].find_one({"_id": conn_id})
    assert conn["last_run"]["ok"] and conn["last_run"]["rows"] == {"encounters": 1, "diagnoses": 1}
    assert conn["watermark"] and conn["last_run"]["since"] == datetime(2026, 1, 1)
    stay = db[ingest.STAYS].find_one({"patient_id": "CGH-M1"})
    assert stay["extras"] == {"ward": "Cardiology"} and not db[ingest.STAYS].find_one({"patient_id": "CGH-M0"})
    assert db[ingest.BATCHES].find_one({"client_id": conn_id})["source"] == "pull"
    assert db["patient_worklist"].find_one({"patient_id": "CGH-M1"})

    # The next night reads from the last pull on: nothing new.
    admin.post(f"/api/ingest/connections/{conn_id}/run")
    worker.run(db, once=True)
    assert db[ingest_connections.CONNECTIONS].find_one({"_id": conn_id})["last_run"]["rows"]["encounters"] == 0


def test_the_agent_sends_a_large_night_in_whole_parts():
    rows = {"encounters": [{"encounter_key": f"E{i}", "patient_key": f"P{i % 7}"} for i in range(1200)],
            "patients": [{"patient_key": f"P{i}"} for i in range(7)],
            "diagnoses": [{"encounter_key": f"E{i}", "code": "I10"} for i in range(1200)],
            "weeks": [{"encounter_key": "E1"}]}
    parts = agent.parts(rows)
    assert [len(p["encounters"]) for p in parts] == [500, 500, 200]
    for p in parts:
        keys = {r["encounter_key"] for r in p["encounters"]}
        assert {r["encounter_key"] for r in p["diagnoses"]} == keys
    assert "weeks" in parts[-1] and "weeks" not in parts[0]
    body, ctype = agent._multipart(parts[2])
    assert ctype.startswith("multipart/form-data") and b'name="diagnoses"' in body

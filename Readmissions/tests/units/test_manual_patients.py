"""Scoring a patient who is typed in or uploaded, and adding them to the list.

Calculating is always on for the roles that score patients and writes nothing;
adding is switched off by default and is for admins only. The model is faked
here (a fixed score and drivers), except in the one smoke test.
"""
import os

import pytest

from api import manual_patients, mimic_scoring
from tests.units.test_isolation import LATEST, as_user, main, world  # noqa: F401

CATEGORIES = {"gender": ["F", "M"], "race": ["WHITE", "BLACK"], "insurance": ["Medicare", "Other"],
              "marital_status": ["MARRIED", "SINGLE"], "admission_type": ["EMERGENCY", "ELECTIVE"]}
DRIVERS = ["Prior admissions: 3 (raises risk)", "Age: 71 (raises risk)", "Stay: 5 days (lowers risk)"]
FORM = {"discharge_date": "2026-04-10", "admit_date": "2026-04-05", "primary_icd_code": "I50.9",
        "primary_diagnosis": "Heart failure", "anchor_age": 71, "gender": "F", "los_days": 5,
        "n_prior_adm": 3, "weight_kg": 82, "spo2": 94, "sbp": 118, "heart_rate": 76,
        "external_ref": "EXT-7", "congestive_heart_failure": True}
FUTURE = "2999-01-01"


@pytest.fixture(autouse=True)
def fake_model(monkeypatch):
    seen = []

    def fake(inputs):
        seen.append(dict(inputs))
        return {"risk_score": 44.4, "risk_band": "High", "drivers": [],
                "driver_1_raw": DRIVERS[0], "driver_2_raw": DRIVERS[1], "driver_3_raw": DRIVERS[2]}
    monkeypatch.setattr(mimic_scoring, "score_manual_entry", fake)
    monkeypatch.setattr(mimic_scoring, "manual_entry_schema", lambda: {"categorical": CATEGORIES})
    return seen


def snapshot(db):
    return {name: sorted(map(str, db[name].find({}, {"_id": 0}))) for name in db.list_collection_names()}


def csv_file(*lines):
    return {"file": ("patients.csv", "\n".join(lines).encode(), "text/csv")}


HEADER = "external_ref,discharge_date,primary_icd_code,primary_diagnosis,anchor_age,gender"


def test_calculating_is_always_on(main, world, monkeypatch):  # noqa: F811
    monkeypatch.setattr(main, "MANUAL_ENTRY_ENABLED", False)
    client = as_user(main, world, "doc.a@a.test")
    assert client.get("/api/manual-entry/template").status_code == 200
    scored = client.post("/api/manual-entry/score", json={"records": [FORM]})
    assert scored.status_code == 200
    row = scored.json()["rows"][0]
    assert (row["risk_score"], row["risk_band"], row["errors"]) == (44.4, "High", [])
    assert row["drivers"][0]["label"] == "Prior admissions"
    parsed = client.post("/api/manual-entry/parse",
                         files=csv_file(HEADER, "A1,2026-04-10,I50.9,Heart failure,71,F"))
    assert parsed.status_code == 200 and parsed.json()["rows"][0]["risk_score"] == 44.4


def test_adding_is_switched_off_by_default(main, world, monkeypatch):  # noqa: F811
    monkeypatch.setattr(main, "MANUAL_ENTRY_ENABLED", False)
    client = as_user(main, world, "admin@a.test")
    before = snapshot(world["db"])
    assert client.post("/api/manual-entry/add", json={"records": [FORM]}).status_code == 403
    legacy = {"patient_id": "X", "discharge_date": "2026-04-10", "risk_score": 1, "risk_band": "Low",
              "driver_1": "", "driver_2": "", "driver_3": ""}
    assert client.post("/api/patients/worklist-add", json=legacy).status_code == 403
    assert snapshot(world["db"]) == before


def test_calculating_writes_nothing(main, world):  # noqa: F811
    client, before = as_user(main, world, "admin@a.test"), snapshot(world["db"])
    client.post("/api/manual-entry/score", json={"records": [FORM, FORM]})
    client.post("/api/manual-entry/parse", files=csv_file(HEADER, "A,2026-04-10,I50.9,HF,71,F"))
    client.get("/api/manual-entry/template")
    assert snapshot(world["db"]) == before


def test_an_added_patient_behaves_like_any_other(main, world, monkeypatch):  # noqa: F811
    monkeypatch.setattr(main, "MANUAL_ENTRY_ENABLED", True)
    db = world["db"]
    r = as_user(main, world, "admin@a.test").post("/api/manual-entry/add", json={"records": [FORM]})
    assert r.status_code == 200, r.text
    added = r.json()["added"][0]
    pid = added["patient_id"]
    assert pid.startswith("MAN-") and len(pid) == 12 and (added["risk_score"], added["risk_band"]) == (44.4, "High")

    row = db["patient_worklist"].find_one({"patient_id": pid})
    assert (row["batch_date"], row["source"], row["hospital_id"]) == (LATEST, "manual", "hosp-a")
    assert (row["clinical_group"], row["primary_icd_code"], row["external_ref"]) == ("heart_failure", "I509", "EXT-7")
    assert row["risk_score"] == 44.4 and row["raw_inputs"]["n_prior_adm"] == 3
    assert "weight_kg" not in row["raw_inputs"]
    care = db["care_actions"].find_one({"patient_id": pid})
    assert care["hospital_id"] == "hosp-a" and not care.get("assigned_doctor_id")
    week0 = db["weekly_monitoring"].find_one({"patient_id": pid})
    assert week0["week_number"] == 0 and week0["source"] == "manual"
    assert week0["discharge_baseline"] == {"dry_weight_kg": 82.0, "baseline_spo2": 94.0, "baseline_sbp": 118.0,
                                           "baseline_hr": 76.0, "source": "recorded", "group": "heart_failure"}
    assert db["risk_registry"].count_documents({"patient_id": pid}) == 1
    assert db["executive_summary"].find_one({"batch_date": LATEST})["total_patients"] == 7

    listed = {p["id"] for p in as_user(main, world, "cm@a.test").get("/api/patients", params={"limit": 100}).json()["data"]}
    assert pid in listed
    assert as_user(main, world, "admin@b.test").get(f"/api/patients/{pid}").status_code == 404
    trend = as_user(main, world, "cm@a.test").get(f"/api/patients/{pid}/trend").json()
    assert len(trend["weeks"]) == 1 and trend["discharge_baseline"]["source"] == "recorded"


def test_adding_ignores_browser_scores(main, world, monkeypatch):  # noqa: F811
    monkeypatch.setattr(main, "MANUAL_ENTRY_ENABLED", True)
    legacy = {"patient_id": "MIMIC-1101", "discharge_date": "2026-04-10", "risk_score": 1.0, "risk_band": "Low",
              "driver_1": "x", "driver_2": "y", "driver_3": "z", "primary_diagnosis": "Heart failure",
              "raw_inputs": {"anchor_age": 71}}
    r = as_user(main, world, "admin@a.test").post("/api/patients/worklist-add", json=legacy)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["patient_id"] != "MIMIC-1101" and (body["risk_score"], body["risk_band"]) == (44.4, "High")
    stored = world["db"]["patient_worklist"].find_one({"patient_id": body["patient_id"]})
    assert stored["risk_score"] == 44.4 and stored["driver_1"] == DRIVERS[0]
    assert world["db"]["patient_worklist"].find_one({"patient_id": "MIMIC-1101", "batch_date": LATEST})["risk_score"] == 40


def test_a_superadmin_must_pick_a_hospital(main, world, monkeypatch):  # noqa: F811
    monkeypatch.setattr(main, "MANUAL_ENTRY_ENABLED", True)
    client = as_user(main, world, "ops@team.test")
    before = snapshot(world["db"])
    assert client.post("/api/manual-entry/add", json={"records": [FORM]}).status_code == 400
    assert snapshot(world["db"]) == before
    r = client.post("/api/manual-entry/add", json={"records": [FORM]}, headers={"X-Hospital-Id": "hosp-b"})
    assert r.status_code == 200
    assert world["db"]["care_actions"].find_one({"patient_id": r.json()["added"][0]["patient_id"]})["hospital_id"] == "hosp-b"


def test_doctors_calculate_but_do_not_add(main, world, monkeypatch):  # noqa: F811
    monkeypatch.setattr(main, "MANUAL_ENTRY_ENABLED", True)
    for email in ("doc.a@a.test", "nurse@a.test", "cm@a.test"):
        client = as_user(main, world, email)
        assert client.post("/api/manual-entry/score", json={"records": [FORM]}).status_code == 200, email
        assert client.post("/api/manual-entry/add", json={"records": [FORM]}).status_code == 403, email


def test_csv_rows_are_checked(main, world):  # noqa: F811
    client = as_user(main, world, "admin@a.test")
    r = client.post("/api/manual-entry/parse", files=csv_file(
        HEADER + ",colour",
        "ok,2026-04-10,I50.9,HF,71,F,red",
        "badcat,2026-04-10,I50.9,HF,71,Z,red",
        "badnum,2026-04-10,I50.9,HF,old,F,red",
        f"future,{FUTURE},I50.9,HF,71,F,red",
        "nodx,2026-04-10,,,71,F,red"))
    assert r.status_code == 200
    body = r.json()
    assert body["unknown_columns"] == ["colour"]
    assert body["rows"][0]["errors"] == [] and body["rows"][0]["risk_score"] == 44.4
    assert "gender must be one of" in body["rows"][1]["errors"][0] and "risk_score" not in body["rows"][1]
    assert body["rows"][2]["errors"] == ["anchor_age must be a number"]
    assert "cannot be in the future" in body["rows"][3]["errors"][0]
    assert body["rows"][4]["errors"] == [] and manual_patients.add_errors(body["rows"][4]["record"])
    assert len(body["rows"]) == 5
    too_many = "\n".join([HEADER] + ["a,2026-04-10,I50.9,HF,71,F"] * 501)
    assert client.post("/api/manual-entry/parse", files=csv_file(too_many)).status_code == 400
    assert client.post("/api/manual-entry/parse", files=csv_file("just one line")).status_code == 400
    assert client.post("/api/manual-entry/parse",
                       files={"file": ("x.csv", b"\xff\xfe\x00", "text/csv")}).status_code == 400


def test_the_template_matches_the_form_fields(main, world):  # noqa: F811
    template = as_user(main, world, "doc.a@a.test").get("/api/manual-entry/template").json()
    names = [c["name"] for c in template["columns"]]
    schema = {"categorical": list(CATEGORIES), "numeric": mimic_scoring.ENTERABLE_NUMERIC,
              "flags": mimic_scoring.ENTERABLE_FLAGS, "comorbidities": mimic_scoring.CHARLSON_FLAGS,
              "labs": mimic_scoring.ENTERABLE_LABS}
    form = [n for group in schema.values() for n in group]
    assert names == [n for n, _, _ in manual_patients.IDENTITY_FIELDS] + form
    assert len(names) == len(set(names)) and set(template["example"]) <= set(names)
    assert next(c for c in template["columns"] if c["name"] == "gender")["options"] == ["F", "M"]
    fields = set(main.ManualPatientRecord.model_fields)
    assert set(names) <= fields


def test_one_bad_row_adds_nothing(main, world, monkeypatch):  # noqa: F811
    monkeypatch.setattr(main, "MANUAL_ENTRY_ENABLED", True)
    before = snapshot(world["db"])
    bad = {**FORM, "discharge_date": FUTURE}
    r = as_user(main, world, "admin@a.test").post("/api/manual-entry/add", json={"records": [FORM, bad, FORM]})
    assert r.status_code == 422
    assert [x["row"] for x in r.json()["detail"]["rows"]] == [2]
    missing = {k: v for k, v in FORM.items() if k not in ("primary_icd_code", "primary_diagnosis")}
    assert as_user(main, world, "admin@a.test").post(
        "/api/manual-entry/add", json={"records": [missing]}).status_code == 422
    assert snapshot(world["db"]) == before


def test_a_model_failure_on_a_later_record_writes_nothing(main, world, monkeypatch):  # noqa: F811
    monkeypatch.setattr(main, "MANUAL_ENTRY_ENABLED", True)
    before = snapshot(world["db"])
    calls = []

    def flaky(inputs):
        calls.append(1)
        if len(calls) == 2:
            raise RuntimeError("model down")
        return {"risk_score": 44.4, "risk_band": "High", "drivers": [], "driver_1_raw": DRIVERS[0],
                "driver_2_raw": DRIVERS[1], "driver_3_raw": DRIVERS[2]}
    monkeypatch.setattr(mimic_scoring, "score_manual_entry", flaky)
    r = as_user(main, world, "admin@a.test").post("/api/manual-entry/add", json={"records": [FORM, FORM, FORM]})
    assert r.status_code == 503
    assert snapshot(world["db"]) == before


@pytest.mark.parametrize("field", ["n_prior_adm", "n_procedures", "n_diagnoses", "n_drug_orders",
                                   "n_distinct_drugs", "ed_hours", "days_since_prev"])
def test_negative_counts_are_rejected(field):
    assert manual_patients.check({**FORM, field: -1}, CATEGORIES) == [f"{field} cannot be negative"]
    assert manual_patients.check({**FORM, field: 0}, CATEGORIES) == []


@pytest.mark.parametrize("email", ["claims@acme.test", "me@patient.test", "rx@bay.test", "carer@home.test"])
def test_insurers_and_patients_cannot_score(main, world, email):  # noqa: F811
    if email not in world["tokens"]:
        pytest.skip("this account does not exist in this deployment")
    client = as_user(main, world, email)
    assert client.get("/api/manual-entry/template").status_code == 403
    assert client.post("/api/manual-entry/score", json={"records": [FORM]}).status_code == 403
    assert client.post("/api/manual-entry/parse", files=csv_file(HEADER, "a,2026-04-10,I50.9,HF,71,F")).status_code == 403


@pytest.mark.skipif(not os.path.exists(mimic_scoring.MODEL_PATH), reason="model artefacts not present")
def test_the_real_model_scores_a_typed_patient(main, world, monkeypatch):  # noqa: F811
    monkeypatch.undo()
    monkeypatch.setattr(main, "db", world["db"])
    result = manual_patients.score({"anchor_age": 71, "gender": "F", "los_days": 5, "n_prior_adm": 3})
    assert 0 <= result["risk_score"] <= 100 and result["risk_band"] in ("Low", "Medium", "High")
    assert result["drivers"]

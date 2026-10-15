"""The ingest API (api/ingest.py): credentials, per-record checks, safe
resends, the nightly file drop, code mappings and the admin side.

Same two hospitals and accounts as test_isolation.py.
"""
import io

import pytest
from fastapi.testclient import TestClient

from api import auth, ingest
from tests.units.test_isolation import as_user, main, world  # noqa: F401


def bundle(enc="E-1", pat="P-1", admit="2026-04-01T08:00", disch="2026-04-05T12:00", **over):
    b = {"stage": "final",
         "encounter": {"encounter_key": enc, "patient_key": pat, "admit_datetime": admit,
                       "discharge_datetime": disch, "age_years": 71, "admission_type": "EMERGENCY",
                       "discharge_disposition": "HOME", "died_in_hospital": False,
                       "payer_class": "MEDICARE"},
         "patient": {"sex": "F"},
         "diagnoses": [{"icd_code": "I5023", "icd_version": 10, "sequence": 1,
                        "description": "Acute on chronic systolic heart failure"}],
         "labs": [{"loinc_code": "2160-0", "test_name": "Creatinine", "value": 1.8, "unit": "mg/dL",
                   "result_datetime": "2026-04-04T06:10", "abnormal_flag": "H"}],
         "medications": [{"drug_name": "furosemide", "order_start_datetime": "2026-04-01T09:00"}],
         "discharge_vitals": {"weight_kg": 84.2, "spo2": 95, "sbp": 132, "heart_rate": 78}}
    for key, value in over.items():
        b[key] = value
    return b


@pytest.fixture
def token(world):
    return ingest.create_client(world["db"], "hosp-a", "HA", "test")["token"]


def machine(main, token=None, **headers):
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return TestClient(main.app, headers=headers, raise_server_exceptions=False)


# ------------------------------------------------------------ credentials
def test_only_a_valid_credential_gets_in(main, world, token):
    assert machine(main).get("/ingest/v1/ping").status_code == 401
    assert machine(main, token[:-3] + "xyz").get("/ingest/v1/ping").status_code == 401
    assert machine(main, "not-a-token").get("/ingest/v1/ping").status_code == 401
    assert machine(main, token).get("/ingest/v1/ping").json()["hospital_id"] == "hosp-a"
    client_id = token.split(".")[0].removeprefix("ing_")
    world["db"][ingest.CLIENTS].update_one({"_id": client_id}, {"$set": {"revoked": True}})
    assert machine(main, token).get("/ingest/v1/ping").status_code == 401


def test_the_secret_is_stored_hashed(world, token):
    stored = world["db"][ingest.CLIENTS].find_one()
    assert token.split(".", 1)[1] not in str(stored)


def test_the_hospital_comes_from_the_credential(main, world, token):
    sneaky = bundle()
    sneaky["hospital_id"] = "hosp-b"                    # not a field: refused, not obeyed
    r = machine(main, token).post("/ingest/v1/encounters", json={"bundles": [sneaky, bundle("E-2")]})
    assert r.status_code == 202
    assert r.json()["accepted"] == 1 and "hospital_id" in r.json()["rejected"][0]["errors"][0]
    stays = list(world["db"][ingest.STAYS].find())
    assert [s["hospital_id"] for s in stays] == ["hosp-a"] and stays[0]["patient_id"] == "HA-P-1"


def test_the_browser_key_is_not_needed_and_does_not_get_in(main, world, token, monkeypatch):
    monkeypatch.setattr(main, "API_KEY", "frontend-key")
    assert machine(main, token).get("/ingest/v1/ping").status_code == 200
    assert machine(main, **{"X-API-Key": "frontend-key"}).get("/ingest/v1/ping").status_code == 401
    # The admin side is /api/: it still needs the key and a signed-in person.
    assert machine(main).get("/api/ingest/clients").status_code == 401


# --------------------------------------------------------- record checks
def test_a_bad_record_is_returned_and_the_rest_go_ahead(main, world, token):
    bad_date = bundle("E-3", disch="2026-03-01T00:00")
    bad_code = bundle("E-4")
    bad_code["encounter"]["admission_type"] = "WALK-IN"
    odd_lab = bundle("E-5")
    odd_lab["labs"][0]["unit"] = "furlongs"
    r = machine(main, token).post("/ingest/v1/encounters",
                                  json={"bundles": [bundle("E-1"), bad_date, bad_code, odd_lab]})
    body = r.json()
    assert body["accepted"] == 2 and [x["encounter_key"] for x in body["rejected"]] == ["E-3", "E-4"]
    assert "before admit_datetime" in body["rejected"][0]["errors"][0]
    assert "WALK-IN" in body["rejected"][1]["errors"][0]
    assert body["warnings"][0]["encounter_key"] == "E-5" and "furlongs" in body["warnings"][0]["warning"]
    assert world["db"][ingest.JOBS].count_documents({"status": "queued"}) == 1


def test_a_resend_replaces_the_stay(main, world, token):
    client = machine(main, token)
    client.post("/ingest/v1/encounters", json={"bundles": [bundle(stage="provisional")]})
    client.post("/ingest/v1/encounters", json={"bundles": [bundle(stage="final")]})
    stays = list(world["db"][ingest.STAYS].find())
    assert len(stays) == 1 and stays[0]["stage"] == "final"


def test_the_same_idempotency_key_is_one_batch(main, world, token):
    client = machine(main, token, **{"Idempotency-Key": "nightly-2026-04-30"})
    first = client.post("/ingest/v1/encounters", json={"bundles": [bundle()]}).json()
    again = client.post("/ingest/v1/encounters", json={"bundles": [bundle()]}).json()
    assert first["batch_id"] == again["batch_id"]
    assert world["db"][ingest.JOBS].count_documents({}) == 1
    assert world["db"][ingest.BATCHES].count_documents({}) == 1


def test_a_batch_reports_its_status(main, world, token):
    client = machine(main, token)
    batch = client.post("/ingest/v1/encounters", json={"bundles": [bundle()]}).json()["batch_id"]
    status = client.get(f"/ingest/v1/batches/{batch}").json()
    assert (status["status"], status["accepted"], status["scored"]) == ("queued", 1, 0)
    other = ingest.create_client(world["db"], "hosp-b", "HB", "test")["token"]
    assert machine(main, other).get(f"/ingest/v1/batches/{batch}").status_code == 404


# ----------------------------------------------------------------- weeks
def test_weekly_check_ins_follow_a_stored_discharge(main, world, token):
    client = machine(main, token)
    client.post("/ingest/v1/encounters", json={"bundles": [bundle()]})
    week = {"patient_key": "P-1", "encounter_key": "E-1", "week_date": "2026-04-12",
            "source": "nurse_call", "readings": {"weight_kg": 86.9, "refill_status": "late"}}
    r = client.post("/ingest/v1/weeks", json={"weeks": [
        week, {**week, "encounter_key": "E-9"}, {**week, "week_date": "2026-03-30"},
        {**week, "readings": {"mood": "low"}}]}).json()
    assert r["accepted"] == 1
    reasons = [x["errors"][0] for x in r["rejected"]]
    assert "Send the discharge first" in reasons[0] and "before the discharge" in reasons[1]
    assert "mood" in reasons[2]
    stored = world["db"][ingest.WEEKS].find_one()
    assert stored["week_number"] == 1 and stored["readings"] == {"weight_kg": 86.9, "refill_status": "late"}
    # A second check-in in the same week adds to it rather than replacing it.
    client.post("/ingest/v1/weeks", json={"weeks": [{**week, "readings": {"sbp": 151}}]})
    assert world["db"][ingest.WEEKS].find_one()["readings"]["sbp"] == 151
    assert world["db"][ingest.WEEKS].count_documents({}) == 1


# ------------------------------------------------------------ file drop
def test_the_nightly_files_make_the_same_stays(main, world, token):
    encounters = ("encounter_key,patient_key,admit_datetime,discharge_datetime,age_years,admission_type,"
                  "discharge_disposition,died_in_hospital,payer_class,discharge_weight_kg\n"
                  "E-1,P-1,2026-04-01 08:00,2026-04-05 12:00,71,EMERGENCY,HOME,false,MEDICARE,84.2\n")
    patients = "patient_key,sex\nP-1,F\n"
    diagnoses = "encounter_key,icd_code,icd_version,sequence\nE-1,I5023,10,1\n"
    labs = ("encounter_key,test_name,loinc_code,value,unit,result_datetime,abnormal_flag\n"
            "E-1,Creatinine,2160-0,1.8,mg/dL,2026-04-04 06:10,H\n")
    weeks = "patient_key,encounter_key,week_date,source,weight_kg,sbp\nP-1,E-1,2026-04-12,device,86.9,149\n"
    files = {name: (f"{name}.csv", io.BytesIO(text.encode()), "text/csv") for name, text in
             (("encounters", encounters), ("patients", patients), ("diagnoses", diagnoses),
              ("labs", labs), ("weeks", weeks))}
    r = machine(main, token).post("/ingest/v1/files", files=files)
    assert r.status_code == 202, r.text
    assert r.json()["encounters"]["accepted"] == 1 and r.json()["weeks"]["accepted"] == 1
    stay = world["db"][ingest.STAYS].find_one()
    assert stay["encounter"]["admission_type"] == "EMERGENCY"
    assert stay["labs"][0]["value"] == 1.8 and stay["discharge_vitals"]["weight_kg"] == 84.2


# ---------------------------------------------------------- admin side
def test_a_hospital_maps_its_own_codes(main, world, token):
    admin = as_user(main, world, "admin@a.test")
    assert admin.put("/api/ingest/mapping", json={"admission_type": {"ER": "WHATEVER"}}).status_code == 422
    assert admin.put("/api/ingest/mapping", json={"admission_type": {"ER": "emergency"}}).status_code == 200
    coded = bundle()
    coded["encounter"]["admission_type"] = "er"
    assert machine(main, token).post("/ingest/v1/encounters", json={"bundles": [coded]}).json()["accepted"] == 1
    assert world["db"][ingest.STAYS].find_one()["encounter"]["admission_type"] == "EMERGENCY"
    assert as_user(main, world, "cm@a.test").put("/api/ingest/mapping", json={}).status_code == 403


def test_admins_manage_their_own_hospitals_credentials(main, world):
    world["db"].client[auth.IDENTITY_DB]["hospitals"].insert_many([{"_id": "hosp-a"}, {"_id": "hosp-b"}])
    admin_a = as_user(main, world, "admin@a.test")
    made = admin_a.post("/api/ingest/clients", json={"patient_prefix": "CGH"}).json()
    assert made["hospital_id"] == "hosp-a" and made["token"].startswith("ing_")
    listed = admin_a.get("/api/ingest/clients").json()["clients"]
    assert [c["client_id"] for c in listed] == [made["client_id"]] and "token" not in listed[0]
    assert as_user(main, world, "admin@b.test").delete(
        f"/api/ingest/clients/{made['client_id']}").status_code == 404
    assert admin_a.delete(f"/api/ingest/clients/{made['client_id']}").status_code == 200
    assert machine(main, made["token"]).get("/ingest/v1/ping").status_code == 401
    assert admin_a.post("/api/ingest/clients", json={"patient_prefix": "no good"}).status_code == 422
    for email in ("cm@a.test", "doc.a@a.test", "claims@acme.test"):
        assert as_user(main, world, email).get("/api/ingest/clients").status_code == 403


def test_demographics_may_come_on_the_encounter(main, world, token):
    """The build plan's example sends sex on the encounter, with no patient block."""
    flat = bundle()
    del flat["patient"]
    flat["encounter"].update(sex="F", marital_status="MARRIED")
    both = bundle("E-2")
    both["encounter"]["sex"] = "M"                       # patient says F: the patient block wins
    r = machine(main, token).post("/ingest/v1/encounters", json={"bundles": [flat, both]}).json()
    assert r["accepted"] == 2, r["rejected"]
    stays = {s["encounter_key"]: s for s in world["db"][ingest.STAYS].find()}
    assert stays["E-1"]["patient"] == {"sex": "F", "marital_status": "MARRIED", "race_ethnicity": None,
                                       "death_date": None}
    assert "sex" not in stays["E-1"]["encounter"] and stays["E-2"]["patient"]["sex"] == "F"
    missing = bundle("E-3")
    del missing["patient"]
    r = machine(main, token).post("/ingest/v1/encounters", json={"bundles": [missing]}).json()
    assert r["rejected"][0]["errors"] == ["patient: Field required"]


def test_a_credential_is_only_made_for_a_real_hospital(main, world):
    world["db"].client[auth.IDENTITY_DB]["hospitals"].insert_one({"_id": "hosp-a"})
    ops = as_user(main, world, "ops@team.test")
    assert ops.post("/api/ingest/clients", json={"patient_prefix": "CGH"}).status_code == 422
    assert ops.post("/api/ingest/clients",
                    json={"patient_prefix": "CGH", "hospital_id": "hosp-aa"}).status_code == 422
    made = ops.post("/api/ingest/clients", json={"patient_prefix": "CGH", "hospital_id": "hosp-a"})
    assert made.status_code == 201 and made.json()["hospital_id"] == "hosp-a"

"""Emergency access (core/emergency.py) and medication changes (core/medications.py),
over the same accounts as test_isolation.py.

Hospital A: doc@a.test looks after patients 0 and 1; patient 2 is in hospital A
but not on the doctor's list (only the nurse's). Patients 4 and 5 are hospital B.
"""
from datetime import datetime, timedelta, timezone

import pytest

from core.access import EMERGENCY_COLLECTION, EMERGENCY_REASON
from core.access_log import COLLECTION as LOG
from core.config import settings
from core.medications import COLLECTION as MEDS
from tests.test_isolation import client, world  # noqa: F401


@pytest.fixture(autouse=True)
def fresh(world):
    ident = world["store"][settings.shared_identity_db_name]
    for col in (ident[EMERGENCY_COLLECTION], ident[LOG], world["db"][MEDS]):
        col.delete_many({})
    yield
    for col in (ident[EMERGENCY_COLLECTION], ident[LOG], world["db"][MEDS]):
        col.delete_many({})


def grants(world):
    return world["store"][settings.shared_identity_db_name][EMERGENCY_COLLECTION]


def log(world, **q):
    return list(world["store"][settings.shared_identity_db_name][LOG].find(q))


def open_glass(world, email="doc@a.test", idx=2, reason="emergency_treatment",
               note="Arrived in ED short of breath"):
    return client(world, email).post("/api/emergency-access",
                                     json={"patient_idx": idx, "reason": reason, "note": note})


# ------------------------------------------------------------ lookup
def test_lookup_says_only_whether_the_number_is_in_my_hospital(world):
    doc = client(world, "doc@a.test")
    assert doc.post("/api/emergency-access/lookup", json={"patient_idx": 0}).json()["status"] == "on_your_list"
    body = doc.post("/api/emergency-access/lookup", json={"patient_idx": 2}).json()
    assert body["status"] == "available" and body["hours"] == 24
    assert set(body) == {"patient_idx", "status", "hours", "reasons"}   # nothing about the patient
    # Another hospital's patient and a number that does not exist: the same answer.
    assert doc.post("/api/emergency-access/lookup", json={"patient_idx": 4}).status_code == 404
    assert doc.post("/api/emergency-access/lookup", json={"patient_idx": 99999}).status_code == 404


@pytest.mark.parametrize("email", ["cm@a.test", "admin@a.test", "claims@acme.test", "me@patient.test"])
def test_only_doctors_and_nurses_break_the_glass(world, email):
    assert client(world, email).post("/api/emergency-access/lookup", json={"patient_idx": 2}).status_code == 403
    assert open_glass(world, email).status_code == 403


# ------------------------------------------------------------- grant
def test_before_the_glass_the_patient_is_not_found(world):
    assert client(world, "doc@a.test").get("/api/patients/2").status_code == 404


def test_breaking_the_glass_opens_the_patient_for_24_hours_and_logs_it(world):
    r = open_glass(world)
    assert r.status_code == 201, r.text
    g = r.json()
    assert g["active"] and g["reason_label"] == "Emergency treatment"
    hours = (datetime.fromisoformat(g["expires_at"]) - datetime.fromisoformat(g["granted_at"]))
    assert hours == timedelta(hours=24)

    doc = client(world, "doc@a.test")
    assert doc.get("/api/patients/2").status_code == 200
    entries = log(world, patient_id="2")
    assert len(entries) == 1 and entries[0]["reason"] == EMERGENCY_REASON
    assert entries[0]["reason_label"] == "Emergency access (break the glass)"
    assert doc.get("/api/patients/2/emergency").json()["grant"]["id"] == g["id"]
    # The doctor's own patients show no banner.
    assert doc.get("/api/patients/0/emergency").json()["grant"] is None


def test_a_nurse_can_too(world):
    # Patient 1 is the nurse's; patient 5 is hospital B. The nurse has no patient
    # in A off their list, so add one.
    world["db"].patient_access.insert_one({"patient_idx": 6, "hospital_id": "hosp-a"})
    try:
        assert open_glass(world, "nurse@a.test", idx=6).status_code == 201
        assert client(world, "nurse@a.test").get("/api/patients/6").status_code == 200
    finally:
        world["db"].patient_access.delete_one({"patient_idx": 6})


def test_a_reason_and_a_real_note_are_required(world):
    assert open_glass(world, reason="curious").status_code == 422
    assert open_glass(world, note="ER").status_code == 422
    assert client(world, "doc@a.test").get("/api/patients/2").status_code == 404


def test_never_across_hospitals(world):
    assert open_glass(world, idx=4).status_code == 404
    assert grants(world).count_documents({}) == 0


def test_not_for_a_patient_already_on_my_list(world):
    assert open_glass(world, idx=0).status_code == 409


def test_the_grant_closes_on_its_own(world):
    open_glass(world)
    grants(world).update_many({}, {"$set": {"expires_at": datetime.now(timezone.utc) - timedelta(minutes=1)}})
    assert client(world, "doc@a.test").get("/api/patients/2").status_code == 404
    assert client(world, "doc@a.test").get("/api/emergency-access/mine").json()["grants"] == []


def test_a_grant_is_personal(world):
    open_glass(world)
    # The nurse sees patient 2 as care team; a colleague doctor would not inherit the grant.
    assert grants(world).count_documents({"email": "doc@a.test"}) == 1
    assert client(world, "cm@a.test").get("/api/patients/2").status_code == 200   # their whole hospital anyway


# ------------------------------------------------- medication changes
def change(world, email="doc@a.test", idx=0, to="TIRZEPATIDE", reason="side_effects", **extra):
    return client(world, email).post(f"/api/patients/{idx}/medications",
                                     json={"to_drug": to, "reason": reason, **extra})


def test_a_doctor_records_a_change_and_the_score_is_untouched(world):
    before = world["db"].patients.find_one({"patient_idx": 0}, {"_id": 0})
    original = before["assigned_molecule"].upper()
    to = "LIRAGLUTIDE" if original != "LIRAGLUTIDE" else "SEMAGLUTIDE"
    r = change(world, to=to, note="Nausea for 3 weeks")
    assert r.status_code == 201, r.text
    assert r.json()["from_drug"] == original and r.json()["to_drug"] == to

    h = client(world, "doc@a.test").get("/api/patients/0/medications").json()
    assert h["original_drug"] == original and h["current_drug"] == to and len(h["changes"]) == 1
    assert "does not alter it" in h["score_note"]
    after = world["db"].patients.find_one({"patient_idx": 0}, {"_id": 0})
    assert after == before                                    # the dataset is never changed


def test_switching_to_the_same_drug_is_refused(world):
    current = world["db"].patients.find_one({"patient_idx": 0})["assigned_molecule"].upper()
    assert change(world, to=current).status_code == 409


def test_stopping_is_a_change_too(world):
    assert change(world, to="STOPPED", reason="cost").status_code == 201


@pytest.mark.parametrize("body,code", [
    ({"to": "ASPIRIN"}, 422), ({"reason": "whim"}, 422), ({"reason": "other"}, 422),
    ({"effective_date": "2999-01-01"}, 422), ({"effective_date": "tomorrow"}, 422)])
def test_bad_changes_are_refused(world, body, code):
    args = {"to": body.pop("to", "TIRZEPATIDE"), "reason": body.pop("reason", "side_effects")}
    assert change(world, **args, **body).status_code == code


@pytest.mark.parametrize("email,code", [("nurse@a.test", 403), ("cm@a.test", 403), ("admin@a.test", 403)])
def test_only_doctors_record_changes(world, email, code):
    assert change(world, email).status_code == code


def test_care_team_reads_the_history_and_reason_roles_need_a_reason(world):
    change(world)
    assert client(world, "nurse@a.test").get("/api/patients/0/medications").status_code == 200
    assert client(world, "cm@a.test").get("/api/patients/0/medications").status_code == 200
    assert client(world, "admin@a.test").get("/api/patients/0/medications").status_code == 403


def test_a_doctor_with_emergency_access_can_record_a_change(world):
    assert change(world, idx=2).status_code == 404
    open_glass(world)
    assert change(world, idx=2).status_code == 201
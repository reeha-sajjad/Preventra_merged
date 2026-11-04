"""Medicines with doses (api/patient_medicines.py), appointment details on a booked
follow-up (api/followups.py), and a patient's own case manager (api/hospital.assign).

Accounts from test_isolation.py: doc.a@a.test looks after A1 and A2; nurse@a.test
A1-A3; cm@a.test and admin@a.test are hospital A's; me@patient.test is A4 and
carer@home.test looks after A4; claims@acme.test insures A1."""
from datetime import timedelta

import pytest

from api import auth, followups, patient_medicines
from ingestion.scheduler import today
from scripts import assign_case_managers, load_patient_medicines
from tests.units.test_isolation import A1, A2, A4, B1, as_user, main, world  # noqa: F401

PILL = {"drug": "Furosemide", "dose": "40 mg", "route": "By mouth", "how_often": "Once a day"}


# ---------------------------------------------------------------- medicines
def test_a_doctor_keeps_the_list_and_nurses_read_it(main, world):
    doc = as_user(main, world, "doc.a@a.test")
    r = doc.post(f"/api/patients/{A1}/medicines", json=PILL)
    assert r.status_code == 201, r.text
    item = r.json()["medicines"][0]
    assert item["dose"] == "40 mg" and item["source"] == "doctor"
    r = doc.put(f"/api/patients/{A1}/medicines/{item['id']}", json={"dose": "80 mg", "how_often": "Twice a day"})
    assert r.json()["medicines"][0]["dose"] == "80 mg"
    assert doc.post(f"/api/patients/{A1}/medicines/{item['id']}/stop",
                    json={"reason": "Kidney function"}).json()["medicines"] == []

    nurse = as_user(main, world, "nurse@a.test")
    body = nurse.get(f"/api/patients/{A1}/medicines").json()
    assert body["stopped"][0]["stop_reason"] == "Kidney function" and body["can_edit"] is False
    assert [h["action"] for h in body["history"]] == ["stopped", "changed", "added"]
    assert nurse.post(f"/api/patients/{A1}/medicines", json=PILL).status_code == 403
    assert as_user(main, world, "cm@a.test").post(f"/api/patients/{A1}/medicines", json=PILL).status_code == 403
    # The model's inputs are never touched.
    assert "raw_inputs" not in str(world["db"]["patient_worklist"].find_one({"patient_id": A1}))


def test_medicines_are_the_clinical_layer(main, world):
    assert as_user(main, world, "doc.a@a.test").get(f"/api/patients/{B1}/medicines").status_code == 404
    assert as_user(main, world, "claims@acme.test").get(f"/api/patients/{A1}/medicines").status_code == 403
    assert as_user(main, world, "admin@a.test").get(f"/api/patients/{A1}/medicines").status_code == 403
    assert as_user(main, world, "doc.a@a.test").post(f"/api/patients/{A1}/medicines",
                                                     json={"drug": "", "dose": "1"}).status_code == 422


def test_the_patient_and_caretaker_see_the_list_and_whether_it_is_demo(main, world):
    planned = load_patient_medicines.plan(world["db"], demo=True)
    load_patient_medicines.apply(world["db"], planned)
    for email in ("me@patient.test", "carer@home.test"):
        meds = as_user(main, world, email).get("/api/my-home").json()["medicines"]
        assert meds["has_demo"] is True and meds["medicines"], email
        assert {"drug", "dose", "how_often"} <= set(meds["medicines"][0])
    # Heart failure patients get heart failure medicines.
    drugs = [m["drug"] for m in patient_medicines.view(world["db"], A1)["medicines"]]
    assert "Furosemide" in drugs
    # A re-run never replaces a list (a doctor may have changed it).
    assert load_patient_medicines.plan(world["db"], demo=True)["lists"] == {}


def test_mimic_orders_running_at_discharge_become_the_list(world, tmp_path):
    adm = tmp_path / "admissions.csv"
    adm.write_text("subject_id,hadm_id,dischtime\n1101,9001,2150-01-10 12:00:00\n1101,9000,2149-01-01 00:00:00\n")
    rx = tmp_path / "prescriptions.csv"
    rx.write_text(
        "subject_id,hadm_id,starttime,stoptime,drug_type,drug,dose_val_rx,dose_unit_rx,doses_per_24_hrs,route\n"
        "1101,9001,2150-01-05 08:00:00,2150-01-20 00:00:00,MAIN,Metoprolol Tartrate,25,mg,2,PO\n"
        "1101,9001,2150-01-05 08:00:00,2150-01-06 00:00:00,MAIN,Vancomycin,1000,mg,2,IV\n"      # IV: hospital only
        "1101,9001,2150-01-02 08:00:00,2150-01-03 00:00:00,MAIN,Senna,8.6,mg,1,PO\n"            # stopped days before
        "1101,9000,2149-01-01 00:00:00,,MAIN,Old Drug,1,mg,1,PO\n")                              # earlier admission
    lists = load_patient_medicines.mimic_items(str(rx), str(adm), {1101})
    assert [(i["drug"], i["dose"], i["how_often"], i["source"]) for i in lists[A1]] == [
        ("Metoprolol Tartrate", "25 mg", "Twice a day", "mimic")]


# ---------------------------------------------------------------- appointments
def _booked(main, world, appointment, reason=None):
    world["db"]["care_actions"].update_one({"patient_id": A4}, {"$set": {"assigned_nurse_ids": [world["ids"]["nurse@a.test"]]}})
    f = as_user(main, world, "nurse@a.test").post(
        f"/api/patients/{A4}/followup", json={"urgency": "routine", **({"reason": reason} if reason else {})})
    assert f.status_code == 201, f.text
    return as_user(main, world, "cm@a.test").post(f"/api/followups/{f.json()['id']}/done",
                                                  json={"outcome": "booked", "appointment": appointment})


def test_a_booked_appointment_reaches_the_patient_and_caretaker(main, world):
    day = (today() + timedelta(days=9)).isoformat()
    r = _booked(main, world, {"date": day, "time": "10:30", "place": "Cardiology clinic", "with": "Dr Turner"})
    assert r.status_code == 200, r.text
    assert r.json()["appointment"]["time"] == "10:30"
    for email in ("me@patient.test", "carer@home.test"):
        nxt = as_user(main, world, email).get("/api/my-home").json()["next_appointment"]
        assert nxt["date"] == day and nxt["place"] == "Cardiology clinic", email
    person = as_user(main, world, "carer@home.test").get("/api/my-people").json()["people"][0]
    assert person["next_appointment"]["with"] == "Dr Turner"


@pytest.mark.parametrize("appointment, outcome", [
    ({"date": "2001-01-01"}, "booked"),                    # in the past
    ({"date": "tomorrow"}, "booked"),
    ({"date": "2999-01-01"}, "booked"),                    # more than a year ahead
    ({"date": "{d}", "time": "25:00"}, "booked"),
    ({"date": "{d}"}, "not_needed"),                       # only a booking has one
])
def test_bad_appointments_are_refused(main, world, appointment, outcome):
    world["db"]["care_actions"].update_one({"patient_id": A4}, {"$set": {"assigned_nurse_ids": [world["ids"]["nurse@a.test"]]}})
    f = as_user(main, world, "nurse@a.test").post(f"/api/patients/{A4}/followup", json={"urgency": "routine"}).json()
    appt = {k: v.replace("{d}", (today() + timedelta(days=3)).isoformat()) for k, v in appointment.items()}
    r = as_user(main, world, "cm@a.test").post(f"/api/followups/{f['id']}/done",
                                               json={"outcome": outcome, "appointment": appt})
    assert r.status_code == 422, r.text


def test_a_behavioural_health_booking_stays_private(main, world):
    day = (today() + timedelta(days=5)).isoformat()
    assert _booked(main, world, {"date": day, "place": "Behavioural health clinic"},
                   reason="behavioural_health").status_code == 200
    assert as_user(main, world, "me@patient.test").get("/api/my-home").json()["next_appointment"] is None
    assert followups.next_appointment(world["db"], A4, include_private=True)["date"] == day


# ---------------------------------------------------------------- case managers
def test_a_patient_is_told_their_own_case_manager(main, world):
    db = world["db"]
    auth.users(db).update_one({"email": "cm@a.test"}, {"$set": {"name": "Emily Carter"}})
    cm_id = world["ids"]["cm@a.test"]
    admin = as_user(main, world, "admin@a.test")
    assert admin.post("/api/care-team", json={"patient_ids": [A4], "case_manager_id": cm_id}).status_code == 200
    assert as_user(main, world, "carer@home.test").get("/api/my-home").json()["care_team"]["case_managers"] == ["Emily Carter"]
    staff = admin.get("/api/staff").json()
    assert staff["case_managers"][0]["patients"] == 1
    # Not someone else's case manager, and nobody's access changes.
    other = str(auth.users(db).insert_one({"email": "cm@b.test", "role": "case_manager", "status": "active",
                                           "hospital_id": "hosp-b"}).inserted_id)
    assert admin.post("/api/care-team", json={"patient_ids": [A1], "case_manager_id": other}).status_code == 404
    assert as_user(main, world, "doc.a@a.test").post("/api/care-team",
                                                     json={"patient_ids": [A1], "case_manager_id": cm_id}).status_code == 403
    history = admin.get(f"/api/patients/{A4}/care-team/history").json()
    assert "Emily Carter" in str(history)


def test_the_script_shares_patients_among_case_managers(world):
    db = world["db"]
    auth.users(db).insert_one({"email": "cm2@a.test", "name": "Omar Siddiqui", "role": "case_manager",
                               "status": "active", "hospital_id": "hosp-a"})
    planned = assign_case_managers.plan(db)
    assert len(planned["hosp-a"]["assign"]) == 4                        # A1-A4
    loads = sorted(c["patients_after"] for c in planned["hosp-a"]["case_managers"])
    assert loads == [2, 2]
    assign_case_managers.apply(db, planned)
    assert all(db["care_actions"].find_one({"patient_id": p}).get("assigned_case_manager_id")
               for p in (A1, A2, A4))
    assert assign_case_managers.plan(db)["hosp-a"]["assign"] == []       # re-run fills only gaps

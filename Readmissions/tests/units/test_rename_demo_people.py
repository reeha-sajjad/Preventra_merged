"""scripts/rename_demo_people.py: demo staff, registry doctors and every copy of
their names get English names; patients and real accounts are left alone, a dry
run writes nothing, a second run changes nothing, and --undo puts it back."""
from datetime import datetime, timezone

import mongomock
import pytest

from api import auth
from scripts import rename_demo_people as rename

T0 = datetime(2026, 9, 1, tzinfo=timezone.utc)


@pytest.fixture
def db():
    c = mongomock.MongoClient()
    db = c["neuroshield"]
    users = auth.users(db)
    users.insert_many([
        {"_id": "u-doc", "email": "cardiology@h1.test", "role": "doctor", "name": "Dr Amina Rahman", "created_at": T0},
        {"_id": "u-nurse", "email": "nurse1@h1.test", "role": "nurse", "name": "Nurse 1", "created_at": T0},
        {"_id": "u-cm", "email": "casemanager@h1.test", "role": "case_manager", "name": "Case Manager", "created_at": T0},
        {"_id": "u-real", "email": "real.doctor@hospital.org", "role": "doctor", "name": "Dr Real Person",
         "created_at": T0},
        {"_id": "u-pat", "email": "me@patient.test", "role": "patient", "name": "Amir Haddad", "created_at": T0},
    ])
    db["doctors"].insert_many([
        {"doctor_id": "DR-card", "email": "cardiology@h1.test", "name": "Dr Amina Rahman", "registered_at": "2026-09-01"},
        {"doctor_id": "DR-amir", "email": "amir@clinic.example", "name": "Dr. Amir Haddad",
         "registered_at": "2026-09-16 10:50:07"},
        {"doctor_id": "DR-elena", "email": "elena@clinic.example", "name": "Dr. Elena Kovač",
         "registered_at": "2026-09-16 10:53:55"},
        {"doctor_id": "DR-real", "email": "real.doctor@hospital.org", "name": "Dr Real Person",
         "registered_at": "2026-09-17"},
    ])
    db["clinical_alerts"].insert_many([
        {"alert_id": "A1", "patient_id": "MIMIC-1", "doctor_id": "DR-amir", "doctor_name": "Dr. Amir Haddad",
         "response": {"doctor_name": "Dr. Amir Haddad", "answered_by": "Nurse 1",
                      "responder": {"id": "u-nurse", "name": "Nurse 1", "role": "nurse"},
                      "recommendation": "Dr. Amir Haddad asked for a call"}},
        {"alert_id": "A2", "patient_id": "MIMIC-2", "doctor_id": "DR-real", "doctor_name": "Dr Real Person"},
    ])
    db["care_actions"].insert_one({"patient_id": "MIMIC-1", "coordinator_name": "Case Manager",
                                   "notes": [{"author": "Nurse 1 (Nurse)", "text": "Called Nurse 1 back"}],
                                   "patient_name": "Amir Haddad"})
    db["followups"].insert_one({"patient_id": "MIMIC-1",
                                "requested_by": {"id": "u-cm", "name": "Case Manager", "role": "case_manager"},
                                "history": [{"action": "requested", "by": {"id": "u-cm", "name": "Case Manager"}}]})
    db["readmission_outcomes"].insert_one({"patient_id": "MIMIC-1", "discharge_date": "2026-09-01",
                                           "recorded_by": {"id": "u-cm", "name": "Case Manager", "role": "case_manager"}})
    return db


def snapshot(db):
    out = {"users": sorted(auth.users(db).find({}, {"_id": 0, "updated_at": 0}), key=str)}   # a rename stamps the time
    for name in ("doctors", "clinical_alerts", "care_actions", "followups", "readmission_outcomes"):
        out[name] = sorted(db[name].find({}, {"_id": 0}), key=str)
    return out


def test_a_dry_run_writes_nothing(db):
    before = snapshot(db)
    report = rename.rename(db, dry=True)
    assert report["accounts"] and report["registry"]
    assert snapshot(db) == before
    assert db.client[auth.IDENTITY_DB][rename.RENAMES].count_documents({}) == 0


def test_every_demo_name_and_every_copy_becomes_english(db):
    rename.rename(db)
    users = {u["_id"]: u["name"] for u in auth.users(db).find()}
    assert users["u-doc"] == "Dr Emily Carter" and users["u-nurse"] == "Olivia Brown"
    assert users["u-cm"] == "Sarah Mitchell"
    assert users["u-real"] == "Dr Real Person"           # a real account is not ours to rename
    assert users["u-pat"] == "Amir Haddad"                # patients are never touched
    doctors = {d["doctor_id"]: d["name"] for d in db["doctors"].find()}
    assert doctors["DR-card"] == "Dr Emily Carter"        # takes its account's name
    assert doctors["DR-real"] == "Dr Real Person"
    assert {doctors["DR-amir"], doctors["DR-elena"]} <= set(rename.NAMES["doctor"])
    assert len(set(doctors.values())) == 4                # nobody shares a name
    a1 = db["clinical_alerts"].find_one({"alert_id": "A1"})
    assert a1["doctor_name"] == doctors["DR-amir"] == a1["response"]["doctor_name"]
    assert a1["response"]["answered_by"] == "Olivia Brown" == a1["response"]["responder"]["name"]
    assert a1["response"]["recommendation"] == "Dr. Amir Haddad asked for a call"   # free text is left alone
    assert db["clinical_alerts"].find_one({"alert_id": "A2"})["doctor_name"] == "Dr Real Person"
    care = db["care_actions"].find_one()
    assert care["coordinator_name"] == "Sarah Mitchell"
    assert care["notes"][0] == {"author": "Olivia Brown (Nurse)", "text": "Called Nurse 1 back"}
    assert care["patient_name"] == "Amir Haddad"
    f = db["followups"].find_one()
    assert f["requested_by"]["name"] == "Sarah Mitchell" == f["history"][0]["by"]["name"]
    assert db["readmission_outcomes"].find_one()["recorded_by"]["name"] == "Sarah Mitchell"


def test_a_second_run_changes_nothing(db):
    rename.rename(db)
    after = snapshot(db)
    report = rename.rename(db)
    assert not report["accounts"] and not report["registry"] and not any(report["copies"].values())
    assert snapshot(db) == after


def test_undo_puts_every_name_back(db):
    before = snapshot(db)
    rename.rename(db)
    assert snapshot(db) != before
    rename.undo(db)
    assert snapshot(db) == before
    assert db.client[auth.IDENTITY_DB][rename.RENAMES].count_documents({}) == 0
    assert rename.undo(db) == {}                          # nothing left to put back


def test_all_includes_real_accounts_when_asked(db):
    rename.rename(db, include_all=True)
    assert auth.users(db).find_one({"_id": "u-real"})["name"] in rename.NAMES["doctor"]
    assert auth.users(db).find_one({"_id": "u-pat"})["name"] == "Amir Haddad"


def test_every_name_on_the_lists_is_unique():
    for pool in rename.NAMES.values():
        assert len(pool) == len(set(pool))

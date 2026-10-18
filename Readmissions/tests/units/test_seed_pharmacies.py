"""scripts/seed_pharmacies.py: every patient in both apps gets a pharmacy,
nothing already set is overwritten, and no patient data changes."""
import copy

import mongomock

from api import auth
from scripts import seed_pharmacies as seed


def snapshot(collection):
    return sorted((copy.deepcopy(d) for d in collection.find({}, {"_id": 0})), key=str)


def world():
    client = mongomock.MongoClient()
    readm, glp1 = client["neuroshield"], client["glp1_analytics"]
    glp1["patient_access"].insert_many([
        {"patient_idx": 1, "hospital_id": "h", "pharmacy": "Main Street Pharmacy"},
        {"patient_idx": 2, "hospital_id": "h", "pharmacy": "CityCare Pharmacy"},
        {"patient_idx": 3, "hospital_id": "h", "pharmacy": "CityCare Pharmacy", "pharmacy_id": "chosen"},
        {"patient_idx": 4, "hospital_id": "h"},                                     # no pharmacy on file
    ])
    glp1["patients"].insert_many([{"patient_idx": i, "dropout_prob": 0.5} for i in range(1, 5)])
    readm["care_actions"].insert_many([{"patient_id": f"MIMIC-{i}", "hospital_id": "h"} for i in range(5)])
    readm["patient_worklist"].insert_many([{"patient_id": f"MIMIC-{i}", "risk_score": 50} for i in range(5)])
    return client, readm, glp1


def test_dry_run_writes_nothing():
    client, readm, glp1 = world()
    before = (snapshot(glp1["patient_access"]), snapshot(readm["care_actions"]))
    out = seed.run(client, "neuroshield", "glp1_analytics", "pw-123456", dry=True, with_logins=True)
    assert out["glp1"] == 2 and out["readmissions"] == 5 and len(out["logins"]) == len(out["pharmacies"])
    assert (snapshot(glp1["patient_access"]), snapshot(readm["care_actions"])) == before
    assert client[auth.IDENTITY_DB]["pharmacies"].count_documents({}) == 0
    assert auth.users(readm).count_documents({}) == 0


def test_patients_are_linked_and_choices_kept():
    client, readm, glp1 = world()
    patients_before = (snapshot(glp1["patients"]), snapshot(readm["patient_worklist"]))
    seed.run(client, "neuroshield", "glp1_analytics", dry=False)
    access = {d["patient_idx"]: d for d in glp1["patient_access"].find()}
    assert access[1]["pharmacy_id"] == "main-street-pharmacy"
    assert access[2]["pharmacy_id"] == "citycare-pharmacy"
    assert access[3]["pharmacy_id"] == "chosen"                     # someone's choice stays
    assert "pharmacy_id" not in access[4]                           # nothing to link from
    for d in readm["care_actions"].find():
        assert d["pharmacy_id"] == auth.slugify_org(seed.readmissions_pharmacy(d["patient_id"]))
    orgs = {o["_id"] for o in client[auth.IDENTITY_DB]["pharmacies"].find()}
    assert {"main-street-pharmacy", "citycare-pharmacy"} <= orgs
    assert (snapshot(glp1["patients"]), snapshot(readm["patient_worklist"])) == patients_before
    # Running again changes nothing.
    again = seed.run(client, "neuroshield", "glp1_analytics", dry=False)
    assert again["glp1"] == 0 and again["readmissions"] == 0 and again["created"] == []


def test_demo_logins_are_pharmacy_staff_of_their_pharmacy():
    client, readm, glp1 = world()
    out = seed.run(client, "neuroshield", "glp1_analytics", "pw-123456", dry=False, with_logins=True)
    users = list(auth.users(readm).find({"role": "pharmacy"}))
    assert len(users) == len(out["pharmacies"])
    main = next(u for u in users if u["email"] == "pharmacy@main-street-pharmacy.test")
    assert main["pharmacy_id"] == "main-street-pharmacy" and main["status"] == "active"
    assert auth.password_matches("pw-123456", main["password_hash"])
    # An existing login is left as it is.
    auth.users(readm).update_one({"_id": main["_id"]}, {"$set": {"password_hash": "kept"}})
    seed.run(client, "neuroshield", "glp1_analytics", "other-pass", dry=False, with_logins=True)
    assert auth.users(readm).find_one({"_id": main["_id"]})["password_hash"] == "kept"
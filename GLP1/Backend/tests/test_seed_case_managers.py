"""scripts/seed_case_managers.py, over the real 7,566 patients in memory.

The point: every patient of the hospital gets exactly one case manager, a
choice already made is never overwritten, nothing outside patient_access and
the new logins changes, and the GLP-1 dataset is untouched.
"""
import time

import bcrypt
import pytest
from fastapi.testclient import TestClient
from jose import jwt
from mongomock_motor import AsyncMongoMockClient

import main
from core import mongo
from core.config import settings
from scripts import seed_case_managers as seed
from tests.test_demo_patient_logins import HID, snapshot, world  # noqa: F401


def access_of(world):
    return {d["patient_idx"]: d for d in world["db"].patient_access.find({}, {"_id": 0})}


def test_split_rerun_and_the_dataset_untouched(world):
    """One world for all of it: building 7,566 patients in memory is the slow part."""
    # An unknown hospital changes nothing.
    with pytest.raises(SystemExit):
        seed.run(world["store"], "Nowhere", "123123123", dry=False)
    # A dry run writes nothing.
    users, access = world["ident"].users.count_documents({}), snapshot(world["db"].patient_access)
    assert sum(seed.run(world["store"], "Demo Hospital", "whatever", dry=True)["counts"].values()) > 0
    assert world["ident"].users.count_documents({}) == users
    assert snapshot(world["db"].patient_access) == access

    # The real run: four logins, one case manager for every patient of the hospital.
    patients_before = snapshot(world["db"].patients)
    out = seed.run(world["store"], "Demo Hospital", "123123123", dry=False)
    cms = {str(u["_id"]): u for u in world["ident"].users.find({"role": "case_manager"})}
    assert len(cms) == 4 and all(u["hospital_id"] == HID and u["status"] == "active" for u in cms.values())
    assert all(bcrypt.checkpw(b"123123123", u["password_hash"].encode()) for u in cms.values())
    access = access_of(world)
    mine = [a for a in access.values() if a["hospital_id"] == HID]
    assert all(a.get("case_manager_id") in cms for a in mine)
    assert not any(a.get("case_manager_id") for a in access.values() if a["hospital_id"] != HID)
    assert sum(out["counts"].values()) == len(mine)
    # Split by main reason: a refill patient goes to a refills case manager.
    reasons = {d["patient_idx"]: d.get("driver_1") or ""
               for d in world["db"].patients.find({}, {"driver_1": 1, "patient_idx": 1})}
    refill = next(a for a in mine if "refill" in reasons[a["patient_idx"]].lower())
    assert cms[refill["case_manager_id"]]["email"].startswith("casemanager.refills")
    assert snapshot(world["db"].patients) == patients_before

    # Re-running keeps every choice made since, and makes no new logins.
    chosen = str(world["ident"].users.find_one({"email": f"casemanager.cost@{HID}.test"})["_id"])
    world["db"].patient_access.update_one({"patient_idx": mine[0]["patient_idx"]},
                                          {"$set": {"case_manager_id": chosen}})
    before = snapshot(world["db"].patient_access)
    again = seed.run(world["store"], "Demo Hospital", "another-pass", dry=False)
    assert sum(again["counts"].values()) == 0 and again["written"] == 0
    assert snapshot(world["db"].patient_access) == before
    assert world["ident"].users.count_documents({"role": "case_manager"}) == 4


def test_an_existing_login_is_reused_and_sees_its_caseload(world):
    uid = str(world["ident"].users.insert_one({
        "email": f"casemanager@{HID}.test", "name": "Case Manager", "role": "case_manager",
        "status": "active", "hospital_id": HID, "password_hash": "kept"}).inserted_id)
    seed.run(world["store"], "Demo Hospital", "123123123", dry=False)
    assert world["ident"].users.find_one({"email": f"casemanager@{HID}.test"})["password_hash"] == "kept"
    expected = world["db"].patient_access.count_documents({"case_manager_id": uid})
    assert expected > 0

    token = jwt.encode({"sub": uid, "exp": int(time.time()) + 3600}, settings.shared_secret_key, algorithm="HS256")
    previous = mongo._client
    mongo._client = AsyncMongoMockClient(mock_mongo_client=world["store"])
    try:
        c = TestClient(main.app, headers={"Authorization": f"Bearer {token}"}, raise_server_exceptions=False)
        today = c.get("/api/case-manager/today?mine=1").json()
        assert today["caseload"]["patients"] == expected == today["stats"]["patients"]
    finally:
        mongo._client = previous
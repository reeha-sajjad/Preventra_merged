"""scripts/demo_mood.py: the simulated mood screens a demo shows. Nobody runs it
against a database from here - these tests use an in-memory store.

The points: it is labelled, repeatable and undoable; it never touches a real
screen, the dataset or the ownership records; and each scene flags as intended.
"""
import copy
from datetime import datetime, timezone

import mongomock
import pytest

from core.config import settings
from core.mood import COLLECTION
from scripts import demo_mood as demo

HID = "demo-hospital"
NOW = datetime(2026, 11, 1, 12, 0, tzinfo=timezone.utc)


def snapshot(collection):
    return sorted((copy.deepcopy(d) for d in collection.find({}, {"_id": 0})), key=lambda d: d.get("patient_idx", 0))


def make_world(logins=5):
    store = mongomock.MongoClient()
    db, ident = store[settings.mongodb_db_name], store[settings.shared_identity_db_name]
    ident.hospitals.insert_many([{"_id": HID, "name": "Demo Hospital"}, {"_id": "other", "name": "Other"}])
    db.patients.insert_many([{"patient_idx": i, "dropout_prob": 0.5, "cluster": 0} for i in range(10, 16)])
    for n in range(1, logins + 1):
        uid = str(ident.users.insert_one({"email": f"glp1.patient{n}@{HID}.test", "role": "patient",
                                          "status": "active", "hospital_id": HID}).inserted_id)
        db.patient_access.insert_one({"patient_idx": 9 + n, "hospital_id": HID, "patient_account_id": uid})
    db.patient_access.insert_one({"patient_idx": 15, "hospital_id": HID})
    return {"store": store, "db": db, "ident": ident}


@pytest.fixture()
def world():
    return make_world()


def test_dry_run_writes_nothing(world):
    before = {n: snapshot(world["db"][n]) for n in world["db"].list_collection_names()}
    rows = demo.run(world["store"], "Demo Hospital", dry=True, now=NOW)
    assert len(rows) == 5
    assert {n: snapshot(world["db"][n]) for n in world["db"].list_collection_names()} == before
    assert COLLECTION not in world["db"].list_collection_names() and demo.STATE not in world["db"].list_collection_names()
    world["store"][settings.mongodb_db_name][demo.STATE].insert_one({"run_id": "r1", "hospital_id": HID})
    world["db"][COLLECTION].insert_one({"patient_idx": 10, "demo_run": "r1", "simulated": True})
    assert demo.run(world["store"], "Demo Hospital", dry=True, undo=True) == [("r1", 1)]
    assert world["db"][COLLECTION].count_documents({}) == 1 and world["db"][demo.STATE].count_documents({}) == 1


def test_each_scene_flags_as_intended_and_everything_is_labelled(world):
    rows = demo.run(world["store"], "Demo Hospital", dry=False, now=NOW)
    assert [(r[0], r[2], r[3]) for r in rows] == [
        ("moderate_first", 10, ["phq8_moderate"]), ("rise", 11, ["phq8_rise"]),
        ("low_mood_event", 12, ["low_mood_trouble"]), ("gad2_only", 13, ["gad2_positive"]),
        ("unflagged", 14, [])]
    assert [r[1] for r in rows] == [f"glp1.patient{n}@{HID}.test" for n in range(1, 6)]
    assert [s[2] for s in demo.SCENES] == [r[3] for r in rows]                    # the table in the script agrees
    docs = list(world["db"][COLLECTION].find({}))
    assert len(docs) == 8
    assert all(d["simulated"] is True and d["demo"] is True and d["recorded_by"] == "Demo data (simulated)"
               for d in docs)
    event = world["db"][COLLECTION].find_one({"patient_idx": 12})
    assert event["trouble"] == ["missed_pickup"] and event["trouble_simulated"] is True
    state = list(world["db"][demo.STATE].find({}))
    assert len(state) == 1 and state[0]["status"] == "done" and state[0]["hospital_id"] == HID
    assert {d["demo_run"] for d in docs} == {state[0]["run_id"]}


def test_nothing_but_the_two_collections_is_written(world):
    patients, access = snapshot(world["db"].patients), snapshot(world["db"].patient_access)
    users = snapshot(world["ident"].users)
    names = set(world["db"].list_collection_names())
    demo.run(world["store"], "Demo Hospital", dry=False, now=NOW)
    assert snapshot(world["db"].patients) == patients and snapshot(world["db"].patient_access) == access
    assert snapshot(world["ident"].users) == users
    assert set(world["db"].list_collection_names()) - names == {COLLECTION, demo.STATE}


def test_running_it_again_leaves_one_set(world):
    demo.run(world["store"], "Demo Hospital", dry=False, now=NOW)
    first = {d["demo_run"] for d in world["db"][COLLECTION].find({})}
    rows = demo.run(world["store"], "Demo Hospital", dry=False, now=NOW)
    assert world["db"][COLLECTION].count_documents({}) == 8 and len(rows) == 5
    assert world["db"][demo.STATE].count_documents({}) == 1
    assert {d["demo_run"] for d in world["db"][COLLECTION].find({})}.isdisjoint(first)


def test_undo_removes_the_simulated_screens_and_never_a_real_one(world):
    demo.run(world["store"], "Demo Hospital", dry=False, now=NOW)
    world["db"][COLLECTION].insert_one({"patient_idx": 10, "at": NOW, "answered_on": "2026-11-01",
                                        "previous_id": "none", "phq2": 1, "flags": []})
    removed = demo.run(world["store"], "Demo Hospital", dry=False, undo=True)
    assert [n for _, n in removed] == [8]
    left = list(world["db"][COLLECTION].find({}))
    assert len(left) == 1 and left[0]["previous_id"] == "none"
    assert world["db"][demo.STATE].count_documents({}) == 0
    assert demo.run(world["store"], "Demo Hospital", dry=False, undo=True) == []


def test_with_fewer_logins_the_first_scenes_run():
    world = make_world(logins=3)
    rows = demo.run(world["store"], "Demo Hospital", dry=False, now=NOW)
    assert [r[0] for r in rows] == ["moderate_first", "rise", "low_mood_event"]
    assert world["db"][COLLECTION].count_documents({}) == 5


def test_unknown_hospital_changes_nothing(world):
    with pytest.raises(SystemExit):
        demo.run(world["store"], "No Such Hospital", dry=False, now=NOW)
    assert demo.STATE not in world["db"].list_collection_names()

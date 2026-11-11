"""A caretaker's daily "medicines given" tick (api/medicines_given.py).
carer@home.test is linked to A4 (me@patient.test), hospital A."""
from api import auth, medicines_given
from tests.units.test_isolation import A4, as_user, main, world  # noqa: F401


def test_a_caretaker_ticks_and_the_care_team_sees_it(main, world):
    carer, me = as_user(main, world, "carer@home.test"), as_user(main, world, "me@patient.test")
    body = carer.post("/api/my-home/medicines", json={"note": "With breakfast"}).json()
    assert len(body["entries"]) == 1 and body["entries"][0]["can_undo"] and body["can_tick"]
    assert carer.post("/api/my-home/medicines", json={}).status_code == 409         # once a day
    seen = me.get("/api/my-home/medicines").json()
    assert seen["last_4_weeks"] == 1 and seen["can_tick"] is False
    assert me.post("/api/my-home/medicines", json={}).status_code == 403
    staff = as_user(main, world, "cm@a.test").get(f"/api/patients/{A4}/medicines-given").json()
    assert staff["entries"][0]["note"] == "With breakfast" and staff["expected_4_weeks"] == 28
    assert as_user(main, world, "admin@b.test").get(f"/api/patients/{A4}/medicines-given").status_code == 404
    assert carer.get(f"/api/patients/{A4}/medicines-given").status_code == 200   # their own people's record
    eid = body["entries"][0]["id"]
    assert me.delete(f"/api/my-home/medicines/{eid}").status_code == 403
    assert carer.delete(f"/api/my-home/medicines/{eid}").json()["entries"] == []
    assert world["db"][medicines_given.COLLECTION].count_documents({}) == 1        # kept, marked undone

def test_a_patient_with_no_caretaker_ticks_their_own(main, world):
    db = world["db"]
    db["care_actions"].update_one({"patient_id": A4}, {"$set": {"caretaker_ids": []}})
    me = as_user(main, world, "me@patient.test")
    assert me.get("/api/my-home/medicines").json()["can_tick"] is True
    body = me.post("/api/my-home/medicines", json={"note": "Took them after lunch"}).json()
    assert body["entries"][0]["by_role"] == "patient" and body["entries"][0]["can_undo"]
    staff = as_user(main, world, "cm@a.test").get(f"/api/patients/{A4}/medicines-given").json()
    assert staff["entries"][0]["note"] == "Took them after lunch" and staff["entries"][0]["by_role"] == "patient"
    assert me.post("/api/my-home/medicines", json={}).status_code == 409         # once a day

    # Once a caretaker is linked, they tick and the patient only reads it.
    carer = auth.users(db).find_one({"email": "carer@home.test"})
    db["care_actions"].update_one({"patient_id": A4}, {"$set": {"caretaker_ids": [str(carer["_id"])]}})
    assert me.get("/api/my-home/medicines").json()["can_tick"] is False


def test_a_note_longer_than_the_limit_is_refused(main, world):
    carer = as_user(main, world, "carer@home.test")
    assert carer.post("/api/my-home/medicines", json={"note": "x" * (medicines_given.MAX_NOTE + 1)}).status_code == 422


def test_the_card_knows_today_is_done(main, world):
    carer = as_user(main, world, "carer@home.test")
    assert carer.get("/api/my-home/medicines").json()["ticked_today"] is False
    assert carer.post("/api/my-home/medicines", json={}).json()["ticked_today"] is True


def test_a_patient_with_no_caretaker_ticks_their_own(main, world):
    db = world["db"]
    db["care_actions"].update_one({"patient_id": A4}, {"$set": {"caretaker_ids": []}})
    me = as_user(main, world, "me@patient.test")
    assert me.get("/api/my-home/medicines").json()["can_tick"] is True
    body = me.post("/api/my-home/medicines", json={"note": "Took them after lunch"}).json()
    assert body["entries"][0]["by_role"] == "patient" and body["entries"][0]["can_undo"]
    staff = as_user(main, world, "cm@a.test").get(f"/api/patients/{A4}/medicines-given").json()
    assert staff["entries"][0]["note"] == "Took them after lunch" and staff["entries"][0]["by_role"] == "patient"
    assert me.post("/api/my-home/medicines", json={}).status_code == 409         # once a day

    # Once a caretaker is linked, they tick and the patient only reads it.
    carer = auth.users(db).find_one({"email": "carer@home.test"})
    db["care_actions"].update_one({"patient_id": A4}, {"$set": {"caretaker_ids": [str(carer["_id"])]}})
    assert me.get("/api/my-home/medicines").json()["can_tick"] is False


def test_a_note_longer_than_the_limit_is_refused(main, world):
    carer = as_user(main, world, "carer@home.test")
    assert carer.post("/api/my-home/medicines", json={"note": "x" * (medicines_given.MAX_NOTE + 1)}).status_code == 422


def test_the_card_knows_today_is_done(main, world):
    carer = as_user(main, world, "carer@home.test")
    assert carer.get("/api/my-home/medicines").json()["ticked_today"] is False
    assert carer.post("/api/my-home/medicines", json={}).json()["ticked_today"] is True
"""A caretaker's daily "medicines given" tick (api/medicines_given.py).
carer@home.test is linked to A4 (me@patient.test), hospital A."""
from api import medicines_given
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
    assert carer.get(f"/api/patients/{A4}/medicines-given").status_code == 403
    eid = body["entries"][0]["id"]
    assert me.delete(f"/api/my-home/medicines/{eid}").status_code == 403
    assert carer.delete(f"/api/my-home/medicines/{eid}").json()["entries"] == []
    assert world["db"][medicines_given.COLLECTION].count_documents({}) == 1        # kept, marked undone
"""The Follow-ups list tells a case manager which requests are for the patients they
look after (the patient's named case manager) and which they have taken
(api/followups.listing: case_manager, for_me, mine). The page filters on these.

Accounts from test_isolation.py: nurse@a.test looks after A1-A3; cm@a.test and
admin@a.test are hospital A's."""
from api import auth, followups
from tests.units.test_isolation import A1, A2, as_user, main, world  # noqa: F401


def test_requests_say_whose_patient_they_are(main, world):
    db = world["db"]
    cm = auth.users(db).find_one({"email": "cm@a.test"})
    nurse = auth.users(db).find_one({"email": "nurse@a.test"})
    db["care_actions"].update_one({"patient_id": A1}, {"$set": {"assigned_case_manager_id": str(cm["_id"])}})
    first = followups.request(db, nurse, None, A1, "routine", "Check weight")
    followups.request(db, nurse, None, A2, "routine", "Call about bloods")

    items = {i["patient_id"]: i for i in as_user(main, world, "cm@a.test").get("/api/followups").json()["items"]}
    assert items[A1]["for_me"] is True and items[A1]["case_manager"]
    assert items[A2]["for_me"] is False and items[A2]["case_manager"] is None
    assert not items[A1]["mine"]                      # not taken yet

    as_user(main, world, "cm@a.test").post(f"/api/followups/{first['id']}/take")
    items = {i["patient_id"]: i for i in as_user(main, world, "cm@a.test").get("/api/followups").json()["items"]}
    assert items[A1]["mine"] is True

    # A hospital admin sees the same list; none of it is "for" them.
    admin_items = as_user(main, world, "admin@a.test").get("/api/followups").json()["items"]
    assert admin_items and not any(i["for_me"] for i in admin_items)
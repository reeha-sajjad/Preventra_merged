"""The patient scope is fetched only as far as a request needs (api/access.py
PatientScope): one patient is one lookup, a list is the full set - and either
way the caller sees exactly the patients they saw before."""
from api import access, auth
from tests.units.test_isolation import A1, A2, A3, A4, B1, as_user, main, world  # noqa: F401


def user(world, email):
    return auth.users(world["db"]).find_one({"email": email})


def test_one_patient_is_one_lookup_not_the_whole_hospital(main, world):
    scope = access.patient_scope(world["db"], user(world, "cm@a.test"))
    assert A1 in scope and B1 not in scope
    assert scope._ids is None                                   # nothing listed for a membership check
    assert scope.allowed([A2, B1, A3]) == [A2, A3]
    assert sorted(scope) == sorted([A1, A2, A3, A4])


def test_a_query_naming_its_patients_still_only_returns_the_callers(main, world):
    db = world["db"]
    view = access.ScopedDB(db, user(world, "doc.a@a.test"), access.patient_scope(db, user(world, "doc.a@a.test")))
    rows = lambda q: {r["patient_id"] for r in view["patient_worklist"].find(q)}  # noqa: E731
    assert rows({"patient_id": A1}) == {A1}
    assert rows({"patient_id": B1}) == set()                      # another hospital's
    assert rows({"patient_id": A3}) == set()                      # their hospital, not their patient
    assert rows({"patient_id": {"$in": [A1, A3, B1]}}) == {A1}
    assert rows({}) == {A1, A2}                                    # a list: the whole scope
    assert view["patient_worklist"].count_documents({"patient_id": B1}) == 0
    assert list(view["weekly_monitoring"].aggregate([{"$match": {"patient_id": B1}}])) == []


def test_an_emergency_grant_counts_without_listing_everyone(main, world):
    db = world["db"]
    nurse = user(world, "nurse@a.test")
    import datetime as dt
    db.client[auth.IDENTITY_DB]["emergency_access"].insert_one({
        "app": "readmissions", "user_id": str(nurse["_id"]), "patient_id": A4,
        "expires_at": dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=1)})
    scope = access.patient_scope(db, nurse)
    assert A4 in scope and B1 not in scope and scope._ids is None
    assert set(scope) == {A1, A2, A3, A4}


def test_a_single_patient_page_still_refuses_other_hospitals(main, world):
    cm = as_user(main, world, "cm@a.test")
    assert cm.get(f"/api/patients/{A1}").status_code == 200
    assert cm.get(f"/api/patients/{B1}").status_code == 404

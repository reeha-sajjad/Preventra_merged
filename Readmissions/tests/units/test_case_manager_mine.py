"""A case manager's "Assigned to me" on the Patients list (api/hospital.care_filter,
GET /api/patients?case_manager=me). It only narrows the list: a case manager still
sees the whole hospital without it, and nobody sees past their own scope with it.

Accounts from test_isolation.py: cm@a.test and admin@a.test are hospital A's."""
from api import auth
from tests.units.test_isolation import A1, A2, as_user, main, world  # noqa: F401


def _ids(client, query=""):
    body = client.get(f"/api/patients?limit=50{query}").json()
    return {r["id"] for r in body["data"]}


def _make_case_manager_of(world, email, patient_id):
    cm = auth.users(world["db"]).find_one({"email": email})
    world["db"]["care_actions"].update_one({"patient_id": patient_id},
                                           {"$set": {"assigned_case_manager_id": str(cm["_id"])}})
    return str(cm["_id"])


def test_assigned_to_me_narrows_a_case_managers_list(main, world):
    cm_id = _make_case_manager_of(world, "cm@a.test", A1)
    cm = as_user(main, world, "cm@a.test")
    everyone = _ids(cm)
    assert {A1, A2} <= everyone                      # still the whole hospital by default
    assert _ids(cm, "&case_manager=me") == {A1}
    assert A1 not in _ids(cm, "&unassigned=case_manager")
    assert A2 in _ids(cm, "&unassigned=case_manager")
    # A hospital admin can look at one case manager's patients by id.
    assert _ids(as_user(main, world, "admin@a.test"), f"&case_manager={cm_id}") == {A1}


def test_the_filter_never_widens_anyones_scope(main, world):
    cm_id = _make_case_manager_of(world, "cm@a.test", A1)
    # Another hospital's admin naming hospital A's case manager gets nothing.
    assert _ids(as_user(main, world, "admin@b.test"), f"&case_manager={cm_id}") == set()
    # A doctor asking for "me" is named on no patient as case manager.
    assert _ids(as_user(main, world, "doc.a@a.test"), "&case_manager=me") == set()
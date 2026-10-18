"""Each patient's one main case manager (core/hospital.py `case_manager_id`):
setting it, "My caseload", and new follow-up requests ringing only that
person's bell (core/followups.open_for_bell). Same accounts as
test_isolation.py; hospital A's patients are 0-3, hospital B's 4 and 5.
"""
import pytest

from core.config import settings
from core.followups import COLLECTION
from tests.test_followups import ask, second_cm  # noqa: F401
from tests.test_isolation import client, world  # noqa: F401


@pytest.fixture(autouse=True)
def fresh(world):
    def clean():
        world["db"][COLLECTION].delete_many({})
        world["db"].patient_access.update_many({}, {"$unset": {"case_manager_id": ""}})
    clean()
    yield
    clean()


def uid(c):
    return c.get("/api/me").json()["id"]


def set_cm(world, idxs, cm_id, who="admin@a.test"):
    return client(world, who).post("/api/care-team", json={"patient_ids": idxs, "case_manager_id": cm_id})


def team(world, idx, who="admin@a.test"):
    rows = client(world, who).get("/api/patients?page_size=50").json()["patients"]
    return next(r for r in rows if r["patient_idx"] == idx)


def new_requests(c):
    return {i["patient_idx"] for i in c.get("/api/notifications").json()["items"] if "followup_new" in i["kinds"]}


# --------------------------------------------------------------- setting it
def test_an_admin_or_case_manager_sets_the_main_case_manager(world, second_cm):
    me = uid(client(world, "cm@a.test"))
    assert set_cm(world, [0, 1], me).status_code == 200
    assert team(world, 0)["case_manager"]["email"] == "cm@a.test"
    # A case manager can change it too - here, to a colleague.
    assert set_cm(world, [1], uid(second_cm), who="cm@a.test").status_code == 200
    assert team(world, 1)["case_manager"]["email"] == "cm2@a.test"
    # Doctors and nurses stay as they were.
    assert [d["email"] for d in team(world, 0)["doctors"]] == ["doc@a.test"]
    # "" clears it.
    assert set_cm(world, [0], "").status_code == 200
    assert team(world, 0)["case_manager"] is None


def test_only_a_case_manager_of_the_same_hospital(world):
    doctor = uid(client(world, "doc@a.test"))
    assert set_cm(world, [0], doctor).status_code == 404          # not a case manager
    assert set_cm(world, [0], "000000000000000000000000").status_code == 404
    assert set_cm(world, [4], uid(client(world, "cm@a.test")), who="admin@b.test").status_code == 404
    assert client(world, "doc@a.test").post("/api/care-team", json={
        "patient_ids": [0], "case_manager_id": ""}).status_code == 403   # doctors don't assign


# ------------------------------------------------------------- my caseload
def test_my_caseload_on_the_patient_list(world, second_cm):
    cm = client(world, "cm@a.test")
    set_cm(world, [0, 2], uid(cm))
    set_cm(world, [1], uid(second_cm))
    mine = {r["patient_idx"] for r in cm.get("/api/patients?case_manager=me&page_size=50").json()["patients"]}
    assert mine == {0, 2}
    nobody = {r["patient_idx"] for r in
              cm.get("/api/patients?unassigned=case_manager&page_size=50").json()["patients"]}
    assert nobody == {3}
    # Still the whole hospital without the filter: colleagues can cover.
    assert len(cm.get("/api/patients?page_size=50").json()["patients"]) == 4


def test_today_for_my_caseload(world, second_cm):
    cm = client(world, "cm@a.test")
    set_cm(world, [0, 3], uid(cm))
    set_cm(world, [1], uid(second_cm))
    whole = cm.get("/api/case-manager/today").json()
    assert whole["caseload"] == {"patients": 2, "mine": False} and whole["stats"]["patients"] == 4
    assert whole["gaps"]["no_case_manager"] == 1                                   # patient 2
    mine = cm.get("/api/case-manager/today?mine=1").json()
    assert mine["caseload"]["mine"] and mine["stats"]["patients"] == 2
    assert {p["patient_idx"] for p in mine["gaps"]["patients"]} == {3}         # 2 isn't mine
    # Follow-ups: my patients' and nobody's, not my colleague's.
    ask(world, idx=1)                                     # second CM's patient
    ask(world, "nurse@a.test", idx=2)                     # nobody's
    assert [n["patient_idx"] for n in cm.get("/api/case-manager/today?mine=1").json()["followups"]["next"]] == [2]
    assert cm.get("/api/case-manager/today").json()["followups"]["waiting"] == 2


def test_the_staff_page_counts_each_case_managers_patients(world, second_cm):
    set_cm(world, [0, 2], uid(client(world, "cm@a.test")))
    body = client(world, "admin@a.test").get("/api/staff").json()
    counts = {c["email"]: c["patients"] for c in body["case_managers"]}
    assert counts == {"cm@a.test": 2, "cm2@a.test": 0} and body["no_case_manager"] == 2


# ------------------------------------------------- who hears about a request
def test_a_request_rings_only_the_patients_case_manager(world, second_cm):
    cm = client(world, "cm@a.test")
    set_cm(world, [0], uid(second_cm))
    ask(world, idx=0)                                     # the second CM's patient
    ask(world, "nurse@a.test", idx=2)                     # nobody's
    assert new_requests(second_cm) == {0, 2}
    assert new_requests(cm) == {2}
    # Everyone still sees both in the list, and anyone can take one.
    listed = {i["patient_idx"]: i for i in cm.get("/api/followups").json()["items"]}
    assert set(listed) == {0, 2}
    assert listed[0]["case_manager"]["email"] == "cm2@a.test" and not listed[0]["my_patient"]
    assert cm.post(f"/api/followups/{listed[0]['id']}/take").status_code == 200


def test_if_their_case_manager_has_left_everyone_hears(world, second_cm):
    set_cm(world, [0], uid(second_cm))
    world["store"][settings.shared_identity_db_name].users.update_one(
        {"email": "cm2@a.test"}, {"$set": {"status": "suspended"}})
    ask(world, idx=0)
    assert new_requests(client(world, "cm@a.test")) == {0}
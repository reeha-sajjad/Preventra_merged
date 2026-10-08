"""The doctors' and nurses' bell (core/notifications.py), over the same accounts
and ownership as test_isolation.py: doc@a.test has patients 0 and 1,
nurse@a.test 0, 1 and 2, and patient 4 belongs to another hospital."""
import pytest

from core.config import settings
from core.notifications import SEEN_COLLECTION
from tests.test_isolation import client, world  # noqa: F401

CARE = ("doc@a.test", "nurse@a.test")


@pytest.fixture(autouse=True)
def fresh(world):
    """Known risks for patients 0-4, an empty bell history, original care teams."""
    db = world["db"]
    risks = {d["patient_idx"]: d["dropout_prob"] for d in
             db.patients.find({"patient_idx": {"$in": [0, 1, 2, 3, 4]}}, {"patient_idx": 1, "dropout_prob": 1})}
    teams = list(db.patient_access.find({}, {"_id": 0}))
    set_risk(world, {0: 0.90, 1: 0.30, 2: 0.80, 3: 0.10, 4: 0.95})
    db[SEEN_COLLECTION].delete_many({})
    world["store"][settings.shared_identity_db_name].access_log.delete_many({})
    yield
    set_risk(world, risks)
    db[SEEN_COLLECTION].delete_many({})
    world["store"][settings.shared_identity_db_name].access_log.delete_many({})
    db.patient_access.delete_many({})
    db.patient_access.insert_many(teams)


def set_risk(world, risks):
    for idx, prob in risks.items():
        world["db"].patients.update_one({"patient_idx": idx}, {"$set": {"dropout_prob": prob}})


def bell(world, email):
    r = client(world, email).get("/api/notifications")
    assert r.status_code == 200, r.text
    return {i["patient_idx"]: i["kinds"] for i in r.json()["items"]}


def uid(world, email):
    return str(world["store"][settings.shared_identity_db_name].users.find_one({"email": email})["_id"])


@pytest.mark.parametrize("email", ["admin@a.test", "cm@a.test", "claims@acme.test", "me@patient.test"])
def test_patient_alerts_are_for_doctors_and_nurses_only(world, email):
    """Others get no patient alerts (a case manager's bell holds follow-up
    requests only - tests/test_followups.py), and cannot dismiss any."""
    c = client(world, email)
    r = c.get("/api/notifications")
    assert r.status_code == 200 and r.json()["items"] == []
    assert r.json()["enabled"] is (email == "cm@a.test")
    assert c.post("/api/notifications/seen", json={"all": True}).status_code == 403


def test_critical_patients_not_yet_opened_are_listed(world):
    assert bell(world, "doc@a.test") == {0: ["unreviewed"]}          # 1 is not critical
    assert bell(world, "nurse@a.test") == {0: ["unreviewed"], 2: ["unreviewed"]}


@pytest.mark.parametrize("email", CARE)
def test_the_bell_never_mentions_anyone_elses_patient(world, email):
    allowed = {"doc@a.test": {0, 1}, "nurse@a.test": {0, 1, 2}}[email]
    assert set(bell(world, email)) <= allowed                          # 4 is critical but not theirs


def test_opening_a_patient_clears_it_for_that_person_only(world):
    assert client(world, "doc@a.test").get("/api/patients/0").status_code == 200
    assert bell(world, "doc@a.test") == {}
    assert 0 in bell(world, "nurse@a.test")


def test_a_big_rise_since_last_look_comes_back(world):
    client(world, "doc@a.test").get("/api/patients/1")                  # looked at it at 30%
    set_risk(world, {1: 0.38})                                          # +8, same band: quiet
    assert 1 not in bell(world, "doc@a.test")
    set_risk(world, {1: 0.42})                                          # +12: alert
    assert bell(world, "doc@a.test")[1] == ["risk_up"]
    client(world, "doc@a.test").get("/api/patients/1")
    assert 1 not in bell(world, "doc@a.test")


def test_moving_up_a_band_alerts_even_when_small(world):
    client(world, "doc@a.test").get("/api/patients/1")                  # 30%: medium
    set_risk(world, {1: 0.51})                                          # +21 and high
    item = client(world, "doc@a.test").get("/api/notifications").json()["items"][0]
    assert item["kinds"] == ["risk_up"] and item["previous_prob"] == pytest.approx(0.30)
    set_risk(world, {1: 0.24})
    client(world, "doc@a.test").get("/api/patients/1")                  # looked at it at 24%: low
    set_risk(world, {1: 0.26})                                          # +2 but low -> medium
    assert bell(world, "doc@a.test")[1] == ["risk_up"]


def test_a_new_patient_on_the_team_is_announced_until_opened(world):
    doc = uid(world, "doc@a.test")
    admin = client(world, "admin@a.test")
    assert admin.post("/api/care-team", json={"patient_ids": [3], "add_doctor_ids": [doc]}).status_code == 200
    assert bell(world, "doc@a.test")[3] == ["new"]                       # low risk, still new
    assert 3 not in bell(world, "nurse@a.test")                         # not on her team
    client(world, "doc@a.test").get("/api/patients/3")
    assert 3 not in bell(world, "doc@a.test")


def test_being_added_again_after_removal_is_new_again(world):
    doc = uid(world, "doc@a.test")
    admin = client(world, "admin@a.test")
    client(world, "doc@a.test").get("/api/patients/0")
    admin.post("/api/care-team", json={"patient_ids": [0], "remove_doctor_ids": [doc]})
    admin.post("/api/care-team", json={"patient_ids": [0], "add_doctor_ids": [doc]})
    assert bell(world, "doc@a.test")[0] == ["new"]


def test_existing_assignments_are_not_announced(world):
    """Patients given out before this feature (the demo seed) have no join time."""
    assert all("new" not in k for k in bell(world, "nurse@a.test").values())


def test_dismissing_one_or_all(world):
    nurse = client(world, "nurse@a.test")
    assert nurse.post("/api/notifications/seen", json={"patient_ids": [2]}).json() == {"seen": 1}
    assert bell(world, "nurse@a.test") == {0: ["unreviewed"]}
    assert nurse.post("/api/notifications/seen", json={"all": True}).json() == {"seen": 1}
    assert bell(world, "nurse@a.test") == {}


def test_dismissing_is_not_an_opening(world):
    """The access log records who opened what; dismissing an alert is not that."""
    client(world, "nurse@a.test").post("/api/notifications/seen", json={"all": True})
    assert list(world["store"][settings.shared_identity_db_name].access_log.find()) == []


def test_dismissing_anyone_elses_patient_is_refused(world):
    c = client(world, "doc@a.test")
    assert c.post("/api/notifications/seen", json={"patient_ids": [4]}).status_code == 404
    assert c.post("/api/notifications/seen", json={"patient_ids": [2]}).status_code == 404
    assert c.post("/api/notifications/seen", json={}).status_code == 422
    assert world["db"][SEEN_COLLECTION].count_documents({}) == 0


def test_counts_and_order(world):
    doc = uid(world, "doc@a.test")
    client(world, "doc@a.test").get("/api/patients/1")
    set_risk(world, {1: 0.60})
    client(world, "admin@a.test").post("/api/care-team", json={"patient_ids": [3], "add_doctor_ids": [doc]})
    body = client(world, "doc@a.test").get("/api/notifications").json()
    assert [i["patient_idx"] for i in body["items"]] == [1, 3, 0]       # risen, new, unreviewed
    assert body["total"] == 3 and body["counts"] == {"risk_up": 1, "new": 1, "unreviewed": 1, "followup_new": 0, "followup_done": 0}
    assert not {"name", "email", "BMXBMI"} & set().union(*body["items"])
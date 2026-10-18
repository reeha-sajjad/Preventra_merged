"""The case manager's side (core/casemanager.py "Today", and hand-over / release
in core/followups.py), over the same accounts as test_isolation.py.

Hospital A: cm@a.test is its case manager; patients 0-3 are hospital A (0 and 1
have a doctor; 2 has only a nurse; 3 has neither). Hospital B: admin@b.test, no
case manager.
"""
import pytest

from core.followups import COLLECTION
from tests.test_followups import ask, second_cm  # noqa: F401
from tests.test_isolation import client, world  # noqa: F401


@pytest.fixture(autouse=True)
def fresh(world):
    world["db"][COLLECTION].delete_many({})
    yield
    world["db"][COLLECTION].delete_many({})


def today(world, email="cm@a.test"):
    return client(world, email).get("/api/case-manager/today")


# -------------------------------------------------------------------- today
def test_today_counts_the_hospitals_patients(world):
    body = today(world).json()
    assert body["hospital"]["id"] == "hosp-a"
    s = body["stats"]
    assert s["patients"] == 4                                   # patients 0-3
    assert s["critical"] + s["high"] <= 4 and 0 <= s["adherent"] <= 4
    assert body["gaps"]["no_doctor"] == 2                       # 2 and 3
    assert body["gaps"]["no_nurse"] == 1                        # 3
    assert {p["patient_idx"] for p in body["gaps"]["patients"]} == {2, 3}
    three = next(p for p in body["gaps"]["patients"] if p["patient_idx"] == 3)
    assert three["missing"] == ["doctor", "nurse"]


def test_today_shows_the_follow_up_queue_and_mine(world):
    f = ask(world, idx=0).json()
    ask(world, "nurse@a.test", idx=2, urgency="routine")
    q = today(world).json()["followups"]
    assert {k: q[k] for k in ("waiting", "urgent_waiting", "mine", "in_progress", "overdue")} == \
        {"waiting": 2, "urgent_waiting": 1, "mine": 0, "in_progress": 0, "overdue": 0}
    assert [n["patient_idx"] for n in q["next"]] == [0, 2]          # urgent first
    client(world, "cm@a.test").post(f"/api/followups/{f['id']}/take")
    q = today(world).json()["followups"]
    assert q["waiting"] == 1 and q["mine"] == 1 and q["in_progress"] == 1


def test_a_followed_up_patient_is_not_unattended(world):
    critical = [p["patient_idx"] for p in today(world).json()["unattended"]["patients"]]
    if not critical:
        pytest.skip("no critical-risk patient in hospital A's sample")
    idx = critical[0]
    who = "doc@a.test" if idx in (0, 1) else "nurse@a.test"
    assert ask(world, who, idx=idx).status_code == 201
    assert idx not in [p["patient_idx"] for p in today(world).json()["unattended"]["patients"]]


@pytest.mark.parametrize("email", ["admin@a.test", "doc@a.test", "nurse@a.test", "claims@acme.test",
                                   "me@patient.test", "ops@team.test"])
def test_today_is_for_case_managers(world, email):
    assert today(world, email).status_code == 403


def test_today_never_counts_another_hospital(world):
    # hospital B has patients 4 and 5; nothing of theirs appears
    body = today(world).json()
    ids = {p["patient_idx"] for p in body["gaps"]["patients"]} | \
          {p["patient_idx"] for p in body["unattended"]["patients"]}
    assert not ids & {4, 5}


# ------------------------------------------------- hand over and release
def test_the_hospitals_case_managers_are_listed(world, second_cm):
    names = [h["email"] for h in client(world, "cm@a.test").get("/api/followups/handlers").json()["handlers"]]
    assert set(names) == {"cm@a.test", "cm2@a.test"}
    assert client(world, "admin@b.test").get("/api/followups/handlers").json()["handlers"] == []
    assert client(world, "doc@a.test").get("/api/followups/handlers").status_code == 403


def test_hand_over_a_taken_request(world, second_cm):
    f = ask(world).json()
    cm = client(world, "cm@a.test")
    cm.post(f"/api/followups/{f['id']}/take")
    other = next(h for h in cm.get("/api/followups/handlers").json()["handlers"] if h["email"] == "cm2@a.test")
    r = cm.post(f"/api/followups/{f['id']}/hand-over", json={"to_user_id": other["id"]})
    assert r.status_code == 200 and r.json()["taken_by"]["email"] == "cm2@a.test"
    # Now it is the colleague's: the first case manager can no longer finish it,
    assert cm.post(f"/api/followups/{f['id']}/done", json={"outcome": "booked"}).status_code == 409
    # and the colleague can.
    assert second_cm.post(f"/api/followups/{f['id']}/done", json={"outcome": "booked"}).status_code == 200
    history = world["db"][COLLECTION].find_one({})["history"]
    assert [h["action"] for h in history] == ["requested", "taken", "handed_over", "done"]


def test_only_the_holder_or_an_admin_can_reassign(world, second_cm):
    f = ask(world).json()
    client(world, "cm@a.test").post(f"/api/followups/{f['id']}/take")
    me = next(h for h in second_cm.get("/api/followups/handlers").json()["handlers"] if h["email"] == "cm2@a.test")
    assert second_cm.post(f"/api/followups/{f['id']}/hand-over", json={"to_user_id": me["id"]}).status_code == 409
    assert second_cm.post(f"/api/followups/{f['id']}/release").status_code == 409
    # The hospital admin steps in.
    admin = client(world, "admin@a.test")
    assert admin.post(f"/api/followups/{f['id']}/hand-over", json={"to_user_id": me["id"]}).status_code == 200


def test_put_back_in_the_queue(world):
    f = ask(world).json()
    cm = client(world, "cm@a.test")
    cm.post(f"/api/followups/{f['id']}/take")
    r = cm.post(f"/api/followups/{f['id']}/release")
    assert r.status_code == 200 and r.json()["status"] == "open" and r.json()["taken_by"] is None
    assert cm.post(f"/api/followups/{f['id']}/release").status_code == 409     # not taken now


def test_cannot_hand_over_to_someone_outside_the_hospital(world):
    f = ask(world).json()
    cm = client(world, "cm@a.test")
    cm.post(f"/api/followups/{f['id']}/take")
    doctor = client(world, "doc@a.test").get("/api/me").json()["id"]        # same hospital, not a case manager
    for target in (doctor, "000000000000000000000000"):
        assert cm.post(f"/api/followups/{f['id']}/hand-over", json={"to_user_id": target}).status_code == 422


def test_a_case_manager_takes_before_handing_over(world, second_cm):
    f = ask(world).json()
    other = next(h for h in second_cm.get("/api/followups/handlers").json()["handlers"] if h["email"] == "cm2@a.test")
    assert client(world, "cm@a.test").post(f"/api/followups/{f['id']}/hand-over",
                                           json={"to_user_id": other["id"]}).status_code == 409

# ------------------------------------------------- the bell after a hand-over
def bell(c):
    return c.get("/api/notifications").json()


def test_the_colleague_is_told_in_the_bell(world, second_cm):
    f = ask(world).json()
    cm = client(world, "cm@a.test")
    cm.post(f"/api/followups/{f['id']}/take")
    other = next(h for h in cm.get("/api/followups/handlers").json()["handlers"] if h["email"] == "cm2@a.test")
    cm.post(f"/api/followups/{f['id']}/hand-over", json={"to_user_id": other["id"]})

    b = bell(second_cm)
    handed = [i for i in b["items"] if "followup_handed" in i["kinds"]]
    assert b["counts"]["followup_handed"] == 1 and len(handed) == 1
    assert handed[0]["followup_id"] == f["id"] and handed[0]["handed_by"]
    assert bell(cm)["counts"]["followup_handed"] == 0                 # not the one who gave it away
    # Loading the list is not enough (the menu badge does that on every page);
    # the Follow-ups page says it has been seen.
    second_cm.get("/api/followups")
    assert bell(second_cm)["counts"]["followup_handed"] == 1
    assert second_cm.post("/api/followups/handed-seen").json() == {"seen": 1}
    assert bell(second_cm)["counts"]["followup_handed"] == 0
    assert client(world, "doc@a.test").post("/api/followups/handed-seen").status_code == 403


def test_the_alert_goes_when_the_request_is_finished_or_put_back(world, second_cm):
    cm = client(world, "cm@a.test")
    other = next(h for h in cm.get("/api/followups/handlers").json()["handlers"] if h["email"] == "cm2@a.test")
    for end in ("done", "release"):
        world["db"][COLLECTION].delete_many({})
        f = ask(world).json()
        cm.post(f"/api/followups/{f['id']}/take")
        cm.post(f"/api/followups/{f['id']}/hand-over", json={"to_user_id": other["id"]})
        body = {"outcome": "booked"} if end == "done" else None
        assert second_cm.post(f"/api/followups/{f['id']}/{end}", json=body).status_code == 200
        assert bell(second_cm)["counts"]["followup_handed"] == 0, end
        assert "handover_alert" not in world["db"][COLLECTION].find_one({})


def test_an_admin_sharing_out_work_alerts_the_case_manager(world):
    f = ask(world).json()
    me = next(h for h in client(world, "admin@a.test").get("/api/followups/handlers").json()["handlers"]
              if h["email"] == "cm@a.test")
    assert client(world, "admin@a.test").post(f"/api/followups/{f['id']}/hand-over",
                                              json={"to_user_id": me["id"]}).status_code == 200
    item = next(i for i in bell(client(world, "cm@a.test"))["items"] if i["followup_id"] == f["id"])
    assert item["kinds"] == ["followup_handed"]          # not also "new": it is taken now
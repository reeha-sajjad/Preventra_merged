"""The discharge plan (api/discharge_plan.py): a checklist that depends on the
patient, and social needs with the actions they call for.

Same accounts as test_isolation.py. A1 (Dr A's, hospital A) is a heart failure
patient; its worklist row's band is set per test.
"""
import pytest

from api import discharge_plan
from tests.units.test_isolation import A1, A2, A3, A4, B1, LATEST, as_user, main, world  # noqa: F401


def set_row(db, pid, band, group="heart_failure"):
    db["patient_worklist"].update_many({"patient_id": pid}, {"$set": {"risk_band": band, "clinical_group": group}})


def keys(plan):
    return [i["key"] for i in plan["items"]]


# ------------------------------------------------------------ the rules
def test_items_depend_on_band_and_living_alone():
    assert discharge_plan.items_for("general", "Low", {}) == [
        "followup_booked", "meds_reconciled", "warning_signs", "checkin_explained", "pharmacy_on_file",
        "social_screened"]
    assert "teach_back" in discharge_plan.items_for("general", "Medium", {})
    assert "contact_named" in discharge_plan.items_for("general", "High", {})
    assert "contact_named" in discharge_plan.items_for("general", "Low", {"lives_alone": "yes"})


@pytest.mark.parametrize("group, band, social, days", [
    ("heart_failure", "Low", {}, 7), ("renal", "Low", {}, 7), ("cardiac_other", "Low", {}, 7),
    ("respiratory", "Low", {}, 14), ("respiratory", "High", {}, 14),
    ("respiratory", "High", {"lives_alone": "yes"}, 7), ("general", "Medium", {"help_at_home": "no"}, 7),
    ("general", "Low", {"lives_alone": "yes"}, 14),
])
def test_how_soon_the_follow_up_should_be(group, band, social, days):
    assert discharge_plan.followup_days(group, band, social)[0] == days


def test_needs_come_with_their_actions():
    found = discharge_plan.needs({"lives_alone": "yes", "transport": "yes", "medicine_cost": "no",
                                  "housing": "unstable"})
    assert [n["key"] for n in found] == ["lives_alone", "medicine_cost", "housing"]
    assert all(n["action"] for n in found)


# ------------------------------------------------------------ the routes
def test_the_care_team_ticks_the_checklist(main, world):
    db = world["db"]
    set_row(db, A1, "High")
    doctor = as_user(main, world, "doc.a@a.test")
    plan = doctor.get(f"/api/patients/{A1}/discharge-plan").json()
    assert keys(plan) == ["followup_booked", "meds_reconciled", "warning_signs", "checkin_explained",
                          "teach_back", "contact_named", "pharmacy_on_file", "social_screened"]
    assert plan["items"][0]["label"] == "Follow-up appointment booked within 7 days"
    assert "Weigh every morning" in plan["items"][2]["hint"]
    assert plan["done"] == 0 and plan["can_edit"]

    plan = doctor.post(f"/api/patients/{A1}/discharge-plan/checklist",
                       json={"item": "followup_booked", "done": True}).json()
    first = plan["items"][0]
    assert first["done"] and first["by"] and first["at"] and plan["done"] == 1
    plan = doctor.post(f"/api/patients/{A1}/discharge-plan/checklist",
                       json={"item": "followup_booked", "done": False}).json()
    assert not plan["items"][0]["done"]
    assert doctor.post(f"/api/patients/{A1}/discharge-plan/checklist",
                       json={"item": "social_screened", "done": True}).status_code == 422
    assert doctor.post(f"/api/patients/{A1}/discharge-plan/checklist",
                       json={"item": "made_up", "done": True}).status_code == 422


def test_facts_on_file_tick_themselves(main, world):
    db = world["db"]
    set_row(db, A2, "High")                                     # A2 fills at pharmacy "bay"
    db["care_actions"].update_one({"patient_id": A2}, {"$set": {"caretaker_ids": ["someone"]}})
    nurse = as_user(main, world, "nurse@a.test")
    plan = nurse.get(f"/api/patients/{A2}/discharge-plan").json()
    by_key = {i["key"]: i for i in plan["items"]}
    assert by_key["pharmacy_on_file"]["done"] and by_key["pharmacy_on_file"]["auto"]
    assert by_key["contact_named"]["done"] and not by_key["contact_named"]["can_toggle"]
    assert nurse.post(f"/api/patients/{A2}/discharge-plan/checklist",
                      json={"item": "pharmacy_on_file", "done": False}).status_code == 409


def test_social_needs_change_the_plan(main, world):
    db = world["db"]
    set_row(db, A3, "Medium", group="respiratory")
    nurse = as_user(main, world, "nurse@a.test")
    plan = nurse.get(f"/api/patients/{A3}/discharge-plan").json()
    assert plan["followup_days"] == 14 and "contact_named" not in keys(plan)
    plan = nurse.post(f"/api/patients/{A3}/discharge-plan/social", json={"answers": {
        "lives_alone": "yes", "food": "no", "housing": "unknown"}}).json()
    assert plan["followup_days"] == 7 and "contact_named" in keys(plan)
    assert "lives alone" in plan["items"][0]["hint"]
    assert [n["key"] for n in plan["social"]["needs"]] == ["lives_alone", "food"]
    assert plan["social"]["answers"]["housing"] == "unknown" and plan["social"]["recorded_by"]
    assert {i["key"]: i["done"] for i in plan["items"]}["social_screened"]
    assert nurse.post(f"/api/patients/{A3}/discharge-plan/social",
                      json={"answers": {"lives_alone": "maybe"}}).status_code == 422
    assert nurse.post(f"/api/patients/{A3}/discharge-plan/social",
                      json={"answers": {"housing": "unknown"}}).status_code == 422


def test_a_new_discharge_starts_a_new_plan(main, world):
    db = world["db"]
    doctor = as_user(main, world, "doc.a@a.test")
    doctor.post(f"/api/patients/{A1}/discharge-plan/checklist", json={"item": "meds_reconciled", "done": True})
    db["patient_worklist"].update_many({"patient_id": A1}, {"$set": {"discharge_date": "2026-05-02"}})
    plan = doctor.get(f"/api/patients/{A1}/discharge-plan").json()
    assert plan["done"] == 0 and plan["discharge_date"] == "2026-05-02"
    assert db[discharge_plan.COLLECTION].count_documents({"patient_id": A1}) == 1     # the old one is kept


@pytest.mark.parametrize("email, status", [
    ("carer@home.test", 403),           # a caretaker opens the record, not the plan
    ("rx@bay.test", 404),               # not their patient
    ("admin@b.test", 404),              # another hospital's
    ("admin@a.test", 403),              # gives a reason first
])
def test_who_reads_the_plan(main, world, email, status):
    pid = A4 if email == "carer@home.test" else A1
    assert as_user(main, world, email).get(f"/api/patients/{pid}/discharge-plan").status_code == status


@pytest.mark.parametrize("email, status", [("me@patient.test", 403), ("claims@acme.test", 403),
                                           ("admin@b.test", 404)])
def test_who_changes_the_plan(main, world, email, status):
    pid = A4 if email == "me@patient.test" else A1
    r = as_user(main, world, email).post(f"/api/patients/{pid}/discharge-plan/checklist",
                                         json={"item": "meds_reconciled", "done": True})
    assert r.status_code == status
    assert world["db"][discharge_plan.COLLECTION].count_documents({}) == 0


def test_a_patient_reads_their_own_plan_but_cannot_edit_it(main, world):
    plan = as_user(main, world, "me@patient.test").get(f"/api/patients/{A4}/discharge-plan").json()
    assert plan["can_edit"] is False and not any(i["can_toggle"] for i in plan["items"])


# ------------------------------------------------------------ the patient's own items and questions
def post_item(client, pid, label):
    return client.post(f"/api/patients/{pid}/discharge-plan/items", json={"label": label})


def post_question(client, pid, label, kind="yes_no"):
    return client.post(f"/api/patients/{pid}/discharge-plan/checkin-questions", json={"label": label, "kind": kind})


def test_a_doctor_adds_and_removes_a_patients_own_item(main, world):
    db = world["db"]
    doctor = as_user(main, world, "doc.a@a.test")
    before = doctor.get(f"/api/patients/{A1}/discharge-plan").json()
    r = post_item(doctor, A1, "  Oxygen   delivered at home ")
    assert r.status_code == 200, r.text
    plan = r.json()
    mine = plan["items"][-1]
    assert mine["custom"] and mine["label"] == "Oxygen delivered at home" and mine["key"].startswith("ci_")
    assert mine["can_remove"] and mine["added_by"] and not mine["done"]
    assert plan["total"] == before["total"] + 1 and plan["can_customise"]
    assert plan["limits"] == {"items": 10, "questions": 5, "label": 150}
    assert not any(i["custom"] or i["can_remove"] for i in plan["items"][:-1])

    plan = doctor.post(f"/api/patients/{A1}/discharge-plan/checklist", json={"item": mine["key"], "done": True}).json()
    assert plan["done"] == before["done"] + 1 and plan["items"][-1]["done"] and plan["items"][-1]["by"]

    plan = doctor.delete(f"/api/patients/{A1}/discharge-plan/items/{mine['key']}").json()
    assert plan["total"] == before["total"] and not any(i["custom"] for i in plan["items"])
    stored = db[discharge_plan.COLLECTION].find_one({"patient_id": A1})
    assert mine["key"] not in (stored.get("checklist") or {}) and stored["custom_items"] == []


def test_custom_items_count_towards_complete(main, world):
    db = world["db"]
    set_row(db, A1, "Low", group="general")
    db["care_actions"].update_one({"patient_id": A1}, {"$set": {"pharmacy_id": "bay"}})
    doctor = as_user(main, world, "doc.a@a.test")
    doctor.post(f"/api/patients/{A1}/discharge-plan/social", json={"answers": {"lives_alone": "no"}})
    for key in ("followup_booked", "meds_reconciled", "warning_signs", "checkin_explained"):
        doctor.post(f"/api/patients/{A1}/discharge-plan/checklist", json={"item": key, "done": True})
    plan = post_item(doctor, A1, "Oxygen delivered at home").json()
    key = (A1, str(plan["discharge_date"]))
    assert plan["done"] == plan["total"] - 1
    assert discharge_plan.complete_for(db, [key]) == {key: False}
    row = db["patient_worklist"].find_one({"patient_id": A1})
    assert discharge_plan.summary(db, [row])["complete"] == 0

    doctor.post(f"/api/patients/{A1}/discharge-plan/checklist", json={"item": plan["items"][-1]["key"], "done": True})
    assert discharge_plan.complete_for(db, [key]) == {key: True}
    assert discharge_plan.summary(db, [row])["complete"] == 1


@pytest.mark.parametrize("email, status", [
    ("cm@a.test", 200), ("doc.a@a.test", 200), ("nurse@a.test", 403), ("admin@a.test", 403),
    ("me@patient.test", 403), ("claims@acme.test", 403), ("admin@b.test", 404),
])
@pytest.mark.parametrize("which", ["items", "checkin-questions"])
def test_who_customises_the_plan(main, world, email, status, which):
    pid = A4 if email == "me@patient.test" else A1
    r = as_user(main, world, email).post(f"/api/patients/{pid}/discharge-plan/{which}",
                                         json={"label": "Something to ask", "kind": "text"})
    assert r.status_code == status, r.text
    if status != 200:
        assert world["db"][discharge_plan.COLLECTION].count_documents({}) == 0


def test_nurse_ticks_a_custom_item_but_cannot_remove_it(main, world):
    item = post_item(as_user(main, world, "doc.a@a.test"), A1, "Oxygen delivered at home").json()["items"][-1]
    nurse = as_user(main, world, "nurse@a.test")
    assert nurse.delete(f"/api/patients/{A1}/discharge-plan/items/{item['key']}").status_code == 403
    plan = nurse.get(f"/api/patients/{A1}/discharge-plan").json()
    mine = plan["items"][-1]
    assert mine["key"] == item["key"] and mine["can_remove"] is False and mine["can_toggle"] is True
    assert plan["can_customise"] is False
    plan = nurse.post(f"/api/patients/{A1}/discharge-plan/checklist", json={"item": mine["key"], "done": True}).json()
    assert plan["items"][-1]["done"]


@pytest.mark.parametrize("which, payload, status", [
    ("items", {"label": ""}, 422), ("items", {"label": "ab"}, 422), ("items", {"label": "x" * 151}, 422),
    ("checkin-questions", {"label": "", "kind": "yes_no"}, 422),
    ("checkin-questions", {"label": "x" * 151, "kind": "text"}, 422),
    ("checkin-questions", {"label": "Any dizziness", "kind": "number"}, 422),
])
def test_custom_labels_are_checked(main, world, which, payload, status):
    r = as_user(main, world, "cm@a.test").post(f"/api/patients/{A1}/discharge-plan/{which}", json=payload)
    assert r.status_code == status
    assert world["db"][discharge_plan.COLLECTION].count_documents({}) == 0


def test_limits_duplicates_and_unknown_ids_are_refused(main, world):
    cm = as_user(main, world, "cm@a.test")
    for n in range(10):
        assert post_item(cm, A1, f"Own item number {n}").status_code == 200
    assert post_item(cm, A1, "One too many").status_code == 409
    assert post_item(cm, A1, "own ITEM number 3").status_code == 409                 # duplicate, any case
    for n in range(5):
        assert post_question(cm, A1, f"Own question {n}").status_code == 200
    assert post_question(cm, A1, "One too many").status_code == 409
    assert post_question(cm, A1, "OWN question 2").status_code == 409
    assert post_item(as_user(main, world, "doc.a@a.test"), A2, "Medicines reviewed and reconciled with the patient"
                     ).status_code == 409                                             # a built-in's wording
    for url in (f"items/ci_00000000", "items/followup_booked", "checkin-questions/cq_00000000"):
        assert cm.delete(f"/api/patients/{A1}/discharge-plan/{url}").status_code == 404


def test_a_new_discharge_drops_custom_items_and_questions(main, world):
    db = world["db"]
    doctor = as_user(main, world, "doc.a@a.test")
    post_item(doctor, A1, "Oxygen delivered at home")
    post_question(doctor, A1, "Any dizziness on stairs")
    db["patient_worklist"].update_many({"patient_id": A1}, {"$set": {"discharge_date": "2026-05-02"}})
    plan = doctor.get(f"/api/patients/{A1}/discharge-plan").json()
    assert not any(i["custom"] for i in plan["items"]) and plan["checkin_questions"] == []
    assert discharge_plan.checkin_questions(db, A1) == []
    assert db[discharge_plan.COLLECTION].count_documents({"patient_id": A1}) == 1     # the old one is kept

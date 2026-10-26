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

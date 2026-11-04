"""The summary page (api/patient_home.py): a caretaker reads it about the patient
by name, and both the patient and the caretaker see their case managers by name.
carer@home.test looks after A4; cm@a.test is hospital A's case manager."""
from api import auth, patient_home
from tests.units.test_isolation import A4, as_user, main, world  # noqa: F401


def name(world, email, full):
    auth.users(world["db"]).update_one({"email": email}, {"$set": {"name": full}})


def test_a_caretaker_reads_about_the_patient_by_name(main, world):
    name(world, "me@patient.test", "Sarah Khan")
    body = as_user(main, world, "carer@home.test").get("/api/my-home").json()
    assert body["caring_for"]["short_name"] == "Sarah"
    assert body["status"]["title"].startswith("Sarah") or "Sarah" in body["status"]["title"]
    assert "Your" not in body["status"]["title"] and "you left" not in body["status"]["text"]
    # The patient still reads "you".
    own = as_user(main, world, "me@patient.test").get("/api/my-home").json()
    assert "Sarah" not in own["status"]["title"]


def test_with_no_real_name_the_whole_label_is_used(main, world):
    body = as_user(main, world, "carer@home.test").get("/api/my-home").json()
    assert body["caring_for"]["short_name"] == f"Patient {A4}"
    assert patient_home.short_name("Demo Patient 1") == "Demo Patient 1"
    assert patient_home.short_name("Ali Raza") == "Ali"


def test_case_managers_are_named(main, world):
    name(world, "cm@a.test", "Hina Malik")
    for email in ("me@patient.test", "carer@home.test"):
        team = as_user(main, world, email).get("/api/my-home").json()["care_team"]
        assert team["case_managers"] == ["Hina Malik"], email
    # Once a case manager has taken the patient on, that one person is shown.
    world["db"]["care_actions"].update_one({"patient_id": A4}, {"$set": {"coordinator_name": "Omar Siddiqui"}})
    team = as_user(main, world, "me@patient.test").get("/api/my-home").json()["care_team"]
    assert team["case_managers"] == ["Omar Siddiqui"]

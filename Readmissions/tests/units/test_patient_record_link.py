"""Linking a patient login to its patient record (api/user_admin.link_patient_record).
me@patient.test is hospital A's patient login, linked to A4 by the fixture."""
from api import user_admin
from tests.units.test_isolation import A1, A4, B1, as_user, main, world  # noqa: F401


def uid(world, email):
    return world["ids"][email]


def link(main, world, actor, app, patient, who="me@patient.test"):
    return as_user(main, world, actor).post(f"/auth/admin/users/{uid(world, who)}/patient-record",
                                            json={"app": app, "patient": patient})


def test_a_hospital_admin_links_and_moves_a_patient_login(main, world):
    pid = uid(world, "me@patient.test")
    rows = as_user(main, world, "admin@a.test").get("/auth/admin/users?role=patient").json()
    assert [r["patient_records"] for r in rows if r["email"] == "me@patient.test"] == [{"readmissions": A4, "glp1": None}]
    # Move it to A1: A4 is unlinked, A1 linked, both remembered.
    assert link(main, world, "admin@a.test", "readmissions", A1).json() == {"readmissions": A1, "glp1": None}
    assert "patient_account_id" not in world["db"]["care_actions"].find_one({"patient_id": A4})
    assert world["db"]["care_actions"].find_one({"patient_id": A1})["patient_account_id"] == pid
    # GLP-1: the record in the same hospital.
    glp1 = world["db"].client[user_admin.GLP1_DB]["patient_access"]
    glp1.insert_many([{"patient_idx": 77, "hospital_id": "hosp-a"}, {"patient_idx": 88, "hospital_id": "hosp-b"}])
    assert link(main, world, "admin@a.test", "glp1", "#77").json()["glp1"] == 77
    assert link(main, world, "admin@a.test", "glp1", "88").status_code == 404        # another hospital's
    assert link(main, world, "admin@a.test", "glp1", "abc").status_code == 422
    # Unlinking.
    assert link(main, world, "admin@a.test", "glp1", "").json()["glp1"] is None
    assert len(glp1.find_one({"patient_idx": 77})["patient_account_history"]) == 2


def test_only_the_right_people_link_the_right_records(main, world):
    assert link(main, world, "admin@a.test", "readmissions", B1).status_code == 404   # hospital B's record
    assert link(main, world, "admin@b.test", "readmissions", A1).status_code == 404   # not their login
    for email in ("cm@a.test", "doc.a@a.test", "claims@acme.test", "me@patient.test"):
        assert link(main, world, email, "readmissions", A1).status_code in (403, 404), email
    # A record another login already opens is refused.
    world["db"]["care_actions"].update_one({"patient_id": A1}, {"$set": {"patient_account_id": "someone-else"}})
    assert link(main, world, "admin@a.test", "readmissions", A1).status_code == 409
    # Only patient logins.
    assert link(main, world, "admin@a.test", "readmissions", A1, who="cm@a.test").status_code == 409
    assert link(main, world, "ops@team.test", "readmissions", A4).status_code == 200
"""Emergency access - "break the glass" (api/emergency.py), against the real service.

Same accounts as test_isolation.py. doc.a@a.test looks after A1 and A2; A3 is
in hospital A but only on the nurse's list; A4 is hospital A with no care team;
B1 and B2 are hospital B. The review covers both apps, so a GLP-1 grant written
to the shared collection shows up in it too.
"""
from datetime import datetime, timedelta, timezone

import pytest

from api import access, access_log, auth
from tests.units.test_isolation import A1, A3, A4, B1, as_user, main, world  # noqa: F401


def grants(world):
    return world["db"].client[auth.IDENTITY_DB][access.EMERGENCY_COLLECTION]


@pytest.fixture(autouse=True)
def fresh(world):
    grants(world).delete_many({})
    yield
    grants(world).delete_many({})


def look(main, world, email, pid):
    return as_user(main, world, email).post("/api/emergency-access/lookup", json={"patient_id": pid})


def open_glass(main, world, email="doc.a@a.test", pid=A3, reason="emergency_treatment",
               note="Arrived in ED with chest pain"):
    return as_user(main, world, email).post("/api/emergency-access",
                                            json={"patient_id": pid, "reason": reason, "note": note})


# ------------------------------------------------------------ lookup
def test_lookup_says_only_whether_the_number_is_in_my_hospital(main, world):
    assert look(main, world, "doc.a@a.test", A1).json()["status"] == "on_your_list"
    body = look(main, world, "doc.a@a.test", A3).json()
    assert body["status"] == "available" and set(body) == {"patient_id", "status", "hours", "reasons"}
    assert look(main, world, "doc.a@a.test", B1).status_code == 404       # another hospital
    assert look(main, world, "doc.a@a.test", "MIMIC-0000").status_code == 404   # does not exist


@pytest.mark.parametrize("email", ["cm@a.test", "admin@a.test", "claims@acme.test", "me@patient.test"])
def test_only_doctors_and_nurses_break_the_glass(main, world, email):
    assert look(main, world, email, A3).status_code == 403
    assert open_glass(main, world, email).status_code == 403


# ------------------------------------------------------------- grant
def test_breaking_the_glass_opens_the_patient_and_logs_it(main, world):
    doc = as_user(main, world, "doc.a@a.test")
    assert doc.get(f"/api/patients/{A3}").status_code == 404
    r = open_glass(main, world)
    assert r.status_code == 201, r.text
    g = r.json()
    span = datetime.fromisoformat(g["expires_at"]) - datetime.fromisoformat(g["granted_at"])
    assert span == timedelta(hours=24) and g["active"]

    assert doc.get(f"/api/patients/{A3}").status_code == 200
    entries = list(access_log.collection(world["db"]).find({"patient_id": A3, "email": "doc.a@a.test"}))
    assert len(entries) == 1 and entries[0]["reason"] == access.EMERGENCY_REASON
    assert doc.get(f"/api/patients/{A3}/emergency").json()["grant"]["id"] == g["id"]
    assert doc.get(f"/api/patients/{A1}/emergency").json()["grant"] is None
    assert [x["patient_id"] for x in doc.get("/api/emergency-access/mine").json()["grants"]] == [A3]


def test_a_nurse_can_too(main, world):
    assert open_glass(main, world, "nurse@a.test", A4).status_code == 201
    assert as_user(main, world, "nurse@a.test").get(f"/api/patients/{A4}").status_code == 200


def test_a_reason_and_a_real_note_are_required(main, world):
    assert open_glass(main, world, reason="curious").status_code == 422
    assert open_glass(main, world, note="ER").status_code == 422
    assert grants(world).count_documents({}) == 0


def test_never_across_hospitals(main, world):
    assert open_glass(main, world, pid=B1).status_code == 404
    assert grants(world).count_documents({}) == 0


def test_not_for_a_patient_already_on_my_list(main, world):
    assert open_glass(main, world, pid=A1).status_code == 409


def test_the_grant_closes_on_its_own(main, world):
    open_glass(main, world)
    grants(world).update_many({}, {"$set": {"expires_at": datetime.now(timezone.utc) - timedelta(minutes=1)}})
    assert as_user(main, world, "doc.a@a.test").get(f"/api/patients/{A3}").status_code == 404


# ------------------------------------------------------------- review
def test_the_admin_reviews_both_apps_in_one_list(main, world):
    open_glass(main, world)
    now = datetime.now(timezone.utc)
    grants(world).insert_one({"app": "glp1", "patient_id": "12", "hospital_id": "hosp-a",
                              "user_id": "x", "email": "doc@glp1.test", "role": "doctor",
                              "reason": "covering", "reason_label": "Covering for the patient's care team",
                              "note": "Weekend cover", "granted_at": now,
                              "expires_at": now + timedelta(hours=24), "review": None})
    admin = as_user(main, world, "admin@a.test")
    body = admin.get("/auth/admin/emergency-access").json()
    assert body["to_review"] == 2 and {i["app"] for i in body["items"]} == {"glp1", "readmissions"}
    assert as_user(main, world, "admin@b.test").get("/auth/admin/emergency-access").json()["items"] == []

    gid = next(i["id"] for i in body["items"] if i["app"] == "readmissions")
    assert admin.post(f"/auth/admin/emergency-access/{gid}/review",
                      json={"outcome": "inappropriate"}).status_code == 422      # needs a note
    done = admin.post(f"/auth/admin/emergency-access/{gid}/review", json={"outcome": "appropriate"}).json()
    assert done["review"]["outcome"] == "appropriate" and done["review"]["by"]["email"] == "admin@a.test"
    assert admin.get("/auth/admin/emergency-access").json()["to_review"] == 1
    assert len(admin.get("/auth/admin/emergency-access?status=all").json()["items"]) == 2


def test_another_hospital_cannot_review(main, world):
    gid = open_glass(main, world).json()["id"]
    assert as_user(main, world, "admin@b.test").post(
        f"/auth/admin/emergency-access/{gid}/review", json={"outcome": "appropriate"}).status_code == 404


@pytest.mark.parametrize("email", ["doc.a@a.test", "cm@a.test", "nurse@a.test"])
def test_only_admins_review(main, world, email):
    assert as_user(main, world, email).get("/auth/admin/emergency-access").status_code == 403
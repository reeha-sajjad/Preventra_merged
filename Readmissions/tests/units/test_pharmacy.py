"""The pharmacy role (api/auth.py, api/access.py, api/user_admin.py), against the
real service. Same accounts as test_isolation.py: rx@bay.test works at the
"bay" pharmacy, which fills A2 (hospital A) and B2 (hospital B).

A pharmacy sees the patients who fill there, in any hospital, at the overview
layer only - no reason opens the clinical layer - and is placed in its pharmacy
by the superadmin.
"""
from api import access, auth
from tests.units.test_isolation import A1, A2, B2, as_user, main, world  # noqa: F401


def rx(main, world):
    return as_user(main, world, "rx@bay.test")


# ------------------------------------------------------------- what they see
def test_a_pharmacy_sees_the_overview_of_its_own_patients(main, world):
    body = rx(main, world).get("/api/patients?limit=50").json()
    assert {r["id"] for r in body["data"]} == {A2, B2} and body["redacted"]
    assert not any(set(r) & access.CLINICAL_FIELDS for r in body["data"])


def test_no_reason_opens_the_clinical_layer(main, world):
    c = rx(main, world)
    summary = c.get(f"/api/patients/{A2}/summary").json()
    assert summary["detail_access"] == "overview_only" and summary["reasons"] == []
    r = c.get(f"/api/patients/{A2}")
    assert r.status_code == 403 and r.json()["detail"]["code"] == "overview_only"
    assert c.post(f"/api/patients/{A2}/open", json={"reason": "care_coordination"}).status_code == 403
    assert c.get(f"/api/patients/{A2}").status_code == 403                       # still closed
    assert c.get(f"/api/patients/{A1}/summary").status_code == 404               # not theirs


def test_a_pharmacy_changes_nothing(main, world):
    c = rx(main, world)
    assert c.post(f"/api/patients/{A2}/notes", json={"text": "x", "author": "rx"}).status_code == 403
    assert c.get("/api/overview").status_code == 403


def test_a_pharmacy_without_a_placement_sees_nothing(main, world):
    users = auth.users(world["db"])
    users.update_one({"email": "rx@bay.test"}, {"$set": {"pharmacy_id": None}})
    assert rx(main, world).get("/api/patients?limit=50").json()["data"] == []


# ---------------------------------------------------------- user management
def test_the_superadmin_creates_a_pharmacy_and_places_its_staff(main, world):
    ops = as_user(main, world, "ops@team.test")
    assert ops.post("/auth/admin/pharmacies", json={"name": "Bay Street Pharmacy"}).status_code == 201
    assert {p["id"] for p in ops.get("/auth/admin/pharmacies").json()} == {"bay-street-pharmacy"}
    r = ops.post("/auth/admin/users", json={"email": "new@rx.test", "role": "pharmacy",
                                           "pharmacy_id": "bay-street-pharmacy"})
    assert r.status_code == 201 and r.json()["user"]["pharmacy_id"] == "bay-street-pharmacy"
    assert ops.post("/auth/admin/users", json={"email": "x@rx.test", "role": "pharmacy"}).status_code == 422
    assert ops.post("/auth/admin/users", json={"email": "y@rx.test", "role": "pharmacy",
                                              "pharmacy_id": "nowhere"}).status_code == 422


def test_a_hospital_admin_cannot_make_pharmacies_or_their_staff(main, world):
    admin = as_user(main, world, "admin@a.test")
    assert admin.post("/auth/admin/pharmacies", json={"name": "Mine"}).status_code == 403
    assert admin.post("/auth/admin/users", json={"email": "z@rx.test", "role": "pharmacy",
                                                "pharmacy_id": "bay"}).status_code == 403


def test_pharmacy_staff_can_ask_to_join_and_wait(main, world):
    out = auth.signup(world["db"], "counter@rx.test", "correct-horse", role="pharmacy")
    account = auth.users(world["db"]).find_one({"email": "counter@rx.test"})
    assert account["role"] == "pharmacy" and account["status"] == "pending"
    assert out["token"]
    # Approving it means choosing the pharmacy.
    world["db"].client[auth.IDENTITY_DB]["pharmacies"].insert_one({"_id": "bay", "name": "Bay"})
    ops = as_user(main, world, "ops@team.test")
    uid = str(account["_id"])
    assert ops.patch(f"/auth/admin/users/{uid}", json={"status": "active"}).status_code == 422
    done = ops.patch(f"/auth/admin/users/{uid}", json={"status": "active", "pharmacy_id": "bay"})
    assert done.status_code == 200 and done.json()["pharmacy_id"] == "bay"
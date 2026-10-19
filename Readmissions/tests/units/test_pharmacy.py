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

# ------------------------------------------------- step 3: home and reports
from api import followups  # noqa: E402


def report(main, world, pid=A2, problem="missed_pickup", urgency="routine", note="Didn't collect on Monday"):
    return rx(main, world).post(f"/api/patients/{pid}/pharmacy-report",
                                json={"problem": problem, "urgency": urgency, "note": note})


def weeks(world, pid, statuses):
    world["db"]["weekly_monitoring"].insert_many([
        {"patient_id": pid, "week_number": n, "observed": True, "risk_score": 50, "risk_band": "Low",
         "monitoring": {"refill_status": s, "adherence_pct": 80}} for n, s in enumerate(statuses, start=2)])


def test_the_pharmacy_home_lists_its_patients_with_their_refills(main, world):
    weeks(world, A2, ["collected", "missed", "missed"])
    body = rx(main, world).get("/api/pharmacy/home").json()
    rows = {p["id"]: p for p in body["patients"]}
    assert set(rows) == {A2, B2}
    assert rows[A2]["refill_latest"] == "missed" and rows[A2]["missed_recent"] == 2
    assert body["patients"][0]["id"] == A2                       # missed refills first
    assert not set(rows[A2]) & access.CLINICAL_FIELDS
    assert body["stats"]["missed"] == 1 and body["problems"]
    for email in ("cm@a.test", "doc.a@a.test", "admin@a.test", "claims@acme.test"):
        assert as_user(main, world, email).get("/api/pharmacy/home").status_code == 403


def test_opening_a_patient_shows_refills_and_is_logged(main, world):
    weeks(world, A2, ["late"])
    c = rx(main, world)
    body = c.get(f"/api/patients/{A2}/summary").json()
    assert body["refill_latest"] == "late"
    c.get(f"/api/patients/{A2}/summary")
    from api import access_log
    entries = list(access_log.collection(world["db"]).find({"email": "rx@bay.test"}))
    assert len(entries) == 1 and entries[0]["reason"] == "dispensing"
    # The log entry never opens the clinical layer.
    assert c.get(f"/api/patients/{A2}").status_code == 403
    assert "refill_latest" not in as_user(main, world, "admin@a.test").get(f"/api/patients/{A2}/summary").json()


def test_a_report_becomes_a_request_for_the_case_managers(main, world):
    r = report(main, world, urgency="urgent")
    assert r.status_code == 201 and r.json()["added_to_open"] is False
    doc = world["db"][followups.COLLECTION].find_one({"patient_id": A2})
    assert doc["status"] == "open" and doc["requested_by"]["role"] == "pharmacy" and doc["urgency"] == "urgent"
    listed = as_user(main, world, "cm@a.test").get("/api/followups").json()["items"]
    item = next(i for i in listed if i["patient_id"] == A2)
    assert item["pharmacy_reports"][0]["problem_label"] == "Missed a refill pickup"
    assert "by" not in item["pharmacy_reports"][0]
    home = rx(main, world).get("/api/pharmacy/home").json()
    assert home["stats"]["open_reports"] == 1 and home["reports"][0]["by_us"]


def test_with_a_request_already_open_the_report_is_added_and_notes_stay_hidden(main, world):
    doctor = as_user(main, world, "doc.a@a.test")
    assert doctor.post(f"/api/patients/{A2}/followup",
                       json={"urgency": "routine", "note": "Breathless on stairs"}).status_code == 201
    r = report(main, world, problem="side_effects", note="")
    assert r.status_code == 201 and r.json()["added_to_open"] is True
    assert world["db"][followups.COLLECTION].count_documents({"patient_id": A2}) == 1
    seen = rx(main, world).get(f"/api/patients/{A2}/followup").json()
    assert "Breathless" not in str(seen) and "Breathless" not in str(r.json())
    fid = str(world["db"][followups.COLLECTION].find_one({"patient_id": A2})["_id"])
    assert rx(main, world).post(f"/api/followups/{fid}/note", json={"text": "x"}).status_code == 403


def test_a_report_needs_a_real_problem_and_a_patient_of_ours(main, world):
    assert report(main, world, problem="nonsense").status_code == 422
    assert report(main, world, problem="other", note="").status_code == 422
    assert report(main, world, pid=A1).status_code == 404
    assert as_user(main, world, "doc.a@a.test").post(f"/api/patients/{A2}/pharmacy-report",
                                                     json={"problem": "missed_pickup"}).status_code == 403


def test_settings_names_the_pharmacy(main, world):
    assert rx(main, world).get("/api/me").json()["pharmacy"]["id"] == "bay"


# ------------------------------------------------------- the two bell alerts
def bell(main, world, email):
    return as_user(main, world, email).get("/api/followups/bell").json()["items"]


def test_a_report_on_a_request_someone_holds_rings_their_bell(main, world):
    cm = as_user(main, world, "cm@a.test")
    report(main, world)
    fid = str(world["db"][followups.COLLECTION].find_one({"patient_id": A2})["_id"])
    assert bell(main, world, "cm@a.test") == []                    # untaken: the waiting count has it
    assert cm.post(f"/api/followups/{fid}/take").status_code == 200
    report(main, world, problem="cost", note="")
    [item] = bell(main, world, "cm@a.test")
    assert item["kind"] == "followup_report" and item["followup_id"] == fid
    assert item["problem_label"] == "Said they can't afford it" and item["by"] == "bay"
    assert bell(main, world, "admin@a.test") == []                 # only the person holding it
    assert cm.post("/api/followups/reports-seen").json()["seen"] == 1
    assert bell(main, world, "cm@a.test") == []


def test_the_pharmacy_hears_when_its_report_is_done(main, world):
    report(main, world)
    fid = str(world["db"][followups.COLLECTION].find_one({"patient_id": A2})["_id"])
    assert bell(main, world, "rx@bay.test") == []
    done = as_user(main, world, "cm@a.test").post(f"/api/followups/{fid}/done",
                                                  json={"outcome": "booked", "note": "New pickup Thursday"})
    assert done.status_code == 200
    [item] = bell(main, world, "rx@bay.test")
    assert item["kind"] == "report_done" and item["outcome_label"] == "Booked"
    assert item["problem_label"] == "Missed a refill pickup" and "Thursday" not in str(item)
    assert as_user(main, world, "cm@a.test").post("/api/pharmacy/seen").status_code == 403
    assert rx(main, world).post("/api/pharmacy/seen").json()["seen"] == 1
    assert bell(main, world, "rx@bay.test") == []
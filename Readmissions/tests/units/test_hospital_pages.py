"""The hospital pages and the two layers of detail, against the real service.

Same two hospitals and nine accounts as test_isolation.py. Checked here:

  * hospital admins and insurers see the overview layer of their patients and
    open the clinical layer one patient at a time, with a reason, logged;
  * the access log is readable per hospital;
  * Overview and Staff count only the caller's patients, and only the roles
    they are for can open them;
  * care-team assignment takes effect at once and never crosses hospitals;
  * the superadmin's hospital picker narrows everything to one hospital;
  * the chatbot keeps to the same layers.
"""
import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from api import access, access_log, auth, chatbot_service
from tests.units.test_isolation import (A1, A2, A3, A4, ACCOUNTS, B1, B2, as_user,  # noqa: F401
                                        main, world)

DX, DRV = "DXMARK", "DRVMARK"     # markers planted in the clinical layer
REASON_EMAILS = ("admin@a.test", "claims@acme.test")
# Routes that answer with counts across a cohort, not with one patient's
# clinical layer. Allowed for everyone who can see the cohort.
AGGREGATE_ROUTES = {"/api/analytics/top-drivers", "/api/patient-groups"}
DETAIL_ROUTES = ("/api/patients/{pid}", "/api/patients/{pid}/trend", "/api/patients/{pid}/forecast",
                 "/api/patients/{pid}/care-actions", "/api/patients/{pid}/clinical-alerts",
                 "/api/patients/{pid}/edit")


@pytest.fixture
def marked(world):
    """The world, with the clinical layer made recognisable in any response."""
    world["db"]["patient_worklist"].update_many(
        {}, {"$set": {"primary_diagnosis": f"{DX} heart failure", "driver_1": f"{DRV} age"}})
    return world


def log(world, **query):
    return list(access_log.collection(world["db"]).find(query))


def ids(response) -> set:
    return {r["id"] for r in response.json().get("data", [])}


def login_again(main, world, email):
    token = auth.login(world["db"], email, "correct-horse")["token"]
    return TestClient(main.app, headers={"Authorization": f"Bearer {token}"},
                      raise_server_exceptions=False)


# ------------------------------------------------------ two layers of detail
@pytest.mark.parametrize("email", REASON_EMAILS)
def test_reason_roles_get_the_overview_layer_in_the_list(main, marked, email):
    body = as_user(main, marked, email).get("/api/patients", params={"limit": 100}).json()
    assert body["redacted"] is True and body["data"]
    for row in body["data"]:
        assert not access.CLINICAL_FIELDS & row.keys(), row.keys()
        assert {"doctor", "nurses", "insurer", "risk_score", "discharge_date"} <= row.keys()
    assert DX not in str(body) and DRV not in str(body)


@pytest.mark.parametrize("email", ["cm@a.test", "doc.a@a.test", "nurse@a.test", "ops@team.test"])
def test_everyone_else_gets_the_whole_row(main, marked, email):
    body = as_user(main, marked, email).get("/api/patients", params={"limit": 100}).json()
    assert body["redacted"] is False
    assert all(DX in r["primary_diagnosis"] for r in body["data"])


@pytest.mark.parametrize("email", REASON_EMAILS)
def test_no_route_shows_the_clinical_layer_before_a_reason(main, marked, email):
    client, seen = as_user(main, marked, email), {}
    for route in main.app.routes:
        if not (isinstance(route, APIRoute) and route.path.startswith("/api/")
                and "GET" in route.methods) or route.path in AGGREGATE_ROUTES:
            continue
        paths = ([route.path] if "{" not in route.path else
                 [route.path.replace("{patient_id}", pid) for pid in ACCOUNTS[email][3]]
                 if route.path.count("{") == 1 and "{patient_id}" in route.path else [])
        for path in paths:
            r = client.get(path)
            if DX in r.text or DRV in r.text:
                seen[path] = r.status_code
    assert seen == {}
    assert log(marked) == []                     # looking at the overview logs nothing


@pytest.mark.parametrize("email", REASON_EMAILS)
def test_detail_routes_ask_for_a_reason(main, marked, email):
    client, pid = as_user(main, marked, email), A1                 # both may see A1
    for path in DETAIL_ROUTES:
        r = client.get(path.format(pid=pid))
        assert r.status_code == 403 and r.json()["detail"]["code"] == "reason_required", path
    summary = client.get(f"/api/patients/{pid}/summary").json()
    assert summary["detail_access"] == "reason_required"
    assert {r["key"] for r in summary["reasons"]} == set(access.REASONS)


def test_a_reason_opens_one_patient_for_the_rest_of_the_sign_in(main, marked):
    client = as_user(main, marked, "admin@a.test")
    assert client.post(f"/api/patients/{A1}/open", json={"reason": "audit"}).json() == \
        {"patient_id": A1, "detail_access": "granted"}
    r = client.get(f"/api/patients/{A1}")
    assert r.status_code == 200 and DRV in r.text
    assert client.get(f"/api/patients/{A1}/summary").json()["detail_access"] == "granted"
    assert client.get(f"/api/patients/{A2}").status_code == 403            # only that patient

    entries = log(marked)
    assert len(entries) == 1
    e = entries[0]
    assert (e["email"], e["patient_id"], e["hospital_id"], e["reason"], e["app"]) == \
        ("admin@a.test", A1, "hosp-a", "audit", "readmissions")

    client.post(f"/api/patients/{A1}/open", json={"reason": "billing"})    # already open
    assert len(log(marked)) == 1
    # Signing in again is a new session: asked again.
    assert login_again(main, marked, "admin@a.test").get(f"/api/patients/{A1}").status_code == 403


def test_only_the_listed_reasons_are_accepted(main, marked):
    client = as_user(main, marked, "admin@a.test")
    assert client.post(f"/api/patients/{A1}/open", json={"reason": "curious"}).status_code == 422
    assert log(marked) == []


def test_opening_another_hospitals_patient_is_404_and_logs_nothing(main, marked):
    client = as_user(main, marked, "admin@a.test")
    assert client.post(f"/api/patients/{B1}/open", json={"reason": "audit"}).status_code == 404
    assert log(marked) == []


@pytest.mark.parametrize("email, reason, label", [
    ("doc.a@a.test", "treatment", "Treatment (care team)"),
    ("nurse@a.test", "treatment", "Treatment (care team)"),
    ("cm@a.test", "case_management", "Care coordination (case manager)"),
])
def test_the_care_team_is_never_asked_but_is_logged_once_per_sign_in(main, marked, email, reason, label):
    """Same rule as GLP-1 (GLP1/Backend/core/access.py AUTOMATIC_REASONS)."""
    client = as_user(main, marked, email)
    for _ in range(2):
        assert client.get(f"/api/patients/{A1}").status_code == 200
    client.get(f"/api/patients/{A1}/trend")
    assert client.get(f"/api/patients/{A1}/summary").json()["detail_access"] == "open"
    entries = log(marked)
    assert [(e["email"], e["patient_id"], e["reason"], e["reason_label"], e["hospital_id"]) for e in entries] == \
        [(email, A1, reason, label, "hosp-a")]


def test_a_care_team_list_or_summary_is_not_an_opening(main, marked):
    client = as_user(main, marked, "doc.a@a.test")
    client.get("/api/patients")
    client.get(f"/api/patients/{A1}/summary")
    assert log(marked) == []


def test_a_care_team_refusal_is_not_logged(main, marked):
    client = as_user(main, marked, "doc.a@a.test")
    assert client.get(f"/api/patients/{B1}").status_code == 404
    assert log(marked) == []


def test_the_reason_list_is_unchanged_and_matches_glp1():
    assert set(access.REASONS) == {"care_coordination", "incident_review", "audit", "billing"}
    assert not set(access.LOGGED_REASONS) & set(access.REASONS)


def test_the_superadmin_is_never_asked_but_is_logged_once_per_sign_in(main, marked):
    client = as_user(main, marked, "ops@team.test")
    for _ in range(3):
        assert client.get(f"/api/patients/{B1}").status_code == 200
    client.get(f"/api/patients/{B1}/trend")
    entries = log(marked)
    assert len(entries) == 1 and entries[0]["reason"] == access.SUPERADMIN_REASON
    assert entries[0]["hospital_id"] == "hosp-b"


def test_the_access_log_is_read_per_hospital(main, marked):
    as_user(main, marked, "admin@a.test").post(f"/api/patients/{A1}/open", json={"reason": "audit"})
    insurer = as_user(main, marked, "claims@acme.test")
    insurer.post(f"/api/patients/{A1}/open", json={"reason": "billing"})
    insurer.post(f"/api/patients/{B1}/open", json={"reason": "billing"})
    as_user(main, marked, "ops@team.test").get(f"/api/patients/{A2}")

    def read(email, **params):
        r = as_user(main, marked, email).get("/auth/admin/access-log", params=params)
        return r.status_code, r.json()

    status, entries = read("admin@a.test")
    assert status == 200
    assert {(e["email"], e["patient_id"]) for e in entries} == \
        {("admin@a.test", A1), ("claims@acme.test", A1), ("ops@team.test", A2)}
    assert all("session" not in e for e in entries)
    assert {(e["email"], e["patient_id"]) for e in read("admin@b.test")[1]} == {("claims@acme.test", B1)}
    # A hospital admin cannot widen its view by asking for another hospital.
    assert {e["hospital_id"] for e in read("admin@a.test", hospital_id="hosp-b")[1]} == {"hosp-a"}
    assert len(read("ops@team.test")[1]) == 4
    assert read("ops@team.test", hospital_id="hosp-b")[1][0]["patient_id"] == B1
    for email in ("doc.a@a.test", "cm@a.test", "claims@acme.test"):
        assert read(email)[0] == 403


def test_reason_roles_cannot_read_a_doctors_inbox(main, marked):
    for email in REASON_EMAILS:
        client = as_user(main, marked, email)
        assert client.get("/api/doctors/DR-A/alerts").status_code == 403
        assert client.get("/api/clinical-alerts/unrouted").status_code == 403
    assert as_user(main, marked, "doc.a@a.test").get("/api/doctors/DR-A/alerts").status_code == 200


def test_reason_roles_cannot_filter_their_way_to_a_diagnosis(main, marked):
    client = as_user(main, marked, "admin@a.test")
    assert ids(client.get("/api/patients", params={"q": "heart"})) == set()
    assert ids(client.get("/api/patients", params={"q": "1101"})) == {A1}
    assert len(ids(client.get("/api/patients", params={"group": "renal"}))) == 4   # ignored
    cm = as_user(main, marked, "cm@a.test")
    assert ids(cm.get("/api/patients", params={"group": "renal"})) == set()        # applied


# ----------------------------------------------------------------- overview
def test_the_overview_counts_the_accounts_own_patients(main, world):
    world["db"]["clinical_alerts"].update_many({"patient_id": {"$in": [A1, B1]}},
                                               {"$set": {"status": "pending"}})
    body = as_user(main, world, "admin@a.test").get("/api/overview").json()
    assert body["total_patients"] == 4
    assert body["no_doctor"] == 2 and body["no_nurse"] == 1         # A3, A4 / A4
    assert body["open_alerts"] == 4 + 1                              # risk alerts + A1's clinical
    assert {m["name"]: m["count"] for m in body["insurer_mix"]} == {"No insurer on file": 3, "acme": 1}
    assert body["staff"] == {"doctors": 1, "nurses": 1}
    assert sum(body["bands"].values()) == 4

    insurer = as_user(main, world, "claims@acme.test").get("/api/overview").json()
    assert insurer["total_patients"] == 2 and insurer["staff"] is None


@pytest.mark.parametrize("email", ["doc.a@a.test", "nurse@a.test", "me@patient.test"])
def test_doctors_nurses_and_patients_have_no_overview_or_staff_page(main, world, email):
    client = as_user(main, world, email)
    assert client.get("/api/overview").status_code == 403
    assert client.get("/api/staff").status_code == 403


# -------------------------------------------------------------------- staff
def test_the_staff_page_lists_the_hospitals_people_with_their_counts(main, world):
    world["db"]["clinical_alerts"].update_one({"patient_id": A1}, {"$set": {"status": "pending"}})
    body = as_user(main, world, "admin@a.test").get("/api/staff").json()
    doctors = {d["email"]: d for d in body["doctors"]}
    assert set(doctors) == {"doc.a@a.test"}
    d = doctors["doc.a@a.test"]
    assert (d["doctor_id"], d["patients"], d["high_risk"], d["open_alerts"], d["has_account"]) == \
        ("DR-A", 2, 0, 1, True)
    nurses = {n["email"]: n for n in body["nurses"]}
    assert set(nurses) == {"nurse@a.test"} and nurses["nurse@a.test"]["patients"] == 3
    assert body["no_doctor"] == 2 and body["no_nurse"] == 1
    # Registry-only doctors (no login yet) are listed, and only in their hospital.
    b = as_user(main, world, "admin@b.test").get("/api/staff").json()
    assert [(x["doctor_id"], x["has_account"], x["patients"]) for x in b["doctors"]] == [("DR-B", False, 1)]
    assert as_user(main, world, "claims@acme.test").get("/api/staff").status_code == 403


def test_the_staff_drill_down_filters_the_patient_list(main, world):
    client = as_user(main, world, "admin@a.test")
    nurse = world["ids"]["nurse@a.test"]
    assert ids(client.get("/api/patients", params={"doctor": "DR-A"})) == {A1, A2}
    assert ids(client.get("/api/patients", params={"nurse": nurse})) == {A1, A2, A3}
    assert ids(client.get("/api/patients", params={"unassigned": "doctor"})) == {A3, A4}
    assert ids(client.get("/api/patients", params={"doctor": "DR-B"})) == set()    # not theirs


# ---------------------------------------------------------------- care teams
def test_assigning_a_care_team_takes_effect_at_once(main, world):
    nurse = world["ids"]["nurse@a.test"]
    doctor = as_user(main, world, "doc.a@a.test")
    assert A3 not in ids(doctor.get("/api/patients"))
    r = as_user(main, world, "cm@a.test").post(
        "/api/care-team", json={"patient_ids": [A3, A4], "doctor_id": "DR-A", "add_nurse_ids": [nurse]})
    assert r.status_code == 200 and r.json()["updated"] == 2
    assert ids(doctor.get("/api/patients")) == {A1, A2, A3, A4}
    assert A4 in ids(as_user(main, world, "nurse@a.test").get("/api/patients"))
    # Replacing the nurses and clearing the doctor.
    as_user(main, world, "admin@a.test").post(
        "/api/care-team", json={"patient_ids": [A4], "doctor_id": "", "nurse_ids": []})
    assert ids(doctor.get("/api/patients")) == {A1, A2, A3}
    row = world["db"]["care_actions"].find_one({"patient_id": A4})
    assert row["assigned_doctor_id"] is None and row["assigned_nurse_ids"] == []


def test_assignment_never_crosses_hospitals(main, world):
    cm = as_user(main, world, "cm@a.test")
    before = list(world["db"]["care_actions"].find({}, {"_id": 0}))
    assert cm.post("/api/care-team", json={"patient_ids": [A3], "doctor_id": "DR-B"}).status_code == 404
    assert cm.post("/api/care-team", json={"patient_ids": [B1], "doctor_id": "DR-A"}).status_code == 404
    # One patient out of reach and the whole batch is refused.
    assert cm.post("/api/care-team", json={"patient_ids": [A3, B2], "doctor_id": "DR-A"}).status_code == 404
    ops = as_user(main, world, "ops@team.test")
    assert ops.post("/api/care-team", json={"patient_ids": [A3, B2], "doctor_id": ""}).status_code == 409
    assert list(world["db"]["care_actions"].find({}, {"_id": 0})) == before


@pytest.mark.parametrize("email", ["doc.a@a.test", "nurse@a.test", "claims@acme.test", "me@patient.test"])
def test_only_admins_and_case_managers_assign(main, world, email):
    r = as_user(main, world, email).post("/api/care-team", json={"patient_ids": [A1], "doctor_id": ""})
    assert r.status_code == 403


def test_a_nurse_from_another_hospital_cannot_be_assigned(main, world):
    other = str(auth.users(world["db"]).insert_one(
        {"email": "nurse@b.test", "role": "nurse", "status": "active", "hospital_id": "hosp-b",
         "password_hash": "x"}).inserted_id)
    r = as_user(main, world, "cm@a.test").post("/api/care-team",
                                               json={"patient_ids": [A3], "add_nurse_ids": [other]})
    assert r.status_code == 404


# ----------------------------------------------------------- hospital picker
def test_the_superadmin_can_look_at_one_hospital(main, world):
    ops = as_user(main, world, "ops@team.test")
    picked = {"X-Hospital-Id": "hosp-b"}
    assert ids(ops.get("/api/patients", headers=picked)) == {B1, B2}
    assert ops.get("/api/overview", headers=picked).json()["total_patients"] == 2
    assert [d["doctor_id"] for d in ops.get("/api/staff", headers=picked).json()["doctors"]] == ["DR-B"]
    assert ops.get(f"/api/patients/{A1}", headers=picked).status_code == 404
    assert len(ids(ops.get("/api/patients", params={"limit": 100}))) == 6          # all hospitals


def test_nobody_else_can_use_the_hospital_picker(main, world):
    admin = as_user(main, world, "admin@a.test")
    assert ids(admin.get("/api/patients", headers={"X-Hospital-Id": "hosp-b"})) == {A1, A2, A3, A4}


# ------------------------------------------------------------------ chatbot
def test_the_chatbot_keeps_to_the_layer(main, marked):
    user = auth.effective(auth.users(marked["db"]).find_one({"email": "admin@a.test"}))
    view = access.scoped(marked["db"], user)
    with pytest.raises(LookupError, match="give a reason"):
        chatbot_service._dispatch("get_patient_details", {"patient_id": A1}, view)
    with pytest.raises(LookupError, match="by risk and trend"):
        chatbot_service._dispatch("list_patients", {"fields": ["primary_diagnosis"]}, view)
    listed = chatbot_service._dispatch("list_patients", {"fields": ["current_band"]}, view)
    assert {p["patient_id"] for p in listed["patients"]} == {A1, A2, A3, A4}

    opened = access.ScopedDB(marked["db"], user, view.scope, opened={A1})
    assert DX in str(chatbot_service._dispatch("get_patient_details", {"patient_id": A1}, opened))
    with pytest.raises(LookupError):
        chatbot_service._dispatch("get_patient_drivers", {"patient_id": A2}, opened)

    doctor = auth.effective(auth.users(marked["db"]).find_one({"email": "doc.a@a.test"}))
    assert DX in str(chatbot_service._dispatch("get_patient_details", {"patient_id": A1},
                                               access.scoped(marked["db"], doctor)))


def test_a_refreshed_token_is_the_same_sign_in(main, marked):
    """The portal refreshes the token when it is revisited; that must not ask
    for the reason again. Signing in again must."""
    token = marked["tokens"]["admin@a.test"]
    client = as_user(main, marked, "admin@a.test")
    client.post(f"/api/patients/{A1}/open", json={"reason": "audit"})
    # A claim changes, as it does when an admin edits the account, so the
    # refreshed token is genuinely a different token.
    auth.users(marked["db"]).update_one({"email": "admin@a.test"},
                                        {"$set": {"app_access": ["readmissions", "glp1"]}})
    fresh = client.post("/auth/refresh", headers={"Authorization": f"Bearer {token}"}).json()["token"]
    assert fresh != token
    again = TestClient(main.app, headers={"Authorization": f"Bearer {fresh}"},
                       raise_server_exceptions=False)
    assert again.get(f"/api/patients/{A1}").status_code == 200
    assert login_again(main, marked, "admin@a.test").get(f"/api/patients/{A1}").status_code == 403
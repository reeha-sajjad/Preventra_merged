"""Hospital and role isolation, tested against the real service.

Two hospitals of patients, one account per role, and an exact list of which
patients each account may see. Every /api route is then called as every
account, and two things must hold:

  * a route about one patient answers 404 for a patient outside the account's
    set - the same answer as for a patient that does not exist;
  * no response, from any route, mentions a patient outside the set.

Routes are walked from api.main itself, so a route added later is covered
without anyone remembering to add it here.
"""
import json
import os
import re

import mongomock
import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from api import access, auth

SECRET = "test-secret-not-the-real-one"
LATEST, PRIOR = "2026-04-29", "2026-04-22"

A1, A2, A3, A4 = "MIMIC-1101", "MIMIC-1102", "MIMIC-1103", "MIMIC-1104"
B1, B2 = "MIMIC-2201", "MIMIC-2202"
EVERYONE = {A1, A2, A3, A4, B1, B2}

# account -> (role, hospital, extra fields, the patients it must see)
ACCOUNTS = {
    "ops@team.test":    ("superadmin", None, {}, EVERYONE),
    "admin@a.test":     ("hospital_admin", "hosp-a", {}, {A1, A2, A3, A4}),
    "cm@a.test":        ("case_manager", "hosp-a", {}, {A1, A2, A3, A4}),
    "doc.a@a.test":     ("doctor", "hosp-a", {}, {A1, A2}),
    "nurse@a.test":     ("nurse", "hosp-a", {}, {A1, A2, A3}),
    "claims@acme.test": ("insurer", None, {"insurer_id": "acme"}, {A1, B1}),
    "rx@bay.test":      ("pharmacy", None, {"pharmacy_id": "bay"}, {A2, B2}),
    "me@patient.test":  ("patient", "hosp-a", {}, {A4}),
    "carer@home.test":  ("caretaker", None, {}, {A4}),         # linked by A4
    "admin@b.test":     ("hospital_admin", "hosp-b", {}, {B1, B2}),
    "floating@x.test":  ("case_manager", None, {}, set()),     # approved, never placed
}


def patient_row(pid, batch, score):
    band = "High" if score >= 60 else "Low"
    return {"patient_id": pid, "batch_date": batch, "risk_score": score, "risk_band": band,
            "current_score": score, "current_band": band, "clinical_group": "heart_failure",
            "clinical_groups": ["heart_failure"], "primary_diagnosis": "Heart failure",
            "primary_icd_code": "I509", "admit_date": "2026-04-01", "discharge_date": "2026-04-10",
            "monitoring_status": "stable", "source": "mimic", "model_version": "test",
            "driver_1": "age", "driver_2": "prior_admissions", "driver_3": "los"}


@pytest.fixture(scope="module")
def main():
    os.environ.setdefault("MONGO_URI", "mongodb://127.0.0.1:1/?serverSelectionTimeoutMS=100")
    from api import main as service
    return service


@pytest.fixture
def world(main, monkeypatch):
    db = mongomock.MongoClient()["neuroshield"]
    monkeypatch.setattr(main, "db", db)
    monkeypatch.setattr(main, "API_KEY", None)
    monkeypatch.setattr(auth, "SHARED_SECRET_KEY", SECRET)
    monkeypatch.setattr(auth, "BCRYPT_ROUNDS", 4)       # nine logins per test; speed, not strength

    tokens, ids = {}, {}
    for email, (role, hospital, extra, _) in ACCOUNTS.items():
        doc = {"email": email, "password_hash": auth.hash_password("correct-horse"),
               "role": role, "status": "active", "hospital_id": hospital, **extra}
        ids[email] = str(auth.users(db).insert_one(doc).inserted_id)
        tokens[email] = auth.login(db, email, "correct-horse")["token"]

    db["doctors"].insert_many([
        {"doctor_id": "DR-A", "name": "Dr A", "email": "doc.a@a.test", "specialty": "Cardiology",
         "clinical_groups": ["heart_failure"], "active": True, "hospital_id": "hosp-a"},
        {"doctor_id": "DR-B", "name": "Dr B", "email": "doc.b@b.test", "specialty": "Cardiology",
         "clinical_groups": ["heart_failure"], "active": True, "hospital_id": "hosp-b"},
    ])
    nurse = ids["nurse@a.test"]
    owners = {
        A1: {"hospital_id": "hosp-a", "assigned_doctor_id": "DR-A", "assigned_nurse_ids": [nurse],
             "insurer_id": "acme"},
        A2: {"hospital_id": "hosp-a", "assigned_doctor_id": "DR-A", "assigned_nurse_ids": [nurse],
             "pharmacy_id": "bay"},
        A3: {"hospital_id": "hosp-a", "assigned_nurse_ids": [nurse]},
        A4: {"hospital_id": "hosp-a", "patient_account_id": ids["me@patient.test"],
             "caretaker_ids": [ids["carer@home.test"]]},
        B1: {"hospital_id": "hosp-b", "assigned_doctor_id": "DR-B", "insurer_id": "acme"},
        B2: {"hospital_id": "hosp-b", "pharmacy_id": "bay"},
    }
    for i, (pid, owner) in enumerate(owners.items()):
        score = 40 + i * 10
        db["patient_worklist"].insert_many([patient_row(pid, LATEST, score),
                                            patient_row(pid, PRIOR, score - 5)])
        db["care_actions"].insert_one({"patient_id": pid, "notes": [], "coordinator_name": None,
                                       "assigned_nurse_ids": [], **owner})
        db["weekly_monitoring"].insert_one({"patient_id": pid, "week_number": 1, "batch_date": LATEST,
                                            "risk_score": score, "risk_band": "Low"})
        db["alerts"].insert_one({"alert_id": f"AL-{pid}", "patient_id": pid, "acknowledged": False,
                                 "created_at": LATEST, "message": f"Alert for {pid}"})
        db["clinical_alerts"].insert_one({"alert_id": f"CA-{pid}", "patient_id": pid,
                                          "doctor_id": owner.get("assigned_doctor_id"),
                                          "status": "open", "created_at": LATEST,
                                          "clinical_group": "heart_failure"})
    # One summary for the whole population, as the refresh script writes today.
    db["executive_summary"].insert_one({"batch_date": LATEST, "total_patients": 6, "high_count": 3,
                                        "medium_count": 0, "low_count": 3, "wow_change": "0"})
    return {"db": db, "tokens": tokens, "ids": ids}


def as_user(main, world, email):
    return TestClient(main.app, headers={"Authorization": f"Bearer {world['tokens'][email]}"},
                      raise_server_exceptions=False)


def mentioned_text(text: str) -> set:
    return {pid for pid in EVERYONE if re.search(rf"\b{re.escape(pid)}\b", text)}


def mentioned(response) -> set:
    """Every test patient id that appears anywhere in a response."""
    text = response.text
    return {pid for pid in EVERYONE if re.search(rf"\b{re.escape(pid)}\b", text)}


def get_routes(main, with_param=None):
    for route in main.app.routes:
        if (isinstance(route, APIRoute) and route.path.startswith("/api/")
                and "GET" in route.methods):
            params = re.findall(r"\{([^}]+)\}", route.path)
            if (with_param is None and not params) or (with_param and params == [with_param]):
                yield route.path


# ------------------------------------------------------------ the core rule
@pytest.mark.parametrize("email", ACCOUNTS)
def test_the_patient_list_is_exactly_what_the_account_may_see(main, world, email):
    rows = as_user(main, world, email).get("/api/patients", params={"limit": 100}).json()
    shown = {r["id"] for r in rows.get("data", [])} if isinstance(rows, dict) else set()
    assert shown == ACCOUNTS[email][3]


@pytest.mark.parametrize("email", ACCOUNTS)
def test_no_list_route_mentions_a_patient_outside_the_account(main, world, email):
    client, allowed = as_user(main, world, email), ACCOUNTS[email][3]
    leaks = {}
    for path in get_routes(main):
        extra = mentioned(client.get(path)) - allowed
        if extra:
            leaks[path] = sorted(extra)
    assert leaks == {}


@pytest.mark.parametrize("email", ACCOUNTS)
def test_every_single_patient_route_hides_other_patients(main, world, email):
    client, allowed = as_user(main, world, email), ACCOUNTS[email][3]
    wrong = {}
    for path in get_routes(main, with_param="patient_id"):
        for pid in EVERYONE - allowed:
            r = client.get(path.replace("{patient_id}", pid))
            if r.status_code != 404 or mentioned(r) - allowed:
                wrong[f"{path} {pid}"] = r.status_code
    assert wrong == {}


@pytest.mark.parametrize("email", ["admin@a.test", "doc.a@a.test", "claims@acme.test"])
def test_a_doctor_inbox_outside_the_account_leaks_nothing(main, world, email):
    client, allowed = as_user(main, world, email), ACCOUNTS[email][3]
    for path in get_routes(main, with_param="doctor_id"):
        for doctor in ("DR-A", "DR-B"):
            assert mentioned(client.get(path.replace("{doctor_id}", doctor))) <= allowed, path


def test_the_summary_counts_only_the_accounts_patients(main, world):
    for email, (_, _, _, allowed) in ACCOUNTS.items():
        r = as_user(main, world, email).get("/api/summary")
        total = r.json().get("total_patients", 0) if r.status_code == 200 else 0
        assert total == len(allowed), email


# ----------------------------------------------- patient-specific actions
@pytest.mark.parametrize("path, body", [
    ("/api/patients/{pid}/notes", {"text": "hello", "author": "x"}),
    ("/api/patients/{pid}/assign", {"coordinator_name": "x"}),
    ("/api/patients/{pid}/discharge-plan/items", {"label": "hello there"}),
    ("/api/patients/{pid}/discharge-plan/checkin-questions", {"label": "hello there", "kind": "yes_no"}),
    ("/api/patients/{pid}/rescore", {"target": "discharge", "commit": True}),
    ("/api/patients/{pid}/rescore", {"target": "weekly", "commit": True}),
    ("/api/ai-insights", {"patient_id": "{pid}", "risk_score": 50, "risk_band": "Low"}),
    ("/api/week-narrative", {"patient_id": "{pid}", "week_number": 1, "risk_score": 50,
                             "risk_band": "Low"}),
])
def test_actions_on_another_hospitals_patient_are_404_and_write_nothing(main, world, path, body):
    before = world["db"]["care_actions"].count_documents({})
    client = as_user(main, world, "admin@a.test")
    filled = json.loads(json.dumps(body).replace("{pid}", B1))
    r = client.post(path.replace("{pid}", B1), json=filled)
    assert r.status_code == 404
    assert world["db"]["care_actions"].count_documents({}) == before


@pytest.mark.parametrize("email", ["claims@acme.test", "me@patient.test"])
def test_insurers_and_patients_cannot_change_records(main, world, email):
    client = as_user(main, world, email)
    pid = next(iter(ACCOUNTS[email][3]))
    assert client.post(f"/api/patients/{pid}/notes", json={"text": "x", "author": "x"}).status_code == 403


def test_alerts_of_another_hospital_cannot_be_acknowledged(main, world):
    client = as_user(main, world, "cm@a.test")                    # a role that may respond
    b_alert = str(world["db"]["alerts"].find_one({"patient_id": B1})["_id"])
    a_alert = str(world["db"]["alerts"].find_one({"patient_id": A1})["_id"])
    assert client.post(f"/api/alerts/{b_alert}/acknowledge").status_code == 404
    assert world["db"]["alerts"].find_one({"patient_id": B1})["acknowledged"] is False
    assert client.post(f"/api/alerts/{a_alert}/acknowledge").status_code == 200
    assert client.post(f"/api/clinical-alerts/CA-{B1}/acknowledge",
                       json={"doctor_id": "DR-A"}).status_code == 404


def test_the_doctor_registry_is_per_hospital(main, world):
    rows = as_user(main, world, "admin@a.test").get("/api/doctors").json()
    ids = {d["doctor_id"] for d in (rows if isinstance(rows, list) else rows.get("doctors", []))}
    assert ids == {"DR-A"}


# ------------------------------------------------------------- chatbot
@pytest.mark.parametrize("email", ["doc.a@a.test", "admin@b.test", "me@patient.test"])
def test_the_chatbot_only_queries_the_accounts_patients(main, world, email):
    from api import chatbot_queries as cq
    user = auth.effective(auth.users(world["db"]).find_one({"email": email}))
    view = access.scoped(world["db"], user)
    listed = cq.list_patients(view, limit=50)
    text = json.dumps(listed, default=str)
    assert {pid for pid in EVERYONE if pid in text} == ACCOUNTS[email][3]
    counted = cq.count_patients(view)
    assert str(len(ACCOUNTS[email][3])) in json.dumps(counted)


def test_score_history_is_the_weekly_series_for_a_monitored_patient(main, world):
    """The detail page's chart shows discharge then each monitored week, not one
    admission point - for the patient's own login too."""
    db = world["db"]
    db["weekly_monitoring"].delete_many({"patient_id": A4})
    db["weekly_monitoring"].insert_many([
        {"patient_id": A4, "week_number": w, "batch_date": LATEST, "risk_score": 30 + w * 2,
         "risk_band": "Low"} for w in (2, 0, 1)])
    body = as_user(main, world, "me@patient.test").get(f"/api/patients/{A4}").json()
    assert body["history"] == [{"week": "Discharge", "date": "", "score": 30.0},
                               {"week": "Wk 1", "date": "", "score": 32.0},
                               {"week": "Wk 2", "date": "", "score": 34.0}]


def test_a_discharge_with_no_weeks_since_is_one_point_not_loader_dates(main, world):
    """One week in the series used to fall back to the risk registry, whose
    dates are when a loader ran - the chart read "7/12, 7/14"."""
    db = world["db"]
    db["weekly_monitoring"].delete_many({"patient_id": A4})
    db["weekly_monitoring"].insert_one({"patient_id": A4, "week_number": 0, "risk_score": 44.9,
                                        "risk_band": "High", "week_date": "2026-10-15"})
    db["risk_registry"].insert_many([{"patient_id": A4, "batch_date": "2026-07-12", "risk_score": 12.0},
                                     {"patient_id": A4, "batch_date": "2026-07-14", "risk_score": 44.9}])
    body = as_user(main, world, "me@patient.test").get(f"/api/patients/{A4}").json()
    assert body["history"] == [{"week": "Discharge", "date": "2026-10-15", "score": 44.9}]


def test_the_care_plan_follows_the_trend_not_just_the_band(main, world):
    """A recovering patient is not told to arrange an urgent evaluation."""
    db = world["db"]
    me = as_user(main, world, "me@patient.test")
    db["patient_worklist"].update_many({"patient_id": A4}, {"$set": {
        "current_score": 41.1, "current_band": "High", "monitoring_status": "improving"}})
    plan = me.get(f"/api/patients/{A4}").json()["care_plan"]
    assert plan["decision"] == "continue" and "do not escalate" in plan["label"]
    assert not any("immediate" in a or "48 hours" in a for a in plan["actions"])
    db["patient_worklist"].update_many({"patient_id": A4}, {"$set": {
        "current_score": 70, "monitoring_status": "deteriorating"}})
    plan = me.get(f"/api/patients/{A4}").json()["care_plan"]
    assert plan["decision"] == "escalate" and any("48 hours" in a for a in plan["actions"])


def test_a_red_flag_in_the_latest_week_means_action_required(main, world):
    db = world["db"]
    db["weekly_monitoring"].delete_many({"patient_id": A4})
    db["weekly_monitoring"].insert_many([
        {"patient_id": A4, "week_number": 0, "risk_score": 60, "risk_band": "High"},
        {"patient_id": A4, "week_number": 1, "risk_score": 70, "risk_band": "High"},
        {"patient_id": A4, "week_number": 2, "risk_score": 62, "risk_band": "High",
         "red_flags": ["spo2"]}])
    body = as_user(main, world, "me@patient.test").get(f"/api/patients/{A4}/trend").json()
    assert body["monitoring_status"] == "action_required"
    assert body["weeks"][-1]["red_flags"] == ["oxygen saturation below 90%"]
    assert body["weeks"][0]["red_flags"] == []


def test_each_week_says_what_became_of_its_alert_for_the_care_team_only(main, world):
    """A red flag said "assess today" with no sign of whether anyone was told."""
    db = world["db"]
    db["weekly_monitoring"].delete_many({"patient_id": A1})
    db["weekly_monitoring"].insert_many([
        {"patient_id": A1, "week_number": 0, "risk_score": 60, "risk_band": "High"},
        {"patient_id": A1, "week_number": 1, "risk_score": 70, "risk_band": "High", "red_flags": ["spo2"]},
        {"patient_id": A1, "week_number": 2, "risk_score": 75, "risk_band": "High", "red_flags": ["spo2"]}])
    db["clinical_alerts"].insert_one({"alert_id": "AL-1", "patient_id": A1, "week_number": 2,
                                      "severity": "critical", "status": "pending", "doctor_name": "Dr A",
                                      "created_at": "2026-10-01 09:00:00"})
    weeks = as_user(main, world, "doc.a@a.test").get(f"/api/patients/{A1}/trend").json()["weeks"]
    assert weeks[1]["alert"] is None                                  # a red flag nobody was told about
    assert (weeks[2]["alert"]["doctor"], weeks[2]["alert"]["status"]) == ("Dr A", "pending")
    db["weekly_monitoring"].update_many({"patient_id": A1}, {"$set": {"patient_id": A4}})
    db["clinical_alerts"].update_one({"alert_id": "AL-1"}, {"$set": {"patient_id": A4}})
    for email in ("me@patient.test", "carer@home.test"):
        weeks = as_user(main, world, email).get(f"/api/patients/{A4}/trend").json()["weeks"]
        assert all("alert" not in w for w in weeks), email


# Every function the chatbot can call, with arguments that try to reach other
# patients: their ids by name, cohort-wide questions, and filters that would
# match everyone.
CHATBOT_CALLS = [
    ("count_patients", {}),
    ("count_patients_by_risk_band", {}),
    ("count_patients_by_risk_band", {"risk_band": "High"}),
    ("list_patients_by_risk_threshold", {"operator": ">=", "threshold": 0, "limit": 50}),
    ("get_top_risk_patients", {"n": 50}),
    ("get_risk_trend_over_time", {}),
    ("get_common_conditions", {}),
    ("get_common_diagnoses", {}),
    ("get_common_drivers", {}),
    ("get_condition_overlap", {}),
    ("get_risk_change_since_discharge", {"direction": "increase"}),
    ("get_risk_change_since_discharge", {"direction": "decrease"}),
    ("run_cohort_query", {"group_by": "patient_id", "top_n": 50}),
    ("run_cohort_query", {"filters": [{"field": "current_score", "op": "gte", "value": 0}]}),
    ("list_patients", {"limit": 50}),
    ("list_patients", {"filters": [{"field": "clinical_group", "op": "eq", "value": "heart_failure"}],
                       "limit": 50}),
    ("list_registered_doctors", {}),
    ("get_doctor_workload", {}),
    ("get_alert_routing", {}),
] + [(name, {"patient_id": pid}) for name in ("get_patient_details", "get_patient_drivers")
     for pid in sorted(EVERYONE)]


@pytest.mark.parametrize("name, args", CHATBOT_CALLS,
                         ids=[f"{n}-{a.get('patient_id', i)}" for i, (n, a) in enumerate(CHATBOT_CALLS)])
def test_a_patient_asking_the_chatbot_only_ever_hears_about_themselves(main, world, name, args):
    """Signed in as a patient, every chatbot function - however it is called -
    answers from that patient's own record or says there is nothing to find."""
    from api import chatbot_service
    user = auth.effective(auth.users(world["db"]).find_one({"email": "me@patient.test"}))
    view = access.scoped(world["db"], user)
    asked = args.get("patient_id")
    try:
        result = chatbot_service._dispatch(name, dict(args), view)
    except (LookupError, ValueError, KeyError) as refused:
        result = str(refused)
        if asked and asked != A4:
            # Someone else's id gets exactly the answer a made-up id gets, so
            # asking reveals nothing - not even that the patient exists.
            try:
                chatbot_service._dispatch(name, {"patient_id": "MIMIC-0000"}, view)
            except LookupError as missing:
                assert result == str(missing).replace("MIMIC-0000", asked)
            return
    assert mentioned_text(json.dumps(result, default=str)) <= {A4}, name
    if asked and asked != A4:
        raise AssertionError(f"{name} answered about {asked} for a patient account")
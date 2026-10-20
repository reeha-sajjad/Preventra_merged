"""Hospital and role isolation for GLP-1, over the real data.

The committed CSVs are loaded through scripts/migrate_csv_to_mongo.py into an
in-memory store - all 7,566 patients and every derived table - so the routes
run over what production serves rather than over a hand-built stand-in. Six
patients are given owners; the rest have none, which leaves them visible to
the superadmin only.
"""
import asyncio
import time
from pathlib import Path

import mongomock
import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
from jose import jwt
from mongomock_motor import AsyncMongoMockClient

import main
from core import chatbot_tools, loader, model, mongo
from core.access import patient_scope
from core.config import settings
from core.security import effective
from scripts import migrate_csv_to_mongo as mig

ALL = 7566
A, B = {0, 1, 2, 3}, {4, 5}

# account -> (role, hospital, extra fields, the patients it must see)
ACCOUNTS = {
    "ops@team.test":    ("superadmin", None, {}, None),                 # None: all 7,566
    "admin@a.test":     ("hospital_admin", "hosp-a", {}, A),
    "cm@a.test":        ("case_manager", "hosp-a", {}, A),
    "doc@a.test":       ("doctor", "hosp-a", {}, {0, 1}),
    "nurse@a.test":     ("nurse", "hosp-a", {}, {0, 1, 2}),
    "claims@acme.test": ("insurer", None, {"insurer_id": "acme"}, {0, 4}),
    "rx@bay.test":      ("pharmacy", None, {"pharmacy_id": "bay"}, {1, 5}),
    "me@patient.test":  ("patient", "hosp-a", {}, {3}),
    "carer@home.test":  ("caretaker", None, {}, {3}),            # linked by patient 3
    "admin@b.test":     ("hospital_admin", "hosp-b", {}, B),
    "floating@x.test":  ("case_manager", None, {}, set()),
}
COST_ROLES = {"superadmin", "insurer"}


@pytest.fixture(scope="module")
def world():
    store = mongomock.MongoClient()
    db = store[settings.mongodb_db_name]
    data = Path(settings.data_dir)
    df = mig.migrate_patients(db, data)
    mig.migrate_cost_effectiveness(db, data)
    mig.migrate_segment_profiles(db, df, list(db.cost_effectiveness.find()))
    mig.migrate_survival_checkpoints(db, data)
    mig.migrate_survival_curves(db, df)
    for step in (mig.migrate_progression_cost, mig.migrate_rebound_risk,
                 mig.migrate_rebound_trajectory, mig.migrate_rebound_sensitivity,
                 mig.migrate_payer_roi, mig.migrate_payer_roi_yearly):
        step(db, data)
    mig.migrate_model_meta(db)

    ids = {}
    for email, (role, hospital, extra, _) in ACCOUNTS.items():
        ids[email] = store[settings.shared_identity_db_name].users.insert_one(
            {"email": email, "role": role, "status": "active", "hospital_id": hospital,
             "app_access": ["glp1", "readmissions"], **extra}).inserted_id
    uid = {e: str(i) for e, i in ids.items()}
    db.patient_access.insert_many([
        {"patient_idx": 0, "hospital_id": "hosp-a", "insurer_id": "acme",
         "assigned_doctor_id": uid["doc@a.test"], "assigned_nurse_ids": [uid["nurse@a.test"]]},
        {"patient_idx": 1, "hospital_id": "hosp-a", "pharmacy_id": "bay",
         "assigned_doctor_id": uid["doc@a.test"], "assigned_nurse_ids": [uid["nurse@a.test"]]},
        {"patient_idx": 2, "hospital_id": "hosp-a", "assigned_nurse_ids": [uid["nurse@a.test"]]},
        {"patient_idx": 3, "hospital_id": "hosp-a", "patient_account_id": uid["me@patient.test"],
         "caretaker_ids": [uid["carer@home.test"]]},
        {"patient_idx": 4, "hospital_id": "hosp-b", "insurer_id": "acme"},
        {"patient_idx": 5, "hospital_id": "hosp-b", "pharmacy_id": "bay"},
    ])

    previous = mongo._client
    mongo._client = AsyncMongoMockClient(mock_mongo_client=store)
    loader.load_binary_artifacts()
    asyncio.run(model.init_startup_caches())
    tokens = {e: jwt.encode({"sub": uid[e], "exp": int(time.time()) + 3600},
                            settings.shared_secret_key, algorithm="HS256") for e in ACCOUNTS}
    yield {"db": db, "tokens": tokens, "store": store}
    mongo._client = previous


def client(world, email):
    return TestClient(main.app, headers={"Authorization": f"Bearer {world['tokens'][email]}"},
                      raise_server_exceptions=False)


def allowed(email):
    return ACCOUNTS[email][3]


def idx_in(payload) -> set:
    """Every patient_idx anywhere in a response."""
    found = set()
    if isinstance(payload, dict):
        for k, v in payload.items():
            if k == "patient_idx" and isinstance(v, int):
                found.add(v)
            else:
                found |= idx_in(v)
    elif isinstance(payload, list):
        for v in payload:
            found |= idx_in(v)
    return found


# ------------------------------------------------------------ the core rule
@pytest.mark.parametrize("email", ACCOUNTS)
def test_the_patient_list_is_exactly_what_the_account_may_see(world, email):
    body = client(world, email).get("/api/patients", params={"page_size": 10000}).json()
    shown = {p["patient_idx"] for p in body["patients"]}
    if allowed(email) is None:
        assert len(shown) == body["total"] == ALL
    else:
        assert shown == allowed(email) and body["total"] == len(allowed(email))


@pytest.mark.parametrize("email", ACCOUNTS)
def test_no_list_route_mentions_a_patient_outside_the_account(world, email):
    if allowed(email) is None:
        pytest.skip("the superadmin may see every patient")
    c, leaks = client(world, email), {}
    for route in main.app.routes:
        if (isinstance(route, APIRoute) and route.path.startswith("/api/")
                and "GET" in route.methods and "{" not in route.path):
            r = c.get(route.path)
            extra = idx_in(r.json()) - allowed(email) if r.status_code == 200 else set()
            if extra:
                leaks[route.path] = sorted(extra)[:5]
    assert leaks == {}


@pytest.mark.parametrize("email", [e for e in ACCOUNTS if e != "ops@team.test"])
def test_single_patient_routes_hide_everyone_else(world, email):
    c = client(world, email)
    for idx in sorted((A | B | {6, 7000}) - allowed(email)):
        for path in (f"/api/patients/{idx}", f"/api/patients/{idx}/pharmacy-view"):
            assert c.get(path).status_code == 404, path
    for idx in allowed(email):
        # Hospital admins and insurers open the clinical layer with a reason
        # (tests/test_hospital_pages.py); for them the record is there, but
        # behind the prompt. A pharmacy never gets it (tests/test_pharmacy.py);
        # a caretaker does, for the patients who linked them, logged
        # (tests/test_caretakers.py).
        expected = 403 if ACCOUNTS[email][0] in ("hospital_admin", "insurer", "pharmacy") else 200
        assert c.get(f"/api/patients/{idx}").status_code == expected


def test_the_summary_counts_only_the_accounts_patients(world):
    for email in ACCOUNTS:
        kpis = client(world, email).get("/api/summary").json()["kpis"]
        assert kpis["total_patients"] == (ALL if allowed(email) is None else len(allowed(email))), email


def test_the_superadmin_summary_is_unchanged(world):
    """Cost figures use the caller's own per-segment counts and adherence
    (core/scoped.py); for the superadmin that is every patient - the same
    figures the stored table was computed from."""
    kpis = client(world, "ops@team.test").get("/api/summary").json()["kpis"]
    cea = list(world["db"].cost_effectiveness.find())
    pts = list(world["db"].patients.find({}, {"cluster": 1, "is_adherent": 1}))
    n = {c: sum(1 for p in pts if p["cluster"] == c) for c in range(4)}
    adh = {c: sum(p.get("is_adherent") or 0 for p in pts if p["cluster"] == c) / max(n[c], 1) for c in range(4)}
    expected_avg = sum(d.get("annual_cost", 0) * n[int(d["cluster"])] for d in cea) / ALL
    expected_waste = sum(round(d.get("annual_cost", 0) * (1 - round(adh[int(d["cluster"])], 4)), 2) * n[int(d["cluster"])]
                         for d in cea)
    assert kpis["avg_annual_cost"] == round(expected_avg)
    assert kpis["wasted_spend_annual"] == round(expected_waste)


def test_only_the_cost_roles_get_drug_spend_in_the_summary(world):
    for email, (role, *_rest) in ACCOUNTS.items():
        kpis = client(world, email).get("/api/summary").json()["kpis"]
        assert (kpis["avg_annual_cost"] is None) == (role not in COST_ROLES), email
        assert (kpis["wasted_spend_annual"] is None) == (role not in COST_ROLES), email


# --------------------------------------------------------- cost and ROI
COST_ROUTES = ["/api/cost-effectiveness", "/api/consequence/downstream-cost",
               "/api/consequence/rebound-risk", "/api/consequence/payer-scenarios",
               "/api/consequence/payer-roi"]


@pytest.mark.parametrize("email", ACCOUNTS)
def test_cost_screens_are_for_the_roles_that_own_the_budget(world, email):
    c, role = client(world, email), ACCOUNTS[email][0]
    for path in COST_ROUTES:
        assert (c.get(path).status_code == 403) == (role not in COST_ROLES), path
    budget = c.post("/api/budget-impact", json={"dropout_reduction_pct": 10})
    assert (budget.status_code == 403) == (role not in COST_ROLES)


def test_an_insurers_cost_views_add_up_only_its_members(world):
    c = client(world, "claims@acme.test")
    assert c.get("/api/consequence/downstream-cost").json()["n_patients_total"] == 2
    assert c.get("/api/consequence/rebound-risk").json()["n_patients_total"] == 2
    full = client(world, "ops@team.test").get("/api/consequence/downstream-cost").json()
    assert full["n_patients_total"] == ALL


# ------------------------------------------------------------- chatbot
def ctx_for(world, email):
    user = effective(world["store"][settings.shared_identity_db_name].users.find_one({"email": email}))
    return chatbot_tools.ToolContext(user=user, scope=asyncio.run(patient_scope(user)))


@pytest.mark.parametrize("email", ["doc@a.test", "claims@acme.test", "me@patient.test"])
def test_chatbot_tools_only_reach_the_accounts_patients(world, email):
    ctx = ctx_for(world, email)
    result, err = asyncio.run(chatbot_tools.dispatch_tool("search_patients", {"page_size": 50}, ctx))
    assert err is None and {p["patient_idx"] for p in result["patients"]} == allowed(email)
    outsider = min(B - allowed(email)) if B - allowed(email) else 7000
    result, err = asyncio.run(chatbot_tools.dispatch_tool("get_patient", {"patient_idx": outsider}, ctx))
    assert err is not None and "not found" in err.lower()


def test_chatbot_refuses_cost_tools_to_care_roles(world):
    result, err = asyncio.run(chatbot_tools.dispatch_tool("get_payer_roi", {}, ctx_for(world, "doc@a.test")))
    assert err and "insurers only" in err


def test_the_chatbot_snapshot_is_per_account(world):
    doctor = asyncio.run(chatbot_tools.build_snapshot(ctx_for(world, "doc@a.test")))
    admin_b = asyncio.run(chatbot_tools.build_snapshot(ctx_for(world, "admin@b.test")))
    assert "Total patients: **2**" in doctor and "Total patients: **2**" in admin_b
    everyone = asyncio.run(chatbot_tools.build_snapshot(ctx_for(world, "ops@team.test")))
    assert f"Total patients: **{ALL:,}**" in everyone
    # Drug spend only for the cost roles.
    assert "wasted spend" not in doctor and "wasted spend" not in admin_b and "wasted spend" in everyone


def test_a_chatbot_conversation_belongs_to_its_account(world):
    from routers import chatbot
    chatbot._SESSIONS["sess-doc"] = {"messages": [{"role": "user", "content": "about patient 0"}],
                                     "touched": time.monotonic(),
                                     "owner": effective(world["store"][settings.shared_identity_db_name]
                                                        .users.find_one({"email": "doc@a.test"}))["id"]}
    assert client(world, "admin@b.test").get("/api/chatbot/session/sess-doc").status_code == 404
    assert client(world, "admin@b.test").delete("/api/chatbot/session/sess-doc").json()["cleared"] is False
    assert client(world, "doc@a.test").get("/api/chatbot/session/sess-doc").status_code == 200


def test_an_account_with_no_patients_reports_no_dropout(world):
    kpis = client(world, "floating@x.test").get("/api/summary").json()["kpis"]
    assert kpis["total_patients"] == 0 and kpis["dropout_rate"] == 0.0
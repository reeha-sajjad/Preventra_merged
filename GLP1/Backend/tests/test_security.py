"""Every GLP-1 /api route admits only active, signed-in users, and reads the
account from the database on each request.

The identity store is a small in-memory fake: GLP-1 talks to Mongo through
motor (async), which mongomock cannot stand in for.
"""
import re
import time

import pytest
from bson import ObjectId
from fastapi import HTTPException
from fastapi.routing import APIRoute
from fastapi.security import HTTPAuthorizationCredentials
from fastapi.testclient import TestClient
from jose import jwt

import main
from core import security
from core.config import settings
from routers.chatbot import _audience


class FakeUsers:
    def __init__(self):
        self.docs = {}

    async def find_one(self, query):
        return self.docs.get(query["_id"])


class FakeIdentityDB:
    def __init__(self):
        self.users = FakeUsers()


@pytest.fixture
def identity(monkeypatch):
    fake = FakeIdentityDB()
    monkeypatch.setattr(security, "get_shared_identity_db", lambda: fake)
    return fake


def add_user(identity, **fields):
    doc = {"_id": ObjectId(), "email": "a@b.com", "role": "doctor", "status": "active",
           "hospital_id": "demo-hospital-a", "app_access": ["glp1", "readmissions"], **fields}
    identity.users.docs[doc["_id"]] = doc
    return doc


def token_for(doc, exp_in=3600, secret=None):
    """A token as the Readmissions auth service would issue it."""
    return jwt.encode({"sub": str(doc["_id"]), "email": doc["email"], "role": "doctor",
                       "exp": int(time.time()) + exp_in},
                      secret or settings.shared_secret_key, algorithm="HS256")


async def call(token):
    return await security.current_user(HTTPAuthorizationCredentials(scheme="Bearer",
                                                                    credentials=token))


# ----------------------------------------------------------- current_user
async def test_an_active_user_is_admitted_with_their_database_record(identity):
    doc = add_user(identity)
    user = await call(token_for(doc))
    assert (user["role"], user["hospital_id"]) == ("doctor", "demo-hospital-a")
    assert "password_hash" not in user


async def test_a_role_change_applies_to_the_very_next_request(identity):
    """The token was issued for a doctor; the record now says nurse. The
    record wins, without anyone signing in again."""
    doc = add_user(identity)
    token = token_for(doc)
    doc["role"] = "nurse"
    assert (await call(token))["role"] == "nurse"


async def test_a_pending_account_is_refused(identity):
    doc = add_user(identity, status="pending", hospital_id=None)
    with pytest.raises(HTTPException) as e:
        await call(token_for(doc))
    assert e.value.status_code == 403


async def test_a_deleted_account_is_refused_even_with_an_unexpired_token(identity):
    doc = add_user(identity)
    token = token_for(doc)
    del identity.users.docs[doc["_id"]]
    with pytest.raises(HTTPException) as e:
        await call(token)
    assert e.value.status_code == 401


async def test_an_account_still_on_a_temporary_password_is_refused(identity):
    doc = add_user(identity, must_change_password=True)
    with pytest.raises(HTTPException) as e:
        await call(token_for(doc))
    assert e.value.status_code == 403


async def test_an_account_without_glp1_access_is_refused(identity):
    doc = add_user(identity, app_access=["readmissions"])
    with pytest.raises(HTTPException) as e:
        await call(token_for(doc))
    assert e.value.status_code == 403


@pytest.mark.parametrize("typed_role", ["Doctor", "superadmin", "hospital_admin"])
async def test_a_role_typed_in_before_fixed_roles_is_never_trusted(identity, typed_role):
    """Legacy accounts have no status field and a free-text role."""
    doc = add_user(identity, role=typed_role, hospital_id="city-hospital")
    del doc["status"]
    user = await call(token_for(doc))
    assert (user["role"], user["status"], user["hospital_id"]) == ("case_manager", "active", None)


@pytest.mark.parametrize("make_token", [
    lambda doc: token_for(doc, exp_in=-1),                      # expired
    lambda doc: token_for(doc, secret="another-secret"),        # forged
    lambda doc: jwt.encode({"sub": str(doc["_id"])}, settings.shared_secret_key,
                           algorithm="HS256"),                  # no expiry
    lambda doc: jwt.encode({"sub": "not-an-id", "exp": int(time.time()) + 60},
                           settings.shared_secret_key, algorithm="HS256"),
])
async def test_unusable_tokens_are_refused(identity, make_token):
    doc = add_user(identity)
    with pytest.raises(HTTPException) as e:
        await call(make_token(doc))
    assert e.value.status_code == 401


def test_the_role_list_matches_the_auth_service():
    """GLP-1 keeps a copy of Readmissions/api/auth.py:ROLES. If this fails, one
    side added or renamed a role without the other."""
    assert security.ROLES == ("superadmin", "hospital_admin", "doctor", "nurse",
                              "case_manager", "insurer", "pharmacy", "patient")


# ------------------------------------------------------- the whole service
def api_routes():
    for route in main.app.routes:
        if isinstance(route, APIRoute) and route.path.startswith("/api/"):
            for method in route.methods:
                yield method, re.sub(r"\{[^}]+\}", "1", route.path)


def test_every_api_route_refuses_a_request_without_a_token():
    client = TestClient(main.app)
    routes = list(api_routes())
    assert len(routes) >= 18                          # the walk found the real routes
    let_through = [(m, p) for m, p in routes if client.request(m, p).status_code not in (401, 403)]
    assert let_through == []


def test_every_api_route_refuses_a_pending_account(identity):
    doc = add_user(identity, status="pending", hospital_id=None)
    client = TestClient(main.app, headers={"Authorization": f"Bearer {token_for(doc)}"})
    let_through = [(m, p) for m, p in api_routes() if client.request(m, p).status_code != 403]
    assert let_through == []


def test_health_stays_open():
    assert TestClient(main.app).get("/health").status_code == 200


# ---------------------------------------------------------------- chatbot
@pytest.mark.parametrize("role, audience", [
    ("superadmin", "insurer"), ("hospital_admin", "case_manager"), ("insurer", "insurer"),
    ("doctor", "case_manager"), ("nurse", "case_manager"),
    ("case_manager", "case_manager"), ("patient", "case_manager"),
])
def test_the_chatbot_audience_comes_from_the_account(role, audience):
    assert _audience({"role": role}) == audience
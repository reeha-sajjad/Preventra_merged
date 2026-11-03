"""Who pays for a patient (api/patient_insurer.py): billing staff set or change a
patient's insurer, the insurer then sees them, and an old insurer loses them.

Same accounts as test_isolation.py: A1 (hospital A) and B1 (hospital B) start
with "acme"; claims@acme.test works for acme; A2 and A3 have no insurer."""
import pytest

from api import auth
from tests.units.test_isolation import A1, A2, A3, B1, as_user, main, world  # noqa: F401
from tests.units.test_manual_patients import FORM, csv_file, fake_model  # noqa: F401


@pytest.fixture
def insurers(world):  # noqa: F811
    world["db"].client[auth.IDENTITY_DB]["insurers"].insert_many([
        {"_id": "acme", "name": "Acme Health"},
        {"_id": "medicare", "name": "Medicare", "member_id_format": "mbi"}])
    return world


def test_the_happy_flow_a_new_patient_reaches_their_insurer(main, insurers):  # noqa: F811
    world = insurers
    claims = as_user(main, world, "claims@acme.test")
    assert claims.get(f"/api/patients/{A2}/insurance").status_code == 404          # not theirs yet

    cm = as_user(main, world, "cm@a.test")
    card = cm.get(f"/api/patients/{A2}/insurance").json()
    assert card["insurer"] is None and card["can_set_insurer"] is True
    r = cm.put(f"/api/patients/{A2}/insurer", json={"insurer": "acme", "member_id": "acm-2024"})
    assert r.status_code == 200, r.text
    assert r.json()["insurer"]["name"] == "Acme Health" and r.json()["member_id"] == "ACM2024"

    # The insurer now has them: their card, and their Members page.
    assert claims.get(f"/api/patients/{A2}/insurance").json()["member_id"] == "ACM2024"
    assert sum(claims.get("/api/insurer/home").json()["bands"].values()) == 3        # A1, B1 and now A2
    assert claims.get("/api/member-lookup?member_id=ACM2024").json()["matches"][0]["patient_id"] == A2


def test_changing_insurer_takes_the_old_one_off_at_once(main, insurers):  # noqa: F811
    world = insurers
    db = world["db"]
    db["care_actions"].update_one({"patient_id": A1}, {"$set": {"insurer_member_id": "ACM111"}})
    claims = as_user(main, world, "claims@acme.test")
    assert claims.get(f"/api/patients/{A1}/insurance").status_code == 200

    admin = as_user(main, world, "admin@a.test")
    r = admin.put(f"/api/patients/{A1}/insurer", json={"insurer": "medicare"})   # by name works too
    assert r.status_code == 200, r.text
    assert r.json()["insurer"]["id"] == "medicare"
    assert r.json()["member_id"] is None and r.json()["can_edit"] is True         # old card's ID is gone

    assert claims.get(f"/api/patients/{A1}/insurance").status_code == 404
    assert sum(claims.get("/api/insurer/home").json()["bands"].values()) == 1        # only B1 left
    doc = db["care_actions"].find_one({"patient_id": A1})
    assert doc["insurer_member_id_history"][-1]["from"] == "ACM111"
    history = admin.get(f"/api/patients/{A1}/insurer-history").json()["history"]
    assert history[0]["from"] == "Acme Health" and history[0]["to"] == "Medicare"
    assert history[0]["member_id_cleared"] is True


def test_member_ids_are_still_checked_when_setting_an_insurer(main, insurers):  # noqa: F811
    world = insurers
    cm = as_user(main, world, "cm@a.test")
    world["db"]["care_actions"].update_one({"patient_id": A1}, {"$set": {"insurer_member_id": "ACM555"}})
    assert cm.put(f"/api/patients/{A2}/insurer", json={"insurer": "acme", "member_id": "ACM555"}).status_code == 409
    assert cm.put(f"/api/patients/{A2}/insurer", json={"insurer": "medicare", "member_id": "ACM555"}).status_code == 422
    assert cm.put(f"/api/patients/{A2}/insurer",
                  json={"insurer": "medicare", "member_id": "1EG4-TE5-MK73"}).json()["member_id"] == "1EG4TE5MK73"
    assert cm.put(f"/api/patients/{A3}/insurer", json={"insurer": "Nobody Mutual"}).status_code == 422
    # Nothing was half-written by the refused calls.
    assert world["db"]["care_actions"].find_one({"patient_id": A3}).get("insurer_id") is None


def test_same_insurer_again_only_fills_a_missing_member_id(main, insurers):  # noqa: F811
    world = insurers
    cm = as_user(main, world, "cm@a.test")
    r = cm.put(f"/api/patients/{A1}/insurer", json={"insurer": "acme", "member_id": "ACM777"})
    assert r.json()["member_id"] == "ACM777"
    assert not world["db"]["care_actions"].find_one({"patient_id": A1}).get("insurer_history")


def test_only_billing_staff_set_it_and_only_for_their_own_patients(main, insurers):  # noqa: F811
    world = insurers
    for email in ("doc.a@a.test", "nurse@a.test", "claims@acme.test", "me@patient.test", "carer@home.test",
                  "rx@bay.test"):
        client = as_user(main, world, email)
        assert client.put(f"/api/patients/{A1}/insurer", json={"insurer": "medicare"}).status_code in (403, 404), email
        assert client.get("/api/insurers").status_code == 403, email
    # Another hospital's patient is "not found", as everywhere.
    assert as_user(main, world, "admin@b.test").put(
        f"/api/patients/{A1}/insurer", json={"insurer": "medicare"}).status_code == 404
    assert as_user(main, world, "cm@a.test").put(
        f"/api/patients/{B1}/insurer", json={"insurer": "medicare"}).status_code == 404
    names = [i["name"] for i in as_user(main, world, "cm@a.test").get("/api/insurers").json()["insurers"]]
    assert names == ["Acme Health", "Medicare"]
    assert world["db"]["care_actions"].find_one({"patient_id": A1})["insurer_id"] == "acme"


def test_a_patient_added_with_an_insurer_goes_straight_to_them(main, insurers, monkeypatch):  # noqa: F811
    world = insurers
    monkeypatch.setattr(main, "MANUAL_ENTRY_ENABLED", True)
    admin = as_user(main, world, "admin@a.test")
    r = admin.post("/api/manual-entry/add",
                   json={"records": [{**FORM, "insurer": "Acme Health", "insurer_member_id": "ACM900"}]})
    assert r.status_code == 200, r.text
    pid = r.json()["added"][0]["patient_id"]
    doc = world["db"]["care_actions"].find_one({"patient_id": pid})
    assert doc["insurer_id"] == "acme" and doc["insurer_member_id"] == "ACM900"
    assert as_user(main, world, "claims@acme.test").get(f"/api/patients/{pid}/insurance").status_code == 200


def test_a_bad_insurer_on_any_row_adds_nothing(main, insurers, monkeypatch):  # noqa: F811
    world = insurers
    monkeypatch.setattr(main, "MANUAL_ENTRY_ENABLED", True)
    before = world["db"]["care_actions"].count_documents({})
    admin = as_user(main, world, "admin@a.test")
    for records in ([{**FORM, "insurer": "acme"}, {**FORM, "insurer": "Nobody Mutual"}],
                    [{**FORM, "insurer_member_id": "ACM1"}],                               # ID with no insurer
                    [{**FORM, "insurer": "acme", "insurer_member_id": "ACM2"},
                     {**FORM, "insurer": "acme", "insurer_member_id": "ACM2"}]):          # same ID twice
        assert admin.post("/api/manual-entry/add", json={"records": records}).status_code == 422
    assert world["db"]["care_actions"].count_documents({}) == before


def test_the_upload_reads_insurer_columns(main, insurers):  # noqa: F811
    world = insurers
    r = as_user(main, world, "admin@a.test").post("/api/manual-entry/parse", files=csv_file(
        "external_ref,discharge_date,primary_diagnosis,insurer,insurer_member_id",
        "EXT-1,2026-04-10,Heart failure,Acme Health,acm-42"))
    assert r.status_code == 200, r.text
    record = r.json()["rows"][0]["record"]
    assert record["insurer"] == "Acme Health" and record["insurer_member_id"] == "acm-42"
    assert "insurer" not in r.json().get("unknown_columns", [])

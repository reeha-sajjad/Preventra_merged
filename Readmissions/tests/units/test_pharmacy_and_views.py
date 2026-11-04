"""Where a patient fills (api/patient_pharmacy.py), the pharmacy reading the
medicine list, and the patient seeing who has looked at their record
(api/record_views.py).

Accounts from test_isolation.py: rx@bay.test works for pharmacy "bay", which
fills for A2 and B2; A4 is me@patient.test's record and carer@home.test looks
after A4; cm@a.test and admin@a.test are hospital A's; claims@acme.test insures A1."""
import pytest

from api import auth
from scripts import load_patient_medicines
from tests.units.test_isolation import A1, A2, A3, A4, B2, as_user, main, world  # noqa: F401
from tests.units.test_manual_patients import FORM, fake_model  # noqa: F401


@pytest.fixture
def orgs(world):  # noqa: F811
    ident = world["db"].client[auth.IDENTITY_DB]
    ident["pharmacies"].insert_many([{"_id": "bay", "name": "Bay Pharmacy"},
                                     {"_id": "riverside", "name": "Riverside Community Pharmacy"}])
    ident["insurers"].insert_one({"_id": "acme", "name": "Acme Health"})
    ident["hospitals"].insert_one({"_id": "hosp-a", "name": "Riverside General Hospital"})
    return world


# ---------------------------------------------------------------- pharmacy
def test_setting_a_pharmacy_puts_the_patient_on_its_page(main, orgs):
    world = orgs
    rx = as_user(main, world, "rx@bay.test")
    assert rx.get(f"/api/patients/{A3}/summary").status_code == 404                       # not theirs yet
    cm = as_user(main, world, "cm@a.test")
    assert [p["name"] for p in cm.get("/api/pharmacies").json()["pharmacies"]] == [
        "Bay Pharmacy", "Riverside Community Pharmacy"]
    r = cm.put(f"/api/patients/{A3}/pharmacy", json={"pharmacy": "Bay Pharmacy"})       # by name
    assert r.status_code == 200 and r.json()["pharmacy"]["id"] == "bay"
    assert rx.get(f"/api/patients/{A3}/summary").status_code == 200
    assert A3 in rx.get("/api/pharmacy/home").text


def test_changing_or_clearing_takes_the_old_pharmacy_off(main, orgs):
    world = orgs
    admin, rx = as_user(main, world, "admin@a.test"), as_user(main, world, "rx@bay.test")
    assert rx.get(f"/api/patients/{A2}/summary").status_code == 200
    r = admin.put(f"/api/patients/{A2}/pharmacy", json={"pharmacy": "riverside"})
    assert r.json()["pharmacy"]["name"] == "Riverside Community Pharmacy"
    assert r.json()["last_change"]["from"] == "Bay Pharmacy"
    assert rx.get(f"/api/patients/{A2}/summary").status_code == 404
    assert admin.put(f"/api/patients/{A2}/pharmacy", json={"pharmacy": ""}).json()["pharmacy"] is None
    history = admin.get(f"/api/patients/{A2}/pharmacy-history").json()["history"]
    assert [(h["from"], h["to"]) for h in history] == [("Riverside Community Pharmacy", None),
                                                        ("Bay Pharmacy", "Riverside Community Pharmacy")]
    assert admin.put(f"/api/patients/{A2}/pharmacy", json={"pharmacy": "Nowhere Drugs"}).status_code == 422


def test_only_billing_staff_set_it(main, orgs):
    world = orgs
    for email in ("doc.a@a.test", "nurse@a.test", "claims@acme.test", "rx@bay.test", "me@patient.test",
                  "carer@home.test"):
        c = as_user(main, world, email)
        assert c.put(f"/api/patients/{A1}/pharmacy", json={"pharmacy": "bay"}).status_code in (403, 404), email
        assert c.get("/api/pharmacies").status_code == 403, email
    assert as_user(main, world, "admin@b.test").put(f"/api/patients/{A1}/pharmacy",
                                                    json={"pharmacy": "bay"}).status_code == 404
    # The care team can see where the patient fills; the insurer cannot.
    assert as_user(main, world, "doc.a@a.test").get(f"/api/patients/{A2}/pharmacy").json()["can_set"] is False
    assert as_user(main, world, "claims@acme.test").get(f"/api/patients/{A1}/pharmacy").status_code == 403


def test_a_patient_added_with_a_pharmacy_reaches_it(main, orgs, monkeypatch):
    world = orgs
    monkeypatch.setattr(main, "MANUAL_ENTRY_ENABLED", True)
    admin = as_user(main, world, "admin@a.test")
    assert admin.post("/api/manual-entry/add",
                      json={"records": [{**FORM, "pharmacy": "Nowhere Drugs"}]}).status_code == 422
    r = admin.post("/api/manual-entry/add", json={"records": [{**FORM, "pharmacy": "Bay Pharmacy"}]})
    assert r.status_code == 200, r.text
    pid = r.json()["added"][0]["patient_id"]
    assert as_user(main, world, "rx@bay.test").get(f"/api/patients/{pid}/summary").status_code == 200


# ---------------------------------------------------- pharmacy reads medicines
def test_a_pharmacy_reads_the_medicine_list_of_its_own_patients_only(main, orgs):
    world = orgs
    load_patient_medicines.apply(world["db"], load_patient_medicines.plan(world["db"], demo=True))
    rx = as_user(main, world, "rx@bay.test")
    body = rx.get(f"/api/patients/{A2}/medicines").json()
    assert body["medicines"] and body["can_edit"] is False and body["history"] == []
    assert rx.get(f"/api/patients/{A1}/medicines").status_code == 404           # fills elsewhere
    assert rx.post(f"/api/patients/{A2}/medicines", json={"drug": "X", "dose": "1 mg"}).status_code == 403
    # Still no diagnoses or drivers for a pharmacy.
    assert rx.get(f"/api/patients/{A2}/care-actions").status_code == 403


# ----------------------------------------------------- who viewed my record
def test_the_patient_sees_who_looked_at_their_record(main, orgs):
    world = orgs
    as_user(main, world, "cm@a.test").get(f"/api/patients/{A4}")             # coordination, logged
    as_user(main, world, "cm@a.test").get(f"/api/patients/{A4}")             # same sign-in: logged once
    as_user(main, world, "carer@home.test").get("/api/my-home")              # caretaker, logged
    admin = as_user(main, world, "admin@a.test")
    admin.post(f"/api/patients/{A4}/open", json={"reason": "audit"})
    views = as_user(main, world, "me@patient.test").get("/api/my-home").json()["viewed_by"]
    rows = {(v["role"], v["why"]) for v in views["entries"]}
    assert ("Hospital administrator", "Audit") in rows
    assert any(v["role"] == "Case manager" and v["where"] == "Riverside General Hospital"
               for v in views["entries"])
    assert any(v["role"] == "Your caretaker" for v in views["entries"])
    # The caretaker's page doesn't carry the patient's list.
    assert as_user(main, world, "carer@home.test").get("/api/my-home").json()["viewed_by"] is None


def test_an_insurer_shows_as_its_organisation(main, orgs):
    world = orgs
    world["db"]["care_actions"].update_one({"patient_id": A4}, {"$set": {"insurer_id": "acme"}})
    as_user(main, world, "claims@acme.test").post(f"/api/patients/{A4}/open", json={"reason": "claim_review"})
    views = as_user(main, world, "me@patient.test").get("/api/my-home").json()["viewed_by"]["entries"]
    assert any(v["who"] == "Acme Health" and v["role"] == "Your insurer" and v["why"] == "Claim review"
               for v in views)

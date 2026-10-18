"""The pharmacy role in GLP-1 (core/security.py, core/access.py), over the same
accounts as test_isolation.py: rx@bay.test works at the "bay" pharmacy, which
fills patient 1 (hospital A) and patient 5 (hospital B).

A pharmacy sees the patients who fill there, in any hospital, at the overview
layer only - no reason opens the clinical layer.
"""
from core.access import CLINICAL_FIELDS
from tests.test_isolation import client, world  # noqa: F401


def rx(world):
    return client(world, "rx@bay.test")


def test_a_pharmacy_sees_the_overview_of_its_own_patients(world):
    body = rx(world).get("/api/patients?page_size=50").json()
    assert {r["patient_idx"] for r in body["patients"]} == {1, 5} and body["redacted"]
    assert not any(set(r) & CLINICAL_FIELDS for r in body["patients"])


def test_no_reason_opens_the_clinical_layer(world):
    c = rx(world)
    summary = c.get("/api/patients/1/summary").json()
    assert summary["detail_access"] == "overview_only" and summary["reasons"] == []
    r = c.get("/api/patients/1")
    assert r.status_code == 403 and r.json()["detail"]["code"] == "overview_only"
    assert c.post("/api/patients/1/open", json={"reason": "care_coordination"}).status_code == 403
    assert c.get("/api/patients/1").status_code == 403
    assert c.get("/api/patients/0/summary").status_code == 404


def test_a_pharmacy_gets_no_hospital_pages_costs_or_follow_up_requests(world):
    c = rx(world)
    for path in ("/api/overview", "/api/staff", "/api/cost-effectiveness", "/api/followups"):
        assert c.get(path).status_code == 403, path
    assert c.post("/api/patients/1/followup", json={"urgency": "urgent"}).status_code == 403
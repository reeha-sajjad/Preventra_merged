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


# ------------------------------------------------- step 2: home and reports
import pytest  # noqa: E402

from core.followups import COLLECTION  # noqa: E402


@pytest.fixture
def clean(world):
    world["db"][COLLECTION].delete_many({})
    yield
    world["db"][COLLECTION].delete_many({})


def report(world, idx=1, problem="missed_pickup", urgency="routine", note="Didn't collect on Monday"):
    return rx(world).post(f"/api/patients/{idx}/pharmacy-report",
                          json={"problem": problem, "urgency": urgency, "note": note})


def test_the_pharmacy_home_lists_its_patients_with_what_a_pharmacy_needs(world, clean):
    body = rx(world).get("/api/pharmacy/home").json()
    assert [p["patient_idx"] for p in body["patients"]] == sorted(
        [1, 5], key=lambda i: -next(p["dropout_prob"] for p in body["patients"] if p["patient_idx"] == i))
    row = body["patients"][0]
    assert {"drug", "refill_band", "hospital", "case_manager", "dropout_prob"} <= set(row)
    assert not set(row) & {"driver_1", "BMXBMI", "LBXGH", "RIDAGEYR"}
    assert body["stats"]["patients"] == 2 and body["problems"]
    for email in ("cm@a.test", "doc@a.test", "admin@a.test", "claims@acme.test"):
        assert client(world, email).get("/api/pharmacy/home").status_code == 403


def test_opening_a_patient_shows_the_drug_and_is_logged(world, clean):
    from core.config import settings
    log = world["store"][settings.shared_identity_db_name].access_log
    log.delete_many({"email": "rx@bay.test"})
    body = rx(world).get("/api/patients/5/summary").json()
    assert body["drug"] and body["refill_band"] in ("low", "typical", "good")
    rx(world).get("/api/patients/5/summary")                       # once per sign-in
    entries = list(log.find({"email": "rx@bay.test"}))
    assert len(entries) == 1 and entries[0]["reason"] == "dispensing" and entries[0]["hospital_id"] == "hosp-b"
    # Nobody else gets the pharmacy facts on the summary.
    assert "drug" not in client(world, "admin@b.test").get("/api/patients/5/summary").json()


def test_a_report_becomes_a_request_for_the_case_manager(world, clean):
    r = report(world, urgency="urgent")
    assert r.status_code == 201 and r.json()["added_to_open"] is False
    doc = world["db"][COLLECTION].find_one({"patient_idx": 1})
    assert doc["status"] == "open" and doc["requested_by"]["role"] == "pharmacy"
    assert doc["pharmacy_reports"][0]["problem"] == "missed_pickup"
    # Hospital A's case manager has it in the bell, saying what the pharmacy reported.
    bell = client(world, "cm@a.test").get("/api/notifications").json()
    item = next(i for i in bell["items"] if i["patient_idx"] == 1)
    assert item["problem_label"] == "Missed a refill pickup"
    # The pharmacy sees it on its home page.
    home = rx(world).get("/api/pharmacy/home").json()
    assert home["stats"]["open_reports"] == 1 and home["reports"][0]["by_us"]


def test_with_a_request_already_open_the_report_is_added_to_it(world, clean):
    doctor = client(world, "doc@a.test")
    assert doctor.post("/api/patients/1/followup", json={"urgency": "routine",
                                                         "note": "Nausea, considering stopping"}).status_code == 201
    r = report(world, problem="side_effects", urgency="urgent", note="")
    assert r.status_code == 201 and r.json()["added_to_open"] is True
    assert world["db"][COLLECTION].count_documents({"patient_idx": 1}) == 1
    doc = world["db"][COLLECTION].find_one({"patient_idx": 1})
    assert doc["urgency"] == "urgent" and len(doc["pharmacy_reports"]) == 1
    # The pharmacy never sees what the doctor wrote.
    seen = rx(world).get("/api/patients/1/followup").json()
    assert "Nausea" not in str(seen) and seen["active"]["status"] == "open"
    assert "Nausea" not in str(r.json())


def test_a_pharmacy_cannot_read_or_write_notes(world, clean):
    report(world)
    fid = str(world["db"][COLLECTION].find_one({"patient_idx": 1})["_id"])
    assert rx(world).post(f"/api/followups/{fid}/note", json={"text": "hi"}).status_code == 403
    assert rx(world).post(f"/api/followups/{fid}/take").status_code == 403


def test_a_report_needs_a_real_problem_and_a_patient_of_ours(world, clean):
    assert report(world, problem="nonsense").status_code == 422
    assert report(world, problem="other", note="").status_code == 422
    assert report(world, idx=0).status_code == 404                     # not filled here
    assert client(world, "doc@a.test").post("/api/patients/1/pharmacy-report",
                                            json={"problem": "missed_pickup"}).status_code == 403


def test_settings_names_the_pharmacy(world):
    assert rx(world).get("/api/me").json()["pharmacy"]["id"] == "bay"

# ------------------------------------------------------- the two bell alerts
def bell(world, email):
    return client(world, email).get("/api/notifications").json()


def test_a_report_on_a_request_someone_holds_rings_their_bell(world, clean):
    cm = client(world, "cm@a.test")
    report(world)
    fid = str(world["db"][COLLECTION].find_one({"patient_idx": 1})["_id"])
    assert cm.post(f"/api/followups/{fid}/take").status_code == 200
    assert not any(i["followup_id"] == fid for i in bell(world, "cm@a.test")["items"])   # taken: gone
    report(world, problem="cost", note="")                         # a second problem while held
    item = next(i for i in bell(world, "cm@a.test")["items"] if i["followup_id"] == fid)
    assert item["kinds"] == ["followup_report"] and item["problem_label"] == "Said they can't afford it"
    assert item["requested_by"] == "bay"                            # the pharmacy, not the person
    # Opening Follow-ups clears it.
    assert cm.post("/api/followups/handed-seen").json()["seen"] == 1
    assert not any(i["followup_id"] == fid for i in bell(world, "cm@a.test")["items"])


def test_the_pharmacy_hears_when_its_report_is_done(world, clean):
    empty = bell(world, "rx@bay.test")
    assert empty["enabled"] is True and empty["total"] == 0
    report(world)
    fid = str(world["db"][COLLECTION].find_one({"patient_idx": 1})["_id"])
    assert bell(world, "rx@bay.test")["total"] == 0                 # not done yet
    done = client(world, "cm@a.test").post(f"/api/followups/{fid}/done",
                                           json={"outcome": "booked", "note": "Rang her, new pickup Thursday"})
    assert done.status_code == 200
    item = bell(world, "rx@bay.test")["items"][0]
    assert item["kinds"] == ["report_done"] and item["outcome_label"] == "Booked"
    assert item["problem_label"] == "Missed a refill pickup" and "Thursday" not in str(item)  # no notes
    # Opening the home page clears it; nobody else can clear it for them.
    assert client(world, "cm@a.test").post("/api/pharmacy/seen").status_code == 403
    assert rx(world).post("/api/pharmacy/seen").json()["seen"] == 1
    assert bell(world, "rx@bay.test")["total"] == 0
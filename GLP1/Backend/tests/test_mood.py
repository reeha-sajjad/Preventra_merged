"""The patient's mood screen over the API (core/mood.py, routers/mood.py), and
who may read it. Same accounts as test_isolation.py: me@patient.test is patient
3 of hospital A, carer@home.test is their caretaker, cm@a.test is the hospital's
case manager, doc@a.test and nurse@a.test have patients 0 and 1 (and 2) - and
are given patient 3 where a test needs them to read it.
"""
import asyncio
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from bson import ObjectId

from core import chatbot_tools, followups, mood, mood_screen
from core.config import settings
from core.mood import COLLECTION
from tests.test_isolation import client, ctx_for, world  # noqa: F401

LOW = {"phq_interest": 2, "phq_down": 2}                       # PHQ-2 4: positive
GAD_ONLY = {"phq_interest": 0, "phq_down": 1, "gad_nervous": 2, "gad_worry": 2}
FINE = {"phq_interest": 0, "phq_down": 0, "gad_nervous": 0, "gad_worry": 1}
REST = {"phq_sleep": 1, "phq_tired": 1, "phq_appetite": 1, "phq_failure": 1,
        "phq_concentration": 1, "phq_movement": 1}
MODERATE = {**LOW, **REST}                                      # PHQ-8 10
WORKED = {**LOW, "phq_sleep": 2, "phq_tired": 2, "phq_appetite": 1, "phq_failure": 1,
          "phq_concentration": 1, "phq_movement": 1}            # PHQ-8 12
CLEAN = (COLLECTION, followups.COLLECTION, "dose_log", "notification_seen", "medication_changes",
         "demo_mood_state")


def ago(days):
    return datetime.now(timezone.utc) - timedelta(days=days)


def uid(world, email):
    return str(world["store"][settings.shared_identity_db_name].users.find_one({"email": email})["_id"])


@pytest.fixture
def clean(world):
    db = world["db"]
    saved = db.patient_access.find_one({"patient_idx": 3})
    for name in CLEAN:
        db[name].delete_many({})
    yield
    for name in CLEAN:
        db[name].delete_many({})
    db.patient_access.replace_one({"patient_idx": 3}, saved)


def me(world):
    return client(world, "me@patient.test")


def cm(world):
    return client(world, "cm@a.test")


def care_team_of_3(world):
    """doc@a.test and nurse@a.test are on patient 3's care team."""
    world["db"].patient_access.update_one({"patient_idx": 3}, {"$set": {
        "assigned_doctor_ids": [uid(world, "doc@a.test")], "assigned_nurse_ids": [uid(world, "nurse@a.test")]}})


def add_screen(world, answers, days_ago=1, trouble=None, idx=3):
    """A screen as the app would have stored it, `days_ago` days back."""
    at = ago(days_ago)
    history = list(world["db"][COLLECTION].find({"patient_idx": idx}))
    world["db"][COLLECTION].insert_one({
        "patient_idx": idx, "at": at, "answered_on": at.date().isoformat(), "by_role": "patient",
        "by_user": "test", "previous_id": str(ObjectId()),
        **mood_screen.assess(answers, history, at, trouble)})


def stored(world, idx=3):
    return list(world["db"][COLLECTION].find({"patient_idx": idx}).sort("at", 1))


def bell(world, email):
    r = client(world, email).get("/api/notifications")
    assert r.status_code == 200, r.text
    return {i["patient_idx"]: i for i in r.json()["items"]}


def mood_kind(world, email, idx=3):
    return "mood" in bell(world, email).get(idx, {"kinds": []})["kinds"]


# ----------------------------------------------------- the patient answers
def test_the_patient_answers_every_two_weeks(world, clean):
    first = me(world).get("/api/my-home/mood").json()
    assert first["due"] is True and first["last_answered"] is None and first["interval_days"] == 14
    assert first["safety"] == mood_screen.SAFETY and first["form"]["followup_from"] == 3
    r = me(world).post("/api/my-home/mood", json={"answers": {**LOW, "gad_nervous": 1}})
    assert r.status_code == 201
    sent = r.json()
    assert sent["sent"] is True and sent["message"] and sent["due"] is False and sent["form"] is None
    # Never a score or a flag back to the patient, on the answer or on the card.
    for body in (sent, me(world).get("/api/my-home/mood").json()):
        text = json.dumps({k: v for k, v in body.items() if k != "form"})
        assert not any(w in text for w in ("phq2", "phq8", "gad2", "flags", "trouble", "answers", "band"))
    today = datetime.now(timezone.utc).date()
    assert sent["last_answered"] == today.isoformat()
    assert sent["next_due"] == (today + timedelta(days=14)).isoformat()
    assert me(world).get("/api/my-home/mood").json()["due"] is False

    again = me(world).post("/api/my-home/mood", json={"answers": LOW})
    assert again.status_code == 409 and sent["next_due"] in again.json()["detail"]

    # A fortnight on, it is due again, and the new screen follows the old.
    doc = stored(world)[0]
    back = ago(14)
    world["db"][COLLECTION].update_one({"_id": doc["_id"]}, {"$set": {"at": back, "answered_on": back.date().isoformat()}})
    assert me(world).get("/api/my-home/mood").json()["due"] is True
    assert me(world).post("/api/my-home/mood", json={"answers": MODERATE}).status_code == 201
    old, new = stored(world)
    assert new["previous_id"] == str(old["_id"]) and old["previous_id"] == "none"
    assert new["phq8"] == 10 and new["flags"] == ["phq8_moderate"] and new["answered_on"] == today.isoformat()


def test_a_demo_screen_is_not_the_patients_own_last_answer(world, clean):
    at = ago(5)
    world["db"][COLLECTION].insert_one({
        "patient_idx": 3, "at": at, "answered_on": at.date().isoformat(), "previous_id": "demo:x:s:1", "demo": True,
        **mood_screen.assess(WORKED, [], at, simulated=True)})
    card = me(world).get("/api/my-home/mood").json()
    assert card["due"] is True and card["last_answered"] is None and card["next_due"] is None and card["form"]
    assert me(world).post("/api/my-home/mood", json={"answers": FINE}).status_code == 201
    assert me(world).post("/api/my-home/mood", json={"answers": FINE}).status_code == 409
    real = [d for d in stored(world) if not d.get("simulated")]
    assert len(real) == 1 and real[0]["previous_id"] == "none"
    assert me(world).get("/api/my-home/mood").json()["last_answered"] == real[0]["answered_on"]
    # The care team's card still lists both, each with its own id.
    screens = cm(world).get("/api/patients/3/mood").json()["screens"]
    assert len(screens) == 2 and len({s["id"] for s in screens}) == 2


def add_demo(world, answers, days_ago):
    at = ago(days_ago)
    world["db"][COLLECTION].insert_one({
        "patient_idx": 3, "at": at, "answered_on": at.date().isoformat(), "previous_id": f"demo:x:{days_ago}",
        "demo": True, **mood_screen.assess(answers, [], at, simulated=True)})


def test_a_real_screen_is_scored_against_real_answers_only(world, clean):
    severe = {**WORKED, "phq_sleep": 3, "phq_tired": 3, "phq_appetite": 3, "phq_failure": 3}      # PHQ-8 18
    add_demo(world, severe, 30)
    assert me(world).post("/api/my-home/mood", json={"answers": MODERATE}).status_code == 201
    real = [d for d in stored(world) if not d.get("simulated")][0]
    assert real["phq8"] == 10 and real["flags"] == ["phq8_moderate"]
    assert real["previous"] is None and real["change"] is None
    # And the other way: a low demo screen is no baseline for a rise.
    world["db"][COLLECTION].delete_many({})
    add_demo(world, {**LOW, "phq_sleep": 0, "phq_tired": 0, "phq_appetite": 0, "phq_failure": 0,
                     "phq_concentration": 0, "phq_movement": 0}, 30)                              # PHQ-8 4
    assert me(world).post("/api/my-home/mood", json={"answers": severe}).status_code == 201
    real = [d for d in stored(world) if not d.get("simulated")][0]
    assert "phq8_rise" not in real["flags"] and real["change"] is None
    # The care team still sees both screens.
    assert len(cm(world).get("/api/patients/3/mood").json()["screens"]) == 2


def test_both_cards_agree_on_when_the_next_questions_are_due(world, clean):
    add_demo(world, WORKED, 2)                                    # a demo screen two days ago
    staff = cm(world).get("/api/patients/3/mood").json()
    assert staff["due"] is True and staff["last_answered"] is None and staff["next_due"] is None
    assert me(world).get("/api/my-home/mood").json()["due"] is True
    me(world).post("/api/my-home/mood", json={"answers": FINE})
    assert cm(world).get("/api/patients/3/mood").json()["next_due"] == me(world).get("/api/my-home/mood").json()["next_due"]


def test_bad_answers_are_refused_and_nothing_is_written(world, clean):
    for body in ({"answers": {"phq_interest": 1, "extra": 2}}, {"answers": {"phq_interest": 7}},
                 {"answers": {"phq_interest": True}}, {"answers": {}}, {},
                 {"answers": {"phq_sleep": 2}}):               # the rest of the PHQ-8 alone: dropped, then empty
        assert me(world).post("/api/my-home/mood", json=body).status_code == 422, body
    assert stored(world) == []
    assert me(world).get("/api/my-home/mood").json()["due"] is True


def test_the_rest_of_the_phq8_is_dropped_when_the_phq2_is_not_positive(world, clean):
    assert me(world).post("/api/my-home/mood", json={"answers": {"phq_interest": 1, "phq_down": 1, **REST}}).status_code == 201
    assert stored(world)[0]["answers"] == {"phq_interest": 1, "phq_down": 1}


def test_only_the_patient_answers_and_only_for_themselves(world, clean):
    for email in ("carer@home.test", "doc@a.test", "cm@a.test", "admin@a.test", "ops@team.test",
                  "claims@acme.test", "rx@bay.test"):
        c = client(world, email)
        assert c.get("/api/my-home/mood").status_code == 403, email
        assert c.post("/api/my-home/mood", json={"answers": LOW}).status_code == 403, email
    assert stored(world) == []


# --------------------------------------------------- who reads the screens
def test_only_doctors_nurses_and_case_managers_read_a_patients_screens(world, clean):
    add_screen(world, WORKED)
    for email in ("ops@team.test", "admin@a.test", "admin@b.test", "claims@acme.test", "rx@bay.test",
                  "carer@home.test", "me@patient.test"):
        assert client(world, email).get("/api/patients/3/mood").status_code == 403, email
    assert client(world, "floating@x.test").get("/api/patients/3/mood").status_code == 404
    assert cm(world).get("/api/patients/3/mood").status_code == 200
    # A doctor or nurse reads it only for patients of their own.
    assert client(world, "doc@a.test").get("/api/patients/3/mood").status_code == 404
    care_team_of_3(world)
    for email in ("doc@a.test", "nurse@a.test"):
        body = client(world, email).get("/api/patients/3/mood")
        assert body.status_code == 200, email
    assert client(world, "doc@a.test").get("/api/patients/2/mood").status_code == 404
    assert client(world, "doc@a.test").get("/api/patients/4/mood").status_code == 404


def test_the_staff_card_has_totals_and_flags_never_the_answers(world, clean):
    add_screen(world, MODERATE, days_ago=20)
    add_screen(world, {**WORKED, "phq_sleep": 3, "phq_tired": 3, "phq_appetite": 3, "phq_failure": 3,
                       "phq_concentration": 3, "phq_movement": 0, "gad_nervous": 2, "gad_worry": 1}, days_ago=1)
    body = cm(world).get("/api/patients/3/mood").json()
    assert [s["answered_on"] for s in body["screens"]] == sorted((s["answered_on"] for s in body["screens"]), reverse=True)
    newest = body["screens"][0]
    assert newest["lines"][0]["text"] == "19 · moderately severe · up 9 since last time"
    assert [f["code"] for f in newest["flags"]] == ["phq8_rise", "gad2_positive"]
    assert body["screens"][1]["flags"][0]["code"] == "phq8_moderate"
    text = json.dumps(body)
    assert '"answers"' not in text and '"trouble"' not in text and "phq_" not in text
    assert body["due"] is False and body["interval_days"] == 14 and body["patient_idx"] == 3
    # No screens yet: still a card, and it says when they are due.
    world["db"][COLLECTION].delete_many({})
    empty = cm(world).get("/api/patients/3/mood").json()
    assert empty["screens"] == [] and empty["due"] is True and empty["last_answered"] is None


def test_opening_the_mood_card_is_logged_like_opening_the_patient(world, clean):
    add_screen(world, WORKED)
    log = world["store"][settings.shared_identity_db_name].access_log
    log.delete_many({})
    assert cm(world).get("/api/patients/3/mood").status_code == 200
    entry = log.find_one({"patient_id": "3", "role": "case_manager"})
    assert entry and entry["reason"] == "case_management"
    log.delete_many({})
    assert client(world, "admin@a.test").get("/api/patients/3/mood").status_code == 403
    assert log.count_documents({"patient_id": "3"}) == 0                  # refused before anything is looked at


# --------------------------------------- a caretaker never meets any of it
def test_a_caretaker_sees_nothing_mood_related(world, clean):
    care_team_of_3(world)
    add_screen(world, WORKED)
    doc, carer = client(world, "doc@a.test"), client(world, "carer@home.test")
    assert doc.post("/api/patients/3/followup", json={
        "urgency": "routine", "note": "Check in on how they are coping", "reason": "behavioural_health"}).status_code == 201
    assert me(world).post("/api/my-home/ask", json={"reason": "mood", "note": "Feeling low lately"}).status_code == 201
    paths = ["/api/my-home?patient=3", "/api/patients/3", "/api/patients/3/followup", "/api/notifications",
             "/api/my-people", "/api/zoe/doses?patient=3", "/api/patients/3/medications", "/api/patients/3/doses"]
    for path in paths:
        r = carer.get(path)
        assert r.status_code in (200, 403, 404), path
        body = r.json()
        if path == "/api/notifications":
            body = body["items"]                      # the counts carry every kind's name, always 0 here
        text = json.dumps(body).lower()
        assert not any(w in text for w in ("phq", "coping", "behavioural", "feeling low", "mood")), path
    follow = carer.get("/api/patients/3/followup").json()
    assert follow["active"]["asked"] == [] and follow["active"]["by_you"] is False
    assert "mood" not in {r["key"] for r in follow["reasons"]}
    assert carer.post("/api/my-home/ask", json={"reason": "mood", "patient_idx": 3}).status_code == 422
    assert carer.get("/api/my-home/mood").status_code == 403
    assert carer.get("/api/patients/3/mood").status_code == 403
    # The patient does see their own ask, in their own words.
    mine = me(world).get("/api/my-home").json()["followup"]
    assert [a["reason_label"] for a in mine["active"]["asked"]] == ["My mood or how I'm coping"]
    assert "mood" in {r["key"] for r in mine["reasons"]}
    assert "Check in on" not in json.dumps(mine) and "ehavioural" not in json.dumps(mine)


# ------------------------------------------------------- the real events
def events(world, idx=3):
    return asyncio.run(mood.trouble_events(idx, datetime.now(timezone.utc)))


def followup_doc(world, **extra):
    world["db"][followups.COLLECTION].insert_one({
        "patient_idx": 3, "status": "done", "urgency": "routine", "hospital_id": "hosp-a",
        "requested_by": {"id": "x", "name": "X", "role": "doctor"}, "requested_at": ago(60), **extra})


def link_carer(world, days_ago):
    world["db"].patient_access.update_one({"patient_idx": 3}, {"$set": {
        "caretaker_ids": [uid(world, "carer@home.test")],
        "caretaker_history": [{"action": "linked", "caretaker_id": uid(world, "carer@home.test"),
                               "by": {"id": "x", "name": "X"}, "at": ago(days_ago)}]}})


def tick(world, days_ago, **extra):
    world["db"].dose_log.insert_one({"patient_idx": 3, "given_at": ago(days_ago),
                                     "logged_by": {"id": "x", "name": "Zara", "role": "caretaker"}, **extra})


def test_a_stopping_ask_in_the_last_30_days_counts(world, clean):
    followup_doc(world, patient_requests=[{"reason": "stopping", "note": "", "at": ago(5), "by": "patient"}])
    assert events(world) == ["stopping_ask"]
    world["db"][followups.COLLECTION].delete_many({})
    followup_doc(world, patient_requests=[{"reason": "stopping", "note": "", "at": ago(40), "by": "patient"}])
    assert events(world) == []
    world["db"][followups.COLLECTION].delete_many({})
    followup_doc(world, patient_requests=[{"reason": "cost", "note": "", "at": ago(2), "by": "patient"}])
    assert events(world) == []


def test_a_missed_pickup_report_in_the_last_30_days_counts(world, clean):
    followup_doc(world, pharmacy_reports=[{"problem": "missed_pickup", "at": ago(5), "pharmacy_id": "bay"}])
    assert events(world) == ["missed_pickup"]
    world["db"][followups.COLLECTION].delete_many({})
    followup_doc(world, pharmacy_reports=[{"problem": "missed_pickup", "at": ago(45), "pharmacy_id": "bay"},
                                          {"problem": "cost", "at": ago(1), "pharmacy_id": "bay"}])
    assert events(world) == []


def test_a_caretaker_with_no_dose_ticks_for_two_weeks_is_a_dose_gap(world, clean):
    link_carer(world, 20)
    assert events(world) == ["dose_gap"]                       # never ticked: counted from the link
    world["db"].patient_access.update_one({"patient_idx": 3}, {"$set": {"caretaker_history": [{
        "action": "linked", "caretaker_id": uid(world, "carer@home.test"), "at": ago(5)}]}})
    assert events(world) == []                                 # only linked 5 days ago
    link_carer(world, 40)
    tick(world, 15)
    assert events(world) == ["dose_gap"]                       # the last tick is 15 days old
    tick(world, 3)
    assert events(world) == []
    world["db"].dose_log.delete_many({})
    tick(world, 15)
    tick(world, 3, undone={"at": ago(2)})                      # an undone tick is not a dose
    assert events(world) == ["dose_gap"]


def test_no_caretaker_no_link_date_or_a_stopped_medicine_is_no_dose_gap(world, clean):
    link_carer(world, 20)
    world["db"].patient_access.update_one({"patient_idx": 3}, {"$set": {"caretaker_ids": []}})
    assert events(world) == []
    world["db"].patient_access.update_one({"patient_idx": 3}, {"$set": {
        "caretaker_ids": [uid(world, "carer@home.test")], "caretaker_history": []}})
    assert events(world) == []                                 # link date unknown: no guess
    link_carer(world, 20)
    assert events(world) == ["dose_gap"]
    world["db"].medication_changes.insert_one({
        "patient_idx": 3, "from_drug": "SEMAGLUTIDE", "to_drug": "STOPPED", "reason": "other",
        "effective_date": datetime.now(timezone.utc).date().isoformat(),
        "recorded_by": {"id": "x", "name": "Dr X"}, "recorded_at": datetime.now(timezone.utc)})
    assert events(world) == []


def test_low_mood_with_an_event_flags_when_the_patient_answers(world, clean):
    followup_doc(world, patient_requests=[{"reason": "stopping", "note": "", "at": ago(5), "by": "patient"}])
    sent = me(world).post("/api/my-home/mood", json={"answers": LOW})
    assert sent.status_code == 201 and "trouble" not in json.dumps(sent.json())
    doc = stored(world)[0]
    assert doc["flags"] == ["low_mood_trouble"] and doc["trouble"] == ["stopping_ask"]
    card = cm(world).get("/api/patients/3/mood").json()["screens"][0]
    assert card["flags"][0]["title"] == "Low mood with trouble taking the medicine"
    assert card["flags"][0]["detail"] == "PHQ-2 4."


# --------------------------------------------------- the doctor's and nurse's bell
def test_a_flagged_screen_rings_the_care_teams_bell_until_they_open_the_patient(world, clean):
    care_team_of_3(world)
    add_screen(world, WORKED)
    item = bell(world, "doc@a.test")[3]
    assert "mood" in item["kinds"] and item["mood"]["title"] == "Mood screen moderate or worse (PHQ-8 12)"
    assert item["mood"]["simulated"] is False and item["mood"]["answered_on"]
    assert mood_kind(world, "nurse@a.test")
    assert client(world, "doc@a.test").get("/api/patients/3").status_code == 200       # opening clears it
    assert not mood_kind(world, "doc@a.test") and mood_kind(world, "nurse@a.test")
    assert client(world, "nurse@a.test").post("/api/notifications/seen", json={"all": True}).status_code == 200
    assert not mood_kind(world, "nurse@a.test")


def test_only_a_recent_flagged_latest_screen_rings(world, clean):
    care_team_of_3(world)
    add_screen(world, GAD_ONLY)
    assert not mood_kind(world, "doc@a.test") and not mood_kind(world, "cm@a.test")   # anxiety alone: quiet
    world["db"][COLLECTION].delete_many({})
    add_screen(world, WORKED, days_ago=30)
    assert not mood_kind(world, "doc@a.test")                                         # too old
    world["db"][COLLECTION].delete_many({})
    add_screen(world, WORKED, days_ago=20)
    assert mood_kind(world, "doc@a.test")
    add_screen(world, FINE, days_ago=1)                                               # a better screen since
    assert not mood_kind(world, "doc@a.test") and not mood_kind(world, "cm@a.test")


def test_a_simulated_screen_says_so_in_the_bell(world, clean):
    care_team_of_3(world)
    at = ago(2)
    world["db"][COLLECTION].insert_one({
        "patient_idx": 3, "at": at, "answered_on": at.date().isoformat(), "previous_id": "demo:x:1", "demo": True,
        **mood_screen.assess(WORKED, [], at, simulated=True)})
    assert bell(world, "doc@a.test")[3]["mood"]["simulated"] is True


# ---------------------------------------------------------- the case manager
def test_the_case_manager_hears_of_mood_flags_for_their_patients(world, clean):
    add_screen(world, WORKED)
    item = bell(world, "cm@a.test")[3]
    assert item["kinds"] == ["mood"] and item["mood"]["title"].startswith("Mood screen moderate")
    # A patient with a main case manager rings that person only.
    other = world["store"][settings.shared_identity_db_name].users.insert_one(
        {"email": "cm2@a.test", "role": "case_manager", "status": "active", "hospital_id": "hosp-a"}).inserted_id
    try:
        world["db"].patient_access.update_one({"patient_idx": 3}, {"$set": {"case_manager_id": str(other)}})
        assert 3 not in bell(world, "cm@a.test")
        world["db"].patient_access.update_one({"patient_idx": 3}, {"$set": {"case_manager_id": uid(world, "cm@a.test")}})
        assert 3 in bell(world, "cm@a.test")
        world["db"].patient_access.update_one({"patient_idx": 3}, {"$unset": {"case_manager_id": ""}})
    finally:
        world["store"][settings.shared_identity_db_name].users.delete_one({"_id": other})
    assert 3 in bell(world, "cm@a.test")


def test_the_case_manager_clears_it_by_opening_the_card_or_marking_all_read(world, clean):
    add_screen(world, WORKED)
    assert 3 in bell(world, "cm@a.test")
    assert cm(world).get("/api/patients/3/mood").status_code == 200
    assert 3 not in bell(world, "cm@a.test")
    world["db"].notification_seen.delete_many({})
    assert 3 in bell(world, "cm@a.test")
    r = cm(world).post("/api/notifications/seen", json={"all": True})
    assert r.status_code == 200 and r.json() == {"seen": 1}
    assert 3 not in bell(world, "cm@a.test")
    # A new screen after they looked rings again.
    add_screen(world, {**WORKED, "phq_sleep": 3, "phq_tired": 3, "phq_appetite": 3, "phq_failure": 3,
                       "phq_concentration": 3, "phq_movement": 3}, days_ago=-0.001)       # just after the seen mark
    assert 3 in bell(world, "cm@a.test")


def test_no_other_account_ever_has_a_mood_item(world, clean):
    care_team_of_3(world)
    add_screen(world, WORKED)
    for email in ("ops@team.test", "admin@a.test", "admin@b.test", "claims@acme.test", "rx@bay.test",
                  "me@patient.test", "carer@home.test", "floating@x.test"):
        body = client(world, email).get("/api/notifications").json()
        assert not any("mood" in i["kinds"] for i in body["items"]), email
        assert body["counts"]["mood"] == 0
    assert client(world, "admin@a.test").post("/api/notifications/seen", json={"all": True}).status_code == 403


def test_the_today_page_lists_flagged_patients_for_the_case_manager(world, clean):
    add_screen(world, WORKED)
    body = cm(world).get("/api/case-manager/today").json()["mood"]
    assert body["total"] == 1 and body["patients"][0]["patient_idx"] == 3
    assert body["patients"][0]["title"].startswith("Mood screen moderate") and body["patients"][0]["dropout_prob"] is not None
    assert cm(world).get("/api/case-manager/today", params={"mine": 1}).json()["mood"] == {"total": 0, "patients": []}
    # Seeing it does not remove it from the list; a better screen does, and so does anxiety alone.
    cm(world).get("/api/patients/3/mood")
    assert cm(world).get("/api/case-manager/today").json()["mood"]["total"] == 1
    add_screen(world, GAD_ONLY, days_ago=0)
    assert cm(world).get("/api/case-manager/today").json()["mood"]["total"] == 0


# ------------------------------------------------------------ follow-ups
def request_bh(world, idx=0, note="Please call about how they are coping"):
    care_team_of_3(world)
    return client(world, "doc@a.test").post(f"/api/patients/{idx}/followup", json={
        "urgency": "routine", "note": note, "reason": "behavioural_health"})


def test_a_doctor_can_give_a_follow_up_a_behavioural_health_reason(world, clean):
    r = request_bh(world)
    assert r.status_code == 201 and r.json()["reason"] == "behavioural_health"
    assert r.json()["reason_label"] == "Behavioural health referral"
    assert client(world, "doc@a.test").post("/api/patients/1/followup", json={
        "urgency": "routine", "reason": "nonsense"}).status_code == 422
    plain = client(world, "doc@a.test").post("/api/patients/1/followup", json={"urgency": "routine"})
    assert plain.status_code == 201 and plain.json()["reason"] is None
    form = client(world, "doc@a.test").get("/api/patients/0/followup").json()
    assert form["followup_reasons"] == [{"key": "behavioural_health", "label": "Behavioural health referral"}]
    assert "followup_reasons" not in cm(world).get("/api/patients/0/followup").json()


def test_the_case_manager_sees_the_reason_and_everyone_else_does_not(world, clean):
    request_bh(world)
    mine = next(i for i in cm(world).get("/api/followups").json()["items"] if i["patient_idx"] == 0)
    assert mine["reason_label"] == "Behavioural health referral" and "coping" in mine["note"]
    for email in ("admin@a.test", "ops@team.test"):
        body = client(world, email).get("/api/followups").json()
        item = next(i for i in body["items"] if i["patient_idx"] == 0)
        assert item["reason"] is None and item["reason_label"] is None
        assert item["note"] == "" and item["notes"] == [] and item["outcome_note"] == ""
        assert "behavioural" not in json.dumps(body).lower() and "coping" not in json.dumps(body)
    insurer = client(world, "claims@acme.test").get("/api/patients/0/followup").json()
    assert insurer["active"]["reason"] is None and insurer["active"]["note"] == ""
    assert "behavioural" not in json.dumps(insurer).lower()
    assert cm(world).get("/api/patients/0/followup").json()["active"]["reason_label"] == "Behavioural health referral"


def test_a_hospital_admin_can_still_take_and_finish_it_without_seeing_why(world, clean):
    request_bh(world)
    fid = str(world["db"][followups.COLLECTION].find_one({"patient_idx": 0})["_id"])
    admin = client(world, "admin@a.test")
    taken = admin.post(f"/api/followups/{fid}/take")
    assert taken.status_code == 200 and taken.json()["status"] == "in_progress"
    assert taken.json()["reason"] is None and taken.json()["note"] == ""
    again = admin.post(f"/api/followups/{fid}/take")
    assert again.status_code == 409 and again.json()["detail"]["followup"]["note"] == ""
    done = admin.post(f"/api/followups/{fid}/done", json={"outcome": "booked", "note": "Booked with the clinic"})
    assert done.status_code == 200 and done.json()["outcome_note"] == "" and done.json()["notes"] == []
    assert cm(world).get("/api/followups", params={"status": "done"}).json()["items"][0]["outcome_note"] == "Booked with the clinic"
    assert admin.get("/api/followups", params={"status": "done"}).json()["items"][0]["outcome_note"] == ""


def test_the_patient_never_sees_the_staff_reason(world, clean):
    request_bh(world, idx=3, note="Screen positive, please call")
    for body in (me(world).get("/api/my-home").json(), me(world).get("/api/patients/3/followup").json()):
        text = json.dumps(body).lower()
        assert "behavioural" not in text and "please call" not in text
    assert me(world).get("/api/patients/3/followup").json()["active"]["status"] == "open"


def test_the_patients_mood_ask_reaches_the_case_manager_and_nobody_else(world, clean):
    r = me(world).post("/api/my-home/ask", json={"reason": "mood", "note": "Struggling a bit"})
    assert r.status_code == 201
    assert [a["reason_label"] for a in r.json()["followup"]["asked"]] == ["My mood or how I'm coping"]
    assert next(i for i in bell(world, "cm@a.test").values() if i["patient_idx"] == 3)["problem_label"] == "My mood or how I'm coping"
    listed = cm(world).get("/api/followups").json()["items"][0]
    assert listed["patient_requests"][0]["reason_label"] == "My mood or how I'm coping"
    for email in ("admin@a.test", "ops@team.test"):
        body = client(world, email).get("/api/followups").json()
        text = json.dumps(body)
        assert "coping" not in text and "Struggling" not in text, email
        item = next(i for i in body["items"] if i["patient_idx"] == 3)
        assert item["patient_requests"] == [] and item["note"] == ""
    # And it stays the patient's own: a second ask joins the open request.
    assert me(world).post("/api/my-home/ask", json={"reason": "cost"}).json()["added_to_open"] is True


def test_a_case_manager_holding_it_hears_of_a_mood_ask_in_neutral_words(world, clean):
    me(world).post("/api/my-home/ask", json={"reason": "refill"})
    fid = str(world["db"][followups.COLLECTION].find_one({"patient_idx": 3})["_id"])
    assert cm(world).post(f"/api/followups/{fid}/take").status_code == 200
    me(world).post("/api/my-home/ask", json={"reason": "mood"})
    holder = next(i for i in bell(world, "cm@a.test").values() if i.get("followup_id") == fid)
    assert holder["kinds"] == ["followup_report"] and holder["problem_label"] == "My mood or how I'm coping"
    # The same ask, for a holder who may not read mood (an admin who stepped in).
    admin_user = {"id": uid(world, "admin@a.test"), "role": "hospital_admin", "hospital_id": "hosp-a"}
    world["db"][followups.COLLECTION].update_one({"_id": ObjectId(fid)}, {"$set": {
        "taken_by.id": admin_user["id"], "report_alert.for": admin_user["id"]}})
    seen = asyncio.run(followups.reported_for_bell(admin_user))
    assert [f["reported_problem"] for f in seen] == [followups.NEUTRAL_ASK]


def test_a_mood_related_booking_is_not_news_to_a_caretaker(world, clean):
    def booked_kind():
        items = client(world, "carer@home.test").get("/api/notifications").json()["items"]
        return any("booked" in i["kinds"] for i in items)
    followup_doc(world, outcome="booked", done_at=ago(1))
    assert booked_kind()
    world["db"][followups.COLLECTION].delete_many({})
    followup_doc(world, outcome="booked", done_at=ago(1), reason="behavioural_health")
    assert not booked_kind()
    world["db"][followups.COLLECTION].delete_many({})
    followup_doc(world, outcome="booked", done_at=ago(1),
                 patient_requests=[{"reason": "mood", "note": "", "at": ago(2), "by": "patient"}])
    assert not booked_kind()


# -------------------------------------------------------------- the chatbot
def test_the_chatbot_never_reads_mood(world, clean):
    care_team_of_3(world)
    add_screen(world, WORKED)
    ctx = ctx_for(world, "cm@a.test")
    result, err = asyncio.run(chatbot_tools.dispatch_tool("get_patient", {"patient_idx": 3}, ctx))
    assert err is None
    assert "phq" not in json.dumps(result, default=str).lower() and "mood" not in json.dumps(result, default=str).lower()
    snapshot = asyncio.run(chatbot_tools.build_snapshot(ctx))
    assert "phq" not in snapshot.lower() and "mood" not in snapshot.lower()
    assert "mood" not in json.dumps(client(world, "cm@a.test").get("/api/patients/3").json()).lower()
    here = Path(__file__).resolve().parent.parent
    for name in ("core/chatbot_tools.py", "core/llm.py", "routers/chatbot.py"):
        assert "mood" not in (here / name).read_text().lower(), name

"""Re-evaluating one patient with no typing (api/patient_rescore.py).

Same two hospitals and accounts as test_isolation.py. A1 is a loaded MIMIC
patient: its 94 inputs come from one real row of the Phase 1 matrix (skipped
when the matrix is not on this machine) and its weekly series is built with the
monitoring rules. A fixed score is patched in only where a test needs a number
the model would not give.
"""
import sys
from pathlib import Path

import pandas as pd
import pytest

from api import access, mimic_scoring, model_studio, scoring_service
from ingestion import worker
from models.monitoring_rules import CARRY, red_flags, score_week, variant_for
from tests.units.test_ingest import bundle, machine, token  # noqa: F401
from tests.units.test_isolation import A1, A2, A3, LATEST, as_user, main, world  # noqa: F401
from tests.units.test_ingest_scoring import PID as INGESTED, died, send

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import backfill_model_inputs as backfill  # noqa: E402

BANDS = model_studio._mimic_bands()
GROUP = "heart_failure"
OBS = {1: {"weight_change_kg": 3.2, "sbp": 132, "heart_rate": 78, "spo2": 95, "adherence_pct": 80},
       3: {"weight_change_kg": 0.4, "sbp": 118, "heart_rate": 80, "spo2": 87, "adherence_pct": 60}}
ROUTE = "/api/patients/{}/rescore"


def band_of(score):
    return scoring_service._band(score, BANDS)


def expected_weeks(pid, base):
    """The series the monitoring rules give for A1's stored readings, worked
    out here without going through rescore_series."""
    out, prev_adj, prev = {}, 0.0, base
    for n in (1, 2, 3):
        if n == 2:                                       # a week not observed holds the last score
            out[n] = prev
            continue
        score, adj, contribs = score_week(base, OBS[n], CARRY * prev_adj, variant=variant_for(pid, n),
                                          group=GROUP, groups=[GROUP], prev_score=prev,
                                          flag_floor=BANDS["low_score_threshold"])
        out[n], prev_adj, prev = score, adj, score
    return out


@pytest.fixture(scope="module")
def real_row():
    path = ROOT / backfill.DEFAULT_MATRIX
    if not path.exists():
        pytest.skip("the Phase 1 matrix is not on this machine")
    state = mimic_scoring._load()
    feats = state["features"]
    matrix = pd.read_parquet(path, columns=["subject_id", "hadm_id"] + feats).head(400)
    scores = state["model"].predict_proba(matrix[feats])[:, 1] * 100
    for i, score in enumerate(scores):                   # a patient below 60, so a rise to 85 is a jump
        if score < 60:
            return mimic_scoring.features_for_storage(matrix.iloc[i][feats])
    pytest.skip("no suitable row")


def seed(world, real_row):
    """A1 with stored inputs, the score they give, and a weekly series."""
    db = world["db"]
    scored = mimic_scoring.score_features(real_row)
    score, band = scored["risk_score"], band_of(scored["risk_score"])
    db["patient_worklist"].update_many(
        {"patient_id": A1}, {"$set": {"risk_score": score, "risk_band": band, "current_score": score,
                                      "current_band": band, "model_version": scoring_service.BUILTIN_VERSION,
                                      "driver_1": scored["driver_1_raw"], "driver_2": scored["driver_2_raw"],
                                      "driver_3": scored["driver_3_raw"], "discharge_date": "2026-04-10"}})
    db["model_inputs"].insert_one({"patient_id": A1, "hadm_id": 1, "features": real_row, "current": True,
                                   "source": "mimic_matrix", "hospital_id": "hosp-a"})
    db["weekly_monitoring"].delete_many({"patient_id": A1})
    base = {"patient_id": A1, "batch_date": LATEST, "hospital_id": "hosp-a", "clinical_group": GROUP,
            "source": "mimic"}
    weeks = expected_weeks(A1, score)
    docs = [{**base, "week_number": 0, "risk_score": score, "risk_band": band, "observed": True,
             "scored_by": "model"}]
    for n in (1, 2, 3):
        docs.append({**base, "week_number": n, "risk_score": weeks[n], "risk_band": band_of(weeks[n]),
                     "observed": n != 2, "monitoring": OBS.get(n), "note": f"note {n}",
                     "red_flags": ["spo2"] if n == 3 else [], "scored_by": "monitoring_rules"})
    docs[3].update(reported_by="patient", remarks="x")
    db["weekly_monitoring"].insert_many(docs)
    return score, band


def fixed(monkeypatch, score, band):
    def fake(_features):
        return {"risk_score": score, "risk_band": band, "drivers": [],
                "driver_1_raw": "Age: 71 (raises risk)", "driver_2_raw": "Stay: 5 days (lowers risk)",
                "driver_3_raw": "Prior admissions: 3 (raises risk)"}
    monkeypatch.setattr(mimic_scoring, "score_features", fake)


def series(db, pid=A1):
    return {d["week_number"]: d for d in db["weekly_monitoring"].find({"patient_id": pid})}


def snapshot(db, *names):
    return {n: sorted(map(str, db[n].find({}, {"_id": 0}))) for n in names}


WRITTEN = ("patient_worklist", "weekly_monitoring", "risk_registry", "executive_summary", "rescore_log",
           "alerts")


def post(client, pid, target, commit=False, **more):
    return client.post(ROUTE.format(pid), json={"target": target, "commit": commit, **more})


# ------------------------------------------------------------------ tests
def test_re_evaluating_an_unchanged_patient_changes_nothing(main, world, real_row):  # noqa: F811
    score, band = seed(world, real_row)
    db, client = world["db"], as_user(main, world, "doc.a@a.test")
    before = series(db)
    for target in ("discharge", "weekly"):
        r = post(client, A1, target)
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["changed"] is False and not any(w["changed"] for w in body["weeks"])
        assert body["proposed"]["score"] == score and body["proposed"]["band"] == band
        assert len(body["weeks"]) == 4
        assert post(client, A1, target, commit=True).status_code == 200
    after = series(db)
    for n in before:
        assert (after[n]["risk_score"], after[n]["risk_band"]) == (before[n]["risk_score"], before[n]["risk_band"])
    row = db["patient_worklist"].find_one({"patient_id": A1, "batch_date": LATEST})
    assert (row["risk_score"], row["risk_band"]) == (score, band)


def test_a_preview_writes_nothing(main, world, real_row, monkeypatch):  # noqa: F811
    seed(world, real_row)
    fixed(monkeypatch, 85.0, "High")
    db, client = world["db"], as_user(main, world, "doc.a@a.test")
    before = snapshot(db, *WRITTEN)
    for target in ("discharge", "weekly"):
        r = post(client, A1, target)
        assert r.status_code == 200 and r.json()["committed"] is False
    assert post(client, A1, "discharge").json()["changed"] is True
    assert snapshot(db, *WRITTEN) == before


def test_a_new_discharge_score_moves_every_week_after_it(main, world, real_row, monkeypatch):  # noqa: F811
    old, _ = seed(world, real_row)
    fixed(monkeypatch, 85.0, "High")
    db, client = world["db"], as_user(main, world, "doc.a@a.test")
    old_series = series(db)

    r = post(client, A1, "discharge", commit=True)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["committed"] and body["changed"] and body["alert_created"] is True
    assert [w["week_number"] for w in body["weeks"]] == [0, 1, 2, 3]

    row = db["patient_worklist"].find_one({"patient_id": A1, "batch_date": LATEST})
    assert (row["risk_score"], row["risk_band"]) == (85.0, band_of(85.0))
    assert row["previous"]["risk_score"] == old and row["driver_1"] == "Age: 71 (raises risk)"

    new = series(db)
    want = expected_weeks(A1, 85.0)
    assert new[0]["risk_score"] == 85.0 and new[0]["driver_1"] == "Age: 71 (raises risk)"
    assert new[1]["risk_score"] == want[1] != old_series[1]["risk_score"]
    assert new[2]["risk_score"] == new[1]["risk_score"] and new[2]["red_flags"] == []
    assert new[3]["risk_score"] == want[3]
    assert (new[3]["reported_by"], new[3]["remarks"], new[3]["monitoring"], new[3]["note"]) == \
        ("patient", "x", OBS[3], "note 3")
    assert new[1]["monitoring"] == OBS[1] and new[1]["note"] == "note 1"
    assert row["current_score"] == new[3]["risk_score"]

    post(client, A1, "discharge", commit=True)
    assert db["risk_registry"].count_documents({"patient_id": A1, "discharge_date": "2026-04-10",
                                                "model_version": scoring_service.BUILTIN_VERSION}) == 1
    log = list(db["rescore_log"].find({"patient_id": A1}))
    assert len(log) == 2 and log[0]["target"] == "discharge" and log[0]["by"] == "doc.a@a.test"
    assert (log[0]["old"]["score"], log[0]["new"]["score"]) == (old, 85.0) and log[0]["weeks_changed"] >= 2
    assert log[0]["hospital_id"] == "hosp-a" and log[0]["inputs"] == "stored_features"
    assert db["alerts"].find_one({"patient_id": A1, "new_score": 85.0, "old_score": old}) is not None


def test_the_weekly_trend_alone_leaves_the_discharge_score(main, world, real_row):  # noqa: F811
    score, _ = seed(world, real_row)
    db = world["db"]
    db["weekly_monitoring"].update_one({"patient_id": A1, "week_number": 1}, {"$set": {"risk_score": 5.0}})
    r = post(as_user(main, world, "doc.a@a.test"), A1, "weekly", commit=True)
    assert r.status_code == 200 and r.json()["changed"] is True
    assert series(db)[1]["risk_score"] == expected_weeks(A1, score)[1]
    assert db["patient_worklist"].find_one({"patient_id": A1, "batch_date": LATEST})["risk_score"] == score
    assert db["rescore_log"].find_one({"patient_id": A1})["target"] == "weekly"


def test_the_browser_cannot_send_a_score(main, world, real_row):  # noqa: F811
    score, _ = seed(world, real_row)
    client = as_user(main, world, "doc.a@a.test")
    r = post(client, A1, "discharge", commit=True, risk_score=1.0, score=1.0, risk_band="Low", base=1.0)
    assert r.status_code == 200 and r.json()["proposed"]["score"] == score
    assert world["db"]["patient_worklist"].find_one({"patient_id": A1, "batch_date": LATEST})["risk_score"] == score
    assert post(client, A1, "nonsense").status_code == 422


def test_without_stored_inputs_only_the_weekly_trend_can_be_re_evaluated(main, world):  # noqa: F811
    db, client = world["db"], as_user(main, world, "cm@a.test")
    db["weekly_monitoring"].insert_one({"patient_id": A2, "week_number": 0, "risk_score": 40.0,
                                        "risk_band": "Medium", "clinical_group": GROUP})
    opts = client.get(ROUTE.format(A2)).json()
    assert opts["discharge"]["possible"] is False and opts["discharge"]["inputs"] is None
    assert opts["discharge"]["reason"] == ("This patient's model inputs have not been loaded yet, so the "
                                           "discharge score can't be re-evaluated. The weekly trend still can.")
    assert opts["weekly"]["possible"] is True
    r = post(client, A2, "discharge", commit=True)
    assert r.status_code == 409 and "not been loaded" in r.json()["detail"]
    assert db["rescore_log"].count_documents({}) == 0
    assert post(client, A2, "weekly").status_code == 200


def test_a_patient_without_a_week_0_has_no_weekly_trend_to_re_evaluate(main, world):  # noqa: F811
    client = as_user(main, world, "cm@a.test")
    opts = client.get(ROUTE.format(A3)).json()
    assert opts["weekly"]["possible"] is False and opts["weekly"]["reason"]
    assert post(client, A3, "weekly").status_code == 409


def test_a_manual_patient_is_re_scored_from_its_saved_inputs(main, world, monkeypatch):  # noqa: F811
    db, client = world["db"], as_user(main, world, "cm@a.test")
    typed = {"anchor_age": 71, "gender": "F", "los_days": 5, "discharge_date": "2026-04-10"}
    db["patient_worklist"].update_many({"patient_id": A3}, {"$set": {"source": "manual", "raw_inputs": typed}})
    db["weekly_monitoring"].insert_one({"patient_id": A3, "week_number": 0, "risk_score": 50.0,
                                        "risk_band": "High", "clinical_group": GROUP, "hospital_id": "hosp-a"})
    seen = []

    def fake(inputs):
        seen.append(dict(inputs))
        return {"risk_score": 33.0, "risk_band": "Medium", "drivers": [], "driver_1_raw": "Age: 71",
                "driver_2_raw": "", "driver_3_raw": ""}
    monkeypatch.setattr(mimic_scoring, "score_manual_entry", fake)

    opts = client.get(ROUTE.format(A3)).json()
    assert opts["discharge"]["inputs"] == "manual" and opts["discharge"]["possible"] is True
    r = post(client, A3, "discharge", commit=True)
    assert r.status_code == 200, r.text
    assert seen[-1] == {"anchor_age": 71, "gender": "F", "los_days": 5}
    assert db["patient_worklist"].find_one({"patient_id": A3, "batch_date": LATEST})["risk_score"] == 33.0
    assert series(db, A3)[0]["risk_score"] == 33.0


def test_a_demo_shifted_patient_updates_its_original_admission(main, world, real_row, monkeypatch):  # noqa: F811
    seed(world, real_row)
    db, client = world["db"], as_user(main, world, "doc.a@a.test")
    db["patient_worklist"].update_many({"patient_id": A1}, {"$set": {"discharge_date": "2026-04-10"}})
    db["demo_prep"].insert_one({"hospital_id": "hosp-a", "patient_id": A1, "shift_days": 20})
    original = "2026-03-21"
    version = scoring_service.BUILTIN_VERSION
    db["risk_registry"].insert_one({"patient_id": A1, "discharge_date": original, "batch_date": original,
                                    "model_version": version, "risk_score": 10.0, "risk_band": "Low"})
    fixed(monkeypatch, 41.0, band_of(41.0))
    assert post(client, A1, "discharge", commit=True).status_code == 200
    rows = list(db["risk_registry"].find({"patient_id": A1, "model_version": version}))
    assert len(rows) == 1
    assert rows[0]["discharge_date"] == original and rows[0]["batch_date"] == original
    assert rows[0]["risk_score"] == 41.0


@pytest.mark.parametrize("email, pid", [("claims@acme.test", A1), ("me@patient.test", "MIMIC-1104")])
def test_insurers_and_patients_cannot_re_evaluate(main, world, email, pid):  # noqa: F811
    client = as_user(main, world, email)
    for commit in (False, True):
        assert post(client, pid, "weekly", commit=commit).status_code == 403
    assert world["db"]["rescore_log"].count_documents({}) == 0


def test_a_hospital_admin_needs_a_reason(main, world, real_row):  # noqa: F811
    seed(world, real_row)
    client = as_user(main, world, "admin@a.test")
    r = post(client, A1, "weekly", commit=True)
    assert r.status_code == 403 and r.json()["detail"]["code"] == access.REASON_REQUIRED["code"]
    assert client.get(ROUTE.format(A1)).status_code == 403
    assert world["db"]["rescore_log"].count_documents({}) == 0
    assert client.post(f"/api/patients/{A1}/open", json={"reason": "audit"}).status_code == 200
    assert post(client, A1, "weekly").status_code == 200


def test_an_ingested_patient_uses_the_worker_path(main, world, token, monkeypatch):  # noqa: F811
    db, client = world["db"], as_user(main, world, "cm@a.test")
    send(main, token, bundle())
    worker.run(db, once=True)
    opts = client.get(ROUTE.format(INGESTED)).json()
    assert opts["source"] == "ingest" and opts["discharge"]["possible"] is True
    assert opts["discharge"]["inputs"] == "ingest"

    before = snapshot(db, *WRITTEN, "ingest_stays")
    for target in ("discharge", "weekly"):
        r = post(client, INGESTED, target)
        assert r.status_code == 200, r.text
    assert snapshot(db, *WRITTEN, "ingest_stays") == before

    calls = []
    real = scoring_service.score_discharges
    monkeypatch.setattr(scoring_service, "score_discharges",
                        lambda *a, **k: calls.append(a) or real(*a, **k))
    r = post(client, INGESTED, "discharge", commit=True)
    assert r.status_code == 200, r.text
    assert calls == [(db, "hosp-a", ["E-1"])]
    assert db["rescore_log"].find_one({"patient_id": INGESTED})["inputs"] == "ingest"


def test_the_split_out_compute_step_writes_nothing(main, world, token):  # noqa: F811
    db = world["db"]
    send(main, token, bundle("E-1"), died("E-2", "2026-04-20T08:00", "2026-04-24T12:00"))
    worker.run(db, once=True)
    before = {n: sorted(map(str, db[n].find({}, {"_id": 0}))) for n in db.list_collection_names()}
    got = scoring_service._compute_discharges(db, "hosp-a", ["E-1", "E-2"])
    assert set(got["targets"]) == {"E-1", "E-2"}
    after = {n: sorted(map(str, db[n].find({}, {"_id": 0}))) for n in db.list_collection_names()}
    assert after == before


def test_update_re_scores_on_the_server(main, world, real_row):  # noqa: F811
    old, _ = seed(world, real_row)
    db, client = world["db"], as_user(main, world, "doc.a@a.test")
    edit = {"anchor_age": 90}
    expected = mimic_scoring.score_features(mimic_scoring.overlay_manual(real_row, edit))["risk_score"]
    assert expected != old

    r = client.post(f"/api/patients/{A1}/predict-update", json={**edit, "discharge_date": "2026-04-10"})
    assert r.status_code == 200 and r.json()["risk_score"] == expected and len(r.json()["weeks"]) == 4
    assert db["model_inputs"].find_one({"patient_id": A1})["features"]["anchor_age"] == real_row["anchor_age"]

    r = client.post(f"/api/patients/{A1}/update", json={
        "raw_inputs": edit, "risk_score": 1.0, "risk_band": "Low", "driver_1": "forged"})
    assert r.status_code == 200, r.text
    assert (r.json()["old_score"], r.json()["new_score"]) == (old, expected)
    row = db["patient_worklist"].find_one({"patient_id": A1, "batch_date": LATEST})
    assert row["risk_score"] == expected and row["driver_1"] != "forged"
    doc = db["model_inputs"].find_one({"patient_id": A1})
    assert doc["features"]["anchor_age"] == 90 and doc["loaded_features"] == real_row
    assert series(db)[0]["risk_score"] == expected
    log = db["rescore_log"].find_one({"patient_id": A1})
    assert log["target"] == "edit" and log["inputs"] == "stored_features"
    client.post(f"/api/patients/{A1}/update", json={"raw_inputs": {"anchor_age": 91}})
    again = db["model_inputs"].find_one({"patient_id": A1})
    assert again["features"]["anchor_age"] == 91 and again["loaded_features"] == real_row


def test_an_ingested_patient_cannot_be_edited_by_hand(main, world, token):  # noqa: F811
    db, client = world["db"], as_user(main, world, "doc.a@a.test")
    send(main, token, bundle())
    worker.run(db, once=True)
    db["care_actions"].update_one({"patient_id": INGESTED}, {"$set": {"assigned_doctor_id": "DR-A"}})
    before = snapshot(db, *WRITTEN, "ingest_stays")
    shown = client.get(f"/api/patients/{INGESTED}/edit")
    assert shown.status_code == 200 and shown.json()["editable"] is False and shown.json()["reason"]
    assert client.post(f"/api/patients/{INGESTED}/update", json={"raw_inputs": {"anchor_age": 80}}).status_code == 409
    assert client.post(f"/api/patients/{INGESTED}/predict-update", json={"anchor_age": 80}).status_code == 409
    assert snapshot(db, *WRITTEN, "ingest_stays") == before

"""The analytics pages count only the caller's own patients (core/scoped.py).
claims@acme.test's members are patients 0 and 4; the superadmin sees all."""
from tests.test_isolation import ALL, client, world  # noqa: F401

MEMBERS = {0, 4}


def own(world):
    pts = list(world["db"].patients.find({"patient_idx": {"$in": list(MEMBERS)}}, {"cluster": 1}))
    return {c: sum(1 for p in pts if p["cluster"] == c) for c in range(4)}


def test_an_insurer_sees_only_its_members_on_every_analytics_page(world):
    c, mine = client(world, "claims@acme.test"), own(world)
    segs = c.get("/api/segments").json()["segments"]
    assert {s["cluster"]: s["n"] for s in segs} == {k: v for k, v in mine.items() if k in {s["cluster"] for s in segs}}
    assert sum(r["n"] for r in c.get("/api/cost-effectiveness").json()["cea"]) == len(MEMBERS)
    budget = c.post("/api/budget-impact", json={"dropout_reduction_pct": 15, "intervention_cost_per_patient": 500,
                                                "population_scope_pct": 100}).json()
    assert sum(s["n_in_scope"] for s in budget["segments"]) == len(MEMBERS)
    assert c.get("/api/consequence/payer-roi").json()["n_patients_total"] == len(MEMBERS)
    surv = c.get("/api/survival").json()
    assert surv["n_patients"] <= len(MEMBERS) and len(surv["curves"]) == 4 and len(surv["median_survival"]) == 4


def test_the_superadmin_still_sees_everyone(world):
    c = client(world, "ops@team.test")
    assert sum(s["n"] for s in c.get("/api/segments").json()["segments"]) == ALL
    assert sum(r["n"] for r in c.get("/api/cost-effectiveness").json()["cea"]) == ALL


def test_a_doctor_sees_only_their_patients_in_segments_and_survival(world):
    c = client(world, "doc@a.test")
    total = sum(s["n"] for s in c.get("/api/segments").json()["segments"])
    assert 0 < total < ALL
    assert c.get("/api/survival").json()["n_patients"] <= total
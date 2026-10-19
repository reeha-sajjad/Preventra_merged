"""scripts/seed_member_ids.py: demo member IDs in both apps, unique per insurer."""
import mongomock

from api import auth, member_ids
from scripts import seed_member_ids as seed


def test_member_ids_are_made_once_unique_and_valid():
    client = mongomock.MongoClient()
    client[auth.IDENTITY_DB]["insurers"].insert_many([{"_id": "medicare", "name": "Medicare"},
                                                     {"_id": "medicaid", "name": "Medicaid"}])
    client["glp1_analytics"]["patient_access"].insert_many(
        [{"patient_idx": i, "insurer_id": ["medicare", "medicaid", None][i % 3]} for i in range(60)]
        + [{"patient_idx": 99, "insurer_id": "medicaid", "insurer_member_id": "KEEPME123"}])
    client["neuroshield"]["care_actions"].insert_many(
        [{"patient_id": f"P-{i}", "insurer_id": "medicare"} for i in range(20)])
    assert seed.run(client, "neuroshield", "glp1_analytics", dry=True)["glp1"]["medicare"] == 20
    assert client["glp1_analytics"]["patient_access"].count_documents({"insurer_member_id": {"$exists": True}}) == 1
    out = seed.run(client, "neuroshield", "glp1_analytics")
    assert out["medicare"] == ["medicare"] and out["readmissions"]["medicare"] == 20
    assert client[auth.IDENTITY_DB]["insurers"].find_one({"_id": "medicare"})["member_id_format"] == "mbi"
    rows = list(client["glp1_analytics"]["patient_access"].find({"insurer_member_id": {"$exists": True}}))
    assert len(rows) == 41 and len({(r["insurer_id"], r["insurer_member_id"]) for r in rows}) == 41
    assert all(member_ids.MBI.match(r["insurer_member_id"]) for r in rows if r["insurer_id"] == "medicare")
    assert client["glp1_analytics"]["patient_access"].find_one({"patient_idx": 99})["insurer_member_id"] == "KEEPME123"
    assert sum(seed.run(client, "neuroshield", "glp1_analytics")["glp1"].values()) == 0      # nothing twice
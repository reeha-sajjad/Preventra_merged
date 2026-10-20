"""
Unit tests for the linkable values pulled out of a chatbot query result.

Only values the query actually returned may become links, so these check the
payload shapes the query layer produces, not anything the model writes.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from api import chatbot_service
from api.chatbot_links import extract_entities
from api.chatbot_llm import GENERAL_QUESTION


def _by_kind(entities, kind):
    return {e["text"]: e["value"] for e in entities if e["kind"] == kind}


def test_patient_ids_are_taken_from_patient_id_keys():
    result = [{"patient_id": 34248474, "risk_score": 81.2, "risk_band": "High"},
              {"patient_id": "20011234", "risk_score": 77.0}]
    assert _by_kind(extract_entities(result), "patient") == {
        "34248474": "34248474", "20011234": "20011234"}


def test_other_numbers_are_not_patient_ids():
    result = {"cohort_size": 1250, "count": 14, "results": [{"patients": 12345678}]}
    assert _by_kind(extract_entities(result), "patient") == {}


def test_condition_labels_map_to_their_filter_key():
    result = {"results": [{"condition": "Heart failure", "patients": 40},
                          {"condition": "Kidney disease", "patients": 22}]}
    assert _by_kind(extract_entities(result), "condition") == {
        "Heart failure": "heart_failure", "Kidney disease": "renal"}


def test_raw_group_keys_are_labelled():
    result = {"patient_id": 1, "clinical_groups": ["diabetes"]}
    assert _by_kind(extract_entities(result), "condition") == {"Diabetes": "diabetes"}


def test_diagnoses_become_searches():
    result = {"results": [{"diagnosis": "Sepsis, unspecified organism", "patients": 9}]}
    assert _by_kind(extract_entities(result), "diagnosis") == {
        "Sepsis, unspecified organism": "Sepsis, unspecified organism"}


class _Doctors:
    def __init__(self, rows):
        self.rows = rows
        self.query = None

    def find(self, query, projection):
        self.query = query
        return [r for r in self.rows if r["name"] in query["name"]["$in"]]


class _DB:
    def __init__(self, rows):
        self.doctors = _Doctors(rows)

    def __getitem__(self, name):
        return self.doctors


def test_doctor_names_resolve_to_registry_ids():
    db = _DB([{"name": "Dr. Sarah Whitfield", "doctor_id": "DR-1"},
              {"name": "Dr. Amir Haddad", "doctor_id": "DR-2"}])
    result = {"workload": [{"doctor": "Dr. Sarah Whitfield", "open_alerts": 3},
                           {"doctor": "unrouted", "open_alerts": 1}]}
    assert _by_kind(extract_entities(result, db), "doctor") == {"Dr. Sarah Whitfield": "DR-1"}
    assert db.doctors.query["name"]["$in"] == ["Dr. Sarah Whitfield"]


def test_doctor_lookup_failure_drops_only_doctor_links():
    class Broken:
        def __getitem__(self, name):
            raise RuntimeError("down")

    result = {"doctors": [{"name": "Dr. X"}], "patient_id": 7}
    entities = extract_entities(result, Broken())
    assert _by_kind(entities, "patient") == {"7": "7"}
    assert _by_kind(entities, "doctor") == {}


def test_no_result_no_entities():
    assert extract_entities(None) == []


def test_answer_turn_carries_entities(monkeypatch):
    monkeypatch.setattr(chatbot_service.chatbot_queries, "describe_vocabularies", lambda db: "")
    monkeypatch.setattr(chatbot_service, "select_function_call",
                        lambda *a, **k: {"name": "get_top_risk_patients", "args": {"n": 1}})
    monkeypatch.setattr(chatbot_service.chatbot_queries, "get_top_risk_patients",
                        lambda db, n: [{"patient_id": 42, "risk_score": 90}])
    monkeypatch.setattr(chatbot_service, "generate_natural_language_answer",
                        lambda *a, **k: "Patient 42 is highest.")
    turn = chatbot_service.answer_turn("top patient?", db=None)
    assert turn["answer"] == "Patient 42 is highest."
    assert turn["entities"] == [{"kind": "patient", "text": "42", "value": "42"}]


def test_answer_turn_without_a_query_has_no_entities(monkeypatch):
    monkeypatch.setattr(chatbot_service.chatbot_queries, "describe_vocabularies", lambda db: "")
    monkeypatch.setattr(chatbot_service, "select_function_call", lambda *a, **k: GENERAL_QUESTION)
    monkeypatch.setattr(chatbot_service, "generate_general_answer", lambda *a, **k: "general")
    assert chatbot_service.answer_turn("what is sepsis", db=None) == {
        "answer": "general", "entities": []}

"""
Orchestrates a single chatbot turn:
  1. Ask Gemini to select one of the predefined functions + arguments.
  2. Execute that function against MongoDB ourselves (never LLM-generated queries).
  3. Ask Gemini to phrase the result as a natural-language answer.

A question with no function behind it takes a fourth route: if it is about
medicine, readmission or the dashboard it is answered from knowledge under a
separate, tightly-bounded instruction; only genuinely unrelated questions are
refused. The recent transcript rides along with every step so follow-ups
("what about the top 10?") resolve against what was already asked.

Every failure mode is caught here and turned into a clean, user-safe message.
Raw exceptions/stack traces never propagate to the caller.
"""

import json
import re

from api import chatbot_queries
from api.chatbot_links import extract_entities
from api.llm import LLMUnavailable
from api.chatbot_llm import (
    GENERAL_QUESTION,
    generate_general_answer,
    generate_natural_language_answer,
    select_function_call,
)

# The transcript is replayed by the browser, so it is neither trustworthy nor
# bounded in size. Both limits are enforced here rather than at the edge, so
# every caller of answer_question gets them.
MAX_HISTORY_MESSAGES = 8
MAX_HISTORY_CHARS = 1500

# A rejected call is not a dead end: the validator already produced a message
# naming the valid fields or values, and handing that back is usually enough for
# the model to fix itself. One retry, because a second failure after an explicit
# correction means the question does not map to anything we have, and looping
# costs the user latency for nothing.
MAX_CORRECTION_ATTEMPTS = 1

CANNOT_ANSWER_MESSAGE = (
    "I can't answer that one. Here is what I can do:\n"
    "• How many patients are in a band, or above or below a risk score\n"
    "• The most common conditions, diagnoses or risk drivers in any of those groups\n"
    "• The highest-risk patients right now\n"
    "• Why one patient is at risk, or their full record\n"
    "• How risk has trended over time\n"
    "• List individual patients with the details you ask for\n"
    "• Which clinicians are registered, what they cover, and how many alerts are "
    "waiting on them\n"
    "• Medical questions — what a condition, lab result or medication is, and what it "
    "means for readmission\n"
    "Try: \u201cwhat is the most common condition among patients above 80?\u201d"
)
GENERIC_ERROR_MESSAGE = "Sorry, I couldn't process that question. Please try again in a moment."
RATE_LIMITED_MESSAGE = (
    "I'm being rate-limited right now — too many questions in a short window. "
    "Wait about a minute and ask again."
)
UNAVAILABLE_MESSAGE = "The assistant is switched off. Ask your administrator to choose an AI model in Settings."


def _llm_error_message(exc: Exception) -> str:
    """
    Distinguish a quota refusal from a real fault.

    Every turn costs two model calls, so a few questions in quick succession can
    exhaust a per-minute quota. Reporting that as a generic failure sends the
    user hunting for a bug that will clear itself in sixty seconds.
    """
    if isinstance(exc, LLMUnavailable):
        return UNAVAILABLE_MESSAGE
    text = str(exc)
    if "RESOURCE_EXHAUSTED" in text or "429" in text:
        return RATE_LIMITED_MESSAGE
    return GENERIC_ERROR_MESSAGE


def _clean_history(history) -> list:
    """Keep the last few well-formed turns and drop everything else."""
    cleaned = []
    for message in (history or [])[-MAX_HISTORY_MESSAGES:]:
        if not isinstance(message, dict):
            continue
        text = str(message.get("text") or "").strip()[:MAX_HISTORY_CHARS]
        if text:
            cleaned.append({"role": "user" if message.get("role") == "user" else "bot",
                            "text": text})
    return cleaned


def _cohort_args(args: dict) -> dict:
    """
    Pull the shared cohort filter out of whatever the model supplied.

    Arguments arrive from an LLM, so a value may be absent, null, or a string
    where a number belongs. Coercing here keeps every cohort function free of
    the same four defensive lines, and anything genuinely invalid still raises
    ValueError inside the query layer where the bounds are defined.
    """
    def num(key):
        value = args.get(key)
        return None if value is None else float(value)

    filters = args.get("filters")
    return {
        "band": args.get("band"),
        "min_score": num("min_score"),
        "max_score": num("max_score"),
        "filters": list(filters) if isinstance(filters, list) and filters else None,
    }


# Numbers worth checking. One and two digit figures are skipped: they appear in
# ordinary prose ("all 5 of them", "a 2 kg gain") and in dates, and flagging
# them would bury the real cases in noise.
_CITED_NUMBER = re.compile(r"(\d[\d,]{2,}(?:\.\d+)?|\d+\.\d+)(%?)")


def _report_ungrounded(answer: str, payload, function_name: str) -> None:
    """
    Check that every figure quoted in the answer appears in the data it came
    from, and log the ones that do not.

    Detection, not prevention. The phrasing step is already instructed never to
    invent a number, and blocking an answer on a regular expression would reject
    correct ones - a model may legitimately write 62% where the payload holds
    62.0. What this buys is evidence: if the instruction ever stops holding,
    there is a log line saying so instead of a plausible wrong number reaching a
    clinician unremarked.
    """
    if not answer or payload is None:
        return
    haystack = json.dumps(payload, default=str)
    normalised = haystack.replace(",", "")
    unsupported = []
    for token, percent_sign in set(_CITED_NUMBER.findall(answer)):
        # Percentages are routinely derived rather than returned: "750 patients
        # (100%)" is arithmetic on a count the payload does carry, and flagging
        # it buries the case that matters. A fabricated percentage is caught
        # anyway, because the count it was derived from would be wrong.
        if percent_sign:
            continue
        bare = token.replace(",", "")
        if bare in normalised:
            continue
        # A rounded restatement of a real figure is grounded enough.
        try:
            if f"{float(bare):.1f}" in normalised or str(int(float(bare))) in normalised:
                continue
        except ValueError:
            pass
        unsupported.append(token)
    if unsupported:
        print(f"[chatbot] UNGROUNDED figures in answer from '{function_name}': "
              f"{', '.join(sorted(unsupported))}")


# Hospital admins and insurers see the overview layer of their patients, and
# one patient's clinical layer only after opening it with a reason (see
# api/access.py). Questions are answered from the same layers: counts and risk
# lists always; a patient's diagnoses and drivers once that patient is open.
# The view passed in says which applies - `redacted` and `opened` are set on
# access.ScopedDB; anything else (a test's raw database) is unrestricted.
# The assigned doctor is the care team the Patients and Staff pages already
# show these roles, not part of the clinical layer.
OVERVIEW_FIELDS = frozenset({"current_score", "discharge_score", "trend_delta", "weeks_tracked",
                             "current_band", "risk_band", "monitoring_status", "doctor",
                             "patient_id"})
_ONE_PATIENT = ("get_patient_drivers", "get_patient_details")

NEEDS_REASON_MESSAGE = ("That is part of the patient's clinical details. Open the patient from "
                        "the Patients page and give a reason first, then ask again.")
LIST_BY_RISK_ONLY_MESSAGE = ("I can list your patients by risk and trend, but not by diagnosis, "
                             "condition or other clinical details - those open one patient at a "
                             "time, with a reason, from the Patients page.")


def guard(name: str, args: dict, db) -> None:
    """Refuse a function call that would reach past the caller's layer."""
    if not getattr(db, "redacted", False):
        return
    if name in _ONE_PATIENT and str(args.get("patient_id", "")) not in db.opened:
        raise LookupError(NEEDS_REASON_MESSAGE)
    if name == "list_patients":
        used = {f.get("field") for f in (args.get("filters") or []) if isinstance(f, dict)}
        used |= {str(f) for f in (args.get("fields") or [])}
        used.add(args.get("sort_by") or "current_score")
        if used - OVERVIEW_FIELDS - {None, "patient_id"}:
            raise LookupError(LIST_BY_RISK_ONLY_MESSAGE)


def _dispatch(name: str, args: dict, db):
    guard(name, args, db)
    if name == "count_patients":
        return chatbot_queries.count_patients(db, **_cohort_args(args))

    if name == "run_cohort_query":
        bucket = args.get("bucket_size")
        top_n = args.get("top_n")
        return chatbot_queries.run_cohort_query(
            db,
            filters=args.get("filters") or [],
            group_by=args.get("group_by") or None,
            bucket_size=float(bucket) if bucket is not None else None,
            metric=args.get("metric") or "count",
            metric_field=args.get("metric_field") or None,
            top_n=int(top_n) if top_n is not None else None)

    if name == "list_patients":
        limit = args.get("limit")
        fields = args.get("fields")
        return chatbot_queries.list_patients(
            db,
            filters=args.get("filters") or [],
            sort_by=args.get("sort_by") or "current_score",
            order=args.get("order") or "desc",
            fields=list(fields) if fields else None,
            limit=int(limit) if limit is not None else None)

    if name == "list_registered_doctors":
        return chatbot_queries.list_registered_doctors(
            db,
            # The model reaches for doctor_name as often as name; accepting both
            # stops a named-doctor question silently returning the whole list.
            name=args.get("name") or args.get("doctor_name") or None,
            specialty=args.get("specialty") or None,
            clinical_group=args.get("clinical_group") or None,
            include_inactive=bool(args.get("include_inactive")))

    if name == "get_doctor_workload":
        return chatbot_queries.get_doctor_workload(
            db, doctor_name=args.get("doctor_name") or None)

    if name == "get_alert_routing":
        return chatbot_queries.get_alert_routing(
            db, clinical_group=args.get("clinical_group") or None)

    if name == "get_risk_change_since_discharge":
        min_change = args.get("min_change")
        return chatbot_queries.get_risk_change_since_discharge(
            db, band=args.get("band"),
            min_change=float(min_change) if min_change is not None else None,
            direction=args.get("direction") or "increase",
            filters=_cohort_args(args)["filters"])

    if name in ("get_common_conditions", "get_common_diagnoses", "get_common_drivers",
                "get_condition_overlap"):
        top_n = args.get("top_n")
        return getattr(chatbot_queries, name)(
            db, **_cohort_args(args),
            top_n=int(top_n) if top_n is not None else None)

    if name == "count_patients_by_risk_band":
        return chatbot_queries.count_patients_by_risk_band(db, risk_band=args.get("risk_band"))

    if name == "list_patients_by_risk_threshold":
        limit = args.get("limit")
        return chatbot_queries.list_patients_by_risk_threshold(
            db,
            operator=args["operator"],
            threshold=float(args["threshold"]),
            limit=int(limit) if limit is not None else None,
        )

    if name == "get_top_risk_patients":
        return chatbot_queries.get_top_risk_patients(db, n=int(args["n"]))

    if name == "get_patient_drivers":
        return chatbot_queries.get_patient_drivers(db, patient_id=str(args["patient_id"]))

    if name == "get_patient_details":
        return chatbot_queries.get_patient_details(db, patient_id=str(args["patient_id"]))

    if name == "get_risk_trend_over_time":
        return chatbot_queries.get_risk_trend_over_time(
            db,
            start_date=args.get("start_date"),
            end_date=args.get("end_date"),
            group_by=args.get("group_by", "week"),
        )

    raise ValueError(f"Unrecognized function selection '{name}'.")


def _run_turn(question: str, db, history=None) -> tuple:
    """One turn: the answer text, and the query result it was phrased from
    (None when no query ran - a refusal, an error or a general answer)."""
    question = (question or "").strip()
    if not question:
        return "Please enter a question.", None

    history = _clean_history(history)

    # Read from the data, not hard-coded, so the catalog cannot drift from what
    # the worklist actually contains.
    try:
        vocabularies = chatbot_queries.describe_vocabularies(db)
    except Exception as exc:
        print(f"[chatbot] could not build the value catalog: {exc}")
        vocabularies = ""

    try:
        selection = select_function_call(question, history, vocabularies=vocabularies)
    except Exception as exc:
        print(f"[chatbot] Gemini function selection failed: {exc}")
        return _llm_error_message(exc), None

    if selection is None:
        return CANNOT_ANSWER_MESSAGE, None

    if selection == GENERAL_QUESTION:
        try:
            return generate_general_answer(question, history) or CANNOT_ANSWER_MESSAGE, None
        except Exception as exc:
            print(f"[chatbot] Gemini general answer failed: {exc}")
            return _llm_error_message(exc), None

    name = selection.get("name")
    args = selection.get("args") or {}
    result = None

    for attempt in range(MAX_CORRECTION_ATTEMPTS + 1):
        try:
            result = _dispatch(name, args, db)
            break
        except ValueError as exc:
            # ValueError here is always our own validator, and it always says
            # what was wrong and what the valid options are. That message is far
            # too useful to send only to a log file.
            print(f"[chatbot] invalid arguments selected for '{name}' "
                  f"(attempt {attempt + 1}): {exc}")
            if attempt >= MAX_CORRECTION_ATTEMPTS:
                return CANNOT_ANSWER_MESSAGE, None
            try:
                retry = select_function_call(
                    question, history, vocabularies=vocabularies,
                    correction={"name": name, "args": args, "error": str(exc)})
            except Exception as retry_exc:
                print(f"[chatbot] correction pass failed: {retry_exc}")
                return _llm_error_message(retry_exc), None
            if not isinstance(retry, dict):
                return CANNOT_ANSWER_MESSAGE, None
            name, args = retry.get("name"), retry.get("args") or {}
        except LookupError as exc:
            print(f"[chatbot] lookup failed for '{name}': {exc}")
            return str(exc), None
        except Exception as exc:
            print(f"[chatbot] MongoDB query failed for '{name}': {exc}")
            return "Sorry, something went wrong while retrieving that data. Please try again in a moment.", None

    try:
        answer = generate_natural_language_answer(question, name, result, history)
    except Exception as exc:
        print(f"[chatbot] Gemini answer generation failed: {exc}")
        return _llm_error_message(exc), None

    _report_ungrounded(answer, result, name)
    return answer, result


def answer_question(question: str, db, history=None) -> str:
    return _run_turn(question, db, history)[0]


def answer_turn(question: str, db, history=None) -> dict:
    """
    The answer, plus the values in it that the chat window can link: patient
    ids, conditions, diagnoses and clinicians taken from the data the answer
    was phrased from (see chatbot_links), never from the model's prose.
    """
    answer, result = _run_turn(question, db, history)
    try:
        entities = extract_entities(result, db)
    except Exception as exc:
        print(f"[chatbot] could not extract answer links: {exc}")
        entities = []
    return {"answer": answer, "entities": entities}

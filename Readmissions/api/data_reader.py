"""
Reading the files a hospital uploads, whatever their shape - for a person to
confirm on the Data page (api/data_upload.py).

Given one or more CSVs and what they are about (discharges, or weekly
follow-up), it proposes:

    which file is the discharges and which hold its diagnoses, labs,
        medications, procedures or patients, and the columns that join them
    which column is which of our fields; every other column of the discharges
        and patients files is kept for the hospital's own model
    the outcome column, and which of its values mean "readmitted within 30 days"
    what the hospital's codes mean in ours (Female -> F, Emergency -> EMERGENCY)
    whether dates are written day first
    what is missing that would make predictions better (gaps)

Measuring is plain code: types, uniqueness, missing values, and how far a
column's values overlap another file's key. The AI model - whichever serves
the "data" feature (api/llm.py) - is then asked to improve the labelling, from
column summaries only (never a row), and anything it proposes that does not
check out against the files is dropped. Without a model, the rules stand.
Nothing is stored until a person confirms.
"""
from __future__ import annotations

import json
import re
from typing import Optional

import pandas as pd

from api import llm
from ingestion import profile as data_profile
from ingestion import schema, vocab

KINDS = ("discharges", "weekly")
DETAIL_TABLES = ("diagnoses", "labs", "medications", "procedures", "drg")
MAX_CODE_VALUES = 60
MIN_JOIN = 0.5            # share of a detail file's keys that must be discharges in the main file

_OUTCOME_NAME = re.compile(r"readmi|readmission|outcome|label|target|returned|bounce", re.I)
_OUTCOME_POSITIVE = re.compile(r"^\s*(<\s*30|yes|y|1|1\.0|true|t|readmitted|within\s*30.*|30\s*d.*)\s*$", re.I)
_ID_NAME = re.compile(r"(id|nbr|no|num|number|key)$|^(mrn|uhid|csn|fin)$", re.I)

# Common ways hospitals write our coded values (compared lower-cased, without
# spaces or punctuation). Values that match none are left for the AI model, or
# for the person reviewing.
CODE_SYNONYMS = {
    "sex": {"f": "F", "female": "F", "woman": "F", "m": "M", "male": "M", "man": "M",
            "unknown": "UNKNOWN", "unknowninvalid": "UNKNOWN", "invalid": "UNKNOWN", "u": "UNKNOWN",
            "other": "OTHER", "o": "OTHER"},
    "race_ethnicity": {"caucasian": "WHITE", "white": "WHITE", "africanamerican": "BLACK", "black": "BLACK",
                       "blackafricanamerican": "BLACK", "asian": "ASIAN", "hispanic": "HISPANIC",
                       "latino": "HISPANIC", "hispaniclatino": "HISPANIC", "other": "OTHER",
                       "nativeamerican": "AMERICAN_INDIAN", "americanindian": "AMERICAN_INDIAN",
                       "pacificislander": "PACIFIC_ISLANDER", "unknown": "UNKNOWN"},
    "admission_type": {"emergency": "EMERGENCY", "er": "EMERGENCY", "ed": "EMERGENCY", "emer": "EMERGENCY",
                       "urgent": "URGENT", "elective": "ELECTIVE", "planned": "ELECTIVE",
                       "observation": "OBSERVATION", "daysurgery": "SAME_DAY_SURGERY",
                       "samedaysurgery": "SAME_DAY_SURGERY"},
    "discharge_disposition": {"home": "HOME", "discharged home": "HOME", "dischargedhome": "HOME",
                              "dischargedtohome": "HOME", "homehealth": "HOME_WITH_SERVICES",
                              "homewithservices": "HOME_WITH_SERVICES", "snf": "SNF",
                              "skillednursingfacility": "SNF", "skillednursing": "SNF", "rehab": "REHAB",
                              "rehabilitation": "REHAB", "ltac": "LTAC", "ama": "AMA",
                              "leftagainstmedicaladvice": "AMA", "transfer": "TRANSFER",
                              "transferred": "TRANSFER", "hospice": "HOSPICE", "expired": "DIED",
                              "died": "DIED", "dead": "DIED", "death": "DIED", "other": "OTHER"},
    "payer_class": {"medicare": "MEDICARE", "medicaid": "MEDICAID", "private": "PRIVATE",
                    "commercial": "PRIVATE", "selfpay": "SELF_PAY", "self": "SELF_PAY", "other": "OTHER"},
    "marital_status": {"single": "SINGLE", "married": "MARRIED", "divorced": "DIVORCED",
                       "widowed": "WIDOWED", "unknown": "UNKNOWN"},
}


def _norm(text) -> str:
    return re.sub(r"[^a-z0-9]", "", str(text).lower())


# ------------------------------------------------------------------ reading
def read_frame(path: str) -> pd.DataFrame:
    """A CSV as text cells, empty cells empty - the same reading every upload gets."""
    try:
        frame = pd.read_csv(path, dtype=str, keep_default_na=False, encoding="utf-8-sig", low_memory=False)
    except UnicodeDecodeError:
        frame = pd.read_csv(path, dtype=str, keep_default_na=False, encoding="latin-1", low_memory=False)
    frame.columns = [str(c).strip() for c in frame.columns]
    return frame


def _clean(series: pd.Series) -> pd.Series:
    """The column's real values: missing markers (?, NA, NULL...) dropped."""
    s = series.astype(str).str.strip()
    return s[(s != "") & ~s.str.lower().isin(data_profile.MISSING_TOKENS)]


def column_summary(series: pd.Series) -> dict:
    """What a column looks like, without any of its values except those of a
    short category list: what is safe to show an AI model and a person."""
    values = _clean(series)
    n, unique = len(series), int(values.nunique())
    out = {"missing_pct": round(100 * (1 - len(values) / max(n, 1)), 1), "n_unique": unique,
           "unique_ratio": round(unique / max(len(values), 1), 3)}
    numeric = pd.to_numeric(values, errors="coerce")
    if len(values) and numeric.notna().mean() > 0.95:
        out["kind"] = "number"
        out["range"] = [float(numeric.min()), float(numeric.max())]
    elif len(values) and _looks_like_dates(values):
        out["kind"] = "date"
    elif unique <= 30:
        out["kind"] = "category"
    elif out["unique_ratio"] > 0.9:
        out["kind"] = "identifier"
    else:
        out["kind"] = "text" if values.str.len().mean() > 30 else "code"
    if data_profile._PERSONAL.search(str(series.name)):
        out["personal"] = True                      # never kept for a model, never shown
    elif unique <= 30:
        out["values"] = {str(k): int(v) for k, v in values.value_counts().head(30).items()}
    return out


def _looks_like_dates(values: pd.Series) -> bool:
    sample = values.head(200)
    if sample.str.len().median() < 6 or not sample.str.contains(r"\d").all():
        return False
    parsed = pd.to_datetime(sample, errors="coerce", format="mixed", dayfirst=False)
    return parsed.notna().mean() > 0.9


def dayfirst(frames: dict) -> Optional[bool]:
    """True when a date column has a first number above 12 (13/10/2026), False
    when the second one does, None when nothing tells."""
    first = second = 0
    for frame in frames.values():
        for col in frame.columns:
            values = _clean(frame[col]).head(500)
            m = values.str.extract(r"^(\d{1,2})[/.\-](\d{1,2})[/.\-](\d{2,4})")
            if m.dropna().empty:
                continue
            first += int((pd.to_numeric(m[0], errors="coerce") > 12).sum())
            second += int((pd.to_numeric(m[1], errors="coerce") > 12).sum())
    if first and not second:
        return True
    if second and not first:
        return False
    return None


# ------------------------------------------------------------- the proposal
def _table_score(table: str, headers: list) -> int:
    return sum(1 for v in data_profile.suggest(table, headers).values() if v)


def _overlap(main_values: set, series: pd.Series) -> float:
    values = set(_clean(series).unique()[:5000])
    return len(values & main_values) / max(len(values), 1) if values else 0.0


def _main_file(frames: dict, kind: str) -> Optional[str]:
    """The file the others hang off: for discharges, the one whose rows are
    discharges (most encounter fields, then most columns); for weekly, the
    check-ins."""
    table = "encounters" if kind == "discharges" else "weeks"
    named = [f for f in frames if data_profile.detect_table(f) == table]
    candidates = named or list(frames)
    return max(candidates, key=lambda f: (_table_score(table, list(frames[f].columns)), frames[f].shape[1]),
               default=None)


def _key(frame: pd.DataFrame, mapping: dict, field: str, unique: bool) -> Optional[str]:
    """The column holding a key: the mapped one, else an id-like column that is
    unique per row (a visit) or that repeats (a patient)."""
    found = next((c for c, f in mapping.items() if f == field), None)
    if found and not _names(frame[found]):
        return found
    if found:
        mapping[found] = None                    # a column of names is not an identifier
    for col in frame.columns:
        if mapping.get(col):
            continue
        values = _clean(frame[col])
        if len(values) < 0.95 * len(frame) or not _ID_NAME.search(_norm(col)):
            continue
        ratio = values.nunique() / max(len(values), 1)
        if (unique and ratio > 0.99) or (not unique and 0.05 < ratio <= 0.99):
            return col
    return None


def _names(series: pd.Series) -> bool:
    """Mostly "First Last": people's names, never used as a key."""
    sample = _clean(series).head(300)
    return bool(len(sample)) and sample.str.fullmatch(r"[A-Za-z'.\-]+( [A-Za-z'.\-]+)+").mean() > 0.6


def _outcome(frame: pd.DataFrame, mapping: dict) -> Optional[dict]:
    for col in frame.columns:
        if mapping.get(col) or not _OUTCOME_NAME.search(col):
            continue
        values = _clean(frame[col])
        counts = values.value_counts()
        if not 2 <= len(counts) <= 6:
            continue
        positive = [v for v in counts.index if _OUTCOME_POSITIVE.match(v)]
        return {"column": col, "values": {str(k): int(v) for k, v in counts.items()}, "positive": positive}
    return None


def _code_guess(field: str, value: str) -> Optional[str]:
    allowed = vocab.VOCABULARIES[field]
    if value.strip().upper() in allowed:
        return value.strip().upper()
    return CODE_SYNONYMS.get(field, {}).get(_norm(value))


def propose(frames: dict, kind: str, saved: Optional[dict] = None) -> dict:
    """The rules' reading of the files. `saved` is the hospital's profile, so a
    file shaped like last time's reads the way it was confirmed then."""
    saved = saved or {}
    main = _main_file(frames, kind)
    files, problems = [], []
    main_table = "encounters" if kind == "discharges" else "weeks"
    main_keys = {}
    for name in [main] + [f for f in frames if f != main]:
        frame = frames[name]
        headers = list(frame.columns)
        if name == main:
            table = main_table
        else:
            options = (["patients"] + list(DETAIL_TABLES)) if kind == "discharges" else ["patients"]
            named = data_profile.detect_table(name)
            table = named if named in options else max(options, key=lambda t: _table_score(t, headers))
        mapping = data_profile.suggest(table, headers, saved)
        dropped = set(((saved.get("drop") or {}).get(table)) or [])
        entry = {"filename": name, "rows": int(len(frame)), "table": table, "columns": mapping,
                 "drop": [c for c in headers if c in dropped and not mapping.get(c)]}
        if name == main:
            enc = _key(frame, mapping, "encounter_key", unique=True) if kind == "discharges" else None
            pat = _key(frame, mapping, "patient_key", unique=False)
            if enc:
                mapping[enc] = "encounter_key"
            if pat and pat != enc:
                mapping[pat] = "patient_key"
            if kind == "discharges" and not enc:
                problems.append(f"{name}: no column identifies each discharge")
            if not pat:
                problems.append(f"{name}: no column identifies the patient")
            main_keys = {"encounter_key": set(_clean(frame[enc]).unique()[:200000]) if enc else set(),
                         "patient_key": set(_clean(frame[pat]).unique()[:200000]) if pat else set()}
        else:
            join = "patient_key" if table == "patients" else "encounter_key"
            best = max(headers, key=lambda c: _overlap(main_keys.get(join, set()), frame[c]), default=None)
            share = _overlap(main_keys.get(join, set()), frame[best]) if best else 0.0
            if best and share >= MIN_JOIN:
                for col, field in list(mapping.items()):
                    if field == join and col != best:
                        mapping[col] = None
                mapping[best] = join
            else:
                entry["table"] = None
                problems.append(f"{name}: none of its columns matches the {join.replace('_', ' ')}s of {main}; "
                                "it is left out")
            entry["join_share"] = round(share, 2)
        files.append(entry)

    main_entry = files[0] if files else None
    outcome = None
    if main_entry and kind == "discharges":
        found = _outcome(frames[main], main_entry["columns"])
        if found:
            outcome = {"filename": main, **found}

    codes = {}
    for entry in files:
        if not entry["table"]:
            continue
        frame = frames[entry["filename"]]
        for col, field in entry["columns"].items():
            if field in vocab.VOCABULARIES:
                values = _clean(frame[col]).value_counts().head(MAX_CODE_VALUES)
                mapped = (saved.get(field) or {})
                codes[field] = {str(v): (mapped.get(str(v)) or _code_guess(field, str(v))) for v in values.index}

    return {"kind": kind, "files": files, "outcome": outcome, "codes": codes, "dayfirst": dayfirst(frames),
            "problems": problems, "source": "rules"}


# -------------------------------------------------------------- the AI model
_SYSTEM = (
    "You help a hospital load its own data files into a 30-day readmission system. You see "
    "summaries of each file's columns - names, types, missing rates, uniqueness, and the values "
    "of short category lists - never patient rows. Improve the proposed reading: which file is "
    "which table, which column is which field, which column is the readmission outcome and which "
    "of its values mean readmitted within 30 days, and what each coded value means. Use field and "
    "value names exactly as listed. When unsure, leave a column unmapped (null): the hospital's "
    "own model still uses unmapped columns. Never map a column to a field it does not describe. "
    "Answer with JSON only.")

_SHAPE = {
    "type": "object",
    "properties": {
        "files": {"type": "array", "items": {"type": "object", "properties": {
            "filename": {"type": "string"}, "table": {"type": ["string", "null"]},
            "columns": {"type": "object", "additionalProperties": {"type": ["string", "null"]}}},
            "required": ["filename", "table", "columns"]}},
        "outcome": {"type": ["object", "null"], "properties": {
            "column": {"type": "string"}, "positive": {"type": "array", "items": {"type": "string"}}}},
        "codes": {"type": "object", "additionalProperties": {
            "type": "object", "additionalProperties": {"type": ["string", "null"]}}},
        "summary": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["files", "codes", "summary"],
}


def refine(proposal: dict, frames: dict) -> dict:
    """The AI model's reading, merged over the rules' wherever it checks out.
    Returns the proposal unchanged when no model may serve "data" or it fails."""
    if not llm.available("data"):
        return proposal
    summaries = {name: {"rows": int(len(f)), "columns": {c: column_summary(f[c]) for c in f.columns}}
                 for name, f in frames.items()}
    catalogue = {t: {"required_to_store": data_profile.STORE_REQUIRED.get(t, data_profile.required(t)),
                     "fields": data_profile.fields(t)} for t in data_profile.TABLES}
    prompt = (
        f"The hospital says these files are: {proposal['kind']}.\n\n"
        f"OUR TABLES AND FIELDS:\n{json.dumps(catalogue)}\n\n"
        f"OUR CODED VALUES:\n{json.dumps({k: list(v) for k, v in vocab.VOCABULARIES.items()})}\n\n"
        f"THE FILES (column summaries):\n{json.dumps(summaries, default=str)}\n\n"
        f"THE RULES' PROPOSAL:\n{json.dumps({k: proposal[k] for k in ('files', 'outcome', 'codes')}, default=str)}\n\n"
        "Return the improved proposal: every file with its table (or null to leave it out) and a "
        "field (or null) for every one of its columns; the outcome column with its positive values "
        "(or null if none); codes as {field: {their value: our value or null}}; and summary: 2 to 4 "
        "plain sentences for a hospital administrator saying what these files are and anything "
        "they should know.")
    try:
        answer = llm.complete_json("data", system=_SYSTEM, prompt=prompt, schema=_SHAPE, retries=1)
    except Exception as exc:                                             # noqa: BLE001
        return {**proposal, "ai_error": f"{type(exc).__name__}"}
    return _merge(proposal, answer, frames)


def _merge(proposal: dict, answer: dict, frames: dict) -> dict:
    out = json.loads(json.dumps(proposal))
    by_name = {f["filename"]: f for f in out["files"]}
    for f in answer.get("files") or []:
        entry = by_name.get(f.get("filename"))
        if not entry:
            continue
        table = f.get("table")
        if table in data_profile.TABLES or table is None:
            if entry is not out["files"][0]:                     # the main file stays the main file
                entry["table"] = table
        allowed = set(data_profile.fields(entry["table"])) if entry["table"] else set()
        for col, field in (f.get("columns") or {}).items():
            if col in entry["columns"] and (field is None or field in allowed):
                entry["columns"][col] = field
        # One column per field: the first claim stands.
        seen = set()
        for col, field in entry["columns"].items():
            if field and field in seen:
                entry["columns"][col] = None
            elif field:
                seen.add(field)
    main = out["files"][0] if out["files"] else None
    o = answer.get("outcome")
    if main and isinstance(o, dict) and o.get("column") in frames[main["filename"]].columns \
            and not main["columns"].get(o["column"]):
        values = _clean(frames[main["filename"]][o["column"]]).value_counts()
        positive = [p for p in (o.get("positive") or []) if p in values.index]
        if positive and len(values) <= 10:
            out["outcome"] = {"filename": main["filename"], "column": o["column"], "positive": positive,
                              "values": {str(k): int(v) for k, v in values.items()}}
    for field, mapping in (answer.get("codes") or {}).items():
        if field not in out["codes"]:
            continue
        allowed = set(vocab.VOCABULARIES[field])
        for value, ours in (mapping or {}).items():
            if value in out["codes"][field] and (ours in allowed):
                out["codes"][field][value] = ours
    out["summary"] = [str(s)[:400] for s in (answer.get("summary") or [])][:4]
    out["source"] = "ai"
    return out


# --------------------------------------------------------- what's missing
# What makes a readmission prediction better, most important first - from the
# built-in model's strongest inputs and the published LACE and HOSPITAL scores.
GAPS = [
    ("dates", "Admission and discharge dates",
     "Length of stay, how soon after the last discharge a patient came back, prior admissions, and the "
     "30-day outcome itself are all worked out from them."),
    ("diagnoses", "Diagnosis codes (ICD-10 or ICD-9)",
     "They place each patient in a condition group and give the comorbidity score."),
    ("labs", "The last lab results before discharge",
     "Hemoglobin, sodium, creatinine or BUN, albumin, glucose or HbA1c are among the strongest signals."),
    ("medications", "Medications at discharge",
     "How many, and high-risk ones such as anticoagulants, insulin, opioids and diuretics."),
    ("disposition", "Where the patient went on discharge",
     "Home, home with services, a nursing facility or rehab, or against medical advice."),
    ("admission_type", "Whether the admission was an emergency", "Unplanned admissions are readmitted more."),
    ("outcome", "Whether each discharge was followed by a readmission within 30 days",
     "Needed to train the hospital's own model. Past discharges with dates give it automatically."),
    ("followup", "Whether a follow-up appointment was booked within 7 days of discharge",
     "One of the few factors the care team can change."),
    ("ed_visits", "Emergency department visits in the 6 months before admission", "Part of the LACE score."),
]
WEEKLY_GAPS = [
    ("weight", "Weight", "A gain of 2 kg in a week is the classic early warning in heart failure."),
    ("vitals", "Blood pressure, heart rate and oxygen saturation", "Read against the patient's discharge values."),
    ("adherence", "Medication adherence or pharmacy refills", "A missed refill often comes before a readmission."),
    ("symptoms", "Symptoms: breathlessness, swelling, wound, pain, confusion", "The monitoring rules score them."),
]


def gaps(proposal: dict, frames: dict) -> list:
    """What these files lack that would make predictions better, most
    important first - for the report after the reading."""
    mapped = {f for e in proposal["files"] if e.get("table") for f in e["columns"].values() if f}
    tables = {e["table"] for e in proposal["files"] if e.get("table")}
    if proposal["kind"] == "weekly":
        have = {"weight": bool(mapped & {"weight_kg", "weight_change_kg"}),
                "vitals": bool(mapped & {"sbp", "heart_rate", "spo2"}),
                "adherence": bool(mapped & {"adherence_pct", "refill_status"}),
                "symptoms": bool(mapped & {"ankle_swelling", "orthopnoea_pillows", "wound_status", "pain_trend",
                                           "new_confusion", "sputum_change"})}
        return [{"what": what, "why": why} for key, what, why in WEEKLY_GAPS if not have[key]]
    extras = " ".join(_norm(c) for e in proposal["files"] for c, f in e["columns"].items() if not f)
    have = {"dates": {"admit_datetime", "discharge_datetime"} <= mapped,
            "diagnoses": "diagnoses" in tables or any(f.startswith("diagnosis_code_") for f in mapped),
            "labs": "labs" in tables or bool(re.search(r"hemoglobin|sodium|creatinine|albumin|glucose|a1c|bun", extras)),
            "medications": "medications" in tables or bool(re.search(r"medication|insulin|drug", extras)),
            "disposition": "discharge_disposition" in mapped or "disposition" in extras,
            "admission_type": "admission_type" in mapped or "admissiontype" in extras,
            "outcome": bool(proposal.get("outcome")) or {"admit_datetime", "discharge_datetime"} <= mapped,
            "followup": "followup" in extras or "appointment" in extras,
            "ed_visits": "emergency" in extras or "edvisit" in extras}
    return [{"what": what, "why": why} for key, what, why in GAPS if not have[key]]


def describe(proposal: dict) -> list:
    """Plain sentences about what was found, when the AI model wrote none."""
    if proposal.get("summary"):
        return proposal["summary"]
    lines = []
    for e in proposal["files"]:
        if not e.get("table"):
            continue
        what = {"encounters": "discharges, one per row", "weeks": "weekly check-ins", "patients": "patients",
                "diagnoses": "diagnoses", "labs": "lab results", "medications": "medications",
                "procedures": "procedures", "drg": "DRG codes"}[e["table"]]
        matched = sum(1 for f in e["columns"].values() if f)
        lines.append(f"{e['filename']}: {e['rows']:,} rows of {what}; {matched} columns matched to ours.")
    main = proposal["files"][0] if proposal["files"] else None
    if main and main.get("table") == "encounters":
        mapped = set(main["columns"].values())
        if not {"admit_datetime", "discharge_datetime"} <= mapped:
            lines.append("There are no admission and discharge dates, so the built-in model cannot score these "
                         "discharges; a model trained on your own data can.")
    o = proposal.get("outcome")
    if o:
        total = sum(o["values"].values())
        yes = sum(n for v, n in o["values"].items() if v in o["positive"])
        lines.append(f"Outcome: {o['column']}; {', '.join(o['positive'])} counts as readmitted within 30 days "
                     f"({yes / max(total, 1):.1%} of rows).")
    return lines


def required_missing(proposal: dict) -> list:
    """What stops the files being stored at all."""
    problems = list(proposal.get("problems") or [])
    main = proposal["files"][0] if proposal["files"] else None
    if main and main.get("table"):
        need = data_profile.STORE_REQUIRED.get(main["table"], [])
        have = set(main["columns"].values())
        problems += [f"{main['filename']}: choose the column for {f.replace('_', ' ')}" for f in need if f not in have]
    return list(dict.fromkeys(problems))


def complete_for_builtin(proposal: dict) -> list:
    """Fields the built-in model needs that the main file does not give."""
    main = proposal["files"][0] if proposal["files"] else None
    if not main or main.get("table") != "encounters":
        return []
    have = set(main["columns"].values())
    if "date_of_birth" in have:
        have.add("age_years")
    return [f for f in schema.COMPLETE_FIELDS if f not in have]

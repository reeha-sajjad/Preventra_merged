"""
A hospital's data profile: how ITS files and tables become ours.

Every hospital exports differently - `ADM_DT` here, `admission_date` there,
dates written 13/10/2026 or 10/13/2026, weight in pounds or kilograms. The
profile says, once per hospital, which of their columns is which of our fields,
and every upload, file drop and database pull from that hospital goes through
it (api/ingest.py) before the usual checks (ingestion/schema.py).

    columns    {table: {their column: our field}}
    extras     {table: [their columns]} kept as-is for the hospital's own
               Model Studio model (encounters and patients only: one value per
               discharge or patient). The built-in model never sees them.
    units      {"weight": "kg" | "lb", "temperature": "C" | "F"}
    dayfirst   dates are written day first (13/10/2026)
    patient_prefix
               the start of every patient id from this hospital (CGH-P1931),
               whichever way the data arrives

Stored on the hospital's ingest_mappings document, next to its code mapping
(admission type "ER" -> EMERGENCY and so on, ingestion/schema.translate).

A column whose name is already one of our fields needs no entry: files that
follow the pilot data request work with an empty profile.
"""
from __future__ import annotations

import re
import warnings
from typing import Optional

import pandas as pd

from ingestion import schema

# ------------------------------------------------------------------ catalogue
# Our fields, per table, in the order a person would map them. Required ones
# are what schema.Bundle / schema.WeekIn need. `date_of_birth` is not stored:
# it is turned into age_years at the admission (bundles_from_files).
_VITALS = [f"discharge_{k}" for k in schema.DischargeVitals.model_fields]
# Diagnosis codes as columns of the discharge itself (diag_1, diag_2, ...), the
# way flat exports carry them. They become the stay's ranked diagnoses.
WIDE_DIAGNOSES = [f"diagnosis_code_{i}" for i in range(1, 10)]
FIELDS = {
    "encounters": (["encounter_key", "patient_key", "admit_datetime", "discharge_datetime", "age_years",
                    "admission_type", "discharge_disposition"],
                   ["date_of_birth", "died_in_hospital", "payer_class", "ed_arrival_datetime",
                    "ed_departure_datetime", "observation_stay", "planned_admission", "stage",
                    *schema.PERSON_FIELDS, *_VITALS, *WIDE_DIAGNOSES]),
    "patients": (["patient_key", "sex"], ["marital_status", "race_ethnicity", "death_date", "date_of_birth"]),
    "diagnoses": (["encounter_key", "icd_code", "icd_version", "sequence"], ["description", "coded_datetime"]),
    "labs": (["encounter_key", "test_name", "value", "result_datetime"],
             ["loinc_code", "unit", "abnormal_flag", "reference_low", "reference_high"]),
    "medications": (["encounter_key", "drug_name", "order_start_datetime"], ["rxnorm_or_atc", "route"]),
    "procedures": (["encounter_key", "procedure_code", "code_system"], ["procedure_datetime"]),
    "drg": (["encounter_key", "drg_system", "drg_code"], ["severity_of_illness", "risk_of_mortality"]),
    "weeks": (["patient_key", "encounter_key", "week_date", "source"],
              ["note", *schema.Readings.model_fields]),
}
TABLES = tuple(FIELDS)
EXTRA_TABLES = ("encounters", "patients")
MAX_EXTRAS = schema.MAX_EXTRAS
# Required for a stay to be stored at all; the rest only makes it complete
# enough for the built-in model (schema.COMPLETE_FIELDS).
STORE_REQUIRED = {"encounters": ["encounter_key", "patient_key"], "patients": ["patient_key"],
                  "weeks": ["patient_key", "week_date"]}
CODED_FIELDS = {"admission_type", "discharge_disposition", "payer_class", "sex", "marital_status",
                "race_ethnicity"}
# How flat exports write "nothing here". "None" is left alone: in many files it
# is a real answer ("no glucose test done").
MISSING_TOKENS = {"?", "na", "n/a", "null", "#n/a", "nan", "-", "--"}
# Never kept as a model input, whatever the profile says: they identify people.
_PERSONAL = re.compile(r"name|address|phone|email|street|postcode|zip|ssn|passport|national_?id|cnic", re.I)
_AGE_BAND = re.compile(r"^\s*[\[(]\s*(\d+)\s*-\s*(\d+)\s*[\])]\s*$")

DATE_FIELDS = {"admit_datetime", "discharge_datetime", "ed_arrival_datetime", "ed_departure_datetime",
               "date_of_birth", "death_date", "coded_datetime", "result_datetime", "order_start_datetime",
               "procedure_datetime", "week_date"}
DATE_ONLY = {"date_of_birth", "death_date", "week_date"}
WEIGHT_FIELDS = {"discharge_weight_kg", "weight_kg", "weight_change_kg"}
TEMPERATURE_FIELDS = {"temperature_c"}

# Other names hospitals use for our fields, compared after lower-casing and
# dropping everything that is not a letter or digit (see _norm).
SYNONYMS = {
    "encounter_key": ["encounterid", "encid", "visitid", "visitnumber", "visitno", "admissionid", "hadmid", "csn",
                      "fin", "accountnumber", "episodeid", "stayid"],
    "patient_key": ["patientid", "patid", "mrn", "medicalrecordnumber", "subjectid", "personid",
                    "hospitalnumber", "uhid", "patientnbr", "patientnumber", "patientno", "memberid", "patient", "pt",
                    "ptid"],
    "admit_datetime": ["admittime", "admitdate", "admissiondate", "admissiondatetime", "admdt",
                       "admitdt", "dateofadmission"],
    "discharge_datetime": ["dischtime", "dischargedate", "dischargetime", "dischdate", "dischdt",
                           "dateofdischarge"],
    "age_years": ["age", "ageyears", "ageatadmission", "anchorage", "ageband", "agegroup"],
    "date_of_birth": ["dob", "birthdate", "dateofbirth"],
    "admission_type": ["admtype", "admissiontype", "admissioncategory", "encountertype"],
    "discharge_disposition": ["dischargelocation", "disposition", "dischargedisposition", "dischdisposition",
                              "dischargedestination"],
    "died_in_hospital": ["hospitalexpireflag", "diedinhospital", "inhospitaldeath", "expired"],
    "payer_class": ["insurance", "payer", "payor", "financialclass", "insurancetype"],
    "ed_arrival_datetime": ["edregtime", "edarrival", "edarrivaltime"],
    "ed_departure_datetime": ["edouttime", "eddeparture", "eddeparturetime"],
    "observation_stay": ["observation", "obsstay"],
    "planned_admission": ["elective", "planned"],
    "sex": ["gender", "sexatbirth"],
    "marital_status": ["marital", "maritalstatus"],
    "race_ethnicity": ["race", "ethnicity", "raceethnicity"],
    "death_date": ["dod", "dateofdeath", "deathdate"],
    "icd_code": ["diagnosiscode", "dxcode", "icd", "icd10", "icd10code", "code"],
    "icd_version": ["icdversion", "codeversion", "version"],
    "sequence": ["seqnum", "seq", "diagnosissequence", "dxseq", "rank", "position"],
    "description": ["longtitle", "diagnosisdescription", "dxdescription", "title"],
    "test_name": ["labname", "testname", "label", "component", "analyte"],
    "loinc_code": ["loinc"],
    "value": ["valuenum", "result", "resultvalue", "labvalue"],
    "unit": ["units", "valueuom", "uom"],
    "result_datetime": ["charttime", "resulttime", "resultdate", "collectedtime", "specimentime"],
    "abnormal_flag": ["flag", "abnormal"],
    "reference_low": ["refrangelower", "reflow", "normallow"],
    "reference_high": ["refrangeupper", "refhigh", "normalhigh"],
    "drug_name": ["drug", "medication", "medicationname", "drugname"],
    "order_start_datetime": ["starttime", "orderstart", "startdate", "ordertime"],
    "rxnorm_or_atc": ["rxnorm", "atc", "ndc"],
    "procedure_code": ["icdprocedurecode", "proccode", "cpt", "cptcode"],
    "code_system": ["codingsystem", "codetype"],
    "drg_code": ["drg"],
    "drg_system": ["drgtype"],
    "severity_of_illness": ["drgseverity", "soi"],
    "risk_of_mortality": ["drgmortality", "rom"],
    "week_date": ["checkindate", "visitdate", "readingdate", "date"],
    "source": ["channel", "readingsource"],
    "weight_kg": ["weight"],
    "sbp": ["systolic", "systolicbp", "bpsystolic"],
    "heart_rate": ["hr", "pulse", "heartrate"],
    "spo2": ["oxygensaturation", "o2sat", "sao2"],
    "temperature_c": ["temperature", "temp"],
}


def _norm(name) -> str:
    return re.sub(r"[^a-z0-9]", "", str(name).lower())


def fields(table: str) -> list:
    required, optional = FIELDS[table]
    return list(required) + list(optional)


def required(table: str) -> list:
    return list(FIELDS[table][0])


def slug(name) -> str:
    """An extra column's name as stored: lower case, letters, digits and
    underscores, at most 40 characters."""
    return re.sub(r"_+", "_", re.sub(r"[^a-z0-9]", "_", str(name).strip().lower())).strip("_")[:40] or "extra"


def detect_table(filename: str) -> Optional[str]:
    """encounters.csv, 2026-10-19_LABS.csv, hosp_diagnoses_export.csv -> the table."""
    name = _norm(filename.rsplit(".", 1)[0])
    for table in sorted(TABLES, key=len, reverse=True):
        if table in name or table.rstrip("s") in name:
            return table
    aliases = {"admissions": "encounters", "visits": "encounters", "stays": "encounters",
               "demographics": "patients", "prescriptions": "medications", "meds": "medications",
               "checkins": "weeks", "monitoring": "weeks", "followup": "weeks", "dx": "diagnoses"}
    return next((t for a, t in aliases.items() if a in name), None)


# ---------------------------------------------------------------- suggestions
_WIDE_DX = re.compile(r"^(diag|dx|diagnosis|icd|icd9|icd10)(code)?(\d)$")


def _wide_diagnosis(header) -> Optional[str]:
    """diag_1, DX2, icd10_code_3 -> diagnosis_code_N."""
    m = _WIDE_DX.match(_norm(header)) if header else None
    return f"diagnosis_code_{m.group(3)}" if m and m.group(3) != "0" else None


def suggest(table: str, headers: list, profile: Optional[dict] = None) -> dict:
    """{their column: our field or None} - the saved choice first, then the
    same name, then a known synonym. Each of our fields is suggested once."""
    saved = ((profile or {}).get("columns") or {}).get(table) or {}
    ours = fields(table)
    by_norm = {_norm(f): f for f in ours}
    for field, names in SYNONYMS.items():
        if field in ours:
            for n in names:
                by_norm.setdefault(n, field)
    out, taken = {}, set()
    for h in headers:
        target = saved.get(h) if h in saved else (h if h in ours else by_norm.get(_norm(h)))
        if target is None and h not in saved and table == "encounters":
            target = _wide_diagnosis(h)
        if target in taken:
            target = None
        out[h] = target
        if target:
            taken.add(target)
    return out


def kept(table: str, column: str, mapping: dict, profile: Optional[dict]) -> bool:
    """Is an unmapped column kept for the hospital's own model? With an explicit
    list on the profile, only those; otherwise every column except the ones
    dropped, the outcome (it would hand the model the answer) and anything
    that identifies a person."""
    profile = profile or {}
    if table not in EXTRA_TABLES or mapping.get(column):
        return False
    explicit = (profile.get("extras") or {}).get(table)
    if explicit is not None:
        return column in explicit
    outcome = profile.get("outcome") or {}
    if outcome.get("table", "encounters") == table and outcome.get("column") == column:
        return False
    return column not in ((profile.get("drop") or {}).get(table) or []) and not _PERSONAL.search(column)


def check(table: str, headers: list, profile: Optional[dict] = None) -> dict:
    """What a file with these headers would give: the mapping that applies,
    what is missing to store it at all and to make it complete for the
    built-in model, what is kept for the hospital's own model, what is dropped."""
    mapping = suggest(table, headers, profile)
    mapped = {v for v in mapping.values() if v}
    if table in ("encounters", "patients") and "date_of_birth" in mapped:
        mapped.add("age_years")
    complete = schema.COMPLETE_FIELDS if table == "encounters" else ()
    outcome = (profile or {}).get("outcome") or {}
    return {"table": table, "mapping": mapping,
            "missing": [f for f in STORE_REQUIRED.get(table, required(table)) if f not in mapped],
            "incomplete": [f for f in complete if f not in mapped],
            "outcome": outcome.get("column") if outcome.get("table", "encounters") == table else None,
            "extras": [h for h in headers if kept(table, h, mapping, profile)],
            "dropped": [h for h in headers if not mapping.get(h) and not kept(table, h, mapping, profile)
                        and h != outcome.get("column")]}


# --------------------------------------------------------------------- apply
def _date(value, dayfirst: bool, date_only: bool = False) -> str:
    """Their date, written ISO so the schema reads it the same for everyone.
    Left as sent when it cannot be read, so the record is refused with the
    value in the reason rather than silently emptied."""
    try:
        with warnings.catch_warnings():
            # pandas warns when an ISO date meets dayfirst=True; it still reads it right.
            warnings.simplefilter("ignore", UserWarning)
            ts = pd.to_datetime(value, dayfirst=dayfirst)
    except (ValueError, TypeError, OverflowError):
        return value
    if pd.isna(ts):
        return value
    if date_only or not (ts.hour or ts.minute or ts.second):
        return ts.date().isoformat()
    return ts.isoformat()


def _number(value):
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return None


def _units(field: str, value, units: dict):
    if field in WEIGHT_FIELDS and units.get("weight") == "lb":
        n = _number(value)
        return value if n is None else round(n * 0.45359237, 2)
    if field in TEMPERATURE_FIELDS and units.get("temperature") == "F":
        n = _number(value)
        return value if n is None else round((n - 32) * 5 / 9, 1)
    return value


def _age(value):
    """An age, or an age band such as [70-80) read as its middle (75)."""
    band = _AGE_BAND.match(str(value))
    if band:
        return (int(band.group(1)) + int(band.group(2))) // 2
    return value


def reported_outcome(value, positive: list) -> Optional[bool]:
    """The hospital's own readmitted-or-not, from its outcome column."""
    if value is None or str(value).strip().lower() in MISSING_TOKENS | {""}:
        return None
    return str(value).strip().lower() in {str(p).strip().lower() for p in positive}


def apply(table: str, rows: list, profile: Optional[dict]) -> list:
    """Their rows -> ours: renamed, dates in ISO, units converted, age bands
    read, the outcome column turned into `reported_outcome`, the hospital's
    other columns kept under `extras` (kept()), everything else dropped."""
    profile = profile or {}
    if not rows:
        return []
    headers = list(dict.fromkeys(h for row in rows for h in row))
    mapping = suggest(table, headers, profile)
    keep = {h for h in headers if kept(table, h, mapping, profile)}
    # Raw coded values ride along only when the hospital keeps everything; an
    # explicit list of kept columns is taken as it stands.
    raw_codes = table in EXTRA_TABLES and (profile.get("extras") or {}).get(table) is None
    units = profile.get("units") or {}
    dayfirst = bool(profile.get("dayfirst"))
    outcome = profile.get("outcome") or {}
    outcome_col = outcome.get("column") if outcome.get("table", "encounters") == table else None
    out = []
    for row in rows:
        new, extras = {}, {}
        for col, value in row.items():
            if value is None or value == "" or str(value).strip().lower() in MISSING_TOKENS:
                continue
            if col == outcome_col:
                new["reported_outcome"] = reported_outcome(value, outcome.get("positive") or [])
                continue
            field = mapping.get(col)
            if field:
                if field in DATE_FIELDS:
                    value = _date(value, dayfirst, field in DATE_ONLY)
                elif field == "age_years":
                    value = _age(value)
                new[field] = _units(field, value, units)
                # A coded value goes in raw as well, so the hospital's own model
                # still has it while the code is not yet mapped to ours.
                if field in CODED_FIELDS and raw_codes and len(extras) < MAX_EXTRAS:
                    extras[slug(col)] = value
            elif col in keep and len(extras) < MAX_EXTRAS:
                extras[slug(col)] = value
        if extras:
            new["extras"] = extras
        out.append(new)
    return out


def icd_version(code, default: int = 10) -> int:
    """9 for a code that starts with a digit (428, 250.83), 10 for one that
    starts with any letter but V or E (I50.23, J441). V and E codes exist in
    both editions (V45, E119), so they take `default`: the edition most of
    the file's codes are in (file_icd_version)."""
    text = str(code).strip().upper()
    if text[:1].isdigit():
        return 9
    if text[:1] in ("V", "E"):
        return default
    return 10


def normalise_icd(code, version: int) -> str:
    """As MIMIC writes them, which the grouping and Charlson rules read: no dot,
    and an ICD-9 code's leading zeros back where a spreadsheet dropped them
    (38 -> 038, 8.45 -> 00845)."""
    text = str(code).strip().upper()
    if version == 9 and text[:1].isdigit():
        whole, _, decimal = text.partition(".")
        return whole.zfill(3) + decimal
    return text.replace(".", "")


def file_icd_version(codes) -> int:
    """The edition most of these codes are written in: 9 when most start with
    a digit, else 10."""
    codes = [str(c).strip() for c in codes if str(c).strip()]
    digits = sum(c[:1].isdigit() for c in codes)
    return 9 if codes and digits * 2 > len(codes) else 10


def age_at(date_of_birth, admitted) -> Optional[int]:
    """Whole years from birth to admission, or None when either is unreadable."""
    try:
        born, adm = pd.Timestamp(date_of_birth), pd.Timestamp(admitted)
    except (ValueError, TypeError):
        return None
    if pd.isna(born) or pd.isna(adm):
        return None
    return int(adm.year - born.year - ((adm.month, adm.day) < (born.month, born.day)))

"""
What a hospital may send, and how each record is checked and translated.

Field names are the pilot data request's (docs/pilot_ingestion_build.md,
section 04), unchanged. Each record is validated on its own: one bad record is
returned with its reasons and the rest of the batch goes ahead.

Coded fields (admission type, disposition, payer, sex, marital status, race)
arrive either as canonical values (ingestion/vocab.py) or as the hospital's
own codes, which its mapping (api/ingest.py, ingest_mappings) translates. A
code that is neither is an error, never a guess.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from ingestion import vocab

MAX_BUNDLES = 500
MAX_WEEKS = 2000
MAX_AGE = 90          # 90 and over is recorded as 90 (de-identification)


def _naive(value: Optional[datetime]) -> Optional[datetime]:
    """Timestamps are stored without a time zone, in the hospital's own time.
    One that carries a zone is converted to UTC first."""
    if value is not None and value.tzinfo is not None:
        return value.astimezone(timezone.utc).replace(tzinfo=None)
    return value


class _In(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class Encounter(_In):
    encounter_key: str = Field(min_length=1, max_length=100)
    patient_key: str = Field(min_length=1, max_length=100)
    admit_datetime: datetime
    discharge_datetime: datetime
    age_years: int = Field(ge=18, le=130)
    admission_type: str = Field(min_length=1, max_length=60)
    discharge_disposition: str = Field(min_length=1, max_length=60)
    died_in_hospital: bool = False
    payer_class: Optional[str] = Field(default=None, max_length=60)
    ed_arrival_datetime: Optional[datetime] = None
    ed_departure_datetime: Optional[datetime] = None
    observation_stay: Optional[bool] = None
    planned_admission: Optional[bool] = None

    _naive_times = field_validator("admit_datetime", "discharge_datetime", "ed_arrival_datetime",
                                   "ed_departure_datetime")(lambda v: _naive(v))

    @model_validator(mode="after")
    def _in_order(self):
        if self.discharge_datetime < self.admit_datetime:
            raise ValueError("discharge_datetime is before admit_datetime")
        if (self.ed_arrival_datetime and self.ed_departure_datetime
                and self.ed_departure_datetime < self.ed_arrival_datetime):
            raise ValueError("ed_departure_datetime is before ed_arrival_datetime")
        self.age_years = min(self.age_years, MAX_AGE)
        return self


class PatientIn(_In):
    sex: str = Field(min_length=1, max_length=30)
    marital_status: Optional[str] = Field(default=None, max_length=60)
    race_ethnicity: Optional[str] = Field(default=None, max_length=80)
    death_date: Optional[date] = None


class Diagnosis(_In):
    icd_code: str = Field(min_length=3, max_length=10)
    # A range, not Literal[9, 10]: a Literal will not take "10" from a CSV cell.
    icd_version: int = Field(ge=9, le=10)
    sequence: int = Field(ge=1, le=999)
    description: Optional[str] = Field(default=None, max_length=300)
    coded_datetime: Optional[datetime] = None


class Lab(_In):
    test_name: str = Field(min_length=1, max_length=120)
    loinc_code: Optional[str] = Field(default=None, max_length=20)
    value: float
    unit: str = Field(default="", max_length=30)
    result_datetime: datetime
    abnormal_flag: Optional[Literal["H", "L", "A", "N", "HH", "LL", ""]] = None
    reference_low: Optional[float] = None
    reference_high: Optional[float] = None

    _naive_time = field_validator("result_datetime")(lambda v: _naive(v))


class Medication(_In):
    drug_name: str = Field(min_length=1, max_length=200)
    order_start_datetime: datetime
    rxnorm_or_atc: Optional[str] = Field(default=None, max_length=30)
    route: Optional[str] = Field(default=None, max_length=40)


class Procedure(_In):
    procedure_code: str = Field(min_length=1, max_length=20)
    code_system: str = Field(min_length=1, max_length=20)
    procedure_datetime: Optional[datetime] = None


class Drg(_In):
    drg_system: str = Field(min_length=1, max_length=20)
    drg_code: str = Field(min_length=1, max_length=20)
    severity_of_illness: Optional[int] = Field(default=None, ge=1, le=4)
    risk_of_mortality: Optional[int] = Field(default=None, ge=1, le=4)


class DischargeVitals(_In):
    """The patient's own reference point, recorded at discharge. Week by week
    readings are compared with these (models/discharge_baseline.py)."""
    weight_kg: Optional[float] = Field(default=None, gt=20, lt=400)
    spo2: Optional[float] = Field(default=None, ge=50, le=100)
    sbp: Optional[float] = Field(default=None, ge=50, le=260)
    heart_rate: Optional[float] = Field(default=None, ge=20, le=250)
    walk_metres: Optional[float] = Field(default=None, ge=0, le=20000)
    pain: Optional[int] = Field(default=None, ge=0, le=10)


PERSON_FIELDS = ("sex", "marital_status", "race_ethnicity")


class Bundle(_In):
    stage: Literal["provisional", "final"] = "final"
    encounter: Encounter
    patient: PatientIn
    diagnoses: list[Diagnosis] = Field(default_factory=list, max_length=200)
    labs: list[Lab] = Field(default_factory=list, max_length=5000)
    medications: list[Medication] = Field(default_factory=list, max_length=2000)
    procedures: list[Procedure] = Field(default_factory=list, max_length=300)
    drg: Optional[Drg] = None
    discharge_vitals: Optional[DischargeVitals] = None

    @model_validator(mode="before")
    @classmethod
    def _person_on_the_encounter(cls, data):
        """Sex, marital status and race may come on the encounter (one flat
        record, as in the build plan's example) or in `patient` (as
        patients.csv sends them). Either way they are kept in `patient`; a
        value given in both places is taken from `patient`."""
        if isinstance(data, dict) and isinstance(data.get("encounter"), dict):
            enc = dict(data["encounter"])
            moved = {k: enc.pop(k) for k in PERSON_FIELDS if k in enc}
            if moved:
                patient = dict(data.get("patient") or {})
                for key, value in moved.items():
                    patient.setdefault(key, value)
                data = {**data, "encounter": enc, "patient": patient}
        return data


class Readings(_In):
    """Exactly the readings models/monitoring_rules.py scores; anything else is
    refused rather than silently ignored."""
    weight_kg: Optional[float] = Field(default=None, gt=20, lt=400)
    weight_change_kg: Optional[float] = Field(default=None, ge=-30, le=30)
    sbp: Optional[float] = Field(default=None, ge=50, le=260)
    heart_rate: Optional[float] = Field(default=None, ge=20, le=250)
    spo2: Optional[float] = Field(default=None, ge=50, le=100)
    temperature_c: Optional[float] = Field(default=None, ge=30, le=44)
    adherence_pct: Optional[float] = Field(default=None, ge=0, le=100)
    walk_metres: Optional[float] = Field(default=None, ge=0, le=20000)
    walk_distance_pct: Optional[float] = Field(default=None, ge=0, le=300)
    orthopnoea_pillows: Optional[int] = Field(default=None, ge=0, le=10)
    rescue_inhaler_uses: Optional[int] = Field(default=None, ge=0, le=200)
    refill_status: Optional[Literal["missed", "late", "collected", "not due"]] = None
    followup_status: Optional[Literal["missed", "attended", "not due"]] = None
    ankle_swelling: Optional[Literal["none", "mild", "marked"]] = None
    sputum_change: Optional[Literal["none", "volume", "purulent", "both"]] = None
    wound_status: Optional[Literal["clean", "red", "discharge", "opening"]] = None
    pain_trend: Optional[Literal["improving", "unchanged", "worse"]] = None
    antibiotic_course: Optional[Literal["completed", "ongoing", "stopped_early", "not_prescribed"]] = None
    new_confusion: Optional[Literal["yes", "no"]] = None

    @model_validator(mode="after")
    def _something(self):
        if not self.model_dump(exclude_none=True):
            raise ValueError("readings is empty")
        return self


class WeekIn(_In):
    patient_key: str = Field(min_length=1, max_length=100)
    encounter_key: str = Field(min_length=1, max_length=100)      # the discharge it follows
    week_date: date
    source: Literal["device", "nurse_call", "patient_app", "pharmacy", "clinic"]
    readings: Readings
    note: Optional[str] = Field(default=None, max_length=500)


# ------------------------------------------------------------- translation
def _errors(exc: ValidationError) -> list:
    return [f"{'.'.join(str(p) for p in e['loc']) or 'record'}: {e['msg']}" for e in exc.errors()]


def translate(field: str, value, mapping: dict) -> tuple:
    """(canonical value, error) for one coded field."""
    if value is None or value == "":
        return None, None
    allowed = vocab.VOCABULARIES[field]
    text = str(value).strip()
    if text.upper() in allowed:
        return text.upper(), None
    mapped = {str(k).strip().lower(): v for k, v in (mapping.get(field) or {}).items()}.get(text.lower())
    if mapped in allowed:
        return mapped, None
    return None, (f"{field}: '{text}' is not a value we know. Send one of {', '.join(allowed)}, "
                  f"or add it to your hospital's code mapping")


def check_bundle(raw: dict, mapping: dict) -> tuple:
    """One discharge as sent -> (canonical stay, errors, warnings).

    The stay is None when there are errors. Warnings are kept with the stay:
    lab results that cannot be read (an unknown unit) are left out of scoring
    and reported, rather than the whole discharge refused for one result."""
    try:
        bundle = Bundle.model_validate(raw)
    except ValidationError as exc:
        return None, _errors(exc), []
    stay = bundle.model_dump(mode="python")
    errors = []
    enc, pat = stay["encounter"], stay["patient"]
    for field, holder, required in (("admission_type", enc, True), ("discharge_disposition", enc, True),
                                    ("payer_class", enc, False), ("sex", pat, True),
                                    ("marital_status", pat, False), ("race_ethnicity", pat, False)):
        value, error = translate(field, holder.get(field), mapping)
        if error:
            errors.append(error)
        holder[field] = value
    if errors:
        return None, errors, []
    warnings = []
    for lab in stay["labs"]:
        key = vocab.lab_key(lab.get("loinc_code"), lab.get("test_name"))
        if key:
            _, problem = vocab.convert_lab(key, lab["value"], lab.get("unit"))
            if problem:
                warnings.append(f"lab result left out: {problem}")
    return stay, [], warnings


def check_week(raw: dict) -> tuple:
    """(week, errors) for one weekly check-in."""
    try:
        week = WeekIn.model_validate(raw)
    except ValidationError as exc:
        return None, _errors(exc)
    out = week.model_dump(mode="python")
    out["readings"] = {k: v for k, v in out["readings"].items() if v is not None}
    return out, []

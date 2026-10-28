import React from 'react';

// ---------------------------------------------------------------------------
// Shared field definitions for the MIMIC-IV model's manual-entry contract.
//
// The model is a HistGradientBoostingClassifier over 94 features, most of which
// nobody can type at a bedside (48 lab aggregates, APR-DRG grades, 17 Charlson
// flags). This form collects only what a clinician plausibly knows at discharge
// and leaves everything else missing.
//
// BLANK MEANS UNKNOWN, NOT ZERO. HistGradientBoosting learned a split direction
// for missing values during training, so an empty field is scored as genuinely
// unknown. Every input here therefore yields `null` when cleared — never 0 and
// never '' — because `exclude_none=True` on the API side is what turns a null
// into NaN. Coercing a blank to 0 would tell the model "this patient has no
// prior admissions", which is a different and much stronger claim.
//
// Field labels mirror models/mimic_drivers.py's label map so a driver card and
// the field that produced it read the same way.
// ---------------------------------------------------------------------------

// Fallback category values, used only if GET /api/manual-entry/schema is
// unreachable. The live schema is authoritative — it is read from the same
// feature spec the model was trained against.
export const FALLBACK_CATEGORICALS = {
  gender: ['F', 'M'],
  insurance: ['Medicaid', 'Medicare', 'No charge', 'Other', 'Private', 'UNKNOWN'],
  marital_status: ['DIVORCED', 'MARRIED', 'SINGLE', 'UNKNOWN', 'WIDOWED'],
  admission_type: ['DIRECT EMER.', 'ELECTIVE', 'EW EMER.', 'SURGICAL SAME DAY ADMISSION', 'URGENT'],
  race: [
    'AMERICAN INDIAN/ALASKA NATIVE', 'ASIAN', 'ASIAN - ASIAN INDIAN', 'ASIAN - CHINESE',
    'ASIAN - KOREAN', 'ASIAN - SOUTH EAST ASIAN', 'BLACK/AFRICAN', 'BLACK/AFRICAN AMERICAN',
    'BLACK/CAPE VERDEAN', 'BLACK/CARIBBEAN ISLAND', 'HISPANIC OR LATINO',
    'HISPANIC/LATINO - CENTRAL AMERICAN', 'HISPANIC/LATINO - COLUMBIAN',
    'HISPANIC/LATINO - CUBAN', 'HISPANIC/LATINO - DOMINICAN', 'HISPANIC/LATINO - GUATEMALAN',
    'HISPANIC/LATINO - HONDURAN', 'HISPANIC/LATINO - MEXICAN', 'HISPANIC/LATINO - PUERTO RICAN',
    'HISPANIC/LATINO - SALVADORAN', 'MULTIPLE RACE/ETHNICITY',
    'NATIVE HAWAIIAN OR OTHER PACIFIC ISLANDER', 'OTHER', 'PATIENT DECLINED TO ANSWER',
    'PORTUGUESE', 'SOUTH AMERICAN', 'UNABLE TO OBTAIN', 'UNKNOWN', 'WHITE',
    'WHITE - BRAZILIAN', 'WHITE - EASTERN EUROPEAN', 'WHITE - OTHER EUROPEAN', 'WHITE - RUSSIAN',
  ],
};

export const CATEGORICAL_FIELDS = [
  { key: 'gender',         label: 'Gender' },
  { key: 'race',           label: 'Race' },
  { key: 'insurance',      label: 'Insurance',      hint: 'proxy for access to follow-up care' },
  { key: 'marital_status', label: 'Marital status', hint: 'proxy for social support at home' },
  { key: 'admission_type', label: 'Admission type' },
];

export const STAY_FIELDS = [
  { key: 'anchor_age',       label: 'Patient age',                   hint: 'years',  min: 0, max: 120 },
  { key: 'los_days',         label: 'Length of stay',                hint: 'days',   min: 0, step: 0.1 },
  { key: 'n_diagnoses',      label: 'Number of diagnoses recorded',                  min: 0 },
  { key: 'n_procedures',     label: 'Procedures during stay',                        min: 0 },
  { key: 'drg_severity',     label: 'APR-DRG severity of illness',   hint: '1–4',    min: 1, max: 4 },
  { key: 'drg_mortality',    label: 'APR-DRG risk of mortality',     hint: '1–4',    min: 1, max: 4 },
];

export const UTILISATION_FIELDS = [
  { key: 'n_prior_adm',      label: 'Prior admissions on record',                    min: 0 },
  { key: 'days_since_prev',  label: 'Gap before this admission',  hint: 'days since previous discharge',  min: 0 },
  { key: 'ed_hours',         label: 'Hours in emergency department',  hint: 'hours', min: 0, step: 0.1 },
];

export const MEDICATION_COUNT_FIELDS = [
  { key: 'n_drug_orders',    label: 'Medication orders during stay',                 min: 0 },
  { key: 'n_distinct_drugs', label: 'Distinct medications',                          min: 0 },
];

export const ENCOUNTER_FLAGS = [
  { key: 'ed_visit',        label: 'Arrived via emergency department' },
  { key: 'is_emergency',    label: 'Emergency or urgent admission' },
  { key: 'readmit_history', label: 'Readmitted within 30 days before' },
];

export const MEDICATION_FLAGS = [
  { key: 'med_insulin',       label: 'Insulin prescribed' },
  { key: 'med_anticoagulant', label: 'Anticoagulant prescribed' },
  { key: 'med_opioid',        label: 'Opioid prescribed' },
  { key: 'med_diuretic',      label: 'Diuretic prescribed' },
  { key: 'med_antipsychotic', label: 'Antipsychotic prescribed' },
];

// The model carries disch_home / disch_snf / disch_ama as three independent
// flags. Exposing them as three separate toggles invites contradictory input
// (home = yes AND skilled nursing = yes), which no real discharge can be, so
// they are collected as one destination and expanded on submit.
export const DISCHARGE_DESTINATIONS = [
  { value: 'home',    label: 'Home',                        flags: { disch_home: 1, disch_snf: 0, disch_ama: 0 } },
  { value: 'snf',     label: 'Skilled nursing or rehab',    flags: { disch_home: 0, disch_snf: 1, disch_ama: 0 } },
  { value: 'ama',     label: 'Left against medical advice', flags: { disch_home: 0, disch_snf: 0, disch_ama: 1 } },
  { value: 'other',   label: 'Other facility or transfer',  flags: { disch_home: 0, disch_snf: 0, disch_ama: 0 } },
];

// Charlson weights, mirroring CHARLSON_WEIGHTS in api/mimic_scoring.py. Used
// only to preview the derived score in the UI — the server always recomputes
// charlson_score from the checkboxes and never trusts a typed value.
export const COMORBIDITIES = [
  { key: 'myocardial_infarction',    label: 'Prior myocardial infarction',   weight: 1 },
  { key: 'congestive_heart_failure', label: 'Congestive heart failure',      weight: 1 },
  { key: 'peripheral_vascular',      label: 'Peripheral vascular disease',   weight: 1 },
  { key: 'cerebrovascular',          label: 'Cerebrovascular disease',       weight: 1 },
  { key: 'dementia',                 label: 'Dementia',                      weight: 1 },
  { key: 'chronic_pulmonary',        label: 'Chronic pulmonary disease',     weight: 1 },
  { key: 'rheumatic',                label: 'Rheumatic disease',             weight: 1 },
  { key: 'peptic_ulcer',             label: 'Peptic ulcer disease',          weight: 1 },
  { key: 'mild_liver',               label: 'Mild liver disease',            weight: 1 },
  { key: 'diabetes_uncomplicated',   label: 'Diabetes without complications', weight: 1 },
  { key: 'diabetes_complicated',     label: 'Diabetes with organ damage',    weight: 2 },
  { key: 'hemiplegia',               label: 'Hemiplegia or paraplegia',      weight: 2 },
  { key: 'renal_disease',            label: 'Chronic kidney disease',        weight: 2 },
  { key: 'malignancy',               label: 'Malignancy',                    weight: 2 },
  { key: 'severe_liver',             label: 'Severe liver disease',          weight: 3 },
  { key: 'metastatic_cancer',        label: 'Metastatic solid tumour',       weight: 6 },
  { key: 'hiv_aids',                 label: 'HIV / AIDS',                    weight: 6 },
];

// Reference ranges match _LAB_CONCEPTS in models/mimic_drivers.py, so the
// range shown under an input is the one the driver explanation will cite.
export const LAB_FIELDS = [
  { key: 'lab_sodium_last',     label: 'Sodium',              stat: 'last',   unit: 'mEq/L', ref: '135–145',  step: 0.1 },
  { key: 'lab_potassium_last',  label: 'Potassium',           stat: 'last',   unit: 'mEq/L', ref: '3.5–5.1',  step: 0.1 },
  { key: 'lab_creatinine_last', label: 'Creatinine',          stat: 'last',   unit: 'mg/dL', ref: '0.6–1.3',  step: 0.01 },
  { key: 'lab_bun_last',        label: 'Blood urea nitrogen', stat: 'last',   unit: 'mg/dL', ref: '7–20',     step: 1 },
  { key: 'lab_glucose_last',    label: 'Blood glucose',       stat: 'last',   unit: 'mg/dL', ref: '70–140',   step: 1 },
  { key: 'lab_hba1c_last',      label: 'HbA1c',               stat: 'last',   unit: '%',     ref: '4.0–5.7',  step: 0.1 },
  { key: 'lab_hemoglobin_last', label: 'Hemoglobin',          stat: 'last',   unit: 'g/dL',  ref: '12.0–16.0', step: 0.1 },
  { key: 'lab_platelets_last',  label: 'Platelet count',      stat: 'last',   unit: 'K/uL',  ref: '150–400',  step: 1 },
  { key: 'lab_albumin_min',     label: 'Albumin',             stat: 'lowest', unit: 'g/dL',  ref: '3.5–5.0',  step: 0.1 },
  { key: 'lab_wbc_max',         label: 'White blood cells',   stat: 'highest', unit: 'K/uL', ref: '4.0–11.0', step: 0.1 },
];

const LAB_STAT_HINT = { last: 'last before discharge', lowest: 'lowest in stay', highest: 'highest in stay' };

// Every model-facing key this form can send, so a form object can be built or
// cleared without listing them twice.
export const ALL_FIELD_KEYS = [
  ...CATEGORICAL_FIELDS.map((f) => f.key),
  ...STAY_FIELDS.map((f) => f.key),
  ...UTILISATION_FIELDS.map((f) => f.key),
  ...MEDICATION_COUNT_FIELDS.map((f) => f.key),
  ...ENCOUNTER_FLAGS.map((f) => f.key),
  ...MEDICATION_FLAGS.map((f) => f.key),
  ...COMORBIDITIES.map((f) => f.key),
  ...LAB_FIELDS.map((f) => f.key),
  'disch_home', 'disch_snf', 'disch_ama',
];

/** A blank form: today's discharge date, every model field unknown. */
export function emptyForm() {
  const f = { discharge_date: new Date().toISOString().split('T')[0] };
  ALL_FIELD_KEYS.forEach((k) => { f[k] = null; });
  return f;
}

/** Charlson total from the ticked comorbidities — preview only; the server recomputes it. */
export function charlsonScore(form) {
  return COMORBIDITIES.reduce((sum, c) => sum + (form?.[c.key] ? c.weight : 0), 0);
}

/** Which discharge destination the three flags currently represent, or '' if unset. */
export function dischargeDestinationOf(form) {
  if (form?.disch_ama === 1) return 'ama';
  if (form?.disch_snf === 1) return 'snf';
  if (form?.disch_home === 1) return 'home';
  if (form?.disch_home === 0 && form?.disch_snf === 0 && form?.disch_ama === 0) return 'other';
  return '';
}

/** How many model fields the clinician has actually supplied. */
export function filledFieldCount(form) {
  return ALL_FIELD_KEYS.filter((k) => form?.[k] !== null && form?.[k] !== undefined && form?.[k] !== '').length;
}

// ---------------------------------------------------------------------------
// Layout primitives
// ---------------------------------------------------------------------------

export function FormSection({ title, subtitle, aside, children, columns = 3 }) {
  const grid = columns === 2
    ? 'grid-cols-1 sm:grid-cols-2'
    : columns === 4
      ? 'grid-cols-1 sm:grid-cols-2 lg:grid-cols-4'
      : 'grid-cols-1 sm:grid-cols-2 lg:grid-cols-3';
  return (
    <div className="bg-white rounded-xl border border-gray-200 shadow-sm overflow-hidden">
      <div className="bg-gray-50 border-b border-gray-200 px-6 py-3 flex items-center justify-between gap-4">
        <div>
          <h3 className="font-semibold text-gray-700 text-sm uppercase tracking-wide">{title}</h3>
          {subtitle && <p className="text-xs text-gray-500 mt-0.5 normal-case">{subtitle}</p>}
        </div>
        {aside}
      </div>
      <div className={`p-6 grid ${grid} gap-5`}>{children}</div>
    </div>
  );
}

export function FieldLabel({ label, hint }) {
  return (
    <label className="block text-sm font-medium text-gray-700 mb-1.5">
      {label}
      {hint && <span className="ml-1 text-gray-400 text-xs font-normal">({hint})</span>}
    </label>
  );
}

const CONTROL_CLASS =
  'w-full border border-gray-300 rounded-lg px-3 py-2 text-sm text-gray-800 bg-white ' +
  'focus:outline-none focus:ring-2 focus:ring-ns-navy/30 focus:border-ns-navy transition-colors';

/**
 * Select with an explicit unknown option. Choosing it sends null, not '',
 * so the field is dropped from the payload rather than sent as an empty
 * category the model would have to coerce.
 */
export function SelectField({ label, hint, value, onChange, options, unknownLabel = 'Unknown' }) {
  return (
    <div>
      <FieldLabel label={label} hint={hint} />
      <select
        value={value ?? ''}
        onChange={(e) => onChange(e.target.value === '' ? null : e.target.value)}
        className={CONTROL_CLASS}
      >
        <option value="">— {unknownLabel} —</option>
        {(options || []).map((opt) =>
          typeof opt === 'object' ? (
            <option key={opt.value} value={opt.value}>{opt.label}</option>
          ) : (
            <option key={opt} value={opt}>{opt}</option>
          )
        )}
      </select>
    </div>
  );
}

/** Number input where clearing the box means unknown, never zero. */
export function NumberField({ label, hint, value, onChange, min, max, step = 1 }) {
  return (
    <div>
      <FieldLabel label={label} hint={hint} />
      <input
        type="number"
        min={min}
        max={max}
        step={step}
        placeholder="Unknown"
        value={value ?? ''}
        onChange={(e) => onChange(e.target.value === '' ? null : Number(e.target.value))}
        className={CONTROL_CLASS}
      />
    </div>
  );
}

export function LabField({ field, value, onChange }) {
  return (
    <div>
      <FieldLabel label={field.label} hint={LAB_STAT_HINT[field.stat]} />
      <div className="relative">
        <input
          type="number"
          step={field.step}
          min={0}
          placeholder="Unknown"
          value={value ?? ''}
          onChange={(e) => onChange(e.target.value === '' ? null : Number(e.target.value))}
          className={`${CONTROL_CLASS} pr-16`}
        />
        <span className="absolute right-3 top-1/2 -translate-y-1/2 text-xs text-gray-400 pointer-events-none">
          {field.unit}
        </span>
      </div>
      <p className="text-xs text-gray-400 mt-1">Normal {field.ref} {field.unit}</p>
    </div>
  );
}

/**
 * Yes / No / Unknown. A plain two-way toggle cannot express "not known",
 * which for this model is a distinct and meaningful input.
 */
export function TriStateField({ label, hint, value, onChange }) {
  const opts = [
    { v: 1, text: 'Yes' },
    { v: 0, text: 'No' },
    { v: null, text: 'Unknown' },
  ];
  return (
    <div>
      <FieldLabel label={label} hint={hint} />
      <div className="flex rounded-lg border border-gray-300 overflow-hidden">
        {opts.map((o, i) => {
          const active = value === o.v || (o.v === null && (value === null || value === undefined));
          return (
            <button
              key={o.text}
              type="button"
              onClick={() => onChange(o.v)}
              className={`flex-1 py-2 text-sm font-medium transition-colors ${i > 0 ? 'border-l border-gray-300' : ''} ${
                active
                  ? o.v === null ? 'bg-gray-200 text-gray-700' : 'bg-ns-navy text-white'
                  : 'bg-white text-gray-600 hover:bg-gray-50'
              }`}
            >
              {o.text}
            </button>
          );
        })}
      </div>
    </div>
  );
}

/** Comorbidity checkbox. Unticked means "not recorded", which the model scores as absent. */
export function CheckboxField({ label, weight, checked, onChange }) {
  return (
    <label className="flex items-start gap-2.5 cursor-pointer group">
      <input
        type="checkbox"
        checked={!!checked}
        onChange={(e) => onChange(e.target.checked ? true : null)}
        className="mt-0.5 h-4 w-4 rounded border-gray-300 text-ns-navy focus:ring-ns-navy/30 cursor-pointer"
      />
      <span className="text-sm text-gray-700 group-hover:text-gray-900 leading-snug">
        {label}
        {weight ? <span className="ml-1 text-xs text-gray-400">+{weight}</span> : null}
      </span>
    </label>
  );
}

export function DateField({ label, value, onChange }) {
  return (
    <div>
      <FieldLabel label={label} />
      <input
        type="date"
        value={value ?? ''}
        onChange={(e) => onChange(e.target.value)}
        className={CONTROL_CLASS}
      />
    </div>
  );
}

/** Free-text input; clearing it means unknown. */
export function TextField({ label, hint, value, onChange, placeholder, maxLength = 200 }) {
  return (
    <div>
      <FieldLabel label={label} hint={hint} />
      <input
        type="text"
        maxLength={maxLength}
        placeholder={placeholder || 'Unknown'}
        value={value ?? ''}
        onChange={(e) => onChange(e.target.value === '' ? null : e.target.value)}
        className={CONTROL_CLASS}
      />
    </div>
  );
}

// ---------------------------------------------------------------------------
// Who the patient is and what they were treated for. These are not model
// inputs (the model reads MimicPatientForm); they decide where the patient is
// filed if they are added to the list, and the baseline weekly readings are
// measured against the discharge vitals. Typed here for patients from outside
// the hospital's own records.
// ---------------------------------------------------------------------------

export const IDENTITY_KEYS = [
  'external_ref', 'admit_date', 'discharge_date', 'primary_icd_code', 'primary_diagnosis',
  'secondary_diagnoses', 'weight_kg', 'spo2', 'sbp', 'heart_rate',
];

export function emptyIdentity() {
  const f = {};
  IDENTITY_KEYS.forEach((k) => { f[k] = null; });
  return f;
}

/** The identity values as the API wants them: blanks dropped, secondary
 *  diagnoses split on ";" into a list. */
export function identityPayload(identity) {
  const out = {};
  IDENTITY_KEYS.forEach((k) => {
    const v = identity?.[k];
    if (v === null || v === undefined || v === '') return;
    if (k === 'secondary_diagnoses') {
      const list = String(v).split(';').map((x) => x.trim()).filter(Boolean);
      if (list.length) out[k] = list;
    } else {
      out[k] = typeof v === 'string' ? v.trim() : v;
    }
  });
  return out;
}

export function IdentityFields({ identity, set }) {
  return (
    <FormSection
      title="Patient & Diagnosis"
      subtitle="Needed to add the patient to the list. A risk score can be calculated without them."
    >
      <TextField label="Your reference" hint="optional, kept with the patient" value={identity.external_ref}
        onChange={set('external_ref')} placeholder="e.g. a hospital record number" />
      <DateField label="Admission date" value={identity.admit_date} onChange={set('admit_date')} />
      <DateField label="Discharge date" value={identity.discharge_date} onChange={set('discharge_date')} />
      <TextField label="Diagnosis code" hint="ICD, e.g. I50.9" value={identity.primary_icd_code}
        onChange={set('primary_icd_code')} maxLength={12} />
      <TextField label="Diagnosis" hint="in words" value={identity.primary_diagnosis}
        onChange={set('primary_diagnosis')} />
      <TextField label="Other diagnoses" hint="separate with ;" value={identity.secondary_diagnoses}
        onChange={set('secondary_diagnoses')} maxLength={500} />
      <NumberField label="Weight at discharge" hint="kg" value={identity.weight_kg}
        onChange={set('weight_kg')} min={1} max={500} step={0.1} />
      <NumberField label="Oxygen saturation at discharge" hint="%" value={identity.spo2}
        onChange={set('spo2')} min={50} max={100} />
      <NumberField label="Systolic blood pressure at discharge" hint="mmHg" value={identity.sbp}
        onChange={set('sbp')} min={40} max={300} />
      <NumberField label="Heart rate at discharge" hint="beats per minute" value={identity.heart_rate}
        onChange={set('heart_rate')} min={20} max={300} />
    </FormSection>
  );
}

export function getScoreColor(band) {
  const n = band?.toLowerCase();
  if (n === 'high')   return 'text-risk-high';
  if (n === 'medium') return 'text-risk-medium';
  return 'text-risk-low';
}

// ---------------------------------------------------------------------------
// The form body, shared by Manual Entry and Update Patient so the two pages
// cannot drift apart the way the inlined UCI copies did.
// ---------------------------------------------------------------------------

export function MimicPatientForm({ form, set, categoricals, showDischargeDate = true }) {
  const cats = categoricals || FALLBACK_CATEGORICALS;
  const cci = charlsonScore(form);
  const destination = dischargeDestinationOf(form);

  const setDestination = (value) => {
    const chosen = DISCHARGE_DESTINATIONS.find((d) => d.value === value);
    const flags = chosen ? chosen.flags : { disch_home: null, disch_snf: null, disch_ama: null };
    Object.entries(flags).forEach(([k, v]) => set(k)(v));
  };

  return (
    <>
      <FormSection title="Patient & Admission">
        {showDischargeDate && (
          <DateField label="Discharge Date" value={form.discharge_date} onChange={set('discharge_date')} />
        )}
        <NumberField
          label="Patient age"
          hint="years"
          value={form.anchor_age}
          onChange={set('anchor_age')}
          min={0}
          max={120}
        />
        {CATEGORICAL_FIELDS.map((f) => (
          <SelectField
            key={f.key}
            label={f.label}
            hint={f.hint}
            value={form[f.key]}
            onChange={set(f.key)}
            options={cats[f.key] || []}
          />
        ))}
        <SelectField
          label="Discharge destination"
          value={destination}
          onChange={(v) => setDestination(v)}
          options={DISCHARGE_DESTINATIONS.map((d) => ({ value: d.value, label: d.label }))}
        />
      </FormSection>

      <FormSection title="This Hospitalisation">
        {STAY_FIELDS.filter((f) => f.key !== 'anchor_age').map((f) => (
          <NumberField
            key={f.key}
            label={f.label}
            hint={f.hint}
            value={form[f.key]}
            onChange={set(f.key)}
            min={f.min}
            max={f.max}
            step={f.step}
          />
        ))}
      </FormSection>

      <FormSection title="Utilisation History">
        {UTILISATION_FIELDS.map((f) => (
          <NumberField
            key={f.key}
            label={f.label}
            hint={f.hint}
            value={form[f.key]}
            onChange={set(f.key)}
            min={f.min}
            step={f.step}
          />
        ))}
        {ENCOUNTER_FLAGS.map((f) => (
          <TriStateField key={f.key} label={f.label} value={form[f.key]} onChange={set(f.key)} />
        ))}
      </FormSection>

      <FormSection title="Medications">
        {MEDICATION_COUNT_FIELDS.map((f) => (
          <NumberField
            key={f.key}
            label={f.label}
            value={form[f.key]}
            onChange={set(f.key)}
            min={f.min}
          />
        ))}
        {MEDICATION_FLAGS.map((f) => (
          <TriStateField key={f.key} label={f.label} value={form[f.key]} onChange={set(f.key)} />
        ))}
      </FormSection>

      <FormSection
        title="Comorbidities (Charlson)"
        subtitle="Tick every condition on the problem list. The score is derived server-side, never typed."
        columns={3}
        aside={
          <div className="text-right shrink-0">
            <div className="text-xs text-gray-500 uppercase tracking-wide">Charlson score</div>
            <div className="text-xl font-bold text-ns-navy leading-tight">{cci}</div>
          </div>
        }
      >
        {COMORBIDITIES.map((c) => (
          <CheckboxField
            key={c.key}
            label={c.label}
            weight={c.weight}
            checked={form[c.key]}
            onChange={set(c.key)}
          />
        ))}
      </FormSection>

      <FormSection
        title="Laboratory Results"
        subtitle="Leave blank where a test was not run — the model handles a missing lab natively."
        columns={4}
      >
        {LAB_FIELDS.map((f) => (
          <LabField key={f.key} field={f} value={form[f.key]} onChange={set(f.key)} />
        ))}
      </FormSection>
    </>
  );
}

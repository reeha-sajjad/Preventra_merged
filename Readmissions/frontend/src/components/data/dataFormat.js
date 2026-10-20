// Constants and helpers shared by the Data page's tabs (pages/DataIngest.jsx).

export const TABLE_LABELS = {
  encounters: 'Discharges (encounters)',
  patients: 'Patients',
  diagnoses: 'Diagnoses',
  labs: 'Lab results',
  medications: 'Medications',
  procedures: 'Procedures',
  drg: 'DRG',
  weeks: 'Weekly check-ins',
};
export const TABLES = Object.keys(TABLE_LABELS);

export const SOURCE_LABELS = { api: 'API', upload: 'Upload', pull: 'Database', rescore: 'Re-score' };

export function when(value) {
  if (!value) return '—';
  const d = new Date(String(value).endsWith('Z') || String(value).includes('+') ? value : `${value}Z`);
  return Number.isNaN(d.getTime()) ? String(value) : d.toLocaleString();
}

export const panel = 'rounded-xl border border-gray-200 bg-white p-5 shadow-sm';
export const input = 'w-full rounded-md border border-gray-300 px-3 py-2 text-sm focus:border-ns-navy focus:outline-none';
export const inputInline = 'rounded-md border border-gray-300 px-2 py-1.5 text-sm focus:border-ns-navy focus:outline-none';
export const button = 'inline-flex items-center gap-1.5 rounded-md bg-ns-navy px-3 py-2 text-sm font-semibold text-white hover:bg-ns-navy/90 disabled:cursor-not-allowed disabled:opacity-50';
export const ghost = 'inline-flex items-center gap-1.5 rounded-md border border-gray-300 bg-white px-3 py-2 text-sm font-medium text-gray-700 hover:bg-gray-50 disabled:cursor-not-allowed disabled:opacity-50';

// ── The plug-and-play upload (UploadPanel.jsx, api/data_upload.py) ─────────
// The file every other one of an upload hangs off, per kind of upload.
export const MAIN_TABLE = { discharges: 'encounters', weekly: 'weeks' };
// Tables a further file of each kind of upload can be.
export const OTHER_TABLES = {
  discharges: ['patients', 'diagnoses', 'labs', 'medications', 'procedures', 'drg'],
  weekly: ['patients'],
};
// Detail rows only need the discharge they belong to (data_upload._validate).
export const DETAIL_TABLES = ['diagnoses', 'labs', 'medications', 'procedures', 'drg'];
// What the built-in model needs of a discharge (ingestion/schema.py COMPLETE_FIELDS).
export const COMPLETE_FIELDS = ['admit_datetime', 'discharge_datetime', 'age_years', 'admission_type',
  'discharge_disposition'];
// The patient id prefix rule (api/ingest.py PREFIX_RE).
export const PREFIX_RE = /^[A-Z0-9]{2,10}$/;
// A column choice meaning "keep it out of everything".
export const DROP = '__drop';

const FIELD_WORDS = { datetime: 'date', icd: 'ICD', drg: 'DRG', loinc: 'LOINC', rxnorm: 'RxNorm', atc: 'ATC',
  sbp: 'systolic BP', dbp: 'diastolic BP', spo2: 'SpO2', ed: 'ED', kg: '(kg)', pct: '(%)', bnp: 'BNP' };
const FIELD_NAMES = { encounter_key: 'Stay id (one per discharge)', patient_key: 'Patient id',
  admit_datetime: 'Admission date', discharge_datetime: 'Discharge date', age_years: 'Age',
  admission_type: 'Admission type', discharge_disposition: 'Discharged to', week_date: 'Week date' };
/** One of our field names as words: admit_datetime -> Admission date. */
export function fieldLabel(field) {
  if (FIELD_NAMES[field]) return FIELD_NAMES[field];
  const wide = /^diagnosis_code_(\d+)$/.exec(field);
  if (wide) return `Diagnosis ${wide[1]}${wide[1] === '1' ? ' (primary)' : ''}`;
  const words = String(field).split('_').map((w) => FIELD_WORDS[w] || w);
  const text = words.join(' ');
  return text.charAt(0).toUpperCase() + text.slice(1);
}
/** One of our coded values as words: HOME_HEALTH -> Home health. */
export function codeLabel(value) {
  const text = String(value).replace(/_/g, ' ').toLowerCase();
  return text.charAt(0).toUpperCase() + text.slice(1);
}

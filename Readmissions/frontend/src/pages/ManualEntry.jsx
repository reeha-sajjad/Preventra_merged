import React, { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import {
  MANUAL_ENTRY_ENABLED, getManualEntrySchema, getManualTemplate, scoreManualRecords, parseManualFile,
  addManualRecords,
} from '../api';
import { can, ADD_PATIENT_ROLES } from '../roles';
import RiskBadge from '../components/shared/RiskBadge';
import DriverCard from '../components/shared/DriverCard';
import {
  MimicPatientForm, IdentityFields, emptyForm, emptyIdentity, identityPayload, filledFieldCount,
} from '../components/shared/PatientFormFields';
import {
  UserPlus, Loader2, AlertCircle, CheckCircle, Info, Download, Upload, ArrowRight,
} from 'lucide-react';

// A blank is unknown, not zero (see PatientFormFields): only supplied fields are sent.
const supplied = (form) => Object.fromEntries(
  Object.entries(form).filter(([, v]) => v !== null && v !== undefined && v !== ''));

// What a record still needs before it can be ADDED to the list. The server
// checks the same (api/manual_patients.add_errors); this only explains it.
const missingForAdd = (record) => [
  ...(record.discharge_date ? [] : ['a discharge date']),
  ...(record.primary_icd_code || record.primary_diagnosis ? [] : ['a diagnosis']),
];

const csvCell = (value) => {
  const text = String(value ?? '');
  return /[",\n]/.test(text) ? `"${text.replace(/"/g, '""')}"` : text;
};

function AddNote() {
  return (
    <p className="text-sm text-gray-600">
      {can(ADD_PATIENT_ROLES)
        ? 'Adding patients to the list is switched off on this deployment. You can still calculate scores.'
        : 'Only hospital admins can add patients to the list. You can still calculate scores.'}
    </p>
  );
}

function Tab({ active, onClick, children }) {
  return (
    <button
      type="button"
      role="tab"
      aria-selected={active}
      onClick={onClick}
      className={`px-4 py-2 text-sm font-semibold border-b-2 -mb-px transition-colors ${
        active ? 'border-ns-navy text-ns-navy' : 'border-transparent text-gray-600 hover:text-gray-900'}`}
    >
      {children}
    </button>
  );
}

function ErrorBox({ title, children }) {
  return (
    <div className="bg-red-50 border border-red-200 rounded-xl p-4 flex items-start space-x-3 text-red-800">
      <AlertCircle size={20} className="mt-0.5 shrink-0" />
      <div>
        <div className="font-semibold">{title}</div>
        <div className="text-sm mt-0.5 text-red-700">{children}</div>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Type the details in
// ---------------------------------------------------------------------------
function TypedPatient({ categoricals, canAdd }) {
  const [form, setForm] = useState(() => { const f = emptyForm(); delete f.discharge_date; return f; });
  const [identity, setIdentity] = useState(emptyIdentity);
  const [calculating, setCalculating] = useState(false);
  const [error, setError] = useState(null);
  const [result, setResult] = useState(null);       // the scored row, and the record it scored
  const [adding, setAdding] = useState(false);
  const [addError, setAddError] = useState(null);
  const [added, setAdded] = useState(null);

  const set = (key) => (val) => setForm((f) => ({ ...f, [key]: val }));
  const setId = (key) => (val) => setIdentity((f) => ({ ...f, [key]: val }));

  const record = () => ({ ...supplied(form), ...identityPayload(identity) });

  const calculate = async (e) => {
    e.preventDefault();
    setCalculating(true);
    setError(null);
    setResult(null);
    setAdded(null);
    setAddError(null);
    try {
      const sent = record();
      const res = await scoreManualRecords([sent]);
      const row = res.rows[0];
      if (row.errors?.length) setError(row.errors.join('; '));
      else setResult({ ...row, sent });
    } catch (err) {
      setError(err.message || 'The score could not be calculated.');
    } finally {
      setCalculating(false);
    }
  };

  const add = async () => {
    setAdding(true);
    setAddError(null);
    try {
      const res = await addManualRecords([result.sent]);
      setAdded(res.added[0]);
    } catch (err) {
      setAddError(err.message || 'The patient could not be added.');
    } finally {
      setAdding(false);
    }
  };

  const reset = () => {
    setForm(() => { const f = emptyForm(); delete f.discharge_date; return f; });
    setIdentity(emptyIdentity());
    setResult(null);
    setError(null);
    setAdded(null);
    setAddError(null);
  };

  const missing = result ? missingForAdd(result.sent) : [];

  return (
    <>
      <form onSubmit={calculate} className="space-y-5">
        <IdentityFields identity={identity} set={setId} />
        <MimicPatientForm form={form} set={set} categoricals={categoricals} showDischargeDate={false} />

        {error && <ErrorBox title="Could not calculate">{error}</ErrorBox>}

        <div className="flex flex-wrap items-center justify-end gap-4">
          <span className="text-sm text-gray-600">
            {filledFieldCount(form)} field{filledFieldCount(form) === 1 ? '' : 's'} supplied — the rest score as unknown.
            Nothing is saved.
          </span>
          <button
            type="submit"
            disabled={calculating}
            className="px-8 py-3 bg-ns-navy text-white rounded-xl font-semibold hover:bg-blue-900 transition-colors shadow-sm focus:ring-2 focus:ring-offset-2 focus:ring-ns-navy disabled:opacity-60 disabled:cursor-not-allowed flex items-center space-x-2"
          >
            {calculating
              ? <><Loader2 size={18} className="animate-spin" /><span>Calculating...</span></>
              : <span>Calculate risk</span>}
          </button>
        </div>
      </form>

      {result && (
        <div className="space-y-5 animate-in slide-in-from-bottom-4 fade-in duration-500">
          <div className="bg-white rounded-xl border border-gray-200 shadow-sm p-6">
            <h2 className="text-lg font-bold text-ns-navy mb-1">Risk result</h2>
            <p className="text-xs text-gray-600 mb-4">Calculated only. This patient is not on any list.</p>
            <div className="flex items-center justify-center space-x-10 py-2">
              <div className="flex flex-col items-center">
                <span className="text-sm text-gray-600 mb-1">Risk score</span>
                <span className="text-6xl font-black text-ns-navy">{parseFloat(result.risk_score).toFixed(1)}%</span>
              </div>
              <div className="h-16 w-px bg-gray-200" />
              <div className="flex flex-col items-center space-y-2">
                <span className="text-sm text-gray-600">Risk band</span>
                <RiskBadge riskBand={result.risk_band} size="md" />
              </div>
            </div>
          </div>

          <div className="bg-white rounded-xl border border-gray-200 shadow-sm p-6">
            <h3 className="text-base font-semibold text-ns-navy mb-4 border-b pb-2">Top 3 risk drivers</h3>
            <div className="space-y-3">
              {(result.drivers || []).map((d, i) => (
                <DriverCard key={i} label={d.label} value={d.value} explanation={d.explanation}
                  category={d.category} riskBand={result.risk_band} />
              ))}
            </div>
          </div>

          <div className="bg-white rounded-xl border border-gray-200 shadow-sm p-6 flex flex-col sm:flex-row items-start sm:items-center justify-between gap-4">
            <div>
              <p className="font-medium text-gray-800">Add this patient to the patient list?</p>
              {!canAdd && <AddNote />}
              {canAdd && missing.length > 0 && (
                <p className="text-sm text-gray-600 mt-0.5">
                  To add them, fill in {missing.join(' and ')} above and calculate again.
                </p>
              )}
              {canAdd && missing.length === 0 && !added && (
                <p className="text-sm text-gray-600 mt-0.5">
                  They are filed under your hospital, not yet assigned to a doctor, and scored again by the server.
                </p>
              )}
              {addError && <p className="text-sm text-red-700 mt-1">{addError}</p>}
              {added && (
                <p className="text-sm text-green-800 font-medium mt-1 flex items-center gap-1">
                  <CheckCircle size={14} /> Added as {added.patient_id}.
                </p>
              )}
            </div>
            <div className="flex items-center space-x-3">
              <button type="button" onClick={reset}
                className="px-4 py-2 text-sm font-medium border border-gray-300 rounded-lg text-gray-700 hover:bg-gray-50 transition-colors">
                New patient
              </button>
              {canAdd && (added ? (
                <Link to={`/patients/${encodeURIComponent(added.patient_id)}`}
                  className="group px-5 py-2 bg-ns-navy text-white rounded-lg text-sm font-semibold hover:bg-blue-900 transition-colors flex items-center space-x-2">
                  <span>Open patient</span>
                  <ArrowRight size={16} className="group-hover:translate-x-0.5 transition-transform" />
                </Link>
              ) : (
                <button type="button" onClick={add} disabled={adding || missing.length > 0}
                  title={missing.length ? `Needs ${missing.join(' and ')}` : undefined}
                  className="px-5 py-2 bg-ns-navy text-white rounded-lg text-sm font-semibold hover:bg-blue-900 transition-colors flex items-center space-x-2 disabled:opacity-60 disabled:cursor-not-allowed">
                  {adding
                    ? <><Loader2 size={16} className="animate-spin" /><span>Adding...</span></>
                    : <span>Add to patient list</span>}
                </button>
              ))}
            </div>
          </div>
        </div>
      )}
    </>
  );
}

// ---------------------------------------------------------------------------
// Upload a CSV
// ---------------------------------------------------------------------------
function UploadedPatients({ canAdd }) {
  const [parsed, setParsed] = useState(null);       // { rows, unknown_columns }
  const [fileName, setFileName] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const [adding, setAdding] = useState(false);
  const [addError, setAddError] = useState(null);
  const [added, setAdded] = useState(null);

  const downloadTemplate = async () => {
    setError(null);
    try {
      const t = await getManualTemplate();
      const names = t.columns.map((c) => c.name);
      const text = [names.map(csvCell).join(','), names.map((n) => csvCell(t.example?.[n])).join(',')].join('\n');
      const url = URL.createObjectURL(new Blob([text], { type: 'text/csv' }));
      const link = document.createElement('a');
      link.href = url;
      link.download = 'patients-template.csv';
      link.click();
      URL.revokeObjectURL(url);
    } catch (err) {
      setError(err.message || 'The template could not be downloaded.');
    }
  };

  const choose = async (e) => {
    const file = e.target.files?.[0];
    e.target.value = '';
    if (!file) return;
    setBusy(true);
    setError(null);
    setParsed(null);
    setAdded(null);
    setAddError(null);
    setFileName(file.name);
    try {
      setParsed(await parseManualFile(file));
    } catch (err) {
      setError(err.message || 'The file could not be read.');
    } finally {
      setBusy(false);
    }
  };

  const rows = parsed?.rows || [];
  const bad = rows.filter((r) => r.errors.length);
  const incomplete = rows.filter((r) => !r.errors.length && missingForAdd(r.record).length);

  const add = async () => {
    setAdding(true);
    setAddError(null);
    try {
      setAdded((await addManualRecords(rows.map((r) => r.record))).added);
    } catch (err) {
      setAddError(err.message || 'The patients could not be added.');
    } finally {
      setAdding(false);
    }
  };

  return (
    <div className="space-y-5">
      <div className="bg-white rounded-xl border border-gray-200 shadow-sm p-6 space-y-4">
        <p className="text-sm text-gray-700">
          Upload a CSV with one patient per row (1 to 500 rows). Each row is checked and scored; nothing is saved
          until you choose to add them.
        </p>
        <div className="flex flex-wrap gap-3">
          <button type="button" onClick={downloadTemplate}
            className="px-4 py-2 text-sm font-semibold border border-gray-300 rounded-lg text-gray-800 hover:bg-gray-50 flex items-center space-x-2">
            <Download size={16} /><span>Download template</span>
          </button>
          <label className="px-4 py-2 text-sm font-semibold bg-ns-navy text-white rounded-lg hover:bg-blue-900 cursor-pointer flex items-center space-x-2">
            <Upload size={16} /><span>{busy ? 'Reading...' : 'Choose a CSV file'}</span>
            <input type="file" accept=".csv,text/csv" onChange={choose} disabled={busy} className="sr-only" />
          </label>
        </div>
      </div>

      {error && <ErrorBox title="Could not use that file">{error}</ErrorBox>}

      {parsed && (
        <div className="bg-white rounded-xl border border-gray-200 shadow-sm p-6 space-y-4">
          <div className="flex flex-wrap items-baseline justify-between gap-2">
            <h2 className="text-lg font-bold text-ns-navy">{fileName}</h2>
            <span className="text-sm text-gray-600">
              {rows.length} patient{rows.length === 1 ? '' : 's'} · {bad.length} with problems
            </span>
          </div>
          {parsed.unknown_columns?.length > 0 && (
            <p className="text-sm text-gray-700 flex items-start gap-1.5">
              <Info size={15} className="mt-0.5 shrink-0" />
              <span>Ignored columns the model does not read: {parsed.unknown_columns.join(', ')}.</span>
            </p>
          )}
          <div className="overflow-x-auto">
            <table className="w-full text-sm text-left">
              <thead>
                <tr className="border-b border-gray-200 text-gray-600">
                  <th className="py-2 pr-4 font-semibold">Row</th>
                  <th className="py-2 pr-4 font-semibold">Reference</th>
                  <th className="py-2 pr-4 font-semibold">Risk score</th>
                  <th className="py-2 pr-4 font-semibold">Band</th>
                  <th className="py-2 font-semibold">Problems</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((r) => (
                  <tr key={r.row} className="border-b border-gray-100 align-top">
                    <td className="py-2 pr-4">{r.row}</td>
                    <td className="py-2 pr-4">{r.record.external_ref || '—'}</td>
                    <td className="py-2 pr-4 font-semibold text-gray-800">
                      {r.errors.length ? '—' : `${Number(r.risk_score).toFixed(1)}%`}
                    </td>
                    <td className="py-2 pr-4">{r.errors.length ? '—' : <RiskBadge riskBand={r.risk_band} size="sm" />}</td>
                    <td className="py-2 text-red-700">
                      {r.errors.join('; ')}
                      {!r.errors.length && missingForAdd(r.record).length > 0 && (
                        <span className="text-gray-600">Needs {missingForAdd(r.record).join(' and ')} to be added</span>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          <div className="border-t border-gray-100 pt-4">
            {!canAdd && <AddNote />}
            {canAdd && (
              <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3">
                <div className="text-sm text-gray-700">
                  {bad.length > 0 && <p>Fix the rows with problems and upload again. Patients are added all together or not at all.</p>}
                  {bad.length === 0 && incomplete.length > 0 && (
                    <p>{incomplete.length} row{incomplete.length === 1 ? ' needs' : 's need'} a discharge date and a diagnosis before the patients can be added.</p>
                  )}
                  {addError && <p className="text-red-700">{addError}</p>}
                  {added && (
                    <p className="text-green-800 font-medium flex items-center gap-1">
                      <CheckCircle size={14} /> Added {added.length} patient{added.length === 1 ? '' : 's'}.
                      {added.length === 1 && (
                        <Link to={`/patients/${encodeURIComponent(added[0].patient_id)}`} className="underline ml-1">Open</Link>
                      )}
                    </p>
                  )}
                </div>
                {!added && (
                  <button type="button" onClick={add} disabled={adding || bad.length > 0 || incomplete.length > 0}
                    className="px-5 py-2 bg-ns-navy text-white rounded-lg text-sm font-semibold hover:bg-blue-900 transition-colors flex items-center space-x-2 disabled:opacity-60 disabled:cursor-not-allowed">
                    {adding
                      ? <><Loader2 size={16} className="animate-spin" /><span>Adding...</span></>
                      : <span>Add {rows.length} patient{rows.length === 1 ? '' : 's'} to the list</span>}
                  </button>
                )}
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Page
// ---------------------------------------------------------------------------
export default function ManualEntry() {
  const [tab, setTab] = useState('type');
  const [categoricals, setCategoricals] = useState(null);

  // Category options are read from the model's own feature spec. If the call
  // fails the form still renders from the values baked in at build time.
  useEffect(() => {
    let cancelled = false;
    getManualEntrySchema()
      .then((schema) => { if (!cancelled && schema?.categorical) setCategoricals(schema.categorical); })
      .catch(() => { /* fall back to built-in category lists */ });
    return () => { cancelled = true; };
  }, []);

  // Calculating is open to every role that sees this page; adding is for admins
  // and only where the deployment allows it. The server refuses both anyway.
  const canAdd = MANUAL_ENTRY_ENABLED && can(ADD_PATIENT_ROLES);

  return (
    <div className="p-4 sm:p-6 max-w-5xl mx-auto space-y-6 pb-16 animate-in fade-in duration-500">
      <div className="border-b border-gray-200 pb-4">
        <div className="flex items-center space-x-3">
          <UserPlus className="text-ns-navy" size={28} />
          <div>
            <h1 className="text-2xl font-bold text-ns-navy">Score a patient</h1>
            <p className="text-gray-600 mt-0.5 text-sm">
              Get a readmission risk score for a patient who is not on your list, by typing their details or
              uploading a file. Calculating saves nothing.
            </p>
          </div>
        </div>
      </div>

      <div className="flex border-b border-gray-200" role="tablist">
        <Tab active={tab === 'type'} onClick={() => setTab('type')}>Type details</Tab>
        <Tab active={tab === 'upload'} onClick={() => setTab('upload')}>Upload a CSV</Tab>
      </div>

      {tab === 'type'
        ? <TypedPatient categoricals={categoricals} canAdd={canAdd} />
        : <UploadedPatients canAdd={canAdd} />}
    </div>
  );
}

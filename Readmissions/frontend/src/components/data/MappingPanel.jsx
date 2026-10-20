import React, { useEffect, useMemo, useRef, useState } from 'react';
import { Save, Loader2, FileSearch, Plus, Trash2 } from 'lucide-react';
import { getIngestProfile, saveIngestProfile, getIngestCodes, saveIngestCodes, previewIngestFile } from '../../api';
import { TABLE_LABELS, TABLES, DROP, fieldLabel, codeLabel, panel, input, inputInline, button, ghost } from './dataFormat';
import { Notice } from './dataParts';

// Columns that would identify a person are never kept (ingestion/profile.py _PERSONAL).
const PERSONAL = /name|address|phone|email|street|postcode|zip|ssn|passport|national_?id|cnic/i;

/**
 * How this hospital's data reads (api/ingest.py /profile and /mapping): its
 * patient id prefix, date order and units; which of its columns is which of
 * our fields, and which it keeps for its own model; and its own codes.
 * `seen` is {table: {headers, mapping}} from files previewed on the Upload tab.
 */
export default function MappingPanel({ seen = {}, onSeen }) {
  const [data, setData] = useState(null);
  const [codes, setCodes] = useState(null);
  const [error, setError] = useState(null);
  const [reloadKey, setReloadKey] = useState(0);

  useEffect(() => {
    let live = true;
    Promise.all([getIngestProfile(), getIngestCodes()])
      .then(([p, c]) => { if (live) { setData(p); setCodes(c); setError(null); } })
      .catch((e) => { if (live) setError(e.message); });
    return () => { live = false; };
  }, [reloadKey]);

  if (error) return <Notice tone="red">{error}</Notice>;
  if (!data || !codes) return <div className="flex items-center gap-2 text-sm text-gray-500"><Loader2 size={16} className="animate-spin" /> Loading…</div>;

  const saved = () => setReloadKey((k) => k + 1);
  return (
    <div className="space-y-5">
      <Basics profile={data.profile} onSaved={saved} />
      <Columns profile={data.profile} tables={data.tables} seen={seen} onSeen={onSeen} onSaved={saved} />
      <Codes codes={codes} onSaved={saved} />
    </div>
  );
}

function useSave(onSaved) {
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState(null);
  const run = async (fn) => {
    setBusy(true); setMsg(null);
    try { await fn(); setMsg({ tone: 'green', text: 'Saved.' }); onSaved?.(); }
    catch (e) { setMsg({ tone: 'red', text: e.message }); }
    finally { setBusy(false); }
  };
  return [busy, msg, run];
}

function Basics({ profile, onSaved }) {
  const [form, setForm] = useState({ patient_prefix: profile.patient_prefix || '', dayfirst: profile.dayfirst,
    weight: profile.units.weight, temperature: profile.units.temperature });
  const [busy, msg, run] = useSave(onSaved);
  const save = () => run(() => saveIngestProfile({
    patient_prefix: form.patient_prefix || undefined, dayfirst: form.dayfirst,
    units: { weight: form.weight, temperature: form.temperature } }));
  return (
    <div className={panel}>
      <h2 className="text-base font-semibold text-gray-800">Basics</h2>
      <div className="mt-3 grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <label className="text-sm text-gray-700">Patient id prefix
          <input className={`${input} mt-1 uppercase`} value={form.patient_prefix} maxLength={10} placeholder="e.g. CGH"
                 onChange={(e) => setForm({ ...form, patient_prefix: e.target.value.toUpperCase() })} />
          <span className="mt-1 block text-xs text-gray-500">Every patient id starts with it: CGH-P1931. Fixed once patients arrive.</span>
        </label>
        <label className="text-sm text-gray-700">Dates are written
          <select className={`${input} mt-1`} value={form.dayfirst ? 'day' : 'month'}
                  onChange={(e) => setForm({ ...form, dayfirst: e.target.value === 'day' })}>
            <option value="month">Month first (10/13/2026)</option>
            <option value="day">Day first (13/10/2026)</option>
          </select>
        </label>
        <label className="text-sm text-gray-700">Weight in
          <select className={`${input} mt-1`} value={form.weight} onChange={(e) => setForm({ ...form, weight: e.target.value })}>
            <option value="kg">kilograms</option><option value="lb">pounds</option>
          </select>
        </label>
        <label className="text-sm text-gray-700">Temperature in
          <select className={`${input} mt-1`} value={form.temperature} onChange={(e) => setForm({ ...form, temperature: e.target.value })}>
            <option value="C">Celsius</option><option value="F">Fahrenheit</option>
          </select>
        </label>
      </div>
      <div className="mt-4 flex items-center gap-3">
        <button type="button" className={button} disabled={busy} onClick={save}>
          {busy ? <Loader2 size={15} className="animate-spin" /> : <Save size={15} />} Save
        </button>
        {msg && <span className={msg.tone === 'red' ? 'text-sm text-red-700' : 'text-sm text-green-700'}>{msg.text}</span>}
      </div>
    </div>
  );
}

function Columns({ profile, tables, seen, onSeen, onSaved }) {
  const [table, setTable] = useState('encounters');
  const [loading, setLoading] = useState(false);
  const [loadError, setLoadError] = useState(null);
  const fileRef = useRef(null);

  const headers = useMemo(() => [...new Set([
    ...(seen[table]?.headers || []), ...Object.keys(profile.columns[table] || {}), ...(profile.extras[table] || []),
    ...(profile.drop[table] || []),
  ])], [seen, table, profile]);

  const loadFile = async (file) => {
    setLoading(true); setLoadError(null);
    try {
      const p = await previewIngestFile(file, table);
      onSeen?.(table, p.headers, p.mapping);
    } catch (e) { setLoadError(e.message); }
    finally { setLoading(false); }
  };

  return (
    <div className={panel}>
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h2 className="text-base font-semibold text-gray-800">Columns</h2>
        <div className="flex flex-wrap gap-2">
          <select className={inputInline} value={table} onChange={(e) => setTable(e.target.value)}>
            {TABLES.map((t) => <option key={t} value={t}>{TABLE_LABELS[t]}</option>)}
          </select>
          <button type="button" className={ghost} onClick={() => fileRef.current?.click()} disabled={loading}>
            {loading ? <Loader2 size={15} className="animate-spin" /> : <FileSearch size={15} />} Read columns from a file
          </button>
          <input ref={fileRef} type="file" accept=".csv,text/csv" className="hidden"
                 onChange={(e) => { if (e.target.files[0]) loadFile(e.target.files[0]); e.target.value = ''; }} />
        </div>
      </div>
      <p className="mt-1 text-sm text-gray-600">
        For each of your columns, which of our fields it is.
        {tables[table].extras_allowed && ' Every other column is kept for your own Model Studio model, unless you say not to use it.'}
        {' '}A column already named like our field needs nothing.
      </p>
      {loadError && <div className="mt-3"><Notice tone="red">{loadError}</Notice></div>}
      {/* Remounted when the table or its columns change, so it starts from the saved choices. */}
      <ColumnEditor key={`${table}:${headers.join('|')}`} table={table} spec={tables[table]} headers={headers}
                    profile={profile} suggested={seen[table]?.mapping || {}} onSaved={onSaved} />
    </div>
  );
}

function ColumnEditor({ table, spec, headers, profile, suggested, onSaved }) {
  // Each column starts as saved, else as the server suggested for the last
  // file seen. A column that is none of our fields is kept for the hospital's
  // own model unless it is dropped - or, on a profile from before drop lists,
  // unless it is missing from the list of columns to keep.
  const explicit = profile.extras[table];
  const outcome = profile.outcome?.table === table ? profile.outcome.column : null;
  const [choice, setChoice] = useState(() => Object.fromEntries(headers.map((h) => {
    const field = (profile.columns[table] || {})[h] || suggested[h];
    if (field) return [h, field];
    const dropped = explicit ? !explicit.includes(h) : (profile.drop[table] || []).includes(h);
    return [h, dropped ? DROP : ''];
  })));
  const [busy, msg, run] = useSave(onSaved);

  const used = Object.values(choice).filter((v) => v && v !== DROP);
  const twice = [...new Set(used.filter((v, i) => used.indexOf(v) !== i))];
  const covered = new Set(used);
  if (covered.has('date_of_birth')) covered.add('age_years');
  const missing = spec.required.filter((f) => !covered.has(f));

  const save = () => run(() => saveIngestProfile({
    columns: { [table]: Object.fromEntries(Object.entries(choice).filter(([, v]) => v && v !== DROP)) },
    ...(spec.extras_allowed ? { drop: { [table]: Object.entries(choice).filter(([, v]) => v === DROP).map(([k]) => k) } } : {}),
  }));

  if (headers.length === 0) {
    return (
      <p className="mt-4 text-sm text-gray-500">
        No {TABLE_LABELS[table].toLowerCase()} file seen yet. Read the columns from one of your files, or upload it on
        the Upload tab.
      </p>
    );
  }
  return (
    <>
      <table className="mt-4 w-full text-left text-sm">
        <thead className="border-b border-gray-200 text-xs uppercase text-gray-500">
          <tr><th className="py-2 pr-3">Your column</th><th className="py-2">Our field</th></tr>
        </thead>
        <tbody>
          {headers.map((h) => (
            <tr key={h} className="border-b border-gray-100">
              <td className="py-1.5 pr-3 font-mono text-xs text-gray-800">{h}</td>
              <td className="py-1.5">
                {h === outcome && !choice[h] ? (
                  <span className="text-xs font-medium text-indigo-700">
                    The readmission outcome: {profile.outcome.positive.join(', ')} means readmitted within 30 days
                  </span>
                ) : (
                  <select className={`${inputInline} w-full max-w-xs ${twice.includes(choice[h]) ? 'border-red-400' : ''}`}
                          value={choice[h] || ''} onChange={(e) => setChoice({ ...choice, [h]: e.target.value })}>
                    {!spec.extras_allowed && <option value="">Not used</option>}
                    {spec.extras_allowed && (PERSONAL.test(h)
                      ? <option value="">Not stored (personal)</option>
                      : <><option value="">Keep for your own model</option><option value={DROP}>Don&apos;t use</option></>)}
                    <optgroup label="Required">{spec.required.map((f) => <option key={f} value={f}>{fieldLabel(f)}</option>)}</optgroup>
                    <optgroup label="Optional">{spec.optional.map((f) => <option key={f} value={f}>{fieldLabel(f)}</option>)}</optgroup>
                  </select>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      <div className="mt-3 space-y-2">
        {missing.length > 0 && (
          <Notice tone="amber">Still without a column: <strong>{missing.map(fieldLabel).join(', ')}</strong>
            {table === 'encounters' && missing.includes('age_years') && ' (or map a date of birth)'}.</Notice>
        )}
        {twice.length > 0 && <Notice tone="red">{twice.map(fieldLabel).join(', ')} chosen for more than one column.</Notice>}
      </div>
      <div className="mt-4 flex items-center gap-3">
        <button type="button" className={button} disabled={busy || twice.length > 0} onClick={save}>
          {busy ? <Loader2 size={15} className="animate-spin" /> : <Save size={15} />} Save {TABLE_LABELS[table].toLowerCase()} columns
        </button>
        {msg && <span className={msg.tone === 'red' ? 'text-sm text-red-700' : 'text-sm text-green-700'}>{msg.text}</span>}
      </div>
      <details className="mt-4 text-xs text-gray-500">
        <summary className="cursor-pointer">All our fields for {TABLE_LABELS[table].toLowerCase()}</summary>
        <p className="mt-1">Required: {spec.required.join(', ')}</p>
        <p className="mt-1">Optional: {spec.optional.join(', ')}</p>
      </details>
    </>
  );
}

function Codes({ codes, onSaved }) {
  const [rows, setRows] = useState(() => Object.entries(codes.mapping)
    .flatMap(([field, m]) => Object.entries(m || {}).map(([code, value]) => ({ field, code, value }))));
  const [busy, msg, run] = useSave(onSaved);
  const fields = Object.keys(codes.canonical);

  const save = () => run(() => {
    const body = Object.fromEntries(fields.map((f) => [f, {}]));
    rows.filter((r) => r.code.trim() && r.value).forEach((r) => { body[r.field][r.code.trim()] = r.value; });
    return saveIngestCodes(body);
  });

  return (
    <div className={panel}>
      <h2 className="text-base font-semibold text-gray-800">Codes</h2>
      <p className="mt-1 text-sm text-gray-600">
        Your system's own values for coded fields, and what they mean in ours - for example ER means Emergency.
        A value that is neither is stored as unknown, with a warning: never guessed.
      </p>
      <table className="mt-4 w-full text-left text-sm">
        <thead className="border-b border-gray-200 text-xs uppercase text-gray-500">
          <tr><th className="py-2 pr-3">Field</th><th className="py-2 pr-3">Your value</th><th className="py-2 pr-3">Our value</th><th /></tr>
        </thead>
        <tbody>
          {rows.map((r, i) => (
            <tr key={i} className="border-b border-gray-100">
              <td className="py-1.5 pr-3">
                <select className={inputInline} value={r.field}
                        onChange={(e) => setRows(rows.map((x, j) => (j === i ? { ...x, field: e.target.value, value: '' } : x)))}>
                  {fields.map((f) => <option key={f} value={f}>{fieldLabel(f)}</option>)}
                </select>
              </td>
              <td className="py-1.5 pr-3">
                <input className={inputInline} value={r.code} placeholder="ER"
                       onChange={(e) => setRows(rows.map((x, j) => (j === i ? { ...x, code: e.target.value } : x)))} />
              </td>
              <td className="py-1.5 pr-3">
                <select className={inputInline} value={r.value}
                        onChange={(e) => setRows(rows.map((x, j) => (j === i ? { ...x, value: e.target.value } : x)))}>
                  <option value="">Choose…</option>
                  {codes.canonical[r.field].map((v) => <option key={v} value={v}>{codeLabel(v)}</option>)}
                </select>
              </td>
              <td className="py-1.5 text-right">
                <button type="button" className="text-gray-400 hover:text-red-600" title="Remove"
                        onClick={() => setRows(rows.filter((_, j) => j !== i))}><Trash2 size={15} /></button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      <div className="mt-3 flex flex-wrap items-center gap-3">
        <button type="button" className={ghost} onClick={() => setRows([...rows, { field: fields[0], code: '', value: '' }])}>
          <Plus size={15} /> Add a code
        </button>
        <button type="button" className={button} disabled={busy} onClick={save}>
          {busy ? <Loader2 size={15} className="animate-spin" /> : <Save size={15} />} Save codes
        </button>
        {msg && <span className={msg.tone === 'red' ? 'text-sm text-red-700' : 'text-sm text-green-700'}>{msg.text}</span>}
      </div>
    </div>
  );
}

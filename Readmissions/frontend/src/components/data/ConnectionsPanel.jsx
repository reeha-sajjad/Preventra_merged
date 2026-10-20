import React, { useCallback, useEffect, useState } from 'react';
import { KeyRound, Database, Plus, Loader2, Play, History, Trash2, Pencil, PlugZap, Copy, CheckCircle2, XCircle } from 'lucide-react';
import {
  getIngestClients, createIngestClient, revokeIngestClient,
  getConnections, createConnection, updateConnection, deleteConnection, testConnection, runConnection,
} from '../../api';
import { TABLE_LABELS, TABLES, panel, input, button, ghost, when } from './dataFormat';
import { Notice } from './dataParts';

/**
 * The two ways a hospital's systems send data on their own: an API credential
 * for its interface engine or nightly script (/ingest/v1), and a database
 * connection Preventra reads on a schedule (api/ingest_connections.py).
 */
export default function ConnectionsPanel({ prefix, onGoToMapping }) {
  return (
    <div className="space-y-5">
      <Credentials prefix={prefix} onGoToMapping={onGoToMapping} />
      <Connections />
    </div>
  );
}

// ---------------------------------------------------------------- API credentials
function Credentials({ prefix, onGoToMapping }) {
  const [clients, setClients] = useState(null);
  const [made, setMade] = useState(null);
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);
  const [copied, setCopied] = useState(false);
  const [reloadKey, setReloadKey] = useState(0);

  useEffect(() => {
    let live = true;
    getIngestClients().then((r) => { if (live) setClients(r.clients); }).catch((e) => { if (live) setError(e.message); });
    return () => { live = false; };
  }, [reloadKey]);

  const create = async () => {
    setBusy(true); setError(null);
    try { setMade(await createIngestClient(prefix)); setReloadKey((k) => k + 1); }
    catch (e) { setError(e.message); }
    finally { setBusy(false); }
  };
  const revoke = async (id) => {
    if (!window.confirm('Revoke this credential? Anything still using it is refused from now on.')) return;
    try { await revokeIngestClient(id); setReloadKey((k) => k + 1); } catch (e) { setError(e.message); }
  };
  const copy = async () => {
    try { await navigator.clipboard.writeText(made.token); setCopied(true); } catch { /* select it by hand */ }
  };

  return (
    <div className={panel}>
      <h2 className="flex items-center gap-2 text-base font-semibold text-gray-800"><KeyRound size={17} /> API credentials</h2>
      <p className="mt-1 text-sm text-gray-600">
        For your interface engine or a nightly script sending to <code className="text-xs">/ingest/v1</code>: discharges and
        weekly check-ins as JSON, or your CSV files in one request.
      </p>
      {!prefix ? (
        <div className="mt-3"><Notice tone="amber">Set the patient id prefix on the{' '}
          <button type="button" className="font-medium underline" onClick={onGoToMapping}>Mapping tab</button> first.</Notice></div>
      ) : (
        <button type="button" className={`${ghost} mt-3`} onClick={create} disabled={busy}>
          {busy ? <Loader2 size={15} className="animate-spin" /> : <Plus size={15} />} New credential
        </button>
      )}
      {made && (
        <div className="mt-3 rounded-lg border border-amber-300 bg-amber-50 p-3 text-sm">
          <p className="font-semibold text-amber-900">Copy this now. It is stored hashed and cannot be shown again.</p>
          <div className="mt-2 flex items-center gap-2">
            <code className="block flex-1 overflow-x-auto rounded bg-white px-2 py-1 text-xs">{made.token}</code>
            <button type="button" className={ghost} onClick={copy}><Copy size={14} /> {copied ? 'Copied' : 'Copy'}</button>
          </div>
        </div>
      )}
      {error && <div className="mt-3"><Notice tone="red">{error}</Notice></div>}
      {clients && clients.length > 0 && (
        <table className="mt-4 w-full text-left text-sm">
          <thead className="border-b border-gray-200 text-xs uppercase text-gray-500">
            <tr><th className="py-2 pr-3">Credential</th><th className="py-2 pr-3">Made</th><th className="py-2 pr-3">Last used</th><th /></tr>
          </thead>
          <tbody>
            {clients.map((c) => (
              <tr key={c.client_id} className="border-b border-gray-100">
                <td className="py-1.5 pr-3 font-mono text-xs">{c.client_id} <span className="text-gray-400">({c.patient_prefix})</span></td>
                <td className="py-1.5 pr-3 text-xs text-gray-600">{when(c.created_at)} · {c.created_by}</td>
                <td className="py-1.5 pr-3 text-xs text-gray-600">{when(c.last_used_at)}</td>
                <td className="py-1.5 text-right">
                  {c.revoked ? <span className="text-xs text-gray-400">revoked</span>
                    : <button type="button" className="text-xs font-medium text-red-700 hover:underline" onClick={() => revoke(c.client_id)}>Revoke</button>}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}

// ----------------------------------------------------------- database connections
const EMPTY = { name: '', dialect: 'mssql', host: '', port: '', database: '', username: '', password: '',
  schedule_at: '', overlap_hours: 2, first_since: '', enabled: true, queries: { encounters: '' } };

function Connections() {
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);
  const [editing, setEditing] = useState(null);           // null, 'new', or a connection
  const [note, setNote] = useState(null);
  const [reloadKey, setReloadKey] = useState(0);
  const reload = useCallback(() => setReloadKey((k) => k + 1), []);

  useEffect(() => {
    let live = true;
    getConnections().then((d) => { if (live) { setData(d); setError(null); } })
      .catch((e) => { if (live) setError(e.message); });
    return () => { live = false; };
  }, [reloadKey]);

  const run = async (c, history) => {
    setNote(null);
    try {
      await runConnection(c.connection_id, history);
      setNote(`${c.name}: pull queued${history ? ' as history' : ''}. Its result shows here and on the Activity tab.`);
      setTimeout(reload, 4000);
    } catch (e) { setError(e.message); }
  };
  const remove = async (c) => {
    if (!window.confirm(`Delete the connection "${c.name}"? What it already brought in stays.`)) return;
    try { await deleteConnection(c.connection_id); reload(); } catch (e) { setError(e.message); }
  };

  if (!data) return error ? <Notice tone="red">{error}</Notice> : null;
  const tz = data.timezone;
  return (
    <div className={panel}>
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h2 className="flex items-center gap-2 text-base font-semibold text-gray-800"><Database size={17} /> Database connections</h2>
          <p className="mt-1 text-sm text-gray-600">
            Preventra reads your database every night, only what changed since the last read, with a read-only user.
            SQL Server, PostgreSQL, MySQL / MariaDB and Oracle.
          </p>
        </div>
        {!editing && (
          <button type="button" className={ghost} onClick={() => setEditing('new')} disabled={!data.key_set}>
            <Plus size={15} /> Add a connection
          </button>
        )}
      </div>
      {!data.key_set && (
        <div className="mt-3"><Notice tone="amber">
          The server has no <code>INGEST_SECRET_KEY</code>, so a database password cannot be stored safely. Set it on the
          API and the worker, then come back.
        </Notice></div>
      )}
      {error && <div className="mt-3"><Notice tone="red">{error}</Notice></div>}
      {note && <div className="mt-3"><Notice tone="green">{note}</Notice></div>}

      {editing && (
        <ConnectionForm key={editing === 'new' ? 'new' : editing.connection_id} existing={editing === 'new' ? null : editing}
          meta={data} onCancel={() => setEditing(null)} onSaved={() => { setEditing(null); reload(); }} />
      )}

      {!editing && data.connections.length === 0 && <p className="mt-4 text-sm text-gray-500">No connections yet.</p>}
      {!editing && data.connections.length > 0 && (
        <ul className="mt-4 space-y-3">
          {data.connections.map((c) => (
            <li key={c.connection_id} className="rounded-lg border border-gray-200 p-3">
              <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
                <span className="font-semibold text-gray-800">{c.name}</span>
                <span className="text-xs text-gray-500">{c.dialect_label} · {c.host}/{c.database}</span>
                <span className={`rounded-full px-2 py-0.5 text-xs font-semibold ${c.enabled ? 'bg-green-50 text-green-700' : 'bg-gray-100 text-gray-500'}`}>
                  {c.enabled ? `daily at ${c.schedule_at || data.default_at} (${tz})` : 'paused'}
                </span>
                <div className="ml-auto flex flex-wrap gap-1.5">
                  <button type="button" className={ghost} onClick={() => run(c, false)} title="Read what changed now"><Play size={14} /> Run now</button>
                  <button type="button" className={ghost} onClick={() => run(c, true)} title="Read and keep as history (training only)"><History size={14} /> Load as history</button>
                  <button type="button" className={ghost} onClick={() => setEditing(c)}><Pencil size={14} /> Edit</button>
                  <button type="button" className={ghost} onClick={() => remove(c)}><Trash2 size={14} /></button>
                </div>
              </div>
              <div className="mt-1.5 text-xs text-gray-600">
                Tables: {Object.keys(c.queries || {}).map((t) => TABLE_LABELS[t] || t).join(', ')}
                {' · '}{c.enabled ? `next ${when(c.next_run_at)}` : 'not scheduled'}
              </div>
              {c.last_run && (
                <div className={`mt-1 text-xs ${c.last_run.ok ? 'text-gray-600' : 'text-red-700'}`}>
                  Last run {when(c.last_run.at)}{c.last_run.history_only ? ' (history)' : ''}:{' '}
                  {c.last_run.ok
                    ? `${Object.entries(c.last_run.rows || {}).map(([t, n]) => `${n} ${t}`).join(', ')}; ${c.last_run.accepted} accepted, ${c.last_run.rejected} rejected`
                    : c.last_run.error}
                </div>
              )}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function ConnectionForm({ existing, meta, onCancel, onSaved }) {
  const [form, setForm] = useState(() => (existing ? {
    ...EMPTY, ...existing, port: existing.port || '', password: '', first_since: existing.first_since || '',
    schedule_at: existing.schedule_at || '', queries: { ...existing.queries },
  } : EMPTY));
  const [busy, setBusy] = useState(null);
  const [error, setError] = useState(null);
  const [report, setReport] = useState(null);
  const set = (k, v) => setForm((f) => ({ ...f, [k]: v }));
  const setQuery = (t, v) => setForm((f) => ({ ...f, queries: { ...f.queries, [t]: v } }));

  const body = () => {
    const out = { ...form, port: form.port ? Number(form.port) : null, overlap_hours: Number(form.overlap_hours) || 0,
      queries: Object.fromEntries(Object.entries(form.queries).filter(([, s]) => s && s.trim())) };
    ['schedule_at', 'first_since', 'password'].forEach((k) => { if (!out[k]) delete out[k]; });
    ['connection_id', 'dialect_label', 'has_password', 'last_run', 'next_run_at', 'watermark', 'created_at',
      'created_by', 'hospital_id'].forEach((k) => delete out[k]);
    return out;
  };

  const test = async () => {
    setBusy('test'); setError(null); setReport(null);
    try { setReport(await testConnection({ ...body(), ...(existing ? { connection_id: existing.connection_id } : {}) })); }
    catch (e) { setError(e.message); }
    finally { setBusy(null); }
  };
  const save = async () => {
    setBusy('save'); setError(null);
    try {
      if (existing) await updateConnection(existing.connection_id, body());
      else await createConnection(body());
      onSaved();
    } catch (e) { setError(e.message); }
    finally { setBusy(null); }
  };

  const unused = TABLES.filter((t) => !(t in form.queries));
  const isOracle = form.dialect === 'oracle';
  return (
    <div className="mt-4 rounded-lg border border-gray-200 bg-gray-50 p-4">
      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
        <label className="text-sm text-gray-700">Name<input className={`${input} mt-1`} value={form.name} onChange={(e) => set('name', e.target.value)} placeholder="Main HIS" /></label>
        <label className="text-sm text-gray-700">Database type
          <select className={`${input} mt-1`} value={form.dialect} onChange={(e) => set('dialect', e.target.value)}>
            {Object.entries(meta.dialects).map(([k, label]) => <option key={k} value={k}>{label}</option>)}
          </select>
        </label>
        <label className="text-sm text-gray-700">Host<input className={`${input} mt-1`} value={form.host} onChange={(e) => set('host', e.target.value)} placeholder="his-db.hospital.local" /></label>
        <label className="text-sm text-gray-700">Port<input className={`${input} mt-1`} value={form.port} onChange={(e) => set('port', e.target.value.replace(/\D/g, ''))} placeholder={String(meta.default_ports[form.dialect] || '')} /></label>
        <label className="text-sm text-gray-700">{isOracle ? 'Service name' : 'Database'}<input className={`${input} mt-1`} value={form.database} onChange={(e) => set('database', e.target.value)} /></label>
        <label className="text-sm text-gray-700">Read-only user<input className={`${input} mt-1`} value={form.username} autoComplete="off" onChange={(e) => set('username', e.target.value)} /></label>
        <label className="text-sm text-gray-700">Password<input type="password" className={`${input} mt-1`} value={form.password} autoComplete="new-password"
          placeholder={existing?.has_password ? 'unchanged' : ''} onChange={(e) => set('password', e.target.value)} /></label>
        <label className="text-sm text-gray-700">Run daily at ({meta.timezone})<input className={`${input} mt-1`} value={form.schedule_at} placeholder={meta.default_at} onChange={(e) => set('schedule_at', e.target.value)} /></label>
        <label className="text-sm text-gray-700">First read from (date)<input type="date" className={`${input} mt-1`} value={form.first_since} onChange={(e) => set('first_since', e.target.value)} /></label>
      </div>
      <label className="mt-3 flex items-center gap-2 text-sm text-gray-700">
        <input type="checkbox" checked={form.enabled} onChange={(e) => set('enabled', e.target.checked)} /> Run every night
      </label>

      <h3 className="mt-4 text-sm font-semibold text-gray-800">Queries</h3>
      <p className="text-xs text-gray-500">
        One SELECT per table, in your own column names (your mapping reads them). Write <code>:since</code> to read only
        what changed since the last run, e.g. <code>SELECT * FROM adt.discharges WHERE last_updated &gt;= :since</code>.
      </p>
      <div className="mt-2 space-y-2">
        {Object.keys(form.queries).map((t) => (
          <div key={t}>
            <div className="flex items-center justify-between text-xs font-medium text-gray-600">
              {TABLE_LABELS[t]}
              <button type="button" className="text-gray-400 hover:text-red-600" onClick={() => setForm((f) => {
                const q = { ...f.queries }; delete q[t]; return { ...f, queries: q };
              })}>remove</button>
            </div>
            <textarea className={`${input} mt-1 font-mono text-xs`} rows={2} value={form.queries[t]} onChange={(e) => setQuery(t, e.target.value)} />
          </div>
        ))}
        {unused.length > 0 && (
          <select className="rounded-md border border-dashed border-gray-300 bg-white px-2 py-1.5 text-sm text-gray-600" value=""
                  onChange={(e) => e.target.value && setQuery(e.target.value, '')}>
            <option value="">+ Add a table…</option>
            {unused.map((t) => <option key={t} value={t}>{TABLE_LABELS[t]}</option>)}
          </select>
        )}
      </div>

      {error && <div className="mt-3"><Notice tone="red">{error}</Notice></div>}
      {report && <TestReport report={report} />}
      <div className="mt-4 flex flex-wrap gap-2">
        <button type="button" className={ghost} onClick={test} disabled={!!busy}>
          {busy === 'test' ? <Loader2 size={15} className="animate-spin" /> : <PlugZap size={15} />} Test
        </button>
        <button type="button" className={button} onClick={save} disabled={!!busy}>
          {busy === 'save' ? <Loader2 size={15} className="animate-spin" /> : null} {existing ? 'Save changes' : 'Save connection'}
        </button>
        <button type="button" className={ghost} onClick={onCancel}>Cancel</button>
      </div>
    </div>
  );
}

function TestReport({ report }) {
  return (
    <div className="mt-3 space-y-2 rounded-lg border border-gray-200 bg-white p-3 text-sm">
      {report.connected ? (
        <p className="flex items-center gap-1.5 text-green-700"><CheckCircle2 size={15} /> Connected.</p>
      ) : (
        <p className="flex items-center gap-1.5 text-red-700"><XCircle size={15} /> {report.error}</p>
      )}
      {Object.entries(report.tables || {}).map(([t, r]) => (
        <div key={t} className="text-xs">
          <span className="font-semibold text-gray-700">{TABLE_LABELS[t] || t}: </span>
          {r.ok ? (
            <>
              {r.sample.length ? `${r.sample.length}${r.sample.length === 5 ? '+' : ''} rows changed in the last day` : 'no rows changed in the last day'}; columns {r.columns.join(', ')}.
              {r.mapping?.missing?.length > 0
                ? <span className="text-amber-700"> No column yet for {r.mapping.missing.join(', ')} - map them on the Mapping tab.</span>
                : <span className="text-green-700"> Every required field has a column.</span>}
            </>
          ) : <span className="text-red-700">{r.error}</span>}
        </div>
      ))}
    </div>
  );
}

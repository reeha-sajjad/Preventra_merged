import React, { useCallback, useEffect, useState } from 'react';
import { Pill, Plus, Pencil, Loader2, X, Info } from 'lucide-react';
import { getPatientMedicines, addPatientMedicine, changePatientMedicine, stopPatientMedicine } from '../../api';

const BLANK = { drug: '', dose: '', route: 'By mouth', how_often: 'Once a day', instructions: '' };
const SOURCE_PILL = {
  demo: 'bg-amber-50 text-amber-800 border-amber-200',
  mimic: 'bg-sky-50 text-sky-800 border-sky-200',
  doctor: 'bg-emerald-50 text-emerald-800 border-emerald-200',
};
const SOURCE_SHORT = { demo: 'Demo', mimic: 'Hospital orders', doctor: 'Doctor' };

/**
 * The patient's medicines at home, with doses (api/patient_medicines.py), on the
 * staff patient page. Everyone on the care team reads it; a doctor adds, changes
 * and stops. Never a prescription, and never an input to the risk score.
 */
export default function MedicinesCard({ patientId }) {
  const [data, setData] = useState(null);
  const [editing, setEditing] = useState(null);      // null | 'new' | item
  const [form, setForm] = useState(BLANK);
  const [stopping, setStopping] = useState(null);
  const [reason, setReason] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);

  const load = useCallback(() => {
    getPatientMedicines(patientId).then(setData).catch(() => setData(null));
  }, [patientId]);
  useEffect(() => { load(); }, [load]);
  if (!data) return null;

  const set = (k) => (e) => setForm((f) => ({ ...f, [k]: e.target.value }));
  const open = (item) => {
    setError(null); setStopping(null);
    setEditing(item || 'new');
    setForm(item ? { drug: item.drug, dose: item.dose, route: item.route || 'By mouth',
                     how_often: item.how_often || 'Once a day', instructions: item.instructions || '' } : BLANK);
  };
  const run = async (fn) => {
    setBusy(true); setError(null);
    try { setData({ ...data, ...(await fn()) }); setEditing(null); setStopping(null); setReason(''); }
    catch (e) { setError(e.message); }
    finally { setBusy(false); }
  };
  const save = (e) => {
    e.preventDefault();
    run(() => (editing === 'new' ? addPatientMedicine(patientId, form)
      : changePatientMedicine(patientId, editing.id, form)));
  };

  const field = 'mt-1 w-full rounded-lg border border-gray-200 bg-white px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-300';

  return (
    <section className="rounded-xl border border-gray-200 bg-white p-6 shadow-sm">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h2 className="flex items-center gap-2 text-base font-semibold text-gray-900">
            <Pill size={17} className="text-gray-400" /> Medicines at home
          </h2>
          <p className="mt-0.5 text-xs text-gray-500">What the patient takes, how much and how often. {data.model_note}</p>
        </div>
        {data.can_edit && !editing && (
          <button type="button" onClick={() => open(null)}
            className="inline-flex items-center gap-1.5 rounded-lg border border-ns-navy/30 px-3 py-1.5 text-sm font-medium text-ns-navy hover:bg-ns-navy/5">
            <Plus size={15} /> Add medicine
          </button>
        )}
      </div>

      {data.has_demo && (
        <p className="mt-3 flex items-start gap-2 rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-900">
          <Info size={14} className="mt-0.5 flex-shrink-0" />
          Medicines marked Demo are made up for this demo, not from any hospital record. A doctor’s change replaces them.
        </p>
      )}

      {data.medicines.length === 0 && !editing && (
        <p className="mt-4 text-sm text-gray-400">No medicines recorded yet.</p>
      )}
      {data.medicines.length > 0 && (
        <div className="mt-4 overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b text-left text-xs uppercase tracking-wide text-gray-400">
                <th className="py-2 pr-3 font-medium">Medicine</th>
                <th className="py-2 pr-3 font-medium">Dose</th>
                <th className="py-2 pr-3 font-medium">How</th>
                <th className="py-2 pr-3 font-medium">How often</th>
                <th className="py-2 pr-3 font-medium">Source</th>
                {data.can_edit && <th className="py-2" />}
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-100">
              {data.medicines.map((m) => (
                <tr key={m.id} className="align-top">
                  <td className="py-2.5 pr-3">
                    <div className="font-medium text-gray-900">{m.drug}</div>
                    {m.instructions && <div className="text-xs text-gray-500">{m.instructions}</div>}
                  </td>
                  <td className="py-2.5 pr-3 font-semibold text-gray-900">{m.dose}</td>
                  <td className="py-2.5 pr-3 text-gray-600">{m.route}</td>
                  <td className="py-2.5 pr-3 text-gray-600">{m.how_often}</td>
                  <td className="py-2.5 pr-3">
                    <span title={m.source_label}
                      className={`inline-block rounded-full border px-2 py-0.5 text-[11px] font-medium ${SOURCE_PILL[m.source] || SOURCE_PILL.doctor}`}>
                      {SOURCE_SHORT[m.source] || 'Doctor'}
                    </span>
                  </td>
                  {data.can_edit && (
                    <td className="whitespace-nowrap py-2.5 text-right">
                      <button type="button" onClick={() => open(m)} className="mr-3 inline-flex items-center gap-1 text-xs font-medium text-ns-navy">
                        <Pencil size={11} /> Change
                      </button>
                      <button type="button" onClick={() => { setEditing(null); setStopping(m); setError(null); }}
                        className="text-xs font-medium text-gray-500 hover:text-red-700">Stop</button>
                    </td>
                  )}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {editing && (
        <form onSubmit={save} className="mt-4 space-y-3 rounded-lg border border-gray-200 bg-gray-50/60 p-4">
          <p className="text-sm font-medium text-gray-800">{editing === 'new' ? 'Add a medicine' : `Change ${editing.drug}`}</p>
          <div className="grid gap-3 sm:grid-cols-2">
            <label className="text-xs text-gray-600">Medicine
              <input value={form.drug} onChange={set('drug')} maxLength={80} required className={field} placeholder="e.g. Furosemide" />
            </label>
            <label className="text-xs text-gray-600">Dose
              <input value={form.dose} onChange={set('dose')} maxLength={80} required className={field} placeholder="e.g. 40 mg" />
            </label>
            <label className="text-xs text-gray-600">How it’s taken
              <select value={form.route} onChange={set('route')} className={field}>
                {data.routes.map((r) => <option key={r}>{r}</option>)}
              </select>
            </label>
            <label className="text-xs text-gray-600">How often
              <select value={form.how_often} onChange={set('how_often')} className={field}>
                {[...new Set([form.how_often, ...data.how_often_options])].map((r) => <option key={r}>{r}</option>)}
              </select>
            </label>
            <label className="text-xs text-gray-600 sm:col-span-2">Instructions (optional)
              <input value={form.instructions} onChange={set('instructions')} maxLength={200} className={field}
                placeholder="e.g. In the morning, with food" />
            </label>
          </div>
          {error && <p role="alert" className="text-sm text-red-700">{error}</p>}
          <div className="flex gap-2">
            <button type="submit" disabled={busy}
              className="inline-flex items-center gap-1.5 rounded-lg bg-ns-navy px-3 py-1.5 text-sm font-semibold text-white disabled:opacity-50">
              {busy && <Loader2 size={13} className="animate-spin" />} Save
            </button>
            <button type="button" onClick={() => setEditing(null)}
              className="rounded-lg border border-gray-200 px-3 py-1.5 text-sm text-gray-600">Cancel</button>
          </div>
        </form>
      )}

      {stopping && (
        <div className="mt-4 space-y-2 rounded-lg border border-red-100 bg-red-50/50 p-4">
          <div className="flex items-start justify-between">
            <p className="text-sm font-medium text-gray-800">Stop {stopping.drug} {stopping.dose}?</p>
            <button type="button" onClick={() => setStopping(null)} aria-label="Close" className="text-gray-400"><X size={16} /></button>
          </div>
          <input value={reason} onChange={(e) => setReason(e.target.value)} maxLength={200}
            placeholder="Why (optional), e.g. side effects" className={field} />
          {error && <p role="alert" className="text-sm text-red-700">{error}</p>}
          <button type="button" disabled={busy} onClick={() => run(() => stopPatientMedicine(patientId, stopping.id, reason))}
            className="inline-flex items-center gap-1.5 rounded-lg bg-red-700 px-3 py-1.5 text-sm font-semibold text-white disabled:opacity-50">
            {busy && <Loader2 size={13} className="animate-spin" />} Stop medicine
          </button>
        </div>
      )}

      {data.stopped?.length > 0 && (
        <details className="mt-4 text-sm">
          <summary className="cursor-pointer text-xs font-medium text-gray-500">Stopped ({data.stopped.length})</summary>
          <ul className="mt-2 space-y-1 text-gray-500">
            {data.stopped.map((m) => (
              <li key={m.id}>
                <span className="line-through">{m.drug} {m.dose}</span>
                {' · '}stopped {new Date(m.stopped_at).toLocaleDateString()}{m.changed_by ? ` by ${m.changed_by}` : ''}
                {m.stop_reason ? ` · ${m.stop_reason}` : ''}
              </li>
            ))}
          </ul>
        </details>
      )}
    </section>
  );
}

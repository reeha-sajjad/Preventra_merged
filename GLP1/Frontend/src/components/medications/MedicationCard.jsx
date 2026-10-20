import { useCallback, useEffect, useState } from 'react';
import { Pill, Loader2, ArrowRight, Info, History } from 'lucide-react';
import { api } from '../../data/api';

/**
 * Medication on the patient's page - Backend/core/medications.py.
 *
 * Shows the drug the risk score was calculated on and the drug the patient is
 * on now, with every recorded change. A doctor can record a switch or a stop.
 * The risk score is the model's and is never changed by hand; the card says so.
 */
export default function MedicationCard({ patientIdx }) {
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);
  const [form, setForm] = useState(null);
  const [busy, setBusy] = useState(false);
  const today = new Date().toISOString().slice(0, 10);

  const load = useCallback(() => {
    api.getMedications(patientIdx).then(setData).catch((e) => setError(e.message));
  }, [patientIdx]);
  useEffect(() => { load(); }, [load]);

  if (error && !data) return null;          // e.g. a reason role before a reason: the gate explains it

  const save = async (e) => {
    e.preventDefault();
    if (!form.to_drug) { setError('Choose the new drug.'); return; }
    if (!form.reason) { setError('Choose a reason.'); return; }
    setBusy(true); setError(null);
    try {
      await api.recordMedicationChange(patientIdx, {
        to_drug: form.to_drug, reason: form.reason, note: form.note.trim() || undefined,
        effective_date: form.effective_date,
      });
      setForm(null); load();
    } catch (err) { setError(err.message); }
    finally { setBusy(false); }
  };

  const changed = data && data.current_drug !== data.original_drug;

  return (
    <div className="card p-5 md:p-6">
      <div className="flex flex-wrap items-start justify-between gap-2 mb-4">
        <div className="flex items-center gap-3">
          <div className="w-9 h-9 rounded-xl flex items-center justify-center" style={{ background: '#EBF4FF' }}>
            <Pill size={16} style={{ color: 'var(--color-primary)' }} />
          </div>
          <div>
            <h2 className="text-base font-semibold text-gray-800" style={{ fontFamily: 'var(--p-font-display)' }}>Medication</h2>
            <p className="text-xs text-gray-400">The GLP-1 drug this patient is on, and any changes.</p>
          </div>
        </div>
        {data?.can_record && !form && (
          <button type="button" onClick={() => setForm({ to_drug: '', reason: '', note: '', effective_date: today })}
            className="text-sm font-semibold px-4 py-2 rounded-lg text-white" style={{ background: 'var(--color-primary)' }}>
            Record a change
          </button>
        )}
      </div>

      {!data && <div className="h-16 rounded-lg bg-gray-100 animate-pulse" />}

      {data && (
        <>
          <div className="grid gap-3 sm:grid-cols-2">
            <div className="rounded-xl border border-gray-100 p-3">
              <div className="text-[10px] font-semibold uppercase tracking-wider text-gray-400">Now on</div>
              <div className="text-lg font-semibold text-gray-800 mt-0.5">{data.current_label}</div>
              {changed && data.changes[0] && (
                <div className="text-[11px] text-gray-400 mt-0.5">since {data.changes[0].effective_date}</div>
              )}
            </div>
            <div className="rounded-xl border border-gray-100 p-3">
              <div className="text-[10px] font-semibold uppercase tracking-wider text-gray-400">Risk score based on</div>
              <div className="text-lg font-semibold text-gray-800 mt-0.5">{data.original_label}</div>
              <div className="text-[11px] text-gray-400 mt-0.5">the drug in the data the model scored</div>
            </div>
          </div>
          {changed && (
            <p className="mt-3 flex gap-2 rounded-lg px-3 py-2.5 text-xs" style={{ background: '#F7FAFC', color: '#4A5568' }}>
              <Info size={14} className="flex-shrink-0 mt-0.5" /> {data.score_note}
            </p>
          )}

          {data.changes.length > 0 && (
            <div className="mt-4">
              <div className="text-[10px] font-semibold uppercase tracking-wider text-gray-400 mb-2 flex items-center gap-1.5">
                <History size={12} /> Changes
              </div>
              <ul className="divide-y divide-gray-100">
                {data.changes.map((c) => (
                  <li key={c.id} className="py-2.5 text-sm">
                    <div className="flex flex-wrap items-center gap-2 text-gray-800">
                      <span>{c.from_label}</span><ArrowRight size={13} className="text-gray-400" />
                      <b>{c.to_label}</b>
                      <span className="text-[11px] px-2 py-0.5 rounded-full" style={{ background: '#F1F5F9', color: '#475569' }}>
                        {c.reason_label}
                      </span>
                    </div>
                    <div className="text-[11px] text-gray-400 mt-0.5">
                      From {c.effective_date} · recorded by {c.recorded_by.name}
                    </div>
                    {c.note && <div className="text-xs text-gray-600 mt-0.5">&ldquo;{c.note}&rdquo;</div>}
                  </li>
                ))}
              </ul>
            </div>
          )}
        </>
      )}

      {form && data && (
        <form onSubmit={save} className="mt-4 rounded-xl border border-gray-200 p-4 space-y-3">
          <div className="grid gap-3 sm:grid-cols-2">
            <label className="block">
              <span className="text-xs font-semibold uppercase tracking-wider text-gray-400">Change to</span>
              <select value={form.to_drug} onChange={(e) => setForm({ ...form, to_drug: e.target.value })}
                className="mt-1.5 w-full text-sm rounded-lg border border-gray-200 px-3 py-2 bg-white focus:outline-none focus:ring-1 focus:ring-blue-400">
                <option value="">Choose…</option>
                {data.drugs.filter((d) => d.key !== data.current_drug).map((d) => (
                  <option key={d.key} value={d.key}>{d.label}</option>
                ))}
              </select>
            </label>
            <label className="block">
              <span className="text-xs font-semibold uppercase tracking-wider text-gray-400">From</span>
              <input type="date" value={form.effective_date} max={today}
                onChange={(e) => setForm({ ...form, effective_date: e.target.value })}
                className="mt-1.5 w-full text-sm rounded-lg border border-gray-200 px-3 py-2 focus:outline-none focus:ring-1 focus:ring-blue-400" />
            </label>
          </div>
          <label className="block">
            <span className="text-xs font-semibold uppercase tracking-wider text-gray-400">Reason</span>
            <select value={form.reason} onChange={(e) => setForm({ ...form, reason: e.target.value })}
              className="mt-1.5 w-full text-sm rounded-lg border border-gray-200 px-3 py-2 bg-white focus:outline-none focus:ring-1 focus:ring-blue-400">
              <option value="">Choose…</option>
              {data.reasons.map((r) => <option key={r.key} value={r.key}>{r.label}</option>)}
            </select>
          </label>
          <label className="block">
            <span className="text-xs font-semibold uppercase tracking-wider text-gray-400">
              Note {form.reason === 'other' ? '(required)' : '(optional)'}
            </span>
            <textarea value={form.note} onChange={(e) => setForm({ ...form, note: e.target.value })} rows={2} maxLength={500}
              placeholder="e.g. nausea for three weeks on the current dose"
              className="mt-1.5 w-full text-sm rounded-lg border border-gray-200 px-3 py-2 focus:outline-none focus:ring-1 focus:ring-blue-400" />
          </label>
          <p className="text-[11px] text-gray-400">
            This records the change for the care team. Prescribe in the hospital&rsquo;s own system; the risk score is not changed.
          </p>
          <div className="flex justify-end gap-2">
            <button type="button" onClick={() => { setForm(null); setError(null); }}
              className="text-sm px-3 py-2 rounded-lg border border-gray-200 text-gray-600 hover:bg-gray-50">Cancel</button>
            <button type="submit" disabled={busy}
              className="inline-flex items-center gap-2 text-sm font-semibold px-4 py-2 rounded-lg text-white disabled:opacity-50"
              style={{ background: 'var(--color-primary)' }}>
              {busy && <Loader2 size={14} className="animate-spin" />} Save change
            </button>
          </div>
        </form>
      )}
      {error && data && <p className="mt-3 rounded-md px-3 py-2 text-sm" style={{ background: '#FFEBEE', color: '#C62828' }}>{error}</p>}
    </div>
  );
}
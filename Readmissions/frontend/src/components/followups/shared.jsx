import React, { useEffect, useState } from 'react';
import { X, Loader2 } from 'lucide-react';
import { finishFollowUp } from '../../api';
import { OUTCOMES } from './format';

// Shared by the patient page's follow-up card and the Follow-ups page.

export function UrgencyPill({ urgency }) {
  const urgent = urgency === 'urgent';
  return (
    <span className={`inline-flex items-center whitespace-nowrap rounded-full px-2 py-0.5 text-[11px] font-semibold ${
      urgent ? 'bg-red-50 text-risk-high' : 'bg-slate-100 text-ns-navy'}`}>
      {urgent ? 'Urgent' : 'Routine'}
    </span>
  );
}

export function StatusPill({ f }) {
  const s = f.status === 'done'
    ? ['bg-green-50 text-risk-low', `Done · ${f.outcome_label}`]
    : f.status === 'in_progress'
      ? ['bg-amber-50 text-amber-800', `In progress · ${f.taken_by?.name}`]
      : ['bg-gray-100 text-gray-600', 'Waiting for a case manager'];
  return (
    <span className={`inline-flex items-center whitespace-nowrap rounded-full px-2 py-0.5 text-[11px] font-semibold ${s[0]}`}>
      {s[1]}
    </span>
  );
}

/** Mark a follow-up done: outcome plus an optional note. */
export function DoneDialog({ followup, onClose, onDone }) {
  const [outcome, setOutcome] = useState(null);
  const [note, setNote] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);

  useEffect(() => {
    const onKey = (e) => e.key === 'Escape' && onClose();
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [onClose]);

  const save = async (e) => {
    e.preventDefault();
    if (!outcome) { setError('Choose what happened.'); return; }
    setBusy(true); setError(null);
    try {
      onDone(await finishFollowUp(followup.id, { outcome, note: note.trim() || undefined }));
    } catch (err) {
      setError(err.message); setBusy(false);
    }
  };

  return (
    <div className="fixed inset-0 z-[60] flex items-center justify-center bg-slate-900/40 p-4" onClick={onClose}>
      <form onSubmit={save} onClick={(e) => e.stopPropagation()} role="dialog" aria-modal="true"
            aria-label="Mark follow-up done" className="w-full max-w-md rounded-xl bg-white shadow-2xl">
        <div className="flex items-start justify-between border-b border-gray-100 px-5 py-4">
          <div>
            <h2 className="text-base font-semibold text-ns-navy">Mark follow-up done</h2>
            <p className="mt-0.5 text-xs text-gray-500">
              Patient {followup.patient_id} · asked by {followup.requested_by?.name}
            </p>
          </div>
          <button type="button" onClick={onClose} aria-label="Close" className="text-gray-400 hover:text-gray-600"><X size={18} /></button>
        </div>
        <div className="space-y-4 px-5 py-4">
          <fieldset className="space-y-2">
            <legend className="mb-2 text-xs font-semibold uppercase tracking-wide text-gray-500">What happened</legend>
            {OUTCOMES.map((o) => (
              <label key={o.key}
                className="flex cursor-pointer items-center gap-3 rounded-lg border px-3 py-2.5 transition-colors"
                style={outcome === o.key ? { borderColor: o.color, background: `${o.color}0D` } : { borderColor: '#E5E7EB' }}>
                <input type="radio" name="outcome" className="sr-only" checked={outcome === o.key} onChange={() => setOutcome(o.key)} />
                <o.icon size={16} style={{ color: o.color }} />
                <span className="flex-1">
                  <span className="block text-sm font-medium text-gray-800">{o.label}</span>
                  <span className="block text-xs text-gray-500">{o.hint}</span>
                </span>
              </label>
            ))}
          </fieldset>
          <label className="block">
            <span className="text-xs font-semibold uppercase tracking-wide text-gray-500">Note for the care team (optional)</span>
            <textarea value={note} onChange={(e) => setNote(e.target.value)} maxLength={500} rows={2}
              placeholder="e.g. Tuesday clinic, 10:30"
              className="mt-1.5 w-full rounded-lg border border-gray-200 px-3 py-2 text-sm focus:outline-none focus:ring-1 focus:ring-ns-navy" />
          </label>
          <p className="text-[11px] text-gray-500">Book the appointment in the hospital&rsquo;s own system first. This only records that it was dealt with.</p>
          {error && <p className="rounded-md bg-red-50 px-3 py-2 text-sm text-risk-high">{error}</p>}
        </div>
        <div className="flex justify-end gap-2 border-t border-gray-100 px-5 py-3">
          <button type="button" onClick={onClose}
            className="rounded-lg border border-gray-200 px-4 py-2 text-sm text-gray-600 hover:bg-gray-50">Cancel</button>
          <button type="submit" disabled={busy}
            className="inline-flex items-center gap-2 rounded-lg bg-ns-navy px-4 py-2 text-sm font-semibold text-white hover:bg-ns-navy/90 disabled:opacity-50">
            {busy && <Loader2 size={15} className="animate-spin" />} Mark done
          </button>
        </div>
      </form>
    </div>
  );
}

import { useEffect, useState } from 'react';
import { X, Loader2 } from 'lucide-react';
import { api } from '../../data/api';
import { OUTCOMES } from './format';

// Shared by the patient page's follow-up card and the Follow-ups page.

export function UrgencyPill({ urgency }) {
  const urgent = urgency === 'urgent';
  return (
    <span className="inline-flex items-center px-2 py-0.5 rounded-full text-[11px] font-semibold whitespace-nowrap"
          style={{ background: urgent ? '#FFEBEE' : '#EBF4FF', color: urgent ? '#C62828' : '#1B4F8A' }}>
      {urgent ? 'Urgent' : 'Routine'}
    </span>
  );
}

export function StatusPill({ f }) {
  const s = f.status === 'done'
    ? { bg: '#E8F5E9', fg: '#2E7D32', text: `Done · ${f.outcome_label}` }
    : f.status === 'in_progress'
      ? { bg: '#FFF3E0', fg: '#EF6C00', text: `In progress · ${f.taken_by?.name}` }
      : { bg: '#F1F5F9', fg: '#475569', text: 'Waiting for a case manager' };
  return (
    <span className="inline-flex items-center px-2 py-0.5 rounded-full text-[11px] font-semibold"
          style={{ background: s.bg, color: s.fg }}>
      {s.text}
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
      onDone(await api.finishFollowUp(followup.id, { outcome, note: note.trim() || undefined }));
    } catch (err) {
      setError(err.message); setBusy(false);
    }
  };

  return (
    <div className="fixed inset-0 z-[60] flex items-center justify-center p-4" style={{ background: 'rgba(15,23,42,0.4)' }}
         onClick={onClose}>
      <form onSubmit={save} onClick={(e) => e.stopPropagation()} role="dialog" aria-modal="true"
            aria-label="Mark follow-up done" className="card w-full max-w-md shadow-2xl" style={{ padding: 0 }}>
        <div className="flex items-start justify-between px-5 py-4 border-b border-gray-100">
          <div>
            <h2 className="text-base font-semibold text-gray-800">Mark follow-up done</h2>
            <p className="text-xs text-gray-400 mt-0.5">
              Patient #{followup.patient_idx} · asked by {followup.requested_by?.name}
            </p>
          </div>
          <button type="button" onClick={onClose} aria-label="Close" className="text-gray-400 hover:text-gray-600"><X size={18} /></button>
        </div>
        <div className="px-5 py-4 space-y-4">
          <fieldset className="space-y-2">
            <legend className="text-xs font-semibold uppercase tracking-wider text-gray-400 mb-2">What happened</legend>
            {OUTCOMES.map((o) => (
              <label key={o.key}
                className="flex items-center gap-3 rounded-lg border px-3 py-2.5 cursor-pointer transition-colors"
                style={outcome === o.key ? { borderColor: o.color, background: `${o.color}0D` } : { borderColor: '#E2E8F0' }}>
                <input type="radio" name="outcome" className="sr-only" checked={outcome === o.key} onChange={() => setOutcome(o.key)} />
                <o.icon size={16} style={{ color: o.color }} />
                <span className="flex-1">
                  <span className="block text-sm font-medium text-gray-800">{o.label}</span>
                  <span className="block text-xs text-gray-400">{o.hint}</span>
                </span>
              </label>
            ))}
          </fieldset>
          <label className="block">
            <span className="text-xs font-semibold uppercase tracking-wider text-gray-400">Note for the care team (optional)</span>
            <textarea value={note} onChange={(e) => setNote(e.target.value)} maxLength={500} rows={2}
              placeholder="e.g. Tuesday clinic, 10:30"
              className="mt-1.5 w-full text-sm rounded-lg border border-gray-200 px-3 py-2 focus:outline-none focus:ring-1 focus:ring-blue-400" />
          </label>
          <p className="text-[11px] text-gray-400">Book the appointment in the hospital&rsquo;s own system first. This only records that it was dealt with.</p>
          {error && <p className="rounded-md px-3 py-2 text-sm" style={{ background: '#FFEBEE', color: '#C62828' }}>{error}</p>}
        </div>
        <div className="flex justify-end gap-2 px-5 py-3 border-t border-gray-100">
          <button type="button" onClick={onClose}
            className="text-sm px-4 py-2 rounded-lg border border-gray-200 text-gray-600 hover:bg-gray-50">Cancel</button>
          <button type="submit" disabled={busy}
            className="inline-flex items-center gap-2 text-sm font-semibold px-4 py-2 rounded-lg text-white disabled:opacity-50"
            style={{ background: 'var(--color-primary)' }}>
            {busy && <Loader2 size={15} className="animate-spin" />} Mark done
          </button>
        </div>
      </form>
    </div>
  );
}
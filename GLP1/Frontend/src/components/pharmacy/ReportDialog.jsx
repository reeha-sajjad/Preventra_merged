import { useEffect, useState } from 'react';
import { X, Loader2, PackageX, FileWarning, Ban, Activity, Wallet, MessageSquare } from 'lucide-react';
import { api } from '../../data/api';

const ICONS = {
  missed_pickup: PackageX, prior_auth: FileWarning, out_of_stock: Ban,
  side_effects: Activity, cost: Wallet, other: MessageSquare,
};

/**
 * A pharmacy reports a refill problem (Backend/core/followups.report). It goes
 * to the patient's case manager as a follow-up - or onto the one already open,
 * so the patient is never phoned twice. The pharmacy sees the status, never
 * what the care team writes.
 */
export default function ReportDialog({ patient, problems, onClose, onDone }) {
  const [problem, setProblem] = useState(null);
  const [urgent, setUrgent] = useState(false);
  const [note, setNote] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);

  useEffect(() => {
    const onKey = (e) => e.key === 'Escape' && onClose();
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [onClose]);

  const send = async (e) => {
    e.preventDefault();
    if (!problem) { setError('Choose what the problem is.'); return; }
    if (problem === 'other' && note.trim().length < 5) { setError('Say briefly what the problem is.'); return; }
    setBusy(true); setError(null);
    try {
      onDone(await api.reportPharmacyProblem(patient.patient_idx,
        { problem, urgency: urgent ? 'urgent' : 'routine', note: note.trim() || undefined }));
    } catch (err) {
      setError(err.message); setBusy(false);
    }
  };

  return (
    <div className="fixed inset-0 z-[60] flex items-center justify-center p-4" style={{ background: 'rgba(15,23,42,0.4)' }}
         onClick={onClose}>
      <form onSubmit={send} onClick={(e) => e.stopPropagation()} role="dialog" aria-modal="true"
            aria-label="Report a refill problem" className="card w-full max-w-md shadow-2xl max-h-[92vh] overflow-y-auto"
            style={{ padding: 0 }}>
        <div className="flex items-start justify-between px-5 py-4 border-b border-gray-100">
          <div>
            <h2 className="text-base font-semibold text-gray-800">Report a refill problem</h2>
            <p className="text-xs text-gray-400 mt-0.5">
              Patient #{patient.patient_idx}
              {patient.case_manager ? ` · goes to ${patient.case_manager}, their case manager` : ' · goes to the hospital’s case managers'}
            </p>
          </div>
          <button type="button" onClick={onClose} aria-label="Close" className="text-gray-400 hover:text-gray-600"><X size={18} /></button>
        </div>
        <div className="px-5 py-4 space-y-4">
          <fieldset className="grid gap-2 sm:grid-cols-2">
            <legend className="text-xs font-semibold uppercase tracking-wider text-gray-400 mb-2">What happened</legend>
            {problems.map((p) => {
              const Icon = ICONS[p.key] || MessageSquare;
              const on = problem === p.key;
              return (
                <label key={p.key}
                  className="flex items-center gap-2.5 rounded-lg border px-3 py-2.5 cursor-pointer transition-colors text-sm"
                  style={on ? { borderColor: 'var(--color-primary)', background: '#EBF4FF' } : { borderColor: '#E2E8F0' }}>
                  <input type="radio" name="problem" className="sr-only" checked={on} onChange={() => setProblem(p.key)} />
                  <Icon size={15} style={{ color: on ? 'var(--color-primary)' : '#94A3B8' }} className="flex-shrink-0" />
                  <span className="text-gray-700 leading-tight">{p.label}</span>
                </label>
              );
            })}
          </fieldset>
          <label className="flex items-center gap-2 text-sm text-gray-700">
            <input type="checkbox" checked={urgent} onChange={(e) => setUrgent(e.target.checked)} className="accent-red-700" />
            Urgent - the patient is out of medicine or close to it
          </label>
          <label className="block">
            <span className="text-xs font-semibold uppercase tracking-wider text-gray-400">
              Note {problem === 'other' ? '(required)' : '(optional)'}
            </span>
            <textarea value={note} onChange={(e) => setNote(e.target.value)} maxLength={500} rows={2}
              placeholder="e.g. Didn't collect on Monday, phone went to voicemail"
              className="mt-1.5 w-full text-sm rounded-lg border border-gray-200 px-3 py-2 focus:outline-none focus:ring-1 focus:ring-blue-400" />
          </label>
          <p className="text-[11px] text-gray-400">
            The case manager contacts the patient and arranges what&rsquo;s needed. You&rsquo;ll see here when someone
            is on it and when it&rsquo;s done.
          </p>
          {error && <p className="rounded-md px-3 py-2 text-sm" style={{ background: '#FFEBEE', color: '#C62828' }}>{error}</p>}
        </div>
        <div className="flex justify-end gap-2 px-5 py-3 border-t border-gray-100">
          <button type="button" onClick={onClose}
            className="text-sm px-4 py-2 rounded-lg border border-gray-200 text-gray-600 hover:bg-gray-50">Cancel</button>
          <button type="submit" disabled={busy}
            className="inline-flex items-center gap-2 text-sm font-semibold px-4 py-2 rounded-lg text-white disabled:opacity-50"
            style={{ background: 'var(--color-primary)' }}>
            {busy && <Loader2 size={15} className="animate-spin" />} Send to the case manager
          </button>
        </div>
      </form>
    </div>
  );
}

/** The status of a follow-up, as a pharmacy sees it. */
export function ReportStatus({ f }) {
  if (!f) return null;
  const s = f.status === 'done'
    ? { bg: '#E8F5E9', fg: '#2E7D32', text: `Done · ${f.outcome_label}` }
    : f.status === 'in_progress'
      ? { bg: '#FFF3E0', fg: '#EF6C00', text: `${f.handled_by} is on it` }
      : { bg: '#F1F5F9', fg: '#475569', text: 'Waiting for a case manager' };
  return (
    <span className="inline-flex items-center px-2 py-0.5 rounded-full text-[11px] font-semibold whitespace-nowrap"
          style={{ background: s.bg, color: s.fg }}>
      {s.text}
    </span>
  );
}
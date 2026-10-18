import React, { useEffect, useState } from 'react';
import { X, Loader2, PackageX, FileWarning, Ban, Activity, Wallet, MessageSquare } from 'lucide-react';
import { reportPharmacyProblem } from '../../api';

const ICONS = {
  missed_pickup: PackageX, prior_auth: FileWarning, out_of_stock: Ban,
  side_effects: Activity, cost: Wallet, other: MessageSquare,
};

/**
 * A pharmacy reports a refill problem (api/followups.report). It goes to the
 * hospital's case managers as a follow-up - or onto the one already open, so
 * the patient is never phoned twice. The same dialog as GLP-1's
 * (GLP1/Frontend/src/components/pharmacy/ReportDialog.jsx).
 */
export default function ReportDialog({ patientId, problems, onClose, onDone }) {
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
      onDone(await reportPharmacyProblem(patientId,
        { problem, urgency: urgent ? 'urgent' : 'routine', note: note.trim() || undefined }));
    } catch (err) {
      setError(err.message); setBusy(false);
    }
  };

  return (
    <div className="fixed inset-0 z-[60] flex items-center justify-center bg-slate-900/40 p-4" onClick={onClose}>
      <form onSubmit={send} onClick={(e) => e.stopPropagation()} role="dialog" aria-modal="true"
            aria-label="Report a refill problem"
            className="max-h-[92vh] w-full max-w-md overflow-y-auto rounded-xl bg-white shadow-2xl">
        <div className="flex items-start justify-between border-b border-gray-100 px-5 py-4">
          <div>
            <h2 className="text-base font-semibold text-gray-800">Report a refill problem</h2>
            <p className="mt-0.5 text-xs text-gray-500">Patient {patientId} · goes to the hospital&rsquo;s case managers</p>
          </div>
          <button type="button" onClick={onClose} aria-label="Close" className="text-gray-400 hover:text-gray-600"><X size={18} /></button>
        </div>
        <div className="space-y-4 px-5 py-4">
          <fieldset className="grid gap-2 sm:grid-cols-2">
            <legend className="mb-2 text-xs font-semibold uppercase tracking-wide text-gray-500">What happened</legend>
            {problems.map((p) => {
              const Icon = ICONS[p.key] || MessageSquare;
              const on = problem === p.key;
              return (
                <label key={p.key}
                  className={`flex cursor-pointer items-center gap-2.5 rounded-lg border px-3 py-2.5 text-sm transition-colors ${
                    on ? 'border-ns-navy bg-ns-navy/5' : 'border-gray-200 hover:bg-gray-50'}`}>
                  <input type="radio" name="problem" className="sr-only" checked={on} onChange={() => setProblem(p.key)} />
                  <Icon size={15} className={`shrink-0 ${on ? 'text-ns-navy' : 'text-gray-400'}`} />
                  <span className="leading-tight text-gray-700">{p.label}</span>
                </label>
              );
            })}
          </fieldset>
          <label className="flex items-center gap-2 text-sm text-gray-700">
            <input type="checkbox" checked={urgent} onChange={(e) => setUrgent(e.target.checked)} className="accent-red-700" />
            Urgent - the patient is out of medicine or close to it
          </label>
          <label className="block">
            <span className="text-xs font-semibold uppercase tracking-wide text-gray-500">
              Note {problem === 'other' ? '(required)' : '(optional)'}
            </span>
            <textarea value={note} onChange={(e) => setNote(e.target.value)} maxLength={500} rows={2}
              placeholder="e.g. Didn't collect on Monday, phone went to voicemail"
              className="mt-1.5 w-full rounded-md border border-gray-300 px-3 py-2 text-sm focus:border-ns-navy focus:outline-none focus:ring-1 focus:ring-ns-navy" />
          </label>
          <p className="text-[11px] text-gray-500">
            A case manager contacts the patient and arranges what&rsquo;s needed. You&rsquo;ll see here when someone is
            on it and when it&rsquo;s done.
          </p>
          {error && <p className="rounded-md border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700">{error}</p>}
        </div>
        <div className="flex justify-end gap-2 border-t border-gray-100 px-5 py-3">
          <button type="button" onClick={onClose}
            className="rounded-md border border-gray-300 px-4 py-2 text-sm text-gray-600 hover:bg-gray-50">Cancel</button>
          <button type="submit" disabled={busy}
            className="inline-flex items-center gap-2 rounded-md bg-ns-navy px-4 py-2 text-sm font-semibold text-white hover:bg-blue-900 disabled:opacity-50">
            {busy && <Loader2 size={15} className="animate-spin" />} Send to the case managers
          </button>
        </div>
      </form>
    </div>
  );
}

/** The status of a follow-up, as a pharmacy sees it. */
export function ReportStatus({ f }) {
  if (!f) return null;
  const [tone, text] = f.status === 'done'
    ? ['bg-green-50 text-risk-low', `Done · ${f.outcome_label}`]
    : f.status === 'in_progress'
      ? ['bg-yellow-50 text-amber-700', `${f.handled_by} is on it`]
      : ['bg-gray-100 text-gray-600', 'Waiting for a case manager'];
  return (
    <span className={`inline-flex items-center whitespace-nowrap rounded-full px-2 py-0.5 text-[11px] font-semibold ${tone}`}>{text}</span>
  );
}
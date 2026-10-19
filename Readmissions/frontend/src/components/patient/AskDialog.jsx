import React, { useEffect, useState } from 'react';
import { X, Loader2 } from 'lucide-react';
import { askCaseManager } from '../../api';

/**
 * A patient asks the hospital's case managers to get in touch
 * (api/followups.patient_request). It becomes a follow-up, or goes onto the one
 * already open, so they're never phoned twice about the same thing. The same
 * dialog as GLP-1's (GLP1/Frontend/src/components/patient/AskDialog.jsx).
 */
export default function AskDialog({ reasons, onClose, onDone }) {
  const [reason, setReason] = useState(null);
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
    if (!reason) { setError('Choose what it’s about.'); return; }
    if (reason === 'other' && note.trim().length < 5) { setError('Say briefly what it’s about.'); return; }
    setBusy(true); setError(null);
    try {
      onDone(await askCaseManager({ reason, note: note.trim() || undefined }));
    } catch (err) {
      setError(err.message); setBusy(false);
    }
  };

  return (
    <div className="fixed inset-0 z-[60] flex items-center justify-center bg-slate-900/40 p-4" onClick={onClose}>
      <form onSubmit={send} onClick={(e) => e.stopPropagation()} role="dialog" aria-modal="true" aria-labelledby="ask-title"
            className="max-h-[92vh] w-full max-w-md overflow-y-auto rounded-xl bg-white shadow-2xl">
        <div className="flex items-start justify-between border-b border-gray-100 px-5 py-4">
          <div>
            <h2 id="ask-title" className="text-base font-semibold text-gray-900">Ask a case manager to get in touch</h2>
            <p className="mt-0.5 text-xs text-gray-500">Your hospital’s case managers will see this.</p>
          </div>
          <button type="button" onClick={onClose} aria-label="Close" className="text-gray-400 hover:text-gray-600"><X size={18} /></button>
        </div>
        <div className="space-y-4 px-5 py-4">
          <fieldset className="space-y-1.5">
            <legend className="mb-2 text-sm font-medium text-gray-700">What’s it about?</legend>
            {reasons.map((r) => {
              const on = reason === r.key;
              return (
                <label key={r.key}
                  className={`flex cursor-pointer items-center gap-3 rounded-lg border px-3 py-2.5 text-sm transition-colors ${
                    on ? 'border-ns-navy bg-blue-50' : 'border-gray-200'}`}>
                  <input type="radio" name="reason" checked={on} onChange={() => setReason(r.key)} className="accent-ns-navy" />
                  <span className="text-gray-700">{r.label}</span>
                </label>
              );
            })}
          </fieldset>
          <label className="block">
            <span className="text-sm font-medium text-gray-700">
              Anything you’d like them to know {reason === 'other' ? '' : <span className="font-normal text-gray-400">(optional)</span>}
            </span>
            <textarea value={note} onChange={(e) => setNote(e.target.value)} maxLength={500} rows={3}
              placeholder="For example, the best time to call you"
              className="mt-1.5 w-full rounded-lg border border-gray-200 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-300" />
          </label>
          <p className="text-xs leading-relaxed text-gray-500">
            This isn’t for emergencies. If you feel very unwell, call 911 or go to the emergency department.
          </p>
          {error && <p role="alert" className="rounded-md bg-red-50 px-3 py-2 text-sm text-red-700">{error}</p>}
        </div>
        <div className="flex justify-end gap-2 border-t border-gray-100 px-5 py-3">
          <button type="button" onClick={onClose}
            className="rounded-lg border border-gray-200 px-4 py-2 text-sm text-gray-600 hover:bg-gray-50">Cancel</button>
          <button type="submit" disabled={busy}
            className="inline-flex items-center gap-2 rounded-lg bg-ns-navy px-4 py-2 text-sm font-semibold text-white disabled:opacity-50">
            {busy && <Loader2 size={15} className="animate-spin" />} Send to the case managers
          </button>
        </div>
      </form>
    </div>
  );
}
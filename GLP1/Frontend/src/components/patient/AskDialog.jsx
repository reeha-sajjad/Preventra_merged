import { useEffect, useState } from 'react';
import { X, Loader2 } from 'lucide-react';
import { api } from '../../data/api';

/**
 * A patient asks their case manager to get in touch (Backend/core/followups.
 * patient_request). It goes to the case manager as a follow-up, or onto the one
 * already open, so they're never phoned twice about the same thing.
 */
export default function AskDialog({ reasons, caseManager, onClose, onDone }) {
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
      onDone(await api.askCaseManager({ reason, note: note.trim() || undefined }));
    } catch (err) {
      setError(err.message); setBusy(false);
    }
  };

  return (
    <div className="fixed inset-0 z-[60] flex items-center justify-center p-4" style={{ background: 'rgba(15,23,42,0.4)' }}
         onClick={onClose}>
      <form onSubmit={send} onClick={(e) => e.stopPropagation()} role="dialog" aria-modal="true"
            aria-labelledby="ask-title" className="card w-full max-w-md shadow-2xl max-h-[92vh] overflow-y-auto"
            style={{ padding: 0 }}>
        <div className="flex items-start justify-between px-5 py-4 border-b border-gray-100">
          <div>
            <h2 id="ask-title" className="text-base font-semibold text-gray-800">Ask your case manager to get in touch</h2>
            <p className="text-xs text-gray-500 mt-0.5">
              {caseManager ? `${caseManager} will see this.` : 'Your hospital’s case managers will see this.'}
            </p>
          </div>
          <button type="button" onClick={onClose} aria-label="Close" className="text-gray-400 hover:text-gray-600"><X size={18} /></button>
        </div>
        <div className="px-5 py-4 space-y-4">
          <fieldset className="space-y-1.5">
            <legend className="text-sm font-medium text-gray-700 mb-2">What’s it about?</legend>
            {reasons.map((r) => {
              const on = reason === r.key;
              return (
                <label key={r.key}
                  className="flex items-center gap-3 rounded-lg border px-3 py-2.5 cursor-pointer text-sm transition-colors"
                  style={on ? { borderColor: 'var(--color-primary)', background: '#EBF4FF' } : { borderColor: '#E2E8F0' }}>
                  <input type="radio" name="reason" checked={on} onChange={() => setReason(r.key)} className="accent-[#1B4F8A]" />
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
              className="mt-1.5 w-full text-sm rounded-lg border border-gray-200 px-3 py-2 focus:outline-none focus:ring-2 focus:ring-blue-300" />
          </label>
          <p className="text-xs text-gray-500 leading-relaxed">
            This isn’t for emergencies. If you feel very unwell, call 911 or go to the emergency department.
          </p>
          {error && <p role="alert" className="rounded-md px-3 py-2 text-sm" style={{ background: '#FFEBEE', color: '#C62828' }}>{error}</p>}
        </div>
        <div className="flex justify-end gap-2 px-5 py-3 border-t border-gray-100">
          <button type="button" onClick={onClose}
            className="text-sm px-4 py-2 rounded-lg border border-gray-200 text-gray-600 hover:bg-gray-50">Cancel</button>
          <button type="submit" disabled={busy}
            className="inline-flex items-center gap-2 text-sm font-semibold px-4 py-2 rounded-lg text-white disabled:opacity-50"
            style={{ background: 'var(--color-primary)' }}>
            {busy && <Loader2 size={15} className="animate-spin" />} Send to my case manager
          </button>
        </div>
      </form>
    </div>
  );
}
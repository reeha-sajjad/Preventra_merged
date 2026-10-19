import { useState } from 'react';
import { Lock, Loader2 } from 'lucide-react';
import { api } from '../../data/api';

/**
 * Hospital admins and insurers see a patient's overview first. This opens the
 * clinical details - vitals, drug, pharmacy, risk drivers - for one patient,
 * after they say why. The backend writes the reason to the access log and
 * refuses the details without it; this is only the form.
 *
 * A pharmacy (overviewOnly) gets a note instead: no reason opens the clinical
 * layer for a pharmacy, and the backend refuses it (Backend/core/access.py).
 */
export default function ClinicalGate({ patientIdx, reasons = [], onOpened, overviewOnly = false, insurer = false }) {
  const [reason, setReason] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);

  const submit = async (e) => {
    e.preventDefault();
    if (!reason) return;
    setBusy(true);
    setError(null);
    try {
      await api.openPatient(patientIdx, reason);
      onOpened?.();
    } catch (err) {
      setError(err.message);
      setBusy(false);
    }
  };

  if (overviewOnly) {
    return (
      <div className="card p-6 flex items-start gap-3 animate-fade-up">
        <div className="w-9 h-9 rounded-xl flex items-center justify-center flex-shrink-0"
             style={{ background: '#EBF4FF', color: 'var(--color-primary)' }}>
          <Lock size={16} />
        </div>
        <div>
          <h2 className="text-base font-semibold text-gray-800" style={{ fontFamily: 'DM Serif Display, serif' }}>
            Clinical details are not shared with pharmacies
          </h2>
          <p className="text-sm text-gray-500 mt-0.5">
            You see this patient&rsquo;s risk, care team and case manager. Vitals and dropout drivers stay
            with the hospital&rsquo;s care team.
          </p>
        </div>
      </div>
    );
  }

  return (
    <form onSubmit={submit} className="card p-6 space-y-4 animate-fade-up">
      <div className="flex items-start gap-3">
        <div className="w-9 h-9 rounded-xl flex items-center justify-center flex-shrink-0"
             style={{ background: '#EBF4FF', color: 'var(--color-primary)' }}>
          <Lock size={16} />
        </div>
        <div>
          <h2 className="text-base font-semibold text-gray-800" style={{ fontFamily: 'DM Serif Display, serif' }}>
            Clinical details
          </h2>
          <p className="text-sm text-gray-500 mt-0.5">
            Vitals, medication, pharmacy and dropout drivers open one patient at a time. Tell us why you
            need them; the reason is recorded in {insurer ? 'the hospital' : 'your hospital'}&apos;s access log.
          </p>
        </div>
      </div>
      <fieldset className="grid gap-2 sm:grid-cols-2">
        <legend className="sr-only">Reason</legend>
        {reasons.map((r) => (
          <label key={r.key}
            className="flex items-center gap-2 rounded-lg border px-3 py-2 text-sm cursor-pointer transition-colors"
            style={{
              borderColor: reason === r.key ? 'var(--color-primary)' : '#E2E8F0',
              background: reason === r.key ? '#EBF4FF' : 'white',
              color: reason === r.key ? 'var(--color-primary)' : '#4A5568',
            }}>
            <input type="radio" name="reason" value={r.key} checked={reason === r.key}
              onChange={() => setReason(r.key)} />
            {r.label}
          </label>
        ))}
      </fieldset>
      {error && <p className="text-sm" style={{ color: '#C62828' }}>{error}</p>}
      <button type="submit" disabled={!reason || busy}
        className="inline-flex items-center gap-2 rounded-lg px-4 py-2 text-sm font-semibold text-white disabled:opacity-50"
        style={{ background: 'var(--color-primary)' }}>
        {busy && <Loader2 size={15} className="animate-spin" />}
        View clinical details
      </button>
    </form>
  );
}
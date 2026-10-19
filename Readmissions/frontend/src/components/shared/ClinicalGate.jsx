import React, { useState } from 'react';
import { Lock, Loader2 } from 'lucide-react';
import { openPatient } from '../../api';

/**
 * Hospital admins and insurers see a patient's overview first. This opens the
 * clinical details - diagnoses, risk drivers, notes, alerts - for one patient,
 * after they say why. The backend writes the reason to the access log and
 * refuses the details without it; this is only the form.
 *
 * A pharmacy (overviewOnly) gets a note instead: no reason opens the clinical
 * layer for a pharmacy, and the backend refuses it (api/access.py).
 */
export default function ClinicalGate({ patientId, reasons = [], onOpened, compact = false, overviewOnly = false, insurer = false }) {
  const [reason, setReason] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);

  const submit = async (e) => {
    e.preventDefault();
    if (!reason) return;
    setBusy(true);
    setError(null);
    try {
      await openPatient(patientId, reason);
      onOpened?.();
    } catch (err) {
      setError(err.message);
      setBusy(false);
    }
  };

  if (overviewOnly) {
    return (
      <div className={`bg-white rounded-xl border border-gray-200 shadow-sm ${compact ? 'p-4' : 'p-6'} flex items-start gap-3`}>
        <div className="rounded-full bg-ns-navy/5 p-2 text-ns-navy"><Lock size={18} /></div>
        <div>
          <h3 className="font-semibold text-ns-navy">Clinical details are not shared with pharmacies</h3>
          <p className="text-sm text-gray-600 mt-0.5">
            You see this patient&apos;s risk, care team and refills. Diagnoses, risk drivers and
            notes stay with the hospital&apos;s care team.
          </p>
        </div>
      </div>
    );
  }

  return (
    <form onSubmit={submit}
      className={`bg-white rounded-xl border border-gray-200 shadow-sm ${compact ? 'p-4' : 'p-6'} space-y-4`}>
      <div className="flex items-start gap-3">
        <div className="rounded-full bg-ns-navy/5 p-2 text-ns-navy"><Lock size={18} /></div>
        <div>
          <h3 className="font-semibold text-ns-navy">Clinical details</h3>
          <p className="text-sm text-gray-600 mt-0.5">
            Diagnoses, risk drivers, notes and alerts open one patient at a time. Tell us why
            you need them; the reason is recorded in {insurer ? 'the hospital' : 'your hospital'}&apos;s access log.
          </p>
        </div>
      </div>
      <fieldset className="grid gap-2 sm:grid-cols-2">
        <legend className="sr-only">Reason</legend>
        {reasons.map((r) => (
          <label key={r.key}
            className={`flex cursor-pointer items-center gap-2 rounded-lg border px-3 py-2 text-sm transition-colors ${
              reason === r.key ? 'border-ns-navy bg-ns-navy/5 text-ns-navy' : 'border-gray-200 text-gray-700 hover:bg-gray-50'
            }`}>
            <input type="radio" name="reason" value={r.key} checked={reason === r.key}
              onChange={() => setReason(r.key)} className="accent-ns-navy" />
            {r.label}
          </label>
        ))}
      </fieldset>
      {error && <p className="text-sm text-red-700">{error}</p>}
      <button type="submit" disabled={!reason || busy}
        className="inline-flex items-center gap-2 rounded-lg bg-ns-navy px-4 py-2 text-sm font-semibold text-white hover:bg-blue-900 disabled:cursor-not-allowed disabled:opacity-60">
        {busy && <Loader2 size={16} className="animate-spin" />}
        View clinical details
      </button>
    </form>
  );
}
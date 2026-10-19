import React, { useCallback, useEffect, useState } from 'react';
import { UsersRound, X, Loader2, UserPlus } from 'lucide-react';
import { getCaretakers, linkCaretaker, unlinkCaretaker } from '../../api';

/**
 * The family or home carers linked to a patient (api/caretakers.py).
 *
 *  - On the patient's own page (no patientId): "People who help with your
 *    care" - the patient adds someone by email and can remove anyone.
 *  - On the staff patient page (patientId given): a hospital admin or case
 *    manager links a caretaker, confirming the patient agreed.
 *
 * Only an existing caretaker account can be added. GLP-1's twin
 * (GLP1/Frontend/src/components/patient/CaretakersCard.jsx).
 */
export default function CaretakersCard({ patientId = null }) {
  const staff = patientId != null;
  const [data, setData] = useState(null);
  const [adding, setAdding] = useState(false);
  const [email, setEmail] = useState('');
  const [agreed, setAgreed] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);

  const load = useCallback(() => {
    getCaretakers(patientId).then(setData).catch((e) => setError(e.message));
  }, [patientId]);
  useEffect(() => { load(); }, [load]);
  if (!data) return null;

  const add = async (e) => {
    e.preventDefault();
    setBusy(true); setError(null);
    try {
      setData(await linkCaretaker({ email: email.trim(), ...(staff ? { patient_id: patientId, patient_agreed: agreed } : {}) }));
      setAdding(false); setEmail(''); setAgreed(false);
    } catch (err) { setError(err.message); }
    finally { setBusy(false); }
  };
  const remove = async (c) => {
    if (!window.confirm(`Remove ${c.name}? They'll stop seeing ${staff ? 'this patient’s' : 'your'} summary straight away.`)) return;
    setError(null);
    try { setData(await unlinkCaretaker(c.id, patientId)); } catch (err) { setError(err.message); }
  };

  return (
    <section className="rounded-xl border border-gray-200 bg-white p-6 shadow-sm" aria-labelledby="carers-title">
      <h2 id="carers-title" className="flex items-center gap-2 text-base font-semibold text-gray-900">
        <UsersRound size={17} className="text-gray-400" /> {staff ? 'Caretakers' : 'People who help with your care'}
      </h2>
      <p className="mt-1 text-xs text-gray-500">
        {staff ? 'Family or home carers who can see this patient’s plain-words summary.'
               : 'Family or carers you add can see this page and ask a case manager for help. You can remove them any time.'}
      </p>
      {data.caretakers.length > 0 ? (
        <ul className="mt-4 space-y-2">
          {data.caretakers.map((c) => (
            <li key={c.id} className="flex items-center gap-3 rounded-lg border border-gray-100 px-3 py-2.5">
              <div className="min-w-0 flex-1">
                <div className="truncate text-sm font-medium text-gray-800">{c.name}</div>
                <div className="truncate text-xs text-gray-500">
                  {c.email}{c.added?.by?.role && c.added.by.role !== 'patient' ? ` · added by ${c.added.by.name}` : ''}
                </div>
              </div>
              <button type="button" onClick={() => remove(c)} aria-label={`Remove ${c.name}`}
                className="text-gray-400 hover:text-gray-700"><X size={16} /></button>
            </li>
          ))}
        </ul>
      ) : (
        <p className="mt-4 text-sm text-gray-500">{staff ? 'No caretakers linked.' : 'You haven’t added anyone.'}</p>
      )}
      {adding ? (
        <form onSubmit={add} className="mt-4 space-y-3 rounded-xl border border-gray-200 p-4">
          <label className="block text-sm text-gray-700">Their email
            <input type="email" required value={email} onChange={(e) => setEmail(e.target.value)} autoComplete="off"
              className="mt-1 w-full rounded-lg border border-gray-200 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-300" />
          </label>
          <p className="text-xs text-gray-500">They need to have signed up as a caretaker with this email first.</p>
          {staff && (
            <label className="flex items-start gap-2 text-sm text-gray-700">
              <input type="checkbox" checked={agreed} onChange={(e) => setAgreed(e.target.checked)} className="mt-0.5" required />
              The patient has agreed to this person seeing their summary.
            </label>
          )}
          {error && <p role="alert" className="text-sm text-red-700">{error}</p>}
          <div className="flex justify-end gap-2">
            <button type="button" onClick={() => { setAdding(false); setError(null); }}
              className="rounded-lg border border-gray-200 px-3.5 py-2 text-sm text-gray-600 hover:bg-gray-50">Cancel</button>
            <button type="submit" disabled={busy}
              className="inline-flex items-center gap-2 rounded-lg bg-ns-navy px-3.5 py-2 text-sm font-semibold text-white disabled:opacity-50">
              {busy && <Loader2 size={14} className="animate-spin" />} Add caretaker
            </button>
          </div>
        </form>
      ) : (
        <>
          {error && <p role="alert" className="mt-3 text-sm text-red-700">{error}</p>}
          {data.can_add && (
            <button type="button" onClick={() => setAdding(true)}
              className="mt-4 inline-flex items-center gap-1.5 rounded-lg border border-gray-200 px-3.5 py-2 text-sm font-medium text-gray-700 hover:bg-gray-50">
              <UserPlus size={14} /> Add a caretaker
            </button>
          )}
        </>
      )}
    </section>
  );
}
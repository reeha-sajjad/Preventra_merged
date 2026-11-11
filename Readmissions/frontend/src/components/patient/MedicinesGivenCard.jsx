import React, { useCallback, useEffect, useState } from 'react';
import { Pill, Check, Loader2, Undo2 } from 'lucide-react';
import { getMedicinesGiven, recordMedicinesGiven, undoMedicinesGiven, getPatientMedicinesGiven } from '../../api';
import { myRole } from '../../roles';

const when = (iso) => new Date(iso).toLocaleString(undefined, { weekday: 'short', day: 'numeric', month: 'short',
                                                                hour: 'numeric', minute: '2-digit' });

/**
 * The daily medicines tick (api/medicines_given.py), with an optional note.
 *  - mode "home": on the patient's home page. A caretaker ticks "Today's
 *    medicines given" (patientId picks the patient). A patient with no
 *    caretaker ticks "Today's medicines taken"; one with a caretaker sees the
 *    caretaker's last tick.
 *  - mode "staff": on the staff patient page, the log, shown only once someone
 *    has recorded something, each entry saying whether the patient or their
 *    caretaker logged it.
 * What was logged, not proof the medicines were taken.
 */
const MAX_NOTE = 120;           // api/medicines_given.MAX_NOTE
export default function MedicinesGivenCard({ patientId = null, mode = 'home' }) {
  const staff = mode === 'staff';
  const [data, setData] = useState(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const [note, setNote] = useState('');
  const isPatient = myRole() === 'patient';

  const load = useCallback(() => {
    (staff ? getPatientMedicinesGiven(patientId) : getMedicinesGiven(patientId)).then(setData).catch(() => setData(null));
  }, [patientId, staff]);
  useEffect(() => { load(); }, [load]);
  if (!data) return null;
  if (staff && data.entries.length === 0) return null;
  if (!staff && !data.can_tick && data.entries.length === 0) return null;   // nobody has ticked yet and this viewer can't

  const last = data.entries[0];
  const tick = async () => {
    setBusy(true); setError(null);
    try { setData(await recordMedicinesGiven(patientId, note.trim())); setNote(''); } catch (e) { setError(e.message); }
    finally { setBusy(false); }
  };
  const undo = async () => {
    setError(null);
    try { setData(await undoMedicinesGiven(last.id, patientId)); } catch (e) { setError(e.message); }
  };

  return (
    <section className="rounded-xl border border-gray-200 bg-white p-6 shadow-sm">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h2 className="flex items-center gap-2 text-base font-semibold text-gray-900">
            <Pill size={17} className="text-gray-400" />
            {staff ? 'Medicines logged' : isPatient && data.can_tick ? 'Medicines taken' : 'Medicines given'}
          </h2>
          <p className="mt-0.5 text-xs text-gray-500">
            {staff ? 'Logged daily by the patient or their caretaker, last 8 weeks.'
              : isPatient && data.can_tick ? 'Tick once you’ve taken today’s medicines.'
              : isPatient ? 'Your caretaker ticks this once the day’s medicines are given.'
              : 'Tick once the day’s medicines are given.'}
          </p>
        </div>
        <div className="text-right">
          <div className="text-2xl font-semibold text-gray-900">
            {data.last_4_weeks} <span className="text-sm font-normal text-gray-400">of {data.expected_4_weeks}</span>
          </div>
          <div className="text-[11px] text-gray-400">days in the last 4 weeks</div>
        </div>
      </div>
      {staff ? (
        <ul className="mt-4 divide-y divide-gray-100">
          {data.entries.slice(0, 10).map((e) => (
            <li key={e.id} className="flex flex-wrap items-baseline justify-between gap-2 py-2 text-sm">
              <span className="text-gray-800">{when(e.given_at)}</span>
              <span className="text-xs text-gray-500">
                {e.logged_by} ({e.by_role === 'patient' ? 'patient' : 'caretaker'}){e.note ? ` · ${e.note}` : ''}
              </span>
            </li>
          ))}
        </ul>
      ) : (
        <div className="mt-4 flex flex-wrap items-center gap-3">
          <p className="min-w-[180px] flex-1 text-sm text-gray-700">
            {last ? <>Last recorded {when(last.given_at)} by {isPatient && last.by_role === 'patient' ? 'you' : last.logged_by}.</>
              : 'Nothing recorded yet.'}
            {last?.can_undo && (
              <button type="button" onClick={undo}
                className="ml-2 inline-flex items-center gap-1 text-xs font-medium text-gray-500 hover:text-gray-800 hover:underline">
                <Undo2 size={11} /> Undo
              </button>
            )}
          </p>
          {data.can_tick && data.ticked_today && (
            <span className="inline-flex items-center gap-1.5 text-sm font-medium text-green-800">
              <Check size={14} /> Done for today
            </span>
          )}
          {data.can_tick && !data.ticked_today && (
            <div className="flex w-full flex-wrap items-center gap-2 sm:w-auto">
              <input value={note} onChange={(e) => setNote(e.target.value)} maxLength={MAX_NOTE}
                placeholder="Add a note (optional)" aria-label="Note for the care team"
                className="min-w-0 flex-1 rounded-lg border border-gray-300 px-3 py-2 text-sm sm:w-56" />
              <button type="button" onClick={tick} disabled={busy}
                className="inline-flex items-center gap-1.5 rounded-lg bg-green-800 px-3.5 py-2 text-sm font-semibold text-white disabled:opacity-50">
                {busy ? <Loader2 size={14} className="animate-spin" /> : <Check size={14} />}
                {isPatient ? 'Today’s medicines taken' : 'Today’s medicines given'}
              </button>
            </div>
          )}
        </div>
      )}
      {error && <p role="alert" className="mt-3 text-sm text-red-700">{error}</p>}
    </section>
  );
}
import React, { useCallback, useEffect, useState } from 'react';
import { Flag, Loader2 } from 'lucide-react';
import { getPatientOutcome, recordPatientOutcome, clearPatientOutcome } from '../../api';

const day = (iso) => (iso
  ? new Date(iso.length === 10 ? `${iso}T00:00:00` : iso).toLocaleDateString(undefined, { day: 'numeric', month: 'short', year: 'numeric' })
  : '');

/**
 * The 30-day outcome of this discharge (api/outcomes.py): readmitted or not,
 * from the hospital's own records when it came in by ingestion, or recorded
 * here by the care team - who may know about a readmission elsewhere. This is
 * what the Outcomes page compares the risk score against.
 */
export default function OutcomeCard({ patientId }) {
  const [data, setData] = useState(null);
  const [mode, setMode] = useState(null);          // null | 'readmitted'
  const [date, setDate] = useState('');
  const [note, setNote] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);

  const load = useCallback(() => {
    getPatientOutcome(patientId).then(setData).catch(() => setData(null));
  }, [patientId]);
  useEffect(() => { load(); }, [load]);
  if (!data) return null;

  const run = async (fn) => {
    setBusy(true); setError(null);
    try { setData(await fn()); setMode(null); setDate(''); setNote(''); }
    catch (e) { setError(e.message); }
    finally { setBusy(false); }
  };
  const rec = data.recorded;
  const records = data.from_records;

  return (
    <section className="rounded-xl border border-gray-200 bg-white p-6 shadow-sm" aria-labelledby="outcome-title">
      <h2 id="outcome-title" className="flex items-center gap-2 text-base font-semibold text-gray-900">
        <Flag size={17} className="text-gray-400" /> 30-day outcome
      </h2>
      <p className="mt-0.5 text-xs text-gray-500">
        Discharged {day(data.discharge_date)} · the 30 days {data.window_closed ? 'ended' : 'end'} {day(data.window_ends)}.
      </p>

      {rec ? (
        <div className={`mt-4 rounded-lg border p-3 text-sm ${rec.readmitted ? 'border-red-200 bg-red-50 text-red-900' : 'border-green-200 bg-green-50 text-green-900'}`}>
          <div className="font-semibold">
            {rec.readmitted ? `Readmitted on ${day(rec.readmit_date)}` : 'Not readmitted within 30 days'}
          </div>
          {rec.note && <div className="mt-1">{rec.note}</div>}
          <div className="mt-1 text-xs opacity-80">Recorded by {rec.by} · {day(rec.at)}</div>
        </div>
      ) : records ? null : (
        <p className="mt-3 text-sm text-gray-600">Not known yet.</p>
      )}

      {records && (
        <p className="mt-3 text-sm text-gray-700">
          <span className="text-gray-500">Hospital records: </span>
          {records.readmitted ? 'readmitted within 30 days' : 'no readmission within 30 days'}
          {rec && rec.readmitted !== records.readmitted && ' - the care team’s record above is the one counted'}
        </p>
      )}

      {data.can_record && mode === null && (
        <div className="mt-4 flex flex-wrap items-center gap-3">
          <button type="button" onClick={() => setMode('readmitted')}
            className="rounded-lg border border-gray-300 px-3 py-2 text-sm font-semibold text-gray-800 hover:border-gray-400">
            {rec?.readmitted ? 'Change readmission date' : 'Record a readmission'}
          </button>
          {rec?.readmitted !== false && (data.window_closed ? (
            <button type="button" disabled={busy}
              onClick={() => run(() => recordPatientOutcome(patientId, { readmitted: false }))}
              className="rounded-lg border border-gray-300 px-3 py-2 text-sm font-semibold text-gray-800 hover:border-gray-400 disabled:opacity-50">
              Not readmitted
            </button>
          ) : (
            <span className="text-xs text-gray-500">“Not readmitted” can be recorded after {day(data.window_ends)}.</span>
          ))}
          {rec && (
            <button type="button" disabled={busy} onClick={() => run(() => clearPatientOutcome(patientId))}
              className="text-sm font-medium text-gray-600 hover:text-gray-900 hover:underline">Remove</button>
          )}
        </div>
      )}

      {mode === 'readmitted' && (
        <form className="mt-4 space-y-3"
          onSubmit={(e) => { e.preventDefault(); run(() => recordPatientOutcome(patientId, { readmitted: true, readmit_date: date, note })); }}>
          <div>
            <label htmlFor="readmit-date" className="text-sm font-medium text-gray-900">Date readmitted</label>
            <input id="readmit-date" type="date" required value={date} onChange={(e) => setDate(e.target.value)}
              min={data.discharge_date} max={data.window_ends}
              className="mt-1 block rounded-lg border border-gray-300 px-3 py-2 text-sm focus:border-ns-navy focus:outline-none focus:ring-2 focus:ring-blue-200" />
          </div>
          <div>
            <label htmlFor="readmit-note" className="text-sm font-medium text-gray-900">Note <span className="font-normal text-gray-500">(optional)</span></label>
            <input id="readmit-note" type="text" maxLength={300} value={note} onChange={(e) => setNote(e.target.value)}
              placeholder="e.g. admitted to another hospital, per the family"
              className="mt-1 block w-full rounded-lg border border-gray-300 px-3 py-2 text-sm focus:border-ns-navy focus:outline-none focus:ring-2 focus:ring-blue-200" />
          </div>
          <div className="flex items-center gap-3">
            <button type="submit" disabled={busy || !date}
              className="inline-flex items-center gap-2 rounded-lg bg-ns-navy px-4 py-2 text-sm font-semibold text-white disabled:opacity-50">
              {busy && <Loader2 size={14} className="animate-spin" />} Save
            </button>
            <button type="button" onClick={() => { setMode(null); setError(null); }}
              className="text-sm font-medium text-gray-600 hover:text-gray-900">Cancel</button>
          </div>
        </form>
      )}
      {error && <p role="alert" className="mt-3 text-sm text-red-700">{error}</p>}
    </section>
  );
}

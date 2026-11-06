import { useEffect, useState } from 'react';
import { HeartHandshake } from 'lucide-react';
import { api } from '../../data/api';

// "YYYY-MM-DD" as a local date, so a US time zone does not show the day before.
function day(iso) {
  const [y, m, d] = iso.split('-').map(Number);
  return new Date(y, m - 1, d).toLocaleDateString(undefined, { day: 'numeric', month: 'short', year: 'numeric' });
}

/**
 * On the staff patient page: the patient's own mood answers (Backend/core/mood.py)
 * as totals, bands, change and flags - not item by item. Only doctors, nurses and
 * case managers get it (the server refuses everyone else, and the card then
 * hides). A screening result, not a diagnosis; it never touches the risk score.
 */
export default function MoodScreenCard({ patientIdx }) {
  const [data, setData] = useState(null);
  useEffect(() => { api.getPatientMood(patientIdx).then(setData).catch(() => setData(null)); }, [patientIdx]);
  if (!data) return null;

  const [latest, ...earlier] = data.screens;
  return (
    <div className="card p-6 mt-6">
      <h2 className="text-base font-semibold text-gray-800 flex items-center gap-2" style={{ fontFamily: 'DM Serif Display, serif' }}>
        <HeartHandshake size={16} className="text-gray-400" /> Mood and anxiety
      </h2>
      <p className="text-xs text-gray-500 mt-0.5">
        The patient’s own answers, every 2 weeks. Screening answers, not a diagnosis; they do not change the risk score.
      </p>

      {!latest ? (
        <p className="mt-4 text-sm text-gray-600">The patient hasn’t answered the mood questions yet.</p>
      ) : (
        <div className="mt-4">
          <div className="flex flex-wrap items-center gap-2 text-sm text-gray-700">
            <span className="font-semibold">Answered {day(latest.answered_on)}</span>
            {latest.simulated && (
              <span className="text-[11px] font-semibold px-2 py-0.5 rounded bg-gray-100 text-gray-700">Demo data (simulated)</span>
            )}
          </div>
          <dl className="mt-3 space-y-1.5 text-sm">
            {latest.lines.map((l) => (
              <div key={l.key} className="flex flex-wrap gap-x-3">
                <dt className="w-36 text-gray-600">{l.label}</dt>
                <dd className={l.positive ? 'font-semibold text-amber-900' : 'text-gray-800'}>{l.text}</dd>
              </div>
            ))}
          </dl>
          {latest.flags.length > 0 && (
            <ul className="mt-3 space-y-2">
              {latest.flags.map((f) => (
                <li key={f.code} className="rounded-md bg-amber-50 border border-amber-200 px-2.5 py-1.5 text-xs text-amber-900">
                  <b>{f.title}.</b> {f.detail} {f.rationale}
                </li>
              ))}
            </ul>
          )}
        </div>
      )}

      {earlier.length > 0 && (
        <ul className="mt-4 divide-y divide-gray-100 border-t border-gray-100">
          {earlier.map((s) => (
            <li key={s.id} className="py-2 flex flex-wrap items-baseline justify-between gap-2 text-sm">
              <span className="text-gray-800">{day(s.answered_on)}{s.simulated ? ' (simulated)' : ''}</span>
              <span className="text-xs text-gray-600">{s.lines[0]?.text ?? 'not answered'}</span>
            </li>
          ))}
        </ul>
      )}

      <p className="mt-4 text-xs text-gray-500">
        {data.due ? 'Next questions due now.' : `Next questions due ${day(data.next_due)}.`}
      </p>
    </div>
  );
}

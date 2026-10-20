import React, { useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { ChevronRight, Pill, AlertTriangle, CalendarCheck, PackageX, Users, Loader2 } from 'lucide-react';
import { getMyPeople } from '../api';
import { ago } from '../components/followups/format';

const BAND = {
  High:   { text: 'text-red-700', pill: 'bg-red-50 text-red-700' },
  Medium: { text: 'text-amber-700', pill: 'bg-amber-50 text-amber-800' },
  Low:    { text: 'text-green-700', pill: 'bg-green-50 text-green-800' },
};
const ALERT = {
  refill_problem: { icon: PackageX, cls: 'text-red-700' },
  dose_due:       { icon: Pill, cls: 'text-amber-700' },
  booked:         { icon: CalendarCheck, cls: 'text-green-700' },
};

/**
 * A caretaker's home (api/caretaker_home.py): everyone who linked them, most
 * in need first - their readmission risk, why they were in hospital, when the
 * medicines were last logged, and anything that needs the carer. Opening a
 * person shows their full summary (PatientHome). GLP-1 has the same page.
 */
export default function MyPeople() {
  const navigate = useNavigate();
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);
  useEffect(() => { getMyPeople().then(setData).catch((e) => setError(e.message)); }, []);

  if (!data) {
    return (
      <div className="flex justify-center py-16 text-sm text-gray-500">
        {error ? <span className="text-red-700">{error}</span> : <Loader2 className="animate-spin" />}
      </div>
    );
  }
  if (data.people.length === 0) {
    return (
      <div className="mx-auto my-8 max-w-xl rounded-xl border border-gray-200 bg-white p-8 text-center">
        <Users size={22} className="mx-auto text-gray-300" />
        <p className="mt-3 text-sm leading-relaxed text-gray-600">
          Nobody has added you as their caretaker yet. Ask the person you care for to add you from their own
          page, using the email you signed up with - or ask their hospital to, with their agreement.
        </p>
      </div>
    );
  }

  return (
    <div className="mx-auto max-w-[1080px] space-y-5 py-6">
      <div>
        <h1 className="text-xl font-bold text-gray-900">People you care for</h1>
        <p className="mt-0.5 text-sm text-gray-500">Anyone who needs you today is at the top.</p>
      </div>
      <div className="grid gap-4 md:grid-cols-2">
        {data.people.map((p) => {
          const band = BAND[p.band] || { text: 'text-gray-800', pill: 'bg-gray-100 text-gray-700' };
          return (
            <button key={p.patient_id} type="button" onClick={() => navigate(`/person/${encodeURIComponent(p.patient_id)}`)}
              className="group rounded-xl border border-gray-200 bg-white p-5 text-left shadow-sm transition-shadow hover:shadow-md">
              <div className="flex items-start justify-between gap-3">
                <div className="min-w-0">
                  <div className="truncate text-base font-semibold text-gray-900">{p.name}</div>
                  <div className="mt-0.5 text-xs text-gray-500">{p.status}</div>
                </div>
                <div className="shrink-0 text-right">
                  <div className={`text-xl font-semibold leading-none ${band.text}`}>{p.risk_score}%</div>
                  {p.band && <div className={`mt-1 inline-block rounded-full px-2 py-0.5 text-[11px] font-semibold ${band.pill}`}>{p.band} risk</div>}
                </div>
              </div>

              {p.alerts.length > 0 && (
                <ul className="mt-3 space-y-1">
                  {p.alerts.map((a) => {
                    const k = ALERT[a.kind] || { icon: AlertTriangle, cls: 'text-gray-600' };
                    return (
                      <li key={a.kind} className={`flex items-center gap-1.5 text-xs font-medium ${k.cls}`}>
                        <k.icon size={12} /> {a.text}
                      </li>
                    );
                  })}
                </ul>
              )}

              <dl className="mt-4 grid grid-cols-3 gap-3 text-xs">
                <div>
                  <dt className="text-gray-400">In hospital for</dt>
                  <dd className="mt-0.5 text-gray-700">{p.reason || '—'}</dd>
                </div>
                <div>
                  <dt className="text-gray-400">Went home</dt>
                  <dd className="mt-0.5 text-gray-700">{p.discharged || '—'}</dd>
                </div>
                <div>
                  <dt className="text-gray-400">Medicines last logged</dt>
                  <dd className="mt-0.5 text-gray-700">
                    {p.logged_today ? 'Today' : p.last_given ? `${ago(p.last_given.at)} · ${p.last_given.by}` : 'Not yet'}
                  </dd>
                </div>
              </dl>
              <div className="mt-3 flex items-center justify-end text-xs font-semibold text-ns-navy">
                Open summary <ChevronRight size={14} className="transition-transform group-hover:translate-x-0.5" />
              </div>
            </button>
          );
        })}
      </div>
    </div>
  );
}
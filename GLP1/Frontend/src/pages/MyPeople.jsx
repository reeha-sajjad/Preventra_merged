import { useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { ChevronRight, Pill, Syringe, AlertTriangle, CalendarCheck, PackageX, Users } from 'lucide-react';
import { api } from '../data/api';
import PageState from '../components/shared/PageState';
import { ago } from '../components/followups/format';

const BAND_COLOR = { Critical: '#C62828', High: '#B45309', Medium: '#94520A', Low: '#1E6B34' };
const ALERT = {
  refill_problem: { icon: PackageX, color: '#C62828' },
  dose_due:       { icon: Syringe, color: '#B45309' },
  booked:         { icon: CalendarCheck, color: '#1E6B34' },
};

function dueText(due) {
  if (!due) return 'Not known yet';
  const d = new Date(due.at).toLocaleDateString(undefined, { weekday: 'short', day: 'numeric', month: 'short' });
  return due.state === 'overdue' ? `Overdue (was due ${d})` : due.state === 'today' ? 'Today' : d;
}

/**
 * A caretaker's home (Backend/core/caretaker_home.py): everyone who linked
 * them, most in need first - their risk, medicine, the last dose logged, when
 * the next is due, and anything that needs the carer. Opening a person shows
 * their full summary (PatientHome). Readmissions has the same page.
 */
export default function MyPeople() {
  const navigate = useNavigate();
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);
  useEffect(() => { api.getMyPeople().then(setData).catch((e) => setError(e.message)); }, []);
  if (!data) return <PageState error={error} label="the people you care for" />;

  if (data.people.length === 0) {
    return (
      <div className="max-w-xl mx-auto card p-8 text-center animate-fade-in">
        <Users size={22} className="mx-auto text-gray-300" />
        <p className="mt-3 text-sm text-gray-600 leading-relaxed">
          Nobody has added you as their caretaker yet. Ask the person you care for to add you from their own
          page, using the email you signed up with - or ask their hospital to, with their agreement.
        </p>
      </div>
    );
  }

  return (
    <div className="max-w-[1080px] mx-auto space-y-5 animate-fade-in">
      <div>
        <h1 className="text-xl text-gray-800" style={{ fontFamily: 'DM Serif Display, serif' }}>People you care for</h1>
        <p className="text-sm text-gray-500 mt-0.5">Anyone who needs you today is at the top.</p>
      </div>
      <div className="grid gap-4 md:grid-cols-2">
        {data.people.map((p) => (
          <button key={p.patient_idx} type="button" onClick={() => navigate(`/person/${p.patient_idx}`)}
            className="card p-5 text-left hover:shadow-md transition-shadow group">
            <div className="flex items-start justify-between gap-3">
              <div className="min-w-0">
                <div className="text-base font-semibold text-gray-800 truncate">{p.name}</div>
                <div className="text-xs text-gray-500 mt-0.5">{p.status}</div>
              </div>
              <div className="text-right shrink-0">
                <div className="text-xl font-semibold leading-none" style={{ color: BAND_COLOR[p.band] }}>
                  {Math.round(p.dropout_prob * 100)}%
                </div>
                <div className="text-[11px] font-semibold mt-1" style={{ color: BAND_COLOR[p.band] }}>{p.band} risk</div>
              </div>
            </div>

            {p.alerts.length > 0 && (
              <ul className="mt-3 space-y-1">
                {p.alerts.map((a) => {
                  const k = ALERT[a.kind] || { icon: AlertTriangle, color: '#475569' };
                  return (
                    <li key={a.kind} className="flex items-center gap-1.5 text-xs font-medium" style={{ color: k.color }}>
                      <k.icon size={12} /> {a.text}
                    </li>
                  );
                })}
              </ul>
            )}

            <dl className="mt-4 grid grid-cols-3 gap-3 text-xs">
              <div>
                <dt className="text-gray-400 flex items-center gap-1"><Pill size={11} /> Medicine</dt>
                <dd className="text-gray-700 mt-0.5">{p.drug || '—'}</dd>
              </div>
              <div>
                <dt className="text-gray-400">Last dose</dt>
                <dd className="text-gray-700 mt-0.5">{p.last_dose ? `${ago(p.last_dose.at)} · ${p.last_dose.by}` : 'None logged'}</dd>
              </div>
              <div>
                <dt className="text-gray-400">Next dose</dt>
                <dd className="mt-0.5" style={{ color: p.next_due?.state === 'overdue' ? '#C62828' : '#374151' }}>
                  {dueText(p.next_due)}
                </dd>
              </div>
            </dl>
            <div className="mt-3 flex items-center justify-end text-xs font-semibold" style={{ color: 'var(--color-primary)' }}>
              Open summary <ChevronRight size={14} className="group-hover:translate-x-0.5 transition-transform" />
            </div>
          </button>
        ))}
      </div>
    </div>
  );
}
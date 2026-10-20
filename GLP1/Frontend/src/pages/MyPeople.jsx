import { useEffect, useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { Users, AlertTriangle, Syringe, PackageX, CalendarCheck, ChevronRight, HeartHandshake, FileText } from 'lucide-react';
import { api } from '../data/api';
import { RiskBadge } from '../components/shared';
import PageState from '../components/shared/PageState';
import { ago } from '../components/followups/format';

const ALERT = {
  refill_problem: { icon: PackageX, color: '#C62828', bg: '#FFEBEE' },
  dose_due:       { icon: Syringe, color: '#B45309', bg: '#FFF3E0' },
  booked:         { icon: CalendarCheck, color: '#2E7D32', bg: '#E8F5E9' },
};

function dueText(due) {
  if (!due) return '—';
  const d = new Date(due.at).toLocaleDateString(undefined, { weekday: 'short', day: 'numeric', month: 'short' });
  return due.state === 'overdue' ? `Overdue · ${d}` : due.state === 'today' ? 'Today' : d;
}

/**
 * A caretaker's home (Backend/core/caretaker_home.py), laid out like the other
 * GLP-1 home pages: the counts that need them, then everyone who linked them,
 * most in need first. A row opens the patient's full record (PatientDetail -
 * the clinical details, read only, logged as "Caretaker (linked by the
 * patient)"); "Summary" opens the plain-words page written to the patient.
 */
export default function MyPeople() {
  const navigate = useNavigate();
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);
  const [view, setView] = useState('all');
  useEffect(() => { api.getMyPeople().then(setData).catch((e) => setError(e.message)); }, []);

  const people = data?.people || [];
  const has = (p, kind) => p.alerts.some((a) => a.kind === kind);
  const shown = useMemo(() => (
    view === 'critical' ? people.filter((p) => p.dropout_prob >= 0.75)
      : view === 'dose' ? people.filter((p) => has(p, 'dose_due'))
      : view === 'refill' ? people.filter((p) => has(p, 'refill_problem'))
      : people
  ), [people, view]);

  if (!data) return <PageState error={error} label="the people you care for" />;

  const stats = {
    people: people.length,
    critical: people.filter((p) => p.dropout_prob >= 0.75).length,
    dose: people.filter((p) => has(p, 'dose_due')).length,
    refill: people.filter((p) => has(p, 'refill_problem')).length,
  };

  return (
    <div className="max-w-[1200px] mx-auto space-y-5 animate-fade-in">
      <div className="card p-5 md:p-6">
        <div className="mb-4">
          <h1 className="text-lg font-semibold text-gray-800 flex items-center gap-2" style={{ fontFamily: 'var(--p-font-display)' }}>
            <HeartHandshake size={17} className="text-gray-400" /> People you care for
          </h1>
          <p className="text-xs text-gray-400 mt-0.5">
            Everyone who added you as their caretaker. What you open here is recorded in their hospital&rsquo;s access log.
          </p>
        </div>
        <div className="grid gap-3 grid-cols-2 lg:grid-cols-4">
          <Tile icon={Users} label="People" value={stats.people} sub="linked to you" color="#1B4F8A"
                active={view === 'all'} onClick={() => setView('all')} />
          <Tile icon={AlertTriangle} label="Critical risk" value={stats.critical} sub="75% or more chance of stopping"
                color="#C62828" active={view === 'critical'} onClick={() => setView('critical')} />
          <Tile icon={Syringe} label="Doses due" value={stats.dose} sub="today or overdue" color="#B45309"
                active={view === 'dose'} onClick={() => setView('dose')} />
          <Tile icon={PackageX} label="Refill problems" value={stats.refill} sub="reported by the pharmacy" color="#C62828"
                active={view === 'refill'} onClick={() => setView('refill')} />
        </div>
      </div>

      <div className="card overflow-hidden" style={{ padding: 0 }}>
        <div className="px-5 py-4 border-b border-gray-100">
          <h2 className="text-base font-semibold text-gray-800" style={{ fontFamily: 'var(--p-font-display)' }}>
            {view === 'critical' ? 'Critical risk' : view === 'dose' ? 'Doses due' : view === 'refill' ? 'Refill problems' : 'Everyone'}
            <span className="ml-2 text-xs font-normal text-gray-400">{shown.length}</span>
          </h2>
        </div>
        {people.length === 0 ? (
          <p className="p-8 text-center text-sm text-gray-500 max-w-lg mx-auto leading-relaxed">
            Nobody has added you as their caretaker yet. Ask the person you care for to add you from their own page,
            using the email you signed up with - or ask their hospital to, with their agreement.
          </p>
        ) : shown.length === 0 ? (
          <p className="p-8 text-center text-sm text-gray-400">Nobody here right now.</p>
        ) : (
          <div className="overflow-x-auto">
            <table className="data-table">
              <thead>
                <tr><th>Person</th><th>Risk</th><th>Medicine</th><th>Last dose</th><th>Next dose</th>
                    <th>Needs you</th><th aria-label="Open" /></tr>
              </thead>
              <tbody>
                {shown.map((p) => (
                  <tr key={p.patient_idx} className="cursor-pointer" onClick={() => navigate(`/patients/${p.patient_idx}`)}>
                    <td>
                      <div className="text-sm font-semibold text-gray-800">{p.name}</div>
                      <div className="text-[11px] text-gray-400 font-mono">Patient #{p.patient_idx}</div>
                    </td>
                    <td><RiskBadge prob={p.dropout_prob} /></td>
                    <td className="text-sm text-gray-700">
                      {p.drug || '—'}
                      {p.schedule && <div className="text-[11px] text-gray-400">{p.schedule}</div>}
                    </td>
                    <td className="text-xs text-gray-600">
                      {p.last_dose ? <>{ago(p.last_dose.at)}<div className="text-[11px] text-gray-400">by {p.last_dose.by}</div></> : 'None logged'}
                    </td>
                    <td className="text-xs whitespace-nowrap" style={{ color: p.next_due?.state === 'overdue' ? '#C62828' : '#4A5568' }}>
                      {dueText(p.next_due)}
                    </td>
                    <td>
                      <div className="flex flex-wrap gap-1">
                        {p.alerts.length === 0 && <span className="text-xs text-gray-300">—</span>}
                        {p.alerts.map((a) => {
                          const k = ALERT[a.kind] || { icon: AlertTriangle, color: '#475569', bg: '#F1F5F9' };
                          return (
                            <span key={a.kind} className="inline-flex items-center gap-1 whitespace-nowrap rounded-full px-2 py-0.5 text-[11px] font-semibold"
                                  style={{ background: k.bg, color: k.color }}>
                              <k.icon size={11} /> {a.text}
                            </span>
                          );
                        })}
                      </div>
                    </td>
                    <td className="text-right whitespace-nowrap">
                      <button type="button" onClick={(e) => { e.stopPropagation(); navigate(`/person/${p.patient_idx}`); }}
                        className="inline-flex items-center gap-1 text-xs font-semibold px-2.5 py-1.5 rounded-lg border border-gray-200 text-gray-600 hover:bg-gray-50 mr-1">
                        <FileText size={12} /> Summary
                      </button>
                      <ChevronRight size={15} className="text-gray-300 inline" />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
}

function Tile({ icon: Icon, label, value, sub, color, active, onClick }) {
  return (
    <button type="button" onClick={onClick} aria-pressed={active}
      className="min-w-0 text-left rounded-xl border p-2.5 sm:p-3 transition-colors hover:bg-blue-50/30"
      style={{ borderColor: active ? 'var(--color-primary)' : '#EDF2F7' }}>
      <div className="flex items-center gap-1.5 text-[10px] font-semibold uppercase tracking-wider text-gray-400 min-w-0">
        <Icon size={12} style={{ color }} className="flex-shrink-0" /> <span className="leading-tight">{label}</span>
      </div>
      <div className="font-display text-2xl font-semibold text-gray-800 mt-1 leading-none">{value.toLocaleString()}</div>
      {sub && <div className="text-[11px] text-gray-400 mt-1">{sub}</div>}
    </button>
  );
}
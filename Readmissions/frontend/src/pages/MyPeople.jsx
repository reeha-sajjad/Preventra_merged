import React, { useEffect, useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { HeartHandshake, Users, AlertTriangle, Pill, PackageX, CalendarCheck, ChevronRight, FileText, Loader2 } from 'lucide-react';
import { getMyPeople } from '../api';
import RiskBadge from '../components/shared/RiskBadge';
import { ago } from '../components/followups/format';

const ALERT = {
  refill_problem: { icon: PackageX, pill: 'bg-red-50 text-red-700' },
  dose_due:       { icon: Pill, pill: 'bg-amber-50 text-amber-800' },
  booked:         { icon: CalendarCheck, pill: 'bg-green-50 text-green-800' },
};

/**
 * A caretaker's home (api/caretaker_home.py), laid out like the pharmacy and
 * insurer pages: the counts that need them, then everyone who linked them, most
 * in need first. A row opens the patient's full record (PatientDetail - read
 * only, logged as "Caretaker (linked by the patient)"); "Summary" opens the
 * plain-words page written to the patient. GLP-1 has the same page.
 */
export default function MyPeople() {
  const navigate = useNavigate();
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);
  const [view, setView] = useState('all');
  useEffect(() => { getMyPeople().then(setData).catch((e) => setError(e.message)); }, []);

  const people = data?.people || [];
  const has = (p, kind) => p.alerts.some((a) => a.kind === kind);
  const shown = useMemo(() => (
    view === 'high' ? people.filter((p) => p.band === 'High')
      : view === 'meds' ? people.filter((p) => has(p, 'dose_due'))
      : view === 'refill' ? people.filter((p) => has(p, 'refill_problem'))
      : people
  ), [people, view]);

  if (error && !data) {
    return <div className="p-6"><div className="rounded-xl border border-red-200 bg-red-50 p-4 text-red-700">{error}</div></div>;
  }
  if (!data) return <div className="flex justify-center p-16 text-gray-400"><Loader2 className="animate-spin" /></div>;

  const stats = {
    people: people.length,
    high: people.filter((p) => p.band === 'High').length,
    meds: people.filter((p) => has(p, 'dose_due')).length,
    refill: people.filter((p) => has(p, 'refill_problem')).length,
  };
  const openRecord = (id) => navigate(`/patients/${encodeURIComponent(id)}`);

  return (
    <div className="mx-auto max-w-7xl space-y-6 p-4 sm:p-6">
      <div>
        <h1 className="flex items-center gap-2 text-2xl font-bold text-ns-navy">
          <HeartHandshake size={24} className="text-gray-400" /> People you care for
        </h1>
        <p className="mt-1 max-w-2xl text-sm text-gray-500">
          Everyone who added you as their caretaker. What you open here is recorded in their hospital&rsquo;s access log.
        </p>
      </div>

      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <Tile icon={Users} label="People" value={stats.people} sub="linked to you" tone="text-ns-navy"
              active={view === 'all'} onClick={() => setView('all')} />
        <Tile icon={AlertTriangle} label="High readmission risk" value={stats.high} sub="latest score" tone="text-risk-high"
              active={view === 'high'} onClick={() => setView('high')} />
        <Tile icon={Pill} label="Medicines to log" value={stats.meds} sub="not logged today" tone="text-amber-700"
              active={view === 'meds'} onClick={() => setView('meds')} />
        <Tile icon={PackageX} label="Refill problems" value={stats.refill} sub="reported by the pharmacy" tone="text-risk-high"
              active={view === 'refill'} onClick={() => setView('refill')} />
      </div>

      <div className="overflow-hidden rounded-xl border border-gray-200 bg-white shadow-sm">
        <div className="border-b border-gray-100 px-5 py-4">
          <h2 className="font-semibold text-gray-800">
            {view === 'high' ? 'High readmission risk' : view === 'meds' ? 'Medicines to log' : view === 'refill' ? 'Refill problems' : 'Everyone'}
            <span className="ml-2 text-xs font-normal text-gray-400">{shown.length}</span>
          </h2>
        </div>
        {people.length === 0 ? (
          <p className="mx-auto max-w-lg p-8 text-center text-sm leading-relaxed text-gray-500">
            Nobody has added you as their caretaker yet. Ask the person you care for to add you from their own page,
            using the email you signed up with - or ask their hospital to, with their agreement.
          </p>
        ) : shown.length === 0 ? (
          <p className="p-8 text-center text-sm text-gray-400">Nobody here right now.</p>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-left text-sm">
              <thead className="bg-gray-50 text-xs uppercase text-gray-500">
                <tr>
                  <th className="px-5 py-3">Person</th><th className="px-5 py-3">Readmission risk</th>
                  <th className="px-5 py-3">In hospital for</th><th className="px-5 py-3">Went home</th>
                  <th className="px-5 py-3">Medicines logged</th><th className="px-5 py-3">Needs you</th>
                  <th className="px-5 py-3" aria-label="Open" />
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-100">
                {shown.map((p) => (
                  <tr key={p.patient_id} className="cursor-pointer hover:bg-gray-50" onClick={() => openRecord(p.patient_id)}>
                    <td className="whitespace-nowrap px-5 py-3">
                      <div className="font-semibold text-gray-900">{p.name}</div>
                      <div className="font-mono text-xs text-gray-400">{p.patient_id}</div>
                    </td>
                    <td className="whitespace-nowrap px-5 py-3">
                      <span className="mr-2 font-bold text-gray-900">{Number(p.risk_score).toFixed(1)}%</span>
                      {p.band && <RiskBadge riskBand={p.band} size="sm" />}
                    </td>
                    <td className="px-5 py-3 text-gray-700">{p.reason || '—'}</td>
                    <td className="whitespace-nowrap px-5 py-3 text-xs text-gray-600">{p.discharged || '—'}</td>
                    <td className="whitespace-nowrap px-5 py-3 text-xs text-gray-600">
                      {p.logged_today ? <span className="font-semibold text-green-700">Today ✓</span>
                        : p.last_given ? <>{ago(p.last_given.at)}<div className="text-gray-400">by {p.last_given.by}</div></>
                        : 'Not yet'}
                    </td>
                    <td className="px-5 py-3">
                      <div className="flex flex-wrap gap-1">
                        {p.alerts.length === 0 && <span className="text-xs text-gray-300">—</span>}
                        {p.alerts.map((a) => {
                          const k = ALERT[a.kind] || { icon: AlertTriangle, pill: 'bg-gray-100 text-gray-700' };
                          return (
                            <span key={a.kind} className={`inline-flex items-center gap-1 whitespace-nowrap rounded-full px-2 py-0.5 text-[11px] font-semibold ${k.pill}`}>
                              <k.icon size={11} /> {a.text}
                            </span>
                          );
                        })}
                      </div>
                    </td>
                    <td className="whitespace-nowrap px-5 py-3 text-right">
                      <button type="button" onClick={(e) => { e.stopPropagation(); navigate(`/person/${encodeURIComponent(p.patient_id)}`); }}
                        className="mr-1 inline-flex items-center gap-1 rounded-md border border-gray-200 px-2.5 py-1.5 text-xs font-semibold text-gray-600 hover:bg-gray-50">
                        <FileText size={12} /> Summary
                      </button>
                      <ChevronRight size={15} className="inline text-gray-300" />
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

function Tile({ icon, label, value, sub, tone, active, onClick }) {
  const Icon = icon;
  return (
    <button type="button" onClick={onClick} aria-pressed={active}
      className={`rounded-lg border bg-white p-3 text-left transition-colors hover:bg-gray-50 sm:p-4 ${active ? 'border-ns-navy ring-1 ring-ns-navy' : 'border-gray-200'}`}>
      <div className={`flex items-center gap-1.5 text-xs font-medium sm:text-sm ${tone}`}><Icon size={15} /> {label}</div>
      <div className="mt-1.5 text-2xl font-bold text-gray-900 sm:text-3xl">{value.toLocaleString()}</div>
      <div className="mt-1 text-xs text-gray-500">{sub}</div>
    </button>
  );
}
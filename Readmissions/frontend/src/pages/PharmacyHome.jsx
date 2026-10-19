import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { Pill, Users, AlertTriangle, PackageX, Send, Search, ChevronRight, CheckCircle2, Loader2 } from 'lucide-react';
import { getPharmacyHome, markPharmacySeen } from '../api';
import RiskBadge from '../components/shared/RiskBadge';
import ReportDialog, { ReportStatus } from '../components/pharmacy/ReportDialog';
import { REFILL } from '../components/pharmacy/refill';
import { ago } from '../components/followups/format';

const PAGE = 25;

/**
 * A pharmacy's home page in Readmissions (api/pharmacy.py): the discharged
 * patients who fill here - missed refills first, then highest readmission risk -
 * with the latest refill from the pharmacy feed, and a way to report a refill
 * problem to the hospital's case managers. No diagnoses or notes. The same page
 * as GLP-1's (GLP1/Frontend/src/pages/PharmacyHome.jsx).
 */
export default function PharmacyHome() {
  const navigate = useNavigate();
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);
  const [query, setQuery] = useState('');
  const [view, setView] = useState('all');           // all | high | missed
  const [page, setPage] = useState(0);
  const [reporting, setReporting] = useState(null);
  const [sent, setSent] = useState(null);

  const load = useCallback(() => {
    getPharmacyHome().then((d) => { setData(d); setError(null); }).catch((e) => setError(e.message));
  }, []);
  useEffect(() => { load(); }, [load]);
  // Opening this page clears "report done" from the bell.
  useEffect(() => { markPharmacySeen().catch(() => {}); }, []);

  const shown = useMemo(() => {
    let rows = data?.patients || [];
    if (view === 'high') rows = rows.filter((p) => String(p.risk_band).toLowerCase() === 'high');
    if (view === 'missed') rows = rows.filter((p) => p.refill_latest === 'missed');
    if (query.trim()) rows = rows.filter((p) => p.id.toLowerCase().includes(query.trim().toLowerCase()));
    return rows;
  }, [data, view, query]);

  if (error && !data) {
    return <div className="p-6"><div className="rounded-xl border border-red-200 bg-red-50 p-4 text-red-700">{error}</div></div>;
  }
  if (!data) return <div className="flex justify-center p-16 text-gray-400"><Loader2 className="animate-spin" /></div>;

  const { stats } = data;
  const pages = Math.max(1, Math.ceil(shown.length / PAGE));
  const rows = shown.slice(page * PAGE, (page + 1) * PAGE);
  const choose = (v) => { setView(v); setPage(0); };
  const open = (id) => navigate(`/patients/${encodeURIComponent(id)}`);

  return (
    <div className="mx-auto max-w-7xl space-y-6 p-4 sm:p-6">
      <div>
        <h1 className="flex items-center gap-2 text-2xl font-bold text-ns-navy">
          <Pill size={24} className="text-gray-400" /> {data.pharmacy?.name || 'Your pharmacy'}
        </h1>
        <p className="mt-1 max-w-2xl text-sm text-gray-500">
          Patients discharged from hospital who fill their prescriptions here. Diagnoses and notes stay with their
          care team.
        </p>
      </div>

      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <Tile icon={Users} label="Patients" value={stats.patients} sub="fill here" active={view === 'all'}
              onClick={() => choose('all')} tone="text-ns-navy" />
        <Tile icon={AlertTriangle} label="High readmission risk" value={stats.high} sub="latest score"
              active={view === 'high'} onClick={() => choose('high')} tone="text-risk-high" />
        <Tile icon={PackageX} label="Missed last refill" value={stats.missed} sub="from your dispensing records"
              active={view === 'missed'} onClick={() => choose('missed')} tone="text-amber-700" />
        <Tile icon={Send} label="Your open reports" value={stats.open_reports} sub="with a case manager" tone="text-gray-600" />
      </div>

      {sent && (
        <div className="flex items-center gap-2 rounded-xl border border-green-200 bg-green-50 px-4 py-3 text-sm text-risk-low">
          <CheckCircle2 size={16} />
          {sent.added_to_open
            ? `Added to the follow-up already open for ${sent.followup.patient_id}.`
            : `Sent. The hospital's case managers have ${sent.followup.patient_id}.`}
        </div>
      )}

      <div className="overflow-hidden rounded-xl border border-gray-200 bg-white shadow-sm">
        <div className="flex flex-wrap items-center justify-between gap-3 border-b border-gray-100 px-5 py-4">
          <h2 className="font-semibold text-gray-800">
            {view === 'high' ? 'High readmission risk' : view === 'missed' ? 'Missed last refill' : 'All patients'}
            <span className="ml-2 text-xs font-normal text-gray-400">{shown.length.toLocaleString()}</span>
          </h2>
          <label className="relative w-full sm:w-56">
            <Search size={14} className="pointer-events-none absolute left-2.5 top-1/2 -translate-y-1/2 text-gray-400" />
            <input value={query} onChange={(e) => { setQuery(e.target.value); setPage(0); }}
              placeholder="Patient number" aria-label="Search by patient number"
              className="w-full rounded-md border border-gray-300 py-2 pl-8 pr-3 text-sm focus:border-ns-navy focus:outline-none focus:ring-1 focus:ring-ns-navy" />
          </label>
        </div>
        <div className="overflow-x-auto">
          <table className="w-full text-left text-sm">
            <thead className="bg-gray-50 text-xs uppercase text-gray-500">
              <tr>
                <th className="px-5 py-3">Patient</th><th className="px-5 py-3">Readmission risk</th>
                <th className="px-5 py-3">Latest refill</th><th className="px-5 py-3">Missed (4 wks)</th>
                <th className="px-5 py-3">Hospital</th><th className="px-5 py-3">Discharged</th>
                <th className="px-5 py-3">Follow-up</th><th className="px-5 py-3" aria-label="Actions" />
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-100">
              {rows.map((p) => {
                const refill = REFILL[p.refill_latest];
                return (
                  <tr key={p.id} className="hover:bg-gray-50">
                    <td className="whitespace-nowrap px-5 py-3">
                      <button type="button" onClick={() => open(p.id)} className="font-mono text-xs font-semibold text-gray-700 hover:underline">{p.id}</button>
                    </td>
                    <td className="whitespace-nowrap px-5 py-3">
                      <span className="mr-2 font-bold text-gray-900">{Number(p.risk_score).toFixed(1)}%</span>
                      <RiskBadge riskBand={p.risk_band} size="sm" />
                    </td>
                    <td className="whitespace-nowrap px-5 py-3">
                      {refill ? <span className={`rounded-full px-2 py-0.5 text-[11px] font-semibold ${refill.pill}`}>{refill.label}</span>
                        : <span className="text-xs text-gray-300">—</span>}
                    </td>
                    <td className={`px-5 py-3 font-mono text-sm ${p.missed_recent ? 'font-semibold text-risk-high' : 'text-gray-400'}`}>{p.missed_recent}</td>
                    <td className="px-5 py-3 text-xs text-gray-500">{p.hospital || '—'}</td>
                    <td className="whitespace-nowrap px-5 py-3 text-xs text-gray-500">{p.discharge_date || '—'}</td>
                    <td className="px-5 py-3">{p.open_followup ? <ReportStatus f={p.open_followup} /> : <span className="text-xs text-gray-300">—</span>}</td>
                    <td className="whitespace-nowrap px-5 py-3 text-right">
                      <button type="button" onClick={() => setReporting(p)}
                        className="rounded-md border border-gray-300 px-2.5 py-1.5 text-xs font-semibold text-gray-700 hover:bg-gray-50">
                        Report a problem
                      </button>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
          {!rows.length && <div className="p-8 text-center text-sm text-gray-400">No patients match.</div>}
        </div>
        {pages > 1 && (
          <div className="flex items-center justify-between border-t border-gray-100 px-5 py-3 text-xs text-gray-500">
            <span>Page {page + 1} of {pages}</span>
            <div className="flex gap-2">
              <button type="button" disabled={page === 0} onClick={() => setPage((n) => n - 1)}
                className="rounded-md border border-gray-300 px-3 py-1.5 disabled:opacity-40">Previous</button>
              <button type="button" disabled={page >= pages - 1} onClick={() => setPage((n) => n + 1)}
                className="rounded-md border border-gray-300 px-3 py-1.5 disabled:opacity-40">Next</button>
            </div>
          </div>
        )}
      </div>

      <div className="overflow-hidden rounded-xl border border-gray-200 bg-white shadow-sm">
        <div className="border-b border-gray-100 px-5 py-4">
          <h2 className="font-semibold text-gray-800">Your reports</h2>
          <p className="mt-0.5 text-xs text-gray-500">Open ones, and those finished in the last 30 days.</p>
        </div>
        {data.reports.length === 0 ? (
          <div className="p-8 text-center text-sm text-gray-400">
            Nothing reported yet. Use &ldquo;Report a problem&rdquo; when a patient misses a pickup or can&rsquo;t get their refill.
          </div>
        ) : (
          <ul className="divide-y divide-gray-100">
            {data.reports.map((r) => (
              <li key={r.id}>
                <button type="button" onClick={() => open(r.patient_id)}
                  className="group flex w-full flex-wrap items-center gap-3 px-5 py-3 text-left hover:bg-gray-50">
                  <span className="w-28 font-mono text-xs font-semibold text-gray-700">{r.patient_id}</span>
                  <span className="min-w-[180px] flex-1 text-sm text-gray-700">
                    {r.reports.map((x) => x.problem_label).join(' · ')}
                    <span className="text-xs text-gray-400"> · {ago(r.reports[r.reports.length - 1]?.at || r.requested_at)}</span>
                  </span>
                  <ReportStatus f={r} />
                  <ChevronRight size={15} className="text-gray-300 group-hover:text-gray-500" />
                </button>
              </li>
            ))}
          </ul>
        )}
      </div>

      {reporting && (
        <ReportDialog patientId={reporting.id} problems={data.problems} onClose={() => setReporting(null)}
          onDone={(out) => { setReporting(null); setSent(out); load(); }} />
      )}
    </div>
  );
}

function Tile({ icon, label, value, sub, tone, active, onClick }) {
  const Icon = icon;
  const body = (
    <>
      <div className={`flex items-center gap-1.5 text-xs font-medium sm:text-sm ${tone}`}><Icon size={15} /> {label}</div>
      <div className="mt-1.5 text-2xl font-bold text-gray-900 sm:text-3xl">{value.toLocaleString()}</div>
      <div className="mt-1 text-xs text-gray-500">{sub}</div>
    </>
  );
  return onClick
    ? <button type="button" onClick={onClick} aria-pressed={active}
        className={`rounded-lg border bg-white p-3 text-left transition-colors hover:bg-gray-50 sm:p-4 ${active ? 'border-ns-navy ring-1 ring-ns-navy' : 'border-gray-200'}`}>{body}</button>
    : <div className="rounded-lg border border-gray-200 bg-white p-3 sm:p-4">{body}</div>;
}
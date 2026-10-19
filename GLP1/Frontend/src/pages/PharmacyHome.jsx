import { useCallback, useEffect, useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { Pill, Users, AlertTriangle, RefreshCw, Send, Search, ChevronRight, CheckCircle2 } from 'lucide-react';
import { api } from '../data/api';
import { RiskBadge } from '../components/shared';
import PageState from '../components/shared/PageState';
import ReportDialog, { ReportStatus } from '../components/pharmacy/ReportDialog';
import { ago } from '../components/followups/format';

const REFILL = {
  low:     { label: 'Low', color: '#C62828', bg: '#FFEBEE' },
  typical: { label: 'Typical', color: '#B45309', bg: '#FFF7ED' },
  good:    { label: 'Good', color: '#2E7D32', bg: '#E8F5E9' },
};
const PAGE = 25;

/**
 * A pharmacy's home page (Backend/core/pharmacy.py): the patients who fill
 * here, highest dropout risk first, with what a pharmacy needs - the drug,
 * how reliably it is refilled, and who their case manager is - and a way to
 * report a refill problem to that case manager. No diagnoses or notes.
 */
export default function PharmacyHome() {
  const navigate = useNavigate();
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);
  const [query, setQuery] = useState('');
  const [view, setView] = useState('all');           // all | critical | low_refill
  const [page, setPage] = useState(0);
  const [reporting, setReporting] = useState(null);
  const [sent, setSent] = useState(null);

  const load = useCallback(() => {
    api.getPharmacyHome().then((d) => { setData(d); setError(null); }).catch((e) => setError(e.message));
  }, []);
  useEffect(() => { load(); }, [load]);
  // Opening this page clears "report done" from the bell.
  useEffect(() => {
    api.markPharmacySeen()
      .then(() => window.dispatchEvent(new Event('preventra:bell-refresh')))
      .catch(() => {});
  }, []);

  const shown = useMemo(() => {
    let rows = data?.patients || [];
    if (view === 'critical') rows = rows.filter((p) => p.dropout_prob >= 0.75);
    if (view === 'low_refill') rows = rows.filter((p) => p.refill_band === 'low');
    if (query.trim()) rows = rows.filter((p) => String(p.patient_idx).includes(query.trim()));
    return rows;
  }, [data, view, query]);

  if (!data) return <PageState error={error} label="your pharmacy's patients" />;
  const { stats } = data;
  const pages = Math.max(1, Math.ceil(shown.length / PAGE));
  const rows = shown.slice(page * PAGE, (page + 1) * PAGE);
  const choose = (v) => { setView(v); setPage(0); };

  return (
    <div className="max-w-[1200px] mx-auto space-y-5 animate-fade-in">
      <div className="card p-5 md:p-6">
        <div className="flex flex-wrap items-start justify-between gap-3 mb-4">
          <div>
            <h1 className="text-lg font-semibold text-gray-800 flex items-center gap-2" style={{ fontFamily: 'DM Serif Display, serif' }}>
              <Pill size={17} className="text-gray-400" /> {data.pharmacy?.name || 'Your pharmacy'}
            </h1>
            <p className="text-xs text-gray-400 mt-0.5">
              Patients on GLP-1 therapy who fill here, in any hospital. Diagnoses and notes stay with their care team.
            </p>
          </div>
        </div>
        <div className="grid gap-3 grid-cols-2 lg:grid-cols-4">
          <Tile icon={Users} label="Patients" value={stats.patients} sub="fill here" color="#1B4F8A"
                active={view === 'all'} onClick={() => choose('all')} />
          <Tile icon={AlertTriangle} label="Critical risk" value={stats.critical} sub="75% or more chance of stopping"
                color="#C62828" active={view === 'critical'} onClick={() => choose('critical')} />
          <Tile icon={RefreshCw} label="Low refill reliability" value={stats.low_refill} sub="compared with all patients"
                color="#B45309" active={view === 'low_refill'} onClick={() => choose('low_refill')} />
          <Tile icon={Send} label="Your open reports" value={stats.open_reports} sub="with a case manager" color="#475569" />
        </div>
      </div>

      {sent && (
        <div className="flex items-center gap-2 rounded-xl px-4 py-3 text-sm" style={{ background: '#E8F5E9', color: '#2E7D32' }}>
          <CheckCircle2 size={16} />
          {sent.added_to_open
            ? `Added to the follow-up already open for patient #${sent.followup.patient_idx}.`
            : `Sent. Patient #${sent.followup.patient_idx}'s case manager has it.`}
        </div>
      )}

      <div className="card overflow-hidden" style={{ padding: 0 }}>
        <div className="flex flex-wrap items-center justify-between gap-3 px-5 py-4 border-b border-gray-100">
          <h2 className="text-base font-semibold text-gray-800" style={{ fontFamily: 'DM Serif Display, serif' }}>
            {view === 'critical' ? 'Critical risk' : view === 'low_refill' ? 'Low refill reliability' : 'All patients'}
            <span className="ml-2 text-xs font-normal text-gray-400">{shown.length.toLocaleString()}</span>
          </h2>
          <label className="relative w-full sm:w-56">
            <Search size={13} className="absolute left-2.5 top-1/2 -translate-y-1/2 text-gray-400 pointer-events-none" />
            <input value={query} onChange={(e) => { setQuery(e.target.value); setPage(0); }}
              placeholder="Patient number" aria-label="Search by patient number"
              className="w-full text-xs pl-8 pr-3 py-2 rounded-lg border border-gray-200 focus:outline-none focus:ring-1 focus:ring-blue-400" />
          </label>
        </div>
        <div className="overflow-x-auto">
          <table className="data-table">
            <thead>
              <tr><th>Patient</th><th>Risk</th><th>Drug</th><th>Refill reliability</th><th>Hospital</th>
                  <th>Case manager</th><th>Follow-up</th><th aria-label="Actions" /></tr>
            </thead>
            <tbody>
              {rows.map((p) => (
                <tr key={p.patient_idx}>
                  <td>
                    <button type="button" onClick={() => navigate(`/patients/${p.patient_idx}`)}
                      className="font-mono text-xs font-semibold text-gray-700 hover:underline">#{p.patient_idx}</button>
                  </td>
                  <td><RiskBadge prob={p.dropout_prob} /></td>
                  <td className="text-sm text-gray-700">{p.drug}</td>
                  <td>
                    {p.refill_band && (
                      <span className="text-[11px] font-semibold px-2 py-0.5 rounded-full"
                            style={{ background: REFILL[p.refill_band].bg, color: REFILL[p.refill_band].color }}>
                        {REFILL[p.refill_band].label}
                      </span>
                    )}
                  </td>
                  <td className="text-xs text-gray-500">{p.hospital || '—'}</td>
                  <td className="text-xs" style={{ color: p.case_manager ? '#2D3748' : '#A0AEC0' }}>
                    {p.case_manager || 'Hospital case managers'}
                  </td>
                  <td>{p.open_followup ? <ReportStatus f={p.open_followup} /> : <span className="text-xs text-gray-300">—</span>}</td>
                  <td className="text-right whitespace-nowrap">
                    <button type="button" onClick={() => setReporting(p)}
                      className="text-xs font-semibold px-2.5 py-1.5 rounded-lg border border-gray-200 text-gray-700 hover:bg-gray-50">
                      Report a problem
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          {!rows.length && <div className="p-8 text-center text-sm text-gray-400">No patients match.</div>}
        </div>
        {pages > 1 && (
          <div className="flex items-center justify-between px-5 py-3 border-t border-gray-100 text-xs text-gray-500">
            <span>Page {page + 1} of {pages}</span>
            <div className="flex gap-2">
              <button type="button" disabled={page === 0} onClick={() => setPage((n) => n - 1)}
                className="px-3 py-1.5 rounded-lg border border-gray-200 disabled:opacity-40">Previous</button>
              <button type="button" disabled={page >= pages - 1} onClick={() => setPage((n) => n + 1)}
                className="px-3 py-1.5 rounded-lg border border-gray-200 disabled:opacity-40">Next</button>
            </div>
          </div>
        )}
      </div>

      <div className="card overflow-hidden" style={{ padding: 0 }}>
        <div className="px-5 py-4 border-b border-gray-100">
          <h2 className="text-base font-semibold text-gray-800" style={{ fontFamily: 'DM Serif Display, serif' }}>Your reports</h2>
          <p className="text-xs text-gray-400 mt-0.5">Open ones, and those finished in the last 30 days.</p>
        </div>
        {data.reports.length === 0 ? (
          <div className="p-8 text-center text-sm text-gray-400">
            Nothing reported yet. Use &ldquo;Report a problem&rdquo; when a patient misses a pickup or can&rsquo;t get their refill.
          </div>
        ) : (
          <ul className="divide-y divide-gray-100">
            {data.reports.map((r) => (
              <li key={r.id}>
                <button type="button" onClick={() => navigate(`/patients/${r.patient_idx}`)}
                  className="group w-full text-left flex flex-wrap items-center gap-3 px-5 py-3 hover:bg-gray-50">
                  <span className="font-mono text-xs font-semibold text-gray-700 w-14">#{r.patient_idx}</span>
                  <span className="flex-1 min-w-[180px] text-sm text-gray-700">
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
        <ReportDialog patient={reporting} problems={data.problems} onClose={() => setReporting(null)}
          onDone={(out) => { setReporting(null); setSent(out); load(); }} />
      )}
    </div>
  );
}

function Tile({ icon: Icon, label, value, sub, color, active, onClick }) {
  const body = (
    <>
      <div className="flex items-center gap-1.5 text-[10px] font-semibold uppercase tracking-wider text-gray-400 min-w-0">
        <Icon size={12} style={{ color }} className="flex-shrink-0" /> <span className="leading-tight">{label}</span>
      </div>
      <div className="font-display text-2xl font-semibold text-gray-800 mt-1 leading-none">{value.toLocaleString()}</div>
      {sub && <div className="text-[11px] text-gray-400 mt-1">{sub}</div>}
    </>
  );
  return onClick
    ? <button type="button" onClick={onClick} aria-pressed={active}
        className="min-w-0 text-left rounded-xl border p-2.5 sm:p-3 transition-colors hover:bg-blue-50/30"
        style={{ borderColor: active ? 'var(--color-primary)' : '#EDF2F7' }}>{body}</button>
    : <div className="min-w-0 rounded-xl border border-gray-100 p-2.5 sm:p-3">{body}</div>;
}
import { useEffect, useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import {
  Building2, Users, AlertTriangle, TrendingDown, HeartHandshake, CalendarClock, Inbox, UserCheck,
  Hourglass, Stethoscope, HeartPulse, ArrowRight, ChevronRight, ShieldQuestion,
} from 'lucide-react';
import { api } from '../data/api';
import { RiskBadge } from '../components/shared';
import PageState from '../components/shared/PageState';

/**
 * "Today": the case manager's home page (Backend/core/casemanager.py).
 *
 * The hospital at a glance - patients on GLP-1 therapy, how many are at
 * critical risk, predicted to stop, adherent - and then the work: follow-up
 * requests, patients missing a doctor or nurse, and critical-risk patients
 * nobody is following up. Each block links to the page where it gets done.
 * Laid out like the Overview and My patients, so the three home pages read as
 * one product.
 *
 * "My caseload" shows the same page for the patients the case manager is the
 * main case manager of; "Whole hospital" for everyone, to cover a colleague.
 * It opens on My caseload when they have one.
 */
export default function CaseManagerHome() {
  const navigate = useNavigate();
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);
  // null until the first answer says whether there is a caseload to open on.
  const [mine, setMine] = useState(null);

  useEffect(() => {
    let live = true;
    api.getCaseManagerToday(!!mine).then((d) => {
      if (!live) return;
      if (mine === null && d.caseload?.patients > 0) { setMine(true); return; }   // reloads, mine
      setData(d);
    }).catch((e) => { if (live) setError(e.message); });
    return () => { live = false; };
  }, [mine]);

  if (!data) return <PageState error={error} label="today's work" />;

  const { stats, followups: f, gaps, unattended } = data;
  const caseload = data.caseload?.patients ?? 0;
  const showingMine = !!data.caseload?.mine;
  // Links into the patient list keep to the same patients as the page.
  const list = (q = '') => `/patients${showingMine ? `?case_manager=me${q ? `&${q}` : ''}` : (q ? `?${q}` : '')}`;
  const choose = (m) => { if (m !== showingMine) { setData(null); setMine(m); } };
  const total = stats.patients;
  const pct = (n) => (total ? Math.round((n / total) * 100) : 0);
  const open = (idx) => navigate(`/patients/${idx}`);

  return (
    <div className="exec-summary-page">

      {/* ── The hospital at a glance ─────────────────────────────── */}
      <div className="card p-5 md:p-6 animate-fade-up">
        <div className="flex flex-wrap items-start justify-between gap-2 mb-4">
          <div>
            <h2 className="text-lg font-semibold text-gray-800 flex items-center gap-2" style={{ fontFamily: 'var(--p-font-display)' }}>
              <Building2 size={17} className="text-gray-400" />
              {data.hospital?.name || 'Your hospital'}
            </h2>
            <p className="text-xs text-gray-400 mt-0.5">
              {showingMine
                ? `Your caseload: the ${caseload.toLocaleString()} patients you are the case manager of, and what needs doing today`
                : 'GLP-1 therapy across your hospital, and what needs doing today'}
            </p>
          </div>
          <div className="flex flex-wrap items-center gap-3">
            {caseload > 0 && (
              <div role="group" aria-label="Whose patients" className="inline-flex rounded-lg border border-gray-200 p-0.5 text-xs font-semibold">
                {[[true, 'My caseload'], [false, 'Whole hospital']].map(([m, label]) => (
                  <button key={label} type="button" onClick={() => choose(m)} aria-pressed={showingMine === m}
                    className="px-3 py-1.5 rounded-md transition-colors"
                    style={showingMine === m ? { background: 'var(--color-primary)', color: 'white' } : { color: '#718096' }}>
                    {label}
                  </button>
                ))}
              </div>
            )}
            <Link to="/overview" className="inline-flex items-center gap-1 text-xs font-semibold whitespace-nowrap"
                  style={{ color: 'var(--color-primary)' }}>
              Hospital overview <ArrowRight size={13} />
            </Link>
          </div>
        </div>
        <div className="grid gap-3 grid-cols-2 lg:grid-cols-4">
          <Tile icon={Users} label="Patients" value={total} sub={showingMine ? 'in your caseload' : 'on GLP-1 therapy'} color="#1B4F8A" to={list()} />
          <Tile icon={AlertTriangle} label="Critical risk" value={stats.critical}
                sub={`75% or more · ${pct(stats.critical)}% of patients`} color="#C62828" to={list('min_risk=75')} />
          <Tile icon={TrendingDown} label="Predicted to stop" value={stats.predicted_dropout}
                sub={`${pct(stats.predicted_dropout)}% of patients`} color="#EF6C00" to={list('prediction=Dropout%20Risk')} />
          <Tile icon={HeartHandshake} label="Adherent" value={stats.adherent}
                sub={`${pct(stats.adherent)}% staying on therapy`} color="#2E7D32" />
        </div>
      </div>

      {/* ── The work ─────────────────────────────────────────────── */}
      <div className="grid gap-5 lg:grid-cols-2 [&>*]:min-w-0">
        <div className="card p-5 md:p-6 animate-fade-up stagger-2 min-w-0">
          <Heading icon={CalendarClock} title="Follow-ups"
                   sub={showingMine ? "Your patients' requests, and those of patients with no case manager yet"
                                    : 'Requests from doctors and nurses to contact and book a patient'}
                   action={<Link to="/follow-ups" className="inline-flex items-center gap-1 text-xs font-semibold whitespace-nowrap"
                                 style={{ color: 'var(--color-primary)' }}>Open follow-ups <ArrowRight size={13} /></Link>} />
          <div className="grid gap-2 sm:gap-3 grid-cols-3">
            <Tile icon={Inbox} label="Waiting" value={f.waiting} color="#C62828" to="/follow-ups"
                  sub={f.urgent_waiting ? `${f.urgent_waiting} urgent` : 'none urgent'} />
            <Tile icon={UserCheck} label="Yours" value={f.mine} color="#1B4F8A" to="/follow-ups?mine=1" sub="you have taken" />
            <Tile icon={Hourglass} label="Overdue" value={f.overdue} color="#B45309" to="/follow-ups" sub="open over 7 days" />
          </div>
          <div className="mt-4">
            <div className="text-[10px] font-semibold uppercase tracking-wider text-gray-400 mb-1.5">Next to pick up</div>
            {f.next.length === 0 ? (
              <p className="text-sm text-gray-400 py-3">Nothing waiting. New requests appear here and in your bell.</p>
            ) : (
              <ul className="divide-y divide-gray-100 -mx-1">
                {f.next.map((r) => (
                  <li key={r.id}>
                    <Link to="/follow-ups" className="group flex items-center gap-3 px-1 py-2 rounded-md hover:bg-gray-50">
                      <span className="font-mono text-xs font-semibold text-gray-700 w-14">#{r.patient_idx}</span>
                      {r.dropout_prob != null && <RiskBadge prob={r.dropout_prob} />}
                      <span className="text-[11px] font-semibold px-2 py-0.5 rounded-full"
                            style={r.urgency === 'urgent' ? { background: '#FFEBEE', color: '#C62828' } : { background: '#EBF4FF', color: '#1B4F8A' }}>
                        {r.urgency === 'urgent' ? 'Urgent' : 'Routine'}
                      </span>
                      <span className="flex-1 min-w-0 truncate text-xs text-gray-500">
                        {r.requested_by}{r.note ? ` · ${r.note}` : ''}
                      </span>
                      {r.overdue && <span className="text-[11px] font-semibold" style={{ color: '#C62828' }}>overdue</span>}
                      <ChevronRight size={15} className="text-gray-300 group-hover:text-gray-500" />
                    </Link>
                  </li>
                ))}
              </ul>
            )}
          </div>
        </div>

        <div className="card p-5 md:p-6 animate-fade-up stagger-3 min-w-0">
          <Heading icon={HeartPulse} title="Gaps in care teams"
                   sub="Patients no doctor or nurse is looking after - highest risk first"
                   action={<Link to="/staff" className="inline-flex items-center gap-1 text-xs font-semibold whitespace-nowrap"
                                 style={{ color: 'var(--color-primary)' }}>Staff <ArrowRight size={13} /></Link>} />
          <div className="grid gap-3 grid-cols-2 mb-3">
            <Tile icon={Stethoscope} label="No doctor" value={gaps.no_doctor} color={gaps.no_doctor ? '#EF6C00' : '#2E7D32'}
                  to={gaps.no_doctor ? list('unassigned=doctor') : undefined} sub={gaps.no_doctor ? 'assign one' : 'all covered'} />
            <Tile icon={HeartPulse} label="No nurse" value={gaps.no_nurse} color={gaps.no_nurse ? '#EF6C00' : '#2E7D32'}
                  to={gaps.no_nurse ? list('unassigned=nurse') : undefined} sub={gaps.no_nurse ? 'assign one' : 'all covered'} />
          </div>
          {!showingMine && gaps.no_case_manager > 0 && caseload >= 0 && (
            <Link to="/patients?unassigned=case_manager" className="block text-xs font-semibold mb-3" style={{ color: '#B45309' }}>
              {gaps.no_case_manager.toLocaleString()} patients have no case manager yet - assign one
            </Link>
          )}
          {gaps.patients.length > 0 && (
            <ul className="divide-y divide-gray-100 -mx-1">
              {gaps.patients.map((p) => (
                <li key={p.patient_idx}>
                  <button type="button" onClick={() => open(p.patient_idx)}
                    className="group w-full flex items-center gap-3 px-1 py-2 text-left hover:bg-gray-50 rounded-md">
                    <span className="font-mono text-xs font-semibold text-gray-700 w-14">#{p.patient_idx}</span>
                    <RiskBadge prob={p.dropout_prob} />
                    <span className="flex-1 text-xs" style={{ color: '#B45309' }}>No {p.missing.join(' or ')}</span>
                    <ChevronRight size={15} className="text-gray-300 group-hover:text-gray-500" />
                  </button>
                </li>
              ))}
            </ul>
          )}
        </div>
      </div>

      {/* ── High risk and nobody following up ────────────────────── */}
      <div className="card p-0 overflow-hidden animate-fade-up stagger-4">
        <div className="flex flex-col sm:flex-row sm:items-start sm:justify-between gap-2 px-5 md:px-6 pt-5 pb-4">
          <div className="flex items-start gap-3">
            <div className="w-9 h-9 rounded-xl flex items-center justify-center flex-shrink-0"
                 style={{ background: unattended.total ? '#FFEBEE' : '#E8F5E9' }}>
              <ShieldQuestion size={16} style={{ color: unattended.total ? '#C62828' : '#2E7D32' }} />
            </div>
            <div>
              <h2 className="text-lg font-semibold text-gray-800" style={{ fontFamily: 'var(--p-font-display)' }}>
                Critical risk, no follow-up
              </h2>
              <p className="text-xs text-gray-400 mt-0.5 max-w-2xl">
                {unattended.total
                  ? `${unattended.total.toLocaleString()} patients at critical risk of stopping with no follow-up open or done in the last 30 days. Check with their doctor or nurse, or contact the patient.`
                  : 'Every critical-risk patient has a follow-up open or done in the last 30 days.'}
              </p>
            </div>
          </div>
          {unattended.total > unattended.patients.length && (
            <Link to={list('min_risk=75')} className="inline-flex items-center gap-1 text-xs font-semibold whitespace-nowrap sm:mt-1"
                  style={{ color: 'var(--color-primary)' }}>
              See all critical <ArrowRight size={13} />
            </Link>
          )}
        </div>
        {unattended.patients.length > 0 && (
          <div className="overflow-x-auto border-t border-gray-100">
            <table className="data-table">
              <thead>
                <tr><th>Patient</th><th>Risk</th><th>Main reason</th><th>Care team</th><th>Drug</th><th aria-label="Open" /></tr>
              </thead>
              <tbody>
                {unattended.patients.map((p) => (
                  <tr key={p.patient_idx} className="cursor-pointer group" onClick={() => open(p.patient_idx)}>
                    <td className="font-mono text-xs font-semibold text-gray-700">#{p.patient_idx}</td>
                    <td><RiskBadge prob={p.dropout_prob} /></td>
                    <td className="text-gray-700">{p.main_reason || '—'}</td>
                    <td className="text-xs text-gray-500">
                      {[...p.doctors, ...p.nurses].join(', ') || <span style={{ color: '#B45309' }}>No care team</span>}
                    </td>
                    <td className="font-mono text-xs text-gray-500">{p.drug}</td>
                    <td className="text-right">
                      <ChevronRight size={16} className="inline text-gray-300 group-hover:text-gray-500 transition-colors" />
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

/* A figure tile, styled like the Overview's hospital card. */
function Tile({ icon: Icon, label, value, sub, color, to }) {
  const body = (
    <>
      <div className="flex items-center gap-1.5 text-[10px] font-semibold uppercase tracking-wider text-gray-400 min-w-0">
        <Icon size={12} style={{ color }} className="flex-shrink-0" /> <span className="leading-tight">{label}</span>
      </div>
      <div className="font-display text-2xl font-semibold text-gray-800 mt-1 leading-none">{value.toLocaleString()}</div>
      {sub && <div className="text-[11px] text-gray-400 mt-1">{sub}</div>}
    </>
  );
  return to
    ? <Link to={to} className="min-w-0 rounded-xl border border-gray-100 p-2.5 sm:p-3 hover:border-blue-200 hover:bg-blue-50/30 transition-colors">{body}</Link>
    : <div className="min-w-0 rounded-xl border border-gray-100 p-2.5 sm:p-3">{body}</div>;
}

function Heading({ icon: Icon, title, sub, action }) {
  return (
    <div className="flex flex-wrap items-start justify-between gap-2 mb-4">
      <div className="flex items-start gap-3">
        <div className="w-9 h-9 rounded-xl flex items-center justify-center flex-shrink-0" style={{ background: '#EBF4FF' }}>
          <Icon size={16} style={{ color: 'var(--color-primary)' }} />
        </div>
        <div>
          <h2 className="text-base font-semibold text-gray-800" style={{ fontFamily: 'var(--p-font-display)' }}>{title}</h2>
          <p className="text-xs text-gray-400">{sub}</p>
        </div>
      </div>
      {action}
    </div>
  );
}
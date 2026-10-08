import { useCallback, useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { CalendarClock, Inbox, Hourglass, AlertTriangle, ChevronRight, TrendingDown } from 'lucide-react';
import { api } from '../data/api';
import { RiskBadge } from '../components/shared';
import { useRole } from '../context/RoleContext';
import { DoneDialog, StatusPill, UrgencyPill } from '../components/followups/shared';
import { ago } from '../components/followups/format';

/**
 * Follow-ups: the case managers' work list (hospital admins can see it and step
 * in). Doctors and hospital nurses ask from a patient's page; a case manager
 * takes the request, books the patient in the hospital's own system, and marks
 * it done here. See Backend/core/followups.py.
 *
 * Shows what booking needs and nothing of the clinical layer.
 */
export default function FollowUps() {
  const navigate = useNavigate();
  const { isCaseManager } = useRole();
  const [tab, setTab] = useState('active');
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(null);
  const [doneFor, setDoneFor] = useState(null);

  const load = useCallback(() => {
    api.getFollowUps(tab).then((d) => { setData(d); setError(null); }).catch((e) => setError(e.message));
  }, [tab]);
  useEffect(() => { load(); }, [load]);
  const switchTab = (k) => { if (k !== tab) { setData(null); setTab(k); } };

  const take = async (f) => {
    setBusy(f.id); setError(null);
    try { await api.takeFollowUp(f.id); } catch (e) { setError(e.message); }
    setBusy(null); load();
  };

  const counts = data?.counts ?? { open: 0, in_progress: 0, overdue: 0 };
  const items = data?.items ?? [];
  const tile = (Icon, label, value, sub, color) => (
    <div className="rounded-xl border border-gray-100 p-3">
      <div className="flex items-center gap-1.5 text-[10px] font-semibold uppercase tracking-wider text-gray-400">
        <Icon size={12} style={{ color }} /> {label}
      </div>
      <div className="font-display text-2xl font-semibold text-gray-800 mt-1 leading-none">{value}</div>
      <div className="text-[11px] text-gray-400 mt-1">{sub}</div>
    </div>
  );

  return (
    <div className="exec-summary-page">
      <div className="card p-5 md:p-6 animate-fade-up">
        <div className="flex flex-wrap items-start justify-between gap-2 mb-4">
          <div>
            <h2 className="text-lg font-semibold text-gray-800 flex items-center gap-2" style={{ fontFamily: 'DM Serif Display, serif' }}>
              <CalendarClock size={17} className="text-gray-400" /> Follow-up requests
            </h2>
            <p className="text-xs text-gray-400 mt-0.5 max-w-xl">
              Doctors and nurses ask for a patient to be followed up. Take a request, contact the patient,
              book them in the hospital&rsquo;s own system, then mark it done.
            </p>
          </div>
        </div>
        <div className="grid gap-3 grid-cols-3">
          {tile(Inbox, 'Waiting', counts.open, 'nobody has taken these yet', '#C62828')}
          {tile(Hourglass, 'In progress', counts.in_progress, 'taken by a case manager', '#EF6C00')}
          {tile(AlertTriangle, 'Overdue', counts.overdue, 'open more than 7 days', '#B45309')}
        </div>
      </div>

      <div className="card p-0 overflow-hidden animate-fade-up stagger-2">
        <div className="flex items-center gap-1 px-4 pt-3 border-b border-gray-100">
          {[['active', 'To do'], ['done', 'Done (last 30 days)']].map(([k, label]) => (
            <button key={k} type="button" onClick={() => switchTab(k)}
              className="text-sm font-medium px-3 py-2 -mb-px border-b-2 transition-colors"
              style={tab === k ? { borderColor: 'var(--color-primary)', color: 'var(--color-primary)' }
                               : { borderColor: 'transparent', color: '#718096' }}>
              {label}
            </button>
          ))}
        </div>

        {error && <p className="m-4 rounded-md px-3 py-2 text-sm" style={{ background: '#FFEBEE', color: '#C62828' }}>{error}</p>}
        {!data && !error && <div className="m-4 h-32 rounded-lg bg-gray-100 animate-pulse" />}

        {data && items.length === 0 && (
          <div className="px-4 py-14 text-center">
            <div className="w-10 h-10 rounded-xl mx-auto flex items-center justify-center" style={{ background: '#E8F5E9' }}>
              <CalendarClock size={18} style={{ color: '#2E7D32' }} />
            </div>
            <div className="mt-3 text-sm font-medium text-gray-700">
              {tab === 'active' ? 'No follow-ups waiting' : 'Nothing finished in the last 30 days'}
            </div>
            <div className="mt-0.5 text-xs text-gray-400">New requests from doctors and nurses appear here and in your bell.</div>
          </div>
        )}

        {items.length > 0 && (
          <div className="overflow-x-auto">
            <table className="data-table">
              <thead>
                <tr>
                  <th>Patient</th><th>Risk</th><th>How soon</th><th>Asked by</th><th>Note</th>
                  <th>{tab === 'active' ? 'Status' : 'Outcome'}</th><th aria-label="Actions" />
                </tr>
              </thead>
              <tbody>
                {items.map((f) => {
                  const othersTook = isCaseManager && f.status === 'in_progress' && !f.mine;
                  return (
                    <tr key={f.id} style={f.overdue ? { background: '#FFF8F8' } : undefined}>
                      <td>
                        <button type="button" onClick={() => navigate(`/patients/${f.patient_idx}`)}
                          className="font-mono text-xs font-semibold text-gray-700 hover:underline">#{f.patient_idx}</button>
                        <div className="text-[11px] text-gray-400 mt-0.5 max-w-[180px] truncate">
                          {[...f.doctors, ...f.nurses].join(', ') || 'No care team'}
                        </div>
                      </td>
                      <td>
                        <RiskBadge prob={f.dropout_prob ?? 0} />
                        {f.risk_went_down && (
                          <div className="flex items-center gap-1 text-[10px] mt-1" style={{ color: '#2E7D32' }}>
                            <TrendingDown size={10} /> risk has gone down
                          </div>
                        )}
                      </td>
                      <td><UrgencyPill urgency={f.urgency} /></td>
                      <td>
                        <div className="text-sm text-gray-700">{f.requested_by.name}</div>
                        <div className="text-[11px]" style={{ color: f.overdue ? '#C62828' : '#A0AEC0' }}>
                          {ago(f.requested_at)}{f.overdue ? ' · overdue' : ''}
                        </div>
                      </td>
                      <td className="text-xs text-gray-600 max-w-[260px]">
                        {f.note || <span className="text-gray-300">—</span>}
                        {f.notes.length > 0 && (
                          <div className="text-[11px] text-gray-400 mt-0.5">+ {f.notes.length} more note{f.notes.length === 1 ? '' : 's'}</div>
                        )}
                        {tab === 'done' && f.outcome_note && <div className="text-[11px] text-gray-500 mt-0.5">Outcome: {f.outcome_note}</div>}
                      </td>
                      <td>
                        <StatusPill f={f} />
                        {tab === 'done' && <div className="text-[11px] text-gray-400 mt-0.5">{f.done_by?.name} · {ago(f.done_at)}</div>}
                      </td>
                      <td className="text-right whitespace-nowrap">
                        {tab === 'active' && !othersTook && (
                          <div className="inline-flex gap-2">
                            {f.status === 'open' && (
                              <button type="button" disabled={busy === f.id} onClick={() => take(f)}
                                className="text-xs font-semibold px-3 py-1.5 rounded-lg text-white disabled:opacity-50"
                                style={{ background: 'var(--color-primary)' }}>I&rsquo;ll take it</button>
                            )}
                            <button type="button" onClick={() => setDoneFor(f)}
                              className="text-xs font-semibold px-3 py-1.5 rounded-lg border border-gray-200 text-gray-700 hover:bg-gray-50">
                              Mark done
                            </button>
                          </div>
                        )}
                        {tab === 'done' && (
                          <button type="button" onClick={() => navigate(`/patients/${f.patient_idx}`)}
                            aria-label={`Open patient ${f.patient_idx}`} className="text-gray-300 hover:text-gray-500">
                            <ChevronRight size={16} />
                          </button>
                        )}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {doneFor && <DoneDialog followup={doneFor} onClose={() => setDoneFor(null)}
                              onDone={() => { setDoneFor(null); load(); }} />}
    </div>
  );
}
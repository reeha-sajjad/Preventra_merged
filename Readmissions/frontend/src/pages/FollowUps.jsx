import React, { useCallback, useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { CalendarClock, Inbox, Hourglass, AlertTriangle, ChevronRight, TrendingDown } from 'lucide-react';
import { getFollowUps, takeFollowUp } from '../api';
import { readClaims } from '../api/auth';
import RiskBadge from '../components/shared/RiskBadge';
import { DoneDialog, StatusPill, UrgencyPill } from '../components/followups/shared';
import { ago } from '../components/followups/format';

/**
 * Follow-ups: the case managers' work list (hospital admins see it too and can
 * step in). Doctors and nurses ask from a patient's page or in an alert reply;
 * a case manager takes the request, books the patient in the hospital's own
 * system, and marks it done here. See api/followups.py.
 *
 * Shows what booking needs and nothing of the clinical layer.
 */
export default function FollowUps() {
  const navigate = useNavigate();
  const isCaseManager = readClaims()?.role === 'case_manager';
  const [tab, setTab] = useState('active');
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(null);
  const [doneFor, setDoneFor] = useState(null);

  const load = useCallback(() => {
    getFollowUps(tab).then((d) => { setData(d); setError(null); }).catch((e) => setError(e.message));
  }, [tab]);
  useEffect(() => { load(); }, [load]);
  const switchTab = (k) => { if (k !== tab) { setData(null); setTab(k); } };

  const take = async (f) => {
    setBusy(f.id); setError(null);
    try { await takeFollowUp(f.id); } catch (e) { setError(e.message); }
    setBusy(null); load();
  };

  const counts = data?.counts ?? { open: 0, in_progress: 0, overdue: 0 };
  const items = data?.items ?? [];
  const th = 'px-4 py-3 text-left text-xs font-semibold uppercase tracking-wide text-gray-500';
  const td = 'px-4 py-3 text-sm align-top';
  const tile = (Icon, label, value, sub, tone) => (
    <div className="rounded-xl border border-gray-200 bg-white p-4 shadow-sm">
      <div className="flex items-center gap-1.5 text-xs font-semibold uppercase tracking-wide text-gray-500">
        <Icon size={14} className={tone} /> {label}
      </div>
      <div className="mt-1 text-3xl font-bold leading-none text-ns-navy tabular-nums">{value}</div>
      <div className="mt-1 text-xs text-gray-500">{sub}</div>
    </div>
  );

  return (
    <div className="mx-auto max-w-6xl space-y-6 p-4 sm:p-6">
      <header>
        <h1 className="flex items-center gap-2 text-2xl font-bold text-ns-navy">
          <CalendarClock size={24} /> Follow-up requests
        </h1>
        <p className="mt-1 max-w-2xl text-sm text-gray-600">
          Doctors and nurses ask for a patient to be followed up. Take a request, contact the patient,
          book them in the hospital&rsquo;s own system, then mark it done.
        </p>
      </header>

      <div className="grid grid-cols-1 gap-4 sm:grid-cols-3">
        {tile(Inbox, 'Waiting', counts.open, 'nobody has taken these yet', 'text-risk-high')}
        {tile(Hourglass, 'In progress', counts.in_progress, 'taken by a case manager', 'text-amber-600')}
        {tile(AlertTriangle, 'Overdue', counts.overdue, 'open more than 7 days', 'text-amber-700')}
      </div>

      <div className="overflow-hidden rounded-xl border border-gray-200 bg-white shadow-sm">
        <div className="flex items-center gap-1 border-b border-gray-100 px-4 pt-3">
          {[['active', 'To do'], ['done', 'Done (last 30 days)']].map(([k, label]) => (
            <button key={k} type="button" onClick={() => switchTab(k)}
              className={`-mb-px border-b-2 px-3 py-2 text-sm font-medium transition-colors ${
                tab === k ? 'border-ns-navy text-ns-navy' : 'border-transparent text-gray-500 hover:text-gray-700'}`}>
              {label}
            </button>
          ))}
        </div>

        {error && <p className="m-4 rounded-md bg-red-50 px-3 py-2 text-sm text-risk-high">{error}</p>}
        {!data && !error && <div className="m-4 h-32 animate-pulse rounded-lg bg-gray-100" />}

        {data && items.length === 0 && (
          <div className="px-4 py-14 text-center">
            <CalendarClock size={28} className="mx-auto text-risk-low" />
            <div className="mt-3 text-sm font-medium text-gray-700">
              {tab === 'active' ? 'No follow-ups waiting' : 'Nothing finished in the last 30 days'}
            </div>
            <div className="mt-0.5 text-xs text-gray-500">
              New requests from doctors and nurses appear here, with a count on the menu.
            </div>
          </div>
        )}

        {items.length > 0 && (
          <div className="overflow-x-auto">
            <table className="w-full">
              <thead className="bg-gray-50">
                <tr>
                  <th className={th}>Patient</th><th className={th}>Risk now</th><th className={th}>How soon</th>
                  <th className={th}>Asked by</th><th className={th}>Note</th>
                  <th className={th}>{tab === 'active' ? 'Status' : 'Outcome'}</th>
                  <th className={th} aria-label="Actions" />
                </tr>
              </thead>
              <tbody>
                {items.map((f) => {
                  const othersTook = isCaseManager && f.status === 'in_progress' && !f.mine;
                  const team = [f.doctor, ...f.nurses].filter(Boolean).join(', ');
                  return (
                    <tr key={f.id} className={`border-t border-gray-100 ${f.overdue ? 'bg-red-50/40' : ''}`}>
                      <td className={td}>
                        <button type="button" onClick={() => navigate(`/patients/${f.patient_id}`)}
                          className="font-mono text-xs font-semibold text-ns-navy hover:underline">{f.patient_id}</button>
                        <div className="mt-0.5 max-w-[200px] truncate whitespace-nowrap text-xs text-gray-500" title={team}>
                          {team || <span className="text-risk-high">No care team</span>}
                        </div>
                      </td>
                      <td className={td}>
                        {f.current_band ? (
                          <div className="flex items-center gap-2">
                            <RiskBadge riskBand={f.current_band} size="sm" />
                            <span className="text-xs text-gray-500 tabular-nums">{f.current_score}%</span>
                          </div>
                        ) : <span className="text-gray-300">—</span>}
                        {f.risk_went_down && (
                          <div className="mt-1 flex items-center gap-1 text-[11px] text-risk-low">
                            <TrendingDown size={11} /> risk has come down since the request
                          </div>
                        )}
                      </td>
                      <td className={td}><UrgencyPill urgency={f.urgency} /></td>
                      <td className={td}>
                        <div className="whitespace-nowrap text-gray-700">{f.requested_by.name}</div>
                        <div className={`whitespace-nowrap text-[11px] ${f.overdue ? 'text-risk-high' : 'text-gray-400'}`}>
                          {ago(f.requested_at)}{f.overdue ? ' · overdue' : ''}
                        </div>
                        {f.source === 'clinical_alert' && (
                          <div className="whitespace-nowrap text-[11px] text-gray-400">from an alert reply</div>
                        )}
                      </td>
                      <td className={`${td} max-w-[280px] text-xs text-gray-600`}>
                        <div className="line-clamp-3 whitespace-pre-line">{f.note || <span className="text-gray-300">—</span>}</div>
                        {f.notes.length > 0 && (
                          <div className="mt-0.5 text-[11px] text-gray-400">+ {f.notes.length} more note{f.notes.length === 1 ? '' : 's'}</div>
                        )}
                        {tab === 'done' && f.outcome_note && <div className="mt-0.5 text-[11px] text-gray-500">Outcome: {f.outcome_note}</div>}
                      </td>
                      <td className={td}>
                        <StatusPill f={f} />
                        {tab === 'done' && <div className="mt-0.5 text-[11px] text-gray-400">{f.done_by?.name} · {ago(f.done_at)}</div>}
                      </td>
                      <td className={`${td} whitespace-nowrap text-right`}>
                        {tab === 'active' && !othersTook && (
                          <div className="inline-flex gap-2">
                            {f.status === 'open' && (
                              <button type="button" disabled={busy === f.id} onClick={() => take(f)}
                                className="rounded-lg bg-ns-navy px-3 py-1.5 text-xs font-semibold text-white hover:bg-ns-navy/90 disabled:opacity-50">
                                I&rsquo;ll take it
                              </button>
                            )}
                            <button type="button" onClick={() => setDoneFor(f)}
                              className="rounded-lg border border-gray-200 px-3 py-1.5 text-xs font-semibold text-gray-700 hover:bg-gray-50">
                              Mark done
                            </button>
                          </div>
                        )}
                        {tab === 'done' && (
                          <button type="button" onClick={() => navigate(`/patients/${f.patient_id}`)}
                            aria-label={`Open patient ${f.patient_id}`} className="text-gray-300 hover:text-gray-500">
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

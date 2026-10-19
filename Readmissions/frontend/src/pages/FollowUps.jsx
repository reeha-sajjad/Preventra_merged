import React, { useCallback, useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { CalendarClock, Inbox, Hourglass, AlertTriangle, Bell } from 'lucide-react';
import { getFollowUps, takeFollowUp, markFollowUpReportsSeen } from '../api';
import { readClaims } from '../api/auth';
import RiskBadge from '../components/shared/RiskBadge';
import { DoneDialog, StatusPill, UrgencyPill } from '../components/followups/shared';
import { ago } from '../components/followups/format';

/**
 * Follow-ups: the case managers' work list (hospital admins and the superadmin
 * can see it and step in). Doctors and nurses ask from a patient's page or by
 * replying to a clinical alert; a case manager takes the request, books the
 * patient in the hospital's own system, and marks it done here. The same page
 * as GLP-1's (GLP1/Frontend/src/pages/FollowUps.jsx); see api/followups.py.
 *
 * Shows what booking needs and nothing of the clinical layer.
 */
function Tile({ icon, label, value, sub, tone }) {
  const Icon = icon;
  return (
    <div className={`rounded-lg border p-3 sm:p-4 ${tone}`}>
      <div className="flex items-center gap-1.5 text-xs font-medium sm:gap-2 sm:text-sm"><Icon size={15} /> {label}</div>
      <div className="mt-1.5 text-2xl font-bold text-gray-900 sm:mt-2 sm:text-3xl">{value}</div>
      <div className="mt-1 hidden text-xs text-gray-500 sm:block">{sub}</div>
    </div>
  );
}

export default function FollowUps() {
  const navigate = useNavigate();
  const me = readClaims();
  const isCaseManager = me?.role === 'case_manager';
  const [tab, setTab] = useState('active');
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(null);
  const [doneFor, setDoneFor] = useState(null);

  const load = useCallback(() => {
    getFollowUps(tab).then((d) => { setData(d); setError(null); }).catch((e) => setError(e.message));
  }, [tab]);
  useEffect(() => { load(); }, [load]);
  // Opening this page clears pharmacy reports on your requests from the bell.
  useEffect(() => { markFollowUpReportsSeen().catch(() => {}); }, []);
  const switchTab = (k) => { if (k !== tab) { setData(null); setTab(k); } };

  const take = async (f) => {
    setBusy(f.id); setError(null);
    try { await takeFollowUp(f.id); } catch (e) { setError(e.message); }
    setBusy(null); load();
  };

  const counts = data?.counts ?? { open: 0, in_progress: 0, overdue: 0 };
  const items = data?.items ?? [];
  const open = (pid) => navigate(`/patients/${encodeURIComponent(pid)}`);

  return (
    <div className="mx-auto max-w-7xl space-y-6 p-4 sm:p-6">
      <div>
        <h1 className="flex items-center gap-2 text-2xl font-bold text-ns-navy">
          <CalendarClock size={24} className="text-gray-400" /> Follow-up requests
        </h1>
        <p className="mt-1 max-w-2xl text-sm text-gray-500">
          Doctors and nurses ask for a patient to be followed up, and pharmacies report refill problems.
          Take a request, contact the patient,
          book them in the hospital&rsquo;s own system, then mark it done.
        </p>
      </div>

      <div className="grid grid-cols-3 gap-3 sm:gap-4">
        <Tile icon={Inbox} label="Waiting" value={counts.open} sub="nobody has taken these yet"
              tone="border-red-100 bg-red-50/50 text-risk-high" />
        <Tile icon={Hourglass} label="In progress" value={counts.in_progress} sub="taken by a case manager"
              tone="border-yellow-100 bg-yellow-50/40 text-risk-medium" />
        <Tile icon={AlertTriangle} label="Overdue" value={counts.overdue} sub="open more than 7 days"
              tone="border-gray-100 bg-gray-50 text-gray-500" />
      </div>

      <section className="overflow-hidden rounded-xl border border-gray-200 bg-white shadow-sm">
        <div className="flex items-center gap-1 border-b border-gray-100 px-4 pt-3">
          {[['active', 'To do'], ['done', 'Done (last 30 days)']].map(([k, label]) => (
            <button key={k} type="button" onClick={() => switchTab(k)}
              className={`-mb-px border-b-2 px-3 py-2 text-sm font-medium transition-colors ${
                tab === k ? 'border-ns-navy text-ns-navy' : 'border-transparent text-gray-500 hover:text-gray-700'}`}>
              {label}
            </button>
          ))}
        </div>

        {error && <p className="m-4 rounded-md border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700">{error}</p>}
        {!data && !error && <div className="m-4 h-32 animate-pulse rounded-lg bg-gray-100" />}

        {data && items.length === 0 && (
          <div className="px-4 py-14 text-center">
            <div className="mx-auto flex h-10 w-10 items-center justify-center rounded-xl bg-green-50 text-risk-low">
              <CalendarClock size={18} />
            </div>
            <div className="mt-3 text-sm font-medium text-gray-700">
              {tab === 'active' ? 'No follow-ups waiting' : 'Nothing finished in the last 30 days'}
            </div>
            <div className="mt-0.5 text-xs text-gray-400">New requests from doctors and nurses appear here.</div>
          </div>
        )}

        {items.length > 0 && (
          <div className="overflow-x-auto">
            <table className="w-full text-left text-sm">
              <thead className="bg-gray-50 text-xs uppercase tracking-wide text-gray-500">
                <tr>
                  <th className="px-5 py-2.5 font-semibold">Patient</th>
                  <th className="px-5 py-2.5 font-semibold">Risk</th>
                  <th className="px-5 py-2.5 font-semibold">Request</th>
                  <th className="px-5 py-2.5 font-semibold">Note</th>
                  <th className="px-5 py-2.5 font-semibold">{tab === 'active' ? 'Status' : 'Outcome'}</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-100">
                {items.map((f) => {
                  const othersTook = isCaseManager && f.status === 'in_progress' && !f.mine;
                  return (
                    <tr key={f.id} className={f.overdue ? 'bg-red-50/40' : undefined}>
                      <td className="px-5 py-3 align-top">
                        <button type="button" onClick={() => open(f.patient_id)}
                          className="font-semibold text-gray-800 hover:underline">{f.patient_id}</button>
                        <div className="mt-0.5 max-w-[180px] truncate text-[11px] text-gray-400">
                          {[...f.doctors, ...f.nurses].join(', ') || 'No care team'}
                        </div>
                      </td>
                      <td className="whitespace-nowrap px-5 py-3 align-top">
                        {f.risk_score != null ? (
                          <><span className="mr-2 font-bold text-gray-900">{f.risk_score.toFixed(1)}%</span>
                            <RiskBadge riskBand={f.risk_band} size="sm" /></>
                        ) : <span className="text-gray-300">—</span>}
                      </td>
                      <td className="whitespace-nowrap px-5 py-3 align-top">
                        <UrgencyPill urgency={f.urgency} />
                        <div className="mt-1 text-gray-700">{f.requested_by.pharmacy || f.requested_by.name}</div>
                        {/* Reported by a pharmacy: what it saw at the counter. */}
                        {f.pharmacy_reports?.length > 0 && (
                          <div className="mt-0.5 flex flex-wrap gap-1">
                            {f.pharmacy_reports.map((p, i) => (
                              <span key={i} className="rounded bg-purple-50 px-1.5 py-0.5 text-[10px] font-semibold text-purple-800">
                                Pharmacy · {p.problem_label}
                              </span>
                            ))}
                          </div>
                        )}
                        <div className={`text-[11px] ${f.overdue ? 'text-risk-high' : 'text-gray-400'}`}>
                          {ago(f.requested_at)}{f.overdue ? ' · overdue' : ''}
                        </div>
                        {f.source === 'clinical_alert' && (
                          <div className="mt-0.5 inline-flex items-center gap-1 text-[11px] text-gray-400">
                            <Bell size={10} /> from an alert reply
                          </div>
                        )}
                      </td>
                      <td className="min-w-[220px] max-w-[320px] px-5 py-3 align-top text-xs text-gray-600">
                        {f.note || <span className="text-gray-300">—</span>}
                        {f.notes.length > 0 && (
                          <div className="mt-0.5 text-[11px] text-gray-400">+ {f.notes.length} more note{f.notes.length === 1 ? '' : 's'}</div>
                        )}
                        {tab === 'done' && f.outcome_note && <div className="mt-0.5 text-[11px] text-gray-500">Outcome: {f.outcome_note}</div>}
                      </td>
                      <td className="px-5 py-3 align-top">
                        <StatusPill f={f} />
                        {tab === 'done' && <div className="mt-0.5 text-[11px] text-gray-400">{f.done_by?.name} · {ago(f.done_at)}</div>}
                        {tab === 'active' && !othersTook && (
                          <div className="mt-2 flex flex-wrap gap-2">
                            {f.status === 'open' && (
                              <button type="button" disabled={busy === f.id} onClick={() => take(f)}
                                className="whitespace-nowrap rounded-md bg-ns-navy px-3 py-1.5 text-xs font-semibold text-white hover:bg-ns-navy/90 disabled:opacity-50">
                                I&rsquo;ll take it
                              </button>
                            )}
                            <button type="button" onClick={() => setDoneFor(f)}
                              className="whitespace-nowrap rounded-md border border-gray-300 px-3 py-1.5 text-xs font-semibold text-gray-700 hover:bg-gray-50">
                              Mark done
                            </button>
                          </div>
                        )}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </section>

      {doneFor && <DoneDialog followup={doneFor} onClose={() => setDoneFor(null)}
                              onDone={() => { setDoneFor(null); load(); }} />}
    </div>
  );
}
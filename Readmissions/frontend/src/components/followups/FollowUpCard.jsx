import React, { useCallback, useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { CalendarClock, Loader2, Send, MessageSquarePlus, ArrowRight, Bell } from 'lucide-react';
import { addFollowUpNote, getPatientFollowUp, requestFollowUp, takeFollowUp } from '../../api';
import { readClaims } from '../../api/auth';
import { can, FOLLOWUP_HANDLE_ROLES, FOLLOWUP_REQUEST_ROLES } from '../../roles';
import { DoneDialog, StatusPill, UrgencyPill } from './shared';
import { ago } from './format';

/**
 * "Follow-up" on a patient's page - the same card as GLP-1's
 * (GLP1/Frontend/src/components/followups/FollowUpCard.jsx).
 *
 *   doctor, nurse        request one (urgent / routine + note), or see the open
 *                        one and add a note to it; see the last outcome
 *   case manager, admin  see it, take it, mark it done
 *
 * Only one request is open per patient; the server enforces it
 * (api/followups.py). A doctor's reply to a clinical alert can open one too.
 */
export default function FollowUpCard({ patientId }) {
  const asks = can(FOLLOWUP_REQUEST_ROLES);
  const handles = can(FOLLOWUP_HANDLE_ROLES);
  const me = readClaims();
  const isCaseManager = me?.role === 'case_manager';
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);
  const [form, setForm] = useState(null);          // { urgency, note } while asking
  const [noteText, setNoteText] = useState('');
  const [busy, setBusy] = useState(false);
  const [doneFor, setDoneFor] = useState(null);

  const load = useCallback(() => {
    getPatientFollowUp(patientId).then(setData).catch((e) => setError(e.message));
  }, [patientId]);
  useEffect(() => { if (asks || handles) load(); }, [load, asks, handles]);

  if (!asks && !handles) return null;

  const run = async (fn) => {
    setBusy(true); setError(null);
    try { await fn(); } catch (e) { setError(e.message); } finally { setBusy(false); load(); }
  };
  const send = (e) => {
    e.preventDefault();
    run(async () => {
      await requestFollowUp(patientId, { urgency: form.urgency, note: form.note.trim() || undefined });
      setForm(null);
    });
  };
  const addNote = (e) => {
    e.preventDefault();
    if (!noteText.trim()) return;
    run(async () => { await addFollowUpNote(data.active.id, noteText.trim()); setNoteText(''); });
  };

  const active = data?.active;
  const recent = data?.recent;
  const othersTook = isCaseManager && active?.status === 'in_progress' && active.taken_by?.id !== me?.sub;

  return (
    <div className="rounded-xl border border-gray-200 bg-white p-5 shadow-sm">
      <div className="mb-3 flex flex-wrap items-start justify-between gap-2 border-b pb-3">
        <div>
          <h2 className="flex items-center gap-2 text-lg font-semibold text-ns-navy">
            <CalendarClock size={20} className="text-gray-400" /> Follow-up
          </h2>
          <p className="text-xs text-gray-500">
            {asks ? 'Ask a case manager to contact this patient and book them in.'
              : 'Requests from this patient’s doctors and nurses.'}
          </p>
        </div>
        {handles && (
          <Link to="/follow-ups" className="inline-flex items-center gap-1 text-sm font-semibold text-ns-navy hover:underline">
            All follow-ups <ArrowRight size={14} />
          </Link>
        )}
      </div>

      {!data && !error && <div className="h-16 animate-pulse rounded-lg bg-gray-100" />}

      {/* The open request */}
      {active && (
        <div className={`rounded-lg border p-4 ${active.overdue ? 'border-red-200 bg-red-50/40' : 'border-gray-200 bg-gray-50'}`}>
          <div className="flex flex-wrap items-center gap-2">
            <UrgencyPill urgency={active.urgency} />
            <StatusPill f={active} />
            {active.overdue && <span className="text-[11px] font-semibold text-risk-high">Open {active.days_open} days</span>}
          </div>
          <p className="mt-2 text-sm text-gray-700">
            <b>{active.requested_by.name}</b> asked for a follow-up {ago(active.requested_at)}
            {active.source === 'clinical_alert' && (
              <span className="ml-1 inline-flex items-center gap-1 text-xs text-gray-500">
                <Bell size={11} /> from their reply to a clinical alert
              </span>
            )}.
          </p>
          {active.note && <p className="mt-1 text-sm text-gray-600">&ldquo;{active.note}&rdquo;</p>}
          {active.notes.length > 0 && (
            <ul className="mt-3 space-y-1.5 border-t border-gray-200 pt-3">
              {active.notes.map((n, i) => (
                <li key={i} className="text-xs text-gray-600">
                  <b className="text-gray-700">{n.by.name}</b> <span className="text-gray-400">· {ago(n.at)}</span><br />{n.text}
                </li>
              ))}
            </ul>
          )}

          {asks && (
            <form onSubmit={addNote} className="mt-3 flex gap-2">
              <input value={noteText} onChange={(e) => setNoteText(e.target.value)} maxLength={500}
                placeholder="Add a note for the case manager" aria-label="Add a note"
                className="min-w-0 flex-1 rounded-md border border-gray-300 px-3 py-2 text-sm focus:border-ns-navy focus:outline-none focus:ring-1 focus:ring-ns-navy" />
              <button type="submit" disabled={busy || !noteText.trim()}
                className="inline-flex items-center gap-1.5 rounded-md border border-gray-300 px-3 py-2 text-sm font-semibold text-gray-700 hover:bg-gray-50 disabled:opacity-40">
                <MessageSquarePlus size={14} /> Add
              </button>
            </form>
          )}
          {handles && othersTook && (
            <p className="mt-3 text-xs text-gray-500">{active.taken_by?.name} is handling this.</p>
          )}
          {handles && !othersTook && (
            <div className="mt-3 flex flex-wrap gap-2">
              {active.status === 'open' && (
                <button type="button" disabled={busy} onClick={() => run(() => takeFollowUp(active.id))}
                  className="rounded-md bg-ns-navy px-4 py-2 text-sm font-semibold text-white hover:bg-ns-navy/90 disabled:opacity-50">
                  I&rsquo;ll take it
                </button>
              )}
              <button type="button" disabled={busy} onClick={() => setDoneFor(active)}
                className="rounded-md border border-gray-300 px-4 py-2 text-sm font-semibold text-gray-700 hover:bg-gray-50 disabled:opacity-50">
                Mark done
              </button>
            </div>
          )}
        </div>
      )}

      {/* The last finished one */}
      {!active && recent && (
        <div className="mb-3 rounded-lg border border-gray-100 px-4 py-3 text-sm text-gray-600">
          <div className="flex flex-wrap items-center gap-2"><StatusPill f={recent} />
            <span className="text-xs text-gray-400">by {recent.done_by?.name} · {ago(recent.done_at)}</span></div>
          {recent.outcome_note && <p className="mt-1.5">&ldquo;{recent.outcome_note}&rdquo;</p>}
        </div>
      )}

      {/* Asking for one */}
      {data && !active && asks && !form && (
        <button type="button" onClick={() => setForm({ urgency: 'routine', note: '' })}
          className="inline-flex items-center gap-2 rounded-md bg-ns-navy px-4 py-2 text-sm font-semibold text-white hover:bg-ns-navy/90">
          <CalendarClock size={15} /> Request follow-up
        </button>
      )}
      {form && (
        <form onSubmit={send} className="space-y-3 rounded-lg border border-gray-200 p-4">
          <div>
            <div className="mb-1.5 text-xs font-semibold uppercase tracking-wide text-gray-500">How soon</div>
            <div className="inline-flex rounded-md border border-gray-300 p-0.5" role="radiogroup" aria-label="How soon">
              {[['urgent', 'Urgent · within days'], ['routine', 'Routine · within weeks']].map(([k, label]) => (
                <button key={k} type="button" role="radio" aria-checked={form.urgency === k}
                  onClick={() => setForm({ ...form, urgency: k })}
                  className={`rounded px-3 py-1.5 text-sm font-medium transition-colors ${
                    form.urgency === k ? (k === 'urgent' ? 'bg-risk-high text-white' : 'bg-ns-navy text-white') : 'text-gray-600'}`}>
                  {label}
                </button>
              ))}
            </div>
          </div>
          <label className="block">
            <span className="text-xs font-semibold uppercase tracking-wide text-gray-500">Note (optional)</span>
            <textarea value={form.note} onChange={(e) => setForm({ ...form, note: e.target.value })} rows={2} maxLength={500}
              placeholder="e.g. check fluid status, review within a week of discharge"
              className="mt-1.5 w-full rounded-md border border-gray-300 px-3 py-2 text-sm focus:border-ns-navy focus:outline-none focus:ring-1 focus:ring-ns-navy" />
          </label>
          <div className="flex flex-wrap items-center justify-between gap-2">
            <span className="text-[11px] text-gray-500">Goes to your hospital&rsquo;s case managers.</span>
            <div className="flex gap-2">
              <button type="button" onClick={() => setForm(null)}
                className="rounded-md border border-gray-300 px-3 py-2 text-sm text-gray-600 hover:bg-gray-50">Cancel</button>
              <button type="submit" disabled={busy}
                className="inline-flex items-center gap-2 rounded-md bg-ns-navy px-4 py-2 text-sm font-semibold text-white hover:bg-ns-navy/90 disabled:opacity-50">
                {busy ? <Loader2 size={14} className="animate-spin" /> : <Send size={14} />} Send request
              </button>
            </div>
          </div>
        </form>
      )}
      {data && !active && !asks && !recent && (
        <p className="text-sm text-gray-400">No follow-up has been requested for this patient.</p>
      )}

      {error && <p className="mt-3 rounded-md border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700">{error}</p>}
      {doneFor && <DoneDialog followup={doneFor} onClose={() => setDoneFor(null)}
                              onDone={() => { setDoneFor(null); load(); }} />}
    </div>
  );
}
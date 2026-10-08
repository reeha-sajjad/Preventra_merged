import { useCallback, useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { CalendarClock, Loader2, Send, MessageSquarePlus, ArrowRight } from 'lucide-react';
import { api } from '../../data/api';
import { useRole } from '../../context/RoleContext';
import { useAuth } from '../../context/AuthContext';
import { DoneDialog, StatusPill, UrgencyPill } from './shared';
import { ago } from './format';

/**
 * "Follow-up" on a patient's page.
 *
 *   doctor, nurse        request one (urgent / routine + note), or see the open
 *                        one and add a note to it; see the last outcome
 *   case manager, admin  see it, take it, mark it done
 *
 * Only one request is open per patient; the server enforces it
 * (Backend/core/followups.py), this card just shows whichever is open.
 */
export default function FollowUpCard({ patientIdx }) {
  const { isCareTeam, handlesFollowUps, isCaseManager } = useRole();
  const { user } = useAuth();
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);
  const [form, setForm] = useState(null);          // { urgency, note } while asking
  const [noteText, setNoteText] = useState('');
  const [busy, setBusy] = useState(false);
  const [doneFor, setDoneFor] = useState(null);

  const load = useCallback(() => {
    api.getPatientFollowUp(patientIdx).then(setData).catch((e) => setError(e.message));
  }, [patientIdx]);
  useEffect(() => { load(); }, [load]);

  if (!isCareTeam && !handlesFollowUps) return null;

  const run = async (fn) => {
    setBusy(true); setError(null);
    try { await fn(); load(); } catch (e) { setError(e.message); load(); } finally { setBusy(false); }
  };

  const send = (e) => {
    e.preventDefault();
    run(async () => {
      await api.requestFollowUp(patientIdx, { urgency: form.urgency, note: form.note.trim() || undefined });
      setForm(null);
    });
  };
  const addNote = (e) => {
    e.preventDefault();
    if (!noteText.trim()) return;
    run(async () => { await api.addFollowUpNote(data.active.id, noteText.trim()); setNoteText(''); });
  };

  const active = data?.active;
  const recent = data?.recent;

  return (
    <div className="card p-5 md:p-6">
      <div className="flex flex-wrap items-start justify-between gap-2 mb-4">
        <div className="flex items-center gap-3">
          <div className="w-9 h-9 rounded-xl flex items-center justify-center" style={{ background: '#EBF4FF' }}>
            <CalendarClock size={16} style={{ color: 'var(--color-primary)' }} />
          </div>
          <div>
            <h2 className="text-base font-semibold text-gray-800" style={{ fontFamily: 'DM Serif Display, serif' }}>Follow-up</h2>
            <p className="text-xs text-gray-400">
              {isCareTeam ? 'Ask a case manager to contact this patient and book them in.'
                : 'Requests from this patient’s doctors and nurses.'}
            </p>
          </div>
        </div>
        {handlesFollowUps && (
          <Link to="/follow-ups" className="inline-flex items-center gap-1 text-xs font-semibold" style={{ color: 'var(--color-primary)' }}>
            All follow-ups <ArrowRight size={13} />
          </Link>
        )}
      </div>

      {!data && !error && <div className="h-16 rounded-lg bg-gray-100 animate-pulse" />}

      {/* The open request */}
      {active && (
        <div className="rounded-xl border p-4" style={{ borderColor: active.overdue ? '#FFCDD2' : '#E2E8F0',
                                                       background: active.overdue ? '#FFF8F8' : '#FAFBFD' }}>
          <div className="flex flex-wrap items-center gap-2">
            <UrgencyPill urgency={active.urgency} />
            <StatusPill f={active} />
            {active.overdue && <span className="text-[11px] font-semibold" style={{ color: '#C62828' }}>Open {active.days_open} days</span>}
          </div>
          <p className="text-sm text-gray-700 mt-2">
            <b>{active.requested_by.name}</b> asked for a follow-up {ago(active.requested_at)}.
          </p>
          {active.note && <p className="text-sm text-gray-600 mt-1">&ldquo;{active.note}&rdquo;</p>}
          {active.notes.length > 0 && (
            <ul className="mt-3 space-y-1.5 border-t border-gray-100 pt-3">
              {active.notes.map((n, i) => (
                <li key={i} className="text-xs text-gray-600">
                  <b className="text-gray-700">{n.by.name}</b> <span className="text-gray-400">· {ago(n.at)}</span><br />{n.text}
                </li>
              ))}
            </ul>
          )}

          {isCareTeam && (
            <form onSubmit={addNote} className="mt-3 flex gap-2">
              <input value={noteText} onChange={(e) => setNoteText(e.target.value)} maxLength={500}
                placeholder="Add a note for the case manager" aria-label="Add a note"
                className="flex-1 min-w-0 text-sm rounded-lg border border-gray-200 px-3 py-2 focus:outline-none focus:ring-1 focus:ring-blue-400" />
              <button type="submit" disabled={busy || !noteText.trim()}
                className="inline-flex items-center gap-1.5 text-sm font-semibold px-3 py-2 rounded-lg border border-gray-200 text-gray-700 hover:bg-gray-50 disabled:opacity-40">
                <MessageSquarePlus size={14} /> Add
              </button>
            </form>
          )}
          {handlesFollowUps && isCaseManager && active.status === 'in_progress' && active.taken_by?.id !== user?.id && (
            <p className="mt-3 text-xs text-gray-500">{active.taken_by?.name} is handling this.</p>
          )}
          {handlesFollowUps && !(isCaseManager && active.status === 'in_progress' && active.taken_by?.id !== user?.id) && (
            <div className="mt-3 flex flex-wrap gap-2">
              {active.status === 'open' && (
                <button type="button" disabled={busy} onClick={() => run(() => api.takeFollowUp(active.id))}
                  className="text-sm font-semibold px-4 py-2 rounded-lg text-white disabled:opacity-50"
                  style={{ background: 'var(--color-primary)' }}>I&rsquo;ll take it</button>
              )}
              <button type="button" disabled={busy} onClick={() => setDoneFor(active)}
                className="text-sm font-semibold px-4 py-2 rounded-lg border border-gray-200 text-gray-700 hover:bg-gray-50 disabled:opacity-50">
                Mark done
              </button>
            </div>
          )}
        </div>
      )}

      {/* The last finished one */}
      {!active && recent && (
        <div className="rounded-xl border border-gray-100 px-4 py-3 mb-3 text-sm text-gray-600">
          <div className="flex flex-wrap items-center gap-2"><StatusPill f={recent} />
            <span className="text-xs text-gray-400">by {recent.done_by?.name} · {ago(recent.done_at)}</span></div>
          {recent.outcome_note && <p className="mt-1.5">&ldquo;{recent.outcome_note}&rdquo;</p>}
        </div>
      )}

      {/* Asking for one */}
      {data && !active && isCareTeam && !form && (
        <button type="button" onClick={() => setForm({ urgency: 'routine', note: '' })}
          className="inline-flex items-center gap-2 text-sm font-semibold px-4 py-2 rounded-lg text-white"
          style={{ background: 'var(--color-primary)' }}>
          <CalendarClock size={15} /> Request follow-up
        </button>
      )}
      {form && (
        <form onSubmit={send} className="rounded-xl border border-gray-200 p-4 space-y-3">
          <div>
            <div className="text-xs font-semibold uppercase tracking-wider text-gray-400 mb-1.5">How soon</div>
            <div className="inline-flex rounded-lg border border-gray-200 p-0.5" role="radiogroup" aria-label="How soon">
              {[['urgent', 'Urgent · within days'], ['routine', 'Routine · within weeks']].map(([k, label]) => (
                <button key={k} type="button" role="radio" aria-checked={form.urgency === k}
                  onClick={() => setForm({ ...form, urgency: k })}
                  className="text-sm px-3 py-1.5 rounded-md font-medium transition-colors"
                  style={form.urgency === k
                    ? { background: k === 'urgent' ? '#C62828' : 'var(--color-primary)', color: 'white' }
                    : { color: '#4A5568' }}>
                  {label}
                </button>
              ))}
            </div>
          </div>
          <label className="block">
            <span className="text-xs font-semibold uppercase tracking-wider text-gray-400">Note (optional)</span>
            <textarea value={form.note} onChange={(e) => setForm({ ...form, note: e.target.value })} rows={2} maxLength={500}
              placeholder="e.g. check side effects, consider a weekly drug"
              className="mt-1.5 w-full text-sm rounded-lg border border-gray-200 px-3 py-2 focus:outline-none focus:ring-1 focus:ring-blue-400" />
          </label>
          <div className="flex items-center justify-between gap-2">
            <span className="text-[11px] text-gray-400">Goes to your hospital&rsquo;s case managers.</span>
            <div className="flex gap-2">
              <button type="button" onClick={() => setForm(null)}
                className="text-sm px-3 py-2 rounded-lg border border-gray-200 text-gray-600 hover:bg-gray-50">Cancel</button>
              <button type="submit" disabled={busy}
                className="inline-flex items-center gap-2 text-sm font-semibold px-4 py-2 rounded-lg text-white disabled:opacity-50"
                style={{ background: 'var(--color-primary)' }}>
                {busy ? <Loader2 size={14} className="animate-spin" /> : <Send size={14} />} Send request
              </button>
            </div>
          </div>
        </form>
      )}
      {data && !active && !isCareTeam && !recent && (
        <p className="text-sm text-gray-400">No follow-up has been requested for this patient.</p>
      )}

      {error && <p className="mt-3 rounded-md px-3 py-2 text-sm" style={{ background: '#FFEBEE', color: '#C62828' }}>{error}</p>}
      {doneFor && <DoneDialog followup={doneFor} onClose={() => setDoneFor(null)}
                              onDone={() => { setDoneFor(null); load(); }} />}
    </div>
  );
}
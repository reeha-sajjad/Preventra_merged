import { useCallback, useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { Send, Loader2, Receipt, ChevronRight } from 'lucide-react';
import { api } from '../../data/api';

/**
 * Billing flags between a hospital and a patient's insurer
 * (Backend/core/insurer_alerts.py). Readmissions has the same two pieces.
 *
 *   PatientFlags  - on the Insurance card: billing staff send a flag, and
 *                   everyone who can see it sees the insurer's reply.
 *   InsurerInbox  - on an insurer's Members page: open flags, with a reply.
 *
 * Only what billing needs travels (45 CFR 164.502(b), minimum necessary), so
 * the form asks people to leave clinical detail out of the note.
 */

const STATUS_STYLE = {
  sent:       { bg: '#F1F5F9', fg: '#475569' },
  received:   { bg: '#E3F2FD', fg: '#1B4F8A' },
  approved:   { bg: '#E8F5E9', fg: '#2E7D32' },
  needs_info: { bg: '#FFF3E0', fg: '#B45309' },
};

const when = (iso) => new Date(iso).toLocaleDateString(undefined, { day: 'numeric', month: 'short' });

function Status({ flag }) {
  const s = STATUS_STYLE[flag.status] || STATUS_STYLE.sent;
  return (
    <span className="inline-flex px-2 py-0.5 rounded-full text-[11px] font-semibold whitespace-nowrap"
          style={{ background: s.bg, color: s.fg }}>{flag.status_label}</span>
  );
}

function FlagLine({ flag, showWho }) {
  return (
    <li className="py-2.5">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="text-sm font-medium text-gray-800">{flag.kind_label}</div>
          <div className="text-xs text-gray-600 mt-0.5 break-words">{flag.note}</div>
          {showWho && <div className="text-[11px] text-gray-400 mt-0.5">{flag.sent_by} · {when(flag.sent_at)}</div>}
        </div>
        <Status flag={flag} />
      </div>
      {flag.replied_at && (
        <div className="mt-1.5 text-xs rounded-lg px-2.5 py-1.5" style={{ background: '#F7FAFC' }}>
          <span className="font-semibold text-gray-700">{flag.insurer}:</span>{' '}
          <span className="text-gray-600">{flag.reply_note || flag.status_label}</span>
          <span className="text-gray-400"> · {when(flag.replied_at)}</span>
        </div>
      )}
    </li>
  );
}

export function PatientFlags({ patientIdx, insurerName }) {
  const [data, setData] = useState(null);
  const [open, setOpen] = useState(false);
  const [kind, setKind] = useState('billing_query');
  const [note, setNote] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);

  const load = useCallback(() => {
    api.getInsurerFlagsFor(patientIdx).then(setData).catch(() => setData(null));
  }, [patientIdx]);
  useEffect(() => { load(); }, [load]);
  if (!data || (!data.can_send && data.flags.length === 0)) return null;

  const send = async (e) => {
    e.preventDefault();
    setBusy(true); setError(null);
    try { await api.sendInsurerFlag(patientIdx, { kind, note }); setNote(''); setOpen(false); load(); }
    catch (err) { setError(err.message); }
    finally { setBusy(false); }
  };

  return (
    <div className="mt-4 pt-4 border-t border-gray-100">
      <div className="flex items-center justify-between gap-3">
        <h3 className="text-sm font-semibold text-gray-700">Billing flags</h3>
        {data.can_send && !open && (
          <button type="button" onClick={() => setOpen(true)}
            className="inline-flex items-center gap-1 text-xs font-semibold" style={{ color: 'var(--color-primary)' }}>
            <Send size={11} /> Send to {insurerName || 'insurer'}
          </button>
        )}
      </div>

      {open && (
        <form onSubmit={send} className="mt-2 space-y-2">
          <label className="block text-sm text-gray-700">About
            <select value={kind} onChange={(e) => setKind(e.target.value)}
              className="mt-1 w-full text-sm rounded-lg border border-gray-200 px-3 py-2 bg-white focus:outline-none focus:ring-2 focus:ring-blue-300">
              {data.kinds.map((k) => <option key={k.key} value={k.key}>{k.label}</option>)}
            </select>
          </label>
          <label className="block text-sm text-gray-700">Note
            <textarea value={note} onChange={(e) => setNote(e.target.value)} rows={2} maxLength={500}
              placeholder="e.g. Claim for March refill was denied - please check coverage"
              className="mt-1 w-full text-sm rounded-lg border border-gray-200 px-3 py-2 focus:outline-none focus:ring-2 focus:ring-blue-300" />
          </label>
          <p className="text-xs text-gray-500">Only what billing needs. Leave out diagnoses and other clinical detail.</p>
          {error && <p role="alert" className="text-sm" style={{ color: '#C62828' }}>{error}</p>}
          <div className="flex gap-2">
            <button type="submit" disabled={busy}
              className="inline-flex items-center gap-1.5 text-sm font-semibold px-3 py-1.5 rounded-lg text-white disabled:opacity-50"
              style={{ background: 'var(--color-primary)' }}>{busy && <Loader2 size={13} className="animate-spin" />} Send</button>
            <button type="button" onClick={() => { setOpen(false); setError(null); }}
              className="text-sm px-3 py-1.5 rounded-lg border border-gray-200 text-gray-600">Cancel</button>
          </div>
        </form>
      )}

      {data.flags.length === 0 ? (
        !open && <p className="text-xs text-gray-400 mt-1">None sent.</p>
      ) : (
        <ul className="divide-y divide-gray-100">
          {data.flags.map((f) => <FlagLine key={f.id} flag={f} showWho />)}
        </ul>
      )}
    </div>
  );
}

export function InsurerInbox() {
  const navigate = useNavigate();
  const [data, setData] = useState(null);
  const [replying, setReplying] = useState(null);
  const [status, setStatus] = useState('received');
  const [note, setNote] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);

  const load = useCallback(() => { api.getInsurerFlags('open').then(setData).catch(() => setData(null)); }, []);
  useEffect(() => { load(); }, [load]);
  // Only when something is open: the bell already says when one arrives.
  if (!data || data.flags.length === 0) return null;

  const send = async (e) => {
    e.preventDefault();
    setBusy(true); setError(null);
    try {
      await api.replyInsurerFlag(replying, { status, note });
      setReplying(null); setNote(''); load();
      window.dispatchEvent(new Event('preventra:bell-refresh'));
    } catch (err) { setError(err.message); }
    finally { setBusy(false); }
  };

  return (
    <section className="card overflow-hidden" style={{ padding: 0 }}>
      <div className="px-5 py-4 border-b border-gray-100">
        <h2 className="text-base font-semibold text-gray-800 flex items-center gap-2" style={{ fontFamily: 'DM Serif Display, serif' }}>
          <Receipt size={15} className="text-gray-400" /> From hospitals
        </h2>
        <p className="text-xs text-gray-400 mt-0.5">Billing questions, prior authorisations and coverage checks waiting on you.</p>
      </div>
      {data.flags.length === 0 ? (
        <p className="p-8 text-center text-sm text-gray-400">Nothing open.</p>
      ) : (
        <ul className="divide-y divide-gray-100">
          {data.flags.map((f) => (
            <li key={f.id} className="px-5 py-3">
              <div className="flex flex-wrap items-start justify-between gap-3">
                <div className="min-w-0">
                  <div className="flex flex-wrap items-baseline gap-x-2">
                    <span className="font-mono text-xs font-semibold text-gray-800">{f.member_id || `Patient #${f.patient_idx}`}</span>
                    <span className="text-sm font-medium text-gray-800">{f.kind_label}</span>
                  </div>
                  <div className="text-xs text-gray-600 mt-0.5 break-words">{f.note}</div>
                  <div className="text-[11px] text-gray-400 mt-0.5">{f.hospital} · {f.sent_by} · {when(f.sent_at)}</div>
                  {f.reply_note && <div className="text-[11px] text-gray-500 mt-0.5">Your reply: {f.reply_note}</div>}
                </div>
                <div className="flex items-center gap-2">
                  <Status flag={f} />
                  {replying !== f.id && (
                    <button type="button" onClick={() => { setReplying(f.id); setStatus(f.status === 'sent' ? 'received' : f.status); setNote(''); setError(null); }}
                      className="text-xs font-semibold" style={{ color: 'var(--color-primary)' }}>Reply</button>
                  )}
                  <button type="button" aria-label="Open member" onClick={() => navigate(`/patients/${f.patient_idx}`)}
                    className="text-gray-300 hover:text-gray-500"><ChevronRight size={15} /></button>
                </div>
              </div>
              {replying === f.id && (
                <form onSubmit={send} className="mt-2 flex flex-wrap items-end gap-2">
                  <select value={status} onChange={(e) => setStatus(e.target.value)} aria-label="Answer"
                    className="text-sm rounded-lg border border-gray-200 px-2 py-1.5 bg-white">
                    {data.statuses.map((s) => <option key={s.key} value={s.key}>{s.label}</option>)}
                  </select>
                  <input value={note} onChange={(e) => setNote(e.target.value)} maxLength={500} aria-label="Note"
                    placeholder="Note for the hospital (optional)"
                    className="flex-1 min-w-[180px] text-sm rounded-lg border border-gray-200 px-3 py-1.5" />
                  <button type="submit" disabled={busy}
                    className="inline-flex items-center gap-1.5 text-sm font-semibold px-3 py-1.5 rounded-lg text-white disabled:opacity-50"
                    style={{ background: 'var(--color-primary)' }}>{busy && <Loader2 size={13} className="animate-spin" />} Send</button>
                  <button type="button" onClick={() => setReplying(null)}
                    className="text-sm px-3 py-1.5 rounded-lg border border-gray-200 text-gray-600">Cancel</button>
                  {error && <p role="alert" className="w-full text-sm" style={{ color: '#C62828' }}>{error}</p>}
                </form>
              )}
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
import { useCallback, useEffect, useRef, useState } from 'react';
import { Link, useLocation, useNavigate } from 'react-router-dom';
import { Bell, TrendingUp, UserPlus, AlertTriangle, ChevronRight, CheckCheck, CalendarClock, CalendarCheck, Forward, Pill, Receipt, Siren } from 'lucide-react';
import { api } from '../../data/api';
import { RiskBadge } from '../shared';

const REFRESH_MS = 60_000;

// One line per reason a patient is in the bell (Backend/core/notifications.py).
const KINDS = {
  followup_handed: { icon: Forward, color: '#1B4F8A', label: () => 'handed to you', text: (i) =>
    `${i.handed_by} handed you ${i.urgency === 'urgent' ? 'an urgent' : 'a'} follow-up${i.overdue ? ' · overdue' : ''}` },
  followup_report: { icon: Pill, color: '#B45309', label: (n) => `pharmacy report${n === 1 ? '' : 's'}`, text: (i) =>
    `${i.requested_by}: ${i.problem_label} · on a follow-up you hold` },
  followup_new: { icon: CalendarClock, color: '#C62828', label: (n) => `follow-up request${n === 1 ? '' : 's'}`, text: (i) =>
    (i.problem_label ? `${i.requested_by}: ${i.problem_label}` :
      `${i.urgency === 'urgent' ? 'Urgent f' : 'F'}ollow-up requested by ${i.requested_by}`) + (i.overdue ? ' · overdue' : '') },
  report_done: { icon: CalendarCheck, color: '#2E7D32', label: (n) => `report${n === 1 ? '' : 's'} done`, text: (i) =>
    `${i.problem_label ? `${i.problem_label} · ` : ''}done: ${i.outcome_label}` },
  followup_done: { icon: CalendarCheck, color: '#2E7D32', label: (n) => `follow-up${n === 1 ? '' : 's'} done`, text: (i) =>
    `Follow-up done · ${i.followup?.outcome_label} (${i.followup?.done_by})` },
  risk_up:    { icon: TrendingUp,    color: '#C62828', text: (i) =>
    `Risk up from ${Math.round((i.previous_prob ?? 0) * 100)}% since you last looked`, label: () => 'risk up' },
  new:        { icon: UserPlus,      color: '#1B4F8A', text: () => 'New on your care team', label: () => 'new' },
  unreviewed: { icon: AlertTriangle, color: '#EF6C00', text: () => 'Critical risk, not reviewed yet', label: () => 'not reviewed' },
  // An insurer's bell (Backend/core/insurer_alerts.py).
  insurer_flag: { icon: Receipt, color: '#1B4F8A', label: (n) => `hospital flag${n === 1 ? '' : 's'}`, text: (i) =>
    `${i.hospital}: ${i.kind_label} · from ${i.requested_by}` },
  turned_critical: { icon: Siren, color: '#C62828', label: () => 'turned critical', text: (i) =>
    i.previous_prob == null ? 'New member, already at critical risk'
      : `Turned critical · was ${Math.round(i.previous_prob * 100)}%` },
};

/**
 * The bell in the top bar. Doctors and hospital nurses: their own patients
 * that need a look - opening a patient clears them (the backend does that when
 * the patient page loads); "Mark all as read" clears the rest. Case managers:
 * follow-up requests, and pharmacy reports on ones they hold. Pharmacies: their
 * reports that are done - opening the pharmacy page clears them. Insurers:
 * hospitals' billing flags (cleared by replying, on the Members page) and
 * members who turned critical (opening the member, or "Mark all as read").
 */
export default function NotificationBell() {
  const navigate = useNavigate();
  const { pathname } = useLocation();
  const [data, setData] = useState(null);
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const box = useRef(null);

  const load = useCallback(() => {
    api.getNotifications().then(setData).catch(() => {});   // a bell that fails stays quiet
  }, []);

  // On arrival, after every page change (opening a patient clears it), and once a minute.
  useEffect(() => { load(); }, [load, pathname]);
  useEffect(() => {
    const t = setInterval(load, REFRESH_MS);
    // A page that changed what is in the bell (Follow-ups) asks for a refresh.
    window.addEventListener('preventra:bell-refresh', load);
    return () => { clearInterval(t); window.removeEventListener('preventra:bell-refresh', load); };
  }, [load]);

  useEffect(() => {
    if (!open) return undefined;
    const onKey = (e) => e.key === 'Escape' && setOpen(false);
    const onClick = (e) => box.current && !box.current.contains(e.target) && setOpen(false);
    window.addEventListener('keydown', onKey);
    window.addEventListener('mousedown', onClick);
    return () => { window.removeEventListener('keydown', onKey); window.removeEventListener('mousedown', onClick); };
  }, [open]);

  const total = data?.total ?? 0;
  const items = data?.items ?? [];
  // Follow-up requests leave the bell when someone takes them (or, handed to
  // you, when you open Follow-ups), not when read.
  const isRequest = (i) => ['followup_new', 'followup_handed', 'followup_report'].some((k) => i.kinds.includes(k));
  const requestsOnly = items.length > 0 && items.every(isRequest);
  // A pharmacy's bell: its finished reports, cleared by opening its page.
  const reportsOnly = items.length > 0 && items.every((i) => i.kinds.includes('report_done'));
  // Cleared by opening Follow-ups (handed to you, or a pharmacy report on yours).
  const clearsOnFollowUps = (i) => i.kinds.includes('followup_handed') || i.kinds.includes('followup_report');

  const isInsurerBell = items.some((i) => i.kinds.includes('insurer_flag') || i.kinds.includes('turned_critical'));
  const flagsOnly = items.length > 0 && items.every((i) => i.kinds.includes('insurer_flag'));

  const markAll = async () => {
    setBusy(true);
    try {
      if (isInsurerBell) await api.markInsurerAlertsSeen();
      else await api.markNotificationsSeen({ all: true });
      load();
    } finally {
      setBusy(false);
    }
  };

  const go = (item) => {
    setOpen(false);
    // Opening Follow-ups clears what was handed to you (the page tells the
    // backend); drop them here at once rather than wait for that.
    if (clearsOnFollowUps(item)) {
      setData((d) => {
        const left = d.items.filter((i) => !clearsOnFollowUps(i));
        return { ...d, items: left, total: Math.max(0, d.total - (d.items.length - left.length)),
                 counts: { ...d.counts, followup_handed: 0, followup_report: 0 } };
      });
    }
    navigate(clearsOnFollowUps(item) ? '/follow-ups?mine=1'
      : item.kinds.includes('followup_new') ? '/follow-ups'
      : item.kinds.includes('report_done') || item.kinds.includes('insurer_flag') ? '/'
      : `/patients/${item.patient_idx}`);
  };

  // Hospital admins only get a bell when their hospital has no case manager.
  if (data && data.enabled === false) return null;

  return (
    <div className="relative" ref={box}>
      <button type="button" onClick={() => setOpen((o) => !o)}
        aria-label={total ? `Notifications, ${total} unread` : 'Notifications'} aria-expanded={open}
        className="relative w-9 h-9 rounded-lg flex items-center justify-center text-gray-500 hover:bg-gray-100 hover:text-gray-700 transition-colors">
        <Bell size={18} />
        {total > 0 && (
          <span className="absolute -top-0.5 -right-0.5 min-w-[18px] h-[18px] px-1 rounded-full text-[10px] font-bold text-white flex items-center justify-center"
                style={{ background: '#C62828', boxShadow: '0 0 0 2px var(--bg-surface)' }}>
            {total > 99 ? '99+' : total}
          </span>
        )}
      </button>

      {open && (
        <div role="dialog" aria-label="Notifications"
             className="card fixed left-4 right-4 top-[64px] sm:absolute sm:left-auto sm:right-0 sm:top-auto sm:mt-2 sm:w-[380px] z-50 shadow-xl overflow-hidden animate-fade-in"
             style={{ padding: 0 }}>
          <div className="flex items-start justify-between gap-3 px-4 pt-4 pb-3 border-b border-gray-100">
            <div>
              <div className="font-semibold text-gray-800 text-sm">Notifications</div>
              <div className="text-xs text-gray-400 mt-0.5">
                {!total ? 'Your patients' : isInsurerBell
                  ? `${total.toLocaleString()} for your members`
                  : reportsOnly
                  ? `${total.toLocaleString()} of your report${total === 1 ? '' : 's'} done`
                  : requestsOnly
                  ? `${total.toLocaleString()} follow-up request${total === 1 ? '' : 's'} for you`
                  : `${total.toLocaleString()} of your patients need a look`}
              </div>
            </div>
            {total > 0 && !requestsOnly && !reportsOnly && !flagsOnly && (
              <button type="button" onClick={markAll} disabled={busy}
                className="inline-flex items-center gap-1 text-xs font-semibold whitespace-nowrap disabled:opacity-50"
                style={{ color: 'var(--color-primary)' }}>
                <CheckCheck size={13} /> Mark all as read
              </button>
            )}
          </div>

          {total > 0 && (
            <div className="flex flex-wrap gap-1.5 px-4 py-2.5 border-b border-gray-100" style={{ background: '#F7FAFC' }}>
              {Object.entries(KINDS).map(([kind, k]) => data.counts?.[kind] > 0 && (
                <span key={kind} className="inline-flex items-center gap-1 text-[11px] font-medium px-2 py-0.5 rounded-full"
                      style={{ background: `${k.color}14`, color: k.color }}>
                  <k.icon size={11} />
                  {data.counts[kind].toLocaleString()} {k.label(data.counts[kind])}
                </span>
              ))}
            </div>
          )}

          {!data ? (
            <div className="p-4 space-y-2" aria-label="Loading notifications">
              {[0, 1, 2].map((i) => <div key={i} className="h-14 rounded-lg bg-gray-100 animate-pulse" />)}
            </div>
          ) : total === 0 ? (
            <div className="px-4 py-10 text-center">
              <div className="w-10 h-10 rounded-xl mx-auto flex items-center justify-center" style={{ background: '#E8F5E9' }}>
                <CheckCheck size={18} style={{ color: '#2E7D32' }} />
              </div>
              <div className="mt-3 text-sm font-medium text-gray-700">You&rsquo;re all caught up</div>
              <div className="mt-0.5 text-xs text-gray-400">New alerts and follow-up requests will show up here.</div>
            </div>
          ) : (
            <ul className="max-h-[420px] overflow-y-auto divide-y divide-gray-100">
              {items.map((i) => (
                <li key={i.followup_id || i.flag_id || i.patient_idx}>
                  <button type="button" onClick={() => go(i)}
                    className="group w-full text-left flex items-start gap-3 px-4 py-3 hover:bg-gray-50 transition-colors">
                    <div className="pt-0.5">{i.dropout_prob != null
                      ? <RiskBadge prob={i.dropout_prob} />
                      : <span className="inline-flex px-2 py-0.5 rounded-full text-xs font-semibold" style={{ background: '#1B4F8A14', color: '#1B4F8A' }}>Billing</span>}</div>
                    <div className="flex-1 min-w-0">
                      <div className="text-sm font-semibold text-gray-800 font-mono">
                        {isInsurerBell && i.member_id ? `Member ${i.member_id}` : `Patient #${i.patient_idx}`}
                      </div>
                      {i.kinds.map((kind) => {
                        const k = KINDS[kind];
                        return (
                          <div key={kind} className="flex items-center gap-1.5 text-xs mt-0.5" style={{ color: k.color }}>
                            <k.icon size={11} className="flex-shrink-0" /> {k.text(i)}
                          </div>
                        );
                      })}
                      {i.main_reason && (
                        <div className="text-[11px] text-gray-400 mt-0.5 truncate">Main reason: {i.main_reason}</div>
                      )}
                    </div>
                    <ChevronRight size={15} className="mt-1 text-gray-300 group-hover:text-gray-500 flex-shrink-0" />
                  </button>
                </li>
              ))}
            </ul>
          )}

          {total > items.length && (
            <div className="px-4 py-2.5 border-t border-gray-100 text-xs text-gray-500 flex items-center justify-between gap-2">
              <span>Showing the {items.length} most important of {total.toLocaleString()}</span>
              <Link to={requestsOnly ? '/follow-ups' : '/'} onClick={() => setOpen(false)} className="font-semibold whitespace-nowrap"
                    style={{ color: 'var(--color-primary)' }}>
                {requestsOnly ? 'Follow-ups' : 'My patients'}
              </Link>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
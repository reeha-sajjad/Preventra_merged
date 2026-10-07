import { useCallback, useEffect, useRef, useState } from 'react';
import { Link, useLocation, useNavigate } from 'react-router-dom';
import { Bell, TrendingUp, UserPlus, AlertTriangle, ChevronRight, CheckCheck } from 'lucide-react';
import { api } from '../../data/api';
import { RiskBadge } from '../shared';

const REFRESH_MS = 60_000;

// One line per reason a patient is in the bell (Backend/core/notifications.py).
const KINDS = {
  risk_up:    { icon: TrendingUp,    color: '#C62828', text: (i) =>
    `Risk up from ${Math.round((i.previous_prob ?? 0) * 100)}% since you last looked` },
  new:        { icon: UserPlus,      color: '#1B4F8A', text: () => 'New on your care team' },
  unreviewed: { icon: AlertTriangle, color: '#EF6C00', text: () => 'Critical risk, not reviewed yet' },
};

/**
 * The bell in the top bar, for doctors and hospital nurses: their own patients
 * that need a look. Opening a patient clears them (the backend does that when
 * the patient page loads); "Mark all as read" clears the rest.
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
    return () => clearInterval(t);
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

  const markAll = async () => {
    setBusy(true);
    try {
      await api.markNotificationsSeen({ all: true });
      load();
    } finally {
      setBusy(false);
    }
  };

  const go = (idx) => {
    setOpen(false);
    navigate(`/patients/${idx}`);
  };

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
                {total ? `${total.toLocaleString()} of your patients need a look` : 'Your patients'}
              </div>
            </div>
            {total > 0 && (
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
                  {data.counts[kind].toLocaleString()} {kind === 'risk_up' ? 'risk up' : kind === 'new' ? 'new' : 'not reviewed'}
                </span>
              ))}
            </div>
          )}

          {total === 0 ? (
            <div className="px-4 py-10 text-center">
              <div className="w-10 h-10 rounded-xl mx-auto flex items-center justify-center" style={{ background: '#E8F5E9' }}>
                <CheckCheck size={18} style={{ color: '#2E7D32' }} />
              </div>
              <div className="mt-3 text-sm font-medium text-gray-700">You&rsquo;re all caught up</div>
              <div className="mt-0.5 text-xs text-gray-400">New patients and rising risk will show up here.</div>
            </div>
          ) : (
            <ul className="max-h-[420px] overflow-y-auto divide-y divide-gray-100">
              {items.map((i) => (
                <li key={i.patient_idx}>
                  <button type="button" onClick={() => go(i.patient_idx)}
                    className="group w-full text-left flex items-start gap-3 px-4 py-3 hover:bg-gray-50 transition-colors">
                    <div className="pt-0.5"><RiskBadge prob={i.dropout_prob} /></div>
                    <div className="flex-1 min-w-0">
                      <div className="text-sm font-semibold text-gray-800 font-mono">Patient #{i.patient_idx}</div>
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
              <Link to="/" onClick={() => setOpen(false)} className="font-semibold whitespace-nowrap"
                    style={{ color: 'var(--color-primary)' }}>
                My patients
              </Link>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
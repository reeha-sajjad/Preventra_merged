import React, { useEffect, useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { getAlerts, acknowledgeAlert, getRisingRisk, markRiskSeen, getFollowUpBell, markInsurerAlertsSeen, markCaretakerSeen, FOLLOWUPS_CHANGED } from '../api';
import { readClaims } from '../api/auth';
import { Bell, Check, CheckCheck, Loader2, Pill, CalendarCheck, Receipt, Siren, PackageX } from 'lucide-react';
import { can, WATCH_ROLES, FOLLOWUP_HANDLE_ROLES, PHARMACY_ROLES, INSURER_ROLES } from '../roles';
import { RisingRiskRow } from './shared/RisingRiskList';

// How many unopened rising patients the dropdown lists; the rest are on the
// Patients page's "Needs attention" panel.
const RISING_IN_BELL = 8;

const POLL_INTERVAL_MS = 30000;

export default function AlertsBell() {
  const navigate = useNavigate();
  const [alerts, setAlerts] = useState([]);
  // The caller's own patients whose latest week got worse (api/risk_watch.py).
  const [rising, setRising] = useState(null);
  const watches = can(WATCH_ROLES);
  // Follow-up news (api/followups.bell): a case manager hears of pharmacy
  // reports on requests they hold; a pharmacy hears when its reports are done;
  // an insurer hears of hospitals' billing flags and members who turned high
  // risk (api/insurer_alerts.py).
  const [news, setNews] = useState([]);
  const isInsurer = can(INSURER_ROLES);
  // A caretaker: refill problems, medicines due, appointments booked (api/caretaker_home.py).
  const isCarer = readClaims()?.role === 'caretaker';
  const hasNews = can(FOLLOWUP_HANDLE_ROLES) || can(PHARMACY_ROLES) || isInsurer || isCarer;
  const [open, setOpen] = useState(false);
  const [acknowledgingId, setAcknowledgingId] = useState(null);
  const containerRef = useRef(null);

  const load = () => {
    getAlerts()
      .then((data) => setAlerts(Array.isArray(data) ? data : []))
      .catch(() => {});
    if (can(WATCH_ROLES)) getRisingRisk(50).then(setRising).catch(() => {});
    if (hasNews) getFollowUpBell().then((d) => setNews(d.items || [])).catch(() => {});
  };

  const unseen = (rising?.patients || []).filter((p) => !p.seen);
  const count = alerts.length + (rising?.unseen || 0) + news.length;

  // Pharmacy reports are cleared by opening Follow-ups; finished reports by
  // opening the pharmacy page (both pages tell the backend).
  const openNews = (item) => {
    setOpen(false);
    if (isCarer) { navigate(`/person/${encodeURIComponent(item.patient_id)}`); return; }
    if (item.kind === 'dose_missed') { navigate(`/patients/${encodeURIComponent(item.patient_id)}`); return; }
    if (isInsurer) {
      // A flag leaves the bell when answered (on Members); a member who turned
      // high risk when opened.
      if (item.kind === 'turned_high') {
        markInsurerAlertsSeen([item.patient_id]).catch(() => {});
        setNews((n) => n.filter((i) => !(i.kind === 'turned_high' && i.patient_id === item.patient_id)));
        navigate(`/patients/${encodeURIComponent(item.patient_id)}`);
      } else navigate('/');
      return;
    }
    setNews([]);
    navigate(item.kind === 'report_done' ? '/' : '/follow-ups');
  };
  const turnedHigh = news.filter((i) => i.kind === 'turned_high');
  const markAllHigh = () => {
    markInsurerAlertsSeen().catch(() => {});
    setNews((n) => n.filter((i) => i.kind !== 'turned_high'));
  };
  // A caretaker's "mark all": refill reports and bookings go; a medicine still
  // to log stays while it is true.
  const carerReadable = isCarer && news.some((i) => i.kind !== 'dose_due');
  const markAllCarer = () => {
    markCaretakerSeen().catch(() => {});
    setNews((n) => n.filter((i) => i.kind === 'dose_due'));
  };

  const openRising = (item) => {
    setOpen(false);
    markRiskSeen(item.patient_id, item.week_number).catch(() => {});
    setRising((r) => r && {
      ...r, unseen: Math.max(0, r.unseen - 1),
      patients: r.patients.map((p) => (p.patient_id === item.patient_id ? { ...p, seen: true } : p)),
    });
    navigate(`/patients/${encodeURIComponent(item.patient_id)}/trend`);
  };

  useEffect(() => {
    load();
    const interval = setInterval(load, POLL_INTERVAL_MS);
    // The Patients page's "Needs attention" panel marks patients seen too.
    window.addEventListener('rising-risk-changed', load);
    window.addEventListener(FOLLOWUPS_CHANGED, load);
    return () => {
      clearInterval(interval);
      window.removeEventListener('rising-risk-changed', load);
      window.removeEventListener(FOLLOWUPS_CHANGED, load);
    };
  }, []);

  useEffect(() => {
    const handleClickOutside = (e) => {
      if (containerRef.current && !containerRef.current.contains(e.target)) {
        setOpen(false);
      }
    };
    document.addEventListener('mousedown', handleClickOutside);
    return () => document.removeEventListener('mousedown', handleClickOutside);
  }, []);

  const handleAcknowledge = async (alertId) => {
    setAcknowledgingId(alertId);
    try {
      await acknowledgeAlert(alertId);
      setAlerts((prev) => prev.filter((a) => a.id !== alertId));
    } catch {
      // leave it in the list — user can retry
    } finally {
      setAcknowledgingId(null);
    }
  };

  const handleGoToPatient = (patientId) => {
    setOpen(false);
    navigate(`/patients/${patientId}`);
  };

  return (
    <div className="relative" ref={containerRef}>
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="relative p-2 text-gray-500 hover:text-ns-navy hover:bg-gray-100 rounded-full transition-colors"
        aria-label="Alerts"
      >
        <Bell size={20} />
        {count > 0 && (
          <span className="absolute -top-0.5 -right-0.5 bg-risk-high text-white text-[10px] font-bold rounded-full min-w-[18px] h-[18px] flex items-center justify-center px-1">
            {count > 9 ? '9+' : count}
          </span>
        )}
      </button>

      {open && (
        <div className="absolute right-0 mt-2 w-96 bg-white rounded-xl border border-gray-200 shadow-lg z-50 overflow-hidden animate-in fade-in slide-in-from-top-2 duration-150">
          <div className="max-h-[28rem] overflow-y-auto divide-y divide-gray-100">
            {news.length > 0 && (
              <>
                <div className="px-4 py-3 bg-gray-50 flex items-center justify-between gap-3">
                  <h3 className="font-semibold text-gray-800 text-sm">
                    {isCarer ? 'The people you care for' : isInsurer ? 'Your members' : news[0].kind === 'report_done' ? 'Your reports that are done'
                      : news.every((i) => i.kind === 'dose_missed') ? 'Medicines not being logged'
                      : 'Follow-up news'}
                  </h3>
                  {carerReadable && (
                    <button type="button" onClick={markAllCarer}
                      className="inline-flex items-center gap-1 text-xs font-semibold text-ns-navy whitespace-nowrap">
                      <CheckCheck size={13} /> Mark all as read
                    </button>
                  )}
                  {turnedHigh.length > 0 && (
                    <button type="button" onClick={markAllHigh}
                      className="inline-flex items-center gap-1 text-xs font-semibold text-ns-navy whitespace-nowrap">
                      <CheckCheck size={13} /> Mark all as read
                    </button>
                  )}
                </div>
                {news.map((item) => (
                  <button key={item.followup_id || item.flag_id || `${item.kind}-${item.patient_id}`} type="button" onClick={() => openNews(item)}
                    className="w-full text-left px-4 py-3 hover:bg-gray-50 transition-colors flex items-start gap-3">
                    {item.kind === 'report_done'
                      ? <CalendarCheck size={16} className="mt-0.5 text-green-700 shrink-0" />
                      : item.kind === 'insurer_flag' ? <Receipt size={16} className="mt-0.5 text-ns-navy shrink-0" />
                      : item.kind === 'turned_high' ? <Siren size={16} className="mt-0.5 text-risk-high shrink-0" />
                      : item.kind === 'refill_problem' ? <PackageX size={16} className="mt-0.5 text-red-700 shrink-0" />
                      : item.kind === 'booked' ? <CalendarCheck size={16} className="mt-0.5 text-green-700 shrink-0" />
                      : <Pill size={16} className="mt-0.5 text-amber-700 shrink-0" />}
                    <span className="flex-1 min-w-0">
                      <span className="block text-sm font-semibold text-ns-navy">
                        {item.patient_name ?? (isInsurer && item.member_id ? `Member ${item.member_id}` : item.patient_id)}
                      </span>
                      <span className="block text-sm text-gray-600 mt-0.5">
                        {item.kind === 'report_done'
                          ? `${item.problem_label ? `${item.problem_label} · ` : ''}done: ${item.outcome_label}`
                          : item.kind === 'insurer_flag' ? `${item.hospital}: ${item.kind_label} · from ${item.by}`
                          : item.kind === 'refill_problem' ? `Pharmacy: ${item.label}`
                          : item.kind === 'dose_due' ? "Today's medicines not logged yet"
                          : item.kind === 'booked' ? 'The case manager booked an appointment'
                          : item.kind === 'dose_missed' ? `No medicines logged in ${item.days} days (last by ${item.by})`
                          : item.kind === 'turned_high'
                          ? `Turned high risk (${Math.round(item.risk_score)}%)${item.previous_band ? ` · was ${item.previous_band}` : ' · new member'}`
                          : `${item.by}: ${item.problem_label}`}
                      </span>
                    </span>
                  </button>
                ))}
              </>
            )}
            {watches && (
              <>
                <div className="px-4 py-3 bg-gray-50">
                  <h3 className="font-semibold text-gray-800 text-sm">Your patients' risk went up</h3>
                  <p className="text-xs text-gray-500">Latest monitoring week: a red flag, a rise of 5+ points, or a move up a band.</p>
                </div>
                {unseen.length === 0 && (
                  <p className="text-sm text-gray-400 italic px-4 py-4 text-center">
                    {rising?.total ? 'All opened. They stay listed on the Patients page.' : 'None of your patients got worse this week.'}
                  </p>
                )}
                {unseen.slice(0, RISING_IN_BELL).map((item) => (
                  <RisingRiskRow key={item.patient_id} item={item} onOpen={openRising} compact />
                ))}
                {unseen.length > RISING_IN_BELL && (
                  <button type="button" onClick={() => { setOpen(false); navigate('/patients'); }}
                    className="w-full px-4 py-2 text-sm font-medium text-ns-navy hover:bg-gray-50">
                    {unseen.length - RISING_IN_BELL} more on the Patients page
                  </button>
                )}
              </>
            )}
            {(alerts.length > 0 || (!watches && news.length === 0)) && (
              <div className="px-4 py-3 bg-gray-50">
                <h3 className="font-semibold text-gray-800 text-sm">Score edits</h3>
              </div>
            )}
            {alerts.length === 0 && !watches && news.length === 0 && (
              <p className="text-sm text-gray-400 italic px-4 py-6 text-center">No unacknowledged alerts.</p>
            )}
            {alerts.map((alert) => (
              <div key={alert.id} className="px-4 py-3 hover:bg-gray-50 transition-colors">
                <div className="flex items-start justify-between gap-3">
                  <button
                    type="button"
                    onClick={() => handleGoToPatient(alert.patient_id)}
                    className="text-left flex-1"
                  >
                    <p className="text-sm font-semibold text-ns-navy hover:underline">{alert.patient_id}</p>
                    <p className="text-sm text-gray-600 mt-0.5">
                      {parseFloat(alert.old_score).toFixed(1)}% ({alert.old_band}) &rarr;{' '}
                      <span className="font-semibold text-risk-high">
                        {parseFloat(alert.new_score).toFixed(1)}% ({alert.new_band})
                      </span>
                    </p>
                    <p className="text-xs text-gray-400 mt-1">{alert.triggered_at}</p>
                  </button>
                  <button
                    type="button"
                    onClick={() => handleAcknowledge(alert.id)}
                    disabled={acknowledgingId === alert.id}
                    className="shrink-0 p-1.5 text-gray-400 hover:text-green-600 hover:bg-green-50 rounded-full transition-colors disabled:opacity-50"
                    title="Acknowledge"
                  >
                    {acknowledgingId === alert.id ? (
                      <Loader2 size={16} className="animate-spin" />
                    ) : (
                      <Check size={16} />
                    )}
                  </button>
                </div>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import {
  Stethoscope, AlertTriangle, Check, Loader2, UserPlus, Inbox, RefreshCw,
  ChevronRight, ShieldAlert, X,
} from 'lucide-react';
import {
  getDoctors, registerDoctor, getDoctorAlerts, getDoctorNotifications,
  getRecommendedActions, acknowledgeClinicalAlert, respondToClinicalAlert,
  dismissClinicalAlert, getUnroutedAlerts, startForecastScan, getForecastScan,
  getMyClinicalAlerts,
} from '../api';
import { readClaims } from '../api/auth';
import { can, REGISTRY_ROLES, FOLLOWUP_REQUEST_ROLES } from '../roles';

// Which doctor the console is acting as. Persisted so a refresh does not send
// the user back to the picker, and namespaced so it cannot collide with
// anything else this app stores.
const IDENTITY_KEY = 'preventra.doctorId';
const POLL_INTERVAL_MS = 30000;
const SCAN_POLL_MS = 3000;

// Reply actions a case manager arranges (api/followups.py BOOKING_ACTIONS),
// and who hands them over by replying (ALERT_REPLY_ROLES there).
const BOOKING_ACTIONS = ['contact_patient', 'schedule_review', 'arrange_labs', 'home_visit',
  'increase_monitoring'];
const ALERT_REPLY_ROLES = [...FOLLOWUP_REQUEST_ROLES, 'superadmin'];

const SEVERITY_STYLE = {
  critical: 'bg-risk-high text-white',
  high: 'bg-orange-500 text-white',
};

export default function DoctorConsole() {
  const navigate = useNavigate();
  const [doctors, setDoctors] = useState([]);
  const [groups, setGroups] = useState([]);
  const [specialties, setSpecialties] = useState([]);
  const [pickedId, setPickedId] = useState(() => localStorage.getItem(IDENTITY_KEY) || '');
  const [alerts, setAlerts] = useState([]);
  const [counts, setCounts] = useState(null);
  const [unrouted, setUnrouted] = useState([]);
  const [actions, setActions] = useState([]);
  const [urgencies, setUrgencies] = useState([]);
  const [loading, setLoading] = useState(false);
  const [showRegister, setShowRegister] = useState(false);
  const [scan, setScan] = useState(null);
  const [error, setError] = useState('');
  // Tracked separately from `error`. If the directory call fails there is no
  // specialty list and no doctor list, so the register form and the identity
  // picker are both useless - showing them empty invites the user to fill in a
  // form that cannot succeed.
  const [directoryDown, setDirectoryDown] = useState('');

  // A signed-in doctor opens their own inbox - their registry entry, matched by
  // email the same way the backend matches it (api/access.py). A nurse sees the
  // alerts on their own patients, whichever doctor they went to, and answers in
  // their own name. Only the superadmin and case managers choose whose inbox to
  // look at.
  const me = readClaims();
  const isDoctor = me?.role === 'doctor';
  const isNurse = me?.role === 'nurse';
  const [notice, setNotice] = useState('');
  const mine = useMemo(
    () => (isDoctor ? doctors.find((d) => (d.email || '').toLowerCase() === (me?.email || '').toLowerCase()) : null),
    [doctors, isDoctor, me?.email],
  );
  const doctorId = isDoctor ? (mine?.doctor_id || '') : pickedId;
  const canRegister = can(REGISTRY_ROLES);
  const canSweep = me?.role === 'superadmin';

  const doctor = useMemo(
    () => doctors.find((d) => d.doctor_id === doctorId) || null,
    [doctors, doctorId],
  );

  const loadDirectory = useCallback(() => {
    getDoctors()
      .then((data) => {
        setDoctors(data.doctors || []);
        setGroups(data.clinical_groups || []);
        setSpecialties(data.specialties || []);
        setDirectoryDown('');
      })
      .catch((e) => {
        // A bare 404 here almost always means the backend predates this
        // feature - a server started before the clinician endpoints existed
        // keeps serving every older route, so nothing else on the dashboard
        // looks broken and the cause is easy to miss.
        setDirectoryDown(/not found/i.test(e.message)
          ? 'The backend is running but does not have the clinician endpoints. '
            + 'It was most likely started before this feature was added — restart '
            + 'it (uvicorn api.main:app --reload) and refresh.'
          : e.message);
      });
  }, []);

  useEffect(() => {
    loadDirectory();
    getRecommendedActions()
      .then((d) => { setActions(d.actions || []); setUrgencies(d.urgencies || []); })
      .catch(() => {});
    getUnroutedAlerts().then((d) => setUnrouted(d.alerts || [])).catch(() => {});
  }, [loadDirectory]);

  const loadInbox = useCallback(() => {
    if (isNurse) {
      setLoading(true);
      getMyClinicalAlerts('open')
        .then((data) => {
          const list = data.alerts || [];
          setAlerts(list);
          setCounts({
            open: list.length,
            critical: list.filter((a) => a.severity === 'critical').length,
            pending: list.filter((a) => a.status === 'pending').length,
          });
          setError('');
        })
        .catch((e) => setError(e.message))
        .finally(() => setLoading(false));
      return;
    }
    if (!doctorId) return;
    setLoading(true);
    Promise.all([
      getDoctorAlerts(doctorId, 'open'),
      getDoctorNotifications(doctorId),
    ])
      .then(([alertData, countData]) => {
        setAlerts(alertData.alerts || []);
        setCounts(countData);
        setError('');
      })
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false));
  }, [doctorId, isNurse]);

  useEffect(() => {
    loadInbox();
    if (!doctorId && !isNurse) return undefined;
    const id = setInterval(loadInbox, POLL_INTERVAL_MS);
    return () => clearInterval(id);
  }, [doctorId, isNurse, loadInbox]);

  // The sweep runs on the server and takes a couple of minutes over the full
  // cohort, so the button starts it and this polls until it stops running.
  useEffect(() => {
    if (!scan?.id || scan.status !== 'Running') return undefined;
    const id = setInterval(() => {
      getForecastScan(scan.id)
        .then((s) => {
          setScan(s);
          if (s.status !== 'Running') { clearInterval(id); loadInbox(); }
        })
        .catch(() => clearInterval(id));
    }, SCAN_POLL_MS);
    return () => clearInterval(id);
  }, [scan?.id, scan?.status, loadInbox]);

  const chooseDoctor = (id) => {
    setPickedId(id);
    if (id) localStorage.setItem(IDENTITY_KEY, id);
    else localStorage.removeItem(IDENTITY_KEY);
  };

  const runScan = () => {
    setScan({ id: null, status: 'Running', current_step: 'starting' });
    startForecastScan()
      .then((r) => setScan({ id: r.scan_id, status: 'Running', current_step: 'queued' }))
      .catch((e) => { setError(e.message); setScan(null); });
  };

  const afterResolve = (alertId, result) => {
    setAlerts((prev) => prev.filter((a) => a.alert_id !== alertId));
    // A reply that asks for something a case manager arranges becomes (or joins)
    // a follow-up request (api/followups.py); say so, so nobody asks twice.
    const f = result?.followup;
    setNotice(f ? `The case managers have this as a follow-up request for ${f.patient_id}.` : '');
  };

  return (
    <div className="p-4 sm:p-6 space-y-6 max-w-6xl">
      <header className="flex items-start justify-between gap-4 flex-wrap">
        <div>
          <h1 className="text-2xl font-bold text-ns-navy flex items-center gap-2">
            <Stethoscope size={24} /> Clinician console
          </h1>
          <p className="text-sm text-gray-600 mt-1">
            Early-warning alerts routed by condition. Acknowledge, then send a
            recommendation back to the care team.
          </p>
        </div>
        <div className="flex items-center gap-2">
          {canSweep && (
          <button
            onClick={runScan}
            disabled={scan?.status === 'Running'}
            className="flex items-center gap-2 px-4 py-2 text-sm rounded-md border border-gray-300 bg-white hover:bg-gray-50 disabled:opacity-50"
          >
            {scan?.status === 'Running'
              ? <Loader2 size={15} className="animate-spin" />
              : <RefreshCw size={15} />}
            Run forecast sweep
          </button>
          )}
          {canRegister && (
          <button
            onClick={() => setShowRegister((v) => !v)}
            disabled={!!directoryDown}
            title={directoryDown ? 'The backend cannot accept registrations right now.' : ''}
            className="flex items-center gap-2 px-4 py-2 text-sm rounded-md bg-ns-navy text-white hover:bg-ns-navy/90 disabled:opacity-40 disabled:cursor-not-allowed"
          >
            <UserPlus size={15} /> Register clinician
          </button>
          )}
        </div>
      </header>

      {/* Anyone but the doctor themselves (or a nurse, who answers as
          themselves) is looking at someone else's inbox. Saying so on screen
          beats letting a reviewer discover it. */}
      {isNurse && (
        <div className="bg-slate-50 border border-slate-200 rounded-md px-4 py-2.5 text-xs text-slate-700 flex items-start gap-2">
          <ShieldAlert size={14} className="shrink-0 mt-0.5" />
          <span>
            Alerts on the patients you are assigned to, whichever doctor they went to.
            Anything you acknowledge or answer is recorded under your own name.
          </span>
        </div>
      )}
      {notice && (
        <div className="bg-green-50 border border-green-200 text-green-800 rounded-md px-4 py-2.5 text-sm flex items-start justify-between gap-3">
          <span>{notice}</span>
          <button type="button" onClick={() => setNotice('')} aria-label="Dismiss" className="text-green-700 hover:text-green-900"><X size={14} /></button>
        </div>
      )}
      {!isDoctor && !isNurse && (
        <div className="bg-amber-50 border border-amber-200 rounded-md px-4 py-2.5 text-xs text-amber-900 flex items-start gap-2">
          <ShieldAlert size={14} className="shrink-0 mt-0.5" />
          <span>
            You are viewing a clinician&apos;s inbox. Anything you acknowledge or answer
            here is recorded against the selected doctor.
          </span>
        </div>
      )}

      {directoryDown && (
        <div className="bg-red-50 border border-red-200 text-red-800 rounded-md px-4 py-3 text-sm">
          <p className="font-medium">Clinician directory unavailable</p>
          <p className="mt-1">{directoryDown}</p>
        </div>
      )}

      {error && !directoryDown && (
        <div className="bg-red-50 border border-red-200 text-red-700 rounded-md px-4 py-3 text-sm">
          {error}
        </div>
      )}

      {scan && (
        <ScanProgress scan={scan} onDismiss={() => setScan(null)} />
      )}

      <section className="bg-white rounded-lg border border-gray-200 p-4">
        <label className="block text-xs font-semibold uppercase tracking-wide text-gray-500">
          {isDoctor ? 'Your inbox' : isNurse ? 'Your patients' : 'Viewing as'}
        </label>
        {!isDoctor && !isNurse && (
        <p className="text-xs text-gray-500 mt-0.5 mb-2">
          Each alert is addressed to one clinician. Choose whose inbox to open.
        </p>
        )}
        <div className="flex items-center gap-3 flex-wrap">
          {isNurse ? (
            <span className="text-sm font-medium text-gray-800 py-2">Alerts on the patients you look after</span>
          ) : isDoctor ? (
            <span className="text-sm font-medium text-gray-800 py-2">
              {mine ? `${mine.name} — ${mine.specialty}` : 'You are not registered for alerts yet'}
            </span>
          ) : (
          <select
            value={doctorId}
            onChange={(e) => chooseDoctor(e.target.value)}
            disabled={doctors.length === 0}
            className="border border-gray-300 rounded-md px-3 py-2 text-sm min-w-[18rem] disabled:bg-gray-100"
          >
            <option value="">
              {doctors.length === 0 ? 'No clinicians registered yet' : 'Select a clinician…'}
            </option>
            {doctors.map((d) => (
              <option key={d.doctor_id} value={d.doctor_id}>
                {d.name} — {d.specialty}
              </option>
            ))}
          </select>
          )}
          {counts && (
            <div className="flex items-center gap-2 text-sm">
              <Pill tone="bg-gray-100 text-gray-700" label={`${counts.open} open`} />
              {counts.critical > 0 && (
                <Pill tone="bg-risk-high text-white" label={`${counts.critical} critical`} />
              )}
              {counts.pending > 0 && (
                <Pill tone="bg-orange-100 text-orange-800" label={`${counts.pending} unread`} />
              )}
            </div>
          )}
          {doctor?.clinical_groups?.length > 0 && (
            <span className="text-xs text-gray-500">
              Covers: {doctor.clinical_groups
                .map((g) => groups.find((x) => x.key === g)?.label || g)
                .join(', ')}
            </span>
          )}
        </div>
      </section>

      {showRegister && (
        <RegisterForm
          specialties={specialties}
          groups={groups}
          onDone={(created) => {
            setShowRegister(false);
            loadDirectory();
            if (created?.doctor_id) chooseDoctor(created.doctor_id);
          }}
          onCancel={() => setShowRegister(false)}
        />
      )}

      {!doctorId && !isNurse ? (
        <EmptyState
          icon={<Stethoscope size={32} />}
          title={isDoctor ? 'Your account is not in the alert registry' : 'Select a clinician to open their inbox'}
          body={isDoctor
            ? 'Alerts are routed to registered clinicians. Ask your hospital admin to register you on the Staff page.'
            : doctors.length === 0
            ? 'No clinicians are registered yet. Use “Register clinician” above — name, specialty and email is all it takes.'
            : 'Alerts are routed by the patient’s condition to whoever covers it.'}
        />
      ) : loading && alerts.length === 0 ? (
        <div className="flex items-center gap-2 text-gray-500 text-sm p-6">
          <Loader2 size={16} className="animate-spin" /> Loading inbox…
        </div>
      ) : alerts.length === 0 ? (
        <EmptyState
          icon={<Inbox size={32} />}
          title="Nothing needs your attention"
          body={`Alerts appear here when a patient’s forecast reaches high or critical.${
            canSweep ? ' Run a forecast sweep to check the cohort now.' : ''}`}
        />
      ) : (
        <ul className="space-y-4">
          {alerts.map((alert) => (
            <AlertCard
              key={alert.alert_id}
              alert={alert}
              doctorId={isNurse ? (alert.doctor_id || '') : doctorId}
              routedTo={isNurse ? (alert.doctor_name || 'no doctor yet') : null}
              actions={actions}
              urgencies={urgencies}
              onOpenPatient={() => navigate(`/patients/${alert.patient_id}`)}
              onResolved={(result) => afterResolve(alert.alert_id, result)}
              onAcknowledged={(updated) =>
                setAlerts((prev) => prev.map((a) =>
                  a.alert_id === updated.alert_id ? updated : a))}
            />
          ))}
        </ul>
      )}

      {unrouted.length > 0 && (
        <section className="bg-white rounded-lg border border-red-200 p-4">
          <h2 className="font-semibold text-risk-high flex items-center gap-2 text-sm">
            <AlertTriangle size={16} /> {unrouted.length} alert{unrouted.length === 1 ? '' : 's'} with no clinician
          </h2>
          <p className="text-xs text-gray-600 mt-1 mb-3">
            No registered doctor covers these conditions. They are held here rather
            than discarded — register a clinician for the specialty and re-run the sweep.
          </p>
          <ul className="text-sm divide-y divide-gray-100">
            {unrouted.slice(0, 8).map((a) => (
              <li key={a.alert_id} className="py-2 flex items-center justify-between gap-3">
                <span className="text-gray-800">
                  {a.patient_id} · {a.patient?.group_label || a.patient?.clinical_group}
                </span>
                <span className="text-xs text-gray-500">{a.routing_reason}</span>
              </li>
            ))}
          </ul>
        </section>
      )}
    </div>
  );
}

/* ── One alert, and the reply form it carries ───────────────────────────── */

function AlertCard({ alert, doctorId, routedTo, actions, urgencies, onOpenPatient, onResolved, onAcknowledged }) {
  const [open, setOpen] = useState(false);
  const [recommendation, setRecommendation] = useState('');
  const [picked, setPicked] = useState([]);
  const [urgency, setUrgency] = useState('routine');
  const [busy, setBusy] = useState('');
  const [formError, setFormError] = useState('');

  const forecast = alert.forecast || {};
  const crossing = forecast.band_crossing;
  const severityStyle = SEVERITY_STYLE[alert.severity] || 'bg-gray-200 text-gray-700';

  const toggle = (key) =>
    setPicked((prev) => (prev.includes(key) ? prev.filter((k) => k !== key) : [...prev, key]));

  const acknowledge = async () => {
    setBusy('ack');
    try {
      onAcknowledged(await acknowledgeClinicalAlert(alert.alert_id, doctorId));
    } catch (e) { setFormError(e.message); } finally { setBusy(''); }
  };

  const submit = async (e) => {
    e.preventDefault();
    if (!recommendation.trim()) { setFormError('A recommendation is required.'); return; }
    setBusy('respond');
    try {
      const result = await respondToClinicalAlert(alert.alert_id, {
        doctor_id: doctorId,
        recommendation: recommendation.trim(),
        actions: picked,
        urgency,
      });
      onResolved(result);
    } catch (err) { setFormError(err.message); } finally { setBusy(''); }
  };

  const dismiss = async () => {
    setBusy('dismiss');
    try {
      await dismissClinicalAlert(alert.alert_id, doctorId, recommendation.trim());
      onResolved();
    } catch (err) { setFormError(err.message); } finally { setBusy(''); }
  };

  return (
    <li className="bg-white rounded-lg border border-gray-200 overflow-hidden">
      <div className="px-5 py-4">
        <div className="flex items-start justify-between gap-4">
          <div className="min-w-0">
            <div className="flex items-center gap-2 flex-wrap">
              <span className={`px-2 py-0.5 rounded text-[10px] font-semibold uppercase tracking-wide ${severityStyle}`}>
                {alert.severity}
              </span>
              <button
                onClick={onOpenPatient}
                className="-my-1.5 flex items-center gap-1 py-1.5 font-semibold text-ns-navy hover:underline"
              >
                {alert.patient_id} <ChevronRight size={14} />
              </button>
              {alert.status === 'acknowledged' && (
                <span className="text-xs text-gray-500 flex items-center gap-1">
                  <Check size={12} /> acknowledged
                </span>
              )}
            </div>
            <p className="text-sm text-gray-700 mt-1">
              {alert.patient?.group_label}
              {alert.patient?.primary_diagnosis ? ` · ${alert.patient.primary_diagnosis}` : ''}
            </p>
            <p className="text-sm font-medium text-gray-900 mt-2">
              {crossing
                ? `Projected to reach ${crossing.to_band} risk in about ${crossing.lead_time_days} days`
                : `Risk ${forecast.current_score}% and ${forecast.velocity_per_week > 0 ? 'rising' : 'flat'}`}
            </p>
          </div>
          <div className="text-right shrink-0">
            <div className="text-2xl font-semibold text-ns-navy tabular-nums">
              {forecast.current_score}%
            </div>
            <div className="text-xs text-gray-500">
              → {forecast.projected_score}% in {forecast.horizon_weeks} wks
            </div>
            <div className="text-xs text-gray-400 mt-1">week {alert.week_number}</div>
          </div>
        </div>

        <ul className="mt-3 space-y-1.5">
          {(forecast.triggers || []).slice(0, 4).map((t) => (
            <li key={t.code} className="text-sm text-gray-700 flex gap-2">
              <span className={`mt-1.5 h-1.5 w-1.5 rounded-full shrink-0 ${
                t.severity === 'critical' ? 'bg-risk-high' : 'bg-orange-400'}`} />
              <span>
                <strong className="font-medium">{t.title}.</strong>{' '}
                <span className="text-gray-600">{t.detail}</span>
              </span>
            </li>
          ))}
        </ul>

        <p className="text-xs text-gray-400 mt-3">
          {routedTo ? `Routed to ${routedTo}` : 'Routed to you'}: {alert.routing_reason} · review {forecast.recommended_review_by}
        </p>
      </div>

      <div className="border-t border-gray-100 px-5 py-3 bg-gray-50 flex items-center gap-2 flex-wrap">
        {alert.status === 'pending' && (
          <button
            onClick={acknowledge}
            disabled={busy === 'ack'}
            className="text-sm px-3 py-1.5 rounded-md border border-gray-300 bg-white hover:bg-gray-100 disabled:opacity-50 flex items-center gap-1.5"
          >
            {busy === 'ack' ? <Loader2 size={14} className="animate-spin" /> : <Check size={14} />}
            Acknowledge
          </button>
        )}
        <button
          onClick={() => setOpen((v) => !v)}
          className="text-sm px-3 py-1.5 rounded-md bg-ns-navy text-white hover:bg-ns-navy/90"
        >
          {open ? 'Close' : 'Recommend action'}
        </button>
      </div>

      {open && (
        <form onSubmit={submit} className="px-5 py-4 border-t border-gray-200 space-y-3">
          <div>
            <label className="block text-xs font-semibold uppercase tracking-wide text-gray-500 mb-1">
              Recommendation
            </label>
            <textarea
              value={recommendation}
              onChange={(e) => setRecommendation(e.target.value)}
              rows={3}
              placeholder="What should the care team do, and why?"
              className="w-full border border-gray-300 rounded-md px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-ns-navy/30"
            />
          </div>

          <div>
            <label className="block text-xs font-semibold uppercase tracking-wide text-gray-500 mb-1.5">
              Actions
            </label>
            <div className="flex flex-wrap gap-1.5">
              {actions.map((a) => (
                <button
                  type="button"
                  key={a.key}
                  onClick={() => toggle(a.key)}
                  className={`text-xs px-2.5 py-1 rounded-full border transition-colors ${
                    picked.includes(a.key)
                      ? 'bg-ns-navy text-white border-ns-navy'
                      : 'bg-white text-gray-700 border-gray-300 hover:border-gray-400'
                  }`}
                >
                  {a.label}
                </button>
              ))}
            </div>
            {/* api/followups.py BOOKING_ACTIONS: these become a follow-up request. */}
            {can(ALERT_REPLY_ROLES) && picked.some((k) => BOOKING_ACTIONS.includes(k)) && (
              <p className="mt-1.5 text-xs text-gray-500">
                Your hospital&apos;s case managers will get this as a follow-up request to arrange.
              </p>
            )}
          </div>

          <div className="flex items-end gap-3 flex-wrap">
            <div>
              <label className="block text-xs font-semibold uppercase tracking-wide text-gray-500 mb-1">
                Urgency
              </label>
              <select
                value={urgency}
                onChange={(e) => setUrgency(e.target.value)}
                className="border border-gray-300 rounded-md px-3 py-1.5 text-sm capitalize"
              >
                {urgencies.map((u) => <option key={u} value={u}>{u}</option>)}
              </select>
            </div>
            <button
              type="submit"
              disabled={busy === 'respond'}
              className="px-4 py-2 text-sm rounded-md bg-ns-navy text-white hover:bg-ns-navy/90 disabled:opacity-50 flex items-center gap-1.5"
            >
              {busy === 'respond' && <Loader2 size={14} className="animate-spin" />}
              Send to care team
            </button>
            <button
              type="button"
              onClick={dismiss}
              disabled={busy === 'dismiss'}
              className="px-3 py-2 text-sm rounded-md border border-gray-300 text-gray-600 hover:bg-gray-50 disabled:opacity-50"
              title="Close without a clinical recommendation. The text above is kept as the reason."
            >
              Not clinically significant
            </button>
          </div>

          {formError && <p className="text-sm text-risk-high">{formError}</p>}
        </form>
      )}
    </li>
  );
}

/* ── Registration ───────────────────────────────────────────────────────── */

function RegisterForm({ specialties, groups, onDone, onCancel }) {
  const [form, setForm] = useState({ name: '', specialty: '', email: '' });
  const [picked, setPicked] = useState([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');

  const submit = async (e) => {
    e.preventDefault();
    setBusy(true);
    setError('');
    try {
      onDone(await registerDoctor({ ...form, clinical_groups: picked }));
    } catch (err) { setError(err.message); } finally { setBusy(false); }
  };

  return (
    <form onSubmit={submit} className="bg-white rounded-lg border border-gray-200 p-5 space-y-4">
      <div className="flex items-center justify-between">
        <h2 className="font-semibold text-ns-navy">Register a clinician</h2>
        <button type="button" onClick={onCancel} className="text-gray-400 hover:text-gray-600">
          <X size={18} />
        </button>
      </div>

      <div className="grid gap-3 sm:grid-cols-3">
        <Field label="Name">
          <input
            required value={form.name}
            onChange={(e) => setForm({ ...form, name: e.target.value })}
            placeholder="Dr. Jane Okafor"
            className="w-full border border-gray-300 rounded-md px-3 py-2 text-sm"
          />
        </Field>
        <Field label="Specialty">
          <select
            required value={form.specialty}
            onChange={(e) => setForm({ ...form, specialty: e.target.value })}
            className="w-full border border-gray-300 rounded-md px-3 py-2 text-sm"
          >
            <option value="">Select…</option>
            {specialties.map((s) => <option key={s} value={s}>{s}</option>)}
          </select>
        </Field>
        <Field label="Email">
          <input
            required type="email" value={form.email}
            onChange={(e) => setForm({ ...form, email: e.target.value })}
            placeholder="j.okafor@hospital.org"
            className="w-full border border-gray-300 rounded-md px-3 py-2 text-sm"
          />
        </Field>
      </div>

      <div>
        <label className="block text-xs font-semibold uppercase tracking-wide text-gray-500 mb-1.5">
          Conditions covered
          <span className="font-normal normal-case tracking-normal text-gray-400">
            {' '}— optional; alerts for these route here first
          </span>
        </label>
        <div className="flex flex-wrap gap-1.5">
          {groups.map((g) => (
            <button
              type="button" key={g.key}
              onClick={() => setPicked((p) =>
                p.includes(g.key) ? p.filter((k) => k !== g.key) : [...p, g.key])}
              className={`text-xs px-2.5 py-1 rounded-full border transition-colors ${
                picked.includes(g.key)
                  ? 'bg-ns-navy text-white border-ns-navy'
                  : 'bg-white text-gray-700 border-gray-300 hover:border-gray-400'
              }`}
            >
              {g.label}
            </button>
          ))}
        </div>
      </div>

      {error && <p className="text-sm text-risk-high">{error}</p>}

      <p className="text-xs text-gray-500">
        Registering the same email again updates that clinician rather than creating
        a second one.
      </p>

      <button
        type="submit" disabled={busy}
        className="px-4 py-2 text-sm rounded-md bg-ns-navy text-white hover:bg-ns-navy/90 disabled:opacity-50 flex items-center gap-1.5"
      >
        {busy && <Loader2 size={14} className="animate-spin" />} Register
      </button>
    </form>
  );
}

/* ── Small pieces ───────────────────────────────────────────────────────── */

function ScanProgress({ scan, onDismiss }) {
  const running = scan.status === 'Running';
  const pct = scan.total ? Math.round((scan.scanned / scan.total) * 100) : 0;
  return (
    <div className="bg-white border border-gray-200 rounded-lg px-5 py-4">
      <div className="flex items-center justify-between gap-3">
        <span className="text-sm font-medium text-ns-navy flex items-center gap-2">
          {running && <Loader2 size={15} className="animate-spin" />}
          {running ? `Forecast sweep — ${scan.current_step}` : `Sweep ${scan.status.toLowerCase()}`}
        </span>
        {!running && (
          <button onClick={onDismiss} className="text-gray-400 hover:text-gray-600">
            <X size={16} />
          </button>
        )}
      </div>
      {running && scan.total ? (
        <>
          <div className="h-1.5 bg-gray-100 rounded-full mt-3 overflow-hidden">
            <div className="h-full bg-ns-navy transition-all" style={{ width: `${pct}%` }} />
          </div>
          <p className="text-xs text-gray-500 mt-1.5">
            {scan.scanned.toLocaleString()} of {scan.total.toLocaleString()} patients
          </p>
        </>
      ) : running ? (
        <p className="text-xs text-gray-500 mt-2">
          Loading the weekly series. This takes a minute or two over the full cohort.
        </p>
      ) : scan.status === 'Failed' ? (
        <p className="text-sm text-risk-high mt-2">{scan.error}</p>
      ) : (
        <p className="text-sm text-gray-700 mt-2">
          {scan.alerts_created} new alert{scan.alerts_created === 1 ? '' : 's'},{' '}
          {scan.alerts_updated} updated, {scan.no_alert} patients clear
          {scan.unrouted ? `, ${scan.unrouted} unrouted` : ''}.
        </p>
      )}
    </div>
  );
}

function Field({ label, children }) {
  return (
    <div>
      <label className="block text-xs font-semibold uppercase tracking-wide text-gray-500 mb-1">
        {label}
      </label>
      {children}
    </div>
  );
}

function Pill({ tone, label }) {
  return <span className={`px-2 py-0.5 rounded-full text-xs font-medium ${tone}`}>{label}</span>;
}

function EmptyState({ icon, title, body }) {
  return (
    <div className="bg-white rounded-lg border border-gray-200 p-10 text-center">
      <div className="text-gray-300 flex justify-center mb-3">{icon}</div>
      <h3 className="font-medium text-gray-800">{title}</h3>
      <p className="text-sm text-gray-500 mt-1 max-w-md mx-auto">{body}</p>
    </div>
  );
}
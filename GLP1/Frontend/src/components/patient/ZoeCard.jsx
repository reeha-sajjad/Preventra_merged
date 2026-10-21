import { useCallback, useEffect, useState } from 'react';
import { BellRing, CalendarDays, Pill, Plus, X, Loader2, Pencil, Check, Undo2 } from 'lucide-react';
import { api } from '../../data/api';

/**
 * Zoe, the patient's reminder companion (Backend/core/zoe.py). Fixed rules,
 * not an AI: Zoe reads the dose schedule and appointments the patient set and
 * says what's coming up. Everything stays in the app - no texts or emails.
 * The patient and the caretakers they linked share one plan (patientIdx picks
 * the patient for a caretaker), and a caretaker who gives the injection ticks
 * "Dose given" (Backend/core/doses.py), which the care team sees. "Today" is
 * worked out in the patient's own time zone, here in the browser.
 */

const pad = (n) => String(n).padStart(2, '0');
const isoDay = (d) => `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
const nice = (iso) => new Date(`${iso}T00:00:00`).toLocaleDateString(undefined, { weekday: 'long', day: 'numeric', month: 'long' });
// Shown the way the patient's own clock shows it (09:00 or 9:00 AM).
const clock = (hhmm) => {
  if (!hhmm) return '';
  const [h, m] = hhmm.split(':').map(Number);
  return new Date(2000, 0, 1, h, m).toLocaleTimeString(undefined, { hour: 'numeric', minute: '2-digit' });
};

/** What Zoe says about the medicine today. */
function doseMessage(plan, now = new Date()) {
  const { medicine, dose, days } = plan;
  if (medicine.stopped || !medicine.frequency && !medicine.needs_form) return null;
  if (!dose) return { tone: 'setup', text: `Tell me when you take your ${medicine.label} and I’ll remind you here.` };
  const at = clock(dose.time);
  const [h, m] = dose.time.split(':').map(Number);
  const passed = now.getHours() * 60 + now.getMinutes() > h * 60 + m;
  if (medicine.frequency === 'daily') {
    return { tone: 'today', text: passed ? `Your daily ${medicine.label} was due at ${at}. If you’ve taken it, you’re all set for today.`
                                         : `Your daily ${medicine.label} is due at ${at} today.` };
  }
  const today = (now.getDay() + 6) % 7;                    // 0 = Monday, as stored
  if (dose.day === today) {
    return { tone: 'today', text: passed ? `Today is your dose day. Your ${medicine.label} was due at ${at}.`
                                         : `Today is your dose day. Your ${medicine.label} is due at ${at}.` };
  }
  if (dose.day === (today + 1) % 7) return { tone: 'soon', text: `Tomorrow is your dose day - ${medicine.label} at ${at}.` };
  return { tone: 'later', text: `Your next ${medicine.label} dose is on ${days[dose.day]} at ${at}.` };
}

function whenLabel(appt, now = new Date()) {
  const today = isoDay(now);
  const tomorrow = isoDay(new Date(now.getFullYear(), now.getMonth(), now.getDate() + 1));
  const day = appt.date === today ? 'Today' : appt.date === tomorrow ? 'Tomorrow' : nice(appt.date);
  return appt.time ? `${day}, ${clock(appt.time)}` : day;
}

export default function ZoeCard({ patientIdx = null, patientName = null }) {
  const caretaker = patientIdx != null;
  const [plan, setPlan] = useState(null);
  const [given, setGiven] = useState(null);               // doses a caretaker recorded
  const [editing, setEditing] = useState(null);           // 'dose' | 'appointment' | null
  const [error, setError] = useState(null);
  const [ticking, setTicking] = useState(false);

  const load = useCallback(() => {
    api.getZoe(patientIdx).then(setPlan).catch(() => setPlan(false));
    api.getZoeDoses(patientIdx).then(setGiven).catch(() => setGiven(null));
  }, [patientIdx]);
  useEffect(() => { load(); }, [load]);
  if (!plan) return null;                                  // loading, or not available: the page works without Zoe

  const tick = async () => {
    setTicking(true); setError(null);
    try { setGiven(await api.doseGiven(null, patientIdx)); } catch (e) { setError(e.message); }
    finally { setTicking(false); }
  };
  const undo = async (id) => {
    setError(null);
    try { setGiven(await api.undoDose(id, patientIdx)); } catch (e) { setError(e.message); }
  };
  const lastGiven = given?.entries?.[0];

  const dose = doseMessage(plan);
  const today = isoDay(new Date());
  const soon = plan.appointments.filter((a) => a.date >= today).slice(0, 5);
  const saved = (next) => { setPlan(next); setEditing(null); setError(null); };

  return (
    <section className="card p-6" aria-labelledby="zoe-title">
      <div className="flex items-start gap-3">
        <div aria-hidden="true" className="w-10 h-10 flex-shrink-0 rounded-full flex items-center justify-center text-white text-lg"
             style={{ background: 'linear-gradient(140deg, #2E6DB4, #1B4F8A)', fontFamily: 'DM Serif Display, serif' }}>Z</div>
        <div className="min-w-0 flex-1">
          <h2 id="zoe-title" className="text-base font-semibold text-gray-800">Zoe</h2>
          <p className="text-xs text-gray-500">
            {caretaker ? `${patientName ? `${patientName}’s` : 'Their'} reminders, shared with them and anyone else caring for them.`
                       : 'Your reminders, shared with the people who help with your care.'}
          </p>
        </div>
      </div>

      <ul className="mt-4 space-y-2.5">
        {dose && (
          <li className="flex gap-3 rounded-xl px-4 py-3"
              style={{ background: dose.tone === 'today' ? '#EBF3FC' : dose.tone === 'soon' ? '#F3F7FC' : '#F8FAFC' }}>
            <Pill size={16} className="flex-shrink-0 mt-0.5" style={{ color: '#1B4F8A' }} />
            <div className="flex-1 text-sm text-gray-800">
              {dose.text}
              {plan.dose && (
                <button type="button" onClick={() => setEditing('dose')}
                  className="ml-2 inline-flex items-center gap-1 text-xs font-medium text-gray-500 hover:text-gray-800 underline-offset-2 hover:underline">
                  <Pencil size={11} /> Change
                </button>
              )}
            </div>
          </li>
        )}
        {soon.map((a) => {
          const close = a.date === today || whenLabel(a).startsWith('Tomorrow');
          return (
            <li key={a.id} className="flex gap-3 rounded-xl px-4 py-3" style={{ background: close ? '#FDF4E7' : '#F8FAFC' }}>
              <CalendarDays size={16} className="flex-shrink-0 mt-0.5" style={{ color: close ? '#94520A' : '#64748B' }} />
              <div className="flex-1 min-w-0 text-sm">
                <div className="text-gray-800"><span className="font-medium">{a.kind_label}</span> · {whenLabel(a)}</div>
                {a.note && <div className="text-xs text-gray-500 mt-0.5 break-words">{a.note}</div>}
              </div>
              <button type="button" aria-label={`Remove ${a.kind_label} on ${whenLabel(a)}`}
                onClick={() => api.removeZoeAppointment(a.id, patientIdx).then(saved).catch((e) => setError(e.message))}
                className="self-start text-gray-400 hover:text-gray-700"><X size={15} /></button>
            </li>
          );
        })}
        {!dose && soon.length === 0 && (
          <li className="text-sm text-gray-500">No reminders yet. Add an appointment and I’ll remind you here.</li>
        )}
      </ul>

      {/* Doses a caretaker recorded: this week (or today) at a glance, the
          last four weeks as a strip, and the recent entries. */}
      {given && !plan.medicine.stopped && (given.can_tick || lastGiven) && (
        <DoseLog given={given} ticking={ticking} onTick={tick} onUndo={undo} />
      )}

      {editing === 'dose' && <DoseForm plan={plan} patientIdx={patientIdx} onSaved={saved} onCancel={() => setEditing(null)} />}
      {editing === 'appointment' && <AppointmentForm plan={plan} patientIdx={patientIdx} onSaved={saved} onCancel={() => setEditing(null)} />}
      {error && <p role="alert" className="mt-3 text-sm" style={{ color: '#C62828' }}>{error}</p>}

      {!editing && (
        <div className="mt-4 flex flex-wrap gap-2">
          {dose && !plan.dose && (
            <button type="button" onClick={() => setEditing('dose')}
              className="inline-flex items-center gap-1.5 text-sm font-semibold px-3.5 py-2 rounded-lg text-white"
              style={{ background: 'var(--color-primary)' }}>
              <BellRing size={14} /> Set my dose reminder
            </button>
          )}
          <button type="button" onClick={() => setEditing('appointment')}
            className="inline-flex items-center gap-1.5 text-sm font-medium px-3.5 py-2 rounded-lg border border-gray-200 text-gray-700 hover:bg-gray-50">
            <Plus size={14} /> Add an appointment
          </button>
        </div>
      )}
      {plan.dose && <p className="mt-4 text-xs text-gray-500 leading-relaxed">{plan.missed_dose}</p>}
    </section>
  );
}

function FormShell({ title, onSubmit, onCancel, busy, error, children, submit }) {
  return (
    <form onSubmit={onSubmit} className="mt-4 rounded-xl border border-gray-200 p-4 space-y-3" aria-label={title}>
      <div className="text-sm font-semibold text-gray-800">{title}</div>
      {children}
      {error && <p role="alert" className="text-sm" style={{ color: '#C62828' }}>{error}</p>}
      <div className="flex justify-end gap-2">
        <button type="button" onClick={onCancel} className="text-sm px-3.5 py-2 rounded-lg border border-gray-200 text-gray-600 hover:bg-gray-50">Cancel</button>
        <button type="submit" disabled={busy}
          className="inline-flex items-center gap-2 text-sm font-semibold px-3.5 py-2 rounded-lg text-white disabled:opacity-50"
          style={{ background: 'var(--color-primary)' }}>
          {busy && <Loader2 size={14} className="animate-spin" />} {submit}
        </button>
      </div>
    </form>
  );
}

const field = 'mt-1 w-full text-sm rounded-lg border border-gray-200 px-3 py-2 focus:outline-none focus:ring-2 focus:ring-blue-300';

function DoseForm({ plan, patientIdx, onSaved, onCancel }) {
  const { medicine } = plan;
  const [form, setForm] = useState(plan.dose?.form || (medicine.needs_form ? '' : medicine.frequency));
  const [day, setDay] = useState(plan.dose?.day ?? '');
  const [time, setTime] = useState(plan.dose?.time || '09:00');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const weekly = (medicine.needs_form ? form : medicine.frequency) === 'weekly';

  const submit = async (e) => {
    e.preventDefault();
    setBusy(true); setError(null);
    try {
      onSaved(await api.setZoeDose({ time, day: weekly ? Number(day) : undefined, form: medicine.needs_form ? form : undefined }, patientIdx));
    } catch (err) { setError(err.message); setBusy(false); }
  };
  const remove = async () => {
    setBusy(true);
    try { onSaved(await api.clearZoeDose(patientIdx)); } catch (err) { setError(err.message); setBusy(false); }
  };

  return (
    <FormShell title={`When do you take your ${medicine.label}?`} onSubmit={submit} onCancel={onCancel}
               busy={busy} error={error} submit="Save reminder">
      {medicine.needs_form && (
        <label className="block text-sm text-gray-700">How do you take it?
          <select value={form} onChange={(e) => setForm(e.target.value)} className={field} required>
            <option value="" disabled>Choose one</option>
            {medicine.forms.map((f) => <option key={f.key} value={f.key}>{f.label}</option>)}
          </select>
        </label>
      )}
      <div className="grid gap-3 sm:grid-cols-2">
        {weekly && (
          <label className="block text-sm text-gray-700">Day of the week
            <select value={day} onChange={(e) => setDay(e.target.value)} className={field} required>
              <option value="" disabled>Choose a day</option>
              {plan.days.map((d, i) => <option key={d} value={i}>{d}</option>)}
            </select>
          </label>
        )}
        <label className="block text-sm text-gray-700">Time
          <input type="time" value={time} onChange={(e) => setTime(e.target.value)} className={field} required />
        </label>
      </div>
      {plan.dose && (
        <button type="button" onClick={remove} className="text-xs text-gray-500 hover:text-gray-800 underline">
          Turn off my dose reminder
        </button>
      )}
    </FormShell>
  );
}

function AppointmentForm({ plan, patientIdx, onSaved, onCancel }) {
  const [kind, setKind] = useState('doctor');
  const [date, setDate] = useState('');
  const [time, setTime] = useState('');
  const [note, setNote] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const today = isoDay(new Date());

  const submit = async (e) => {
    e.preventDefault();
    setBusy(true); setError(null);
    try {
      onSaved(await api.addZoeAppointment({ kind, date, time: time || undefined, note: note.trim() || undefined }, patientIdx));
    } catch (err) { setError(err.message); setBusy(false); }
  };

  return (
    <FormShell title="Add an appointment" onSubmit={submit} onCancel={onCancel} busy={busy} error={error} submit="Add appointment">
      <label className="block text-sm text-gray-700">What is it?
        <select value={kind} onChange={(e) => setKind(e.target.value)} className={field}>
          {plan.kinds.map((k) => <option key={k.key} value={k.key}>{k.label}</option>)}
        </select>
      </label>
      <div className="grid gap-3 sm:grid-cols-2">
        <label className="block text-sm text-gray-700">Date
          <input type="date" value={date} min={today} onChange={(e) => setDate(e.target.value)} className={field} required />
        </label>
        <label className="block text-sm text-gray-700">Time <span className="text-gray-400">(optional)</span>
          <input type="time" value={time} onChange={(e) => setTime(e.target.value)} className={field} />
        </label>
      </div>
      <label className="block text-sm text-gray-700">Note <span className="text-gray-400">(optional)</span>
        <input value={note} onChange={(e) => setNote(e.target.value)} maxLength={80} placeholder="For example, bring my medicine list"
               className={field} />
      </label>
    </FormShell>
  );
}


const DAY = 86400000;
const short = (d) => d.toLocaleDateString(undefined, { weekday: 'short', day: 'numeric', month: 'short' });

/** The dose log: weekly medicines as four weekly boxes, daily ones as the last
 * seven days, then the recent entries with who logged them. A carer can log
 * more than one (the backend only refuses a second tap within two hours). */
function DoseLog({ given, ticking, onTick, onUndo }) {
  const entries = given.entries || [];
  const at = entries.map((e) => new Date(e.given_at));
  const now = new Date();
  const daily = given.frequency === 'daily';
  // Rolling windows ending today: 4 weeks of 7 days, or 7 single days.
  const span = daily ? 1 : 7;
  const count = daily ? 7 : 4;
  const slots = Array.from({ length: count }, (_, i) => {
    const end = new Date(now.getFullYear(), now.getMonth(), now.getDate() + 1 - i * span);
    const begin = new Date(end - span * DAY);
    const hit = at.find((d) => d >= begin && d < end);
    const label = daily ? (i === 0 ? 'Today' : begin.toLocaleDateString(undefined, { weekday: 'short' }))
                        : (i === 0 ? 'This week' : i === 1 ? 'Last week' : `${i} weeks ago`);
    return { label, hit };
  }).reverse();
  const current = slots[slots.length - 1];
  return (
    <div className="mt-3 rounded-xl border border-gray-100 px-4 py-3 space-y-3">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="text-sm">
          <span className="font-semibold text-gray-800">{daily ? 'Today: ' : 'This week: '}</span>
          {current.hit
            ? <span style={{ color: '#1E6B34' }}>given {short(current.hit)} ✓</span>
            : <span style={{ color: '#B45309' }}>not logged yet</span>}
          {given.expected_4_weeks
            ? <span className="text-gray-500"> · {given.last_4_weeks} of {given.expected_4_weeks} doses in the last 4 weeks</span> : null}
        </div>
        {given.can_tick && (
          <button type="button" onClick={onTick} disabled={ticking}
            className="inline-flex items-center gap-1.5 text-sm font-semibold px-3.5 py-2 rounded-lg text-white disabled:opacity-50"
            style={{ background: '#1E6B34' }}>
            {ticking ? <Loader2 size={14} className="animate-spin" /> : <Check size={14} />} Log a dose given now
          </button>
        )}
      </div>

      <div className={`grid gap-2 ${daily ? 'grid-cols-7' : 'grid-cols-4'}`}>
        {slots.map((sl) => (
          <div key={sl.label} className="rounded-lg px-2 py-2 text-center border"
               style={{ background: sl.hit ? '#EAF5EC' : '#F8FAFC', borderColor: sl.hit ? '#BFE0C6' : '#EDF2F7' }}>
            <div className="text-[10px] font-semibold uppercase tracking-wide text-gray-400">{sl.label}</div>
            <div className="text-sm font-semibold mt-0.5" style={{ color: sl.hit ? '#1E6B34' : '#A0AEC0' }}>
              {sl.hit ? '✓' : '—'}
            </div>
          </div>
        ))}
      </div>

      {entries.length > 0 ? (
        <ul className="divide-y divide-gray-100">
          {entries.slice(0, 4).map((e) => (
            <li key={e.id} className="flex flex-wrap items-center justify-between gap-2 py-1.5 text-xs">
              <span className="text-gray-700">
                {new Date(e.given_at).toLocaleString(undefined, { weekday: 'short', day: 'numeric', month: 'short', hour: 'numeric', minute: '2-digit' })}
                <span className="text-gray-400"> · by {e.logged_by}</span>
                {e.note && <span className="text-gray-500"> · {e.note}</span>}
              </span>
              {e.can_undo && (
                <button type="button" onClick={() => onUndo(e.id)}
                  className="inline-flex items-center gap-1 font-medium text-gray-500 hover:text-gray-800 hover:underline">
                  <Undo2 size={11} /> Undo
                </button>
              )}
            </li>
          ))}
        </ul>
      ) : (
        <p className="text-xs text-gray-500">No doses logged yet.</p>
      )}
    </div>
  );
}
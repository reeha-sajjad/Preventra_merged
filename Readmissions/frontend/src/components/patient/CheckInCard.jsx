import React, { useCallback, useEffect, useState } from 'react';
import { ClipboardCheck, Loader2, ShieldAlert, CheckCircle2, ChevronDown } from 'lucide-react';
import { getCheckIn, sendCheckIn } from '../../api';

const formatDay = (iso) => (iso
  ? new Date(`${iso}T00:00:00`).toLocaleDateString(undefined, { weekday: 'long', day: 'numeric', month: 'long' })
  : '');

/**
 * The weekly check-in (api/checkins.py) on a patient's home page: this week's
 * questions, chosen by the patient's monitoring plan, sent once a week for the
 * 30 days after discharge. A caretaker answers for the person they care for
 * (patientId). The answers are scored like any other feed's; the patient never
 * sees a score - a red flag gets the safety advice instead. After the usual
 * questions come any the care team added for this patient, then a free-text
 * remark; neither is scored, and at least one usual question must be answered.
 */
export default function CheckInCard({ patientId = null, caretaker = false, patientName = null }) {
  const [data, setData] = useState(null);
  const [answers, setAnswers] = useState({});
  const [customAnswers, setCustomAnswers] = useState({});
  const [remarks, setRemarks] = useState('');
  const [started, setStarted] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const [result, setResult] = useState(null);

  const load = useCallback(() => {
    getCheckIn(patientId).then(setData).catch(() => setData(null));
  }, [patientId]);
  useEffect(() => { load(); }, [load]);

  if (!data) return null;
  // Nothing to say outside the 30 days, or before monitoring has started.
  if (!data.open && !data.done_this_week && data.closed_reason !== 'too_soon' && !result) return null;

  const who = caretaker && patientName ? patientName.split(' ')[0] : null;
  const set = (key, value) => setAnswers((a) => ({ ...a, [key]: value }));
  const filled = (v) => v !== '' && v !== undefined && v !== null;
  const answered = Object.values(answers).filter(filled).length;
  const custom = data.custom_questions || [];
  const customSet = (key, value) => setCustomAnswers((a) => ({ ...a, [key]: value }));
  const extras = remarks.trim() !== '' || Object.values(customAnswers).some((v) => filled(v) && String(v).trim() !== '');
  const reset = () => { setAnswers({}); setCustomAnswers({}); setRemarks(''); };
  const main = data.questions.filter((q) => !q.monitor);
  const monitor = data.questions.filter((q) => q.monitor);

  const submit = async (e) => {
    e.preventDefault();
    setBusy(true); setError(null);
    try {
      const asked = new Set(custom.map((q) => q.key));
      const sendCustom = Object.fromEntries(Object.entries(customAnswers).filter(([k]) => asked.has(k)));
      const out = await sendCheckIn(answers, patientId, sendCustom, remarks.trim() || null);
      setResult(out); setData(out); setStarted(false); reset();
    } catch (err) { setError(err.message); }
    finally { setBusy(false); }
  };

  return (
    <section className="rounded-xl border border-gray-200 bg-white p-6 shadow-sm" aria-labelledby="checkin-title">
      <h2 id="checkin-title" className="flex items-center gap-2 text-base font-semibold text-gray-900">
        <ClipboardCheck size={17} className="text-gray-400" />
        {who ? `${who}’s weekly check-in` : 'Your weekly check-in'}
      </h2>

      {result && (
        result.urgent ? (
          <div role="alert" className="mt-4 flex gap-3 rounded-lg border border-red-200 bg-red-50 p-4">
            <ShieldAlert size={18} className="mt-0.5 shrink-0 text-red-700" />
            <div className="text-sm text-red-900">
              <p className="font-semibold">{result.message}</p>
              {result.red_flags?.length > 0 && (
                <p className="mt-1 text-red-800">What needs looking at: {result.red_flags.join(', ')}.</p>
              )}
            </div>
          </div>
        ) : (
          <p role="status" className="mt-4 flex items-center gap-2 text-sm text-green-800">
            <CheckCircle2 size={16} /> {result.message}
          </p>
        )
      )}

      {data.closed_reason === 'too_soon' && (
        <p className="mt-3 text-sm text-gray-600">
          Check-ins start the day after going home. Each week a few questions about how {who || 'you'}{' '}
          {who ? 'is' : 'are'} doing go straight to the care team.
        </p>
      )}

      {data.done_this_week && !result && (
        <p className="mt-3 text-sm text-gray-700">This week’s check-in is in. Thank you.</p>
      )}
      {data.done_this_week && (
        <p className="mt-2 text-xs text-gray-500">
          {data.next_opens ? `The next one opens on ${formatDay(data.next_opens)}.`
            : `That was the last one - the 30 days finish on ${formatDay(data.window_ends)}.`}
        </p>
      )}

      {data.open && !started && (
        <div className="mt-3">
          <p className="text-sm text-gray-600">
            Week {data.week_number} since going home. A few questions - about two minutes. Answer what you can;
            every answer helps the care team.
          </p>
          <button type="button" onClick={() => { setStarted(true); setResult(null); }}
            className="mt-4 rounded-lg bg-ns-navy px-4 py-2.5 text-sm font-semibold text-white focus:outline-none focus-visible:ring-2 focus-visible:ring-blue-400 focus-visible:ring-offset-2">
            Start this week’s check-in
          </button>
        </div>
      )}

      {data.open && started && (
        <form onSubmit={submit} className="mt-4 space-y-5">
          {main.map((q) => <Question key={q.key} q={q} value={answers[q.key]} onChange={(v) => set(q.key, v)} />)}
          {monitor.length > 0 && (
            <details className="group rounded-lg border border-gray-200 px-4 py-3">
              <summary className="flex cursor-pointer list-none items-center justify-between text-sm font-medium text-gray-800">
                If you have a home monitor (optional)
                <ChevronDown size={16} className="text-gray-500 transition-transform group-open:rotate-180" />
              </summary>
              <div className="mt-4 space-y-5">
                {monitor.map((q) => <Question key={q.key} q={q} value={answers[q.key]} onChange={(v) => set(q.key, v)} />)}
              </div>
            </details>
          )}
          {custom.length > 0 && (
            <div className="space-y-5">
              <h3 className="text-sm font-semibold text-gray-900">From your care team</h3>
              {custom.map((q) => (
                <Question key={q.key} q={q} value={customAnswers[q.key]} onChange={(v) => customSet(q.key, v)} />
              ))}
            </div>
          )}
          <div>
            <label htmlFor="checkin-remarks" className="text-sm font-medium text-gray-900">
              Anything else you’d like your care team to know? (optional)
            </label>
            <textarea id="checkin-remarks" rows={3} maxLength={data.remarks_max || 500} value={remarks}
              onChange={(e) => setRemarks(e.target.value)}
              className="mt-2 w-full rounded-lg border border-gray-300 px-3 py-2 text-sm focus:border-ns-navy focus:outline-none focus:ring-2 focus:ring-blue-200" />
            <div className="mt-1 flex flex-wrap justify-between gap-2 text-xs text-gray-500">
              <span>Not for emergencies - if you feel much worse, call 911.</span>
              <span>{remarks.length}/{data.remarks_max || 500}</span>
            </div>
          </div>
          {answered === 0 && extras && (
            <p className="text-xs text-gray-500">Answer at least one of the questions above to send.</p>
          )}
          {error && <p role="alert" className="text-sm text-red-700">{error}</p>}
          <div className="flex flex-wrap items-center gap-3">
            <button type="submit" disabled={busy || answered === 0}
              className="inline-flex items-center gap-2 rounded-lg bg-ns-navy px-4 py-2.5 text-sm font-semibold text-white disabled:opacity-50">
              {busy && <Loader2 size={14} className="animate-spin" />} Send to the care team
            </button>
            <button type="button" onClick={() => { setStarted(false); reset(); setError(null); }}
              className="text-sm font-medium text-gray-600 hover:text-gray-900">Cancel</button>
            <span className="text-xs text-gray-500">{answered} answered</span>
          </div>
        </form>
      )}
    </section>
  );
}

function Question({ q, value, onChange }) {
  const id = `checkin-${q.key}`;
  if (q.kind === 'choice') {
    return (
      <fieldset>
        <legend className="text-sm font-medium text-gray-900">{q.label}</legend>
        {q.hint && <p className="mt-0.5 text-xs text-gray-500">{q.hint}</p>}
        <div className="mt-2 flex flex-wrap gap-2">
          {q.options.map((o) => {
            const on = String(value) === String(o.value);
            return (
              <label key={String(o.value)}
                className={`relative cursor-pointer rounded-full border px-3 py-1.5 text-sm focus-within:ring-2 focus-within:ring-blue-400 ${
                  on ? 'border-ns-navy bg-ns-navy text-white' : 'border-gray-300 bg-white text-gray-800 hover:border-gray-400'}`}>
                <input type="radio" name={id} value={String(o.value)} checked={on} className="sr-only"
                  onChange={() => onChange(o.value)} />
                {o.label}
              </label>
            );
          })}
        </div>
      </fieldset>
    );
  }
  if (q.kind === 'text') {
    return (
      <div>
        <label htmlFor={id} className="text-sm font-medium text-gray-900">{q.label}</label>
        <input id={id} type="text" maxLength={q.max} value={value ?? ''} onChange={(e) => onChange(e.target.value)}
          className="mt-2 w-full rounded-lg border border-gray-300 px-3 py-2 text-sm focus:border-ns-navy focus:outline-none focus:ring-2 focus:ring-blue-200" />
      </div>
    );
  }
  return (
    <div>
      <label htmlFor={id} className="text-sm font-medium text-gray-900">{q.label}</label>
      {q.hint && <p className="mt-0.5 text-xs text-gray-500">{q.hint}</p>}
      <div className="mt-2 flex items-center gap-2">
        <input id={id} type="number" inputMode="decimal" min={q.min} max={q.max} step={q.step}
          value={value ?? ''} onChange={(e) => onChange(e.target.value)}
          className="w-32 rounded-lg border border-gray-300 px-3 py-2 text-sm focus:border-ns-navy focus:outline-none focus:ring-2 focus:ring-blue-200" />
        {q.unit && <span className="text-sm text-gray-600">{q.unit}</span>}
      </div>
    </div>
  );
}

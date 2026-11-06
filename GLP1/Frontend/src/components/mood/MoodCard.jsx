import { useEffect, useState } from 'react';
import { HeartHandshake, Loader2 } from 'lucide-react';
import { api } from '../../data/api';

// "YYYY-MM-DD" as a local date, so a US time zone does not show the day before.
function day(iso) {
  const [y, m, d] = iso.split('-').map(Number);
  return new Date(y, m - 1, d).toLocaleDateString(undefined, { day: 'numeric', month: 'long', year: 'numeric' });
}

const filled = (v) => v !== undefined && v !== null && v !== '';

/**
 * The patient's own mood questions (Backend/core/mood.py), every 2 weeks. The
 * answers go to the care team; the patient is never shown a score or a flag,
 * only when they last answered and when the next questions are due. Not for a
 * caretaker: the server refuses them, so the page does not render this for them.
 */
export default function MoodCard() {
  const [data, setData] = useState(null);
  const [open, setOpen] = useState(false);
  const [answers, setAnswers] = useState({});
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const [sent, setSent] = useState(null);
  useEffect(() => { api.getMyMood().then(setData).catch(() => setData(null)); }, []);
  if (!data) return null;

  const form = data.form;
  // Both PHQ-2 answers in, and they add up to a positive screen: ask the rest.
  const showMore = (a) => {
    if (!form || !form.phq2.every((q) => filled(a[q.key]))) return false;
    return form.phq2.reduce((sum, q) => sum + Number(a[q.key]), 0) >= form.followup_from;
  };
  const set = (key, value) => setAnswers((a) => {
    const next = { ...a, [key]: value };
    if (!showMore(next)) form.phq8_more.forEach((q) => { delete next[q.key]; });
    return next;
  });
  const given = Object.fromEntries(Object.entries(answers).filter(([, v]) => filled(v)));
  const count = Object.keys(given).length;

  const submit = async (e) => {
    e.preventDefault();
    setBusy(true); setError(null);
    try {
      const out = await api.sendMyMood(given);
      setSent(out.message);
      setData(out);
      setOpen(false); setAnswers({});
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <section className="card p-6" aria-labelledby="mood-title">
      <h2 id="mood-title" className="flex items-center gap-2 text-base font-semibold text-gray-800">
        <HeartHandshake size={17} className="text-gray-400" /> How are you feeling?
      </h2>
      <div role="note" className="mt-3 rounded-lg border border-gray-200 bg-gray-50 px-3 py-2 text-sm text-gray-700">
        {data.safety}
      </div>
      {sent && <p className="mt-3 text-sm text-gray-700" role="status">{sent}</p>}

      {!open && data.due && (
        <>
          <p className="mt-3 text-sm text-gray-600">
            A few questions about the last 2 weeks. Your answers go to your care team.
          </p>
          <button type="button" onClick={() => setOpen(true)}
            className="mt-4 inline-flex items-center gap-2 text-sm font-semibold px-4 py-2.5 rounded-lg bg-primary text-white focus:outline-none focus-visible:ring-2 focus-visible:ring-offset-2 focus-visible:ring-blue-400">
            Answer the questions
          </button>
        </>
      )}

      {!open && !data.due && data.last_answered && (
        <p className="mt-3 text-sm text-gray-600">
          You last answered on {day(data.last_answered)}. The next questions are due on {day(data.next_due)}.
        </p>
      )}

      {open && form && (
        <form onSubmit={submit} className="mt-4 space-y-5">
          <p className="text-sm text-gray-600">{form.stem}</p>
          {[...form.phq2, ...(showMore(answers) ? form.phq8_more : []), ...form.gad2].map((q) => (
            <Question key={q.key} q={q} options={form.options} value={answers[q.key]} onChange={(v) => set(q.key, v)} />
          ))}
          {error && <p role="alert" className="text-sm text-red-700">{error}</p>}
          <div className="flex flex-wrap items-center gap-3">
            <button type="submit" disabled={busy || count === 0}
              className="inline-flex items-center gap-2 rounded-lg bg-primary px-4 py-2.5 text-sm font-semibold text-white disabled:opacity-50">
              {busy && <Loader2 size={14} className="animate-spin" />} Send to my care team
            </button>
            <button type="button" onClick={() => { setOpen(false); setAnswers({}); setError(null); }}
              className="text-sm font-medium text-gray-600 hover:text-gray-900">Cancel</button>
            <span className="text-xs text-gray-500">{count} answered</span>
          </div>
        </form>
      )}
    </section>
  );
}

function Question({ q, options, value, onChange }) {
  const id = `mood-${q.key}`;
  return (
    <fieldset>
      <legend className="text-sm font-medium text-gray-900">{q.label}</legend>
      <div className="mt-2 flex flex-wrap gap-2">
        {options.map((o) => {
          const on = String(value) === String(o.value);
          return (
            <label key={String(o.value)}
              className={`relative cursor-pointer rounded-full border px-3 py-1.5 text-sm focus-within:ring-2 focus-within:ring-blue-400 ${
                on ? 'border-primary bg-primary text-white' : 'border-gray-300 bg-white text-gray-800 hover:border-gray-400'}`}>
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

import React, { useCallback, useEffect, useState } from 'react';
import { ClipboardList, Check, Loader2, HeartHandshake } from 'lucide-react';
import { getDischargePlan, tickDischargeChecklist, recordSocialNeeds } from '../../api';

const when = (iso) => (iso
  ? new Date(iso).toLocaleDateString(undefined, { day: 'numeric', month: 'short', year: 'numeric' }) : '');

/**
 * The discharge plan (api/discharge_plan.py) on the staff patient page: a
 * checklist that depends on the patient's band, condition and home situation,
 * and the social needs asked at discharge with the action each one calls for.
 * Items backed by a fact on file (a linked caretaker, a pharmacy) tick
 * themselves. Read only for the roles that cannot edit patients.
 */
export default function DischargePlanCard({ patientId }) {
  const [plan, setPlan] = useState(null);
  const [busy, setBusy] = useState(null);
  const [error, setError] = useState(null);
  const [editing, setEditing] = useState(false);

  const load = useCallback(() => {
    getDischargePlan(patientId).then(setPlan).catch(() => setPlan(null));
  }, [patientId]);
  useEffect(() => { load(); }, [load]);
  if (!plan) return null;

  const toggle = async (item) => {
    setBusy(item.key); setError(null);
    try { setPlan(await tickDischargeChecklist(patientId, item.key, !item.done)); }
    catch (e) { setError(e.message); }
    finally { setBusy(null); }
  };
  const social = plan.social;
  const recorded = Boolean(social.recorded_at);

  return (
    <section className="rounded-xl border border-gray-200 bg-white p-6 shadow-sm" aria-labelledby="plan-title">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h2 id="plan-title" className="flex items-center gap-2 text-base font-semibold text-gray-900">
            <ClipboardList size={17} className="text-gray-400" /> Discharge plan
          </h2>
          <p className="mt-0.5 text-xs text-gray-500">
            For the discharge on {plan.discharge_date || 'record'} · what to have in place before and just after going home.
          </p>
        </div>
        <div className="text-right">
          <div className="text-2xl font-semibold text-gray-900">
            {plan.done} <span className="text-sm font-normal text-gray-500">of {plan.total}</span>
          </div>
          <div className="text-[11px] text-gray-500">done</div>
        </div>
      </div>

      <ul className="mt-4 space-y-2">
        {plan.items.map((item) => (
          <li key={item.key} className="flex items-start gap-3 rounded-lg border border-gray-100 p-3">
            <button type="button" onClick={() => toggle(item)} disabled={!item.can_toggle || busy === item.key}
              aria-pressed={item.done}
              aria-label={item.can_toggle ? `${item.done ? 'Untick' : 'Tick'}: ${item.label}`
                : `${item.label}: ${item.done ? 'done' : 'not done'}`}
              className={`mt-0.5 flex h-5 w-5 shrink-0 items-center justify-center rounded border ${
                item.done ? 'border-green-700 bg-green-700 text-white' : 'border-gray-400 bg-white'
              } ${item.can_toggle ? 'cursor-pointer' : 'cursor-default'} disabled:opacity-80`}>
              {busy === item.key ? <Loader2 size={12} className="animate-spin text-gray-500" /> : item.done && <Check size={13} />}
            </button>
            <div className="min-w-0 flex-1">
              <div className={`text-sm ${item.done ? 'text-gray-600' : 'font-medium text-gray-900'}`}>{item.label}</div>
              {item.hint && <div className="mt-0.5 text-xs text-gray-500">{item.hint}</div>}
              {item.done && (item.auto
                ? <div className="mt-0.5 text-xs text-gray-500">On file</div>
                : item.by && <div className="mt-0.5 text-xs text-gray-500">{item.by} · {when(item.at)}</div>)}
            </div>
          </li>
        ))}
      </ul>

      <div className="mt-5 border-t border-gray-100 pt-4">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <h3 className="flex items-center gap-2 text-sm font-semibold text-gray-900">
            <HeartHandshake size={15} className="text-gray-400" /> Social needs
          </h3>
          {plan.can_edit && !editing && (
            <button type="button" onClick={() => setEditing(true)} className="text-sm font-semibold text-ns-navy hover:underline">
              {recorded ? 'Update' : 'Ask and record'}
            </button>
          )}
        </div>
        {editing ? (
          <SocialForm social={social} onCancel={() => setEditing(false)}
            onSave={async (answers) => {
              const next = await recordSocialNeeds(patientId, answers);
              setPlan(next); setEditing(false);
            }} />
        ) : !recorded ? (
          <p className="mt-2 text-sm text-gray-500">Not asked yet. These don’t change the risk score - they change what the care team arranges.</p>
        ) : (
          <>
            {social.needs.length === 0 ? (
              <p className="mt-2 text-sm text-gray-700">No needs found.</p>
            ) : (
              <ul className="mt-3 space-y-2">
                {social.needs.map((n) => (
                  <li key={n.key} className="rounded-lg border border-amber-200 bg-amber-50 p-3 text-sm text-amber-900">
                    {n.action}
                  </li>
                ))}
              </ul>
            )}
            <p className="mt-2 text-xs text-gray-500">Recorded by {social.recorded_by} · {when(social.recorded_at)}</p>
          </>
        )}
      </div>
      {error && <p role="alert" className="mt-3 text-sm text-red-700">{error}</p>}
    </section>
  );
}

function SocialForm({ social, onSave, onCancel }) {
  const [answers, setAnswers] = useState(social.answers);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const save = async (e) => {
    e.preventDefault();
    setBusy(true); setError(null);
    try { await onSave(answers); } catch (err) { setError(err.message); setBusy(false); }
  };
  return (
    <form onSubmit={save} className="mt-3 space-y-4">
      {social.questions.map((q) => (
        <fieldset key={q.key}>
          <legend className="text-sm font-medium text-gray-900">{q.label}</legend>
          <div className="mt-1.5 flex flex-wrap gap-2">
            {q.options.map((o) => {
              const on = answers[q.key] === o.value;
              return (
                <label key={o.value}
                  className={`relative cursor-pointer rounded-full border px-3 py-1 text-sm focus-within:ring-2 focus-within:ring-blue-400 ${
                    on ? 'border-ns-navy bg-ns-navy text-white' : 'border-gray-300 bg-white text-gray-800 hover:border-gray-400'}`}>
                  <input type="radio" name={`social-${q.key}`} value={o.value} checked={on} className="sr-only"
                    onChange={() => setAnswers((a) => ({ ...a, [q.key]: o.value }))} />
                  {o.label}
                </label>
              );
            })}
          </div>
        </fieldset>
      ))}
      {error && <p role="alert" className="text-sm text-red-700">{error}</p>}
      <div className="flex items-center gap-3">
        <button type="submit" disabled={busy}
          className="inline-flex items-center gap-2 rounded-lg bg-ns-navy px-4 py-2 text-sm font-semibold text-white disabled:opacity-50">
          {busy && <Loader2 size={14} className="animate-spin" />} Save
        </button>
        <button type="button" onClick={onCancel} className="text-sm font-medium text-gray-600 hover:text-gray-900">Cancel</button>
      </div>
    </form>
  );
}

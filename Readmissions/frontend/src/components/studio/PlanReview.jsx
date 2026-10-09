import React, { useEffect, useMemo, useRef, useState } from 'react';
import { Sparkles, AlertTriangle, ListChecks, Loader2, ArrowRight, Info, History } from 'lucide-react';
import { scanStudioJob } from '../../api';
import { Card } from './StudioParts';
import { fmt } from './studioFormat';

const ROLE_LABEL = { numeric: 'Number', categorical: 'Category', drop: 'Leave out' };

function defaultRole(profileCol) {
  if (!profileCol) return 'drop';
  if (['identifier-like', 'text', 'date'].includes(profileCol.kind)) return 'drop';
  return ['numeric', 'binary'].includes(profileCol.kind) ? 'numeric' : 'categorical';
}

/**
 * The plan a person approves before anything trains: which column is the
 * outcome, which identifies the patient, which orders time, and what every
 * other column is. Gemini (or the name/type rules) proposed it; nothing here is
 * applied until "Train with this plan".
 */
export default function PlanReview({ job, onConfirm, busy, error }) {
  const { profile, plan } = job;
  const columns = Object.keys(profile?.columns || {});
  // The leakage scan, re-run whenever the outcome changes so its flags always
  // describe the outcome actually chosen (api/model_studio.scan_job).
  const [association, setAssociation] = useState(job.association || []);
  const [scanning, setScanning] = useState(false);
  const assoc = useMemo(() => Object.fromEntries(association.map((a) => [a.column, a])), [association]);
  const reasons = useMemo(() => {
    const out = {};
    (plan.drop_columns || []).forEach((d) => { out[d.column] = { kind: 'drop', text: d.reason }; });
    (plan.leakage_suspects || []).forEach((d) => { out[d.column] = { kind: 'leak', text: d.reason }; });
    return out;
  }, [plan]);

  const [label, setLabel] = useState(plan.label_column || '');
  const [positive, setPositive] = useState(plan.positive_value ?? '');
  // How the outcome becomes yes/no: a value that means yes, or "at or above a threshold".
  const [rule, setRule] = useState(plan.label_threshold != null ? 'threshold' : 'value');
  const [threshold, setThreshold] = useState(plan.label_threshold ?? '');
  const [idCol, setIdCol] = useState(plan.id_column || '');
  const [timeCol, setTimeCol] = useState(plan.time_column || '');
  const [roles, setRoles] = useState(() => {
    const r = {};
    columns.forEach((c) => { r[c] = 'drop'; });
    (plan.numeric_columns || []).forEach((c) => { r[c] = 'numeric'; });
    (plan.categorical_columns || []).forEach((c) => { r[c] = 'categorical'; });
    return r;
  });
  const [fair, setFair] = useState(new Set(plan.sensitive_columns || []));

  const reserved = new Set([label, idCol, timeCol].filter(Boolean));
  const roleOf = (c) => roles[c] ?? defaultRole(profile.columns[c]);
  const features = columns.filter((c) => !reserved.has(c) && roleOf(c) !== 'drop');
  const labelValues = Object.keys(profile.columns[label]?.values || {});
  const labelIsNumber = ['numeric', 'binary'].includes(profile.columns[label]?.kind);
  const weekly = job.target === 'weekly';
  const history = Boolean(idCol && timeCol);
  const outcomeRule = {
    label_column: label || null,
    positive_value: rule === 'value' && positive !== '' ? positive : null,
    label_threshold: rule === 'threshold' && threshold !== '' ? Number(threshold) : null,
    id_column: idCol || null, time_column: timeCol || null,
  };
  const ruleKey = JSON.stringify(outcomeRule);
  const firstScan = useRef(ruleKey);

  useEffect(() => {
    if (ruleKey === firstScan.current || !label) return undefined;
    if (rule === 'threshold' && threshold === '') return undefined;
    let live = true;
    const timer = setTimeout(() => {
      setScanning(true);
      scanStudioJob(job.job_id, JSON.parse(ruleKey))
        .then((a) => { if (live) setAssociation(a); })
        .catch(() => { /* the scan is advisory; confirming re-runs it */ })
        .finally(() => { if (live) setScanning(false); });
    }, 500);
    return () => { live = false; clearTimeout(timer); };
  }, [ruleKey]); // eslint-disable-line react-hooks/exhaustive-deps

  const submit = () => {
    const numeric = features.filter((c) => roleOf(c) === 'numeric');
    const categorical = features.filter((c) => roleOf(c) === 'categorical');
    const dropped = columns.filter((c) => !reserved.has(c) && roleOf(c) === 'drop')
      .map((c) => ({ column: c, reason: reasons[c]?.text || 'left out by reviewer' }));
    onConfirm({
      ...outcomeRule,
      numeric_columns: numeric, categorical_columns: categorical, drop_columns: dropped,
      sensitive_columns: [...fair].filter((c) => features.includes(c)),
    });
  };

  const select = (value, onChange, allowNone, noneText = 'None') => (
    <select value={value} onChange={(e) => onChange(e.target.value)}
      className="w-full rounded-lg border border-gray-300 bg-white px-2.5 py-1.5 text-sm focus:outline-none focus:ring-2 focus:ring-ns-navy/30">
      {allowNone && <option value="">{noneText}</option>}
      {!allowNone && !value && <option value="">Choose…</option>}
      {columns.map((c) => <option key={c} value={c}>{c}</option>)}
    </select>
  );

  const leaky = association.filter((a) => a.flag && features.includes(a.column));

  return (
    <div className="space-y-5">
      <Card title="Proposed plan" icon={<Sparkles size={18} />}
        right={<span className={`rounded-full px-2.5 py-0.5 text-xs font-medium ${job.plan_source === 'gemini' ? 'bg-indigo-50 text-indigo-700' : 'bg-gray-100 text-gray-600'}`}>
          {job.plan_source === 'gemini' ? 'Proposed by Gemini' : 'Proposed from column names and types'}
        </span>}>
        {plan.summary && <p className="mb-4 text-sm text-gray-700">{plan.summary}</p>}
        <div className="grid gap-4 md:grid-cols-4">
          <label className="text-sm">
            <span className="mb-1 block font-medium text-gray-700">Outcome (readmitted?)</span>
            {select(label, setLabel, false)}
          </label>
          <div className="text-sm">
            <span className="mb-1 block font-medium text-gray-700">Counts as "yes" when</span>
            {labelIsNumber && (
              <select value={rule} onChange={(e) => setRule(e.target.value)} aria-label="How the outcome becomes yes or no"
                className="mb-1.5 w-full rounded-lg border border-gray-300 bg-white px-2.5 py-1.5 text-sm">
                <option value="value">it has a particular value</option>
                <option value="threshold">it is at or above a threshold</option>
              </select>
            )}
            {labelIsNumber && rule === 'threshold' ? (
              <div className="flex items-center gap-2">
                <span className="text-gray-500">≥</span>
                <input type="number" value={threshold} onChange={(e) => setThreshold(e.target.value)}
                  placeholder="e.g. 40" aria-label="Threshold"
                  className="w-full rounded-lg border border-gray-300 px-2.5 py-1.5 text-sm" />
              </div>
            ) : labelValues.length ? (
              <select value={positive} onChange={(e) => setPositive(e.target.value)}
                className="w-full rounded-lg border border-gray-300 bg-white px-2.5 py-1.5 text-sm">
                <option value="">1 / yes / true</option>
                {labelValues.map((v) => <option key={v} value={v}>{v}</option>)}
              </select>
            ) : (
              <input value={positive} onChange={(e) => setPositive(e.target.value)} placeholder="1 / yes / true"
                className="w-full rounded-lg border border-gray-300 px-2.5 py-1.5 text-sm" />
            )}
          </div>
          <label className="text-sm">
            <span className="mb-1 block font-medium text-gray-700">
              Patient identifier{weekly && <span className="text-red-600"> *</span>}
            </span>
            {select(idCol, setIdCol, true)}
            <span className="mt-1 block text-xs text-gray-500">Keeps each patient on one side of the split.</span>
          </label>
          <label className="text-sm">
            <span className="mb-1 block font-medium text-gray-700">Date or week order</span>
            {select(timeCol, setTimeCol, true, 'None (random split)')}
            <span className="mt-1 block text-xs text-gray-500">The newest patients become the test set.</span>
          </label>
        </div>
        <div className={`mt-4 flex items-start gap-2 rounded-lg px-3 py-2 text-xs ${history
          ? 'bg-indigo-50 text-indigo-800' : 'bg-gray-50 text-gray-500'}`}>
          <History size={14} className="mt-0.5 shrink-0" />
          {history ? (
            <span><b>Patient history on.</b> The pipeline may build features from each patient's
              earlier rows of {timeCol} (change since last time, trends), never later ones. A look-ahead
              check enforces this before training. Files scored later must include each patient's
              earlier rows.</span>
          ) : (
            <span>Patient history off: it needs both a patient identifier and a date or week column.</span>
          )}
        </div>
      </Card>

      {(plan.data_issues?.length > 0 || leaky.length > 0) && (
        <div className="rounded-xl border border-amber-200 bg-amber-50 p-4 text-sm text-amber-900">
          <div className="mb-1.5 flex items-center gap-1.5 font-semibold"><AlertTriangle size={16} /> Worth checking</div>
          <ul className="space-y-1">
            {leaky.map((a) => (
              <li key={a.column}>• <b>{a.column}</b> predicts the outcome {a.flag === 'very likely leakage'
                ? 'almost perfectly' : 'very strongly'} on its own (AUROC {fmt(a.auroc)}). If it only
                becomes known after the moment of prediction, leave it out.</li>
            ))}
            {(plan.data_issues || []).map((d, i) => <li key={i}>• {d}</li>)}
          </ul>
        </div>
      )}

      <Card title={`Columns (${features.length} used as features)`} icon={<ListChecks size={18} />}>
        <div className="overflow-x-auto">
          <table className="w-full text-left text-sm">
            <thead className="text-xs text-gray-500">
              <tr>
                <th className="pb-2 pr-3">Column</th><th className="pb-2 pr-3">Use as</th>
                <th className="pb-2 pr-3">Type seen</th><th className="pb-2 pr-3">Missing</th>
                <th className="pb-2 pr-3">Distinct</th>
                <th className="pb-2 pr-3" title="AUROC of this column alone. Near 1.0 usually means leakage.">
                  Alone predicts {scanning && <Loader2 size={12} className="inline animate-spin text-gray-400" />}
                </th>
                <th className="pb-2 pr-3" title="Report performance separately for each value of this column">Fairness</th>
                <th className="pb-2">Note</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-100">
              {columns.map((c) => {
                const p = profile.columns[c];
                const isReserved = reserved.has(c);
                const a = assoc[c];
                const why = reasons[c];
                return (
                  <tr key={c} className={isReserved ? 'bg-ns-navy/5' : ''}>
                    <td className="py-1.5 pr-3 font-medium text-gray-800">{c}</td>
                    <td className="py-1.5 pr-3">
                      {isReserved ? (
                        <span className="text-xs font-semibold text-ns-navy">
                          {c === label ? 'Outcome' : c === idCol ? 'Patient id' : 'Time order'}
                        </span>
                      ) : (
                        <select value={roleOf(c)} onChange={(e) => setRoles({ ...roles, [c]: e.target.value })}
                          className={`rounded-md border px-1.5 py-0.5 text-xs ${roleOf(c) === 'drop' ? 'border-gray-200 text-gray-400' : 'border-gray-300 text-gray-800'}`}>
                          {Object.entries(ROLE_LABEL).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
                        </select>
                      )}
                    </td>
                    <td className="py-1.5 pr-3 text-xs text-gray-500">{p.kind}</td>
                    <td className="py-1.5 pr-3 text-xs text-gray-500">{p.missing_pct}%</td>
                    <td className="py-1.5 pr-3 text-xs text-gray-500">{p.n_unique.toLocaleString()}</td>
                    <td className="py-1.5 pr-3 text-xs">
                      {a ? (
                        <span className={a.flag === 'very likely leakage' ? 'font-semibold text-red-700'
                          : a.flag ? 'font-semibold text-amber-700' : 'text-gray-500'}>{fmt(a.auroc)}</span>
                      ) : <span className="text-gray-300">—</span>}
                    </td>
                    <td className="py-1.5 pr-3">
                      {!isReserved && roleOf(c) !== 'drop' && (
                        <input type="checkbox" checked={fair.has(c)} className="accent-ns-navy"
                          aria-label={`Check fairness across ${c}`}
                          onChange={() => {
                            const next = new Set(fair);
                            if (next.has(c)) next.delete(c); else next.add(c);
                            setFair(next);
                          }} />
                      )}
                    </td>
                    <td className="py-1.5 text-xs">
                      {why && <span className={why.kind === 'leak' ? 'text-red-700' : 'text-gray-500'}>
                        {why.kind === 'leak' ? 'Possible leakage: ' : ''}{why.text}</span>}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </Card>

      {error && <div className="rounded-lg border border-red-200 bg-red-50 p-3 text-sm text-red-700">{error}</div>}
      <div className="flex flex-wrap items-center justify-between gap-3">
        <p className="flex items-center gap-1.5 text-xs text-gray-500">
          <Info size={14} /> Gemini writes the pipeline next. It sees the column statistics above, never patient rows.
        </p>
        <button type="button" onClick={submit}
          disabled={busy || !label || (weekly && !idCol) || !features.length
            || (rule === 'threshold' && labelIsNumber && threshold === '')}
          className="inline-flex items-center gap-2 rounded-lg bg-ns-navy px-4 py-2 text-sm font-semibold text-white hover:bg-ns-navy/90 disabled:opacity-50">
          {busy ? <Loader2 size={16} className="animate-spin" /> : <ArrowRight size={16} />}
          Train with this plan
        </button>
      </div>
    </div>
  );
}

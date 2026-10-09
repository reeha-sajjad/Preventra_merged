import React, { useState } from 'react';
import {
  CheckCircle2, Circle, Loader2, PauseCircle, XCircle, AlertTriangle, Info, Download,
  ShieldCheck, ShieldAlert, ShieldX, Sparkles, ChevronRight,
} from 'lucide-react';
import {
  LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer, BarChart, Bar,
  ReferenceLine, Legend,
} from 'recharts';

import { download, fmt } from './studioFormat';

export function StepIcon({ status }) {
  // shrink-0: in a narrow step card the label wraps, and without it the
  // flex row squeezes the icon down to a sliver.
  if (status === 'done') return <CheckCircle2 size={18} className="shrink-0 text-emerald-600" />;
  if (status === 'running') return <Loader2 size={18} className="shrink-0 animate-spin text-ns-navy" />;
  if (status === 'waiting') return <PauseCircle size={18} className="shrink-0 text-amber-500" />;
  if (status === 'failed') return <XCircle size={18} className="shrink-0 text-red-600" />;
  return <Circle size={18} className="shrink-0 text-gray-300" />;
}

export function CheckIcon({ status }) {
  if (status === 'pass') return <CheckCircle2 size={16} className="mt-0.5 shrink-0 text-emerald-600" />;
  if (status === 'warn') return <AlertTriangle size={16} className="mt-0.5 shrink-0 text-amber-500" />;
  if (status === 'fail') return <XCircle size={16} className="mt-0.5 shrink-0 text-red-600" />;
  return <Info size={16} className="mt-0.5 shrink-0 text-gray-400" />;
}

const VERDICT_STYLE = {
  deploy: { Icon: ShieldCheck, cls: 'bg-emerald-50 border-emerald-200 text-emerald-800' },
  caution: { Icon: ShieldAlert, cls: 'bg-amber-50 border-amber-200 text-amber-800' },
  do_not_deploy: { Icon: ShieldX, cls: 'bg-red-50 border-red-200 text-red-800' },
};

export function VerdictBadge({ verdict, size = 'sm' }) {
  if (!verdict) return null;
  const { Icon, cls } = VERDICT_STYLE[verdict.decision] || VERDICT_STYLE.caution;
  return (
    <span className={`inline-flex items-center gap-1 rounded-full border px-2 py-0.5 font-medium ${cls} ${size === 'sm' ? 'text-xs' : 'text-sm'}`}>
      <Icon size={size === 'sm' ? 12 : 15} /> {verdict.headline}
    </span>
  );
}

export function Card({ title, icon, right, children, className = '' }) {
  return (
    <section className={`rounded-xl border border-gray-200 bg-white shadow-sm ${className}`}>
      {(title || right) && (
        <div className="flex items-center justify-between gap-3 border-b border-gray-100 px-5 py-3">
          <h3 className="flex items-center gap-2 font-semibold text-ns-navy">{icon}{title}</h3>
          {right}
        </div>
      )}
      <div className="p-5">{children}</div>
    </section>
  );
}

export function Metric({ label, value, sub, hint }) {
  return (
    <div className="rounded-lg border border-gray-100 bg-gray-50 p-3" title={hint}>
      <div className="text-xs text-gray-500">{label}</div>
      <div className="text-xl font-bold text-gray-800">{value}</div>
      {sub && <div className="mt-0.5 text-xs text-gray-500">{sub}</div>}
    </div>
  );
}

const CODE_TABS = [
  ['preprocess', 'Preprocessing'], ['transform', 'Transformation'], ['train', 'Training'],
  ['helpers', 'Imports & helpers'], ['full', 'Whole file'],
];

/** The pipeline code, split into the three stages a reviewer reads in turn. */
export function CodeViewer({ code, sections, origin, attempts, filename = 'pipeline_code.py' }) {
  const [tab, setTab] = useState('preprocess');
  if (!code) return null;
  const body = tab === 'full' ? code : (sections?.[tab] || sections?.module || code);
  const failed = (attempts || []).filter((a) => a.error);
  return (
    <div>
      <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
        <span className={`inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 text-xs font-medium ${origin === 'gemini' ? 'bg-indigo-50 text-indigo-700' : 'bg-gray-100 text-gray-700'}`}>
          <Sparkles size={13} />
          {origin === 'gemini'
            ? `Written by Gemini${attempts?.length ? ` · attempt ${attempts.length}` : ''}`
            : 'Preventra standard pipeline'}
        </span>
        <button type="button" onClick={() => download(code, filename, 'text/x-python')}
          className="inline-flex items-center gap-1.5 rounded-lg border border-gray-300 px-2.5 py-1 text-xs font-medium text-gray-700 hover:bg-gray-50">
          <Download size={13} /> Download .py
        </button>
      </div>
      <div className="mb-2 flex flex-wrap gap-1">
        {CODE_TABS.map(([key, label]) => (
          <button key={key} type="button" onClick={() => setTab(key)}
            className={`rounded-md px-2.5 py-1 text-xs font-medium ${tab === key ? 'bg-ns-navy text-white' : 'text-gray-600 hover:bg-gray-100'}`}>
            {label}
          </button>
        ))}
      </div>
      <pre className="max-h-[28rem] overflow-auto rounded-lg bg-slate-900 p-4 text-xs leading-relaxed text-slate-100"><code>{body}</code></pre>
      {failed.length > 0 && (
        <details className="group mt-3 text-xs">
          <summary className="flex cursor-pointer list-none items-center gap-1 font-semibold text-amber-700">
            <ChevronRight size={13} className="transition-transform group-open:rotate-90" />
            {failed.length} earlier attempt{failed.length > 1 ? 's' : ''} failed and {failed.length > 1 ? 'were' : 'was'} corrected
          </summary>
          <div className="mt-2 space-y-2">
            {failed.map((a) => (
              <div key={a.attempt}>
                <pre className="overflow-auto whitespace-pre-wrap rounded bg-amber-50 p-2 text-amber-900">
                  Attempt {a.attempt} ({a.stage}): {a.error}
                </pre>
                {a.code && (
                  <details className="mt-1">
                    <summary className="cursor-pointer text-amber-700">Show the code that failed</summary>
                    <pre className="mt-1 max-h-64 overflow-auto rounded bg-slate-900 p-3 text-slate-100"><code>{a.code}</code></pre>
                  </details>
                )}
              </div>
            ))}
          </div>
        </details>
      )}
    </div>
  );
}

export function RocChart({ points, auroc }) {
  if (!points?.length) return null;
  return (
    <div className="h-56">
      <ResponsiveContainer width="100%" height="100%">
        <LineChart data={points} margin={{ top: 5, right: 10, bottom: 15, left: -10 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="#f0f0f0" />
          <XAxis dataKey="fpr" type="number" domain={[0, 1]} tick={{ fontSize: 11 }}
            label={{ value: 'False positive rate', position: 'insideBottom', offset: -8, fontSize: 11 }} />
          <YAxis type="number" domain={[0, 1]} tick={{ fontSize: 11 }} />
          <Tooltip formatter={(v) => fmt(v, 2)} />
          <ReferenceLine segment={[{ x: 0, y: 0 }, { x: 1, y: 1 }]} stroke="#cbd5e1" strokeDasharray="4 4" />
          <Line type="monotone" dataKey="tpr" name={`ROC (AUROC ${fmt(auroc)})`} stroke="#0F2A4A"
            strokeWidth={2} dot={false} />
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}

export function CalibrationChart({ bins }) {
  if (!bins?.length) return null;
  const data = bins.map((b) => ({ predicted: b.predicted * 100, observed: b.observed * 100, n: b.n }));
  const max = Math.max(...data.map((d) => Math.max(d.predicted, d.observed)), 10);
  return (
    <div className="h-56">
      <ResponsiveContainer width="100%" height="100%">
        <LineChart data={data} margin={{ top: 5, right: 10, bottom: 15, left: -10 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="#f0f0f0" />
          <XAxis dataKey="predicted" type="number" domain={[0, Math.ceil(max)]} tick={{ fontSize: 11 }}
            tickFormatter={(v) => `${Math.round(v)}%`}
            label={{ value: 'Predicted risk', position: 'insideBottom', offset: -8, fontSize: 11 }} />
          <YAxis type="number" domain={[0, Math.ceil(max)]} tick={{ fontSize: 11 }}
            tickFormatter={(v) => `${Math.round(v)}%`} />
          <Tooltip formatter={(v, name) => [`${Number(v).toFixed(1)}%`, name]} />
          <Legend verticalAlign="top" height={20} wrapperStyle={{ fontSize: 11 }} />
          <ReferenceLine segment={[{ x: 0, y: 0 }, { x: max, y: max }]} stroke="#cbd5e1" strokeDasharray="4 4" />
          <Line type="monotone" dataKey="observed" name="Observed readmission rate" stroke="#C0392B"
            strokeWidth={2} dot={{ r: 3 }} />
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}

export function HistogramChart({ histogram, threshold }) {
  if (!histogram?.length) return null;
  const data = histogram.map((h) => ({ bin: `${Math.round(h.from * 100)}`, n: h.n, from: h.from }));
  return (
    <div className="h-56">
      <ResponsiveContainer width="100%" height="100%">
        <BarChart data={data} margin={{ top: 5, right: 10, bottom: 15, left: -10 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="#f0f0f0" vertical={false} />
          <XAxis dataKey="bin" tick={{ fontSize: 11 }}
            label={{ value: 'Predicted risk (%)', position: 'insideBottom', offset: -8, fontSize: 11 }} />
          <YAxis tick={{ fontSize: 11 }} />
          <Tooltip formatter={(v) => [v, 'Patients']} labelFormatter={(l) => `${l}–${Number(l) + 5}%`} />
          {threshold != null && (
            <ReferenceLine x={`${Math.floor(threshold * 20) * 5}`} stroke="#C0392B" strokeDasharray="4 4"
              label={{ value: 'Threshold', fontSize: 10, fill: '#C0392B', position: 'top' }} />
          )}
          <Bar dataKey="n" fill="#0F2A4A" radius={[3, 3, 0, 0]} />
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}

export function ImportanceBars({ items }) {
  if (!items?.length) return <p className="text-sm text-gray-500">Not available for this model.</p>;
  const top = items.slice(0, 12);
  const max = Math.max(...top.map((i) => i.importance), 1e-9);
  return (
    <div className="space-y-1.5">
      {top.map((i) => (
        <div key={i.feature} className="flex items-center gap-2 text-xs">
          <span className="w-44 shrink-0 truncate text-gray-700" title={i.feature}>{i.feature}</span>
          <div className="h-2.5 flex-1 rounded bg-gray-100">
            <div className="h-2.5 rounded bg-ns-navy" style={{ width: `${Math.max(i.importance / max, 0) * 100}%` }} />
          </div>
          <span className="w-14 text-right tabular-nums text-gray-500">{fmt(i.importance, 3)}</span>
        </div>
      ))}
      <p className="pt-1 text-[11px] text-gray-400">
        Drop in test AUROC when the feature is shuffled. Higher means the model relies on it more.
      </p>
    </div>
  );
}

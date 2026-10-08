import React from 'react';
import { Gauge, Scale, BarChart3, Users, Sparkles, Activity } from 'lucide-react';
import {
  Card, Metric, CheckIcon, VerdictBadge, RocChart, CalibrationChart, HistogramChart,
  ImportanceBars,
} from './StudioParts';
import { fmt, pct } from './studioFormat';

/**
 * What a trained model achieved on rows it never saw, against the alternatives,
 * with the deploy recommendation on top. Every number here is computed by the
 * backend's own evaluation (api/model_studio.compute_metrics), not reported by
 * the generated pipeline.
 */
export default function ModelReport({ evaluation, verdict, explanation, trainReport }) {
  if (!evaluation) return null;
  const m = evaluation.metrics;
  const split = evaluation.split || {};
  const report = trainReport?.report || {};

  return (
    <div className="space-y-5">
      <VerdictCard verdict={verdict} explanation={explanation} />

      <Card title="Performance on held-out patients" icon={<Gauge size={18} />}
        right={<span className="text-xs text-gray-500">
          Split by {evaluation.split_method} · test {split.test?.rows?.toLocaleString()} rows,{' '}
          {split.test?.positives?.toLocaleString()} readmitted
        </span>}>
        <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
          <Metric label="AUROC" value={fmt(m.auroc)}
            sub={`95% CI ${fmt(m.auroc_ci?.[0])}–${fmt(m.auroc_ci?.[1])}`}
            hint="How well the model ranks readmitted patients above the rest. 0.5 is chance, 1.0 is perfect." />
          <Metric label="AUPRC" value={fmt(m.auprc)} sub={`base rate ${pct(m.prevalence)}`}
            hint="Precision-recall area. Compare with the base rate, not with 1.0." />
          <Metric label="Brier score" value={fmt(m.brier)} sub="lower is better"
            hint="Mean squared error of the predicted probabilities." />
          <Metric label="Calibration" value={`slope ${fmt(m.calibration_slope, 2)}`}
            sub={`predicted ÷ observed ${fmt(m.calibration_ratio, 2)}`}
            hint="A slope of 1.0 and a ratio of 1.0 mean the scores read as true probabilities." />
          <Metric label="Precision" value={pct(m.precision)} sub="of flagged patients readmitted" />
          <Metric label="Recall" value={pct(m.recall)} sub="of readmissions flagged" />
          <Metric label="Flagged" value={`${m.flagged_pct}%`} sub={`threshold ${pct(m.threshold)}`}
            hint={`Threshold: ${m.threshold_basis}`} />
          <Metric label="F1 / specificity" value={fmt(m.f1, 2)} sub={`specificity ${pct(m.specificity)}`} />
        </div>
        <p className="mt-3 text-xs text-gray-500">
          The threshold was chosen on separate validation rows ({m.threshold_basis}), so precision and
          recall above are not tuned to the test set.
          {report.algorithm && <> Model: <b>{report.algorithm.replace(/_/g, ' ')}</b>
            {report.calibration && <> with {report.calibration} calibration</>}.</>}
        </p>
      </Card>

      <div className="grid gap-5 lg:grid-cols-3">
        <Card title="ROC curve" icon={<Activity size={18} />}><RocChart points={m.roc_curve} auroc={m.auroc} /></Card>
        <Card title="Calibration" icon={<Scale size={18} />}>
          <CalibrationChart bins={m.calibration_bins} />
          <p className="mt-1 text-[11px] text-gray-400">Dashed line = perfect calibration.</p>
        </Card>
        <Card title="Score distribution" icon={<BarChart3 size={18} />}>
          <HistogramChart histogram={m.score_histogram} threshold={m.threshold} />
        </Card>
      </div>

      <div className="grid gap-5 lg:grid-cols-2">
        <Card title="Compared on the same test patients" icon={<Scale size={18} />}>
          <table className="w-full text-left text-sm">
            <thead className="text-xs text-gray-500">
              <tr><th className="pb-2 pr-3">Model</th><th className="pb-2 pr-3">AUROC</th>
                <th className="pb-2 pr-3">Brier</th><th className="pb-2">Difference (95% CI)</th></tr>
            </thead>
            <tbody className="divide-y divide-gray-100">
              <tr className="font-semibold text-ns-navy">
                <td className="py-2">This model</td><td>{fmt(m.auroc)}</td><td>{fmt(m.brier)}</td><td>—</td>
              </tr>
              {(evaluation.comparisons || []).map((c) => (
                <tr key={c.name} className="align-top text-gray-700">
                  <td className="py-2 pr-2">
                    {c.name}
                    {c.kind !== 'baseline' && (
                      <div className="text-[11px] text-gray-400">
                        {c.kind === 'base' ? 'model being retrained' : 'currently active'}
                      </div>
                    )}
                    {c.note && <div className="text-xs text-amber-700">{c.note}</div>}
                  </td>
                  <td>{fmt(c.auroc)}</td><td>{fmt(c.brier)}</td>
                  <td>{c.delta_auroc ? (
                    <span className={c.delta_auroc.ci_low > 0 ? 'text-emerald-700'
                      : c.delta_auroc.ci_high < 0 ? 'text-red-700' : 'text-gray-600'}>
                      {c.delta_auroc.estimate > 0 ? '+' : ''}{fmt(c.delta_auroc.estimate)}{' '}
                      <span className="text-xs">({fmt(c.delta_auroc.ci_low)} to {fmt(c.delta_auroc.ci_high)})</span>
                    </span>
                  ) : '—'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </Card>
        <Card title="What the model relies on" icon={<BarChart3 size={18} />}>
          <ImportanceBars items={evaluation.importance} />
        </Card>
      </div>

      {evaluation.subgroups?.length > 0 && (
        <Card title="Performance by group" icon={<Users size={18} />}>
          <div className="overflow-x-auto">
            <table className="w-full text-left text-sm">
              <thead className="text-xs text-gray-500">
                <tr><th className="pb-2 pr-3">Group</th>
                  <th className="pb-2 pr-3">{evaluation.subgroups[0]?.unit === 'rows' ? 'Rows' : 'Patients'}</th>
                  <th className="pb-2 pr-3">Readmitted</th><th className="pb-2">AUROC</th></tr>
              </thead>
              <tbody className="divide-y divide-gray-100">
                {evaluation.subgroups.map((s) => {
                  const low = s.auroc != null && s.auroc < m.auroc - 0.08;
                  return (
                    <tr key={`${s.column}-${s.level}`} className={low ? 'text-amber-800' : 'text-gray-700'}>
                      <td className="py-1.5">{s.column} = <b>{s.level}</b></td>
                      <td>{s.n.toLocaleString()}</td><td>{s.positives.toLocaleString()}</td>
                      <td>{s.auroc != null ? fmt(s.auroc) : <span className="text-xs text-gray-400">too few to say</span>}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </Card>
      )}
    </div>
  );
}

function VerdictCard({ verdict, explanation }) {
  if (!verdict) return null;
  const tone = {
    deploy: 'border-emerald-200 bg-emerald-50/60',
    caution: 'border-amber-200 bg-amber-50/60',
    do_not_deploy: 'border-red-200 bg-red-50/60',
  }[verdict.decision];
  return (
    <section className={`rounded-xl border p-5 ${tone}`}>
      <div className="mb-3 flex flex-wrap items-center gap-3">
        <VerdictBadge verdict={verdict} size="md" />
        <span className="text-xs text-gray-500">Decided by fixed rules; explained by{' '}
          {explanation?.source === 'gemini' ? 'Gemini' : 'the rules themselves'}</span>
      </div>
      {explanation?.bullets?.length > 0 && explanation.source === 'gemini' && (
        <div className="mb-4 rounded-lg border border-white/60 bg-white/70 p-3">
          <div className="mb-1.5 flex items-center gap-1.5 text-xs font-semibold text-indigo-700">
            <Sparkles size={13} /> In plain words
          </div>
          <ul className="space-y-1 text-sm text-gray-700">
            {explanation.bullets.map((b, i) => <li key={i}>• {b}</li>)}
          </ul>
        </div>
      )}
      <ul className="space-y-1.5">
        {verdict.checks.map((c) => (
          <li key={c.name} className="flex items-start gap-2 text-sm">
            <CheckIcon status={c.status} />
            <span><b className="text-gray-800">{c.name}.</b> <span className="text-gray-700">{c.detail}</span></span>
          </li>
        ))}
      </ul>
    </section>
  );
}

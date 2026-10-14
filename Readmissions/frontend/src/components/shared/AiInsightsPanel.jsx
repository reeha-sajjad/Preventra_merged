import React, { useState } from 'react';
import { getAiInsights } from '../../api';
import { can, COST_ROLES } from '../../roles';
import BulletList from './BulletList';
import { Sparkles, Loader2, AlertCircle, DollarSign, Lightbulb, ChevronRight,
         ShieldCheck, TrendingUp, AlertTriangle } from 'lucide-react';

function formatUsd(value) {
  const n = Number(value);
  if (Number.isNaN(n)) return '—';
  return n.toLocaleString('en-US', { style: 'currency', currency: 'USD', maximumFractionDigits: 0 });
}


/** One itemised cost block: every line that makes up a total, then the total. */
function CostTable({ title, items, total }) {
  if (!items?.length) return null;
  return (
    <div>
      <div className="text-xs font-semibold text-gray-700 mb-1.5">{title}</div>
      <table className="w-full text-xs">
        <tbody>
          {items.map((line) => (
            <tr key={line.item} className="border-b border-gray-100">
              <td className="py-1.5 pr-3 text-gray-700">{line.item}</td>
              <td className="py-1.5 pr-3 text-gray-400">{line.basis}</td>
              <td className="py-1.5 text-right text-gray-800 whitespace-nowrap">{formatUsd(line.amount)}</td>
            </tr>
          ))}
          <tr>
            <td className="pt-1.5 pr-3 font-semibold text-gray-800" colSpan={2}>Total</td>
            <td className="pt-1.5 text-right font-semibold text-gray-800 whitespace-nowrap">{formatUsd(total)}</td>
          </tr>
        </tbody>
      </table>
    </div>
  );
}

/**
 * On-demand Gemini-estimated ROI + counterfactual explanation, appended to
 * the end of a patient's risk verdict (prediction result, recalculation
 * result, or the patient detail page).
 */
export default function AiInsightsPanel({ patientId, riskScore, riskBand, drivers,
                                          trendStatus = null, compact = false }) {
  const [insights, setInsights] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  // Roles that do not see costs get the counterfactual only; the server sends
  // nothing else (api/main.py get_ai_insights), this just labels the panel.
  const seesCosts = can(COST_ROLES);

  const handleGenerate = async () => {
    setLoading(true);
    setError(null);
    try {
      const result = await getAiInsights({
        patient_id: patientId,
        risk_score: riskScore,
        risk_band: riskBand,
        // Trajectory, not just level: 55% and falling calls for a different
        // action from 55% and climbing, and the verdict is computed server-side
        // from both.
        trend_status: trendStatus,
        drivers: (drivers || []).map((d) => ({
          label: d.label,
          value: d.value,
          explanation: d.explanation,
          source: d.source,
        })),
      });
      setInsights(result);
    } catch (err) {
      setError(err.message || 'Could not generate AI insights.');
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className={`bg-white rounded-xl border border-gray-200 shadow-sm ${compact ? 'p-4' : 'p-6'}`}>
      <div className={`flex items-center justify-between ${compact ? 'mb-3' : 'mb-4 border-b pb-4'}`}>
        <h3 className={`font-semibold text-ns-navy flex items-center space-x-2 ${compact ? 'text-sm' : 'text-base'}`}>
          <Sparkles className="text-gray-400" size={compact ? 16 : 20} />
          <span>{seesCosts ? <>AI Insights — ROI &amp; Counterfactual</> : 'AI Insights — What would lower the risk'}</span>
        </h3>
        {!insights && (
          <button
            type="button"
            onClick={handleGenerate}
            disabled={loading}
            className="flex items-center space-x-1.5 px-3 py-1.5 text-xs font-semibold bg-ns-navy text-white rounded-lg hover:bg-blue-900 transition-colors disabled:opacity-60 disabled:cursor-not-allowed"
          >
            {loading ? <Loader2 size={14} className="animate-spin" /> : <Sparkles size={14} />}
            <span>{loading ? 'Estimating…' : 'Generate insights'}</span>
          </button>
        )}
      </div>

      {!insights && !loading && !error && (
        <p className="text-sm text-gray-500">
          {seesCosts
            ? 'Estimate the return on investment of a care-coordination intervention for this patient, and get a counterfactual explaining what would most lower their risk.'
            : 'Get a counterfactual explaining which of this patient’s risk factors, if addressed, would most lower their risk.'}
        </p>
      )}

      {error && (
        <div className="bg-red-50 border border-red-200 rounded-lg p-3 flex items-start space-x-2 text-red-800 text-sm mb-3">
          <AlertCircle size={16} className="mt-0.5 shrink-0" />
          <span>{error}</span>
        </div>
      )}

      {insights && (
        <div className="space-y-4 animate-in fade-in duration-300">
          {/* ROI stats. Every figure is computed server-side from the itemised
              model in api/roi_model.py, so the breakdown below is the actual
              arithmetic rather than a restatement of a lump sum. Absent for the
              roles that do not see costs - the server sends none. */}
          {!insights.costs_hidden && (
          <div>
            <div className="flex items-center space-x-1.5 text-xs font-semibold text-gray-500 uppercase tracking-wide mb-2">
              <DollarSign size={14} />
              <span>Estimated Return on Investment</span>
            </div>

            {/* The verdict first. Four dollar figures with no recommendation
                attached read as an argument for spending the money, which is
                wrong for any patient below the break-even risk. */}
            {insights.case && (
              <div
                className={`border rounded-lg p-3 mb-3 flex items-start space-x-2 ${
                  insights.case.show_roi
                    ? 'bg-blue-50/60 border-blue-100'
                    : insights.case.decision === 'watch'
                      ? 'bg-amber-50/60 border-amber-100'
                      : 'bg-green-50/60 border-green-100'
                }`}
              >
                {insights.case.show_roi
                  ? <TrendingUp size={16} className="text-ns-navy mt-0.5 shrink-0" />
                  : insights.case.decision === 'watch'
                    ? <AlertTriangle size={16} className="text-risk-medium mt-0.5 shrink-0" />
                    : <ShieldCheck size={16} className="text-risk-low mt-0.5 shrink-0" />}
                <div>
                  <div className="text-sm font-semibold text-ns-navy">{insights.case.label}</div>
                  <p className="text-xs text-gray-600 mt-1 leading-relaxed">{insights.case.reason}</p>
                </div>
              </div>
            )}
            <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
              <div className="bg-gray-50 border border-gray-100 rounded-lg p-3">
                <div className="text-xs text-gray-500">Readmission Cost</div>
                <div className="text-lg font-bold text-gray-800">{formatUsd(insights.estimated_readmission_cost_usd)}</div>
              </div>
              <div className="bg-gray-50 border border-gray-100 rounded-lg p-3">
                <div className="text-xs text-gray-500">Intervention Cost</div>
                <div className="text-lg font-bold text-gray-800">{formatUsd(insights.estimated_intervention_cost_usd)}</div>
              </div>
              <div className="bg-gray-50 border border-gray-100 rounded-lg p-3">
                <div className="text-xs text-gray-500">Expected Savings</div>
                <div className="text-lg font-bold text-risk-low">{formatUsd(insights.expected_cost_avoided_usd)}</div>
              </div>
              <div className="bg-gray-50 border border-gray-100 rounded-lg p-3">
                <div className="text-xs text-gray-500">Net ROI</div>
                {/* Negative is a loss, and must not be painted in the same
                    colour as a return. */}
                <div className={`text-lg font-bold ${
                  Number(insights.net_roi_usd) < 0 ? 'text-risk-high' : 'text-ns-navy'}`}>
                  {formatUsd(insights.net_roi_usd)}
                  {insights.roi_ratio != null && (
                    <span className="text-xs font-medium text-gray-400 ml-1">({insights.roi_ratio}x)</span>
                  )}
                </div>
              </div>
            </div>

            {insights.break_even_risk_pct != null && (
              <p className="text-xs text-gray-400 mt-2">
                Break-even for this programme is a{' '}
                <span className="font-semibold text-gray-500">{insights.break_even_risk_pct}%</span>{' '}
                readmission risk. Below that, delivering the intervention costs more than the
                readmission it is expected to avert.
              </p>
            )}

            {/* The audit trail. Without this the four totals above are just
                assertions; with it a finance reviewer can see every amount that
                was added and every one that was taken off. */}
            {(insights.readmission_cost_items || insights.calculation_steps) && (
              <details className="mt-3 group">
                <summary className="text-xs font-semibold text-ns-navy cursor-pointer hover:underline list-none flex items-center gap-1">
                  <ChevronRight size={13} className="transition-transform group-open:rotate-90" />
                  <span>Show how this was calculated</span>
                </summary>

                <div className="mt-3 space-y-4">
                  <CostTable
                    title="Cost of one readmission episode"
                    items={insights.readmission_cost_items}
                    total={insights.estimated_readmission_cost_usd}
                  />
                  <CostTable
                    title="Cost of delivering the intervention to one patient"
                    items={insights.intervention_cost_items}
                    total={insights.estimated_intervention_cost_usd}
                  />

                  {insights.calculation_steps?.length > 0 && (
                    <div>
                      <div className="text-xs font-semibold text-gray-700 mb-1.5">Calculation</div>
                      <table className="w-full text-xs">
                        <tbody>
                          {insights.calculation_steps.map((step) => (
                            <tr key={step.step} className="border-b border-gray-100 last:border-0">
                              <td className="py-1.5 pr-3 text-gray-700 font-medium whitespace-nowrap">{step.step}</td>
                              <td className="py-1.5 pr-3 text-gray-500">{step.formula}</td>
                              <td className="py-1.5 text-right font-semibold text-gray-800 whitespace-nowrap">
                                {formatUsd(step.amount)}
                              </td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  )}

                  {insights.assumptions?.length > 0 && (
                    <div>
                      <div className="text-xs font-semibold text-gray-700 mb-1.5">Assumptions</div>
                      <BulletList items={insights.assumptions} className="text-xs text-gray-500" />
                    </div>
                  )}
                </div>
              </details>
            )}

            <BulletList items={insights.roi_rationale} className="text-sm text-gray-600 mt-3" />
          </div>
          )}

          {/* Counterfactual — always the final block */}
          <div className={insights.costs_hidden ? '' : 'border-t border-gray-100 pt-4'}>
            <div className="flex items-center space-x-1.5 text-xs font-semibold text-gray-500 uppercase tracking-wide mb-2">
              <Lightbulb size={14} />
              <span>Counterfactual Explanation</span>
            </div>
            <div className="bg-blue-50/60 border border-blue-100 rounded-lg p-3 text-sm text-gray-700">
              <BulletList items={insights.counterfactual_explanation} />
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

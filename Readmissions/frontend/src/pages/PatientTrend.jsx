import React, { useCallback, useState } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import { ArrowLeft, User, ShieldCheck, TrendingUp, ChevronRight, AlertTriangle } from 'lucide-react';
import WeeklyTrendPanel from '../components/dashboard/WeeklyTrendPanel';
import AiInsightsPanel from '../components/shared/AiInsightsPanel';
import { can, COST_ROLES } from '../roles';

export default function PatientTrend() {
  const { id } = useParams();
  const navigate = useNavigate();

  // The trend panel already fetches the series; it hands it up here so the ROI
  // panel can run on the LATEST point rather than the discharge score.
  const [trend, setTrend] = useState(null);
  const handleTrendLoaded = useCallback((data) => setTrend(data), []);

  const weekly = trend?.series_kind === 'weekly';
  const latest = trend?.weeks?.length ? trend.weeks[trend.weeks.length - 1] : null;

  // Whether there is a case for intervening at all. Decided by the API from
  // the itemised cost model, not here - see api/roi_model.roi_case.
  // A role that does not see costs gets no verdict from the API (it is written
  // in dollars), so it goes straight to the counterfactual.
  const roiCase = trend?.roi_case;
  const showRoi = roiCase ? roiCase.show_roi : true;
  const seesCosts = can(COST_ROLES);

  const insights = (
    <AiInsightsPanel
      patientId={id}
      riskScore={latest?.risk_score}
      riskBand={latest?.risk_band}
      drivers={latest?.drivers}
      trendStatus={trend?.monitoring_status}
    />
  );

  return (
    <div className="p-4 sm:p-6 max-w-5xl mx-auto space-y-6 pb-16 animate-in fade-in duration-500">
      <div className="border-b border-gray-200 pb-4">
        {/* Returns to the worklist, which is where this page is actually opened
            from. It previously went to /patients/:id - a different screen the
            user never passed through to get here. */}
        <button
          onClick={() => navigate('/')}
          className="flex items-center space-x-2 text-gray-500 hover:text-ns-navy transition-colors font-medium mb-3 focus:outline-none"
        >
          <ArrowLeft size={18} />
          <span>Back to Worklist</span>
        </button>
        <div className="flex items-center space-x-3">
          <User className="text-ns-navy" size={28} />
          <div>
            <h1 className="text-2xl font-bold text-ns-navy">Post-Discharge Risk Trend — {id}</h1>
            <p className="text-gray-500 mt-0.5 text-sm">
              Readmission risk re-scored over time, with the clinical factors behind each score, the
              interval between them, and a trajectory verdict. The panel below states which cadence
              this patient's series actually has.
            </p>
          </div>
        </div>
      </div>

      <WeeklyTrendPanel patientId={id} compact={false} onLoaded={handleTrendLoaded} />

      {/* ROI on the CURRENT position, not the discharge one. On a weekly series
          the drivers are the monitored signals - adherence, weight, refills -
          so the counterfactual names things a care team can still act on.

          A patient who is low-risk or recovering has NO ROI case: below the
          break-even risk the coordinator hours cost more than the readmission
          they would be expected to avert. Leading with "expected savings"
          there would recommend spending money the model itself says is lost,
          so the verdict replaces the figures and the arithmetic moves behind a
          disclosure for anyone who wants to check it. */}
      {latest && !showRoi && (
        <div className="space-y-2">
          <div className="bg-white rounded-xl border border-gray-200 shadow-sm p-6">
            <div className="flex items-start space-x-3">
              {/* "No case yet" and "no case, and risk is rising" are different
                  messages, and a green shield on the second one is the wrong
                  signal entirely. */}
              {roiCase.decision === 'watch'
                ? <AlertTriangle className="text-risk-medium mt-0.5 shrink-0" size={22} />
                : <ShieldCheck className="text-risk-low mt-0.5 shrink-0" size={22} />}
              <div>
                <h3 className="font-semibold text-ns-navy">{roiCase.label}</h3>
                <p className="text-sm text-gray-600 mt-1.5 leading-relaxed">{roiCase.reason}</p>
              </div>
            </div>
            <details className="mt-4 group">
              <summary className="text-xs font-semibold text-ns-navy cursor-pointer hover:underline list-none flex items-center gap-1">
                <ChevronRight size={13} className="transition-transform group-open:rotate-90" />
                <span>Show the ROI arithmetic anyway</span>
              </summary>
              <div className="mt-3">{insights}</div>
            </details>
          </div>
        </div>
      )}

      {latest && showRoi && (
        <div className="space-y-2">
          {roiCase && (
            <div className="bg-white rounded-xl border border-gray-200 shadow-sm px-5 py-4 flex items-start space-x-3">
              <TrendingUp className="text-ns-navy mt-0.5 shrink-0" size={20} />
              <div>
                <div className="font-semibold text-ns-navy text-sm">{roiCase.label}</div>
                <p className="text-sm text-gray-600 mt-1 leading-relaxed">{roiCase.reason}</p>
              </div>
            </div>
          )}
          {seesCosts && (
          <p className="text-sm text-gray-500">
            The estimate below covers intervening{' '}
            <span className="font-semibold text-gray-700">now</span>, at this patient's latest score
            of {Number(latest.risk_score).toFixed(1)}%
            {trend?.first_score != null && (
              <> rather than the {Number(trend.first_score).toFixed(1)}% recorded at discharge</>
            )}
            .
          </p>
          )}
          {insights}
          {seesCosts && weekly && (
            <p className="text-xs text-gray-400 italic">
              Weekly scores are the calibrated discharge probability adjusted by the monitoring
              rules, so read this figure as directional rather than as a calibrated expected value.
            </p>
          )}
        </div>
      )}
    </div>
  );
}

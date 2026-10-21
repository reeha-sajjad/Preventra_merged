import React, { useEffect, useRef, useState } from 'react';
import { Link } from 'react-router-dom';
import { getPatientTrend, getWeekNarrative } from '../../api';
import { aiOn, useAiStatus } from '../../hooks/useAiStatus';
import TrendStatusBadge, { trendStatusConfig } from '../shared/TrendStatusBadge';
import RiskBadge from '../shared/RiskBadge';
import BulletList from '../shared/BulletList';
import {
  LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer, Dot,
} from 'recharts';
import {
  AlertCircle, ArrowUpRight, ArrowDownRight, Minus, ChevronRight, CalendarClock, Sparkles, Loader2,
  HeartPulse, Smartphone, Pill, Stethoscope, Cpu, User, Radio, RadioTower, History,
  Target, Info, ClipboardList,
} from 'lucide-react';

const BAND_DOT_COLOR = { High: '#C0392B', Medium: '#F0A500', Low: '#1E7D44' };

/**
 * Which system a number arrived from.
 *
 * A monitoring week is not one feed, it is several, and they do not carry the
 * same weight: a pharmacy dispensing record is an external fact, an adherence
 * percentage typed into an app is the patient's own account of one. Showing the
 * origin next to the value is what lets a coordinator weigh them differently.
 *
 * The feed names are placeholders - no integration exists yet - and are
 * assigned per patient by the API so one patient keeps the same pharmacy and
 * the same device hub across every week.
 */
const FEED_ICON = {
  device: HeartPulse,
  app: Smartphone,
  symptom: ClipboardList,
  pharmacy: Pill,
  clinic: Stethoscope,
  model: Cpu,
  self: User,
  carried: History,
};

function SourceTag({ source, className = '' }) {
  if (!source?.feed) return null;
  const Icon = FEED_ICON[source.kind] || Radio;
  return (
    <span
      title={source.channel || ''}
      className={`inline-flex items-center gap-1 text-[11px] text-gray-500 ${className}`}
    >
      <Icon size={11} className="shrink-0" />
      <span>{source.feed}</span>
    </span>
  );
}

function TrendDot(props) {
  const { cx, cy, payload } = props;
  const color = BAND_DOT_COLOR[payload.risk_band] || '#0F2A4A';
  return <Dot cx={cx} cy={cy} r={5} fill={color} stroke="#fff" strokeWidth={1.5} />;
}

// A week with no readings shows no change of its own: the score is only held.
// The first week after it is measured from the last readings, and says which.
function DeltaIndicator({ delta, held = false, sinceWeek = null }) {
  if (held) {
    return <span className="text-xs text-gray-400">held</span>;
  }
  if (delta === null || delta === undefined) {
    return <span className="text-xs text-gray-400">baseline</span>;
  }
  const since = sinceWeek != null && (
    <span className="block text-[10px] font-normal text-gray-500 text-right">
      since {sinceWeek === 0 ? 'discharge' : `wk ${sinceWeek}`}
    </span>
  );
  if (delta > 0.4) {
    return (
      <span className="text-xs font-semibold text-risk-high">
        <span className="inline-flex items-center"><ArrowUpRight size={13} className="mr-0.5" /> +{delta.toFixed(1)}</span>
        {since}
      </span>
    );
  }
  if (delta < -0.4) {
    return (
      <span className="text-xs font-semibold text-risk-low">
        <span className="inline-flex items-center"><ArrowDownRight size={13} className="mr-0.5" /> {delta.toFixed(1)}</span>
        {since}
      </span>
    );
  }
  return (
    <span className="text-xs font-semibold text-gray-400">
      <span className="inline-flex items-center"><Minus size={13} className="mr-0.5" /> {delta.toFixed(1)}</span>
      {since}
    </span>
  );
}

function WeekCard({ week, patientId, weekly, seriesDiagnosis, group, groupFocus }) {
  const explains = aiOn(useAiStatus(), 'insights');
  const [narrative, setNarrative] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);

  const handleExplain = async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await getWeekNarrative({
        patient_id: patientId,
        week_number: week.week_number,
        risk_score: week.risk_score,
        risk_band: week.risk_band,
        delta: week.delta,
        week_trend: week.week_trend,
        days_since_prev: week.days_since_prev,
        interval_label: week.interval_label,
        admit_date: week.admit_date,
        discharge_date: week.discharge_date,
        los_days: week.los_days,
        series_kind: weekly ? 'weekly' : 'admissions',
        observed: week.observed !== false,
        primary_diagnosis: week.primary_diagnosis || seriesDiagnosis || '',
        clinical_group: group || '',
        group_focus: groupFocus || '',
        drivers: (week.drivers || []).map((d) => ({
          label: d.label,
          value: d.value,
          explanation: d.explanation,
          source: d.source,
        })),
      });
      setNarrative(res.narrative);
    } catch (err) {
      setError(err.message || 'Could not generate an explanation.');
    } finally {
      setLoading(false);
    }
  };

  // A monitoring week nobody reported: nothing new was measured.
  const silent = weekly && week.observed === false;

  return (
    <div className="bg-white border border-gray-200 rounded-lg p-4">
      <div className="flex items-start justify-between mb-2">
        <div>
          <div className="flex items-center space-x-2">
            <span className="text-xs font-semibold text-gray-500 uppercase tracking-wide">
              {weekly
                ? (week.week_number === 0 ? 'At Discharge' : `Week ${week.week_number}`)
                : `Admission ${week.week_number}`}
            </span>
            <span className="text-xs text-gray-500">
              · {weekly ? week.week_date : `discharged ${week.discharge_date || week.batch_date}`}
            </span>
          </div>
          {/* The elapsed time is the context that makes the delta readable: the
              same +16 points means something very different after 7 days than
              after 3 years. */}
          <div className="text-xs text-gray-500 mt-0.5">
            {week.rapid_return && (
              <span className="text-risk-high font-semibold mr-1">Rapid readmission · </span>
            )}
            {weekly && week.observed === false && (
              <span className="text-risk-medium font-semibold mr-1">No contact · </span>
            )}
            {week.interval_label}
            {!weekly && week.los_days != null && ` · ${week.los_days}d stay`}
          </div>
          {/* An admission's diagnosis is what makes its score readable: the
              same rise means something different after a GI bleed than after
              elective chemotherapy. */}
          {!weekly && week.primary_diagnosis && (
            <div className="text-xs text-gray-600 mt-1 font-medium">
              {week.primary_diagnosis}
              {week.primary_icd_code && (
                <span className="text-gray-400 font-normal"> · ICD {week.primary_icd_code}</span>
              )}
            </div>
          )}
        </div>
        <DeltaIndicator delta={week.delta} held={silent} sinceWeek={week.delta_since_week} />
      </div>
      <div className="flex items-center space-x-3 mb-3">
        <span className="text-2xl font-black text-gray-800">{week.risk_score.toFixed(1)}%</span>
        <RiskBadge riskBand={week.risk_band} size="sm" />
      </div>
      {/* A red flag needs assessing today whatever else improved this week, so
          it sits above the drivers rather than among them. */}
      {week.red_flags?.length > 0 && (
        <div className="mb-3 rounded-md bg-red-50 border border-red-200 px-2.5 py-1.5 text-xs text-red-800">
          <span className="font-semibold">Red flag - assess today:</span> {week.red_flags.join(', ')}
        </div>
      )}
      {/* Silence after warning signs is a reason to call, not to wait: the
          score is held, not improved. */}
      {silent && week.contact_overdue && (
        <div className="mb-3 rounded-md bg-yellow-50 border border-yellow-200 px-2.5 py-1.5 text-xs text-yellow-900">
          <span className="font-semibold">Contact the patient today:</span> no readings while the last
          ones put them at {week.risk_band}. The score is held from{' '}
          {week.held_from_week ? `week ${week.held_from_week}` : 'discharge'}, not improved.
        </div>
      )}
      {/* A silent week has no readings of its own. The last ones taken are
          shown as what they are - older readings - without repeating what they
          meant, which the week they came from already says. */}
      {silent && week.drivers?.length > 0 && (
        <div className="text-xs text-gray-500">
          <div className="mb-1 font-medium text-gray-600">
            Last readings ({week.held_from_week ? `week ${week.held_from_week}` : 'discharge'})
          </div>
          <ul className="space-y-0.5">
            {week.drivers.map((d, idx) => (
              <li key={idx}><span className="text-gray-600">{d.label}:</span> {d.value}</li>
            ))}
          </ul>
        </div>
      )}
      {!silent && week.drivers?.length > 0 && (
        <div className="space-y-2">
          {week.drivers.map((d, idx) => (
            <div key={idx} className="text-xs">
              <div className="text-gray-700">
                <span className="font-medium">{d.label}:</span> {d.value}
              </div>
              {/* What the reading means for this patient - the part a reader
                  needs most, so it is not the faintest text on the card. */}
              {d.explanation && (
                <div className="mt-1 border-l-2 border-ns-navy/25 pl-2 leading-snug text-gray-600">
                  {d.explanation}
                </div>
              )}
              <SourceTag source={d.source} className="mt-0.5" />
            </div>
          ))}
        </div>
      )}

      {/* Which systems were heard from this week - not just the ones that
          happened to make the top three drivers. */}
      {week.sources?.length > 0 && (
        <div className="mt-3 pt-2 border-t border-gray-100">
          <div className="text-[11px] uppercase tracking-wide text-gray-400 font-semibold mb-1">
            Data received from
          </div>
          <div className="flex flex-wrap gap-x-3 gap-y-1">
            {week.sources.map((s) => (
              <SourceTag key={s.feed} source={s} />
            ))}
          </div>
        </div>
      )}
      {week.sources?.length === 0 && (
        <div className="mt-3 pt-2 border-t border-gray-100 flex items-center gap-1 text-[11px] text-risk-medium">
          <RadioTower size={11} />
          <span>No feed reported this week</span>
        </div>
      )}

      <div className="mt-3 pt-3 border-t border-gray-100">
        {explains && !silent && !narrative && !loading && (
          <button
            type="button"
            onClick={handleExplain}
            className="flex items-center space-x-1.5 text-xs font-semibold text-ns-navy hover:underline"
          >
            <Sparkles size={13} />
            <span>Explain this {weekly ? 'week' : 'admission'}</span>
          </button>
        )}
        {loading && (
          <span className="flex items-center space-x-1.5 text-xs text-gray-400">
            <Loader2 size={13} className="animate-spin" />
            <span>Generating…</span>
          </span>
        )}
        {error && <p className="text-xs text-red-600">{error}</p>}
        {narrative && (
          <div className="bg-blue-50/60 border border-blue-100 rounded-lg p-2.5 text-xs text-gray-700">
            <div className="flex items-center space-x-1.5 font-semibold text-ns-navy mb-1.5">
              <Sparkles size={13} />
              <span>Explanation</span>
            </div>
            <BulletList items={narrative} />
          </div>
        )}
      </div>
    </div>
  );
}

export default function WeeklyTrendPanel({ patientId, compact = false, onLoaded }) {
  const [trend, setTrend] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  // Held in a ref so the fetch effect never depends on the callback's identity:
  // a parent that re-renders with a new function must not retrigger the request.
  const onLoadedRef = useRef(onLoaded);
  useEffect(() => { onLoadedRef.current = onLoaded; }, [onLoaded]);

  useEffect(() => {
    setLoading(true);
    setError(null);
    getPatientTrend(patientId)
      .then((data) => {
        setTrend(data);
        setLoading(false);
        // Hand the series up so the page can hang an ROI panel off the latest
        // point without issuing a duplicate request for the same trend.
        onLoadedRef.current?.(data);
      })
      .catch((err) => { setError(err.message); setLoading(false); });
  }, [patientId]);

  if (loading) {
    return (
      <div className={`bg-white rounded-xl border border-gray-200 shadow-sm animate-pulse ${compact ? 'p-4 h-48' : 'p-6 h-80'}`}>
        <div className="h-4 bg-gray-200 rounded w-40 mb-4" />
        <div className="h-32 bg-gray-100 rounded" />
      </div>
    );
  }

  if (error) {
    return (
      <div className="bg-red-50 border border-red-200 rounded-xl p-4 flex items-start space-x-3 text-red-800">
        <AlertCircle size={20} className="mt-0.5 shrink-0" />
        <div>
          <div className="font-semibold">Failed to load risk trend</div>
          <div className="text-sm mt-0.5 text-red-600">{error}</div>
        </div>
      </div>
    );
  }

  if (!trend) return null;

  const cfg = trendStatusConfig(trend.monitoring_status);
  const weekly = trend.series_kind === 'weekly';
  const weeksToShow = compact ? trend.weeks.slice(-3) : trend.weeks;

  return (
    <div className={`bg-white rounded-xl border border-gray-200 shadow-sm ${compact ? 'p-4' : 'p-6'}`}>
      <div className={`flex items-center justify-between ${compact ? 'mb-3' : 'mb-4 border-b pb-4'}`}>
        <h3 className={`font-semibold text-ns-navy flex items-center space-x-2 ${compact ? 'text-sm' : 'text-lg'}`}>
          <CalendarClock className="text-gray-400" size={compact ? 16 : 22} />
          <span>{weekly ? 'Weekly Post-Discharge Monitoring' : 'Readmission Risk Trend'}</span>
        </h3>
        <TrendStatusBadge status={trend.monitoring_status} size={compact ? 'sm' : 'md'} />
      </div>

      {/* The admission this monitoring window follows. Every week below is a
          re-score of the SAME index stay, so its diagnosis belongs here rather
          than repeated on each card. */}
      {weekly && trend.primary_diagnosis && (
        <div className="flex items-baseline flex-wrap gap-x-2 mb-3">
          <span className="text-xs uppercase tracking-wide text-gray-400 font-semibold">
            Admitted for
          </span>
          <span className="text-sm font-semibold text-ns-navy">{trend.primary_diagnosis}</span>
          {trend.primary_icd_code && (
            <span className="text-xs text-gray-400">ICD {trend.primary_icd_code}</span>
          )}
          {trend.index_discharge_date && (
            <span className="text-xs text-gray-400">· discharged {trend.index_discharge_date}</span>
          )}
        </div>
      )}

      {/* Which condition this patient is monitored as.

          Shown, never applied silently: the group changes how every reading
          below was weighted and worded, so the card states what it was decided
          on and how confident that decision is. A grouping matched only on a
          secondary diagnosis TITLE is approximate, because the loader keeps no
          codes for secondaries - and the card says so rather than implying the
          same certainty as a coded match. */}
      {!compact && weekly && trend.clinical_group && (
        <div className="border border-gray-200 bg-gray-50 rounded-lg px-3 py-2.5 mb-4">
          <div className="flex items-start gap-2">
            <Target size={15} className="text-ns-navy mt-0.5 shrink-0" />
            <div className="min-w-0">
              <div className="text-sm">
                <span className="text-gray-500">Monitored as</span>{' '}
                <span className="font-semibold text-ns-navy">{trend.clinical_group.label}</span>
                {trend.clinical_group.confidence === 'moderate' && (
                  <span className="ml-2 text-xs text-risk-medium">approximate match</span>
                )}
              </div>
              {trend.signal_plan?.focus && (
                <div className="text-xs text-gray-600 mt-0.5">{trend.signal_plan.focus}</div>
              )}
              <div className="text-xs text-gray-400 mt-1">
                Decided on: {trend.clinical_group.evidence}
              </div>
              {/* What this group is asked for beyond the shared seven. These are
                  the readings a patient would not think to report and a generic
                  form would never request. */}
              {trend.signal_plan?.condition_specific?.length > 0 && (
                <div className="text-xs text-gray-600 mt-1.5">
                  <span className="text-gray-400">Also monitored for this condition: </span>
                  {trend.signal_plan.condition_specific.map((s) => s.label).join(', ')}
                </div>
              )}
              {trend.signal_plan?.low_value?.length > 0 && (
                <div className="text-xs text-gray-400 mt-1 flex items-start gap-1">
                  <Info size={11} className="mt-0.5 shrink-0" />
                  <span>
                    Carries little weight for this condition:{' '}
                    {trend.signal_plan.low_value.join(', ').toLowerCase()}
                  </span>
                </div>
              )}
              {/* The patient's own reference point. "+2.4 kg" only means
                  something next to the weight it is 2.4 kg above. */}
              {trend.baseline_rows?.length > 0 && (
                <details className="group/base mt-2">
                  <summary className="text-xs font-semibold text-ns-navy cursor-pointer hover:underline list-none flex items-center gap-1">
                    <ChevronRight size={11} className="transition-transform group-open/base:rotate-90" />
                    <span>Baseline at discharge</span>
                  </summary>
                  <div className="mt-2 grid grid-cols-2 sm:grid-cols-3 gap-x-4 gap-y-1">
                    {trend.baseline_rows.map((r) => (
                      <div key={r.key} className="text-xs" title={r.why}>
                        <span className="text-gray-400">{r.label}: </span>
                        <span className="font-semibold text-gray-700">{r.value} {r.unit}</span>
                      </div>
                    ))}
                  </div>
                  {trend.discharge_baseline?.source === 'simulated' && (
                    <div className="text-xs text-gray-400 mt-1.5 italic">
                      Simulated. In a live programme this is recorded by whoever discharges
                      the patient.
                    </div>
                  )}
                </details>
              )}
            </div>
          </div>
        </div>
      )}

      {/* Cadence disclosure: what one point actually represents. */}
      {!compact && weekly && (
        <p className="text-xs text-gray-500 mb-4">
          Re-scored every 7 days across the 30-day post-discharge window ·{' '}
          <span className="font-semibold">
            {trend.weeks.filter((w) => w.week_number > 0 && w.observed !== false).length} of{' '}
            {trend.weeks.filter((w) => w.week_number > 0).length}
          </span>{' '}
          monitoring weeks had readings; in the others the score is held from the last readings, not re-scored.
        </p>
      )}
      {!compact && !weekly && trend.median_gap_days != null && (
        <p className="text-xs text-gray-500 -mt-2 mb-4">
          One point per scored admission, not a fixed schedule. Median gap between this patient's
          admissions: <span className="font-semibold">{trend.median_gap_days} days</span>
          {trend.span_days != null && ` · ${trend.weeks_tracked} admissions across ${trend.span_days} days`}.
        </p>
      )}

      {/* Recommended action banner */}
      <div className={`border rounded-lg px-3 py-2.5 mb-4 text-sm ${cfg.soft} ${cfg.text}`}>
        {trend.recommended_action}
      </div>

      {/* Summary stats */}
      {!compact && (
        <div className="grid grid-cols-3 sm:grid-cols-4 gap-3 mb-5">
          <div className="bg-gray-50 border border-gray-100 rounded-lg p-3">
            <div className="text-xs text-gray-500">{weekly ? 'Weeks Monitored' : 'Admissions Scored'}</div>
            <div className="text-lg font-bold text-gray-800">{trend.weeks_tracked}</div>
          </div>
          <div className="bg-gray-50 border border-gray-100 rounded-lg p-3">
            <div className="text-xs text-gray-500">{weekly ? 'At Discharge' : 'First Admission'}</div>
            <div className="text-lg font-bold text-gray-800">{trend.first_score.toFixed(1)}%</div>
          </div>
          <div className="bg-gray-50 border border-gray-100 rounded-lg p-3">
            <div className="text-xs text-gray-500">{weekly ? 'Latest Week' : 'Latest Admission'}</div>
            <div className="text-lg font-bold text-gray-800">{trend.latest_score.toFixed(1)}%</div>
          </div>
          <div className="bg-gray-50 border border-gray-100 rounded-lg p-3">
            <div className="text-xs text-gray-500">Net Change</div>
            <div className={`text-lg font-bold ${trend.net_change > 0 ? 'text-risk-high' : trend.net_change < 0 ? 'text-risk-low' : 'text-gray-800'}`}>
              {trend.net_change > 0 ? '+' : ''}{trend.net_change.toFixed(1)}
            </div>
          </div>
        </div>
      )}

      {/* Chart */}
      <div className={compact ? 'h-32 w-full mb-4' : 'h-64 w-full mb-6'}>
        <ResponsiveContainer width="100%" height="100%">
          <LineChart data={trend.weeks} margin={{ top: 5, right: 15, left: -20, bottom: 5 }}>
            <CartesianGrid strokeDasharray="3 3" vertical={false} stroke="#f0f0f0" />
            <XAxis dataKey="week_label" tick={{ fill: '#9ca3af', fontSize: 12 }} axisLine={false} tickLine={false} />
            <YAxis domain={[0, 100]} tick={{ fill: '#9ca3af', fontSize: 12 }} axisLine={false} tickLine={false} />
            <Tooltip contentStyle={{ borderRadius: '8px', border: '1px solid #e5e7eb', boxShadow: '0 4px 6px -1px rgb(0 0 0 / 0.1)' }} />
            <Line type="monotone" dataKey="risk_score" stroke="#0F2A4A" strokeWidth={2.5} dot={<TrendDot />} activeDot={{ r: 7 }} />
          </LineChart>
        </ResponsiveContainer>
      </div>

      {/* Per-week breakdown */}
      <div className={`grid gap-3 ${compact ? 'grid-cols-1' : 'grid-cols-1 sm:grid-cols-2 lg:grid-cols-3'}`}>
        {weeksToShow.slice().reverse().map((week) => (
          <WeekCard key={week.week_number} week={week} patientId={patientId} weekly={weekly}
                    seriesDiagnosis={trend.primary_diagnosis}
                    group={trend.clinical_group?.label}
                    groupFocus={trend.signal_plan?.focus} />
        ))}
      </div>

      {compact && (
        <Link
          to={`/patients/${patientId}/trend`}
          className="mt-4 flex items-center justify-center space-x-1.5 text-sm font-semibold text-ns-navy hover:underline"
        >
          <span>{weekly ? 'View Full Weekly Trend' : 'View Full Risk Trend'}</span>
          <ChevronRight size={16} />
        </Link>
      )}
    </div>
  );
}

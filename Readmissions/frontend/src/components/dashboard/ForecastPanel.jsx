import React, { useEffect, useState } from 'react';
import {
  AlertTriangle, TrendingUp, ShieldCheck, Clock, Loader2, Stethoscope, Info,
} from 'lucide-react';
import { getPatientForecast, getPatientClinicalAlerts } from '../../api';

// Severity drives the whole panel's colour. Kept in one place so the header,
// the border and each trigger row can never disagree about how bad this is.
const SEVERITY = {
  critical: {
    label: 'Critical', ring: 'border-risk-high', chip: 'bg-risk-high text-white',
    tint: 'bg-red-50', text: 'text-risk-high',
  },
  high: {
    label: 'High', ring: 'border-orange-400', chip: 'bg-orange-500 text-white',
    tint: 'bg-orange-50', text: 'text-orange-700',
  },
  moderate: {
    label: 'Watch', ring: 'border-risk-medium', chip: 'bg-risk-medium text-white',
    tint: 'bg-amber-50', text: 'text-amber-700',
  },
  none: {
    label: 'No warning', ring: 'border-gray-200', chip: 'bg-gray-200 text-gray-700',
    tint: 'bg-gray-50', text: 'text-gray-600',
  },
};

const STREAM_LABEL = {
  trajectory: 'Trend',
  vitals: 'Vitals',
  symptoms: 'Symptoms',
  engagement: 'Engagement',
  mood: 'Mood',
};

const CONFIDENCE_TEXT = {
  high: 'text-gray-600',
  moderate: 'text-amber-700',
  low: 'text-gray-500',
};

export default function ForecastPanel({ patientId }) {
  const [data, setData] = useState(null);
  const [alerts, setAlerts] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');

  useEffect(() => {
    if (!patientId) return;
    let cancelled = false;
    setLoading(true);
    setError('');

    Promise.all([
      getPatientForecast(patientId),
      getPatientClinicalAlerts(patientId).catch(() => ({ alerts: [] })),
    ])
      .then(([forecastResponse, alertResponse]) => {
        if (cancelled) return;
        setData(forecastResponse);
        setAlerts(alertResponse?.alerts || []);
      })
      .catch((e) => !cancelled && setError(e.message || 'Could not load the forecast.'))
      .finally(() => !cancelled && setLoading(false));

    return () => { cancelled = true; };
  }, [patientId]);

  if (loading) {
    return (
      <div className="bg-white rounded-lg border border-gray-200 p-6 flex items-center gap-2 text-gray-500">
        <Loader2 size={16} className="animate-spin" />
        <span className="text-sm">Building forecast…</span>
      </div>
    );
  }

  if (error) {
    return (
      <div className="bg-white rounded-lg border border-gray-200 p-6 text-sm text-gray-600">
        {error}
      </div>
    );
  }

  const forecast = data?.forecast;
  if (!forecast || forecast.status === 'no_data') {
    return (
      <div className="bg-white rounded-lg border border-gray-200 p-6">
        <h3 className="font-semibold text-ns-navy flex items-center gap-2">
          <TrendingUp size={18} /> Early warning
        </h3>
        <p className="text-sm text-gray-500 mt-2">
          No weekly monitoring has been recorded for this patient yet, so there is
          nothing to project from.
        </p>
      </div>
    );
  }

  const tone = SEVERITY[forecast.severity] || SEVERITY.none;
  const crossing = forecast.band_crossing;
  const responded = alerts.filter((a) => a.response);

  return (
    <div className={`bg-white rounded-lg border-2 ${tone.ring} overflow-hidden`}>
      {/* Header: the verdict and the deadline that follows from it */}
      <div className={`${tone.tint} px-6 py-4 flex items-start justify-between gap-4`}>
        <div>
          <h3 className="font-semibold text-ns-navy flex items-center gap-2">
            {forecast.severity === 'none'
              ? <ShieldCheck size={18} className="text-risk-low" />
              : <AlertTriangle size={18} className={tone.text} />}
            Early warning
          </h3>
          <p className={`text-sm mt-1 ${tone.text} font-medium`}>
            {crossing
              ? `On track to reach ${crossing.to_band} risk in about ${crossing.lead_time_days} days`
              : forecast.severity === 'none'
                ? 'No warning signs this week'
                : `${forecast.triggers.length} warning sign${forecast.triggers.length === 1 ? '' : 's'} this week`}
          </p>
        </div>
        <span className={`shrink-0 px-3 py-1 rounded-full text-xs font-semibold ${tone.chip}`}>
          {tone.label}
        </span>
      </div>

      <div className="px-6 py-4 space-y-4">
        {/* The projection, stated as numbers so it can be argued with */}
        <div className="grid grid-cols-3 gap-3 text-center">
          <Figure label="Now" value={`${forecast.current_score}%`} sub={forecast.current_band} />
          <Figure
            label="Per week"
            value={`${forecast.velocity_per_week > 0 ? '+' : ''}${forecast.velocity_per_week}`}
            sub={forecast.acceleration > 0 ? 'accelerating' : 'steady'}
          />
          <Figure
            label={`In ${forecast.horizon_weeks} wks`}
            value={`${forecast.projected_score}%`}
            sub={forecast.projected_band}
            highlight={forecast.projected_band !== forecast.current_band}
          />
        </div>

        {forecast.severity !== 'none' && (
          <div className="flex items-center gap-2 text-sm text-gray-700">
            <Clock size={14} className={tone.text} />
            <span>Clinical review <strong>{forecast.recommended_review_by}</strong></span>
          </div>
        )}

        {/* Why. Each row is one rule, with the reasoning it fired on. */}
        {forecast.triggers.length > 0 && (
          <ul className="space-y-2">
            {forecast.triggers.map((t) => {
              const rowTone = SEVERITY[t.severity] || SEVERITY.none;
              return (
                <li key={t.code} className="border border-gray-200 rounded-md p-3">
                  <div className="flex items-start justify-between gap-3">
                    <span className="font-medium text-sm text-gray-900">{t.title}</span>
                    <span className={`shrink-0 text-[10px] uppercase tracking-wide px-2 py-0.5 rounded ${rowTone.chip}`}>
                      {STREAM_LABEL[t.stream] || t.stream}
                    </span>
                  </div>
                  <p className="text-sm text-gray-700 mt-1">{t.detail}</p>
                  <p className="text-xs text-gray-500 mt-1.5 leading-relaxed">{t.rationale}</p>
                </li>
              );
            })}
          </ul>
        )}

        {/* What the clinician said back, if anything */}
        {responded.length > 0 && (
          <div className="border-t border-gray-200 pt-3 space-y-3">
            <h4 className="text-xs font-semibold uppercase tracking-wide text-gray-500 flex items-center gap-1.5">
              <Stethoscope size={13} /> Clinician response
            </h4>
            {responded.map((a) => (
              <div key={a.alert_id} className="bg-blue-50 border border-blue-100 rounded-md p-3">
                <div className="flex items-baseline justify-between gap-2">
                  <span className="text-sm font-medium text-ns-navy">
                    {a.response.doctor_name}
                    {a.response.specialty ? ` · ${a.response.specialty}` : ''}
                  </span>
                  <span className="text-xs text-gray-500">{a.response.responded_at}</span>
                </div>
                <p className="text-sm text-gray-800 mt-1 whitespace-pre-wrap">
                  {a.response.recommendation}
                </p>
                {a.response.action_labels?.length > 0 && (
                  <ul className="mt-2 flex flex-wrap gap-1.5">
                    {a.response.action_labels.map((label) => (
                      <li key={label} className="text-xs bg-white border border-blue-200 text-ns-navy rounded-full px-2 py-0.5">
                        {label}
                      </li>
                    ))}
                  </ul>
                )}
              </div>
            ))}
          </div>
        )}

        {/* What this is, stated on the panel rather than in a document nobody
            opens. The confidence line and the basis line say different things:
            one is about this patient's data, the other about the method. */}
        <div className="border-t border-gray-200 pt-3 space-y-1.5">
          <p className={`text-xs ${CONFIDENCE_TEXT[forecast.confidence]}`}>
            <strong className="capitalize">{forecast.confidence} confidence.</strong>{' '}
            {forecast.confidence_reason}
          </p>
          <p className="text-xs text-gray-400 flex items-start gap-1.5 leading-relaxed">
            <Info size={12} className="shrink-0 mt-0.5" />
            {forecast.basis}
          </p>
        </div>
      </div>
    </div>
  );
}

function Figure({ label, value, sub, highlight }) {
  return (
    <div className={`rounded-md py-2 ${highlight ? 'bg-amber-50 border border-amber-200' : 'bg-gray-50'}`}>
      <div className="text-[10px] uppercase tracking-wide text-gray-500">{label}</div>
      <div className="text-lg font-semibold text-ns-navy tabular-nums">{value}</div>
      <div className="text-xs text-gray-500">{sub}</div>
    </div>
  );
}

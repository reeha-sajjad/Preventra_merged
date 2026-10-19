import React, { useCallback, useEffect, useState } from 'react';
import { getPatientById, getPatientSummary } from '../../api';
import RiskBadge from '../shared/RiskBadge';
import CareTeamCard from '../shared/CareTeamCard';
import ClinicalGate from '../shared/ClinicalGate';
import DriverCard from '../shared/DriverCard';
import DiagnosisList from '../shared/DiagnosisList';
import AiInsightsPanel from '../shared/AiInsightsPanel';
import WeeklyTrendPanel from './WeeklyTrendPanel';
import CareCoordination from './CareCoordination';
import { X, Calendar, AlertCircle } from 'lucide-react';

// ---------------------------------------------------------------------------
// Skeleton
// ---------------------------------------------------------------------------
function DetailSkeleton() {
  return (
    <div className="space-y-5 animate-pulse p-6">
      <div className="bg-white p-5 rounded-xl border border-gray-200 space-y-4">
        <div className="h-4 bg-gray-200 rounded w-24 mx-auto" />
        <div className="flex justify-center space-x-6">
          <div className="h-16 bg-gray-200 rounded w-20" />
          <div className="h-16 bg-gray-100 rounded w-20" />
        </div>
        <div className="h-4 bg-gray-100 rounded w-40 mx-auto" />
      </div>
      {[0,1,2].map((i) => (
        <div key={i} className="bg-white rounded-xl border border-gray-200 p-4 space-y-2">
          <div className="h-4 bg-gray-200 rounded w-1/2" />
          <div className="h-3 bg-gray-100 rounded w-full" />
          <div className="h-3 bg-gray-100 rounded w-3/4" />
        </div>
      ))}
    </div>
  );
}

// Must match the transition-duration classes below so the panel finishes
// sliding off-screen before the parent actually unmounts it.
const CLOSE_TRANSITION_MS = 300;

export default function PatientDetailPanel({ patientId, onClose, onChanged }) {
  // The overview layer first - risk, care team, insurer, and whether the
  // clinical details are open to this user yet. Hospital admins and insurers
  // give a reason before the rest loads (api/access.py).
  const [summary, setSummary] = useState(null);
  const [patient, setPatient] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError]     = useState(null);
  const [reloadKey, setReloadKey] = useState(0);

  // Drives the slide/fade transition. Starts false so the panel first paints
  // off-screen, then flips true a frame later so the transition actually runs.
  const [visible, setVisible] = useState(false);

  useEffect(() => {
    let live = true;
    setLoading(true);
    setError(null);
    setPatient(null);
    getPatientSummary(patientId)
      .then((sum) => {
        if (!live) return null;
        setSummary(sum);
        if (sum.detail_access === 'reason_required' || sum.detail_access === 'overview_only') return null;
        return getPatientById(patientId).then((data) => { if (live) setPatient(data); });
      })
      .then(() => { if (live) setLoading(false); })
      .catch((err) => { if (live) { setError(err.message); setLoading(false); } });
    return () => { live = false; };
  }, [patientId, reloadKey]);

  const reload = () => setReloadKey((k) => k + 1);

  // Runs once on mount only — switching to a different patient while the
  // panel is already open should not replay the slide-in. A rAF-only flip can
  // get coalesced with the initial paint (or, in a backgrounded/non-composited
  // tab, never fire at all), silently skipping the animation — a short timeout
  // reliably runs on the next macrotask regardless, so it's used instead.
  useEffect(() => {
    const timer = window.setTimeout(() => setVisible(true), 20);
    return () => window.clearTimeout(timer);
  }, []);

  const handleClose = useCallback(() => {
    setVisible(false);
    window.setTimeout(onClose, CLOSE_TRANSITION_MS);
  }, [onClose]);

  useEffect(() => {
    const handleEsc = (e) => { if (e.key === 'Escape') handleClose(); };
    window.addEventListener('keydown', handleEsc);
    return () => window.removeEventListener('keydown', handleEsc);
  }, [handleClose]);

  const getScoreColor = (band) => {
    const n = band?.toLowerCase();
    if (n === 'high')   return 'text-risk-high';
    if (n === 'medium') return 'text-risk-medium';
    return 'text-risk-low';
  };

  return (
    <>
      <div
        className={`fixed inset-0 bg-gray-900/40 z-40 backdrop-blur-sm transition-opacity duration-300 ease-out ${
          visible ? 'opacity-100' : 'opacity-0'
        }`}
        onClick={handleClose}
      />

      <div
        className={`fixed inset-y-0 right-0 z-50 w-full max-w-md bg-gray-50 shadow-2xl flex flex-col border-l border-gray-200 overflow-hidden transition-transform duration-300 ease-out will-change-transform ${
          visible ? 'translate-x-0' : 'translate-x-full'
        }`}
      >
        {/* Header */}
        <div className="bg-white border-b border-gray-200 px-6 py-4 flex items-center justify-between shrink-0">
          <h2 className="text-xl font-bold text-ns-navy">Patient Details</h2>
          <button onClick={handleClose} className="p-2 -mr-2 text-gray-400 hover:text-gray-600 hover:bg-gray-100 rounded-full transition-colors">
            <X size={20} />
          </button>
        </div>

        {/* Content */}
        <div className="flex-1 overflow-y-auto">
          {loading && <DetailSkeleton />}

          {!loading && error && (
            <div className="m-6 bg-red-50 border border-red-200 rounded-xl p-4 flex items-start space-x-3 text-red-800">
              <AlertCircle size={20} className="mt-0.5 shrink-0" />
              <div>
                <div className="font-semibold">Failed to load patient</div>
                <div className="text-sm mt-0.5 text-red-600">{error}</div>
              </div>
            </div>
          )}

          {!loading && !error && summary && !patient && (
            <div className="space-y-6 p-6 pb-24">
              <div className="bg-white p-5 rounded-xl border border-gray-200 shadow-sm flex flex-col items-center text-center space-y-3">
                <div className="text-sm font-semibold text-gray-500 uppercase tracking-widest">{summary.id}</div>
                <div className="flex items-center space-x-6">
                  <span className={`text-5xl font-black ${getScoreColor(summary.current_band)}`}>
                    {parseFloat(summary.current_score).toFixed(1)}%
                  </span>
                  <RiskBadge riskBand={summary.current_band} size="md" />
                </div>
                <div className="text-sm text-gray-600 flex items-center gap-2">
                  <Calendar size={16} className="text-gray-400" />
                  <span>Last Discharge: <strong>{summary.discharge_date}</strong></span>
                </div>
              </div>
              <CareTeamCard summary={summary} onChanged={() => { reload(); onChanged?.(); }} />
              <ClinicalGate patientId={summary.id} reasons={summary.reasons} onOpened={reload} compact
                overviewOnly={summary.detail_access === 'overview_only'} insurer={summary.viewer === 'insurer'} />
            </div>
          )}

          {!loading && patient && (
            <div className="space-y-6 p-6 pb-24">
              {/* Top Summary */}
              <div className="bg-white p-5 rounded-xl border border-gray-200 shadow-sm flex flex-col items-center justify-center text-center space-y-4">
                <div className="text-sm font-semibold text-gray-500 uppercase tracking-widest">{patient.id}</div>
                <div className="flex items-center space-x-6">
                  <div className="flex flex-col items-center">
                    <span className="text-sm text-gray-500 mb-1">Risk Score</span>
                    <span className={`text-5xl font-black ${getScoreColor(patient.risk_band)}`}>
                      {parseFloat(patient.risk_score).toFixed(1)}%
                    </span>
                  </div>
                  <div className="h-12 w-px bg-gray-200" />
                  <div className="flex flex-col items-center space-y-2 py-1">
                    <span className="text-sm text-gray-500">Risk Band</span>
                    <RiskBadge riskBand={patient.risk_band} size="md" />
                  </div>
                </div>
                <div className="w-full border-t border-gray-100 pt-3 mt-2 flex items-center justify-center space-x-2 text-sm text-gray-600">
                  <Calendar size={16} className="text-gray-400" />
                  <span>Last Discharge: <strong>{patient.discharge_date}</strong></span>
                </div>
              </div>

              <CareTeamCard summary={summary} onChanged={() => { reload(); onChanged?.(); }} />

              {/* What the patient was treated for. Above the drivers on
                  purpose: a risk score and its reasons only mean something
                  once you know which patient they belong to. */}
              <DiagnosisList patient={patient} />

              {/* Drivers */}
              <div>
                <h3 className="text-lg font-semibold text-ns-navy mb-4 border-b pb-2">Primary Drivers</h3>
                <div className="space-y-3">
                  {(patient.drivers || []).map((driver, idx) => (
                    <DriverCard
                      key={idx}
                      label={driver.label}
                      value={driver.value}
                      explanation={driver.explanation}
                      category={driver.category}
                      riskBand={patient.risk_band}
                    />
                  ))}
                </div>
                {(!patient.drivers || patient.drivers.length === 0) && (
                  <p className="text-gray-500 text-sm italic">No driving factors reported.</p>
                )}
              </div>

              {/* Readmission risk trend across this patient's admissions */}
              <WeeklyTrendPanel patientId={patient.id} compact />

              <AiInsightsPanel
                patientId={patient.id}
                riskScore={patient.risk_score}
                riskBand={patient.risk_band}
                drivers={patient.drivers}
                compact
              />

              <CareCoordination patientId={patient.id} />
            </div>
          )}
        </div>
      </div>
    </>
  );
}
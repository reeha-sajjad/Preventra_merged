import React, { useEffect, useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import {
  getPatientForEdit, predictPatientUpdate, commitPatientUpdate, getManualEntrySchema,
} from '../api';
import RiskBadge from '../components/shared/RiskBadge';
import DriverCard from '../components/shared/DriverCard';
import AiInsightsPanel from '../components/shared/AiInsightsPanel';
import { WeekTable } from '../components/patient/RescoreDialog';
import {
  MimicPatientForm, emptyForm, filledFieldCount, getScoreColor,
} from '../components/shared/PatientFormFields';
import {
  UserCog,
  Loader2,
  AlertCircle,
  CheckCircle,
  ArrowLeft,
  ArrowRight,
  TrendingUp,
  BellRing,
  Info,
} from 'lucide-react';

export default function UpdatePatient() {
  const { id } = useParams();
  const navigate = useNavigate();

  const [form, setForm]           = useState(null);
  const [categoricals, setCategoricals] = useState(null);
  const [originalScore, setOriginalScore] = useState(null);
  const [originalBand, setOriginalBand]   = useState(null);
  const [loadingPatient, setLoadingPatient] = useState(true);
  const [loadError, setLoadError] = useState(null);
  // False for a patient whose discharge comes from the hospital's feed.
  const [editable, setEditable] = useState(true);
  const [lockedReason, setLockedReason] = useState(null);

  const [loading, setLoading]     = useState(false);
  const [error, setError]         = useState(null);
  const [result, setResult]       = useState(null);
  const [saving, setSaving]       = useState(false);
  const [saved, setSaved]         = useState(false);
  const [alertCreated, setAlertCreated] = useState(false);
  const [saveError, setSaveError] = useState(null);

  useEffect(() => {
    let cancelled = false;
    setLoadingPatient(true);
    setLoadError(null);
    getPatientForEdit(id)
      .then((data) => {
        if (cancelled) return;
        // Overlay the stored inputs on a blank MIMIC form. Batch-loaded
        // patients carry raw_inputs = {} (scripts/load_mimic_to_mongo.py keeps
        // the 94-feature vector local), so without this the form would render
        // undefined values and React would flip the inputs to uncontrolled.
        const stored = data.raw_inputs || {};
        const merged = { ...emptyForm(), ...Object.fromEntries(
          Object.entries(stored).filter(([, v]) => v !== undefined)
        ) };
        merged.discharge_date = stored.discharge_date || data.discharge_date
          || new Date().toISOString().split('T')[0];
        setForm(merged);
        setOriginalScore(data.risk_score);
        setOriginalBand(data.risk_band);
        setEditable(data.editable !== false);
        setLockedReason(data.reason || null);
        setLoadingPatient(false);
      })
      .catch((err) => {
        if (cancelled) return;
        setLoadError(err.message);
        setLoadingPatient(false);
      });
    return () => { cancelled = true; };
  }, [id]);

  const set = (key) => (val) => setForm((f) => ({ ...f, [key]: val }));

  useEffect(() => {
    let cancelled = false;
    getManualEntrySchema()
      .then((schema) => { if (!cancelled && schema?.categorical) setCategoricals(schema.categorical); })
      .catch(() => { /* fall back to built-in category lists */ });
    return () => { cancelled = true; };
  }, []);

  const handleSubmit = async (e) => {
    e.preventDefault();
    setLoading(true);
    setError(null);
    setResult(null);
    setSaved(false);
    setSaveError(null);

    try {
      const res = await predictPatientUpdate(id, form);
      setResult(res);
    } catch (err) {
      setError(err.message || 'Prediction failed. Is the backend running?');
    } finally {
      setLoading(false);
    }
  };

  const handleCommit = async () => {
    if (!result) return;
    setSaving(true);
    setSaveError(null);

    try {
      // The server scores the inputs itself; no score is sent.
      const res = await commitPatientUpdate(id, {
        raw_inputs:     form,
        discharge_date: result.discharge_date,
      });
      setAlertCreated(!!res.alert_created);
      setSaved(true);
    } catch (err) {
      setSaveError(err.message || 'Failed to update patient.');
    } finally {
      setSaving(false);
    }
  };

  if (loadingPatient) {
    return (
      <div className="p-4 sm:p-6 max-w-5xl mx-auto space-y-6 animate-pulse">
        <div className="h-6 bg-gray-200 rounded w-40" />
        <div className="h-64 bg-gray-100 rounded-xl" />
      </div>
    );
  }

  if (loadError) {
    return (
      <div className="p-4 sm:p-6 max-w-5xl mx-auto">
        <button onClick={() => navigate(-1)} className="flex items-center space-x-2 text-gray-500 hover:text-ns-navy mb-6 font-medium">
          <ArrowLeft size={18} /><span>Back</span>
        </button>
        <div className="bg-red-50 border border-red-200 rounded-xl p-4 flex items-start space-x-3 text-red-800">
          <AlertCircle size={20} className="mt-0.5 shrink-0" />
          <div>
            <div className="font-semibold">Could not load patient</div>
            <div className="text-sm mt-0.5 text-red-600">{loadError}</div>
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="p-4 sm:p-6 max-w-5xl mx-auto space-y-6 pb-16 animate-in fade-in duration-500">

      {/* Page header */}
      <div className="border-b border-gray-200 pb-4">
        <button
          onClick={() => navigate(`/patients/${id}`)}
          className="flex items-center space-x-2 text-gray-500 hover:text-ns-navy transition-colors font-medium mb-3 focus:outline-none"
        >
          <ArrowLeft size={18} />
          <span>Back to Patient</span>
        </button>
        <div className="flex items-center space-x-3">
          <UserCog className="text-ns-navy" size={28} />
          <div>
            <h1 className="text-2xl font-bold text-ns-navy">Update Patient — {id}</h1>
            <p className="text-gray-500 mt-0.5 text-sm">
              Current score: <span className={`font-semibold ${getScoreColor(originalBand)}`}>{parseFloat(originalScore).toFixed(1)}%</span> ({originalBand}).
              Edit any field below to recalculate.
            </p>
          </div>
        </div>
      </div>

      {/* A batch-loaded patient has no stored inputs to edit. Recalculating from
          a blank form scores a different, mostly-unknown patient, so say so
          rather than letting the new number look like a like-for-like update. */}
      {!editable && (
        <div className="bg-gray-50 border border-gray-200 rounded-xl p-4 flex items-start space-x-3 text-gray-800">
          <Info size={18} className="mt-0.5 shrink-0" />
          <div className="text-sm">
            <div className="font-semibold">This patient can't be edited by hand</div>
            <p className="mt-0.5">
              {lockedReason || 'Their record comes from the hospital\'s feed.'} Use Re-evaluate on the
              patient page to score it again with the current model.
            </p>
          </div>
        </div>
      )}

      {editable && filledFieldCount(form) === 0 && (
        <div className="bg-amber-50 border border-amber-200 rounded-xl p-4 flex items-start space-x-3 text-amber-900">
          <Info size={18} className="mt-0.5 shrink-0" />
          <div className="text-sm">
            <div className="font-semibold">No stored inputs for this patient</div>
            <p className="mt-0.5 text-amber-800">
              This patient was scored by the batch pipeline, which keeps the 94-feature vector out of the
              database. The current score of {parseFloat(originalScore).toFixed(1)}% came from that full vector.
              Anything you enter below is scored as a new, largely unknown patient, so the two numbers are not
              directly comparable.
            </p>
          </div>
        </div>
      )}

      {/* Form */}
      {editable && (
      <form onSubmit={handleSubmit} className="space-y-5">

        <MimicPatientForm form={form} set={set} categoricals={categoricals} />

        {error && (
          <div className="bg-red-50 border border-red-200 rounded-xl p-4 flex items-start space-x-3 text-red-800">
            <AlertCircle size={20} className="mt-0.5 shrink-0" />
            <div>
              <div className="font-semibold">Prediction failed</div>
              <div className="text-sm mt-0.5 text-red-600">{error}</div>
            </div>
          </div>
        )}

        <div className="flex items-center justify-end gap-4">
          <span className="text-sm text-gray-500">
            {filledFieldCount(form)} field{filledFieldCount(form) === 1 ? '' : 's'} supplied — the rest score as unknown
          </span>
          <button
            type="submit"
            disabled={loading}
            className="px-8 py-3 bg-ns-navy text-white rounded-xl font-semibold hover:bg-blue-900 transition-colors shadow-sm focus:ring-2 focus:ring-offset-2 focus:ring-ns-navy disabled:opacity-60 disabled:cursor-not-allowed flex items-center space-x-2"
          >
            {loading ? (
              <>
                <Loader2 size={18} className="animate-spin" />
                <span>Recalculating...</span>
              </>
            ) : (
              <span>Recalculate Risk</span>
            )}
          </button>
        </div>
      </form>
      )}

      {/* Result panel */}
      {result && (
        <div className="space-y-5 animate-in slide-in-from-bottom-4 fade-in duration-500">

          <div className="bg-white rounded-xl border border-gray-200 shadow-sm p-6">
            <div className="flex items-center justify-between mb-5">
              <h2 className="text-lg font-bold text-ns-navy flex items-center space-x-2">
                <TrendingUp size={20} className="text-gray-400" />
                <span>Recalculated Result</span>
              </h2>
              <span className="text-xs text-gray-400 font-mono">{id}</span>
            </div>

            <div className="flex items-center justify-center space-x-10 py-4">
              <div className="flex flex-col items-center opacity-60">
                <span className="text-sm text-gray-500 mb-1">Previous Score</span>
                <span className={`text-3xl font-bold ${getScoreColor(originalBand)}`}>
                  {parseFloat(originalScore).toFixed(1)}%
                </span>
                <RiskBadge riskBand={originalBand} size="sm" />
              </div>
              <ArrowRight className="text-gray-300" size={28} />
              <div className="flex flex-col items-center">
                <span className="text-sm text-gray-500 mb-1">New Score</span>
                <span className={`text-6xl font-black ${getScoreColor(result.risk_band)}`}>
                  {parseFloat(result.risk_score).toFixed(1)}%
                </span>
                <RiskBadge riskBand={result.risk_band} size="md" />
              </div>
            </div>
          </div>

          <div className="bg-white rounded-xl border border-gray-200 shadow-sm p-6">
            <h3 className="text-base font-semibold text-ns-navy mb-4 border-b pb-2">
              Top 3 Risk Drivers
            </h3>
            <div className="space-y-3">
              {(result.drivers || []).map((driver, idx) => (
                <DriverCard
                  key={idx}
                  label={driver.label}
                  value={driver.value}
                  explanation={driver.explanation}
                  category={driver.category}
                  riskBand={result.risk_band}
                />
              ))}
            </div>
          </div>

          {result.weeks?.length > 0 && (
            <div className="bg-white rounded-xl border border-gray-200 shadow-sm p-6">
              <h3 className="text-base font-semibold text-ns-navy mb-1">Weekly scores</h3>
              <p className="text-sm text-gray-600 mb-3">
                The weekly scores are recomputed from the new discharge score.
              </p>
              <WeekTable weeks={result.weeks} />
            </div>
          )}

          <AiInsightsPanel
            patientId={id}
            riskScore={result.risk_score}
            riskBand={result.risk_band}
            drivers={result.drivers}
          />

          <div className="bg-white rounded-xl border border-gray-200 shadow-sm p-6 flex flex-col sm:flex-row items-start sm:items-center justify-between gap-4">
            <div>
              <p className="font-medium text-gray-800">Save this recalculated score to the Dashboard?</p>
              <p className="text-sm text-gray-500 mt-0.5">
                The patient's worklist entry will be updated in place.
              </p>
              {saveError && (
                <p className="text-sm text-red-600 mt-1">{saveError}</p>
              )}
              {saved && (
                <div className="mt-1 space-y-1">
                  <p className="text-sm text-green-600 font-medium flex items-center gap-1">
                    <CheckCircle size={14} /> Patient updated in the worklist.
                  </p>
                  {alertCreated && (
                    <p className="text-sm text-amber-600 font-medium flex items-center gap-1">
                      <BellRing size={14} /> Risk increase alert raised for this patient.
                    </p>
                  )}
                </div>
              )}
            </div>
            <div className="flex items-center space-x-3">
              {saved ? (
                <button
                  type="button"
                  onClick={() => navigate(`/patients/${id}`)}
                  className="group px-5 py-2 bg-ns-navy text-white rounded-lg text-sm font-semibold hover:bg-blue-900 transition-colors flex items-center space-x-2"
                >
                  <span>View Patient</span>
                  <ArrowRight size={16} className="group-hover:translate-x-0.5 transition-transform" />
                </button>
              ) : (
                <button
                  type="button"
                  onClick={handleCommit}
                  disabled={saving}
                  className="px-5 py-2 bg-ns-navy text-white rounded-lg text-sm font-semibold hover:bg-blue-900 transition-colors flex items-center space-x-2 disabled:opacity-60 disabled:cursor-not-allowed"
                >
                  {saving ? (
                    <>
                      <Loader2 size={16} className="animate-spin" />
                      <span>Saving...</span>
                    </>
                  ) : (
                    <span>Update Patient</span>
                  )}
                </button>
              )}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

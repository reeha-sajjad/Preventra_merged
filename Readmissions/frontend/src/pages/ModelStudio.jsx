import React, { useCallback, useEffect, useState } from 'react';
import { FlaskConical, Boxes, Calculator, Sparkles, AlertCircle, Loader2 } from 'lucide-react';
import { getStudioOverview, getStudioModels } from '../api';
import TrainSetup from '../components/studio/TrainSetup';
import TrainingRun from '../components/studio/TrainingRun';
import ModelLibrary from '../components/studio/ModelLibrary';
import ScorePanel from '../components/studio/ScorePanel';
import { TARGET_LABELS, fmt } from '../components/studio/studioFormat';

const TABS = [
  { key: 'train', label: 'Train a model', icon: <FlaskConical size={16} /> },
  { key: 'library', label: 'Model library', icon: <Boxes size={16} /> },
  { key: 'score', label: 'Score patients', icon: <Calculator size={16} /> },
];

/**
 * Model Studio: train a readmission model on this hospital's own data, see how
 * it performs against the model in use, and choose which one is active.
 * The backend is api/model_studio.py.
 */
export default function ModelStudio() {
  const [tab, setTab] = useState('train');
  const [overview, setOverview] = useState(null);
  const [models, setModels] = useState(null);
  const [error, setError] = useState(null);
  const [jobId, setJobId] = useState(null);
  const [preset, setPreset] = useState(null);

  // Bumped to reload the active models, the library and the recent runs.
  const [reloadKey, setReloadKey] = useState(0);
  const refresh = useCallback(() => setReloadKey((k) => k + 1), []);

  useEffect(() => {
    let live = true;
    Promise.all([getStudioOverview(), getStudioModels()])
      .then(([o, m]) => {
        if (!live) return;
        setOverview(o);
        setModels(m);
        setError(null);
      })
      .catch((e) => { if (live) setError(e.message); });
    return () => { live = false; };
  }, [reloadKey]);

  const retrain = (model) => {
    setPreset({ target: model.target, baseModelId: model.model_id, key: Date.now() });
    setJobId(null);
    setTab('train');
  };

  return (
    <div className="mx-auto max-w-7xl p-4 sm:p-6">
      <div className="mb-5 flex flex-wrap items-start justify-between gap-4">
        <div>
          <h1 className="flex items-center gap-2 text-2xl font-bold text-ns-navy">
            <FlaskConical size={26} /> Model Studio
          </h1>
          <p className="mt-1 text-sm text-gray-500">
            Train readmission models on your hospital's own data, compare them with the model in use,
            and choose which one scores your patients.
          </p>
        </div>
        {overview && (
          <span className={`inline-flex items-center gap-1.5 rounded-full px-3 py-1 text-xs font-medium ${overview.llm_available
            ? 'bg-indigo-50 text-indigo-700' : 'bg-amber-50 text-amber-800'}`}>
            <Sparkles size={13} />
            {overview.llm_available ? 'Gemini connected' : 'Gemini not configured: standard pipeline only'}
          </span>
        )}
      </div>

      {error && (
        <div className="mb-5 flex items-start gap-2 rounded-xl border border-red-200 bg-red-50 p-4 text-sm text-red-700">
          <AlertCircle size={18} className="mt-0.5 shrink-0" /> {error}
        </div>
      )}

      {overview && (
        <div className="mb-5 grid gap-3 sm:grid-cols-2">
          {Object.entries(TARGET_LABELS).map(([t, label]) => {
            const m = overview.active?.[t];
            return (
              <div key={t} className="rounded-xl border border-gray-200 bg-white px-4 py-3 shadow-sm">
                <div className="text-xs font-semibold uppercase tracking-wide text-gray-400">Active · {label}</div>
                <div className="mt-0.5 flex flex-wrap items-baseline gap-x-2 font-semibold text-gray-800">
                  {m?.name || '—'}
                  {m?.builtin && <span className="text-xs font-normal text-gray-500">built-in</span>}
                </div>
                <div className="text-xs text-gray-500">
                  {m?.metrics?.auroc != null ? `AUROC ${fmt(m.metrics.auroc)}` : m?.algorithm}
                </div>
              </div>
            );
          })}
        </div>
      )}

      <div className="mb-5 flex gap-1 border-b border-gray-200">
        {TABS.map((t) => (
          <button key={t.key} type="button" onClick={() => setTab(t.key)}
            className={`-mb-px inline-flex items-center gap-2 border-b-2 px-4 py-2 text-sm font-medium transition-colors ${tab === t.key
              ? 'border-ns-navy text-ns-navy' : 'border-transparent text-gray-500 hover:text-gray-700'}`}>
            {t.icon} {t.label}
          </button>
        ))}
      </div>

      {!overview && !error && (
        <div className="flex items-center gap-2 text-sm text-gray-500"><Loader2 size={16} className="animate-spin" /> Loading…</div>
      )}

      {overview && tab === 'train' && (jobId ? (
        <TrainingRun key={jobId} jobId={jobId} onModelsChanged={refresh}
          onClose={() => { setJobId(null); setPreset(null); refresh(); }} />
      ) : (
        <TrainSetup key={preset?.key || 'new'} overview={overview} models={models} preset={preset}
          onStarted={(id) => { setJobId(id); refresh(); }} onOpenJob={setJobId} />
      ))}
      {overview && tab === 'library' && (
        <ModelLibrary models={models} onChanged={refresh} onRetrain={retrain} />
      )}
      {overview && tab === 'score' && <ScorePanel overview={overview} />}
    </div>
  );
}

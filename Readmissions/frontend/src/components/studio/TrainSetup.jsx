import React, { useMemo, useRef, useState } from 'react';
import { UploadCloud, Loader2, Play, ShieldCheck, FileSpreadsheet, History } from 'lucide-react';
import { startStudioJob } from '../../api';
import { Card } from './StudioParts';
import { TARGET_LABELS, fmt } from './studioFormat';

const TARGET_HELP = {
  discharge: 'One row per hospital stay, as known at discharge, with a 30-day readmission outcome.',
  weekly: 'One row per patient per monitoring week (weight, adherence, refills, vitals...), with the readmission outcome and a patient identifier.',
};

/**
 * Start a run: what to predict, from scratch or by retraining a saved or
 * built-in model, which algorithm family, and the CSV.
 */
export default function TrainSetup({ overview, models, preset, onStarted, onOpenJob }) {
  const [target, setTarget] = useState(preset?.target || 'discharge');
  const [mode, setMode] = useState(preset?.baseModelId ? 'retrain' : 'new');
  const [baseModelId, setBaseModelId] = useState(preset?.baseModelId || '');
  const [algorithm, setAlgorithm] = useState('auto');
  const [name, setName] = useState('');
  const [file, setFile] = useState(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const [drag, setDrag] = useState(false);
  const input = useRef(null);

  const retrainable = useMemo(
    () => (models || []).filter((m) => m.target === target && m.can_retrain !== false),
    [models, target]);
  const limits = overview?.limits || {};

  const pick = (f) => {
    if (!f) return;
    if (!f.name.toLowerCase().endsWith('.csv')) { setError('Choose a .csv file.'); return; }
    setError(null);
    setFile(f);
  };

  const start = async () => {
    setBusy(true);
    setError(null);
    try {
      const job = await startStudioJob({
        file, target, mode, algorithm, name,
        baseModelId: mode === 'retrain' ? baseModelId : null,
      });
      onStarted(job.job_id);
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  };

  const seg = (value, current, set, label) => (
    <button key={value} type="button" onClick={() => set(value)}
      className={`flex-1 rounded-lg border px-3 py-2 text-sm font-medium transition-colors ${current === value
        ? 'border-ns-navy bg-ns-navy text-white' : 'border-gray-300 bg-white text-gray-700 hover:bg-gray-50'}`}>
      {label}
    </button>
  );

  return (
    <div className="grid gap-5 lg:grid-cols-3">
      <Card title="New training run" className="lg:col-span-2">
        <div className="space-y-5">
          <div>
            <div className="mb-1.5 text-sm font-medium text-gray-700">What should the model predict?</div>
            <div className="flex gap-2">
              {Object.entries(TARGET_LABELS).map(([k, v]) => seg(k, target, (t) => {
                setTarget(t);
                setBaseModelId('');
              }, v))}
            </div>
            <p className="mt-1.5 text-xs text-gray-500">{TARGET_HELP[target]}</p>
          </div>

          <div>
            <div className="mb-1.5 text-sm font-medium text-gray-700">Start from</div>
            <div className="flex gap-2">
              {seg('new', mode, setMode, 'A new model')}
              {seg('retrain', mode, setMode, 'Retrain an existing model')}
            </div>
            {mode === 'retrain' && (
              <div className="mt-2">
                <select value={baseModelId} onChange={(e) => setBaseModelId(e.target.value)}
                  className="w-full rounded-lg border border-gray-300 bg-white px-3 py-2 text-sm">
                  <option value="">Choose the model to retrain…</option>
                  {retrainable.map((m) => (
                    <option key={m.model_id} value={m.model_id}>
                      {m.name}{m.builtin ? ' (built-in)' : ''} · AUROC {fmt(m.metrics?.auroc)}
                      {m.active ? ' · active' : ''}
                    </option>
                  ))}
                </select>
                <p className="mt-1.5 text-xs text-gray-500">
                  Gemini starts from that model's pipeline and adapts it to the new file; the result is
                  compared with it on the same new test patients.
                </p>
              </div>
            )}
          </div>

          <label className="block text-sm">
            <span className="mb-1.5 block font-medium text-gray-700">Algorithm</span>
            <select value={algorithm} onChange={(e) => setAlgorithm(e.target.value)}
              className="w-full rounded-lg border border-gray-300 bg-white px-3 py-2 text-sm">
              {Object.entries(overview?.algorithms || { auto: 'Let the pipeline choose' }).map(([k, v]) => (
                <option key={k} value={k}>{v}</option>
              ))}
            </select>
          </label>

          <label className="block text-sm">
            <span className="mb-1.5 block font-medium text-gray-700">Name (optional)</span>
            <input value={name} onChange={(e) => setName(e.target.value)}
              placeholder={`${TARGET_LABELS[target]} model, ${new Date().toLocaleDateString()}`}
              className="w-full rounded-lg border border-gray-300 px-3 py-2 text-sm" />
          </label>

          <div
            onDragOver={(e) => { e.preventDefault(); setDrag(true); }}
            onDragLeave={() => setDrag(false)}
            onDrop={(e) => { e.preventDefault(); setDrag(false); pick(e.dataTransfer.files?.[0]); }}
            onClick={() => input.current?.click()}
            className={`flex cursor-pointer flex-col items-center justify-center rounded-xl border-2 border-dashed px-4 py-8 text-center transition-colors ${drag ? 'border-ns-navy bg-ns-navy/5' : 'border-gray-300 hover:border-ns-navy/50'}`}>
            <input ref={input} type="file" accept=".csv,text/csv" className="hidden"
              onChange={(e) => pick(e.target.files?.[0])} />
            {file ? (
              <>
                <FileSpreadsheet size={28} className="mb-2 text-ns-navy" />
                <div className="text-sm font-medium text-gray-800">{file.name}</div>
                <div className="text-xs text-gray-500">{(file.size / 1e6).toFixed(1)} MB · click to change</div>
              </>
            ) : (
              <>
                <UploadCloud size={28} className="mb-2 text-gray-400" />
                <div className="text-sm font-medium text-gray-700">Drop a CSV here, or click to choose</div>
                <div className="text-xs text-gray-500">
                  Up to {limits.max_upload_mb || 200} MB; files over {(limits.max_rows || 300000).toLocaleString()} rows are sampled by patient
                </div>
              </>
            )}
          </div>

          {error && <div className="rounded-lg border border-red-200 bg-red-50 p-3 text-sm text-red-700">{error}</div>}
          <div className="flex justify-end">
            <button type="button" onClick={start}
              disabled={busy || !file || (mode === 'retrain' && !baseModelId)}
              className="inline-flex items-center gap-2 rounded-lg bg-ns-navy px-5 py-2 text-sm font-semibold text-white hover:bg-ns-navy/90 disabled:opacity-50">
              {busy ? <Loader2 size={16} className="animate-spin" /> : <Play size={16} />} Start
            </button>
          </div>
        </div>
      </Card>

      <div className="space-y-5">
        <Card title="How a run works" icon={<ShieldCheck size={18} />}>
          <ol className="list-decimal space-y-1.5 pl-4 text-sm text-gray-700">
            <li>The file is profiled: types, missing values, ranges.</li>
            <li>{overview?.llm_available ? 'Gemini proposes' : 'Name and type rules propose'} the outcome, patient id and feature roles. <b>You review and edit the plan.</b></li>
            <li>{overview?.llm_available ? 'Gemini writes' : 'The standard pipeline provides'} the preprocessing, transformation and training code. It is checked, then run in an isolated process.</li>
            <li>Whole patients are held out for testing (the newest ones, if there is a date).</li>
            <li>Metrics, comparisons and fairness checks are computed independently of the generated code, and fixed rules recommend whether to deploy.</li>
          </ol>
          <p className="mt-3 rounded-lg bg-indigo-50 p-2.5 text-xs text-indigo-800">
            Gemini sees column statistics only, never patient rows.
          </p>
        </Card>
        {overview?.jobs?.length > 0 && (
          <Card title="Recent runs" icon={<History size={18} />}>
            <ul className="space-y-1.5">
              {overview.jobs.map((j) => (
                <li key={j.job_id}>
                  <button type="button" onClick={() => onOpenJob(j.job_id)}
                    className="w-full rounded-lg px-2 py-1.5 text-left text-sm hover:bg-gray-50">
                    <div className="font-medium text-gray-800">{j.name}</div>
                    <div className="text-xs text-gray-500">{j.created_at} · {j.status.replace(/_/g, ' ')}</div>
                  </button>
                </li>
              ))}
            </ul>
          </Card>
        )}
      </div>
    </div>
  );
}

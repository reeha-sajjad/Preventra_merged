import React, { useCallback, useEffect, useRef, useState } from 'react';
import {
  FileSpreadsheet, Code2, ScrollText, Save, Rocket, RotateCcw, Loader2, CheckCircle2, X,
  AlertTriangle, ChevronRight,
} from 'lucide-react';
import {
  getStudioJob, confirmStudioJob, saveStudioJob, deployStudioModel, discardStudioJob,
} from '../../api';
import { Card, CodeViewer, StepIcon } from './StudioParts';
import { TARGET_LABELS } from './studioFormat';
import PlanReview from './PlanReview';
import ModelReport from './ModelReport';

const STEPS = [
  ['profile', 'Profile data'], ['analyze', 'AI analysis'], ['confirm', 'Review plan'],
  ['generate', 'Write pipeline'], ['train', 'Train'], ['evaluate', 'Evaluate'],
  ['recommend', 'Recommend'],
];
const ACTIVE = new Set(['running', 'queued']);

/** One training run, from upload to deploy. Polls while the server is working. */
export default function TrainingRun({ jobId, onClose, onModelsChanged }) {
  const [job, setJob] = useState(null);
  const [error, setError] = useState(null);
  const [confirming, setConfirming] = useState(false);
  const [confirmError, setConfirmError] = useState(null);
  const [saving, setSaving] = useState(false);
  const [deploying, setDeploying] = useState(false);
  const [name, setName] = useState('');
  const [deployed, setDeployed] = useState(null);
  const timer = useRef(null);

  const load = useCallback(async () => {
    try {
      const j = await getStudioJob(jobId);
      setJob(j);
      setName((n) => n || j.name);
      return j;
    } catch (e) {
      setError(e.message);
      return null;
    }
  }, [jobId]);

  useEffect(() => {
    let live = true;
    const tick = async () => {
      const j = await load();
      if (live && j && ACTIVE.has(j.status)) timer.current = setTimeout(tick, 2000);
    };
    tick();
    return () => { live = false; clearTimeout(timer.current); };
  }, [load]);

  const poll = () => {
    clearTimeout(timer.current);
    const tick = async () => {
      const j = await load();
      if (j && ACTIVE.has(j.status)) timer.current = setTimeout(tick, 2000);
    };
    timer.current = setTimeout(tick, 1000);
  };

  const confirm = async (plan) => {
    setConfirming(true);
    setConfirmError(null);
    try {
      setJob(await confirmStudioJob(jobId, plan));
      poll();
    } catch (e) {
      setConfirmError(e.message);
    } finally {
      setConfirming(false);
    }
  };

  const save = async () => {
    setSaving(true);
    try {
      await saveStudioJob(jobId, name);
      await load();
      onModelsChanged?.();
    } catch (e) {
      setError(e.message);
    } finally {
      setSaving(false);
    }
  };

  const deploy = async () => {
    const decision = job.verdict?.decision;
    const warn = decision === 'do_not_deploy'
      ? 'The evaluation recommends NOT deploying this model. Deploy it anyway?'
      : decision === 'caution'
        ? 'The evaluation recommends caution. Deploy this model for your hospital?'
        : `Deploy "${name}" as the active ${TARGET_LABELS[job.target].toLowerCase()} model?`;
    if (!window.confirm(warn)) return;
    setDeploying(true);
    try {
      let modelId = job.saved_model_id;
      if (!modelId) modelId = (await saveStudioJob(jobId, name)).model_id;
      setDeployed(await deployStudioModel(modelId));
      await load();
      onModelsChanged?.();
    } catch (e) {
      setError(e.message);
    } finally {
      setDeploying(false);
    }
  };

  const discard = async () => {
    if (job?.status === 'completed' && !job.saved_model_id
        && !window.confirm('This model has not been saved. Discard it?')) return;
    try { await discardStudioJob(jobId); } catch { /* already gone */ }
    onClose();
  };

  if (error && !job) {
    return <div className="rounded-lg border border-red-200 bg-red-50 p-4 text-sm text-red-700">{error}</div>;
  }
  if (!job) return <div className="flex items-center gap-2 text-sm text-gray-500"><Loader2 size={16} className="animate-spin" /> Loading run…</div>;

  const done = job.status === 'completed';
  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h2 className="text-lg font-semibold text-ns-navy">{job.name}</h2>
          <p className="text-sm text-gray-500">
            {TARGET_LABELS[job.target]} · {job.mode === 'retrain' ? `retraining ${job.base_model_id}` : 'new model'} ·{' '}
            <span className="inline-flex items-center gap-1"><FileSpreadsheet size={13} />{job.filename}</span>
          </p>
        </div>
        <button type="button" onClick={discard}
          className="inline-flex items-center gap-1.5 rounded-lg border border-gray-300 px-3 py-1.5 text-sm text-gray-700 hover:bg-gray-50">
          {done || job.status === 'failed' ? <RotateCcw size={15} /> : <X size={15} />}
          {done || job.status === 'failed' ? 'New run' : 'Cancel run'}
        </button>
      </div>

      <ol className="grid grid-cols-2 gap-2 sm:grid-cols-4 2xl:grid-cols-7">
        {STEPS.map(([key, label], i) => {
          const s = job.steps?.[key] || {};
          return (
            <li key={key} className={`rounded-lg border px-3 py-2 ${s.status === 'running' ? 'border-ns-navy/40 bg-ns-navy/5'
              : s.status === 'waiting' ? 'border-amber-300 bg-amber-50' : s.status === 'failed' ? 'border-red-200 bg-red-50'
                : 'border-gray-200 bg-white'}`}>
              <div className="flex items-center gap-1.5 text-sm font-medium text-gray-800">
                <StepIcon status={s.status} /> <span className="text-xs text-gray-400">{i + 1}</span> {label}
              </div>
              {s.detail && <div className="mt-1 line-clamp-2 text-[11px] text-gray-500" title={s.detail}>{s.detail}</div>}
            </li>
          );
        })}
      </ol>

      {job.status === 'failed' && (
        <div className="flex items-start gap-2 rounded-xl border border-red-200 bg-red-50 p-4 text-sm text-red-800">
          <AlertTriangle size={18} className="mt-0.5 shrink-0" />
          <div><b>The run stopped.</b> {job.error}</div>
        </div>
      )}

      {job.status === 'awaiting_confirmation' && job.plan && (
        <PlanReview job={job} onConfirm={confirm} busy={confirming} error={confirmError} />
      )}

      {done && (
        <Card>
          <div className="flex flex-wrap items-end gap-3">
            <label className="min-w-[16rem] flex-1 text-sm">
              <span className="mb-1 block font-medium text-gray-700">Model name</span>
              <input value={name} onChange={(e) => setName(e.target.value)} disabled={!!job.saved_model_id}
                className="w-full rounded-lg border border-gray-300 px-3 py-1.5 text-sm disabled:bg-gray-50" />
            </label>
            {job.saved_model_id ? (
              <span className="inline-flex items-center gap-1.5 rounded-lg bg-emerald-50 px-3 py-2 text-sm font-medium text-emerald-700">
                <CheckCircle2 size={16} /> Saved as {job.saved_model_id}
              </span>
            ) : (
              <button type="button" onClick={save} disabled={saving || !name.trim()}
                className="inline-flex items-center gap-2 rounded-lg border border-ns-navy px-4 py-2 text-sm font-semibold text-ns-navy hover:bg-ns-navy/5 disabled:opacity-50">
                {saving ? <Loader2 size={16} className="animate-spin" /> : <Save size={16} />} Save model
              </button>
            )}
            <button type="button" onClick={deploy} disabled={deploying || !!deployed}
              className="inline-flex items-center gap-2 rounded-lg bg-ns-navy px-4 py-2 text-sm font-semibold text-white hover:bg-ns-navy/90 disabled:opacity-50">
              {deploying ? <Loader2 size={16} className="animate-spin" /> : <Rocket size={16} />}
              {deployed ? 'Deployed' : 'Deploy'}
            </button>
          </div>
          {deployed && (
            <p className="mt-3 text-sm text-emerald-700">
              {deployed.name} is now the active {TARGET_LABELS[deployed.target].toLowerCase()} model for your
              hospital. New patient files scored in "Score patients" use it.
            </p>
          )}
          {error && <p className="mt-3 text-sm text-red-700">{error}</p>}
        </Card>
      )}

      {done && (
        <ModelReport evaluation={job.evaluation} verdict={job.verdict} explanation={job.explanation}
          trainReport={job.train_report} />
      )}

      {job.code && (
        <Card title="Pipeline code" icon={<Code2 size={18} />}>
          <CodeViewer code={job.code} sections={job.code_sections} origin={job.code_origin}
            attempts={job.code_attempts} filename={`${job.job_id}_pipeline.py`} />
        </Card>
      )}

      {job.profile && job.status !== 'awaiting_confirmation' && (
        <details className="group rounded-xl border border-gray-200 bg-white shadow-sm">
          <summary className="flex cursor-pointer list-none items-center gap-2 px-5 py-3 font-semibold text-ns-navy">
            <ChevronRight size={16} className="transition-transform group-open:rotate-90" />
            <FileSpreadsheet size={18} /> Data: {job.profile.n_rows.toLocaleString()} rows, {job.profile.n_columns} columns
          </summary>
          {job.plan && (
            <div className="px-5 pb-4 text-sm text-gray-700">
              {job.plan.summary && <p className="mb-2">{job.plan.summary}</p>}
              <p>Outcome <b>{job.plan.label_column}</b>
                {job.plan.id_column && <> · patient id <b>{job.plan.id_column}</b></>}
                {job.plan.time_column && <> · time <b>{job.plan.time_column}</b></>} ·{' '}
                {(job.plan.numeric_columns?.length || 0) + (job.plan.categorical_columns?.length || 0)} features ·{' '}
                {job.plan.drop_columns?.length || 0} left out
              </p>
            </div>
          )}
        </details>
      )}

      <details className="group rounded-xl border border-gray-200 bg-white shadow-sm" open={ACTIVE.has(job.status)}>
        <summary className="flex cursor-pointer list-none items-center gap-2 px-5 py-3 font-semibold text-ns-navy">
          <ChevronRight size={16} className="transition-transform group-open:rotate-90" />
          <ScrollText size={18} /> Run log
          {ACTIVE.has(job.status) && <Loader2 size={15} className="animate-spin text-gray-400" />}
        </summary>
        <div className="max-h-72 overflow-auto border-t border-gray-100 bg-gray-50 px-5 py-3 font-mono text-xs text-gray-700">
          {(job.logs || []).map((l, i) => (
            <div key={i} className="whitespace-pre-wrap">
              <span className="text-gray-400">{l.t.slice(11)}</span>{' '}
              <span className="text-indigo-600">[{l.step}]</span> {l.message}
            </div>
          ))}
        </div>
      </details>
    </div>
  );
}

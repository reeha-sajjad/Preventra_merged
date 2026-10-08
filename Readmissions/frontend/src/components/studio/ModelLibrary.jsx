import React, { useState } from 'react';
import { Rocket, RefreshCw, Trash2, Eye, Loader2, Boxes, X, Code2 } from 'lucide-react';
import { deployStudioModel, deleteStudioModel, getStudioModel } from '../../api';
import { Card, CodeViewer, VerdictBadge } from './StudioParts';
import { TARGET_LABELS, fmt } from './studioFormat';
import ModelReport from './ModelReport';

/**
 * Every model this hospital can use: the built-ins, and each saved run. Any of
 * them can be made active (deploy), retrained on a new file, or inspected.
 */
export default function ModelLibrary({ models, onChanged, onRetrain }) {
  const [busy, setBusy] = useState(null);
  const [error, setError] = useState(null);
  const [open, setOpen] = useState(null);

  const act = async (id, fn, confirmText) => {
    if (confirmText && !window.confirm(confirmText)) return;
    setBusy(id);
    setError(null);
    try {
      await fn(id);
      onChanged();
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(null);
    }
  };

  const view = async (m) => {
    if (m.builtin) { setOpen({ ...m }); return; }
    setBusy(m.model_id);
    try { setOpen(await getStudioModel(m.model_id)); } catch (e) { setError(e.message); }
    finally { setBusy(null); }
  };

  if (open) return <ModelDetail model={open} onClose={() => setOpen(null)} />;

  return (
    <div className="space-y-5">
      {error && <div className="rounded-lg border border-red-200 bg-red-50 p-3 text-sm text-red-700">{error}</div>}
      {['discharge', 'weekly'].map((target) => {
        const list = (models || []).filter((m) => m.target === target);
        return (
          <Card key={target} title={TARGET_LABELS[target]} icon={<Boxes size={18} />}>
            {list.length === 0 ? <p className="text-sm text-gray-500">No models yet.</p> : (
              <div className="overflow-x-auto">
                <table className="w-full text-left text-sm">
                  <thead className="text-xs text-gray-500">
                    <tr><th className="pb-2 pr-3">Model</th><th className="pb-2 pr-3">AUROC</th>
                      <th className="pb-2 pr-3">Brier</th><th className="pb-2 pr-3">Trained on</th>
                      <th className="pb-2 pr-3">Recommendation</th><th className="pb-2 text-right">Actions</th></tr>
                  </thead>
                  <tbody className="divide-y divide-gray-100">
                    {list.map((m) => (
                      <tr key={m.model_id} className={m.active ? 'bg-emerald-50/50' : ''}>
                        <td className="py-2.5 pr-3">
                          <div className="flex flex-wrap items-center gap-2 font-medium text-gray-800">
                            {m.name}
                            {m.active && <span className="rounded-full bg-emerald-600 px-2 py-0.5 text-[11px] font-semibold text-white">Active</span>}
                            {m.builtin && <span className="rounded-full bg-gray-200 px-2 py-0.5 text-[11px] font-semibold text-gray-700">Built-in</span>}
                          </div>
                          <div className="text-xs text-gray-500">
                            {m.algorithm ? String(m.algorithm).replace(/_/g, ' ') : ''}
                            {m.created_at && <> · {m.created_at}</>}{m.created_by && <> · {m.created_by}</>}
                            {m.base_model_id && <> · retrained from {m.base_model_id}</>}
                          </div>
                        </td>
                        <td className="pr-3 tabular-nums">{fmt(m.metrics?.auroc)}</td>
                        <td className="pr-3 tabular-nums">{fmt(m.metrics?.brier)}</td>
                        <td className="pr-3 text-xs text-gray-600">
                          {m.data?.rows ? `${Number(m.data.rows).toLocaleString()} rows` : '—'}
                          {m.data?.filename && <div className="truncate text-gray-400">{m.data.filename}</div>}
                        </td>
                        <td className="pr-3">{m.verdict ? <VerdictBadge verdict={m.verdict} /> : <span className="text-xs text-gray-400">—</span>}</td>
                        <td className="py-2.5">
                          <div className="flex justify-end gap-1.5">
                            <IconButton title="View" onClick={() => view(m)} busy={busy === m.model_id}><Eye size={15} /></IconButton>
                            {m.can_retrain !== false && (
                              <IconButton title="Retrain on a new file" onClick={() => onRetrain(m)}><RefreshCw size={15} /></IconButton>
                            )}
                            {!m.active && (
                              <IconButton title="Deploy (make active)" onClick={() => act(m.model_id, deployStudioModel,
                                `Make "${m.name}" the active ${TARGET_LABELS[m.target].toLowerCase()} model for your hospital?`)}
                                busy={busy === m.model_id}><Rocket size={15} /></IconButton>
                            )}
                            {!m.builtin && !m.active && (
                              <IconButton title="Delete" danger onClick={() => act(m.model_id, deleteStudioModel,
                                `Delete "${m.name}"? This cannot be undone.`)}><Trash2 size={15} /></IconButton>
                            )}
                          </div>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </Card>
        );
      })}
    </div>
  );
}

function IconButton({ title, onClick, busy, danger, children }) {
  return (
    <button type="button" title={title} aria-label={title} onClick={onClick} disabled={busy}
      className={`rounded-lg border p-1.5 transition-colors disabled:opacity-50 ${danger
        ? 'border-red-200 text-red-600 hover:bg-red-50' : 'border-gray-300 text-gray-700 hover:bg-gray-50'}`}>
      {busy ? <Loader2 size={15} className="animate-spin" /> : children}
    </button>
  );
}

function ModelDetail({ model, onClose }) {
  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h2 className="text-lg font-semibold text-ns-navy">{model.name}</h2>
          <p className="text-sm text-gray-500">
            {TARGET_LABELS[model.target]} · {model.model_id}
            {model.data?.label_column && <> · outcome <b>{model.data.label_column}</b></>}
            {model.data?.id_column && <> · patient id <b>{model.data.id_column}</b></>}
          </p>
        </div>
        <button type="button" onClick={onClose}
          className="inline-flex items-center gap-1.5 rounded-lg border border-gray-300 px-3 py-1.5 text-sm text-gray-700 hover:bg-gray-50">
          <X size={15} /> Back to library
        </button>
      </div>
      {model.builtin ? (
        <Card>
          <p className="text-sm text-gray-700">{model.description}</p>
          {model.metrics?.auroc != null && (
            <p className="mt-2 text-sm text-gray-600">Reported AUROC {fmt(model.metrics.auroc)}, AUPRC{' '}
              {fmt(model.metrics.auprc)}, Brier {fmt(model.metrics.brier)} on its own MIMIC-IV test set.</p>
          )}
        </Card>
      ) : (
        <>
          <ModelReport evaluation={model.evaluation} verdict={model.verdict}
            explanation={model.explanation} trainReport={model.train_report} />
          {model.code && (
            <Card title="Pipeline code" icon={<Code2 size={18} />}>
              <CodeViewer code={model.code} origin={model.code_origin}
                sections={model.code_sections} filename={`${model.model_id}_pipeline.py`} />
            </Card>
          )}
        </>
      )}
    </div>
  );
}

import React, { useMemo, useRef, useState } from 'react';
import { UploadCloud, Loader2, Download, Calculator, AlertTriangle, FileSpreadsheet } from 'lucide-react';
import { scoreStudioFile } from '../../api';
import RiskBadge from '../shared/RiskBadge';
import { Card } from './StudioParts';
import { TARGET_LABELS, download } from './studioFormat';

const PAGE = 100;

/** Score a file of new patients with the hospital's active model. */
export default function ScorePanel({ overview }) {
  const [target, setTarget] = useState('discharge');
  const [file, setFile] = useState(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const [result, setResult] = useState(null);
  const [shown, setShown] = useState(PAGE);
  const input = useRef(null);
  const active = overview?.active?.[target];

  const rows = useMemo(() => (result ? [...result.rows].sort((a, b) => b.score - a.score) : []),
    [result]);

  const score = async () => {
    setBusy(true);
    setError(null);
    setResult(null);
    try {
      setResult(await scoreStudioFile({ file, target }));
      setShown(PAGE);
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  };

  const exportCsv = () => {
    const head = ['row', result.id_column || 'id', 'risk_score_pct', 'risk_band'].join(',');
    const lines = result.rows.map((r) => [r.row, r.id ?? '', r.score, r.band].join(','));
    download([head, ...lines].join('\n'), `scores_${target}_${new Date().toISOString().slice(0, 10)}.csv`,
      'text/csv');
  };

  return (
    <div className="space-y-5">
      <Card title="Score new patients" icon={<Calculator size={18} />}>
        <div className="grid gap-4 md:grid-cols-3">
          <label className="text-sm">
            <span className="mb-1.5 block font-medium text-gray-700">Model</span>
            <select value={target} onChange={(e) => { setTarget(e.target.value); setResult(null); }}
              className="w-full rounded-lg border border-gray-300 bg-white px-3 py-2 text-sm">
              {Object.entries(TARGET_LABELS).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
            </select>
            {active && (
              <span className="mt-1.5 block text-xs text-gray-500">
                Active: <b className="text-gray-700">{active.name}</b>
                {active.can_score === false && <span className="text-amber-700"> (rules-based; cannot score files)</span>}
              </span>
            )}
          </label>
          <div className="text-sm md:col-span-2">
            <span className="mb-1.5 block font-medium text-gray-700">Patient file (.csv, same columns as training)</span>
            <button type="button" onClick={() => input.current?.click()}
              className="flex w-full items-center gap-3 rounded-lg border border-dashed border-gray-300 px-3 py-2.5 text-left hover:border-ns-navy/50">
              <input ref={input} type="file" accept=".csv,text/csv" className="hidden"
                onChange={(e) => { setFile(e.target.files?.[0] || null); setResult(null); }} />
              {file ? <FileSpreadsheet size={18} className="text-ns-navy" /> : <UploadCloud size={18} className="text-gray-400" />}
              <span className={file ? 'text-gray-800' : 'text-gray-500'}>{file ? file.name : 'Choose a file…'}</span>
            </button>
          </div>
        </div>
        {error && <div className="mt-4 rounded-lg border border-red-200 bg-red-50 p-3 text-sm text-red-700">{error}</div>}
        <div className="mt-4 flex justify-end">
          <button type="button" onClick={score} disabled={busy || !file || active?.can_score === false}
            className="inline-flex items-center gap-2 rounded-lg bg-ns-navy px-5 py-2 text-sm font-semibold text-white hover:bg-ns-navy/90 disabled:opacity-50">
            {busy ? <Loader2 size={16} className="animate-spin" /> : <Calculator size={16} />} Score
          </button>
        </div>
      </Card>

      {result && (
        <Card title={`${result.rows.length.toLocaleString()} rows scored with ${result.model.name}`}
          right={<button type="button" onClick={exportCsv}
            className="inline-flex items-center gap-1.5 rounded-lg border border-gray-300 px-3 py-1.5 text-sm text-gray-700 hover:bg-gray-50">
            <Download size={15} /> Download CSV</button>}>
          <div className="mb-4 flex flex-wrap gap-3">
            {['High', 'Medium', 'Low'].map((b) => (
              <div key={b} className="flex items-center gap-2 rounded-lg border border-gray-100 bg-gray-50 px-3 py-2">
                <RiskBadge riskBand={b} size="sm" />
                <span className="text-lg font-bold text-gray-800">{(result.summary[b] || 0).toLocaleString()}</span>
              </div>
            ))}
            <div className="text-xs text-gray-500 self-center">
              High from {result.bands.high_score_threshold}%, Medium from {result.bands.low_score_threshold}%
            </div>
          </div>
          {result.missing_columns?.length > 0 && (
            <div className="mb-4 flex items-start gap-2 rounded-lg border border-amber-200 bg-amber-50 p-3 text-sm text-amber-900">
              <AlertTriangle size={16} className="mt-0.5 shrink-0" />
              <span>{result.missing_columns.length} column(s) the model uses are not in this file and were
                treated as unknown: {result.missing_columns.slice(0, 12).join(', ')}
                {result.missing_columns.length > 12 ? '…' : ''}. Scores are less reliable.</span>
            </div>
          )}
          <div className="overflow-x-auto">
            <table className="w-full text-left text-sm">
              <thead className="text-xs text-gray-500">
                <tr><th className="pb-2">Row</th><th className="pb-2">{result.id_column || 'Patient'}</th>
                  <th className="pb-2">Risk</th><th className="pb-2">Band</th></tr>
              </thead>
              <tbody className="divide-y divide-gray-100">
                {rows.slice(0, shown).map((r) => (
                  <tr key={r.row}>
                    <td className="py-1.5 text-gray-500">{r.row}</td>
                    <td className="py-1.5 font-medium text-gray-800">{r.id ?? '—'}</td>
                    <td className="py-1.5 tabular-nums font-semibold">{r.score.toFixed(1)}%</td>
                    <td className="py-1.5"><RiskBadge riskBand={r.band} size="sm" /></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {shown < rows.length && (
            <button type="button" onClick={() => setShown(shown + PAGE)}
              className="mt-3 text-sm font-medium text-ns-navy hover:underline">
              Show {Math.min(PAGE, rows.length - shown)} more (highest risk first)
            </button>
          )}
        </Card>
      )}
    </div>
  );
}

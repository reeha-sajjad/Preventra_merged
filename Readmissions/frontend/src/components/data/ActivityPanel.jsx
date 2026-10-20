import React, { useCallback, useEffect, useState } from 'react';
import { RefreshCw, ChevronDown, ChevronRight } from 'lucide-react';
import { getIngestBatches } from '../../api';
import { SOURCE_LABELS, panel, ghost, when } from './dataFormat';
import { StatusPill, Notice } from './dataParts';

const KIND_LABELS = { encounters: 'Discharges', weeks: 'Weekly check-ins', rescore: 'Re-score' };

/**
 * Everything that came in, newest first (api/ingest.py /batches): from where,
 * what was accepted or refused and why, and how far its scoring has got. Checks
 * again by itself while anything is still being scored.
 */
export default function ActivityPanel({ reloadKey = 0 }) {
  const [batches, setBatches] = useState(null);
  const [error, setError] = useState(null);
  const [open, setOpen] = useState(null);
  const [tick, setTick] = useState(0);
  const reload = useCallback(() => setTick((t) => t + 1), []);

  useEffect(() => {
    let live = true;
    getIngestBatches(40).then((r) => { if (live) { setBatches(r.batches); setError(null); } })
      .catch((e) => { if (live) setError(e.message); });
    return () => { live = false; };
  }, [tick, reloadKey]);

  const busy = (batches || []).some((b) => ['queued', 'processing'].includes(b.status));
  useEffect(() => {
    if (!busy) return undefined;
    const t = setTimeout(reload, 4000);
    return () => clearTimeout(t);
  }, [busy, batches, reload]);

  return (
    <div className={panel}>
      <div className="flex items-center justify-between">
        <h2 className="text-base font-semibold text-gray-800">Activity</h2>
        <button type="button" className={ghost} onClick={reload}><RefreshCw size={14} /> Refresh</button>
      </div>
      {error && <div className="mt-3"><Notice tone="red">{error}</Notice></div>}
      {batches && batches.length === 0 && <p className="mt-3 text-sm text-gray-500">Nothing has come in yet.</p>}
      {batches && batches.length > 0 && (
        <table className="mt-3 w-full text-left text-sm">
          <thead className="border-b border-gray-200 text-xs uppercase text-gray-500">
            <tr>
              <th className="py-2 pr-2" /><th className="py-2 pr-3">When</th><th className="py-2 pr-3">From</th>
              <th className="py-2 pr-3">What</th><th className="py-2 pr-3 text-right">Accepted</th>
              <th className="py-2 pr-3 text-right">Rejected</th><th className="py-2 pr-3 text-right">Scored</th>
              <th className="py-2">Status</th>
            </tr>
          </thead>
          <tbody>
            {batches.map((b) => {
              const detail = b.rejected.length || b.warnings.length || b.errors.length || b.not_scored.length;
              const isOpen = open === b.batch_id;
              return (
                <React.Fragment key={b.batch_id}>
                  <tr className={`border-b border-gray-100 ${detail ? 'cursor-pointer hover:bg-gray-50' : ''}`}
                      onClick={() => detail && setOpen(isOpen ? null : b.batch_id)}>
                    <td className="py-1.5 pr-2 text-gray-400">{detail ? (isOpen ? <ChevronDown size={14} /> : <ChevronRight size={14} />) : null}</td>
                    <td className="py-1.5 pr-3 whitespace-nowrap text-xs text-gray-600">{when(b.created_at)}</td>
                    <td className="py-1.5 pr-3 text-xs">
                      {SOURCE_LABELS[b.source] || b.source}
                      {b.by && <span className="text-gray-400"> · {b.by}</span>}
                    </td>
                    <td className="py-1.5 pr-3 text-xs">
                      {KIND_LABELS[b.kind] || b.kind}
                      {b.history_only && <span className="ml-1 rounded bg-indigo-50 px-1.5 py-0.5 text-[10px] font-semibold text-indigo-700">history</span>}
                    </td>
                    <td className="py-1.5 pr-3 text-right">{b.accepted}</td>
                    <td className={`py-1.5 pr-3 text-right ${b.rejected.length ? 'text-red-700' : ''}`}>{b.rejected.length}</td>
                    <td className="py-1.5 pr-3 text-right">{b.history_only ? '—' : b.scored}</td>
                    <td className="py-1.5"><StatusPill status={b.status} /></td>
                  </tr>
                  {isOpen && (
                    <tr className="border-b border-gray-100 bg-gray-50">
                      <td />
                      <td colSpan={7} className="space-y-1 py-2 pr-3 text-xs text-gray-700">
                        {b.errors.map((e, i) => <div key={`e${i}`} className="text-red-700">{e}</div>)}
                        {b.rejected.slice(0, 50).map((r, i) => (
                          <div key={`r${i}`}><strong>{r.encounter_key || `record ${r.index + 1}`}</strong>: {r.errors.join('; ')}</div>
                        ))}
                        {b.rejected.length > 50 && <div className="text-gray-500">…and {b.rejected.length - 50} more refused</div>}
                        {b.not_scored.slice(0, 20).map((n, i) => (
                          <div key={`n${i}`} className="text-gray-600">{n.encounter_key || n.patient_id}: {n.reason}</div>
                        ))}
                        {b.warnings.slice(0, 20).map((w, i) => (
                          <div key={`w${i}`} className="text-amber-800">{w.encounter_key}: {w.warning}</div>
                        ))}
                      </td>
                    </tr>
                  )}
                </React.Fragment>
              );
            })}
          </tbody>
        </table>
      )}
    </div>
  );
}

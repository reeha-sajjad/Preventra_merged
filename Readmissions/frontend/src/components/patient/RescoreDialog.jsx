import React, { useCallback, useEffect, useRef, useState } from 'react';
import { RefreshCw, X, Loader2, AlertCircle, CheckCircle, BellRing, ArrowRight } from 'lucide-react';
import { getRescoreOptions, rescorePatient } from '../../api';
import RiskBadge from '../shared/RiskBadge';

const TABS = [
  { key: 'discharge', label: 'Discharge score', intro: 'Re-scores the discharge from the inputs stored for this patient. Nothing is typed.' },
  { key: 'weekly', label: 'Weekly trend', intro: 'Re-scores each week from the discharge score and the readings already stored. Nothing is typed.' },
];

const pct = (v) => (v == null ? '-' : `${parseFloat(v).toFixed(1)}%`);
const weekName = (n) => (n === 0 ? 'Discharge' : `Wk ${n}`);

function Score({ score, band }) {
  return (
    <span className="inline-flex items-center gap-2">
      <span className="tabular-nums font-semibold text-gray-900">{pct(score)}</span>
      {band && <RiskBadge riskBand={band} size="sm" />}
    </span>
  );
}

export function WeekTable({ weeks }) {
  if (!weeks?.length) return null;
  return (
    <div className="overflow-x-auto rounded-lg border border-gray-200">
      <table className="w-full text-sm">
        <thead className="bg-gray-50 text-left text-xs uppercase tracking-wide text-gray-600">
          <tr>
            <th className="px-3 py-2 font-semibold">Week</th>
            <th className="px-3 py-2 font-semibold">Before</th>
            <th className="px-3 py-2 font-semibold">After</th>
            <th className="px-3 py-2 font-semibold">Result</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-gray-100">
          {weeks.map((w) => (
            <tr key={w.week_number}>
              <td className="px-3 py-2 font-medium text-gray-800">{weekName(w.week_number)}</td>
              <td className="px-3 py-2"><Score score={w.old_score} band={w.old_band} /></td>
              <td className="px-3 py-2"><Score score={w.new_score} band={w.new_band} /></td>
              <td className="px-3 py-2 text-gray-700">
                {w.rescored === false ? `Kept (${w.reason || 'not re-scored'})` : w.changed ? 'Changed' : 'Same'}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/**
 * Re-evaluate one patient with the models, with nothing typed: the discharge
 * score (and, with it, the weeks after it) or the weekly trend alone. A preview
 * first; Save writes it. The server always does the scoring - nothing the
 * browser holds is sent as a score (api/patient_rescore.py).
 */
export default function RescoreDialog({ patientId, onSaved }) {
  const [open, setOpen] = useState(false);
  const [options, setOptions] = useState(null);
  const [tab, setTab] = useState('discharge');
  const [preview, setPreview] = useState(null);
  const [busy, setBusy] = useState(false);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(null);
  const [error, setError] = useState(null);
  const headingRef = useRef(null);

  const close = useCallback(() => {
    setOpen(false);
    setPreview(null);
    setSaved(null);
    setError(null);
  }, []);

  useEffect(() => {
    if (!open) return undefined;
    let live = true;
    headingRef.current?.focus();
    setOptions(null);
    getRescoreOptions(patientId)
      .then((o) => { if (live) setOptions(o); })
      .catch((e) => { if (live) setError(e.message); });
    const onKey = (e) => e.key === 'Escape' && close();
    window.addEventListener('keydown', onKey);
    return () => { live = false; window.removeEventListener('keydown', onKey); };
  }, [open, patientId, close]);

  const pick = (key) => {
    setTab(key);
    setPreview(null);
    setSaved(null);
    setError(null);
  };

  const run = async (commit) => {
    commit ? setSaving(true) : setBusy(true);
    setError(null);
    try {
      const res = await rescorePatient(patientId, { target: tab, commit });
      if (commit) {
        setSaved(res);
        setPreview(res);
        onSaved?.();
      } else {
        setPreview(res);
      }
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(false);
      setSaving(false);
    }
  };

  const section = options?.[tab];
  const info = TABS.find((t) => t.key === tab);
  const blocked = section && !section.possible;
  const modelName = tab === 'discharge' ? section?.model?.name : 'Monitoring rules';

  return (
    <>
      <button
        type="button"
        onClick={() => setOpen(true)}
        className="flex items-center space-x-1.5 text-sm font-semibold text-ns-navy hover:underline mt-1"
      >
        <RefreshCw size={16} />
        <span>Re-evaluate</span>
      </button>

      {open && (
        <div className="fixed inset-0 z-[60] flex items-center justify-center bg-gray-900/40 p-4" onClick={close}>
          <div
            onClick={(e) => e.stopPropagation()}
            className="max-h-[90vh] w-full max-w-2xl overflow-y-auto rounded-xl bg-white shadow-2xl"
            role="dialog" aria-modal="true" aria-label="Re-evaluate patient"
          >
            <div className="flex items-center justify-between border-b px-5 py-4">
              <h2 ref={headingRef} tabIndex={-1} className="text-lg font-semibold text-ns-navy outline-none">Re-evaluate {patientId}</h2>
              <button type="button" onClick={close} aria-label="Close"
                className="rounded-full p-1.5 text-gray-500 hover:bg-gray-100 hover:text-gray-700">
                <X size={18} />
              </button>
            </div>

            <div className="flex gap-1 border-b px-5 pt-3" role="tablist">
              {TABS.map((t) => (
                <button key={t.key} type="button" role="tab" aria-selected={tab === t.key}
                  onClick={() => pick(t.key)}
                  className={`-mb-px border-b-2 px-3 py-2 text-sm font-semibold ${tab === t.key
                    ? 'border-ns-navy text-ns-navy' : 'border-transparent text-gray-600 hover:text-gray-800'}`}>
                  {t.label}
                </button>
              ))}
            </div>

            <div className="space-y-4 px-5 py-4">
              {!options && !error && <div className="h-24 animate-pulse rounded-lg bg-gray-100" />}

              {options && (
                <>
                  <p className="text-sm text-gray-700">{info.intro}</p>
                  {!blocked && modelName && (
                    <p className="text-sm text-gray-600">Model: <strong>{modelName}</strong></p>
                  )}
                  {section?.reason && (
                    <div className="flex items-start gap-2 rounded-lg border border-gray-200 bg-gray-50 p-3 text-sm text-gray-700">
                      <AlertCircle size={16} className="mt-0.5 shrink-0 text-gray-500" />
                      <span>{section.reason}</span>
                    </div>
                  )}
                </>
              )}

              {error && (
                <div className="flex items-start gap-2 rounded-lg border border-red-200 bg-red-50 p-3 text-sm text-red-800">
                  <AlertCircle size={16} className="mt-0.5 shrink-0" />
                  <span>{error}</span>
                </div>
              )}

              {preview && (
                <div className="space-y-4">
                  {tab === 'discharge' && (
                    <div className="flex flex-wrap items-center gap-4 rounded-lg border border-gray-200 p-4">
                      <div>
                        <div className="text-xs font-medium text-gray-600">Now</div>
                        <Score score={preview.current.score} band={preview.current.band} />
                      </div>
                      <ArrowRight size={20} className="text-gray-500" />
                      <div>
                        <div className="text-xs font-medium text-gray-600">Re-evaluated</div>
                        <Score score={preview.proposed.score} band={preview.proposed.band} />
                      </div>
                    </div>
                  )}
                  {tab === 'discharge' && preview.proposed.drivers?.length > 0 && (
                    <div>
                      <div className="mb-1 text-sm font-semibold text-gray-800">Top drivers</div>
                      <ul className="space-y-1 text-sm text-gray-700">
                        {preview.proposed.drivers.map((d, i) => (
                          <li key={i}>
                            <strong>{d.label}</strong>{d.value ? `: ${d.value}` : ''}
                            {d.explanation ? ` (${d.explanation})` : ''}
                          </li>
                        ))}
                      </ul>
                    </div>
                  )}
                  {preview.proposed.note && <p className="text-sm text-gray-600">{preview.proposed.note}</p>}
                  {tab === 'discharge' && (
                    <p className="text-sm text-gray-700">Weekly scores are recomputed from the new discharge score.</p>
                  )}
                  <WeekTable weeks={preview.weeks} />
                  {saved && (
                    <div className="space-y-1">
                      <p className="flex items-center gap-1 text-sm font-medium text-green-700">
                        <CheckCircle size={14} /> Saved
                      </p>
                      {saved.alert_created && (
                        <p className="flex items-center gap-1 text-sm font-medium text-amber-800">
                          <BellRing size={14} /> Alert raised
                        </p>
                      )}
                    </div>
                  )}
                </div>
              )}
            </div>

            <div className="flex items-center justify-end gap-3 border-t px-5 py-4">
              {preview && !preview.changed && !saved && (
                <span className="text-sm text-gray-600">No change, nothing to save</span>
              )}
              <button type="button" onClick={close}
                className="rounded-lg px-4 py-2 text-sm font-semibold text-gray-700 hover:bg-gray-100">
                {saved ? 'Close' : 'Cancel'}
              </button>
              {!saved && (
                <button type="button" onClick={() => run(false)} disabled={!options || blocked || busy || saving}
                  className="flex items-center gap-2 rounded-lg border border-ns-navy px-4 py-2 text-sm font-semibold text-ns-navy hover:bg-gray-50 disabled:cursor-not-allowed disabled:opacity-50">
                  {busy && <Loader2 size={16} className="animate-spin" />}
                  <span>{busy ? 'Calculating...' : 'Preview'}</span>
                </button>
              )}
              {preview && !saved && (
                <button type="button" onClick={() => run(true)} disabled={!preview.changed || saving || busy}
                  className="flex items-center gap-2 rounded-lg bg-ns-navy px-4 py-2 text-sm font-semibold text-white hover:bg-blue-900 disabled:cursor-not-allowed disabled:opacity-50">
                  {saving && <Loader2 size={16} className="animate-spin" />}
                  <span>{saving ? 'Saving...' : 'Save'}</span>
                </button>
              )}
            </div>
          </div>
        </div>
      )}
    </>
  );
}

import React, { useCallback, useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { RefreshCw, Loader2, Moon, Download, FlaskConical } from 'lucide-react';
import { getIngestScoring, rescoreNow, downloadTrainingFile } from '../../api';
import { panel, button, ghost, when } from './dataFormat';
import { StatusPill } from './dataParts';

/**
 * Which models score this hospital's patients, how many were scored by an
 * earlier one, and a way to bring them up to date now rather than at the
 * nightly check (api/ingest.py /scoring and /rescore). Shown on the Data page
 * and in Model Studio, next to where a model is deployed.
 */
export default function RescoreCard({ compact = false, reloadKey = 0 }) {
  const navigate = useNavigate();
  const [status, setStatus] = useState(null);
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState(null);
  const [tick, setTick] = useState(0);
  const reload = useCallback(() => setTick((t) => t + 1), []);

  useEffect(() => {
    let live = true;
    getIngestScoring()
      .then((s) => { if (live) { setStatus(s); setError(null); } })
      .catch((e) => { if (live) setError(e.message); });
    return () => { live = false; };
  }, [tick, reloadKey]);

  // While a re-score runs, check on it every few seconds; otherwise every
  // quarter minute, so uploads being scored and nightly runs show up.
  const running = status?.last_rescore && ['queued', 'processing'].includes(status.last_rescore.status);
  useEffect(() => {
    if (compact && !running) return undefined;
    const t = setTimeout(reload, running ? 4000 : 15000);
    return () => clearTimeout(t);
  }, [compact, running, status, reload]);

  const rescore = async () => {
    setBusy(true); setMessage(null);
    try {
      const r = await rescoreNow();
      setMessage(r.queued ? `${r.queued} patient${r.queued === 1 ? '' : 's'} queued for re-scoring.`
        : 'Every patient is already scored by the deployed models.');
      reload();
    } catch (e) { setError(e.message); }
    finally { setBusy(false); }
  };

  const download = async () => {
    setError(null);
    try { await downloadTrainingFile(); } catch (e) { setError(e.message); }
  };

  if (error && !status) {
    return compact ? null : <div className={`${panel} text-sm text-red-700`}>{error}</div>;
  }
  if (!status) return null;
  if (compact && !status.due && !status.waiting && !running) return null;

  const models = status.models || {};
  return (
    <div className={`${panel} ${compact ? 'mb-5' : ''}`}>
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h2 className="text-base font-semibold text-gray-800">Scoring</h2>
          <p className="mt-0.5 text-sm text-gray-600">
            Discharges: <strong>{models.discharge?.name || '—'}</strong>
            {models.discharge?.builtin && <span className="text-gray-400"> (built-in)</span>}
            {' · '}Weekly: <strong>{models.weekly?.name || '—'}</strong>
          </p>
          {/* Current patients stored but not scored: no deployed model can read them yet. They are scored
              when one is deployed (api/scoring_service.waiting_for_a_model). */}
          {status.waiting > 0 && (
            <p className="mt-1 text-sm font-medium text-amber-700">
              {status.waiting} patient{status.waiting === 1 ? ' is' : 's are'} waiting for a model that can read them.
              Train one in Model Studio on your history file; they are scored as soon as you deploy it.
            </p>
          )}
          <p className="mt-1 text-sm">
            {status.due > 0 ? (
              <span className="font-medium text-amber-700">
                {status.due} of {status.patients} patient{status.patients === 1 ? '' : 's'} scored by an earlier model.
              </span>
            ) : (
              <span className="text-gray-500">
                {status.patients ? `All ${status.patients} patients are scored by the deployed models.`
                  : 'No patients have come in through Data yet.'}
              </span>
            )}
          </p>
        </div>
        <div className="flex flex-wrap gap-2">
          {!compact && (
            <button type="button" className={ghost} onClick={download}>
              <Download size={15} /> Training file
            </button>
          )}
          {status.waiting > 0 && (
            <button type="button" className={ghost} onClick={() => navigate('/model-studio')}>
              <FlaskConical size={15} /> Model Studio
            </button>
          )}
          <button type="button" className={button} onClick={rescore} disabled={busy || !(status.due || status.waiting)}>
            {busy ? <Loader2 size={15} className="animate-spin" /> : <RefreshCw size={15} />} Re-score now
          </button>
        </div>
      </div>
      <div className="mt-3 flex flex-wrap items-center gap-x-5 gap-y-1 text-xs text-gray-500">
        <span className="inline-flex items-center gap-1">
          <Moon size={13} /> Checked every night at {status.nightly?.at} ({status.nightly?.timezone}); next {when(status.nightly?.next_run_at)}
        </span>
        {status.last_rescore && (
          <span className="inline-flex items-center gap-1.5">
            Last re-score {when(status.last_rescore.at)} by {status.last_rescore.by}: {status.last_rescore.patients} patients
            <StatusPill status={status.last_rescore.status} />
          </span>
        )}
      </div>
      {message && <p className="mt-2 text-sm text-green-700">{message}</p>}
      {error && <p className="mt-2 text-sm text-red-700">{error}</p>}
    </div>
  );
}

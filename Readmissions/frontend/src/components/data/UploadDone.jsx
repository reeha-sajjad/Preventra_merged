import React, { useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { Loader2, CheckCircle2, AlertTriangle, FlaskConical, Upload } from 'lucide-react';
import { getTrainingStatus, trainFromData } from '../../api';
import { panel, button, ghost } from './dataFormat';
import { Notice } from './dataParts';
import RescoreCard from './RescoreCard';

/**
 * Step 5 of an upload: the import as it runs, what came of it, and - once a
 * hospital has enough discharges with a known outcome - training its own
 * model on them in Model Studio (api/data_upload.py /training, /train).
 */
export default function UploadDone({ draft, onAgain }) {
  const rows = draft.files.reduce((n, f) => n + f.rows, 0);
  const discharges = draft.kind === 'discharges';

  if (draft.status === 'importing') {
    return (
      <div className={panel}>
        <p className="flex items-center gap-2 font-semibold text-gray-800">
          <Loader2 size={18} className="animate-spin text-ns-navy" /> Storing {discharges ? 'discharges' : 'check-ins'}…
        </p>
        <p className="mt-1 text-sm text-gray-600">
          Each row is checked and stored{rows > 20000 ? '; a file this size takes a few minutes' : ''}. You can leave
          this page: the import carries on, and the Activity tab shows it.
        </p>
      </div>
    );
  }
  if (draft.status === 'failed') {
    return (
      <div className={`${panel} space-y-3`}>
        <Notice tone="red">The import stopped: {draft.error || 'unknown error'}</Notice>
        <button type="button" className={ghost} onClick={onAgain}>Upload again</button>
      </div>
    );
  }

  const r = draft.result || {};
  return (
    <div className="space-y-5">
      <div className={panel}>
        <p className="flex items-center gap-2 font-semibold text-gray-800">
          <CheckCircle2 size={18} className="text-green-600" /> Stored
        </p>
        <p className="mt-1 text-sm text-gray-700">
          {(r.accepted || 0).toLocaleString()} {discharges ? 'discharge' : 'check-in'}{r.accepted === 1 ? '' : 's'} stored
          {r.rejected > 0 && <>, <span className="text-red-700">{r.rejected.toLocaleString()} not stored</span></>}.{' '}
          {r.history_only ? 'Kept as history: for training your own model and as earlier admissions of future patients.'
            : 'They are being scored now and appear on the patient list within a minute or two. If no deployed model '
              + 'can read them yet, they wait - see Scoring below - and are scored as soon as you deploy one.'}
        </p>
        {r.rejected > 0 && (
          <Examples title="Not stored" tone="red" items={(r.rejected_examples || []).map((x) => ({
            key: x.encounter_key || x.patient_key || `row ${x.index + 1}`, text: (x.errors || []).join('; ') }))}
            more={r.rejected - (r.rejected_examples || []).length} />
        )}
        {r.warnings > 0 && (
          <Examples title="Stored, with something left empty" tone="amber" items={(r.warning_examples || []).map((x) => ({
            key: x.encounter_key || `row ${x.index + 1}`, text: x.warning }))}
            more={r.warnings - (r.warning_examples || []).length} />
        )}
        <div className="mt-4">
          <button type="button" className={ghost} onClick={onAgain}><Upload size={15} /> Upload more</button>
        </div>
      </div>

      {discharges && !r.history_only && <RescoreCard />}
      {discharges && <TrainCard key={draft.draft_id} />}
    </div>
  );
}

function Examples({ title, tone, items, more }) {
  const colour = tone === 'red' ? 'bg-red-50' : 'bg-amber-50';
  return (
    <details className="mt-3 text-sm">
      <summary className="cursor-pointer font-medium text-gray-700">{title}: examples</summary>
      <ul className="mt-2 max-h-64 space-y-1 overflow-y-auto text-xs text-gray-700">
        {items.map((x, i) => <li key={i} className={`rounded px-2 py-1 ${colour}`}><strong>{x.key}</strong>: {x.text}</li>)}
        {more > 0 && <li className="text-gray-500">…and {more.toLocaleString()} more</li>}
      </ul>
    </details>
  );
}

function TrainCard() {
  const navigate = useNavigate();
  const [status, setStatus] = useState(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);

  useEffect(() => {
    let live = true;
    getTrainingStatus().then((s) => { if (live) setStatus(s); }).catch((e) => { if (live) setError(e.message); });
    return () => { live = false; };
  }, []);

  const train = async () => {
    setBusy(true); setError(null);
    try {
      const job = await trainFromData();
      navigate(`/model-studio?job=${encodeURIComponent(job.job_id)}`);
    } catch (e) { setError(e.message); setBusy(false); }
  };

  return (
    <div className={panel}>
      <h3 className="flex items-center gap-2 font-semibold text-gray-800"><FlaskConical size={18} /> Your own model</h3>
      {!status && !error && <p className="mt-2 text-sm text-gray-500"><Loader2 size={14} className="inline animate-spin" /> Counting…</p>}
      {status && (
        <>
          <p className="mt-1 text-sm text-gray-700">
            {status.discharges.toLocaleString()} discharges stored for this hospital;{' '}
            {status.outcome_known.toLocaleString()} with a known 30-day outcome, {status.readmitted.toLocaleString()} of
            them readmitted.
          </p>
          {status.ready ? (
            <p className="mt-1 text-sm text-gray-600">
              Enough to train a model on your own patients. Model Studio trains it, shows how it does against the
              model in use, and lets you choose which one scores your patients.
            </p>
          ) : (
            <p className="mt-1 text-sm text-gray-600">
              Training needs at least {status.minimum} discharges with a known outcome, some readmitted and some not.
              Past discharges with an outcome column, or with admission and discharge dates, give it.
            </p>
          )}
          <div className="mt-3 flex flex-wrap gap-2">
            <button type="button" className={button} disabled={!status.ready || busy} onClick={train}>
              {busy ? <Loader2 size={15} className="animate-spin" /> : <FlaskConical size={15} />} Train on the stored history
            </button>
            {/* A model can also be trained on a file - the hospital's own export - in Model Studio; current
                patients uploaded here are then scored on their rows exactly as sent. */}
            <button type="button" className={ghost} onClick={() => navigate('/model-studio')}>
              <FlaskConical size={15} /> Train on a file in Model Studio
            </button>
          </div>
        </>
      )}
      {error && <div className="mt-3"><Notice tone="red">{error}</Notice></div>}
    </div>
  );
}

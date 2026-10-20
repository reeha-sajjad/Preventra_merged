import React, { useEffect, useRef, useState } from 'react';
import { Upload, FileText, X, Loader2, Hospital, CalendarClock, Sparkles } from 'lucide-react';
import { createDraft, deleteDraft, getDraft, confirmDraft } from '../../api';
import { panel, button, ghost } from './dataFormat';
import { Notice } from './dataParts';
import UploadReview from './UploadReview';
import UploadDone from './UploadDone';

const MAX_MB = 100;

const KINDS = [
  { key: 'discharges', icon: <Hospital size={22} />, title: 'Discharges',
    text: 'Patients leaving hospital: the stay, diagnoses, labs, medications. One flat export or several files. '
      + 'Scored when the patient goes home.' },
  { key: 'weekly', icon: <CalendarClock size={22} />, title: 'Weekly follow-up',
    text: 'Check-ins after discharge: weight, blood pressure, symptoms, medicines taken. Updates each '
      + "patient's risk week by week. Their discharges need to be here first." },
];

/**
 * Upload the hospital's own CSV exports, as they come out of its systems. Say
 * what they are about, drop them in, and the files are read for you - which
 * file is which, which column is which, which says who was readmitted - by
 * rules and, when one is allowed patient data, an AI model
 * (api/data_reader.py). Nothing is stored until a person confirms the reading;
 * confirming also saves it, so the next export of the same shape reads the
 * same way however it arrives (api/data_upload.py).
 */
export default function UploadPanel({ prefix, onHeaders, onUploaded }) {
  const [kind, setKind] = useState(null);
  const [files, setFiles] = useState([]);
  const [reading, setReading] = useState(false);
  const [report, setReport] = useState(null);   // how the files were read
  const [draft, setDraft] = useState(null);     // the import, once confirmed
  const [error, setError] = useState(null);
  const inputRef = useRef(null);

  const add = (list) => {
    setError(null);
    const csv = [...list].filter((f) => /\.csv$/i.test(f.name));
    const big = csv.find((f) => f.size > MAX_MB * 1024 * 1024);
    if (csv.length < list.length) setError('Only CSV files can be read.');
    if (big) setError(`${big.name} is larger than ${MAX_MB} MB.`);
    setFiles((all) => [...all, ...csv.filter((f) => f !== big && !all.some((a) => a.name === f.name))]);
  };

  const read = async () => {
    setReading(true); setError(null);
    try {
      const r = await createDraft(kind, files);
      setReport(r);
      r.proposal.files.filter((f) => f.table).forEach((f) => onHeaders?.(f.table, Object.keys(f.columns), f.columns));
    } catch (e) { setError(e.message); }
    finally { setReading(false); }
  };

  const reset = () => {
    if (report && !draft) deleteDraft(report.draft_id).catch(() => {});
    setFiles([]); setReport(null); setDraft(null); setError(null);
  };

  const confirm = async (body) => {
    const started = await confirmDraft(report.draft_id, body);
    setDraft(started);
  };

  // An import in progress: follow it until it is done.
  const draftId = draft?.draft_id;
  const importing = draft?.status === 'importing';
  useEffect(() => {
    if (!importing) return undefined;
    let live = true;
    const timer = setInterval(() => {
      getDraft(draftId).then((d) => {
        if (!live) return;
        setDraft(d);
        if (d.status === 'done') onUploaded?.();
      }).catch((e) => { if (live) setDraft((cur) => ({ ...cur, status: 'failed', error: e.message })); });
    }, 2000);
    return () => { live = false; clearInterval(timer); };
  }, [draftId, importing, onUploaded]);

  if (draft && draft.status !== 'review') {
    return <UploadDone draft={draft} onAgain={reset} />;
  }
  if (report) {
    return <UploadReview report={report} prefix={prefix} onConfirm={confirm} onCancel={reset} />;
  }

  return (
    <div className="space-y-5">
      <div className={panel}>
        <h2 className="text-base font-semibold text-gray-800">1. What are these files about?</h2>
        <div className="mt-3 grid gap-3 sm:grid-cols-2">
          {KINDS.map((k) => (
            <button key={k.key} type="button" onClick={() => setKind(k.key)}
              className={`flex items-start gap-3 rounded-lg border-2 p-4 text-left transition-colors ${kind === k.key
                ? 'border-ns-navy bg-ns-navy/5' : 'border-gray-200 hover:border-gray-300'}`}>
              <span className={kind === k.key ? 'text-ns-navy' : 'text-gray-400'}>{k.icon}</span>
              <span>
                <span className="block font-semibold text-gray-800">{k.title}</span>
                <span className="mt-0.5 block text-sm text-gray-600">{k.text}</span>
              </span>
            </button>
          ))}
        </div>
        <p className="mt-3 text-xs text-gray-500">
          Only have data from the time of discharge? That is all Preventra needs; weekly follow-up is optional.
        </p>
      </div>

      {kind && (
        <div className={panel}>
          <h2 className="text-base font-semibold text-gray-800">2. Add your files</h2>
          <p className="mt-1 text-sm text-gray-600">
            CSV exports in your own column names: one file, or several that belong together (discharges with
            their diagnoses, labs or medications, say). They are read for you; you check the reading before
            anything is stored.
          </p>
          <div
            className="mt-4 flex cursor-pointer flex-col items-center justify-center rounded-lg border-2 border-dashed border-gray-300 px-4 py-8 text-center text-sm text-gray-500 hover:border-ns-navy hover:text-ns-navy"
            onClick={() => inputRef.current?.click()}
            onDragOver={(e) => e.preventDefault()}
            onDrop={(e) => { e.preventDefault(); add(e.dataTransfer.files); }}>
            <Upload size={24} />
            <span className="mt-2 font-medium">Drop CSV files here, or click to choose</span>
            <span className="text-xs">Up to {MAX_MB} MB each</span>
            <input ref={inputRef} type="file" accept=".csv,text/csv" multiple className="hidden"
                   onChange={(e) => { add(e.target.files); e.target.value = ''; }} />
          </div>

          {files.length > 0 && (
            <ul className="mt-4 divide-y divide-gray-100 rounded-lg border border-gray-200">
              {files.map((f) => (
                <li key={f.name} className="flex items-center gap-3 px-3 py-2 text-sm">
                  <FileText size={16} className="text-gray-400" />
                  <span className="font-medium text-gray-800">{f.name}</span>
                  <span className="text-xs text-gray-500">{(f.size / 1024 / 1024).toFixed(1)} MB</span>
                  <button type="button" className="ml-auto text-gray-400 hover:text-gray-700" title="Remove"
                          disabled={reading} onClick={() => setFiles((all) => all.filter((x) => x !== f))}>
                    <X size={16} />
                  </button>
                </li>
              ))}
            </ul>
          )}

          {error && <div className="mt-3"><Notice tone="red">{error}</Notice></div>}

          <div className="mt-4 flex flex-wrap items-center gap-3">
            <button type="button" className={button} disabled={!files.length || reading} onClick={read}>
              {reading ? <Loader2 size={15} className="animate-spin" /> : <Sparkles size={15} />}
              {reading ? 'Reading…' : `Read ${files.length > 1 ? `${files.length} files` : 'the file'}`}
            </button>
            {files.length > 0 && !reading && (
              <button type="button" className={ghost} onClick={() => setFiles([])}>Clear</button>
            )}
            {reading && (
              <span className="text-xs text-gray-500">Working out which file and column is which; large files take a little while.</span>
            )}
          </div>
        </div>
      )}
    </div>
  );
}

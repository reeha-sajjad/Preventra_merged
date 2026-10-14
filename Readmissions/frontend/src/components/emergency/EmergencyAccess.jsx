import React, { useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { ShieldAlert, X, Loader2, Search, ArrowRight, Clock } from 'lucide-react';
import { emergencyGrant, emergencyLookup, getPatientEmergency } from '../../api';

/**
 * Emergency access ("break the glass") - api/emergency.py. The same flow as
 * GLP-1's (GLP1/Frontend/src/components/emergency/EmergencyAccess.jsx).
 *
 * A doctor or nurse opens a patient of their hospital who is not on their care
 * team: type the exact patient number, give a reason and a note, and the
 * record opens for 24 hours. Logged, and reviewed by the hospital admin. The
 * lookup says nothing about the patient until a reason is given.
 */
export function EmergencyAccessButton({ className = '' }) {
  const [open, setOpen] = useState(false);
  return (
    <>
      <button type="button" onClick={() => setOpen(true)}
        className={`inline-flex items-center gap-1.5 rounded-md border border-red-200 bg-red-50 px-3 py-2 text-sm font-semibold text-red-700 hover:bg-red-100 ${className}`}>
        <ShieldAlert size={15} /> Emergency access
      </button>
      {open && <EmergencyAccessDialog onClose={() => setOpen(false)} />}
    </>
  );
}

export function EmergencyAccessDialog({ onClose }) {
  const navigate = useNavigate();
  const [number, setNumber] = useState('');
  const [found, setFound] = useState(null);
  const [reason, setReason] = useState(null);
  const [note, setNote] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);

  useEffect(() => {
    const onKey = (e) => e.key === 'Escape' && onClose();
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [onClose]);

  const go = (id) => { onClose(); navigate(`/patients/${encodeURIComponent(id)}`); };

  const lookup = async (e) => {
    e.preventDefault();
    if (!number.trim()) return;
    setBusy(true); setError(null); setFound(null);
    try { setFound(await emergencyLookup(number.trim())); }
    catch (err) { setError(err.message); }
    finally { setBusy(false); }
  };

  const grant = async (e) => {
    e.preventDefault();
    if (!reason) { setError('Choose a reason.'); return; }
    if (note.trim().length < 10) { setError('Say briefly why (at least 10 characters). It is reviewed.'); return; }
    setBusy(true); setError(null);
    try {
      await emergencyGrant({ patient_id: found.patient_id, reason, note: note.trim() });
      go(found.patient_id);
    } catch (err) { setError(err.message); setBusy(false); }
  };

  return (
    <div className="fixed inset-0 z-[60] flex items-center justify-center bg-slate-900/45 p-4" onClick={onClose}>
      <div onClick={(e) => e.stopPropagation()} role="dialog" aria-modal="true" aria-label="Emergency access"
           className="w-full max-w-md rounded-xl bg-white shadow-2xl">
        <div className="flex items-start justify-between border-b border-gray-100 px-5 py-4">
          <div className="flex items-start gap-3">
            <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-red-100 text-red-700">
              <ShieldAlert size={17} />
            </div>
            <div>
              <h2 className="text-base font-semibold text-gray-800">Emergency access</h2>
              <p className="mt-0.5 text-xs text-gray-500">Open a patient of your hospital who is not on your list, for 24 hours.</p>
            </div>
          </div>
          <button type="button" onClick={onClose} aria-label="Close" className="text-gray-400 hover:text-gray-600"><X size={18} /></button>
        </div>

        <div className="space-y-4 px-5 py-4">
          <form onSubmit={lookup}>
            <label htmlFor="ea-number" className="text-xs font-semibold uppercase tracking-wide text-gray-500">Patient number</label>
            <div className="mt-1.5 flex gap-2">
              <input id="ea-number" value={number} autoFocus
                onChange={(e) => { setNumber(e.target.value); setFound(null); setError(null); }}
                placeholder="Exactly as on the chart, e.g. MIMIC-1042"
                className="min-w-0 flex-1 rounded-md border border-gray-300 px-3 py-2 font-mono text-sm focus:border-ns-navy focus:outline-none focus:ring-1 focus:ring-ns-navy" />
              <button type="submit" disabled={busy || !number.trim()}
                className="inline-flex items-center gap-1.5 rounded-md border border-gray-300 px-3 py-2 text-sm font-semibold text-gray-700 hover:bg-gray-50 disabled:opacity-40">
                {busy && !found ? <Loader2 size={14} className="animate-spin" /> : <Search size={14} />} Find
              </button>
            </div>
            <p className="mt-1.5 text-[11px] text-gray-500">The full number only. Nothing about the patient is shown until you give a reason.</p>
          </form>

          {found?.status === 'on_your_list' && (
            <div className="flex items-center justify-between gap-3 rounded-md bg-green-50 px-3 py-3 text-sm text-green-800">
              {found.patient_id} is already on your list.
              <button type="button" onClick={() => go(found.patient_id)} className="inline-flex items-center gap-1 whitespace-nowrap font-semibold">
                Open <ArrowRight size={14} />
              </button>
            </div>
          )}
          {found?.status === 'granted' && (
            <div className="flex items-center justify-between gap-3 rounded-md bg-orange-50 px-3 py-3 text-sm text-orange-800">
              You already have emergency access to {found.patient_id}.
              <button type="button" onClick={() => go(found.patient_id)} className="inline-flex items-center gap-1 whitespace-nowrap font-semibold">
                Open <ArrowRight size={14} />
              </button>
            </div>
          )}

          {found?.status === 'available' && (
            <form onSubmit={grant} className="space-y-3">
              <p className="text-sm text-gray-700">
                <b className="font-mono">{found.patient_id}</b> is in your hospital but not on your care team.
              </p>
              <fieldset className="space-y-1.5">
                <legend className="mb-1.5 text-xs font-semibold uppercase tracking-wide text-gray-500">Reason</legend>
                {found.reasons.map((r) => (
                  <label key={r.key}
                    className={`flex cursor-pointer items-center gap-2.5 rounded-md border px-3 py-2 text-sm ${
                      reason === r.key ? 'border-red-600 bg-red-50' : 'border-gray-200'}`}>
                    <input type="radio" name="ea-reason" checked={reason === r.key} onChange={() => setReason(r.key)}
                           className="accent-red-700" />
                    {r.label}
                  </label>
                ))}
              </fieldset>
              <label className="block">
                <span className="text-xs font-semibold uppercase tracking-wide text-gray-500">Why (required)</span>
                <textarea value={note} onChange={(e) => setNote(e.target.value)} rows={2} maxLength={500}
                  placeholder="e.g. Arrived in the ED unconscious, regular doctor off site"
                  className="mt-1.5 w-full rounded-md border border-gray-300 px-3 py-2 text-sm focus:border-red-400 focus:outline-none focus:ring-1 focus:ring-red-300" />
              </label>
              <div className="flex gap-2 rounded-md bg-red-50 px-3 py-2.5 text-xs text-red-800">
                <Clock size={14} className="mt-0.5 shrink-0" />
                <span>
                  Access lasts {found.hours} hours, then closes on its own. It is recorded in the access log with your
                  name and reason, and your hospital admin reviews every emergency access.
                </span>
              </div>
              <button type="submit" disabled={busy}
                className="inline-flex w-full items-center justify-center gap-2 rounded-md bg-red-700 px-4 py-2.5 text-sm font-semibold text-white hover:bg-red-800 disabled:opacity-50">
                {busy && <Loader2 size={15} className="animate-spin" />} I understand, open the record
              </button>
            </form>
          )}

          {error && <p className="rounded-md border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700">{error}</p>}
        </div>
      </div>
    </div>
  );
}

/** On the patient's page, when emergency access is how the caller got there. */
export function EmergencyBanner({ patientId }) {
  const [hours, setHours] = useState(null);         // hours left, or null: no banner
  useEffect(() => {
    let live = true;
    getPatientEmergency(patientId).then((d) => {
      if (live) setHours(d.grant ? Math.max(0, Math.round((new Date(d.grant.expires_at) - Date.now()) / 3600000)) : null);
    }).catch(() => {});
    return () => { live = false; };
  }, [patientId]);
  if (hours === null) return null;
  return (
    <div className="flex flex-wrap items-center gap-x-3 gap-y-1 rounded-xl border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-800">
      <ShieldAlert size={16} className="shrink-0" />
      <span className="font-semibold">Emergency access</span>
      <span>
        You are not on this patient&rsquo;s care team. Access closes in about {hours} hour{hours === 1 ? '' : 's'}.
        Recorded and reviewed by your hospital admin.
      </span>
    </div>
  );
}
import { useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { ShieldAlert, X, Loader2, Search, ArrowRight, Clock } from 'lucide-react';
import { api } from '../../data/api';

/**
 * Emergency access ("break the glass") - Backend/core/emergency.py.
 *
 * A doctor or hospital nurse opens a patient of their hospital who is not on
 * their care team: type the exact patient number, give a reason and a note,
 * and the record opens for 24 hours. Logged, and reviewed by the hospital
 * admin. The lookup says nothing about the patient until a reason is given.
 */
export function EmergencyAccessButton({ className = '' }) {
  const [open, setOpen] = useState(false);
  return (
    <>
      <button type="button" onClick={() => setOpen(true)}
        className={`inline-flex items-center gap-1.5 text-xs font-semibold px-3 py-2 rounded-lg border transition-colors ${className}`}
        style={{ borderColor: '#FECACA', color: '#B91C1C', background: '#FFF5F5' }}>
        <ShieldAlert size={14} /> Emergency access
      </button>
      {open && <EmergencyAccessDialog onClose={() => setOpen(false)} />}
    </>
  );
}

export function EmergencyAccessDialog({ onClose }) {
  const navigate = useNavigate();
  const [number, setNumber] = useState('');
  const [found, setFound] = useState(null);       // lookup result
  const [reason, setReason] = useState(null);
  const [note, setNote] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);

  useEffect(() => {
    const onKey = (e) => e.key === 'Escape' && onClose();
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [onClose]);

  const go = (idx) => { onClose(); navigate(`/patients/${idx}`); };

  const lookup = async (e) => {
    e.preventDefault();
    if (!/^\d+$/.test(number.trim())) { setError('Type the patient number exactly, digits only.'); return; }
    setBusy(true); setError(null); setFound(null);
    try { setFound(await api.emergencyLookup(number.trim())); }
    catch (err) { setError(err.message); }
    finally { setBusy(false); }
  };

  const grant = async (e) => {
    e.preventDefault();
    if (!reason) { setError('Choose a reason.'); return; }
    if (note.trim().length < 10) { setError('Say briefly why (at least 10 characters). It is reviewed.'); return; }
    setBusy(true); setError(null);
    try {
      await api.emergencyGrant({ patient_idx: found.patient_idx, reason, note: note.trim() });
      go(found.patient_idx);
    } catch (err) { setError(err.message); setBusy(false); }
  };

  return (
    <div className="fixed inset-0 z-[60] flex items-center justify-center p-4" style={{ background: 'rgba(15,23,42,0.45)' }}
         onClick={onClose}>
      <div onClick={(e) => e.stopPropagation()} role="dialog" aria-modal="true" aria-label="Emergency access"
           className="card w-full max-w-md shadow-2xl" style={{ padding: 0 }}>
        <div className="flex items-start justify-between px-5 py-4 border-b border-gray-100">
          <div className="flex items-start gap-3">
            <div className="w-9 h-9 rounded-xl flex items-center justify-center flex-shrink-0" style={{ background: '#FEE2E2' }}>
              <ShieldAlert size={17} style={{ color: '#B91C1C' }} />
            </div>
            <div>
              <h2 className="text-base font-semibold text-gray-800">Emergency access</h2>
              <p className="text-xs text-gray-500 mt-0.5">
                Open a patient of your hospital who is not on your list, for 24 hours.
              </p>
            </div>
          </div>
          <button type="button" onClick={onClose} aria-label="Close" className="text-gray-400 hover:text-gray-600"><X size={18} /></button>
        </div>

        <div className="px-5 py-4 space-y-4">
          <form onSubmit={lookup}>
            <label htmlFor="ea-number" className="text-xs font-semibold uppercase tracking-wider text-gray-400">Patient number</label>
            <div className="mt-1.5 flex gap-2">
              <input id="ea-number" value={number} inputMode="numeric" autoFocus
                onChange={(e) => { setNumber(e.target.value); setFound(null); setError(null); }}
                placeholder="Exactly as on the chart, e.g. 1042"
                className="flex-1 min-w-0 text-sm rounded-lg border border-gray-200 px-3 py-2 font-mono focus:outline-none focus:ring-1 focus:ring-blue-400" />
              <button type="submit" disabled={busy || !number.trim()}
                className="inline-flex items-center gap-1.5 text-sm font-semibold px-3 py-2 rounded-lg border border-gray-200 text-gray-700 hover:bg-gray-50 disabled:opacity-40">
                {busy && !found ? <Loader2 size={14} className="animate-spin" /> : <Search size={14} />} Find
              </button>
            </div>
            <p className="text-[11px] text-gray-400 mt-1.5">The full number only. Nothing about the patient is shown until you give a reason.</p>
          </form>

          {found?.status === 'on_your_list' && (
            <div className="rounded-lg px-3 py-3 text-sm flex items-center justify-between gap-3" style={{ background: '#E8F5E9', color: '#2E7D32' }}>
              Patient #{found.patient_idx} is already on your list.
              <button type="button" onClick={() => go(found.patient_idx)} className="inline-flex items-center gap-1 font-semibold whitespace-nowrap">
                Open <ArrowRight size={14} />
              </button>
            </div>
          )}
          {found?.status === 'granted' && (
            <div className="rounded-lg px-3 py-3 text-sm flex items-center justify-between gap-3" style={{ background: '#FFF7ED', color: '#9A3412' }}>
              You already have emergency access to #{found.patient_idx}.
              <button type="button" onClick={() => go(found.patient_idx)} className="inline-flex items-center gap-1 font-semibold whitespace-nowrap">
                Open <ArrowRight size={14} />
              </button>
            </div>
          )}

          {found?.status === 'available' && (
            <form onSubmit={grant} className="space-y-3">
              <p className="text-sm text-gray-700">
                Patient <b className="font-mono">#{found.patient_idx}</b> is in your hospital but not on your care team.
              </p>
              <fieldset className="space-y-1.5">
                <legend className="text-xs font-semibold uppercase tracking-wider text-gray-400 mb-1.5">Reason</legend>
                {found.reasons.map((r) => (
                  <label key={r.key} className="flex items-center gap-2.5 rounded-lg border px-3 py-2 cursor-pointer text-sm"
                    style={reason === r.key ? { borderColor: '#B91C1C', background: '#FFF5F5' } : { borderColor: '#E2E8F0' }}>
                    <input type="radio" name="ea-reason" checked={reason === r.key} onChange={() => setReason(r.key)}
                           className="accent-red-700" />
                    {r.label}
                  </label>
                ))}
              </fieldset>
              <label className="block">
                <span className="text-xs font-semibold uppercase tracking-wider text-gray-400">Why (required)</span>
                <textarea value={note} onChange={(e) => setNote(e.target.value)} rows={2} maxLength={500}
                  placeholder="e.g. Arrived in the ED unconscious, regular doctor off site"
                  className="mt-1.5 w-full text-sm rounded-lg border border-gray-200 px-3 py-2 focus:outline-none focus:ring-1 focus:ring-red-300" />
              </label>
              <div className="rounded-lg px-3 py-2.5 text-xs flex gap-2" style={{ background: '#FEF2F2', color: '#991B1B' }}>
                <Clock size={14} className="flex-shrink-0 mt-0.5" />
                <span>
                  Access lasts {found.hours} hours, then closes on its own. It is recorded in the access log with your
                  name and reason, and your hospital admin reviews every emergency access.
                </span>
              </div>
              <button type="submit" disabled={busy}
                className="w-full inline-flex items-center justify-center gap-2 text-sm font-semibold px-4 py-2.5 rounded-lg text-white disabled:opacity-50"
                style={{ background: '#B91C1C' }}>
                {busy && <Loader2 size={15} className="animate-spin" />} I understand, open the record
              </button>
            </form>
          )}

          {error && <p className="rounded-md px-3 py-2 text-sm" style={{ background: '#FFEBEE', color: '#C62828' }}>{error}</p>}
        </div>
      </div>
    </div>
  );
}

/** On the patient's page, when emergency access is how the caller got there. */
export function EmergencyBanner({ patientIdx }) {
  const [hours, setHours] = useState(null);       // hours left, or null: no banner
  useEffect(() => {
    let live = true;
    api.getPatientEmergency(patientIdx).then((d) => {
      if (live) setHours(d.grant ? Math.max(0, Math.round((new Date(d.grant.expires_at) - Date.now()) / 3600000)) : null);
    }).catch(() => {});
    return () => { live = false; };
  }, [patientIdx]);
  if (hours === null) return null;
  return (
    <div className="flex flex-wrap items-center gap-x-3 gap-y-1 px-4 py-3 rounded-xl text-sm mb-5 animate-fade-up"
         style={{ background: '#FEF2F2', color: '#991B1B', border: '1px solid #FECACA' }}>
      <ShieldAlert size={16} className="flex-shrink-0" />
      <span className="font-semibold">Emergency access</span>
      <span>
        You are not on this patient&rsquo;s care team. Access closes in about {hours} hour{hours === 1 ? '' : 's'}.
        Recorded and reviewed by your hospital admin.
      </span>
    </div>
  );
}
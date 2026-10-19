import React, { useEffect, useState } from 'react';
import { X, Loader2 } from 'lucide-react';
import { getStaff, setCareTeam } from '../../api';

/**
 * Assign a doctor and nurses to one patient, or to several at once.
 *
 * One patient: the doctor and the nurse list are set as shown.
 * Several: the doctor changes only if one is picked, and ticked nurses are
 * added to whoever each patient already has.
 *
 * Only doctors registered for alert routing can be assigned - the Staff page
 * registers the rest. Everyone offered belongs to the patients' hospital; the
 * server checks that again and refuses the whole batch otherwise.
 */
export default function CareTeamDialog({ patientIds, hospitalId, current, onClose, onSaved }) {
  const single = patientIds.length === 1;
  const [staff, setStaff] = useState(null);
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);
  const [doctorId, setDoctorId] = useState(single ? (current?.doctorId ?? '') : '__keep__');
  const [nurseIds, setNurseIds] = useState(single ? (current?.nurseIds ?? []) : []);

  useEffect(() => {
    let live = true;
    getStaff().then((s) => { if (live) setStaff(s); }).catch((e) => { if (live) setError(e.message); });
    return () => { live = false; };
  }, []);

  useEffect(() => {
    const onKey = (e) => e.key === 'Escape' && onClose();
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [onClose]);

  // The superadmin looking at every hospital gets every hospital's staff back;
  // only the patients' own hospital's people can be assigned.
  const sameHospital = (p) => !hospitalId || !p.hospital_id || p.hospital_id === hospitalId;
  const doctors = (staff?.doctors || []).filter((d) => d.doctor_id && d.registered && sameHospital(d));
  const unregistered = (staff?.doctors || []).filter((d) => !d.registered && sameHospital(d)).length;
  const nurses = (staff?.nurses || []).filter(sameHospital);

  const toggleNurse = (id) =>
    setNurseIds((ids) => (ids.includes(id) ? ids.filter((n) => n !== id) : [...ids, id]));

  const save = async (e) => {
    e.preventDefault();
    const body = { patient_ids: patientIds };
    if (single) {
      body.doctor_id = doctorId;
      body.nurse_ids = nurseIds;
    } else {
      if (doctorId !== '__keep__') body.doctor_id = doctorId;
      if (nurseIds.length) body.add_nurse_ids = nurseIds;
    }
    if (!single && body.doctor_id === undefined && !body.add_nurse_ids) {
      setError('Pick a doctor or at least one nurse.');
      return;
    }
    setBusy(true);
    setError(null);
    try {
      await setCareTeam(body);
      onSaved?.();
    } catch (err) {
      setError(err.message);
      setBusy(false);
    }
  };

  return (
    <div className="fixed inset-0 z-[60] flex items-center justify-center bg-gray-900/40 p-4" onClick={onClose}>
      <form onSubmit={save} onClick={(e) => e.stopPropagation()}
        className="w-full max-w-md rounded-xl bg-white shadow-2xl" role="dialog" aria-modal="true"
        aria-label="Assign care team">
        <div className="flex items-center justify-between border-b px-5 py-4">
          <h2 className="text-lg font-semibold text-ns-navy">
            {single ? `Care team for ${patientIds[0]}` : `Care team for ${patientIds.length} patients`}
          </h2>
          <button type="button" onClick={onClose} aria-label="Close"
            className="rounded-full p-1.5 text-gray-400 hover:bg-gray-100 hover:text-gray-600"><X size={18} /></button>
        </div>
        <div className="space-y-5 px-5 py-4">
          {!staff && !error && <div className="h-24 animate-pulse rounded-lg bg-gray-100" />}
          {staff && (<>
            <label className="block">
              <span className="text-sm font-medium text-gray-700">Doctor</span>
              <select value={doctorId} onChange={(e) => setDoctorId(e.target.value)}
                className="mt-1.5 w-full rounded-lg border border-gray-300 bg-white px-3 py-2 text-sm focus:border-ns-navy focus:outline-none focus:ring-2 focus:ring-ns-navy/30">
                {!single && <option value="__keep__">Leave as it is</option>}
                <option value="">No doctor</option>
                {doctors.map((d) => (
                  <option key={d.doctor_id} value={d.doctor_id}>
                    {d.name}{d.specialty ? ` · ${d.specialty}` : ''} ({d.patients} patients)
                  </option>
                ))}
              </select>
              {unregistered > 0 && (
                <span className="mt-1 block text-xs text-gray-500">
                  {unregistered} doctor account{unregistered === 1 ? ' is' : 's are'} not registered for alerts yet
                  and can&apos;t be assigned - register them on the Staff page.
                </span>
              )}
            </label>
            <fieldset>
              <legend className="text-sm font-medium text-gray-700">
                {single ? 'Hospital nurses' : 'Add hospital nurses'}
              </legend>
              {nurses.length === 0 && <p className="mt-1.5 text-sm text-gray-500">No nurse accounts in this hospital yet.</p>}
              <div className="mt-1.5 max-h-48 space-y-1 overflow-y-auto">
                {nurses.map((n) => (
                  <label key={n.id} className="flex cursor-pointer items-center gap-2 rounded-md px-2 py-1.5 text-sm hover:bg-gray-50">
                    <input type="checkbox" checked={nurseIds.includes(n.id)} onChange={() => toggleNurse(n.id)}
                      className="accent-ns-navy" />
                    <span className="flex-1 text-gray-800">{n.name}</span>
                    <span className="text-xs text-gray-400">{n.patients} patients</span>
                  </label>
                ))}
              </div>
            </fieldset>
          </>)}
          {error && <p className="rounded-md bg-red-50 px-3 py-2 text-sm text-red-700">{error}</p>}
        </div>
        <div className="flex justify-end gap-2 border-t px-5 py-3">
          <button type="button" onClick={onClose}
            className="rounded-lg border border-gray-300 px-4 py-2 text-sm font-medium text-gray-700 hover:bg-gray-50">Cancel</button>
          <button type="submit" disabled={!staff || busy}
            className="inline-flex items-center gap-2 rounded-lg bg-ns-navy px-4 py-2 text-sm font-semibold text-white hover:bg-blue-900 disabled:opacity-60">
            {busy && <Loader2 size={16} className="animate-spin" />} Save
          </button>
        </div>
      </form>
    </div>
  );
}
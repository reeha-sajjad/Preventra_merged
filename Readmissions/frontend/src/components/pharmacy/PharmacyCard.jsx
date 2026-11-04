import React, { useCallback, useEffect, useState } from 'react';
import { Store, Pencil, Loader2 } from 'lucide-react';
import { getPatientPharmacy, setPatientPharmacy, getPharmacies } from '../../api';

/**
 * Where the patient fills their prescriptions (api/patient_pharmacy.py). The care
 * team and the pharmacy itself see it; billing staff (hospital admin, case manager,
 * superadmin) set, change or clear it. The backend refuses everyone else, and the
 * card then stays hidden.
 */
export default function PharmacyCard({ patientId }) {
  const [data, setData] = useState(null);
  const [editing, setEditing] = useState(false);
  const [options, setOptions] = useState(null);
  const [choice, setChoice] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);

  const load = useCallback(() => {
    getPatientPharmacy(patientId).then(setData).catch(() => setData(null));
  }, [patientId]);
  useEffect(() => { load(); }, [load]);
  if (!data) return null;

  const start = async () => {
    setError(null); setChoice(data.pharmacy?.id || ''); setEditing(true);
    if (!options) {
      try { setOptions((await getPharmacies()).pharmacies || []); }
      catch (err) { setError(err.message); }
    }
  };

  const save = async (e) => {
    e.preventDefault();
    setBusy(true); setError(null);
    try { setData(await setPatientPharmacy(patientId, choice)); setEditing(false); }
    catch (err) { setError(err.message); }
    finally { setBusy(false); }
  };

  const leaving = data.pharmacy && choice !== data.pharmacy.id;
  return (
    <section className="rounded-xl border border-gray-200 bg-white p-6 shadow-sm">
      <h2 className="mb-3 flex items-center gap-2 text-base font-semibold text-gray-900">
        <Store size={17} className="text-gray-400" /> Pharmacy
      </h2>
      {editing ? (
        <form onSubmit={save} className="space-y-3">
          <label htmlFor={`pharmacy-${patientId}`} className="block text-sm text-gray-700">
            Where they fill prescriptions
            <select id={`pharmacy-${patientId}`} value={choice} onChange={(e) => setChoice(e.target.value)}
              disabled={!options}
              className="mt-1 w-full rounded-lg border border-gray-200 bg-white px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-300">
              <option value="">{options ? 'None / not known' : 'Loading…'}</option>
              {(options || []).map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
            </select>
          </label>
          {leaving && (
            <p className="rounded-lg bg-amber-50 px-3 py-2 text-xs text-amber-800">
              {data.pharmacy.name} will no longer see this patient. The change is recorded.
            </p>
          )}
          {error && <p role="alert" className="text-sm text-red-700">{error}</p>}
          <div className="flex gap-2">
            <button type="submit" disabled={busy || choice === (data.pharmacy?.id || '')}
              className="inline-flex items-center gap-1.5 rounded-lg bg-ns-navy px-3 py-1.5 text-sm font-semibold text-white disabled:opacity-50">
              {busy && <Loader2 size={13} className="animate-spin" />} Save
            </button>
            <button type="button" onClick={() => { setEditing(false); setError(null); }}
              className="rounded-lg border border-gray-200 px-3 py-1.5 text-sm text-gray-600">Cancel</button>
          </div>
        </form>
      ) : (
        <div className="flex flex-wrap items-baseline gap-x-4 gap-y-1">
          <span className={`text-sm ${data.pharmacy ? 'text-gray-800' : 'text-gray-400'}`}>
            {data.pharmacy ? data.pharmacy.name : 'No pharmacy on file.'}
          </span>
          {data.can_set && (
            <button type="button" onClick={start}
              className="inline-flex items-center gap-1 text-xs font-medium text-ns-navy">
              <Pencil size={11} /> {data.pharmacy ? 'Change pharmacy' : 'Set pharmacy'}
            </button>
          )}
          {data.last_change && (
            <p className="w-full text-xs text-gray-400">
              {data.last_change.to ? (data.last_change.from ? `Changed from ${data.last_change.from}` : 'Set')
                : `Removed ${data.last_change.from}`}
              {data.last_change.by ? ` by ${data.last_change.by}` : ''}
              {data.last_change.at ? ` on ${new Date(data.last_change.at).toLocaleDateString()}` : ''}
            </p>
          )}
        </div>
      )}
    </section>
  );
}

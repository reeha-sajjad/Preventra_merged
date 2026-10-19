import React, { useCallback, useEffect, useState } from 'react';
import { CreditCard, Pencil, Loader2 } from 'lucide-react';
import { getInsurance, setMemberId } from '../../api';
import { PatientFlags } from './InsurerFlags';

/**
 * A patient's insurer and member ID (api/member_ids.py) - the ID on their
 * insurance card that the hospital bills with. Only billing staff (hospital
 * admin, case manager, superadmin), the insurer and the patient see it; the
 * backend refuses everyone else, and the card then stays hidden. GLP-1's twin.
 */
export default function InsuranceCard({ patientId, compact = false }) {
  const [data, setData] = useState(null);
  const [editing, setEditing] = useState(false);
  const [value, setValue] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);

  const load = useCallback(() => {
    getInsurance(patientId).then(setData).catch(() => setData(null));
  }, [patientId]);
  useEffect(() => { load(); }, [load]);
  if (!data) return null;

  const save = async (e) => {
    e.preventDefault();
    setBusy(true); setError(null);
    try { setData(await setMemberId(patientId, value)); setEditing(false); }
    catch (err) { setError(err.message); }
    finally { setBusy(false); }
  };

  const body = !data.insurer ? (
    <p className="text-sm text-gray-400">No insurer on file.</p>
  ) : editing ? (
    <form onSubmit={save} className="space-y-2">
      <label className="block text-sm text-gray-700">{data.insurer.name} member ID
        <input value={value} onChange={(e) => setValue(e.target.value)} autoFocus
          placeholder={data.format === 'mbi' ? 'e.g. 1EG4-TE5-MK73' : 'As printed on the card'}
          className="mt-1 w-full rounded-lg border border-gray-200 px-3 py-2 font-mono text-sm uppercase focus:outline-none focus:ring-2 focus:ring-blue-300" />
      </label>
      {data.format === 'mbi' && <p className="text-xs text-gray-500">Medicare number (MBI): 11 characters. Dashes are optional.</p>}
      {error && <p role="alert" className="text-sm text-red-700">{error}</p>}
      <div className="flex gap-2">
        <button type="submit" disabled={busy}
          className="inline-flex items-center gap-1.5 rounded-lg bg-ns-navy px-3 py-1.5 text-sm font-semibold text-white disabled:opacity-50">
          {busy && <Loader2 size={13} className="animate-spin" />} Save
        </button>
        <button type="button" onClick={() => { setEditing(false); setError(null); }}
          className="rounded-lg border border-gray-200 px-3 py-1.5 text-sm text-gray-600">Cancel</button>
      </div>
    </form>
  ) : (
    <div className="flex flex-wrap items-baseline gap-x-4 gap-y-1">
      <span className="text-sm text-gray-800">{data.insurer.name}</span>
      <span className={`font-mono text-sm ${data.member_id ? 'font-semibold text-gray-900' : 'text-gray-400'}`}>
        {data.member_id || 'No member ID yet'}
      </span>
      {data.can_edit && (
        <button type="button" onClick={() => { setValue(data.member_id || ''); setEditing(true); }}
          className="inline-flex items-center gap-1 text-xs font-medium text-ns-navy">
          <Pencil size={11} /> {data.member_id ? 'Change' : 'Add member ID'}
        </button>
      )}
    </div>
  );

  if (compact) return body;
  return (
    <section className="rounded-xl border border-gray-200 bg-white p-6 shadow-sm">
      <h2 className="mb-3 flex items-center gap-2 text-base font-semibold text-gray-900">
        <CreditCard size={17} className="text-gray-400" /> Insurance
      </h2>
      {body}
      {data.insurer && <PatientFlags patientId={patientId} insurerName={data.insurer.name} />}
    </section>
  );
}
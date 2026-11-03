import React, { useEffect, useState } from 'react';
import { getInsurers } from '../../api';

/**
 * Who pays for a patient: an insurer from the list (api/patient_insurer.py)
 * and, if the card is to hand, the member ID on it. Used on Add patient and on
 * the patient page's Insurance card. Billing staff only - the backend refuses
 * everyone else, and the list then stays empty.
 *
 * value     { insurer: id or '', member_id: '' }
 * onChange  called with the new value
 */
export default function InsurerPicker({ value, onChange, idPrefix = 'payer', exclude = null }) {
  const [insurers, setInsurers] = useState(null);
  const [error, setError] = useState(null);
  useEffect(() => {
    getInsurers().then((r) => setInsurers(r.insurers || [])).catch((e) => setError(e.message));
  }, []);

  const set = (key) => (e) => onChange({ ...value, [key]: e.target.value });
  const chosen = (insurers || []).find((i) => i.id === value.insurer);
  const options = (insurers || []).filter((i) => i.id !== exclude);

  if (error) return <p className="text-sm text-red-700">Couldn’t load the insurers: {error}</p>;
  return (
    <div className="grid gap-3 sm:grid-cols-2">
      <label htmlFor={`${idPrefix}-insurer`} className="block text-sm text-gray-700">
        Insurer (who pays)
        <select id={`${idPrefix}-insurer`} value={value.insurer || ''} onChange={set('insurer')}
          disabled={!insurers}
          className="mt-1 w-full rounded-lg border border-gray-200 bg-white px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-300">
          <option value="">{insurers ? 'Not known yet' : 'Loading…'}</option>
          {options.map((i) => <option key={i.id} value={i.id}>{i.name}</option>)}
        </select>
      </label>
      <label htmlFor={`${idPrefix}-member`} className="block text-sm text-gray-700">
        Member ID <span className="text-gray-400">(optional)</span>
        <input id={`${idPrefix}-member`} value={value.member_id || ''} onChange={set('member_id')}
          disabled={!value.insurer}
          placeholder={chosen?.format === 'mbi' ? 'e.g. 1EG4-TE5-MK73' : 'As printed on the card'}
          className="mt-1 w-full rounded-lg border border-gray-200 px-3 py-2 font-mono text-sm uppercase focus:outline-none focus:ring-2 focus:ring-blue-300 disabled:bg-gray-50" />
      </label>
      {insurers && insurers.length === 0 && (
        <p className="text-xs text-gray-500 sm:col-span-2">No insurers yet. The superadmin adds them in User Management.</p>
      )}
    </div>
  );
}

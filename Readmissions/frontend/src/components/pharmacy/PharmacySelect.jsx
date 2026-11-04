import React, { useEffect, useState } from 'react';
import { getPharmacies } from '../../api';

/** A pharmacy dropdown for Add patient (api/patient_pharmacy.py). Billing staff only. */
export default function PharmacySelect({ value, onChange, id = 'add-pharmacy' }) {
  const [options, setOptions] = useState(null);
  const [error, setError] = useState(null);
  useEffect(() => {
    getPharmacies().then((r) => setOptions(r.pharmacies || [])).catch((e) => setError(e.message));
  }, []);
  if (error) return <p className="text-sm text-red-700">Couldn’t load the pharmacies: {error}</p>;
  return (
    <label htmlFor={id} className="block text-sm text-gray-700">
      Pharmacy (where they fill prescriptions)
      <select id={id} value={value || ''} onChange={(e) => onChange(e.target.value)} disabled={!options}
        className="mt-1 w-full rounded-lg border border-gray-200 bg-white px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-300">
        <option value="">{options ? 'Not known yet' : 'Loading…'}</option>
        {(options || []).map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
      </select>
    </label>
  );
}

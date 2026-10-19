import React, { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { Search, Loader2 } from 'lucide-react';
import { findByMemberId } from '../../api';

/** "Find by member ID" (api/member_ids.find) - billing staff and insurers,
 *  among their own patients only. GLP-1's twin. */
export default function MemberLookup() {
  const navigate = useNavigate();
  const [value, setValue] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);

  const go = async (e) => {
    e.preventDefault();
    if (!value.trim()) return;
    setBusy(true); setError(null);
    try {
      const out = await findByMemberId(value);
      navigate(`/patients/${encodeURIComponent(out.matches[0].patient_id)}`);
    } catch (err) { setError(err.message); }
    finally { setBusy(false); }
  };

  return (
    <form onSubmit={go} className="flex flex-wrap items-center gap-2" aria-label="Find a patient by member ID">
      <label className="relative">
        <span className="sr-only">Member ID</span>
        <Search size={14} className="pointer-events-none absolute top-1/2 left-2.5 -translate-y-1/2 text-gray-400" />
        <input value={value} onChange={(e) => setValue(e.target.value)} placeholder="Find by member ID"
          className="w-52 rounded-lg border border-gray-300 py-2 pr-3 pl-8 font-mono text-sm uppercase focus:outline-none focus:ring-2 focus:ring-blue-300" />
      </label>
      <button type="submit" disabled={busy}
        className="inline-flex items-center gap-1 rounded-lg border border-gray-300 bg-white px-3 py-2 text-sm font-medium text-gray-700 hover:bg-gray-50 disabled:opacity-50">
        {busy && <Loader2 size={13} className="animate-spin" />} Find
      </button>
      {error && <span role="alert" className="text-sm text-red-700">{error}</span>}
    </form>
  );
}
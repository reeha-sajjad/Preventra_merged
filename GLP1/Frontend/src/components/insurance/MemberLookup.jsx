import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { Search, Loader2 } from 'lucide-react';
import { api } from '../../data/api';

/**
 * "Find by member ID" (Backend/core/member_ids.find): billing staff and
 * insurers type the ID from an insurance card or a claim and go straight to
 * the patient. Searches only the caller's own patients.
 */
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
      const out = await api.findByMemberId(value);
      navigate(`/patients/${out.matches[0].patient_idx}`);
    } catch (err) { setError(err.message); }
    finally { setBusy(false); }
  };

  return (
    <form onSubmit={go} className="flex flex-wrap items-center gap-2" aria-label="Find a patient by member ID">
      <label className="relative">
        <span className="sr-only">Member ID</span>
        <Search size={13} className="absolute left-2.5 top-1/2 -translate-y-1/2 text-gray-400 pointer-events-none" />
        <input value={value} onChange={(e) => setValue(e.target.value)} placeholder="Find by member ID"
          className="w-48 text-xs font-mono uppercase pl-8 pr-3 py-2 rounded-lg border border-gray-200 focus:outline-none focus:ring-1 focus:ring-blue-400" />
      </label>
      <button type="submit" disabled={busy}
        className="inline-flex items-center gap-1 text-xs font-semibold px-3 py-2 rounded-lg border border-gray-200 text-gray-700 hover:bg-gray-50 disabled:opacity-50">
        {busy && <Loader2 size={12} className="animate-spin" />} Find
      </button>
      {error && <span role="alert" className="text-xs" style={{ color: '#C62828' }}>{error}</span>}
    </form>
  );
}
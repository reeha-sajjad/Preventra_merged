import { useEffect, useState } from 'react';
import { Syringe } from 'lucide-react';
import { api } from '../../data/api';

/**
 * On the staff patient page: the doses a caretaker recorded giving
 * (Backend/core/doses.py) - what was logged, not proof the dose was taken.
 * Shown only when a caretaker has recorded something, so it doesn't sit empty
 * on every patient's page.
 */
export default function DosesCard({ patientIdx }) {
  const [data, setData] = useState(null);
  useEffect(() => { api.getPatientDoses(patientIdx).then(setData).catch(() => setData(null)); }, [patientIdx]);
  if (!data || data.entries.length === 0) return null;

  const when = (iso) => new Date(iso).toLocaleString(undefined, { weekday: 'short', day: 'numeric', month: 'short',
                                                                  hour: 'numeric', minute: '2-digit' });
  return (
    <div className="card p-6 mt-6">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h2 className="text-base font-semibold text-gray-800 flex items-center gap-2" style={{ fontFamily: 'DM Serif Display, serif' }}>
            <Syringe size={16} className="text-gray-400" /> Doses given
          </h2>
          <p className="text-xs text-gray-400 mt-0.5">Logged by the patient’s caretaker, last 8 weeks.</p>
        </div>
        {data.expected_4_weeks && (
          <div className="text-right">
            <div className="text-2xl font-semibold text-gray-800">{data.last_4_weeks} <span className="text-sm font-normal text-gray-400">of {data.expected_4_weeks}</span></div>
            <div className="text-[11px] text-gray-400">expected in the last 4 weeks</div>
          </div>
        )}
      </div>
      <ul className="mt-4 divide-y divide-gray-100">
        {data.entries.slice(0, 10).map((e) => (
          <li key={e.id} className="py-2 flex flex-wrap items-baseline justify-between gap-2 text-sm">
            <span className="text-gray-800">{when(e.given_at)}</span>
            <span className="text-xs text-gray-500">{e.logged_by}{e.note ? ` · ${e.note}` : ''}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}
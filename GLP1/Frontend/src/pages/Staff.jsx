import { useEffect, useMemo, useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { Stethoscope, HeartPulse, ChevronRight, Search, X } from 'lucide-react';
import { api } from '../data/api';
import { useRole } from '../context/RoleContext';
import PageState from '../components/shared/PageState';

/**
 * Who looks after whom: each doctor and nurse with their patients and how many
 * of those are non-adherent. For hospital admins, case managers and the
 * superadmin. Doctors and nurses are accounts, added in User Management; a row
 * clicks through to the patient list filtered to that person's patients.
 * The search box narrows both tables by name, email or hospital.
 */
export default function Staff() {
  const navigate = useNavigate();
  const { isManager } = useRole();
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);
  const [query, setQuery] = useState('');

  useEffect(() => {
    let live = true;
    api.getStaff().then((d) => { if (live) setData(d); }).catch((e) => { if (live) setError(e); });
    return () => { live = false; };
  }, []);

  // Every word typed must appear in the person's name, email or hospital.
  const matches = useMemo(() => {
    const words = query.trim().toLowerCase().split(/\s+/).filter(Boolean);
    return (p) => {
      const text = [p.name, p.email, p.hospital_name, p.hospital_id].filter(Boolean).join(' ').toLowerCase();
      return words.every((w) => text.includes(w));
    };
  }, [query]);

  if (!data) return <PageState error={error} label="the staff list" />;

  const showHospital = !data.hospital;
  const doctors = data.doctors.filter(matches);
  const nurses = data.nurses.filter(matches);
  const total = data.doctors.length + data.nurses.length;
  const table = (title, Icon, people, param) => (
    <div className="card overflow-hidden">
      <div className="flex items-center gap-2 px-5 py-4 border-b border-gray-100">
        <Icon size={16} className="text-gray-400" />
        <h2 className="text-base font-semibold text-gray-800" style={{ fontFamily: 'DM Serif Display, serif' }}>{title}</h2>
      </div>
      <div className="overflow-x-auto">
        <table className="data-table">
          <thead>
            <tr>
              <th>Name</th>
              {showHospital && <th>Hospital</th>}
              <th>Patients</th><th>Non-adherent</th><th>High dropout risk</th><th style={{ width: 40 }} />
            </tr>
          </thead>
          <tbody>
            {people.map((p) => (
              <tr key={p.id} className="cursor-pointer" onClick={() => navigate(`/patients?${param}=${encodeURIComponent(p.id)}`)}>
                <td>
                  <div className="text-sm font-medium text-gray-800">{p.name}</div>
                  <div className="text-[11px] text-gray-400">{p.email}</div>
                </td>
                {showHospital && <td className="text-xs text-gray-500">{p.hospital_name || p.hospital_id || '—'}</td>}
                <td className="font-mono text-sm font-semibold text-gray-800">{p.patients}</td>
                <td className="font-mono text-sm" style={{ color: p.non_adherent ? '#C62828' : '#A0AEC0', fontWeight: p.non_adherent ? 600 : 400 }}>
                  {p.non_adherent}
                </td>
                <td className="font-mono text-sm" style={{ color: p.high_risk ? '#EF6C00' : '#A0AEC0', fontWeight: p.high_risk ? 600 : 400 }}>
                  {p.high_risk}
                </td>
                <td className="text-gray-300"><ChevronRight size={15} /></td>
              </tr>
            ))}
          </tbody>
        </table>
        {!people.length && (
          <div className="p-8 text-center text-sm text-gray-400">{query.trim() ? 'No one matches your search.' : 'None yet.'}</div>
        )}
      </div>
    </div>
  );

  return (
    <div className="max-w-[1100px] mx-auto space-y-5 animate-fade-in">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold text-gray-800" style={{ fontFamily: 'DM Serif Display, serif' }}>Staff</h1>
          <p className="text-xs text-gray-400 mt-0.5">
            {data.hospital ? data.hospital.name : 'All hospitals'} · click someone to see their patients
          </p>
        </div>
        <div className="flex flex-wrap gap-2 text-xs font-semibold">
                  {data.no_doctor > 0 ? (
          <Link to="/patients?unassigned=doctor" className="px-3 py-1.5 rounded-full" style={{ background: '#FFF3E0', color: '#EF6C00' }}>
            {data.no_doctor.toLocaleString()} patients without a doctor
          </Link>
        ) : (
          <span className="px-3 py-1.5 rounded-full" style={{ background: '#E8F5E9', color: '#2E7D32' }}>Every patient has a doctor</span>
        )}
        {data.no_nurse > 0 ? (
          <Link to="/patients?unassigned=nurse" className="px-3 py-1.5 rounded-full" style={{ background: '#FFF3E0', color: '#EF6C00' }}>
            {data.no_nurse.toLocaleString()} patients without a hospital nurse
          </Link>
        ) : (
          <span className="px-3 py-1.5 rounded-full" style={{ background: '#E8F5E9', color: '#2E7D32' }}>Every patient has a hospital nurse</span>
        )}
        </div>
      </div>

      <div className="flex flex-wrap items-center gap-3">
        <label className="relative flex-1 min-w-[240px] max-w-md">
          <Search size={15} className="absolute left-3 top-1/2 -translate-y-1/2 text-gray-400 pointer-events-none" />
          <input type="search" value={query} onChange={(e) => setQuery(e.target.value)}
            placeholder={showHospital ? 'Search by name, email or hospital' : 'Search by name or email'}
            aria-label="Search staff"
            className="w-full text-sm rounded-lg border border-gray-200 bg-white pl-9 pr-9 py-2 focus:outline-none focus:ring-1 focus:ring-blue-400" />
          {query && (
            <button type="button" onClick={() => setQuery('')} aria-label="Clear search"
              className="absolute right-2.5 top-1/2 -translate-y-1/2 text-gray-400 hover:text-gray-600">
              <X size={15} />
            </button>
          )}
        </label>
        {query.trim() && (
          <span className="text-xs text-gray-500">
            {(doctors.length + nurses.length).toLocaleString()} of {total.toLocaleString()} staff
          </span>
        )}
      </div>

      {table('Doctors', Stethoscope, doctors, 'doctor')}
      {table('Hospital nurses', HeartPulse, nurses, 'nurse')}

      <p className="text-xs text-gray-500">
        Assign patients from the <Link to="/patients" className="font-semibold" style={{ color: 'var(--color-primary)' }}>Patients</Link> page:
        tick them and choose <em>Assign care team</em>, or open one patient.
        {isManager && <> New doctors and nurses are added in{' '}
          <Link to="/settings" className="font-semibold" style={{ color: 'var(--color-primary)' }}>Settings</Link>.</>}
      </p>
    </div>
  );
}
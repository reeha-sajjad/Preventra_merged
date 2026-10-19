import React, { useCallback, useEffect, useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { AlertCircle, Stethoscope, HeartPulse, UserPlus, Loader2, ChevronRight } from 'lucide-react';
import { getStaff, registerDoctor } from '../api';
import { can, MANAGER_ROLES, REGISTRY_ROLES } from '../roles';

/**
 * Who looks after whom. For hospital admins, case managers and the superadmin.
 *
 * Doctors and nurses are accounts (added in User Management). A doctor also
 * needs to be in the alert registry, with a specialty, before patients can be
 * assigned to them - the registry is what routes clinical alerts. Rows click
 * through to the patient list filtered to that person's patients.
 */
function RegisterDoctor({ doctor, specialties, onDone }) {
  const [specialty, setSpecialty] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const submit = async (e) => {
    e.preventDefault();
    e.stopPropagation();
    setBusy(true);
    setError(null);
    try {
      await registerDoctor({ name: doctor.name, email: doctor.email, specialty, clinical_groups: [] });
      onDone();
    } catch (err) {
      setError(err.message);
      setBusy(false);
    }
  };
  return (
    <form onSubmit={submit} onClick={(e) => e.stopPropagation()} className="flex flex-wrap items-center gap-2">
      <select value={specialty} onChange={(e) => setSpecialty(e.target.value)} required
        className="rounded-md border border-gray-300 bg-white px-2 py-1 text-xs">
        <option value="">Specialty…</option>
        {specialties.map((s) => <option key={s} value={s}>{s}</option>)}
      </select>
      <button type="submit" disabled={!specialty || busy}
        className="inline-flex items-center gap-1 rounded-md bg-ns-navy px-2.5 py-1 text-xs font-semibold text-white disabled:opacity-50">
        {busy ? <Loader2 size={12} className="animate-spin" /> : <UserPlus size={12} />} Register
      </button>
      {error && <span className="w-full text-xs text-red-700">{error}</span>}
    </form>
  );
}

export default function Staff() {
  const navigate = useNavigate();
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);
  const [reloadKey, setReloadKey] = useState(0);
  const canRegister = can(REGISTRY_ROLES);

  useEffect(() => {
    let live = true;
    getStaff().then((d) => { if (live) setData(d); }).catch((e) => { if (live) setError(e.message); });
    return () => { live = false; };
  }, [reloadKey]);
  const reload = useCallback(() => setReloadKey((k) => k + 1), []);

  if (error) {
    return (
      <div className="p-4 sm:p-6 max-w-6xl mx-auto">
        <div className="bg-red-50 border border-red-200 rounded-xl p-4 flex gap-3 text-red-800">
          <AlertCircle size={20} className="shrink-0" /><span>{error}</span>
        </div>
      </div>
    );
  }
  if (!data) {
    return <div className="p-4 sm:p-6 max-w-6xl mx-auto"><div className="h-64 animate-pulse rounded-xl bg-gray-100" /></div>;
  }

  const showHospital = !data.hospital;           // the superadmin looking at every hospital
  const th = 'px-4 py-3 text-left text-xs font-semibold uppercase tracking-wide text-gray-500';
  const td = 'px-4 py-3 text-sm';
  const row = 'cursor-pointer border-t border-gray-100 hover:bg-blue-50/50';

  return (
    <div className="p-4 sm:p-6 max-w-6xl mx-auto space-y-6">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-2xl font-bold text-ns-navy">Staff</h1>
          <p className="text-sm text-gray-500 mt-1">
            {data.hospital ? data.hospital.name : 'All hospitals'} · click someone to see their patients
          </p>
        </div>
        <div className="flex flex-wrap gap-2 text-sm">
                  {data.no_doctor > 0 ? (
          <Link to="/patients?unassigned=doctor"
            className="rounded-full border border-amber-200 bg-amber-50 px-3 py-1 font-medium text-amber-800 hover:bg-amber-100">
            {data.no_doctor.toLocaleString()} patients without a doctor
          </Link>
        ) : (
          <span className="rounded-full border border-green-200 bg-green-50 px-3 py-1 font-medium text-green-800">
            Every patient has a doctor
          </span>
        )}
        {data.no_nurse > 0 ? (
          <Link to="/patients?unassigned=nurse"
            className="rounded-full border border-amber-200 bg-amber-50 px-3 py-1 font-medium text-amber-800 hover:bg-amber-100">
            {data.no_nurse.toLocaleString()} patients without a nurse
          </Link>
        ) : (
          <span className="rounded-full border border-green-200 bg-green-50 px-3 py-1 font-medium text-green-800">
            Every patient has a nurse
          </span>
        )}
        </div>
      </div>

      <section className="overflow-hidden rounded-xl border border-gray-200 bg-white shadow-sm">
        <h2 className="flex items-center gap-2 border-b px-4 py-3 font-semibold text-ns-navy">
          <Stethoscope size={18} className="text-gray-400" /> Doctors
        </h2>
        <div className="overflow-x-auto">
          <table className="w-full whitespace-nowrap">
            <thead className="bg-gray-50"><tr>
              <th className={th}>Name</th><th className={th}>Specialty</th>
              {showHospital && <th className={th}>Hospital</th>}
              <th className={th}>Patients</th><th className={th}>High risk</th>
              <th className={th}>Open alerts</th><th className={th} />
            </tr></thead>
            <tbody>
              {data.doctors.map((d) => (
                <tr key={d.doctor_id || d.id} className={d.doctor_id ? row : 'border-t border-gray-100'}
                  onClick={() => d.doctor_id && navigate(`/patients?doctor=${encodeURIComponent(d.doctor_id)}`)}>
                  <td className={td}>
                    <div className="font-medium text-gray-800">{d.name}</div>
                    <div className="text-xs text-gray-400">{d.email}{!d.has_account && ' · no login yet'}</div>
                  </td>
                  <td className={td}>
                    {d.registered ? d.specialty : canRegister
                      ? <RegisterDoctor doctor={d} specialties={data.specialties || []} onDone={reload} />
                      : <span className="text-xs text-amber-700">Not registered for alerts - ask your hospital admin</span>}
                  </td>
                  {showHospital && <td className={`${td} text-gray-500`}>{d.hospital_id || '—'}</td>}
                  <td className={`${td} font-semibold`}>{d.patients}</td>
                  <td className={`${td} ${d.high_risk ? 'font-semibold text-risk-high' : 'text-gray-400'}`}>{d.high_risk}</td>
                  <td className={`${td} ${d.open_alerts ? 'font-semibold text-risk-medium' : 'text-gray-400'}`}>{d.open_alerts}</td>
                  <td className={`${td} text-gray-300`}>{d.doctor_id && <ChevronRight size={16} />}</td>
                </tr>
              ))}
              {!data.doctors.length && (
                <tr><td colSpan={7} className="px-4 py-8 text-center text-sm text-gray-500">No doctors yet.</td></tr>
              )}
            </tbody>
          </table>
        </div>
      </section>

      <section className="overflow-hidden rounded-xl border border-gray-200 bg-white shadow-sm">
        <h2 className="flex items-center gap-2 border-b px-4 py-3 font-semibold text-ns-navy">
          <HeartPulse size={18} className="text-gray-400" /> Hospital nurses
        </h2>
        <div className="overflow-x-auto">
          <table className="w-full whitespace-nowrap">
            <thead className="bg-gray-50"><tr>
              <th className={th}>Name</th>
              {showHospital && <th className={th}>Hospital</th>}
              <th className={th}>Patients</th><th className={th}>High risk</th><th className={th} />
            </tr></thead>
            <tbody>
              {data.nurses.map((n) => (
                <tr key={n.id} className={row} onClick={() => navigate(`/patients?nurse=${encodeURIComponent(n.id)}`)}>
                  <td className={td}>
                    <div className="font-medium text-gray-800">{n.name}</div>
                    <div className="text-xs text-gray-400">{n.email}</div>
                  </td>
                  {showHospital && <td className={`${td} text-gray-500`}>{n.hospital_id || '—'}</td>}
                  <td className={`${td} font-semibold`}>{n.patients}</td>
                  <td className={`${td} ${n.high_risk ? 'font-semibold text-risk-high' : 'text-gray-400'}`}>{n.high_risk}</td>
                  <td className={`${td} text-gray-300`}><ChevronRight size={16} /></td>
                </tr>
              ))}
              {!data.nurses.length && (
                <tr><td colSpan={5} className="px-4 py-8 text-center text-sm text-gray-500">No nurses yet.</td></tr>
              )}
            </tbody>
          </table>
        </div>
      </section>

      <p className="text-sm text-gray-500">
        Assign patients from the <Link to="/patients" className="font-medium text-ns-navy hover:underline">Patients</Link> page:
        tick them and choose <em>Assign care team</em>, or open one patient.
        {can(MANAGER_ROLES) && <> New doctors and nurses are added in{' '}
          <Link to="/settings" className="font-medium text-ns-navy hover:underline">User Management</Link>.</>}
      </p>
    </div>
  );
}
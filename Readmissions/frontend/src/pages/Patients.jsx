import React, { useEffect, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import { X } from 'lucide-react';
import FilterBar from '../components/dashboard/FilterBar';
import PatientWorklist from '../components/dashboard/PatientWorklist';
import PatientDetailPanel from '../components/dashboard/PatientDetailPanel';
import RisingRiskPanel from '../components/dashboard/RisingRiskPanel';
import { getStaff } from '../api';
import { can, STAFF_ROLES, WATCH_ROLES, MY_PATIENTS_ROLES } from '../roles';
import { EmergencyAccessButton } from '../components/emergency/EmergencyAccess';

/**
 * Who are my patients? The same page for every role; the backend decides whose
 * patients are on it (api/access.py) and, for hospital admins and insurers,
 * leaves the clinical columns out.
 */
export default function Patients() {
  const [selectedPatientId, setSelectedPatientId] = useState(null);
  const [searchParams, setSearchParams] = useSearchParams();
  const doctor = searchParams.get('doctor');
  const nurse = searchParams.get('nurse');
  const unassigned = searchParams.get('unassigned');
  const [names, setNames] = useState({});
  // Bumped when the care team changes in the side panel, so the row updates.
  const [listKey, setListKey] = useState(0);

  // Name the person a Staff-page drill-down filtered on, rather than show an id.
  useEffect(() => {
    if (!(doctor || nurse) || !can(STAFF_ROLES)) return undefined;
    let live = true;
    getStaff().then((s) => {
      if (!live) return;
      const out = {};
      (s.doctors || []).forEach((d) => { if (d.doctor_id) out[d.doctor_id] = d.name; });
      (s.nurses || []).forEach((n) => { out[n.id] = n.name; });
      setNames(out);
    }).catch(() => {});
    return () => { live = false; };
  }, [doctor, nurse]);

  const careFilter = doctor ? `Doctor: ${names[doctor] || doctor}`
    : nurse ? `Hospital nurse: ${names[nurse] || nurse}`
    : unassigned === 'doctor' ? 'No doctor assigned'
    : unassigned === 'nurse' ? 'No nurse assigned' : null;

  const clearCare = () => {
    const next = new URLSearchParams(searchParams);
    ['doctor', 'nurse', 'unassigned'].forEach((k) => next.delete(k));
    setSearchParams(next);
  };

  return (
    <div className="p-4 sm:p-6 max-w-7xl mx-auto relative">
      {/* A doctor's or nurse's own patients who got worse, above everything
          else on their list. Not on a care-team drill-down, which is someone
          else's list. */}
      {/* A patient not on this list: break the glass (doctors and nurses). */}
      {can(MY_PATIENTS_ROLES) && (
        <div className="mb-4 flex justify-end"><EmergencyAccessButton /></div>
      )}
      {can(WATCH_ROLES) && !careFilter && <RisingRiskPanel />}
      <FilterBar />
      {careFilter && (
        <div className="mb-4 -mt-2 flex items-center gap-2 text-sm">
          <span className="text-gray-500">Showing</span>
          <span className="inline-flex items-center gap-1.5 rounded-full border border-ns-navy/30 bg-ns-navy/5 px-3 py-1 font-medium text-ns-navy">
            {careFilter}
            <button type="button" onClick={clearCare} aria-label="Clear care-team filter"
              className="rounded-full hover:bg-ns-navy/10"><X size={14} /></button>
          </span>
        </div>
      )}
      <PatientWorklist onSelectPatient={setSelectedPatientId} refreshKey={listKey} />
      {selectedPatientId && (
        <PatientDetailPanel patientId={selectedPatientId} onClose={() => setSelectedPatientId(null)}
          onChanged={() => setListKey((k) => k + 1)} />
      )}
    </div>
  );
}
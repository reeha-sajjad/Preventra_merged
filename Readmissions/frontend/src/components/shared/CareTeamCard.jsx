import React, { useState } from 'react';
import { Users, Stethoscope, HeartPulse, Building, Building2, PencilLine, ClipboardList, Mail } from 'lucide-react';
import CareTeamDialog from './CareTeamDialog';

/**
 * Who looks after a patient, and who pays. The overview layer, so it shows for
 * everyone who can see the patient; the change button only for the roles that
 * assign (the server refuses anyone else).
 */
export default function CareTeamCard({ summary, onChanged }) {
  const [editing, setEditing] = useState(false);
  if (!summary) return null;
  // An insurer: the hospital and who to contact there (api/hospital.for_insurer).
  if (summary.viewer === 'insurer') return <HospitalContact summary={summary} />;
  const { doctor, nurses = [], insurer } = summary;

  return (
    <div className="bg-white rounded-xl border border-gray-200 shadow-sm p-5">
      <div className="flex items-center justify-between border-b pb-3 mb-3">
        <h2 className="text-lg font-semibold text-ns-navy flex items-center gap-2">
          <Users size={20} className="text-gray-400" /> Care team
        </h2>
        {summary.can_assign && (
          <button type="button" onClick={() => setEditing(true)}
            className="flex items-center gap-1.5 rounded-lg border border-ns-navy/30 px-3 py-1.5 text-sm font-medium text-ns-navy hover:bg-ns-navy/5">
            <PencilLine size={15} /> Change
          </button>
        )}
      </div>
      <dl className="grid gap-3 text-sm sm:grid-cols-3">
        <div>
          <dt className="flex items-center gap-1.5 text-gray-500"><Stethoscope size={14} /> Doctor</dt>
          <dd className="mt-0.5 font-medium text-gray-800">
            {doctor ? <>{doctor.name}{doctor.specialty && <span className="text-gray-400 font-normal"> · {doctor.specialty}</span>}</>
              : <span className="text-risk-high">Not assigned</span>}
          </dd>
        </div>
        <div>
          <dt className="flex items-center gap-1.5 text-gray-500"><HeartPulse size={14} /> Hospital nurses</dt>
          <dd className="mt-0.5 font-medium text-gray-800">
            {nurses.length ? nurses.map((n) => n.name).join(', ') : <span className="text-risk-high">None assigned</span>}
          </dd>
        </div>
        <div>
          <dt className="flex items-center gap-1.5 text-gray-500"><Building size={14} /> Insurer</dt>
          <dd className="mt-0.5 font-medium text-gray-800">{insurer?.name || <span className="text-gray-400">None on file</span>}</dd>
        </div>
      </dl>
      {editing && (
        <CareTeamDialog
          patientIds={[summary.id]}
          hospitalId={summary.hospital_id}
          current={{ doctorId: doctor?.id || '', nurseIds: nurses.map((n) => n.id) }}
          onClose={() => setEditing(false)}
          onSaved={() => { setEditing(false); onChanged?.(); }}
        />
      )}
    </div>
  );
}

function HospitalContact({ summary }) {
  const people = summary.case_managers || [];
  return (
    <div className="bg-white rounded-xl border border-gray-200 shadow-sm p-5">
      <h2 className="text-lg font-semibold text-ns-navy flex items-center gap-2 border-b pb-3 mb-3">
        <Building2 size={20} className="text-gray-400" /> Hospital contact
      </h2>
      <dl className="grid gap-3 text-sm sm:grid-cols-3">
        <div>
          <dt className="flex items-center gap-1.5 text-gray-500"><Building2 size={14} /> Hospital</dt>
          <dd className="mt-0.5 font-medium text-gray-800">{summary.hospital || <span className="text-gray-400">None on file</span>}</dd>
        </div>
        <div>
          <dt className="flex items-center gap-1.5 text-gray-500"><ClipboardList size={14} /> Coordinator</dt>
          <dd className="mt-0.5 font-medium text-gray-800">{summary.coordinator || <span className="text-gray-400">None assigned yet</span>}</dd>
        </div>
        <div>
          <dt className="flex items-center gap-1.5 text-gray-500"><Mail size={14} /> Case managers</dt>
          <dd className="mt-0.5 space-y-0.5">
            {people.length ? people.map((p) => (
              <div key={p.id}>
                <span className="font-medium text-gray-800">{p.name}</span>{' '}
                <a href={`mailto:${p.email}`} className="text-xs text-ns-navy hover:underline">{p.email}</a>
              </div>
            )) : <span className="text-gray-400">None on file</span>}
          </dd>
        </div>
      </dl>
    </div>
  );
}
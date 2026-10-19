import { useState } from 'react';
import { Users, Stethoscope, HeartPulse, Building, Building2, PencilLine, Store, ClipboardList, Mail } from 'lucide-react';
import CareTeamDialog from './CareTeamDialog';

/**
 * Who looks after a patient, and who pays - the overview layer, shown to
 * everyone who can see the patient. Pharmacy is clinical, so it only appears
 * once the details are open. The change button only for the roles that assign.
 * An insurer sees the hospital and the case manager to contact instead
 * (Backend/core/hospital.for_insurer) - not the doctors or nurses.
 */
export default function CareTeamCard({ team, patientIdx, canAssign, onChanged }) {
  const [editing, setEditing] = useState(false);
  if (!team) return null;
  if (team.viewer === 'insurer') return <HospitalContact team={team} />;
  const { nurses = [], insurer, pharmacy } = team;
  const doctors = team.doctors || (team.doctor ? [team.doctor] : []);
  const item = (Icon, label, value, missing) => (
    <div>
      <div className="flex items-center gap-1.5 text-[11px] text-gray-400 uppercase tracking-wider font-medium">
        <Icon size={13} /> {label}
      </div>
      <div className="text-sm font-semibold mt-0.5" style={{ color: value ? '#2D3748' : missing ? '#C62828' : '#A0AEC0' }}>
        {value || (missing ? `No ${label.toLowerCase().replace(/s$/, '')} assigned` : 'None on file')}
      </div>
    </div>
  );

  return (
    <div className="card p-6">
      <div className="flex items-center justify-between mb-4">
        <h2 className="text-base font-semibold text-gray-800 flex items-center gap-2"
            style={{ fontFamily: 'DM Serif Display, serif' }}>
          <Users size={16} className="text-gray-400" /> Care team
        </h2>
        {canAssign && (
          <button type="button" onClick={() => setEditing(true)}
            className="flex items-center gap-1.5 text-xs font-medium px-3 py-1.5 rounded-lg"
            style={{ background: '#EBF4FF', color: '#1B4F8A' }}>
            <PencilLine size={13} /> Change
          </button>
        )}
      </div>
      <div className="grid gap-4 sm:grid-cols-2">
        {item(Stethoscope, doctors.length > 1 ? 'Doctors' : 'Doctor', doctors.map((d) => d.name).join(', '), true)}
        {item(HeartPulse, nurses.length > 1 ? 'Hospital nurses' : 'Hospital nurse', nurses.map((n) => n.name).join(', '), true)}
        {item(ClipboardList, 'Case manager', team.case_manager?.name, true)}
        {item(Building, 'Insurer', insurer?.name, false)}
        {pharmacy !== undefined && item(Store, 'Pharmacy', pharmacy, false)}
      </div>
      {editing && (
        <CareTeamDialog
          patientIdxs={[patientIdx]}
          hospitalId={team.hospital_id}
          current={{ doctorIds: doctors.map((d) => d.id), nurseIds: nurses.map((n) => n.id),
                     caseManagerId: team.case_manager?.id || '' }}
          onClose={() => setEditing(false)}
          onSaved={() => { setEditing(false); onChanged?.(); }}
        />
      )}
    </div>
  );
}
function HospitalContact({ team }) {
  const cm = team.case_manager;
  return (
    <div className="card p-6">
      <h2 className="text-base font-semibold text-gray-800 flex items-center gap-2 mb-4"
          style={{ fontFamily: 'DM Serif Display, serif' }}>
        <Building2 size={16} className="text-gray-400" /> Hospital contact
      </h2>
      <div className="grid gap-4 sm:grid-cols-2">
        <div>
          <div className="flex items-center gap-1.5 text-[11px] text-gray-400 uppercase tracking-wider font-medium">
            <Building2 size={13} /> Hospital
          </div>
          <div className="text-sm font-semibold mt-0.5 text-gray-800">{team.hospital || 'None on file'}</div>
        </div>
        <div>
          <div className="flex items-center gap-1.5 text-[11px] text-gray-400 uppercase tracking-wider font-medium">
            <ClipboardList size={13} /> Case manager
          </div>
          {cm ? (
            <div className="mt-0.5">
              <div className="text-sm font-semibold text-gray-800">{cm.name}</div>
              {cm.email && (
                <a href={`mailto:${cm.email}`} className="inline-flex items-center gap-1 text-xs mt-0.5"
                   style={{ color: 'var(--color-primary)' }}><Mail size={11} /> {cm.email}</a>
              )}
            </div>
          ) : (
            <div className="text-sm mt-0.5 text-gray-400">None assigned yet</div>
          )}
        </div>
      </div>
    </div>
  );
}
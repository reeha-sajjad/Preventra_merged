import { createContext, useContext } from 'react';
import { useAuth } from './AuthContext';

const RoleContext = createContext(null);

// Display names for the fixed roles in Readmissions/api/auth.py:ROLES.
const ROLE_LABELS = {
  superadmin:     'Superadmin',
  hospital_admin: 'Hospital admin',
  doctor:         'Doctor',
  nurse:          'Nurse',
  case_manager:   'Case manager',
  insurer:        'Insurer',
  patient:        'Patient',
};

// Roles whose main view is cost and ROI rather than individual patients.
const COST_VIEW_ROLES = ['superadmin', 'hospital_admin', 'insurer'];

// The hospital pages, mirroring Backend/core/access.py. Doctors and nurses have
// no Overview or Staff page: their Patients list is their home.
const OVERVIEW_ROLES = ['superadmin', 'hospital_admin', 'case_manager', 'insurer'];
const STAFF_ROLES    = ['superadmin', 'hospital_admin', 'case_manager'];
const ASSIGN_ROLES   = ['superadmin', 'hospital_admin', 'case_manager'];
// Hospital admins and insurers open a patient's clinical details with a reason.
const REASON_ROLES   = ['hospital_admin', 'insurer'];
// The people who look after patients directly.
const CARE_TEAM_ROLES = ['doctor', 'nurse'];
// Who handles follow-up requests (Backend/core/followups.py HANDLE_ROLES).
// Case managers are the ones they are for; admins can step in.
const FOLLOW_UP_ROLES = ['superadmin', 'hospital_admin', 'case_manager'];

// The role is the one an administrator assigned to the account - read from the
// signed-in user, never chosen here. Hiding a panel is a courtesy; the backend
// decides what each role can actually fetch.
export function RoleProvider({ children }) {
  const { user } = useAuth();
  const role = user?.role || 'case_manager';
  return (
    <RoleContext.Provider value={{
      role,
      roleLabel:  ROLE_LABELS[role] || role,
      isCostView: COST_VIEW_ROLES.includes(role),
      // Patients get one page - their own record - and nothing else.
      isPatient:  role === 'patient',
      isSuperadmin: role === 'superadmin',
      hasOverview:  OVERVIEW_ROLES.includes(role),
      hasStaff:     STAFF_ROLES.includes(role),
      canAssign:    ASSIGN_ROLES.includes(role),
      needsReason:  REASON_ROLES.includes(role),
      isManager:    role === 'superadmin' || role === 'hospital_admin',
      // Doctors and hospital nurses: their home page is "My patients".
      isCareTeam:   CARE_TEAM_ROLES.includes(role),
      handlesFollowUps: FOLLOW_UP_ROLES.includes(role),
      isCaseManager: role === 'case_manager',
    }}>
      {children}
    </RoleContext.Provider>
  );
}

export const useRole = () => useContext(RoleContext);
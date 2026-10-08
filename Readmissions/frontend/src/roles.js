// Which pages and controls each role gets. The backend refuses everything
// else regardless (api/access.py); these lists only decide what the menu and
// the page offer, so nobody is shown a control that will be refused.
import { readClaims } from './api/auth';

export const OVERVIEW_ROLES = ['superadmin', 'hospital_admin', 'case_manager', 'insurer'];
export const STAFF_ROLES = ['superadmin', 'hospital_admin', 'case_manager'];
export const ASSIGN_ROLES = ['superadmin', 'hospital_admin', 'case_manager'];
export const MANAGER_ROLES = ['superadmin', 'hospital_admin'];
// Registering a doctor for alert routing (api/access.py ACTIONS manage_doctors).
export const REGISTRY_ROLES = ['superadmin', 'hospital_admin'];
// Hospital admins and insurers open a patient's clinical details with a reason.
export const REASON_ROLES = ['hospital_admin', 'insurer'];
// The clinician console is a doctor's alert inbox; the roles that must give a
// reason per patient cannot read a whole inbox of clinical alerts.
export const CONSOLE_ROLES = ['superadmin', 'doctor', 'case_manager'];
// Who is told when their patients' risk goes up (api/access.py watch_risk).
// A doctor or nurse hears about their own patients only.
export const WATCH_ROLES = ['superadmin', 'doctor', 'nurse', 'case_manager'];
// Model Studio: training, comparing and deploying the hospital's own models
// (api/access.py manage_models).
export const STUDIO_ROLES = ['superadmin', 'hospital_admin'];

export const ROLE_LABELS = {
  superadmin: 'Superadmin', hospital_admin: 'Hospital admin', doctor: 'Doctor', nurse: 'Nurse',
  case_manager: 'Case manager', insurer: 'Insurer', patient: 'Patient',
};

export const myRole = () => readClaims()?.role || 'case_manager';
export const can = (roles) => roles.includes(myRole());

// Which pages and controls each role gets. The backend refuses everything
// else regardless (api/access.py); these lists only decide what the menu and
// the page offer, so nobody is shown a control that will be refused.
import { readClaims } from './api/auth';

export const OVERVIEW_ROLES = ['superadmin', 'hospital_admin', 'case_manager', 'insurer'];
export const STAFF_ROLES = ['superadmin', 'hospital_admin', 'case_manager'];
// Follow-up requests (api/followups.py): doctors and nurses ask; case managers
// arrange them, and hospital admins and the superadmin can step in.
export const FOLLOWUP_REQUEST_ROLES = ['doctor', 'nurse'];
export const FOLLOWUP_HANDLE_ROLES = ['superadmin', 'hospital_admin', 'case_manager'];
export const ASSIGN_ROLES = ['superadmin', 'hospital_admin', 'case_manager'];
// Who finds a patient by insurance member ID (api/member_ids.py).
// An insurer's home is its members page (pages/InsurerHome.jsx); Overview moves to /overview.
export const INSURER_ROLES = ['insurer'];
export const MEMBER_LOOKUP_ROLES = ['superadmin', 'hospital_admin', 'case_manager', 'insurer'];
export const MANAGER_ROLES = ['superadmin', 'hospital_admin'];
// Registering a doctor for alert routing (api/access.py ACTIONS manage_doctors).
export const REGISTRY_ROLES = ['superadmin', 'hospital_admin'];
// Hospital admins and insurers open a patient's clinical details with a reason.
export const REASON_ROLES = ['hospital_admin', 'insurer'];
// The clinician console: a doctor's alert inbox, or for a nurse the alerts on
// their own patients. The roles that must give a reason per patient cannot
// read a whole inbox of clinical alerts.
export const CONSOLE_ROLES = ['superadmin', 'doctor', 'nurse', 'case_manager'];
// Who is told when their patients' risk goes up (api/access.py watch_risk).
// A doctor or nurse hears about their own patients only.
export const WATCH_ROLES = ['superadmin', 'doctor', 'nurse', 'case_manager'];
// Model Studio: training, comparing and deploying the hospital's own models
// (api/access.py manage_models).
export const STUDIO_ROLES = ['superadmin', 'hospital_admin'];
// Cost and ROI figures (api/access.py ACTIONS view_costs). A case manager
// coordinates care and does not manage money: they see the counterfactual and
// the care plan, without the dollar figures.
export const COST_ROLES = ['superadmin', 'hospital_admin', 'doctor', 'nurse', 'insurer', 'patient'];
// The Outcomes page (api/access.py view_outcomes): acting on alerts and
// follow-ups, discharge plans, readmissions observed against expected. No
// money on it, so a case manager sees it too.
export const OUTCOME_ROLES = ['superadmin', 'hospital_admin', 'case_manager'];
// Adding or removing a patient's own checklist items and check-in questions
// (api/access.py ACTIONS customise_plan). Nurses and hospital admins tick but
// do not author.
export const CUSTOMISE_PLAN_ROLES = ['superadmin', 'doctor', 'case_manager'];
// Doctors and nurses: their own patients are their home page (GLP-1's
// CARE_TEAM_ROLES in RoleContext.jsx).
export const MY_PATIENTS_ROLES = ['doctor', 'nurse'];
// A pharmacy's staff: their patient list is their own page (pages/PharmacyHome.jsx);
// the backend shows them the overview only (api/access.py OVERVIEW_ONLY_ROLES).
export const PHARMACY_ROLES = ['pharmacy'];

export const ROLE_LABELS = {
  superadmin: 'Superadmin', hospital_admin: 'Hospital admin', doctor: 'Doctor', nurse: 'Hospital nurse',
  case_manager: 'Case manager', insurer: 'Insurer', pharmacy: 'Pharmacy', caretaker: 'Caretaker',
  patient: 'Patient',
};

export const myRole = () => readClaims()?.role || 'case_manager';
export const can = (roles) => roles.includes(myRole());
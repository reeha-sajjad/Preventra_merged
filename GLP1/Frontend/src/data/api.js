import { endSession } from "../context/AuthContext";

const BASE = import.meta.env.VITE_API_URL ?? "http://localhost:8000";

// Accounts live in one place for both products: the Readmissions API's /auth/*
// endpoints, backed by `shared_identity.users`. This app never mints a token -
// it only receives one (from here, or from the portal on the URL fragment) and
// sends it back on protected calls. Keep this pointed at the auth service, not
// at VITE_API_URL.
export const AUTH_BASE = (import.meta.env.VITE_AUTH_URL ?? "http://localhost:8001").replace(/\/$/, "");

// Read straight from storage rather than from React state: this module is
// imported by hooks that run before any provider mounts, and AuthContext
// persists the token here on every sign-in and on the portal hand-off.
const TOKEN_KEY = "glp1_token";

// The superadmin's hospital picker. The choice rides on every request as a
// header; the backend ignores it for everyone else. Per tab, like a session.
const HOSPITAL_KEY = "glp1_acting_hospital";

export function getActingHospital() {
  try { return sessionStorage.getItem(HOSPITAL_KEY) || ""; } catch { return ""; }
}

export function setActingHospital(hospitalId) {
  try {
    if (hospitalId) sessionStorage.setItem(HOSPITAL_KEY, hospitalId);
    else sessionStorage.removeItem(HOSPITAL_KEY);
  } catch { /* private mode: the picker just will not stick */ }
}

/** Every /api route needs a bearer token signed by the auth service. */
function authHeaders(extra = {}) {
  const token = localStorage.getItem(TOKEN_KEY);
  const hospital = getActingHospital();
  return {
    ...extra,
    ...(token ? { Authorization: `Bearer ${token}` } : {}),
    ...(hospital ? { "X-Hospital-Id": hospital } : {}),
  };
}

/** A refusal, keeping the status and - for "give a reason first" - its code,
 *  so a page can show the prompt rather than an error. */
export class ApiError extends Error {
  constructor(message, status, code) {
    super(message);
    this.status = status;
    this.code = code;
  }
}

/** 401 means the session is over: the token expired or the account was
 *  removed. End it here rather than let the caller fall back to mock data,
 *  which would show a signed-out user a dashboard of made-up numbers. */
async function check(res, path) {
  if (res.status === 401) endSession();
  if (!res.ok) {
    const detail = (await res.json().catch(() => ({})))?.detail;
    const message = typeof detail === "string" ? detail
      : detail?.message || `API ${path} \u2192 ${res.status}`;
    throw new ApiError(message, res.status, detail?.code);
  }
}

async function get(path) {
  const res = await fetch(`${BASE}${path}`, { headers: authHeaders() });
  await check(res, path);
  return res.json();
}

async function post(path, body) {
  const res = await fetch(`${BASE}${path}`, {
    method: "POST",
    headers: authHeaders({ "Content-Type": "application/json" }),
    body: JSON.stringify(body),
  });
  await check(res, path);
  return res.json();
}

async function del(path) {
  const res = await fetch(`${BASE}${path}`, { method: "DELETE", headers: authHeaders() });
  await check(res, path);
  return res.json();
}

/** The auth service answers on its own origin and needs no API key. */
async function authPost(path, body) {
  const res = await fetch(`${AUTH_BASE}${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    // FastAPI puts the reason in `detail`; surfacing it is the difference
    // between "invalid email or password" and a blank "something went wrong".
    throw new Error(typeof data.detail === "string" ? data.detail : `Auth ${path} \u2192 ${res.status}`);
  }
  return normaliseSession(data);
}

/** The auth service speaks {token, user:{sub,...}}; this app speaks
 *  {access_token, user:{id,...}}. Translate once, here. */
function normaliseSession(data) {
  const u = data.user ?? {};
  return {
    access_token: data.token ?? data.access_token,
    user: {
      id:         u.sub ?? u.id,
      email:      u.email,
      role:       u.role,
      org_id:     u.org_id,
      org_name:   u.org_name,
      app_access: u.app_access ?? [],
    },
  };
}

export const api = {
  getSummary:           ()       => get("/api/summary"),
  getGlobalSHAP:        ()       => get("/api/shap/global"),
  getPatients:          (params) => get("/api/patients?" + new URLSearchParams(params)),
  getPatient:           (id)     => get(`/api/patients/${id}`),
  getSegments:          ()       => get("/api/segments"),
  getSegment:           (id)     => get(`/api/segments/${id}`),
  getSurvival:          ()       => get("/api/survival"),
  getCostEffectiveness: ()       => get("/api/cost-effectiveness"),
  getBudgetImpact:      (body)   => post("/api/budget-impact", body),
  getModelInfo:         ()       => get("/api/model/info"),

  // Hospital pages - overview layer only (Backend/core/hospital.py)
  getOverview:          ()       => get("/api/overview"),
  getStaff:             ()       => get("/api/staff"),
  /** { patient_ids, doctor_id?, nurse_ids?, add_nurse_ids? } */
  setCareTeam:          (body)   => post("/api/care-team", body),
  /** A patient's overview layer, and whether their clinical details are open yet. */
  getPatientSummary:    (id)     => get(`/api/patients/${id}/summary`),
  /** Open one patient's clinical details with a reason (hospital admins, insurers). */
  openPatient:          (id, reason) => post(`/api/patients/${id}/open`, { reason }),

  // The bell - doctors and hospital nurses (Backend/core/notifications.py)
  getNotifications:     (limit = 20) => get(`/api/notifications?limit=${limit}`),
  /** { patient_ids } or { all: true } */
  markNotificationsSeen: (body)  => post("/api/notifications/seen", body),

  // Follow-up requests (Backend/core/followups.py)
  // Emergency access ("break the glass") - Backend/core/emergency.py
  emergencyLookup:      (idx)    => post("/api/emergency-access/lookup", { patient_idx: Number(idx) }),
  emergencyGrant:       (body)   => post("/api/emergency-access", body),
  getPatientEmergency:  (id)     => get(`/api/patients/${id}/emergency`),
  // Medication changes - Backend/core/medications.py
  getMedications:       (id)     => get(`/api/patients/${id}/medications`),
  recordMedicationChange: (id, body) => post(`/api/patients/${id}/medications`, body),
  getPatientFollowUp:   (id)     => get(`/api/patients/${id}/followup`),
  /** { urgency: 'urgent' | 'routine', note? } - doctors and hospital nurses */
  requestFollowUp:      (id, body) => post(`/api/patients/${id}/followup`, body),
  /** status: 'active' | 'done' - case managers, hospital admins */
  getFollowUps:         (status = 'active') => get(`/api/followups?status=${status}`),
  markHandedSeen:       () => post('/api/followups/handed-seen', {}),
  addFollowUpNote:      (fid, text) => post(`/api/followups/${fid}/note`, { text }),
  takeFollowUp:         (fid)    => post(`/api/followups/${fid}/take`, {}),
  /** { outcome: 'booked' | 'unreachable' | 'not_needed', note? } */
  getFollowUpHandlers:  ()       => get("/api/followups/handlers"),
  releaseFollowUp:      (fid)    => post(`/api/followups/${fid}/release`, {}),
  handOverFollowUp:     (fid, toUserId) => post(`/api/followups/${fid}/hand-over`, { to_user_id: toUserId }),
  getPharmacyHome:      () => get('/api/pharmacy/home'),
  reportPharmacyProblem: (idx, body) => post(`/api/patients/${idx}/pharmacy-report`, body),
  markPharmacySeen:     () => post('/api/pharmacy/seen', {}),
  // A patient's own home page - Backend/core/patient_home.py
  /** patient: a caretaker picks which of their people (omit for a patient) */
  getMyHome:            (patient) => get(`/api/my-home${patient != null ? `?patient=${patient}` : ''}`),
  /** { reason, note?, patient_idx? } - the patient (or caretaker) asks the case manager */
  askCaseManager:       (body) => post('/api/my-home/ask', body),
  // Caretakers a patient links - Backend/core/caretakers.py
  getCaretakers:        (patient) => get(`/api/caretakers${patient != null ? `?patient=${patient}` : ''}`),
  /** { email, patient_idx? (staff), patient_agreed? (staff) } */
  linkCaretaker:        (body) => post('/api/caretakers', body),
  unlinkCaretaker:      (id, patient) => del(`/api/caretakers/${id}${patient != null ? `?patient=${patient}` : ''}`),
  // Zoe, the patient's reminders - Backend/core/zoe.py
  getZoe:               () => get('/api/zoe'),
  /** { time: 'HH:MM', day?: 0-6 (0 = Monday), form?: 'weekly' | 'daily' } */
  setZoeDose:           (body) => post('/api/zoe/dose', body),
  clearZoeDose:         () => del('/api/zoe/dose'),
  /** { kind, date: 'YYYY-MM-DD', time?, note? } */
  addZoeAppointment:    (body) => post('/api/zoe/appointments', body),
  removeZoeAppointment: (id) => del(`/api/zoe/appointments/${id}`),
  getCaseManagerToday:  (mine = false) => get(`/api/case-manager/today${mine ? "?mine=1" : ""}`),
  finishFollowUp:       (fid, body) => post(`/api/followups/${fid}/done`, body),

  // Consequence Model (Phase 4 "Cost of Inaction" screen)
  getDownstreamCost:    ()       => get("/api/consequence/downstream-cost"),
  getReboundRisk:       ()       => get("/api/consequence/rebound-risk"),
  getPayerScenarios:    ()       => get("/api/consequence/payer-scenarios"),
  getPayerROI:          (interventionCost = 500, payerType = "current", adherenceUplift = 0.15) =>
    get(`/api/consequence/payer-roi?intervention_cost=${interventionCost}&payer_type=${payerType}&adherence_uplift=${adherenceUplift}`),

  // Chatbot
  postChatMessage:  (body)  => post("/api/chatbot/message", body),
  getChatSession:   (id)    => get(`/api/chatbot/session/${id}`),
  clearChatSession: (id)    => del(`/api/chatbot/session/${id}`),
  /** Your own account, for Settings (Backend/core/hospital.me). */
  getMe:                ()       => get("/api/me"),
  /** Replace your own password at the shared sign-in service; returns a fresh token. */
  changePassword: async (token, current_password, new_password) => {
    const res = await fetch(`${AUTH_BASE}/auth/change-password`, {
      method: "POST",
      headers: { "Content-Type": "application/json", Authorization: `Bearer ${token}` },
      body: JSON.stringify({ current_password, new_password }),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(typeof data.detail === "string" ? data.detail : `Could not change the password (${res.status})`);
    return data;
  },
  signup: (body) => authPost("/auth/signup", body),
  login:  (body) => authPost("/auth/login", body),
};
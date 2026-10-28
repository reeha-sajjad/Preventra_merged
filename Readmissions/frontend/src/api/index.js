import * as MockAPI from './mock';
import { getToken, signOut } from './auth';

// ─── Environment switch ────────────────────────────────────────────────────
// Set VITE_USE_MOCK=true in .env to fall back to in-memory mock data.
// Defaults to false so real backend is used when the variable is absent.
const USE_MOCK = import.meta.env.VITE_USE_MOCK === 'true';
const BASE_URL = (import.meta.env.VITE_API_BASE_URL || 'http://localhost:8000').replace(/\/$/, '');
// Also the shared auth service (login and User Management live on this API).
export const API_BASE_URL = BASE_URL;
const API_KEY = import.meta.env.VITE_API_KEY || '';

// Adding a patient by hand is off by default and must match MANUAL_ENTRY_ENABLED
// on the backend, which refuses the create endpoints regardless of what this
// says. Hiding the page is the courtesy; the server is the control.
export const MANUAL_ENTRY_ENABLED =
  String(import.meta.env.VITE_MANUAL_ENTRY_ENABLED ?? 'false').trim().toLowerCase() === 'true';

// ─── Hospital picker ───────────────────────────────────────────────────────
// The superadmin can look at one hospital at a time. The choice rides on every
// request as a header, and the backend ignores it for everyone else. Kept for
// the tab only, like the token.
const HOSPITAL_KEY = 'preventra.actingHospital';

export function getActingHospital() {
  try { return window.sessionStorage.getItem(HOSPITAL_KEY) || ''; } catch { return ''; }
}

export function setActingHospital(hospitalId) {
  try {
    if (hospitalId) window.sessionStorage.setItem(HOSPITAL_KEY, hospitalId);
    else window.sessionStorage.removeItem(HOSPITAL_KEY);
  } catch { /* private mode: the picker just will not stick */ }
}

/** An API refusal, keeping the status and - for the "give a reason" refusal -
 *  its code, so a page can show the prompt instead of an error. */
export class ApiError extends Error {
  constructor(message, status, code) {
    super(message);
    this.status = status;
    this.code = code;
  }
}

export const isReasonRequired = (err) => err?.code === 'reason_required';

// ─── Generic fetch helper ──────────────────────────────────────────────────
async function apiFetch(path, options = {}) {
  const headers = { ...(options.headers || {}) };
  if (API_KEY) headers['X-API-Key'] = API_KEY;

  // Shared-login token, handed over by the portal. Sent alongside the API key
  // rather than instead of it: the key says "our frontend", the token says
  // "this person". The backend refuses every /api call without it.
  const token = getToken();
  if (token) headers['Authorization'] = `Bearer ${token}`;
  const hospital = getActingHospital();
  if (hospital) headers['X-Hospital-Id'] = hospital;

  const res = await fetch(`${BASE_URL}${path}`, { ...options, headers });
  // The session is over - the token expired or the account was removed. Send
  // them to sign in again rather than leave a dashboard of failed requests.
  if (res.status === 401) signOut();
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    const detail = body?.detail;
    // FastAPI's validation errors are a list (of objects, or of our own
    // sentences); the reason prompt's is an object.
    const message = typeof detail === 'string' ? detail
      : detail?.message || (Array.isArray(detail) ? detail.map((d) => (typeof d === 'string' ? d : d.msg)).join('; ') : '')
      || `API error ${res.status}: ${path}`;
    throw new ApiError(message, res.status, detail?.code);
  }
  return res.json();
}

// Anything that can change the follow-up queue says so, and the menu's
// waiting count (Sidebar.jsx) refreshes at once rather than on the next page.
export const FOLLOWUPS_CHANGED = 'preventra:followups-changed';
const followUpsChanged = (result) => {
  try { window.dispatchEvent(new Event(FOLLOWUPS_CHANGED)); } catch { /* no window */ }
  return result;
};

// ─── Real API implementations ──────────────────────────────────────────────
const json = (body) => ({
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify(body),
});

const RealAPI = {
  getSummary: () => apiFetch('/api/summary'),
  /** The signed-in account as the auth service sees it now. */
  getMe: () => apiFetch('/auth/me'),

  /** Your own account for Settings: name, hospital, how many patients you can see. */
  getAccount: () => apiFetch('/api/me'),
  /** Change your own password at the shared auth service. Not through apiFetch:
   *  a wrong current password is a 401, which apiFetch would treat as the
   *  session being over and sign the user out. Returns { token }. */
  changePassword: async (currentPassword, newPassword) => {
    const headers = { 'Content-Type': 'application/json', Authorization: `Bearer ${getToken()}` };
    if (API_KEY) headers['X-API-Key'] = API_KEY;
    const res = await fetch(`${BASE_URL}/auth/change-password`, {
      method: 'POST', headers,
      body: JSON.stringify({ current_password: currentPassword, new_password: newPassword }),
    });
    const body = await res.json().catch(() => ({}));
    if (!res.ok) {
      const d = body?.detail;
      throw new ApiError(typeof d === 'string' ? d : 'The password could not be changed.', res.status);
    }
    return body;
  },

  // ─── Hospital pages ──────────────────────────────────────────────────────
  // Overview layer only - counts, names, assignments. See api/hospital.py.
  getOverview: () => apiFetch('/api/overview'),
  getStaff: () => apiFetch('/api/staff'),
  /** A doctor's or nurse's own patients at a glance - the My patients home page. */
  getMyPatients: () => apiFetch('/api/my-patients'),
  /** { patient_ids, doctor_id?, nurse_ids?, add_nurse_ids? } - see api/hospital.assign. */
  setCareTeam: (body) => apiFetch('/api/care-team', json(body)),
  /** A patient's overview layer, and whether their clinical details are open yet. */
  getPatientSummary: (id) => apiFetch(`/api/patients/${encodeURIComponent(id)}/summary`),
  /** Open one patient's clinical details with a reason (hospital admins, insurers). */
  openPatient: (id, reason) =>
    apiFetch(`/api/patients/${encodeURIComponent(id)}/open`, json({ reason })),

  // ─── Emergency access ("break the glass") - api/emergency.py ─────────────
  emergencyLookup: (patientId) => apiFetch('/api/emergency-access/lookup', json({ patient_id: patientId })),
  emergencyGrant: (body) => apiFetch('/api/emergency-access', json(body)),
  getPatientEmergency: (id) => apiFetch(`/api/patients/${encodeURIComponent(id)}/emergency`),

  // ─── Follow-ups ──────────────────────────────────────────────────────────
  // Doctors and nurses ask, case managers arrange. See api/followups.py.
  // Anything that can change the queue announces it (followUpsChanged), so the
  // menu's waiting count refreshes at once rather than on the next page.
  getFollowUps: (status = 'active') => apiFetch(`/api/followups?status=${status}`),
  getFollowUpsWaiting: () => apiFetch('/api/followups/waiting'),
  // The bell's follow-up news: pharmacy reports on requests a case manager
  // holds, or a pharmacy's reports that are done. Opening Follow-ups (or the
  // pharmacy page) clears them and tells the bell.
  getFollowUpBell: () => apiFetch('/api/followups/bell'),
  markFollowUpReportsSeen: () =>
    apiFetch('/api/followups/reports-seen', { method: 'POST' }).then(followUpsChanged),
  markPharmacySeen: () => apiFetch('/api/pharmacy/seen', { method: 'POST' }).then(followUpsChanged),
  getPatientFollowUp: (id) => apiFetch(`/api/patients/${encodeURIComponent(id)}/followup`),
  requestFollowUp: (id, body) =>
    apiFetch(`/api/patients/${encodeURIComponent(id)}/followup`, json(body)).then(followUpsChanged),
  addFollowUpNote: (fid, text) => apiFetch(`/api/followups/${fid}/note`, json({ text })),
  takeFollowUp: (fid) =>
    apiFetch(`/api/followups/${fid}/take`, { method: 'POST' }).then(followUpsChanged),
  finishFollowUp: (fid, body) =>
    apiFetch(`/api/followups/${fid}/done`, json(body)).then(followUpsChanged),
  // Pharmacies (api/pharmacy.py): their patients, and refill-problem reports.
  getPharmacyHome: () => apiFetch('/api/pharmacy/home'),
  // A patient's own home page (api/patient_home.py), and asking a case manager.
  // A caretaker's people and bell (api/caretaker_home.py).
  getMyPeople: () => apiFetch('/api/my-people'),
  markCaretakerSeen: () => apiFetch('/api/my-people/seen', { method: 'POST' }),
  getMyHome: (patient) => apiFetch(`/api/my-home${patient ? `?patient=${encodeURIComponent(patient)}` : ''}`),
  askCaseManager: (body) => apiFetch('/api/my-home/ask', json(body)).then(followUpsChanged),
  // A caretaker's daily "medicines given" tick (api/medicines_given.py).
  getMedicinesGiven: (patient) => apiFetch(`/api/my-home/medicines${patient ? `?patient=${encodeURIComponent(patient)}` : ''}`),
  recordMedicinesGiven: (patient) => apiFetch(`/api/my-home/medicines${patient ? `?patient=${encodeURIComponent(patient)}` : ''}`, json({})),
  undoMedicinesGiven: (id, patient) => apiFetch(`/api/my-home/medicines/${id}${patient ? `?patient=${encodeURIComponent(patient)}` : ''}`,
    { method: 'DELETE' }),
  getPatientMedicinesGiven: (id) => apiFetch(`/api/patients/${encodeURIComponent(id)}/medicines-given`),
  // The weekly check-in a patient or caretaker sends (api/checkins.py).
  getCheckIn: (patient) => apiFetch(`/api/my-home/checkin${patient ? `?patient=${encodeURIComponent(patient)}` : ''}`),
  sendCheckIn: (answers, patient, customAnswers = {}, remarks = null) =>
    apiFetch('/api/my-home/checkin', json({ answers, patient_id: patient || null, custom_answers: customAnswers, remarks })),
  // The discharge plan: checklist and social needs (api/discharge_plan.py).
  getDischargePlan: (id) => apiFetch(`/api/patients/${encodeURIComponent(id)}/discharge-plan`),
  tickDischargeChecklist: (id, item, done) =>
    apiFetch(`/api/patients/${encodeURIComponent(id)}/discharge-plan/checklist`, json({ item, done })),
  recordSocialNeeds: (id, answers) =>
    apiFetch(`/api/patients/${encodeURIComponent(id)}/discharge-plan/social`, json({ answers })),
  // A patient's own checklist items and check-in questions (api/discharge_plan.py).
  addPlanItem: (id, label) =>
    apiFetch(`/api/patients/${encodeURIComponent(id)}/discharge-plan/items`, json({ label })),
  removePlanItem: (id, itemId) =>
    apiFetch(`/api/patients/${encodeURIComponent(id)}/discharge-plan/items/${encodeURIComponent(itemId)}`,
      { method: 'DELETE' }),
  addCheckinQuestion: (id, label, kind) =>
    apiFetch(`/api/patients/${encodeURIComponent(id)}/discharge-plan/checkin-questions`, json({ label, kind })),
  removeCheckinQuestion: (id, questionId) =>
    apiFetch(`/api/patients/${encodeURIComponent(id)}/discharge-plan/checkin-questions/${encodeURIComponent(questionId)}`,
      { method: 'DELETE' }),
  // Outcomes (api/outcomes.py): the page, and one patient's 30-day outcome.
  getOutcomes: (days = 30) => apiFetch(`/api/outcomes?days=${days}`),
  getPatientOutcome: (id) => apiFetch(`/api/patients/${encodeURIComponent(id)}/outcome`),
  recordPatientOutcome: (id, body) => apiFetch(`/api/patients/${encodeURIComponent(id)}/outcome`, json(body)),
  clearPatientOutcome: (id) => apiFetch(`/api/patients/${encodeURIComponent(id)}/outcome`, { method: 'DELETE' }),
  // An insurer's home (api/insurer.py).
  getInsurerHome: () => apiFetch('/api/insurer/home'),
  // Insurance member IDs (api/member_ids.py).
  getInsurance: (id) => apiFetch(`/api/patients/${encodeURIComponent(id)}/insurance`),
  setMemberId: (id, memberId) => apiFetch(`/api/patients/${encodeURIComponent(id)}/insurance`, json({ member_id: memberId })),
  findByMemberId: (memberId) => apiFetch(`/api/member-lookup?member_id=${encodeURIComponent(memberId)}`),
  // Insurer alerts and hospitals' billing flags (api/insurer_alerts.py).
  getInsurerFlagsFor: (id) => apiFetch(`/api/patients/${encodeURIComponent(id)}/insurer-flags`),
  sendInsurerFlag: (id, body) => apiFetch(`/api/patients/${encodeURIComponent(id)}/insurer-flags`, json(body)),
  getInsurerFlags: (status = 'open') => apiFetch(`/api/insurer/flags?status=${status}`),
  replyInsurerFlag: (fid, body) => apiFetch(`/api/insurer/flags/${fid}/reply`, json(body)),
  markInsurerAlertsSeen: (patientIds) => apiFetch('/api/insurer/alerts/seen', json({ patient_ids: patientIds ?? null })),
  // Caretakers a patient links (api/caretakers.py).
  getCaretakers: (patient) => apiFetch(`/api/caretakers${patient ? `?patient=${encodeURIComponent(patient)}` : ''}`),
  linkCaretaker: (body) => apiFetch('/api/caretakers', json(body)),
  unlinkCaretaker: (id, patient) => apiFetch(`/api/caretakers/${id}${patient ? `?patient=${encodeURIComponent(patient)}` : ''}`,
    { method: 'DELETE' }),
  reportPharmacyProblem: (id, body) =>
    apiFetch(`/api/patients/${encodeURIComponent(id)}/pharmacy-report`, json(body)).then(followUpsChanged),

  // ─── Clinician loop ──────────────────────────────────────────────────────
  // Forecast -> alert -> the doctor's inbox -> a recommendation back on the
  // patient's record. See api/doctor_service.py.
  getDoctors: () => apiFetch('/api/doctors'),
  registerDoctor: (doctor) => apiFetch('/api/doctors', json(doctor)),
  deactivateDoctor: (doctorId) =>
    apiFetch(`/api/doctors/${encodeURIComponent(doctorId)}`, { method: 'DELETE' }),

  getPatientForecast: (patientId) =>
    apiFetch(`/api/patients/${encodeURIComponent(patientId)}/forecast`),
  getPatientClinicalAlerts: (patientId) =>
    apiFetch(`/api/patients/${encodeURIComponent(patientId)}/clinical-alerts`),

  // The sweep is a background job: this returns a scan_id immediately and the
  // caller polls getForecastScan until status leaves "Running".
  startForecastScan: (limit = null) => apiFetch('/api/forecast/scan', json({ limit })),
  getForecastScan: (scanId) => apiFetch(`/api/forecast/scan/${encodeURIComponent(scanId)}`),

  getDoctorAlerts: (doctorId, status) =>
    apiFetch(`/api/doctors/${encodeURIComponent(doctorId)}/alerts`
      + (status ? `?status=${encodeURIComponent(status)}` : '')),
  getDoctorNotifications: (doctorId) =>
    apiFetch(`/api/doctors/${encodeURIComponent(doctorId)}/notifications`),
  getUnroutedAlerts: () => apiFetch('/api/clinical-alerts/unrouted'),
  /** The nurse console: alerts on the caller's own patients, whichever doctor they went to. */
  getMyClinicalAlerts: (status = 'open') =>
    apiFetch(`/api/clinical-alerts/mine?status=${encodeURIComponent(status)}`),
  getRecommendedActions: () => apiFetch('/api/clinical-alerts/actions'),

  acknowledgeClinicalAlert: (alertId, doctorId) =>
    apiFetch(`/api/clinical-alerts/${encodeURIComponent(alertId)}/acknowledge`,
      json({ doctor_id: doctorId })),
  // A reply can hand a follow-up request to the case managers.
  respondToClinicalAlert: (alertId, body) =>
    apiFetch(`/api/clinical-alerts/${encodeURIComponent(alertId)}/respond`, json(body))
      .then(followUpsChanged),
  dismissClinicalAlert: (alertId, doctorId, reason) =>
    apiFetch(`/api/clinical-alerts/${encodeURIComponent(alertId)}/dismiss`,
      json({ doctor_id: doctorId, reason })),

  /**
   * The worklist.
   *
   * The condition filter is applied server-side because it has to be evaluated
   * against the whole cohort, not against whichever page happens to be loaded.
   * Band, trend and text filtering stay client-side, where they are instant.
   */
  getPatients: (params = {}) => {
    const qs = new URLSearchParams({
      limit: String(params.limit ?? 25),
      page: String(params.page ?? 1),
      sort: params.sort || 'score-desc',
    });
    // group accepts several condition keys, comma separated; `match` decides
    // whether a patient needs any of them or all of them.
    const groups = Array.isArray(params.group) ? params.group : [params.group];
    const picked = groups.filter((g) => g && g !== 'All');
    if (picked.length) {
      qs.set('group', picked.join(','));
      if (params.match === 'all') qs.set('match', 'all');
    }
    if (params.band && params.band !== 'All') qs.set('band', params.band);
    if (params.status && params.status !== 'All') qs.set('status', params.status);
    if (params.q) qs.set('q', params.q);
    // Care-team filters, from the Staff page and the Overview.
    for (const key of ['doctor', 'nurse', 'unassigned']) {
      if (params[key]) qs.set(key, params[key]);
    }
    // Returns { page, limit, total, data } — total is what drives pagination,
    // so the envelope is passed through rather than flattened to an array.
    return apiFetch(`/api/patients?${qs.toString()}`);
  },

  /** Conditions present in the current batch, with patient counts. */
  getPatientGroups: () => apiFetch('/api/patient-groups'),

  getPatientById: (id) => apiFetch(`/api/patients/${id}`),

  getSummaryHistory: () => apiFetch('/api/summary/history'),

  getModelMetrics: () => apiFetch('/api/model/metrics'),

  getPipelineRuns: () => apiFetch('/api/pipeline/runs'),

  getTopDrivers: (riskBand = 'High') =>
    apiFetch(`/api/analytics/top-drivers?risk_band=${riskBand}`),

  /**
   * Upload a CSV file to trigger the scoring pipeline.
   * @param {File} file - The File object from the drop zone / input element.
   * @returns {{ run_id: string, status: string }}
   */
  uploadFile: (file) => {
    const form = new FormData();
    form.append('file', file);
    return apiFetch('/api/pipeline/upload', { method: 'POST', body: form });
  },

  /**
   * Poll the status of a running pipeline.
   * @param {string} runId
   */
  getPipelineStatus: (runId) => apiFetch(`/api/pipeline/status/${runId}`),

  // ── Model Studio (api/model_studio.py) ──────────────────────────────────
  getStudioOverview: () => apiFetch('/api/studio/overview'),
  getStudioModels: (target) =>
    apiFetch(`/api/studio/models${target ? `?target=${encodeURIComponent(target)}` : ''}`),
  getStudioModel: (id) => apiFetch(`/api/studio/models/${encodeURIComponent(id)}`),
  deployStudioModel: (id) =>
    apiFetch(`/api/studio/models/${encodeURIComponent(id)}/deploy`, { method: 'POST' }),
  deleteStudioModel: (id) =>
    apiFetch(`/api/studio/models/${encodeURIComponent(id)}`, { method: 'DELETE' }),
  startStudioJob: ({ file, target, mode, algorithm, name, baseModelId }) => {
    const form = new FormData();
    form.append('file', file);
    form.append('target', target);
    form.append('mode', mode);
    form.append('algorithm', algorithm);
    form.append('name', name || '');
    if (baseModelId) form.append('base_model_id', baseModelId);
    return apiFetch('/api/studio/jobs', { method: 'POST', body: form });
  },
  getStudioJob: (id) => apiFetch(`/api/studio/jobs/${encodeURIComponent(id)}`),
  confirmStudioJob: (id, plan) => apiFetch(`/api/studio/jobs/${encodeURIComponent(id)}/confirm`, json(plan)),
  saveStudioJob: (id, name) => apiFetch(`/api/studio/jobs/${encodeURIComponent(id)}/save`, json({ name })),
  scanStudioJob: (id, plan) => apiFetch(`/api/studio/jobs/${encodeURIComponent(id)}/scan`, json(plan)),
  discardStudioJob: (id) =>
    apiFetch(`/api/studio/jobs/${encodeURIComponent(id)}`, { method: 'DELETE' }),
  scoreStudioFile: ({ file, target }) => {
    const form = new FormData();
    form.append('file', file);
    form.append('target', target);
    return apiFetch('/api/studio/score', { method: 'POST', body: form });
  },

  // ── AI models (api/ai_settings.py) ──────────────────────────────────────
  /** Which AI features are on, for everyone: the app hides the ones that are off. */
  getAiStatus: () => apiFetch('/api/ai/status'),
  getAiSettings: () => apiFetch('/api/ai/settings'),
  saveAiSettings: (body) => apiFetch('/api/ai/settings', { ...json(body), method: 'PUT' }),
  /** A provider's models, live; `apiKey`/`baseUrl` try ones not saved yet. */
  listAiModels: (provider, apiKey, baseUrl) =>
    apiFetch('/api/ai/models', json({ provider, api_key: apiKey || undefined, base_url: baseUrl || undefined })),
  testAiModel: (provider, model, apiKey, baseUrl) =>
    apiFetch('/api/ai/test', json({ provider, model, api_key: apiKey || undefined, base_url: baseUrl || undefined })),

  /**
   * Field list and valid category values for the manual-entry form, read from
   * the same feature spec the model was trained against. Fetching it keeps the
   * form in sync with the model instead of hardcoding categories that a
   * retrain could silently invalidate.
   */
  getManualEntrySchema: () => apiFetch('/api/manual-entry/schema'),

  /**
   * Run model inference for a single manually entered patient.
   * Does NOT write to the DB — call savePatientToWorklist separately.
   */
  predictPatient: (payload) =>
    apiFetch('/api/patients/predict', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    }),

  /**
   * Persist a scored patient into the patient_worklist so they appear on the Dashboard.
   */
  savePatientToWorklist: (payload) =>
    apiFetch('/api/patients/worklist-add', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    }),

  /**
   * Ask the dashboard chatbot a natural-language question about patient risk data.
   * `history` carries the recent transcript so follow-ups ("what about the top
   * 10?") resolve; the backend trims it and treats it as context, not command.
   */
  askChatbot: (question, history = []) =>
    apiFetch('/api/chatbot/query', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ question, history }),
    }),

  /**
   * Care coordinator workflow — assignment + notes timeline for a patient.
   */
  getCareActions: (patientId) => apiFetch(`/api/patients/${patientId}/care-actions`),

  /**
   * Weekly post-discharge risk trend for a patient — one point per batch run,
   * with that week's drivers plus an overall stability/deterioration verdict.
   */
  getPatientTrend: (patientId) => apiFetch(`/api/patients/${patientId}/trend`),

  /**
   * On-demand AI call: ROI estimate + counterfactual explanation for a
   * patient's current risk verdict (score/band/drivers).
   */
  getAiInsights: (payload) =>
    apiFetch('/api/ai-insights', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    }),

  /**
   * On-demand AI call: narrate why a single week's trend-series score
   * moved the way it did, using that week's drivers + delta.
   */
  getWeekNarrative: (payload) =>
    apiFetch('/api/week-narrative', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    }),

  assignCoordinator: (patientId, coordinatorName) =>
    apiFetch(`/api/patients/${patientId}/assign`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ coordinator_name: coordinatorName }),
    }),

  addCareNote: (patientId, text, author) =>
    apiFetch(`/api/patients/${patientId}/notes`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(author ? { text, author } : { text }),
    }),

  /**
   * Patient data update workflow — edit an existing patient's metrics,
   * preview the recalculated score, then commit it.
   */
  getPatientForEdit: (patientId) => apiFetch(`/api/patients/${patientId}/edit`),

  predictPatientUpdate: (patientId, payload) =>
    apiFetch(`/api/patients/${patientId}/predict-update`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    }),

  commitPatientUpdate: (patientId, payload) =>
    apiFetch(`/api/patients/${patientId}/update`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    }),

  /**
   * Score-change alerts raised by the patient update workflow.
   */
  getAlerts: () => apiFetch('/api/alerts'),

  acknowledgeAlert: (alertId) =>
    apiFetch(`/api/alerts/${alertId}/acknowledge`, { method: 'POST' }),

  /**
   * The caller's patients whose latest monitoring week got worse (red flag,
   * a rise of 5+ points, or a move up a band), most serious first.
   */
  getRisingRisk: (limit = 50) => apiFetch(`/api/rising-risk?limit=${limit}`),

  markRiskSeen: (patientId, weekNumber) =>
    apiFetch(`/api/rising-risk/${encodeURIComponent(patientId)}/seen`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ week_number: weekNumber }),
    }),
};

// ─── Exports ───────────────────────────────────────────────────────────────
// Individual named exports so every import site stays unchanged.
const API = USE_MOCK ? MockAPI : RealAPI;

export const getSummary        = API.getSummary;
export const getMe             = USE_MOCK ? () => Promise.resolve(null) : RealAPI.getMe;

/** Wakes the API: the liveness probe needs no sign-in and touches no data. */
export const pingService = () => fetch(`${BASE_URL}/healthz`);

// Hospital pages - live data only. A made-up staff list or care team would be
// indistinguishable on screen from a real one.
const HOSPITAL_MOCK_ERROR = 'The hospital pages require the real backend. Set VITE_USE_MOCK=false.';
const realOnly = (fn) => (USE_MOCK ? () => Promise.reject(new Error(HOSPITAL_MOCK_ERROR)) : fn);
export const getAccount        = realOnly(RealAPI.getAccount);
export const changePassword    = realOnly(RealAPI.changePassword);
export const getOverview       = realOnly(RealAPI.getOverview);
export const getStaff          = realOnly(RealAPI.getStaff);
export const getMyPatients     = realOnly(RealAPI.getMyPatients);
export const emergencyLookup     = realOnly(RealAPI.emergencyLookup);
export const emergencyGrant      = realOnly(RealAPI.emergencyGrant);
export const getPatientEmergency = realOnly(RealAPI.getPatientEmergency);
export const getFollowUps        = realOnly(RealAPI.getFollowUps);
export const getFollowUpsWaiting = realOnly(RealAPI.getFollowUpsWaiting);
export const getFollowUpBell     = realOnly(RealAPI.getFollowUpBell);
export const markFollowUpReportsSeen = realOnly(RealAPI.markFollowUpReportsSeen);
export const markPharmacySeen    = realOnly(RealAPI.markPharmacySeen);
export const getPatientFollowUp  = realOnly(RealAPI.getPatientFollowUp);
export const requestFollowUp     = realOnly(RealAPI.requestFollowUp);
export const addFollowUpNote     = realOnly(RealAPI.addFollowUpNote);
export const takeFollowUp        = realOnly(RealAPI.takeFollowUp);
export const finishFollowUp      = realOnly(RealAPI.finishFollowUp);
export const getPharmacyHome     = realOnly(RealAPI.getPharmacyHome);
export const getMyHome           = realOnly(RealAPI.getMyHome);
export const getMyPeople         = realOnly(RealAPI.getMyPeople);
export const markCaretakerSeen   = realOnly(RealAPI.markCaretakerSeen);
export const askCaseManager      = realOnly(RealAPI.askCaseManager);
export const getCaretakers       = realOnly(RealAPI.getCaretakers);
export const getInsurance        = realOnly(RealAPI.getInsurance);
export const getInsurerHome      = realOnly(RealAPI.getInsurerHome);
export const setMemberId         = realOnly(RealAPI.setMemberId);
export const findByMemberId      = realOnly(RealAPI.findByMemberId);
export const getInsurerFlagsFor  = realOnly(RealAPI.getInsurerFlagsFor);
export const sendInsurerFlag     = realOnly(RealAPI.sendInsurerFlag);
export const getInsurerFlags     = realOnly(RealAPI.getInsurerFlags);
export const replyInsurerFlag    = realOnly(RealAPI.replyInsurerFlag);
export const markInsurerAlertsSeen = realOnly(RealAPI.markInsurerAlertsSeen);
export const getMedicinesGiven   = realOnly(RealAPI.getMedicinesGiven);
export const recordMedicinesGiven = realOnly(RealAPI.recordMedicinesGiven);
export const undoMedicinesGiven  = realOnly(RealAPI.undoMedicinesGiven);
export const getPatientMedicinesGiven = realOnly(RealAPI.getPatientMedicinesGiven);
export const getCheckIn          = realOnly(RealAPI.getCheckIn);
export const sendCheckIn         = realOnly(RealAPI.sendCheckIn);
export const getDischargePlan    = realOnly(RealAPI.getDischargePlan);
export const tickDischargeChecklist = realOnly(RealAPI.tickDischargeChecklist);
export const recordSocialNeeds   = realOnly(RealAPI.recordSocialNeeds);
export const addPlanItem         = realOnly(RealAPI.addPlanItem);
export const removePlanItem      = realOnly(RealAPI.removePlanItem);
export const addCheckinQuestion  = realOnly(RealAPI.addCheckinQuestion);
export const removeCheckinQuestion = realOnly(RealAPI.removeCheckinQuestion);
export const getOutcomes         = realOnly(RealAPI.getOutcomes);
export const getPatientOutcome   = realOnly(RealAPI.getPatientOutcome);
export const recordPatientOutcome = realOnly(RealAPI.recordPatientOutcome);
export const clearPatientOutcome = realOnly(RealAPI.clearPatientOutcome);
export const linkCaretaker       = realOnly(RealAPI.linkCaretaker);
export const unlinkCaretaker     = realOnly(RealAPI.unlinkCaretaker);
export const reportPharmacyProblem = realOnly(RealAPI.reportPharmacyProblem);
export const setCareTeam       = realOnly(RealAPI.setCareTeam);
export const getPatientSummary = realOnly(RealAPI.getPatientSummary);
export const openPatient       = realOnly(RealAPI.openPatient);
export const getPatients       = API.getPatients;
export const getPatientGroups  = USE_MOCK
  ? () => Promise.resolve({ groups: [] })
  : API.getPatientGroups;
export const getPatientById    = API.getPatientById;
export const getSummaryHistory = API.getSummaryHistory;
export const getModelMetrics   = API.getModelMetrics;
export const getPipelineRuns   = API.getPipelineRuns;
export const getTopDrivers     = USE_MOCK
  ? MockAPI.getTopDrivers
  : RealAPI.getTopDrivers;

export const getPatientTrend   = USE_MOCK
  ? MockAPI.getPatientTrend
  : RealAPI.getPatientTrend;

export const getAiInsights     = USE_MOCK
  ? MockAPI.getAiInsights
  : RealAPI.getAiInsights;

export const getWeekNarrative  = USE_MOCK
  ? MockAPI.getWeekNarrative
  : RealAPI.getWeekNarrative;

// These two only exist in the real API (no-ops in mock mode)
export const uploadFile = USE_MOCK
  ? () => Promise.resolve({ run_id: 'MOCK-RUN', status: 'Running' })
  : RealAPI.uploadFile;

// Model Studio trains and scores on the server; there is nothing to mock.
export const getStudioOverview = realOnly(RealAPI.getStudioOverview);
export const getStudioModels = realOnly(RealAPI.getStudioModels);
export const getStudioModel = realOnly(RealAPI.getStudioModel);
export const deployStudioModel = realOnly(RealAPI.deployStudioModel);
export const deleteStudioModel = realOnly(RealAPI.deleteStudioModel);
export const startStudioJob = realOnly(RealAPI.startStudioJob);
export const getStudioJob = realOnly(RealAPI.getStudioJob);
export const confirmStudioJob = realOnly(RealAPI.confirmStudioJob);
export const saveStudioJob = realOnly(RealAPI.saveStudioJob);
export const scanStudioJob = realOnly(RealAPI.scanStudioJob);
export const discardStudioJob = realOnly(RealAPI.discardStudioJob);
export const scoreStudioFile = realOnly(RealAPI.scoreStudioFile);

// AI models. With mock data every AI feature shows, as it always has.
export const getAiStatus = USE_MOCK
  ? () => Promise.resolve({ chatbot: { on: true }, insights: { on: true }, studio: { on: true }, data: { on: true } })
  : RealAPI.getAiStatus;
export const getAiSettings = realOnly(RealAPI.getAiSettings);
export const saveAiSettings = realOnly(RealAPI.saveAiSettings);
export const listAiModels = realOnly(RealAPI.listAiModels);
export const testAiModel = realOnly(RealAPI.testAiModel);
export const getPipelineStatus = USE_MOCK
  ? () => Promise.resolve({ status: 'Completed', current_step: 'registry_updated', patient_count: 0 })
  : RealAPI.getPipelineStatus;

// Manual entry — only works with real backend (VITE_USE_MOCK=false)
// The schema call resolves to null in mock mode so the form falls back to its
// built-in category lists rather than failing to render at all.
export const getManualEntrySchema = USE_MOCK
  ? () => Promise.resolve(null)
  : RealAPI.getManualEntrySchema;

export const predictPatient = USE_MOCK
  ? () => Promise.reject(new Error('Manual entry requires the real backend. Set VITE_USE_MOCK=false.'))
  : RealAPI.predictPatient;

export const savePatientToWorklist = USE_MOCK
  ? () => Promise.reject(new Error('Manual entry requires the real backend. Set VITE_USE_MOCK=false.'))
  : RealAPI.savePatientToWorklist;

export const askChatbot = USE_MOCK
  ? () => Promise.reject(new Error('The chatbot requires the real backend. Set VITE_USE_MOCK=false.'))
  : RealAPI.askChatbot;

// Care coordinator workflow — only works with real backend
const CARE_ACTIONS_MOCK_ERROR = 'Care coordinator workflow requires the real backend. Set VITE_USE_MOCK=false.';
export const getCareActions = USE_MOCK
  ? () => Promise.reject(new Error(CARE_ACTIONS_MOCK_ERROR))
  : RealAPI.getCareActions;

export const assignCoordinator = USE_MOCK
  ? () => Promise.reject(new Error(CARE_ACTIONS_MOCK_ERROR))
  : RealAPI.assignCoordinator;

export const addCareNote = USE_MOCK
  ? () => Promise.reject(new Error(CARE_ACTIONS_MOCK_ERROR))
  : RealAPI.addCareNote;

// Patient update + alerts workflow — only works with real backend
const UPDATE_MOCK_ERROR = 'The patient update workflow requires the real backend. Set VITE_USE_MOCK=false.';
export const getPatientForEdit = USE_MOCK
  ? () => Promise.reject(new Error(UPDATE_MOCK_ERROR))
  : RealAPI.getPatientForEdit;

export const predictPatientUpdate = USE_MOCK
  ? () => Promise.reject(new Error(UPDATE_MOCK_ERROR))
  : RealAPI.predictPatientUpdate;

export const commitPatientUpdate = USE_MOCK
  ? () => Promise.reject(new Error(UPDATE_MOCK_ERROR))
  : RealAPI.commitPatientUpdate;

export const getAlerts = USE_MOCK
  ? () => Promise.resolve([])
  : RealAPI.getAlerts;

// Live data only: an invented "your patient is getting worse" would be
// indistinguishable from a real one.
export const getRisingRisk = USE_MOCK
  ? () => Promise.resolve({ total: 0, urgent: 0, unseen: 0, patients: [] })
  : RealAPI.getRisingRisk;

export const markRiskSeen = USE_MOCK ? () => Promise.resolve(null) : RealAPI.markRiskSeen;

export const acknowledgeAlert = USE_MOCK
  ? () => Promise.reject(new Error(UPDATE_MOCK_ERROR))
  : RealAPI.acknowledgeAlert;


// ─── Clinician loop ────────────────────────────────────────────────────────
// Every one of these writes to, or reads from, live MongoDB. There is no
// mock equivalent: a fabricated inbox would be indistinguishable on screen
// from a real one, which is exactly the confusion to avoid in a clinical tool.
const CLINICIAN_MOCK_ERROR =
  'The clinician workflow requires the real backend. Set VITE_USE_MOCK=false.';
const clinicianOnly = (fn) =>
  (USE_MOCK ? () => Promise.reject(new Error(CLINICIAN_MOCK_ERROR)) : fn);

export const getDoctors = clinicianOnly(RealAPI.getDoctors);
export const registerDoctor = clinicianOnly(RealAPI.registerDoctor);
export const deactivateDoctor = clinicianOnly(RealAPI.deactivateDoctor);
export const getPatientForecast = clinicianOnly(RealAPI.getPatientForecast);
export const getPatientClinicalAlerts = clinicianOnly(RealAPI.getPatientClinicalAlerts);
export const startForecastScan = clinicianOnly(RealAPI.startForecastScan);
export const getForecastScan = clinicianOnly(RealAPI.getForecastScan);
export const getDoctorAlerts = clinicianOnly(RealAPI.getDoctorAlerts);
export const getDoctorNotifications = clinicianOnly(RealAPI.getDoctorNotifications);
export const getUnroutedAlerts = clinicianOnly(RealAPI.getUnroutedAlerts);
export const getMyClinicalAlerts = clinicianOnly(RealAPI.getMyClinicalAlerts);
export const getRecommendedActions = clinicianOnly(RealAPI.getRecommendedActions);
export const acknowledgeClinicalAlert = clinicianOnly(RealAPI.acknowledgeClinicalAlert);
export const respondToClinicalAlert = clinicianOnly(RealAPI.respondToClinicalAlert);
export const dismissClinicalAlert = clinicianOnly(RealAPI.dismissClinicalAlert);
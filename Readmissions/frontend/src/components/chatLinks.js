// Turning a chatbot answer into links.
//
// The answer is plain text, so what becomes a link is decided by two sources:
//
//  1. `entities` from the backend - patient ids, conditions, diagnoses and
//     clinicians taken from the data the answer was phrased from (see
//     api/chatbot_links.py). These are known to exist and to be the caller's.
//  2. A fixed vocabulary the dashboard can always filter on - risk bands, trend
//     statuses, the conditions in this batch, page names, ICD codes - so an
//     answer that did not run a query (a general question) still links them.
//
// Every link lands on a URL the dashboard already understands: a patient's
// weekly trend (or their page, for the roles that give a reason first), or the
// Patients worklist with its query parameters set.

import { can, CONSOLE_ROLES, OVERVIEW_ROLES, REASON_ROLES, STAFF_ROLES } from '../roles';

const escapeRe = (s) => s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');

const worklist = (params) => `/patients?${new URLSearchParams(params)}`;

const ENTITY_ROUTES = {
  // The weekly trend - the scores the chatbot talks about. The roles that give a
  // reason first land on the patient's page, where they give it.
  patient:   (v) => (can(REASON_ROLES)
    ? { to: `/patients/${encodeURIComponent(v)}`, title: `Open patient ${v}` }
    : { to: `/patients/${encodeURIComponent(v)}/trend`, title: `Open the weekly trend for ${v}` }),
  condition: (v, t) => ({ to: worklist({ group: v }), title: `Show patients with ${t}` }),
  diagnosis: (v) => ({ to: worklist({ search: v }), title: `Search the worklist for "${v}"` }),
  doctor:    (v, t) => ({ to: worklist({ doctor: v }), title: `Show ${t}'s patients` }),
};

// Patient ids are stored as "MIMIC-12431768". An id the backend did not hand
// over (one from an earlier turn, say) still links: with the prefix it is
// unambiguous; as eight bare digits - which counts and dates never are - it
// becomes a worklist search, which matches the id in either stored form.
const PATIENT_ID = /(?<![\w-])(MIMIC-)?(\d{8})(?![\w-])/g;

const BAND_TO = (word) => {
  const band = word[0].toUpperCase() + word.slice(1).toLowerCase();
  return { to: worklist({ filter: band }), title: `Show ${band.toLowerCase()}-risk patients` };
};

// "stable" and "improving" are ordinary English, so they only link when the
// sentence is plainly about a trend status.
const TREND_PATTERNS = [
  [/\bneeds?[ -]attention\b/gi, 'NeedsAttention', 'patients that need attention'],
  [/\baction[ _-]required\b/gi, 'action_required', 'patients needing action'],
  [/\bdeteriorating\b/gi, 'deteriorating', 'deteriorating patients'],
  [/\b(?:improving|stable)\b(?=\s+(?:patients?|trends?|status))/gi, null, null],
  [/(?<=\b(?:status|trend)(?: is| of|:)?\s+)(?:improving|stable)\b/gi, null, null],
];

const PAGES = [
  [/\bPatients page\b/g, '/patients', () => true],
  [/\bStaff page\b/g, '/staff', () => can(STAFF_ROLES)],
  [/\bAnalytics(?: page)?\b/g, '/analytics', () => true],
  [/\bOverview(?: page)?\b/g, '/', () => can(OVERVIEW_ROLES)],
  [/\b(?:Clinician|Doctor) Console\b/gi, '/doctor', () => can(CONSOLE_ROLES)],
];

const ICD = /\bICD(?:-?(?:9|10))?(?:\s+code)?:?\s+([A-Z]?\d{2,3}(?:\.\d{1,4})?[A-Z0-9]*)\b/g;

/**
 * Every matcher for one answer: [{ re, link(match) }], where link returns
 * { to, title } or null to skip that match.
 */
export function buildMatchers({ entities = [], groups = [], patientOnly = false }) {
  // Hospital admins and insurers see the overview layer: the server ignores a
  // condition filter or diagnosis search for them, so those would be links to
  // an unfiltered list that only looks filtered.
  const clinical = !can(REASON_ROLES);
  const matchers = [];

  for (const e of entities) {
    const route = ENTITY_ROUTES[e.kind];
    const text = String(e.text || '').trim();
    if (!route || !text) continue;
    if (!clinical && (e.kind === 'condition' || e.kind === 'diagnosis')) continue;
    const re = new RegExp(`(?<![\\w-])${escapeRe(text)}(?![\\w-])`, 'gi');
    matchers.push({ re, priority: 2, kind: e.kind, link: () => route(String(e.value), text) });
    // The answer often drops the prefix: "12431768" for MIMIC-12431768.
    const bare = e.kind === 'patient' && /^[A-Za-z]+-(\d+)$/.exec(text);
    if (bare) {
      matchers.push({ re: new RegExp(`(?<![\\w-])${bare[1]}(?![\\w-])`, 'g'), priority: 2,
        kind: e.kind, link: () => route(String(e.value), text) });
    }
  }

  // A patient's dashboard has their own record and nothing else to link to.
  if (patientOnly) return matchers.filter((m) => m.kind === 'patient');

  matchers.push({ re: PATIENT_ID, priority: 1,
    link: (m) => (m[1] ? ENTITY_ROUTES.patient(m[0])
      : { to: worklist({ search: m[2] }), title: `Find patient ${m[2]}` }) });

  matchers.push({ re: /\b(high|medium|low)(?=[- ]risk\b|\s+band\b)/gi, priority: 1,
    link: (m) => BAND_TO(m[1]) });
  // "High: 14 patients", "91.2% (High)" - a capitalised band used as a label.
  matchers.push({ re: /\b(High|Medium|Low)(?=\s*[:()])/g, priority: 1,
    link: (m) => BAND_TO(m[1]) });

  for (const [re, trend, what] of TREND_PATTERNS) {
    matchers.push({ re, priority: 1, link: (m) => {
      const key = trend || m[0].toLowerCase();
      return { to: worklist({ trend: key }), title: `Show ${what || `${key} patients`}` };
    } });
  }

  if (clinical) {
    for (const g of groups) {
      if (!g?.key || !g?.label) continue;
      matchers.push({ re: new RegExp(`(?<![\\w-])${escapeRe(g.label)}(?![\\w-])`, 'gi'),
        priority: 1, link: () => ENTITY_ROUTES.condition(g.key, g.label) });
    }
    matchers.push({ re: ICD, priority: 1,
      link: (m) => ENTITY_ROUTES.diagnosis(m[1]) });
  }

  for (const [re, to, allowed] of PAGES) {
    if (allowed()) matchers.push({ re, priority: 1, link: () => ({ to, title: `Go to ${to}` }) });
  }

  return matchers;
}

/**
 * Split text into [{ text, link? }]. Overlapping matches resolve to the one
 * that starts first; at the same start, the backend's entity beats the generic
 * pattern and the longer match beats the shorter.
 */
export function linkify(text, matchers) {
  if (!text) return [{ text: '' }];
  const hits = [];
  for (const { re, priority, link } of matchers) {
    re.lastIndex = 0;
    for (const m of text.matchAll(re)) {
      const target = link(m);
      if (target) hits.push({ start: m.index, end: m.index + m[0].length, priority, ...target });
    }
  }
  hits.sort((a, b) => a.start - b.start || b.priority - a.priority || (b.end - b.start) - (a.end - a.start));

  const out = [];
  let cursor = 0;
  for (const h of hits) {
    if (h.start < cursor) continue;
    if (h.start > cursor) out.push({ text: text.slice(cursor, h.start) });
    out.push({ text: text.slice(h.start, h.end), link: { to: h.to, title: h.title } });
    cursor = h.end;
  }
  if (cursor < text.length) out.push({ text: text.slice(cursor) });
  return out;
}

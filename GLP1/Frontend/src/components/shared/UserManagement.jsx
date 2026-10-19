// User Management - identical copy in GLP1/Frontend and Readmissions/frontend
// (src/components/shared/UserManagement.jsx). Accounts are shared by both apps,
// so change both files together.
//
// Everything here is a view over the auth service's /auth/admin/* endpoints
// (Readmissions/api/user_admin.py), which decide who may do what. Hiding a
// control is a courtesy; the server refuses the call either way.
import { useCallback, useEffect, useState } from 'react';

const ROLE_LABELS = {
  superadmin: 'Superadmin', hospital_admin: 'Hospital admin', doctor: 'Doctor',
  nurse: 'Nurse', case_manager: 'Case manager', insurer: 'Insurer', pharmacy: 'Pharmacy',
  caretaker: 'Caretaker', patient: 'Patient',
};

// Mirrors ASSIGNABLE in Readmissions/api/user_admin.py.
const ASSIGNABLE = {
  superadmin: ['hospital_admin', 'doctor', 'nurse', 'case_manager', 'insurer', 'pharmacy', 'caretaker', 'patient'],
  hospital_admin: ['doctor', 'nurse', 'case_manager', 'patient'],
};
const HOSPITAL_ROLES = ['hospital_admin', 'doctor', 'nurse', 'case_manager', 'patient'];

const input = 'w-full rounded-md border border-gray-300 bg-white px-2.5 py-1.5 text-sm text-gray-800 focus:border-blue-500 focus:outline-none';
// The same control, sized to its content rather than its column.
const inputInline = input.replace('w-full ', '');
const button = 'rounded-md bg-blue-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-blue-700 disabled:opacity-50';
const ghost = 'rounded-md border border-gray-300 px-2.5 py-1 text-xs font-medium text-gray-700 hover:bg-gray-50 disabled:opacity-50';
const panel = 'rounded-xl border border-gray-200 bg-white p-5';

/**
 * @param {string}   authBaseUrl  origin of the shared auth service (the Readmissions API)
 * @param {Function} getToken     returns the signed-in user's bearer token. Pass a stable
 *                               function (module-level), not an inline arrow: a new
 *                               function each render would reload the list each render.
 * @param {object}   me           the signed-in account: { sub|id, email, role, hospital_id }
 */
export default function UserManagement({ authBaseUrl, getToken, me }) {
  const isSuper = me?.role === 'superadmin';
  const myId = me?.sub || me?.id;
  const assignable = ASSIGNABLE[me?.role] || [];

  const [users, setUsers] = useState([]);
  // Sign-ups waiting on this admin: for a hospital admin, the requests to join
  // its hospital; for the superadmin, every one.
  const [requests, setRequests] = useState([]);
  const [hospitals, setHospitals] = useState([]);
  const [insurers, setInsurers] = useState([]);
  const [pharmacies, setPharmacies] = useState([]);
  // role files the list by role - including what each pending sign-up asked to be.
  const [filter, setFilter] = useState({ hospital_id: '', status: '', role: '' });
  const [error, setError] = useState(null);
  const [notice, setNotice] = useState(null);
  const [busy, setBusy] = useState(false);

  const request = useCallback(async (path, { method = 'GET', body } = {}) => {
    const res = await fetch(`${authBaseUrl.replace(/\/$/, '')}${path}`, {
      method,
      headers: { Authorization: `Bearer ${getToken()}`,
                 ...(body ? { 'Content-Type': 'application/json' } : {}) },
      body: body ? JSON.stringify(body) : undefined,
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      const detail = Array.isArray(data.detail) ? data.detail.map((d) => d.msg).join('; ') : data.detail;
      throw new Error(detail || `Request failed (${res.status})`);
    }
    return data;
  }, [authBaseUrl, getToken]);

  // Bumped after every change to refetch the lists.
  const [reloadKey, setReloadKey] = useState(0);

  useEffect(() => {
    // A response that arrives after the filters changed is dropped, so a slow
    // earlier request cannot overwrite a newer answer.
    let current = true;
    const qs = new URLSearchParams(Object.entries(filter).filter(([, v]) => v));
    Promise.all([
      request(`/auth/admin/users${qs.toString() ? `?${qs}` : ''}`),
      request('/auth/admin/hospitals'),
      isSuper ? request('/auth/admin/insurers') : Promise.resolve([]),
      request('/auth/admin/users?status=pending'),
      isSuper ? request('/auth/admin/pharmacies') : Promise.resolve([]),
    ]).then(([u, h, i, pending, ph]) => {
      if (!current) return;
      setUsers(u); setHospitals(h); setInsurers(i); setPharmacies(ph); setError(null);
      // A suspended account is pending too, but keeps its hospital; a sign-up
      // is attached to nothing yet.
      setRequests(pending.filter(isSignup));
    }).catch((e) => { if (current) setError(e.message); });
    return () => { current = false; };
  }, [request, filter, isSuper, reloadKey]);

  const run = async (action, onDone) => {
    setBusy(true); setError(null);
    try { const out = await action(); onDone?.(out); setReloadKey((k) => k + 1); }
    catch (e) { setError(e.message); }
    finally { setBusy(false); }
  };

  const hospitalName = (id) => hospitals.find((h) => h.id === id)?.name || id || '—';

  return (
    <div className="space-y-5">
      <div>
        <h2 className="text-lg font-semibold text-gray-800">User Management</h2>
        <p className="text-sm text-gray-500">
          {isSuper ? 'All hospitals, insurers and pharmacies.' : `Staff and patients of ${hospitalName(me?.hospital_id)}.`}
          {' '}Accounts work in both GLP-1 and Readmissions.
        </p>
      </div>

      {error && <div className="rounded-md bg-red-50 px-3 py-2 text-sm text-red-700">{error}</div>}

      {requests.length > 0 && (
        <div className="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-900">
          <span>
            <b>{requests.length}</b> {requests.length === 1 ? 'person has' : 'people have'} signed up
            {isSuper ? '' : ` to join ${hospitalName(me?.hospital_id)}`} and {requests.length === 1 ? 'is' : 'are'} waiting
            for approval.
          </span>
          <button className={ghost} onClick={() => setFilter({ ...filter, status: 'pending' })}>Review</button>
        </div>
      )}
      {notice && <Notice notice={notice} onClose={() => setNotice(null)} />}

      {isSuper && (
        <div className="grid gap-5 md:grid-cols-3">
          <OrgPanel title="Hospitals" items={hospitals} busy={busy}
                    onCreate={(name) => run(() => request('/auth/admin/hospitals', { method: 'POST', body: { name } }))} />
          <OrgPanel title="Insurers" items={insurers} busy={busy}
                    onCreate={(name) => run(() => request('/auth/admin/insurers', { method: 'POST', body: { name } }))} />
          <OrgPanel title="Pharmacies" items={pharmacies} busy={busy}
                    onCreate={(name) => run(() => request('/auth/admin/pharmacies', { method: 'POST', body: { name } }))} />
        </div>
      )}

      <AddUser assignable={assignable} isSuper={isSuper} hospitals={hospitals} insurers={insurers}
               pharmacies={pharmacies} busy={busy}
               onAdd={(body) => run(() => request('/auth/admin/users', { method: 'POST', body }),
                                    (out) => setNotice({ kind: 'created', results: [out] }))} />

      <ImportCsv isSuper={isSuper} hospitals={hospitals} insurers={insurers} pharmacies={pharmacies} busy={busy}
                 onImport={(body) => run(() => request('/auth/admin/users/import', { method: 'POST', body }),
                                         (out) => setNotice({ kind: 'import', ...out }))} />

      <div className={panel}>
        <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
          <h3 className="font-semibold text-gray-800">Accounts ({users.length})</h3>
          <div className="flex gap-2">
            {isSuper && (
              <select className={input} value={filter.hospital_id}
                      onChange={(e) => setFilter({ ...filter, hospital_id: e.target.value })}>
                <option value="">All hospitals</option>
                {hospitals.map((h) => <option key={h.id} value={h.id}>{h.name}</option>)}
              </select>
            )}
            <select className={input} value={filter.status}
                    onChange={(e) => setFilter({ ...filter, status: e.target.value })}>
              <option value="">Any status</option>
              <option value="pending">Pending</option>
              <option value="active">Active</option>
            </select>
            <select className={input} value={filter.role}
                    onChange={(e) => setFilter({ ...filter, role: e.target.value })}>
              <option value="">Any role</option>
                            {Object.entries(ROLE_LABELS)
                .filter(([r]) => r !== 'superadmin' && (isSuper || r === 'hospital_admin' || assignable.includes(r)))
                .map(([r, label]) => (
                <option key={r} value={r}>{label}</option>))}
            </select>
          </div>
        </div>
        <div className="overflow-x-auto">
          <table className="w-full text-left text-sm">
            <thead className="border-b border-gray-200 text-xs uppercase text-gray-500">
              {/* A hospital admin only ever sees its own hospital's people, so a
                  hospital column would say the same thing on every row. */}
              <tr><th className="py-2 pr-3">Account</th><th className="py-2 pr-3">Role</th>
                  {isSuper && <><th className="py-2 pr-3">Hospital</th><th className="py-2 pr-3">Insurer / pharmacy</th></>}
                  <th className="py-2 pr-3">Status</th>
                  <th className="py-2 pr-3">Created</th><th className="py-2" /></tr>
            </thead>
            <tbody>
              {users.map((u) => (
                // Keyed on what the row starts from, so an approval or a decline
                // resets its pickers instead of keeping the old choice.
                <UserRow key={`${u.sub}:${u.status}:${u.hospital_id}:${u.requested_hospital_id}`}
                         user={u} me={me} myId={myId} isSuper={isSuper}
                         assignable={assignable} hospitals={hospitals} insurers={insurers}
                         pharmacies={pharmacies} hospitalName={hospitalName} busy={busy}
                         onSave={(body) => run(() => request(`/auth/admin/users/${u.sub}`, { method: 'PATCH', body }))}
                         onDecline={() => run(() => request(`/auth/admin/users/${u.sub}/decline`, { method: 'POST' }))} />
              ))}
              {!users.length && (
                <tr><td colSpan={isSuper ? 7 : 5} className="py-6 text-center text-gray-400">No accounts match.</td></tr>
              )}
            </tbody>
          </table>
        </div>
      </div>

      <EmergencyReview request={request} isSuper={isSuper} hospitals={hospitals} hospitalName={hospitalName} />
      <AccessLog request={request} isSuper={isSuper} hospitals={hospitals} hospitalName={hospitalName} />
    </div>
  );
}

// Emergency access ("break the glass") to review - in both apps. A doctor or
// nurse who opened a patient not on their care team, with their reason and
// note; the admin marks each one appropriate, or not appropriate with a note
// so it can be followed up (Readmissions/api/emergency.py, 45 CFR 164.312(a)(2)(ii)).
function EmergencyReview({ request, isSuper, hospitals, hospitalName }) {
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);
  const [status, setStatus] = useState('to_review');
  const [hospitalId, setHospitalId] = useState('');
  const [reloadKey, setReloadKey] = useState(0);
  const [editing, setEditing] = useState(null);        // { id, outcome, note }
  const [busy, setBusy] = useState(false);
  // Opens by itself when there is something to review; after that it stays
  // however the admin leaves it (it does not snap shut after the last review).
  const [open, setOpen] = useState(false);

  useEffect(() => {
    let current = true;
    const qs = new URLSearchParams(Object.entries({ status, hospital_id: hospitalId }).filter(([, v]) => v));
    request(`/auth/admin/emergency-access?${qs}`)
      .then((d) => {
        if (!current) return;
        setData(d); setError(null);
        if (d.to_review > 0) setOpen(true);
      })
      .catch((e) => { if (current) setError(e.message); });
    return () => { current = false; };
  }, [request, status, hospitalId, reloadKey]);

  const save = async () => {
    setBusy(true); setError(null);
    try {
      await request(`/auth/admin/emergency-access/${editing.id}/review`,
                    { method: 'POST', body: { outcome: editing.outcome, note: editing.note } });
      setEditing(null); setReloadKey((k) => k + 1);
    } catch (e) { setError(e.message); }
    finally { setBusy(false); }
  };

  const waiting = data?.to_review || 0;
  return (
    <details className={panel} open={open} onToggle={(e) => setOpen(e.currentTarget.open)}>
      <summary className="cursor-pointer font-semibold text-gray-800">
        Emergency access to review
        {waiting > 0 && (
          <span className="ml-2 rounded-full bg-red-600 px-2 py-0.5 text-xs font-bold text-white">{waiting}</span>
        )}
      </summary>
      <p className="mt-2 text-xs text-gray-500">
        A doctor or nurse opened a patient who is not on their care team (&ldquo;break the glass&rdquo;), with the
        reason and note they gave. The record stayed open to them for 24 hours. Check each one was for a real
        emergency; anything not appropriate needs a note so it can be followed up.
      </p>
      <div className="mt-3 flex flex-wrap gap-2">
        <select className={inputInline} value={status} onChange={(e) => setStatus(e.target.value)}>
          <option value="to_review">To review</option>
          <option value="all">All</option>
        </select>
        {isSuper && (
          <select className={inputInline} value={hospitalId} onChange={(e) => setHospitalId(e.target.value)}>
            <option value="">All hospitals</option>
            {hospitals.map((h) => <option key={h.id} value={h.id}>{h.name}</option>)}
          </select>
        )}
      </div>
      {error && <div className="mt-3 rounded-md bg-red-50 px-3 py-2 text-sm text-red-700">{error}</div>}
      <div className="mt-3 overflow-x-auto">
        <table className="w-full text-left text-sm">
          <thead className="border-b border-gray-200 text-xs uppercase text-gray-500">
            <tr><th className="py-2 pr-3">When</th><th className="py-2 pr-3">Who</th>
                <th className="py-2 pr-3">App</th><th className="py-2 pr-3">Patient</th>
                {isSuper && <th className="py-2 pr-3">Hospital</th>}
                <th className="py-2 pr-3">Reason and note</th><th className="py-2">Review</th></tr>
          </thead>
          <tbody>
            {(data?.items || []).map((g) => (
              <tr key={g.id} className="border-b border-gray-100 align-top">
                <td className="py-2 pr-3 whitespace-nowrap text-xs text-gray-500">
                  {new Date(g.granted_at).toLocaleString()}
                  {g.active && <div className="mt-0.5 text-[11px] font-semibold text-red-700">still open</div>}
                </td>
                <td className="py-2 pr-3">
                  <div className="text-gray-800">{g.user.name || g.user.email}</div>
                  <div className="text-xs text-gray-500">{ROLE_LABELS[g.user.role] || g.user.role}</div>
                </td>
                <td className="py-2 pr-3 whitespace-nowrap text-gray-700">{APP_LABELS[g.app] || g.app}</td>
                <td className="py-2 pr-3 font-mono text-xs text-gray-700">{g.patient_id}</td>
                {isSuper && <td className="py-2 pr-3 text-gray-700">{hospitalName(g.hospital_id)}</td>}
                <td className="py-2 pr-3 max-w-xs">
                  <div className="text-gray-800">{g.reason_label}</div>
                  <div className="text-xs text-gray-500">&ldquo;{g.note}&rdquo;</div>
                </td>
                <td className="py-2 min-w-[200px]">
                  {g.review ? (
                    <div>
                      <span className={`rounded-full px-2 py-0.5 text-xs font-semibold ${g.review.outcome === 'appropriate'
                        ? 'bg-green-50 text-green-700' : 'bg-red-50 text-red-700'}`}>{g.review.outcome_label}</span>
                      <div className="mt-0.5 text-[11px] text-gray-500">{g.review.by.email}</div>
                      {g.review.note && <div className="text-xs text-gray-600">{g.review.note}</div>}
                    </div>
                  ) : editing?.id === g.id ? (
                    <div className="space-y-1.5">
                      <select className={input} value={editing.outcome}
                              onChange={(e) => setEditing({ ...editing, outcome: e.target.value })}>
                        {(data.outcomes || []).map((o) => <option key={o.key} value={o.key}>{o.label}</option>)}
                      </select>
                      <input className={input} placeholder={editing.outcome === 'inappropriate' ? 'What was wrong (required)' : 'Note (optional)'}
                             value={editing.note} onChange={(e) => setEditing({ ...editing, note: e.target.value })} />
                      <div className="flex gap-1.5">
                        <button type="button" className={button} disabled={busy} onClick={save}>Save</button>
                        <button type="button" className={ghost} onClick={() => setEditing(null)}>Cancel</button>
                      </div>
                    </div>
                  ) : (
                    <button type="button" className={ghost}
                            onClick={() => setEditing({ id: g.id, outcome: 'appropriate', note: '' })}>Review</button>
                  )}
                </td>
              </tr>
            ))}
            {data && !data.items.length && (
              <tr><td colSpan={isSuper ? 7 : 6} className="py-6 text-center text-gray-400">
                {status === 'to_review' ? 'Nothing to review.' : 'No emergency access yet.'}
              </td></tr>
            )}
          </tbody>
        </table>
      </div>
    </details>
  );
}

const APP_LABELS = { readmissions: 'Readmissions', glp1: 'GLP-1' };

// Who opened which patient's clinical details, and why - in both apps. A
// hospital admin sees its own hospital's patients (including views by our team
// and by insurers); the superadmin sees every hospital. Read-only: the server
// writes an entry each time someone opens a patient (api/access_log.py).
function AccessLog({ request, isSuper, hospitals, hospitalName }) {
  const [entries, setEntries] = useState(null);
  const [error, setError] = useState(null);
  const [app, setApp] = useState('');
  const [hospitalId, setHospitalId] = useState('');

  useEffect(() => {
    let current = true;
    const qs = new URLSearchParams(Object.entries({ app, hospital_id: hospitalId }).filter(([, v]) => v));
    request(`/auth/admin/access-log${qs.toString() ? `?${qs}` : ''}`)
      .then((rows) => { if (current) { setEntries(rows); setError(null); } })
      .catch((e) => { if (current) setError(e.message); });
    return () => { current = false; };
  }, [request, app, hospitalId]);

  return (
    <details className={panel}>
      <summary className="cursor-pointer font-semibold text-gray-800">
        Access log{entries ? ` (${entries.length}${entries.length >= 200 ? '+' : ''})` : ''}
      </summary>
      <p className="mt-2 text-xs text-gray-500">
        Every time someone opens a patient&apos;s clinical details, once per patient per
        sign-in, with the reason. Hospital admins and insurers give a reason; for doctors and
        nurses it is &ldquo;Treatment&rdquo;, for case managers &ldquo;Care coordination&rdquo;,
        and &ldquo;Emergency access&rdquo; when they broke the glass.
      </p>
      <div className="mt-3 flex flex-wrap gap-2">
        <select className={inputInline} value={app} onChange={(e) => setApp(e.target.value)}>
          <option value="">Both apps</option>
          <option value="readmissions">Readmissions</option>
          <option value="glp1">GLP-1</option>
        </select>
        {isSuper && (
          <select className={inputInline} value={hospitalId} onChange={(e) => setHospitalId(e.target.value)}>
            <option value="">All hospitals</option>
            {hospitals.map((h) => <option key={h.id} value={h.id}>{h.name}</option>)}
          </select>
        )}
      </div>
      {error && <div className="mt-3 rounded-md bg-red-50 px-3 py-2 text-sm text-red-700">{error}</div>}
      <div className="mt-3 overflow-x-auto">
        <table className="w-full text-left text-sm">
          <thead className="border-b border-gray-200 text-xs uppercase text-gray-500">
            <tr><th className="py-2 pr-3">When</th><th className="py-2 pr-3">Who</th>
                <th className="py-2 pr-3">App</th><th className="py-2 pr-3">Patient</th>
                {isSuper && <th className="py-2 pr-3">Hospital</th>}
                <th className="py-2">Reason</th></tr>
          </thead>
          <tbody>
            {(entries || []).map((e, i) => (
              <tr key={`${e.at}-${e.user_id}-${e.patient_id}-${i}`} className="border-b border-gray-100">
                <td className="py-2 pr-3 whitespace-nowrap text-xs text-gray-500">
                  {e.at ? new Date(e.at).toLocaleString() : '—'}
                </td>
                <td className="py-2 pr-3">
                  <div className="text-gray-800">{e.email}</div>
                  <div className="text-xs text-gray-500">{ROLE_LABELS[e.role] || e.role}</div>
                </td>
                <td className="py-2 pr-3 text-gray-700">{APP_LABELS[e.app] || e.app}</td>
                <td className="py-2 pr-3 font-mono text-xs text-gray-700">{e.patient_id}</td>
                {isSuper && <td className="py-2 pr-3 text-gray-700">{hospitalName(e.hospital_id)}</td>}
                <td className="py-2 text-gray-700">{e.reason_label}</td>
              </tr>
            ))}
            {entries && !entries.length && (
              <tr><td colSpan={isSuper ? 6 : 5} className="py-6 text-center text-gray-400">Nobody has opened a patient&apos;s details yet.</td></tr>
            )}
          </tbody>
        </table>
      </div>
    </details>
  );
}

// A self-signup nobody has approved yet: pending, attached to nothing.
const isSignup = (user) => user.status === 'pending' && !user.hospital_id && !user.insurer_id
  && !user.pharmacy_id;

// Can this signed-in manager change this row? The same rules as the server's
// _may_manage, so the controls shown are the ones that will work.
function canManage(user, me, myId, isSuper) {
  if (user.role === 'superadmin' || user.sub === myId) return false;
  if (isSuper) return true;
  if (!ASSIGNABLE.hospital_admin.includes(user.role)) return false;
  return user.hospital_id === me?.hospital_id
    || (isSignup(user) && user.requested_hospital_id === me?.hospital_id);
}

function UserRow({ user, me, myId, isSuper, assignable, hospitals, insurers, pharmacies = [], hospitalName, busy,
                  onSave, onDecline }) {
  const [role, setRole] = useState(user.role);
  // A sign-up starts on the hospital it asked to join, so approving it is one click.
  const startingHospital = user.hospital_id || user.requested_hospital_id || '';
  const [hospitalId, setHospitalId] = useState(startingHospital);
  const [insurerId, setInsurerId] = useState(user.insurer_id || '');
  const [pharmacyId, setPharmacyId] = useState(user.pharmacy_id || '');
  const editable = canManage(user, me, myId, isSuper);
  const changed = role !== user.role || (hospitalId || null) !== (startingHospital || null)
                  || (insurerId || null) !== (user.insurer_id || null)
                  || (pharmacyId || null) !== (user.pharmacy_id || null);
  const placement = () => (HOSPITAL_ROLES.includes(role) ? { hospital_id: hospitalId || null }
                           : role === 'insurer' ? { insurer_id: insurerId || null }
                           : role === 'pharmacy' ? { pharmacy_id: pharmacyId || null } : {});

  return (
    <tr className="border-b border-gray-100 align-top">
      <td className="py-2 pr-3">
        <div className="font-medium text-gray-800">{user.email}</div>
        <div className="text-xs text-gray-500">
          {user.name || '—'}{user.must_change_password && ' · must set a password'}
        </div>
        {/* A sign-up names the role it wants; the admin confirms or changes it
            with the role picker before approving. */}
        {isSignup(user) && user.requested_role && (
          <div className="mt-0.5 text-xs font-medium text-amber-700">
            Signed up as {ROLE_LABELS[user.requested_role] || user.requested_role}
            {user.requested_hospital_id && <> for {hospitalName(user.requested_hospital_id)}</>}
          </div>
        )}
        {isSignup(user) && user.declined && isSuper && (
          <div className="mt-0.5 text-xs text-gray-500">
            Request to join {hospitalName(user.declined.hospital_id)} declined by {user.declined.by}
          </div>
        )}
      </td>
      <td className="py-2 pr-3">
        {editable ? (
          <select className={input} value={role} onChange={(e) => setRole(e.target.value)}>
            {[...new Set([user.role, ...assignable])].map((r) => (
              <option key={r} value={r}>{ROLE_LABELS[r] || r}</option>))}
          </select>
        ) : ROLE_LABELS[user.role] || user.role}
      </td>
      {isSuper && (<>
        <td className="py-2 pr-3">
          {editable && HOSPITAL_ROLES.includes(role) ? (
            <select className={input} value={hospitalId} onChange={(e) => setHospitalId(e.target.value)}>
              <option value="">— choose —</option>
              {hospitals.map((h) => <option key={h.id} value={h.id}>{h.name}</option>)}
            </select>
          ) : isSignup(user) && user.requested_hospital_id ? (
            <span className="text-amber-700">{hospitalName(user.requested_hospital_id)} (requested)</span>
          ) : user.hospital_id ? hospitalName(user.hospital_id) : <span className="text-gray-400">—</span>}
        </td>
        <td className="py-2 pr-3">
          {editable && role === 'insurer' ? (
            <select className={input} value={insurerId} onChange={(e) => setInsurerId(e.target.value)}>
              <option value="">— choose —</option>
              {insurers.map((i) => <option key={i.id} value={i.id}>{i.name}</option>)}
            </select>
          ) : editable && role === 'pharmacy' ? (
            // Pharmacy staff: the pharmacy they work at, which decides whose
            // patients they see (the ones who fill there).
            <select className={input} value={pharmacyId} onChange={(e) => setPharmacyId(e.target.value)}>
              <option value="">— choose —</option>
              {pharmacies.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
            </select>
          ) : user.pharmacy_id ? (pharmacies.find((p) => p.id === user.pharmacy_id)?.name || user.pharmacy_id)
          : user.insurer_id || user.patient_insurer_id
            // Insurer staff: the company they work for. A patient: the insurance
            // on their patient record (looked up live by the auth service).
            ? (insurers.find((i) => i.id === (user.insurer_id || user.patient_insurer_id))?.name
               || user.insurer_id || user.patient_insurer_id)
            : <span className="text-gray-400">—</span>}
        </td>
      </>)}
      <td className="py-2 pr-3">
        <span className={`rounded-full px-2 py-0.5 text-xs font-medium ${
          user.status === 'pending' ? 'bg-amber-100 text-amber-800' : 'bg-green-100 text-green-800'}`}>
          {user.status}
        </span>
      </td>
      <td className="py-2 pr-3 text-xs text-gray-500">{(user.created_at || '').slice(0, 10) || '—'}</td>
      <td className="py-2 text-right whitespace-nowrap">
        {editable && (
          <div className="flex justify-end gap-1.5">
            {changed && (
              <button className={ghost} disabled={busy}
                      onClick={() => onSave({ role, ...placement() })}>Save</button>
            )}
            {user.status === 'pending' ? (<>
              <button className={ghost} disabled={busy}
                      onClick={() => onSave({ status: 'active', role, ...placement() })}>Approve</button>
              {isSignup(user) && user.requested_hospital_id && (
                <button className={ghost} disabled={busy} onClick={onDecline}
                        title="Take this request off the hospital's list. The account stays pending.">Decline</button>
              )}
            </>) : (
              <button className={ghost} disabled={busy}
                      onClick={() => onSave({ status: 'pending' })}>Suspend</button>
            )}
          </div>
        )}
      </td>
    </tr>
  );
}

function OrgPanel({ title, items, busy, onCreate }) {
  const [name, setName] = useState('');
  return (
    <div className={panel}>
      <h3 className="mb-2 font-semibold text-gray-800">{title} ({items.length})</h3>
      <form className="mb-3 flex gap-2"
            onSubmit={(e) => { e.preventDefault(); if (name.trim()) { onCreate(name.trim()); setName(''); } }}>
        <input className={input}
               placeholder={`New ${title.toLowerCase().replace(/ies$/, 'y').replace(/s$/, '')} name`}
               value={name} onChange={(e) => setName(e.target.value)} />
        <button className={button} disabled={busy || !name.trim()}>Add</button>
      </form>
      <ul className="max-h-40 space-y-1 overflow-y-auto text-sm text-gray-700">
        {items.map((o) => <li key={o.id}>{o.name} <span className="text-xs text-gray-400">{o.id}</span></li>)}
      </ul>
    </div>
  );
}

function AddUser({ assignable, isSuper, hospitals, insurers, pharmacies = [], busy, onAdd }) {
  const [form, setForm] = useState({ email: '', name: '', role: assignable[0] || '',
                                     hospital_id: '', insurer_id: '', pharmacy_id: '' });
  const set = (k) => (e) => setForm({ ...form, [k]: e.target.value });
  return (
    <div className={panel}>
      <h3 className="mb-1 font-semibold text-gray-800">Add a person</h3>
      <p className="mb-3 text-xs text-gray-500">
        They get a temporary password to change at first sign-in. If they already
        signed up and are waiting for approval, this approves their account instead.
      </p>
      <form className="grid gap-2 md:grid-cols-5"
            onSubmit={(e) => {
              e.preventDefault();
              onAdd({ email: form.email, name: form.name, role: form.role,
                      hospital_id: isSuper && HOSPITAL_ROLES.includes(form.role) ? form.hospital_id || null : null,
                      insurer_id: form.role === 'insurer' ? form.insurer_id || null : null,
                      pharmacy_id: form.role === 'pharmacy' ? form.pharmacy_id || null : null });
              setForm({ ...form, email: '', name: '' });
            }}>
        <input className={input} type="email" required placeholder="Email" value={form.email} onChange={set('email')} />
        <input className={input} placeholder="Name" value={form.name} onChange={set('name')} />
        <select className={input} value={form.role} onChange={set('role')}>
          {assignable.map((r) => <option key={r} value={r}>{ROLE_LABELS[r]}</option>)}
        </select>
        {isSuper && HOSPITAL_ROLES.includes(form.role) ? (
          <select className={input} required value={form.hospital_id} onChange={set('hospital_id')}>
            <option value="">Hospital…</option>
            {hospitals.map((h) => <option key={h.id} value={h.id}>{h.name}</option>)}
          </select>
        ) : form.role === 'insurer' ? (
          <select className={input} required value={form.insurer_id} onChange={set('insurer_id')}>
            <option value="">Insurer…</option>
            {insurers.map((i) => <option key={i.id} value={i.id}>{i.name}</option>)}
          </select>
        ) : form.role === 'pharmacy' ? (
          <select className={input} required value={form.pharmacy_id} onChange={set('pharmacy_id')}>
            <option value="">Pharmacy…</option>
            {pharmacies.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
          </select>
        ) : <div />}
        <button className={button} disabled={busy}>Add</button>
      </form>
    </div>
  );
}

function ImportCsv({ isSuper, hospitals, insurers, pharmacies = [], busy, onImport }) {
  const [csv, setCsv] = useState('');
  const [hospitalId, setHospitalId] = useState('');
  const [insurerId, setInsurerId] = useState('');
  const [pharmacyId, setPharmacyId] = useState('');
  const readFile = (e) => {
    const file = e.target.files?.[0];
    if (file) file.text().then(setCsv);
  };
  return (
    <details className={panel}>
      <summary className="cursor-pointer font-semibold text-gray-800">Import staff from CSV</summary>
      <p className="mt-2 text-xs text-gray-500">
        Header row required: <code>email,name,role</code>. Roles use the names above
        (for example <code>doctor</code>, <code>nurse</code>). Each row succeeds or fails on its own.
      </p>
      <div className="mt-3 space-y-2">
        <input type="file" accept=".csv,text/csv" onChange={readFile} className="text-sm" />
        <textarea className={`${input} h-28 font-mono text-xs`} value={csv} onChange={(e) => setCsv(e.target.value)}
                  placeholder={'email,name,role\ndoc@hospital.org,Dr Jane Smith,doctor'} />
        {isSuper && (
          <div className="grid gap-2 md:grid-cols-3">
            <select className={input} value={hospitalId} onChange={(e) => setHospitalId(e.target.value)}>
              <option value="">Hospital for hospital roles…</option>
              {hospitals.map((h) => <option key={h.id} value={h.id}>{h.name}</option>)}
            </select>
            <select className={input} value={insurerId} onChange={(e) => setInsurerId(e.target.value)}>
              <option value="">Insurer for insurer rows…</option>
              {insurers.map((i) => <option key={i.id} value={i.id}>{i.name}</option>)}
            </select>
            <select className={input} value={pharmacyId} onChange={(e) => setPharmacyId(e.target.value)}>
              <option value="">Pharmacy for pharmacy rows…</option>
              {pharmacies.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
            </select>
          </div>
        )}
        <button className={button} disabled={busy || !csv.trim()}
                onClick={() => onImport({ csv, hospital_id: hospitalId || null, insurer_id: insurerId || null,
                                          pharmacy_id: pharmacyId || null })}>
          Import
        </button>
      </div>
    </details>
  );
}

// Temporary passwords are shown once, here, and never again - the server keeps
// only a hash. The admin hands them over; the user must change theirs at first
// sign-in.
function Notice({ notice, onClose }) {
  const created = notice.kind === 'import' ? notice.created : notice.results;
  const failed = notice.kind === 'import' ? notice.failed : [];
  return (
    <div className="rounded-xl border border-blue-200 bg-blue-50 p-4 text-sm text-gray-800">
      <div className="mb-2 flex items-start justify-between gap-3">
        <b>{notice.kind === 'import' ? `Imported ${created.length}, ${failed.length} failed` : 'Done'}</b>
        <button className={ghost} onClick={onClose}>Dismiss</button>
      </div>
      {created.some((c) => c.temporary_password) && (
        <p className="mb-2 text-xs text-gray-600">
          Temporary passwords are shown only now. Share each one privately; the person
          must change it the first time they sign in.
        </p>
      )}
      <ul className="space-y-1">
        {created.map((c) => (
          <li key={c.user.sub}>
            {c.user.email} — {c.approved_existing
              ? 'existing account approved (they keep their own password)'
              : <>temporary password <code className="rounded bg-white px-1.5 py-0.5 font-mono">{c.temporary_password}</code></>}
          </li>
        ))}
        {failed.map((f) => (
          <li key={`${f.line}-${f.email}`} className="text-red-700">
            Line {f.line} ({f.email || 'no email'}): {f.error}
          </li>
        ))}
      </ul>
    </div>
  );
}
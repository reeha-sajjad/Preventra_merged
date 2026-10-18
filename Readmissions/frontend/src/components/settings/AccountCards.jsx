import React, { useEffect, useState } from 'react';
import { UserCircle, KeyRound, Loader2, CheckCircle2, Eye, EyeOff } from 'lucide-react';
import { changePassword, getAccount } from '../../api';
import { replaceToken } from '../../api/auth';
import { ROLE_LABELS } from '../../roles';

// The same two cards as GLP-1's Settings (GLP1/Frontend/src/components/settings/
// AccountCards.jsx), restyled for this app. Accounts and passwords are shared,
// so the wording matches too.

const MIN_LENGTH = 8;          // api/auth.py MIN_PASSWORD_LENGTH

function CardHeader({ icon, title, sub }) {
  return (
    <div className="mb-5 flex items-center gap-3">
      <div className="flex h-9 w-9 items-center justify-center rounded-lg bg-ns-navy/10 text-ns-navy">
        {icon}
      </div>
      <div>
        <h2 className="font-semibold text-gray-800">{title}</h2>
        <p className="text-xs text-gray-500">{sub}</p>
      </div>
    </div>
  );
}

/** Who you are here: name, email, role, hospital, how many patients you can see. */
export function AccountCard() {
  const [me, setMe] = useState(null);
  const [error, setError] = useState(null);

  useEffect(() => {
    let live = true;
    getAccount().then((d) => { if (live) setMe(d); }).catch((e) => { if (live) setError(e.message); });
    return () => { live = false; };
  }, []);

  const rows = me ? [
    ['Name', me.name || '—'],
    ['Email', me.email],
    ['Role', ROLE_LABELS[me.role] || me.role],
    me.hospital && ['Hospital', me.hospital.name],
    me.insurer && ['Organisation', me.insurer.name],
    me.pharmacy && ['Pharmacy', me.pharmacy.name],
    // A patient sees their own record; "1 patient" would only confuse.
    me.role !== 'patient' && ['Patients you can see', me.patients === null ? 'Every patient' : me.patients.toLocaleString()],
  ].filter(Boolean) : [];

  return (
    <section className="rounded-xl border border-gray-200 bg-white p-6">
      <CardHeader icon={<UserCircle size={17} />} title="Your account"
                  sub="Set by your administrator. Ask them if anything here is wrong." />
      {!me && !error && <div className="h-28 animate-pulse rounded-lg bg-gray-100" />}
      {error && <p className="text-sm text-gray-500">Your account details could not be loaded.</p>}
      {me && (
        <dl className="divide-y divide-gray-100">
          {rows.map(([k, v]) => (
            <div key={k} className="grid gap-1 py-2.5 sm:grid-cols-[180px_1fr] sm:gap-4">
              <dt className="text-xs font-semibold uppercase tracking-wide text-gray-500">{k}</dt>
              <dd className="text-sm text-gray-800">{v}</dd>
            </div>
          ))}
        </dl>
      )}
    </section>
  );
}

/** Change your own password at the shared sign-in service (it applies to both apps). */
export function PasswordCard() {
  const [form, setForm] = useState({ current: '', next: '', again: '' });
  const [shown, setShown] = useState({});
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const [done, setDone] = useState(false);

  const set = (k) => (e) => { setForm({ ...form, [k]: e.target.value }); setDone(false); setError(null); };

  const save = async (e) => {
    e.preventDefault();
    if (form.next.length < MIN_LENGTH) { setError(`The new password must be at least ${MIN_LENGTH} characters.`); return; }
    if (form.next !== form.again) { setError('The two new passwords do not match.'); return; }
    if (form.next === form.current) { setError('Choose a password different from the current one.'); return; }
    setBusy(true); setError(null);
    try {
      const data = await changePassword(form.current, form.next);
      if (data?.token) replaceToken(data.token);
      setForm({ current: '', next: '', again: '' });
      setDone(true);
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  };

  // Each field has its own show/hide eye. The eye sits outside the <label>,
  // so the field's accessible name stays just its label.
  const input = (k, label, autoComplete) => (
    <div>
      <label htmlFor={`pw-${k}`} className="text-xs font-semibold uppercase tracking-wide text-gray-500">{label}</label>
      <div className="relative mt-1.5">
        <input id={`pw-${k}`} type={shown[k] ? 'text' : 'password'} value={form[k]} onChange={set(k)}
          autoComplete={autoComplete} required
          className="w-full rounded-md border border-gray-300 py-2 pl-3 pr-10 text-sm focus:border-ns-navy focus:outline-none focus:ring-1 focus:ring-ns-navy" />
        <button type="button" onClick={() => setShown((s) => ({ ...s, [k]: !s[k] }))}
          aria-label={`${shown[k] ? 'Hide' : 'Show'} ${label.toLowerCase()}`}
          className="absolute inset-y-0 right-0 flex w-10 items-center justify-center rounded-r-md text-gray-400 hover:text-gray-600">
          {shown[k] ? <EyeOff size={15} /> : <Eye size={15} />}
        </button>
      </div>
    </div>
  );

  return (
    <section className="rounded-xl border border-gray-200 bg-white p-6">
      <CardHeader icon={<KeyRound size={17} />} title="Password"
                  sub="One password for GLP-1 and Readmissions. At least 8 characters." />
      <form onSubmit={save} className="max-w-md space-y-4">
        {input('current', 'Current password', 'current-password')}
        {input('next', 'New password', 'new-password')}
        {input('again', 'Re-type new password', 'new-password')}
        {error && <p className="rounded-md border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700">{error}</p>}
        {done && (
          <p className="flex items-center gap-2 rounded-md border border-green-200 bg-green-50 px-3 py-2 text-sm text-green-800">
            <CheckCircle2 size={15} /> Password changed. Use the new one next time you sign in.
          </p>
        )}
        <button type="submit" disabled={busy}
          className="!mt-5 inline-flex items-center gap-2 rounded-md bg-ns-navy px-4 py-2 text-sm font-semibold text-white hover:bg-ns-navy/90 disabled:opacity-50">
          {busy && <Loader2 size={15} className="animate-spin" />} Change password
        </button>
      </form>
    </section>
  );
}
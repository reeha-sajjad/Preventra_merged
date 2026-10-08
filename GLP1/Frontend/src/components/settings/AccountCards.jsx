import { useEffect, useState } from 'react';
import { UserCircle, KeyRound, Loader2, CheckCircle2, Eye, EyeOff } from 'lucide-react';
import { api } from '../../data/api';
import { useAuth } from '../../context/AuthContext';
import { useRole } from '../../context/RoleContext';

const MIN_LENGTH = 8;          // Readmissions/api/auth.py MIN_PASSWORD_LENGTH

function CardHeader({ icon: Icon, title, sub }) {
  return (
    <div className="flex items-center gap-3 mb-5">
      <div className="w-9 h-9 rounded-xl flex items-center justify-center" style={{ background: '#EBF4FF' }}>
        <Icon size={17} style={{ color: 'var(--color-primary)' }} />
      </div>
      <div>
        <div className="font-semibold text-gray-800">{title}</div>
        <div className="text-xs text-gray-400">{sub}</div>
      </div>
    </div>
  );
}

/** Who you are here: name, email, role, hospital, how many patients you can see. */
export function AccountCard() {
  const { roleLabel } = useRole();
  const [me, setMe] = useState(null);
  const [error, setError] = useState(null);

  useEffect(() => {
    let live = true;
    api.getMe().then((d) => { if (live) setMe(d); }).catch((e) => { if (live) setError(e.message); });
    return () => { live = false; };
  }, []);

  const rows = me ? [
    ['Name', me.name || '—'],
    ['Email', me.email],
    ['Role', roleLabel],
    me.hospital && ['Hospital', me.hospital.name],
    me.insurer && ['Organisation', me.insurer.name],
    ['Patients you can see', me.patients === null ? 'Every patient' : me.patients.toLocaleString()],
  ].filter(Boolean) : [];

  return (
    <div className="card p-6">
      <CardHeader icon={UserCircle} title="Your account"
                  sub="Set by your administrator. Ask them if anything here is wrong." />
      {!me && !error && <div className="h-28 rounded-lg bg-gray-100 animate-pulse" />}
      {error && <p className="text-sm text-gray-500">Your account details could not be loaded.</p>}
      {me && (
        <dl className="divide-y divide-gray-100">
          {rows.map(([k, v]) => (
            <div key={k} className="grid gap-1 py-2.5 sm:grid-cols-[180px_1fr] sm:gap-4">
              <dt className="text-xs font-semibold uppercase tracking-wider text-gray-400">{k}</dt>
              <dd className="text-sm text-gray-700">{v}</dd>
            </div>
          ))}
        </dl>
      )}
    </div>
  );
}

/** Change your own password at the shared sign-in service (it applies to both apps). */
export function PasswordCard() {
  const { token, replaceToken } = useAuth();
  const [form, setForm] = useState({ current: '', next: '', again: '' });
  const [show, setShow] = useState(false);
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
      const data = await api.changePassword(token, form.current, form.next);
      if (data?.token) replaceToken(data.token);
      setForm({ current: '', next: '', again: '' });
      setDone(true);
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  };

  const input = (k, label, autoComplete) => (
    <label className="block">
      <span className="text-xs font-semibold uppercase tracking-wider text-gray-400">{label}</span>
      <input type={show ? 'text' : 'password'} value={form[k]} onChange={set(k)} autoComplete={autoComplete} required
        className="mt-1.5 w-full text-sm rounded-lg border border-gray-200 px-3 py-2 focus:outline-none focus:ring-1 focus:ring-blue-400" />
    </label>
  );

  return (
    <div className="card p-6">
      <CardHeader icon={KeyRound} title="Password"
                  sub="One password for GLP-1 and Readmissions. At least 8 characters." />
      <form onSubmit={save} className="space-y-4 max-w-md">
        {input('current', 'Current password', 'current-password')}
        {input('next', 'New password', 'new-password')}
        {input('again', 'New password again', 'new-password')}
        <button type="button" onClick={() => setShow((s) => !s)}
          className="inline-flex items-center gap-1.5 text-xs font-medium text-gray-500 hover:text-gray-700">
          {show ? <EyeOff size={13} /> : <Eye size={13} />} {show ? 'Hide' : 'Show'} passwords
        </button>
        {error && <p className="rounded-md px-3 py-2 text-sm" style={{ background: '#FFEBEE', color: '#C62828' }}>{error}</p>}
        {done && (
          <p className="flex items-center gap-2 rounded-md px-3 py-2 text-sm" style={{ background: '#E8F5E9', color: '#2E7D32' }}>
            <CheckCircle2 size={15} /> Password changed. Use the new one next time you sign in.
          </p>
        )}
        <button type="submit" disabled={busy}
          className="inline-flex items-center gap-2 text-sm font-semibold px-4 py-2 rounded-lg text-white disabled:opacity-50"
          style={{ background: 'var(--color-primary)' }}>
          {busy && <Loader2 size={15} className="animate-spin" />} Change password
        </button>
      </form>
    </div>
  );
}
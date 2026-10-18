import React from 'react';
import { ArrowLeftRight, ExternalLink, LayoutGrid, LogOut } from 'lucide-react';
import { getToken, readClaims, signOut } from '../api/auth';

// Moving between the two products without signing in again.
//
// The same hand-off the portal uses: a full navigation with the token in the
// URL *fragment*. A fragment is never sent to the server, so the token stays
// out of access logs, proxy logs and Referer headers. The receiving app reads
// it on load and strips it from the address bar.
//
// Nothing here grants access. `app_access` decides what to *show*; both
// backends check the signature themselves and will refuse a token that was not
// issued for them. Hiding a tile is a courtesy, not a control.
const GLP1_URL = (import.meta.env.VITE_GLP1_URL || '').replace(/\/$/, '');

// Display names for the fixed roles in api/auth.py:ROLES on the backend.
const ROLE_LABELS = {
  superadmin: 'Superadmin', hospital_admin: 'Hospital admin', doctor: 'Doctor',
  nurse: 'Nurse', case_manager: 'Case manager', insurer: 'Insurer', pharmacy: 'Pharmacy',
  patient: 'Patient',
};

export default function AppSwitcher({ onNavigate = () => {} }) {
  const token = getToken();
  const claims = readClaims();

  // Opened directly rather than through the portal: there is no token to carry,
  // so sending someone to the other app would only land them on its login page.
  if (!token || !claims) return null;

  const granted = Array.isArray(claims.app_access) ? claims.app_access : [];
  // No early return for accounts without GLP-1: they still need the account
  // line and Sign out below, which live in this same panel.
  const hasGlp1 = granted.includes('glp1');

  const go = (base) => {
    onNavigate();
    window.location.href = `${base}/#token=${encodeURIComponent(token)}`;
  };

  return (
    <div className="border-t border-gray-700 p-4">
      {hasGlp1 && (
        <div className="mb-2 flex items-center gap-2 text-xs font-semibold uppercase tracking-wide text-gray-400">
          <ArrowLeftRight size={14} />
          Switch app
        </div>
      )}

      {hasGlp1 && (
        <button
          type="button"
          onClick={() => GLP1_URL && go(GLP1_URL)}
          disabled={!GLP1_URL}
          // Disabled rather than hidden when the URL is not configured: hiding it
          // looks identical to the account not having GLP-1 access, which is a
          // different problem and would be investigated in the wrong place.
          title={GLP1_URL ? `Open GLP-1 Analytics at ${GLP1_URL}` : 'VITE_GLP1_URL is not set'}
          className={`flex w-full items-center justify-between rounded-md px-3 py-2 text-left text-sm transition-colors ${
            GLP1_URL
              ? 'bg-gray-800 text-white hover:bg-gray-700'
              : 'cursor-not-allowed bg-gray-800/50 text-gray-500'
          }`}
        >
          <span className="flex items-center gap-2">
            <LayoutGrid size={16} />
            GLP-1 Analytics
          </span>
          {GLP1_URL
            ? <ExternalLink size={14} className="shrink-0 opacity-70" />
            : <span className="text-[10px] uppercase">not set</span>}
        </button>
      )}

      {claims.email && (
        <div className="mt-3 truncate text-xs text-gray-400" title={claims.email}>
          {claims.email}
          {ROLE_LABELS[claims.role] && <span className="text-gray-500"> · {ROLE_LABELS[claims.role]}</span>}
        </div>
      )}

      <button
        type="button"
        onClick={signOut}
        className="mt-2 flex w-full items-center gap-2 rounded-md px-3 py-2 text-left text-sm text-gray-400 transition-colors hover:bg-gray-800 hover:text-white"
      >
        <LogOut size={16} />
        Sign out
      </button>
    </div>
  );
}
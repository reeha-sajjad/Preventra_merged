import React from 'react';
import { API_BASE_URL } from '../api';
import { getToken, readClaims } from '../api/auth';
import UserManagement from '../components/shared/UserManagement';
import { AccountCard, PasswordCard } from '../components/settings/AccountCards';

// Everyone's own account and password first - the same as GLP-1's Settings.
// Below that, for hospital admins and the superadmin, User Management: accounts
// are shared by both apps, and GLP-1 shows the same component.
export default function Settings() {
  const me = readClaims();
  const isManager = me?.role === 'superadmin' || me?.role === 'hospital_admin';

  return (
    <div className="mx-auto max-w-6xl space-y-6 p-4 md:p-6">
      <div className="grid gap-6 lg:grid-cols-2">
        <AccountCard />
        <PasswordCard />
      </div>
      {isManager && (
        <UserManagement authBaseUrl={API_BASE_URL} getToken={getToken} me={me} />
      )}
    </div>
  );
}
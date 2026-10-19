import React from 'react';
import { useLocation } from 'react-router-dom';
import { Menu } from 'lucide-react';
import AlertsBell from './AlertsBell';
import HospitalPicker from './HospitalPicker';
import { readClaims } from '../api/auth';
import { can, MY_PATIENTS_ROLES, PHARMACY_ROLES } from '../roles';

export default function TopNav({ onMenuClick = () => {} }) {
  const location = useLocation();

  const getPageTitle = (path) => {
    if (path === '/') return readClaims()?.role === 'patient' ? 'My health'
      : can(PHARMACY_ROLES) ? 'Pharmacy' : can(MY_PATIENTS_ROLES) ? 'My patients' : 'Overview';
    if (path === '/patients') return 'Patients';
    if (path.startsWith('/patients/')) return 'Patient Detail';
    if (path === '/staff') return 'Staff';
    if (path === '/follow-ups') return 'Follow-ups';
    if (path === '/settings') return 'Settings';
    if (path === '/analytics') return 'Analytics';
    if (path === '/doctor') return 'Clinician Console';
    if (path === '/upload') return 'Pipeline Upload';
    if (path === '/about') return 'About Preventra';
    return 'Preventra';
  };

  const title = getPageTitle(location.pathname);
  const now = new Date();

  return (
    <header className="flex shrink-0 items-center justify-between gap-3 border-b border-gray-200 bg-white px-4 py-3 sm:px-6 sm:py-4">
      <div className="flex min-w-0 items-center gap-2">
        <button
          onClick={onMenuClick}
          aria-label="Open menu"
          className="-ml-1 rounded-md p-2 text-gray-600 hover:bg-gray-100 hover:text-gray-900 md:hidden"
        >
          <Menu size={22} />
        </button>
        <h2 className="truncate text-lg font-semibold text-gray-800 sm:text-xl">{title}</h2>
      </div>

      <div className="flex shrink-0 items-center gap-2 sm:gap-4">
        <HospitalPicker />
        <AlertsBell />
        {/* The full timestamp wraps to three lines on a phone and pushed the
            header to 133px tall. Narrow screens get the time only; the date is
            the same day in every realistic use of this screen. */}
        <div className="text-sm font-medium text-gray-500">
          <span className="hidden lg:inline">Last updated: </span>
          <span className="hidden sm:inline">{now.toLocaleString()}</span>
          <span className="sm:hidden">
            {now.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}
          </span>
        </div>
      </div>
    </header>
  );
}
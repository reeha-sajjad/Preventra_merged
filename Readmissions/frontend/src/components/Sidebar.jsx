import React, { useEffect } from 'react';
import { NavLink, useLocation } from 'react-router-dom';
import { LayoutDashboard, BarChart, UserPlus, Info, Stethoscope, Users, UsersRound, ClipboardList, X,
         FlaskConical } from 'lucide-react';
import { MANUAL_ENTRY_ENABLED } from '../api';
import { readClaims } from '../api/auth';
import { can, OVERVIEW_ROLES, STAFF_ROLES, CONSOLE_ROLES, MANAGER_ROLES, STUDIO_ROLES,
         ROLE_LABELS } from '../roles';
import AppSwitcher from './AppSwitcher';

// Below `md` the sidebar is an off-canvas drawer; at `md` and above it is the
// static column it has always been. One component rather than two, so a nav
// item cannot be added to the desktop menu and forgotten on mobile.
export default function Sidebar({ open = false, onClose = () => {} }) {
  const location = useLocation();

  // Navigating closes the drawer. Without this the menu stays open on top of
  // the page the user just asked for.
  useEffect(() => { onClose(); }, [location.pathname]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    if (!open) return undefined;
    const onKey = (e) => e.key === 'Escape' && onClose();
    document.addEventListener('keydown', onKey);
    // Stop the page behind the drawer scrolling under the user's finger.
    const previous = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    return () => {
      document.removeEventListener('keydown', onKey);
      document.body.style.overflow = previous;
    };
  }, [open, onClose]);

  const role = readClaims()?.role;
  const isPatient = role === 'patient';
  // Each role's pages - hidden, not dimmed, for everyone else. The server
  // refuses them anyway; this keeps the menu to what the user can use.
  // Doctors and nurses have no Overview: their Patients list is their home.
  const navItems = isPatient ? [
    { name: 'My record', path: '/my-record', icon: <Info size={20} /> },
  ] : [
    ...(can(OVERVIEW_ROLES) ? [{ name: 'Overview', path: '/', icon: <LayoutDashboard size={20} /> }] : []),
    { name: 'Patients', path: '/patients', icon: <ClipboardList size={20} /> },
    ...(can(STAFF_ROLES) ? [{ name: 'Staff', path: '/staff', icon: <UsersRound size={20} /> }] : []),
    ...(MANUAL_ENTRY_ENABLED
      ? [{ name: 'Manual Entry', path: '/manual-entry', icon: <UserPlus size={20} /> }]
      : []),
    { name: 'Analytics', path: '/analytics', icon: <BarChart size={20} /> },
    ...(can(STUDIO_ROLES) ? [{ name: 'Model Studio', path: '/model-studio', icon: <FlaskConical size={20} /> }] : []),
    ...(can(CONSOLE_ROLES) ? [{ name: 'Clinician Console', path: '/doctor', icon: <Stethoscope size={20} /> }] : []),
    { name: 'About Preventra', path: '/about', icon: <Info size={20} /> },
    ...(can(MANAGER_ROLES) ? [{ name: 'User Management', path: '/settings', icon: <Users size={20} /> }] : []),
  ];

  return (
    <>
      {/* Backdrop, mobile only. Tapping it closes the drawer. */}
      <div
        onClick={onClose}
        aria-hidden="true"
        className={`fixed inset-0 z-40 bg-black/50 transition-opacity md:hidden ${
          open ? 'opacity-100' : 'pointer-events-none opacity-0'
        }`}
      />

      <div
        className={`fixed inset-y-0 left-0 z-50 flex h-full w-64 shrink-0 flex-col border-r border-gray-800 bg-ns-navy text-white transition-transform duration-200 ease-out md:static md:translate-x-0 ${
          open ? 'translate-x-0' : '-translate-x-full'
        }`}
      >
        <div className="flex items-center justify-between border-b border-gray-700 p-6">
          <div className="flex items-center space-x-2 text-xl font-bold">
            {/* Shield only, not the full lockup: the logo stacks the mark above
                the wordmark, which collapses into a smudge at this height. The
                word is already set in text beside it. */}
            <img src="/logo-mark.png" alt="" className="h-7 w-auto" />
            <span>Preventra</span>
          </div>
          <button
            onClick={onClose}
            aria-label="Close menu"
            className="-mr-2 rounded-md p-2 text-white/70 hover:bg-white/10 hover:text-white md:hidden"
          >
            <X size={20} />
          </button>
        </div>

        <nav className="flex-1 space-y-1 overflow-y-auto py-4">
          {navItems.map((item) => (
            <NavLink
              key={item.path}
              to={item.path}
              end={item.path === '/'}
              // The pathname effect below covers real navigation. This covers
              // tapping the item for the page you are already on, which is not
              // a navigation and would otherwise leave the drawer sitting open
              // over the page it just failed to change.
              onClick={onClose}
              className={({ isActive }) =>
                `flex items-center space-x-3 px-6 py-3 transition-colors ${
                  isActive
                    ? 'bg-gray-800 text-white border-r-4 border-risk-medium'
                    : 'text-gray-300 hover:bg-gray-800 hover:text-white'
                }`
              }
            >
              {item.icon}
              <span>{item.name}</span>
            </NavLink>
          ))}
        </nav>

        {/* Renders nothing unless the portal handed over a token, so a direct
            visit still just sees the Care team footer. */}
        <AppSwitcher onNavigate={onClose} />

        <div className="border-t border-gray-700 p-4">
          <div className="text-sm font-medium text-white">{ROLE_LABELS[role] || 'Care team'}</div>
        </div>
      </div>
    </>
  );
}

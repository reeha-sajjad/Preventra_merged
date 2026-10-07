import { useEffect, useState } from 'react';
import { NavLink, useLocation } from 'react-router-dom';
import { useRole } from '../../context/RoleContext';
import { useAuth } from '../../context/AuthContext';
import {
  LayoutDashboard, Users, UserCircle, PieChart, TrendingDown, UsersRound,
  Calculator, Settings, ChevronLeft, ChevronRight,
  Building2, Stethoscope, AlertTriangle, LogOut, Menu, X, ExternalLink,
} from 'lucide-react';
import HospitalPicker from '../hospital/HospitalPicker';
import NotificationBell from './NotificationBell';

// `only` names the RoleContext flag a page needs; pages for other roles are
// hidden, not dimmed (the backend refuses them anyway).
const NAV_ITEMS = [
  { to: '/',         icon: LayoutDashboard, label: 'Overview',             primary: null, only: 'hasOverview' },
  { to: '/',         icon: LayoutDashboard, label: 'My patients',          primary: null, only: 'isCareTeam' },
  { to: '/patients', icon: Users,           label: 'Patients',             primary: null },
  { to: '/staff',    icon: UsersRound,      label: 'Staff',                primary: null, only: 'hasStaff' },
  { to: '/segments', icon: PieChart,        label: 'Segment Explorer',     primary: null },
  { to: '/survival', icon: TrendingDown,    label: 'Survival Analysis',    primary: null },
  // { to: '/cost',     icon: DollarSign,      label: 'Cost-Effectiveness',   primary: 'cost' },
  { to: '/budget',      icon: Calculator,     label: 'Budget Simulator',     primary: 'cost' },
  { to: '/consequence', icon: AlertTriangle,  label: 'Cost of Inaction',     primary: 'cost' },
  // { to: '/settings', icon: Settings,        label: 'Settings & Data Info', primary: null },
];

const PAGE_TITLES = { '/settings': 'Settings', '/my-record': 'My record' };

// `primary` marks who a page is mainly for: 'cost' pages for the roles that own
// the budget, 'care_team' pages for the people looking after patients. Pages
// meant for the other group are dimmed and badged, not hidden.
function NavItem({ item, collapsed, isCostView, extra = {} }) {
  const { to, icon: Icon, label, primary } = item;
  const mismatch = primary && (
    (primary === 'cost'      && !isCostView) ||
    (primary === 'care_team' &&  isCostView)
  );
  const badgeLabel = primary === 'cost' ? 'Finance' : 'Care team';
  const badgeColor = primary === 'cost' ? '#2E6DB4' : '#2E7D32';
  return (
    <NavLink
      key={to} to={to} {...extra}
      className={({ isActive }) => `nav-link ${isActive ? 'active' : ''}`}
      title={collapsed ? label : undefined}
      style={{ opacity: mismatch ? 0.5 : 1 }}
    >
      <Icon size={17} className="nav-icon flex-shrink-0" />
      {!collapsed && <span className="animate-fade-in truncate flex-1">{label}</span>}
      {!collapsed && mismatch && (
        <span className="text-[8px] font-bold px-1.5 py-0.5 rounded-full flex-shrink-0 ml-1"
              style={{ background: `${badgeColor}28`, color: `${badgeColor}cc` }}>
          {badgeLabel}
        </span>
      )}
    </NavLink>
  );
}

export default function AppShell({ children }) {
  const [collapsed, setCollapsed] = useState(false);
  const [mobileOpen, setMobileOpen] = useState(false);
  const roleFlags = useRole();
  const { roleLabel, isCostView, isPatient, isCareTeam } = roleFlags;
  const { logout, user, token } = useAuth();
  const location = useLocation();
  const allowed = (item) => !item.only || roleFlags[item.only];

  const pageTitle = NAV_ITEMS.find(n => n.to === location.pathname && allowed(n))?.label
    ?? PAGE_TITLES[location.pathname]
    ?? (location.pathname.startsWith('/patients/') ? 'Patient detail' : 'GLP-1 Platform');

  // The mobile drawer always shows the full-width sidebar, even if the user
  // collapsed it on desktop before shrinking the window.
  const isCollapsed = collapsed && !mobileOpen;

  // Escape closes the drawer.
  useEffect(() => {
    if (!mobileOpen) return;
    const onKey = (e) => { if (e.key === 'Escape') setMobileOpen(false); };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [mobileOpen]);

  return (
    <div className="flex h-screen overflow-hidden" style={{ background: 'var(--bg-canvas)' }}>
      {/* ── Mobile backdrop ─────────────────────────────────────── */}
      {mobileOpen && (
        <div
          className="fixed inset-0 z-30 bg-black/40 lg:hidden"
          aria-hidden="true"
          onClick={() => setMobileOpen(false)}
        />
      )}

      {/* ── Sidebar ─────────────────────────────────────────────── */}
      <aside
        className={`fixed inset-y-0 left-0 z-40 flex flex-col h-full flex-shrink-0
          transition-transform duration-300 ease-in-out
          lg:static lg:translate-x-0 lg:z-auto lg:transition-all
          ${mobileOpen ? 'translate-x-0' : '-translate-x-full'}`}
        style={{
          width: isCollapsed ? 68 : 240,
          background: 'var(--bg-sidebar)',
          borderRight: '1px solid rgba(255,255,255,0.06)',
        }}
      >
        {/* Logo */}
        <div className="flex items-center gap-3 px-4 h-[60px] flex-shrink-0 border-b border-white/10">
                    <img src="/preventra-logo.png" alt="Preventra" className="flex-shrink-0 h-10 w-auto object-contain" />
          {!isCollapsed && (
            <div className="animate-fade-in overflow-hidden flex-1">
              <div className="text-white font-display text-sm font-semibold leading-tight">GLP-1</div>
              <div className="text-white/40 text-[10px] tracking-widest uppercase">Analytics</div>
            </div>
          )}
          <button
            onClick={() => setMobileOpen(false)}
            aria-label="Close navigation"
            className="lg:hidden flex-shrink-0 w-8 h-8 rounded-lg flex items-center justify-center text-white/60 hover:text-white hover:bg-white/10 transition-colors"
          >
            <X size={18} />
          </button>
        </div>

        {/* Role - assigned by an administrator, so shown here, never chosen */}
        {!isCollapsed && (
          <div className="mx-3 mt-4 mb-2 rounded-lg px-3 py-2 animate-fade-in"
               style={{ background: 'rgba(255,255,255,0.06)' }}>
            <div className="text-[10px] text-white/30 uppercase tracking-widest">Role</div>
            <div className="flex items-center gap-1.5 text-[12px] font-medium text-white/80 mt-0.5">
              {isCostView ? <Building2 size={12} /> : <Stethoscope size={12} />}
              {roleLabel}
            </div>
          </div>
        )}

        {/* Nav links — any tap inside closes the mobile drawer */}
        <nav className="flex-1 overflow-y-auto px-2 py-2 space-y-0.5" onClick={() => setMobileOpen(false)}>
          {isPatient ? (
            // A patient sees one thing: their own record.
            <NavLink to="/my-record" className={({ isActive }) => `nav-link ${isActive ? 'active' : ''}`}
              title={isCollapsed ? 'My record' : undefined}>
              <UserCircle size={17} className="nav-icon flex-shrink-0" />
              {!isCollapsed && <span className="animate-fade-in">My record</span>}
            </NavLink>
          ) : (<>
          {/* Section: Overview */}
          {!isCollapsed && <div className="text-[10px] text-white/25 uppercase tracking-widest px-3 pt-3 pb-1">Overview</div>}
          {NAV_ITEMS.slice(0, 4).filter(allowed).map(item => (
            <NavItem key={item.to} item={item} collapsed={isCollapsed} isCostView={isCostView}
              extra={item.to === '/' ? { end: true } : {}} />
          ))}

          {/* Section: Analytics */}
          {!isCollapsed && <div className="text-[10px] text-white/25 uppercase tracking-widest px-3 pt-4 pb-1">Analytics</div>}
          {NAV_ITEMS.slice(4, 6).map(item => (
            <NavItem key={item.to} item={item} collapsed={isCollapsed} isCostView={isCostView} />
          ))}

          {/* Section: Financial - only for the roles that own the budget; hidden,
              not dimmed, for everyone else (the backend refuses them anyway) */}
          {isCostView && !isCollapsed && (
            <div className="flex items-center gap-2 px-3 pt-4 pb-1">
              <div className="text-[10px] text-white/25 uppercase tracking-widest">Financial</div>
              <div className="text-[9px] bg-blue-500/30 text-blue-300 px-1.5 py-0.5 rounded-full">Primary</div>
            </div>
          )}
          {isCostView && NAV_ITEMS.slice(6, 8).map(item => (
            <NavItem key={item.to} item={item} collapsed={isCollapsed} isCostView={isCostView} />
          ))}

          {/* Settings */}
          {!isCollapsed && <div className="text-[10px] text-white/25 uppercase tracking-widest px-3 pt-4 pb-1">System</div>}
          <NavLink to="/settings" className={({ isActive }) => `nav-link ${isActive ? 'active' : ''}`}
            title={isCollapsed ? 'Settings' : undefined}>
            <Settings size={17} className="nav-icon flex-shrink-0" />
            {!isCollapsed && <span className="animate-fade-in">Settings & Data Info</span>}
          </NavLink>
          </>)}
        </nav>
        {/* Switch App */}
      {!collapsed && user?.app_access?.includes('readmissions') && (
        <div className="mx-3 mb-2 pt-2 border-t border-white/10">
          <div className="text-[10px] text-white/25 uppercase tracking-widest px-1 pb-1">
            Switch App
          </div>
          <a
            href={`${import.meta.env.VITE_READMISSIONS_URL ?? 'https://readmission-frontend-production.up.railway.app'}/#token=${encodeURIComponent(token)}`}
            className="flex items-center justify-between px-3 py-2 rounded-lg text-xs text-white/70 hover:bg-white/08 hover:text-white transition-colors"
          >
            <span className="flex items-center gap-2">
              <ExternalLink size={13} />
              Readmissions
            </span>
          </a>
          {user?.email && (
            <div className="px-1 pt-1 text-[10px] text-white/30 truncate">
              {user.email}
            </div>
          )}
        </div>
      )}
        {/* Logout button */}
        <div className="px-2 pb-1">
          <button
            onClick={logout}
            className="w-full flex items-center justify-center gap-1.5 h-9 rounded-lg text-xs font-medium transition-all hover:opacity-90"
            style={{ background: 'var(--color-primary-light)', color: '#fff' }}
          >
            <LogOut size={14} />
            {!isCollapsed && <span className="animate-fade-in">Log out</span>}
          </button>
        </div>
        {/* Collapse toggle — desktop only */}
        <div className="hidden lg:block border-t border-white/10 p-2">
          <button
            onClick={() => setCollapsed(c => !c)}
            className="w-full flex items-center justify-center h-9 rounded-lg text-white/40 hover:text-white hover:bg-white/08 transition-colors"
          >
            {isCollapsed ? <ChevronRight size={16} /> : <ChevronLeft size={16} />}
            {!isCollapsed && <span className="ml-2 text-xs animate-fade-in">Collapse</span>}
          </button>
        </div>
      </aside>

      {/* ── Main area ────────────────────────────────────────────── */}
      <div className="flex flex-col flex-1 min-w-0 overflow-hidden">
        {/* Topnav */}
        <header className="flex-shrink-0 flex items-center justify-between gap-3 px-4 lg:px-6"
          style={{ height: 60, background: 'var(--bg-surface)', borderBottom: '1px solid var(--border)' }}>
          <div className="flex items-center gap-3 min-w-0">
            <button
              onClick={() => setMobileOpen(true)}
              aria-label="Open navigation"
              className="lg:hidden flex-shrink-0 w-9 h-9 -ml-1 rounded-lg flex items-center justify-center text-gray-600 hover:bg-gray-100 transition-colors"
            >
              <Menu size={20} />
            </button>
            <div className="min-w-0">
              <h1 className="font-display text-lg text-gray-800 leading-tight truncate">{pageTitle}</h1>
              <p className="hidden sm:block text-xs text-gray-400 leading-none mt-0.5">GLP-1 Adherence & Cost Intelligence Platform</p>
            </div>
          </div>
          <div className="flex items-center gap-3 flex-shrink-0">
            <HospitalPicker />
            {isCareTeam && <NotificationBell />}
            {/* Role indicator pill */}
            <div className="flex items-center gap-2 px-3 py-1.5 rounded-full text-xs font-medium"
                 style={{ background: isCostView ? '#E3F2FD' : '#E8F5E9', color: isCostView ? '#1B4F8A' : '#2E7D32' }}>
              {isCostView ? <Building2 size={12} /> : <Stethoscope size={12} />}
              <span className="hidden sm:inline">{roleLabel}</span>
            </div>
            {/* Data freshness */}
            <div className="hidden md:block text-xs text-gray-400">
              Data as of <span className="font-medium text-gray-600">May 2026</span>
            </div>
          </div>
        </header>

        {/* Page content */}
        <main className="flex-1 overflow-auto p-4 lg:p-6">
          {children}
        </main>
      </div>
    </div>
  );
}
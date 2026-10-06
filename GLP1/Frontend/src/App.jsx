import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom';
import { useEffect } from 'react';
import { AuthProvider, useAuth, PORTAL_URL } from './context/AuthContext';
import { RoleProvider } from './context/RoleContext';
import { PatientsProvider } from './context/PatientsContext';
import AppShell from './components/layout/AppShell';
import LoadingScreen from './components/shared/LoadingScreen';
import ChatWidget from './components/chatbot/ChatWidget';
import { useAppLoader } from './hooks/useAppLoader';
import ExecutiveSummary from './pages/ExecutiveSummary';
import CareHome from './pages/CareHome';
import PatientRiskPanel from './pages/PatientRiskPanel';
import PatientDetail from './pages/PatientDetail';
import SegmentExplorer from './pages/SegmentExplorer';
import SurvivalAnalysis from './pages/SurvivalAnalysis';
import CostEffectiveness from './pages/CostEffectiveness';
import BudgetSimulator from './pages/BudgetSimulator';
import CostOfInaction from './pages/CostOfInaction';
import Settings from './pages/Settings';
import MyRecord from './pages/MyRecord';
import Staff from './pages/Staff';
import { useRole } from './context/RoleContext';

function AuthenticatedApp() {
  const { ready, progress, status } = useAppLoader();

  if (!ready) {
    return <LoadingScreen progress={progress} status={status} />;
  }

  return (
    <RoleProvider>
      <PatientsProvider>
        <AppShell>
          <RoleRoutes />
        </AppShell>
      </PatientsProvider>
    </RoleProvider>
  );
}

// Which pages each role can reach. The backend refuses the rest anyway; these
// redirects stop a page from opening at all, so it never falls back to stand-in
// data after a refusal. Doctors and nurses have no Overview: their Patients
// list is their home.
function RoleRoutes() {
  const { isCostView, isPatient, hasOverview, hasStaff, isCareTeam } = useRole();

  if (isPatient) {
    return (
      <Routes>
        <Route path="/my-record"    element={<MyRecord />} />
        <Route path="/patients/:id" element={<PatientDetail />} />
        <Route path="*"             element={<Navigate to="/my-record" replace />} />
      </Routes>
    );
  }

  return (
    <>
      <Routes>
        <Route path="/"             element={hasOverview ? <ExecutiveSummary />
                                             : isCareTeam ? <CareHome />
                                             : <Navigate to="/patients" replace />} />
        <Route path="/patients"     element={<PatientRiskPanel />} />
        {hasStaff && <Route path="/staff" element={<Staff />} />}
        <Route path="/patients/:id" element={<PatientDetail />} />
        <Route path="/segments"     element={<SegmentExplorer />} />
        <Route path="/survival"     element={<SurvivalAnalysis />} />
        {isCostView && <Route path="/cost"        element={<CostEffectiveness />} />}
        {isCostView && <Route path="/budget"      element={<BudgetSimulator />} />}
        {isCostView && <Route path="/consequence" element={<CostOfInaction />} />}
        <Route path="/settings"     element={<Settings />} />
        <Route path="*"             element={<Navigate to="/" replace />} />
      </Routes>
      <ChatWidget />
    </>
  );
}

// GLP-1 no longer has its own login screen — an unauthenticated visitor
// gets sent straight back to the shared Portal instead.
function RedirectToPortal() {
  useEffect(() => {
    // replace: Back must not return to a page that immediately redirects again.
    window.location.replace(PORTAL_URL);
  }, []);
  return <LoadingScreen progress={0} status="Redirecting to sign in..." />;
}

function RootRoutes() {
  const { isAuthenticated } = useAuth();

  return (
    <Routes>
      <Route
        path="/*"
        element={isAuthenticated ? <AuthenticatedApp /> : <RedirectToPortal />}
      />
    </Routes>
  );
}

export default function App() {
  return (
    <AuthProvider>
      <BrowserRouter>
        <RootRoutes />
      </BrowserRouter>
    </AuthProvider>
  );
}
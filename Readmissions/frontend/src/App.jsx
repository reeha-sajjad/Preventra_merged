import React from 'react';
import { BrowserRouter as Router, Routes, Route, Navigate } from 'react-router-dom';
import Layout from './components/Layout';
import Overview from './pages/Overview';
import Patients from './pages/Patients';
import Staff from './pages/Staff';
import PatientDetail from './pages/PatientDetail';
import PatientTrend from './pages/PatientTrend';
import UpdatePatient from './pages/UpdatePatient';
import Analytics from './pages/Analytics';
import DoctorConsole from './pages/DoctorConsole';
import ManualEntry from './pages/ManualEntry';
import About from './pages/About';
import Settings from './pages/Settings';
import TestComponents from './pages/TestComponents';
import MyRecord from './pages/MyRecord';
import ModelStudio from './pages/ModelStudio';
import { MANUAL_ENTRY_ENABLED } from './api';
import { readClaims } from './api/auth';
import { can, OVERVIEW_ROLES, STAFF_ROLES, CONSOLE_ROLES, STUDIO_ROLES } from './roles';
import LoadingScreen from './components/LoadingScreen';
import { useAppLoader } from './hooks/useAppLoader';

/**
 * The care-team dashboard.
 *
 * Every visitor is a signed-in account, and the backend shows each one only
 * its own patients (api/access.py): the hospital for its admin and case
 * managers, their assigned patients for doctors and nurses, members for an
 * insurer. A patient gets a single page - their own record. The routes below
 * only decide which pages open; the backend decides what data they get.
 *
 * The hospital pages (see api/hospital.py): Overview for the roles that run
 * or pay for a hospital's care, Patients for everyone, Staff for the roles
 * that assign. Doctors and nurses start on their Patients list.
 */
function App() {
  // The opening sequence, as in GLP-1: shown once per page load while the
  // first requests wake the service. Navigating inside the app never repeats it.
  const { ready, progress, status } = useAppLoader();
  if (!ready) return <LoadingScreen progress={progress} status={status} />;
  return <AppRoutes />;
}

function AppRoutes() {
  if (readClaims()?.role === 'patient') {
    return (
      <Router>
        <Routes>
          <Route element={<Layout />}>
            <Route path="/my-record" element={<MyRecord />} />
            <Route path="/patients/:id" element={<PatientDetail />} />
            <Route path="/patients/:id/trend" element={<PatientTrend />} />
            <Route path="*" element={<Navigate to="/my-record" replace />} />
          </Route>
        </Routes>
      </Router>
    );
  }

  return (
    <Router>
      <Routes>
        <Route element={<Layout />}>
          <Route path="/" element={can(OVERVIEW_ROLES) ? <Overview /> : <Navigate to="/patients" replace />} />
          <Route path="/patients" element={<Patients />} />
          {can(STAFF_ROLES) && <Route path="/staff" element={<Staff />} />}
          <Route path="/patients/:id" element={<PatientDetail />} />
          <Route path="/patients/:id/trend" element={<PatientTrend />} />
          <Route path="/patients/:id/update" element={<UpdatePatient />} />
          <Route path="/analytics" element={<Analytics />} />
          {can(CONSOLE_ROLES) && <Route path="/doctor" element={<DoctorConsole />} />}
          {can(STUDIO_ROLES) && <Route path="/model-studio" element={<ModelStudio />} />}
          {/* Off by default. The backend refuses the create endpoints too, so
              typing the URL gets you a form that cannot save. */}
          {MANUAL_ENTRY_ENABLED && <Route path="/manual-entry" element={<ManualEntry />} />}
          <Route path="/about" element={<About />} />
          <Route path="/settings" element={<Settings />} />
          <Route path="/test-components" element={<TestComponents />} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Route>
      </Routes>
    </Router>
  );
}

export default App;

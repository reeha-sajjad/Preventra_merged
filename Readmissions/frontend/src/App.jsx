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
import PatientHome from './pages/PatientHome';
import MyPeople from './pages/MyPeople';
import InsurerHome from './pages/InsurerHome';
import ModelStudio from './pages/ModelStudio';
import MyPatients from './pages/MyPatients';
import FollowUps from './pages/FollowUps';
import PharmacyHome from './pages/PharmacyHome';
import Outcomes from './pages/Outcomes';
import { readClaims } from './api/auth';
import { can, INSURER_ROLES, OVERVIEW_ROLES, STAFF_ROLES, CONSOLE_ROLES, STUDIO_ROLES, MY_PATIENTS_ROLES,
         FOLLOWUP_HANDLE_ROLES, PHARMACY_ROLES, OUTCOME_ROLES, SCORE_ROLES } from './roles';
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
  // A caretaker: everyone they look after, then one person's summary.
  if (readClaims()?.role === 'caretaker') {
    return (
      <Router>
        <Routes>
          <Route element={<Layout />}>
            <Route path="/" element={<MyPeople />} />
            <Route path="/person/:pid" element={<PatientHome />} />
            {/* The full record, read only, of the people who linked them. */}
            <Route path="/patients/:id" element={<PatientDetail />} />
            <Route path="/patients/:id/trend" element={<PatientTrend />} />
            <Route path="/settings" element={<Settings />} />
            <Route path="*" element={<Navigate to="/" replace />} />
          </Route>
        </Routes>
      </Router>
    );
  }

  // Patients get the plain-words home page.
  if (readClaims()?.role === 'patient') {
    return (
      <Router>
        <Routes>
          <Route element={<Layout />}>
            {/* A patient's home is their own record in plain words. The
                clinician's patient pages are not theirs to read through. */}
            <Route path="/" element={<PatientHome />} />
            <Route path="/settings" element={<Settings />} />
            <Route path="*" element={<Navigate to="/" replace />} />
          </Route>
        </Routes>
      </Router>
    );
  }

  return (
    <Router>
      <Routes>
        <Route element={<Layout />}>
          {/* The home page: the hospital's Overview, or a doctor's or nurse's own patients. */}
          <Route path="/" element={can(PHARMACY_ROLES) ? <PharmacyHome /> : can(INSURER_ROLES) ? <InsurerHome />
            : can(OVERVIEW_ROLES) ? <Overview />
            : can(MY_PATIENTS_ROLES) ? <MyPatients /> : <Navigate to="/patients" replace />} />
          {/* A pharmacy's patient list is its home page. */}
          <Route path="/patients" element={can(PHARMACY_ROLES) ? <Navigate to="/" replace /> : <Patients />} />
          {/* An insurer's overview is part of Members now. */}
          {can(INSURER_ROLES) && <Route path="/overview" element={<Navigate to="/" replace />} />}
          {can(STAFF_ROLES) && <Route path="/staff" element={<Staff />} />}
          {can(FOLLOWUP_HANDLE_ROLES) && <Route path="/follow-ups" element={<FollowUps />} />}
          {can(OUTCOME_ROLES) && <Route path="/outcomes" element={<Outcomes />} />}
          <Route path="/patients/:id" element={<PatientDetail />} />
          <Route path="/patients/:id/trend" element={<PatientTrend />} />
          <Route path="/patients/:id/update" element={<UpdatePatient />} />
          <Route path="/analytics" element={<Analytics />} />
          {can(CONSOLE_ROLES) && <Route path="/doctor" element={<DoctorConsole />} />}
          {can(STUDIO_ROLES) && <Route path="/model-studio" element={<ModelStudio />} />}
          {/* Calculating a score is open to the roles that score patients; the
              page shows the add buttons only where the server would allow it. */}
          {can(SCORE_ROLES) && <Route path="/manual-entry" element={<ManualEntry />} />}
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
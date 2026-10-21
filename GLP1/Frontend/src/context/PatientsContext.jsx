import { createContext, useCallback, useContext, useEffect, useState } from 'react';
import { api } from '../data/api';

const PatientsContext = createContext(null);

// Every patient the signed-in user may see, loaded once and filtered in the
// browser. `redacted` is the server saying this user gets the overview layer
// only (hospital admins, insurers), so the clinical columns are absent.
export function PatientsProvider({ children }) {
  const [patients, setPatients] = useState([]);
  const [redacted, setRedacted] = useState(false);
  const [emergency, setEmergency] = useState([]);   // emergency-access patients, kept off the list
  const [loading, setLoading]   = useState(true);
  const [error, setError]       = useState(null);
  const [version, setVersion]   = useState(0);

  useEffect(() => {
    let live = true;
    api.getPatients({ page: 0, page_size: 10000, sort_by: "dropout_prob", sort_dir: "desc" })
      .then((res) => { if (live) { setPatients(res.patients); setRedacted(Boolean(res.redacted)); setEmergency(res.emergency || []); } })
      .catch((err) => { if (live) setError(err); })
      .finally(() => { if (live) setLoading(false); });
    return () => { live = false; };
  }, [version]);

  // After a care-team change, so the list shows the new names.
  const reload = useCallback(() => setVersion((v) => v + 1), []);

  return (
    <PatientsContext.Provider value={{ patients, redacted, emergency, loading, error, reload }}>
      {children}
    </PatientsContext.Provider>
  );
}

export const usePatients = () => useContext(PatientsContext);
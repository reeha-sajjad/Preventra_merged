// export { usePatients } from '../context/PatientsContext';
import { useCallback, useEffect, useState } from "react";
import { api } from "../data/api";

/**
 * One patient: the overview layer first (risk, care team, insurer, and whether
 * the clinical details are open to this user), then the record itself once it
 * is. Hospital admins and insurers give a reason before that second call is
 * made (Backend/core/access.py); everyone else gets both at once.
 *
 * No stand-in data: a patient the backend refuses reads as unavailable, never
 * as someone else's record.
 */
export function usePatientRecord(id) {
  const [state, setState] = useState({ summary: null, data: null, loading: true, error: null });
  const [version, setVersion] = useState(0);

  useEffect(() => {
    if (id == null) return undefined;
    let live = true;
    api.getPatientSummary(Number(id))
      .then((summary) => {
        if (!live) return null;
        if (summary.detail_access === "reason_required" || summary.detail_access === "overview_only") {
          setState({ summary, data: null, loading: false, error: null });
          return null;
        }
        return api.getPatient(Number(id)).then((data) => {
          if (live) setState({ summary, data, loading: false, error: null });
        });
      })
      .catch((error) => { if (live) setState({ summary: null, data: null, loading: false, error }); });
    return () => { live = false; };
  }, [id, version]);

  // After a reason is given or the care team changes.
  const reload = useCallback(() => setVersion((v) => v + 1), []);
  return { ...state, reload };
}
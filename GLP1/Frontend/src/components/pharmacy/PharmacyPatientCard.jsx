import { useCallback, useEffect, useState } from 'react';
import { Pill, RefreshCw, Send, CheckCircle2 } from 'lucide-react';
import { api } from '../../data/api';
import ReportDialog, { ReportStatus } from './ReportDialog';

const REFILL = {
  low: ['Low', '#C62828'], typical: ['Typical', '#B45309'], good: ['Good', '#2E7D32'],
};

/**
 * On a patient's page, for a pharmacy: the drug and refill reliability (from
 * the patient summary - Backend/routers/patients.get_patient_summary), the
 * follow-up status, and "Report a refill problem".
 */
export default function PharmacyPatientCard({ summary }) {
  const [followup, setFollowup] = useState(null);
  const [reporting, setReporting] = useState(false);
  const [sent, setSent] = useState(null);

  const load = useCallback(() => {
    api.getPatientFollowUp(summary.patient_idx).then(setFollowup).catch(() => {});
  }, [summary.patient_idx]);
  useEffect(() => { load(); }, [load]);

  const refill = REFILL[summary.refill_band];
  const current = followup?.active || followup?.recent;
  return (
    <div className="card p-6">
      <div className="flex flex-wrap items-center justify-between gap-3 mb-4">
        <h2 className="text-base font-semibold text-gray-800 flex items-center gap-2" style={{ fontFamily: 'var(--p-font-display)' }}>
          <Pill size={16} className="text-gray-400" /> Dispensing
        </h2>
        <button type="button" onClick={() => setReporting(true)} disabled={!followup}
          className="inline-flex items-center gap-1.5 text-xs font-semibold px-3 py-2 rounded-lg text-white disabled:opacity-50"
          style={{ background: 'var(--color-primary)' }}>
          <Send size={13} /> Report a refill problem
        </button>
      </div>
      <div className="grid gap-4 sm:grid-cols-3">
        <div>
          <div className="text-[11px] text-gray-400 uppercase tracking-wider font-medium">GLP-1 drug</div>
          <div className="text-sm font-semibold text-gray-800 mt-0.5">{summary.drug || '—'}</div>
        </div>
        <div>
          <div className="flex items-center gap-1.5 text-[11px] text-gray-400 uppercase tracking-wider font-medium">
            <RefreshCw size={12} /> Refill reliability
          </div>
          <div className="text-sm font-semibold mt-0.5" style={{ color: refill?.[1] || '#A0AEC0' }}>
            {refill ? `${refill[0]} (compared with all patients)` : 'Not on file'}
          </div>
        </div>
        <div>
          <div className="text-[11px] text-gray-400 uppercase tracking-wider font-medium">Follow-up</div>
          <div className="mt-1">
            {!followup ? <span className="text-sm text-gray-300">Loading…</span>
              : current ? <ReportStatus f={current} /> : <span className="text-sm text-gray-400">None open</span>}
          </div>
        </div>
      </div>
      {sent && (
        <p className="mt-4 flex items-center gap-2 text-sm" style={{ color: '#2E7D32' }}>
          <CheckCircle2 size={15} />
          {sent.added_to_open ? 'Added to the follow-up already open for this patient.' : 'Sent to the patient’s case manager.'}
        </p>
      )}
      {reporting && followup && (
        <ReportDialog patient={{ patient_idx: summary.patient_idx, case_manager: summary.case_manager?.name }}
          problems={followup.problems || []} onClose={() => setReporting(false)}
          onDone={(out) => { setReporting(false); setSent(out); load(); }} />
      )}
    </div>
  );
}
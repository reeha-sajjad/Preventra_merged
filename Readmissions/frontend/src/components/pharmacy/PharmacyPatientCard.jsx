import React, { useCallback, useEffect, useState } from 'react';
import { Pill, Send, CheckCircle2 } from 'lucide-react';
import { getPatientFollowUp } from '../../api';
import ReportDialog, { ReportStatus } from './ReportDialog';
import { REFILL } from './refill';

/**
 * On a patient's page, for a pharmacy: the latest refill from the pharmacy
 * feed (on the patient summary - api/main.get_patient_summary), the follow-up
 * status, and "Report a refill problem".
 */
export default function PharmacyPatientCard({ summary }) {
  const [followup, setFollowup] = useState(null);
  const [reporting, setReporting] = useState(false);
  const [sent, setSent] = useState(null);

  const load = useCallback(() => {
    getPatientFollowUp(summary.id).then(setFollowup).catch(() => {});
  }, [summary.id]);
  useEffect(() => { load(); }, [load]);

  const refill = REFILL[summary.refill_latest];
  const current = followup?.active || followup?.recent;
  return (
    <div className="rounded-xl border border-gray-200 bg-white p-6 shadow-sm">
      <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
        <h3 className="flex items-center gap-2 font-semibold text-ns-navy"><Pill size={18} /> Dispensing</h3>
        <button type="button" onClick={() => setReporting(true)} disabled={!followup}
          className="inline-flex items-center gap-1.5 rounded-md bg-ns-navy px-3 py-2 text-sm font-semibold text-white hover:bg-blue-900 disabled:opacity-50">
          <Send size={14} /> Report a refill problem
        </button>
      </div>
      <div className="grid gap-4 sm:grid-cols-3">
        <div>
          <div className="text-xs font-medium uppercase tracking-wide text-gray-500">Latest refill</div>
          <div className={`mt-0.5 text-sm font-semibold ${refill?.text || 'text-gray-400'}`}>
            {refill ? `${refill.label}${summary.refill_week ? ` (week ${summary.refill_week})` : ''}` : 'No record yet'}
          </div>
        </div>
        <div>
          <div className="text-xs font-medium uppercase tracking-wide text-gray-500">Missed lately</div>
          <div className={`mt-0.5 text-sm font-semibold ${summary.missed_recent ? 'text-risk-high' : 'text-gray-700'}`}>
            {summary.missed_recent || 0} of the last 4 weeks
          </div>
        </div>
        <div>
          <div className="text-xs font-medium uppercase tracking-wide text-gray-500">Follow-up</div>
          <div className="mt-1">
            {!followup ? <span className="text-sm text-gray-300">Loading…</span>
              : current ? <ReportStatus f={current} /> : <span className="text-sm text-gray-400">None open</span>}
          </div>
        </div>
      </div>
      {sent && (
        <p className="mt-4 flex items-center gap-2 text-sm text-risk-low">
          <CheckCircle2 size={15} />
          {sent.added_to_open ? 'Added to the follow-up already open for this patient.' : 'Sent to the hospital’s case managers.'}
        </p>
      )}
      {reporting && followup && (
        <ReportDialog patientId={summary.id} problems={followup.problems || []}
          onClose={() => setReporting(false)} onDone={(out) => { setReporting(false); setSent(out); load(); }} />
      )}
    </div>
  );
}
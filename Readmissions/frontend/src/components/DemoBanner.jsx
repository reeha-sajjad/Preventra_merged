import React from 'react';
import { Info } from 'lucide-react';

/**
 * Says plainly that what is on screen is demonstration data, so nobody reads a
 * MIMIC patient as a real person or a default cost as a real bill.
 *
 * On unless the build sets VITE_DEMO_DATA=false - the deployment that loads a
 * real hospital's records turns it off; forgetting to set it errs on the side
 * of saying "demo". Read at build time, like every VITE_ setting.
 */
export const DEMO_DATA = import.meta.env.VITE_DEMO_DATA !== 'false';

export default function DemoBanner() {
  if (!DEMO_DATA) return null;
  return (
    <div role="note"
      className="flex items-start gap-2 border-b border-amber-200 bg-amber-50 px-4 py-2 text-xs text-amber-900 sm:px-6">
      <Info size={14} className="mt-0.5 shrink-0" />
      <span>
        <strong>Demo data.</strong> Patients come from MIMIC-IV, a de-identified research dataset, and some
        dates are moved for the demo. Cost figures are published US averages, not any hospital&rsquo;s or
        insurer&rsquo;s real figures.
      </span>
    </div>
  );
}

/** The source line under every readmission cost figure. */
export function CostSourceNote({ className = '' }) {
  return (
    <p className={`text-xs text-gray-500 ${className}`}>
      Illustrative figures, not this organisation&rsquo;s costs. $15,500 is the average hospital cost of a
      Medicare 30-day readmission in 2018 (
      <a href="https://hcup-us.ahrq.gov/reports/statbriefs/sb278-Conditions-Frequent-Readmissions-By-Payer-2018.jsp"
        target="_blank" rel="noreferrer" className="text-ns-navy underline">AHRQ HCUP Statistical Brief #278</a>
      ); the line items split it for illustration. &ldquo;Avoidable&rdquo; assumes follow-up prevents a quarter
      of readmissions &mdash; an assumption, not a measured result.
    </p>
  );
}
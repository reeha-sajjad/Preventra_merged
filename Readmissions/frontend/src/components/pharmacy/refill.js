// How a weekly refill status (weekly_monitoring.monitoring.refill_status, from
// the pharmacy feed) is shown. Shared by the pharmacy page and patient card.
export const REFILL = {
  missed:    { label: 'Missed', text: 'text-risk-high', pill: 'bg-red-50 text-risk-high' },
  late:      { label: 'Late', text: 'text-amber-700', pill: 'bg-amber-50 text-amber-700' },
  collected: { label: 'Collected', text: 'text-risk-low', pill: 'bg-green-50 text-risk-low' },
  'not due': { label: 'Not due', text: 'text-gray-600', pill: 'bg-gray-100 text-gray-600' },
};
import { CalendarCheck, PhoneOff, MinusCircle } from 'lucide-react';

// Shared by the follow-up card, the Follow-ups page and the done dialog.

export const OUTCOMES = [
  { key: 'booked',      label: 'Booked',                 icon: CalendarCheck, color: '#2E7D32',
    hint: 'Booked in the hospital’s own system' },
  { key: 'unreachable', label: 'Couldn’t reach patient', icon: PhoneOff, color: '#EF6C00',
    hint: 'Tried and could not get hold of them' },
  { key: 'not_needed',  label: 'Not needed',             icon: MinusCircle,   color: '#718096',
    hint: 'Already seen, or no longer needed' },
];

export function ago(iso) {
  if (!iso) return '';
  const mins = Math.round((Date.now() - new Date(iso).getTime()) / 60000);
  if (mins < 1) return 'just now';
  if (mins < 60) return `${mins} min ago`;
  const hours = Math.round(mins / 60);
  if (hours < 24) return `${hours} h ago`;
  const days = Math.round(hours / 24);
  return `${days} day${days === 1 ? '' : 's'} ago`;
}
import React from 'react';

// Small components shared by the Data page's tabs (pages/DataIngest.jsx).

export function Notice({ tone = 'gray', children }) {
  const tones = {
    gray: 'border-gray-200 bg-gray-50 text-gray-700',
    red: 'border-red-200 bg-red-50 text-red-700',
    amber: 'border-amber-200 bg-amber-50 text-amber-800',
    green: 'border-green-200 bg-green-50 text-green-800',
  };
  return <div className={`rounded-lg border px-3 py-2 text-sm ${tones[tone]}`}>{children}</div>;
}

export function StatusPill({ status }) {
  const tones = {
    done: 'bg-green-50 text-green-700', queued: 'bg-gray-100 text-gray-600',
    processing: 'bg-blue-50 text-blue-700', failed: 'bg-red-50 text-red-700',
  };
  return (
    <span className={`rounded-full px-2 py-0.5 text-xs font-semibold ${tones[status] || 'bg-gray-100 text-gray-600'}`}>
      {status}
    </span>
  );
}

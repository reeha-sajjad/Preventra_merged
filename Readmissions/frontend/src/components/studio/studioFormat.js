// Shared constants and formatting for the Model Studio components. Kept out of
// the component files so React fast refresh can hot-swap those cleanly.

export const TARGET_LABELS = {
  discharge: 'Risk at discharge',
  weekly: 'Weekly monitoring',
};

export const fmt = (v, digits = 3) => (v == null || Number.isNaN(v) ? '—' : Number(v).toFixed(digits));
export const pct = (v, digits = 1) => (v == null ? '—' : `${(Number(v) * 100).toFixed(digits)}%`);

export function download(text, filename, type = 'text/plain') {
  const url = URL.createObjectURL(new Blob([text], { type }));
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}

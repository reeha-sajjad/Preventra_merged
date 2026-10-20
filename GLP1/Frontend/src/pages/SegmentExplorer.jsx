import { useState } from 'react';
import {
  RadarChart, Radar, PolarGrid, PolarAngleAxis, ResponsiveContainer,
  BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip, Cell,
} from 'recharts';
import { LayoutGrid, Rows } from 'lucide-react';
import { SectionHeader, ProgressBar } from '../components/shared';
import { SEGMENT_COLORS, SEGMENT_LABELS, SEGMENT_SHORT } from '../data/mockData';
import { useSegments } from '../hooks/useSegments';
import { useRole } from '../context/RoleContext';
import PageState from '../components/shared/PageState';

const fmt$ = n => `$${n.toLocaleString()}`;
// The radar's grey outline: the caller's own average across all their patients.
const AVG_FIELDS = ['age', 'bmi', 'hba1c', 'cost_pressure', 'bio_friction', 'comorbidity'];
function ownAverages(segments) {
  const total = segments.reduce((t, s) => t + (s.n || 0), 0) || 1;
  return Object.fromEntries(AVG_FIELDS.map((k) => [k, segments.reduce((t, s) => t + (s[k] || 0) * (s.n || 0), 0) / total]));
}

function normalize(seg) {
  return [
    { axis: 'Age',          value: Math.round((seg.age          / 80)  * 100), raw: `${seg.age.toFixed(0)}y` },
    { axis: 'BMI',          value: Math.round((seg.bmi          / 45)  * 100), raw: `${seg.bmi}` },
    { axis: 'HbA1c',        value: Math.round((seg.hba1c        / 12)  * 100), raw: `${seg.hba1c}%` },
    { axis: 'Cost Pressure',value: Math.round((seg.cost_pressure/ 60)  * 100), raw: seg.cost_pressure.toFixed(1) },
    { axis: 'Side Effects', value: Math.round((seg.bio_friction / 0.7) * 100), raw: seg.bio_friction.toFixed(3) },
    { axis: 'Comorbidity',  value: Math.round((seg.comorbidity  / 2)   * 100), raw: `${seg.comorbidity.toFixed(2)}/2` },
  ];
}

const METRIC_FIELDS = [
  ['Adherence Rate',   s => `${(s.adherence * 100).toFixed(1)}%`],
  ['Patients',         s => s.n.toLocaleString()],
  ['Avg OOP Cost',     s => fmt$(s.oop_cost.toFixed(0))],
  ['Cost/HbA1c Pt',   s => fmt$(s.cost_per_hba1c)],
  ['Wasted Spend/Pt', s => fmt$(s.wasted_per_pt)],
  ['Bio Friction',     s => s.bio_friction.toFixed(3)],
  ['Comorbidity',      s => `${s.comorbidity.toFixed(2)}/2`],
  ['Avg HbA1c',        s => `${s.hba1c}%`],
];

// Compare-table rows: label, value extractor, formatting, and which direction is "good"
const COMPARE_ROWS = [
  { label: 'Patients',         get: s => s.n,               fmt: s => s.n.toLocaleString(),                  dir: null },
  { label: 'Adherence Rate',   get: s => s.adherence,       fmt: s => `${(s.adherence*100).toFixed(1)}%`,    dir: 'higher' },
  { label: 'Dropout Rate',     get: s => 1-s.adherence,     fmt: s => `${((1-s.adherence)*100).toFixed(1)}%`,dir: 'lower' },
  { label: 'Avg Age',          get: s => s.age,             fmt: s => `${s.age.toFixed(0)}y`,                dir: null },
  { label: 'Avg BMI',          get: s => s.bmi,             fmt: s => `${s.bmi}`,                            dir: null },
  { label: 'Avg HbA1c',        get: s => s.hba1c,           fmt: s => `${s.hba1c}%`,                         dir: null },
  { label: 'OOP Cost',         get: s => s.oop_cost,        fmt: s => `$${s.oop_cost.toFixed(0)}`,           dir: 'lower' },
  { label: 'Cost Pressure',    get: s => s.cost_pressure,   fmt: s => s.cost_pressure.toFixed(1),            dir: 'lower' },
  { label: 'Bio Friction',     get: s => s.bio_friction,    fmt: s => s.bio_friction.toFixed(3),             dir: 'lower' },
  { label: 'Comorbidity',      get: s => s.comorbidity,     fmt: s => `${s.comorbidity.toFixed(2)}/2`,       dir: 'lower' },
  { label: 'Wasted Spend/Pt', get: s => s.wasted_per_pt,   fmt: s => `$${s.wasted_per_pt.toLocaleString()}`,dir: 'lower' },
  { label: 'Cost/HbA1c Pt',   get: s => s.cost_per_hba1c,  fmt: s => `$${s.cost_per_hba1c.toLocaleString()}`,dir: 'lower' },
  { label: 'Cost/Weight% Lost',get: s => s.cost_per_weight, fmt: s => `$${s.cost_per_weight.toLocaleString()}`,dir: 'lower' },
];

export default function SegmentExplorer() {
  const { isInsurer } = useRole();
  const { segments: segmentProfiles, error: segmentsError } = useSegments();
  const [active, setActive] = useState(0);
  const [mode, setMode]     = useState('single');

  if (!segmentProfiles) return <PageState error={segmentsError} label="the segments" />;

  // The caller's own average across segments (the backend counts only their
  // patients - Backend/core/scoped.py), not a fixed population figure.
  const totalN = segmentProfiles.reduce((t, s) => t + (s.n || 0), 0);
  const ownAvg = totalN ? segmentProfiles.reduce((t, s) => t + (s.adherence || 0) * (s.n || 0), 0) / totalN : 0;

  const seg   = segmentProfiles[active] ?? segmentProfiles[0];
  const color = SEGMENT_COLORS[active];

  return (
    <div className="max-w-[1200px] mx-auto space-y-5 animate-fade-in">

      {/* ── Segment tab row + mode toggle ──────────────────────── */}
      <div className="flex items-center justify-between flex-wrap gap-3">
        <div className="flex gap-2 flex-wrap">
          {segmentProfiles.map((s, i) => (
            <button key={i} onClick={() => { setActive(i); setMode('single'); }}
              className="flex flex-col items-start px-4 py-2.5 rounded-xl text-left transition-all border"
              style={{
                background:  active === i ? SEGMENT_COLORS[i] : 'white',
                borderColor: active === i ? SEGMENT_COLORS[i] : '#E2E8F0',
                color:       active === i ? 'white' : '#4A5568',
                transform:   active === i ? 'translateY(-2px)' : 'none',
                boxShadow:   active === i ? `0 4px 12px ${SEGMENT_COLORS[i]}44` : 'none',
              }}>
              <span className="text-xs font-bold leading-tight">{SEGMENT_SHORT[i]}</span>
              <span className="text-[10px] mt-0.5 opacity-80">
                n={s.n.toLocaleString()} · {(s.adherence * 100).toFixed(0)}% adherent
              </span>
            </button>
          ))}
        </div>
        <button onClick={() => setMode(m => m === 'single' ? 'compare' : 'single')}
          className="flex items-center gap-2 px-3 py-2 rounded-lg border border-gray-200 text-sm text-gray-600 hover:border-gray-300 transition-colors">
          {mode === 'single'
            ? <><Rows size={14} /> Compare All</>
            : <><LayoutGrid size={14} /> Single View</>}
        </button>
      </div>

      {mode === 'single' ? (
        <>
          {/* ── Three-panel row ──────────────────────────────── */}
          <div className="grid grid-cols-1 lg:grid-cols-3 gap-5">

            {/* Radar */}
            <div className="card p-5">
              <SectionHeader title="Clinical Profile" sub={`vs the average of all your ${isInsurer ? 'members' : 'patients'} (grey)`} />
              <ResponsiveContainer width="100%" height={220}>
                <RadarChart data={normalize(seg)} margin={{ top: 10, right: 20, bottom: 10, left: 20 }}>
                  <PolarGrid stroke="#E2E8F0" />
                  <PolarAngleAxis dataKey="axis" tick={{ fontSize: 10, fill: '#718096' }} />
                  <Radar name="Segment"       dataKey="value" stroke={color}   fill={color}   fillOpacity={0.25} strokeWidth={2} />
                  <Radar name="Population Avg" dataKey="value"
                    data={normalize(ownAverages(segmentProfiles))}
                    stroke="#CBD5E0" fill="#CBD5E0" fillOpacity={0.1} strokeWidth={1.5} strokeDasharray="4 2" />
                </RadarChart>
              </ResponsiveContainer>
            </div>

            {/* Metrics grid */}
            <div className="card p-5">
              <SectionHeader title="Key Metrics" sub={SEGMENT_LABELS[active]} />
              <div className="grid grid-cols-2 gap-2.5">
                {METRIC_FIELDS.map(([label, fmt]) => (
                  <div key={label} className="rounded-lg p-3" style={{ background: '#F7FAFC' }}>
                    <div className="text-[10px] text-gray-400 uppercase tracking-wider mb-1">{label}</div>
                    <div className="font-display text-lg font-semibold" style={{ color }}>{fmt(seg)}</div>
                  </div>
                ))}
              </div>
            </div>

            {/* Adherence breakdown + characteristics */}
            <div className="card p-5 space-y-4">
              <SectionHeader title="Adherence Breakdown" sub={`vs all your ${isInsurer ? 'members' : 'patients'}`} />
              <div>
                <div className="flex justify-between text-xs mb-1.5">
                  <span className="text-gray-500">This segment</span>
                  <span className="font-semibold" style={{ color }}>{(seg.adherence * 100).toFixed(1)}%</span>
                </div>
                <ProgressBar value={seg.adherence} color={color} height={8} />
              </div>
              <div>
                <div className="flex justify-between text-xs mb-1.5">
                  <span className="text-gray-500">{isInsurer ? 'All your members' : 'All your patients'}</span>
                  <span className="font-semibold text-gray-600">{(ownAvg * 100).toFixed(1)}%</span>
                </div>
                <ProgressBar value={ownAvg} color="#CBD5E0" height={8} />
              </div>

              <div className="pt-1 space-y-2">
                <div className="text-[10px] font-bold text-gray-400 uppercase tracking-wider">Key Characteristics</div>
                {[
                  `Age: ${seg.age.toFixed(0)}y avg`,
                  `HbA1c: ${seg.hba1c}% (${seg.hba1c >= 6.5 ? 'Diabetic' : seg.hba1c >= 5.7 ? 'Pre-diabetic' : 'Normal'})`,
                  `Side effect risk: ${seg.bio_friction < 0.3 ? 'Low' : seg.bio_friction < 0.5 ? 'Moderate' : 'High'} (${seg.bio_friction})`,
                  `Financial pressure: ${seg.cost_pressure < 25 ? 'Low' : seg.cost_pressure < 40 ? 'Moderate' : 'High'}`,
                ].map((note, i) => (
                  <div key={i} className="flex items-start gap-2 text-xs text-gray-600">
                    <span className="mt-1.5 w-1.5 h-1.5 rounded-full flex-shrink-0" style={{ background: color }} />
                    {note}
                  </div>
                ))}
              </div>

              <div className="rounded-xl p-3 text-xs" style={{ background: `${color}10`, border: `1px solid ${color}22` }}>
                <div className="font-semibold mb-1" style={{ color }}>Intervention Priority</div>
                <div className="text-gray-600 leading-relaxed">
                  {active === 0 ? 'High — Low clinical urgency driving dropout. Focus on perceived benefit and outcome visibility.' :
                   active === 1 ? 'High — Financial barriers are primary driver. Target copay assistance and cost support.' :
                   active === 2 ? 'Low — Segment is performing well. Monitor for refill gaps only.' :
                                  'Medium — Mixed drivers. Combine side effect management with adherence check-ins.'}
                </div>
              </div>
            </div>
          </div>

          {/* ── Cost comparison bar ─────────────────────────────── */}
          <div className="card p-5">
            <SectionHeader
              title="Cost-Effectiveness Comparison"
              sub="Cost per unit of clinical outcome — lower is better" />
            <div className="grid grid-cols-1 md:grid-cols-2 gap-5">
              {['cost_per_hba1c', 'cost_per_weight'].map(metric => (
                <div key={metric}>
                  <div className="text-xs text-gray-500 mb-3">
                    {metric === 'cost_per_hba1c'
                      ? 'Cost per HbA1c point reduced ($)'
                      : 'Cost per % body weight lost ($)'}
                  </div>
                  <ResponsiveContainer width="100%" height={140}>
                    <BarChart
                      data={segmentProfiles.map(s => ({
                        name: SEGMENT_SHORT[s.cluster].split(' ').slice(0, 2).join(' '),
                        value: s[metric],
                      }))}
                      margin={{ top: 0, right: 16, bottom: 0, left: 0 }}>
                      <CartesianGrid vertical={false} />
                      <XAxis dataKey="name" tick={{ fontSize: 10 }} />
                      <YAxis tickFormatter={v => `$${(v / 1000).toFixed(0)}k`} tick={{ fontSize: 10 }} />
                      <Tooltip formatter={v => [`$${v.toLocaleString()}`, '']} />
                      <Bar dataKey="value" radius={[4, 4, 0, 0]}>
                        {segmentProfiles.map((_, i) => (
                          <Cell key={i} fill={SEGMENT_COLORS[i]} opacity={i === active ? 1 : 0.3} />
                        ))}
                      </Bar>
                    </BarChart>
                  </ResponsiveContainer>
                </div>
              ))}
            </div>
          </div>

        </>
      ) : (
        /* ── Compare mode ──────────────────────────────────────── */
        <div className="card overflow-hidden">
          <div className="overflow-x-auto">
            <table className="data-table">
              <thead>
                <tr>
                  <th className="w-40">Metric</th>
                  {segmentProfiles.map((s, i) => (
                    <th key={i}>
                      <div className="flex items-center gap-1.5">
                        <span className="w-2.5 h-2.5 rounded-full" style={{ background: SEGMENT_COLORS[i] }} />
                        {SEGMENT_SHORT[i]}
                      </div>
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {COMPARE_ROWS.map(({ label, get, fmt, dir }) => {
                  const vals  = segmentProfiles.map(get);
                  const best  = dir === 'higher' ? Math.max(...vals) : dir === 'lower' ? Math.min(...vals) : null;
                  const worst = dir === 'higher' ? Math.min(...vals) : dir === 'lower' ? Math.max(...vals) : null;
                  return (
                    <tr key={label}>
                      <td className="font-medium text-gray-600 text-xs">{label}</td>
                      {segmentProfiles.map((s, i) => {
                        const val    = get(s);
                        const isBest  = dir !== null && val === best;
                        const isWorst = dir !== null && val === worst;
                        return (
                          <td key={i} style={{ borderLeft: `3px solid ${SEGMENT_COLORS[i]}25` }}>
                            <span className="font-mono text-sm inline-block px-2 py-0.5 rounded-md"
                                  style={{
                                    background: isBest ? '#E8F5E9' : isWorst ? '#FFEBEE' : 'transparent',
                                    color:      isBest ? '#2E7D32' : isWorst ? '#C62828' : 'inherit',
                                    fontWeight: (isBest || isWorst) ? 600 : 400,
                                  }}>
                              {fmt(s)}
                            </span>
                          </td>
                        );
                      })}
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
          <div className="flex items-center gap-4 px-4 py-3 border-t border-gray-100 text-[11px] text-gray-500">
            <span className="flex items-center gap-1.5">
              <span className="w-3 h-3 rounded bg-green-100 inline-block border border-green-300" />
              Best value in row
            </span>
            <span className="flex items-center gap-1.5">
              <span className="w-3 h-3 rounded bg-red-100 inline-block border border-red-300" />
              Worst value in row
            </span>
          </div>
        </div>
      )}
    </div>
  );
}
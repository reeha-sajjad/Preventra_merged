import { useMemo } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import {
  Users, AlertTriangle, TrendingUp, TrendingDown, ArrowRight, ChevronRight,
  Stethoscope, Pill, Bell, Gauge,
} from 'lucide-react';
import { SectionHeader, SegmentDot, RiskBadge } from '../components/shared';
import { SEGMENT_SHORT, SEGMENT_COLORS } from '../data/mockData';
import { usePatients } from '../hooks/usePatients';
import { useRole } from '../context/RoleContext';
import PageState from '../components/shared/PageState';

// Same bands as the Patients page and the risk badges (index.css .risk-*).
const BANDS = [
  { key: 'critical', label: 'Critical', range: '75% or more', min: 0.75, max: Infinity, color: 'var(--risk-critical)' },
  { key: 'high',     label: 'High',     range: '50–74%',      min: 0.50, max: 0.75,     color: 'var(--risk-high)' },
  { key: 'medium',   label: 'Medium',   range: '25–49%',      min: 0.25, max: 0.50,     color: 'var(--risk-medium)' },
  { key: 'low',      label: 'Low',      range: 'under 25%',   min: 0,    max: 0.25,     color: 'var(--risk-low)' },
];
const NEEDS_ATTENTION_SHOWN = 8;

/**
 * "My patients": the home page of the people who look after patients - doctors
 * and hospital nurses. Everything here is worked out from their own patient
 * list, which the server already limits to the patients assigned to them
 * (Backend/core/access.py), so nothing on this page can show anyone else's.
 *
 * Laid out like the Overview (ExecutiveSummary.jsx + HospitalStrip.jsx) so the
 * two home pages read as one product.
 */
export default function CareHome() {
  const navigate = useNavigate();
  const { roleLabel } = useRole();
  const { patients, loading, error } = usePatients();

  const view = useMemo(() => {
    const bands = BANDS.map((b) => ({
      ...b,
      n: patients.filter((p) => p.dropout_prob >= b.min && p.dropout_prob < b.max).length,
    }));
    const critical = patients
      .filter((p) => p.dropout_prob >= BANDS[0].min)
      .sort((a, b) => b.dropout_prob - a.dropout_prob);
    const predicted = patients.filter((p) => p.prediction === 'Dropout Risk').length;

    const count = (key) => {
      const out = {};
      for (const p of patients) if (p[key] != null && p[key] !== '') out[p[key]] = (out[p[key]] || 0) + 1;
      return Object.entries(out).sort((a, b) => b[1] - a[1]);
    };
    // Each patient's own biggest reason, counted across this person's patients.
    const reasons = count('driver_1').slice(0, 6);
    const drugs = count('assigned_molecule');

    const segments = [0, 1, 2, 3].map((c) => {
      const mine = patients.filter((p) => p.cluster === c);
      return { cluster: c, n: mine.length, critical: mine.filter((p) => p.dropout_prob >= BANDS[0].min).length };
    });
    return { bands, critical, predicted, reasons, drugs, segments };
  }, [patients]);

  if (loading || error) return <PageState error={error} label="your patients" />;

  const total = patients.length;
  const pct = (n) => (total ? Math.round((n / total) * 100) : 0);

  if (!total) {
    return (
      <div className="exec-summary-page">
        <div className="card p-10 text-center animate-fade-up">
          <div className="w-11 h-11 rounded-xl mx-auto flex items-center justify-center" style={{ background: '#EBF4FF' }}>
            <Users size={20} style={{ color: 'var(--color-primary)' }} />
          </div>
          <h2 className="mt-4 text-lg font-semibold text-gray-800" style={{ fontFamily: 'DM Serif Display, serif' }}>
            No patients assigned to you yet
          </h2>
          <p className="mt-1 text-sm text-gray-500 max-w-md mx-auto">
            Your hospital admin or case manager adds you to patients&rsquo; care teams. Once they do,
            those patients appear here.
          </p>
        </div>
      </div>
    );
  }

  const [critical, high] = view.bands;

  return (
    <div className="exec-summary-page">

      {/* ── Your patients at a glance (same layout as the Overview's hospital card) ── */}
      <div className="card p-5 md:p-6 animate-fade-up">
        <div className="flex flex-wrap items-start justify-between gap-2 mb-4">
          <div>
            <h2 className="text-lg font-semibold text-gray-800 flex items-center gap-2" style={{ fontFamily: 'DM Serif Display, serif' }}>
              <Stethoscope size={17} className="text-gray-400" />
              Your patients
            </h2>
            <p className="text-xs text-gray-400 mt-0.5">
              {total.toLocaleString()} {total === 1 ? 'patient' : 'patients'} on GLP-1 therapy assigned to you
              {roleLabel ? ` as ${/^[aeiou]/i.test(roleLabel) ? 'an' : 'a'} ${roleLabel.toLowerCase()}` : ''}
            </p>
          </div>
          <Link to="/patients" className="inline-flex items-center gap-1 text-xs font-semibold whitespace-nowrap"
                style={{ color: 'var(--color-primary)' }}>
            Open patient list <ArrowRight size={13} />
          </Link>
        </div>

        <div className="grid gap-3 grid-cols-2 lg:grid-cols-4">
          <Tile icon={Users} label="My patients" value={total} sub="assigned to you" color="#1B4F8A" to="/patients" />
          <Tile icon={AlertTriangle} label="Critical risk" value={critical.n}
                sub={`${critical.range} · ${pct(critical.n)}% of patients`} color="#C62828" to="/patients?min_risk=75" />
          <Tile icon={TrendingUp} label="High risk" value={high.n}
                sub={`${high.range} · ${pct(high.n)}% of patients`} color="#EF6C00" />
          <Tile icon={TrendingDown} label="Predicted to stop" value={view.predicted}
                sub={`${pct(view.predicted)}% of patients`} color="#C62828" to="/patients?prediction=Dropout%20Risk" />
        </div>

        <div className="grid gap-6 md:grid-cols-2 mt-5 pt-5 border-t border-gray-100">
          <div>
            <MiniHeading icon={Gauge}>Risk spread</MiniHeading>
            <div className="flex rounded-md overflow-hidden h-2.5 mb-3" style={{ background: 'var(--border)' }}>
              {view.bands.map((b) => b.n > 0 && (
                <div key={b.key} style={{ width: `${(b.n / total) * 100}%`, background: b.color }}
                     title={`${b.label}: ${b.n.toLocaleString()}`} />
              ))}
            </div>
            <ul className="space-y-1.5">
              {view.bands.map((b) => (
                <li key={b.key} className="flex items-center justify-between text-xs">
                  <span className="flex items-center gap-2 text-gray-600">
                    <span className="w-2 h-2 rounded-full" style={{ background: b.color }} />
                    {b.label} <span className="text-gray-400">· {b.range}</span>
                  </span>
                  <Count n={b.n} share={pct(b.n)} />
                </li>
              ))}
            </ul>
          </div>
          <div>
            <MiniHeading icon={Pill}>Drug mix</MiniHeading>
            <ul className="space-y-1.5">
              {view.drugs.map(([drug, n]) => (
                <li key={drug} className="flex items-center justify-between text-xs">
                  <span className="font-mono text-gray-600">{drug}</span>
                  <Count n={n} share={pct(n)} />
                </li>
              ))}
            </ul>
          </div>
        </div>
      </div>

      {/* ── Needs attention ─────────────────────────────────────────── */}
      <div className="card p-0 overflow-hidden animate-fade-up stagger-2">
        <div className="flex flex-col sm:flex-row sm:items-start sm:justify-between gap-2 px-5 md:px-6 pt-5 pb-4">
          <div className="flex items-start gap-3">
            <div className="w-9 h-9 rounded-xl flex items-center justify-center flex-shrink-0"
                 style={{ background: critical.n ? '#FFEBEE' : '#E8F5E9' }}>
              <Bell size={16} style={{ color: critical.n ? '#C62828' : '#2E7D32' }} />
            </div>
            <div>
              <h2 className="text-lg font-semibold text-gray-800" style={{ fontFamily: 'DM Serif Display, serif' }}>
                Needs attention
              </h2>
              <p className="text-xs text-gray-400 mt-0.5">
                {critical.n
                  ? `${critical.n.toLocaleString()} of your patients are at critical risk of stopping their GLP-1 therapy, highest first`
                  : 'None of your patients is at critical risk right now'}
              </p>
            </div>
          </div>
          {critical.n > NEEDS_ATTENTION_SHOWN && (
            <Link to="/patients?min_risk=75" className="inline-flex items-center gap-1 text-xs font-semibold whitespace-nowrap sm:mt-1"
                  style={{ color: 'var(--color-primary)' }}>
              See all {critical.n.toLocaleString()} <ArrowRight size={13} />
            </Link>
          )}
        </div>
        {critical.n > 0 && (
          <div className="overflow-x-auto border-t border-gray-100">
            <table className="data-table">
              <thead>
                <tr><th>Patient</th><th>Risk</th><th>Main reason</th><th>Segment</th><th>Drug</th><th aria-label="Open" /></tr>
              </thead>
              <tbody>
                {view.critical.slice(0, NEEDS_ATTENTION_SHOWN).map((p) => (
                  <tr key={p.patient_idx} className="cursor-pointer group"
                      onClick={() => navigate(`/patients/${p.patient_idx}`)}>
                    <td className="font-mono text-xs font-semibold text-gray-700">#{p.patient_idx}</td>
                    <td><RiskBadge prob={p.dropout_prob} /></td>
                    <td className="text-gray-700">{p.driver_1 || '—'}</td>
                    <td><SegmentDot cluster={p.cluster} size="sm" /></td>
                    <td className="font-mono text-xs text-gray-500">{p.assigned_molecule}</td>
                    <td className="text-right">
                      <ChevronRight size={16} className="inline text-gray-300 group-hover:text-gray-500 transition-colors" />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {/* ── Why, and which groups ───────────────────────────────────── */}
      <div className="exec-bottom-grid">
        <div className="card p-5 animate-fade-up stagger-4">
          <SectionHeader title="Why your patients are at risk"
                         sub="Each patient's biggest reason, counted across your patients" />
          <div className="space-y-2.5 mt-1">
            {view.reasons.map(([reason, n], i) => (
              <div key={reason} className="flex items-center gap-3">
                <span className="text-[10px] font-bold rounded-full flex items-center justify-center flex-shrink-0"
                      style={{ width: 22, height: 22, minWidth: 22,
                               background: i === 0 ? '#FFEBEE' : '#F7FAFC', color: i === 0 ? '#C62828' : '#718096' }}>
                  {i + 1}
                </span>
                <div className="flex-1 min-w-0">
                  <div className="flex items-center justify-between mb-1">
                    <span className="text-xs text-gray-700 truncate font-medium">{reason}</span>
                    <span className="text-xs font-mono font-semibold ml-2 flex-shrink-0 text-gray-600">
                      {n.toLocaleString()} <span className="font-normal text-gray-400">· {pct(n)}%</span>
                    </span>
                  </div>
                  <div className="h-1.5 rounded-full overflow-hidden" style={{ background: '#EDF2F7' }}>
                    <div className="h-full rounded-full transition-all duration-700"
                         style={{ width: `${(n / (view.reasons[0]?.[1] || 1)) * 100}%`,
                                  background: `hsl(${220 - i * 20}, 65%, ${45 + i * 3}%)` }} />
                  </div>
                </div>
              </div>
            ))}
          </div>
        </div>

        <div className="card p-5 animate-fade-up stagger-5">
          <SectionHeader title="Your patients by segment" sub="Groups of patients with a similar profile"
            action={<Link to="/segments" className="inline-flex items-center gap-1 text-xs font-semibold whitespace-nowrap"
                          style={{ color: 'var(--color-primary)' }}>Segment Explorer <ArrowRight size={13} /></Link>} />
          <div className="flex rounded-md overflow-hidden h-2.5 mb-4">
            {view.segments.map((s) => s.n > 0 && (
              <div key={s.cluster} style={{ width: `${(s.n / total) * 100}%`, background: SEGMENT_COLORS[s.cluster] }}
                   title={`${SEGMENT_SHORT[s.cluster]}: ${pct(s.n)}%`} />
            ))}
          </div>
          <div className="divide-y divide-gray-100">
            {view.segments.map((s) => (
              <div key={s.cluster} className="flex items-center justify-between gap-3 py-2.5">
                <SegmentDot cluster={s.cluster} />
                <div className="flex items-center gap-4 text-xs flex-shrink-0">
                  <span className="text-gray-400">
                    {s.critical ? <><b style={{ color: '#C62828' }}>{s.critical.toLocaleString()}</b> critical</> : 'none critical'}
                  </span>
                  <span className="w-20 text-right"><Count n={s.n} share={pct(s.n)} /></span>
                </div>
              </div>
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}

/* A figure tile, styled like the Overview's hospital card. Links to the
   patient list filtered to exactly those patients, when there is such a list. */
function Tile({ icon: Icon, label, value, sub, color, to }) {
  const body = (
    <>
      <div className="flex items-center gap-1.5 text-[10px] font-semibold uppercase tracking-wider text-gray-400">
        <Icon size={12} style={{ color }} /> {label}
      </div>
      <div className="font-display text-2xl font-semibold text-gray-800 mt-1 leading-none">{value.toLocaleString()}</div>
      {sub && <div className="text-[11px] text-gray-400 mt-1">{sub}</div>}
    </>
  );
  return to
    ? <Link to={to} className="rounded-xl border border-gray-100 p-3 hover:border-blue-200 hover:bg-blue-50/30 transition-colors">{body}</Link>
    : <div className="rounded-xl border border-gray-100 p-3">{body}</div>;
}

function MiniHeading({ icon: Icon, children }) {
  return (
    <div className="text-[10px] font-semibold uppercase tracking-wider text-gray-400 mb-2 flex items-center gap-1.5">
      <Icon size={12} /> {children}
    </div>
  );
}

function Count({ n, share }) {
  return (
    <span className="font-semibold text-gray-800">
      {n.toLocaleString()} <span className="font-normal text-gray-400">· {share}%</span>
    </span>
  );
}
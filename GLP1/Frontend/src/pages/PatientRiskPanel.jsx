import { useState, useMemo, useRef, useEffect } from 'react';
import { useNavigate, useSearchParams, Link } from 'react-router-dom';
import { Search, Filter, ChevronLeft, ChevronRight, DollarSign, ChevronUp, ChevronDown, Settings2, X, Building2, Users } from 'lucide-react';
import CareTeamDialog from '../components/hospital/CareTeamDialog';
import { SegmentDot } from '../components/shared';
import { SkeletonTable } from '../components/shared/LoadingSkeleton';
import { SEGMENT_SHORT, SEGMENT_COLORS } from '../data/mockData';
import { usePatients } from '../hooks/usePatients';
import { useRole } from '../context/RoleContext';

// A patient's doctors. Rows from before a patient could have several carry
// only `doctor`.
const doctorsOf = (p) => p.doctors || (p.doctor ? [p.doctor] : []);

const PAGE_SIZE = 20;
const MOLECULES = ['All', 'SEMAGLUTIDE', 'TIRZEPATIDE', 'LIRAGLUTIDE', 'DULAGLUTIDE'];
const SEGMENTS  = ['All', ...SEGMENT_SHORT];

const isFinancial = (driver) =>
  driver.toLowerCase().includes('financial') ||
  driver.toLowerCase().includes('out-of-pocket') ||
  driver.toLowerCase().includes('cost');

/* ── Risk label from probability ──────────────────────────────────── */
function getRiskMeta(prob) {
  const pct = Math.round(prob * 100);
  if (pct >= 75) return { label: 'Critical', color: '#C62828', bg: '#FFEBEE' };
  if (pct >= 50) return { label: 'High',     color: '#EF6C00', bg: '#FFF3E0' };
  if (pct >= 25) return { label: 'Medium',   color: '#B45309', bg: '#FFFDE7' };
  return              { label: 'Low',      color: '#2E7D32', bg: '#E8F5E9' };
}

/* ── Interpolate bar color from green → yellow → orange → red ─────── */
function getRiskBarColor(pct) {
  // 0% = green, 33% = yellow, 66% = orange, 100% = red
  if (pct <= 25) return '#43A047';
  if (pct <= 40) return '#7CB342';
  if (pct <= 55) return '#FDD835';
  if (pct <= 70) return '#FF9800';
  if (pct <= 85) return '#EF5350';
  return '#C62828';
}

/* ── Risk Bar ─────────────────────────────────────────────────────── */
function RiskBar({ prob, prediction }) {
  const pct = Math.round(prob * 100);
  const meta = getRiskMeta(prob);
  const isDropout = prediction === 'Dropout Risk';
  const barColor = getRiskBarColor(pct);

  return (
    <div style={{ minWidth: 150 }}>
      <div className="flex items-center gap-2">
        {/* Bar track */}
        <div className="flex-1 relative h-2 rounded-full overflow-hidden" style={{ background: '#EDF2F7' }}>
          <div className="absolute top-0 left-0 h-full rounded-full transition-all duration-500" style={{ width: `${pct}%`, background: barColor }} />
        </div>
        {/* Percentage */}
        <span className="text-sm font-bold font-mono flex-shrink-0" style={{ color: meta.color, minWidth: 32, textAlign: 'right' }}>
          {pct}%
        </span>
      </div>
      {/* Label below */}
      <div className="flex items-center gap-1 mt-1">
        <span className="text-[10px] font-semibold px-1.5 py-0.5 rounded-full" style={{ background: meta.bg, color: meta.color }}>
          {meta.label}
        </span>
        <span className="text-[10px] text-gray-400">·</span>
        <span className="text-[10px]" style={{ color: isDropout ? '#C62828' : '#2E7D32' }}>
          {prediction}
        </span>
      </div>
    </div>
  );
}

/* ── Column Definitions ───────────────────────────────────────────── */
// `clinical` columns are the clinical layer: absent from a hospital admin's or
// insurer's rows (the server leaves them out), so never offered to them.
const ALL_COLUMNS = [
  { key: 'risk',      label: 'Risk',       alwaysOn: true,  sortable: true,  sortKey: 'dropout_prob' },
  { key: 'care_team', label: 'Care Team',  alwaysOn: false, sortable: false },
  { key: 'insurer',   label: 'Insurer',    alwaysOn: false, sortable: false },
  { key: 'segment',   label: 'Segment',    alwaysOn: false, sortable: false, clinical: true },
  { key: 'driver_1',  label: 'Top Driver', alwaysOn: false, sortable: false, clinical: true },
  { key: 'driver_2',  label: 'Driver 2',   alwaysOn: false, sortable: false, clinical: true },
  { key: 'drug',      label: 'Drug',       alwaysOn: false, sortable: false, clinical: true },
  { key: 'pharmacy',  label: 'Pharmacy',   alwaysOn: false, sortable: false, clinical: true },
  { key: 'oop_cost',  label: 'OOP Cost',   alwaysOn: false, sortable: true,  sortKey: 'avg_oop_cost', clinical: true },
  { key: 'bmi',       label: 'BMI',        alwaysOn: false, sortable: true,  sortKey: 'BMXBMI', clinical: true },
  { key: 'age',       label: 'Age',        alwaysOn: false, sortable: true,  sortKey: 'RIDAGEYR', clinical: true },
  { key: 'hba1c',     label: 'HbA1c',      alwaysOn: false, sortable: true,  sortKey: 'LBXGH', clinical: true },
];

const DEFAULT_VISIBLE = ['risk', 'care_team', 'segment', 'driver_1', 'drug', 'insurer'];

/* ── Column Settings Dropdown ─────────────────────────────────────── */
function ColumnSettings({ visible, setVisible, columns }) {
  const [open, setOpen] = useState(false);
  const ref = useRef(null);

  useEffect(() => {
    const handler = (e) => { if (ref.current && !ref.current.contains(e.target)) setOpen(false); };
    document.addEventListener('mousedown', handler);
    return () => document.removeEventListener('mousedown', handler);
  }, []);

  const toggle = (key) => {
    setVisible(prev => prev.includes(key) ? prev.filter(k => k !== key) : [...prev, key]);
  };

  return (
    <div className="relative" ref={ref}>
      <button
        onClick={() => setOpen(o => !o)}
        className="flex items-center gap-1.5 text-xs font-medium px-3 py-2 rounded-lg border transition-colors"
        style={{
          borderColor: open ? 'var(--color-primary)' : '#E2E8F0',
          color: open ? 'var(--color-primary)' : '#718096',
          background: open ? '#EBF4FF' : 'white',
        }}
      >
        <Settings2 size={13} /> Columns
      </button>
      {open && (
        <div className="absolute right-0 top-full mt-1 z-50 card p-3 shadow-lg" style={{ minWidth: 200 }}>
          <div className="text-xs font-semibold text-gray-500 mb-2 uppercase tracking-wider">Toggle Columns</div>
          <div className="space-y-1">
            {columns.map(col => (
              <label
                key={col.key}
                className="flex items-center gap-2.5 px-2 py-1.5 rounded-md hover:bg-gray-50 cursor-pointer text-xs"
                style={{ opacity: col.alwaysOn ? 0.5 : 1 }}
              >
                <div
                  className="w-4 h-4 rounded border flex items-center justify-center flex-shrink-0 transition-colors"
                  style={{
                    borderColor: visible.includes(col.key) ? 'var(--color-primary)' : '#CBD5E0',
                    background: visible.includes(col.key) ? 'var(--color-primary)' : 'white',
                  }}
                >
                  {visible.includes(col.key) && (
                    <svg width="10" height="8" viewBox="0 0 10 8" fill="none"><path d="M1 4L3.5 6.5L9 1" stroke="white" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"/></svg>
                  )}
                </div>
                <span className="text-gray-700">{col.label}</span>
                {col.alwaysOn && <span className="text-[9px] text-gray-400 ml-auto">Required</span>}
                <input
                  type="checkbox"
                  checked={visible.includes(col.key)}
                  onChange={() => !col.alwaysOn && toggle(col.key)}
                  disabled={col.alwaysOn}
                  className="sr-only"
                />
              </label>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

/* ── Sort Header Button ───────────────────────────────────────────── */
function SortHeader({ label, sortKey: sk, currentSort, currentDir, onSort }) {
  const isActive = currentSort === sk;
  return (
    <button
      onClick={() => onSort(sk)}
      className="flex items-center gap-1 hover:text-gray-800 transition-colors group whitespace-nowrap"
    >
      {label}
      <span className="flex flex-col" style={{ lineHeight: 0 }}>
        <ChevronUp
          size={10}
          style={{
            color: isActive && currentDir === 'asc' ? 'var(--color-primary)' : '#CBD5E0',
            marginBottom: -2,
          }}
        />
        <ChevronDown
          size={10}
          style={{
            color: isActive && currentDir === 'desc' ? 'var(--color-primary)' : '#CBD5E0',
            marginTop: -2,
          }}
        />
      </span>
    </button>
  );
}

/* ── Main Component ───────────────────────────────────────────────── */
export default function PatientRiskPanel() {
  const navigate = useNavigate();
  const { isCostView, needsReason, canAssign } = useRole();
  const { patients, loading, redacted, reload } = usePatients();
  // Filters the Overview and the Staff page link in with.
  const [searchParams, setSearchParams] = useSearchParams();
  const care = { doctor: searchParams.get('doctor'), nurse: searchParams.get('nurse'),
                 unassigned: searchParams.get('unassigned') };
  const [search, setSearch]           = useState('');
  const [segFilter, setSegFilter]     = useState('All');
  const [molFilter, setMolFilter]     = useState('All');
  const [minRisk, setMinRisk]         = useState(() => Number(searchParams.get('min_risk')) || 0);
  const [predFilter, setPredFilter]   = useState(() => searchParams.get('prediction') || 'All');
  const [selected, setSelected]       = useState([]);
  const [assigning, setAssigning]     = useState(null);
  const [financialOnly, setFinancialOnly] = useState(false);
  const [sortKey, setSortKey]         = useState('dropout_prob');
  const [sortDir, setSortDir]         = useState('desc');
  const [page, setPage]               = useState(0);
  const [visibleCols, setVisibleCols] = useState(DEFAULT_VISIBLE);
  const [filtersOpen, setFiltersOpen] = useState(false);

  const filtered = useMemo(() => {
    let d = [...patients];
    // driver_1 is absent from an overview-layer row, so search is by number there.
    if (search)            d = d.filter(p => String(p.patient_idx).includes(search) || (p.driver_1 || '').toLowerCase().includes(search.toLowerCase()));
    if (segFilter !== 'All') d = d.filter(p => SEGMENT_SHORT[p.cluster] === segFilter);
    if (molFilter !== 'All') d = d.filter(p => p.assigned_molecule === molFilter);
    if (predFilter !== 'All') d = d.filter(p => p.prediction === predFilter);
    if (financialOnly)     d = d.filter(p => isFinancial(p.driver_1 || ''));
    if (care.doctor)       d = d.filter(p => doctorsOf(p).some(x => x.id === care.doctor));
    if (care.nurse)        d = d.filter(p => (p.nurses || []).some(n => n.id === care.nurse));
    if (care.unassigned === 'doctor') d = d.filter(p => !doctorsOf(p).length);
    if (care.unassigned === 'nurse')  d = d.filter(p => !(p.nurses || []).length);
    d = d.filter(p => p.dropout_prob * 100 >= minRisk);
    d.sort((a, b) => {
      const av = a[sortKey], bv = b[sortKey];
      return sortDir === 'asc' ? (av > bv ? 1 : -1) : (av < bv ? 1 : -1);
    });
    return d;
  }, [patients, search, segFilter, molFilter, predFilter, financialOnly, minRisk, sortKey, sortDir,
      care.doctor, care.nurse, care.unassigned]);

  const pages     = Math.ceil(filtered.length / PAGE_SIZE);
  const visible   = filtered.slice(page * PAGE_SIZE, (page + 1) * PAGE_SIZE);
  const highRisk  = filtered.filter(p => p.dropout_prob >= 0.75).length;
  const financial = filtered.filter(p => isFinancial(p.driver_1 || '')).length;

  // The care-team filter, named rather than shown as an id.
  const careLabel = care.doctor ? `Doctor: ${patients.flatMap(doctorsOf).find(x => x.id === care.doctor)?.name || 'selected'}`
    : care.nurse ? `Hospital nurse: ${patients.flatMap(p => p.nurses || []).find(n => n.id === care.nurse)?.name || 'selected'}`
    : care.unassigned === 'doctor' ? 'No doctor assigned'
    : care.unassigned === 'nurse' ? 'No hospital nurse assigned' : null;
  const clearCare = () => {
    const next = new URLSearchParams(searchParams);
    ['doctor', 'nurse', 'unassigned'].forEach(k => next.delete(k));
    setSearchParams(next);
  };

  const pageIdxs = visible.map(p => p.patient_idx);
  const allSelected = pageIdxs.length > 0 && pageIdxs.every(i => selected.includes(i));
  const toggleRow = (i) => setSelected(s => (s.includes(i) ? s.filter(x => x !== i) : [...s, i]));
  const toggleAll = () => setSelected(allSelected ? selected.filter(i => !pageIdxs.includes(i))
                                                  : [...new Set([...selected, ...pageIdxs])]);
  // One hospital's staff for the dialog; a mixed selection (superadmin, all
  // hospitals) is refused by the server anyway.
  const selectionHospital = (() => {
    const hs = new Set(patients.filter(p => selected.includes(p.patient_idx)).map(p => p.hospital_id));
    return hs.size === 1 ? [...hs][0] : undefined;
  })();

  const toggleSort = (key) => {
    if (sortKey === key) setSortDir(d => d === 'asc' ? 'desc' : 'asc');
    else { setSortKey(key); setSortDir('desc'); }
    setPage(0);
  };

  const resetFilters = () => {
    setSearch(''); setSegFilter('All'); setMolFilter('All');
    setPredFilter('All'); setFinancialOnly(false); setMinRisk(0); setPage(0);
  };

  const hasCol = (key) => visibleCols.includes(key);

  const activeFilterCount = [
    segFilter !== 'All', molFilter !== 'All', predFilter !== 'All', financialOnly, minRisk > 0
  ].filter(Boolean).length;

  /* ── Cell renderers ── */
  const renderCell = (col, p) => {
    switch (col.key) {
      case 'risk':
        return <RiskBar prob={p.dropout_prob} prediction={p.prediction} />;
      case 'segment':
        return <SegmentDot cluster={p.cluster} size="sm" />;
      case 'driver_1':
        return (
          <div style={{ maxWidth: 190 }}>
            <div className="text-xs text-gray-700 truncate">{p.driver_1}</div>
            <div className="text-[10px] mt-0.5 font-medium"
                 style={{ color: p.driver_1_direction.includes('increases') ? 'var(--risk-high)' : 'var(--color-positive)' }}>
              {p.driver_1_direction.includes('increases') ? '↑' : '↓'} {p.driver_1_direction}
            </div>
          </div>
        );
      case 'driver_2':
        return (
          <div style={{ maxWidth: 160 }}>
            <div className="text-xs text-gray-500 truncate">{p.driver_2}</div>
            <div className="text-[10px] mt-0.5"
                 style={{ color: p.driver_2_direction.includes('increases') ? '#EF6C00' : '#718096' }}>
              {p.driver_2_direction.includes('increases') ? '↑' : '↓'}
            </div>
          </div>
        );
      case 'care_team':
        return (
          <div style={{ maxWidth: 180 }}>
            <div className="text-xs truncate" style={{ color: doctorsOf(p).length ? '#2D3748' : '#C62828' }}
                 title={doctorsOf(p).map(x => x.name).join(', ')}>
              {doctorsOf(p).length ? doctorsOf(p).map(x => x.name).join(', ') : 'No doctor'}
            </div>
            <div className="text-[10px] text-gray-400 truncate mt-0.5">
              {(p.nurses || []).length ? p.nurses.map(n => n.name).join(', ') : 'No hospital nurse'}
            </div>
          </div>
        );
      case 'insurer':
        return <span className="text-xs text-gray-600">{p.insurer?.name || '—'}</span>;
      case 'pharmacy':
        return <span className="text-xs text-gray-600">{p.pharmacy || '—'}</span>;
      case 'drug':
        return (
          <span className="text-xs font-mono px-2 py-1 rounded-md" style={{ background: '#F7FAFC', color: '#4A5568' }}>
            {p.assigned_molecule.slice(0, 4)}
          </span>
        );
      case 'oop_cost':
        return <span className="font-mono text-xs text-gray-700">${p.avg_oop_cost.toFixed(0)}</span>;
      case 'bmi':
        return <span className="font-mono text-xs text-gray-700">{p.BMXBMI}</span>;
      case 'age':
        return <span className="font-mono text-xs text-gray-700">{p.RIDAGEYR}y</span>;
      case 'hba1c':
        return <span className="font-mono text-xs text-gray-700">{p.LBXGH}</span>;
      default:
        return null;
    }
  };

  // The overview layer (hospital admins, insurers) never offers a clinical column.
  const offeredColumns = ALL_COLUMNS.filter(c => !(redacted && c.clinical));
  const visibleColumns = offeredColumns.filter(c => hasCol(c.key));

  if (loading) {
    return (
      <div className="risk-panel-page animate-fade-in">
        <SkeletonTable rows={20} />
      </div>
    );
  }

  /* ── Filter select component ── */
  const FilterSelect = ({ label, value, onChange, options }) => (
    <div className="flex-1" style={{ minWidth: 140 }}>
      <label className="block text-[10px] font-semibold uppercase tracking-wider text-gray-400 mb-1">{label}</label>
      <select value={value} onChange={e => { onChange(e.target.value); setPage(0); }}
        className="w-full text-xs rounded-lg border border-gray-200 px-2.5 py-2 bg-white focus:outline-none focus:ring-1 focus:ring-blue-400">
        {options.map(o => <option key={o}>{o}</option>)}
      </select>
    </div>
  );

  return (
    <div className="risk-panel-page animate-fade-in">
      {/* ── Overview-layer banner (hospital admins, insurers) ─────── */}
      {needsReason && (
        <div className="flex items-center gap-2.5 px-4 py-3 rounded-xl text-sm font-medium mb-4 animate-fade-up"
          style={{ background: '#EBF4FF', color: '#1B4F8A', border: '1px solid #BFDBFE' }}>
          <Building2 size={15} />
          Adherence risk, care team and insurer for each patient. Vitals, medication and dropout drivers open one
          patient at a time, with a reason.
        </div>
      )}

      {/* ── Finance-view context banner ──────────────────────────── */}
      {isCostView && !needsReason && (
        <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-2 sm:gap-3 px-4 py-3 rounded-xl text-sm font-medium mb-4 animate-fade-up"
          style={{ background: '#EBF4FF', color: '#1B4F8A', border: '1px solid #BFDBFE' }}>
          <span className="flex items-center gap-2.5">
            <Building2 size={15} />
            Finance view — This panel is designed for the care team. Individual patient data is available for reference.
          </span>
          <div className="flex flex-wrap items-center gap-3 text-xs font-semibold flex-shrink-0">
            <Link to="/cost" className="hover:underline underline-offset-2" style={{ color: '#1B4F8A' }}>Cost-Effectiveness →</Link>
            <Link to="/budget" className="hover:underline underline-offset-2" style={{ color: '#1B4F8A' }}>Budget Simulator →</Link>
          </div>
        </div>
      )}

      {/* ── Top bar: title + search + settings ──────────────────── */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3 mb-4">
        <div>
          <h1 className="text-xl font-semibold text-gray-800" style={{ fontFamily: 'DM Serif Display, serif' }}>
            Patients
          </h1>
          <p className="text-xs text-gray-400 mt-0.5">
            {filtered.length} patients · {highRisk} critical risk{!redacted && ` · ${financial} financial barrier`}
          </p>
        </div>
        <div className="flex items-center gap-2">
          <div className="relative flex-1 sm:flex-none">
            <Search size={13} className="absolute left-2.5 top-1/2 -translate-y-1/2 text-gray-400" />
            <input
              value={search}
              onChange={e => { setSearch(e.target.value); setPage(0); }}
              placeholder={redacted ? 'Search by patient number…' : 'Search patients or drivers…'}
              className="text-xs pl-8 pr-3 py-2 rounded-lg border border-gray-200 focus:outline-none focus:ring-1 focus:ring-blue-400 bg-white w-full sm:w-[220px]"
            />
          </div>
          <button
            onClick={() => setFiltersOpen(o => !o)}
            className="flex items-center gap-1.5 text-xs font-medium px-3 py-2 rounded-lg border transition-colors relative"
            style={{
              borderColor: filtersOpen ? 'var(--color-primary)' : '#E2E8F0',
              color: filtersOpen ? 'var(--color-primary)' : '#718096',
              background: filtersOpen ? '#EBF4FF' : 'white',
            }}
          >
            <Filter size={13} /> Filters
            {activeFilterCount > 0 && (
              <span className="absolute -top-1.5 -right-1.5 w-4 h-4 rounded-full text-[9px] font-bold flex items-center justify-center text-white"
                    style={{ background: 'var(--color-primary)' }}>
                {activeFilterCount}
              </span>
            )}
          </button>
          {canAssign && selected.length > 0 && (
            <button onClick={() => setAssigning(selected)}
              className="flex items-center gap-1.5 text-xs font-semibold px-3 py-2 rounded-lg text-white"
              style={{ background: 'var(--color-primary)' }}>
              <Users size={13} /> Assign care team ({selected.length})
            </button>
          )}
          <ColumnSettings visible={visibleCols} setVisible={setVisibleCols} columns={offeredColumns} />
        </div>
      </div>

      {careLabel && (
        <div className="flex items-center gap-2 mb-4 text-xs">
          <span className="text-gray-400">Showing</span>
          <span className="inline-flex items-center gap-1.5 font-medium px-2.5 py-1 rounded-full"
                style={{ background: '#EBF4FF', color: 'var(--color-primary)' }}>
            {careLabel}
            <button onClick={clearCare} aria-label="Clear care-team filter"><X size={11} /></button>
          </span>
        </div>
      )}

      {/* ── Collapsible Filter Bar ─────────────────────────────── */}
      {filtersOpen && (
        <div className="card p-4 mb-4 animate-fade-up" style={{ animationDuration: '0.2s' }}>
          <div className="flex items-center justify-between mb-3">
            <span className="text-xs font-semibold text-gray-600 flex items-center gap-1.5">
              <Filter size={12} /> Filter Options
            </span>
            <button onClick={() => setFiltersOpen(false)} className="text-gray-400 hover:text-gray-600">
              <X size={14} />
            </button>
          </div>
          <div className="flex flex-wrap gap-3 items-end">
            {!redacted && <FilterSelect label="Segment" value={segFilter} onChange={setSegFilter} options={SEGMENTS} />}
            {!redacted && <FilterSelect label="Molecule" value={molFilter} onChange={setMolFilter} options={MOLECULES} />}
            <FilterSelect label="Prediction" value={predFilter} onChange={setPredFilter}
              options={['All', 'Dropout Risk', 'Likely Adherent']} />

            {/* Min risk slider */}
            <div className="flex-1" style={{ minWidth: 160 }}>
              <label className="block text-[10px] font-semibold uppercase tracking-wider text-gray-400 mb-1">
                Min Risk: <span className="font-mono text-blue-600">{minRisk}%</span>
              </label>
              <input type="range" min={0} max={90} step={5} value={minRisk}
                onChange={e => { setMinRisk(+e.target.value); setPage(0); }} />
            </div>

            {/* Financial toggle */}
            {!redacted && <div className="flex-shrink-0">
              <label className="block text-[10px] font-semibold uppercase tracking-wider text-gray-400 mb-1">Financial Only</label>
              <label className="flex items-center gap-2 cursor-pointer mt-1">
                <div className="relative flex-shrink-0">
                  <input type="checkbox" checked={financialOnly}
                    onChange={e => { setFinancialOnly(e.target.checked); setPage(0); }}
                    className="sr-only" />
                  <div className="w-8 h-4 rounded-full transition-colors"
                       style={{ background: financialOnly ? 'var(--color-primary)' : '#CBD5E0' }} />
                  <div className="absolute top-0.5 left-0.5 w-3 h-3 rounded-full bg-white shadow transition-transform"
                       style={{ transform: financialOnly ? 'translateX(16px)' : 'none' }} />
                </div>
                <DollarSign size={12} className="text-orange-500" />
              </label>
            </div>}

            {/* Reset */}
            <button onClick={resetFilters}
              className="flex-shrink-0 text-xs text-gray-500 hover:text-gray-700 px-3 py-2 rounded-lg border border-gray-200 hover:border-gray-300 transition-colors">
              Reset
            </button>
          </div>

          {/* Active filter badges */}
          {activeFilterCount > 0 && (
            <div className="flex flex-wrap gap-1.5 mt-3 pt-3 border-t border-gray-100">
              {segFilter !== 'All' && (
                <span className="inline-flex items-center gap-1 text-[10px] font-medium px-2 py-1 rounded-full"
                      style={{ background: '#EBF4FF', color: 'var(--color-primary)' }}>
                  Segment: {segFilter}
                  <button onClick={() => setSegFilter('All')}><X size={10} /></button>
                </span>
              )}
              {molFilter !== 'All' && (
                <span className="inline-flex items-center gap-1 text-[10px] font-medium px-2 py-1 rounded-full"
                      style={{ background: '#EBF4FF', color: 'var(--color-primary)' }}>
                  Drug: {molFilter}
                  <button onClick={() => setMolFilter('All')}><X size={10} /></button>
                </span>
              )}
              {predFilter !== 'All' && (
                <span className="inline-flex items-center gap-1 text-[10px] font-medium px-2 py-1 rounded-full"
                      style={{ background: '#EBF4FF', color: 'var(--color-primary)' }}>
                  {predFilter}
                  <button onClick={() => setPredFilter('All')}><X size={10} /></button>
                </span>
              )}
              {financialOnly && (
                <span className="inline-flex items-center gap-1 text-[10px] font-medium px-2 py-1 rounded-full"
                      style={{ background: '#FFF3E0', color: '#EF6C00' }}>
                  Financial only
                  <button onClick={() => setFinancialOnly(false)}><X size={10} /></button>
                </span>
              )}
              {minRisk > 0 && (
                <span className="inline-flex items-center gap-1 text-[10px] font-medium px-2 py-1 rounded-full"
                      style={{ background: '#FFEBEE', color: '#C62828' }}>
                  Risk ≥ {minRisk}%
                  <button onClick={() => setMinRisk(0)}><X size={10} /></button>
                </span>
              )}
            </div>
          )}
        </div>
      )}

      {/* ── Risk Legend strip ────────────────────────────────────── */}
      <div className="flex items-center gap-4 mb-4 px-1 flex-wrap">
        <span className="text-[10px] font-semibold uppercase tracking-wider text-gray-400">Risk Levels:</span>
        {[
          ['Critical', '≥75%', '#C62828', '#FFEBEE'],
          ['High', '50–74%', '#EF6C00', '#FFF3E0'],
          ['Medium', '25–49%', '#B45309', '#FFFDE7'],
          ['Low', '<25%', '#2E7D32', '#E8F5E9'],
        ].map(([label, range, color, bg]) => (
          <span key={label} className="inline-flex items-center gap-1.5 text-[10px]">
            <span className="w-2.5 h-2.5 rounded-sm" style={{ background: color }} />
            <span className="font-medium" style={{ color }}>{label}</span>
            <span className="text-gray-400">{range}</span>
          </span>
        ))}
      </div>

      {/* ── Table ────────────────────────────────────────────────── */}
      <div className="card overflow-hidden">
        <div className="overflow-x-auto">
          <table className="data-table">
            <thead>
              <tr>
                {canAssign && (
                  <th style={{ width: 32 }}>
                    <input type="checkbox" checked={allSelected} onChange={toggleAll}
                      aria-label="Select every patient on this page" />
                  </th>
                )}
                <th style={{ width: 50 }}>#</th>
                {visibleColumns.map(col => (
                  <th key={col.key}>
                    {col.sortable ? (
                      <SortHeader
                        label={col.label}
                        sortKey={col.sortKey}
                        currentSort={sortKey}
                        currentDir={sortDir}
                        onSort={toggleSort}
                      />
                    ) : (
                      col.label
                    )}
                  </th>
                ))}
                <th style={{ width: 70 }}>Action</th>
              </tr>
            </thead>
            <tbody>
              {visible.map((p) => (
                <tr key={p.patient_idx} className="cursor-pointer group"
                    onClick={() => navigate(`/patients/${p.patient_idx}`)}>
                  {canAssign && (
                    <td onClick={e => e.stopPropagation()}>
                      <input type="checkbox" checked={selected.includes(p.patient_idx)}
                        onChange={() => toggleRow(p.patient_idx)} aria-label={`Select patient ${p.patient_idx}`} />
                    </td>
                  )}
                  <td>
                    <span className="text-xs font-mono text-gray-400">#{String(p.patient_idx).padStart(3, '0')}</span>
                  </td>
                  {visibleColumns.map(col => (
                    <td key={col.key}>{renderCell(col, p)}</td>
                  ))}
                  <td>
                    <button
                      className="text-xs px-3 py-1.5 rounded-lg font-medium transition-all opacity-70 group-hover:opacity-100"
                      style={{ background: '#EBF4FF', color: '#1B4F8A' }}
                      onClick={e => { e.stopPropagation(); navigate(`/patients/${p.patient_idx}`); }}>
                      View →
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>

        {visible.length === 0 && (
          <div className="flex flex-col items-center justify-center h-40 text-sm text-gray-400 gap-2">
            <Search size={20} className="text-gray-300" />
            No patients match the current filters.
          </div>
        )}

        {/* Pagination */}
        <div className="flex items-center justify-between px-4 py-3 border-t border-gray-100">
          <span className="text-xs text-gray-500">
            Showing <b className="text-gray-700">{page * PAGE_SIZE + 1}–{Math.min((page + 1) * PAGE_SIZE, filtered.length)}</b> of {filtered.length}
          </span>
          <div className="flex items-center gap-1.5">
            <button disabled={page === 0} onClick={() => setPage(p => p - 1)}
              className="w-7 h-7 rounded-lg flex items-center justify-center border border-gray-200 disabled:opacity-30 hover:border-blue-300 transition-colors">
              <ChevronLeft size={13} />
            </button>
            {Array.from({ length: Math.min(5, pages) }, (_, i) => i + Math.max(0, page - 2))
              .filter(i => i < pages)
              .map(i => (
                <button key={i} onClick={() => setPage(i)}
                  className="w-7 h-7 rounded-lg text-xs font-medium border transition-colors"
                  style={{
                    borderColor: i === page ? 'var(--color-primary)' : '#E2E8F0',
                    background:  i === page ? 'var(--color-primary)' : 'white',
                    color:       i === page ? 'white' : '#4A5568',
                  }}>
                  {i + 1}
                </button>
              ))}
            <button disabled={page >= pages - 1} onClick={() => setPage(p => p + 1)}
              className="w-7 h-7 rounded-lg flex items-center justify-center border border-gray-200 disabled:opacity-30 hover:border-blue-300 transition-colors">
              <ChevronRight size={13} />
            </button>
          </div>
        </div>
      </div>

      {assigning && (
        <CareTeamDialog
          patientIdxs={assigning}
          hospitalId={selectionHospital}
          current={assigning.length === 1 ? (() => {
            const row = patients.find(p => p.patient_idx === assigning[0]);
            return { doctorIds: doctorsOf(row || {}).map(x => x.id), nurseIds: (row?.nurses || []).map(n => n.id) };
          })() : undefined}
          onClose={() => setAssigning(null)}
          onSaved={() => { setAssigning(null); setSelected([]); reload(); }}
        />
      )}
    </div>
  );
}
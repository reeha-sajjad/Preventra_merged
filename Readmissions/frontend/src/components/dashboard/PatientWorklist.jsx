import React, { useEffect, useState, useMemo } from 'react';
import { useSearchParams } from 'react-router-dom';
import { getPatients, getPatientGroups } from '../../api';
import ConditionFilter from './ConditionFilter';
import RiskBadge from '../shared/RiskBadge';
import TrendStatusBadge from '../shared/TrendStatusBadge';
import CareTeamDialog from '../shared/CareTeamDialog';
import { can, ASSIGN_ROLES, INSURER_ROLES } from '../../roles';
import { ChevronRight, ChevronLeft, AlertCircle, ArrowUp, ArrowDown, ArrowDownUp,
         Download, Users } from 'lucide-react';

// ---------------------------------------------------------------------------
// CSV export
// ---------------------------------------------------------------------------
const CSV_COLUMNS = [
  { key: 'id', label: 'Patient ID' },
  { key: 'discharge_score', label: 'Discharge Score' },
  { key: 'current_score', label: 'Current Score' },
  { key: 'trend_delta', label: 'Change Since Discharge' },
  { key: 'monitoring_status', label: 'Trend Status' },
  { key: 'risk_band', label: 'Risk Band' },
  { key: 'primary_diagnosis', label: 'Admitting Diagnosis' },
  // The export is what gets opened in Excel and worked through, so it carries
  // the diagnoses the table folds away and every condition the patient has -
  // not just the one group they are monitored under.
  { key: 'other_diagnoses', label: 'Other Diagnoses',
    get: (r) => (r.diagnoses || []).slice(1).map((d) => d.title).join('; ') },
  { key: 'n_diagnoses_coded', label: 'Diagnoses Coded This Stay' },
  { key: 'conditions', label: 'Conditions Present',
    get: (r) => (r.conditions || []).map((c) => c.label).join('; ') },
  { key: 'group_label', label: 'Monitoring Group' },
  { key: 'primary_driver_label', label: 'Primary Driver' },
  { key: 'discharge_date', label: 'Discharge Date' },
  { key: 'doctor', label: 'Doctor', get: (r) => r.doctor?.name || '' },
  { key: 'nurses', label: 'Hospital nurses', get: (r) => (r.nurses || []).map((n) => n.name).join('; ') },
  { key: 'insurer', label: 'Insurer', get: (r) => r.insurer?.name || '' },
];
// An insurer's export: the hospital and member ID instead of the care team -
// the server sends no doctors or nurses to an insurer (api/hospital.for_insurer).
const INSURER_CSV_KEYS = new Set(['doctor', 'nurses', 'insurer']);
const INSURER_CSV_EXTRA = [
  { key: 'member_id', label: 'Member ID', get: (r) => r.member_id || '' },
  { key: 'hospital', label: 'Hospital', get: (r) => r.hospital || '' },
];
// Left out of a hospital admin's or insurer's export, as the server leaves them
// out of their rows: the clinical layer opens one patient at a time.
const CLINICAL_CSV_KEYS = new Set(['primary_diagnosis', 'other_diagnoses', 'n_diagnoses_coded',
  'conditions', 'group_label', 'primary_driver_label']);

function escapeCsvField(value) {
  const str = String(value ?? '');
  if (/[",\n]/.test(str)) return `"${str.replace(/"/g, '""')}"`;
  return str;
}

function downloadWorklistCsv(rows, redacted) {
  const insurer = can(INSURER_ROLES);
  const columns = [...CSV_COLUMNS.filter((c) => (!redacted || !CLINICAL_CSV_KEYS.has(c.key))
                                         && !(insurer && INSURER_CSV_KEYS.has(c.key))),
                   ...(insurer ? INSURER_CSV_EXTRA : [])];
  const header = columns.map((c) => c.label).join(',');
  const lines = rows.map((row) =>
    columns.map((c) => escapeCsvField(c.get ? c.get(row) : row[c.key])).join(','));
  const csv = [header, ...lines].join('\n');

  const blob = new Blob([csv], { type: 'text/csv;charset=utf-8;' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = `patient_worklist_${new Date().toISOString().split('T')[0]}.csv`;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}

// ---------------------------------------------------------------------------
// Diagnoses
// ---------------------------------------------------------------------------
/**
 * Every diagnosis recorded for the admission, not only the admitting one.
 *
 * A patient averages 11.8 coded diagnoses; the Phase-1 extract keeps the
 * principal plus three secondaries, and only the principal keeps its ICD code.
 * The count of what is not shown travels with the list rather than being
 * quietly dropped, because "4 diagnoses" and "4 of 18" mean different things
 * when you are deciding whether a patient is sicker than the row suggests.
 *
 * Conditions that put the patient in a monitoring group are marked, so a row
 * admitted for liver disease and returned under a heart failure filter shows
 * on its face which line matched.
 */
function DiagnosisCell({ row, highlight }) {
  const [open, setOpen] = useState(false);
  const list = row.diagnoses?.length
    ? row.diagnoses
    : [{ position: 'principal', code: row.primary_icd_code, title: row.primary_diagnosis, group: '' }];

  const principal = list[0];
  const secondary = list.slice(1);
  // A condition the user filtered on is always worth showing; otherwise the
  // extra lines stay folded so the table keeps its shape.
  const matched = secondary.filter((d) => d.group && highlight.includes(d.group));
  const shown = open ? secondary : matched;
  const hidden = secondary.length - shown.length;
  const notCoded = Math.max((row.n_diagnoses_coded || 0) - list.length, 0);

  const Line = ({ d, bold }) => (
    <div className="flex items-baseline gap-1.5">
      <span className={`truncate ${bold ? 'text-gray-700' : 'text-gray-500'}`} title={d.title}>
        {d.title || <span className="text-gray-400">not coded</span>}
      </span>
      {d.group && highlight.includes(d.group) && (
        <span className="shrink-0 text-[10px] uppercase tracking-wide text-ns-navy bg-ns-navy/5 border border-ns-navy/15 rounded px-1 py-px">
          {d.group_label}
        </span>
      )}
    </div>
  );

  return (
    <div className="space-y-0.5">
      <Line d={principal} bold />
      {principal.code && (
        <div className="text-xs text-gray-400">ICD {principal.code}</div>
      )}

      {shown.map((d, i) => <Line key={`${d.title}-${i}`} d={d} />)}

      {(hidden > 0 || open) && secondary.length > 0 && (
        <button
          type="button"
          onClick={(e) => { e.stopPropagation(); setOpen((o) => !o); }}
          className="-my-1.5 py-1.5 text-xs text-ns-navy/70 hover:text-ns-navy hover:underline"
        >
          {open ? 'show less' : `+${hidden} more ${hidden === 1 ? 'diagnosis' : 'diagnoses'}`}
        </button>
      )}

      {open && notCoded > 0 && (
        <div className="text-[11px] text-gray-400">
          {notCoded} further {notCoded === 1 ? 'diagnosis' : 'diagnoses'} coded for this stay, not in the extract
        </div>
      )}

      {row.group_label && row.clinical_group !== 'general' && (
        <div className="text-xs text-ns-navy/70 pt-0.5" title={row.group_evidence || undefined}>
          Monitored as: {row.group_label}
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Change since discharge
// ---------------------------------------------------------------------------
function DeltaCell({ delta }) {
  if (delta === null || delta === undefined) {
    return <span className="text-xs text-gray-300">—</span>;
  }
  const rounded = parseFloat(delta);
  if (rounded > 0.4) {
    return (
      <span className="inline-flex items-center text-sm font-bold text-risk-high">
        <ArrowUp size={14} className="mr-0.5" />+{rounded.toFixed(1)}
      </span>
    );
  }
  if (rounded < -0.4) {
    return (
      <span className="inline-flex items-center text-sm font-bold text-risk-low">
        <ArrowDown size={14} className="mr-0.5" />{rounded.toFixed(1)}
      </span>
    );
  }
  return <span className="text-sm font-medium text-gray-400">{rounded.toFixed(1)}</span>;
}

// ---------------------------------------------------------------------------
// Care team
// ---------------------------------------------------------------------------
function CareTeamCell({ row }) {
  // An insurer gets the hospital, not the clinical staff.
  if (can(INSURER_ROLES)) return <div className="text-sm text-gray-700">{row.hospital || '—'}</div>;
  const nurses = row.nurses || [];
  return (
    <div className="space-y-0.5 text-sm">
      <div className={row.doctor ? 'text-gray-700' : 'text-risk-high'}>
        {row.doctor ? row.doctor.name : 'No doctor'}
      </div>
      <div className="text-xs text-gray-400">
        {nurses.length ? nurses.map((n) => n.name).join(', ') : 'No nurse'}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Skeleton
// ---------------------------------------------------------------------------
function WorklistSkeleton() {
  return (
    <div className="bg-white rounded-xl border border-gray-200 shadow-sm overflow-hidden animate-pulse">
      <div className="bg-gray-50 border-b border-gray-200 px-6 py-4 grid grid-cols-6 gap-4">
        {['w-24','w-20','w-28','w-48','w-24','w-8'].map((w, i) => (
          <div key={i} className={`h-4 bg-gray-200 rounded ${w}`} />
        ))}
      </div>
      {[...Array(8)].map((_, i) => (
        <div key={i} className="px-6 py-4 border-b border-gray-100 grid grid-cols-6 gap-4 items-center">
          <div className="h-6 bg-gray-100 rounded w-12" />
          <div className="h-5 bg-gray-100 rounded w-16" />
          <div className="h-4 bg-gray-100 rounded w-20" />
          <div className="h-4 bg-gray-100 rounded w-full" />
          <div className="h-4 bg-gray-100 rounded w-24" />
          <div className="h-4 bg-gray-100 rounded w-6" />
        </div>
      ))}
    </div>
  );
}

// What the worklist's current score is made of, for the hover text: the
// discharge score, then one re-score per monitoring week.
const scoreHistory = (row) => {
  const weeks = Math.max(0, (row.weeks_tracked || 1) - 1);
  return `Latest score after ${weeks} week${weeks === 1 ? '' : 's'} of monitoring`;
};

export default function PatientWorklist({ onSelectPatient, refreshKey = 0 }) {
  const [patients, setPatients]   = useState([]);
  const [total, setTotal]         = useState(0);
  const [groups, setGroups]       = useState([]);
  const [loading, setLoading]     = useState(true);
  const [exporting, setExporting] = useState(false);
  const [error, setError]         = useState(null);
  const [searchParams, setSearchParams] = useSearchParams();
  const [currentPage, setCurrentPage] = useState(1);
  const rowsPerPage = 25;
  // A hospital admin or insurer gets the overview layer only; the server says
  // so on the response, and the diagnosis and driver columns go.
  const [redacted, setRedacted] = useState(false);
  const canAssign = can(ASSIGN_ROLES);
  const [selected, setSelected] = useState([]);
  const [assigning, setAssigning] = useState(null);       // patient ids in the dialog
  const [reloadKey, setReloadKey] = useState(0);

  const sort = searchParams.get('sort') || 'score-desc';

  const handleSort = (column) => {
    let newSort = `${column}-desc`;
    if (sort === `${column}-desc`) {
      newSort = `${column}-asc`;
    }
    const newParams = new URLSearchParams(searchParams);
    newParams.set('sort', newSort);
    setSearchParams(newParams);
  };

  // Every filter, the sort and the page are query parameters now.
  //
  // They used to be applied in the browser over the whole cohort, which meant
  // downloading all of it before rendering a 25-row page - 27 seconds against
  // Atlas at 2,000 patients, and worse at 4,000. MongoDB does all of it against
  // an index and returns one page, so latency no longer grows with the cohort.
  // Several conditions at once, comma separated in the URL so a filtered
  // worklist stays shareable. `match` is any (union) or all (intersection).
  const groupFilter  = (searchParams.get('group') || '')
    .split(',').map((g) => g.trim()).filter(Boolean);
  const matchMode    = searchParams.get('match') === 'all' ? 'all' : 'any';
  const bandFilter   = searchParams.get('filter') || 'All';
  const trendFilter  = searchParams.get('trend')  || 'All';
  const search       = searchParams.get('search') || '';
  // Care-team filters, from the Staff page's drill-down and the Overview.
  const care = { doctor: searchParams.get('doctor') || '', nurse: searchParams.get('nurse') || '',
                 unassigned: searchParams.get('unassigned') || '' };

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    setSelected([]);
    getPatients({ group: groupFilter, match: matchMode, band: bandFilter,
                  status: trendFilter, q: search, sort, ...care,
                  page: currentPage, limit: rowsPerPage })
      .then((resp) => {
        if (cancelled) return;
        setPatients(resp?.data || []);
        setTotal(resp?.total ?? 0);
        setRedacted(Boolean(resp?.redacted));
        setLoading(false);
      })
      .catch((err) => { if (!cancelled) { setError(err.message); setLoading(false); } });
    return () => { cancelled = true; };
    // groupFilter is a fresh array every render; keying the effect on its
    // joined form stops it refetching on every unrelated state change.
  }, [groupFilter.join(','), matchMode, bandFilter, trendFilter, search, sort, currentPage, // eslint-disable-line react-hooks/exhaustive-deps
      care.doctor, care.nurse, care.unassigned, reloadKey, refreshKey]);

  // The conditions actually present in this batch, with counts, so the filter
  // never offers an option that would return nothing.
  useEffect(() => {
    getPatientGroups()
      .then((res) => setGroups(res?.groups || []))
      .catch(() => setGroups([]));
  }, []);

  // Back to page 1 whenever a filter or the sort changes — but only if we are
  // not already there, otherwise this fires a second, identical fetch.
  useEffect(() => {
    setCurrentPage((p) => (p === 1 ? p : 1));
  }, [groupFilter.join(','), matchMode, bandFilter, trendFilter, search, sort, // eslint-disable-line react-hooks/exhaustive-deps
      care.doctor, care.nurse, care.unassigned]);

  // The server has already filtered, sorted and paged. What arrives IS the page.
  const paginatedData = patients;
  const totalPages    = Math.max(Math.ceil(total / rowsPerPage), 1);

  // Export covers the whole filtered set, not the page on screen, so it issues
  // its own request rather than serialising what happens to be rendered.
  const handleExport = async () => {
    setExporting(true);
    try {
      const resp = await getPatients({ group: groupFilter, match: matchMode,
                                       band: bandFilter, status: trendFilter,
                                       q: search, sort, ...care, page: 1, limit: 5000 });
      downloadWorklistCsv(resp?.data || [], Boolean(resp?.redacted));
    } catch (err) {
      setError(err.message);
    } finally {
      setExporting(false);
    }
  };



  const getScoreColor = (band) => {
    const n = band?.toLowerCase();
    if (n === 'high')   return 'text-risk-high';
    if (n === 'medium') return 'text-risk-medium';
    return 'text-risk-low';
  };

  const pageIds = paginatedData.map((r) => r.id);
  const allSelected = pageIds.length > 0 && pageIds.every((id) => selected.includes(id));
  const toggleRow = (id) =>
    setSelected((ids) => (ids.includes(id) ? ids.filter((x) => x !== id) : [...ids, id]));
  const toggleAll = () => setSelected(allSelected ? [] : pageIds);
  // The dialog offers the staff of the patients' hospital. A selection that
  // spans hospitals (only possible for the superadmin viewing every hospital)
  // is refused by the server, so the dialog gets no hospital to filter on.
  const selectionHospital = (() => {
    const hs = new Set(paginatedData.filter((r) => selected.includes(r.id)).map((r) => r.hospital_id));
    return hs.size === 1 ? [...hs][0] : undefined;
  })();

  const SortHeader = ({ label, column }) => {
    const isSorted = sort.startsWith(column);
    const isDesc = sort === `${column}-desc`;
    
    return (
      <th 
        className="px-6 py-4 cursor-pointer hover:bg-gray-100 transition-colors select-none group"
        onClick={() => handleSort(column)}
      >
        <div className="flex items-center space-x-1.5">
          <div className={`flex flex-col ${isSorted ? 'text-ns-navy' : 'text-gray-300 group-hover:text-gray-400'}`}>
            {!isSorted && <ArrowDownUp size={14} />}
            {isSorted && isDesc && <ArrowDown size={14} />}
            {isSorted && !isDesc && <ArrowUp size={14} />}
          </div>
          <span className={isSorted ? "text-ns-navy font-semibold" : ""}>{label}</span>
        </div>
      </th>
    );
  };

  if (loading) return <WorklistSkeleton />;

  if (error) {
    return (
      <div className="bg-red-50 border border-red-200 rounded-xl p-4 flex items-start space-x-3 text-red-800">
        <AlertCircle size={20} className="mt-0.5 shrink-0" />
        <div>
          <div className="font-semibold">Failed to load patient worklist</div>
          <div className="text-sm mt-0.5 text-red-600">{error}</div>
        </div>
      </div>
    );
  }

  return (
    <div className="bg-white rounded-xl border border-gray-200 shadow-sm flex flex-col overflow-hidden">
      <div className="px-6 py-3 border-b border-gray-100 flex flex-wrap items-center justify-between gap-3">
        {/* Filter by condition. Server-side, because it has to be evaluated
            against the whole cohort rather than the rows already loaded. Not
            offered to the roles that see the overview layer only - the server
            would ignore it for them. */}
        {redacted ? (
          <p className="text-sm text-gray-500">
            Diagnoses and risk drivers open per patient, with a reason.
          </p>
        ) : (
        <ConditionFilter
          groups={groups}
          selected={groupFilter}
          match={matchMode}
          onChange={(keys, mode) => {
            const next = new URLSearchParams(searchParams);
            if (keys.length) next.set('group', keys.join(','));
            else next.delete('group');
            if (keys.length > 1 && mode === 'all') next.set('match', 'all');
            else next.delete('match');
            setSearchParams(next);
          }}
        />
        )}
        <div className="flex items-center gap-2">
        {canAssign && selected.length > 0 && (
          <button
            type="button"
            onClick={() => setAssigning(selected)}
            className="flex items-center space-x-2 px-3 py-1.5 text-sm font-semibold text-white bg-ns-navy rounded-lg hover:bg-blue-900 transition-colors"
          >
            <Users size={16} />
            <span>Assign care team ({selected.length})</span>
          </button>
        )}
        <button
          type="button"
          onClick={handleExport}
          disabled={total === 0 || exporting}
          className="flex items-center space-x-2 px-3 py-1.5 text-sm font-medium text-gray-600 border border-gray-300 rounded-lg hover:bg-gray-50 transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
        >
          <Download size={16} />
          <span>{exporting ? 'Preparing…' : `Export CSV${total ? ` (${total.toLocaleString()})` : ''}`}</span>
        </button>
        </div>
      </div>
      {/* Below `md` the same rows render as cards. Eight columns is 1,671px of
          table, which on a phone means swiping sideways five times to read one
          patient - technically scrollable, practically unusable. The card keeps
          the same data and the same tap target. */}
      <div className="divide-y divide-gray-100 md:hidden">
        {paginatedData.length > 0 ? (
          paginatedData.map((row) => (
            <button
              key={row.id}
              onClick={() => onSelectPatient(row.id)}
              className="block w-full px-4 py-3 text-left transition-colors active:bg-blue-50/60"
            >
              <div className="flex items-start justify-between gap-3">
                <div className="min-w-0">
                  <div className="flex items-baseline gap-2">
                    <span className={`text-2xl font-bold ${getScoreColor(row.risk_band)}`}>
                      {parseFloat(row.risk_score).toFixed(1)}%
                    </span>
                    <RiskBadge riskBand={row.risk_band} size="sm" />
                  </div>
                  <div className="mt-0.5 text-xs text-gray-500" title={scoreHistory(row)}>
                    {row.weeks_tracked > 1 && row.discharge_score != null
                      ? `Now · was ${parseFloat(row.discharge_score).toFixed(1)}% at discharge`
                      : 'At discharge'}
                  </div>
                </div>
                <div className="flex shrink-0 flex-col items-end gap-1">
                  <TrendStatusBadge status={row.monitoring_status} size="sm" short />
                  <DeltaCell delta={row.trend_delta} />
                </div>
              </div>

              <div className="mt-2 text-sm font-semibold text-gray-800">{row.id}</div>
              {!redacted && (
                <div className="mt-1 text-sm text-gray-700">
                  <DiagnosisCell row={row} highlight={groupFilter} />
                </div>
              )}
              {row.primary_driver_label && (
                <div className="mt-1 text-xs text-gray-600">{row.primary_driver_label}</div>
              )}
              <div className="mt-2"><CareTeamCell row={row} /></div>
              <div className="mt-1 text-xs text-gray-400">
                Discharged {row.discharge_date}{row.insurer ? ` · ${row.insurer.name}` : ''}{row.member_id ? ` · ${row.member_id}` : ''}
              </div>
            </button>
          ))
        ) : (
          <div className="px-4 py-12 text-center text-gray-500">
            No patients match the current filters.
          </div>
        )}
      </div>

      <div className="hidden overflow-x-auto md:block">
        <table className="w-full text-left text-sm whitespace-nowrap">
          <thead className="bg-gray-50 border-b border-gray-200 text-gray-500 font-medium">
            <tr>
              {canAssign && (
                <th className="pl-6 pr-2 py-4 w-8">
                  <input type="checkbox" checked={allSelected} onChange={toggleAll}
                    aria-label="Select every patient on this page" className="accent-ns-navy" />
                </th>
              )}
              <SortHeader label="Current Risk" column="score" />
              <SortHeader label="Since Discharge" column="delta" />
              <SortHeader label="Trend" column="trend" />
              <SortHeader label="Risk Band" column="band" />
              <SortHeader label="Patient ID" column="id" />
              <th className="px-6 py-4">{can(INSURER_ROLES) ? 'Hospital' : 'Care Team'}</th>
              {!redacted && <SortHeader label="Diagnoses" column="diagnosis" />}
              {/* Not "Primary": the column now shows one of the patient's top
                  three drivers, not necessarily the highest-ranked one. */}
              {!redacted && <SortHeader label="Key Risk Driver" column="driver" />}
              <SortHeader label="Discharge Date" column="date" />
              {!can(INSURER_ROLES) && <th className="px-6 py-4">Insurer</th>}
            </tr>
          </thead>
          <tbody className="divide-y divide-gray-100">
            {paginatedData.length > 0 ? (
              paginatedData.map((row) => (
                <tr
                  key={row.id}
                  onClick={() => onSelectPatient(row.id)}
                  className="hover:bg-blue-50/50 cursor-pointer transition-colors group"
                >
                  {canAssign && (
                    <td className="pl-6 pr-2 py-3" onClick={(e) => e.stopPropagation()}>
                      <input type="checkbox" checked={selected.includes(row.id)}
                        onChange={() => toggleRow(row.id)} aria-label={`Select ${row.id}`}
                        className="accent-ns-navy" />
                    </td>
                  )}
                  <td className="px-6 py-3">
                    <span className={`text-xl font-bold ${getScoreColor(row.risk_band)}`}>
                      {parseFloat(row.risk_score).toFixed(1)}%
                    </span>
                    {/* The headline is the latest weekly re-score; the line
                        under it is where the patient started, at discharge. */}
                    {row.weeks_tracked > 1 && row.discharge_score != null ? (
                      <div className="text-xs text-gray-500 mt-0.5" title={scoreHistory(row)}>
                        Now · was {parseFloat(row.discharge_score).toFixed(1)}% at discharge
                      </div>
                    ) : (
                      <div className="text-xs text-gray-500 mt-0.5">At discharge</div>
                    )}
                  </td>
                  <td className="px-6 py-3">
                    <DeltaCell delta={row.trend_delta} />
                  </td>
                  <td className="px-6 py-3">
                    <TrendStatusBadge status={row.monitoring_status} size="sm" short />
                  </td>
                  <td className="px-6 py-3">
                    <RiskBadge riskBand={row.risk_band} size="sm" />
                  </td>
                  <td className="px-6 py-3 font-semibold text-gray-800">
                    {row.id}
                    {row.member_id && <div className="text-xs font-mono font-normal text-gray-500">{row.member_id}</div>}
                  </td>
                  <td className="px-6 py-3"><CareTeamCell row={row} /></td>
                  {!redacted && (
                    <td className="px-6 py-3 max-w-sm text-gray-700">
                      <DiagnosisCell row={row} highlight={groupFilter} />
                    </td>
                  )}
                  {!redacted && (
                    <td className="px-6 py-3 max-w-xs truncate text-gray-600">{row.primary_driver_label}</td>
                  )}
                  <td className="px-6 py-3 text-gray-500">{row.discharge_date}</td>
                  {!can(INSURER_ROLES) && <td className="px-6 py-3 text-gray-500">{row.insurer?.name || '—'}</td>}
                </tr>
              ))
            ) : (
              <tr>
                <td colSpan={(redacted ? 8 : 10) + (canAssign ? 1 : 0) - (can(INSURER_ROLES) ? 1 : 0)} className="px-6 py-12 text-center text-gray-500">
                  No patients match the current filters.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>

      {totalPages > 0 && (
        <div className="flex flex-col items-center justify-between gap-2 border-t border-gray-200 bg-gray-50 px-4 py-3 sm:flex-row sm:px-6">
          <div className="text-sm font-medium text-gray-500">
            Showing {(currentPage - 1) * rowsPerPage + 1} to{' '}
            {Math.min(currentPage * rowsPerPage, total)} of {total.toLocaleString()} records
          </div>
          <div className="flex items-center space-x-2">
            <button
              onClick={() => setCurrentPage((p) => Math.max(1, p - 1))}
              disabled={currentPage === 1}
              aria-label="Previous page"
              className="rounded border border-gray-200 bg-white p-2 text-gray-500 hover:bg-gray-50 disabled:cursor-not-allowed disabled:opacity-50"
            >
              <ChevronLeft size={18} />
            </button>
            <span className="px-2 text-sm font-medium text-gray-700">
              Page {currentPage} of {totalPages}
            </span>
            <button
              onClick={() => setCurrentPage((p) => Math.min(totalPages, p + 1))}
              disabled={currentPage === totalPages}
              aria-label="Next page"
              className="rounded border border-gray-200 bg-white p-2 text-gray-500 hover:bg-gray-50 disabled:cursor-not-allowed disabled:opacity-50"
            >
              <ChevronRight size={18} />
            </button>
          </div>
        </div>
      )}
      {assigning && (
        <CareTeamDialog
          patientIds={assigning}
          hospitalId={selectionHospital}
          current={assigning.length === 1 ? (() => {
            const row = paginatedData.find((r) => r.id === assigning[0]);
            return { doctorId: row?.doctor?.id || '', nurseIds: (row?.nurses || []).map((n) => n.id) };
          })() : undefined}
          onClose={() => setAssigning(null)}
          onSaved={() => { setAssigning(null); setReloadKey((k) => k + 1); }}
        />
      )}
    </div>
  );
}
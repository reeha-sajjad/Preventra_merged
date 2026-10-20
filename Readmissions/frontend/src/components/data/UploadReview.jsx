import React, { useMemo, useState } from 'react';
import { FileText, Sparkles, CheckCircle2, AlertTriangle, Loader2, Lightbulb, Database } from 'lucide-react';
import {
  TABLE_LABELS, MAIN_TABLE, OTHER_TABLES, DETAIL_TABLES, COMPLETE_FIELDS, PREFIX_RE, DROP,
  fieldLabel, codeLabel, panel, button, ghost, input, inputInline,
} from './dataFormat';
import { Notice } from './dataParts';

const DATE_FIELD = /(_datetime|_date|^date_of_birth)$/;
// Values that usually mean "readmitted" (api/data_reader.py _OUTCOME_POSITIVE).
const POSITIVE = /^\s*(<\s*30|yes|y|1|1\.0|true|t|readmitted|within\s*30.*|30\s*d.*)\s*$/i;
// Column names that usually hold the outcome (api/data_reader.py _OUTCOME_NAME).
const OUTCOME_NAME = /readmi|readmission|outcome|label|target|returned|bounce/i;

/** The fields a file of a table must give to be stored (api/data_upload.py _validate). */
const needsOf = (table, tables) => (!table ? [] : DETAIL_TABLES.includes(table) ? ['encounter_key'] : tables[table]?.required || []);

/** The reading the server proposed, as the page edits it. */
function initial(report) {
  const p = report.proposal;
  return {
    files: p.files.map((f) => ({ filename: f.filename, rows: f.rows, table: f.table || '',
      columns: { ...f.columns }, drop: [...(f.drop || [])], join_share: f.join_share })),
    outcome: p.outcome ? { column: p.outcome.column, positive: [...p.outcome.positive] } : null,
    codes: JSON.parse(JSON.stringify(p.codes || {})),
    dayfirst: p.dayfirst,
    historyOnly: report.kind === 'discharges' && Boolean(p.outcome),
    prefix: '',
  };
}

/**
 * Step 3 of an upload: how the files were read, for a person to check and
 * correct before anything is stored - which file is which, which column is
 * which of our fields, which says who was readmitted, what the hospital's
 * codes mean. Most uploads need nothing changed here.
 */
export default function UploadReview({ report, prefix, onConfirm, onCancel }) {
  const [state, setState] = useState(() => initial(report));
  const [busy, setBusy] = useState(false);
  const [errors, setErrors] = useState([]);
  const { kind } = report;
  const mainTable = MAIN_TABLE[kind];
  const main = state.files.find((f) => f.table === mainTable);

  const setFile = (filename, change) => setState((s) => ({
    ...s, files: s.files.map((f) => (f.filename === filename ? { ...f, ...change(f) } : f)) }));

  const setTable = (filename, table) => setState((s) => ({
    ...s,
    files: s.files.map((f) => {
      if (f.filename === filename) {
        const allowed = new Set(table ? report.tables[table].fields : []);
        const columns = Object.fromEntries(Object.entries(f.columns).map(([c, v]) => [c, allowed.has(v) ? v : null]));
        return { ...f, table, columns };
      }
      // One file is the discharges (or the check-ins): choosing another one leaves this one out.
      return table === mainTable && f.table === mainTable ? { ...f, table: '' } : f;
    }),
    outcome: table === mainTable ? null : s.outcome,
  }));

  const setColumn = (filename, column, choice) => {
    setFile(filename, (f) => {
      const columns = { ...f.columns };
      // One column per field: the field moves to this column.
      if (choice && choice !== DROP) Object.keys(columns).forEach((c) => { if (columns[c] === choice) columns[c] = null; });
      columns[column] = choice && choice !== DROP ? choice : null;
      const drop = f.drop.filter((c) => c !== column).concat(choice === DROP ? [column] : []);
      return { columns, drop };
    });
    if (choice && state.outcome?.column === column && main?.filename === filename) {
      setState((s) => ({ ...s, outcome: null }));
    }
  };

  // The coded fields some file's column now gives, and that column's values.
  const coded = useMemo(() => {
    const out = [];
    state.files.filter((f) => f.table).forEach((f) => {
      Object.entries(f.columns).forEach(([col, field]) => {
        if (!field || !report.vocabularies[field]) return;
        const seen = report.columns[f.filename]?.[col]?.values;
        const values = seen ? Object.entries(seen) : Object.keys(state.codes[field] || {}).map((v) => [v, null]);
        out.push({ field, column: col, filename: f.filename, values });
      });
    });
    return out;
  }, [state.files, state.codes, report]);

  const problems = useMemo(() => {
    const out = [];
    const mains = state.files.filter((f) => f.table === mainTable);
    if (mains.length !== 1) out.push(`Choose which file holds the ${kind === 'discharges' ? 'discharges' : 'weekly check-ins'}.`);
    state.files.filter((f) => f.table).forEach((f) => {
      const have = new Set(Object.values(f.columns));
      needsOf(f.table, report.tables).filter((n) => !have.has(n)).forEach((n) => out.push(`${f.filename}: choose the column for ${fieldLabel(n).toLowerCase()}.`));
    });
    if (state.outcome && !state.outcome.positive.length) out.push('Tick which outcome values mean readmitted within 30 days.');
    if (!prefix && !PREFIX_RE.test(state.prefix)) out.push('Choose a patient id prefix.');
    return out;
  }, [state, prefix, kind, mainTable, report.tables]);

  const mainFields = new Set(Object.values(main?.columns || {}));
  if (mainFields.has('date_of_birth')) mainFields.add('age_years');
  const notForBuiltIn = kind === 'discharges' && main ? COMPLETE_FIELDS.filter((f) => !mainFields.has(f)) : [];
  const hasDates = state.files.some((f) => f.table && Object.values(f.columns).some((v) => v && DATE_FIELD.test(v)));
  const rows = main?.rows || 0;

  const submit = async () => {
    setBusy(true); setErrors([]);
    const codes = {};
    coded.forEach(({ field, values }) => {
      codes[field] = { ...(codes[field] || {}),
        ...Object.fromEntries(values.map(([v]) => [v, state.codes[field]?.[v] || null])) };
    });
    try {
      await onConfirm({
        files: state.files.map((f) => ({ filename: f.filename, table: f.table || null, columns: f.columns,
          drop: f.drop.filter((c) => !f.columns[c]) })),
        outcome: kind === 'discharges' && state.outcome ? state.outcome : null,
        codes,
        dayfirst: state.dayfirst,
        patient_prefix: prefix ? null : state.prefix,
        history_only: kind === 'discharges' && state.historyOnly,
      });
    } catch (e) {
      setErrors(e.message.split('; '));
      setBusy(false);
    }
  };

  return (
    <div className="space-y-5">
      <div className={panel}>
        <div className="flex flex-wrap items-center justify-between gap-2">
          <h2 className="text-base font-semibold text-gray-800">3. Check how your files were read</h2>
          <ReadBy report={report} />
        </div>
        <div className="mt-3 grid gap-5 lg:grid-cols-2">
          <ul className="space-y-1.5 text-sm text-gray-700">
            {report.summary.map((line, i) => (
              <li key={i} className="flex items-start gap-2"><CheckCircle2 size={15} className="mt-0.5 shrink-0 text-green-600" />{line}</li>
            ))}
            {(report.proposal.problems || []).map((p, i) => (
              <li key={`p${i}`} className="flex items-start gap-2 text-amber-800"><AlertTriangle size={15} className="mt-0.5 shrink-0" />{p}</li>
            ))}
          </ul>
          {report.gaps.length > 0 && (
            <div className="rounded-lg bg-indigo-50/60 p-3">
              <p className="flex items-center gap-1.5 text-sm font-semibold text-indigo-900">
                <Lightbulb size={15} /> Would make predictions better, if you have it
              </p>
              <ul className="mt-2 space-y-1.5 text-sm">
                {report.gaps.map((g) => (
                  <li key={g.what}><span className="font-medium text-gray-800">{g.what}.</span>{' '}
                    <span className="text-gray-600">{g.why}</span></li>
                ))}
              </ul>
            </div>
          )}
        </div>
      </div>

      {state.files.map((f) => (
        <FileCard key={f.filename} file={f} kind={kind} report={report} needs={needsOf(f.table, report.tables)}
          outcomeColumn={f.table === mainTable ? state.outcome?.column : null}
          onTable={(t) => setTable(f.filename, t)} onColumn={(c, v) => setColumn(f.filename, c, v)} />
      ))}

      {kind === 'discharges' && main && (
        <OutcomeCard file={main} summary={report.columns[main.filename]} outcome={state.outcome}
          onChange={(outcome) => setState((s) => ({ ...s, outcome,
            historyOnly: outcome && !s.outcome ? true : s.historyOnly }))} />
      )}

      {coded.length > 0 && (
        <CodesCard coded={coded} codes={state.codes} vocabularies={report.vocabularies}
          onChange={(field, value, ours) => setState((s) => ({
            ...s, codes: { ...s.codes, [field]: { ...(s.codes[field] || {}), [value]: ours || null } } }))} />
      )}

      <div className={panel}>
        <h2 className="text-base font-semibold text-gray-800">4. Store them</h2>
        <div className="mt-3 space-y-4 text-sm">
          {kind === 'discharges' && (
            <fieldset className="space-y-2">
              <legend className="mb-1 font-medium text-gray-800">Are these patients leaving hospital now?</legend>
              <label className="flex items-start gap-2 text-gray-700">
                <input type="radio" className="mt-0.5" checked={state.historyOnly}
                       onChange={() => setState((s) => ({ ...s, historyOnly: true }))} />
                <span><strong>No, these are past discharges.</strong> Keep them to train your own model and as earlier
                  admissions of future patients. They do not go on the worklist.</span>
              </label>
              <label className="flex items-start gap-2 text-gray-700">
                <input type="radio" className="mt-0.5" checked={!state.historyOnly}
                       onChange={() => setState((s) => ({ ...s, historyOnly: false }))} />
                <span><strong>Yes, current patients.</strong> Score them and put them on the worklist.</span>
              </label>
            </fieldset>
          )}

          {hasDates && (
            <label className="block max-w-sm">
              <span className="font-medium text-gray-800">Dates are written</span>
              <select className={`${input} mt-1`} value={state.dayfirst === null ? '' : String(state.dayfirst)}
                onChange={(e) => setState((s) => ({ ...s, dayfirst: e.target.value === '' ? null : e.target.value === 'true' }))}>
                <option value="true">Day first: 31/12/2026</option>
                <option value="false">Month first: 12/31/2026</option>
                <option value="">Year first, or both read the same</option>
              </select>
            </label>
          )}

          {!prefix && (
            <label className="block max-w-sm">
              <span className="font-medium text-gray-800">Patient id prefix</span>
              <input className={`${input} mt-1 uppercase`} value={state.prefix} maxLength={10} placeholder="CGH"
                     onChange={(e) => setState((s) => ({ ...s, prefix: e.target.value.toUpperCase().replace(/[^A-Z0-9]/g, '') }))} />
              <span className="mt-1 block text-xs text-gray-500">
                2 to 10 letters or digits that start every patient id at this hospital: CGH makes CGH-P1931. Set once.
              </span>
            </label>
          )}

          {notForBuiltIn.length > 0 && !state.historyOnly && (
            <Notice tone="amber">
              The built-in model needs {notForBuiltIn.map((f) => fieldLabel(f).toLowerCase()).join(', ')}, which
              these files do not have. The discharges are stored, and scored once you train a model on your own data.
            </Notice>
          )}

          {problems.length > 0 && (
            <Notice tone="amber">
              <ul className="list-disc space-y-0.5 pl-4">{problems.map((p) => <li key={p}>{p}</li>)}</ul>
            </Notice>
          )}
          {errors.length > 0 && (
            <Notice tone="red"><ul className="list-disc space-y-0.5 pl-4">{errors.map((p) => <li key={p}>{p}</li>)}</ul></Notice>
          )}

          <div className="flex flex-wrap gap-3">
            <button type="button" className={button} disabled={problems.length > 0 || busy} onClick={submit}>
              {busy ? <Loader2 size={15} className="animate-spin" /> : <Database size={15} />}
              Store {rows.toLocaleString()} {kind === 'discharges' ? 'discharges' : 'check-ins'}
            </button>
            <button type="button" className={ghost} disabled={busy} onClick={onCancel}>Start over</button>
          </div>
          <p className="text-xs text-gray-500">
            The reading is saved too: your next export of the same shape is read the same way, whether it is
            uploaded here, sent by your systems or read from your database.
          </p>
        </div>
      </div>
    </div>
  );
}

function ReadBy({ report }) {
  if (report.ai) {
    return <span className="inline-flex items-center gap-1 rounded-full bg-indigo-50 px-2.5 py-1 text-xs font-medium text-indigo-700"><Sparkles size={13} /> Read with your AI model</span>;
  }
  return (
    <span className="rounded-full bg-gray-100 px-2.5 py-1 text-xs font-medium text-gray-600"
          title={report.ai_error ? `The AI model did not answer (${report.ai_error})` : 'No AI model is chosen for reading files: Settings, AI'}>
      {report.ai_error ? 'AI model unavailable: read by rules' : 'Read by rules'}
    </span>
  );
}

/** What a column looks like, from its summary: never more than a short category list. */
function looks(s) {
  if (!s) return '';
  if (s.personal) return 'Personal details: never stored';
  const empty = s.missing_pct >= 1 ? ` · ${Math.round(s.missing_pct)}% empty` : '';
  const values = s.values ? Object.keys(s.values) : [];
  if (s.kind === 'number') return `Number, ${fmtNum(s.range[0])} to ${fmtNum(s.range[1])}${empty}`;
  if (s.kind === 'date') return `Date${empty}`;
  if (values.length) return `${values.slice(0, 4).join(', ')}${values.length > 4 ? ` +${values.length - 4} more` : ''}${empty}`;
  if (s.kind === 'identifier') return `Identifier, every row different${empty}`;
  return `${s.kind === 'text' ? 'Text' : 'Codes'}, ${s.n_unique.toLocaleString()} different values${empty}`;
}
const fmtNum = (n) => (Math.abs(n) >= 1000 ? Math.round(n).toLocaleString() : String(Math.round(n * 100) / 100));

function FileCard({ file, kind, report, needs, outcomeColumn, onTable, onColumn }) {
  const summary = report.columns[file.filename] || {};
  const table = file.table;
  const fields = table ? report.tables[table].fields : [];
  const keeps = table && report.tables[table].extras_allowed;
  const columns = Object.keys(file.columns);
  const used = columns.filter((c) => file.columns[c]).length;
  const kept = keeps ? columns.filter((c) => !file.columns[c] && !file.drop.includes(c) && c !== outcomeColumn
    && !summary[c]?.personal).length : 0;
  const missing = needs.filter((n) => !Object.values(file.columns).includes(n));
  const keyOf = (field) => columns.find((c) => file.columns[c] === field);
  const options = [MAIN_TABLE[kind], ...OTHER_TABLES[kind]];

  return (
    <div className={`${panel} ${table ? '' : 'opacity-75'}`}>
      <div className="flex flex-wrap items-center gap-3">
        <FileText size={18} className="text-gray-400" />
        <span className="font-semibold text-gray-800">{file.filename}</span>
        <span className="text-xs text-gray-500">{file.rows.toLocaleString()} rows</span>
        <select className={`${inputInline} ml-auto`} value={table} onChange={(e) => onTable(e.target.value)}>
          {options.map((t) => <option key={t} value={t}>{TABLE_LABELS[t]}</option>)}
          <option value="">Leave this file out</option>
        </select>
      </div>

      {table && (
        <p className="mt-2 text-sm text-gray-600">
          {table === 'encounters' && <>Each discharge: <Col name={keyOf('encounter_key')} />. </>}
          {table !== 'encounters' && DETAIL_TABLES.includes(table) && <>Belongs to the discharge in <Col name={keyOf('encounter_key')} />. </>}
          {['encounters', 'patients', 'weeks'].includes(table) && <>Each patient: <Col name={keyOf('patient_key')} />. </>}
          {file.join_share != null && <>{Math.round(file.join_share * 100)}% of its rows match the main file. </>}
          {used} column{used === 1 ? '' : 's'} matched to ours{keeps ? `, ${kept} more kept for your own model` : ''}.
        </p>
      )}
      {missing.length > 0 && (
        <p className="mt-1 flex items-center gap-1.5 text-sm text-amber-800">
          <AlertTriangle size={15} /> Choose the column for {missing.map((m) => fieldLabel(m).toLowerCase()).join(', ')} below.
        </p>
      )}

      {table && (
        <details className="mt-3" open={missing.length > 0 || columns.length <= 12}>
          <summary className="cursor-pointer text-sm font-medium text-ns-navy">Columns ({columns.length})</summary>
          <div className="mt-2 overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-gray-200 text-left text-xs uppercase tracking-wide text-gray-500">
                  <th className="py-1.5 pr-3 font-medium">Your column</th>
                  <th className="py-1.5 pr-3 font-medium">What it holds</th>
                  <th className="py-1.5 font-medium">Used as</th>
                </tr>
              </thead>
              <tbody>
                {columns.map((c) => {
                  const s = summary[c];
                  const field = file.columns[c];
                  const value = field || (file.drop.includes(c) ? DROP : '');
                  return (
                    <tr key={c} className="border-b border-gray-100 last:border-0">
                      <td className="py-1.5 pr-3 font-mono text-xs text-gray-800">{c}</td>
                      <td className="max-w-xs truncate py-1.5 pr-3 text-xs text-gray-500" title={looks(s)}>{looks(s)}</td>
                      <td className="py-1.5">
                        {c === outcomeColumn && !field ? (
                          <span className="text-xs font-medium text-indigo-700">The readmission outcome (below)</span>
                        ) : (
                          <select className={`${inputInline} w-full min-w-[12rem] ${field ? 'border-green-300 bg-green-50/50' : ''}`}
                                  value={value} onChange={(e) => onColumn(c, e.target.value)}>
                            {keeps && !s?.personal && <option value="">Keep for your own model</option>}
                            {keeps && s?.personal && <option value="">Not stored (personal)</option>}
                            {!keeps && <option value="">Not used</option>}
                            {keeps && !s?.personal && <option value={DROP}>Don&apos;t use</option>}
                            <optgroup label="Our fields">
                              {fields.map((f) => <option key={f} value={f}>{fieldLabel(f)}{needs.includes(f) ? ' (needed)' : ''}</option>)}
                            </optgroup>
                          </select>
                        )}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </details>
      )}
    </div>
  );
}

const Col = ({ name }) => (name
  ? <code className="rounded bg-gray-100 px-1 py-0.5 text-xs text-gray-800">{name}</code>
  : <span className="font-medium text-amber-700">not chosen</span>);

function OutcomeCard({ file, summary, outcome, onChange }) {
  // Columns that could say who came back: not one of our fields, a short list
  // of values (a number only when it is 0/1), outcome-like names first.
  const could = (c) => {
    const s = summary[c];
    const n = Object.keys(s?.values || {}).length;
    return !file.columns[c] && n >= 2 && n <= 10 && (s.kind !== 'number' || n === 2);
  };
  const choices = Object.keys(file.columns).filter((c) => c === outcome?.column || could(c))
    .sort((a, b) => Number(OUTCOME_NAME.test(b)) - Number(OUTCOME_NAME.test(a)));
  const values = outcome ? summary[outcome.column]?.values || {} : {};
  const total = Object.values(values).reduce((a, b) => a + b, 0);
  const yes = Object.entries(values).filter(([v]) => outcome.positive.includes(v)).reduce((a, [, n]) => a + n, 0);
  const toggle = (v) => onChange({ ...outcome,
    positive: outcome.positive.includes(v) ? outcome.positive.filter((x) => x !== v) : [...outcome.positive, v] });

  return (
    <div className={panel}>
      <h3 className="font-semibold text-gray-800">Readmission outcome</h3>
      <p className="mt-1 text-sm text-gray-600">
        Does {file.filename} say which patients were readmitted within 30 days? It is what your own model learns
        from. Without it, Preventra works the outcome out from admission and discharge dates where it has them.
      </p>
      <select className={`${input} mt-3 max-w-sm`} value={outcome?.column || ''}
        onChange={(e) => onChange(e.target.value ? { column: e.target.value,
          positive: Object.keys(summary[e.target.value]?.values || {}).filter((v) => POSITIVE.test(v)) } : null)}>
        <option value="">No, there is no such column</option>
        {choices.map((c) => <option key={c} value={c}>{c}</option>)}
      </select>
      {outcome && (
        <div className="mt-3">
          <p className="text-sm text-gray-700">Which values mean <strong>readmitted within 30 days</strong>?</p>
          <div className="mt-2 flex flex-wrap gap-2">
            {Object.entries(values).map(([v, n]) => (
              <label key={v} className={`inline-flex cursor-pointer items-center gap-2 rounded-md border px-3 py-1.5 text-sm ${outcome.positive.includes(v)
                ? 'border-ns-navy bg-ns-navy/5 text-ns-navy' : 'border-gray-300 text-gray-700'}`}>
                <input type="checkbox" checked={outcome.positive.includes(v)} onChange={() => toggle(v)} />
                <span className="font-mono">{v}</span><span className="text-xs text-gray-500">{n.toLocaleString()}</span>
              </label>
            ))}
          </div>
          {outcome.positive.length > 0 && total > 0 && (
            <p className="mt-2 text-xs text-gray-500">
              {(100 * yes / total).toFixed(1)}% of discharges readmitted within 30 days; the other values count as not readmitted.
            </p>
          )}
        </div>
      )}
    </div>
  );
}

function CodesCard({ coded, codes, vocabularies, onChange }) {
  const open = coded.some(({ field, values }) => values.some(([v]) => !codes[field]?.[v]));
  return (
    <details className={panel} open={open}>
      <summary className="cursor-pointer font-semibold text-gray-800">
        Your codes{open ? ' (some need a meaning)' : ''}
      </summary>
      <p className="mt-1 text-sm text-gray-600">
        What your values mean in Preventra&apos;s terms. A value left empty is stored as unknown.
      </p>
      <div className="mt-3 grid gap-5 md:grid-cols-2">
        {coded.map(({ field, column, values }) => (
          <div key={`${field}:${column}`}>
            <p className="text-sm font-medium text-gray-800">{fieldLabel(field)} <span className="font-mono text-xs text-gray-500">({column})</span></p>
            <table className="mt-1 w-full text-sm">
              <tbody>
                {values.map(([v, n]) => (
                  <tr key={v} className="border-b border-gray-100 last:border-0">
                    <td className="py-1 pr-2 font-mono text-xs text-gray-800">{v}</td>
                    <td className="py-1 pr-2 text-right text-xs text-gray-400">{n != null ? n.toLocaleString() : ''}</td>
                    <td className="py-1">
                      <select className={`${inputInline} w-full ${codes[field]?.[v] ? '' : 'border-amber-300 bg-amber-50'}`}
                              value={codes[field]?.[v] || ''} onChange={(e) => onChange(field, v, e.target.value)}>
                        <option value="">Unknown</option>
                        {vocabularies[field].map((o) => <option key={o} value={o}>{codeLabel(o)}</option>)}
                      </select>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ))}
      </div>
    </details>
  );
}

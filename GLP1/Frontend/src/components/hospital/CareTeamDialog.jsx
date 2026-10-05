import { useEffect, useMemo, useState } from 'react';
import { X, Loader2, Search } from 'lucide-react';
import { api } from '../../data/api';

/**
 * Change the doctors and nurses of one patient, or of several at once.
 *
 * One patient: tick everyone who should look after them; the lists are saved
 *   as shown, so unticking someone takes them off.
 * Several: for each person choose Add or Remove; anyone left alone stays as
 *   each patient already has them.
 *
 * Everyone offered belongs to the patients' hospital; the server checks that
 * again and refuses the whole batch otherwise (Backend/core/hospital.assign).
 */
export default function CareTeamDialog({ patientIdxs, hospitalId, current, onClose, onSaved }) {
  const single = patientIdxs.length === 1;
  const [staff, setStaff] = useState(null);
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);
  const [query, setQuery] = useState('');
  // One patient: the ticked ids. Several: id -> 'add' | 'remove'.
  const [picked, setPicked] = useState(() => (single
    ? { doctor: new Set(current?.doctorIds ?? []), nurse: new Set(current?.nurseIds ?? []) }
    : { doctor: {}, nurse: {} }));

  useEffect(() => {
    let live = true;
    api.getStaff().then((s) => { if (live) setStaff(s); }).catch((e) => { if (live) setError(e.message); });
    return () => { live = false; };
  }, []);

  useEffect(() => {
    const onKey = (e) => e.key === 'Escape' && onClose();
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [onClose]);

  // The superadmin looking at every hospital gets every hospital's staff back;
  // only the patients' own hospital's people can be assigned.
  const sameHospital = (p) => !hospitalId || !p.hospital_id || p.hospital_id === hospitalId;
  const words = query.trim().toLowerCase().split(/\s+/).filter(Boolean);
  const matches = (p) => words.every((w) => `${p.name} ${p.email}`.toLowerCase().includes(w));
  const people = useMemo(() => ({
    doctor: (staff?.doctors || []).filter(sameHospital),
    nurse: (staff?.nurses || []).filter(sameHospital),
  }), [staff, hospitalId]); // eslint-disable-line react-hooks/exhaustive-deps

  const toggle = (kind, id) => setPicked((p) => {
    const next = new Set(p[kind]);
    next.has(id) ? next.delete(id) : next.add(id);
    return { ...p, [kind]: next };
  });
  const setAction = (kind, id, action) => setPicked((p) => {
    const next = { ...p[kind] };
    if (next[id] === action) delete next[id]; else next[id] = action;
    return { ...p, [kind]: next };
  });

  const save = async (e) => {
    e.preventDefault();
    const body = { patient_ids: patientIdxs };
    if (single) {
      body.doctor_ids = [...picked.doctor];
      body.nurse_ids = [...picked.nurse];
    } else {
      for (const kind of ['doctor', 'nurse']) {
        const entries = Object.entries(picked[kind]);
        const add = entries.filter(([, a]) => a === 'add').map(([id]) => id);
        const remove = entries.filter(([, a]) => a === 'remove').map(([id]) => id);
        if (add.length) body[`add_${kind}_ids`] = add;
        if (remove.length) body[`remove_${kind}_ids`] = remove;
      }
      if (Object.keys(body).length === 1) {
        setError('Choose at least one person to add or remove.');
        return;
      }
    }
    setBusy(true);
    setError(null);
    try {
      await api.setCareTeam(body);
      onSaved?.();
    } catch (err) {
      setError(err.message);
      setBusy(false);
    }
  };

  const changes = single ? null
    : Object.keys(picked.doctor).length + Object.keys(picked.nurse).length;

  const section = (kind, title) => {
    const everyone = people[kind];
    const shown = everyone.filter(matches);
    return (
      <fieldset>
        <legend className="text-xs font-semibold uppercase tracking-wider text-gray-400">
          {title}{single && picked[kind].size ? ` · ${picked[kind].size} assigned` : ''}
        </legend>
        {everyone.length === 0 && (
          <p className="mt-1.5 text-sm text-gray-400">No {kind} accounts in this hospital yet.</p>
        )}
        {everyone.length > 0 && shown.length === 0 && (
          <p className="mt-1.5 text-sm text-gray-400">No {kind}s match your search.</p>
        )}
        <div className="mt-1.5 max-h-44 overflow-y-auto space-y-1">
          {shown.map((p) => single ? (
            <label key={p.id} className="flex items-center gap-2 rounded-md px-2 py-1.5 text-sm hover:bg-gray-50 cursor-pointer">
              <input type="checkbox" checked={picked[kind].has(p.id)} onChange={() => toggle(kind, p.id)} />
              <span className="flex-1 text-gray-700">{p.name}</span>
              <span className="text-xs text-gray-400">{p.patients} patients</span>
            </label>
          ) : (
            <div key={p.id} className="flex items-center gap-2 rounded-md px-2 py-1.5 text-sm hover:bg-gray-50">
              <span className="flex-1 text-gray-700">{p.name}</span>
              <span className="text-xs text-gray-400 mr-1">{p.patients} patients</span>
              {['add', 'remove'].map((action) => {
                const on = picked[kind][p.id] === action;
                const color = action === 'add' ? '#2E7D32' : '#C62828';
                return (
                  <button key={action} type="button" onClick={() => setAction(kind, p.id, action)}
                    aria-pressed={on}
                    className="text-xs font-semibold px-2 py-1 rounded-md border"
                    style={on ? { background: color, borderColor: color, color: 'white' }
                              : { borderColor: '#E2E8F0', color: '#718096' }}>
                    {action === 'add' ? 'Add' : 'Remove'}
                  </button>
                );
              })}
            </div>
          ))}
        </div>
      </fieldset>
    );
  };

  return (
    <div className="fixed inset-0 z-[60] flex items-center justify-center p-4" style={{ background: 'rgba(15,23,42,0.4)' }}
         onClick={onClose}>
      <form onSubmit={save} onClick={(e) => e.stopPropagation()} role="dialog" aria-modal="true"
            aria-label="Change care team" className="card w-full max-w-lg shadow-2xl" style={{ padding: 0 }}>
        <div className="flex items-center justify-between px-5 py-4 border-b border-gray-100">
          <div>
            <h2 className="text-base font-semibold text-gray-800">
              {single ? `Care team for patient #${patientIdxs[0]}` : `Care team for ${patientIdxs.length} patients`}
            </h2>
            <p className="text-xs text-gray-400 mt-0.5">
              {single ? 'Tick everyone who looks after this patient.'
                : 'Add or remove people for all selected patients. Anyone left alone stays as they are.'}
            </p>
          </div>
          <button type="button" onClick={onClose} aria-label="Close" className="text-gray-400 hover:text-gray-600">
            <X size={18} />
          </button>
        </div>
        <div className="px-5 py-4 space-y-5">
          {!staff && !error && <div className="h-24 rounded-lg bg-gray-100 animate-pulse" />}
          {staff && (<>
            <label className="relative block">
              <Search size={14} className="absolute left-3 top-1/2 -translate-y-1/2 text-gray-400 pointer-events-none" />
              <input type="search" value={query} onChange={(e) => setQuery(e.target.value)}
                placeholder="Search doctors and hospital nurses" aria-label="Search staff"
                className="w-full text-sm rounded-lg border border-gray-200 pl-8 pr-3 py-2 focus:outline-none focus:ring-1 focus:ring-blue-400" />
            </label>
            {section('doctor', 'Doctors')}
            {section('nurse', 'Hospital nurses')}
          </>)}
          {error && <p className="rounded-md px-3 py-2 text-sm" style={{ background: '#FFEBEE', color: '#C62828' }}>{error}</p>}
        </div>
        <div className="flex items-center justify-end gap-2 px-5 py-3 border-t border-gray-100">
          {!single && changes > 0 && (
            <span className="mr-auto text-xs text-gray-500">{changes} change{changes === 1 ? '' : 's'} for {patientIdxs.length} patients</span>
          )}
          <button type="button" onClick={onClose}
            className="text-sm px-4 py-2 rounded-lg border border-gray-200 text-gray-600 hover:bg-gray-50">Cancel</button>
          <button type="submit" disabled={!staff || busy}
            className="inline-flex items-center gap-2 text-sm font-semibold px-4 py-2 rounded-lg text-white disabled:opacity-50"
            style={{ background: 'var(--color-primary)' }}>
            {busy && <Loader2 size={15} className="animate-spin" />} Save
          </button>
        </div>
      </form>
    </div>
  );
}
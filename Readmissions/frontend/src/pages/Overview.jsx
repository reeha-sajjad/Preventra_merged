import React, { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import {
  Users, AlertCircle, ArrowUp, ArrowDown, TrendingUp, Bell, Stethoscope, HeartPulse,
  CalendarCheck, ClipboardList, Building, ChevronRight,
} from 'lucide-react';
import { getOverview } from '../api';
import { can, STAFF_ROLES } from '../roles';

/**
 * How is my hospital doing? For hospital admins, case managers and the
 * superadmin; an insurer gets the same page over its own members. Doctors and
 * nurses never reach it - they land on their own patient list.
 *
 * Counts only (api/hospital.py). Every tile that can be acted on links to the
 * patient list already filtered to the patients it counts.
 */
function Tile({ icon, label, value, sub, tone = 'gray', to }) {
  const Icon = icon;
  const tones = {
    gray: 'bg-gray-50 border-gray-100 text-gray-500',
    red: 'bg-red-50/50 border-red-100 text-risk-high',
    amber: 'bg-yellow-50/40 border-yellow-100 text-risk-medium',
    green: 'bg-green-50/40 border-green-100 text-risk-low',
  };
  const body = (
    <>
      <div className="flex items-center gap-2 text-sm font-medium">
        <Icon size={17} /> <span>{label}</span>
      </div>
      <div className="mt-2 text-3xl font-bold text-gray-900">{value}</div>
      {sub && <div className="mt-1 text-xs text-gray-500">{sub}</div>}
    </>
  );
  const cls = `block rounded-lg border p-4 ${tones[tone]}`;
  return to ? (
    <Link to={to} className={`${cls} transition-shadow hover:shadow-sm hover:border-gray-300`}>{body}</Link>
  ) : <div className={cls}>{body}</div>;
}

function Skeleton() {
  return (
    <div className="p-4 sm:p-6 max-w-7xl mx-auto space-y-6 animate-pulse">
      <div className="h-8 w-64 rounded bg-gray-200" />
      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        {[...Array(8)].map((_, i) => <div key={i} className="h-28 rounded-lg bg-gray-100" />)}
      </div>
      <div className="grid gap-6 lg:grid-cols-2">
        <div className="h-56 rounded-xl bg-gray-100" /><div className="h-56 rounded-xl bg-gray-100" />
      </div>
    </div>
  );
}

export default function Overview() {
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);

  useEffect(() => {
    let live = true;
    getOverview()
      .then((d) => { if (live) setData(d); })
      .catch((e) => { if (live) setError(e.message); });
    return () => { live = false; };
  }, []);

  if (error) {
    return (
      <div className="p-4 sm:p-6 max-w-7xl mx-auto">
        <div className="bg-red-50 border border-red-200 rounded-xl p-4 flex items-start gap-3 text-red-800">
          <AlertCircle size={20} className="mt-0.5 shrink-0" />
          <div><div className="font-semibold">Failed to load the overview</div>
            <div className="text-sm mt-0.5 text-red-600">{error}</div></div>
        </div>
      </div>
    );
  }
  if (!data) return <Skeleton />;

  const total = data.total_patients || 0;
  const bands = data.bands || { High: 0, Medium: 0, Low: 0 };
  const isInsurer = data.staff === null;
  const pct = (n) => (total ? Math.round((n / total) * 100) : 0);

  return (
    <div className="p-4 sm:p-6 max-w-7xl mx-auto space-y-6">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-2xl font-bold text-ns-navy flex items-center gap-2">
            <Building size={24} className="text-gray-400" />
            {data.hospital?.name || (isInsurer ? 'Your members' : 'All hospitals')}
          </h1>
          <p className="text-sm text-gray-500 mt-1">
            {isInsurer ? 'Readmission risk across your members, in every hospital.'
              : 'Readmission risk, alerts and care-team coverage for your patients.'}
          </p>
        </div>
        {data.batch_date && (
          <div className="rounded-full border bg-white px-3 py-1.5 text-sm text-gray-500">
            Batch date: <strong>{data.batch_date}</strong>
          </div>
        )}
      </div>

      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <Tile icon={Users} label="Patients" value={total.toLocaleString()} sub="in the latest batch" to="/patients" />
        <Tile icon={AlertCircle} label="High readmission risk" tone="red" value={bands.High.toLocaleString()}
          to="/patients?filter=High"
          sub={data.high_delta
            ? <span className="inline-flex items-center gap-0.5">
                {data.high_delta > 0 ? <ArrowUp size={12} /> : <ArrowDown size={12} />}
                {Math.abs(data.high_delta)} since the previous batch
              </span>
            : 'no change since the previous batch'} />
        <Tile icon={TrendingUp} label="Needs attention" tone="red" value={data.needs_attention.toLocaleString()}
          sub="risk rising since discharge" to="/patients?trend=NeedsAttention" />
        <Tile icon={Bell} label="Open alerts" tone="amber" value={data.open_alerts.toLocaleString()}
          sub="risk increases and clinical alerts" />
              {data.staff ? (<>
        <Tile icon={Stethoscope} label="Doctors" tone={data.no_doctor ? 'amber' : 'green'}
          value={data.staff.doctors.toLocaleString()}
          sub={data.no_doctor ? `${data.no_doctor.toLocaleString()} patients without a doctor` : 'All patients assigned'}
          to="/staff" />
        <Tile icon={HeartPulse} label="Hospital nurses" tone={data.no_nurse ? 'amber' : 'green'}
          value={data.staff.nurses.toLocaleString()}
          sub={data.no_nurse ? `${data.no_nurse.toLocaleString()} patients without a nurse` : 'All patients assigned'}
          to="/staff" />
      </>) : (<>
        <Tile icon={Stethoscope} label="No doctor assigned" tone={data.no_doctor ? 'amber' : 'green'}
          value={data.no_doctor.toLocaleString()} sub={`${pct(data.no_doctor)}% of patients`}
          to="/patients?unassigned=doctor" />
        <Tile icon={HeartPulse} label="No nurse assigned" tone={data.no_nurse ? 'amber' : 'green'}
          value={data.no_nurse.toLocaleString()} sub={`${pct(data.no_nurse)}% of patients`}
          to="/patients?unassigned=nurse" />
      </>)}
        <Tile icon={CalendarCheck} label="Discharged in 30 days" value={data.discharged_30d.toLocaleString()}
          sub="before the batch date" />
        <Tile icon={ClipboardList} label="Admissions on record" value={data.admissions_on_record.toLocaleString()}
          sub="every scored admission of these patients" />
      </div>

      <div className="grid gap-6 lg:grid-cols-2">
        <section className="rounded-xl border border-gray-200 bg-white p-5 shadow-sm">
          <h2 className="text-lg font-semibold text-ns-navy">Risk bands</h2>
          <p className="text-xs text-gray-500">Current readmission risk, latest batch</p>
          <div className="mt-4 flex h-3 overflow-hidden rounded-full bg-gray-100">
            <div className="bg-risk-high" style={{ width: `${pct(bands.High)}%` }} />
            <div className="bg-risk-medium" style={{ width: `${pct(bands.Medium)}%` }} />
            <div className="bg-risk-low" style={{ width: `${pct(bands.Low)}%` }} />
          </div>
          <ul className="mt-4 space-y-2 text-sm">
            {[['High', 'bg-risk-high'], ['Medium', 'bg-risk-medium'], ['Low', 'bg-risk-low']].map(([b, c]) => (
              <li key={b}>
                <Link to={`/patients?filter=${b}`} className="flex items-center justify-between rounded-md px-2 py-1.5 hover:bg-gray-50">
                  <span className="flex items-center gap-2 text-gray-700"><span className={`h-3 w-3 rounded-full ${c}`} />{b}</span>
                  <span className="font-semibold text-gray-900">{bands[b].toLocaleString()} <span className="font-normal text-gray-400">· {pct(bands[b])}%</span></span>
                </Link>
              </li>
            ))}
          </ul>
        </section>

        <section className="rounded-xl border border-gray-200 bg-white p-5 shadow-sm">
          <h2 className="text-lg font-semibold text-ns-navy">Insurer mix</h2>
          <p className="text-xs text-gray-500">Who pays for these patients</p>
          <ul className="mt-4 space-y-2.5 text-sm">
            {(data.insurer_mix || []).map((m) => (
              <li key={m.id || 'none'}>
                <div className="flex justify-between"><span className="text-gray-700">{m.name}</span>
                  <span className="font-semibold text-gray-900">{m.count.toLocaleString()}</span></div>
                <div className="mt-1 h-1.5 overflow-hidden rounded-full bg-gray-100">
                  <div className="h-full rounded-full bg-ns-navy/70" style={{ width: `${pct(m.count)}%` }} />
                </div>
              </li>
            ))}
            {!(data.insurer_mix || []).length && <li className="text-gray-400">No patients yet.</li>}
          </ul>
        </section>
      </div>

      {data.staff && (
        <section className="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-gray-200 bg-white p-5 shadow-sm">
          <div className="text-sm text-gray-700">
            <span className="font-semibold text-gray-900">{data.staff.doctors}</span> doctors registered for alerts and{' '}
            <span className="font-semibold text-gray-900">{data.staff.nurses}</span> nurses.
          </div>
          {can(STAFF_ROLES) && (
            <Link to="/staff" className="inline-flex items-center gap-1 text-sm font-semibold text-ns-navy hover:underline">
              Staff and workload <ChevronRight size={15} />
            </Link>
          )}
        </section>
      )}
    </div>
  );
}
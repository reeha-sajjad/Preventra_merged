import React, { useEffect, useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import {
  Users, AlertCircle, TrendingUp, Bell, Stethoscope, ChevronRight, ArrowRight, Gauge,
  HeartPulse, ListOrdered,
} from 'lucide-react';
import { getMyPatients } from '../api';
import { readClaims } from '../api/auth';
import RiskBadge from '../components/shared/RiskBadge';
import TrendStatusBadge from '../components/shared/TrendStatusBadge';
import { EmergencyAccessButton } from '../components/emergency/EmergencyAccess';

/**
 * "My patients": the home page of a doctor or nurse. Their own patients only -
 * the server works it out from the same scope as the patient list
 * (api/hospital.py my_patients). The same page as GLP-1's "My patients"
 * (GLP1/Frontend/src/pages/CareHome.jsx), in this app's style, so the two
 * products greet a clinician the same way.
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

function Section({ icon, title, sub, action, children }) {
  const Icon = icon;
  return (
    <section className="rounded-xl border border-gray-200 bg-white p-5 shadow-sm">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div>
          <h2 className="flex items-center gap-2 text-lg font-semibold text-ns-navy">
            <Icon size={18} className="text-gray-400" /> {title}
          </h2>
          {sub && <p className="text-xs text-gray-500">{sub}</p>}
        </div>
        {action}
      </div>
      <div className="mt-4">{children}</div>
    </section>
  );
}

function Bars({ items, total, empty }) {
  if (!items.length) return <p className="text-sm text-gray-400">{empty}</p>;
  const top = items[0].count || 1;
  return (
    <ul className="space-y-2.5 text-sm">
      {items.map((m) => (
        <li key={m.name}>
          <div className="flex justify-between gap-3">
            <span className="truncate text-gray-700">{m.name}</span>
            <span className="shrink-0 font-semibold text-gray-900">
              {m.count.toLocaleString()} <span className="font-normal text-gray-400">· {total ? Math.round((m.count / total) * 100) : 0}%</span>
            </span>
          </div>
          <div className="mt-1 h-1.5 overflow-hidden rounded-full bg-gray-100">
            <div className="h-full rounded-full bg-ns-navy/70" style={{ width: `${(m.count / top) * 100}%` }} />
          </div>
        </li>
      ))}
    </ul>
  );
}

function Skeleton() {
  return (
    <div className="mx-auto max-w-7xl animate-pulse space-y-6 p-4 sm:p-6">
      <div className="h-8 w-64 rounded bg-gray-200" />
      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        {[...Array(4)].map((_, i) => <div key={i} className="h-28 rounded-lg bg-gray-100" />)}
      </div>
      <div className="h-72 rounded-xl bg-gray-100" />
    </div>
  );
}

export default function MyPatients() {
  const navigate = useNavigate();
  const isDoctor = readClaims()?.role === 'doctor';
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);

  useEffect(() => {
    let live = true;
    getMyPatients()
      .then((d) => { if (live) setData(d); })
      .catch((e) => { if (live) setError(e.message); });
    return () => { live = false; };
  }, []);

  if (error) {
    return (
      <div className="mx-auto max-w-7xl p-4 sm:p-6">
        <div className="flex items-start gap-3 rounded-xl border border-red-200 bg-red-50 p-4 text-red-800">
          <AlertCircle size={20} className="mt-0.5 shrink-0" />
          <div><div className="font-semibold">Your patients could not be loaded</div>
            <div className="mt-0.5 text-sm text-red-600">{error}</div></div>
        </div>
      </div>
    );
  }
  if (!data) return <Skeleton />;

  const total = data.total || 0;
  const pct = (n) => (total ? Math.round((n / total) * 100) : 0);

  if (!total) {
    return (
      <div className="mx-auto max-w-7xl p-4 sm:p-6">
        <div className="rounded-xl border border-gray-200 bg-white p-10 text-center shadow-sm">
          <div className="mx-auto flex h-11 w-11 items-center justify-center rounded-xl bg-ns-navy/10 text-ns-navy">
            <Users size={20} />
          </div>
          <h2 className="mt-4 text-lg font-semibold text-gray-800">No patients assigned to you yet</h2>
          <p className="mx-auto mt-1 max-w-md text-sm text-gray-500">
            Your hospital admin or case manager adds you to patients&rsquo; care teams. Once they do,
            those patients appear here.
          </p>
          <div className="mt-4 flex justify-center"><EmergencyAccessButton /></div>
          <p className="mt-2 text-xs text-gray-400">Treating a patient who isn&rsquo;t on your list in an emergency? Use emergency access.</p>
        </div>
      </div>
    );
  }

  const { bands } = data;

  return (
    <div className="mx-auto max-w-7xl space-y-6 p-4 sm:p-6">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="flex items-center gap-2 text-2xl font-bold text-ns-navy">
            <Stethoscope size={24} className="text-gray-400" /> Your patients
          </h1>
          <p className="mt-1 text-sm text-gray-500">
            Readmission risk for the {total.toLocaleString()} {total === 1 ? 'patient' : 'patients'} assigned
            to you as {isDoctor ? 'their doctor' : 'their nurse'}.
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-3">
          <EmergencyAccessButton />
          {data.batch_date && (
            <div className="rounded-full border bg-white px-3 py-1.5 text-sm text-gray-500">
              Batch date: <strong>{data.batch_date}</strong>
            </div>
          )}
        </div>
      </div>

      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <Tile icon={Users} label="My patients" value={total.toLocaleString()} sub="assigned to you" to="/patients" />
        <Tile icon={AlertCircle} label="High readmission risk" tone="red" value={bands.High.toLocaleString()}
          sub={`${pct(bands.High)}% of your patients`} to="/patients?filter=High" />
        <Tile icon={TrendingUp} label="Getting worse" tone="red" value={data.needs_attention.toLocaleString()}
          sub="deteriorating or action required" to="/patients?trend=NeedsAttention" />
        <Tile icon={Bell} label="Open clinical alerts" tone={data.open_alerts ? 'amber' : 'green'}
          value={data.open_alerts.toLocaleString()}
          sub={isDoctor ? 'waiting for your reply' : 'on your patients'}
          to={isDoctor ? '/doctor' : undefined} />
      </div>

      <Section icon={ListOrdered} title="Needs attention"
        sub={data.attention_total
          ? 'Getting worse first, then the highest risk'
          : 'None of your patients is high risk or getting worse right now'}
        action={data.attention_total > data.attention.length && (
          <Link to="/patients?filter=High" className="inline-flex items-center gap-1 text-sm font-semibold text-ns-navy hover:underline">
            See all {data.attention_total.toLocaleString()} <ArrowRight size={14} />
          </Link>
        )}>
        {data.attention.length > 0 && (
          <div className="-mx-5 overflow-x-auto border-t border-gray-100">
            <table className="w-full text-left text-sm">
              <thead className="bg-gray-50 text-xs uppercase tracking-wide text-gray-500">
                <tr>
                  <th className="px-5 py-2.5 font-semibold">Patient</th>
                  <th className="px-5 py-2.5 font-semibold">Risk</th>
                  <th className="px-5 py-2.5 font-semibold">Trend</th>
                  <th className="px-5 py-2.5 font-semibold">Diagnosis</th>
                  <th className="px-5 py-2.5 font-semibold">Main reason</th>
                  <th className="px-5 py-2.5" aria-label="Open" />
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-100">
                {data.attention.map((p) => (
                  <tr key={p.id} onClick={() => navigate(`/patients/${encodeURIComponent(p.id)}`)}
                      className="group cursor-pointer transition-colors hover:bg-blue-50/50">
                    <td className="whitespace-nowrap px-5 py-3 font-semibold text-gray-800">{p.id}</td>
                    <td className="whitespace-nowrap px-5 py-3">
                      <span className="mr-2 font-bold text-gray-900">{p.risk_score.toFixed(1)}%</span>
                      <RiskBadge riskBand={p.risk_band} size="sm" />
                    </td>
                    <td className="whitespace-nowrap px-5 py-3"><TrendStatusBadge status={p.monitoring_status} size="sm" short /></td>
                    <td className="max-w-xs truncate px-5 py-3 text-gray-700" title={p.primary_diagnosis}>{p.primary_diagnosis || '—'}</td>
                    <td className="max-w-xs truncate px-5 py-3 text-gray-700" title={p.main_reason}>{p.main_reason || '—'}</td>
                    <td className="px-5 py-3 text-right">
                      <ChevronRight size={16} className="inline text-gray-300 group-hover:text-gray-500" />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Section>

      <div className="grid gap-6 lg:grid-cols-3">
        <Section icon={Gauge} title="Risk bands" sub="Current readmission risk, latest batch">
          <div className="flex h-3 overflow-hidden rounded-full bg-gray-100">
            <div className="bg-risk-high" style={{ width: `${pct(bands.High)}%` }} />
            <div className="bg-risk-medium" style={{ width: `${pct(bands.Medium)}%` }} />
            <div className="bg-risk-low" style={{ width: `${pct(bands.Low)}%` }} />
          </div>
          <ul className="mt-4 space-y-1 text-sm">
            {[['High', 'bg-risk-high'], ['Medium', 'bg-risk-medium'], ['Low', 'bg-risk-low']].map(([b, c]) => (
              <li key={b}>
                <Link to={`/patients?filter=${b}`} className="flex items-center justify-between rounded-md px-2 py-1.5 hover:bg-gray-50">
                  <span className="flex items-center gap-2 text-gray-700"><span className={`h-3 w-3 rounded-full ${c}`} />{b}</span>
                  <span className="font-semibold text-gray-900">{bands[b].toLocaleString()} <span className="font-normal text-gray-400">· {pct(bands[b])}%</span></span>
                </Link>
              </li>
            ))}
          </ul>
        </Section>
        <Section icon={HeartPulse} title="Conditions" sub="What your patients are monitored for">
          <Bars items={data.conditions} total={total} empty="No conditions recorded." />
        </Section>
        <Section icon={TrendingUp} title="Why they are at risk" sub="Each patient's main reason, counted">
          <Bars items={data.reasons} total={total} empty="No reasons recorded yet." />
        </Section>
      </div>
    </div>
  );
}
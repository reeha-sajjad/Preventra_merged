import React, { useEffect, useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { Users, AlertTriangle, Wallet, ShieldCheck, Building2, ChevronRight, CreditCard, TrendingUp, ArrowUp, ArrowDown } from 'lucide-react';
import { getInsurerHome } from '../api';
import MemberLookup from '../components/insurance/MemberLookup';
import { InsurerInbox } from '../components/insurance/InsurerFlags';

const usd = (n) => `$${Math.round(n || 0).toLocaleString()}`;

/**
 * An insurer's home in Readmissions (api/insurer.py): its members across
 * hospitals, how their risk is moving week to week, the readmission cost their
 * current risk carries, how much timely follow-up could avoid, and the
 * highest-risk members by member ID. The insurer's one Members page (it took in
 * the old Members overview). Overview layer only. GLP-1's twin
 * (GLP1/Frontend/src/pages/InsurerHome.jsx).
 */
export default function InsurerHome() {
  const navigate = useNavigate();
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);
  useEffect(() => { getInsurerHome().then(setData).catch((e) => setError(e.message)); }, []);
  if (!data) return <p className="p-6 text-sm text-gray-500">{error ? `Couldn’t load your members: ${error}` : 'Loading your members…'}</p>;

  const { stats } = data;
  return (
    <div className="mx-auto max-w-7xl space-y-5 p-4 sm:p-6">
      <div className="rounded-xl border border-gray-200 bg-white p-5 shadow-sm md:p-6">
        <div className="mb-4 flex flex-wrap items-start justify-between gap-3">
          <div>
            <h1 className="text-2xl font-bold text-gray-900">{data.insurer.name}</h1>
            <p className="mt-0.5 text-sm text-gray-500">
              Your members home from hospital, across {stats.hospitals} hospital{stats.hospitals === 1 ? '' : 's'}.
            </p>
          </div>
          <MemberLookup />
        </div>
        <div className="grid grid-cols-2 gap-3 lg:grid-cols-5">
          <Tile icon={Users} label="Members" value={stats.members.toLocaleString()}
                sub={`${data.discharged_30d.toLocaleString()} went home in the last 30 days`} />
          <Tile icon={AlertTriangle} label="High readmission risk" value={stats.high_risk.toLocaleString()} tone="text-risk-high"
                sub={data.high_delta
                  ? <span className="inline-flex items-center gap-0.5">
                      {data.high_delta > 0 ? <ArrowUp size={11} /> : <ArrowDown size={11} />}
                      {Math.abs(data.high_delta)} since last week</span>
                  : 'no change since last week'} />
          <Tile icon={TrendingUp} label="Risk rising" value={data.rising.toLocaleString()} tone="text-risk-high"
                sub="getting worse since discharge" />
          <Tile icon={Wallet} label="Expected readmission cost" value={usd(stats.expected_cost)}
                sub={`risk × ${usd(stats.readmission_cost)} per readmission`} />
          <Tile icon={ShieldCheck} label="Avoidable with follow-up" value={usd(stats.avoidable)} tone="text-green-800"
                sub="if timely follow-up prevents a quarter" />
        </div>
        {stats.without_member_id > 0 && (
          <p className="mt-3 flex items-center gap-1.5 text-xs text-gray-500">
            <CreditCard size={12} /> {stats.without_member_id.toLocaleString()} member{stats.without_member_id === 1 ? ' has' : 's have'} no
            member ID on file yet - the hospital adds it from the insurance card.
          </p>
        )}
      </div>

      <InsurerInbox />

      <div className="grid gap-5 lg:grid-cols-5">
        <section className="overflow-hidden rounded-xl border border-gray-200 bg-white shadow-sm lg:col-span-3">
          <div className="border-b border-gray-100 px-5 py-4">
            <h2 className="text-base font-semibold text-gray-900">High-risk members</h2>
            <p className="mt-0.5 text-xs text-gray-500">Most likely to be readmitted. Their clinical details need a reason to open.</p>
          </div>
          {data.high_risk_members.length === 0 ? (
            <p className="p-8 text-center text-sm text-gray-400">No members at high risk.</p>
          ) : (
            <table className="w-full text-sm">
              <thead className="bg-gray-50 text-left text-xs text-gray-500">
                <tr><th className="px-5 py-2">Member ID</th><th className="px-3 py-2">Risk</th><th className="px-3 py-2">Hospital</th>
                    <th className="px-3 py-2">Went home</th><th /></tr>
              </thead>
              <tbody className="divide-y divide-gray-100">
                {data.high_risk_members.map((m) => (
                  <tr key={m.patient_id} className="cursor-pointer hover:bg-gray-50"
                      onClick={() => navigate(`/patients/${encodeURIComponent(m.patient_id)}`)}>
                    <td className="px-5 py-2.5">
                      <span className="font-mono text-xs font-semibold text-gray-900">{m.member_id || '—'}</span>
                      <span className="block text-[11px] text-gray-400">{m.patient_id}</span>
                    </td>
                    <td className="px-3 py-2.5 font-semibold text-risk-high">{m.risk_score}%</td>
                    <td className="px-3 py-2.5 text-xs text-gray-600">{m.hospital || '—'}</td>
                    <td className="px-3 py-2.5 text-xs text-gray-600">{m.discharge_date || '—'}</td>
                    <td className="px-3 py-2.5 text-right"><ChevronRight size={15} className="inline text-gray-300" /></td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </section>

        <section className="overflow-hidden rounded-xl border border-gray-200 bg-white shadow-sm lg:col-span-2">
          <div className="border-b border-gray-100 px-5 py-4">
            <h2 className="flex items-center gap-2 text-base font-semibold text-gray-900">
              <Building2 size={15} className="text-gray-400" /> By hospital
            </h2>
          </div>
          <ul className="divide-y divide-gray-100">
            {data.by_hospital.map((h) => (
              <li key={h.hospital_id || 'none'} className="px-5 py-3">
                <div className="flex items-baseline justify-between gap-3">
                  <span className="text-sm font-medium text-gray-900">{h.hospital}</span>
                  <span className="text-xs text-gray-500">{h.members.toLocaleString()} members</span>
                </div>
                <div className="mt-1 flex flex-wrap gap-x-4 text-xs text-gray-500">
                  <span><span className="font-semibold text-risk-high">{h.high_risk.toLocaleString()}</span> high risk</span>
                  <span>{usd(h.expected_cost)} expected</span>
                  <span className="text-green-800">{usd(h.avoidable)} avoidable</span>
                </div>
              </li>
            ))}
          </ul>
        </section>
      </div>

      <section className="rounded-xl border border-gray-200 bg-white p-5 shadow-sm">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <h2 className="text-base font-semibold text-gray-900">Risk bands</h2>
            <p className="mt-0.5 text-xs text-gray-500">Your members' current readmission risk, week of {data.batch_date}</p>
          </div>
          <Link to="/patients" className="text-sm font-semibold text-ns-navy hover:underline">All members</Link>
        </div>
        <div className="mt-4 flex h-3 overflow-hidden rounded-full bg-gray-100">
          {[['High', 'bg-risk-high'], ['Medium', 'bg-risk-medium'], ['Low', 'bg-risk-low']].map(([b, c]) => (
            <div key={b} className={c} style={{ width: `${(data.bands[b] / (stats.members || 1)) * 100}%` }} />
          ))}
        </div>
        <ul className="mt-3 flex flex-wrap gap-x-6 gap-y-1 text-sm">
          {[['High', 'bg-risk-high'], ['Medium', 'bg-risk-medium'], ['Low', 'bg-risk-low']].map(([b, c]) => (
            <li key={b}>
              <Link to={`/patients?filter=${b}`} className="flex items-center gap-2 hover:underline">
                <span className={`h-2.5 w-2.5 rounded-full ${c}`} /> {b}
                <span className="font-semibold text-gray-900">{data.bands[b].toLocaleString()}</span>
                <span className="text-gray-400">· {Math.round((data.bands[b] / (stats.members || 1)) * 100)}%</span>
              </Link>
            </li>
          ))}
        </ul>
      </section>
    </div>
  );
}

function Tile({ icon: Icon, label, value, sub, tone = 'text-gray-900' }) {
  return (
    <div className="min-w-0 rounded-xl border border-gray-100 p-3">
      <div className="flex items-center gap-1.5 text-xs font-medium text-gray-500"><Icon size={13} /> {label}</div>
      <div className={`mt-1 text-2xl font-bold leading-none ${tone}`}>{value}</div>
      {sub && <div className="mt-1 text-[11px] text-gray-400">{sub}</div>}
    </div>
  );
}
import { useEffect, useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { Users, AlertTriangle, Wallet, TrendingDown, Building2, ChevronRight, CreditCard, Activity, Pill, PieChart } from 'lucide-react';
import { api } from '../data/api';
import { RiskBadge } from '../components/shared';
import PageState from '../components/shared/PageState';
import MemberLookup from '../components/insurance/MemberLookup';
import { InsurerInbox } from '../components/insurance/InsurerFlags';

const usd = (n) => `$${Math.round(n || 0).toLocaleString()}`;

/**
 * An insurer's one Members page (Backend/core/insurer.py): its members on GLP-1
 * across hospitals, how many take it as prescribed, what their drug costs and
 * how much of that is spent on members likely to stop - by hospital, by drug
 * and by behaviour segment - and who the critical-risk members are, by member
 * ID, so the payer's own care management can reach them. Hospitals' billing
 * flags sit at the top. Overview layer only; a member's clinical details still
 * need a reason. Readmissions has the same page for readmission risk.
 */
export default function InsurerHome() {
  const navigate = useNavigate();
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);
  useEffect(() => { api.getInsurerHome().then(setData).catch((e) => setError(e.message)); }, []);
  if (!data) return <PageState error={error} label="your members" />;

  const { stats } = data;
  return (
    <div className="max-w-[1200px] mx-auto space-y-5 animate-fade-in">
      <div className="card p-5 md:p-6">
        <div className="flex flex-wrap items-start justify-between gap-3 mb-4">
          <div>
            <h1 className="text-lg font-semibold text-gray-800" style={{ fontFamily: 'DM Serif Display, serif' }}>
              {data.insurer.name}
            </h1>
            <p className="text-xs text-gray-400 mt-0.5">
              Your members on GLP-1 therapy, across {stats.hospitals} hospital{stats.hospitals === 1 ? '' : 's'}.
            </p>
          </div>
          <MemberLookup />
        </div>
        <div className="grid gap-3 grid-cols-2 lg:grid-cols-5">
          <Tile icon={Users} label="Members" value={stats.members.toLocaleString()} color="#1B4F8A" />
          <Tile icon={Activity} label="Taking it as prescribed" value={`${Math.round((stats.adherent / (stats.members || 1)) * 100)}%`}
                sub={`${stats.adherent.toLocaleString()} members`} color="#2E7D32" />
          <Tile icon={AlertTriangle} label="Critical risk" value={stats.high_risk.toLocaleString()}
                sub={`${Math.round(data.high_risk_line * 100)}% or more chance of stopping`} color="#C62828" />
          <Tile icon={Wallet} label="Annual drug spend" value={usd(stats.annual_spend)} color="#2E6DB4" />
          <Tile icon={TrendingDown} label="Spend at risk" value={usd(stats.spend_at_risk)}
                sub="on members likely to stop early" color="#B45309" />
        </div>
        {stats.without_member_id > 0 && (
          <p className="mt-3 text-xs text-gray-500 flex items-center gap-1.5">
            <CreditCard size={12} /> {stats.without_member_id.toLocaleString()} member{stats.without_member_id === 1 ? ' has' : 's have'} no
            member ID on file yet - the hospital adds it from the insurance card.
          </p>
        )}
      </div>

      <InsurerInbox />

      <div className="grid gap-5 lg:grid-cols-5 items-start">
        <section className="card overflow-hidden lg:col-span-3" style={{ padding: 0 }}>
          <div className="px-5 py-4 border-b border-gray-100">
            <h2 className="text-base font-semibold text-gray-800" style={{ fontFamily: 'DM Serif Display, serif' }}>Critical-risk members</h2>
            <p className="text-xs text-gray-400 mt-0.5">Most likely to stop first. Their clinical details need a reason to open.</p>
          </div>
          {data.high_risk_members.length === 0 ? (
            <p className="p-8 text-center text-sm text-gray-400">No members at critical risk.</p>
          ) : (
            <div className="overflow-x-auto">
              <table className="data-table">
                <thead><tr><th>Member ID</th><th>Risk</th><th>Hospital</th><th>Drug</th><th aria-label="Open" /></tr></thead>
                <tbody>
                  {data.high_risk_members.map((m) => (
                    <tr key={m.patient_idx} className="cursor-pointer" onClick={() => navigate(`/patients/${m.patient_idx}`)}>
                      <td>
                        <span className="font-mono text-xs font-semibold text-gray-800">{m.member_id || '—'}</span>
                        <span className="block text-[11px] text-gray-400">Patient #{m.patient_idx}</span>
                      </td>
                      <td><RiskBadge prob={m.dropout_prob} /></td>
                      <td className="text-xs text-gray-600">{m.hospital || '—'}</td>
                      <td className="text-xs text-gray-600">{m.drug}</td>
                      <td className="text-right"><ChevronRight size={15} className="text-gray-300 inline" /></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </section>

        <div className="space-y-5 lg:col-span-2">
        <section className="card overflow-hidden" style={{ padding: 0 }}>
          <div className="px-5 py-4 border-b border-gray-100">
            <h2 className="text-base font-semibold text-gray-800 flex items-center gap-2" style={{ fontFamily: 'DM Serif Display, serif' }}>
              <Building2 size={15} className="text-gray-400" /> By hospital
            </h2>
          </div>
          <ul className="divide-y divide-gray-100">
            {data.by_hospital.map((h) => (
              <li key={h.hospital_id || 'none'} className="px-5 py-3">
                <div className="flex items-baseline justify-between gap-3">
                  <span className="text-sm font-medium text-gray-800">{h.hospital}</span>
                  <span className="text-xs text-gray-500">{h.members.toLocaleString()} members</span>
                </div>
                <div className="mt-1 flex flex-wrap gap-x-4 text-xs text-gray-500">
                  <span><span style={{ color: '#C62828' }} className="font-semibold">{h.high_risk.toLocaleString()}</span> critical</span>
                  <span>{usd(h.annual_spend)} a year</span>
                  <span style={{ color: '#B45309' }}>{usd(h.spend_at_risk)} at risk</span>
                </div>
              </li>
            ))}
          </ul>
        </section>

        <section className="card overflow-hidden" style={{ padding: 0 }}>
          <div className="px-5 py-4 border-b border-gray-100">
            <h2 className="text-base font-semibold text-gray-800 flex items-center gap-2" style={{ fontFamily: 'DM Serif Display, serif' }}>
              <Pill size={15} className="text-gray-400" /> By drug
            </h2>
          </div>
          <ul className="px-5 py-3 space-y-2">
            {data.drug_mix.map((d) => (
              <li key={d.drug}>
                <div className="flex items-baseline justify-between gap-3 text-sm">
                  <span className="text-gray-700">{d.drug}</span>
                  <span className="text-xs text-gray-500">{d.members.toLocaleString()} · {Math.round((d.members / (stats.members || 1)) * 100)}%</span>
                </div>
                <div className="h-1.5 mt-1 rounded-full overflow-hidden" style={{ background: '#EDF2F7' }}>
                  <div className="h-full rounded-full" style={{ width: `${(d.members / (stats.members || 1)) * 100}%`, background: '#2E6DB4' }} />
                </div>
              </li>
            ))}
          </ul>
        </section>
        </div>
      </div>

      <section className="card overflow-hidden" style={{ padding: 0 }}>
        <div className="px-5 py-4 border-b border-gray-100 flex flex-wrap items-start justify-between gap-3">
          <div>
            <h2 className="text-base font-semibold text-gray-800 flex items-center gap-2" style={{ fontFamily: 'DM Serif Display, serif' }}>
              <PieChart size={15} className="text-gray-400" /> Where the spend at risk is
            </h2>
            <p className="text-xs text-gray-400 mt-0.5">Your members by behaviour segment: how many take it as prescribed, and the drug spend on those likely to stop.</p>
          </div>
          <div className="flex flex-wrap gap-x-4 gap-y-1 text-xs font-semibold" style={{ color: 'var(--color-primary)' }}>
            <Link to="/segments">Segment Explorer</Link>
            <Link to="/budget">Budget Simulator</Link>
            <Link to="/consequence">Cost of Inaction</Link>
          </div>
        </div>
        <ul className="divide-y divide-gray-100">
          {data.by_segment.map((g) => (
            <li key={g.cluster} className="px-5 py-3 grid gap-2 sm:grid-cols-[1fr_auto] sm:items-center">
              <div className="min-w-0">
                <div className="flex items-center gap-2 text-sm font-medium text-gray-800">
                  <span className="w-2.5 h-2.5 rounded-full flex-shrink-0" style={{ background: g.color || '#A0AEC0' }} />
                  {g.label}
                </div>
                <div className="mt-0.5 text-xs text-gray-500">
                  {g.members.toLocaleString()} members · {Math.round((g.adherence || 0) * 100)}% taking it as prescribed
                </div>
              </div>
              <div className="sm:text-right">
                <div className="text-sm font-semibold" style={{ color: '#B45309' }}>{usd(g.spend_at_risk)}</div>
                <div className="h-1.5 mt-1 rounded-full overflow-hidden sm:w-40" style={{ background: '#EDF2F7' }}>
                  <div className="h-full rounded-full" style={{ width: `${(g.spend_at_risk / (stats.spend_at_risk || 1)) * 100}%`, background: '#B45309' }} />
                </div>
              </div>
            </li>
          ))}
        </ul>
      </section>
    </div>
  );
}

function Tile({ icon: Icon, label, value, sub, color }) {
  return (
    <div className="min-w-0 rounded-xl border border-gray-100 p-3">
      <div className="flex items-center gap-1.5 text-[11px] font-medium text-gray-500">
        <Icon size={12} style={{ color }} /> {label}
      </div>
      <div className="font-display text-2xl font-semibold text-gray-800 mt-1 leading-none">{value}</div>
      {sub && <div className="text-[11px] text-gray-400 mt-1">{sub}</div>}
    </div>
  );
}
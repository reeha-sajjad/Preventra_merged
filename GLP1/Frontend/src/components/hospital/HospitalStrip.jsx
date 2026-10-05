import { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { Building2, Stethoscope, HeartPulse, Users, AlertTriangle, Pill, ArrowRight } from 'lucide-react';
import { api } from '../../data/api';
import { useRole } from '../../context/RoleContext';
import { SkeletonCard } from '../shared/LoadingSkeleton';

const fmtMoney = (n) => (n >= 1e6 ? `$${(n / 1e6).toFixed(1)}M` : n >= 1e3 ? `$${(n / 1e3).toFixed(0)}K` : `$${n}`);

/**
 * The hospital's side of the Overview: who is on therapy, who is at risk,
 * whether everyone has a care team, and who pays. Counts only
 * (Backend/core/hospital.py). Each figure that can be acted on links to the
 * patient list filtered to exactly those patients.
 */
export default function HospitalStrip() {
  const { hasStaff } = useRole();
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);

  useEffect(() => {
    let live = true;
    api.getOverview().then((d) => { if (live) setData(d); }).catch((e) => { if (live) setError(e); });
    return () => { live = false; };
  }, []);

  if (error) return null;               // the summary below still stands on its own
  if (!data) return <SkeletonCard h={180} />;

  const total = data.total_patients || 0;
  const pct = (n) => (total ? Math.round((n / total) * 100) : 0);
  const isInsurer = data.staff === null;
  const tile = (Icon, label, value, sub, color, to) => {
    const body = (
      <>
        <div className="flex items-center gap-1.5 text-[10px] font-semibold uppercase tracking-wider text-gray-400">
          <Icon size={12} style={{ color }} /> {label}
        </div>
        <div className="font-display text-2xl font-semibold text-gray-800 mt-1 leading-none">{value}</div>
        {sub && <div className="text-[11px] text-gray-400 mt-1">{sub}</div>}
      </>
    );
    return to
      ? <Link to={to} className="rounded-xl border border-gray-100 p-3 hover:border-blue-200 hover:bg-blue-50/30 transition-colors">{body}</Link>
      : <div className="rounded-xl border border-gray-100 p-3">{body}</div>;
  };

  return (
    <div className="card p-5 md:p-6 animate-fade-up">
      <div className="flex flex-wrap items-start justify-between gap-2 mb-4">
        <div>
          <h2 className="text-lg font-semibold text-gray-800 flex items-center gap-2" style={{ fontFamily: 'DM Serif Display, serif' }}>
            <Building2 size={17} className="text-gray-400" />
            {data.hospital?.name || (isInsurer ? 'Your members' : 'All hospitals')}
          </h2>
          <p className="text-xs text-gray-400 mt-0.5">
            {isInsurer ? 'GLP-1 therapy across your members, in every hospital'
              : 'GLP-1 therapy, dropout risk and care-team coverage for your patients'}
          </p>
        </div>
      </div>

      <div className="grid gap-3 grid-cols-2 md:grid-cols-3 xl:grid-cols-6">
        {tile(Users, 'On GLP-1 therapy', total.toLocaleString(), 'patients', '#1B4F8A', '/patients')}
        {tile(HeartPulse, 'Adherent', data.adherent.toLocaleString(), `${pct(data.adherent)}% of patients`, '#2E7D32')}
        {tile(AlertTriangle, 'Non-adherent', data.non_adherent.toLocaleString(), `${pct(data.non_adherent)}% of patients`, '#C62828',
              '/patients?prediction=Dropout%20Risk')}
        {tile(AlertTriangle, 'High dropout risk', data.high_risk.toLocaleString(),
              `≥${Math.round(data.high_risk_threshold * 100)}% predicted risk`, '#EF6C00', '/patients?min_risk=75')}
              {hasStaff && data.staff ? (<>
        {tile(Stethoscope, 'Doctors', data.staff.doctors.toLocaleString(),
              data.no_doctor ? `${data.no_doctor.toLocaleString()} patients without a doctor` : 'All patients assigned',
              data.no_doctor ? '#EF6C00' : '#2E7D32',
              data.no_doctor ? '/patients?unassigned=doctor' : '/staff')}
        {tile(HeartPulse, 'Hospital nurses', data.staff.nurses.toLocaleString(),
              data.no_nurse ? `${data.no_nurse.toLocaleString()} patients without a hospital nurse` : 'All patients assigned',
              data.no_nurse ? '#EF6C00' : '#2E7D32',
              data.no_nurse ? '/patients?unassigned=nurse' : '/staff')}
      </>) : (<>
        {tile(Stethoscope, 'No doctor', data.no_doctor.toLocaleString(), `${pct(data.no_doctor)}% of patients`,
              data.no_doctor ? '#EF6C00' : '#2E7D32', '/patients?unassigned=doctor')}
        {tile(HeartPulse, 'No hospital nurse', data.no_nurse.toLocaleString(), `${pct(data.no_nurse)}% of patients`,
              data.no_nurse ? '#EF6C00' : '#2E7D32', '/patients?unassigned=nurse')}
      </>)}
      </div>

      <div className="grid gap-5 md:grid-cols-3 mt-5 pt-5 border-t border-gray-100">
        <div>
          <div className="text-[10px] font-semibold uppercase tracking-wider text-gray-400 mb-2 flex items-center gap-1.5">
            <Pill size={12} /> Drug mix
          </div>
          <ul className="space-y-1.5">
            {data.drug_mix.map((d) => (
              <li key={d.drug} className="flex items-center justify-between text-xs">
                <span className="font-mono text-gray-600">{d.drug}</span>
                <span className="font-semibold text-gray-800">{d.count.toLocaleString()} <span className="font-normal text-gray-400">· {pct(d.count)}%</span></span>
              </li>
            ))}
          </ul>
        </div>
        <div>
          <div className="text-[10px] font-semibold uppercase tracking-wider text-gray-400 mb-2 flex items-center gap-1.5">
            <Building2 size={12} /> Insurer mix
          </div>
          <ul className="space-y-1.5">
            {data.insurer_mix.map((m) => (
              <li key={m.id || 'none'} className="flex items-center justify-between text-xs">
                <span className="text-gray-600">{m.name}</span>
                <span className="font-semibold text-gray-800">{m.count.toLocaleString()} <span className="font-normal text-gray-400">· {pct(m.count)}%</span></span>
              </li>
            ))}
          </ul>
        </div>
        {data.drug_spend && (
          <div>
            <div className="text-[10px] font-semibold uppercase tracking-wider text-gray-400 mb-2">Drug spend (annual)</div>
            <div className="font-display text-2xl font-semibold text-gray-800">{fmtMoney(data.drug_spend.annual)}</div>
            <div className="text-xs text-gray-400 mt-1">
              of which <b style={{ color: '#C62828' }}>{fmtMoney(data.drug_spend.wasted)}</b> on patients who drop out
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

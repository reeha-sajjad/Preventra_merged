import { useParams, useNavigate, Link } from 'react-router-dom';
import { ArrowLeft, AlertTriangle, CheckCircle, TrendingUp, TrendingDown, Heart, DollarSign, Pill, Activity, Shield, Building2 } from 'lucide-react';
import { LineChart, Line, XAxis, YAxis, ResponsiveContainer, Tooltip, CartesianGrid } from 'recharts';
import { SegmentDot } from '../components/shared';
import { SEGMENT_COLORS, SEGMENT_LABELS } from '../data/mockData';
import { usePatientRecord } from '../hooks/usePatient';
import { useSurvival } from '../hooks/useSurvival';
import { useRole } from '../context/RoleContext';
import { usePatients } from '../hooks/usePatients';
import CareTeamCard from '../components/hospital/CareTeamCard';
import FollowUpCard from '../components/followups/FollowUpCard';
import ClinicalGate from '../components/hospital/ClinicalGate';

// ── Plain-language interpretations keyed by driver label ──────────────────────
const INTERPRETATIONS = {
  'Financial pressure relative to income':
    'Out-of-pocket costs are high relative to income, creating financial pressure that commonly leads to discontinuation.',
  'Provider & pharmacy refill reliability':
    "Refill reliability for this patient's provider network is limited — supply disruptions significantly increase dropout risk.",
  'Blood sugar control (HbA1c)':
    'HbA1c level indicates the degree of clinical motivation. Higher uncontrolled values typically drive stronger resolve to continue therapy.',
  'Side effect intensity (GI friction)':
    'GI side effect intensity for this molecule is elevated. Nausea and GI discomfort are the leading cause of early discontinuation.',
  'Body weight / BMI severity':
    'Higher BMI patients typically see more visible early results, improving motivation — but stalled progress can become a dropout trigger.',
  'Out-of-pocket medication cost':
    'High absolute drug cost creates a direct, ongoing financial barrier to continued therapy.',
  'Overall disease burden':
    'Higher comorbidity burden increases clinical urgency, which can drive adherence — but also overwhelm the patient.',
};

// ── Action recommendations keyed by driver label ──────────────────────────────
const RECOMMENDATIONS = {
  'Financial pressure relative to income': {
    label: 'Copay Assistance',
    text:  'Connect patient to manufacturer copay assistance program. Average saving: $300–600/year.',
    color: '#EF6C00', bg: '#FFF3E0',
  },
  'Provider & pharmacy refill reliability': {
    label: 'Refill Continuity Check',
    text:  'Verify prior authorization status and confirm pharmacy refill continuity. Consider mail-order pharmacy.',
    color: '#1B4F8A', bg: '#EBF4FF',
  },
  'Side effect intensity (GI friction)': {
    label: 'Side Effect Check-in',
    text:  'Schedule a check-in call. Consider dose titration review or antiemetic support.',
    color: '#C62828', bg: '#FFEBEE',
  },
  'Blood sugar control (HbA1c)': {
    label: 'Standard Monitoring',
    text:  'High HbA1c indicates strong clinical motivation. Standard monitoring is sufficient.',
    color: '#2E7D32', bg: '#E8F5E9',
  },
  'Out-of-pocket medication cost': {
    label: 'Cost Support',
    text:  'Explore patient assistance programs and formulary alternatives.',
    color: '#EF6C00', bg: '#FFF3E0',
  },
  'Body weight / BMI severity': {
    label: 'Progress Milestone Review',
    text:  'Set clear weight milestone expectations. Stalled progress is a major dropout trigger — early re-engagement matters.',
    color: '#1B4F8A', bg: '#EBF4FF',
  },
  'Overall disease burden': {
    label: 'Care Coordination',
    text:  'High comorbidity may require coordinated care. Ensure treatment plan is integrated across providers.',
    color: '#1B4F8A', bg: '#EBF4FF',
  },
};

/* ── Risk color helper ────────────────────────────────────────────── */
function getRiskColor(pct) {
  if (pct >= 75) return '#C62828';
  if (pct >= 50) return '#EF6C00';
  if (pct >= 25) return '#F9A825';
  return '#2E7D32';
}

function getRiskLabel(pct) {
  if (pct >= 75) return 'Critical';
  if (pct >= 50) return 'High';
  if (pct >= 25) return 'Medium';
  return 'Low';
}

/* ── Stat item used in profile grid ───────────────────────────────── */
function StatItem({ label, value, sub, icon: Icon, iconColor }) {
  return (
    <div className="flex items-start gap-3">
      {Icon && (
        <div className="w-9 h-9 rounded-xl flex items-center justify-center flex-shrink-0 mt-0.5"
             style={{ background: `${iconColor}14` }}>
          <Icon size={16} style={{ color: iconColor }} />
        </div>
      )}
      <div>
        <div className="text-[11px] text-gray-400 uppercase tracking-wider font-medium">{label}</div>
        <div className="text-sm font-semibold text-gray-800 mt-0.5">{value}</div>
        {sub && <div className="text-[11px] text-gray-400 mt-0.5">{sub}</div>}
      </div>
    </div>
  );
}

/* ── SHAP Driver Card ─────────────────────────────────────────────── */
function DriverCard({ rank, driver, direction, shap }) {
  const isRisk  = direction?.includes('increases');
  const color   = isRisk ? '#EF6C00' : '#2E7D32';
  const bgColor = isRisk ? '#FFF8F0' : '#F0FFF4';
  const barWidth = Math.min(100, (Math.abs(shap ?? 0) / 0.5) * 100);

  return (
    <div className="rounded-xl p-5 transition-all flex flex-col justify-between h-full"
         style={{ background: bgColor, border: `1px solid ${color}18` }}>
      <div className="flex items-start justify-between gap-3 mb-4">
        <div className="flex items-start gap-3">
          <span className="text-xs font-bold rounded-full w-8 h-8 flex items-center justify-center flex-shrink-0 bg-white shadow-sm"
                style={{ color }}>
            {rank}
          </span>
          <div>
            <div className="text-sm font-medium text-gray-800 leading-snug">{driver}</div>
            <div className="text-xs mt-1.5 font-medium flex items-center gap-1" style={{ color }}>
              {isRisk ? <TrendingUp size={12} /> : <TrendingDown size={12} />}
              {isRisk ? 'Increasing risk' : 'Reducing risk'}
            </div>
          </div>
        </div>
        <span className="text-xs font-mono font-semibold text-gray-400 flex-shrink-0 mt-1">
          SHAP: {Math.abs(shap ?? 0).toFixed(3)}
        </span>
      </div>
      <div className="h-2.5 rounded-full overflow-hidden bg-white">
        <div className="h-full rounded-full transition-all duration-700"
             style={{ width: `${barWidth}%`, background: color }} />
      </div>
    </div>
  );
}

/* ── Main Page ────────────────────────────────────────────────────── */
export default function PatientDetail() {
  const { id }   = useParams();
  const navigate = useNavigate();
  const { isCostView, isPatient, canAssign } = useRole();
  const { summary, data: patientData, loading: patientLoading, reload } = usePatientRecord(id);
  const patientList = usePatients();
  const careTeamChanged = () => { reload(); patientList?.reload(); };
  const { data: survivalData } = useSurvival();

  const backButton = !isPatient && (
    <button onClick={() => navigate('/patients')}
      className="flex items-center gap-2 text-sm text-gray-400 hover:text-gray-700 mb-4 transition-colors font-medium">
      <ArrowLeft size={16} /> Back to patients
    </button>
  );

  // Overview layer only, until a hospital admin or insurer gives a reason.
  if (summary && !patientData && summary.detail_access === 'reason_required') {
    const pct = Math.round(summary.dropout_prob * 100);
    const color = getRiskColor(pct);
    const isDropout = summary.prediction === 'Dropout Risk';
    return (
      <div className="patient-detail-page animate-fade-in space-y-6">
        {backButton}
        <div className="card p-6 md:p-8 flex flex-wrap items-center justify-between gap-6">
          <div className="flex items-center gap-4">
            <div className="w-14 h-14 rounded-2xl flex items-center justify-center text-white text-lg font-bold"
                 style={{ background: 'var(--color-primary)' }}>#{summary.patient_idx}</div>
            <h1 className="text-xl font-semibold text-gray-800" style={{ fontFamily: 'DM Serif Display, serif' }}>
              Patient #{summary.patient_idx}
            </h1>
          </div>
          <div className="flex items-center gap-5">
            <div className="flex flex-col items-center">
              <div className="w-20 h-20 rounded-full flex items-center justify-center"
                   style={{ background: `${color}12`, border: `3px solid ${color}` }}>
                <span className="text-2xl font-bold font-mono" style={{ color }}>{pct}%</span>
              </div>
              <span className="text-[10px] font-semibold uppercase tracking-wider mt-1.5" style={{ color }}>
                {getRiskLabel(pct)} Risk
              </span>
            </div>
            <span className="inline-flex items-center gap-1.5 text-sm font-semibold px-4 py-2 rounded-xl"
                  style={{ background: isDropout ? '#FFEBEE' : '#E8F5E9', color: isDropout ? '#C62828' : '#2E7D32' }}>
              {isDropout ? <AlertTriangle size={15} /> : <CheckCircle size={15} />}
              {summary.prediction}
            </span>
          </div>
        </div>
        <CareTeamCard team={summary} patientIdx={summary.patient_idx} canAssign={summary.can_assign} onChanged={careTeamChanged} />
        <ClinicalGate patientIdx={summary.patient_idx} reasons={summary.reasons} onOpened={reload} />
      </div>
    );
  }

  if (!patientData) {
    return (
      <div className="card p-8 text-center text-sm text-gray-500 animate-fade-in">
        {patientLoading ? 'Loading patient…' : 'This patient record is not available to you.'}
      </div>
    );
  }

  const patient   = patientData.patient;
  const survivalCurves = survivalData?.curves ?? [];
  const survivalCheckpoints = survivalData?.checkpoints ?? [];

  const segColor = SEGMENT_COLORS[patient.cluster];
  const rec      = RECOMMENDATIONS[patient.driver_1] ?? RECOMMENDATIONS['Blood sugar control (HbA1c)'];
  const checkpoint = patientData.segment_survival ?? survivalCheckpoints[patient.cluster] ?? survivalCheckpoints[0];
  const pct = Math.round(patient.dropout_prob * 100);
  const riskColor = getRiskColor(pct);
  const riskLabel = getRiskLabel(pct);
  const isDropout = patient.prediction === 'Dropout Risk';

  const miniKM = survivalCurves.map(sc => ({
    ...sc,
    highlighted: sc.cluster === patient.cluster,
  }));

  const hba1cLabel =
    patient.LBXGH < 5.7  ? 'Normal' :
    patient.LBXGH < 6.5  ? 'Pre-diabetic' : 'Diabetic range';

  const frictionLabel =
    patient.bio_friction < 0.30 ? 'Low' :
    patient.bio_friction < 0.45 ? 'Moderate' : 'High';

  return (
    <div className="patient-detail-page animate-fade-in">

      {/* ── Back button ──────────────────────────────────────────── */}
      {backButton}

      {/* ── Finance-view context banner ──────────────────────────── */}
      {isCostView && (
        <div className="flex items-center justify-between gap-3 px-4 py-3 rounded-xl text-sm font-medium mb-5 animate-fade-up"
          style={{ background: '#EBF4FF', color: '#1B4F8A', border: '1px solid #BFDBFE' }}>
          <span className="flex items-center gap-2.5">
            <Building2 size={15} />
            Finance view — Individual patient detail is a care team tool. Population-level analysis is in the financial screens.
          </span>
          <div className="flex items-center gap-3 text-xs font-semibold flex-shrink-0">
            <Link to="/cost"   className="hover:underline underline-offset-2" style={{ color: '#1B4F8A' }}>Cost-Effectiveness →</Link>
            <Link to="/budget" className="hover:underline underline-offset-2" style={{ color: '#1B4F8A' }}>Budget Simulator →</Link>
          </div>
        </div>
      )}

      {/* ── Hero header ──────────────────────────────────────────── */}
      <div className="card p-6 md:p-8 mb-6">
        <div className="detail-hero-grid">
          {/* Patient identity */}
          <div className="flex items-center gap-4">
            <div className="w-14 h-14 rounded-2xl flex items-center justify-center text-white text-lg font-bold flex-shrink-0 shadow-sm"
                 style={{ background: segColor }}>
              #{patient.patient_idx}
            </div>
            <div>
              <h1 className="text-xl font-semibold text-gray-800" style={{ fontFamily: 'DM Serif Display, serif' }}>
                Patient #{patient.patient_idx}
              </h1>
              <div className="flex items-center gap-3 mt-1">
                <SegmentDot cluster={patient.cluster} size="sm" />
                <span className="text-xs text-gray-400">·</span>
                <span className="text-xs text-gray-500">{patient.RIDAGEYR} years old</span>
                <span className="text-xs text-gray-400">·</span>
                <span className="text-xs font-mono text-gray-500">{patient.assigned_molecule}</span>
              </div>
            </div>
          </div>

          {/* Risk score display */}
          <div className="flex items-center gap-5">
            {/* Circular-ish big risk number */}
            <div className="flex flex-col items-center">
              <div className="relative w-20 h-20 rounded-full flex items-center justify-center"
                   style={{ background: `${riskColor}12`, border: `3px solid ${riskColor}` }}>
                <span className="text-2xl font-bold font-mono" style={{ color: riskColor }}>{pct}%</span>
              </div>
              <span className="text-[10px] font-semibold uppercase tracking-wider mt-1.5" style={{ color: riskColor }}>
                {riskLabel} Risk
              </span>
            </div>
            {/* Prediction badge */}
            <div className="flex flex-col gap-2">
              <span className="inline-flex items-center gap-1.5 text-sm font-semibold px-4 py-2 rounded-xl"
                    style={{
                      background: isDropout ? '#FFEBEE' : '#E8F5E9',
                      color: isDropout ? '#C62828' : '#2E7D32',
                    }}>
                {isDropout ? <AlertTriangle size={15} /> : <CheckCircle size={15} />}
                {patient.prediction}
              </span>
              <span className="text-[11px] text-gray-400 text-center">Model prediction</span>
            </div>
          </div>
        </div>
      </div>

      {/* ── Care team, insurer and pharmacy ──────────────────────── */}
      <div className="mb-6">
        <CareTeamCard team={patient} patientIdx={patient.patient_idx}
          canAssign={canAssign && !isPatient} onChanged={careTeamChanged} />
      </div>

      {/* ── Follow-up request (care team asks, case manager handles) ── */}
      {!isPatient && (
        <div className="mb-6">
          <FollowUpCard patientIdx={patient.patient_idx} />
        </div>
      )}

      {/* ── Top 2-col grid: profile/financial/rec  |  drivers ──────── */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6 mb-6">

        {/* Left: Clinical Profile + Financial & Medication + Recommended Action */}
        <div className="flex flex-col gap-6">

          {/* Clinical Profile */}
          <div className="card p-6">
            <h2 className="text-base font-semibold text-gray-800 mb-5" style={{ fontFamily: 'DM Serif Display, serif' }}>
              Clinical Profile
            </h2>
            <div className="detail-stats-grid">
              <StatItem label="Age" value={`${patient.RIDAGEYR} years`} icon={Heart} iconColor="#E91E63" />
              <StatItem label="BMI" value={`${patient.BMXBMI} kg/m²`}
                sub={patient.BMXBMI >= 30 ? 'Obese' : patient.BMXBMI >= 25 ? 'Overweight' : 'Normal'}
                icon={Activity} iconColor="#1B4F8A" />
              <StatItem label="HbA1c" value={`${patient.LBXGH}%`} sub={hba1cLabel} icon={Activity} iconColor="#7B1FA2" />
              <StatItem label="Comorbidities" value={`${patient.comorbidity_score} / 2`}
                sub={patient.comorbidity_score >= 2 ? 'High burden' : 'Low burden'}
                icon={Shield} iconColor="#EF6C00" />
            </div>
          </div>

          {/* Financial & Medication */}
          <div className="card p-6">
            <h2 className="text-base font-semibold text-gray-800 mb-5" style={{ fontFamily: 'DM Serif Display, serif' }}>
              Financial & Medication
            </h2>
            <div className="detail-stats-grid">
              <StatItem label="OOP Cost" value={`$${patient.avg_oop_cost.toFixed(0)}/mo`} icon={DollarSign} iconColor="#EF6C00" />
              <StatItem label="Cost Pressure Index" value={patient.income_cost_pressure.toFixed(1)}
                sub={patient.income_cost_pressure > 60 ? 'High pressure' : 'Manageable'}
                icon={DollarSign} iconColor="#C62828" />
              <StatItem label="Molecule" value={patient.assigned_molecule} sub={`Generation ${patient.drug_generation}`}
                icon={Pill} iconColor="#1B4F8A" />
              <StatItem label="Side Effect Risk" value={frictionLabel}
                sub={`Bio friction: ${patient.bio_friction.toFixed(3)}`}
                icon={AlertTriangle} iconColor={patient.bio_friction > 0.45 ? '#C62828' : '#EF6C00'} />
            </div>
          </div>

          {/* Recommended Action */}
          <div className="card p-6" style={{ borderLeft: `4px solid ${rec.color}` }}>
            <div className="flex items-start gap-4">
              <div className="w-10 h-10 rounded-xl flex items-center justify-center flex-shrink-0"
                   style={{ background: rec.bg || `${rec.color}14` }}>
                <CheckCircle size={18} style={{ color: rec.color }} />
              </div>
              <div>
                <div className="text-xs font-semibold uppercase tracking-wider mb-1" style={{ color: rec.color }}>
                  Recommended: {rec.label}
                </div>
                <p className="text-sm text-gray-600 leading-relaxed">{rec.text}</p>
              </div>
            </div>
          </div>
        </div>

        {/* Right: Top Dropout Drivers */}
        <div className="card p-6 h-full flex flex-col">
          <h2 className="text-base font-semibold text-gray-800 mb-2" style={{ fontFamily: 'DM Serif Display, serif' }}>
            Top Dropout Drivers
          </h2>
          <p className="text-xs text-gray-400 mb-5">
            SHAP-based attribution for this patient's predicted dropout probability
          </p>
          <div className="flex flex-col flex-1 gap-3">
            {[
              { driver: patient.driver_1, direction: patient.driver_1_direction, shap: patient.driver_1_shap },
              { driver: patient.driver_2, direction: patient.driver_2_direction, shap: patient.driver_2_shap },
              { driver: patient.driver_3, direction: patient.driver_3_direction, shap: patient.driver_3_shap },
            ].map((d, i) => (
              <div key={i} className="flex-1 flex flex-col">
                <DriverCard rank={i + 1} {...d} />
              </div>
            ))}
          </div>

          {/* Interpretation callout */}
          <div className="mt-5 p-4 rounded-xl" style={{ background: '#F7FAFC', border: '1px solid #E2E8F0' }}>
            <div className="text-xs font-semibold text-gray-500 uppercase tracking-wider mb-2">
              Why is this patient flagged?
            </div>
            <p className="text-sm text-gray-600 leading-relaxed">
              {INTERPRETATIONS[patient.driver_1] ??
                'Multiple clinical and financial factors are contributing to elevated dropout risk.'}
            </p>
          </div>
        </div>
      </div>

      {/* ── Full-width bottom row: Survival Curve | Dropout Checkpoints ── */}
      {checkpoint && survivalCurves.length > 0 && (
      <div className="grid grid-cols-1 md:grid-cols-2 gap-6">

        {/* Survival Curve */}
        <div className="card p-6">
          <h2 className="text-base font-semibold text-gray-800 mb-2" style={{ fontFamily: 'DM Serif Display, serif' }}>
            Segment Survival Curve
          </h2>
          <p className="text-xs text-gray-400 mb-4">
            Highlighted: <span style={{ color: segColor, fontWeight: 600 }}>
              {SEGMENT_LABELS[patient.cluster].split(' ').slice(0, 3).join(' ')}
            </span>
          </p>
          <ResponsiveContainer width="100%" height={220}>
            <LineChart margin={{ top: 4, right: 12, bottom: 4, left: -10 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="#EDF2F7" />
              <XAxis dataKey="day" type="number" domain={[0, 180]}
                ticks={[0, 30, 60, 90, 180]} tick={{ fontSize: 11, fill: '#718096' }}
                axisLine={{ stroke: '#E2E8F0' }} tickLine={false} />
              <YAxis domain={[0, 1]} tickFormatter={v => `${Math.round(v * 100)}%`}
                tick={{ fontSize: 11, fill: '#718096' }} axisLine={false} tickLine={false} />
              <Tooltip formatter={v => `${(v * 100).toFixed(0)}%`}
                labelFormatter={v => `Day ${v}`} />
              {miniKM.map(sc => (
                <Line key={sc.cluster} data={sc.data} dataKey="survival" dot={false}
                  stroke={sc.highlighted ? SEGMENT_COLORS[sc.cluster] : '#E2E8F0'}
                  strokeWidth={sc.highlighted ? 3 : 1}
                  name={sc.label} />
              ))}
            </LineChart>
          </ResponsiveContainer>
        </div>

        {/* Dropout Checkpoints */}
        <div className="card p-6">
          <h2 className="text-base font-semibold text-gray-800 mb-5" style={{ fontFamily: 'DM Serif Display, serif' }}>
            Segment Dropout Checkpoints
          </h2>
          <div className="space-y-4">
            {[
              ['Day 30',  checkpoint.day30],
              ['Day 60',  checkpoint.day60],
              ['Day 90',  checkpoint.day90],
              ['Day 180', checkpoint.day180],
            ].map(([day, rate]) => {
              const p = rate * 100;
              const barColor = p >= 60 ? '#EF5350' : p >= 35 ? '#FF7043' : '#43A047';
              return (
                <div key={day}>
                  <div className="flex items-center justify-between mb-1.5">
                    <span className="text-sm font-medium text-gray-600">{day}</span>
                    <span className="text-sm font-bold font-mono" style={{ color: barColor }}>
                      {p.toFixed(1)}%
                    </span>
                  </div>
                  <div className="h-2.5 rounded-full overflow-hidden" style={{ background: '#EDF2F7' }}>
                    <div className="h-full rounded-full transition-all duration-700"
                         style={{ width: `${p}%`, background: barColor }} />
                  </div>
                </div>
              );
            })}
          </div>

          <div className="mt-5 p-4 rounded-xl text-sm text-gray-600 leading-relaxed"
               style={{ background: '#F7FAFC', border: '1px solid #E2E8F0' }}>
            In the <b>{SEGMENT_LABELS[patient.cluster]}</b> segment,{' '}
            {Math.round(checkpoint.day90 * 100)}% of patients discontinue by day 90.
          </div>
        </div>

      </div>
      )}
    </div>
  );
}
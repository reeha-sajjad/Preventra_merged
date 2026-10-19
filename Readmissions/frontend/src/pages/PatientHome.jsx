import React, { useCallback, useEffect, useState } from 'react';
import { Hospital, Stethoscope, UserRound, Store, Building2, PhoneCall, ShieldAlert, CalendarCheck, Pill, CreditCard } from 'lucide-react';
import { getMyHome } from '../api';
import AskDialog from '../components/patient/AskDialog';
import CaretakersCard from '../components/patient/CaretakersCard';
import MedicinesGivenCard from '../components/patient/MedicinesGivenCard';
import InsuranceCard from '../components/insurance/InsuranceCard';

// How each level of the status reads (api/patient_home.STATUS). Calm colours
// throughout: "extra support" is blue, not red - it means the team is helping.
const TONE = {
  on_track: { fg: '#1E6B34', bg: '#EAF5EC', ring: '#BFE0C6' },
  watch:    { fg: '#94520A', bg: '#FDF4E7', ring: '#F3D9AE' },
  support:  { fg: '#0F2A4A', bg: '#EBF3FC', ring: '#C4DAF3' },
};

function greeting() {
  const h = new Date().getHours();
  return h < 12 ? 'Good morning' : h < 18 ? 'Good afternoon' : 'Good evening';
}

const formatDate = (iso) => (iso
  ? new Date(iso.length === 10 ? `${iso}T00:00:00` : iso).toLocaleDateString(undefined, { day: 'numeric', month: 'long', year: 'numeric' })
  : '');

/**
 * A patient's home page (api/patient_home.py): their own record in plain
 * words - how their recovery looks, their hospital stay, their care team - and
 * a way to ask a case manager to get in touch. GLP-1's twin
 * (GLP1/Frontend/src/pages/PatientHome.jsx), without Zoe.
 */
export default function PatientHome() {
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);
  const [asking, setAsking] = useState(false);
  const [sent, setSent] = useState(null);
  // A caretaker looking after several people picks whose summary to see.
  const [who, setWho] = useState(null);

  const load = useCallback(() => {
    getMyHome(who).then((d) => { setData(d); setError(null); }).catch((e) => setError(e.message));
  }, [who]);
  useEffect(() => { load(); }, [load]);

  if (!data) {
    return <p className="p-6 text-sm text-gray-500">{error ? `Your summary couldn’t load: ${error}` : 'Loading your summary…'}</p>;
  }
  const first = data.name ? data.name.split(' ')[0] : null;
  const caretaker = data.viewer === 'caretaker';
  if (!data.linked && caretaker) {
    return (
      <div className="mx-auto mt-6 max-w-xl rounded-xl border border-gray-200 bg-white p-8 text-center">
        <h1 className="text-xl font-semibold text-gray-900">{greeting()}{first ? `, ${first}` : ''}</h1>
        <p className="mt-3 text-sm leading-relaxed text-gray-600">
          Nobody has added you as their caretaker yet. Ask the person you care for to add you from their own page,
          using the email you signed up with - or ask their hospital to, with their agreement.
        </p>
      </div>
    );
  }
  if (!data.linked) {
    return (
      <div className="mx-auto max-w-xl rounded-xl border border-gray-200 bg-white p-8 text-center">
        <h1 className="text-xl font-semibold text-gray-900">{greeting()}{first ? `, ${first}` : ''}</h1>
        <p className="mt-3 text-sm leading-relaxed text-gray-600">
          Your hospital record isn’t linked to your account yet. Ask your care team to link it, and your summary will
          appear here.
        </p>
      </div>
    );
  }

  const tone = TONE[data.status.key] || TONE.watch;
  const { stay, recovery, care_team: team, followup } = data;
  const current = followup.active || followup.recent;

  return (
    <div className="mx-auto max-w-[1080px] space-y-5 py-6">
      {caretaker && (
        // Whose summary this is - written to the patient, read by their carer.
        <div className="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-gray-200 bg-white px-5 py-4">
          <p className="text-sm text-gray-700">
            {greeting()}{first ? `, ${first}` : ''}. You’re looking at <span className="font-semibold">{data.caring_for.patient_name}</span>’s
            summary as their caretaker. It’s written to them.
          </p>
          {data.caring_for.people.length > 1 && (
            <label className="flex items-center gap-2 text-sm text-gray-600">Show
              <select value={data.patient_id} onChange={(e) => { setSent(null); setWho(e.target.value); }}
                className="rounded-lg border border-gray-200 px-2.5 py-1.5 text-sm">
                {data.caring_for.people.map((p) => <option key={p.patient_id} value={p.patient_id}>{p.name}</option>)}
              </select>
            </label>
          )}
        </div>
      )}
      <section className="rounded-2xl border p-6 md:p-8" style={{ background: tone.bg, borderColor: tone.ring }}
               aria-labelledby="status-title">
        {!caretaker && <p className="text-sm text-gray-600">{greeting()}{first ? `, ${first}` : ''}</p>}
        <h1 id="status-title" className="mt-2 max-w-[30ch] text-[26px] font-semibold leading-tight md:text-[32px]"
            style={{ color: tone.fg }}>
          {data.status.title}
        </h1>
        <p className="mt-3 max-w-[62ch] text-[15px] leading-relaxed text-gray-700">{data.status.text}</p>
      </section>

      <div className="grid gap-5 lg:grid-cols-3">
        <div className="space-y-5 lg:col-span-2">
          <Card icon={Hospital} title="Your hospital stay" id="stay">
            {(stay.admitted || stay.discharged) && (
              <dl className="mt-4 grid gap-3 sm:grid-cols-2">
                {stay.admitted && <Fact label="Admitted" value={formatDate(stay.admitted)} />}
                {stay.discharged && <Fact label="Went home" value={formatDate(stay.discharged)} />}
              </dl>
            )}
            {stay.reason && (
              <p className="mt-4 text-sm text-gray-700"><span className="text-gray-500">Main reason for your stay: </span>{stay.reason}</p>
            )}
            {stay.conditions.length > 0 && (
              <p className="mt-2 text-sm text-gray-700"><span className="text-gray-500">Conditions on your record: </span>{stay.conditions.join(', ')}</p>
            )}
            <p className="mt-3 text-xs text-gray-500">As your hospital recorded it. Your doctor can explain what it means for you.</p>
          </Card>

          <MedicinesGivenCard key={data.patient_id} patientId={caretaker ? data.patient_id : null} />

          <Card icon={CalendarCheck} title="Since you went home" id="recovery">
            <ul className="mt-4 space-y-3 text-sm text-gray-700">
              <li className="flex gap-3">
                <CalendarCheck size={16} className="mt-0.5 shrink-0 text-gray-400" />
                {recovery.weeks > 0
                  ? `Your care team has followed your recovery for ${recovery.weeks} week${recovery.weeks === 1 ? '' : 's'}.`
                  : 'Your care team will follow how you’re doing in the weeks after you went home.'}
              </li>
              {recovery.refill && (
                <li className="flex gap-3"><Pill size={16} className="mt-0.5 shrink-0 text-gray-400" />{recovery.refill}</li>
              )}
            </ul>
            <p className="mt-3 text-xs text-gray-500">
              Keep taking your medicines as prescribed, and go to your follow-up appointments.
            </p>
          </Card>
        </div>

        <div className="space-y-5">
          <Card icon={PhoneCall} title="Need to talk?" id="talk">
            {sent ? (
              <p className="mt-3 text-sm text-green-800" role="status">
                {sent.added_to_open
                  ? 'Sent. We added it to the request already open, so you’ll hear from one person.'
                  : 'Sent. A case manager will get in touch.'}
              </p>
            ) : current && current.status !== 'done' ? (
              <p className="mt-3 text-sm text-gray-700">
                {current.status === 'in_progress'
                  ? `${current.handled_by} is arranging this and will be in touch.`
                  : 'A case manager has been asked to get in touch. You’ll hear from them soon.'}
              </p>
            ) : (
              <p className="mt-3 text-sm text-gray-600">
                A case manager can help with how you’re feeling, your medicines, appointments or help at home.
              </p>
            )}
            {current?.status === 'done' && !sent && (
              <p className="mt-2 text-xs text-gray-500">
                Last request: {current.outcome_label?.toLowerCase()} on {formatDate(current.done_at)}.
              </p>
            )}
            {followup.can_ask ? (
              <button type="button" onClick={() => setAsking(true)}
                className="mt-4 w-full rounded-lg bg-ns-navy px-4 py-2.5 text-sm font-semibold text-white focus:outline-none focus-visible:ring-2 focus-visible:ring-blue-400 focus-visible:ring-offset-2">
                Ask a case manager to get in touch
              </button>
            ) : (
              <p className="mt-3 text-xs text-gray-500">Your record isn’t with a hospital yet, so there’s no case manager to ask.</p>
            )}
          </Card>

          <Card title="Your care team" id="team">
            <ul className="mt-4 space-y-3 text-sm">
              <TeamRow icon={Building2} role="Hospital" names={team.hospital ? [team.hospital] : []} />
              <TeamRow icon={Stethoscope} role="Doctor"
                       names={team.doctor ? [team.doctor.specialty ? `${team.doctor.name} (${team.doctor.specialty})` : team.doctor.name] : []} />
              <TeamRow icon={UserRound} role={team.nurses.length > 1 ? 'Nurses' : 'Nurse'} names={team.nurses.map((n) => n.name)} />
              <TeamRow icon={PhoneCall} role="Case managers" names={[]} empty="Your hospital’s case managers" muted={false} />
              <TeamRow icon={Store} role="Pharmacy" names={team.pharmacy ? [team.pharmacy] : []} />
              {!caretaker && (
                // The patient's own insurance and member ID, as on their card.
                <li className="flex gap-3">
                  <CreditCard size={16} className="mt-0.5 shrink-0 text-gray-400" />
                  <div className="min-w-0">
                    <div className="text-xs text-gray-500">Insurance</div>
                    <InsuranceCard patientId={data.patient_id} compact />
                  </div>
                </li>
              )}
            </ul>
          </Card>

          {!caretaker && <CaretakersCard />}

          <aside className="flex gap-3 rounded-xl border border-red-200 bg-red-50/60 p-4">
            <ShieldAlert size={18} className="mt-0.5 shrink-0 text-red-700" />
            <p className="text-xs leading-relaxed text-gray-700">{data.safety}</p>
          </aside>
        </div>
      </div>

      {asking && (
        <AskDialog reasons={followup.reasons} patientId={caretaker ? data.patient_id : undefined}
          onClose={() => setAsking(false)}
          onDone={(out) => { setAsking(false); setSent(out); load(); }} />
      )}
    </div>
  );
}

function Card({ icon: Icon, title, id, children }) {
  return (
    <section className="rounded-xl border border-gray-200 bg-white p-6 shadow-sm" aria-labelledby={`${id}-title`}>
      <h2 id={`${id}-title`} className="flex items-center gap-2 text-base font-semibold text-gray-900">
        {Icon && <Icon size={17} className="text-gray-400" />} {title}
      </h2>
      {children}
    </section>
  );
}

function Fact({ label, value }) {
  return (
    <div className="rounded-xl border border-gray-100 p-4">
      <dt className="text-xs text-gray-500">{label}</dt>
      <dd className="mt-1 text-lg font-semibold text-gray-900">{value}</dd>
    </div>
  );
}

function TeamRow({ icon: Icon, role, names, empty = 'Not assigned yet', muted = true }) {
  return (
    <li className="flex gap-3">
      <Icon size={16} className="mt-0.5 shrink-0 text-gray-400" />
      <div className="min-w-0">
        <div className="text-xs text-gray-500">{role}</div>
        <div className={names.length || !muted ? 'text-gray-800' : 'text-gray-400'}>{names.length ? names.join(', ') : empty}</div>
      </div>
    </li>
  );
}
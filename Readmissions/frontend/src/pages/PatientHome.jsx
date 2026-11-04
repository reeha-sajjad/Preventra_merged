import React, { useCallback, useEffect, useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import { ArrowLeft, Gauge, Hospital, Stethoscope, UserRound, Store, Building2, PhoneCall, ShieldAlert, CalendarCheck, Pill, CreditCard, MapPin, Clock, Info } from 'lucide-react';
import { getMyHome } from '../api';
import AskDialog from '../components/patient/AskDialog';
import CaretakersCard from '../components/patient/CaretakersCard';
import MedicinesGivenCard from '../components/patient/MedicinesGivenCard';
import CheckInCard from '../components/patient/CheckInCard';
import InsuranceCard from '../components/insurance/InsuranceCard';

const BAND_TEXT = { High: 'text-red-700', Medium: 'text-amber-700', Low: 'text-green-700' };
const BAND_PILL = { High: 'bg-red-50 text-red-700', Medium: 'bg-amber-50 text-amber-800', Low: 'bg-green-50 text-green-800' };

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
  // A caretaker opens one of their people from My people (/person/:pid).
  const { pid: who = null } = useParams();

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
  // A caretaker reads about the patient by name; the patient reads "you".
  const them = caretaker ? (data.caring_for?.short_name || data.caring_for?.patient_name || 'them') : null;
  const say = (forPatient, forCarer) => (caretaker ? forCarer : forPatient);
  const current = followup.active || followup.recent;

  return (
    <div className="mx-auto max-w-[1080px] space-y-5 py-6">
      {caretaker && (
        // Whose summary this is - written to the patient, read by their carer.
        <div className="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-gray-200 bg-white px-5 py-4">
          <p className="text-sm text-gray-700">
            {greeting()}{first ? `, ${first}` : ''}. You’re looking at <span className="font-semibold">{data.caring_for.patient_name}</span>’s
            summary as their caretaker.
          </p>
          <div className="flex items-center gap-4">
            <Link to={`/patients/${encodeURIComponent(data.patient_id)}`} className="text-sm font-semibold text-ns-navy">
              Full clinical record
            </Link>
            <Link to="/" className="inline-flex items-center gap-1 text-sm font-semibold text-ns-navy">
              <ArrowLeft size={14} /> All my people
            </Link>
          </div>
          {/* The care team's score, for the carer who helps manage the recovery. */}
          {data.risk && (
            <div className="flex w-full flex-wrap items-center gap-3 rounded-lg border border-gray-100 px-4 py-3">
              <Gauge size={16} className="text-gray-400" />
              <span className="text-sm text-gray-600">Chance of going back into hospital, as the care team sees it:</span>
              <span className={`text-lg font-semibold ${BAND_TEXT[data.risk.band] || 'text-gray-800'}`}>{data.risk.score}%</span>
              {data.risk.band && (
                <span className={`rounded-full px-2 py-0.5 text-xs font-semibold ${BAND_PILL[data.risk.band] || ''}`}>{data.risk.band}</span>
              )}
            </div>
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

      {/* What someone comes to this page for, first: what to take, and when to go. */}
      <div className="grid items-start gap-5 lg:grid-cols-3">
        <MedicineList className="lg:col-span-2" meds={data.medicines} say={say} them={them} />
        <div className="space-y-5">
          <NextAppointment appt={data.next_appointment} say={say} them={them}
            onAsk={followup.can_ask ? () => setAsking(true) : null} />
            <Card title={say('Your care team', `${them}’s care team`)} id="team">
              <ul className="mt-4 space-y-3 text-sm">
                <TeamRow icon={Building2} role="Hospital" names={team.hospital ? [team.hospital] : []} />
                <TeamRow icon={Stethoscope} role="Doctor" person
                         names={team.doctor ? [team.doctor.name] : []} detail={team.doctor?.specialty} />
                <TeamRow icon={UserRound} role={team.nurses.length > 1 ? 'Nurses' : 'Nurse'} person names={team.nurses.map((n) => n.name)} />
                <TeamRow icon={PhoneCall} role={(team.case_managers || []).length === 1 ? 'Case manager' : 'Case managers'} person
                         names={team.case_managers || []} empty={say('Your hospital’s case managers', 'The hospital’s case managers')}
                         muted={false} />
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
        </div>
      </div>

      <div className="grid gap-5 lg:grid-cols-3">
        <div className="space-y-5 lg:col-span-2">
          <CheckInCard key={`checkin-${data.patient_id}`} patientId={caretaker ? data.patient_id : null}
            caretaker={caretaker} patientName={data.caring_for?.patient_name} />

          <Card icon={Hospital} title={say('Your hospital stay', `${them}’s hospital stay`)} id="stay">
            {(stay.admitted || stay.discharged) && (
              <dl className="mt-4 grid gap-3 sm:grid-cols-2">
                {stay.admitted && <Fact label="Admitted" value={formatDate(stay.admitted)} />}
                {stay.discharged && <Fact label="Went home" value={formatDate(stay.discharged)} />}
              </dl>
            )}
            {stay.reason && (
              <p className="mt-4 text-sm text-gray-700"><span className="text-gray-500">{say('Main reason for your stay: ', 'Main reason for the stay: ')}</span>{stay.reason}</p>
            )}
            {stay.conditions.length > 0 && (
              <p className="mt-2 text-sm text-gray-700"><span className="text-gray-500">{say('Conditions on your record: ', 'Conditions on their record: ')}</span>{stay.conditions.join(', ')}</p>
            )}
            <p className="mt-3 text-xs text-gray-500">{say('As your hospital recorded it. Your doctor can explain what it means for you.',
              'As the hospital recorded it. Their doctor can explain what it means.')}</p>
          </Card>

          <MedicinesGivenCard key={data.patient_id} patientId={caretaker ? data.patient_id : null} />

          <Card icon={CalendarCheck} title={say('Since you went home', `Since ${them} went home`)} id="recovery">
            <ul className="mt-4 space-y-3 text-sm text-gray-700">
              <li className="flex gap-3">
                <CalendarCheck size={16} className="mt-0.5 shrink-0 text-gray-400" />
                {recovery.weeks > 0
                  ? say(`Your care team has followed your recovery for ${recovery.weeks} week${recovery.weeks === 1 ? '' : 's'}.`,
                        `The care team has followed ${them}’s recovery for ${recovery.weeks} week${recovery.weeks === 1 ? '' : 's'}.`)
                  : say('Your care team will follow how you’re doing in the weeks after you went home.',
                        `The care team will follow how ${them} is doing in the weeks after they went home.`)}
              </li>
              {recovery.refill && (
                <li className="flex gap-3"><Pill size={16} className="mt-0.5 shrink-0 text-gray-400" />{recovery.refill}</li>
              )}
            </ul>
            <p className="mt-3 text-xs text-gray-500">
              {say('Keep taking your medicines as prescribed, and go to your follow-up appointments.',
                   `Help ${them} keep taking their medicines as prescribed and get to their follow-up appointments.`)}
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
                {say('A case manager can help with how you’re feeling, your medicines, appointments or help at home.',
                     `A case manager can help with how ${them} is doing, their medicines, appointments or help at home.`)}
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
              <p className="mt-3 text-xs text-gray-500">{say('Your record isn’t with a hospital yet', 'Their record isn’t with a hospital yet')}, so there’s no case manager to ask.</p>
            )}
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

const AVATAR = ['bg-sky-100 text-sky-800', 'bg-emerald-100 text-emerald-800', 'bg-violet-100 text-violet-800',
                'bg-amber-100 text-amber-800', 'bg-rose-100 text-rose-800'];
const initials = (name) => name.replace(/^Dr\.? /, '').split(' ').filter(Boolean).slice(0, 2).map((w) => w[0]).join('').toUpperCase();

function TeamRow({ icon: Icon, role, names, empty = 'Not assigned yet', muted = true, person = false, detail = null }) {
  const first = names[0];
  return (
    <li className="flex items-center gap-3">
      {person && first ? (
        <span aria-hidden="true" className={`flex h-9 w-9 shrink-0 items-center justify-center rounded-full text-xs font-semibold ${
          AVATAR[first.length % AVATAR.length]}`}>{initials(first)}</span>
      ) : (
        <span aria-hidden="true" className="flex h-9 w-9 shrink-0 items-center justify-center rounded-full bg-gray-100">
          <Icon size={16} className="text-gray-500" />
        </span>
      )}
      <div className="min-w-0">
        <div className="text-xs text-gray-500">{role}</div>
        <div className={`font-medium ${names.length || !muted ? 'text-gray-900' : 'text-gray-400'}`}>
          {names.length ? names.join(', ') : empty}
          {detail && <span className="font-normal text-gray-500"> · {detail}</span>}
        </div>
      </div>
    </li>
  );
}

/** What to take: each medicine with its dose (api/patient_medicines.for_home). */
function MedicineList({ meds, say, them, className = '' }) {
  const list = meds?.medicines || [];
  return (
    <section className={`rounded-xl border border-gray-200 bg-white p-6 shadow-sm ${className}`} aria-labelledby="meds-title">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h2 id="meds-title" className="flex items-center gap-2 text-base font-semibold text-gray-900">
          <Pill size={17} className="text-gray-400" /> {say('Your medicines', `${them}’s medicines`)}
        </h2>
        {list.length > 0 && <span className="text-xs text-gray-500">{list.length} medicine{list.length === 1 ? '' : 's'}</span>}
      </div>
      {meds?.has_demo && (
        <p className="mt-3 flex items-start gap-2 rounded-lg bg-amber-50 px-3 py-2 text-xs text-amber-900">
          <Info size={14} className="mt-0.5 shrink-0" /> Demo medicines - made up for this demo, not from a real record.
        </p>
      )}
      {list.length === 0 ? (
        <p className="mt-4 text-sm text-gray-500">
          {say('Your care team hasn’t added your medicines here yet. Follow your discharge letter.',
               `The care team hasn’t added ${them}’s medicines here yet. Follow the discharge letter.`)}
        </p>
      ) : (
        <ul className="mt-3 divide-y divide-gray-100">
          {list.map((m) => (
            <li key={m.id} className="flex items-center gap-3 py-3">
              <span aria-hidden="true" className="flex h-9 w-9 shrink-0 items-center justify-center rounded-full bg-ns-navy/5">
                <Pill size={16} className="text-ns-navy" />
              </span>
              <div className="min-w-0 flex-1">
                <div className="font-semibold text-gray-900">{m.drug}</div>
                <div className="text-xs text-gray-500">
                  {m.route}{m.instructions ? ` · ${m.instructions}` : ''}
                </div>
              </div>
              <div className="shrink-0 text-right">
                <div className="text-base font-semibold text-ns-navy">{m.dose}</div>
                <div className="text-xs text-gray-600">{m.how_often}</div>
              </div>
            </li>
          ))}
        </ul>
      )}
      <p className="mt-4 text-xs text-gray-500">
        {say('Questions about a medicine or a dose? Ask your doctor or pharmacist - don’t change a dose on your own.',
             `Questions about a medicine or a dose? Ask ${them}’s doctor or pharmacist - don’t change a dose on your own.`)}
      </p>
    </section>
  );
}

/** The next booked appointment (api/followups.next_appointment). */
function NextAppointment({ appt, say, them, onAsk }) {
  const day = appt?.date ? new Date(`${appt.date}T00:00:00`) : null;
  return (
    <section className="flex flex-col rounded-xl border border-gray-200 bg-white p-6 shadow-sm" aria-labelledby="appt-title">
      <h2 id="appt-title" className="flex items-center gap-2 text-base font-semibold text-gray-900">
        <CalendarCheck size={17} className="text-gray-400" /> Next appointment
      </h2>
      {day ? (
        <div className="mt-4 flex gap-4">
          <div className="flex h-16 w-16 shrink-0 flex-col items-center justify-center rounded-xl bg-ns-navy text-white">
            <span className="text-[11px] font-semibold uppercase tracking-wide opacity-80">
              {day.toLocaleDateString(undefined, { month: 'short' })}
            </span>
            <span className="text-2xl font-semibold leading-none">{day.getDate()}</span>
          </div>
          <div className="min-w-0 space-y-1 text-sm text-gray-700">
            <div className="font-semibold text-gray-900">{day.toLocaleDateString(undefined, { weekday: 'long' })}</div>
            {appt.time && <div className="flex items-center gap-1.5"><Clock size={14} className="text-gray-400" /> {appt.time}</div>}
            {appt.place && <div className="flex items-center gap-1.5"><MapPin size={14} className="text-gray-400" /> {appt.place}</div>}
            {appt.with && <div className="flex items-center gap-1.5"><Stethoscope size={14} className="text-gray-400" /> {appt.with}</div>}
          </div>
        </div>
      ) : (
        <p className="mt-4 flex-1 text-sm text-gray-500">
          {say('Nothing booked yet. If you need one, a case manager can arrange it.',
               `Nothing booked for ${them} yet. If they need one, a case manager can arrange it.`)}
        </p>
      )}
      {appt?.booked_by && <p className="mt-3 text-xs text-gray-400">Booked by {appt.booked_by}</p>}
      {!day && onAsk && (
        <button type="button" onClick={onAsk}
          className="mt-4 rounded-lg border border-ns-navy/30 px-3 py-2 text-sm font-semibold text-ns-navy hover:bg-ns-navy/5">
          Ask for an appointment
        </button>
      )}
    </section>
  );
}

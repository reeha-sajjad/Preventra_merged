import { useCallback, useEffect, useState } from 'react';
import { Pill, Stethoscope, HeartPulse, UserRound, Store, Building2, CheckCircle2, Eye, PhoneCall, ShieldAlert, Clock } from 'lucide-react';
import { api } from '../data/api';
import PageState from '../components/shared/PageState';
import AskDialog from '../components/patient/AskDialog';
import ZoeCard from '../components/patient/ZoeCard';

// How each level of the status reads (Backend/core/patient_home.STATUS). Calm
// colours throughout: even the "extra support" level is blue, not red - it
// means the team is helping, not that something is wrong.
const TONE = {
  on_track: { fg: '#1E6B34', bg: '#EAF5EC', ring: '#BFE0C6' },
  watch:    { fg: '#94520A', bg: '#FDF4E7', ring: '#F3D9AE' },
  support:  { fg: '#1B4F8A', bg: '#EBF3FC', ring: '#C4DAF3' },
};

function greeting() {
  const h = new Date().getHours();
  return h < 12 ? 'Good morning' : h < 18 ? 'Good afternoon' : 'Good evening';
}

function formatDate(iso) {
  if (!iso) return '';
  return new Date(iso).toLocaleDateString(undefined, { day: 'numeric', month: 'long', year: 'numeric' });
}

/**
 * A patient's home page (Backend/core/patient_home.py): their own record in
 * plain words - how things look, their medicine, their numbers, their care
 * team - and a way to ask their case manager to get in touch.
 */
export default function PatientHome() {
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);
  const [asking, setAsking] = useState(false);
  const [sent, setSent] = useState(null);

  const load = useCallback(() => {
    api.getMyHome().then((d) => { setData(d); setError(null); }).catch((e) => setError(e.message));
  }, []);
  useEffect(() => { load(); }, [load]);

  if (!data) return <PageState error={error} label="your health summary" />;

  const first = data.name ? data.name.split(' ')[0] : null;
  if (!data.linked) {
    return (
      <div className="max-w-xl mx-auto card p-8 text-center animate-fade-in">
        <h1 className="text-xl text-gray-800" style={{ fontFamily: 'DM Serif Display, serif' }}>
          {greeting()}{first ? `, ${first}` : ''}
        </h1>
        <p className="mt-3 text-sm text-gray-600 leading-relaxed">
          Your GLP-1 record isn’t linked to your account yet. Ask your care team to link it, and your health
          summary will appear here.
        </p>
      </div>
    );
  }

  const tone = TONE[data.status.key] || TONE.on_track;
  const { medicine, care_team: team, followup } = data;
  const current = followup.active || followup.recent;

  return (
    <div className="max-w-[1080px] mx-auto space-y-5 animate-fade-in">
      {/* How things look - the one thing a patient opens this page to read */}
      <section className="rounded-2xl p-6 md:p-8 border" style={{ background: tone.bg, borderColor: tone.ring }}
               aria-labelledby="status-title">
        <p className="text-sm text-gray-600">{greeting()}{first ? `, ${first}` : ''}</p>
        <h1 id="status-title" className="mt-2 text-[28px] md:text-[34px] leading-tight max-w-[30ch]"
            style={{ fontFamily: 'DM Serif Display, serif', color: tone.fg }}>
          {data.status.title}
        </h1>
        <p className="mt-3 text-[15px] leading-relaxed text-gray-700 max-w-[62ch]">{data.status.text}</p>
        <p className="mt-4 text-xs text-gray-500">Based on your record as of {data.data_as_of}.</p>
      </section>

      <div className="grid gap-5 lg:grid-cols-3">
        <div className="lg:col-span-2 space-y-5">
          {/* Zoe - the patient's own reminders */}
          <ZoeCard />

          {/* Medicine */}
          <section className="card p-6" aria-labelledby="med-title">
            <h2 id="med-title" className="flex items-center gap-2 text-base font-semibold text-gray-800">
              <Pill size={17} className="text-gray-400" /> Your medicine
            </h2>
            {medicine.stopped ? (
              <p className="mt-3 text-sm text-gray-700">
                Your doctor recorded that you stopped GLP-1 treatment
                {medicine.changed_on ? ` on ${formatDate(medicine.changed_on)}` : ''}.
              </p>
            ) : (
              <div className="mt-3 flex flex-wrap items-baseline gap-x-6 gap-y-2">
                <div className="text-2xl text-gray-900" style={{ fontFamily: 'DM Serif Display, serif' }}>{medicine.label}</div>
                {medicine.schedule && (
                  <div className="flex items-center gap-1.5 text-sm text-gray-600">
                    <Clock size={14} className="text-gray-400" /> {medicine.schedule}
                  </div>
                )}
              </div>
            )}
            {medicine.changed_on && !medicine.stopped && (
              <p className="mt-2 text-xs text-gray-500">
                Changed on {formatDate(medicine.changed_on)}{medicine.change_reason ? ` (${medicine.change_reason.toLowerCase()})` : ''}.
              </p>
            )}
            <p className="mt-3 text-xs text-gray-500">
              Take it as your doctor prescribed. Don’t change or stop it without talking to your care team.
            </p>
          </section>

          {/* What matters for this patient */}
          {data.factors.length > 0 && (
            <section className="card p-6" aria-labelledby="factors-title">
              <h2 id="factors-title" className="flex items-center gap-2 text-base font-semibold text-gray-800">
                <HeartPulse size={17} className="text-gray-400" /> What matters most for you
              </h2>
              <ul className="mt-4 space-y-4">
                {data.factors.map((f) => (
                  <li key={f.title} className="flex gap-3">
                    <span className="mt-0.5 flex-shrink-0 w-7 h-7 rounded-full flex items-center justify-center"
                          style={{ background: f.helping ? '#EAF5EC' : '#FDF4E7' }}>
                      {f.helping
                        ? <CheckCircle2 size={15} style={{ color: '#1E6B34' }} aria-label="Helping you" />
                        : <Eye size={15} style={{ color: '#94520A' }} aria-label="Worth watching" />}
                    </span>
                    <div>
                      <div className="text-sm font-semibold text-gray-800">{f.title}</div>
                      <p className="text-sm text-gray-600 leading-relaxed mt-0.5 max-w-[62ch]">{f.text}</p>
                    </div>
                  </li>
                ))}
              </ul>
            </section>
          )}

          {/* Clinical profile */}
          <section className="card p-6" aria-labelledby="profile-title">
            <h2 id="profile-title" className="flex items-center gap-2 text-base font-semibold text-gray-800">
              <Stethoscope size={17} className="text-gray-400" /> Your health numbers
            </h2>
            <dl className="mt-4 grid gap-3 sm:grid-cols-3">
              {data.profile.map((p) => (
                <div key={p.key} className="rounded-xl border border-gray-100 p-4">
                  <dt className="text-xs text-gray-500">{p.label}</dt>
                  <dd className="mt-1 text-2xl text-gray-900" style={{ fontFamily: 'DM Serif Display, serif' }}>{p.value}</dd>
                  {p.note && <dd className="mt-0.5 text-xs text-gray-600">{p.note}</dd>}
                </div>
              ))}
            </dl>
            {data.conditions.length > 0 && (
              <p className="mt-4 text-sm text-gray-600">
                Also on your record: {data.conditions.join(' and ').toLowerCase().replace(/^./, (c) => c.toUpperCase())}.
              </p>
            )}
            <p className="mt-3 text-xs text-gray-500">
              From your record as of {data.data_as_of}. Your doctor can explain what these mean for you.
            </p>
          </section>
        </div>

        <div className="space-y-5">
          {/* Case manager - asking, and what happened */}
          <section className="card p-6" aria-labelledby="cm-title">
            <h2 id="cm-title" className="flex items-center gap-2 text-base font-semibold text-gray-800">
              <PhoneCall size={17} className="text-gray-400" /> Need to talk?
            </h2>
            {sent ? (
              <p className="mt-3 text-sm" style={{ color: '#1E6B34' }} role="status">
                {sent.added_to_open
                  ? 'Sent. We added it to the request already open, so you’ll hear from one person.'
                  : `Sent. ${team.case_manager?.name || 'A case manager'} will get in touch.`}
              </p>
            ) : current && current.status !== 'done' ? (
              <p className="mt-3 text-sm text-gray-700">
                {current.status === 'in_progress'
                  ? `${current.handled_by} is arranging this and will be in touch.`
                  : 'Your case manager has been asked to get in touch. You’ll hear from them soon.'}
              </p>
            ) : (
              <p className="mt-3 text-sm text-gray-600">
                Your case manager can help with side effects, costs, refills or booking an appointment.
              </p>
            )}
            {current?.status === 'done' && !sent && (
              <p className="mt-2 text-xs text-gray-500">
                Last request: {current.outcome_label?.toLowerCase()} on {formatDate(current.done_at)}.
              </p>
            )}
            {followup.can_ask ? (
              <button type="button" onClick={() => setAsking(true)}
                className="mt-4 w-full inline-flex items-center justify-center gap-2 text-sm font-semibold px-4 py-2.5 rounded-lg text-white focus:outline-none focus-visible:ring-2 focus-visible:ring-offset-2 focus-visible:ring-blue-400"
                style={{ background: 'var(--color-primary)' }}>
                Ask my case manager to get in touch
              </button>
            ) : (
              <p className="mt-3 text-xs text-gray-500">Your record isn’t with a hospital yet, so there’s no case manager to ask.</p>
            )}
          </section>

          {/* Care team */}
          <section className="card p-6" aria-labelledby="team-title">
            <h2 id="team-title" className="text-base font-semibold text-gray-800">Your care team</h2>
            <ul className="mt-4 space-y-3 text-sm">
              <TeamRow icon={Building2} role="Hospital" names={team.hospital ? [team.hospital] : []} />
              <TeamRow icon={Stethoscope} role={team.doctors.length > 1 ? 'Doctors' : 'Doctor'} names={team.doctors.map((d) => d.name)} />
              <TeamRow icon={UserRound} role={team.nurses.length > 1 ? 'Nurses' : 'Nurse'} names={team.nurses.map((n) => n.name)} />
              <TeamRow icon={PhoneCall} role="Case manager" names={team.case_manager ? [team.case_manager.name] : []}
                       empty="Your hospital’s case managers" />
              <TeamRow icon={Store} role="Pharmacy" names={team.pharmacy ? [team.pharmacy] : []} />
            </ul>
          </section>

          {/* Safety - always shown, never dismissible */}
          <aside className="rounded-xl border p-4 flex gap-3" style={{ borderColor: '#F5C6C6', background: '#FFF6F6' }}>
            <ShieldAlert size={18} className="flex-shrink-0 mt-0.5" style={{ color: '#B42318' }} />
            <p className="text-xs leading-relaxed text-gray-700">{data.safety}</p>
          </aside>
        </div>
      </div>

      {asking && (
        <AskDialog reasons={followup.reasons} caseManager={team.case_manager?.name}
          onClose={() => setAsking(false)}
          onDone={(out) => { setAsking(false); setSent(out); load(); }} />
      )}
    </div>
  );
}

function TeamRow({ icon: Icon, role, names, empty = 'Not assigned yet' }) {
  return (
    <li className="flex gap-3">
      <Icon size={16} className="flex-shrink-0 mt-0.5 text-gray-400" />
      <div className="min-w-0">
        <div className="text-xs text-gray-500">{role}</div>
        <div className={names.length ? 'text-gray-800' : 'text-gray-400'}>{names.length ? names.join(', ') : empty}</div>
      </div>
    </li>
  );
}
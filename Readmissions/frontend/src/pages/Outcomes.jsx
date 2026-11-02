import React, { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import {
  Target, AlertCircle, BellRing, Timer, CalendarClock, PhoneOff, ClipboardCheck, HeartHandshake, Users,
  MessageSquareText, Flag, Info, Brain,
} from 'lucide-react';
import { getOutcomes } from '../api';

/**
 * Outcomes (api/outcomes.py): did the care team act on what Preventra told
 * them, and did fewer patients come back? Acting on it - alerts, follow-ups,
 * staying in touch, discharge plans - over a chosen period; readmissions
 * observed against what the discharge scores expected, and who came back - by
 * patient, condition and last readings, every group of fewer than 11 withheld
 * by the server. For hospital admins, case managers and the superadmin. Counts
 * over the caller's patients only.
 */
const hours = (h) => (h === null || h === undefined ? '—' : h < 1 ? `${Math.round(h * 60)} min` : h < 48 ? `${h} h` : `${Math.round(h / 24)} days`);
const pct = (n, d) => (d ? `${Math.round((100 * n) / d)}%` : '—');
const rate = (r) => (r === null || r === undefined ? '—' : `${r}%`);
const plural = (n, word) => `${(n ?? 0).toLocaleString()} ${word}${n === 1 ? '' : 's'}`;
const shown = (n) => (n === null || n === undefined ? 'fewer than 11' : n.toLocaleString());
const HIDDEN = {
  small: 'Fewer than 11 patients - hidden',
  complement: 'Hidden so the smaller group beside it cannot be worked out',
};

function Tile({ icon, label, value, sub, tone = 'gray', to }) {
  const Icon = icon;
  const tones = {
    gray: 'bg-gray-50 border-gray-100 text-gray-600',
    red: 'bg-red-50/50 border-red-100 text-red-700',
    amber: 'bg-amber-50/50 border-amber-100 text-amber-800',
    green: 'bg-green-50/40 border-green-100 text-green-800',
  };
  const body = (
    <>
      <div className="flex items-center gap-2 text-sm font-medium"><Icon size={17} /> <span>{label}</span></div>
      <div className="mt-2 text-3xl font-bold text-gray-900">{value}</div>
      {sub && <div className="mt-1 text-xs text-gray-600">{sub}</div>}
    </>
  );
  const cls = `block rounded-lg border p-4 ${tones[tone]}`;
  return to ? <Link to={to} className={`${cls} transition-shadow hover:border-gray-300 hover:shadow-sm`}>{body}</Link>
    : <div className={cls}>{body}</div>;
}

function Section({ title, sub, children }) {
  return (
    <section className="space-y-3">
      <div>
        <h2 className="text-lg font-semibold text-ns-navy">{title}</h2>
        {sub && <p className="text-sm text-gray-600">{sub}</p>}
      </div>
      {children}
    </section>
  );
}

function Bar({ label, count, total, tone = 'bg-ns-navy/70' }) {
  const width = total ? Math.round((100 * count) / total) : 0;
  return (
    <li>
      <div className="flex justify-between text-sm"><span className="text-gray-700">{label}</span>
        <span className="font-semibold text-gray-900">{count.toLocaleString()} <span className="font-normal text-gray-500">· {pct(count, total)}</span></span></div>
      <div className="mt-1 h-1.5 overflow-hidden rounded-full bg-gray-100"><div className={`h-full rounded-full ${tone}`} style={{ width: `${width}%` }} /></div>
    </li>
  );
}

export default function Outcomes() {
  const [days, setDays] = useState(30);
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);

  useEffect(() => {
    let live = true;
    getOutcomes(days).then((d) => { if (live) setData(d); }).catch((e) => { if (live) setError(e.message); });
    return () => { live = false; };
  }, [days]);

  return (
    <div className="mx-auto max-w-7xl space-y-8 p-4 sm:p-6">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="flex items-center gap-2 text-2xl font-bold text-ns-navy">
            <Target size={24} className="text-gray-400" /> Outcomes
          </h1>
          <p className="mt-1 text-sm text-gray-600">How the care team acted on what Preventra flagged, and how many patients came back.</p>
        </div>
        <div role="group" aria-label="Period" className="inline-flex rounded-lg border border-gray-200 bg-white p-0.5">
          {(data?.periods || [7, 30, 90]).map((d) => (
            <button key={d} type="button" aria-pressed={days === d}
              onClick={() => { if (d !== days) { setData(null); setError(null); setDays(d); } }}
              className={`rounded-md px-3 py-1.5 text-sm font-medium ${days === d ? 'bg-ns-navy text-white' : 'text-gray-700 hover:bg-gray-50'}`}>
              {d} days
            </button>
          ))}
        </div>
      </div>

      {error && (
        <div className="flex items-start gap-3 rounded-xl border border-red-200 bg-red-50 p-4 text-red-800">
          <AlertCircle size={20} className="mt-0.5 shrink-0" />
          <div><div className="font-semibold">The outcomes couldn’t load</div><div className="mt-0.5 text-sm">{error}</div></div>
        </div>
      )}
      {!data && !error && (
        <div className="grid animate-pulse gap-4 sm:grid-cols-2 lg:grid-cols-4">
          {[...Array(8)].map((_, i) => <div key={i} className="h-28 rounded-lg bg-gray-100" />)}
        </div>
      )}
      {data && <Body data={data} />}
    </div>
  );
}

function Body({ data }) {
  const { alerts: a, followups: f, contact: c, discharge_planning: p, readmissions: r } = data;
  return (
    <>
      <Section title="Acting on alerts" sub={`Clinical alerts raised in the last ${data.days} days.`}>
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
          <Tile icon={BellRing} label="Alerts raised" value={a.raised.toLocaleString()}
            sub={a.unrouted ? `${a.unrouted} with no doctor to route to` : 'all routed to a doctor'} tone={a.unrouted ? 'amber' : 'gray'} />
          <Tile icon={ClipboardCheck} label="Acknowledged" value={pct(a.acknowledged, a.raised)}
            sub={`${a.acknowledged.toLocaleString()} of ${a.raised.toLocaleString()}`} />
          <Tile icon={Timer} label="Time to acknowledge" value={hours(a.median_hours_to_acknowledge)} sub="median" />
          <Tile icon={MessageSquareText} label="Answered by a clinician" value={pct(a.answered, a.raised)}
            sub={`median ${hours(a.median_hours_to_answer)} after the alert`} />
        </div>
      </Section>

      <Section title="Follow-ups" sub={`Requests made in the last ${data.days} days, and how they ended.`}>
        <div className="grid gap-4 lg:grid-cols-3">
          <div className="grid gap-4 sm:grid-cols-2 lg:col-span-2">
            <Tile icon={CalendarClock} label="Requested" value={f.requested.toLocaleString()}
              sub={`${f.done.toLocaleString()} done${f.median_days_to_done === null ? '' : ` · median ${plural(f.median_days_to_done, 'day')}`}`}
              to="/follow-ups" />
            <Tile icon={AlertCircle} label="Open for over a week" value={f.overdue.toLocaleString()}
              tone={f.overdue ? 'red' : 'green'} sub={f.overdue ? 'waiting on a case manager' : 'none waiting'} to="/follow-ups" />
          </div>
          <section className="rounded-xl border border-gray-200 bg-white p-5 shadow-sm">
            <h3 className="text-sm font-semibold text-gray-900">How they ended</h3>
            <ul className="mt-3 space-y-2.5">
              {f.outcomes.map((o) => <Bar key={o.key} label={o.label} count={o.count} total={f.done}
                tone={o.key === 'unreachable' ? 'bg-red-600/70' : 'bg-ns-navy/70'} />)}
            </ul>
            {!f.done && <p className="mt-2 text-sm text-gray-500">None finished in this period.</p>}
          </section>
        </div>
      </Section>

      <Section title="Staying in touch" sub="Patients inside their 30 days now, and the weekly check-ins sent from home.">
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
          <Tile icon={Users} label="Being monitored" value={c.monitored_now.toLocaleString()} sub="inside the 30-day window today" />
          <Tile icon={PhoneOff} label="Gone quiet" value={c.overdue_now.toLocaleString()} tone={c.overdue_now ? 'red' : 'green'}
            sub="no readings for over 9 days after warning signs" to="/patients?trend=NeedsAttention" />
          <Tile icon={MessageSquareText} label="Check-ins sent" value={c.checkins.toLocaleString()}
            sub={`from ${plural(c.checkin_patients, 'patient')} · ${c.by_caretaker ? `${c.by_caretaker.toLocaleString()} sent by a caretaker` : 'none sent by a caretaker'}${c.checkins_simulated ? ` · ${c.checkins_simulated.toLocaleString()} simulated for the demo, not counted` : ''}`} />
          <Tile icon={Flag} label="Check-ins with a red flag" value={c.with_red_flags.toLocaleString()}
            tone={c.with_red_flags ? 'amber' : 'gray'} sub="each raised an alert at once" />
        </div>
      </Section>

      {data.mental_health && <MentalHealth m={data.mental_health} days={data.days} />}

      <Section title="Discharge planning" sub={`Patients discharged in the last ${data.days} days.`}>
        <div className="grid gap-4 lg:grid-cols-3">
          <div className="grid gap-4 sm:grid-cols-3 lg:col-span-2">
            <Tile icon={ClipboardCheck} label="Plan complete" value={pct(p.complete, p.discharges)}
              sub={`${p.complete} of ${p.discharges.toLocaleString()} discharges`} />
            <Tile icon={CalendarClock} label="Follow-up booked" value={pct(p.followup_booked, p.discharges)}
              sub="ticked on the discharge plan" />
            <Tile icon={HeartHandshake} label="Social needs asked" value={pct(p.screened, p.discharges)}
              sub={`${p.with_needs} with at least one need`} />
          </div>
          <section className="rounded-xl border border-gray-200 bg-white p-5 shadow-sm">
            <h3 className="text-sm font-semibold text-gray-900">Needs found</h3>
            <p className="text-xs text-gray-500">Share of the patients who were asked</p>
            {p.needs.length ? (
              <ul className="mt-3 space-y-2.5">
                {p.needs.map((n) => <Bar key={n.key} label={n.label} count={n.count} total={p.screened} tone="bg-amber-600/70" />)}
              </ul>
            ) : <p className="mt-2 text-sm text-gray-500">{p.screened ? 'None found.' : 'Not asked for anyone yet.'}</p>}
          </section>
        </div>
      </Section>

      <Readmissions r={r} />

      {data.readmission_detail && <ReadmissionDetail d={data.readmission_detail} />}
    </>
  );
}

/**
 * The mood screens patients answered in their weekly check-ins (models/mood_screen.py).
 * Flag only: nothing here changes a risk score, and a screen is not a diagnosis.
 * The comparison is co-occurrence, not cause.
 */
function MentalHealth({ m, days }) {
  const compare = m.by_screen.positive.known > 0 || m.by_screen.negative.known > 0;
  return (
    <Section title="Mental health"
      sub={`Mood screens patients answered in their weekly check-ins in the last ${days} days. Screens, not diagnoses: they flag, they never change a risk score.`}>
      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <Tile icon={Brain} label="Patients screened" value={m.screened.toLocaleString()} sub={`answered the PHQ-2 at least once${m.simulated ? ` · ${m.simulated} with simulated demo answers` : ''}`} />
        <Tile icon={Flag} label="Screened positive" value={m.positive.toLocaleString()} tone={m.positive ? 'amber' : 'gray'}
          sub={`PHQ-2 of 3 or more · ${pct(m.positive, m.screened)} of those screened`} />
        <Tile icon={AlertCircle} label="Moderate or worse" value={m.moderate_or_worse.toLocaleString()}
          tone={m.moderate_or_worse ? 'amber' : 'gray'} sub="PHQ-8 of 10 or more" />
        <Tile icon={CalendarClock} label="Referred" value={m.referred.toLocaleString()}
          sub="behavioural health follow-ups requested" to="/follow-ups" />
      </div>

      {compare && (
        <div className="grid gap-4 sm:grid-cols-2">
          {[['positive', 'Screened positive'], ['negative', 'Screened negative']].map(([k, label]) => {
            const b = m.by_screen[k];
            return (
              <div key={k} className="rounded-lg border border-gray-100 bg-gray-50 p-4">
                <div className="text-sm font-medium text-gray-700">{label}</div>
                <div className="mt-1 text-2xl font-bold text-gray-900">{rate(b.observed_rate)}</div>
                <div className="text-xs text-gray-600">readmitted · {b.known} with a known outcome · expected {rate(b.expected_rate)}</div>
              </div>
            );
          })}
        </div>
      )}

      <p className="flex gap-2 rounded-lg border border-gray-200 bg-white p-4 text-sm text-gray-700">
        <Info size={16} className="mt-0.5 shrink-0 text-gray-500" />
        <span>
          Every discharge with a known 30-day outcome whose patient answered the mood questions, whatever the period above.
          A difference says low mood and readmission went together in these patients, not that one caused the other. Small
          numbers move a lot: read fewer than about 30 known outcomes as a direction, not a result.
          {m.simulated_compared > 0 && ` ${m.simulated_compared} of the discharges compared have simulated demo answers.`}
        </span>
      </p>
    </Section>
  );
}

function Readmissions({ r }) {
  const o = r.overall;
  const ratioText = (x) => (x === null || x === undefined ? '—' : `${x}×`);
  return (
    <Section title="Readmissions: observed against expected"
      sub="Expected is the average discharge score - the model is calibrated, so a band’s scores are its expected rate. Every discharge with a known 30-day outcome, whatever the period above.">
      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <Tile icon={Flag} label="Outcome known" value={o.known.toLocaleString()}
          sub={`of ${r.past_30_days.toLocaleString()} discharges past their 30 days or already back`} />
        <Tile icon={Target} label="Observed" value={rate(o.observed_rate)} sub={`${o.readmitted} readmitted`}
          tone={o.ratio !== null && o.ratio > 1 ? 'red' : o.ratio !== null ? 'green' : 'gray'} />
        <Tile icon={Target} label="Expected" value={rate(o.expected_rate)} sub="from the discharge scores" />
        <Tile icon={Target} label="Observed ÷ expected" value={ratioText(o.ratio)}
          sub={o.ratio === null ? 'no outcomes yet' : o.ratio < 1 ? 'fewer came back than expected' : 'as many or more than expected'} />
      </div>

      <div className="overflow-x-auto rounded-xl border border-gray-200 bg-white shadow-sm">
        <table className="min-w-full text-sm">
          <thead className="bg-gray-50 text-left text-xs font-semibold uppercase tracking-wide text-gray-600">
            <tr>
              <th scope="col" className="px-4 py-3">Band at discharge</th>
              <th scope="col" className="px-4 py-3 text-right">Outcome known</th>
              <th scope="col" className="px-4 py-3 text-right">Readmitted</th>
              <th scope="col" className="px-4 py-3 text-right">Observed</th>
              <th scope="col" className="px-4 py-3 text-right">Expected</th>
              <th scope="col" className="px-4 py-3 text-right">Observed ÷ expected</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-gray-100">
            {[...r.bands, { band: 'All', ...o }].map((b) => (
              <tr key={b.band} className={b.band === 'All' ? 'font-semibold' : ''}>
                <th scope="row" className="px-4 py-2.5 text-left font-medium text-gray-900">{b.band}</th>
                <td className="px-4 py-2.5 text-right text-gray-800">{b.known.toLocaleString()}</td>
                <td className="px-4 py-2.5 text-right text-gray-800">{b.readmitted.toLocaleString()}</td>
                <td className="px-4 py-2.5 text-right text-gray-800">{rate(b.observed_rate)}</td>
                <td className="px-4 py-2.5 text-right text-gray-800">{rate(b.expected_rate)}</td>
                <td className="px-4 py-2.5 text-right text-gray-800">{ratioText(b.ratio)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {(r.by_checklist.complete.known > 0 || r.by_checklist.incomplete.known > 0) && (
        <div className="grid gap-4 sm:grid-cols-2">
          {[['complete', 'With a complete discharge plan'], ['incomplete', 'With an incomplete plan']].map(([k, label]) => {
            const b = r.by_checklist[k];
            return (
              <div key={k} className="rounded-lg border border-gray-100 bg-gray-50 p-4">
                <div className="text-sm font-medium text-gray-700">{label}</div>
                <div className="mt-1 text-2xl font-bold text-gray-900">{rate(b.observed_rate)}</div>
                <div className="text-xs text-gray-600">readmitted · {b.known} with a known outcome · expected {rate(b.expected_rate)}</div>
              </div>
            );
          })}
        </div>
      )}

      <p className="flex gap-2 rounded-lg border border-gray-200 bg-white p-4 text-sm text-gray-700">
        <Info size={16} className="mt-0.5 shrink-0 text-gray-500" />
        <span>
          Outcomes come from the hospital’s own records ({r.sources.records}) and from the care team ({r.sources.care_team}),
          recorded on each patient’s page. A readmission to another hospital only counts when someone records it, so the
          observed rate can be too low but not too high. Small numbers move a lot: read a band with fewer than about 30 known
          outcomes as a direction, not a result.
        </span>
      </p>
    </Section>
  );
}

/** One group in a "who was readmitted" card: its rate and bar, or why it is not shown. */
function GroupRow({ label, cell }) {
  if (cell.hidden) {
    return (
      <li className="flex justify-between gap-3 text-sm">
        <span className="text-gray-700">{label}</span>
        <span className="text-right text-xs text-gray-500">{HIDDEN[cell.hidden]}</span>
      </li>
    );
  }
  return (
    <li>
      <div className="flex justify-between text-sm"><span className="text-gray-700">{label}</span>
        <span className="font-semibold text-gray-900">{rate(cell.observed_rate)} <span className="font-normal text-gray-500">· {cell.readmitted.toLocaleString()} of {cell.known.toLocaleString()}</span></span></div>
      <div className="mt-1 h-1.5 overflow-hidden rounded-full bg-gray-100"><div className="h-full rounded-full bg-ns-navy/70" style={{ width: `${Math.min(100, Math.round(cell.observed_rate || 0))}%` }} /></div>
    </li>
  );
}

/**
 * Who came back (api/outcomes.py readmission_detail): readmitted patients against
 * those who were not, by who they were, their conditions and their last readings.
 * Descriptive - a difference says two things went together, never that one caused
 * the other. The server withholds every group of fewer than 11 patients; this page
 * only says so.
 */
function ReadmissionDetail({ d }) {
  if (!d.enough) {
    return (
      <Section title="Who comes back" sub="Readmitted patients against those who were not: who they were, their conditions and their last readings.">
        <p className="flex gap-2 rounded-lg border border-gray-200 bg-gray-50 p-4 text-sm text-gray-700">
          <Info size={16} className="mt-0.5 shrink-0 text-gray-500" />
          <span>
            Not enough known outcomes yet: {d.known.toLocaleString()} of the {d.min_group} needed. These comparisons appear once
            at least {d.min_group} discharges have a known 30-day outcome - from the hospital’s records or recorded on each
            patient’s page.
          </span>
        </p>
      </Section>
    );
  }
  const mix = d.conditions;
  const lr = d.last_readings;
  const side = (word, s) => `${word}: ${shown(s.with_readings)} of ${shown(s.known)} had a reading ${word === 'Readmitted' ? 'before they came back' : 'in their 30 days'} (${shown(s.no_readings)} had none).`;
  // The server sends no counts at all when any group in them is fewer than 11.
  const withheld = lr.readmitted.known === null;
  const share = (x) => (x.reported === 0 ? <span className="text-xs text-gray-500">not reported</span>
    : <>{rate(x.share)} <span className="text-xs text-gray-500">· {x.with.toLocaleString()} of {x.reported.toLocaleString()}</span></>);
  return (
    <>
      <Section title="Who was readmitted"
        sub={`Readmission rate by group, against ${rate(d.overall.observed_rate)} for all ${d.overall.known.toLocaleString()} discharges with a known 30-day outcome, whatever the period above.`}>
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {d.who.map((dim) => (
            <section key={dim.key} className="rounded-xl border border-gray-200 bg-white p-5 shadow-sm">
              <h3 className="text-sm font-semibold text-gray-900">{dim.label}</h3>
              <p className="text-xs text-gray-500">Readmitted · of the group with a known outcome</p>
              <ul className="mt-3 space-y-2.5">
                {dim.groups.filter((g) => g.hidden || g.known).map((g) => <GroupRow key={g.key} label={g.label} cell={g} />)}
              </ul>
            </section>
          ))}
        </div>
        <p className="flex gap-2 rounded-lg border border-gray-200 bg-white p-4 text-sm text-gray-700">
          <Info size={16} className="mt-0.5 shrink-0 text-gray-500" />
          <span>
            “Not recorded” is a group of its own, never dropped: insurance and where the patient went come from the hospital’s
            records or the model’s stored inputs. A group of fewer than 11 patients is never shown; when one is hidden, the next
            smallest is hidden too, so it cannot be worked out from the total. A difference says these things went together,
            not that one caused the other.
          </span>
        </p>
      </Section>

      <Section title="Disease mix" sub="Readmission rate with and without each condition. A patient with several conditions counts under each.">
        {mix.known === null ? (
          <p className="flex gap-2 rounded-lg border border-gray-200 bg-gray-50 p-4 text-sm text-gray-700">
            <Info size={16} className="mt-0.5 shrink-0 text-gray-500" />
            <span>Hidden: fewer than 11 discharges have, or lack, diagnoses on record, so any comparison could be used to pick them out.</span>
          </p>
        ) : mix.conditions.length ? (
          <div className="overflow-x-auto rounded-xl border border-gray-200 bg-white shadow-sm">
            <table className="min-w-full text-sm">
              <thead className="bg-gray-50 text-left text-xs font-semibold uppercase tracking-wide text-gray-600">
                <tr>
                  <th scope="col" className="px-4 py-3">Condition</th>
                  <th scope="col" className="px-4 py-3 text-right">With it</th>
                  <th scope="col" className="px-4 py-3 text-right">Without it</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-100">
                {mix.conditions.map((c) => (
                  <tr key={c.key}>
                    <th scope="row" className="px-4 py-2.5 text-left font-medium text-gray-900">{c.label}</th>
                    {c.hidden ? (
                      <td colSpan={2} className="px-4 py-2.5 text-right text-xs text-gray-500">Fewer than 11 patients with or without it - hidden</td>
                    ) : [c.with, c.without].map((x, i) => (
                      <td key={i} className="px-4 py-2.5 text-right text-gray-800">
                        {rate(x.observed_rate)} <span className="text-xs text-gray-500">· {x.readmitted.toLocaleString()} of {x.known.toLocaleString()}</span>
                      </td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : <p className="text-sm text-gray-600">No condition matched these patients’ diagnoses.</p>}
        {mix.known !== null && mix.unknown !== 0 && (
          <p className="text-sm text-gray-600">{shown(mix.unknown)} discharges with no diagnoses on record are left out.</p>
        )}
      </Section>

      <Section title="Readings before readmission"
        sub="Each warning sign at the patient’s last weekly reading: before the day they came back, or within their 30 days for those who did not.">
        {!withheld && (
          <p className="text-sm text-gray-700">
            {side('Readmitted', lr.readmitted)} {side('Not readmitted', lr.not_readmitted)}
          </p>
        )}
        {withheld ? (
          <p className="flex gap-2 rounded-lg border border-gray-200 bg-gray-50 p-4 text-sm text-gray-700">
            <Info size={16} className="mt-0.5 shrink-0 text-gray-500" />
            <span>Hidden: fewer than 11 patients are in one of the groups (with or without a reading, or readmitted with no date), so showing the rest could be used to pick them out.</span>
          </p>
        ) : lr.hidden ? (
          <p className="flex gap-2 rounded-lg border border-gray-200 bg-gray-50 p-4 text-sm text-gray-700">
            <Info size={16} className="mt-0.5 shrink-0 text-gray-500" />
            <span>Not enough patients with readings to compare yet: at least {d.min_group} are needed in each group.</span>
          </p>
        ) : (
          <div className="overflow-x-auto rounded-xl border border-gray-200 bg-white shadow-sm">
            <table className="min-w-full text-sm">
              <thead className="bg-gray-50 text-left text-xs font-semibold uppercase tracking-wide text-gray-600">
                <tr>
                  <th scope="col" className="px-4 py-3">Warning sign</th>
                  <th scope="col" className="px-4 py-3 text-right">Readmitted</th>
                  <th scope="col" className="px-4 py-3 text-right">Not readmitted</th>
                  <th scope="col" className="px-4 py-3 text-right">Readmitted ÷ not</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-100">
                {lr.signs.map((x) => (
                  <tr key={x.key}>
                    <th scope="row" className="px-4 py-2.5 text-left font-medium text-gray-900">
                      {x.label}
                      <div className="text-xs font-normal text-gray-500">{x.detail}</div>
                    </th>
                    {x.hidden ? (
                      <td colSpan={3} className="px-4 py-2.5 text-right text-xs text-gray-500">A group of fewer than 11 - hidden</td>
                    ) : (
                      <>
                        <td className="px-4 py-2.5 text-right text-gray-800">{share(x.readmitted)}</td>
                        <td className="px-4 py-2.5 text-right text-gray-800">{share(x.not_readmitted)}</td>
                        <td className="px-4 py-2.5 text-right text-gray-800">
                          {x.ratio !== null ? `${x.ratio}×` : x.readmitted.share > 0 && x.not_readmitted.share === 0 ? 'only in readmitted' : '—'}
                        </td>
                      </>
                    )}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        {!withheld && lr.undated !== 0 && (
          <p className="text-sm text-gray-600">{shown(lr.undated)} readmissions with no date are left out: their readings cannot be split into before and after.</p>
        )}
        {!withheld && lr.simulated !== 0 && (
          <p className="text-sm text-gray-600">
            {lr.simulated === null ? 'Some' : lr.simulated.toLocaleString()} of these last readings are simulated demo data, not readings from patients.
          </p>
        )}
        {!withheld && !lr.hidden && (
          <p className="flex gap-2 rounded-lg border border-gray-200 bg-white p-4 text-sm text-gray-700">
            <Info size={16} className="mt-0.5 shrink-0 text-gray-500" />
            <span>
              Sorted by how much more common each sign was among readmitted patients. Each share is of the patients whose last
              reading reported that sign - a blank is not counted as a no. A sign that was more common before readmission went
              together with coming back in these patients; it does not show that it caused it. Small numbers move a lot: read
              fewer than about 30 in a group as a direction, not a result.
            </span>
          </p>
        )}
      </Section>
    </>
  );
}

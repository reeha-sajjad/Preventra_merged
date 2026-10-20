# Preventra Readmission Risk — demo script v2

**Runtime:** ~4:21 (261s) · **VO word count:** 589 · **Read rate:** 139 wpm (measured from the
GLP-1 narration, so the two films pace alike)
**Format:** 1920×1080, 16:9 · **Narrator:** same voice as the GLP-1 film — female, neutral
American, warm and credible
**Status:** v2. Supersedes `demo_voiceover_script_readmission.md`. Three claim fixes below.

---

## What changed from v1, and why

| v1 said | Problem | v2 says |
|---|---|---|
| "weight from a home device, medication adherence from the patient's app, refills from the pharmacy, and visits to the clinic" | `WeeklyTrendPanel.jsx:26` — *"The feed names are placeholders — no integration exists yet."* Data comes from `scripts/simulate_weekly_monitoring.py`. Presented to payers as live feeds, this is a misrepresentation. | Same four sources, framed as what the layer is **designed to combine**, with "the data here is simulated" said out loud. |
| Teaches "sixty percent means six in ten" at discharge, then re-scores weekly with no caveat | The viewer carries a calibrated reading into numbers that are not calibrated. `PatientTrend.jsx:122` prints the opposite on screen: *"read this figure as directional rather than as a calibrated expected value."* The video would claim more than the app does. | Keeps the discharge claim (calibration slope 1.010 — it is sound) and adds one sentence carrying the UI's own caveat into the weekly scene. |
| "so nothing is missed" · "Fewer readmissions, better recoveries" | Absolute and guaranteed-outcome claims — the class the GLP-1 brief bans. | "every alert has an owner, and every decision is on the record" · a closing line that promises visibility, not outcomes. |

Everything else in v1 verified clean. Every UI string is verbatim, and the role-visibility
claims in scene 2 match `api/access.py` exactly.

---

## Voiceover only — for TTS / narrator

> Paste this block and nothing else. Numbers are already spelled out. Check that "Preventra"
> comes back as pre-VEN-tra on the first generation — it is said six times.

Every year, millions of patients are readmitted within thirty days of going home. Many of those readmissions are preventable — if the care team knows who is at risk, and why, while there is still time to act.

This is Preventra. One secure sign-in gives each person the products their role allows. Here, we open Readmission Risk.

The overview shows the hospital's whole discharged population at a glance: how many patients sit in the high, medium and low risk bands right now, who pays for their care, and how many need attention this week.

Access is role-based from the first click. Doctors and nurses see the patients assigned to them. Case managers see their hospital. Insurers see their own members, in any hospital.

The patient worklist is where the day starts. Every discharged patient is ranked by their current risk of readmission, and by how much that risk has moved since discharge.

One click narrows the list to the patients who need attention — those whose risk is rising, or who need action now. Filter by condition, export the list, and assign a doctor and nurses to each patient, so every high-risk patient has an owner.

Opening a patient shows their readmission risk. At discharge, that score comes from a calibrated machine learning model — so sixty percent means roughly six in ten similar patients were readmitted.

But a number alone is not enough to act on. Preventra shows the primary drivers behind each score, each in plain language: a short gap since the last admission, a history of repeat admissions, an abnormal lab result before discharge. And it suggests clinical actions the team can consider.

Risk does not stop changing when the patient leaves. Preventra re-scores every patient every seven days across the thirty-day window after discharge.

The weekly layer is designed to combine the signals that matter for that patient's condition — weight from a home device, medication adherence from a patient app, refills from the pharmacy, visits to the clinic. Those feeds are illustrative, and the data here is simulated. Every reading shows its source: a pharmacy record and a self-reported number are not the same kind of evidence.

A weekly score is the discharge probability adjusted by the monitoring rules — directional, not a calibrated expected value.

When something needs a clinician today, a red flag appears at the top of the week. One click explains it in plain language: what changed, why it matters, and what to consider next.

When a patient's risk rises, the right people hear about it. The alerts bell tells each doctor and nurse about their own patients. The clinician console routes each alert to the responsible clinician, who records their response — so every alert has an owner, and every decision is on the record.

Finally, the Preventra assistant lets anyone on the team ask questions in plain English.

Start from a common question, or type your own. Every answer is built from your hospital's live data — and only from the patients you are allowed to see.

Answers are interactive. Click a patient to open their record. Click a condition, and the worklist filters to those patients instantly. For longer sessions, the assistant expands to full screen.

From sign-in to the patient who needs a call today, Preventra turns discharge data into action: who is at risk, why, how that is changing week by week, and what the team can do about it. The clinical decision stays where it belongs — with the care team.

Preventra Readmission Risk. From discharge data to decisions made in time.

---

## Shot list

Timings assume 139 wpm plus a one-second beat between scenes. Cut picture to the recorded
voiceover, not to this table.

### 1 · 0:00–0:24 · Sign in — 56 words
**On screen:** The Preventra portal. Type email and password, sign in. The "Choose an
application" screen appears; click **Readmission Risk**.
**Capture:** Use a prepared account with the password already in a manager — nobody wants to
watch typing. Blur or swap the email if it is a personal one.

### 2 · 0:25–0:54 · Overview dashboard — 66 words
**On screen:** The Overview page: risk bands, insurer mix, needs-attention counts. Hover the
risk band chart once, slowly.
**On screen (verbatim):** `Needs attention`
**Capture:** The role claim is spoken here, so the sidebar should show a **Case Manager** role
badge, not Superadmin. Signing in as the role you are describing is worth the extra setup.

### 3 · 0:55–1:26 · Patient worklist — 72 words
**On screen:** Patients page. Click **Needs Attention**, then pick a condition (Heart failure)
in the condition filter. Show the trend arrows and the care team column. Briefly show
**Export CSV**.
**On screen (verbatim):** `Needs Attention` · `Export CSV` · `Monitored as`
**Capture:** Pick the demo patient for scenes 4–6 now and make sure they are visible in this
filtered list, so the next cut is a click, not a search.

### 4 · 1:27–2:01 · Risk score and drivers — 79 words
**On screen:** Open the patient. Show the current risk score and band, then scroll to
**Primary Drivers**, **Score History** and **Suggested Clinical Actions**.
**On screen (verbatim):** `Primary Drivers` · `Score History` · `Suggested Clinical Actions`
**Capture:** The score on screen must be near sixty percent, or the "six in ten" line
contradicts the frame. Choose the patient for that before recording.

### 5 · 2:02–3:01 · Weekly post-discharge monitoring — 136 words
**On screen:** Open the full weekly trend. Show the chart, the "Monitored as" card, the
recommended action banner, then a week card with a red flag, its drivers and explanations, and
the data sources. Click **Explain this week**.
**On screen (verbatim):** `Monitored as` · `Explain this week`
**Capture:** The longest scene, and the one carrying both corrections. Hold on the per-reading
source icons while the narration says "every reading shows its source" — the point only lands
if the viewer can see the icons. Keep the italic caveat line at the bottom of the trend page
**in frame** when the directional sentence is read; it is the product agreeing with the
narrator on camera.

### 6 · 3:02–3:23 · Alerts and the clinician console — 50 words
**On screen:** Click the alerts bell. Then open the **Clinician Console** and respond to an
alert.
**On screen (verbatim):** `Your patients' risk went up` · `Clinician Console`
**Capture:** Actually record the response being typed and saved. "Every decision is on the
record" is a claim the frame should prove.

### 7 · 3:24–3:55 · The AI assistant — 72 words
**On screen:** Open the chat. Show the common questions, click **Who are the 5 highest-risk
patients right now?** Click a patient ID in the answer to open that patient. Expand to full
screen, ask **What are the most common conditions among high-risk patients?**, and click
**Heart failure** in the answer to filter the worklist.
**On screen (verbatim):** `Common questions` · `Who are the 5 highest-risk patients right now?`
**Capture:** Pre-run both questions once so the responses are warm. If a generation is slow on
camera, cut the dead air — do not speed-ramp the cursor.

### 8 · 3:56–4:21 · Close — 58 words
**On screen:** Back to the Overview, or the Analytics page showing model performance.
**Capture:** If you use the Analytics page, the metrics on it are legible and on the record —
make sure they are numbers you are willing to publish.

---

## Claim compliance

| Must not say | What the script says instead |
|---|---|
| Guarantees fewer readmissions or better outcomes | "turns discharge data into action", "From discharge data to decisions made in time" |
| Nothing is missed / nothing slips through | "every alert has an owner, and every decision is on the record" |
| Preventra decides clinical treatment | "suggests clinical actions the team can consider"; "The clinical decision stays where it belongs — with the care team" |
| Live integrations with devices, pharmacies or patient apps | "designed to combine"; "Those feeds are illustrative, and the data here is simulated" |
| A weekly score is a calibrated probability | "directional, not a calibrated expected value" |

The discharge-score claim ("sixty percent means roughly six in ten") **is** supportable —
calibration slope 1.010 on MIMIC. It is scoped to discharge on purpose. Do not let a rewrite
move it.

Scene 8's "The clinical decision stays where it belongs — with the care team" is the
decision-support disclaimer. Load-bearing; do not cut it for time.

## Production notes

- Record the VO first, then cut picture to it — same as the GLP-1 film.
- Use the **same narrator** as the GLP-1 video. Two films in one voice read as one company.
- `F11` fullscreen, cursor capture off, 1920×1080 at 60 fps, CQP 16. The browser tab bar and
  the Windows taskbar must not be in frame.
- Patient IDs are never read aloud; the narration says "this patient" throughout.
- If you need this under four minutes, cut these two sentences and nothing else: the
  "pharmacy record and a self-reported number" line in scene 5 (−20s with its beat) and
  "For longer sessions, the assistant expands to full screen" in scene 7 (−4s).

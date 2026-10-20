# Preventra Readmission Risk — master script v1

**Runtime:** ~1:35 (95s) · **VO word count:** 202 · **Read rate:** 139 wpm
**Format:** 1920×1080, 16:9 · **Narrator:** the same voice as the GLP-1 film
**Status:** draft for approval. Companion piece to `GLP1/docs/GLP1_Video_Master_Script_v1.md`.

A 94-second narrative ad, built to sit beside the GLP-1 film: same length, same nine-beat
shape, same closing structure. Ray is to this film what Jen is to that one. Scene 4's pivot
line is deliberately the same sentence in both — it is the device that makes them a pair.

This is **not** the demo script. The four-minute click-by-click walkthrough is
`Readmissions_Demo_Script_v2.md` and still stands; the two do different jobs.

---

## Voiceover only — for TTS / narrator

> Paste this block and nothing else. Numbers are spelled out. Same narrator as the GLP-1 film.

Ray is sixty-eight. Today he goes home after nine days in hospital for heart failure. A discharge plan, new medications, a follow-up in two weeks.

But the thirty days that decide whether he comes back don't happen in the hospital. They happen at home, where nobody is watching.

Two pounds, then four, on the bathroom scale. A pill he skips because it makes him dizzy. A follow-up he reschedules, then forgets.

None of it reaches his care team. Not yet.

Preventra re-scores Ray every seven days across the thirty-day window — and flags the weeks where his risk of readmission is rising, while there is still time to act.

And it shows what's driving it — the weight gain, the missed doses, the follow-up that never happened — each reading showing its source, so the team knows what to address, not just who to call.

So the call happens on day eleven, while it is still a phone call. The decision stays where it belongs — with Ray's care team.

And the same intelligence scales. Across a discharged population, Preventra gives hospitals and payers visibility into who is rising, why, and where to focus first.

Preventra Readmission Risk. From discharge data to decisions made in time.

---

## Shot list

### 1 · 0:00–0:11 · Discharge — 25 words
**VO:** Ray is sixty-eight. Today he goes home after nine days in hospital for heart failure. A discharge plan, new medications, a follow-up in two weeks.
**On screen:** none
**Shot:** Ray leaving a ward — wheelchair at the doors, a bag of medications, a daughter carrying it. Natural light. Establish his face clearly; this is the reference frame for every later scene.

### 2 · 0:12–0:22 · Home — 23 words
**VO:** But the thirty days that decide whether he comes back don't happen in the hospital. They happen at home, where nobody is watching.
**On screen:** none
**Shot:** The front door closing behind him. A quiet living room. Hold — this is the line the whole film turns on.

### 3 · 0:22–0:33 · The signals nobody sees — 23 words
**VO:** Two pounds, then four, on the bathroom scale. A pill he skips because it makes him dizzy. A follow-up he reschedules, then forgets.
**On screen:** none — do not caption these
**Shot:** Three beats, one per sentence, ~3.5s each. Bathroom scale readout. A pill organiser with one compartment still full. A phone screen with a clinic callback dismissed. Cut on the sentence, not the music.

### 4 · 0:34–0:40 · The gap — 9 words
**VO:** None of it reaches his care team. Not yet.
**On screen:** none
**Shot:** A dark monitor at an empty nurses' station. Hold. **Two full seconds of silence after "Not yet."** — this beat is identical to scene 4 of the GLP-1 film on purpose.

### 5 · 0:41–0:53 · Preventra re-scores — 28 words
**VO:** Preventra re-scores Ray every seven days across the thirty-day window — and flags the weeks where his risk of readmission is rising, while there is still time to act.
**On screen (verbatim):**
```
Monitored as  Heart failure
Discharged:
weeks monitored
```
**Shot:** First product reveal. **Screen-record the real app.** The weekly trend chart with the line climbing. Slow keyframed push into the flagged week. Do not put a large weekly percentage on screen — see the claim note below.

### 6 · 0:53–1:08 · The drivers — 34 words
**VO:** And it shows what's driving it — the weight gain, the missed doses, the follow-up that never happened — each reading showing its source, so the team knows what to address, not just who to call.
**On screen (verbatim):**
```
Primary Drivers
Explain this week
```
**Shot:** The week card expanded: its drivers, its explanations, and the per-reading source icons. Reveal each driver on its matching phrase. Hold on the source icons for "each reading showing its source" — the point only lands if the icons are legible.

### 7 · 1:08–1:19 · The call — 24 words
**VO:** So the call happens on day eleven, while it is still a phone call. The decision stays where it belongs — with Ray's care team.
**On screen (verbatim):**
```
Your patients' risk went up
```
**Shot:** A coordinator at the alerts bell, then on the phone; cut to Ray answering at home. **End on Ray, not the screen** — the second sentence must play over a human face.

### 8 · 1:19–1:30 · Population and payer — 25 words
**VO:** And the same intelligence scales. Across a discharged population, Preventra gives hospitals and payers visibility into who is rising, why, and where to focus first.
**On screen (verbatim):**
```
Needs Attention
```
**Shot:** Pull back from Ray's single record into the worklist and the Overview risk bands — one continuous zoom-out if the edit allows, so "scales" is shown rather than stated.

### 9 · 1:30–1:35 · Close — 11 words
**VO:** Preventra Readmission Risk. From discharge data to decisions made in time.
**On screen:** Preventra logo · preventra.health
**Shot:** Dissolve to logo on white. Music tails out to ~1:38.

---

## Claim compliance

| Must not say | What the script says instead |
|---|---|
| Guarantees fewer readmissions or better outcomes | "while there is still time to act"; "while it is still a phone call" |
| Preventra decides clinical treatment | "The decision stays where it belongs — with Ray's care team" |
| The outreach prevents the readmission | "the call happens on day eleven, while it is still a phone call" — the intervention is possible, not proven effective |
| A weekly score is a calibrated probability | no weekly percentage is spoken, and none should be held large on screen |
| Live device, pharmacy or patient-app integrations | scene 3 is Ray's lived experience, not a feed; scene 6 says only that each reading shows its source, which the UI does |

Scene 7's second sentence is the decision-support disclaimer. Load-bearing — do not cut it
for time.

**Two production-level compliance items.**

1. **No large weekly percentage on screen.** A weekly score is the discharge probability
   adjusted by the monitoring rules, and the app itself labels it directional rather than a
   calibrated expected value (`frontend/src/pages/PatientTrend.jsx:122`). The film never says a
   weekly number; do not let the edit hold one in close-up either. The *discharge* score is
   calibrated and safe to show.
2. **Add a persistent super: "Illustrative data. Not a real patient."** The weekly feeds are
   simulated (`scripts/simulate_weekly_monitoring.py`; the feed names are placeholders — see
   the comment at `WeeklyTrendPanel.jsx:26`). One small corner line across the product scenes
   covers it. **The GLP-1 film needs the same super** — Jen T. is synthetic too.

## Before this can be shot

The detail page titles patients by raw ID (`<h1>{patient.id}</h1>`,
`frontend/src/pages/PatientDetail.jsx:172`), so "Ray D." will not appear on screen without the
same display layer GLP-1 got in `GLP1/Frontend/src/demo/presentation.js` — a `?demo=1` mapping
of id → name, plus a seeded record. Budget the same small piece of work, and seed Ray with a
heart-failure group label, a climbing weekly trend, and a flagged week around day eleven so the
footage matches the script.

## Production notes

- Record the VO first, then cut picture to it.
- Same narrator as the GLP-1 film. Two films in one voice read as one company.
- Ray is the same man in all nine scenes. Any shot where his face drifts gets replaced.
- 15-second teaser: scenes 4 → 5 → 6 → 9, with a shorter VO re-read.

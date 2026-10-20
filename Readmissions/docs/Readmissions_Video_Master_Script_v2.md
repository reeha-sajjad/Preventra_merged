# Preventra Readmission Risk — master script v2
## "What the discharge summary doesn't say"

**Runtime:** ~1:35 (95s) · **VO word count:** 200 · **Read rate:** 139 wpm
**Format:** 1920×1080, 16:9 · **Narrator:** the same voice as the GLP-1 film
**Status:** draft for approval. Replaces the Ray patient-journey draft (v1), which mirrored the
GLP-1 film too closely to stand beside it.

The film is built on one image: a discharge summary is a complete record of everything that
already happened, and the back of it is blank. The thirty days that decide whether the patient
returns are not on the page. Preventra writes that page.

**Why this concept and not another patient journey.** The GLP-1 film is a patient-journey film.
Repeating the form with a different patient would read as a retread. This one is led by paper,
type and light rather than by a face — so the two films share a voice and a palette while
looking nothing alike. It also means no AI-generated humans, which is what went wrong in the
GLP-1 capture.

---

## Where text is allowed to come from

**The problem this solves.** AI video generators cannot render legible text. Every word they
produce comes out as plausible-looking gibberish, and a healthcare film whose subject is a
*document* will fail on exactly that. The original scene 05 asked the generator to write words
onto paper, which it will never do correctly.

The fix is a hard rule, not better prompting. Three sources of imagery, and **words may only
come from two of them**:

| Source | Used for | Text? |
|---|---|---|
| **AI generation** | people, hands, rooms, paper, light, texture, motion | **Never.** Not one word, number, dial reading or label. |
| **Screen recording** | every product scene, 05 onward | Yes — it is the real app, so the text is real |
| **Motion graphics** | the ink line, titles, the logo, the super | Yes — real typography you control |

Any frame that needs a word is either a screen recording or built in After Effects. If a
generated shot contains text you did not put there — a dial reading, a label, a sign, a monitor
in the background — **that shot is rejected, not graded around.** This is the same rule the
GLP-1 brief sets for AI-generated monitors.

Scene 05 is rebuilt below so the hero shot needs **no typography at all**.

## Voiceover only — for TTS / narrator

> Paste this block and nothing else. Numbers are spelled out. Same narrator as the GLP-1 film.

A discharge summary is a complete document. The admission. The procedure. The medications. The follow-up date. Everything that happened to this patient is on this page, and all of it is true.

Then someone turns it over. And the thirty days that actually decide whether they come back — those are blank.

Not unknown. Not unknowable. Just unwritten — because nobody was watching the week the weight started climbing, or the week the pills stopped.

Preventra writes that page.

Every seven days across the thirty-day window, it scores the patient again — and marks the weeks where the risk of readmission is rising, while there is still time to act.

And it writes down why. The weight gain. The missed doses. The follow-up that never happened. Each line showing where it came from — so the team reads a reason, not just a number.

Which is how a page becomes a phone call on day eleven. The decision stays where it belongs — with the care team.

And it is written for every patient who went home this month. Across a discharged population, hospitals and payers see who is rising, why, and where to look first.

Preventra Readmission Risk. The page after the last page.

---

## Shot list

### 1 · 0:00–0:14 · The document — 32 words
**VO:** A discharge summary is a complete document. The admission. The procedure. The medications. The follow-up date. Everything that happened to this patient is on this page, and all of it is true.
**On screen:** none
**Shot:** Extreme macro, raking light, paper fibre visible. Four slow pushes — one per sentence fragment — but **each lands on a structural feature, never on a word**: the staple in the corner; a printed horizontal rule; a signature line with a single hand-written stroke across it; the folded edge catching light. These read unmistakably as an official document without the generator ever attempting a letter.
If you want section headings visible, **add them in post as real typography** over a clean plate. Never ask the generator for them.
**Human beat (~1.5s):** on "to this patient", cut to her — a woman in her late sixties in a discharge chair by a window, mid-shot, slightly soft, held just long enough to register a face. Then straight back to the paper. She is the only patient in the film, and we meet her through the document rather than before it.

### 2 · 0:15–0:23 · The blank side — 19 words
**VO:** Then someone turns it over. And the thirty days that actually decide whether they come back — those are blank.
**On screen:** none
**Shot:** A hand turns the sheet. Hold on the empty reverse, filling the frame. This is the thesis image of the film — give it room and do not cut early.
**This is the safest shot in the film.** A blank page is the one thing a generator renders perfectly, because there is nothing to render. Expect this scene to work first time.
**Human beat:** widen for a moment before the turn — the hands belong to a care coordinator at a desk late in the day, lamp on, a quiet ward behind her. No face yet. Then push back down into the blank page.

### 3 · 0:23–0:33 · Unwritten — 22 words
**VO:** Not unknown. Not unknowable. Just unwritten — because nobody was watching the week the weight started climbing, or the week the pills stopped.
**On screen:** none
**Shot:** Same macro language, with the person inside it — and **every numeral and label removed from frame**, because all three of these objects normally carry text:
- **The scale.** Bare feet stepping on, framed at the ankles. The dial is never in shot, or is seen edge-on and out of focus. The weight is carried by the feet and the pause, not by a number.
- **The pills.** A plain unlabelled organiser or an amber bottle turned away from camera. A hand hesitates over a full compartment and closes the lid. No day letters, no prescription label.
- **The phone.** Face-down on a table, or screen-side but filled with window glare. The thumb gesture carries the meaning; the screen shows nothing readable.

Hands and feet only, no face. These are the three moments nobody wrote down — and **the film returns to all three in scene 06**, so shoot them long, to be reused.

### 4 · 0:34–0:36 · The turn — 4 words
**VO:** Preventra writes that page.
**On screen:** none
**Shot:** Nothing moves. Hold the blank page. **Two full seconds of silence after the line** before anything appears. This is the pivot — the shortest beat in the film and the one everything rests on.
**No human here.** The hold has to stay abstract; a face would soften the turn.

### 5 · 0:38–0:51 · The page fills — 30 words
**VO:** Every seven days across the thirty-day window, it scores the patient again — and marks the weeks where the risk of readmission is rising, while there is still time to act.
**On screen (verbatim):**
```
Monitored as  Heart failure
weeks monitored
```
**Shot:** **The hero shot, rebuilt so it contains no text whatsoever.**

The narration says the product *marks* the weeks. A mark is not a word. So nothing writes
itself onto the page — **a single ink line draws itself across it**, left to right, climbing.
Four small tick marks drop beneath it as it passes, one per week. That is the entire graphic:
a line and four ticks. No letters, no numbers, no dates.

Then the line keeps going. The paper dissolves, the grain becomes the chart background, and
**the ink line becomes the trend line in the app** — continuous, unbroken across the cut. The
document does not just become the product; the mark the document was missing turns out to be
the thing the product draws.

**Build this in After Effects over a still plate of blank paper. Do not send it through the
generator.** From the dissolve onward it is a **screen recording of the real app**, so every
word from here to scene 08 is real text.

To make the dissolve land, draw the ink line to **match the actual shape of the seeded
patient's trend curve**. Screen-record the chart first, trace its path, then animate the ink
line to it.
**No human in this beat** — the dissolve has to read cleanly, and a cutaway breaks it.

### 6 · 0:51–1:06 · The reasons — 33 words
**VO:** And it writes down why. The weight gain. The missed doses. The follow-up that never happened. Each line showing where it came from — so the team reads a reason, not just a number.
**On screen (verbatim):**
```
Primary Drivers
Explain this week
```
**Shot:** The flagged week expanded — drivers, explanations, and the per-reading source icons. Reveal each driver on its matching phrase. Hold on the source icons for "each line showing where it came from"; the point only lands if they are legible.
**All of this is screen recording, so the text is real.** Never let a generator produce a Preventra screen — the words on it are the most scrutinised frames in the film.
**Human callback — the payoff of the film.** As the narrator names each driver, flash the matching shot from scene 03: "the weight gain" to the feet on the scale, "the missed doses" to the hand on the organiser, "the follow-up that never happened" to the thumb on the phone. About 0.4s each, cutting back to the panel between. Same footage, no extra shoot day. You watched these moments go unrecorded; now the product names them back to you.

### 7 · 1:06–1:16 · The call — 22 words
**VO:** Which is how a page becomes a phone call on day eleven. The decision stays where it belongs — with the care team.
**On screen (verbatim):**
```
Your patients' risk went up
```
**Shot:** **The human scene.** The coordinator from scene 02, now seen fully — she reads the alert, picks up the phone, dials. Cut to the patient from scene 01 answering at home. **The second sentence plays over her face, not a screen.** Two faces, ten seconds; the only sustained human presence in the film, which is exactly why it lands.

### 8 · 1:16–1:29 · Population and payer — 29 words
**VO:** And it is written for every patient who went home this month. Across a discharged population, hospitals and payers see who is rising, why, and where to look first.
**On screen (verbatim):**
```
Needs Attention
```
**Shot:** Pull back from the one record into the worklist, then the Overview risk bands. One continuous zoom-out if the edit allows. Optionally: the single sheet of paper becomes a stack, then a wall of them, before cutting to the cohort view.
**Human beat (~2s):** a care-management room — three or four people at screens, phones in use. Scale is people doing this work, not only data growing.
**Throw the background monitors out of focus.** A generated room will fill every screen with garbled text; at a wide aperture they become soft glows and the problem disappears. If a monitor is sharp enough to read, reject the shot.

### 9 · 1:29–1:33 · Close — 9 words
**VO:** Preventra Readmission Risk. The page after the last page.
**On screen:** Preventra logo · preventra.health
**Shot:** Return to the blank page, now written. Dissolve to logo on white. Music tails to ~1:35.

---

## Claim compliance

| Must not say | What the script says instead |
|---|---|
| Guarantees fewer readmissions or better outcomes | "while there is still time to act"; "a page becomes a phone call" |
| Preventra decides clinical treatment | "The decision stays where it belongs — with the care team" |
| The outreach prevents the readmission | the film stops at the call being made and never shows an outcome |
| A weekly score is a calibrated probability | no weekly percentage is spoken, and none should be held large on screen |
| Live device, pharmacy or patient-app integrations | scene 3 describes what went unobserved; scene 6 says only that each line shows its source, which the UI does |

Scene 7's second sentence is the decision-support disclaimer. Load-bearing — do not cut it
for time.

**Three production-level compliance items.**

1. **The discharge summary must never be legible.** This now happens for free: under the text
   rule above, the document never shows a word at all. Do not build a realistic filled-in
   medical record as a prop, and never use a real document from any hospital. The compliance
   requirement and the AI-text limitation point the same way, which is why scene 01 lands on
   staples, rules and folds rather than on headings.
2. **No large weekly percentage on screen.** A weekly score is the discharge probability
   adjusted by the monitoring rules, and the app labels it directional rather than a calibrated
   expected value (`frontend/src/pages/PatientTrend.jsx:122`). The film never says a weekly
   number; don't let the edit hold one in close-up. The *discharge* score is calibrated and
   safe to show.
3. **Persistent super: "Illustrative data. Not a real patient."** The weekly feeds are
   simulated (`scripts/simulate_weekly_monitoring.py`; the feed names are placeholders — see
   the comment at `WeeklyTrendPanel.jsx:26`). **The GLP-1 film needs the same super.**

## Before this can be shot

Scenes 5 to 8 are the real app, so the capture needs a seeded record: a heart-failure group
label, a climbing weekly trend, and a flagged week around day eleven. Unlike the v1 draft, this
film **never names the patient**, so the `?demo=1` name-mapping layer GLP-1 needed is not
required here — the record can stay a bare ID, since it is never on camera in close-up and
never spoken. That removes about an hour of work from the plan.

## The human shots, and why they sit where they do

The paper stays the spine. People enter as fragments, get named by the product, and only then
resolve into faces — so the film warms up without becoming the patient-journey film this
concept replaced.

| Scene | Human presence |
|---|---|
| 01 | The patient, ~1.5s, seen once and soft. We meet her through the document. |
| 02 | The coordinator's hands only. No face. |
| 03 | Feet on a scale, a hand on a pill organiser, a thumb on a phone. No face. |
| 04 and 05 | **None.** The pivot and the dissolve must stay clean. |
| 06 | The scene-03 fragments flashed back as each driver is named. Reused footage. |
| 07 | Both faces, in full. The only sustained human beat. |
| 08 | A care-management room, ~2s. |
| 09 | None. Back to paper. |

**Casting is two people, both practical.** The patient appears in 01 and 07 and must be the
same person in both; the coordinator appears in 02 (hands), 07 (face) and 08. All of it is a
half-day shoot with real people — there is still **no AI-generated human anywhere in the
film**, which is the advantage this concept had over the patient-journey draft and the one
thing not to trade away.

Scene 06 is the structural reason this works. Because the fragments in 03 are shot to be
reused, adding people costs one extra setup rather than a second shoot — and it turns scene
06 from a UI panel into the emotional payoff of the film.

## Production notes

- Record the VO first, then cut picture to it.
- Same narrator as the GLP-1 film. Two films in one voice read as one company.
- If you can shoot practically — a sheet of paper, a desk, one lamp, a macro lens — the whole
  text problem disappears, because real printed words photograph correctly. That remains the
  best option for scenes 1 to 4. The rule above exists for the shots you do generate.
- **Reject, do not grade.** A generated shot with gibberish text cannot be fixed in colour or
  blur without looking fixed. Re-prompt it with the text element removed from the scene.
- Shoot the scene-03 fragments long. They are used twice, and the second use (scene 06) needs
  clean 0.4-second cuts, which is far easier from generous takes.
- The paper and the UI must share a colour temperature. Grade the macro footage toward the
  app's whites, or the dissolve in scene 5 will break.
- `F11` fullscreen, cursor capture off, 1920×1080 at 60 fps, CQP 16 for every product scene.
- 15-second teaser: scenes 2 → 4 → 5 → 9, with a shorter VO re-read.

## If you want the closing line to pair with the GLP-1 film

The GLP-1 film closes *"From GLP-1 utilization to treatment intelligence."* This one closes
*"The page after the last page,"* which is earned by its own concept and is the stronger line.
If brand consistency matters more than the image, substitute *"From discharge data to decisions
made in time"* — same length, same beat, keeps the From/To structure across both films.

# Gemini / Veo prompt sheet — "What the discharge summary doesn't say"

Companion to `Readmissions_Video_Master_Script_v2.md`. **One prompt per scene** — six scene
prompts, plus two reference stills to make first. Everything else in the film is a screen
recording or After Effects.

## Do not generate these

| Scene | Why not | Make it how |
|---|---|---|
| 05 — the ink line | A drawn graphic that must match the real chart curve | After Effects over a blank-paper plate |
| 05–06 — every Preventra screen | A generated UI is gibberish, and these are the most scrutinised frames in the film | Screen recording of the real app |
| 08 — the worklist and risk bands | Same | Screen recording |
| 09 — logo and URL | Typography | Motion graphics |

Scene 06 generates nothing at all — it reuses the scene 03 clips as flashbacks.

---

## Settings for every clip

- **16:9, 1080p.**
- **Discard the audio.** Veo generates its own ambience and will invent dialogue whenever people
  are on screen. You have a narrator. Never prompt for speech.
- **Generate 3–4 variants of every scene.** The rejection rate is high and the good one is cheap.
- **Attach the reference stills where the scene says so.** Use the reference-image input
  (Flow's ingredients input, or image-to-video), not a text description of the person — a text
  description gives you a different woman every time.
- **Negative prompt — paste on every single generation:**

```
text, writing, letters, numbers, captions, subtitles, watermark, logo, signage,
labels, prescription label, user interface, on-screen graphics, readable screen,
clock face, dial reading, distorted hands, extra fingers, warped fingers
```

### The 8-second ceiling

Veo returns 8 seconds. Three scenes are longer than that, so run the same prompt twice and cut
between the two generations — do not shorten the prompt to fit.

| Scene | Screen time | Generations |
|---|---|---|
| 01 | 14s | 2 |
| 02 | 8s | 1 |
| 03 | 10s | 2 |
| 04 | 2s | 1 |
| 07 | 10s | 2 (one tends to favour each woman) |
| 08 | 2s | 1 |

---

## Make these two stills first, and lock them

Every face in the film comes from these. Generate, pick one of each, and do not regenerate them
later — if the woman changes between scene 01 and scene 07, the film breaks.

### Still A · the patient — used in scenes 01 and 07

```
Photoreal portrait of a woman in her late sixties, warm and tired, short grey-brown
hair, soft lines around the eyes, wearing a plain cardigan over a simple blouse.
Seated in a hospital discharge chair beside a large window. Soft cool north-facing
daylight from camera left. Neutral clinical background, thrown out of focus, white
and pale blue. Shot on 50mm at f/2, shallow depth of field. Documentary realism,
unretouched skin, no stylisation. Calm, dignified, unglamorous.
```

### Still B · the care coordinator — used in scenes 02, 07 and 08

```
Photoreal portrait of a woman in her late thirties, composed and alert, dark hair
tied back, plain navy scrubs top, a lanyard worn with the badge turned backwards so
no card face shows. Seated at a desk in a quiet hospital office in the late
afternoon. Warm desk lamp from camera right mixing with cool window light from
behind. Shot on 50mm at f/2, shallow depth of field. Documentary realism,
unretouched, no stylisation.
```

---

## Scene 01 · The document · 0:00–0:14 · attach Still A

```
A sequence of four extreme macro shots on blank white paper, followed by one portrait.

Shot one: a single staple in the corner of a stack of completely blank white paper on
a wooden desk, paper fibre and tooth clearly visible, raking low-angle morning light
from camera left casting a long soft shadow from the staple. The camera pushes in
very slowly.

Shot two: a single thin grey horizontal rule printed across an otherwise completely
blank sheet of white paper, the page empty apart from that one line. The camera
drifts slowly along the line.

Shot three: one looping abstract ink pen stroke on blank white paper, a flowing
continuous mark rather than handwriting, fresh black ink with a slight wet sheen. The
camera drifts slowly along the stroke.

Shot four: the folded corner of a blank white sheet lifting slightly off the desk,
catching raking light, with a deep soft shadow beneath the fold.

Shot five: cut to a woman in her late sixties matching the reference image, seated in
a hospital discharge chair beside a large window. She breathes, blinks once, and
turns her head a few degrees toward the window. No other movement.

The first four shots are 100mm macro at f/2.8 to f/4; the final portrait is 50mm at
f/2. Consistent soft cool daylight and a muted clinical palette of white, navy and
pale blue throughout. No speech. Photoreal documentary realism, shallow depth of
field, no stylisation.
```

## Scene 02 · The blank side · 0:15–0:23 · attach Still B

```
One continuous shot. Open wide at 35mm, f/2.8: a woman in her late thirties in navy
scrubs, matching the reference image, sits at a desk in a quiet hospital office late
in the day, a lit desk lamp beside her, an empty corridor softly out of focus behind
her. She holds a single sheet of completely blank white paper and turns it over with
slow, deliberate, well-formed hands. The camera pushes in past her toward the page as
she turns it, ending in extreme macro on the completely blank reverse side filling the
entire frame. Warm desk lamp light from camera right mixing with cool blue evening
window light. No speech. Photoreal documentary realism, shallow depth of field, muted
clinical palette.
```

## Scene 03 · The signals nobody sees · 0:23–0:33

> Generate this twice and keep generous takes. Scene 06 reuses all three beats as
> half-second flashbacks, and short takes make clean sub-second cuts much harder.

```
A sequence of three intimate macro shots of an older woman at home. Hands and feet
only; her face is never visible.

Shot one, low angle at floor level, 50mm at f/2.8: her bare feet step onto a plain
white bathroom scale on a tiled bathroom floor. The frame cuts at mid-calf and the
scale's dial is entirely out of frame. The feet settle, weight shifting slightly,
then go still. Soft diffused morning light through a frosted window.

Shot two, 85mm at f/2.8: her hand opens the lid of a plain unmarked white pill
organiser on a kitchen counter, one compartment still full of white tablets. The hand
hesitates above it, then closes the lid. The organiser surface is smooth and
completely unmarked.

Shot three, 85mm at f/2.8: a smartphone lies face-down on a wooden kitchen table. Her
thumb and finger slide it a few centimetres away across the wood, then withdraw from
frame. The screen stays face-down and is never visible.

Soft overcast daylight throughout, muted palette, slow and quiet. No speech.
Photoreal documentary realism, shallow depth of field.
```

## Scene 04 · The turn · 0:34–0:36

```
Extreme macro, 100mm at f/4. A completely blank sheet of white paper fills the entire
frame, lit by soft raking light from camera left. Visible paper fibre and tooth, a
faint shadow along one edge. The camera is locked off and completely static. Nothing
moves except an almost imperceptible drift of light across the surface. No speech.
Photoreal, still, contemplative, muted clinical palette.
```

## Scene 07 · The call · 1:06–1:16 · attach Still A and Still B

```
Two shots.

Shot one, medium, 50mm at f/2: a woman in her late thirties in navy scrubs, matching
the second reference image, sits at her desk, looks up from her monitor, reaches for a
desk phone and lifts the handset to her ear. Her expression is focused, not alarmed.
The monitor sits behind her shoulder and is thrown completely out of focus. Warm desk
lamp from camera right, cool window light behind.

Shot two, medium, 50mm at f/2: cut to a woman in her late sixties, matching the first
reference image, at home in a softly lit living room. She picks up a ringing phone and
holds it to her ear, listens, and her expression eases very slightly. Warm afternoon
light through a net curtain, with a very slight handheld drift.

No speech in either shot. Photoreal documentary realism, warm and human, shallow depth
of field.
```

## Scene 08 · Population · 1:16–1:29

```
Wide shot, 35mm at f/1.8. Four people work at desks in a bright modern hospital
care-management office, two on phones, two facing monitors. All monitors are far from
camera and thrown completely out of focus into soft pale glows. Gentle natural
movement — someone leaning back, a chair turning. Cool daylight from a wall of
windows. The camera pushes in slowly. No speech. Photoreal documentary realism, very
shallow depth of field, muted clinical palette.
```

---

## Reject rules

Reject and re-prompt. Do not try to fix these in the edit:

- **Any legible text anywhere in frame** — a dial, a label, a badge, a sign, a background
  monitor. This is the entire reason the shot list was rewritten.
- **Hands with wrong finger counts or impossible bends.** Scenes 02, 03 and 07 are all hands;
  expect to throw several away.
- **The face drifting** between scene 01 and scene 07, or across 02 / 07 / 08. Go back to the
  reference still rather than re-prompting from text.
- **Anything that looks like stock footage** — glossy, over-lit, smiling at nothing. The
  register is quiet and documentary.

## After generation

Grade every generated clip toward the app's whites **before** building the scene 05 dissolve.
If the paper and the product sit at different colour temperatures, the transition the whole
film rests on will not land.

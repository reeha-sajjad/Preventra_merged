# Preventra — design language

One language across three surfaces: the Portal, GLP-1 and Readmissions. A user who
signs in at the Portal and lands in either app should not feel they changed vendors.

This file is the **decision record**, not a style suggestion. Phase 0 locked the
palette, the type and the risk bands; later phases implement them. Where a value here
disagrees with code, the file is the target and the code is the backlog.

Companion docs: `CLAUDE.md` (orientation), `ACCESS_CONTROL.md` (roles), root `README.md`
(runbook). The parity map in `CLAUDE.md` still governs *which files* change together —
this file governs *what they change to*.

---

## Why this exists — the state Phase 0 found

Three design languages, not one drifting:

| | Portal | GLP-1 | Readmissions |
|---|---|---|---|
| Brand colour | teal `#3ecfae` + navy `#0e1b2a` | blue `#1B4F8A`, sidebar `#0F2D4F` | navy `#0F2A4A`, amber accent |
| Type | Sora + IBM Plex Sans, loaded | Arial, plus 47 `DM Serif Display` refs that never load | system `font-sans` |
| Tailwind | none, hand CSS | v3 (`tailwind.config.js`) | v4 (`@theme` in `index.css`) |
| Style authoring | hand CSS | 483 inline `style={{}}`, 102 distinct hex | utilities, 32 distinct hex |
| Footer | yes, 3 pages | none | none |
| Size | 3 html files | 63 jsx / 10,798 lines | 68 jsx / 12,285 lines |

Four defects worth naming, because they drove the decisions below:

1. **Every GLP-1 heading renders as Times New Roman.** `fontFamily: 'DM Serif Display, serif'`
   appears 47 times across 22 files and nothing loads that font — not `index.html`, not
   the CSS, not `public/`. The browser falls back to generic serif.
2. **`font-mono` means two different things.** GLP-1's v3 config maps `mono` to Arial, so
   its 72 `font-mono` uses are a silent no-op; Readmissions' 22 uses get real system mono.
   The same intent — line up a column of numbers — renders differently per app.
3. **`text-gray-400` is used as body text 442 times** (249 GLP-1, 193 Readmissions) at
   2.54:1 on white. That fails WCAG AA for text, in a clinical product.
4. **`risk-high` fails contrast in the app that defined it.** `#EF6C00` is 3.08:1 on white
   and 2.81:1 on its own `#FFF3E0` badge — the band that matters most clinically is the
   least readable one.

The good news, which shaped the plan: the apps are closer than they look. Both already
use the same Tailwind gray ramp, the same radius steps, and a 12px-radius white card
(`.card` 102× in GLP-1; `bg-white rounded-xl border border-gray-200 shadow-sm` repeated
~60× in Readmissions, in five different class orderings — the same card, unnamed).
`UserManagement.jsx` is **byte-identical** in both apps, 735 lines, zero diff, across the
v3/v4 split. A shared component survives if it sticks to core utilities.

---

## The three Phase 0 decisions

**1. The Portal's palette is canonical — teal + navy.**
It is the actual brand mark, it is the most deliberate of the three, and it is the screen
that sets expectations before anyone reaches an app. GLP-1's `#1B4F8A` is demoted to a
data/chart colour (43 occurrences to revisit). Readmissions' `#0F2A4A` collapses into the
canonical navy `#0e1b2a` — a 2-point shift nobody will notice.

**2. Four risk bands, from GLP-1's set, re-tuned for contrast.**
Readmissions gains a Critical band it lacks today. The finer gradation already drives
GLP-1's worklist sorting, and keeping red for the top band only means red still means
*red*. Values are re-tuned below because the originals fail AA.

**3. Sora for headings, IBM Plex Sans for body — exactly what the Portal loads.**
This retires all 47 phantom serif references. No separate mono face: numbers use IBM Plex
Sans with `font-variant-numeric: tabular-nums`, which is what the 94 `font-mono` uses were
reaching for.

---

## Tokens

Names are prefixed `--p-` so they cannot collide with either app's existing variables
during migration. Every ratio below is computed, not estimated.

### Brand and ink

| Token | Value | Use | On white |
|---|---|---|---|
| `--p-brand` | `#3ecfae` | teal **fields** only — never text, never behind white text | 1.96:1 ✗ |
| `--p-brand-deep` | `#1f9e83` | hover/press on a teal field | 3.35:1 (large only) |
| `--p-brand-ink` | `#21836d` | teal **text** and links on white | 4.63:1 ✓ |
| `--p-brand-icon` | `#29a68a` | teal icons and ≥24px text | 3.04:1 ✓ large |
| `--p-brand-wash` | `#e4f7f2` | teal tint behind a section | — |
| `--p-ink` | `#0e1b2a` | primary text, sidebar field, **button fills** | 17.37:1 ✓ |
| `--p-ink-soft` | `#1d3046` | secondary navy surfaces | — |
| `--p-muted` | `#5a6b78` | secondary text | 5.51:1 ✓ |
| `--p-canvas` | `#f3f7f8` | page background | — |
| `--p-surface` | `#ffffff` | card background | — |
| `--p-line` | `#dfe7e9` | decorative dividers and card borders | 1.25:1 — decorative only |
| `--p-line-strong` | `#789aa2` | borders that are the *only* indicator of a control | 3.02:1 ✓ |
| `--p-focus` | `#21836d` | 3px focus ring, 3px offset | 4.63:1 ✓ |

**Teal is a field, not an inkwell.** `#3ecfae` is 1.96:1 on white and 1.96:1 under white
text — it can never carry text in either direction. It works as a field with navy on top
(8.89:1). The Portal already reached this conclusion; its own CSS comments "buttons: navy
on white reads at any size". Primary buttons are navy with a white label (17.37:1).

**Focus rings:** the Portal currently rings in `--teal` at 1.96:1, which fails the 3:1
non-text requirement. Phase 1 switches it to `--p-focus`.

### Risk bands — three tokens per band

A band needs different colours for a bar, for a label, and for a badge background. One
token per band cannot satisfy all three, which is exactly how `risk-high` ended up failing.

| Band | `-fill` (bars, dots, series) | `-ink` (text) | `-tint` (badge bg) | ink on white | ink on tint |
|---|---|---|---|---|---|
| critical | `#c62828` 5.62:1 ✓ | `#c62828` | `#ffebee` | 5.62:1 ✓ | 4.92:1 ✓ |
| high | `#ef6c00` 3.08:1 ✓ | `#b65200` | `#fff3e0` | 5.00:1 ✓ | 4.56:1 ✓ |
| medium | `#c58700` 3.07:1 ✓ | `#92400e` | `#fffde7` | 7.09:1 ✓ | 6.91:1 ✓ |
| low | `#2e7d32` 5.13:1 ✓ | `#2e7d32` | `#e8f5e9` | 5.13:1 ✓ | 4.56:1 ✓ |

`-fill` values are judged at 3:1 (WCAG 1.4.11, graphical objects); `-ink` at 4.5:1 (text).
Readmissions' amber `#F0A500` is darkened to `#c58700` — at 2.08:1 it failed even the
graphical threshold.

**Colour never carries a band alone.** Adjacent fills are 1.82:1, 1.00:1 and 1.67:1 apart —
high and medium are *identical* in luminance, differing only in hue, so they vanish
together for a colourblind or greyscale reader. Every band must render its word
(Critical / High / Medium / Low) or an icon. Both apps already do this today; the rule is
written down so neither stops.

### Neutrals

Adopt the Tailwind gray ramp as-is — both apps already use the same members, so there is
nothing to migrate. One rule, from the 442 failing uses:

- `gray-400` `#9ca3af` (2.54:1) — **decorative only**: separators, disabled glyphs, placeholder icons. Never text a user must read.
- `gray-500` `#6b7280` (4.83:1) — the floor for secondary text.
- `gray-600` `#4b5563` (7.56:1), `gray-700` `#374151` (10.31:1) — body and strong text.

### Status (non-clinical)

`--p-positive` `#2e7d32`, `--p-negative` `#c62828`, `--p-warning` `#92400e`,
`--p-info` `#1b4f8a`. Deliberately the same values as the risk inks: a saved-successfully
tick and a low-risk badge should not be two different greens. `--p-info` is where GLP-1's
old primary blue retires to.

---

## Type

Sora 500/600/700 for headings, IBM Plex Sans 400/500/600 for everything else — the two
faces the Portal already loads from Google Fonts.

| Step | Size / line | Face, weight | Replaces |
|---|---|---|---|
| `display` | 28px / 1.2 | Sora 600 | the one-off `text-[34px]`, `text-[28px]`, `text-[32px]`, `text-[26px]` |
| `h1` | 20px / 1.3 | Sora 600 | `text-xl`, page titles, the serif headings |
| `h2` | 16px / 1.4 | Sora 600 | `text-lg` card headings |
| `h3` | 14px / 1.4 | Sora 600 | `text-base` sub-headings |
| `body` | 14px / 1.55 | Plex 400 | `text-sm` — the workhorse (244 GLP-1, 439 Readmissions) |
| `body-sm` | 13px / 1.5 | Plex 400 | `text-[13px]`, `text-[13.5px]`, `text-[12.5px]` |
| `label` | 12px / 1.4 | Plex 500 | `text-xs` (318 GLP-1, 317 Readmissions) |
| `micro` | 11px / 1.3 | Plex 600, `0.06em`, uppercase | `text-[11px]` (77 + 39) and `text-[10px]` (78 + 10) |

Two rules:

- **11px is the floor.** `text-[9px]` and `text-[8px]` (4 uses in GLP-1) go. `text-[10px]`
  becomes `micro`.
- **Numbers never change face.** Tabular figures come from
  `font-variant-numeric: tabular-nums` on IBM Plex Sans, not a mono family. This is what
  GLP-1's 72 Arial-mapped `font-mono` uses and Readmissions' 22 real-mono uses both wanted,
  and it makes a column of risk percentages line up identically in both apps.

Fonts are **self-hosted** in each app's `public/`, not pulled from `fonts.googleapis.com`.
The Portal may call a CDN; a clinical app behind a hospital proxy should not depend on one,
and it keeps a third party out of the request path on a page showing patient data.

---

## Geometry

Already near-identical in both apps, so these codify practice rather than change it.

| Token | Value | Note |
|---|---|---|
| `--p-radius-sm` | 6px | `rounded-md` (32 GLP-1, 82 Readmissions) |
| `--p-radius` | 8px | `rounded-lg` — the default (133, 142) |
| `--p-radius-lg` | 12px | `rounded-xl` — **the card** (67, 120) |
| `--p-radius-pill` | 9999px | badges, role pills (94, 72) |
| `--p-shadow-card` | `0 1px 3px rgba(14,27,42,.06), 0 4px 16px rgba(14,27,42,.04)` | GLP-1's `--shadow-card`, renoted on navy |
| `--p-shadow-raised` | `0 4px 12px rgba(14,27,42,.10), 0 8px 28px rgba(14,27,42,.07)` | hover, popovers |
| `--p-sidebar-w` | 240px | GLP-1's width wins; Readmissions loses 16px |
| `--p-sidebar-w-collapsed` | 68px | Readmissions gains a collapse it lacks |
| `--p-topnav-h` | 60px | GLP-1's fixed height; Readmissions' padding-derived header converges on it |

Spacing is Tailwind's 4px scale. Card padding is 20px (`p-5`) for dense cards and 24px
(`p-6`) for page-level cards; `rounded-2xl` (18 uses) is not in the scale and goes.

---

## Rules that outlive the migration

1. **Tokens, not hex.** No new raw hex in either `src/`. Phase 5 enforces this in CI.
2. **Duplicate, never import.** The two services deploy separately, so shared files are
   twin copies edited together — see the parity map in `CLAUDE.md`. `UserManagement.jsx`
   proves a twin can stay byte-identical.
3. **Plain CSS custom properties are the shared layer.** They behave identically under
   Tailwind v3 and v4, so the token file needs no build integration and cannot break
   either app. Each app's `theme.extend` (v3) / `@theme` (v4) *maps onto* the tokens;
   existing utility classes keep working untouched.
4. **Do not unify the Tailwind versions as part of this.** Upgrading GLP-1 v3 → v4 is a
   migration across 63 files and would stall the visible work. Revisit as its own project.
5. **Never colour alone.** Every risk band carries its word or an icon.
6. **Contrast is a gate, not a preference.** Text 4.5:1, large text and graphical objects
   3:1. The ratio table above is the reference; re-run the check when adding a colour.
7. **Hiding UI is still only a courtesy.** Styling changes nothing about authorisation —
   the backend refusal comes first, exactly as `CLAUDE.md` requires.

---

## What this commits each app to

| Change | GLP-1 | Readmissions | Portal |
|---|---|---|---|
| Load Sora + IBM Plex, self-hosted | new | new | switch CDN → self-host |
| Retire phantom serif | 47 refs, 22 files | — | — |
| `font-mono` → tabular-nums | 72 | 22 | — |
| Brand colour shift | `#1B4F8A` → `--p-info`, 43 uses | `#0F2A4A` → `--p-ink` | none |
| Risk band tokens | 4 bands re-tuned | 3 bands → 4 | — |
| `gray-400` text → `gray-500` | 249 | 193 | — |
| Inline `style={{}}` → tokens/classes | 483 | 21 | — |
| Named card primitive | `.card` → `Card` | ~60 class repeats → `Card` | — |
| Sidebar / header / footer | restyle, keep collapse | gain collapse, lose 16px | footer already exists |
| Focus ring | new | new | teal → `--p-focus` |

---

## Open — not decided in Phase 0

- **Dark mode.** Nothing in any surface supports it today. The token structure makes it
  possible later; no one has asked, so no values are reserved.
- **Chart palette.** Both apps use Recharts, and segment colours (`--seg-0..3`) are
  separate from risk bands. Phase 3 territory.
- **Density.** Both apps lean on 11–14px type for clinician screens. Whether a
  comfortable/compact toggle is wanted is a product question.
- **The 16px Readmissions sidebar loss.** 256px → 240px shortens the longest nav label
  ("Clinician Console"); worth an eye before Phase 2 ships.

---

## Phases

- **Phase 0 — this file.** Palette, type and risk bands decided and verified. ✅
- **Phase 1** — token file as twin copies, font loading, the serif and `font-mono` fixes,
  v3/v4 theme mapping onto tokens.
- **Phase 2** — chrome: sidebar, header, and the footer neither app has.
- **Phase 3** — named primitives: `Card`, `Button`, `Badge`, `DataTable`, `Dialog`, `EmptyState`.
- **Phase 4** — migrate screens, retiring inline styles app by app.
- **Phase 5** — guardrails: no-raw-hex check, parity check over the twin file list.

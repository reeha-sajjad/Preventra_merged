"""
Verify every colour pairing DESIGN.md commits to.

DESIGN.md is the design-language decision record; this script is the part of it a
machine can check. It encodes the token table and the gate each pairing has to clear,
then fails if one slips. Run it when you add or change a colour, and wire it into CI
alongside the Phase 5 no-raw-hex check.

    python scripts/check_contrast.py           # report, exit 1 on any failure
    python scripts/check_contrast.py --quiet    # only failures

Gates come from WCAG 2.2:
  4.5:1  body text                      (1.4.3)
  3.0:1  text >=24px, or >=18.66px bold (1.4.3)
  3.0:1  graphical objects and the part  (1.4.11)
         of a control that identifies it

What this cannot check: whether colour is the ONLY thing carrying a meaning. Adjacent
risk fills sit as close as 1.00:1, so a band always needs its word or an icon beside it.
That rule lives in DESIGN.md and is enforced by review, not here.
"""
from __future__ import annotations

import sys

WHITE = "#ffffff"


def _linear(channel: int) -> float:
    c = channel / 255
    return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4


def luminance(hex_colour: str) -> float:
    h = hex_colour.lstrip("#")
    r, g, b = (int(h[i:i + 2], 16) for i in (0, 2, 4))
    return 0.2126 * _linear(r) + 0.7152 * _linear(g) + 0.0722 * _linear(b)


def contrast(fg: str, bg: str) -> float:
    a, b = luminance(fg), luminance(bg)
    hi, lo = max(a, b), min(a, b)
    return (hi + 0.05) / (lo + 0.05)


# ── The token table from DESIGN.md ───────────────────────────────────────────
# (label, foreground, background, gate)
CHECKS = [
    # Brand and ink
    ("brand-ink on surface",            "#21836d", WHITE,     4.5),
    ("brand-icon on surface (large)",   "#29a68a", WHITE,     3.0),
    ("ink on surface",                  "#0e1b2a", WHITE,     4.5),
    ("ink on canvas",                   "#0e1b2a", "#f3f7f8", 4.5),
    ("muted on surface",                "#5a6b78", WHITE,     4.5),
    ("muted on canvas",                 "#5a6b78", "#f3f7f8", 4.5),
    ("line-strong on surface",          "#789aa2", WHITE,     3.0),
    ("focus ring on surface",           "#21836d", WHITE,     3.0),

    # Teal is a field, never an inkwell: navy rides on top of it.
    ("ink on brand field",              "#0e1b2a", "#3ecfae", 4.5),
    # Primary button: navy fill, white label.
    ("button label on ink",             WHITE,     "#0e1b2a", 4.5),
    # Sidebar is an ink field.
    ("sidebar label on ink",            WHITE,     "#0e1b2a", 4.5),
    ("sidebar muted label on ink",      "#a6abb2", "#0e1b2a", 4.5),
    ("sidebar active accent on ink",    "#3ecfae", "#0e1b2a", 3.0),

    # Risk fills — graphical objects (bars, dots, chart series) on a white card
    ("risk-critical-fill on surface",   "#c62828", WHITE,     3.0),
    ("risk-high-fill on surface",       "#ef6c00", WHITE,     3.0),
    ("risk-medium-fill on surface",     "#c58700", WHITE,     3.0),
    ("risk-low-fill on surface",        "#2e7d32", WHITE,     3.0),

    # Risk inks — text, on a white card and on the band's own badge tint
    ("risk-critical-ink on surface",    "#c62828", WHITE,     4.5),
    ("risk-critical-ink on tint",       "#c62828", "#ffebee", 4.5),
    ("risk-high-ink on surface",        "#b65200", WHITE,     4.5),
    ("risk-high-ink on tint",           "#b65200", "#fff3e0", 4.5),
    ("risk-medium-ink on surface",      "#92400e", WHITE,     4.5),
    ("risk-medium-ink on tint",         "#92400e", "#fffde7", 4.5),
    ("risk-low-ink on surface",         "#2e7d32", WHITE,     4.5),
    ("risk-low-ink on tint",            "#2e7d32", "#e8f5e9", 4.5),

    # Status (non-clinical)
    ("positive on surface",             "#2e7d32", WHITE,     4.5),
    ("negative on surface",             "#c62828", WHITE,     4.5),
    ("warning on surface",              "#92400e", WHITE,     4.5),
    ("info on surface",                 "#1b4f8a", WHITE,     4.5),

    # Neutral floor: gray-400 is decorative only, so it is NOT listed as text.
    ("gray-500 text on surface",        "#6b7280", WHITE,     4.5),
    ("gray-600 text on surface",        "#4b5563", WHITE,     4.5),
    ("gray-700 text on surface",        "#374151", WHITE,     4.5),
]

# Pairings DESIGN.md explicitly forbids. Each must FAIL the gate — if one starts
# passing, the token changed and the prose in DESIGN.md is now wrong.
FORBIDDEN = [
    ("brand as text on surface",        "#3ecfae", WHITE,     4.5),
    ("white label on brand field",      WHITE,     "#3ecfae", 4.5),
    ("gray-400 as text on surface",     "#9ca3af", WHITE,     4.5),
]


def main() -> int:
    quiet = "--quiet" in sys.argv
    failures = []

    if not quiet:
        print("Pairings DESIGN.md requires")
        print("-" * 64)
    for label, fg, bg, gate in CHECKS:
        ratio = contrast(fg, bg)
        ok = ratio >= gate
        if not ok:
            failures.append((label, ratio, gate))
        if not quiet or not ok:
            print("  %-34s %5.2f:1  need %.1f  %s"
                  % (label, ratio, gate, "ok" if ok else "FAIL"))

    if not quiet:
        print("\nPairings DESIGN.md forbids (these must stay below the gate)")
        print("-" * 64)
    for label, fg, bg, gate in FORBIDDEN:
        ratio = contrast(fg, bg)
        still_forbidden = ratio < gate
        if not still_forbidden:
            failures.append((label + " (now passes; DESIGN.md says it cannot)", ratio, gate))
        if not quiet or not still_forbidden:
            print("  %-34s %5.2f:1  under %.1f  %s"
                  % (label, ratio, gate, "ok" if still_forbidden else "STALE DOC"))

    print()
    if failures:
        print("%d problem(s):" % len(failures))
        for label, ratio, gate in failures:
            print("  - %s: %.2f:1 against a gate of %.1f" % (label, ratio, gate))
        print("\nFix the token or update DESIGN.md — they are meant to agree.")
        return 1

    print("All %d pairings clear their gate; all %d forbidden pairings still fail theirs."
          % (len(CHECKS), len(FORBIDDEN)))
    return 0


if __name__ == "__main__":
    sys.exit(main())

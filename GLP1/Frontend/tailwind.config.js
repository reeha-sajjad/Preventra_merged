/** @type {import('tailwindcss').Config} */

// Tailwind v3 here; Readmissions is on v4 with an `@theme` block instead. Both
// map onto the same `--p-*` tokens in src/styles/preventra-tokens.css, so the
// two apps agree on colour and type without sharing a module. See DESIGN.md.
//
// Values are `var(--p-*)` rather than literal hex so there is exactly one place
// a colour is defined. Safe here because nothing in this app uses an opacity
// modifier on a custom colour (`bg-primary/50`), which is the one v3 feature
// that needs a literal. If you add one, give that colour a literal value.
export default {
  content: ['./index.html', './src/**/*.{js,ts,jsx,tsx}'],
  theme: {
    extend: {
      fontFamily: {
        // `display` was Arial, so the 26 `font-display` uses did nothing; the
        // 47 inline `DM Serif Display` references pointed at a font no app
        // ever loaded and fell back to Times. Both now resolve to Sora.
        display: ['var(--p-font-display)'],
        sans: ['var(--p-font-body)'],
        body: ['var(--p-font-body)'],
        // `mono` was Arial too, which made all 72 `font-mono` uses a silent
        // no-op while the same class in Readmissions got real mono. Now both
        // apps mean the same thing: identifiers, code and credentials.
        mono: ['var(--p-font-mono)'],
      },
      colors: {
        // #1B4F8A retires to `info` as a data colour; the brand is now the
        // Portal's navy + teal. Navy, not teal, carries white text.
        primary: {
          DEFAULT: 'var(--p-ink)',
          light: 'var(--p-ink-soft)',
          dark: 'var(--p-ink)',
        },
        brand: {
          DEFAULT: 'var(--p-brand)',
          deep: 'var(--p-brand-deep)',
          ink: 'var(--p-brand-ink)',
          wash: 'var(--p-brand-wash)',
        },
        surface: 'var(--p-surface)',
        canvas: 'var(--p-canvas)',
        border: 'var(--p-line)',
        muted: 'var(--p-muted)',
        info: 'var(--p-info)',
        positive: 'var(--p-positive)',
        negative: 'var(--p-negative)',
        warning: 'var(--p-warning)',
      },
      borderRadius: {
        DEFAULT: 'var(--p-radius)',
        card: 'var(--p-radius-lg)',
      },
      boxShadow: {
        card: 'var(--p-shadow-card)',
        raised: 'var(--p-shadow-raised)',
      },
      animation: {
        'fade-up': 'fadeUp 0.4s ease both',
        'fade-in': 'fadeIn 0.3s ease both',
        'slide-in': 'slideIn 0.35s ease both',
      },
      keyframes: {
        fadeUp: { '0%': { opacity: 0, transform: 'translateY(14px)' }, '100%': { opacity: 1, transform: 'translateY(0)' } },
        fadeIn: { '0%': { opacity: 0 }, '100%': { opacity: 1 } },
        slideIn: { '0%': { opacity: 0, transform: 'translateX(-14px)' }, '100%': { opacity: 1, transform: 'translateX(0)' } },
      },
    },
  },
  plugins: [],
}

/** @type {import('tailwindcss').Config} */
// The color names below are bound to CSS custom properties defined in
// src/index.css. The CSS vars carry the *values* from UI_TOKENS.md
// (--bg, --surface, --primary, …). This indirection means dark mode
// (Tier 1 #1) is a single CSS-var swap, not a class rename across the
// codebase. Per UI_TOKENS.md, tokens win over mockups where they
// disagree.
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  darkMode: ["class"],
  theme: {
    extend: {
      colors: {
        bg: "var(--bg)",
        surface: "var(--surface)",
        "surface-2": "var(--surface-2)",
        ink: "var(--ink)",
        "ink-2": "var(--ink-2)",
        "ink-3": "var(--ink-3)",
        // Slice 5 dark-text fix: `text-muted-foreground` (174 usages) was an
        // unmapped/inert utility, so muted text inherited --ink and rendered
        // near-white in dark mode (no hierarchy). Alias it to the canonical dim
        // token (ink-3) so muted text is correctly dimmed in both themes,
        // matching every other hint. (bg-muted/text-muted left unmapped =
        // unchanged from current live behavior.)
        "muted-foreground": "var(--ink-3)",
        hairline: "var(--hairline)",
        primary: "var(--primary)",
        "primary-soft": "var(--primary-soft)",
        green: "var(--green)",
        "green-soft": "var(--green-soft)",
        amber: "var(--amber)",
        "amber-soft": "var(--amber-soft)",
        // v3.65.1: amber-dim is the AA-safe ink for text on amber-soft.
        // See HANDOFF_V7.md "amber-soft + amber text WCAG AA finding".
        "amber-dim": "var(--amber-dim)",
        red: "var(--red)",
        "red-soft": "var(--red-soft)",
      },
      borderRadius: {
        sm: "var(--r-sm)",
        DEFAULT: "var(--r-md)",
        md: "var(--r-md)",
        lg: "var(--r-lg)",
      },
      fontFamily: {
        sans: [
          "Inter",
          "-apple-system",
          "BlinkMacSystemFont",
          "system-ui",
          "sans-serif",
        ],
      },
      letterSpacing: {
        // Mockup signature — see UI_TOKENS.md "Type".
        tight: "-0.01em",
        tighter: "-0.025em",
      },
    },
  },
  plugins: [],
};

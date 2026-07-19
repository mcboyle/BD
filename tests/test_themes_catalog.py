"""v3.64.x — themes restoration.

The legacy `/` UI shipped 31 named themes (Default Dark/Light, Console,
Editorial, Midnight, Nord, Solarized, Paper, Monochrome, Terminal,
Ember, Forest, Rosé Pine, Crimson, Daylight, Neon, Dracula, Gruvbox,
Tokyo Night, Catppuccin Mocha/Latte, One Dark, Monokai, Everforest,
Ayu Mirage, Sepia, Steel, Mint, Wine, Synthwave, Carbon). The SPA
collapsed this to three modes in v3.64.0. This release brings the
full catalog back.

The themes themselves live in `frontend/src/lib/themes.ts` — that's a
TypeScript module the SPA's Vite build consumes. These tests live on
the Python side because that's where the project's gate runs; they
parse the TS source as text and verify the catalog is well-formed.
The contract:

  1. All 31 legacy theme IDs are present.
  2. Each theme defines at least the SPA's load-bearing CSS vars
     (bg, surface, ink, primary).
  3. is_dark flags are correctly applied based on luminance.
"""
from __future__ import annotations

import re
from pathlib import Path


# Repo-rooted path so the test runs correctly regardless of cwd. Same
# pattern as test_v3_43_19_audit_fixes.py: parents[1] gives the repo
# root from tests/<this file>.
THEMES_TS = (Path(__file__).resolve().parents[1]
             / "frontend" / "src" / "lib" / "themes.ts")

EXPECTED_THEME_IDS = {
    "dark", "light",
    "console", "editorial", "midnight", "nord", "solarized", "paper",
    "monochrome", "terminal", "ember", "forest", "rose", "crimson",
    "daylight", "neon", "dracula", "gruvbox", "tokyo", "mocha",
    "latte", "onedark", "monokai", "everforest", "ayu", "sepia",
    "steel", "mint", "wine", "synthwave", "carbon",
}

# CSS vars the SPA references in index.css and Tailwind config. Every
# theme MUST define these or the page renders broken under that theme.
REQUIRED_VARS = {"bg", "surface", "surface-2", "ink", "ink-2", "ink-3",
                 "primary", "primary-soft"}


def _read_source() -> str:
    """Pulled into a helper so a test_themes_catalog_file_exists is
    a separate concern from a corrupted-source case."""
    return THEMES_TS.read_text(encoding="utf-8")


def test_themes_catalog_file_exists():
    """The TS catalog must exist where the SPA expects it."""
    assert THEMES_TS.is_file(), (
        f"Expected {THEMES_TS} — the SPA's themes catalog is missing. "
        f"The desktop-shell + themes restoration release ships this "
        f"file at frontend/src/lib/themes.ts.")


def test_theme_id_tuple_lists_all_31_legacy_themes():
    """THEME_IDS contains exactly the 31 legacy theme keys from
    bulk_downloader/static/app.js. A future refactor that drops one
    of the named themes (a regression) trips this test."""
    src = _read_source()
    # Match the THEME_IDS tuple body, which is one or more "id" strings
    # separated by commas.
    m = re.search(r"export const THEME_IDS\s*=\s*\[([^\]]+)\]", src)
    assert m, "THEME_IDS tuple not found in themes.ts"
    ids = set(re.findall(r'"([^"]+)"', m.group(1)))
    missing = EXPECTED_THEME_IDS - ids
    extra = ids - EXPECTED_THEME_IDS
    assert not missing, f"Missing theme IDs: {sorted(missing)}"
    # Extras are not fatal but the canonical set is fixed at 31
    # until/unless the operator says otherwise. Surface the case as
    # a clear failure rather than an under-the-rug accumulation.
    assert not extra, (
        f"Unexpected theme IDs: {sorted(extra)} — these aren't part "
        f"of the legacy 31. Update EXPECTED_THEME_IDS or remove them.")


def test_each_theme_defines_all_required_vars():
    """Every theme must define the SPA's load-bearing CSS vars. A
    theme missing `primary` would render with the previous theme's
    primary leaking through, since we don't reset before applying."""
    src = _read_source()
    # Each theme is a key block: `<id>: { ... vars: { ... } },`
    # Catch the vars-object body for each.
    for theme_id in EXPECTED_THEME_IDS:
        # Find the start of this theme's block. Lazy match so we don't
        # consume past the next theme.
        m = re.search(
            rf'\b{re.escape(theme_id)}:\s*\{{[^{{}}]*?vars:\s*\{{(.+?)\}}\s*,?\s*\}}',
            src, re.DOTALL)
        assert m, f"Could not find vars block for theme {theme_id!r}"
        body = m.group(1)
        # Extract keys, accepting both "bare" and "quoted" forms.
        keys = set(re.findall(r'(?:"([^"]+)"|(\w[\w-]*))\s*:', body))
        # Flatten the alternation tuples
        flat_keys = {a or b for (a, b) in keys}
        missing = REQUIRED_VARS - flat_keys
        assert not missing, (
            f"Theme {theme_id!r} missing required vars: {sorted(missing)}")


def test_is_dark_flag_matches_bg_luminance():
    """isDark must reflect the actual bg brightness. A theme with a
    near-white bg flagged as dark would break Tailwind's `dark:`
    utilities (focus rings, card borders) which key off the `.dark`
    class on <html>."""
    src = _read_source()
    # Per-theme: grab the isDark flag AND the bg color, compare.
    # Pattern: `<id>: { label: "...", isDark: true|false, vars: { bg: "#…", … } }`
    for theme_id in EXPECTED_THEME_IDS:
        m = re.search(
            rf'\b{re.escape(theme_id)}:\s*\{{[^{{}}]*?label:[^,]+,\s*isDark:\s*(true|false),\s*vars:\s*\{{\s*bg:\s*"(#[0-9a-fA-F]{{3,6}})"',
            src, re.DOTALL)
        assert m, f"Could not parse isDark + bg for theme {theme_id!r}"
        is_dark_declared = m.group(1) == "true"
        bg = m.group(2).lstrip("#")
        if len(bg) == 3:
            bg = "".join(c*2 for c in bg)
        r, g, b = int(bg[0:2], 16), int(bg[2:4], 16), int(bg[4:6], 16)
        lum = (r*0.299 + g*0.587 + b*0.114) / 255
        is_dark_computed = lum < 0.5
        assert is_dark_declared == is_dark_computed, (
            f"Theme {theme_id!r}: isDark={is_dark_declared} but bg={m.group(2)} "
            f"luminance={lum:.3f} suggests isDark={is_dark_computed}. "
            f"A mismatch breaks Tailwind dark: utilities under this theme.")


def test_theme_mode_type_includes_system_plus_all_ids():
    """The ThemeMode type union must accept "system" and every theme
    ID. A future refactor that narrows the union (e.g. accidentally
    re-introducing the old `'system' | 'light' | 'dark'`) trips this."""
    src = _read_source()
    m = re.search(
        r'export type ThemeMode\s*=\s*"system"\s*\|\s*ThemeId\s*;', src)
    assert m, (
        "ThemeMode must be defined as `\"system\" | ThemeId`. Got a "
        "different shape — likely a regression to a narrower union.")

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


# F060: the two default themes are what `system` resolves to, so their
# text tokens must be readable on every surface token they are drawn on.
# WCAG 2.1 AA for normal-size text is 4.5:1.
_AA_NORMAL = 4.5
_DEFAULT_THEMES = ("light", "dark")
_TEXT_ON_SURFACE = tuple(
    (fg, bg)
    for fg in ("ink", "ink-2", "ink-3", "primary")
    for bg in ("bg", "surface", "surface-2")
) + (("primary", "primary-soft"),)


def _theme_vars(src: str, theme_id: str) -> dict[str, str]:
    m = re.search(
        rf'\b{re.escape(theme_id)}:\s*\{{[^{{}}]*?vars:\s*\{{(.+?)\}}\s*,?\s*\}}',
        src, re.DOTALL)
    assert m, f"Could not find vars block for theme {theme_id!r}"
    return {a or b: v for (a, b, v) in re.findall(
        r'(?:"([^"]+)"|(\w[\w-]*))\s*:\s*"([^"]+)"', m.group(1))}


def _relative_luminance(hex_color: str) -> float:
    """WCAG 2.1 relative luminance of an opaque `#rrggbb`."""
    h = hex_color.lstrip("#")
    lin = []
    for i in (0, 2, 4):
        v = int(h[i:i + 2], 16) / 255
        lin.append(v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4)
    return 0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2]


def _contrast_ratio(fg: str, bg: str) -> float:
    hi, lo = sorted((_relative_luminance(fg), _relative_luminance(bg)),
                    reverse=True)
    return (hi + 0.05) / (lo + 0.05)


def _low_contrast_pairs(palette: dict[str, str]) -> list[tuple[str, str, float]]:
    low = []
    for fg, bg in _TEXT_ON_SURFACE:
        ratio = _contrast_ratio(palette[fg], palette[bg])
        if ratio < _AA_NORMAL:
            low.append((fg, bg, round(ratio, 2)))
    return low


def test_default_themes_text_tokens_meet_wcag_aa_on_surfaces():
    """Muted text (`ink-3`) on the page background and the accent on its
    soft tint rendered below 4.5:1 in the default light theme, which is
    most of what /settings draws at 11px."""
    src = _read_source()
    checked = 0
    for theme_id in _DEFAULT_THEMES:
        palette = _theme_vars(src, theme_id)
        for fg, bg in _TEXT_ON_SURFACE:
            assert re.fullmatch(r"#[0-9a-fA-F]{6}", palette[fg]), (theme_id, fg)
            assert re.fullmatch(r"#[0-9a-fA-F]{6}", palette[bg]), (theme_id, bg)
            checked += 1
        low = _low_contrast_pairs(palette)
        assert not low, (
            f"Theme {theme_id!r} text below {_AA_NORMAL}:1 "
            f"(fg, bg, ratio): {low}")
    assert checked == len(_DEFAULT_THEMES) * 13


def test_contrast_probe_flags_the_palette_f060_measured():
    """Negative control: the probe must say yes on the shipped-broken
    light palette, or its silence on the current one means nothing."""
    measured = {
        "bg": "#eaeef3", "surface": "#ffffff", "surface-2": "#f6f9fc",
        "ink": "#0a2540", "ink-2": "#4b5563", "ink-3": "#6b7280",
        "primary": "#635bff", "primary-soft": "#f0effe",
    }
    assert _low_contrast_pairs(measured) == [
        ("ink-3", "bg", 4.15),
        ("primary", "bg", 4.03),
        ("primary", "surface-2", 4.45),
        ("primary", "primary-soft", 4.13),
    ]

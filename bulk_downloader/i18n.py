"""Internationalization (i18n) — Phase 137, Block Q.

BD's UI is English-only. Some operators run BD in non-English-speaking
households; others have a partner who doesn't read English. This module
provides the plumbing:

  • extract_strings(html_path) — scan a template for visible text
    suitable for translation. Skips script/style/comments, ignores
    text inside variable placeholders.

  • load_locale(lang) — load a locale file from INSTALL_DIR/locales/<lang>.json
    Returns {string_id: translation}. Falls back to English.

  • translate(en_text, *, lang) — lookup helper. If no translation,
    returns the original. Logs misses for the extractor to pick up.

  • dump_pot() — write a .pot-style template for translators
    (json format, not real gettext).

The actual UI integration is a separate phase — front-end needs to
wrap user-visible strings with `t('...')`. This module is the
server-side support: catalog management + API for the front-end to
fetch the active locale.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Optional


def _locales_dir() -> Path:
    """Where to find locale .json files. Priority:
      1. INSTALL_DIR/locales (operator-owned, may contain customizations)
      2. <package>/locales (ships baseline en.json + sample es.json)

    The runtime checks #1 first per-file via load_locale; this helper
    returns #1's path so writes go there. The bundled fallback is
    consulted directly inside load_locale."""
    try:
        from .constants import INSTALL_DIR
        return Path(INSTALL_DIR) / "locales"
    except Exception:
        return Path.cwd() / "locales"


def _bundled_locales_dir() -> Path:
    """Locales that ship with the package — used as fallback when
    INSTALL_DIR has no override."""
    return Path(__file__).parent / "locales"


# ─── Extraction ───────────────────────────────────────────────────────

# Regex for HTML text nodes — best-effort, doesn't parse the DOM.
# We strip <script>, <style>, comments, attributes first, then
# split on tags to get loose text.
_TAG_RE = re.compile(r"<[^>]+>", re.DOTALL)
_SCRIPT_RE = re.compile(r"<script\b[^>]*>.*?</script>", re.IGNORECASE | re.DOTALL)
_STYLE_RE = re.compile(r"<style\b[^>]*>.*?</style>", re.IGNORECASE | re.DOTALL)
_COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)
_PLACEHOLDER_RE = re.compile(r"\{\{[^}]+\}\}|\{[^}]+\}")
_ENTITY_RE = re.compile(r"&[a-zA-Z]+;|&#\d+;")


def extract_strings(html: str, *,
                   min_length: int = 2,
                   skip_numeric: bool = True) -> list:
    """Scan an HTML string for translatable text. Returns a list of
    {text, occurrences, suggested_id} dicts, dedup'd."""
    if not html:
        return []
    # Strip non-text blocks
    cleaned = _SCRIPT_RE.sub("", html)
    cleaned = _STYLE_RE.sub("", cleaned)
    cleaned = _COMMENT_RE.sub("", cleaned)
    # Remove placeholders (template variables)
    cleaned = _PLACEHOLDER_RE.sub(" ", cleaned)
    # Split on tags
    pieces = _TAG_RE.split(cleaned)
    counts: dict = {}
    for piece in pieces:
        text = _ENTITY_RE.sub(" ", piece).strip()
        if not text or len(text) < min_length:
            continue
        if skip_numeric and re.fullmatch(r"[\d\s.,:;\-+%]+", text):
            continue
        if text.startswith("$") or text.startswith("//"):
            continue
        counts[text] = counts.get(text, 0) + 1
    out = []
    for text, n in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])):
        out.append({"text": text, "occurrences": n,
                    "suggested_id": _suggest_id(text)})
    return out


def _suggest_id(text: str) -> str:
    """Generate a stable ID for a string. Best-effort: snake_case the
    first few words, truncate."""
    slug = re.sub(r"[^a-zA-Z0-9 ]", "", text).strip().lower()
    parts = slug.split()[:5]
    return "_".join(parts)[:48] or "untitled"


def extract_from_files(paths: list) -> dict:
    """Run extract_strings over each path; merge into a single
    catalog. Returns {string: {occurrences, files, suggested_id}}."""
    catalog: dict = {}
    for p in paths:
        try:
            text = Path(p).read_text(encoding="utf-8")
        except Exception:
            continue
        for entry in extract_strings(text):
            t = entry["text"]
            if t not in catalog:
                catalog[t] = {
                    "text": t,
                    "occurrences": 0,
                    "files": set(),
                    "suggested_id": entry["suggested_id"],
                }
            catalog[t]["occurrences"] += entry["occurrences"]
            catalog[t]["files"].add(str(p))
    # Serialize sets for JSON friendliness
    for v in catalog.values():
        v["files"] = sorted(v["files"])
    return catalog


# ─── Locale management ────────────────────────────────────────────────

_LOCALES_CACHE: dict = {}
_MISSES: dict = {}


def load_locale(lang: str) -> dict:
    """Load <locales_dir>/<lang>.json. Cached. Empty dict if missing.

    Search order (per Phase 191):
      1. INSTALL_DIR/locales/<lang>.json (operator overrides)
      2. <package>/locales/<lang>.json (shipped baseline)

    The cache is keyed by lang only; if an operator drops a custom
    file at runtime they should call `clear_locale_cache()` (TODO).
    """
    if lang in _LOCALES_CACHE:
        return _LOCALES_CACHE[lang]
    candidate_paths = [
        _locales_dir() / f"{lang}.json",
        _bundled_locales_dir() / f"{lang}.json",
    ]
    data = {}
    for path in candidate_paths:
        if path.is_file():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                break
            except Exception as e:
                sys.stderr.write(f"[i18n] failed to load {path}: {e}\n")
                continue
    # Strip the _meta block from the runtime dict — the front-end
    # doesn't need the "what is this file" annotation.
    if isinstance(data, dict) and "_meta" in data:
        data = {k: v for k, v in data.items() if k != "_meta"}
    _LOCALES_CACHE[lang] = data
    return data


def available_locales() -> list:
    """List locales available on disk. Merges:
      • INSTALL_DIR/locales (operator overrides + custom)
      • <package>/locales (bundled baseline + samples)
    Returns sorted, deduplicated."""
    seen = set()
    for d in (_locales_dir(), _bundled_locales_dir()):
        if not d.is_dir():
            continue
        for p in d.glob("*.json"):
            seen.add(p.stem)
    return sorted(seen)


def translate(text: str, *, lang: str = "en") -> str:
    """Lookup translation. Falls back to original. Logs misses."""
    if not lang or lang == "en":
        return text
    cat = load_locale(lang)
    if text in cat:
        return cat[text]
    _MISSES[lang] = _MISSES.get(lang, set())
    _MISSES[lang].add(text)
    return text


def report_misses() -> dict:
    """Return strings that have been looked up but had no translation.
    UI for translators to focus on what's needed."""
    return {lang: sorted(strings) for lang, strings in _MISSES.items()}


# ─── POT-style template export ────────────────────────────────────────

def dump_template(template_files: list) -> dict:
    """Build a translation template suitable for editing. Output:
      {
        "version": "1.0",
        "generated_at": "...",
        "entries": [
          {"id": "save", "en": "Save", "occurrences": 42,
           "files": ["index.html", ...]},
          ...
        ]
      }
    """
    import datetime
    catalog = extract_from_files(template_files)
    return {
        "version": "1.0",
        "generated_at": datetime.datetime.utcnow().strftime(
            "%Y-%m-%dT%H:%M:%SZ"),
        "entry_count": len(catalog),
        "entries": [
            {"id": v["suggested_id"], "en": v["text"],
             "occurrences": v["occurrences"], "files": v["files"]}
            for v in sorted(catalog.values(),
                            key=lambda x: (-x["occurrences"], x["text"]))
        ],
    }


def save_locale(lang: str, translations: dict) -> bool:
    """Write a locale file. `translations` is {english_text: native_text}."""
    d = _locales_dir()
    d.mkdir(exist_ok=True)
    path = d / f"{lang}.json"
    try:
        # v3.47.8 (#43): atomic write — translation overrides are
        # user-curated and shouldn't be truncated on crash
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(translations, indent=2,
                                  ensure_ascii=False),
                       encoding="utf-8")
        tmp.replace(path)
        # Invalidate cache
        _LOCALES_CACHE.pop(lang, None)
        return True
    except Exception as e:
        sys.stderr.write(f"[i18n] save {path} failed: {e}\n")
        return False

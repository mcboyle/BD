"""v3.66.0 — selector auto-detection (templates-free path).

The static `login_templates_data.LOGIN_TEMPLATES` and `templates.TEMPLATES`
registries are hostname-keyed lookup tables: they only help on sites
whose entry is in the table. This module exposes a single function
that produces the same `learned.{login, download}` shape WITHOUT
consulting either registry — useful for new sites or sites whose
template has gone stale.

Entry point:
    detect_site_config(url, *, login_url=None, html=None, page=None,
                       quality_pref=None) -> dict

Returns:
    {
        "ok": bool,
        "source": "live" | "html" | "url_fetch" | None,
        "learned": {
            "login":    {user_field, pass_field, submit_btn},  # optional
            "download": {row_selectors, trigger_selectors,
                         url_attribute},                       # optional
        },
        "warnings": [str, ...],
        "error":   str | None,
    }

Branch selection (matches what the caller has in hand):
    1. `page` provided (Playwright Page from an already-driven session)
       → snapshot DOM, run extract_login_from_html + find_best_download.
    2. `html` provided (operator pasted page source)
       → extract_login_from_html only — download path needs a live page
       so we omit `learned.download` and warn.
    3. `url` provided, no page/html
       → if Playwright is importable AND chromium is present, spin a
         one-shot browser, navigate, then act as in (1). Otherwise
         signal ok=False with a clear reason.

The function never raises; bad input or unavailable Playwright is
returned as `ok: False, error: "..."` with `learned: {}`.
"""
from __future__ import annotations

import os
from typing import Any, Dict, Optional

# Optional deps — import lazily inside the branches that need them so
# import-time cost stays near zero and unit tests can exercise the
# HTML path on hosts without Playwright.


def snapshot_via_playwright(url: str, *,
                            timeout_ms: int = 15000) -> Optional[Dict[str, Any]]:
    """v3.66.5: spin a one-shot headless Chromium, navigate to `url`,
    return the rendered HTML + the final URL after redirects. Returns
    None when Playwright is unavailable or navigation fails outright;
    a warning is silently swallowed so callers can fall through to
    their own error handling.

    Used by app._auto_pick_templates when template_auto_detect_mode is
    'deep' — deep_detect needs rendered HTML, not static source, and
    we already have the Playwright plumbing here in auto_detect.
    """
    try:
        from . import cloak as _cloak  # type: ignore
    except ImportError:
        return None
    try:
        with _cloak.cloaked_page(headless=True) as pg:
            try:
                pg.goto(url, timeout=timeout_ms,
                        wait_until="domcontentloaded")
            except Exception:
                # Page partially loaded is still useful — capture whatever
                # we have.
                pass
            try:
                html = pg.content()
                final_url = pg.url
            except Exception:
                html = None
                final_url = url
            if not html:
                return None
            return {"html": html, "final_url": final_url}
    except Exception:
        return None


def detect_site_config(
    url: str = "",
    *,
    login_url: Optional[str] = None,
    html: Optional[str] = None,
    page: Optional[Any] = None,
    quality_pref: Optional[str] = None,
    timeout_ms: int = 15000,
) -> Dict[str, Any]:
    """Detect login + download selectors for a site without templates.

    Exactly one of `page`, `html`, or `url` is sufficient — they're
    tried in that order. Passing more than one is fine; the most-direct
    input wins.

    `login_url` is consulted only when `page`/`html` are absent and we
    need to navigate ourselves: it's where the login form lives, while
    `url` is the content page used for download-selector discovery.
    If `login_url` is omitted, `url` is used for both.

    `quality_pref` is forwarded to the download detector for tiebreaks
    between same-row candidates ("1080p", "720p", etc.). Mirrors the
    site-level `quality_preference` setting.
    """
    result: Dict[str, Any] = {
        "ok": False,
        "source": None,
        "learned": {},
        "warnings": [],
        "error": None,
    }

    # ── Branch 1: live page in hand ─────────────────────────────────
    if page is not None:
        try:
            html_snapshot = page.content()
        except Exception as e:
            result["error"] = f"page.content() failed: {type(e).__name__}: {e}"
            return result
        login = _detect_login_from_html(html_snapshot,
                                        warnings=result["warnings"])
        if login:
            result["learned"]["login"] = login
        dl = _detect_download_live(page, quality_pref=quality_pref,
                                   warnings=result["warnings"])
        if dl:
            result["learned"]["download"] = dl
        result["ok"] = bool(result["learned"])
        result["source"] = "live"
        if not result["ok"]:
            result["error"] = ("no login form and no download candidates "
                               "found on this page")
        return result

    # ── Branch 2: pasted HTML ───────────────────────────────────────
    if html is not None:
        login = _detect_login_from_html(html, warnings=result["warnings"])
        if login:
            result["learned"]["login"] = login
            result["ok"] = True
        else:
            result["error"] = "no login form recognized in the HTML"
        result["warnings"].append(
            "download selectors require a live Playwright page; "
            "skipping that half of the detection")
        result["source"] = "html"
        return result

    # ── Branch 3: URL — fetch with Playwright if available ──────────
    if not url and not login_url:
        result["error"] = "need at least a URL, HTML, or live page"
        return result
    target_url = login_url or url
    page_url = url or login_url

    try:
        from . import cloak as _cloak  # type: ignore
    except ImportError:
        result["error"] = ("cloak/Playwright unavailable; pass `html` "
                           "(pasted source) or `page` (live Page) instead")
        return result

    # Honor the env var the rest of the codebase uses to point at the
    # bundled Chromium snapshot — without this, Playwright tries to
    # download a fresh browser, which fails offline. (CloakBrowser owns its
    # own binary; this warning is for the Playwright fallback path.)
    if not os.environ.get("PLAYWRIGHT_BROWSERS_PATH"):
        result["warnings"].append(
            "PLAYWRIGHT_BROWSERS_PATH is unset; Playwright may not find "
            "the bundled Chromium — set it to the ms-playwright/ dir")

    try:
        with _cloak.cloaked_page(headless=True) as pg:
            try:
                pg.goto(target_url, timeout=timeout_ms,
                        wait_until="domcontentloaded")
            except Exception as e:
                result["warnings"].append(
                    f"navigation to {target_url!r} failed: "
                    f"{type(e).__name__}: {str(e)[:120]}")
            login_html = pg.content()
            login = _detect_login_from_html(login_html,
                                            warnings=result["warnings"])
            if login:
                result["learned"]["login"] = login
            # If login_url and url differ, navigate to url for the
            # download probe.
            if page_url and page_url != target_url:
                try:
                    pg.goto(page_url, timeout=timeout_ms,
                            wait_until="domcontentloaded")
                except Exception as e:
                    result["warnings"].append(
                        f"navigation to {page_url!r} failed: "
                        f"{type(e).__name__}: {str(e)[:120]}")
            dl = _detect_download_live(pg, quality_pref=quality_pref,
                                       warnings=result["warnings"])
            if dl:
                result["learned"]["download"] = dl
    except Exception as e:
        result["error"] = (f"Browser session failed: "
                           f"{type(e).__name__}: {str(e)[:160]}")
        return result

    result["ok"] = bool(result["learned"])
    result["source"] = "url_fetch"
    if not result["ok"] and not result["error"]:
        result["error"] = ("the page loaded but no login form or "
                           "download candidates were recognized")
    return result


# ── helpers ─────────────────────────────────────────────────────────


def _detect_login_from_html(html: str, *, warnings: list) -> dict | None:
    """Run the existing extract_login_from_html. Returns the login
    block on success, None on failure (with warnings appended)."""
    try:
        from . import template_extractor as _te
    except Exception as e:
        warnings.append(f"template_extractor import failed: {e}")
        return None
    try:
        r = _te.extract_login_from_html(html)
    except Exception as e:
        warnings.append(
            f"extract_login_from_html raised: {type(e).__name__}: {e}")
        return None
    if not r.get("ok"):
        warnings.append(f"login extract: {r.get('error') or 'failed'}")
        return None
    warnings.extend(r.get("warnings") or [])
    login = r.get("login") or {}
    # Strip empty selector lists so the caller can tell which fields
    # we actually have evidence for.
    return {k: v for k, v in login.items() if v} or None


def _detect_download_live(page, *, quality_pref, warnings: list
                          ) -> dict | None:
    """Probe the live page for the best download candidate, then
    synthesize a row_selector + url_attribute pair from it. Returns
    a learned.download block on success, None otherwise.

    Strategy: ask the existing detect.find_best_download (which we
    already trust for runtime selection) to identify the element,
    then walk back from the located element to a stable selector.
    """
    try:
        from . import detect as _detect
    except Exception as e:
        warnings.append(f"detect import failed: {e}")
        return None
    try:
        best = _detect.find_best_download(page, custom="", learned=None)
    except Exception as e:
        warnings.append(
            f"find_best_download raised: {type(e).__name__}: {str(e)[:120]}")
        return None
    if not best:
        warnings.append("no download candidate found by find_best_download")
        return None

    # find_best_download returns a Playwright Locator we can introspect
    # to derive a stable selector. The Locator API doesn't expose the
    # underlying selector string directly, so we evaluate against the
    # actual DOM element and synthesize.
    loc = best.get("locator")
    if loc is None:
        warnings.append("download candidate had no locator handle")
        return None

    try:
        descriptor = loc.evaluate(_JS_DESCRIBE_ELEMENT)
    except Exception as e:
        warnings.append(
            f"could not describe download element: "
            f"{type(e).__name__}: {str(e)[:120]}")
        return None

    row_selectors, url_attribute = _synthesize_row_selector(descriptor)
    if not row_selectors:
        warnings.append("could not synthesize a stable row selector "
                        "from the located download element")
        return None

    out = {
        "row_selectors": row_selectors,
        "url_attribute": url_attribute or "href",
    }
    # Trigger selectors are an optional companion — a "Show download"
    # button that has to be clicked to reveal the row. find_best_download
    # doesn't currently surface those, so we leave the field empty
    # rather than guessing. The teach overlay or static templates can
    # fill them in later.
    out["trigger_selectors"] = []
    return out


# A small JS snippet evaluated in the browser to grab the structural
# attributes of the matched element. Returning a single dict keeps the
# round-trip to one call.
_JS_DESCRIBE_ELEMENT = """
(el) => {
    if (!el) return null;
    const attrs = {};
    for (const a of el.attributes || []) attrs[a.name] = a.value;
    // Which of the URL-bearing attributes actually has a value?
    const url_attrs = ['data-href', 'data-url', 'data-src',
                       'data-download', 'data-fileurl', 'href', 'src'];
    let url_attr = '';
    for (const k of url_attrs) {
        if (el.getAttribute && el.getAttribute(k)) { url_attr = k; break; }
    }
    return {
        tag: (el.tagName || '').toLowerCase(),
        id:  el.id || '',
        classes: Array.from(el.classList || []),
        url_attribute: url_attr,
        attrs: attrs,
    };
}
"""


# Recognize CSS class names that look auto-generated / build-hashed and
# would not survive a redeploy. Cheap heuristic, same shape used in the
# template auditor: 5-8 chars, mixed-case with ≥2 internal flips, no
# common English bigram.
def _looks_hashed_class(cls: str) -> bool:
    import re
    if not cls or len(cls) < 5 or len(cls) > 8:
        return False
    if re.search(r"[aeiou]{2}|tt|ll|ss|nn|mm|rr|pp", cls.lower()):
        return False
    flips = 0
    for i in range(1, len(cls)):
        if (cls[i - 1].isalpha() and cls[i].isalpha()
                and cls[i - 1].islower() != cls[i].islower()):
            flips += 1
    return flips >= 2


def _synthesize_row_selector(descriptor: dict) -> tuple[list, str]:
    """Pick stable selectors for the located element.

    Returns (selectors, url_attribute). The selector list is ordered
    most-specific → least-specific so the runtime can fall through.
    """
    if not descriptor or not isinstance(descriptor, dict):
        return [], ""
    tag = (descriptor.get("tag") or "").lower()
    el_id = descriptor.get("id") or ""
    classes = [c for c in (descriptor.get("classes") or [])
               if isinstance(c, str)]
    url_attr = descriptor.get("url_attribute") or ""
    attrs = descriptor.get("attrs") or {}

    sels: list[str] = []
    # 1. ID is the most stable identifier — but only if the id itself
    #    isn't hashed (some sites generate `id="dl-x9aJ"` per page load).
    if el_id and not _looks_hashed_class(el_id):
        sels.append(f"#{el_id}")
    # 2. Tag + the data attribute that carries the URL. This is the
    #    single most reliable selector for download buttons in
    #    practice: even when classes rotate, the data-* contract is
    #    almost always preserved.
    if url_attr:
        sels.append(f"{tag}[{url_attr}]")
    # 3. Tag + first non-hashed class.
    stable_classes = [c for c in classes if not _looks_hashed_class(c)]
    if stable_classes:
        cls = stable_classes[0]
        sels.append(f"{tag}.{cls}")
    # 4. Tag + aria-label or download attribute as last-resort
    #    semantic anchor.
    for attr in ("aria-label", "title", "download"):
        if attrs.get(attr):
            v = str(attrs[attr])[:40].replace("'", "\\'")
            sels.append(f"{tag}[{attr}='{v}']")
            break
    # Dedupe while preserving order
    seen, out = set(), []
    for s in sels:
        if s not in seen:
            out.append(s)
            seen.add(s)
    return out, url_attr


# ── F1 / CAP-1: record-time selector auto-narrow ─────────────────────
#
# api_prune_selectors narrows a site's learned selectors by RUNTIME
# miss-ratio (needs accumulated hit/miss data). This is the complementary
# RECORD-TIME step: a fresh detection emits a spray of candidate selectors
# per role with no runtime data yet; these reduce that spray to a minimal,
# stable set using structural signals only, so a fresh site starts lean.
# Pure/offline; reuses the in-module _looks_hashed_class (no new import
# edge). Non-destructive by contract.

def _record_time_shape_key(sel: str) -> str:
    """Cheap structural fingerprint for record-time dedup: lowercase and
    collapse hashed class/id tokens to a placeholder so two candidates
    differing only by a rotated hash share one shape. A fingerprint, not a
    CSS parser (cf. cross_site_selectors.selector_shape) — kept
    self-contained so this adds no cross-module import edge."""
    import re
    s = (sel or "").strip()
    if not s:
        return ""

    # NB: test hashedness on the ORIGINAL-case token — _looks_hashed_class
    # keys on case flips, so lowercasing first would erase the signal and
    # fail to collapse rotated-hash variants.
    def _tok(m):
        prefix, name = m.group(1), m.group(2)
        return prefix + ("#h" if _looks_hashed_class(name) else name.lower())

    return re.sub(r"([#.])([A-Za-z0-9_-]+)", _tok, s).lower()


def _is_fragile_selector(sel: str) -> bool:
    """Record-time-fragile: references a hashed class/id token, or is
    purely positional (:nth-*). These are the candidates a runtime prune
    would most likely later drop, so they are deprioritized up front."""
    import re
    s = sel or ""
    if ":nth-" in s.lower():
        return True
    for _pfx, name in re.findall(r"([#.])([A-Za-z0-9_-]+)", s):
        if _looks_hashed_class(name):
            return True
    return False


def narrow_selectors(selectors, *, max_keep: int = 3):
    """Record-time auto-narrow of a FRESH candidate selector list (no
    runtime hit/miss data yet). Returns ``(kept, dropped)``:

      1. dedupe by structural shape (keep first occurrence);
      2. drop fragile candidates when any stable one survives;
      3. cap to ``max_keep``.

    NON-DESTRUCTIVE: a non-empty input never yields an empty ``kept`` — if
    every candidate is fragile the best fragile ones are kept. Each dropped
    entry is ``{"selector", "reason"}`` with reason in
    {duplicate_shape, fragile, over_cap}. Pure; no I/O."""
    if not selectors:
        return [], []
    dropped = []
    seen_shapes = set()
    deduped = []
    for sel in selectors:
        if not isinstance(sel, str) or not sel.strip():
            continue
        shape = _record_time_shape_key(sel)
        if shape in seen_shapes:
            dropped.append({"selector": sel, "reason": "duplicate_shape"})
            continue
        seen_shapes.add(shape)
        deduped.append(sel)
    stable = [s for s in deduped if not _is_fragile_selector(s)]
    fragile = [s for s in deduped if _is_fragile_selector(s)]
    if stable:
        for s in fragile:
            dropped.append({"selector": s, "reason": "fragile"})
        ranked = stable
    else:
        ranked = fragile  # non-destructive: keep the best fragile candidates
    kept = ranked[:max_keep]
    for s in ranked[max_keep:]:
        dropped.append({"selector": s, "reason": "over_cap"})
    return kept, dropped


# selector-list roles narrowed at record time. url_attribute is NOT here —
# it is either a scalar (untouched) or a list aligned to row_selectors
# (narrowed in lockstep below).
_NARROW_LIST_ROLES = ("row_selectors", "trigger_selectors")


def narrow_detected_block(block, *, max_keep: int = 3):
    """Apply :func:`narrow_selectors` to the selector-list roles of a freshly
    detected login/download block. Returns ``(new_block, report)`` where
    ``report`` maps role -> dropped list (only roles that dropped something).

    Alignment (merge_learned v3.43.10): if ``url_attribute`` is a parallel
    list index-aligned to ``row_selectors``, its slots are dropped in
    lockstep with the rows they describe — never desynchronized. Scalar
    roles (url_attribute string, tier_label, user_field, …) are untouched.
    Pure: the input block is not mutated."""
    if not isinstance(block, dict):
        return block, {}
    new_block = dict(block)
    report = {}

    rows = block.get("row_selectors")
    url_attr = block.get("url_attribute")
    parallel = (isinstance(rows, list) and isinstance(url_attr, list)
                and len(rows) == len(url_attr) and len(rows) > 0)

    for role in _NARROW_LIST_ROLES:
        val = block.get(role)
        if not isinstance(val, list) or not val:
            continue
        if role == "row_selectors" and parallel:
            # narrow rows, then keep url_attribute aligned to survivors
            kept, dropped = narrow_selectors(val, max_keep=max_keep)
            kept_set = set(kept)
            aligned_urls = [url_attr[i] for i, r in enumerate(val)
                            if r in kept_set]
            # preserve kept order exactly as narrow_selectors returned it
            order = {r: n for n, r in enumerate(kept)}
            pairs = sorted(
                [(r, url_attr[i]) for i, r in enumerate(val) if r in kept_set],
                key=lambda p: order[p[0]])
            new_block[role] = [p[0] for p in pairs]
            new_block["url_attribute"] = [p[1] for p in pairs]
        else:
            kept, dropped = narrow_selectors(val, max_keep=max_keep)
            new_block[role] = kept
        if dropped:
            report[role] = dropped
    return new_block, report

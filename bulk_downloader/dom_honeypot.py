"""DOM-visibility honeypot detection — P5-3.

Catches a class of anti-bot honeypot that R-P5-2's URL scorer cannot:
decoy ``<a>`` tags and buttons that scrape as legitimate download
links but render invisible to real users (CSS-hidden, off-screen,
aria-hidden, tabindex=-1). The wide-scan in
``detect.find_best_download`` happily harvests such elements because
its filters look at text/extension shape, not at user visibility.

Two parallel entrypoints — one for BeautifulSoup elements, one for
Playwright locators — sharing the same vocabularies. Plus a
duplicate-field decoy detector for forms with paired hidden/visible
fields under the same name.

This module is read at call time (not module load) by the env-var
gate in ``detect.find_best_download``; ``BD_DOM_HONEYPOT_FILTER``
controls activation. Default OFF — release ships byte-for-byte
compatible with v3.66.27 for any operator who doesn't opt in.

Design notes (don't re-litigate — these are pinned by tests):

  * ``tabindex="-1"`` is a contributing signal, NOT a standalone
    trigger for links/buttons. Modal close buttons and JS focus-
    managed widgets legitimately use it. For form *inputs*,
    ``deep_detect._is_visible_input`` treats it as standalone —
    that's correct for inputs but wrong for links, hence the
    separate ``is_link_decoy`` implementation.
  * BeautifulSoup-level vs Playwright: BS can only see inline
    ``style=`` and inline ``<style>`` block CSS. Playwright's
    ``is_visible()`` is layout-aware and catches external
    stylesheets too. The two are NOT redundant — the
    ``BD_DOM_HONEYPOT_FILTER=strict`` mode runs both for
    layout-aware confirmation; ``=cheap`` runs only BS.
  * Rules combine by OR (any signal fires), but the (bool, reason)
    return surfaces only the first matched reason so the operator
    log doesn't get noisy.
"""

from __future__ import annotations

import re
from typing import Tuple

# We import HONEYPOT_CSS_HIDDEN from deep_detect to keep one source
# of truth for the inline-style patterns list. The patterns
# themselves are battle-tested in v3.66.10+ for the form-field
# detector — reusing them for link/button detection means improvements
# to either side propagate to both.
from .deep_detect import HONEYPOT_CSS_HIDDEN

__all__ = (
    "TRAP_URL_TERMS",
    "TRAP_TEXT_TERMS",
    "DECOY_LINK_HIDDEN_PATTERNS",
    "is_link_decoy",
    "is_link_decoy_playwright",
    "find_duplicate_field_decoys",
)

# URL-path substrings characteristic of redirect/tracking/affiliate
# decoys. These appear in download-link traps that resolve to ad
# trackers rather than media. Match is case-insensitive substring,
# against the href/data-href/data-url values.
#
# Care taken to avoid common-word collisions: ``/sponsor`` (not
# ``/sport``); ``/affiliate`` (not ``/file``); ``/redirect?`` (with
# the query-mark to avoid matching ``/redirected-content``).
TRAP_URL_TERMS = (
    "/click?",
    "/clk?",
    "/clk/",
    "/go?",
    "/out?",
    "/redirect?",
    "/redir?",
    "/trk?",
    "/track?",
    "/r/click",
    "affiliate",
    "/sponsor",
    "/sponsored",
    "/popup",
    "/interstitial",
    "/adclick",
    "/adframe",
)

# Visible-text vocabulary for decoy buttons. Hits like "Click here
# to download" wrapped around an ad link, or "Sponsored" labels on
# fake download buttons. Substring match against gathered text;
# whole-word checking is not strictly enforced because most of
# these are unambiguous in context.
#
# NOT included intentionally: bare "ad" — too noisy (matches
# "download", "addon", "add to cart", etc).
TRAP_TEXT_TERMS = (
    "sponsored",
    "advertisement",
    "advertise",
    "promoted",
    "popup",
    "interstitial",
    "tracking pixel",
)

# Re-export under the name the spec asked for (C1 module-level
# constants). This is an alias for HONEYPOT_CSS_HIDDEN imported
# from deep_detect — keeping one source of truth.
DECOY_LINK_HIDDEN_PATTERNS = HONEYPOT_CSS_HIDDEN


def _iter_css_rule_blocks(css_text):
    """Yield ``(selector_list, body)`` for each ``… { body }`` CSS block.

    Linear-time, backtracking-free CSS rule walk — mirrors
    ``deep_detect._iter_css_rule_blocks`` but defined here to avoid reaching into
    another module's underscore-private (the same reason the prior regex was
    duplicated). The old ``([^{}]+)\\{([^{}]*)\\}`` regex backtracked O(n²) on a
    long brace-free CSS region; splitting on ``}`` is O(n) and brace-safe. Not a
    real CSS parser; best-effort, as before. See test_deep_detect_redos."""
    if not css_text or "{" not in css_text:
        return
    for chunk in css_text.split("}"):
        ob = chunk.rfind("{")
        if ob == -1:
            continue
        body = chunk[ob + 1:]          # brace-free: no '}' in chunk, after last '{'
        sel = chunk[:ob]
        cut = max(sel.rfind("{"), sel.rfind("}"))  # keep only the run before '{'
        if cut != -1:
            sel = sel[cut + 1:]
        if sel:                        # the old '+' required >=1 selector char
            yield sel, body

# F3 (v3.66.50): conditional at-rule openers. _iter_css_rule_blocks is flat
# (its body is brace-free, so it cannot see inside a nested block), which means a
# rule hidden only inside `@media (...) { .x{display:none} }` escapes the
# css-class consultation entirely. We strip one level of at-rule wrapper so the
# nested rules become visible to the flat walk. The prelude run is BOUNDED
# ({0,512}, not *): real at-rule preludes are short, and the unbounded run let a
# junk CSS blob with many `@media`-but-no-`{` runs backtrack quadratically.
_AT_RULE_OPENER_RE = re.compile(r"@(?:media|supports|container)[^{]{0,512}\{", re.I)


def _flatten_at_rules(css_text: str) -> str:
    """Expose rules nested inside conditional at-rules (``@media`` /
    ``@supports`` / ``@container``) to the flat :func:`_iter_css_rule_blocks`
    walk by removing the at-rule opener token. Not a real CSS parser —
    the now-unbalanced trailing ``}`` of each unwrapped block is
    harmless to the tolerant rule-block regex (it simply fails to
    start a match).

    Rationale: a download link/button hidden only inside an ``@media``
    block is overwhelmingly an anti-bot honeypot — real buttons are
    restyled across breakpoints, not removed. Treating ``@media``-nested
    hiding the same as top-level hiding closes that gap.

    Tradeoff: a genuinely responsive narrow-breakpoint hide is now also
    consulted and could be flagged. Acceptable because (a) the whole
    filter is opt-in (``BD_DOM_HONEYPOT_FILTER``) and fail-open, and
    (b) for form inputs the duplicate-field check still requires a
    *visible* same-name companion before it reports anything.
    """
    if "@" not in css_text:
        return css_text
    return _AT_RULE_OPENER_RE.sub("", css_text)


# F3 (v3.66.50): strict-mode computed-style probe. Playwright's
# locator.is_visible() returns True for opacity:0, off-screen
# transforms, clip-path clipping, and pointer-events:none — the
# element still has a non-empty bounding box. These are exactly the
# states a JS-injected honeypot lands in after hydration. Strict mode
# reads the *computed* style (post-JS) and flags them. getComputedStyle
# normalizes transform to a matrix, so we inspect the matrix components
# (a/d ≈ scale, e/f ≈ translate) rather than the authored function text.
_STRICT_PROBE_JS = r"""
el => {
  const s = getComputedStyle(el);
  const op = parseFloat(s.opacity);
  if (!isNaN(op) && op === 0) return 'computed_opacity_zero';
  if (s.pointerEvents === 'none') return 'computed_pointer_events_none';
  const t = (s.transform || '').toLowerCase();
  if (t && t !== 'none') {
    const m = t.match(/matrix(?:3d)?\(([^)]+)\)/);
    if (m) {
      const n = m[1].split(',').map(x => parseFloat(x));
      if (n.length === 6) {
        if (n[0] === 0 && n[3] === 0) return 'computed_transform_scale_zero';
        if (n[4] <= -9999 || n[5] <= -9999) return 'computed_transform_offscreen';
      } else if (n.length === 16) {
        if (n[0] === 0 && n[5] === 0) return 'computed_transform_scale_zero';
        if (n[12] <= -9999 || n[13] <= -9999) return 'computed_transform_offscreen';
      }
    }
  }
  const c = (s.clipPath || s.webkitClipPath || '').toLowerCase();
  if (c && c !== 'none') {
    if (c.indexOf('inset(100%') !== -1 || c.indexOf('circle(0') !== -1) {
      return 'computed_clip_path';
    }
  }
  return '';
}
""".strip()


def _bs_inline_style_hidden(el) -> bool:
    """True if the element's inline ``style=`` attribute contains
    any of the known hiding patterns. Whitespace-stripped and
    lowercased before substring match — matches
    ``_is_visible_input`` behavior."""
    style = (el.get("style") or "").lower().replace(" ", "")
    for pat in DECOY_LINK_HIDDEN_PATTERNS:
        if pat in style:
            return True
    return False


def _bs_css_class_hidden(el, css_text: str) -> bool:
    """True if any CSS rule in ``css_text`` targets one of this
    element's classes or its id and contains a hiding declaration.
    Mirrors ``deep_detect._is_visible_input`` css_text consultation
    (simple selector list match, no specificity)."""
    if not css_text:
        return False
    # F3: unwrap @media/@supports/@container so rules hidden only
    # inside a conditional block are consulted (the flat rule-block
    # regex can't see into nested braces on its own).
    css_text = _flatten_at_rules(css_text)
    selectors_for_el = []
    el_id = el.get("id")
    if el_id:
        selectors_for_el.append("#" + el_id)
    classes = el.get("class") or []
    if isinstance(classes, str):
        classes = classes.split()
    for cls in classes:
        if isinstance(cls, str) and cls:
            selectors_for_el.append("." + cls)
    if not selectors_for_el:
        return False
    for sel_block, body in _iter_css_rule_blocks(css_text):
        rule_selectors = [s.strip() for s in sel_block.split(",")]
        if not any(s in rule_selectors for s in selectors_for_el):
            continue
        body_l = body.lower().replace(" ", "")
        for pat in DECOY_LINK_HIDDEN_PATTERNS:
            if pat in body_l:
                return True
    return False


def _bs_url_is_trap(el) -> bool:
    """Check this element's URL-bearing attributes for trap terms."""
    for attr in ("href", "data-href", "data-url", "data-src"):
        v = el.get(attr) or ""
        if not isinstance(v, str):
            continue
        v_l = v.lower()
        for term in TRAP_URL_TERMS:
            if term in v_l:
                return True
    return False


def _bs_text_is_trap(el) -> bool:
    """Check this element's visible-text-shaped attributes for
    trap terms. We can't actually call ``el.inner_text()`` on a
    BeautifulSoup element the way Playwright does — instead we
    pull the rendered string content plus the common label
    attributes."""
    parts = []
    try:
        # BS's get_text() reads concatenated descendant text.
        t = el.get_text(" ", strip=True)
        if t:
            parts.append(t)
    except Exception:
        pass
    for attr in ("title", "aria-label", "alt"):
        v = el.get(attr) or ""
        if isinstance(v, str) and v:
            parts.append(v)
    joined = " ".join(parts).lower()
    if not joined:
        return False
    for term in TRAP_TEXT_TERMS:
        if term in joined:
            return True
    return False


def is_link_decoy(el, css_text: str = "") -> Tuple[bool, str]:
    """Classify a BeautifulSoup link/button element as a decoy or
    not. Returns ``(is_decoy, reason)``.

    Combines visibility checks (inline style, ``hidden`` attribute,
    ``aria-hidden``, CSS-text class consultation) with URL-pattern
    and text-pattern matching against the TRAP_* vocabularies.

    Tabindex semantics: ``tabindex="-1"`` alone is NOT a decoy
    trigger for links — it has too many legitimate uses (modal
    close, JS-managed focus). It's a contributing signal: combines
    with any other signal to fire.

    Visibility-class signals (inline style, hidden, aria-hidden,
    CSS class) ARE standalone triggers — an explicitly-hidden
    download link is overwhelmingly an anti-bot honeypot.

    The reason string is one of:
      - ``"inline_style_hidden"``
      - ``"hidden_attribute"``
      - ``"aria_hidden"``
      - ``"css_class_hidden"``
      - ``"trap_url_term"``
      - ``"trap_text_term"``
      - ``"tabindex_minus1_plus_<signal>"`` (combo)
      - ``""`` (not a decoy)
    """
    # Visibility signals — each standalone.
    if _bs_inline_style_hidden(el):
        return True, "inline_style_hidden"
    if el.get("hidden") is not None:
        return True, "hidden_attribute"
    if el.get("aria-hidden") == "true":
        return True, "aria_hidden"
    if _bs_css_class_hidden(el, css_text):
        return True, "css_class_hidden"

    # URL/text trap terms — each standalone.
    if _bs_url_is_trap(el):
        return True, "trap_url_term"
    if _bs_text_is_trap(el):
        return True, "trap_text_term"

    # Tabindex=-1: only a decoy when combined with another signal.
    # Since we already checked the standalone signals above and
    # they didn't fire, the only way tabindex=-1 contributes here
    # is in combination with a weaker hint — currently the only
    # such hint exposed at this layer is the URL/text trap match,
    # which itself is standalone. So in practice tabindex=-1 on
    # its own without any other signal returns "not a decoy".
    # This block exists so future weaker signals can compose with
    # tabindex (e.g. "off-screen positioning hint without an
    # explicit -9999px" -> add tabindex check).
    return False, ""


def is_link_decoy_playwright(locator, strict: bool = False) -> Tuple[bool, str]:
    """Playwright-locator variant of :func:`is_link_decoy`.

    Uses ``locator.is_visible(timeout=300)`` for layout-aware
    visibility — catches external-stylesheet hidden elements that
    the BS variant misses — and ``locator.get_attribute(...)`` for
    the URL/text vocabularies.

    When ``strict`` is True (``BD_DOM_HONEYPOT_FILTER=strict``), a
    final computed-style probe runs via ``locator.evaluate`` to catch
    the post-hydration hidden states that ``is_visible()`` reports as
    visible (a non-empty bounding box satisfies is_visible):
    ``opacity:0``, off-screen ``transform``, ``clip-path`` clipping,
    and ``pointer-events:none`` — the JS-injected-honeypot case. The
    strict reasons are ``computed_opacity_zero``,
    ``computed_pointer_events_none``, ``computed_transform_scale_zero``,
    ``computed_transform_offscreen``, and ``computed_clip_path``.

    The 300ms timeout per locator can add up; callers should keep
    this on the winner-confirmation path rather than every
    candidate. Layered with the cheap BS check, this means most
    candidates short-circuit on the cheap path before paying for
    a layout pass.

    Reasons:
      - ``"not_visible"`` — covers ALL layout-level invisibility
        (display:none, off-screen, etc.) including external
        stylesheets
      - ``"aria_hidden"``
      - ``"trap_url_term"``
      - ``"trap_text_term"``
      - ``""`` (not a decoy)

    Fail-open: any exception from Playwright is treated as "can't
    tell, assume visible" — mirrors P5-2's fail-open principle. A
    locator we can't introspect must not cost the operator a real
    download.
    """
    # Layout-aware visibility — the killer feature of this path.
    try:
        if not locator.is_visible(timeout=300):
            return True, "not_visible"
    except Exception:
        # Fail open — pretend visible.
        pass

    # aria-hidden — Playwright's is_visible() doesn't catch this
    # (the element can be aria-hidden but layout-present), so check
    # explicitly.
    try:
        if locator.get_attribute("aria-hidden") == "true":
            return True, "aria_hidden"
    except Exception:
        pass

    # URL trap terms.
    try:
        for attr in ("href", "data-href", "data-url", "data-src"):
            v = locator.get_attribute(attr) or ""
            if isinstance(v, str):
                v_l = v.lower()
                for term in TRAP_URL_TERMS:
                    if term in v_l:
                        return True, "trap_url_term"
    except Exception:
        pass

    # Text trap terms.
    try:
        text_parts = []
        try:
            t = locator.inner_text(timeout=300) or ""
            if t:
                text_parts.append(t)
        except Exception:
            pass
        for attr in ("title", "aria-label", "alt"):
            try:
                v = locator.get_attribute(attr) or ""
                if isinstance(v, str) and v:
                    text_parts.append(v)
            except Exception:
                pass
        joined = " ".join(text_parts).lower()
        if joined:
            for term in TRAP_TEXT_TERMS:
                if term in joined:
                    return True, "trap_text_term"
    except Exception:
        pass

    # Strict mode: computed-style probe for post-hydration hidden
    # states that is_visible() misses (opacity:0, off-screen transform,
    # clip-path, pointer-events:none). Runs last so the cheap checks
    # short-circuit first. Fail-open on any evaluate error.
    if strict:
        try:
            reason = locator.evaluate(_STRICT_PROBE_JS)
            if reason:
                return True, reason
        except Exception:
            pass

    return False, ""


# F11 (v3.66.50): find_duplicate_field_decoys was extracted to the
# standalone bulk_downloader.duplicate_fields module. Re-exported here
# lazily (PEP 562 module __getattr__) so existing
# ``from .dom_honeypot import find_duplicate_field_decoys`` imports keep
# working without an import cycle (duplicate_fields imports the
# visibility helpers from this module at load time).
def __getattr__(name):
    if name in ("find_duplicate_field_decoys", "_input_is_hidden"):
        from . import duplicate_fields as _df
        return getattr(_df, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

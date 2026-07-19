"""learn_impl.selectors -- verbatim from learn.py (DECOMP-LEAF cut 5)."""

from __future__ import annotations
import re


_CSS_SAFE_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_-]*$")


def _css_escape_ident(s):
    """Escape a class or ID for use as a CSS ident token."""
    if not s:
        return ""
    if _CSS_SAFE_RE.match(s):
        return s
    return re.sub(r"([^A-Za-z0-9_-])", r"\\\1", s)


def _css_escape_attr_value(s):
    """Escape an attribute value to sit safely inside single quotes."""
    if not isinstance(s, str):
        return ""
    return s.replace("\\", "\\\\").replace("'", "\\'")


_CSS_IN_JS_RE = re.compile(
    r"^("
    r"sc-[a-z0-9]{6,}-?\d*|"          # styled-components: sc-slvd0m-5, sc-bdvvtL
    r"css-[a-z0-9]{5,}|"              # emotion: css-1l4w6pd
    r"jsx-\d{8,}|"                    # styled-jsx
    r"_[A-Za-z]{1,3}_[a-z0-9]{5,}|"   # CSS Modules: _btn_a3f4b
    r"[A-Za-z]{4,}_[a-z0-9]{5,}|"     # CSS Modules variant
    r"[A-Z][A-Za-z]+-[a-z]+-\d+|"     # MUI v4 / JSS: MuiButton-root-238
    r"[A-Z][A-Za-z]{1,3}\d{6,}|"      # generic prefixed-digit
    r"jss\d+"                         # bare JSS class: jss12, jss345
    r")$"
)


def _looks_hashed(s):
    """Does `s` look like a build-tool-generated unstable identifier?"""
    if not s: return True
    if _CSS_IN_JS_RE.match(s): return True
    # Random-looking strings: alphanumeric only, ≥6 chars, mixed case, no
    # word boundary cues. Matches things like 'ljLTqn', 'aBxC9z'.
    if 6 <= len(s) <= 14 and s.isalnum() and any(c.isupper() for c in s) and any(c.islower() for c in s):
        # Count vowel/consonant ratio — real words tend toward 30-50% vowels;
        # random strings rarely cluster that way at small lengths.
        vowels = sum(1 for c in s.lower() if c in "aeiou")
        ratio = vowels / len(s)
        if ratio < 0.15 or ratio > 0.6: return True
    return False


_STABLE_CLASS_KW = (
    "login", "signin", "sign-in", "submit", "logon", "log-in", "log_in",
    "btn-primary", "loginbutton", "submitbutton", "loginbtn",
    "auth-submit", "auth-button", "btn-login", "primary-btn",
)


def synthesize_selectors(rec):
    """Given one recorded event, produce CSS selectors ranked best-to-worst.
    Returns a list (possibly empty if nothing stable could be produced).

    Ranking (top = most stable across page rebuilds):
      1. #id (when id doesn't look hashed)
      2. [data-testid='...']
      3. tag[name='...']
      4. input[type='...']  (only for input-type-driven semantics)
      5. input[autocomplete='...']
      6. [aria-label='...']
      7. tag.stable-keyword-class
      8. tag:has-text('short text') (for buttons/anchors)

    v3.65.2: ids/classes routed through _css_escape_ident; attribute
    values through _css_escape_attr_value. Without this, an id like
    "login:form" produced selector "#login:form" which Playwright parses
    as id=login + pseudo-class :form (no element matches). Critically,
    this output is persisted into learned.{login,download}.* and
    replayed forever, so a single corrupt selector breaks every
    subsequent login.
    """
    if not rec: return []
    out = []
    tag = (rec.get("tag") or "").lower()

    # 1. id
    rid = rec.get("id") or ""
    if rid and not _looks_hashed(rid):
        out.append(f"#{_css_escape_ident(rid)}")

    # 2. data-testid (frameworks often guarantee these are stable)
    tid = rec.get("testid") or ""
    if tid:
        out.append(f"[data-testid='{_css_escape_attr_value(tid)}']")

    # 3. name attribute (forms — extremely stable)
    name = rec.get("name") or ""
    if name and tag:
        out.append(f"{tag}[name='{_css_escape_attr_value(name)}']")

    # 4. input type-driven (only for inputs)
    t = rec.get("type") or ""
    if tag == "input" and t in ("email", "password", "submit"):
        out.append(f"input[type='{_css_escape_attr_value(t)}']")

    # 5. autocomplete (browser-spec values are very stable)
    ac = rec.get("autocomplete") or ""
    if ac in ("username", "email", "current-password", "new-password"):
        out.append(f"input[autocomplete='{_css_escape_attr_value(ac)}']")

    # 6. aria-label
    al = rec.get("ariaLabel") or ""
    if al and len(al) < 60:
        out.append(f"[aria-label='{_css_escape_attr_value(al)}']")

    # 7. Class-name with semantic keyword
    cls = rec.get("cls") or ""
    if cls:
        for c in cls.split():
            if _looks_hashed(c): continue
            cl = c.lower()
            if any(kw in cl for kw in _STABLE_CLASS_KW):
                ec = _css_escape_ident(c)
                if tag:
                    out.append(f"{tag}.{ec}")
                else:
                    out.append(f".{ec}")
                break  # one class is enough; stop at the most-relevant one

    # 8. text-based (clickable elements only)
    text = (rec.get("text") or "").strip()
    if text and len(text) < 40 and tag in ("button", "a", "div", "span", "li"):
        safe = _css_escape_attr_value(text)
        out.append(f"{tag}:has-text('{safe}')")
        # Also offer a more-permissive cross-tag version
        out.append(f"[role='button']:has-text('{safe}'), {tag}:has-text('{safe}')")

    # Dedupe while preserving order
    seen = set(); deduped = []
    for s in out:
        if s in seen: continue
        seen.add(s); deduped.append(s)
    return deduped


_SUBMIT_TEXT_KW = ("login", "log in", "sign in", "signin", "submit",
                   "continue", "log on", "logon", "get in", "enter")


def _is_submit_shaped(c):
    if not c:
        return False
    tag = (c.get("tag") or "").lower()
    typ = (c.get("type") or "").lower()
    if tag == "button":
        return True
    if tag == "input" and typ in ("submit", "button", "image"):
        return True
    if (c.get("role") or "").lower() == "button":
        return True
    cls = (c.get("cls") or "").lower()
    if cls and any(kw in cls for kw in _STABLE_CLASS_KW):
        return True
    txt = (c.get("text") or "").strip().lower()
    if txt and any(kw in txt for kw in _SUBMIT_TEXT_KW):
        return True
    return False


_DL_URL_ATTRS = ("dataHref", "href", "dataUrl", "dataSrc", "dataDownload")


_DL_URL_EXT_RE = re.compile(r"\.(mp4|mkv|webm|mov|m4v|ts)(\?|$)", re.I)


def _which_url_attr(rec):
    """Find which attribute on this click carries an actual file URL.
    Returns ('attribute_name_for_selector', value) or (None, None).
    Maps the JS-side camelCase back to the HTML attribute name.

    v3.42.1 bug fix: also check the ancestor URL (recorded by the JS
    recorder walking up the DOM). Users routinely click the inner
    <span> of an <a href="..."> so the click target has no href of
    its own but an ancestor does."""
    js_to_html = {
        "dataHref": "data-href",
        "href": "href",
        "dataUrl": "data-url",
        "dataSrc": "data-src",
        "dataDownload": "data-download",
    }
    for js_name in _DL_URL_ATTRS:
        v = rec.get(js_name) or ""
        if v and (_DL_URL_EXT_RE.search(v) or "download" in v.lower() or "/dl/" in v.lower()):
            return js_to_html[js_name], v
    # v3.42.1: fall back to ancestor URL — set by JS recorder when the
    # click target itself has no URL attribute but an ancestor (usually
    # the wrapping <a>) does.
    anc_attr = rec.get("ancestorAttr") or ""
    anc_url = rec.get("ancestorUrl") or ""
    if anc_attr and anc_url and (
        _DL_URL_EXT_RE.search(anc_url)
        or "download" in anc_url.lower()
        or "/dl/" in anc_url.lower()
    ):
        return anc_attr, anc_url
    return None, None


def _synthesize_download_row_selector(rec):
    """Selectors for a download-row click. Order:
      1. data-signed-url-key='downloads.4K' — the wowgirls pattern, very stable
      2. element[data-href] (or data-url, data-src) — pattern match without value
      3. tag.semantic-class[attr] — combines class with presence-of-attribute
      4. Fall back to standard synthesize_selectors for plain selectors
    The point is to capture the *structural pattern* for download rows so
    it works on every video on the same site, not just the one we recorded."""
    out = []
    tag = (rec.get("tag") or "").lower() or "*"

    # Which URL attribute does this row have?
    url_attr, _ = _which_url_attr(rec)

    # 1. data-signed-url-key (wowgirls/CDN-specific signal — very specific)
    # We don't capture this attribute by default but include the pattern
    # in case the site uses it. Future enhancement: add to the recorder.

    # 2. tag[url_attr] — "any element of this tag with this attribute"
    if url_attr:
        out.append(f"{tag}[{url_attr}]")
        # Also more permissive — any tag with this attribute
        out.append(f"[{url_attr}]")

    # 3. Combine semantic class with attribute presence
    # v3.65.2: escape class names; e.g. a Tailwind-arbitrary class
    # like `clickable-[data-href]` (yes, this happens) would otherwise
    # produce malformed selectors here.
    cls = rec.get("cls") or ""
    if cls and url_attr:
        for c in cls.split():
            if _looks_hashed(c): continue
            cl = c.lower()
            if any(kw in cl for kw in ("download", "clickable", "video", "row")):
                ec = _css_escape_ident(c)
                out.append(f"{tag}.{ec}[{url_attr}]")
                out.append(f".{ec}[{url_attr}]")
                break

    # 4. Fall back to general synthesis (id, testid, name, etc.)
    out.extend(synthesize_selectors(rec))

    # Dedupe preserving order
    seen = set(); deduped = []
    for s in out:
        if s in seen: continue
        seen.add(s); deduped.append(s)
    return deduped

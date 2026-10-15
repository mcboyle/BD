"""Selector playground (Phase 112, Block P — highest-impact support tool).

The operator's debug cycle on a broken site:
  1. Site changed its HTML; existing selector misses
  2. Open page in browser, inspect DOM, copy a new candidate selector
  3. Update the template
  4. Re-run a test URL through BD to see if it still misses
  5. Iterate

Steps 2-4 are slow. The playground compresses them into one HTTP
round-trip:

  • Fetch the page (with the same headers / cookies BD would use)
  • Take a screenshot via Playwright if installed (optional)
  • Accept candidate CSS/XPath selectors from the operator
  • For each candidate, report:
      - Match count
      - First-match outerHTML
      - First-match text
      - First-match attributes
      - Computed XPath of the first match (for cross-reference)
  • Suggest related selectors (siblings, parent, by-class) when the
    operator's candidate misses

Two modes:
  • requests-only: cheap, no JS execution, works without Playwright.
    Selectors run against the static HTML. Good for sites that
    don't lazy-load.
  • playwright: full browser, JS executed, dynamic selectors work.
    Slower (~3s/eval); requires playwright installed + a configured
    browser.

We never persist the operator's queries — pure scratchpad. The
returned data IS the result; nothing leaks to disk.
"""
from __future__ import annotations

import json
import sys
from typing import Optional
from urllib.parse import urlparse, urljoin


_HAS_REQUESTS = False
_HAS_LXML = False
_HAS_CSSSELECT = False
_HAS_PLAYWRIGHT = False

try:
    import requests  # type: ignore
    _HAS_REQUESTS = True
except ImportError:
    pass

try:
    from lxml import html as _lxml_html  # type: ignore
    _HAS_LXML = True
except ImportError:
    try:
        # Fall back to BeautifulSoup for CSS, but XPath unavailable
        from bs4 import BeautifulSoup  # type: ignore
        _HAS_BS4 = True
    except ImportError:
        _HAS_BS4 = False

try:
    import cssselect  # noqa: F401
    _HAS_CSSSELECT = True
except ImportError:
    pass


def is_available() -> dict:
    """Report which backends are reachable."""
    avail = {"requests": _HAS_REQUESTS, "lxml": _HAS_LXML,
             "cssselect": _HAS_CSSSELECT, "playwright": False}
    try:
        from playwright.sync_api import sync_playwright  # noqa: F401
        avail["playwright"] = True
    except ImportError:
        pass
    return avail


def _css_to_xpath_simple(css: str) -> Optional[str]:
    """Tiny CSS-to-XPath translator for the common cases the operator
    actually types. Used as a fallback when the `cssselect` package
    isn't installed. Handles:

      tag, .class, #id, tag.class, tag#id, tag.class.class2,
      tag[attr], tag[attr=value], descendant (space), child (>)

    Returns None for unsupported syntax — caller falls back to error.
    """
    if not css or not css.strip():
        return None
    import re as _re
    # Split into simple selectors and combinators. A quoted attribute value
    # stays in its token: a[href='/a b'] holds a space that is no combinator.
    tokens: list[str] = []
    cur = ""
    quote = ""
    depth = 0
    for ch in css.strip():
        if quote:
            cur += ch
            if ch == quote:
                quote = ""
        elif ch in ("'", '"') and depth:
            quote = ch
            cur += ch
        elif ch == "[":
            depth += 1
            cur += ch
        elif ch == "]":
            depth -= 1
            cur += ch
        elif ch in (">", " ", "+", "~"):
            if cur:
                tokens.append(cur)
                cur = ""
            tokens.append(ch)
        else:
            cur += ch
    if cur:
        tokens.append(cur)
    xpath_parts: list[str] = []
    last_combinator = None
    for tok in tokens:
        if tok == ">":
            if last_combinator == ">" or not xpath_parts:
                return None  # "a > > b", or a selector that starts with ">"
            last_combinator = ">"
            continue
        if tok == " ":
            # whitespace around ">" is not a descendant combinator
            if last_combinator is None:
                last_combinator = " "
            continue
        if tok in ("+", "~"):
            return None  # unsupported
        # Parse one simple selector: tag.class.class#id[attr=value]. Anything
        # this grammar does not consume (a pseudo-class, an operator such as
        # ^=) is unsupported: ignoring it would count a different selector.
        m = _re.fullmatch(
            r"([a-zA-Z][a-zA-Z0-9_-]*|\*)?"
            r"((?:\.[a-zA-Z0-9_-]+|#[a-zA-Z0-9_-]+"
            r"|\[[a-zA-Z_][a-zA-Z0-9_:-]*(?:=(?:'[^']*'|\"[^\"]*\"|[a-zA-Z0-9_-]+))?\])*)",
            tok)
        if not m or not tok:
            return None
        tag = m.group(1) or "*"
        rest = m.group(2)
        preds = []
        for kind, name, attr, has_val, val in _re.findall(
                r"([.#])([a-zA-Z0-9_-]+)"
                r"|\[([a-zA-Z_][a-zA-Z0-9_:-]*)(=(?:'([^']*)'|\"[^\"]*\"|[a-zA-Z0-9_-]+))?\]",
                rest):
            if kind == ".":
                preds.append(f"contains(concat(' ', normalize-space(@class), ' '), ' {name} ')")
            elif kind == "#":
                preds.append(f"@id='{name}'")
            elif has_val:
                raw = has_val[1:]
                if raw[:1] in ("'", '"'):
                    raw = raw[1:-1]
                if "'" not in raw:
                    preds.append(f"@{attr}='{raw}'")
                elif '"' not in raw:
                    preds.append(f'@{attr}="{raw}"')
                else:
                    return None  # XPath 1.0 has no escape for both quotes
            else:
                preds.append(f"@{attr}")
        step = tag
        if preds:
            step += "[" + " and ".join(preds) + "]"
        xpath_parts.append(("/" if last_combinator == ">" else "//") + step)
        last_combinator = None
    if last_combinator == ">":
        return None  # dangling "a >"
    return "".join(xpath_parts) if xpath_parts else None

# ─── Fetch ────────────────────────────────────────────────────────────

_MAX_REDIRECTS = 5


def _host_public(url: str):
    """(ok, reason) for url's host via the single canonical SSRF predicate
    (provider_resolve_impl._common._is_safe_public_host, which rejects loopback/private/
    link-local/reserved/multicast + RFC 6598 CGNAT). Refuses non-http(s) schemes and
    fails closed on a resolution error (the predicate resolves + fails closed itself)."""
    from bulk_downloader.provider_resolve_impl._common import _is_safe_public_host
    p = urlparse(url)
    if p.scheme not in ("http", "https"):
        return False, f"scheme must be http or https (got {p.scheme or 'none'!r})"
    host = p.hostname or ""
    if not host:
        return False, "invalid host"
    return _is_safe_public_host(host)


def _host_by_name(url: str) -> bool:
    """True when a connect to ``url`` looks its host up by name, so the address
    judged before the send must be the one connected to (DNS-rebinding pin)."""
    import ipaddress
    try:
        ipaddress.ip_address(urlparse(url).hostname or "")
    except ValueError:
        return True
    return False


def fetch_page(url: str, *, headers: Optional[dict] = None,
              cookies: Optional[dict] = None,
              timeout: int = 30,
              use_playwright: bool = False) -> dict:
    """Fetch `url` and return {ok, status, html, final_url, error}.
    When `use_playwright=True`, the page is rendered with JS executed
    so dynamic selectors work."""
    # v3.66.552 (F-CBD12-02): this is a route-reachable server-side fetch that returns the
    # response body -- a full SSRF read primitive. Validate the target host against the
    # single canonical predicate BEFORE either branch and refuse a non-global target
    # (loopback/private/link-local/CGNAT), returning the module's blocked shape with no
    # body. Redirects are re-validated per hop below (requests) / by a route guard
    # (playwright) so a public URL can't 30x us to an internal host.
    _ok, _why = _host_public(url)
    if not _ok:
        return {"ok": False, "error": f"blocked: {_why}", "html": ""}
    if use_playwright:
        return _fetch_playwright(url, timeout=timeout, headers=headers,
                                cookies=cookies)
    if not _HAS_REQUESTS:
        return {"ok": False, "error": "requests not installed", "html": ""}
    hdrs = {"User-Agent": "BulkDownloader-Playground/1.0",
            "Accept": "text/html,application/xhtml+xml"}
    if headers:
        hdrs.update(headers)
    from bulk_downloader.provider_resolve_impl._common import SSRFBlocked
    try:
        # Follow redirects manually, re-validating the host at every hop, so a public
        # first hop can't 302 us to an internal address (allow_redirects=True did).
        cur = url
        for _ in range(_MAX_REDIRECTS + 1):
            _hop_ok, _hop_why = _host_public(cur)
            if not _hop_ok:
                raise SSRFBlocked(_hop_why)
            if _host_by_name(cur):
                # Rebind pin: connect only to an address judged at connect time.
                from bulk_downloader import deep_http as _dh
                with _dh.pinned_session(_dh.public_address_allowed) as session:
                    r = session.get(cur, headers=hdrs, cookies=cookies or {},
                                    timeout=timeout, allow_redirects=False)
            else:
                r = requests.get(cur, headers=hdrs, cookies=cookies or {},
                                timeout=timeout, allow_redirects=False)
            loc = r.headers.get("Location") if getattr(r, "headers", None) else None
            if r.status_code in (301, 302, 303, 307, 308) and loc:
                cur = urljoin(cur, loc)
                continue
            return {"ok": r.ok, "status": r.status_code,
                    "html": r.text, "final_url": r.url,
                    "headers": dict(r.headers), "error": ""}
        raise SSRFBlocked("too many redirects")
    except SSRFBlocked as e:
        return {"ok": False, "error": f"blocked: {e}", "html": ""}
    except Exception as e:
        return {"ok": False, "error": str(e)[:300], "html": ""}


def _fetch_playwright(url: str, *, timeout: int = 30,
                     headers: Optional[dict] = None,
                     cookies: Optional[dict] = None) -> dict:
    """Playwright fetch with JS execution. Best-effort — falls back
    to requests-style failure shape on any error."""
    try:
        from . import cloak as _cloak
    except ImportError:
        return {"ok": False, "error": "browser backend not available",
                "html": ""}
    browser = pw = None
    try:
        # Canonical backend (CloakBrowser when resolved, else vanilla
        # Playwright) so the selector test renders like a real cloaked capture.
        browser, pw, backend = _cloak.launch_browser(headless=True)
        ctx = browser.new_context()
        if cookies:
            ctx.add_cookies([{"name": k, "value": v,
                             "url": url} for k, v in cookies.items()])
        page = ctx.new_page()

        # v3.66.552 (F-CBD12-02): SSRF guard for the browser branch -- abort any request
        # (initial nav, redirect hop, or subresource) whose host is non-global, so a
        # redirect from a public page can't reach an internal service. fetch_page already
        # validated the initial host; this re-validates every hop the browser makes.
        def _guard_route(route):
            try:
                _rok, _ = _host_public(route.request.url)
            except Exception:
                _rok = False
            if _rok:
                route.continue_()
            else:
                route.abort()
        page.route("**/*", _guard_route)

        if headers:
            page.set_extra_http_headers(headers)
        resp = page.goto(url, timeout=timeout * 1000)
        try:
            page.wait_for_load_state("networkidle", timeout=5000)
        except Exception:
            pass  # networkidle isn't required
        html = page.content()
        final_url = page.url
        # Report the real navigation status: a 404/500 page is not a success.
        # goto() returns None only for same-document navigations (no response).
        if resp is None:
            ok, status = True, 200
        else:
            status = resp.status
            ok = 200 <= status < 400
        return {"ok": ok, "status": status, "html": html,
                "final_url": final_url, "error": ""}
    except Exception as e:
        return {"ok": False, "error": str(e)[:300], "html": ""}
    finally:
        try:
            if browser is not None:
                browser.close()
        except Exception:
            pass
        if pw:
            try:
                pw.stop()
            except Exception:
                pass


# ─── Selector evaluation ──────────────────────────────────────────────

from .selector_check import _has_wanted_text, _split_has_text


def evaluate_selectors(html: str, selectors: list) -> list:
    """For each selector, return {selector, syntax, count, sample,
    sample_text, sample_attrs, error}.

    Auto-detects CSS vs XPath: a selector starting with '/', './/' or '('
    is XPath, otherwise CSS. XPath requires lxml; CSS works via
    lxml's cssselect or BeautifulSoup."""
    if not _HAS_LXML:
        return [{"selector": s, "error": "lxml not installed; install lxml for selector evaluation"}
                for s in selectors]
    if not html:
        return [{"selector": s, "error": "empty html"} for s in selectors]
    try:
        tree = _lxml_html.fromstring(html)
    except Exception as e:
        return [{"selector": s, "error": f"parse: {e}"} for s in selectors]
    results = []
    for sel in selectors:
        sel_str = (sel or "").strip()
        if not sel_str:
            results.append({"selector": sel, "error": "empty selector"})
            continue
        # "(//a)[1]" is XPath too: no CSS selector starts with "(".
        syntax = "xpath" if sel_str.startswith(("/", ".//", "(")) else "css"
        try:
            if syntax == "xpath":
                matches = tree.xpath(sel_str)
            else:
                split = _split_has_text(sel_str)
                if split is None:
                    results.append({"selector": sel_str, "syntax": "css",
                                    "status": "review", "count": None,
                                    "error": ""})
                    continue
                css_sel, wanted = split
                if _HAS_CSSSELECT:
                    matches = tree.cssselect(css_sel)
                else:
                    # No cssselect package — translate to XPath
                    xp = _css_to_xpath_simple(css_sel)
                    if not xp:
                        results.append({"selector": sel_str, "syntax": "css",
                                        "count": 0,
                                        "error": "cssselect not installed and CSS too complex for fallback"})
                        continue
                    matches = tree.xpath(xp)
                if wanted:
                    matches = [
                        el for el in matches
                        if hasattr(el, "tag")
                        and _has_wanted_text(el.text_content() or "", wanted)
                    ]
            count = len(matches)
            entry = {"selector": sel_str, "syntax": syntax,
                     "count": count, "error": ""}
            if count > 0:
                first = matches[0]
                if hasattr(first, "tag"):
                    entry["sample"] = _lxml_html.tostring(first,
                        encoding="unicode")[:500]
                    entry["sample_text"] = (first.text_content() or "").strip()[:300]
                    entry["sample_attrs"] = dict(first.attrib or {})
                    entry["sample_xpath"] = tree.getroottree().getpath(first)
                else:
                    # XPath text() / attribute() match returns a string
                    entry["sample"] = str(first)[:500]
                    entry["sample_text"] = str(first)[:300]
                    entry["sample_attrs"] = {}
            results.append(entry)
        except Exception as e:
            results.append({"selector": sel_str, "syntax": syntax,
                            "count": 0, "error": str(e)[:200]})
    return results


# ─── Suggest related selectors ────────────────────────────────────────

def suggest_alternatives(html: str, missing_selector: str,
                        *, limit: int = 6) -> list:
    """When a selector misses, suggest plausible neighbors.

    Strategy: pick the most-common tag at the depth implied by the
    selector + classes that appear most in the doc. Returns a list
    of candidate selectors, ordered by frequency DESC.

    Best-effort heuristic, not exhaustive search."""
    if not _HAS_LXML or not html:
        return []
    try:
        tree = _lxml_html.fromstring(html)
    except Exception:
        return []
    # Find the tag at the end of the missing selector
    tag_hint = ""
    for tok in reversed((missing_selector or "").replace(">", " ").split()):
        if tok and tok[0].isalpha():
            tag_hint = tok.lstrip(".#").split(".")[0].split("[")[0]
            break
    # Most common classes/IDs on tags of that name
    candidates: dict = {}
    try:
        elements = tree.xpath(f"//{tag_hint}") if tag_hint else tree.xpath("//*")
    except Exception:
        elements = []
    for el in elements[:5000]:
        cls = el.get("class", "")
        if cls:
            for c in cls.split():
                c = c.strip()
                if c and len(c) > 1:
                    key = f"{el.tag}.{c}" if tag_hint else f".{c}"
                    candidates[key] = candidates.get(key, 0) + 1
        eid = el.get("id", "")
        if eid:
            key = f"{el.tag}#{eid}" if tag_hint else f"#{eid}"
            candidates[key] = candidates.get(key, 0) + 1
    pairs = sorted(candidates.items(), key=lambda kv: kv[1], reverse=True)
    return [{"selector": k, "matches": v} for k, v in pairs[:limit]]


# ─── One-call playground entry point ──────────────────────────────────

def playground(url: str, selectors: list, *,
              cookies: Optional[dict] = None,
              use_playwright: bool = False,
              suggest_on_miss: bool = True) -> dict:
    """Run the full playground flow: fetch + evaluate + (optionally)
    suggest alternatives for misses.

    Returns a dict the UI renders directly."""
    fetch = fetch_page(url, cookies=cookies, use_playwright=use_playwright)
    out = {
        "url": url,
        "final_url": fetch.get("final_url", ""),
        "fetch_ok": fetch.get("ok", False),
        "fetch_error": fetch.get("error", ""),
        "status": fetch.get("status"),
        "selectors": [],
    }
    if not fetch.get("ok"):
        return out
    html = fetch.get("html", "")
    results = evaluate_selectors(html, selectors)
    # Augment misses with alternatives
    if suggest_on_miss:
        for r in results:
            if r.get("count", 0) == 0 and not r.get("error"):
                r["suggestions"] = suggest_alternatives(html, r["selector"])
    out["selectors"] = results
    return out

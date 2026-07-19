from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional, Tuple
from urllib.parse import (
    parse_qsl, urlencode, urljoin, urlparse, urlunparse,
)


def _parse_csp_policy(policy_text: str) -> dict:
    """Parse a CSP `content` attribute or response-header value into
    a {directive: [source, source, ...]} dict. Sources are returned
    as raw tokens — keyword quotes, wildcards, hosts all included.
    Whitespace around tokens is trimmed.

    Returns an empty dict on empty/non-string input.
    """
    out: Dict[str, List[str]] = {}
    if not isinstance(policy_text, str) or not policy_text.strip():
        return out
    # CSP separator is ';'. Each chunk is `directive source source ...`.
    for chunk in policy_text.split(";"):
        chunk = chunk.strip()
        if not chunk:
            continue
        parts = chunk.split()
        if not parts:
            continue
        directive = parts[0].lower()
        sources = [s.strip() for s in parts[1:] if s.strip()]
        out[directive] = sources
    return out


def _extract_csp_from_html(html: str) -> Optional[dict]:
    """Find a `<meta http-equiv="Content-Security-Policy" content="...">`
    tag and return its parsed directives. Returns None when no CSP
    meta tag is present, an empty-content one is present (no signal),
    or the html isn't parseable.

    Note: only the FIRST CSP meta tag is honored. Spec allows multiple
    (intersection semantics), but in practice this is rare and would
    require a more sophisticated merger.
    """
    try:
        from bs4 import BeautifulSoup
    except ImportError:
        return None
    if not isinstance(html, str) or not html.strip():
        return None
    try:
        soup = BeautifulSoup(html, "html.parser")
    except Exception:
        return None
    for meta in soup.find_all("meta"):
        equiv = (meta.get("http-equiv") or "").lower()
        if equiv != "content-security-policy":
            continue
        content = meta.get("content") or ""
        if not content.strip():
            continue
        directives = _parse_csp_policy(content)
        if not directives:
            continue
        return {"policy": content, "directives": directives}
    return None


def _extract_csp_from_headers(headers: Optional[dict]) -> Optional[dict]:
    """v3.66.20 — extract CSP from response headers.

    HTTP servers may send CSP as:
      • ``Content-Security-Policy: <policy>``                 (enforcing)
      • ``Content-Security-Policy-Report-Only: <policy>``     (advisory)

    A single header value may be a comma-separated list of policies
    per the spec; each is parsed independently and the directive
    sources are intersected (since the browser must satisfy ALL).

    Both header names are considered. Enforcing takes priority over
    report-only for annotation purposes — a report-only violation is
    just telemetry, not a load failure — but if only report-only is
    present, we surface that.

    Returns the same shape as ``_extract_csp_from_html``:
        {"policy": "<canonical text>", "directives": {dir: [src...]}}
    or None when no header CSP is present or parseable. The
    ``policy`` field is the raw concatenation of headers found
    (for transparency / debugging); the actual matching engine
    consults ``directives``.

    The headers mapping is treated case-insensitively.
    """
    if not headers:
        return None
    # Build a case-insensitive view; tolerate both dict-like and
    # multi-value containers (httpx Headers has .get_list; we don't
    # depend on it — just take the value directly).
    enforcing_vals: List[str] = []
    report_vals: List[str] = []
    try:
        items = headers.items() if hasattr(headers, "items") else list(headers)
    except Exception:
        return None
    for k, v in items:
        if not isinstance(k, str):
            continue
        kl = k.lower()
        if kl == "content-security-policy":
            if isinstance(v, str) and v.strip():
                enforcing_vals.append(v)
        elif kl == "content-security-policy-report-only":
            if isinstance(v, str) and v.strip():
                report_vals.append(v)

    chosen_vals = enforcing_vals if enforcing_vals else report_vals
    if not chosen_vals:
        return None

    # Each header value is a comma-separated list of policies; each
    # policy is a semicolon-separated list of directives. Per spec,
    # the browser enforces every policy independently — a candidate
    # must pass ALL of them. We model that by intersecting the
    # source token sets per directive across all policies. If a
    # directive only appears in some policies, the policies missing
    # it implicitly allow anything for that directive (default-src
    # would apply at match time), so we union those by leaving the
    # directive as the union — but at match time we evaluate every
    # listed source. The simpler model: collect every policy's
    # directives separately, then take the INTERSECTION of allowed
    # sources for any directive present in 2+ policies.
    policies: List[dict] = []
    for raw in chosen_vals:
        for policy_text in raw.split(","):
            policy_text = policy_text.strip()
            if not policy_text:
                continue
            d = _parse_csp_policy(policy_text)
            if d:
                policies.append(d)
    if not policies:
        return None
    if len(policies) == 1:
        merged = policies[0]
    else:
        # Per-directive intersection across policies that mention it.
        merged: Dict[str, List[str]] = {}
        all_dirs = set()
        for p in policies:
            all_dirs.update(p.keys())
        for d in all_dirs:
            source_sets = [set(p[d]) for p in policies if d in p]
            if not source_sets:
                continue
            common = set.intersection(*source_sets) \
                if len(source_sets) > 1 else source_sets[0]
            # Preserve original order from the first policy that
            # carried the directive.
            for p in policies:
                if d in p:
                    merged[d] = [s for s in p[d] if s in common]
                    break

    return {
        "policy": " , ".join(chosen_vals),
        "directives": merged,
        "from": "header_enforcing" if enforcing_vals else "header_report_only",
    }


def _csp_source_matches(source_token: str, candidate_url: str,
                        base_url: str = "") -> bool:
    """Return True iff `candidate_url` matches the CSP source token.

    Supports:
      • 'self'          — same scheme + host as base_url
      • 'none'          — never matches anything (handled at caller)
      • '*'             — matches any scheme/host
      • 'https:' / 'http:' — scheme-only allow
      • 'cdn.foo.com'   — host match (any scheme that's not blocked)
      • '*.foo.com'     — host suffix wildcard
      • 'https://cdn.foo.com' — scheme + host match

    Quoted-string keywords other than 'self' / 'none' (e.g.
    'unsafe-inline', 'nonce-xyz', 'sha256-...') always return False
    here — they don't apply to URL allow-listing.
    """
    try:
        from urllib.parse import urlparse
    except ImportError:
        return False
    if not candidate_url:
        return False
    tok = source_token.strip()
    if not tok:
        return False
    # Quoted keywords
    if tok.startswith("'") and tok.endswith("'"):
        kw = tok[1:-1].lower()
        if kw == "self":
            if not base_url:
                return False
            b = urlparse(base_url); c = urlparse(candidate_url)
            return (b.scheme == c.scheme
                    and b.hostname == c.hostname)
        # 'none' is handled at the caller; everything else is non-URL
        return False
    # '*' matches everything
    if tok == "*":
        return True
    # Scheme-only: "https:" / "http:" / "data:" / "blob:"
    if tok.endswith(":") and "/" not in tok:
        c = urlparse(candidate_url)
        return c.scheme == tok[:-1]
    # Full scheme://host or just a host (possibly with wildcard)
    try:
        c = urlparse(candidate_url)
    except Exception:
        return False
    # Parse the token. If it has '://', honor the scheme part.
    if "://" in tok:
        try:
            t = urlparse(tok)
        except Exception:
            return False
        if t.scheme and c.scheme != t.scheme:
            return False
        host_pat = (t.hostname or "")
    else:
        host_pat = tok
    chost = c.hostname or ""
    # Wildcard host: *.foo.com matches a.foo.com, b.x.foo.com, etc.
    # Does NOT match foo.com itself (per CSP spec).
    if host_pat.startswith("*."):
        suffix = host_pat[1:]  # ".foo.com"
        return chost.endswith(suffix) and chost != suffix.lstrip(".")
    return chost == host_pat


def _candidate_violates_csp(candidate_url: str, csp_directives: dict,
                            base_url: str = "") -> Optional[str]:
    """Return a human-readable violation reason if `candidate_url`
    violates the CSP `media-src` / `connect-src` / `default-src`
    directive chain. Returns None when no violation.

    Resolution order: media-src first (the directive that actually
    governs <video>/<audio> src); fall back to connect-src (for
    fetch()-driven loads); then default-src as the final catch-all.
    If none of those three directives are present, there's nothing
    to violate against — returns None.
    """
    if not candidate_url or not csp_directives:
        return None
    # Pick the directive that applies. Per spec, the FIRST present in
    # the order media-src → connect-src → default-src is the only
    # one consulted for media; we don't intersect.
    sources = None
    used = None
    for d in ("media-src", "connect-src", "default-src"):
        if d in csp_directives:
            sources = csp_directives[d]
            used = d
            break
    if sources is None:
        return None
    # `'none'` is a special case: empty allow-list disallows everything.
    for s in sources:
        if s.strip() == "'none'":
            return (f"CSP {used}='none' disallows all media URLs; "
                    f"candidate {candidate_url[:60]} is blocked")
    # Match against every source token; any match → allowed.
    for s in sources:
        if _csp_source_matches(s, candidate_url, base_url=base_url):
            return None
    # No match → violation.
    return (f"CSP {used} would block {candidate_url[:60]} "
            f"(allowed sources: {' '.join(sources[:4])}"
            f"{'…' if len(sources) > 4 else ''})")


def _candidate_is_mixed_content(candidate_url: str,
                                base_url: str) -> bool:
    """Return True iff base_url is https:// but candidate_url is
    http://. Browsers block this by default and the candidate will
    silently fail to load via the player.
    """
    if not candidate_url or not base_url:
        return False
    try:
        from urllib.parse import urlparse
        b = urlparse(base_url); c = urlparse(candidate_url)
    except Exception:
        return False
    return b.scheme == "https" and c.scheme == "http"


def _apply_csp_annotations(cands: List[dict],
                           csp: Optional[dict],
                           base_url: str,
                           warnings_sink: List[str]) -> None:
    """Walk candidates, annotate any that violate CSP or are mixed-
    content, and add session-level warning strings to warnings_sink
    (which P17's _build_disclaimers will pick up and structure).

    Side effects only — does NOT reorder, remove, or score-penalize
    candidates. CSP/mixed-content are informational signals; the
    operator may legitimately want to fetch a violating URL outside
    the browser context (e.g. inspecting a manifest body or pulling
    a same-domain mirror that the page's CSP didn't list). We just
    surface the signal; the ranking question is left to score-based
    heuristics that already exist.

    Per-candidate annotation:
      • c["csp_violation"]  = True iff the candidate URL would be
                              blocked by the page's CSP policy.
      • c["mixed_content"]  = True iff candidate is http:// on an
                              https:// page.
      • c["warnings"]       gets a human-readable line per violation.

    Session-level: one summary warning per class is appended to
    warnings_sink so P17 emits a structured disclaimer.
    """
    if not cands:
        return
    csp_directives = (csp or {}).get("directives") or {}
    csp_violations = 0
    mixed_violations = 0
    for c in cands:
        url = c.get("url")
        if not url:
            continue
        # Mixed content first (cheaper check, doesn't need CSP at all)
        if _candidate_is_mixed_content(url, base_url):
            c["mixed_content"] = True
            c.setdefault("warnings", []).append(
                "mixed_content: candidate is http:// but page is https://; "
                f"browser will block this URL")
            mixed_violations += 1
        # CSP — only checked when we have a parsed policy
        if csp_directives:
            reason = _candidate_violates_csp(
                url, csp_directives, base_url=base_url)
            if reason:
                c["csp_violation"] = True
                c.setdefault("warnings", []).append(
                    f"csp_violation: {reason}")
                csp_violations += 1
    # Session-level summary warnings (one per class)
    if csp_violations:
        warnings_sink.append(
            f"csp_violation: {csp_violations} candidate(s) would be "
            f"blocked by the page's Content-Security-Policy")
    if mixed_violations:
        warnings_sink.append(
            f"mixed_content: {mixed_violations} candidate(s) use http:// "
            f"on an https:// page; browsers block these by default")

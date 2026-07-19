#!/usr/bin/env python3
"""scrub_recon.py — strip credentials/session data from bd-recon captures.

These captures are from REAL authenticated member sessions. Before any
of them can serve as a committed test fixture, every credential-bearing
field must be replaced with a placeholder. This script removes:

  * top-level cookies (raw + parsed), local_storage, session_storage
  * per-network-log-entry request/response headers named like auth or
    cookie carriers (Cookie, Set-Cookie, Authorization, X-*-Token, etc.)
  * request_body / response_body (may carry tokens, PII, signed URLs
    with embedded auth) — replaced with a length marker so extractors
    that only care about *shape* still see something
  * any query-string component that looks like a key/token/signature

What it KEEPS (the extraction-relevant structure):
  * url paths + hosts (with token-like query params redacted)
  * content types, status codes, durations
  * player_elements (DOM structure)
  * script_tags_of_interest CONTENT for application/ld+json and inline
    config blobs (these are page content, not credentials) — but run
    through the same query-redactor in case they embed signed URLs
  * js_globals (config blobs — redacted for token-like values)
  * discovered_media_urls (paths kept; signed query params redacted)

Usage:
    python3 scrub_recon.py <in_dir> <out_dir>
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path


# Header names whose VALUES are credentials — drop the value entirely.
# Redaction primitives now live in bulk_downloader.capture_redact so the
# capture-TIME path (session_capture, A-T1) and this post-hoc scrubber
# share one source of truth. Local _-aliases keep the rest of this
# script (scrub_capture etc.) unchanged.
from bulk_downloader.capture_redact import (  # noqa: E402
    PLACEHOLDER as _PLACEHOLDER,
    SENSITIVE_HEADER as _SENSITIVE_HEADER,
    SENSITIVE_QS_KEY as _SENSITIVE_QS_KEY,
    redact_query as _redact_query,
    scrub_headers as _scrub_headers,
    body_marker as _body_marker,
    scrub_globals as _scrub_globals,
)


# Regexes for the last-line inline-content scrub (F-TOOLS_OTHER07-01): embedded
# signed URLs, and floor-keyed credential values in JSON / JS-config / query form.
_CONTENT_URL_RE = re.compile(r"https?://[^\s\"'<>()]+")
_CONTENT_JSON_KV_RE = re.compile(r'("([A-Za-z_][\w.\-]{0,60})"\s*:\s*")([^"]*)(")')
_CONTENT_FORM_KV_RE = re.compile(r'([A-Za-z_][\w.\-]{0,60})=([^"\',;}\s&]+)')


def _redact_script_content(s: str) -> str:
    """Scrub an inline script_tags_of_interest[].content blob before it becomes a
    committed fixture: redact embedded signed URLs AND floor-keyed credential
    values (apiKey / token / bearer / signature / ... in JSON, JS-config, or
    query form), reusing the same query-redactor + I0008 floor classifier the
    module docstring already promises is applied. VALUES only -- page structure
    is preserved. (F-TOOLS_OTHER07-01)"""
    if not isinstance(s, str) or not s:
        return s
    s = _CONTENT_URL_RE.sub(lambda m: _redact_query(m.group(0)), s)
    s = _CONTENT_JSON_KV_RE.sub(
        lambda m: (m.group(1) + _PLACEHOLDER + m.group(4))
        if _SENSITIVE_QS_KEY.search(m.group(2)) else m.group(0), s)
    s = _CONTENT_FORM_KV_RE.sub(
        lambda m: (m.group(1) + "=" + _PLACEHOLDER)
        if _SENSITIVE_QS_KEY.search(m.group(1)) else m.group(0), s)
    return s


def scrub_capture(d: dict) -> dict:
    out = dict(d)

    # 1. Top-level credential stores — drop entirely.
    for k in ("cookies", "local_storage", "session_storage"):
        if k in out:
            out[k] = _PLACEHOLDER

    # 2. Network log — scrub headers + bodies, redact URLs.
    nl = out.get("network_log")
    if isinstance(nl, list):
        scrubbed = []
        for entry in nl:
            if not isinstance(entry, dict):
                scrubbed.append(entry)
                continue
            e = dict(entry)
            if "url" in e:
                e["url"] = _redact_query(e["url"])
            if "request_headers" in e:
                e["request_headers"] = _scrub_headers(e["request_headers"])
            if "response_headers" in e:
                e["response_headers"] = _scrub_headers(e["response_headers"])
            if "request_body" in e:
                e["request_body"] = _body_marker(e.get("request_body"))
            if "response_body" in e:
                e["response_body"] = _body_marker(e.get("response_body"))
            scrubbed.append(e)
        out["network_log"] = scrubbed

    # 3. discovered_media_urls — redact signed query params (keep path).
    media = out.get("discovered_media_urls")
    if isinstance(media, list):
        for m in media:
            if isinstance(m, dict) and "url" in m:
                m["url"] = _redact_query(m["url"])
                for ex in (m.get("extras") or []):
                    if isinstance(ex, dict) and "from" in ex:
                        ex["from"] = _redact_query(ex["from"])

    # 4. js_globals — redact token-like URL query strings within.
    if "js_globals" in out:
        out["js_globals"] = _scrub_globals(out["js_globals"])

    # 5. script_tags_of_interest — keep content (page data) but run BOTH the
    #    inline content and the src through the query-redactor + floor classifier,
    #    so an embedded signed URL / API key / bearer token cannot ride into a
    #    committed fixture. This matches the module docstring's promise. (F-TOOLS_OTHER07-01)
    sti = out.get("script_tags_of_interest")
    if isinstance(sti, list):
        for s in sti:
            if isinstance(s, dict):
                if s.get("src"):
                    s["src"] = _redact_query(s["src"])
                if isinstance(s.get("content"), str) and s["content"]:
                    s["content"] = _redact_script_content(s["content"])

    # 6. api_probes — redact URLs.
    ap = out.get("api_probes")
    if isinstance(ap, dict):
        for v in ap.values():
            if isinstance(v, dict) and "url" in v:
                v["url"] = _redact_query(v["url"])

    # 7. referrer / url top-level — redact query.
    for k in ("url", "referrer"):
        if isinstance(out.get(k), str):
            out[k] = _redact_query(out[k])

    return out


def main():
    if len(sys.argv) != 3:
        print(__doc__)
        sys.exit(2)
    in_dir, out_dir = Path(sys.argv[1]), Path(sys.argv[2])
    out_dir.mkdir(parents=True, exist_ok=True)
    n = 0
    for f in sorted(in_dir.glob("*.json")):
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
        except Exception as e:
            print(f"  skip {f.name}: {e}")
            continue
        scrubbed = scrub_capture(d)
        (out_dir / f.name).write_text(
            json.dumps(scrubbed, indent=2), encoding="utf-8")
        n += 1
    print(f"scrubbed {n} capture(s) -> {out_dir}")


if __name__ == "__main__":
    main()

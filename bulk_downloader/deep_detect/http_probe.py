from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional, Tuple
import re

from ._common import (BINARY_MIME_PREFIXES, _parse_content_disposition)
from .urls import (decode_url)


_PROBE_RESPONSE_HEADERS = (
    "content-type",
    "content-disposition",
    "content-length",
    "last-modified",
    "etag",
    "location",
    "x-final-url",
)


def _refine_source_type_from_headers(
        current_type: str,
        content_type: str,
        content_disposition: str,
        url: str,
) -> Tuple[str, List[str]]:
    """Given the offline-detected source_type and the HEAD response
    headers, return (refined_type, reasons[]). The refined type is
    never LESS specific than the input — at worst we keep the
    original. The reasons list explains every promotion."""
    reasons: List[str] = []
    cd = _parse_content_disposition(content_disposition)
    ct = (content_type or "").lower().split(";")[0].strip()

    # Stream MIME types win over everything — even Content-Disposition:
    # attachment. Some servers serve .m3u8/.mpd with attachment headers,
    # and we'd rather treat them as manifests than as opaque downloads.
    # These promotions can also upgrade a less-specific current_type
    # (e.g. "unknown") to a manifest type.
    if ct == "application/vnd.apple.mpegurl" or ct == "application/x-mpegurl":
        reasons.append(f"Content-Type: {ct}")
        return "hls_manifest", reasons
    if ct == "application/dash+xml":
        reasons.append(f"Content-Type: {ct}")
        return "dash_manifest", reasons
    if ct == "application/vnd.ms-sstr+xml":
        reasons.append(f"Content-Type: {ct}")
        return "smooth_streaming_manifest", reasons

    # Explicit attachment disposition — but only promotes if the current
    # type is non-specific. Don't downgrade an already-typed candidate
    # (e.g. hls_manifest, dash_manifest, json_ld_media, resolution_card)
    # to a generic "header_attachment" just because the server set the
    # download header. The contract is "never LESS specific than input".
    if cd["type"] == "attachment" and current_type in (
            "unknown", "extensionless_file"):
        reasons.append(
            "Content-Disposition: attachment"
            + (f' (filename="{cd["filename"]}")' if cd["filename"] else "")
        )
        return "header_attachment", reasons

    # Binary MIME family — promote unknown to a typed download.
    # Pre-fix: when current_type was already "extensionless_file" and
    # the body matched a binary prefix, this branch returned
    # "extensionless_file" unchanged AND appended a reason — a no-op
    # rebrand that fired a redundant log line. Now we keep the type
    # but signal "confirmed" via the reasons list so the call site
    # can apply a smaller score bonus.
    for prefix in BINARY_MIME_PREFIXES:
        if ct.startswith(prefix):
            if current_type == "unknown":
                reasons.append(f"Content-Type: {ct}")
                return "extensionless_file", reasons
            if current_type == "extensionless_file":
                # Same type — but the HEAD body type CONFIRMS our
                # offline guess. Surface a reason so the caller can
                # award a smaller bonus.
                reasons.append(f"Content-Type: {ct} confirms file body")
                return current_type, reasons
            break

    # No refinement.
    return current_type, reasons


def _probe_head(
        http,
        url: str,
        *,
        headers: Optional[dict] = None,
        timeout: float = 5.0,
        _clock=None,
) -> dict:
    """Issue a single HEAD probe and return a normalized
    {ok, status, headers, final_url, error, elapsed_ms} record. The
    `http` arg is an httpx.Client-like object; tests inject a stub.
    We never raise — every failure is captured in the record.

    `ok` is True only for 2xx and 3xx statuses; non-success codes
    (4xx/5xx including the 405/501 that prompted a GET retry) leave
    `ok` False so downstream code doesn't have to re-check.

    `elapsed_ms` (v3.66.14, P8) is the wall-clock duration of the
    probe in milliseconds, including the GET-range:0-0 retry path
    when applicable. Reported as an int (rounded). 0 if the call
    aborted before any time elapsed (e.g. http=None). `_clock` is
    injectable for deterministic tests; defaults to time.monotonic.
    """
    out = {
        "ok": False, "status": 0, "headers": {},
        "final_url": url, "error": None, "elapsed_ms": 0,
    }
    if http is None:
        out["error"] = "no http client available"
        return out

    if _clock is None:
        import time as _time
        _clock = _time.monotonic
    _t0 = _clock()

    def _hdr_get(resp_headers, key: str) -> str:
        """Case-insensitive header lookup that works against both
        httpx.Headers (case-insensitive natively) and plain test-stub
        dicts (case-sensitive). Returns '' when missing."""
        if not resp_headers:
            return ""
        try:
            v = resp_headers.get(key)
            if v:
                return v
        except Exception:
            pass
        # Plain dict — try the lower/upper/title variants explicitly,
        # then do a final lower-cased scan for stubs that use a
        # different capitalization (e.g. "ETag" vs "etag").
        key_l = key.lower()
        for variant in (key_l, key.upper(), key_l.title()):
            try:
                v = resp_headers.get(variant)
                if v:
                    return v
            except Exception:
                pass
        try:
            for k, v in (resp_headers.items()
                         if hasattr(resp_headers, "items") else ()):
                if isinstance(k, str) and k.lower() == key_l and v:
                    return v
        except Exception:
            pass
        return ""

    try:
        try:
            resp = http.head(
                url,
                headers=headers or {},
                timeout=timeout,
                follow_redirects=True,
            )
            out["status"] = int(getattr(resp, "status_code", 0) or 0)
            # Some servers refuse HEAD with 405 / 501 — re-try with GET
            # range:0-0 so we still get the response headers without
            # pulling a body. Only do this if HEAD failed AND looks like
            # a method-not-allowed-style failure.
            if out["status"] in (405, 501):
                try:
                    rg = http.get(
                        url,
                        headers={**(headers or {}), "Range": "bytes=0-0"},
                        timeout=timeout,
                        follow_redirects=True,
                    )
                    out["status"] = int(getattr(rg, "status_code", 0) or 0)
                    resp = rg
                except Exception as e:
                    out["error"] = (
                        f"HEAD got {out['status']}; "
                        f"GET range:0-0 retry failed: {type(e).__name__}")
                    return out
            # `ok` is success-status-aware, not just "the call returned
            # without raising". A 404 HEAD response is not "ok" for the
            # caller's purposes.
            out["ok"] = 200 <= out["status"] < 400
            resp_headers = getattr(resp, "headers", {}) or {}
            for k in _PROBE_RESPONSE_HEADERS:
                v = _hdr_get(resp_headers, k)
                if v:
                    out["headers"][k] = v
            final = getattr(resp, "url", None)
            if final:
                out["final_url"] = str(final)
        except Exception as e:
            out["error"] = f"{type(e).__name__}: {str(e)[:160]}"
        return out
    finally:
        # v3.66.14 (P8): wall-clock duration. Always recorded, even on
        # error paths, so dashboards can show "this probe failed
        # AND took 5 seconds" vs "this probe failed instantly".
        out["elapsed_ms"] = int(round((_clock() - _t0) * 1000))


META_REFRESH_RE = re.compile(
    r"""<meta\b([^>]*)>""",
    re.I | re.S,
)


_META_HTTP_EQUIV_RE = re.compile(
    r"""http-equiv\s*=\s*['"]?refresh['"]?""", re.I)


_META_CONTENT_URL_RE = re.compile(
    r"""content\s*=\s*['"]?\s*\d+\s*;\s*url\s*=\s*"""
    r"""['"]?([^'">\s]+)""",
    re.I,
)


_HTML_COMMENT_RE = re.compile(r"<!--.*?-->", re.S)


def follow_meta_refresh(html: str, *, base_url: str = "") -> Optional[str]:
    """Return the URL named in a <meta http-equiv="refresh"> tag, or
    None if there isn't one. Resolved against base_url.

    v3.66.10: attribute order is no longer assumed. A page with
    `<meta content="0;url=..." http-equiv="refresh">` (content
    before http-equiv) is now recognized. Also tolerates an extra
    `'` or `"` immediately before the URL value.

    v3.66.11 (bugs EE/FF/GG):

      • EE (multi-meta): A page may have multiple <meta refresh>
        tags (rare but legal — e.g., a no-script fallback below a
        JS-driven one). We return the FIRST match by document
        order. Browsers typically honor the first one too. Locked
        in by test_bug_EE_first_match_wins.

      • FF (redirect loops): follow_meta_refresh is a single-call
        helper — it returns the target string and never recurses.
        Callers that decide to follow recursively MUST add their
        own loop detection (track visited URLs). The current
        deep_detect_live caller doesn't recurse; it records the
        meta-refresh in report["probes"]["meta_refresh"] as
        diagnostic data. A self-refresh (target == base_url) is
        still returned but logged for caller awareness.

      • GG (HTML-comment decoys): the previous regex scanned the
        entire HTML, including content inside <!-- --> comments.
        A decoy meta-refresh in a comment would be picked up
        ahead of a real one elsewhere on the page. Comments are
        now stripped before scanning so only live tags match."""
    if not html or not isinstance(html, str):
        return None
    # v3.66.11 (bug GG): strip HTML comments before matching so a
    # decoy <meta refresh> inside <!-- --> can't shadow a live one.
    # Cheap regex sub on the input is fine here; we don't need to
    # round-trip through a full HTML parser.
    sanitized = _HTML_COMMENT_RE.sub("", html)
    for tag_match in META_REFRESH_RE.finditer(sanitized):
        attrs_blob = tag_match.group(1)
        if not _META_HTTP_EQUIV_RE.search(attrs_blob):
            continue
        url_match = _META_CONTENT_URL_RE.search(attrs_blob)
        if not url_match:
            continue
        return decode_url(url_match.group(1), base_url=base_url)
    return None


def _poll_async_workflow(
        http,
        workflow: dict,
        *,
        headers: Optional[dict] = None,
        max_attempts: int = 20,
        interval: float = 2.0,
        timeout: float = 5.0,
        sleep=None,
) -> dict:
    """Execute a two-step POST reveal workflow and poll until either
    a download URL appears or we hit max_attempts. Returns:

        {
            "ok":             bool,
            "download_url":   str | None,
            "status_url":     str | None,
            "attempts":       int,
            "last_response":  dict | None,   # debug
            "error":          str | None,
        }

    The function looks for a download URL in three places, in order:

      1. A direct redirect — POST returns 3xx with Location.
      2. A JSON response body with one of {url, downloadUrl,
         download_url, fileUrl, file_url, signedUrl, signed_url}.
      3. A `status_url` plus polling — JSON response says
         {status:"pending", status_url:"..."}; we GET that URL until
         it returns a final URL or `status:"complete"` with one of
         the keys above.
    """
    out = {
        "ok": False,
        "download_url": None,
        "status_url": None,
        "attempts": 0,
        "last_response": None,
        "error": None,
    }
    if http is None:
        out["error"] = "no http client available"
        return out
    if sleep is None:
        import time as _time
        sleep = _time.sleep

    action = workflow.get("action") or ""
    if not action:
        out["error"] = "workflow has no action URL"
        return out
    data = dict(workflow.get("safe_fields") or {})
    # We deliberately don't fill in user_fields — those are for the
    # human or a higher layer. Leave them blank; the workflow will
    # error out cleanly if it requires them, and we won't have
    # forged an email address.
    # Honeypot fields are explicitly DROPPED.

    out["attempts"] += 1
    try:
        resp = http.post(
            action,
            data=data,
            headers=headers or {},
            timeout=timeout,
            follow_redirects=False,
        )
    except Exception as e:
        out["error"] = f"initial POST failed: {type(e).__name__}: {e}"
        return out

    status = int(getattr(resp, "status_code", 0) or 0)
    out["last_response"] = {"status": status, "url": str(getattr(
        resp, "url", action))}

    # Helper: case-insensitive header lookup that works against both
    # httpx.Headers (case-insensitive natively) and plain test-stub
    # dicts (case-sensitive). Mirrors the helper in _probe_head.
    def _hdr_get(resp_headers, key: str) -> str:
        if not resp_headers:
            return ""
        if hasattr(resp_headers, "get"):
            v = resp_headers.get(key)
            if v is not None:
                return str(v)
            # Fall through to case-insensitive walk for plain dict stubs.
            key_l = key.lower()
            try:
                for k, v in resp_headers.items():
                    if str(k).lower() == key_l:
                        return str(v)
            except Exception:
                pass
        return ""

    # Redirect → final URL
    if 300 <= status < 400:
        loc = _hdr_get(getattr(resp, "headers", {}) or {}, "Location")
        if loc:
            out["download_url"] = decode_url(loc, base_url=action)
            out["ok"] = True
            return out

    # JSON body inspection
    body = None
    try:
        body = resp.json() if hasattr(resp, "json") else None
    except Exception:
        body = None

    def _looks_like_url(v: str) -> bool:
        """Filter placeholders ('PENDING', 'TBD', 'null', '') out of
        the JSON-body URL extraction. We accept any value that looks
        like a real URL: explicit scheme, protocol-relative, or
        absolute path. Anything else — including unscheme'd tokens —
        is rejected so we don't resolve 'PENDING' as a relative path
        against `action` and return that as the 'download URL'."""
        if not isinstance(v, str):
            return False
        v = v.strip()
        if not v:
            return False
        return (
            v.startswith(("http://", "https://", "//", "/"))
            or v.startswith(("data:", "blob:"))
        )

    if isinstance(body, dict):
        for key in ("url", "downloadUrl", "download_url",
                    "fileUrl", "file_url",
                    "signedUrl", "signed_url",
                    "location"):
            v = body.get(key)
            if _looks_like_url(v):
                out["download_url"] = decode_url(v, base_url=action)
                out["ok"] = True
                return out
        # Explicit failure state on the initial response. Without this
        # check, a server returning `{"status":"failed","error":"..."}`
        # falls through to the bottom and produces the generic "POST
        # returned status=N with no recognizable download URL" error,
        # which hides the real failure cause from the operator.
        state = (body.get("status") or body.get("state") or "").lower()
        if state in ("error", "failed", "cancelled", "canceled"):
            err_detail = (body.get("error") or body.get("message")
                          or body.get("reason") or "")
            out["error"] = (f"workflow reported state={state!r}"
                            + (f": {str(err_detail)[:120]}"
                               if err_detail else ""))
            return out
        # Status-URL polling. Widened key recognition (v3.66.10) to
        # cover more API conventions. Pre-fix only matched the four
        # snake/camel-cased status_url variants; many APIs use
        # poll_uri, task_url, job_url instead.
        status_url_keys = (
            "status_url", "statusUrl", "statusURL",
            "status_uri", "statusUri", "statusURI",
            "poll_url", "pollUrl", "pollURL",
            "poll_uri", "pollUri", "pollURI",
            "task_url", "taskUrl", "taskURL",
            "job_url", "jobUrl", "jobURL",
            "polling_url", "pollingUrl", "pollingURL",
            "check_url", "checkUrl", "checkURL",
        )
        status_url = None
        for k in status_url_keys:
            v = body.get(k)
            if isinstance(v, str) and v.strip():
                status_url = v
                break
        if status_url and _looks_like_url(status_url):
            status_url = decode_url(status_url, base_url=action)
            out["status_url"] = status_url
            # Loop: poll FIRST, then sleep before the next poll (only
            # if there's a next poll). This is the opposite of the
            # original code which slept first — that wasted `interval`
            # seconds when the job was ready immediately.
            #
            # Off-by-one note: range(max_attempts) gives exactly
            # max_attempts polls. The original range(1, max_attempts)
            # ran max_attempts-1 polls AND reported "polled N attempts"
            # in the error, both wrong.
            #
            # Between-poll delay precedence (highest first):
            #   1. Retry-After response header (HTTP standard; seconds
            #      or HTTP-date — we only honor the seconds form here)
            #   2. retry_after / retry_after_seconds / poll_after_seconds
            #      keys in the JSON body
            #   3. caller-supplied `interval` fallback
            for attempt in range(max_attempts):
                out["attempts"] += 1
                try:
                    sresp = http.get(
                        status_url,
                        headers=headers or {},
                        timeout=timeout,
                        follow_redirects=True,
                    )
                except Exception as e:
                    out["error"] = (
                        f"poll attempt {attempt + 1} failed: "
                        f"{type(e).__name__}: {e}")
                    return out
                sstatus = int(getattr(sresp, "status_code", 0) or 0)
                out["last_response"] = {
                    "status": sstatus,
                    "url": str(getattr(sresp, "url", status_url))}
                try:
                    sbody = sresp.json() if hasattr(sresp, "json") else None
                except Exception:
                    sbody = None
                if isinstance(sbody, dict):
                    for key in ("url", "downloadUrl", "download_url",
                                "fileUrl", "file_url",
                                "signedUrl", "signed_url",
                                "location"):
                        v = sbody.get(key)
                        if _looks_like_url(v):
                            out["download_url"] = decode_url(
                                v, base_url=status_url)
                            out["ok"] = True
                            return out
                    state = (sbody.get("status") or sbody.get("state")
                             or "").lower()
                    if state in ("error", "failed", "cancelled",
                                 "canceled"):
                        err_detail = (sbody.get("error")
                                      or sbody.get("message")
                                      or sbody.get("reason") or "")
                        out["error"] = (
                            f"workflow reported state={state!r}"
                            + (f": {str(err_detail)[:120]}"
                               if err_detail else ""))
                        return out
                # Schedule next poll if there is one.
                if attempt + 1 < max_attempts:
                    sleep_for = interval
                    # Body-supplied delay (server's preference).
                    if isinstance(sbody, dict):
                        for hint_key in ("retry_after",
                                         "retry_after_seconds",
                                         "poll_after_seconds",
                                         "pollAfterSeconds"):
                            hv = sbody.get(hint_key)
                            if isinstance(hv, (int, float)) and hv > 0:
                                sleep_for = float(hv)
                                break
                    # Retry-After response header (overrides body hint
                    # if both are present; HTTP standard takes priority).
                    ra = _hdr_get(getattr(sresp, "headers", {}) or {},
                                   "Retry-After")
                    if ra:
                        try:
                            ra_seconds = float(str(ra).strip())
                            if ra_seconds > 0:
                                sleep_for = ra_seconds
                        except (ValueError, TypeError):
                            # Retry-After can also be an HTTP-date; we
                            # don't parse that form here — fall back to
                            # whatever sleep_for was set to above.
                            pass
                    # Sanity cap: never sleep longer than 60s per poll
                    # even if the server asks for more. Operators can
                    # raise this by passing a higher `interval` if a
                    # real workflow legitimately needs long delays.
                    sleep_for = min(sleep_for, 60.0)
                    sleep(sleep_for)
            out["error"] = (
                f"polled {max_attempts} attempts without resolution")
            return out

    out["error"] = (
        f"POST returned status={status} with no recognizable "
        "download URL in headers or body")
    return out

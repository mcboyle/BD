"""E-T1 — TLS / anti-bot fingerprinting *detection* (presence-only).

POSTURE (read first)
--------------------
This module is strictly **detect-and-surface-risk**. It answers one
question from data the remote site already handed us: *is this site
fingerprinting us (TLS / behavioral / browser) and which vendor is doing
it?* It then surfaces that as a risk signal to the operator.

It does NOT, and must never:
  * sniff or reconstruct a TLS ClientHello,
  * compute a JA3/JA4 to *present* (only reads ones a site echoes back),
  * impersonate, rotate, or pin any fingerprint,
  * feed any replay path.

Those are the curl_cffi / behavioral-replay half (B-T3 / E-T2), which is
**posture-declined** without an explicit, logged posture-change decision.
Confirmed scope this build: detect presence only. (See OPEN_THREADS E-T1,
CAPTURE_SYNTHESIS tiered phases §E.)

WHY THIS IS POSTURE-CLEAN
-------------------------
We have no packet-level access from inside Playwright, and we deliberately
don't want it — chasing the raw handshake is the road to impersonation
tooling. But a site that fingerprints us advertises that fact in its
*responses*: anti-bot vendors set characteristic cookies and headers, serve
recognizable challenge/interstitial pages, and some echo the JA3/JA4 they
computed back in a debug header. Every signal this module reads is already
present in the captured ``response_headers`` / ``response_status`` /
``url`` — we are reading the site's own tells, not probing it. Detection
here strengthens the operator's risk picture (e.g. "this site runs DataDome;
a naive download will likely be challenged") without enabling evasion.

INPUT
-----
A capture dict in bd-recon format (``session_capture.to_capture_dict()``):
``{"network_log": [ {url, response_status, response_headers, ...}, ... ]}``.
Response headers may be a list of ``{"name","value"}`` dicts (CDP shape) or a
flat ``{name: value}`` mapping — both are accepted. Headers are matched
case-insensitively. Already-redacted values are fine: we match on header
*names* and vendor-cookie *prefixes*, not on secret values.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

# ── Vendor signature table ────────────────────────────────────────────
# Each vendor entry lists the *response-side* tells. We match:
#   * header names (case-insensitive, exact),
#   * Set-Cookie name prefixes (the cookie a vendor plants on challenge),
#   * server/header value substrings (case-insensitive).
# A vendor is "detected" when >=1 tell fires. We keep the matched tells so
# the operator sees the evidence, not just a verdict.
#
# Sourcing note: these are well-known, publicly documented anti-bot tells
# (vendor docs + the WAF/anti-bot detection literature). No secret/evasion
# knowledge is encoded — only "this header means vendor X is present".

_VENDORS: Tuple[Tuple[str, Dict[str, Tuple[str, ...]]], ...] = (
    ("cloudflare", {
        "header_names": ("cf-ray", "cf-cache-status", "cf-mitigated"),
        "cookie_prefixes": ("__cf_bm", "cf_clearance", "__cfwaitingroom"),
        "server_substrings": ("cloudflare",),
    }),
    ("akamai", {
        "header_names": ("x-akamai-transformed", "akamai-grn"),
        "cookie_prefixes": ("ak_bmsc", "bm_sv", "bm_sz", "_abck"),
        "server_substrings": ("akamaighost",),
    }),
    ("datadome", {
        "header_names": ("x-datadome", "x-dd-b"),
        "cookie_prefixes": ("datadome",),
        "server_substrings": ("datadome",),
    }),
    ("perimeterx", {
        "header_names": ("x-px", "x-px-block"),
        "cookie_prefixes": ("_px", "_pxhd", "_pxvid", "pxcts"),
        "server_substrings": ("perimeterx",),
    }),
    ("imperva_incapsula", {
        "header_names": ("x-iinfo", "x-cdn"),
        "cookie_prefixes": ("incap_ses", "visid_incap", "nlbi_"),
        "server_substrings": ("incapsula",),
    }),
    ("kasada", {
        "header_names": ("x-kpsdk-ct", "x-kpsdk-v"),
        "cookie_prefixes": ("kpsdk",),
        "server_substrings": ("kasada",),
    }),
    ("aws_waf", {
        "header_names": ("x-amzn-waf-action",),
        "cookie_prefixes": ("aws-waf-token",),
        "server_substrings": (),
    }),
)

# Headers that echo a computed TLS/HTTP2 fingerprint back to the client.
# Their *presence* is the signal (the site computed a JA3/JA4 on us); we do
# not parse or reuse the value.
_FP_ECHO_HEADERS: Tuple[str, ...] = (
    "x-ja3", "x-ja3-hash", "x-ja4", "x-ja4-hash",
    "x-tls-fingerprint", "x-http2-fingerprint", "x-fingerprint",
)

# Challenge / interstitial signals: a fingerprinting site that decides we
# look like a bot serves one of these instead of content.
_CHALLENGE_STATUSES: Tuple[int, ...] = (401, 403, 429, 503)
_CHALLENGE_URL_TERMS: Tuple[str, ...] = (
    "/cdn-cgi/challenge", "/_incapsula_resource", "/.well-known/captcha",
    "captcha", "challenge-platform", "px-captcha",
)


def _normalize_headers(raw: Any) -> Dict[str, str]:
    """Accept CDP list-of-dicts OR flat mapping; return a lowercased-key
    dict. We lowercase keys (HTTP header names are case-insensitive) but
    preserve values as-is. Non-string/odd shapes degrade to {} so a single
    malformed entry never breaks a whole-capture scan."""
    out: Dict[str, str] = {}
    if isinstance(raw, dict):
        for k, v in raw.items():
            if isinstance(k, str):
                out[k.lower()] = "" if v is None else str(v)
    elif isinstance(raw, (list, tuple)):
        for item in raw:
            if isinstance(item, dict):
                name = item.get("name")
                if isinstance(name, str):
                    val = item.get("value")
                    out[name.lower()] = "" if val is None else str(val)
    return out


def _collect_set_cookie(headers: Dict[str, str]) -> str:
    """Set-Cookie may appear once or be folded; return a lowercased blob to
    substring-match cookie-name prefixes against. We match prefixes, not
    values, so redaction of the value half is irrelevant."""
    blob = headers.get("set-cookie", "")
    # Some captures fold multiple cookies under one key separated by newline.
    return blob.lower()


def detect_fingerprinting(capture: Dict[str, Any]) -> Dict[str, Any]:
    """Scan one capture dict for fingerprinting/anti-bot presence.

    Returns a structured finding (always — never raises on bad input):

        {
          "fingerprinting_detected": bool,
          "vendors": [ {"vendor": str, "tells": [str, ...],
                        "first_seen_url": str} ],
          "fp_echo_headers": [ {"header": str, "url": str} ],
          "challenges": [ {"url": str, "status": int, "reason": str} ],
          "summary": str,            # one-line operator-facing risk note
          "requests_scanned": int,
        }

    The finding is advisory risk context. It carries no instruction to
    evade and emits nothing runnable.
    """
    log = []
    if isinstance(capture, dict):
        raw_log = capture.get("network_log")
        if isinstance(raw_log, (list, tuple)):
            log = raw_log

    vendors_seen: Dict[str, Dict[str, Any]] = {}
    fp_echo: List[Dict[str, str]] = []
    challenges: List[Dict[str, Any]] = []
    scanned = 0

    for entry in log:
        if not isinstance(entry, dict):
            continue
        scanned += 1
        url = str(entry.get("url", ""))
        headers = _normalize_headers(entry.get("response_headers"))
        set_cookie = _collect_set_cookie(headers)
        server_val = headers.get("server", "").lower()

        # --- vendor signatures ---
        for vendor, sig in _VENDORS:
            tells: List[str] = []
            for hn in sig["header_names"]:
                if hn in headers:
                    tells.append("header:" + hn)
            for cp in sig["cookie_prefixes"]:
                # cookie-name prefix at a name boundary in the Set-Cookie blob
                if cp in set_cookie:
                    tells.append("cookie:" + cp)
            for ss in sig["server_substrings"]:
                if ss and ss in server_val:
                    tells.append("server:" + ss)
            if tells:
                rec = vendors_seen.setdefault(
                    vendor, {"vendor": vendor, "tells": [],
                             "first_seen_url": url})
                for t in tells:
                    if t not in rec["tells"]:
                        rec["tells"].append(t)

        # --- explicit fingerprint-echo headers ---
        for fph in _FP_ECHO_HEADERS:
            if fph in headers:
                fp_echo.append({"header": fph, "url": url})

        # --- challenge / interstitial responses ---
        status = entry.get("response_status")
        url_l = url.lower()
        url_hit = next((t for t in _CHALLENGE_URL_TERMS if t in url_l), None)
        status_hit = isinstance(status, int) and status in _CHALLENGE_STATUSES
        if url_hit or status_hit:
            reason_parts = []
            if status_hit:
                reason_parts.append("status=%d" % status)
            if url_hit:
                reason_parts.append("url~%s" % url_hit)
            challenges.append({
                "url": url,
                "status": status if isinstance(status, int) else None,
                "reason": ", ".join(reason_parts),
            })

    vendors = sorted(vendors_seen.values(), key=lambda r: r["vendor"])
    detected = bool(vendors or fp_echo)

    # One-line operator-facing summary. Names the risk; suggests nothing
    # evasive — the posture-clean response to "site X fingerprints us" is
    # "expect challenges / it may not be downloadable", not "rotate TLS".
    if not detected and not challenges:
        summary = "No fingerprinting/anti-bot tells in %d request(s)." % scanned
    else:
        bits = []
        if vendors:
            bits.append("anti-bot vendor(s): " +
                        ", ".join(v["vendor"] for v in vendors))
        if fp_echo:
            bits.append("%d fingerprint-echo header(s)" % len(fp_echo))
        if challenges:
            bits.append("%d challenge response(s)" % len(challenges))
        summary = ("Fingerprinting/anti-bot present — " + "; ".join(bits) +
                   ". Expect bot challenges; this site may resist automated "
                   "download. (Detection only — no evasion performed.)")

    return {
        "fingerprinting_detected": detected,
        "vendors": vendors,
        "fp_echo_headers": fp_echo,
        "challenges": challenges,
        "summary": summary,
        "requests_scanned": scanned,
    }

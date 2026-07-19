from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional, Tuple
import html as _htmllib
import base64
from urllib.parse import (
    parse_qsl, urlencode, urljoin, urlparse, urlunparse,
)
import re

from ._common import (BINARY_MIME_PREFIXES, PROGRESSIVE_MEDIA_EXTENSIONS, PROVIDERS, STREAM_MANIFEST_EXTENSIONS, _SIGNED_URL_SHORT_TTL_THRESHOLD, _parse_content_disposition, _url_host, _url_path)


STREAM_SEGMENT_EXTENSIONS = (
    ".ts", ".m2ts", ".mts", ".m4s",
    ".cmfv", ".cmfa",
)


SUBTITLE_EXTENSIONS = (
    ".vtt", ".srt", ".ass", ".ssa", ".ttml", ".dfxp", ".sbv",
)


SIDECAR_EXTENSIONS = (
    ".sha256", ".sha512", ".md5",
    ".sig", ".asc", ".minisig",
    ".torrent",
)


SUSPICIOUS_URL_PATTERNS = (
    "?utm_",
    "/go?", "/out?", "/redirect?", "/click?", "/clk?",
    "/track?", "/tracking?",
    "/ad/", "/ads/",
    "doubleclick.net", "googlesyndication",
    "adservice",
)


STREAM_MIME_TYPES = (
    "application/vnd.apple.mpegurl",
    "application/x-mpegurl",
    "audio/mpegurl",
    "application/dash+xml",
    "application/vnd.ms-sstr+xml",
    "application/f4m+xml",
    "application/smil+xml",
    "audio/x-scpls",
    "application/xspf+xml",
)


CDN_MEDIA_HINTS = (
    "cdn.", "/cdn/", "media.", "/media/", "/assets/",
    "/files/", "/uploads/", "/downloads/", "/static/",
    "/storage/",
    "s3.amazonaws.com", "amazonaws.com",
    "cloudfront.net",
    "azureedge.net", "blob.core.windows.net",
    "googleusercontent.com", "storage.googleapis.com",
    "r2.cloudflarestorage.com",
    "b-cdn.net", "bunnycdn.com",
    "fastly.net", "akamaihd.net",
)


SIGNED_URL_HINTS = (
    "expires=", "expiry=", "exp=",
    "signature=", "sig=", "token=",
    "policy=", "key-pair-id=",
    "x-amz-signature", "x-amz-expires",
    "x-goog-signature",
)


def decode_url(raw_url: str, *, base_url: str = "",
               base_href: str = "") -> str:
    """Normalize a URL we found in HTML/JSON to its real form.

    Handles: HTML entities (&amp; → &), JSON-escaped slashes
    (\\/ → /), unicode-escaped colons/slashes (\\u002F → /),
    percent-encoded URL chars (https%3A%2F%2F → https://),
    protocol-relative (//host/x → scheme://host/x), and relative
    URLs (resolved against base_href if a <base> tag was present,
    else against base_url).

    No fetching, no allocation surprises — pure string transforms."""
    if not raw_url:
        return ""
    s = raw_url.strip()
    # HTML entity decode
    s = _htmllib.unescape(s)
    # JSON unicode-escaped chars. Pre-fix used `s.encode().decode(
    # "unicode_escape")` which is the Python literal-escape grammar
    # AS A WHOLE — it decodes \n, \t, \xNN, \\ etc. in addition to
    # \uXXXX. That's too broad for our purpose: a URL like
    # `https://x.com/path\to` (no, not a real URL, but malformed JSON
    # could surface such) was being mutated. Replace with a narrow
    # regex that only consumes \uXXXX sequences.
    if "\\u" in s and len(s) < 4096:
        try:
            s = re.sub(
                r"\\u([0-9A-Fa-f]{4})",
                lambda m: chr(int(m.group(1), 16)),
                s,
            )
        except (ValueError, OverflowError):
            pass
    # JSON-escaped slashes
    s = s.replace("\\/", "/")
    # Percent-encoded URLs hiding inside another URL's query
    if s.startswith(("https%3A%2F%2F", "http%3A%2F%2F")):
        from urllib.parse import unquote
        s = unquote(s)
    # Protocol-relative
    if s.startswith("//"):
        scheme = urlparse(base_url or base_href or "https://x").scheme
        s = f"{scheme or 'https'}:{s}"
    # Relative
    if not s.startswith(("http://", "https://", "data:", "blob:",
                          "javascript:", "mailto:", "ftp://", "ftps://",
                          "rtmp://", "rtsp://", "ws://", "wss://")):
        anchor = base_href or base_url
        if anchor:
            s = urljoin(anchor, s)
        else:
            # No anchor → the URL cannot be resolved. Returning the
            # raw relative path would mislead downstream code, which
            # uses _url_path(s).endswith(ext) for extension scoring;
            # "./foo.mp4" would falsely match .mp4 and earn a file-
            # extension bonus despite being unfetchable. Better to
            # signal failure with an empty string.
            return ""
    return s


def maybe_decode_base64(value: str) -> Optional[str]:
    """If `value` looks like a sensibly-sized base64 string, decode
    it and return the result as text. Returns None otherwise — safer
    to return None than to surface garbage bytes.

    Constraint: 40–50000 chars, valid base64 alphabet only. This
    catches embedded config blobs without burning cycles on every
    short identifier."""
    if not value or not (40 <= len(value) <= 50_000):
        return None
    if not re.fullmatch(r"[A-Za-z0-9+/=_-]+", value):
        return None
    try:
        # url-safe variants too
        v = value.replace("-", "+").replace("_", "/")
        # add padding
        v += "=" * (-len(v) % 4)
        decoded = base64.b64decode(v, validate=False)
    except Exception:
        return None
    # Heuristic: must be mostly printable to be useful as a string.
    try:
        text = decoded.decode("utf-8")
    except UnicodeDecodeError:
        return None
    printable = sum(1 for c in text if c.isprintable() or c in "\n\r\t")
    if len(text) and printable / len(text) < 0.85:
        return None
    return text


_TRACKING_PARAMS_TO_STRIP = frozenset({
    # Google Analytics / Ads
    "utm_source", "utm_medium", "utm_campaign", "utm_term",
    "utm_content", "utm_id", "utm_name", "utm_referrer",
    "_ga", "_gl", "gclid", "gclsrc", "gbraid", "wbraid", "dclid",
    # Facebook
    "fbclid", "fb_action_ids", "fb_action_types", "fb_ref",
    "fb_source", "_openstat",
    # Microsoft / Bing
    "msclkid",
    # LinkedIn
    "li_fat_id",
    # Mailchimp / mailers
    "mc_cid", "mc_eid",
    # Yahoo
    "yclid",
    # TikTok
    "ttclid",
    # HubSpot
    "_hsenc", "_hsmi", "__hssc", "__hstc", "__hsfp", "hsCtaTracking",
    # Generic referral / debug params we should also drop
    "ref", "ref_src", "ref_url", "source",
    "share", "share_id", "shared", "shareid",
    "trk", "trkCampaign", "trkUserId",
})


_FUNCTIONAL_FRAGMENT_PREFIXES = (
    "t=",          # media timecode: `#t=10,20`
    "xywh=",       # Media Fragments Spec spatial selector
    "track=",      # Media Fragments Spec track selector
    "id=",         # some video providers
)


def canonicalize_url(url: str) -> str:
    """Return a normalized form of a URL suitable for use as a
    deduplication key.

    Operations applied, in order:
      1. urlparse the input. On parse failure, return the lowercased
         original string (so non-URL keys still dedupe consistently).
      2. Lowercase the scheme and host. (v3.66.11) IDN hosts are also
         normalized to their punycode ASCII form so unicode and
         punycode spellings of the same host dedupe.
      3. Drop default ports (:80 for http, :443 for https).
      4. Normalize the path: collapse multiple consecutive slashes
         into one, then strip a trailing slash UNLESS the path is
         just "/".
      5. Parse the query string. Drop any param whose name (lowercase)
         is in _TRACKING_PARAMS_TO_STRIP. Keep everything else (including
         signed-URL params like `Signature`, `Expires`, `X-Amz-*`).
         Sort remaining params alphabetically by name (then by value
         for repeated names) so query-order variation doesn't matter.
      6. Drop the fragment unless it begins with one of the
         _FUNCTIONAL_FRAGMENT_PREFIXES (media timecodes, etc.) OR
         looks like SPA fragment routing (starts with `/` or `!/`).
         (v3.66.11) SPA-routed URLs whose unique payload lives in the
         fragment otherwise dedupe incorrectly to the same key.

    The returned string is the canonicalized URL. It is NOT what
    callers should fetch — the original URL on the candidate dict
    stays intact for that. This function exists only to compare two
    URLs for "are these the same resource?"
    """
    if not url or not isinstance(url, str):
        return ""
    s = url.strip()
    if not s:
        return ""
    try:
        parsed = urlparse(s)
    except Exception:
        return s.lower()
    # Some schemes don't carry a host (data:, blob:, javascript:)
    # — for those, fall back to the raw lowercased URL.
    if parsed.scheme in ("data", "blob", "javascript", "mailto"):
        return s
    if not parsed.scheme and not parsed.netloc:
        # Relative URL — can't canonicalize without a base. Best-effort
        # return lowercased.
        return s.lower()

    scheme = (parsed.scheme or "").lower()
    host = (parsed.hostname or "").lower()
    # v3.66.11 (bug 19): IDN hosts must normalize to ASCII (punycode)
    # form so that `xn--exmple-cua.com` and `éxmple.com` dedupe to the
    # same key. Hostname's already lowercased; encode-decode round-
    # trip through idna gives us the canonical form. Failure (e.g.
    # IDN-invalid input) leaves the lowercased host alone.
    if host and any(ord(ch) > 127 for ch in host):
        try:
            host = host.encode("idna").decode("ascii")
        except (UnicodeError, UnicodeDecodeError):
            pass  # not valid IDN — fall back to lowercased original
    # Re-attach userinfo if present (rare but legal). We keep userinfo
    # case-sensitive because passwords are.
    userinfo = ""
    if parsed.username is not None:
        userinfo = parsed.username
        if parsed.password is not None:
            userinfo += ":" + parsed.password
        userinfo += "@"
    # Port: drop defaults.
    port = parsed.port
    netloc = userinfo + host
    if port is not None and not (
            (scheme == "http" and port == 80)
            or (scheme == "https" and port == 443)
            or (scheme == "ftp" and port == 21)):
        netloc += f":{port}"

    # Normalize path: collapse runs of slashes; strip trailing slash
    # unless path is exactly "/".
    path = parsed.path or ""
    # Collapse `//` to `/` (but not at the very start of a protocol-
    # relative URL, which urlparse already separates into .netloc).
    while "//" in path:
        path = path.replace("//", "/")
    if len(path) > 1 and path.endswith("/"):
        path = path.rstrip("/")
    if not path:
        path = ""

    # Query: drop tracking params, sort the rest.
    if parsed.query:
        pairs = parse_qsl(parsed.query, keep_blank_values=True)
        filtered = [
            (k, v) for k, v in pairs
            if k.lower() not in _TRACKING_PARAMS_TO_STRIP
        ]
        # Sort by key then value for stable canonicalization.
        filtered.sort()
        query = urlencode(filtered, doseq=False)
    else:
        query = ""

    # Fragment: drop unless functional.
    #
    # v3.66.11 (bug 17): SPA fragment routing (`#/path/123`,
    # `#!/page`, `#/video/abc?q=x`) was being dropped — meaning two
    # distinct SPA URLs `app#/video/123` and `app#/video/456`
    # canonicalized to the SAME key and would silently dedupe to
    # one. Preserve fragments that look like in-fragment routing
    # (start with `/` or `!/`) in addition to the media-fragment
    # prefixes.
    fragment = parsed.fragment or ""
    if fragment:
        is_functional = any(
            fragment.startswith(p)
            for p in _FUNCTIONAL_FRAGMENT_PREFIXES)
        is_spa_route = (fragment.startswith("/")
                        or fragment.startswith("!/"))
        if not (is_functional or is_spa_route):
            fragment = ""

    return urlunparse((scheme, netloc, path, parsed.params,
                       query, fragment))


def _parse_signed_url_timestamp(s: str) -> Optional["datetime"]:
    """Parse a timestamp into a UTC datetime, accepting:
      • bare seconds-since-epoch ("1748275200")
      • ISO 8601 with or without timezone ("2026-05-26T12:00:00Z")
      • AWS X-Amz-Date format ("20260526T120000Z")
    Returns None on failure."""
    if not s:
        return None
    from datetime import datetime, timezone
    s = s.strip()
    # Unix epoch
    if s.isdigit() or (s.startswith("-") and s[1:].isdigit()):
        try:
            return datetime.fromtimestamp(int(s), tz=timezone.utc)
        except (ValueError, OSError, OverflowError):
            return None
    # AWS X-Amz-Date: YYYYMMDDTHHMMSSZ (compact ISO 8601 basic)
    if len(s) == 16 and s[8] == "T" and s.endswith("Z"):
        try:
            return datetime.strptime(s, "%Y%m%dT%H%M%SZ").replace(
                tzinfo=timezone.utc)
        except ValueError:
            return None
    # Try standard ISO 8601 forms
    for fmt in ("%Y-%m-%dT%H:%M:%SZ",
                "%Y-%m-%dT%H:%M:%S.%fZ",
                "%Y-%m-%dT%H:%M:%S%z",
                "%Y-%m-%dT%H:%M:%S.%f%z"):
        try:
            dt = datetime.strptime(s.replace("Z", "+00:00")
                                   if fmt.endswith("%z") else s, fmt)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt
        except ValueError:
            continue
    # Last resort: fromisoformat (Python 3.11+ handles Z)
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None


def detect_signed_url(url: str, *,
                      now: Optional["datetime"] = None
                      ) -> dict:
    """Inspect a URL's query parameters for known cryptographic
    signing schemes. Returns:

        {
            "is_signed":        bool,
            "provider":         str | None,    # one of:
                                  # 'aws_s3_v4', 'aws_s3_v2',
                                  # 'cloudfront', 'azure_sas',
                                  # 'gcs_v4', 'bunny_token',
                                  # 'cloudflare_stream', 'akamai',
                                  # 'jw_player', 'generic_signed'
            "expires_at":       str | None,    # ISO 8601 UTC if derivable
            "ttl_seconds":      int | None,    # seconds remaining at `now`
            "reasons":          [str],         # which params we matched
            "expired":          bool,          # ttl_seconds < 0
            "expiring_soon":    bool,          # 0 < ttl_seconds < THRESHOLD
        }

    `now` is injected for tests; defaults to datetime.now(UTC).
    No network. Pure URL inspection.
    """
    from datetime import datetime, timezone
    out = {
        "is_signed": False,
        "provider": None,
        "expires_at": None,
        "ttl_seconds": None,
        "reasons": [],
        "expired": False,
        "expiring_soon": False,
    }
    if not url or not isinstance(url, str):
        return out
    try:
        parsed = urlparse(url)
    except Exception:
        return out
    if not parsed.query:
        return out
    pairs = parse_qsl(parsed.query, keep_blank_values=True)
    # Build a case-insensitive lookup of the params present. Some
    # providers use mixed case (X-Amz-*) and some lowercase (sig=).
    # Keep the FIRST occurrence per case-folded key.
    params: Dict[str, str] = {}
    for k, v in pairs:
        kl = k.lower()
        if kl not in params:
            params[kl] = v

    expires_at = None
    provider = None

    # ── AWS S3 v4 / GCS v4 ────────────────────────────────────────
    # Both have X-Amz-* / X-Goog-* with Algorithm + Date + Expires
    # (offset in seconds from Date). v4 signature is always SigV4.
    if "x-amz-algorithm" in params and "x-amz-date" in params \
            and "x-amz-expires" in params:
        provider = "aws_s3_v4"
        out["reasons"].append("X-Amz-Algorithm + X-Amz-Expires")
        try:
            secs = int(params["x-amz-expires"])
            date = _parse_signed_url_timestamp(params["x-amz-date"])
            if date is not None:
                from datetime import timedelta
                expires_at = date + timedelta(seconds=secs)
        except (ValueError, TypeError):
            pass
    elif "x-goog-algorithm" in params and "x-goog-date" in params \
            and "x-goog-expires" in params:
        provider = "gcs_v4"
        out["reasons"].append("X-Goog-Algorithm + X-Goog-Expires")
        try:
            secs = int(params["x-goog-expires"])
            date = _parse_signed_url_timestamp(params["x-goog-date"])
            if date is not None:
                from datetime import timedelta
                expires_at = date + timedelta(seconds=secs)
        except (ValueError, TypeError):
            pass
    # ── AWS S3 v2 / CloudFront / Akamai-style ──────────────────────
    # All three use a Signature + Expires (seconds-since-epoch) pair.
    # Distinguish by the access-key param:
    #   AWS S3 v2:  AWSAccessKeyId
    #   CloudFront: Key-Pair-Id
    elif "signature" in params and "expires" in params:
        if "awsaccesskeyid" in params:
            provider = "aws_s3_v2"
            out["reasons"].append("Signature + Expires + AWSAccessKeyId")
        elif "key-pair-id" in params:
            provider = "cloudfront"
            out["reasons"].append("Signature + Expires + Key-Pair-Id")
        else:
            provider = "generic_signed"
            out["reasons"].append("Signature + Expires")
        expires_at = _parse_signed_url_timestamp(params["expires"])
    # ── Azure SAS ─────────────────────────────────────────────────
    # Recognized by `sig=` plus `se=` (signed expiry) and `sv=`
    # (signed version). The expiry is ISO 8601.
    elif "sig" in params and "se" in params and "sv" in params:
        provider = "azure_sas"
        out["reasons"].append("Azure SAS sig + se + sv")
        expires_at = _parse_signed_url_timestamp(params["se"])
    # ── Bunny.net / KeyCDN / generic token ─────────────────────────
    # `token=<hash>&expires=<unix>` (or `token_path=`).
    elif "token" in params and "expires" in params:
        provider = "bunny_token"
        out["reasons"].append("token + expires (Bunny/KeyCDN style)")
        expires_at = _parse_signed_url_timestamp(params["expires"])
    # ── CloudFlare Stream ─────────────────────────────────────────
    # Embedded JWT-style token in the path or as `?token=<jwt>`. We
    # don't decode the JWT here; just flag.
    elif "videodelivery.net" in (parsed.hostname or "").lower() \
            or "cloudflarestream.com" in (parsed.hostname or "").lower():
        if "token" in params or "sig" in params:
            provider = "cloudflare_stream"
            out["reasons"].append("CloudFlare Stream signed URL")
    # ── Akamai EdgeAuth ───────────────────────────────────────────
    # hdnea= / hdntl= / __hdnts= / auth_token=
    elif any(k in params for k in
             ("hdnea", "hdntl", "__hdnts", "auth_token")):
        provider = "akamai"
        out["reasons"].append("Akamai EdgeAuth token")
        # Akamai tokens embed expiry as a substring like `exp=N~`
        # inside the token value. Try to extract.
        for k in ("hdnea", "hdntl", "__hdnts", "auth_token"):
            if k in params:
                tok = params[k]
                m = re.search(r"exp=(\d+)", tok)
                if m:
                    expires_at = _parse_signed_url_timestamp(m.group(1))
                    break
    # ── JW Player signed URLs ─────────────────────────────────────
    # `exp=<unix>&sig=<hex>` shape used by jwplayer.com.
    elif "exp" in params and "sig" in params \
            and len(params.get("sig", "")) > 16:
        provider = "jw_player"
        out["reasons"].append("JW Player exp + sig")
        expires_at = _parse_signed_url_timestamp(params["exp"])

    if provider is None:
        # Nothing matched. Even a very long opaque token doesn't make
        # a URL "signed" in any actionable way — we'd just be guessing.
        return out

    out["is_signed"] = True
    out["provider"] = provider

    # Compute TTL.
    if expires_at is not None:
        out["expires_at"] = expires_at.isoformat()
        if now is None:
            now = datetime.now(timezone.utc)
        # Make sure expires_at is timezone-aware for the subtraction.
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
        ttl = (expires_at - now).total_seconds()
        out["ttl_seconds"] = int(ttl)
        if ttl < 0:
            out["expired"] = True
        elif ttl < _SIGNED_URL_SHORT_TTL_THRESHOLD:
            out["expiring_soon"] = True
    return out


def classify_url(
    url: str,
    *,
    mime: str = "",
    content_disposition: str = "",
    page_url: str = "",
) -> dict:
    """Decide which SOURCE_TYPES bucket a URL falls into.

    The classification is layered: header info (Content-Disposition,
    MIME) wins over URL inspection, which wins over text guessing.
    Provider host substrings are recognized before generic extensions
    so a kaltura.com URL ending in .mp4 is still tagged as a
    provider embed, not a raw direct file (the provider may serve
    it through DRM or auth).

    Returns:
        {
            "type": one of SOURCE_TYPES,
            "is_primary": bool,         # vs sidecar/segment/preview
            "container": str | None,    # mp4 / webm / mkv / ...
            "reasons": [str, ...],      # why this classification
        }
    """
    out = {"type": "unknown", "is_primary": False,
           "container": None, "reasons": []}
    if not url:
        return out

    path = _url_path(url)
    host = _url_host(url)
    lower = url.lower()
    mime_l = (mime or "").lower()
    cd_l = (content_disposition or "").lower()

    # Header signals take priority. Use the structured parser to get
    # the disposition TYPE (attachment / inline / form-data); pre-fix
    # this was a substring check, so a filename containing
    # "attachment" (e.g. `inline; filename="attachment_form.pdf"`)
    # would falsely classify as header_attachment.
    if content_disposition:
        cd_parsed = _parse_content_disposition(content_disposition)
        if cd_parsed["type"] == "attachment":
            out["type"] = "header_attachment"
            out["is_primary"] = True
            out["reasons"].append("Content-Disposition: attachment")
            return out

    # Blob URLs are transient — never a final answer.
    if lower.startswith("blob:"):
        out["type"] = "blob_transient"
        out["reasons"].append("blob: URL is browser-local; "
                               "resolve underlying source")
        return out

    # Legacy stream protocols — not HTTP downloads.
    for proto in ("rtmp://", "rtmps://", "rtmpe://", "rtmpt://",
                  "rtsp://", "mms://"):
        if lower.startswith(proto):
            out["type"] = "legacy_stream"
            out["reasons"].append(f"non-HTTP protocol: {proto}")
            return out

    # WebRTC SDP files.
    if path.endswith(".sdp"):
        out["type"] = "webrtc_live"
        out["reasons"].append(".sdp WebRTC session description")
        return out

    # Provider-host check (kaltura.com / brightcove / wistia / vimeo
    # / youtube / mux / cloudflarestream / bunny / panopto / vidyard /
    # dailymotion / sproutvideo). These take precedence over the
    # extension check so a provider-served `.mp4` is still tagged as
    # a provider embed — the URL is rarely playable cross-origin.
    # Match against the host, not the full URL — pre-fix, a query
    # like `?ref=vimeo.com` would falsely mark any URL as a Vimeo
    # embed.
    for prov_name, hosts, _markers in PROVIDERS:
        for h in hosts:
            if h in host:
                out["type"] = f"{prov_name}_embed" \
                    if prov_name != "panopto" else "panopto_session"
                # The mux/cloudflare/bunny names ship as "<n>_stream",
                # not "<n>_embed".
                if prov_name in ("mux", "cloudflare_stream",
                                 "bunny_stream"):
                    out["type"] = f"{prov_name}_stream" \
                        if not prov_name.endswith("_stream") \
                        else prov_name
                out["is_primary"] = True
                out["reasons"].append(f"provider host: {h}")
                return out

    # Streaming manifests.
    for ext in STREAM_MANIFEST_EXTENSIONS:
        if path.endswith(ext):
            if ext == ".m3u8":
                out["type"] = "hls_manifest"
            elif ext == ".mpd":
                out["type"] = "dash_manifest"
            elif ext in (".ism", ".isml"):
                out["type"] = "smooth_streaming_manifest"
            elif ext == ".f4m":
                out["type"] = "adobe_hds_manifest"
            else:
                out["type"] = "hls_manifest"
            out["is_primary"] = True
            out["reasons"].append(f"streaming manifest ({ext})")
            return out

    # Stream MIME types (handles extensionless URLs).
    if mime_l in STREAM_MIME_TYPES:
        if "mpegurl" in mime_l:
            out["type"] = "hls_manifest"
        elif "dash" in mime_l:
            out["type"] = "dash_manifest"
        else:
            out["type"] = "hls_manifest"
        out["is_primary"] = True
        out["reasons"].append(f"streaming MIME: {mime_l}")
        return out

    # Stream segments — present in the candidate list at LOW priority
    # so the scorer can de-rank them in favor of the parent manifest.
    for ext in STREAM_SEGMENT_EXTENSIONS:
        if path.endswith(ext):
            out["type"] = "stream_segment"
            out["reasons"].append(
                f"chunk/segment ({ext}) — prefer parent manifest")
            return out

    # Subtitle/caption sidecar.
    for ext in SUBTITLE_EXTENSIONS:
        if path.endswith(ext):
            out["type"] = "subtitle_track"
            out["reasons"].append(f"subtitle/caption ({ext})")
            return out

    # Checksum/signature sidecar.
    for ext in SIDECAR_EXTENSIONS:
        if path.endswith(ext):
            out["type"] = ("checksum_sidecar"
                           if ext in (".sha256", ".sha512", ".md5")
                           else "signature_sidecar")
            out["reasons"].append(f"sidecar file ({ext})")
            return out

    # Direct file via extension.
    for ext in PROGRESSIVE_MEDIA_EXTENSIONS:
        if path.endswith(ext):
            out["type"] = "direct_file"
            out["is_primary"] = True
            out["container"] = ext.lstrip(".")
            out["reasons"].append(f"direct file extension ({ext})")
            return out

    # MIME-based direct file (extensionless URL with binary MIME).
    for prefix in BINARY_MIME_PREFIXES:
        if mime_l.startswith(prefix):
            out["type"] = "extensionless_file"
            out["is_primary"] = True
            out["reasons"].append(f"binary MIME without extension "
                                   f"({mime_l})")
            return out

    return out

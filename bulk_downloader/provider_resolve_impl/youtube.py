"""provider_resolve_impl.youtube -- verbatim youtube resolver from provider_resolve.py."""

from __future__ import annotations
import json
import os
import re
import shutil
import subprocess
from typing import Callable, Dict, List, Optional, Tuple
from urllib.parse import quote as _urlquote, urlparse as _urlparse, parse_qs as _parse_qs

from ._common import _coerce_int


import sys as _sys  # H-07 shim capture
_PR_SHIM_REF = _sys.modules.get("bulk_downloader.provider_resolve")


def __pr_shim():
    # Return the provider_resolve SHIM object THIS module was loaded with.
    # Captured at import time (when our own shim loaded us) so that if the
    # test suite drops bulk_downloader.* from sys.modules and a fresh copy is
    # imported, the function a test invokes (via its collection-time `pr`)
    # still reads the SAME object that test monkeypatched -- a call-time
    # sys.modules re-fetch would return the reloaded twin and miss the patch.
    global _PR_SHIM_REF
    if _PR_SHIM_REF is None:
        import bulk_downloader.provider_resolve as _m
        _PR_SHIM_REF = _m
    return _PR_SHIM_REF


_YOUTUBE_WATCH_URL_TMPL = "https://www.youtube.com/watch?v={video_id}"


_YT_PLAYER_RESPONSE_RE = re.compile(
    r"ytInitialPlayerResponse\s*=\s*(\{)"
)


def _slice_balanced_json(text: str, start_idx: int) -> Optional[str]:
    """Given ``text`` and an index pointing at an opening '{', return
    the substring through the matching closing '}' (inclusive), or
    None if braces don't balance before the string ends. Honors string
    literals so a '}' inside a JSON string doesn't decrement the
    counter. Used to slice ytInitialPlayerResponse out of inline JS.
    """
    if start_idx >= len(text) or text[start_idx] != "{":
        return None
    depth = 0
    in_str = False
    escape = False
    for i in range(start_idx, len(text)):
        ch = text[i]
        if in_str:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[start_idx:i + 1]
    return None


_YT_VIDEO_ID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")


_YT_CIPHER_SUBPROC_TIMEOUT_SECONDS = 30


_YT_CIPHER_YTDLP_PATH_CACHE: List[Optional[str]] = [...]


def _yt_cipher_backend() -> str:
    """Read ``BD_YOUTUBE_CIPHER`` env var; return canonicalized backend.

    Returns one of ``"off"``, ``"yt-dlp"``, ``"player-js"``.

    Unknown or unset values map to ``"off"`` — fail closed. We don't log a
    warning here because the default IS off; an empty/missing env var is
    the expected case for nearly every install.
    """
    raw = os.environ.get("BD_YOUTUBE_CIPHER", "").strip().lower()
    # v3.66.315 (CLI->GUI parity): store key `youtube_cipher` overrides the env seed.
    try:
        from bulk_downloader import global_config as _gc
        _s = _gc.get("youtube_cipher", None)
        if _s not in (None, ""):
            raw = str(_s).strip().lower()
    except Exception:
        pass
    if raw in ("yt-dlp", "ytdlp", "yt_dlp"):
        return "yt-dlp"
    if raw in ("player-js", "playerjs", "player_js"):
        return "player-js"
    return "off"


def _yt_cipher_ytdlp_path() -> Optional[str]:
    """Memoized ``shutil.which("yt-dlp")``. Returns the absolute path or
    ``None`` if yt-dlp is not on PATH.

    Called at most once per process (more precisely: once per module
    load). The PATH itself can change at runtime in test fixtures —
    callers that need to bust the cache can assign
    ``_YT_CIPHER_YTDLP_PATH_CACHE[0] = ...`` to reset.
    """
    if _YT_CIPHER_YTDLP_PATH_CACHE[0] is ...:
        _YT_CIPHER_YTDLP_PATH_CACHE[0] = shutil.which("yt-dlp")
    return _YT_CIPHER_YTDLP_PATH_CACHE[0]


# The historical helper remains a monkeypatch seam for focused cipher tests.
# Production must not consult its PATH cache: the shared resolver owns the
# current command-selection policy for every real yt-dlp consumer.
_YT_CIPHER_DEFAULT_PATH_HELPER = _yt_cipher_ytdlp_path


def _decipher_signed_formats_ytdlp(
    video_id: str,
    *,
    embed_url: Optional[str] = None,
    _run: Optional[Callable] = None,
) -> Tuple[List[dict], Optional[str]]:
    """Shell out to ``yt-dlp --dump-single-json``; convert each format with
    a direct ``url`` to a candidate.

    ``_run`` is a test seam. Production callers pass nothing and we
    resolve ``subprocess.run`` at call time (so module-level monkeypatches
    on ``subprocess.run`` flow through the dispatcher path, not just
    through direct calls to this function). Tests can either pass an
    explicit ``_run`` here or monkeypatch ``subprocess.run`` on the
    ``subprocess`` module.

    Returns ``(candidates, error)``. ``error`` is ``None`` on success.
    """
    _pr = __pr_shim()  # H-07: the shim instance THIS module was loaded with
    if _run is None:
        _run = subprocess.run
    if not _YT_VIDEO_ID_RE.match(video_id):
        return [], (
            f"yt-dlp dispatch rejected video_id={video_id!r}: "
            "must match ^[A-Za-z0-9_-]{11}$"
        )

    path_helper = _pr._yt_cipher_ytdlp_path
    if path_helper is not _YT_CIPHER_DEFAULT_PATH_HELPER:
        # A shim monkeypatch is an explicit test/integration injection.  It is
        # deliberately distinct from the legacy cached production lookup.
        injected_path = path_helper()
        ytdlp_argv = (injected_path,) if injected_path else None
    else:
        ytdlp_argv = None
    if ytdlp_argv is None:
        # Use the single canonical route in production.  In particular, do
        # not let a stale cipher-local PATH cache bypass console/module
        # precedence selected by status, runner fallback, and the plugin.
        from bulk_downloader import ytdlp_updater
        ytdlp_argv = ytdlp_updater.resolve_ytdlp_argv()
    if not ytdlp_argv:
        return [], (
            "yt-dlp not installed; install yt-dlp or set "
            "BD_YOUTUBE_CIPHER=off"
        )

    watch_url = f"https://www.youtube.com/watch?v={video_id}"
    argv = list(ytdlp_argv) + [
        "--dump-single-json",
        "--no-warnings",
        "--no-playlist",
        "--skip-download",
        watch_url,
    ]
    try:
        proc = _run(
            argv,
            capture_output=True,
            timeout=_YT_CIPHER_SUBPROC_TIMEOUT_SECONDS,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return [], (
            f"yt-dlp subprocess timed out after "
            f"{_YT_CIPHER_SUBPROC_TIMEOUT_SECONDS}s on "
            f"video_id={video_id}"
        )
    except (OSError, FileNotFoundError) as ex:
        return [], (
            f"yt-dlp subprocess failed to launch: "
            f"{type(ex).__name__}: {ex}"
        )

    if proc.returncode != 0:
        stderr = (proc.stderr or b"")
        if isinstance(stderr, (bytes, bytearray)):
            stderr_text = stderr.decode("utf-8", errors="replace")
        else:
            stderr_text = str(stderr)
        first_line = stderr_text.splitlines()[0] if stderr_text else ""
        return [], (
            f"yt-dlp subprocess failed with exit code {proc.returncode}: "
            f"{first_line}"
        )

    stdout = proc.stdout or b""
    if isinstance(stdout, (bytes, bytearray)):
        stdout_text = stdout.decode("utf-8", errors="replace")
    else:
        stdout_text = str(stdout)

    try:
        data = json.loads(stdout_text)
    except (ValueError, AttributeError) as ex:
        return [], f"yt-dlp output not parseable: {ex}"
    if not isinstance(data, dict):
        return [], "yt-dlp output is not a JSON object"

    formats = data.get("formats")
    if not isinstance(formats, list):
        return [], (
            f"yt-dlp returned no formats with direct URLs for "
            f"video_id={video_id}"
        )

    candidates: List[dict] = []
    for fmt in formats:
        if not isinstance(fmt, dict):
            continue
        direct = fmt.get("url")
        if not isinstance(direct, str) or not direct:
            continue
        # yt-dlp's schema differs slightly from ytInitialPlayerResponse's
        # streamingData.formats: ``ext`` instead of mimeType, ``vcodec``
        # /``acodec`` instead of a combined codec string, ``format_note``
        # /``format_id`` for the human label.
        height = _coerce_int(fmt.get("height"))
        width = _coerce_int(fmt.get("width"))
        bitrate = _coerce_int(
            fmt.get("tbr") or fmt.get("vbr") or fmt.get("abr")
        )
        fps_val = _coerce_int(fmt.get("fps"))
        size_bytes = _coerce_int(
            fmt.get("filesize") or fmt.get("filesize_approx")
        )
        itag = fmt.get("format_id")
        vcodec = fmt.get("vcodec") or ""
        acodec = fmt.get("acodec") or ""
        ext = fmt.get("ext") or ""
        codec_parts = [p for p in (vcodec, acodec) if p and p != "none"]
        codec = "/".join(codec_parts) if codec_parts else (ext or None)
        quality_label = (
            fmt.get("format_note")
            or (f"{height}p" if height else None)
            or fmt.get("format")
        )
        res = None
        if height:
            res = {
                "height": height,
                "label": quality_label or f"{height}p",
                "rank": height,
            }
            if width:
                res["width"] = width
        score = 80 + (height // 100 if height else 0)
        candidates.append({
            "url": direct,
            "source_type": "youtube_resolved_cipher_ytdlp",
            "score": score,
            "resolution": res,
            "codec": codec,
            "fps": float(fps_val) if fps_val else None,
            "size_bytes": size_bytes,
            "bitrate": bitrate,
            "found_in": "provider_resolved:youtube",
            "resolved_from": embed_url,
            "provider_resolved": True,
            "itag": itag,
            "reasons": [
                f"YouTube deciphered format {quality_label or 'unknown'} "
                f"(itag={itag}, codec={codec or 'unknown'}, "
                f"via yt-dlp subprocess)"
            ],
            "warnings": [
                "deciphered URL has a short undocumented TTL; "
                "do not cache aggressively"
            ],
            "requires_click": False,
        })

    if not candidates:
        return [], (
            f"yt-dlp returned no formats with direct URLs for "
            f"video_id={video_id}"
        )

    return candidates, None


_YT_JS_URL_RE = re.compile(r'"jsUrl"\s*:\s*"([^"]+)"')


_YT_DECIPHER_FN_RE = re.compile(
    r'(?P<name>[a-zA-Z0-9$_]{1,128})\s*=\s*function\(\s*(?P<arg>[a-zA-Z0-9$_]{1,128})\s*\)\s*\{\s*'
    r'(?P=arg)\s*=\s*(?P=arg)\.split\(\s*(?:""|\'\')\s*\)\s*;'
    r'(?P<body>.*?)'
    r'return\s+(?P=arg)\.join\(\s*(?:""|\'\')\s*\)',
    re.DOTALL,
)


_YT_DECIPHER_STMT_RE = re.compile(
    r'(?P<obj>[a-zA-Z0-9$_]{1,128})\.(?P<method>[a-zA-Z0-9$_]{1,128})\('
    r'\s*[a-zA-Z0-9$_]{1,128}\s*(?:,\s*(?P<arg>\d+)\s*)?\)')


_YT_TRANSFORM_METHOD_RE = re.compile(
    r'(?P<name>[a-zA-Z0-9$_]{1,128})\s*:\s*function\([^)]*\)\s*\{(?P<mbody>[^}]*)\}')


_YT_PLAYER_JS_HOST_SUFFIXES = (".youtube.com", ".google.com",
                               ".googlevideo.com", ".ytimg.com")


def _classify_yt_transform(body: str) -> Optional[str]:
    """Classify one transform-object method body as one of the three
    YouTube signature operations, or None if unrecognized (→ fail loud).

      * reverse — ``a.reverse()``
      * splice  — ``a.splice(0,b)``
      * swap    — ``var c=a[0];a[0]=a[b%a.length];a[b%a.length]=c``
    """
    if "reverse" in body:
        return "reverse"
    if "splice" in body:
        return "splice"
    if "a[0]" in body or "%" in body:
        return "swap"
    return None


def _build_yt_decipher_ops(
    player_js: str,
) -> Tuple[Optional[List[Tuple[str, int]]], Optional[str]]:
    """Parse the decipher pipeline out of the player JS.

    Returns ``(ops, None)`` on success, where ``ops`` is an ordered list
    of ``(operation, arg)`` tuples, or ``(None, error)`` on any parse
    failure (the caller surfaces this as a fail-loud rotation error).
    """
    fm = _YT_DECIPHER_FN_RE.search(player_js)
    if not fm:
        return None, ("decipher function "
                      "(name=function(a){a=a.split(\"\")...}) not found")
    body = fm.group("body")
    calls = list(_YT_DECIPHER_STMT_RE.finditer(body))
    if not calls:
        return None, "decipher function body contained no transform calls"
    obj_name = calls[0].group("obj")
    seq = [(c.group("method"), int(c.group("arg") or 0)) for c in calls]

    om = re.search(r'(?:var\s+)?' + re.escape(obj_name) + r'\s*=\s*\{',
                   player_js)
    if not om:
        return None, f"transform object {obj_name!r} definition not found"
    brace_idx = player_js.find("{", om.start())
    if brace_idx < 0:
        return None, f"transform object {obj_name!r} opening brace not found"
    obj_blob = _slice_balanced_json(player_js, brace_idx)
    if not obj_blob:
        return None, f"transform object {obj_name!r} braces unbalanced"

    method_ops: Dict[str, str] = {}
    for mm in _YT_TRANSFORM_METHOD_RE.finditer(obj_blob):
        op = _classify_yt_transform(mm.group("mbody"))
        if op is None:
            return None, (f"unrecognized transform method "
                          f"{mm.group('name')!r}")
        method_ops[mm.group("name")] = op
    if not method_ops:
        return None, f"transform object {obj_name!r} had no parseable methods"

    ops: List[Tuple[str, int]] = []
    for method, arg in seq:
        op = method_ops.get(method)
        if op is None:
            return None, f"call to undefined transform method {method!r}"
        ops.append((op, arg))
    return ops, None


def _apply_yt_decipher_ops(sig: str, ops: List[Tuple[str, int]]) -> str:
    """Apply the parsed operation list to a signature string."""
    a = list(sig)
    for op, n in ops:
        if op == "reverse":
            a.reverse()
        elif op == "splice":
            del a[:n]
        elif op == "swap":
            if a:
                n = n % len(a)
                a[0], a[n] = a[n], a[0]
    return "".join(a)


def _decipher_signed_formats_playerjs(
    video_id: str,
    *,
    embed_url: Optional[str] = None,
    http_get: HttpGet,
) -> Tuple[List[dict], Optional[str]]:
    """In-process player-JS decipher (C4-B).

    Fetches ``youtube.com/watch?v=<id>``, slices ``signatureCipher``
    formats out of ``ytInitialPlayerResponse``, fetches the watch page's
    player JS (``jsUrl``), parses the signature-transform pipeline, and
    deciphers each signed payload into a direct URL.

    Returns ``(candidates, error)``; ``error`` is None on success. On
    cipher rotation (unparseable player JS) it returns a clear
    ``parse failed for player JS at <url>`` error and emits no
    candidates — never a silent fallback. See the posture/scope note on
    the section header above.
    """
    if not _YT_VIDEO_ID_RE.match(video_id):
        return [], (
            f"player-js dispatch rejected video_id={video_id!r}: "
            "must match ^[A-Za-z0-9_-]{11}$"
        )

    watch_url = _YOUTUBE_WATCH_URL_TMPL.format(
        video_id=_urlquote(str(video_id)))
    try:
        status, _headers, body = http_get(watch_url)
    except Exception as ex:
        return [], (
            f"player-js watch request failed: {type(ex).__name__}: {ex}")
    if status == 404:
        return [], ("player-js watch returned 404 — video_id may be "
                    "invalid, deleted, or private")
    if status >= 400:
        return [], f"player-js watch returned HTTP {status}"
    try:
        html = (body.decode("utf-8", errors="replace")
                if isinstance(body, (bytes, bytearray)) else str(body))
    except Exception as ex:
        return [], f"player-js watch body not decodable: {ex}"

    m = _YT_PLAYER_RESPONSE_RE.search(html)
    if not m:
        return [], ("player-js: watch page had no ytInitialPlayerResponse "
                    "(consent interstitial, age-gate, or region block?)")
    blob = _slice_balanced_json(html, m.start(1))
    if not blob:
        return [], "player-js: ytInitialPlayerResponse JSON unbalanced"
    try:
        data = json.loads(blob)
    except (ValueError, AttributeError) as ex:
        return [], f"player-js: ytInitialPlayerResponse not parseable: {ex}"
    if not isinstance(data, dict):
        return [], "player-js: ytInitialPlayerResponse is not an object"

    playability = data.get("playabilityStatus") or {}
    pstatus = (playability.get("status")
               if isinstance(playability, dict) else None)
    if pstatus and pstatus != "OK":
        reason = (playability.get("reason")
                  if isinstance(playability, dict) else None) or pstatus
        return [], (
            f"player-js: playabilityStatus={pstatus} ({reason}) — not "
            "deciphering (age-gated, region-locked, DRM, or private)")

    streaming = data.get("streamingData") or {}
    signed: List[Tuple[dict, bool]] = []
    for key, adaptive_marker in (("formats", False), ("adaptiveFormats", True)):
        lst = streaming.get(key)
        if isinstance(lst, list):
            for fmt in lst:
                if (isinstance(fmt, dict) and not fmt.get("url")
                        and (fmt.get("signatureCipher") or fmt.get("cipher"))):
                    signed.append((fmt, adaptive_marker))
    if not signed:
        return [], (
            f"player-js: no signatureCipher formats for video_id={video_id} "
            "(nothing to decipher — formats may already carry direct urls)")

    jm = _YT_JS_URL_RE.search(html)
    if not jm:
        return [], (
            f"player-js: could not locate jsUrl in watch page for "
            f"video_id={video_id} (YouTube markup changed — parser needs "
            "updating)")
    js_path = jm.group(1).replace("\\/", "/").replace("\\u002F", "/")
    js_url = (js_path if js_path.startswith("http")
              else "https://www.youtube.com" + js_path)
    host = (_urlparse(js_url).hostname or "").lower()
    if not (host in ("youtube.com", "www.youtube.com")
            or any(host.endswith(s) for s in _YT_PLAYER_JS_HOST_SUFFIXES)):
        return [], (f"player-js: refusing to fetch player JS from "
                    f"non-YouTube host {host!r}")

    try:
        jstatus, _jheaders, jbody = http_get(js_url)
    except Exception as ex:
        return [], (f"player-js: player JS request failed "
                    f"({type(ex).__name__}: {ex})")
    if jstatus >= 400:
        return [], f"player-js: player JS fetch returned HTTP {jstatus} at {js_url}"
    try:
        player_js = (jbody.decode("utf-8", errors="replace")
                     if isinstance(jbody, (bytes, bytearray)) else str(jbody))
    except Exception as ex:
        return [], f"player-js: player JS body not decodable: {ex}"

    ops, perr = _build_yt_decipher_ops(player_js)
    if perr:
        # FAIL LOUD on rotation — the operator needs to know the parser is
        # stale, not get a silent empty result.
        return [], f"player-js: parse failed for player JS at {js_url}: {perr}"

    candidates: List[dict] = []
    for fmt, adaptive_marker in signed:
        sc = fmt.get("signatureCipher") or fmt.get("cipher") or ""
        try:
            q = _parse_qs(sc)
        except Exception:
            continue
        s_vals = q.get("s")
        url_vals = q.get("url")
        if not s_vals or not url_vals:
            continue
        s = s_vals[0]
        base = url_vals[0]
        sp = (q.get("sp") or ["signature"])[0]
        try:
            deciphered = _apply_yt_decipher_ops(s, ops)
        except Exception as ex:
            return [], (f"player-js: decipher failed applying ops at "
                        f"{js_url}: {type(ex).__name__}: {ex}")
        sep = "&" if "?" in base else "?"
        final_url = f"{base}{sep}{sp}={_urlquote(deciphered)}"

        height = _coerce_int(fmt.get("height"))
        width = _coerce_int(fmt.get("width"))
        bitrate = _coerce_int(fmt.get("bitrate") or fmt.get("averageBitrate"))
        fps_val = _coerce_int(fmt.get("fps"))
        size_bytes = _coerce_int(fmt.get("contentLength"))
        mime = fmt.get("mimeType") or ""
        quality_label = fmt.get("qualityLabel") or (
            f"{height}p" if height else None)
        itag = fmt.get("itag")
        res = None
        if height:
            res = {"height": height,
                   "label": quality_label or f"{height}p",
                   "rank": height}
            if width:
                res["width"] = width
        score = 80 + (height // 100 if height else 0)
        candidates.append({
            "url": final_url,
            "source_type": "youtube_resolved_cipher_playerjs",
            "score": score,
            "resolution": res,
            "codec": mime or None,
            "fps": float(fps_val) if fps_val else None,
            "size_bytes": size_bytes,
            "bitrate": bitrate,
            "found_in": "provider_resolved:youtube",
            "resolved_from": embed_url,
            "provider_resolved": True,
            "itag": itag,
            "reasons": [
                f"YouTube {'adaptive ' if adaptive_marker else ''}deciphered "
                f"format {quality_label or 'unknown'} (itag={itag}, "
                f"mime={mime or 'unknown'}, via in-process player-JS)"
            ],
            "warnings": [
                "deciphered URL has a short undocumented TTL; "
                "do not cache aggressively",
                "in-process signatureCipher decipher (player-js backend); "
                "breaks on YouTube cipher rotation",
            ],
            "requires_click": False,
        })

    if not candidates:
        return [], (
            f"player-js: deciphered 0 of {len(signed)} signed format(s) for "
            f"video_id={video_id} (signatureCipher missing s/url params)")
    return candidates, None


def _decipher_signed_formats(
    video_id: str,
    signed_count: int,
    *,
    embed_url: Optional[str] = None,
    http_get: HttpGet,
) -> Tuple[List[dict], Optional[str]]:
    """Dispatch to the configured cipher backend.

    Reads ``BD_YOUTUBE_CIPHER`` at call time (not at module load) so
    tests can flip the env var between calls without reimport gymnastics.
    Returns ``(candidates, error)``. ``candidates == []`` and
    ``error is not None`` whenever no URLs were produced.

    Called only after resolve_youtube confirms ``signed_count > 0`` and
    ``candidates`` is empty — so this function is never on the
    happy-path hot loop.
    """
    backend = _yt_cipher_backend()
    if backend == "off":
        return [], (
            f"youtube has {signed_count} signed format(s) but "
            "BD_YOUTUBE_CIPHER=off; set to yt-dlp or player-js to "
            "decipher"
        )
    if backend == "yt-dlp":
        return _decipher_signed_formats_ytdlp(
            video_id, embed_url=embed_url,
        )
    if backend == "player-js":
        return _decipher_signed_formats_playerjs(
            video_id, embed_url=embed_url, http_get=http_get,
        )
    # Unreachable — _yt_cipher_backend canonicalizes to off/yt-dlp/player-js
    # — but keep the explicit error in case someone monkeypatches the
    # backend reader and breaks the contract.
    return [], (
        f"unknown BD_YOUTUBE_CIPHER backend: {backend!r}"
    )


def resolve_youtube(
    ids: dict,
    *,
    embed: Optional[dict] = None,
    http_get: HttpGet,
) -> Tuple[List[dict], Optional[str]]:
    """Resolve a YouTube embed by GETting the watch page and parsing
    its inline ``ytInitialPlayerResponse`` JSON.

    Approach
    --------
    The resolver fetches ``youtube.com/watch?v=<id>``, locates the
    ``ytInitialPlayerResponse = {...}`` inline JS assignment, and
    slices out the JSON. ``streamingData`` is then inspected for:

      * ``hlsManifestUrl`` (livestream or HLS-only VOD) — emitted at
        score 90.
      * ``formats[]`` entries with a direct ``url`` — emitted at
        ``80 + height//100``. ``itag``, ``qualityLabel``, ``fps``,
        ``mimeType``, and ``contentLength`` are surfaced when present.
      * ``adaptiveFormats[]`` entries with a direct ``url`` (rare;
        usually only when YouTube's experiment buckets disable signing)
        — emitted with a ``_adaptive`` suffix on source_type so
        consumers can tell them apart from premerged formats.

    Formats whose only addressable field is ``signatureCipher`` are
    skipped; we record a ``signed_formats_skipped`` count and surface
    it in the error string if no direct-URL candidates were found
    after the walk.

    What we don't do
    ----------------
    No subprocess (``yt-dlp``), no signature deciphering, no
    age-gated bypass. If the page doesn't contain a parseable
    ``ytInitialPlayerResponse`` or ``playabilityStatus`` isn't ``OK``,
    the resolver returns a clear error — no silent failure paths.

    The default ``_default_http_get`` issues a plain GET with our
    standard User-Agent; YouTube serves the player response to that
    UA without cookies for unrestricted content. Callers that need
    cookie-bearing extraction (e.g. for age-restricted content) must
    inject their own ``http_get`` via the dispatcher.
    """
    _pr = __pr_shim()  # H-07: the shim instance THIS module was loaded with
    _decipher_signed_formats = _pr._decipher_signed_formats
    if not isinstance(ids, dict):
        ids = {}

    video_id = ids.get("video_id")
    if not video_id:
        # _extract_provider_ids' YouTube patterns yield video_id_embed
        # / video_id_short / video_id_v; the deep_detect glue copies
        # the first of those to a canonical 'video_id' key (see
        # _extract_provider_ids in deep_detect.py, v3.66.20 update).
        # Fall back to the legacy keys for callers wiring ids by hand.
        for k in ("video_id_embed", "video_id_short", "video_id_v"):
            v = ids.get(k)
            if isinstance(v, str) and v:
                video_id = v
                break
    if not video_id:
        return [], "missing video_id"

    url = _YOUTUBE_WATCH_URL_TMPL.format(video_id=_urlquote(str(video_id)))
    try:
        status, _headers, body = http_get(url)
    except Exception as ex:
        return [], (
            f"youtube watch request failed: {type(ex).__name__}: {ex}"
        )

    if status == 404:
        return [], (
            "youtube watch returned 404 — video_id may be invalid, "
            "deleted, or private"
        )
    if status >= 400:
        return [], f"youtube watch returned HTTP {status}"

    try:
        html = body.decode("utf-8", errors="replace") \
            if isinstance(body, (bytes, bytearray)) else str(body)
    except Exception as ex:
        return [], f"youtube watch body not decodable: {ex}"

    # Locate ytInitialPlayerResponse ------------------------------------
    m = _YT_PLAYER_RESPONSE_RE.search(html)
    if not m:
        return [], (
            "youtube watch page did not contain ytInitialPlayerResponse "
            "— may be a consent interstitial, age-gate, or region block"
        )
    blob = _slice_balanced_json(html, m.start(1))
    if not blob:
        return [], "youtube ytInitialPlayerResponse JSON unbalanced"
    try:
        data = json.loads(blob)
    except (ValueError, AttributeError) as ex:
        return [], f"youtube ytInitialPlayerResponse not parseable: {ex}"
    if not isinstance(data, dict):
        return [], "youtube ytInitialPlayerResponse is not an object"

    # Playability status -------------------------------------------------
    playability = data.get("playabilityStatus") or {}
    pstatus = playability.get("status") if isinstance(playability, dict) else None
    if pstatus and pstatus != "OK":
        reason = (playability.get("reason") if isinstance(playability, dict)
                  else None) or pstatus
        return [], (
            f"youtube playabilityStatus={pstatus} ({reason}); skipping "
            "(age-gated, region-locked, DRM, or private)"
        )

    streaming = data.get("streamingData")
    hls_url = None
    formats: List[dict] = []
    adaptive: List[dict] = []
    if isinstance(streaming, dict):
        hu = streaming.get("hlsManifestUrl")
        if isinstance(hu, str) and hu:
            hls_url = hu
        f = streaming.get("formats")
        if isinstance(f, list):
            formats = [x for x in f if isinstance(x, dict)]
        af = streaming.get("adaptiveFormats")
        if isinstance(af, list):
            adaptive = [x for x in af if isinstance(x, dict)]

    embed_url = (embed or {}).get("url")
    candidates: List[dict] = []
    signed_skipped = 0

    if hls_url:
        candidates.append({
            "url": hls_url,
            "source_type": "youtube_resolved_hls",
            "score": 90,
            "resolution": None,
            "codec": None,
            "fps": None,
            "size_bytes": None,
            "found_in": "provider_resolved:youtube",
            "resolved_from": embed_url,
            "provider_resolved": True,
            "reasons": ["YouTube hlsManifestUrl from streamingData"],
            "warnings": [],
            "requires_click": False,
        })

    def _emit(fmt: dict, *, adaptive_marker: bool) -> None:
        nonlocal signed_skipped
        direct = fmt.get("url")
        if not isinstance(direct, str) or not direct:
            # signatureCipher path — we can't decipher in-process.
            if fmt.get("signatureCipher") or fmt.get("cipher"):
                signed_skipped += 1
            return
        height = _coerce_int(fmt.get("height"))
        width = _coerce_int(fmt.get("width"))
        bitrate = _coerce_int(fmt.get("bitrate") or fmt.get("averageBitrate"))
        fps_val = _coerce_int(fmt.get("fps"))
        size_bytes = _coerce_int(fmt.get("contentLength"))
        mime = fmt.get("mimeType") or ""
        quality_label = fmt.get("qualityLabel") or (
            f"{height}p" if height else None
        )
        itag = fmt.get("itag")
        res = None
        if height:
            res = {
                "height": height,
                "label": quality_label or f"{height}p",
                "rank": height,
            }
            if width:
                res["width"] = width
        score = 80 + (height // 100 if height else 0)
        source_type = ("youtube_resolved_adaptive" if adaptive_marker
                       else "youtube_resolved")
        candidates.append({
            "url": direct,
            "source_type": source_type,
            "score": score,
            "resolution": res,
            "codec": mime or None,
            "fps": float(fps_val) if fps_val else None,
            "size_bytes": size_bytes,
            "bitrate": bitrate,
            "found_in": "provider_resolved:youtube",
            "resolved_from": embed_url,
            "provider_resolved": True,
            "itag": itag,
            "reasons": [
                f"YouTube {'adaptive ' if adaptive_marker else ''}"
                f"format {quality_label or 'unknown'} "
                f"(itag={itag}, mime={mime or 'unknown'})"
            ],
            "warnings": [],
            "requires_click": False,
        })

    for fmt in formats:
        _emit(fmt, adaptive_marker=False)
    for fmt in adaptive:
        _emit(fmt, adaptive_marker=True)

    if not candidates:
        if signed_skipped:
            # v3.66.26: dispatch to configured cipher backend. When
            # BD_YOUTUBE_CIPHER=off (default), the dispatcher returns an
            # error string that satisfies v3.66.20's pinned contract
            # (contains "signed" + the count). When set to yt-dlp or
            # player-js, success becomes possible — candidates carry
            # source_type ``youtube_resolved_cipher_*``.
            cipher_cands, cipher_err = _decipher_signed_formats(
                str(video_id),
                signed_skipped,
                embed_url=embed_url,
                http_get=http_get,
            )
            if cipher_cands:
                return cipher_cands, None
            return [], cipher_err
        return [], (
            "youtube streamingData had no usable formats "
            "(no hlsManifestUrl and no formats with direct urls)"
        )

    return candidates, None

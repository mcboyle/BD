"""ytdlp_extractor -- GH-2a (v3.66.693): the yt-dlp -> plugin ``@extractor``
adapter.

691 wired the plugin ``@extractor`` *dispatch*: during capture,
``ExtractorsMixin._try_plugin_extractor`` consults
``plugins.get_extractor(site_id)`` and, given a ``{"video_url", ...}`` result,
downloads it via the existing ``_do_direct_http_download`` (progressive http
only; ``is_hls``/``{}``/``None`` fall through). GH-2a supplies a yt-dlp *shim*
that plugs straight into that dispatch.

Shape (all pure/injectable so no live yt-dlp is needed to test):

  * ``build_ytdlp_info_cmd``   -- pure, smuggle-safe builder for the info CLI
    (``yt-dlp -j --skip-download``; metadata probe, no download). Mirrors the
    ``_build_ytdlp_cmd`` conventions: ``socks5h://`` remote-DNS proxy, a
    maintained Netscape ``.txt`` cookie file, and a bare ``--`` option
    terminator so a URL beginning with ``-`` is always a positional target.
  * ``info_to_extractor_result`` -- pure mapper from yt-dlp ``-j`` JSON to the
    extractor contract. Picks the highest-resolution *progressive muxed http*
    format (a single downloadable file). If only HLS/DASH manifests exist it
    returns ``{"video_url": <manifest>, "is_hls": True}`` so the 691 dispatch
    defers (HLS via plugin extractor is a follow-on). Nothing usable -> ``{}``.
  * ``make_ytdlp_extractor`` -- the ``fn(url, ctx) -> dict`` shim: resolve the
    yt-dlp binary (runtime gate; missing -> ``{}`` -> fall through), run the
    info cmd, parse the first JSON object, map it. A nonzero exit or unparseable
    output degrades to ``{}`` (fall through, never raise).
  * ``register_ytdlp_extractor`` / ``maybe_register_from_config`` -- opt-in.
    A site that sets the *undeclared* site-cfg key ``ytdlp_extractor`` truthy
    registers a shim under its ``site_id`` via ``plugins.register_extractor``.
    Undeclared => invisible to the config/env inventory (a backend-only
    opt-in; no GLOBAL_CONFIG_SCHEMA / CFG_FIELDS / site_editor declaration).

This module imports only stdlib at load time; the ``plugins`` edge is
function-local (deferred to registration), so importing it is cheap and
cycle-free.
"""
from __future__ import annotations

import json as _json
import os as _os
import shutil as _shutil
import subprocess as _subprocess
from typing import Callable, Optional


# ── proxy helper (local; mirrors runner_extractors._socks_remote_dns) ──
def _socks_remote_dns(proxy_url: Optional[str]) -> str:
    """Force remote DNS on a SOCKS proxy url (``socks5://`` -> ``socks5h://``)
    so an info probe resolves the target host *through* the tunnel instead of
    leaking it on the clear interface. Empty/None -> "" (no proxy)."""
    p = (proxy_url or "").strip()
    if not p:
        return ""
    if p.startswith("socks5://"):
        return "socks5h://" + p[len("socks5://"):]
    return p


# ── build_ytdlp_info_cmd (pure) ───────────────────────────────────────
def build_ytdlp_info_cmd(*, ytdlp, url: str, cookie_file: str = "",
                         proxy_url: Optional[str] = None) -> list:
    """Pure builder for the yt-dlp info-probe CLI (unit-testable, no side
    effects). ``-j --skip-download`` prints one JSON object per video and
    downloads nothing; ``--no-playlist`` keeps it to the single target."""
    prefix = list(ytdlp) if isinstance(ytdlp, (list, tuple)) else [ytdlp]
    cmd = prefix + ["-j", "--skip-download",
           "--no-warnings", "--no-progress", "--no-playlist"]
    proxy = _socks_remote_dns(proxy_url)
    if proxy:
        cmd += ["--proxy", proxy]
    # yt-dlp wants Netscape-format cookies; only a maintained .txt is usable.
    if cookie_file and cookie_file.endswith(".txt") and _os.path.exists(cookie_file):
        cmd += ["--cookies", cookie_file]
    # F-RUN01-02: terminate options with a bare '--' so a URL beginning with
    # '-' is a positional target, never smuggled into yt-dlp's own flag surface.
    cmd.append("--")
    cmd.append(url)
    return cmd


# ── info_to_extractor_result (pure mapper) ────────────────────────────
def _proto_is_http(proto: Optional[str]) -> bool:
    p = (proto or "")
    if not p.startswith("http"):
        return False
    return ("m3u8" not in p) and ("dash" not in p)


def _proto_is_hls_dash(proto: Optional[str]) -> bool:
    p = (proto or "")
    return ("m3u8" in p) or ("dash" in p)


def _is_muxed(fmt: dict) -> bool:
    """A single directly-downloadable file needs both a video and an audio
    stream present (yt-dlp uses ``"none"`` for an absent stream). A
    video-only / audio-only format would require muxing during a real
    download, which the 691 direct-http path does not do -> skip it."""
    vcodec = fmt.get("vcodec")
    acodec = fmt.get("acodec")
    return (vcodec not in (None, "", "none")) and (acodec not in (None, "", "none"))


def info_to_extractor_result(info) -> dict:
    """Map a yt-dlp ``-j`` info dict to the plugin ``@extractor`` contract.

    Returns ``{"video_url", "is_hls", "title"?, "ext"?}`` for a progressive
    http file (``is_hls=False``); ``{"video_url": <manifest>, "is_hls": True}``
    when only HLS/DASH is available (691 dispatch defers); ``{}`` otherwise.
    """
    if not isinstance(info, dict):
        return {}
    title = (info.get("title") or "").strip()
    top_ext = (info.get("ext") or "").strip()

    candidates = []          # (height, tbr, url, ext)
    hls_url = ""
    formats = info.get("formats")
    if isinstance(formats, list) and formats:
        for f in formats:
            if not isinstance(f, dict):
                continue
            u = (f.get("url") or "").strip()
            proto = f.get("protocol") or ""
            if _proto_is_hls_dash(proto):
                if not hls_url and u:
                    hls_url = u
                continue
            if not u or not _proto_is_http(proto):
                continue
            if not _is_muxed(f):
                continue
            candidates.append((
                int(f.get("height") or 0),
                float(f.get("tbr") or 0.0),
                u,
                (f.get("ext") or "").strip(),
            ))
    else:
        # No formats list: fall back to the top-level url + protocol.
        u = (info.get("url") or "").strip()
        proto = info.get("protocol") or ""
        if u and _proto_is_hls_dash(proto):
            hls_url = u
        elif u and _proto_is_http(proto):
            candidates.append((
                int(info.get("height") or 0),
                float(info.get("tbr") or 0.0),
                u,
                top_ext,
            ))

    if candidates:
        candidates.sort(key=lambda t: (t[0], t[1]))
        _h, _tbr, url, ext = candidates[-1]     # highest resolution / bitrate
        res = {"video_url": url, "is_hls": False}
        if title:
            res["title"] = title
        ext = ext or top_ext
        if ext:
            res["ext"] = ext
        return res

    if hls_url:
        res = {"video_url": hls_url, "is_hls": True}
        if title:
            res["title"] = title
        return res

    return {}


# ── subprocess runner (default; injectable for tests) ─────────────────
def _default_run(cmd: list):
    """Run the info cmd; return ``(returncode, stdout, stderr)``. A metadata
    probe is cheap -- bound it to 120s so a hung extractor can't wedge a
    worker. Never raises for a nonzero exit (that degrades to ``{}``)."""
    r = _subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    return (r.returncode, r.stdout or "", r.stderr or "")


# ── make_ytdlp_extractor (the shim) ───────────────────────────────────
def make_ytdlp_extractor(config: Optional[dict], *,
                         run: Optional[Callable] = None,
                         which: Optional[Callable] = None,
                         resolve: Optional[Callable] = None) -> Callable:
    """Build a ``fn(url, ctx) -> dict`` plugin ``@extractor`` shim backed by
    yt-dlp's ``-j`` info probe. ``run`` / ``which`` are injectable for tests
    (default: real subprocess + ``shutil.which``)."""
    cfg = dict(config or {})
    _run = run or _default_run
    _which = which or _shutil.which
    if resolve is None:
        def _resolve():
            from . import ytdlp_updater
            if which is None:
                return ytdlp_updater.resolve_ytdlp_argv()
            exe = _which("yt-dlp") or _which("youtube-dl")
            return ytdlp_updater.resolve_ytdlp_argv(exe) if exe else None
    else:
        _resolve = resolve

    def _extract(url: str, ctx: Optional[dict] = None) -> dict:
        ytdlp = _resolve()
        if not ytdlp:
            return {}                       # runtime gate: no binary -> fall through
        cookie_file = (cfg.get("cookie_file") or "").strip()
        proxy_url = (cfg.get("proxy_url") or cfg.get("vpn_socks_url") or "") or None
        cmd = build_ytdlp_info_cmd(ytdlp=ytdlp, url=url,
                                   cookie_file=cookie_file, proxy_url=proxy_url)
        try:
            rc, out, _err = _run(cmd)
        except Exception:
            return {}                       # timeout / exec error -> fall through
        if rc != 0 or not out:
            return {}
        # -j prints one JSON object per line; --no-playlist -> the first line
        # is the target video. Be robust to a stray trailing line.
        line = ""
        for ln in out.splitlines():
            if ln.strip():
                line = ln.strip()
                break
        if not line:
            return {}
        try:
            info = _json.loads(line)
        except Exception:
            return {}
        return info_to_extractor_result(info)

    return _extract


# ── register / opt-in ─────────────────────────────────────────────────
def register_ytdlp_extractor(site_id: str, config: Optional[dict], **kw) -> None:
    """Register a yt-dlp shim under ``site_id`` via ``plugins.register_extractor``
    so the 691 dispatch routes this site through yt-dlp. ``**kw`` (``run`` /
    ``which``) are forwarded to ``make_ytdlp_extractor`` for testing."""
    from . import plugins as _P     # function-local: deferred, cycle-free
    _P.register_extractor(site_id, make_ytdlp_extractor(config, **kw))


def maybe_register_from_config(site_id: str, config) -> bool:
    """Opt-in gate: register a yt-dlp extractor for ``site_id`` iff the
    *undeclared* site-cfg key ``ytdlp_extractor`` is truthy. Returns True iff a
    registration happened. Anything falsy / absent / non-dict -> no-op, False."""
    if not isinstance(config, dict):
        return False
    if not config.get("ytdlp_extractor"):
        return False
    register_ytdlp_extractor(site_id, config)
    return True

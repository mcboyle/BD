"""Row 722 (G5): API/media extraction fallback for SPA scene pages.

Measured live (tiny4k.com, Nuxt SPA, 2026-09-15): the rendered DOM held nav
and ad strips only -- no anchor, button or ``<source>`` a scraper could score,
so ``find_best_download`` returned None and the run ended in "No download
button found".  Yet the page itself had already fetched everything a human's
"Downloads" click uses: a same-site ``/api/members/releases/<slug>`` JSON with
``downloadOptions`` (label/format/quality/filename) and ``streams`` (direct
mp4 URLs per rendition), sent with a site header (``x-site``) that the API
refuses without (404 on a bare re-fetch).  An option without a URL of its own
is resolved through the record's ``downloads?filename=`` sub-resource, which
answers ``{"url": <signed CDN URL>}``.

This module is generic: it does not know site names.  It knows
  * how to REMEMBER the same-site ``/api/`` JSON the page fetched, together
    with the custom request headers the SPA sent (never cookie/authorization),
  * how to WALK such JSON for download-like options (keys matching
    download|files|sources|qualities|streams|renditions|formats with URL
    values, or filename-only options resolvable through the record),
  * how to RANK by resolution label, then size, API options before incidental
    page media,
and leaves the transfer to the runner's existing direct-download path.
The fallback is consulted ONLY when the DOM yielded no candidate (runner.py).
"""
from __future__ import annotations

import json
import os
import re
from typing import Any, Dict, Iterable, List, Optional
from urllib.parse import quote, urljoin, urlparse

MAX_RECORDS = 40            # per page; a SPA fetches a handful, ads fetch more
MAX_BODY_BYTES = 2_000_000  # a scene record is a few KB; skip catalog dumps

OPTION_KEY_RE = re.compile(
    r"^(download|downloads|downloadoptions|download_options|files|sources|"
    r"qualities|streams|renditions|formats|videos|media)$", re.I)
URL_KEY_RE = re.compile(r"^(url|src|href|file|link|download_url|downloadurl)$", re.I)
LABEL_KEY_RE = re.compile(r"^(quality|label|resolution|height|name|res)$", re.I)
# dl95-file-examples-5: ogg/ogv play inline in Chromium like mp4/webm, so a
# direct Ogg URL reaches this fallback with no download event -- admit it.
MEDIA_EXT_RE = re.compile(r"\.(mp4|m4v|mov|webm|mkv|ogv|ogg|m3u8|mpd)(\?|$)", re.I)
# Extensions a chosen file keeps on disk; anything else is saved as .mp4.
KEEP_FILE_EXTS = (".mp4", ".m4v", ".mov", ".webm", ".mkv", ".wmv", ".ogv", ".ogg")


def spa_file_ext(fname: str) -> str:
    """The extension to save a chosen spa-api file under (".mp4" by default)."""
    ext = os.path.splitext(fname or "")[1].lower()
    return ext if ext in KEEP_FILE_EXTS else ".mp4"


HEIGHT_RE = re.compile(r"(?<!\d)(240|360|480|540|720|1080|1440|2160|4320)(?:p|P)?(?!\d)")
# Request headers worth replaying on a same-site re-fetch: the SPA's own
# custom headers and content negotiation.  Never cookie / authorization --
# cookies travel with ``credentials: include`` and are never copied out.
REPLAY_HEADER_RE = re.compile(r"^(x-[a-z0-9_-]+|accept|content-type)$", re.I)


def _same_site(page_url: str, url: str) -> bool:
    try:
        a = urlparse(page_url).hostname or ""
        b = urlparse(url).hostname or ""
    except Exception:
        return False
    if not a or not b:
        return False
    a = a.lower(); b = b.lower()
    return a == b or a.endswith("." + b) or b.endswith("." + a)


def is_api_json_response(page_url: str, url: str, content_type: str) -> bool:
    """A same-site ``/api/`` response carrying JSON."""
    if not _same_site(page_url, url):
        return False
    path = urlparse(url).path or ""
    if "/api/" not in path and not path.endswith("/api"):
        return False
    return "json" in (content_type or "").lower()


def replayable_headers(headers: Dict[str, str]) -> Dict[str, str]:
    """The subset of a recorded request's headers a re-fetch may replay."""
    out: Dict[str, str] = {}
    for k, v in (headers or {}).items():
        if isinstance(k, str) and isinstance(v, str) and REPLAY_HEADER_RE.match(k):
            out[k.lower()] = v
    return out


class ApiCapture:
    """Remembers the same-site ``/api/`` JSON responses a page fetched.

    ``install(page)`` hooks Playwright's ``response`` event; bodies are read
    lazily in ``records()`` (after load), so the listener itself never blocks
    the page.  Bounded: MAX_RECORDS entries, MAX_BODY_BYTES each.
    """

    def __init__(self, page_url: str = ""):
        self.page_url = page_url
        self._responses: List[Any] = []

    def install(self, page) -> "ApiCapture":
        try:
            page.on("response", self._on_response)
        except Exception:
            pass
        return self

    def _on_response(self, response) -> None:
        try:
            if len(self._responses) >= MAX_RECORDS:
                return
            url = str(response.url)
            ct = ""
            try:
                ct = response.headers.get("content-type", "") or ""
            except Exception:
                ct = ""
            page_url = self.page_url
            if not page_url:
                try:
                    page_url = response.request.frame.url or url
                except Exception:
                    page_url = url
            if is_api_json_response(page_url, url, ct):
                self._responses.append(response)
        except Exception:
            pass

    def records(self) -> List[Dict[str, Any]]:
        """``[{url, headers, json}]`` for every remembered JSON response."""
        out: List[Dict[str, Any]] = []
        for resp in list(self._responses):
            try:
                if int(getattr(resp, "status", 200) or 200) >= 400:
                    continue
                body = resp.text()
                if not body or len(body) > MAX_BODY_BYTES:
                    continue
                data = json.loads(body)
            except Exception:
                continue
            try:
                req_headers = dict(resp.request.headers or {})
            except Exception:
                req_headers = {}
            out.append({"url": str(resp.url),
                        "headers": replayable_headers(req_headers),
                        "json": data})
        return out


def _height_of(*texts: Any) -> int:
    for t in texts:
        if t is None:
            continue
        if isinstance(t, (int, float)) and 100 <= int(t) <= 8640:
            return int(t)
        m = HEIGHT_RE.search(str(t))
        if m:
            return int(m.group(1))
        s = str(t).strip().lower()
        if s in ("4k", "uhd"):
            return 2160
        if s in ("hd", "fhd"):
            return 1080
    return 0


def _size_of(d: Dict[str, Any]) -> int:
    for k in ("size", "bytes", "filesize", "file_size", "sizeBytes"):
        v = d.get(k)
        try:
            if v is not None and int(v) > 0:
                return int(v)
        except Exception:
            continue
    return 0


def _first_url_value(d: Dict[str, Any]) -> str:
    for k, v in d.items():
        if isinstance(v, str) and URL_KEY_RE.match(str(k)) and v.strip():
            return v.strip()
    return ""


def _option_candidates(record_url: str, key: str, value: Any,
                       page_url: str) -> Iterable[Dict[str, Any]]:
    """Candidates from one download-like key's value (list or dict)."""
    items: List[Any] = []
    if isinstance(value, list):
        items = value
    elif isinstance(value, dict):
        # {"1080": url, "2160": url} or {"hd": {...}} shapes
        items = [{"label": k, "url": v} if isinstance(v, str) else
                 (dict(v, label=v.get("label") or k) if isinstance(v, dict) else v)
                 for k, v in value.items()]
    for it in items:
        if isinstance(it, str):
            if MEDIA_EXT_RE.search(it) or "/" in it:
                yield {"url": urljoin(page_url, it), "label": "",
                       "height": _height_of(it), "size": 0,
                       "source": f"api:{key}", "filename": ""}
            continue
        if not isinstance(it, dict):
            continue
        url = _first_url_value(it)
        fmt = str(it.get("format") or it.get("type") or it.get("ext") or "")
        filename = str(it.get("filename") or it.get("file_name") or "")
        label = ""
        for k, v in it.items():
            if LABEL_KEY_RE.match(str(k)) and isinstance(v, (str, int, float)):
                label = str(v); break
        height = _height_of(it.get("height"), it.get("quality"),
                            it.get("resolution"), label, filename, url)
        cand = {"url": urljoin(page_url, url) if url else "",
                "label": label or filename, "height": height,
                "size": _size_of(it), "source": f"api:{key}",
                "filename": filename, "format": fmt.lower()}
        if not url and filename:
            # Filename-only option: the record's ``downloads`` sub-resource
            # answers {"url": ...} for it (measured live; see module doc).
            base = record_url.split("?", 1)[0].rstrip("/")
            cand["resolve_url"] = f"{base}/downloads?filename={quote(filename)}"
        if cand["url"] or cand.get("resolve_url"):
            yield cand


# dl95-kellymadisonmedia-2: a trailer / teaser / preview / sample FILE is never the
# scene. Tokens are delimited by / _ . - (a word-boundary \b does not split on "_",
# so "1138_maddie_wren_trailer_1080p_pf.mp4" slipped through as the member scene).
PREVIEW_MEDIA_RE = re.compile(
    r"(?:^|[/_.-])(?:trailers?|teasers?|previews?|samples?)(?:[/_.-]|$)", re.I)


def is_preview_media(url: str) -> bool:
    try:
        return bool(PREVIEW_MEDIA_RE.search(urlparse(url).path or ""))
    except Exception:
        return False


def preview_media_urls(page_url: str, urls: Iterable[str]) -> List[str]:
    """The trailer/preview media files among what the page fetched (see above)."""
    out: List[str] = []
    for u in urls or []:
        if isinstance(u, str) and u.strip():
            u = urljoin(page_url, u.strip())
            if MEDIA_EXT_RE.search(u) and is_preview_media(u) and u not in out:
                out.append(u)
    return out


def api_candidates(page_url: str, records: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Walk every remembered API record for download-like options."""
    out: List[Dict[str, Any]] = []
    seen = set()

    def walk(node: Any, record_url: str, depth: int) -> None:
        if depth > 6 or node is None:
            return
        if isinstance(node, dict):
            for k, v in node.items():
                if OPTION_KEY_RE.match(str(k)) and isinstance(v, (list, dict)):
                    for c in _option_candidates(record_url, str(k), v, page_url):
                        ident = c.get("url") or c.get("resolve_url")
                        if ident and ident not in seen:
                            seen.add(ident); out.append(c)
                elif isinstance(v, (dict, list)):
                    walk(v, record_url, depth + 1)
        elif isinstance(node, list):
            for v in node[:50]:
                walk(v, record_url, depth + 1)

    for rec in records or []:
        walk(rec.get("json"), str(rec.get("url") or page_url), 0)
    return out


def scene_player_candidates(page_url: str, html: str, job_url: str = "",
                            strict: bool = False) -> List[Dict[str, Any]]:
    """Recover child sources whose route identifies this numeric scene.

    Keep the parser's complete URL: the trailing slash and signed query are
    transport data. Only the path is inspected for identity and resolution.
    A bare rendition has unknown quality, never an inferred top tier.

    dl95-beeg-1-live-1 (cx-2 F1): the page may have moved on from the job
    (a feed rewrites the address bar), so the page's scene counts only while
    it is the JOB's: a *job_url* naming another scene id proves nothing, and
    under *strict* a job_url naming no scene id proves nothing either.
    """
    scene = re.fullmatch(r"/video/(\d+)/?", urlparse(page_url).path)
    if not scene:
        return []
    job_scene = re.fullmatch(r"/video/(\d+)/?", urlparse(job_url or "").path)
    if (job_scene.group(1) if job_scene else None) != scene.group(1) and (
            strict or job_scene):
        return []
    from .deep_detect.providers import extract_player_configs

    scene_id = re.escape(scene.group(1))
    route = re.compile(
        rf"/get_file/[^/]+/[^/]+/\d+/{scene_id}/{scene_id}"
        r"(?:_(\d{3,4})p)?\.mp4/?")
    out = []
    seen = set()
    for source in extract_player_configs(html, base_url=page_url):
        if (source.get("source_type") != "videojs_source"
                or source.get("found_in") != "<video class=video-js><source>"):
            continue
        url = source.get("url") or ""
        parsed = urlparse(url)
        match = route.fullmatch(parsed.path)
        if parsed.scheme not in ("http", "https") or not parsed.netloc or not match:
            continue
        if url in seen:
            continue
        seen.add(url)
        height = int(match.group(1) or 0)
        out.append({"url": url, "label": f"{height}p" if height else "unknown",
                    "height": height, "size": 0, "source": "scene-player",
                    "filename": parsed.path.rstrip("/").rsplit("/", 1)[-1]})
    return out


# dl95-beeg-1-live-1: beeg's scene route is one opaque id (/-0920833012505915),
# its player is a blob:, and its feed prefetches the NEXT scenes' streams too.
# The scene's own stream is the one whose path carries that id
# (.../av1_720p/920833012505915.mp4.m3u8).  Height: a rendition segment
# ("av1_720p"), else the tallest WxH a master's variant list declares
# ("multi=426x240:240p:...,1920x1080:1080p:..."), which ffmpeg's default
# stream selection takes.
_OPAQUE_SCENE_SEGMENT_RE = re.compile(r"[-_]?0*(\d{8,})")
_ID_NAMED_SEGMENT_RE = re.compile(r"0*(\d{8,})(?:\.[A-Za-z0-9]+)*")
_RENDITION_SEGMENT_RE = re.compile(r"(?:[A-Za-z0-9]+_)?(\d{3,4})p", re.I)
_VARIANT_DIMENSIONS_RE = re.compile(r"(?<!\d)\d{3,4}x(\d{3,4})(?!\d)")


def scene_stream_candidates(page_url: str, urls: Iterable[str]) -> List[Dict[str, Any]]:
    """Streams the page requested whose path names the scene *page_url* asks for.

    Pass the JOB's URL: beeg's feed rewrites the address bar to the next
    scene as it plays on.  Identity is positive evidence only: the route must
    carry exactly one opaque id segment, and a stream qualifies only when one
    of its path segments IS that id (leading zeros aside) or an id-named file.
    A signed token, another scene's id or an id-less playlist never qualifies.
    """
    ids = [m.group(1) for seg in urlparse(page_url).path.split("/")
           if (m := _OPAQUE_SCENE_SEGMENT_RE.fullmatch(seg))]
    if len(ids) != 1:
        return []
    out: List[Dict[str, Any]] = []
    seen = set()
    for u in urls or []:
        if not isinstance(u, str) or not u.strip():
            continue
        u = urljoin(page_url, u.strip())
        parsed = urlparse(u)
        if parsed.scheme not in ("http", "https") or not MEDIA_EXT_RE.search(parsed.path):
            continue
        segments = [s for s in parsed.path.split("/") if s]
        named = [s for s in segments
                 if (m := _ID_NAMED_SEGMENT_RE.fullmatch(s)) and m.group(1) == ids[0]]
        if not named or u in seen:
            continue
        seen.add(u)
        heights = [int(m.group(1)) for s in segments
                   if (m := _RENDITION_SEGMENT_RE.fullmatch(s))]
        if not heights:
            heights = [max(int(h) for h in _VARIANT_DIMENSIONS_RE.findall(s))
                       for s in segments if _VARIANT_DIMENSIONS_RE.search(s)]
        height = heights[-1] if heights else 0
        out.append({"url": u, "label": f"{height}p" if height else "unknown",
                    "height": height, "size": 0, "source": "scene-stream",
                    "filename": re.sub(r"\.(m3u8|mpd)$", "", named[-1], flags=re.I)})
    return out


# dl95-beeg-2: the page's own fetch of a playlist it already requested (same
# session, same egress as the player); "" on any refusal.
MANIFEST_TEXT_JS = """async (url) => {
  try {
    const r = await fetch(url, {credentials: 'include'});
    if (!r.ok) return '';
    return (await r.text()).slice(0, 262144);
  } catch (e) { return ''; }
}"""

_H264_CODEC_RE = re.compile(r"\bavc[13]\.", re.I)


def _master_order(master_text: str, master_url: str) -> List[str]:
    """Variant URIs in master order, resolved as streaming_manifest resolves them."""
    lines = [ln.strip() for ln in (master_text or "").splitlines() if ln.strip()]
    order: List[str] = []
    for i, ln in enumerate(lines):
        if ln.startswith("#EXT-X-STREAM-INF:"):
            uri = next((n for n in lines[i + 1:] if not n.startswith("#")), "")
            order.append(uri if uri.startswith(("http://", "https://"))
                         else urljoin(master_url, uri))
    return order


def hls_variant_for(master_text: str, master_url: str,
                    want_height: int) -> Optional[Dict[str, Any]]:
    """dl95-beeg-2: the variant of an HLS master that a ranked height names.

    beeg's "multi=" master lists 240p first; the segmented downloader maps the
    FIRST video stream, so a master labelled 1080p landed 240p.  Picks, in
    order: an h264 variant at *want_height*, any variant at it, the tallest
    h264 variant, the tallest variant -- highest bandwidth within a height.
    None when the text is not a master or no variant declares a height.
    ``program`` is the variant's index in master order (ffmpeg's program id)
    and ``audio_group`` its EXT-X-MEDIA audio group: such a variant's playlist
    carries no audio, so it must be taken from the master (lens B19-B).
    """
    try:
        from .streaming_manifest import parse_streaming_manifest
        manifest = parse_streaming_manifest(master_text or "", base_url=master_url)
    except ValueError:
        return None
    if not manifest.is_master:
        return None
    sized = [v for v in manifest.variants
             if v.height and v.uri.startswith(("http://", "https://"))]
    if not sized:
        return None
    h264 = [v for v in sized if _H264_CODEC_RE.search(v.codecs or "")]
    for pool in ([v for v in h264 if v.height == want_height],
                 [v for v in sized if v.height == want_height], h264, sized):
        if pool:
            best = max(pool, key=lambda v: (v.height, v.bandwidth))
            return {"url": best.uri, "height": int(best.height),
                    "codecs": best.codecs or "", "audio_group": best.audio_group or "",
                    "program": _master_order(master_text, master_url).index(best.uri)}
    return None




def xhamster_source_candidates(page_url: str, page) -> list[dict[str, Any]]:
    """Read only the current scene's published MP4 download menu."""
    try:
        target = urlparse(page_url)
    except ValueError:
        return []
    if (target.scheme not in ("http", "https")
            or not (target.hostname == "xhamster.com"
                    or (target.hostname or "").endswith(".xhamster.com"))
            or not target.path.startswith("/videos/")):
        return []
    try:
        current = urlparse(page.url)
        if (current.hostname, current.path.rstrip("/")) != (target.hostname, target.path.rstrip("/")):
            return []
        data = page.evaluate("""() => {
          const s = window.initials;
          if (!s || !s.videoModel || !s.downloadDropdownComponent) return null;
          return {model: {id: s.videoModel.id, pageURL: s.videoModel.pageURL},
                  menu: {videoId: s.downloadDropdownComponent.videoId,
                         mp4: s.downloadDropdownComponent.sources?.mp4}};
        }""")
    except Exception:
        return []
    if not isinstance(data, dict):
        return []
    model, menu = data.get("model"), data.get("menu")
    if not isinstance(model, dict) or not isinstance(menu, dict):
        return []
    if not model.get("id") or str(model["id"]) != str(menu.get("videoId")):
        return []
    try:
        scene = urlparse(str(model.get("pageURL") or ""))
    except ValueError:
        return []
    if (scene.hostname, scene.path.rstrip("/")) != (target.hostname, target.path.rstrip("/")):
        return []
    sources = menu.get("mp4")
    if not isinstance(sources, dict):
        return []
    out = []
    for label, value in list(sources.items())[:32]:
        quality = re.fullmatch(r"(\d{3,4})p", str(label), re.IGNORECASE)
        if not quality or not isinstance(value, str):
            continue
        try:
            media = urlparse(value)
        except ValueError:
            continue
        host = media.hostname or ""
        if (media.scheme not in ("http", "https") or media.username or media.password
                or not (host == "xhcdn.com" or host.endswith(".xhcdn.com"))
                or not media.path.lower().endswith(".mp4")):
            continue
        out.append({"url": value, "label": str(label), "height": int(quality[1]),
                    "size": 0, "source": "xhamster-menu", "filename": ""})
    return out


def page_media_candidates(page_url: str, urls: Iterable[str]) -> List[Dict[str, Any]]:
    """Media files the page actually requested / bound to <video>/<source>."""
    out: List[Dict[str, Any]] = []
    seen = set()
    for u in urls or []:
        if isinstance(u, dict) and u.get("source") == "kvs-flashvars":
            # dl95-kvs-flashvars-1: already a candidate (label, height, bound applied)
            if u.get("url") and u["url"] not in seen:
                seen.add(u["url"])
                out.append(u)
            continue
        if not isinstance(u, str) or not u.strip():
            continue
        u = urljoin(page_url, u.strip())
        # A KVS player's file keeps a directory slash after its name:
        # /get_file/.../<id>.mp4/?... (dl95-porn00-1-live-1; as dl95-justporn-1).
        if not u.startswith(("http://", "https://")) or not (
                MEDIA_EXT_RE.search(u) or MEDIA_EXT_RE.search(urlparse(u).path.rstrip("/"))):
            continue
        if u in seen:
            continue
        if is_preview_media(u):  # dl95-kellymadisonmedia-2
            continue
        seen.add(u)
        out.append({"url": u, "label": "", "height": _height_of(u), "size": 0,
                    "source": "page-media", "filename": ""})
    return out


def kvs_flashvars_candidates(page_url: str, items: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """dl95-kvs-flashvars-1: the files a KVS player declares in its page-global
    ``flashvars`` (video_url, video_alt_url, video_alt_url2 ... each with a
    ``*_text`` label such as "720p"). The player fetches nothing until play, so
    neither page media nor API JSON sees them (porn00: /get_file/.../42561_720p.mp4/).
    A license-obfuscated ``function/0/...`` value is not decodable here and is skipped."""
    out: List[Dict[str, Any]] = []
    seen = set()
    for it in items or []:
        if not isinstance(it, dict):
            continue
        raw = str(it.get("url") or "").strip()
        if not raw or raw.startswith("function/"):
            continue
        u = urljoin(page_url, raw)
        if not u.startswith(("http://", "https://")) or u in seen:
            continue
        seen.add(u)
        label = str(it.get("text") or "")
        out.append({"url": u, "label": label, "height": _height_of(label, u), "size": 0,
                    "source": "kvs-flashvars", "filename": ""})
    return out


def rank_candidates(cands: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Highest resolution first, then size; API options before page media;
    an explicit download option before a stream; files before manifests."""
    def key(c):
        is_manifest = 1 if re.search(r"\.(m3u8|mpd)(\?|$)", c.get("url") or "", re.I) else 0
        fmt = (c.get("format") or "").lower()
        wmv = 1 if fmt == "wmv" or (c.get("url") or "").lower().endswith(".wmv") else 0
        source = str(c.get("source", ""))
        return (-int(c.get("height") or 0), -int(c.get("size") or 0),
                0 if source.startswith("api:") else 1,
                0 if "download" in source.lower() else 1,   # a download file over a stream
                is_manifest, wmv)
    return sorted(cands, key=key)


PAGE_MEDIA_JS = """() => {
  const out = [];
  // Video only: an <audio><source> (a page's sound effect or player) is never
  // the scene (dl95-file-examples-5 lens R1).
  for (const e of document.querySelectorAll('video, video > source')) {
    const s = e.currentSrc || e.src || e.getAttribute('src') || '';
    if (s) out.push(s);
  }
  try {
    for (const r of performance.getEntriesByType('resource')) {
      if (r.initiatorType === 'audio') continue;
      if (/\\.(mp4|m4v|mov|webm|mkv|m3u8|mpd)(\\?|$)/i.test(r.name)) out.push(r.name);
    }
  } catch (e) {}
  return out.slice(0, 60);
}"""

KVS_FLASHVARS_JS = """() => {
  const fv = window.flashvars;
  if (!fv || typeof fv !== 'object') return [];
  const out = [];
  for (const k of Object.keys(fv)) {
    if (!/^video_(alt_)?url\\d*$/.test(k)) continue;
    const v = fv[k];
    if (typeof v === 'string' && v) out.push({url: v, text: String(fv[k + '_text'] || '')});
  }
  return out.slice(0, 12);
}"""

RESOLVE_JS = """async ([url, headers]) => {
  const r = await fetch(url, {headers: headers || {}, credentials: 'include'});
  const t = await r.text();
  let j = null; try { j = JSON.parse(t); } catch (e) {}
  return {status: r.status, json: j};
}"""


def resolve_candidate_url(page, cand: Dict[str, Any], headers: Dict[str, str]) -> str:
    """Fill ``cand['url']`` for a filename-only option via the page's own
    session (same-origin fetch with credentials and the SPA's own headers)."""
    if cand.get("url"):
        return cand["url"]
    ru = cand.get("resolve_url")
    if not ru:
        return ""
    try:
        res = page.evaluate(RESOLVE_JS, [ru, dict(headers or {})])
    except Exception:
        return ""
    if not isinstance(res, dict) or int(res.get("status") or 0) >= 400:
        return ""
    data = res.get("json")
    url = ""
    if isinstance(data, dict):
        url = _first_url_value(data)
        if not url:
            for v in data.values():
                if isinstance(v, dict):
                    url = _first_url_value(v)
                    if url:
                        break
    if url:
        cand["url"] = urljoin(ru, url)
    return cand.get("url") or ""


# ── dl95-fullporner-1: third-party embed players ─────────────────────────────
# A scene whose player is a cross-origin <iframe> (fullporner -> xiaoshenke.net)
# has no media in the top frame. The embed's <video> lists its renditions as
# typed <source> children with extension-less URLs (/vid/<n>/1080,
# type=video/mp4), while the <video>'s own currentSrc is a pre-roll AD -- so
# only <video> > <source> is read, never the <video> element itself.
FRAME_SOURCES_JS = """() => [...document.querySelectorAll('video > source')]
  .map(s => [s.src || s.getAttribute('src') || '', s.type || ''])"""


def frame_source_candidates(frame_url: str, pairs: Iterable[Any],
                            page_url: str = "") -> List[Dict[str, Any]]:
    """Candidates from one frame's [src, type] <source> pairs. A typed video/*
    source needs no extension; an untyped one needs a media extension; any
    other type (audio/*) is refused. Files are named after the scene page."""
    slug = (urlparse(page_url or "").path or "").rstrip("/").rsplit("/", 1)[-1]
    out: List[Dict[str, Any]] = []
    seen = set()
    for pair in pairs or []:
        try:
            src, typ = str(pair[0] or "").strip(), str(pair[1] or "").strip().lower()
        except (TypeError, IndexError):
            continue
        if not src:
            continue
        u = urljoin(frame_url, src)
        if not u.startswith(("http://", "https://")) or u in seen:
            continue
        if typ and not typ.startswith("video/"):
            continue
        if not typ and not MEDIA_EXT_RE.search(u):
            continue
        seen.add(u)
        last = (urlparse(u).path or "").rstrip("/").rsplit("/", 1)[-1]
        out.append({"url": u, "label": "", "height": _height_of(last, u), "size": 0,
                    "source": "embed-frame", "filename": f"{slug}.mp4" if slug else ""})
    return out


# A player is on screen and player-sized; a hidden (display:none) or pixel
# iframe -- an ad slot, a tracker -- is never the scene's player (lens R1).
_MIN_PLAYER_W, _MIN_PLAYER_H = 200, 150


# The iframe and every ancestor must actually render: a visibility:hidden or
# transparent iframe keeps its box (lens R1 gen2), so the box alone is not proof.
_FRAME_SHOWN_JS = """e => {
  for (let n = e; n && n.nodeType === 1; n = n.parentElement) {
    const s = getComputedStyle(n);
    if (s.display === 'none' || s.visibility === 'hidden' || s.visibility === 'collapse'
        || parseFloat(s.opacity) < 0.05) return false;
  }
  return true;
}"""


def _frame_element_shown(frame):
    """The <iframe> element embedding ``frame`` in its parent document: its box
    when it renders (itself and its ancestors in THAT document), else None."""
    el = frame.frame_element()
    if el is None or not el.is_visible() or not el.evaluate(_FRAME_SHOWN_JS):
        return None
    box = el.bounding_box()
    if not box or box.get("x", 0) + box.get("width", 0) <= 0 \
            or box.get("y", 0) + box.get("height", 0) <= 0:
        return None
    return box


def _is_visible_player_frame(frame) -> bool:
    """Player-sized, and visible through EVERY embedding document up to the top
    page: a hidden outer iframe hides the player nested in it (lens R1 gen3)."""
    try:
        box = _frame_element_shown(frame)
        if not box or box.get("width", 0) < _MIN_PLAYER_W \
                or box.get("height", 0) < _MIN_PLAYER_H:
            return False
        outer = frame.parent_frame
        while outer is not None and outer.parent_frame is not None:
            if not _frame_element_shown(outer):
                return False
            outer = outer.parent_frame
    except Exception:
        return False
    return True


def _child_frames(page) -> list:
    try:
        main = page.main_frame
        return [f for f in page.frames if f is not main
                and (f.url or "").startswith(("http://", "https://"))
                and _is_visible_player_frame(f)]
    except Exception:
        return []


def embed_frame_candidates(page, page_url: str = "") -> List[Dict[str, Any]]:
    """<video> > <source> options inside the page's visible player frames.
    Ranked with the page's own media by height (PM ruling 0245Z 2(a)); hidden
    frames never contribute (lens R1), so an ad slot cannot enter the ranking."""
    cands: List[Dict[str, Any]] = []
    for fr in _child_frames(page):
        try:
            pairs = fr.evaluate(FRAME_SOURCES_JS) or []
        except Exception:
            continue
        cands += frame_source_candidates(fr.url, pairs, page_url or getattr(page, "url", ""))
    return cands


def third_party_frame_hosts(page) -> List[str]:
    """Hosts of child frames on a different host than the page (embed players)."""
    try:
        top = urlparse(page.url or "").hostname or ""
    except Exception:
        top = ""
    hosts: List[str] = []
    for fr in _child_frames(page):
        h = urlparse(fr.url).hostname or ""
        if h and h != top and h not in hosts:
            hosts.append(h)
    return hosts

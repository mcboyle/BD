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
MEDIA_EXT_RE = re.compile(r"\.(mp4|m4v|mov|webm|mkv|m3u8|mpd)(\?|$)", re.I)
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


def page_media_candidates(page_url: str, urls: Iterable[str]) -> List[Dict[str, Any]]:
    """Media files the page actually requested / bound to <video>/<source>."""
    out: List[Dict[str, Any]] = []
    seen = set()
    for u in urls or []:
        if not isinstance(u, str) or not u.strip():
            continue
        u = urljoin(page_url, u.strip())
        if not u.startswith(("http://", "https://")) or not MEDIA_EXT_RE.search(u):
            continue
        if u in seen:
            continue
        seen.add(u)
        out.append({"url": u, "label": "", "height": _height_of(u), "size": 0,
                    "source": "page-media", "filename": ""})
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
  for (const e of document.querySelectorAll('video,source')) {
    const s = e.currentSrc || e.src || e.getAttribute('src') || '';
    if (s) out.push(s);
  }
  try {
    for (const r of performance.getEntriesByType('resource')) {
      if (/\\.(mp4|m4v|mov|webm|mkv|m3u8|mpd)(\\?|$)/i.test(r.name)) out.push(r.name);
    }
  } catch (e) {}
  return out.slice(0, 60);
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

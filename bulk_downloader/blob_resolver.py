"""Map ``blob:`` video sources to the HLS/DASH manifests that fed them.

A MediaSource-backed ``<video src="blob:https://site/uuid">`` is not
fetchable; the bytes came from playlist/manifest requests the page made
just before. Feed this resolver the page's ``Network.*`` CDP events (or a
recon ``network_log``), tell it which blob URLs the DOM shows, and
``resolve`` returns the manifests whose document origin matches the blob's
origin and which were requested before the blob was observed -- grouped as
one master plus its variant representations.

Manifest detection accepts the real proxy shape seen in the recon corpus
(``https://m3u8.gammacdn.com?u=https%3A...%2F53601_01.m3u8``): the
extension check runs over the path AND every decoded query value, and the
Content-Type check covers the case where neither carries it.
"""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple
import base64
from urllib.parse import parse_qsl, unquote, urljoin, urlsplit

from .hls_downloader import (is_dash_content_type, is_dash_url,
                             is_hls_content_type, is_hls_url)

_REQUEST = "Network.requestWillBeSent"
_RESPONSE = "Network.responseReceived"
_FINISHED = "Network.loadingFinished"
_FAILED = "Network.loadingFailed"
_STREAM_INF = re.compile(r"^#EXT-X-STREAM-INF:(.*)$", re.MULTILINE)
_ATTR = re.compile(r'([A-Z0-9-]+)=("[^"]*"|[^,]*)')
_HEIGHT_HINT = re.compile(r"(?<!\d)(\d{3,4})p(?!\w)", re.IGNORECASE)
_BLOB_SOURCES_JS = """
() => Array.from(document.querySelectorAll('video, audio, source'))
  .flatMap(el => [el.currentSrc, el.src, el.getAttribute('src')])
  .filter(s => typeof s === 'string' && s.startsWith('blob:'))
"""


@dataclass(frozen=True)
class Manifest:
    url: str
    kind: str
    requested_at: float
    document_url: str = ""
    frame_id: str = ""
    request_id: str = ""
    mime_type: str = ""
    body_seen: bool = False
    upstream_url: str = ""
    body: str = field(default="", repr=False, compare=False)

    @property
    def origin_host(self) -> str:
        return _host(self.document_url)


@dataclass(frozen=True)
class Representation:
    label: str
    url: str
    observed: bool
    bandwidth: Optional[int] = None
    width: Optional[int] = None
    height: Optional[int] = None
    rep_id: Optional[str] = None


@dataclass(frozen=True)
class BlobResolution:
    blob_url: str
    found: bool
    reason: str
    kind: str = ""
    master: Optional[Manifest] = None
    manifests: Tuple[Manifest, ...] = ()
    representations: Tuple[Representation, ...] = ()


def _host(url: Any) -> str:
    try:
        return (urlsplit(str(url)).hostname or "").lower()
    except ValueError:
        return ""


def blob_origin_host(blob_url: Any) -> str:
    if not isinstance(blob_url, str) or not blob_url.startswith("blob:"):
        return ""
    return _host(blob_url[len("blob:"):])


def _upstream(url: str) -> str:
    """The manifest URL a proxy wraps in a query value, else the URL itself."""
    try:
        for _key, value in parse_qsl(urlsplit(url).query, keep_blank_values=True):
            if is_hls_url(value) or is_dash_url(value):
                return value
    except ValueError:
        pass
    return url


def manifest_kind(url: Any, mime_type: Any = "") -> str:
    if not isinstance(url, str):
        return ""
    upstream = _upstream(url)
    if is_hls_url(url) or is_hls_url(upstream) or is_hls_content_type(mime_type or ""):
        return "hls"
    if is_dash_url(url) or is_dash_url(upstream) or is_dash_content_type(mime_type or ""):
        return "dash"
    return ""


def label_for(*, width: Optional[int], height: Optional[int], url: str,
              rep_id: Optional[str]) -> str:
    if height:
        return f"{height}p"
    hint = _HEIGHT_HINT.search(unquote(urlsplit(url).path.rsplit("/", 1)[-1]))
    if hint:
        return f"{hint.group(1)}p"
    if rep_id:
        return rep_id
    return unquote(urlsplit(_upstream(url)).path.rsplit("/", 1)[-1]) or url


def _int(value: Any) -> Optional[int]:
    try:
        return int(str(value).strip().strip('"'))
    except (TypeError, ValueError):
        return None


def _resolve_relative(base: str, ref: str) -> str:
    """RFC 3986 resolution against the master's URL: relative names, absolute
    paths (``/hls/x.m3u8``), protocol-relative (``//cdn2/x.m3u8``) and dotted
    (``../v/x.m3u8``) variant URIs all resolve the way the player resolves them."""
    return urljoin(base, ref)


def _hls_variants(master: Manifest) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    lines = master.body.splitlines()
    for i, line in enumerate(lines):
        m = _STREAM_INF.match(line.strip())
        if not m:
            continue
        attrs = {k: v.strip('"') for k, v in _ATTR.findall(m.group(1))}
        uri = next((ln.strip() for ln in lines[i + 1:] if ln.strip() and not ln.startswith("#")), "")
        if not uri:
            continue
        width = height = None
        if "x" in attrs.get("RESOLUTION", ""):
            width, height = (_int(p) for p in attrs["RESOLUTION"].lower().split("x", 1))
        out.append({"url": _resolve_relative(_upstream(master.url), uri),
                    "bandwidth": _int(attrs.get("BANDWIDTH")), "width": width,
                    "height": height, "rep_id": None})
    return out


def _dash_representations(master: Manifest) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    try:
        root = ET.fromstring(master.body)
    except ET.ParseError:
        return out
    for el in root.iter():
        if el.tag.rsplit("}", 1)[-1] != "Representation":
            continue
        out.append({"url": master.url, "bandwidth": _int(el.get("bandwidth")),
                    "width": _int(el.get("width")), "height": _int(el.get("height")),
                    "rep_id": el.get("id")})
    return out


class BlobResolver:
    """Accumulates manifest requests and blob observations; ``resolve`` joins them."""

    def __init__(self, body_fetcher: Optional[Callable[[str], Optional[str]]] = None):
        self.body_fetcher = body_fetcher
        self.manifests: List[Manifest] = []
        self.blobs: Dict[str, Dict[str, Any]] = {}
        self.errors: List[str] = []
        self._pending: Dict[str, Dict[str, Any]] = {}

    @property
    def manifest_count(self) -> int:
        return len(self.manifests)

    def _note(self, where: str, exc: BaseException) -> None:
        self.errors.append(f"{where}: {type(exc).__name__}: {exc}")

    # ---- ingestion -------------------------------------------------------

    def feed(self, method: str, params: Any) -> None:
        """One CDP ``Network.*`` event. Never raises; malformed input is ignored."""
        try:
            self._feed(method, params)
        except Exception as exc:  # fail-soft by contract: a bad event must not stop capture
            self._note(f"feed {method}", exc)

    def _feed(self, method: str, params: Any) -> None:
        if not isinstance(params, dict):
            return
        rid = params.get("requestId")
        if rid is None:
            return
        rid = str(rid)
        if method == _REQUEST:
            req = params.get("request")
            if not isinstance(req, dict) or not isinstance(req.get("url"), str):
                return
            self._pending[rid] = {
                "url": req["url"], "requested_at": float(params.get("timestamp") or 0.0),
                "document_url": str(params.get("documentURL") or ""),
                "frame_id": str(params.get("frameId") or ""), "mime_type": "",
            }
        elif method == _RESPONSE:
            pending = self._pending.get(rid)
            resp = params.get("response")
            if pending is not None and isinstance(resp, dict):
                pending["mime_type"] = str(resp.get("mimeType") or "")
        elif method == _FINISHED:
            pending = self._pending.pop(rid, None)
            if pending is not None:
                self._admit(request_id=rid, **pending)
        elif method == _FAILED:
            # an aborted load (players abort segment/variant loads routinely)
            # is not a source and must not stay pending for the session
            self._pending.pop(rid, None)

    def _admit(self, *, url: str, requested_at: float, document_url: str, frame_id: str,
               mime_type: str, request_id: str) -> None:
        kind = manifest_kind(url, mime_type)
        if not kind:
            return
        body = ""
        if self.body_fetcher is not None and request_id:
            try:
                fetched = self.body_fetcher(request_id)
            except Exception as exc:
                self._note(f"body {request_id}", exc)
                fetched = None
            if isinstance(fetched, str):
                body = fetched
        self.manifests.append(Manifest(
            url=url, kind=kind, requested_at=requested_at, document_url=document_url,
            frame_id=frame_id, request_id=request_id, mime_type=mime_type,
            body_seen=bool(body), upstream_url=_upstream(url), body=body))

    def feed_network_log(self, entries: Any, *, document_url: str = "") -> int:
        """Recon/session-capture ``network_log`` rows. Returns rows accepted."""
        accepted = 0
        if not isinstance(entries, list):
            return 0
        for entry in entries:
            if not isinstance(entry, dict) or not isinstance(entry.get("url"), str):
                continue
            headers = entry.get("response_headers") or {}
            mime = ""
            if isinstance(headers, dict):
                mime = str(next((v for k, v in headers.items()
                                 if str(k).lower() == "content-type"), "") or "")
            ts = entry.get("timestamp")
            try:
                requested_at = float(ts) / 1000.0 if ts is not None else 0.0
            except (TypeError, ValueError):
                requested_at = 0.0
            body = entry.get("response_body")
            kind = manifest_kind(entry["url"], mime)
            accepted += 1
            if not kind:
                continue
            body = body if isinstance(body, str) and body.lstrip().startswith(("#EXTM3U", "<")) else ""
            self.manifests.append(Manifest(
                url=entry["url"], kind=kind, requested_at=requested_at,
                document_url=document_url, mime_type=mime, body_seen=bool(body),
                upstream_url=_upstream(entry["url"]), body=body))
        return accepted

    def observe_blob(self, blob_url: str, *, document_url: str = "",
                     observed_at: Optional[float] = None) -> None:
        if not isinstance(blob_url, str) or not blob_url.startswith("blob:"):
            return
        self.blobs[blob_url] = {"document_url": document_url, "observed_at": observed_at}

    # ---- resolution ------------------------------------------------------

    def resolve(self, blob_url: str) -> BlobResolution:
        if not isinstance(blob_url, str) or not blob_url.startswith("blob:"):
            return BlobResolution(str(blob_url), False, "not a blob: URL")
        seen = self.blobs.get(blob_url)
        if seen is None:
            return BlobResolution(blob_url, False, "blob not observed")
        host = blob_origin_host(blob_url) or _host(seen["document_url"])
        observed_at = seen["observed_at"]
        candidates = [
            m for m in self.manifests
            if (not m.origin_host or not host or m.origin_host == host)
            and (observed_at is None or m.requested_at <= observed_at)
        ]
        if not candidates:
            return BlobResolution(blob_url, False,
                                  f"no manifest request observed for origin {host or '?'}")
        candidates.sort(key=lambda m: m.requested_at)
        kind = candidates[0].kind
        same_kind = [m for m in candidates if m.kind == kind]
        master = next((m for m in same_kind if _STREAM_INF.search(m.body)), None)
        if master is None:
            master = next((m for m in same_kind if m.kind == "dash" and m.body_seen), None)
        if master is None:
            master = same_kind[0]
        reps = self._representations(master, same_kind)
        return BlobResolution(blob_url, True, f"origin:{host or '?'}", kind=kind,
                              master=master, manifests=tuple(same_kind),
                              representations=tuple(reps))

    def _representations(self, master: Manifest, manifests: List[Manifest]) -> List[Representation]:
        declared = _hls_variants(master) if master.kind == "hls" else _dash_representations(master)
        observed_by_upstream = {m.upstream_url.split("?", 1)[0]: m for m in manifests if m is not master}
        out: List[Representation] = []
        claimed = set()
        for d in declared:
            key = d["url"].split("?", 1)[0]
            hit = observed_by_upstream.get(key)
            if hit is not None:
                claimed.add(hit.url)
            out.append(Representation(
                label=label_for(width=d["width"], height=d["height"], url=d["url"], rep_id=d["rep_id"]),
                url=hit.url if hit is not None else d["url"], observed=hit is not None,
                bandwidth=d["bandwidth"], width=d["width"], height=d["height"], rep_id=d["rep_id"]))
        for m in manifests:
            if m is master or m.url in claimed:
                continue
            out.append(Representation(
                label=label_for(width=None, height=None, url=m.upstream_url, rep_id=None),
                url=m.url, observed=True))
        return out


# ---- live driver (needs a Playwright page; not exercised against a browser here) ----

def attach(page, resolver: Optional[BlobResolver] = None) -> BlobResolver:
    """Wire a CDP session's ``Network.*`` events into ``resolver``."""
    if resolver is None:
        resolver = BlobResolver()
    client = page.context.new_cdp_session(page)
    client.send("Network.enable")

    def _fetch_body(rid: str) -> Optional[str]:
        # Chromium hands HLS playlists (application/vnd.apple.mpegurl and
        # application/x-mpegURL) back base64Encoded=True; DASH XML comes as
        # text. Both are the manifest text once decoded -- never drop one.
        res = client.send("Network.getResponseBody", {"requestId": rid})
        if not isinstance(res, dict) or not isinstance(res.get("body"), str):
            return None
        if res.get("base64Encoded"):
            try:
                return base64.b64decode(res["body"], validate=True).decode("utf-8", "replace")
            except (ValueError, TypeError):
                return None
        return res["body"]

    resolver.body_fetcher = _fetch_body
    for event in (_REQUEST, _RESPONSE, _FINISHED, _FAILED):
        client.on(event, lambda params, _ev=event: resolver.feed(_ev, params))
    return resolver


def resolve_page(page, resolver: BlobResolver) -> List[BlobResolution]:
    """Read the DOM's ``blob:`` media sources and resolve each once, in order."""
    try:
        found = page.evaluate(_BLOB_SOURCES_JS)
    except Exception as exc:
        resolver._note("evaluate", exc)
        found = []
    document_url = str(getattr(page, "url", "") or "")
    results: List[BlobResolution] = []
    seen = set()
    for blob in found if isinstance(found, list) else []:
        if not isinstance(blob, str) or not blob.startswith("blob:") or blob in seen:
            continue
        seen.add(blob)
        resolver.observe_blob(blob, document_url=document_url)
        results.append(resolver.resolve(blob))
    return results

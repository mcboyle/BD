"""frame_hierarchy — Row 931: nested-document discovery and component bridge.

Third-party players sit inside iframes (often cross-origin, so
out-of-process) that a top-level DOM query never sees. This module:

  * enables ``Target.setAutoAttach`` over a CDP session so out-of-process
    iframe targets attach and are reported,
  * walks ``Page.getFrameTree`` into a flat, depth-annotated hierarchy,
  * probes every frame for encapsulated media elements and player controls,
  * extracts stream parameters (kind, host, query, resolution hint) from
    the media URLs it finds.

Never navigates, never logs in, never downloads. The CDP driver is
``inspect_frame_hierarchy``; everything it delegates to is pure and
unit-tested with synthetic frame trees and probe results.
"""
from __future__ import annotations

import re
from typing import Any, Callable, Dict, Iterable, List, Optional
from urllib.parse import parse_qsl, urlsplit

AUTO_ATTACH_PARAMS: Dict[str, Any] = {
    "autoAttach": True,
    "waitForDebuggerOnStart": False,
    "flatten": True,
}

_STREAM_KINDS = (
    (re.compile(r"\.m3u8(?:$|[?#])", re.I), "hls"),
    (re.compile(r"\.mpd(?:$|[?#])", re.I), "dash"),
    (re.compile(r"\.(?:mp4|webm|mov|m4v|mkv|mp3|m4a|ogg)(?:$|[?#])", re.I),
     "progressive"),
)
_RESOLUTION_RE = re.compile(r"(?<!\d)(\d{3,4})[xX](\d{3,4})(?!\d)|(?<!\d)(\d{3,4})p(?!\w)")

# Runs inside one frame; returns JSON-able lists of media elements and
# player controls. Reads attributes only -- no playback, no fetch.
MEDIA_PROBE_JS = """
() => {
  const attrs = (el, names) => {
    const out = {};
    for (const n of names) { const v = el.getAttribute(n); if (v !== null) out[n] = v; }
    return out;
  };
  const media = [];
  for (const el of document.querySelectorAll('video, audio')) {
    const sources = [];
    for (const s of el.querySelectorAll('source')) {
      sources.push({src: s.getAttribute('src') || '', type: s.getAttribute('type') || ''});
    }
    media.push({
      tag: el.tagName.toLowerCase(),
      src: el.getAttribute('src') || '',
      current_src: el.currentSrc || '',
      poster: el.getAttribute('poster') || '',
      sources,
      data: attrs(el, Array.from(el.attributes, a => a.name).filter(n => n.startsWith('data-'))),
    });
  }
  const controls = [];
  const sel = '[aria-label], [role=button], button, [class*=play], [class*=pause], [class*=mute], [class*=fullscreen], [class*=quality]';
  for (const el of document.querySelectorAll(sel)) {
    const label = (el.getAttribute('aria-label') || el.textContent || '').trim().slice(0, 80);
    const cls = el.className && typeof el.className === 'string' ? el.className : '';
    if (!/play|pause|mute|volume|fullscreen|quality|settings|seek|progress/i.test(label + ' ' + cls)) continue;
    controls.push({tag: el.tagName.toLowerCase(), label, class: cls.slice(0, 120)});
  }
  return {media, controls};
}
"""


def walk_frame_tree(tree: Dict[str, Any], *, depth: int = 0,
                    parent_id: Optional[str] = None) -> List[Dict[str, Any]]:
    """Flatten a ``Page.getFrameTree`` result into pre-order rows carrying
    ``frame_id``, ``parent_id``, ``depth``, ``url`` and ``name``."""
    node = tree.get("frameTree", tree)
    frame = node.get("frame") or {}
    fid = str(frame.get("id") or "")
    rows = [{
        "frame_id": fid,
        "parent_id": parent_id,
        "depth": depth,
        "url": str(frame.get("url") or ""),
        "name": str(frame.get("name") or ""),
        "out_of_process": False,
    }]
    for child in node.get("childFrames") or []:
        rows.extend(walk_frame_tree(child, depth=depth + 1, parent_id=fid))
    return rows


def merge_attached_targets(frames: List[Dict[str, Any]],
                           attached: Iterable[Dict[str, Any]]
                           ) -> List[Dict[str, Any]]:
    """Mark frames whose target attached out-of-process and append any
    attached iframe target the tree did not carry (OOPIFs the parent's
    tree cannot describe) at depth ``max_depth + 1``."""
    by_id = {f["frame_id"]: f for f in frames}
    max_depth = max((f["depth"] for f in frames), default=0)
    out = list(frames)
    for ev in attached:
        info = ev.get("targetInfo") or {}
        if info.get("type") != "iframe":
            continue
        tid = str(info.get("targetId") or "")
        if tid in by_id:
            by_id[tid]["out_of_process"] = True
            continue
        row = {
            "frame_id": tid,
            "parent_id": str(info.get("openerId") or "") or None,
            "depth": max_depth + 1,
            "url": str(info.get("url") or ""),
            "name": "",
            "out_of_process": True,
        }
        by_id[tid] = row
        out.append(row)
    return out


def extract_stream_parameters(url: str) -> Dict[str, Any]:
    """Describe a media URL: transport kind, host, path, query pairs and
    any resolution hint in the path. Never fetches."""
    if not url:
        return {"url": "", "kind": "none", "host": "", "path": "",
                "query": {}, "resolution": None}
    if url.startswith("blob:"):
        return {"url": url, "kind": "blob", "host": "", "path": "",
                "query": {}, "resolution": None}
    parts = urlsplit(url)
    kind = "unknown"
    for rx, name in _STREAM_KINDS:
        if rx.search(parts.path):
            kind = name
            break
    resolution = None
    hits = _RESOLUTION_RE.findall(parts.path)   # a rung may sit in a directory segment
    if hits:
        _w, h, p = hits[-1]
        resolution = int(h or p)
    return {
        "url": url,
        "kind": kind,
        "host": parts.hostname or "",
        "path": parts.path,
        "query": dict(parse_qsl(parts.query, keep_blank_values=True)),
        "resolution": resolution,
    }


def classify_probe(frame: Dict[str, Any], probe: Any) -> Dict[str, Any]:
    """Attach a frame's probe result (``MEDIA_PROBE_JS`` output) to the
    frame row, with stream parameters extracted for every media URL."""
    probe = probe if isinstance(probe, dict) else {}
    media_out = []
    for m in probe.get("media") or []:
        if not isinstance(m, dict):
            continue
        urls = [u for u in (m.get("current_src"), m.get("src")) if u]
        urls += [s.get("src") for s in (m.get("sources") or [])
                 if isinstance(s, dict) and s.get("src")]
        seen: List[str] = []
        streams = []
        for u in urls:
            if u in seen:
                continue
            seen.append(u)
            streams.append(extract_stream_parameters(u))
        media_out.append({
            "tag": m.get("tag") or "",
            "poster": m.get("poster") or "",
            "data": dict(m.get("data") or {}),
            "streams": streams,
        })
    controls = [c for c in (probe.get("controls") or []) if isinstance(c, dict)]
    return {**frame, "media": media_out, "controls": controls,
            "probe_error": probe.get("error")}


def inspect_frame_hierarchy(client: Any,
                            evaluate_in_frame: Callable[[Dict[str, Any]], Any]
                            ) -> Dict[str, Any]:
    """Driver over an abstract CDP ``client`` (``send``/``on``) and a
    per-frame evaluator. Enables ``Target.setAutoAttach`` BEFORE reading
    the tree so OOPIF targets are attached and reported, then probes every
    frame. Returns ``{frames, n_frames, max_depth, n_media, n_oopif}``."""
    attached: List[Dict[str, Any]] = []
    client.on("Target.attachedToTarget", attached.append)
    client.send("Target.setAutoAttach", dict(AUTO_ATTACH_PARAMS))
    tree = client.send("Page.getFrameTree") or {}
    frames = merge_attached_targets(walk_frame_tree(tree), attached)
    out = []
    for f in frames:
        try:
            probe = evaluate_in_frame(f)
        except Exception as e:  # a detached/navigating frame is reported, not fatal
            probe = {"error": f"{type(e).__name__}: {str(e)[:120]}"}
        out.append(classify_probe(f, probe))
    return {
        "frames": out,
        "n_frames": len(out),
        "max_depth": max((f["depth"] for f in out), default=0),
        "n_media": sum(len(f["media"]) for f in out),
        "n_oopif": sum(1 for f in out if f["out_of_process"]),
    }


def discover_frame_hierarchy(page: Any) -> Dict[str, Any]:
    """Live driver over a Playwright ``page``: CDP for target attachment and
    the frame tree, Playwright frames (matched by URL) for the DOM probe.
    Not exercised by the unit suite (needs a real browser)."""
    client = page.context.new_cdp_session(page)
    pw_frames = {getattr(fr, "url", ""): fr for fr in getattr(page, "frames", [])}

    def _evaluate(frame: Dict[str, Any]) -> Any:
        fr = pw_frames.get(frame["url"])
        if fr is None:
            return {"error": "no Playwright frame for url"}
        return fr.evaluate(MEDIA_PROBE_JS)

    return inspect_frame_hierarchy(client, _evaluate)

"""Adaptive Streaming Manifest Parser (HLS/DASH).

Provides structured extraction, variant resolution, segment enumeration, and DRM detection
for HTTP Live Streaming (HLS / m3u8) and MPEG-DASH (MPD) manifests.
"""
from __future__ import annotations

import re
import urllib.parse
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional, Tuple


@dataclass
class StreamVariant:
    """Represents a selectable stream rendition/variant."""
    uri: str
    bandwidth: int = 0
    resolution: Optional[str] = None
    width: Optional[int] = None
    height: Optional[int] = None
    codecs: str = ""
    frame_rate: Optional[float] = None
    audio_group: str = ""
    subtitle_group: str = ""
    stream_id: str = ""


@dataclass
class MediaSegment:
    """Represents a media chunk/segment in a stream playlist."""
    uri: str
    duration: float = 0.0
    title: str = ""
    sequence_number: int = 0
    byte_range: Optional[str] = None
    key_method: Optional[str] = None
    key_uri: Optional[str] = None
    key_iv: Optional[str] = None


@dataclass
class StreamingManifest:
    """Structured representation of an HLS or MPEG-DASH manifest."""
    format: str  # "hls" | "dash"
    is_master: bool = False
    is_vod: bool = True
    is_live: bool = False
    target_duration: Optional[float] = None
    total_duration: float = 0.0
    variants: List[StreamVariant] = field(default_factory=list)
    segments: List[MediaSegment] = field(default_factory=list)
    drm_schemes: List[str] = field(default_factory=list)
    base_url: str = ""

    def get_best_variant(
        self,
        prefer_resolution: Optional[str] = None,
        max_bandwidth: Optional[int] = None,
    ) -> Optional[StreamVariant]:
        """Select optimal variant according to resolution preference or bandwidth cap."""
        candidates = self.variants
        if not candidates:
            return None

        if max_bandwidth is not None:
            filtered = [v for v in candidates if v.bandwidth <= max_bandwidth]
            if filtered:
                candidates = filtered

        if prefer_resolution:
            for v in candidates:
                if v.resolution == prefer_resolution:
                    return v

        return candidates[0] if candidates else None

    def to_dict(self) -> Dict[str, Any]:
        """Serialize manifest structure to JSON-serializable dictionary."""
        return asdict(self)


def _parse_hls_attributes(attr_line: str) -> Dict[str, str]:
    """Parse comma-separated HLS tag attributes handling quoted strings."""
    pattern = re.compile(r'([A-Z0-9_-]+)=(?:"([^"]*)"|([^",\s]+))')
    attrs: Dict[str, str] = {}
    for match in pattern.finditer(attr_line):
        key = match.group(1)
        val = match.group(2) if match.group(2) is not None else match.group(3)
        attrs[key] = val or ""
    return attrs


def _parse_iso_duration(iso_str: str) -> float:
    """Parse ISO 8601 duration (e.g. PT0H5M30.000S, PT330S) into seconds."""
    if not iso_str:
        return 0.0
    pattern = re.compile(
        r"P(?:(?P<days>\d+)D)?T(?:(?P<hours>\d+)H)?(?:(?P<minutes>\d+)M)?(?:(?P<seconds>[\d.]+)S)?"
    )
    match = pattern.match(iso_str)
    if not match:
        return 0.0
    d = match.groupdict()
    days = float(d.get("days") or 0)
    hours = float(d.get("hours") or 0)
    minutes = float(d.get("minutes") or 0)
    seconds = float(d.get("seconds") or 0)
    return (days * 86400.0) + (hours * 3600.0) + (minutes * 60.0) + seconds


class StreamingManifestParser:
    """Adaptive Streaming Manifest Parser for HLS and MPEG-DASH."""

    def parse(self, text: str, base_url: str = "") -> StreamingManifest:
        """Parse raw manifest text and return a structured StreamingManifest."""
        clean = text.lstrip()
        if clean.startswith("#EXTM3U") or "#EXT-X-" in clean:
            return self.parse_hls(text, base_url=base_url)
        elif "<MPD" in clean:
            return self.parse_dash(text, base_url=base_url)
        else:
            raise ValueError("Unrecognized streaming manifest format (expected #EXTM3U or <MPD>)")

    def parse_hls(self, text: str, base_url: str = "") -> StreamingManifest:
        """Parse HLS master or media playlist."""
        lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
        is_master = any(ln.startswith("#EXT-X-STREAM-INF:") for ln in lines)

        if is_master:
            variants: List[StreamVariant] = []
            for idx, ln in enumerate(lines):
                if not ln.startswith("#EXT-X-STREAM-INF:"):
                    continue
                attr_str = ln[len("#EXT-X-STREAM-INF:"):].strip()
                attrs = _parse_hls_attributes(attr_str)

                # Next non-comment line is the rendition URI
                uri = ""
                for next_ln in lines[idx + 1:]:
                    if not next_ln.startswith("#"):
                        uri = next_ln
                        break

                if base_url and uri and not uri.startswith(("http://", "https://")):
                    resolved_uri = urllib.parse.urljoin(base_url, uri)
                else:
                    resolved_uri = uri

                try:
                    bw = int(attrs.get("BANDWIDTH", 0) or 0)
                except ValueError:
                    bw = 0

                res = attrs.get("RESOLUTION")
                w, h = None, None
                if res and "x" in res:
                    try:
                        parts = res.split("x", 1)
                        w, h = int(parts[0]), int(parts[1])
                    except ValueError:
                        pass

                fr = None
                if "FRAME-RATE" in attrs:
                    try:
                        fr = float(attrs["FRAME-RATE"])
                    except ValueError:
                        pass

                variants.append(
                    StreamVariant(
                        uri=resolved_uri,
                        bandwidth=bw,
                        resolution=res,
                        width=w,
                        height=h,
                        codecs=attrs.get("CODECS", ""),
                        frame_rate=fr,
                        audio_group=attrs.get("AUDIO", ""),
                        subtitle_group=attrs.get("SUBTITLES", ""),
                    )
                )

            variants.sort(key=lambda v: v.bandwidth, reverse=True)
            return StreamingManifest(
                format="hls",
                is_master=True,
                is_vod=True,
                is_live=False,
                variants=variants,
                base_url=base_url,
            )
        else:
            # Media playlist
            is_vod = any(ln == "#EXT-X-ENDLIST" for ln in lines)
            is_live = not is_vod
            seq = 0
            target_dur = None
            total_dur = 0.0
            segments: List[MediaSegment] = []

            curr_key_method: Optional[str] = None
            curr_key_uri: Optional[str] = None
            curr_key_iv: Optional[str] = None
            curr_byterange: Optional[str] = None
            seen_key_methods: set[str] = set()

            for idx, ln in enumerate(lines):
                if ln.startswith("#EXT-X-MEDIA-SEQUENCE:"):
                    try:
                        seq = int(ln.split(":", 1)[1].strip())
                    except ValueError:
                        pass
                elif ln.startswith("#EXT-X-TARGETDURATION:"):
                    try:
                        target_dur = float(ln.split(":", 1)[1].strip())
                    except ValueError:
                        pass
                elif ln.startswith("#EXT-X-KEY:"):
                    kattrs = _parse_hls_attributes(ln[len("#EXT-X-KEY:"):].strip())
                    curr_key_method = kattrs.get("METHOD")
                    if curr_key_method:
                        seen_key_methods.add(curr_key_method)
                    kuri = kattrs.get("URI")
                    if kuri and base_url and not kuri.startswith(("http://", "https://")):
                        curr_key_uri = urllib.parse.urljoin(base_url, kuri)
                    else:
                        curr_key_uri = kuri
                    curr_key_iv = kattrs.get("IV")
                elif ln.startswith("#EXT-X-BYTERANGE:"):
                    curr_byterange = ln.split(":", 1)[1].strip()
                elif ln.startswith("#EXTINF:"):
                    inf = ln[len("#EXTINF:"):].strip()
                    parts = inf.split(",", 1)
                    dur = 0.0
                    title = ""
                    try:
                        dur = float(parts[0])
                    except ValueError:
                        pass
                    if len(parts) > 1:
                        title = parts[1].strip()

                    # Next non-comment line is segment URI
                    uri = ""
                    for next_ln in lines[idx + 1:]:
                        if not next_ln.startswith("#"):
                            uri = next_ln
                            break

                    if base_url and uri and not uri.startswith(("http://", "https://")):
                        resolved_uri = urllib.parse.urljoin(base_url, uri)
                    else:
                        resolved_uri = uri

                    segments.append(
                        MediaSegment(
                            uri=resolved_uri,
                            duration=dur,
                            title=title,
                            sequence_number=seq,
                            byte_range=curr_byterange,
                            key_method=curr_key_method,
                            key_uri=curr_key_uri,
                            key_iv=curr_key_iv,
                        )
                    )
                    total_dur += dur
                    seq += 1
                    curr_byterange = None

            drm_schemes = sorted(seen_key_methods)
            return StreamingManifest(
                format="hls",
                is_master=False,
                is_vod=is_vod,
                is_live=is_live,
                target_duration=target_dur,
                total_duration=total_dur,
                segments=segments,
                drm_schemes=drm_schemes,
                base_url=base_url,
            )

    def parse_dash(self, text: str, base_url: str = "") -> StreamingManifest:
        """Parse MPEG-DASH MPD manifest."""
        root = ET.fromstring(text)

        # Strip XML namespaces for tag matching
        def strip_ns(tag: str) -> str:
            return tag.split("}")[-1] if "}" in tag else tag

        mpd_type = root.attrib.get("type", "static")
        is_vod = mpd_type != "dynamic"
        is_live = mpd_type == "dynamic"

        dur_str = root.attrib.get("mediaPresentationDuration", "")
        total_duration = _parse_iso_duration(dur_str)

        variants: List[StreamVariant] = []
        drm_schemes: List[str] = []

        # Find periods, adaptation sets, representations
        for elem in root.iter():
            tag = strip_ns(elem.tag)
            if tag == "ContentProtection":
                scheme = elem.attrib.get("schemeIdUri")
                if scheme and scheme not in drm_schemes:
                    drm_schemes.append(scheme)

        # Parse AdaptationSets and their Representations
        has_adaptations = False
        for adapt in root.iter():
            if strip_ns(adapt.tag) != "AdaptationSet":
                continue
            has_adaptations = True
            content_type = adapt.attrib.get("contentType", "").lower()
            mime_type = adapt.attrib.get("mimeType", "").lower()

            for rep in adapt.iter():
                if strip_ns(rep.tag) != "Representation":
                    continue
                rep_type = rep.attrib.get("contentType", content_type).lower()
                rep_mime = rep.attrib.get("mimeType", mime_type).lower()

                has_video_dims = "width" in rep.attrib or "height" in rep.attrib
                is_audio = rep_type == "audio" or ("audio" in rep_mime and not has_video_dims)

                if is_audio:
                    continue

                rep_id = rep.attrib.get("id", "")
                try:
                    bw = int(rep.attrib.get("bandwidth", 0) or 0)
                except ValueError:
                    bw = 0
                w, h = None, None
                if "width" in rep.attrib and "height" in rep.attrib:
                    try:
                        w = int(rep.attrib["width"])
                        h = int(rep.attrib["height"])
                    except ValueError:
                        pass
                res = f"{w}x{h}" if w and h else None

                fr = None
                if "frameRate" in rep.attrib:
                    try:
                        fr_val = rep.attrib["frameRate"]
                        if "/" in fr_val:
                            n, d = fr_val.split("/", 1)
                            fr = float(n) / float(d)
                        else:
                            fr = float(fr_val)
                    except ValueError:
                        pass

                codecs = rep.attrib.get("codecs", adapt.attrib.get("codecs", ""))
                uri = rep.attrib.get("BaseURL", "")
                if not uri:
                    for child in rep:
                        if strip_ns(child.tag) == "BaseURL" and child.text:
                            uri = child.text.strip()
                            break
                if base_url and uri and not uri.startswith(("http://", "https://")):
                    uri = urllib.parse.urljoin(base_url, uri)

                variants.append(
                    StreamVariant(
                        uri=uri,
                        bandwidth=bw,
                        resolution=res,
                        width=w,
                        height=h,
                        codecs=codecs,
                        frame_rate=fr,
                        stream_id=rep_id,
                    )
                )

        if not has_adaptations:
            for rep in root.iter():
                if strip_ns(rep.tag) != "Representation":
                    continue
                rep_mime = rep.attrib.get("mimeType", "").lower()
                has_video_dims = "width" in rep.attrib or "height" in rep.attrib
                if "audio" in rep_mime and not has_video_dims:
                    continue
                rep_id = rep.attrib.get("id", "")
                try:
                    bw = int(rep.attrib.get("bandwidth", 0) or 0)
                except ValueError:
                    bw = 0
                w, h = None, None
                if "width" in rep.attrib and "height" in rep.attrib:
                    try:
                        w = int(rep.attrib["width"])
                        h = int(rep.attrib["height"])
                    except ValueError:
                        pass
                res = f"{w}x{h}" if w and h else None

                fr = None
                if "frameRate" in rep.attrib:
                    try:
                        fr_val = rep.attrib["frameRate"]
                        if "/" in fr_val:
                            n, d = fr_val.split("/", 1)
                            fr = float(n) / float(d)
                        else:
                            fr = float(fr_val)
                    except ValueError:
                        pass

                codecs = rep.attrib.get("codecs", "")
                uri = rep.attrib.get("BaseURL", "")
                if not uri:
                    for child in rep:
                        if strip_ns(child.tag) == "BaseURL" and child.text:
                            uri = child.text.strip()
                            break
                if base_url and uri and not uri.startswith(("http://", "https://")):
                    uri = urllib.parse.urljoin(base_url, uri)

                variants.append(
                    StreamVariant(
                        uri=uri,
                        bandwidth=bw,
                        resolution=res,
                        width=w,
                        height=h,
                        codecs=codecs,
                        frame_rate=fr,
                        stream_id=rep_id,
                    )
                )

        variants.sort(key=lambda v: v.bandwidth, reverse=True)

        return StreamingManifest(
            format="dash",
            is_master=True,
            is_vod=is_vod,
            is_live=is_live,
            total_duration=total_duration,
            variants=variants,
            drm_schemes=drm_schemes,
            base_url=base_url,
        )


def parse_streaming_manifest(text: str, base_url: str = "") -> StreamingManifest:
    """Convenience functional interface for parsing streaming manifests."""
    return StreamingManifestParser().parse(text, base_url=base_url)

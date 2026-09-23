"""Adaptive Streaming Manifest Parser (HLS/DASH).

Provides structured extraction, variant resolution, segment enumeration, and DRM detection
for HTTP Live Streaming (HLS / m3u8) and MPEG-DASH (MPD) manifests.
"""
from __future__ import annotations

import math
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
    segments: List[MediaSegment] = field(default_factory=list)
    initialization_uri: Optional[str] = None
    initialization_byte_range: Optional[str] = None
    index_range: Optional[str] = None
    segment_template: Dict[str, str] = field(default_factory=dict)
    segment_timeline: List[Dict[str, str]] = field(default_factory=list)
    segments_complete: bool = False


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
    initialization_uri: Optional[str] = None
    initialization_byte_range: Optional[str] = None


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
        r"P(?:(?P<days>\d+)D)?(?:T(?:(?P<hours>\d+)H)?(?:(?P<minutes>\d+)M)?(?:(?P<seconds>\d+(?:\.\d+)?)S)?)?"
    )
    match = pattern.fullmatch(iso_str)
    if not match:
        return 0.0
    d = match.groupdict()
    days = float(d.get("days") or 0)
    hours = float(d.get("hours") or 0)
    minutes = float(d.get("minutes") or 0)
    seconds = float(d.get("seconds") or 0)
    return (days * 86400.0) + (hours * 3600.0) + (minutes * 60.0) + seconds


def _dash_children(node: ET.Element, name: str) -> List[ET.Element]:
    return [child for child in node if child.tag.rsplit("}", 1)[-1] == name]


def _dash_template_url(
    template: str, variant: StreamVariant, number: Optional[int] = None,
    time: Optional[int] = None,
) -> str:
    def replace(match: re.Match[str]) -> str:
        token = match.group(0)
        if token == "$$":
            return "$"
        spec = re.fullmatch(
            r"(RepresentationID|Bandwidth|Number|Time)(%0?\d*[diouxX])?", token[1:-1]
        )
        if spec is None:
            raise ValueError(f"Unsupported DASH template token: {token}")
        name, formatting = spec.groups()
        values = {"RepresentationID": variant.stream_id, "Bandwidth": variant.bandwidth,
                  "Number": number, "Time": time}
        value = values[name]
        if value is None:
            raise ValueError(f"Unresolved DASH template token: {token}")
        if name == "RepresentationID":
            if formatting or not value:
                raise ValueError("DASH RepresentationID requires an unformatted identifier")
            return str(value)
        if formatting:
            width = re.search(r"\d+", formatting)
            if width and int(width.group()) > 32:
                raise ValueError("DASH template formatting width exceeds 32")
            return formatting % value
        return str(value)

    return re.sub(r"\$\$|\$[^$]+\$", replace, template)


def _dash_segment_times(
    attrs: Dict[str, str], timeline: List[Dict[str, str]],
    period_duration: Optional[float],
) -> Optional[List[Tuple[int, int]]]:
    timescale = int(attrs.get("timescale", "1"))
    if timescale <= 0:
        raise ValueError("DASH timescale must be positive")
    offset = int(attrs.get("presentationTimeOffset", "0"))
    points: List[Tuple[int, int]] = []
    if timeline:
        current = 0
        for index, entry in enumerate(timeline):
            current = int(entry.get("t", current))
            duration = int(entry.get("d", "0"))
            repeat = int(entry.get("r", "0"))
            if duration <= 0 or repeat < -1:
                raise ValueError("Invalid DASH timeline duration or repeat")
            if repeat == -1:
                if index + 1 < len(timeline):
                    next_time = timeline[index + 1].get("t")
                    if next_time is None:
                        return None
                    end: float = int(next_time)
                elif period_duration is not None:
                    end = offset + period_duration * timescale
                else:
                    return None
                count = max(0, math.ceil((end - current) / duration))
            else:
                count = repeat + 1
            if len(points) + count > 100000:
                raise ValueError("DASH segment expansion exceeds 100000 segments")
            points.extend((current + step * duration, duration) for step in range(count))
            current += count * duration
        return points

    if "duration" not in attrs or period_duration is None:
        return None
    duration = int(attrs["duration"])
    if duration <= 0:
        raise ValueError("DASH segment duration must be positive")
    delta = int(attrs.get("eptDelta", "0"))
    count = max(0, math.ceil((period_duration * timescale - delta) / duration))
    if count > 100000:
        raise ValueError("DASH segment expansion exceeds 100000 segments")
    return [(offset + step * duration, duration) for step in range(count)]


def _dash_resolve_segments(
    nodes: List[ET.Element], variant: StreamVariant, manifest_url: str,
    period_duration: Optional[float],
) -> None:
    base_uri = manifest_url
    has_base = False
    kind = ""
    attrs: Dict[str, str] = {}
    initialization: Optional[ET.Element] = None
    timeline: List[Dict[str, str]] = []
    segment_urls: List[ET.Element] = []
    for node in nodes:
        bases = _dash_children(node, "BaseURL")
        relative = (bases[0].text or "").strip() if bases else node.get("BaseURL", "")
        if relative:
            base_uri = urllib.parse.urljoin(base_uri, relative)
            has_base = True
        addressing = [child for child in node if child.tag.rsplit("}", 1)[-1]
                      in {"SegmentTemplate", "SegmentList", "SegmentBase"}]
        if len(addressing) > 1:
            raise ValueError("Multiple DASH segment addressing modes at one level")
        if not addressing:
            continue
        descriptor = addressing[0]
        current_kind = descriptor.tag.rsplit("}", 1)[-1]
        if current_kind != kind:
            attrs = {}
            initialization = None
            timeline = []
            segment_urls = []
        kind = current_kind
        attrs.update(descriptor.attrib)
        init_nodes = _dash_children(descriptor, "Initialization")
        if init_nodes:
            initialization = init_nodes[0]
        timelines = _dash_children(descriptor, "SegmentTimeline")
        if timelines:
            timeline = [dict(entry.attrib) for entry in _dash_children(timelines[0], "S")]
        urls = _dash_children(descriptor, "SegmentURL")
        if urls:
            segment_urls = urls

    variant.index_range = attrs.get("indexRange")
    variant.segment_timeline = timeline
    if initialization is not None:
        if not initialization.get("sourceURL") and not has_base:
            raise ValueError("DASH Initialization has no media resource")
        variant.initialization_uri = urllib.parse.urljoin(
            base_uri, initialization.get("sourceURL", "")
        )
        variant.initialization_byte_range = initialization.get("range")
    if kind == "SegmentTemplate":
        variant.segment_template = dict(attrs)
        variant.segment_template["base_url"] = base_uri
        if attrs.get("initialization"):
            variant.initialization_uri = urllib.parse.urljoin(
                base_uri, _dash_template_url(attrs["initialization"], variant)
            )
        points = _dash_segment_times(attrs, timeline, period_duration)
        if points is not None and attrs.get("media"):
            start = int(attrs.get("startNumber", "1"))
            timescale = int(attrs.get("timescale", "1"))
            for index, (time, duration) in enumerate(points):
                variant.segments.append(MediaSegment(
                    uri=urllib.parse.urljoin(
                        base_uri, _dash_template_url(attrs["media"], variant, start + index, time)
                    ),
                    duration=duration / timescale,
                    sequence_number=start + index,
                    initialization_uri=variant.initialization_uri,
                    initialization_byte_range=variant.initialization_byte_range,
                ))
            variant.segments_complete = True
    elif kind == "SegmentList":
        timescale = int(attrs.get("timescale", "1"))
        if timescale <= 0:
            raise ValueError("DASH timescale must be positive")
        points = _dash_segment_times(attrs, timeline, period_duration) if timeline else None
        if points is not None and len(points) != len(segment_urls):
            raise ValueError("DASH SegmentList and timeline lengths differ")
        if len(segment_urls) > 100000:
            raise ValueError("DASH segment expansion exceeds 100000 segments")
        start = int(attrs.get("startNumber", "1"))
        for index, segment in enumerate(segment_urls):
            media = segment.get("media", "")
            if not media and (not has_base or urllib.parse.urlsplit(base_uri).path.endswith("/")):
                raise ValueError("DASH SegmentURL has no media resource")
            duration = points[index][1] if points is not None else int(attrs.get("duration", "0"))
            variant.segments.append(MediaSegment(
                uri=urllib.parse.urljoin(base_uri, media),
                duration=duration / timescale,
                sequence_number=start + index,
                byte_range=segment.get("mediaRange"),
                initialization_uri=variant.initialization_uri,
                initialization_byte_range=variant.initialization_byte_range,
            ))
        variant.segments_complete = True
    elif has_base and base_uri and not urllib.parse.urlsplit(base_uri).path.endswith("/"):
        variant.segments.append(MediaSegment(
            uri=base_uri, duration=period_duration or 0.0,
            initialization_uri=variant.initialization_uri,
            initialization_byte_range=variant.initialization_byte_range,
        ))
        variant.segments_complete = True
    variant.uri = variant.segments[0].uri if variant.segments else ""


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
            is_vod = any(ln in {"#EXT-X-ENDLIST", "#EXT-X-PLAYLIST-TYPE:VOD"} for ln in lines)
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
            initialization_uri: Optional[str] = None
            initialization_byte_range: Optional[str] = None

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
                    if curr_key_method and curr_key_method != "NONE":
                        seen_key_methods.add(curr_key_method)
                    kuri = kattrs.get("URI")
                    if kuri and base_url and not kuri.startswith(("http://", "https://")):
                        curr_key_uri = urllib.parse.urljoin(base_url, kuri)
                    else:
                        curr_key_uri = kuri
                    curr_key_iv = kattrs.get("IV")
                    if curr_key_method == "NONE":
                        curr_key_uri = None
                        curr_key_iv = None
                elif ln.startswith("#EXT-X-MAP:"):
                    mattrs = _parse_hls_attributes(ln[len("#EXT-X-MAP:"):].strip())
                    initialization_uri = urllib.parse.urljoin(base_url, mattrs["URI"])
                    initialization_byte_range = mattrs.get("BYTERANGE")
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
                            initialization_uri=initialization_uri,
                            initialization_byte_range=initialization_byte_range,
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
        """Parse DASH representations and the segment addresses present in this MPD."""
        root = ET.fromstring(text)
        is_live = root.get("type", "static") == "dynamic"
        total_duration = _parse_iso_duration(root.get("mediaPresentationDuration", ""))
        variants: List[StreamVariant] = []
        drm_schemes: List[str] = []
        for element in root.iter():
            if element.tag.rsplit("}", 1)[-1] == "ContentProtection":
                scheme = element.get("schemeIdUri")
                if scheme and scheme not in drm_schemes:
                    drm_schemes.append(scheme)

        periods = _dash_children(root, "Period") or [root]
        previous_end: Optional[float] = None if is_live else 0.0
        for index, period in enumerate(periods):
            period_start = (_parse_iso_duration(period.attrib["start"])
                            if "start" in period.attrib else previous_end)
            period_duration: Optional[float] = None
            if "duration" in period.attrib:
                period_duration = _parse_iso_duration(period.attrib["duration"])
            elif (index + 1 < len(periods) and "start" in periods[index + 1].attrib
                  and period_start is not None):
                period_duration = _parse_iso_duration(periods[index + 1].attrib["start"]) - period_start
            elif (index == len(periods) - 1 and total_duration > 0
                  and period_start is not None):
                period_duration = total_duration - period_start
            if period_duration is not None and period_duration < 0:
                raise ValueError("DASH Period has a negative duration")
            previous_end = (period_start + period_duration
                            if period_start is not None and period_duration is not None else None)

            adaptations = _dash_children(period, "AdaptationSet") + [period]
            for adaptation in adaptations:
                for representation in _dash_children(adaptation, "Representation"):
                    metadata = dict(adaptation.attrib)
                    metadata.update(representation.attrib)
                    content_type = metadata.get("contentType", "").lower()
                    mime_type = metadata.get("mimeType", "").lower()
                    has_video_dims = "width" in metadata or "height" in metadata
                    if content_type == "audio" or ("audio" in mime_type and not has_video_dims):
                        continue
                    try:
                        bandwidth = int(representation.get("bandwidth", "0"))
                    except ValueError:
                        bandwidth = 0
                    width, height = None, None
                    if "width" in metadata and "height" in metadata:
                        try:
                            width, height = int(metadata["width"]), int(metadata["height"])
                        except ValueError:
                            pass
                    frame_rate = None
                    if "frameRate" in metadata:
                        try:
                            value = metadata["frameRate"]
                            if "/" in value:
                                numerator, denominator = value.split("/", 1)
                                frame_rate = float(numerator) / float(denominator)
                            else:
                                frame_rate = float(value)
                        except (ValueError, ZeroDivisionError):
                            pass
                    variant = StreamVariant(
                        uri="", bandwidth=bandwidth,
                        resolution=f"{width}x{height}" if width and height else None,
                        width=width, height=height, codecs=metadata.get("codecs", ""),
                        frame_rate=frame_rate, stream_id=representation.get("id", ""),
                    )
                    nodes = [root]
                    if period is not root:
                        nodes.append(period)
                    if adaptation is not period:
                        nodes.append(adaptation)
                    nodes.append(representation)
                    _dash_resolve_segments(nodes, variant, base_url, period_duration)
                    variants.append(variant)

        variants.sort(key=lambda variant: variant.bandwidth, reverse=True)
        return StreamingManifest(
            format="dash", is_master=True, is_vod=not is_live, is_live=is_live,
            total_duration=total_duration, variants=variants,
            drm_schemes=drm_schemes, base_url=base_url,
        )


def parse_streaming_manifest(text: str, base_url: str = "") -> StreamingManifest:
    """Convenience functional interface for parsing streaming manifests."""
    return StreamingManifestParser().parse(text, base_url=base_url)

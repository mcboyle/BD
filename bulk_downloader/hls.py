"""Row 900 -- multi-segment HLS media key fetching and payload assembly.

Segmented HLS media whose manifest declares a per-segment `#EXT-X-KEY`
needs the key fetched and each segment decrypted before the segments can
be concatenated into one continuous playable file. This module owns that
seam: parsing `#EXT-X-KEY` descriptors, fetching the key bytes through a
caller-supplied (already-authenticated) fetcher, decrypting AES-128-CBC
segments, and assembling the result.

CHARTER SCOPE (project-knowledge/DRM_EME_DETECTION_DECISION.md): this is
the SAME "downloadable-aes" playback yt-dlp already downloads natively --
a site-provided, fetchable AES-128/SAMPLE-AES key, not DRM. That decision's
hard line is CDM-DRM (Widevine/PlayReady/FairPlay) key extraction and
decryption, which stays permanently out of scope. Every function here that
touches a key descriptor calls `_assert_downloadable`, which reuses
`bulk_downloader.drm_detect.classify_protection` -- the existing, single
place that decides what counts as DRM -- and raises `DrmScopeError` before
any fetch is attempted for a CDM-classified descriptor. There is no CDM
path, no license request, and no Widevine/PlayReady/FairPlay handling
anywhere in this file, ever.

Fleet Rule 21 (0 site logins touched): this module never opens its own
network connection or performs a login. Every fetch goes through a
caller-supplied callable, so the caller owns the authenticated
session/cookies/headers.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional, Sequence

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from bulk_downloader import drm_detect

_KEY_LINE_RE = re.compile(r'^#EXT-X-KEY:(.*)$')
_ATTR_RE = re.compile(r'([A-Z0-9-]+)=("(?:[^"\\]|\\.)*"|[^,]*)')
_BYTERANGE_RE = re.compile(r'^#EXT-X-BYTERANGE:(\d+)(?:@(\d+))?\s*$')
SUPPORTED_METHODS = frozenset({"AES-128"})   # whole-segment AES-128-CBC; SAMPLE-AES is a different cipher layout
AES_KEY_LEN = 16                              # METHOD=AES-128 is exactly a 128-bit key (RFC 8216 4.3.2.4)


class UnsupportedHlsError(ValueError):
    """Raised for a manifest feature this module does not implement
    (a non-AES-128 METHOD, an initialization MAP): refused explicitly,
    never silently decrypted with the wrong algorithm or mislaid."""


class DrmScopeError(Exception):
    """Raised when a key descriptor classifies as CDM-DRM. This module
    never fetches or uses such a key -- see project-knowledge/
    DRM_EME_DETECTION_DECISION.md, 'THE HARD LINE'."""


@dataclass(frozen=True)
class KeyDescriptor:
    method: str
    uri: Optional[str]
    keyformat: Optional[str]
    iv: Optional[bytes]


@dataclass(frozen=True)
class SegmentPlan:
    uri: str
    key: Optional[KeyDescriptor]
    iv: Optional[bytes]
    byterange: Optional[tuple] = None   # (length, offset) from #EXT-X-BYTERANGE


def parse_iv(iv_text: Optional[str]) -> Optional[bytes]:
    """RFC 8216 4.3.2.4: IV is a hexadecimal-sequence (0x prefix) of a
    128-bit value; the spec allows fewer digits than 32 (``0x1`` is the
    same IV as ``0x00000000000000000000000000000001``), so the value is
    parsed as an integer and rendered as 16 big-endian bytes. Longer
    than 128 bits or non-hex is an error, not a short IV."""
    if not iv_text:
        return None
    text = iv_text.strip()
    if not text.lower().startswith("0x"):
        raise ValueError(f"IV must be a 0x hexadecimal-sequence, got {iv_text!r}")
    digits = text[2:]
    if not digits or not re.fullmatch(r"[0-9a-fA-F]+", digits):
        raise ValueError(f"IV is not hexadecimal: {iv_text!r}")
    value = int(digits, 16)
    if value >= 1 << 128:
        raise ValueError(f"IV is wider than 128 bits: {iv_text!r}")
    return value.to_bytes(16, "big")


def _unquote(v: str) -> str:
    v = v.strip()
    if len(v) >= 2 and v[0] == '"' and v[-1] == '"':
        return v[1:-1]
    return v


def _parse_attrs(attr_text: str) -> Dict[str, str]:
    return {k: _unquote(v) for k, v in _ATTR_RE.findall(attr_text)}


def _descriptor_from_attrs(attrs: Dict[str, str]) -> Optional[KeyDescriptor]:
    method = (attrs.get("METHOD") or "").upper()
    if not method or method == "NONE":
        return None
    iv = parse_iv(attrs.get("IV"))
    return KeyDescriptor(
        method=method,
        uri=attrs.get("URI"),
        keyformat=attrs.get("KEYFORMAT"),
        iv=iv,
    )


def parse_key_descriptors(manifest_text: str) -> List[KeyDescriptor]:
    """Extract every `#EXT-X-KEY` descriptor from an HLS manifest.
    `METHOD=NONE` lines are skipped (no key -- unencrypted segments)."""
    out: List[KeyDescriptor] = []
    for line in (manifest_text or "").splitlines():
        line = line.strip()
        m = _KEY_LINE_RE.match(line)
        if not m:
            continue
        desc = _descriptor_from_attrs(_parse_attrs(m.group(1)))
        if desc is not None:
            out.append(desc)
    return out


def default_iv_for_sequence(seq: int) -> bytes:
    """RFC 8216 5.2: when a KEY tag has no explicit IV, the IV is the media
    sequence number of the segment, as a 16-byte big-endian integer."""
    return int(seq).to_bytes(16, "big")


def plan_segments(manifest_text: str, media_sequence: int = 0) -> List[SegmentPlan]:
    """Walk the manifest top-to-bottom tracking the currently active
    `#EXT-X-KEY` (HLS semantics: a KEY tag applies to every following
    segment URI until superseded by another KEY tag), pairing each segment
    URI with its key descriptor and resolved IV (explicit IV attribute,
    else derived from the running media sequence number)."""
    active: Optional[KeyDescriptor] = None
    seq = media_sequence
    plans: List[SegmentPlan] = []
    pending_range: Optional[tuple] = None
    next_offset: Optional[int] = None   # RFC 8216 4.3.2.2: omitted @offset continues the previous sub-range
    for raw in (manifest_text or "").splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith("#EXT-X-MEDIA-SEQUENCE:"):
            seq = int(line.split(":", 1)[1].strip())
            continue
        if line.startswith("#EXT-X-KEY:"):
            m = _KEY_LINE_RE.match(line)
            active = _descriptor_from_attrs(_parse_attrs(m.group(1))) if m else None
            continue
        if line.startswith("#EXT-X-MAP:"):
            # an initialization section (fMP4) is not a media segment; concatenating
            # it as one would corrupt the payload and this module does not lay out fMP4
            raise UnsupportedHlsError("EXT-X-MAP initialization sections are not supported")
        if line.startswith("#EXT-X-BYTERANGE:"):
            m = _BYTERANGE_RE.match(line)
            if not m:
                raise ValueError(f"malformed BYTERANGE: {line!r}")
            length = int(m.group(1))
            if m.group(2) is not None:
                offset = int(m.group(2))
            elif next_offset is not None:
                offset = next_offset
            else:
                raise ValueError("BYTERANGE without @offset needs a preceding sub-range of the same resource")
            pending_range = (length, offset)
            continue
        if line.startswith("#"):
            continue
        # a bare (non-comment) line in a media playlist is a segment URI
        iv = None
        if active is not None:
            iv = active.iv if active.iv is not None else default_iv_for_sequence(seq)
        plans.append(SegmentPlan(uri=line, key=active, iv=iv, byterange=pending_range))
        next_offset = (pending_range[0] + pending_range[1]) if pending_range else None
        pending_range = None
        seq += 1
    return plans


def _assert_downloadable(desc: KeyDescriptor) -> None:
    """The hard scope gate: refuse anything that classifies as CDM-DRM.
    Reuses the existing detection classifier rather than re-deriving the
    keyformat -> system mapping, so there is exactly one place that decides
    what counts as DRM."""
    probe = f"#EXT-X-KEY:METHOD={desc.method}"
    if desc.keyformat:
        probe += f',KEYFORMAT="{desc.keyformat}"'
    result = drm_detect.classify_protection(hls_text=probe)
    if result["category"] == drm_detect.CAT_CDM:
        raise DrmScopeError(
            f"key descriptor classifies as CDM-DRM ({result['system']}); "
            f"this module never fetches or uses such a key")
    if desc.method not in SUPPORTED_METHODS:
        # SAMPLE-AES (per-sample CBC inside the container) or anything else
        # is NOT whole-segment AES-128-CBC: refuse, never substitute the cipher
        raise UnsupportedHlsError(f"METHOD={desc.method} is not supported (only {sorted(SUPPORTED_METHODS)})")
    if not desc.uri:
        raise ValueError("key descriptor has no URI to fetch")


def fetch_key_bytes(desc: KeyDescriptor, fetch: Callable[[str], bytes]) -> bytes:
    """Fetch the raw key bytes for `desc` via the caller-supplied `fetch`
    callable. The caller owns the authenticated session/cookies -- this
    function never opens its own connection. Refuses before fetching
    anything that classifies as CDM-DRM."""
    _assert_downloadable(desc)
    key = fetch(desc.uri)
    _check_key(key)
    return key


def _check_key(key: bytes) -> None:
    if not isinstance(key, (bytes, bytearray)) or len(key) != AES_KEY_LEN:
        raise ValueError(f"AES-128 key must be exactly {AES_KEY_LEN} bytes, got {len(key) if key is not None else None}")


def decrypt_segment(ciphertext: bytes, key: bytes, iv: bytes) -> bytes:
    """AES-128-CBC decrypt one HLS segment (HLS spec METHOD=AES-128), then
    strip PKCS7 padding. The key is exactly 16 bytes: a 32-byte key would
    silently select AES-256, a different cipher from the one declared."""
    _check_key(key)
    if len(iv) != 16:
        raise ValueError("IV must be 16 bytes")
    decryptor = Cipher(algorithms.AES(key), modes.CBC(iv)).decryptor()
    padded = decryptor.update(ciphertext) + decryptor.finalize()
    if not padded:
        raise ValueError("decrypted segment is empty")
    pad_len = padded[-1]
    if pad_len < 1 or pad_len > 16 or padded[-pad_len:] != bytes([pad_len]) * pad_len:
        raise ValueError("invalid PKCS7 padding on decrypted segment")
    return padded[:-pad_len]


def assemble_payload(segments: Sequence[bytes], keys: Sequence[Optional[bytes]],
                      ivs: Sequence[Optional[bytes]]) -> bytes:
    """Decrypt (where a key is given) and concatenate segments, in order,
    into one continuous byte payload. `keys[i] is None` means segment i is
    unencrypted (METHOD=NONE) and is appended as-is."""
    if not (len(segments) == len(keys) == len(ivs)):
        raise ValueError("segments/keys/ivs must be the same length")
    out = bytearray()
    for seg, key, iv in zip(segments, keys, ivs):
        if key is None:
            out += seg
        else:
            if iv is None:
                raise ValueError("AES-128 segment requires an IV")
            out += decrypt_segment(seg, key, iv)
    return bytes(out)


def write_payload(path: str, payload: bytes) -> None:
    """Write the assembled payload to `path`, exactly, no transformation."""
    with open(path, "wb") as f:
        f.write(payload)


def assemble_from_manifest(manifest_text: str, *,
                            fetch_segment: Callable[[str], bytes],
                            fetch_key: Callable[[str], bytes],
                            media_sequence: int = 0) -> bytes:
    """End-to-end: plan segments, refuse any CDM-DRM descriptor before
    fetching anything, fetch each distinct key once (cached by URI) via
    the caller-supplied `fetch_key`, fetch and decrypt every segment in
    order via `fetch_segment`, and return the assembled payload."""
    plans = plan_segments(manifest_text, media_sequence=media_sequence)
    for plan in plans:
        if plan.key is not None:
            _assert_downloadable(plan.key)
    key_cache: Dict[str, bytes] = {}
    segments: List[bytes] = []
    keys: List[Optional[bytes]] = []
    ivs: List[Optional[bytes]] = []
    for plan in plans:
        raw_key = None
        if plan.key is not None:
            if plan.key.uri not in key_cache:
                key_cache[plan.key.uri] = fetch_key_bytes(plan.key, fetch_key)
            raw_key = key_cache[plan.key.uri]
        data = fetch_segment(plan.uri)
        if plan.byterange is not None:
            # the URI names a resource; this segment is the declared sub-range of it
            length, offset = plan.byterange
            if offset + length > len(data):
                raise ValueError(f"BYTERANGE {length}@{offset} exceeds the {len(data)}-byte resource {plan.uri}")
            data = data[offset:offset + length]
        segments.append(data)
        keys.append(raw_key)
        ivs.append(plan.iv)
    return assemble_payload(segments, keys, ivs)

"""MSE (Media Source Extensions) buffer monitor and assembler (Row 929).

Modern players fetch fragmented media and push the bytes straight into
``SourceBuffer.appendBuffer`` -- there is no media URL to download. The
document-start hook below records what the page appends; the Python half
reassembles those segments into the container the page was decoding.

Two halves, mirroring :mod:`bulk_downloader.eme_detect`:

* **Page half:** :data:`MSE_INIT_JS` wraps ``URL.createObjectURL`` (to learn
  which MediaSource a ``<video>`` is bound to), ``MediaSource.prototype
  .addSourceBuffer`` (mime per buffer) and ``SourceBuffer.prototype
  .appendBuffer`` (a private copy of every appended chunk). Every wrapper
  calls straight through; playback is untouched. The only page-visible
  surface is one NON-enumerable ``window.__bd_mse`` accessor, the wrappers
  keep their native ``name``/``length`` and read as ``[native code]`` under
  ``Function.prototype.toString``, and the page-side buffer is emptied by
  each drain and capped so a long session cannot grow it unbounded.
* **Python half:** :func:`read_mse_records` drains the page;
  :class:`SegmentStore` orders segments per SourceBuffer and
  :meth:`SegmentStore.assemble` concatenates them in append order, which for
  fMP4 (init ``ftyp``+``moov`` then ``moof``/``mdat`` pairs) and WebM
  (EBML header then clusters) is the on-disk file the page was playing.
"""

from __future__ import annotations

import base64
import struct
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Tuple

# Bytes the page will hold between drains before it starts counting drops.
MSE_PAGE_BUFFER_CAP = 64 * 1024 * 1024

MSE_INIT_JS = r"""
(() => {
  try {
    if (Object.getOwnPropertyDescriptor(window, '__bd_mse')) return;
    const CAP = %(cap)d;
    const state = {segments: [], buffers: [], bytes: 0, dropped: 0, seq: 0,
                   nextSource: 1, nextBuffer: 1};
    const sourceIds = new WeakMap();
    const bufferIds = new WeakMap();
    const natives = new WeakMap();
    const nativeToString = Function.prototype.toString;

    const wrap = (target, name, make) => {
      const desc = Object.getOwnPropertyDescriptor(target, name);
      if (!desc || typeof desc.value !== 'function') return;
      const orig = desc.value;
      const w = make(orig);
      Object.defineProperty(w, 'name', {value: orig.name, configurable: true});
      Object.defineProperty(w, 'length', {value: orig.length, configurable: true});
      natives.set(w, nativeToString.call(orig));
      Object.defineProperty(target, name, {value: w, writable: desc.writable,
                                           enumerable: desc.enumerable, configurable: desc.configurable});
    };

    wrap(Function.prototype, 'toString', (orig) => function toString() {
      const n = natives.get(this);
      return n !== undefined ? n : orig.call(this);
    });

    const sourceId = (ms) => {
      let id = sourceIds.get(ms);
      if (!id) { id = state.nextSource++; sourceIds.set(ms, id); }
      return id;
    };

    if (typeof URL !== 'undefined') {
      wrap(URL, 'createObjectURL', (orig) => function createObjectURL(obj) {
        try {
          if (typeof MediaSource !== 'undefined' && obj instanceof MediaSource) sourceId(obj);
        } catch (e) {}
        return orig.apply(this, arguments);
      });
    }

    if (typeof MediaSource !== 'undefined' && MediaSource.prototype) {
      wrap(MediaSource.prototype, 'addSourceBuffer', (orig) => function addSourceBuffer(mime) {
        const sb = orig.apply(this, arguments);
        try {
          const id = state.nextBuffer++;
          bufferIds.set(sb, id);
          state.buffers.push({id: id, mime: String(mime), source: sourceId(this)});
        } catch (e) {}
        return sb;
      });
    }

    if (typeof SourceBuffer !== 'undefined' && SourceBuffer.prototype) {
      wrap(SourceBuffer.prototype, 'appendBuffer', (orig) => function appendBuffer(data) {
        try {
          const id = bufferIds.get(this);
          if (id) {
            let u8 = null;
            if (data instanceof ArrayBuffer) u8 = new Uint8Array(data.slice(0));
            else if (ArrayBuffer.isView(data)) u8 = new Uint8Array(data.buffer.slice(data.byteOffset, data.byteOffset + data.byteLength));
            if (u8) {
              if (state.bytes + u8.byteLength > CAP) state.dropped++;
              else { state.segments.push({sb: id, seq: state.seq++, bytes: u8}); state.bytes += u8.byteLength; }
            }
          }
        } catch (e) {}
        return orig.apply(this, arguments);
      });
    }

    const b64 = (u8) => {
      let s = '';
      for (let i = 0; i < u8.length; i += 0x8000) s += String.fromCharCode.apply(null, u8.subarray(i, i + 0x8000));
      return btoa(s);
    };

    Object.defineProperty(window, '__bd_mse', {
      value: Object.freeze({
        drain() {
          const segs = state.segments; state.segments = []; state.bytes = 0;
          const out = {buffers: state.buffers.slice(),
                       segments: segs.map(s => ({sb: s.sb, seq: s.seq, b64: b64(s.bytes)})),
                       dropped: state.dropped};
          state.dropped = 0;
          return out;
        }
      }),
      writable: false, enumerable: false, configurable: false
    });
  } catch (e) {}
})();
""" % {"cap": MSE_PAGE_BUFFER_CAP}

_EMPTY: Dict[str, Any] = {"buffers": [], "segments": [], "dropped": 0}


def read_mse_records(page) -> Dict[str, Any]:
    """Drain the page-side buffer. Never raises; an unreachable page reads as
    nothing captured."""
    try:
        out = page.evaluate("() => (window.__bd_mse ? window.__bd_mse.drain() : null)")
    except Exception:
        return dict(_EMPTY, buffers=[], segments=[])
    if not isinstance(out, dict):
        return dict(_EMPTY, buffers=[], segments=[])
    return {"buffers": list(out.get("buffers") or []),
            "segments": list(out.get("segments") or []),
            "dropped": int(out.get("dropped") or 0)}


# ── ISO-BMFF box walker ──────────────────────────────────────────────────────

def iter_boxes(data: bytes) -> Iterator[Tuple[str, int, int]]:
    """Top-level boxes as (type, start, end). Stops at the first box that does
    not fit the buffer, so a truncated tail is never reported as a box."""
    pos, n = 0, len(data)
    while pos + 8 <= n:
        size = struct.unpack(">I", data[pos:pos + 4])[0]
        kind = data[pos + 4:pos + 8].decode("latin-1")
        hdr = 8
        if size == 1:
            if pos + 16 > n:
                return
            size = struct.unpack(">Q", data[pos + 8:pos + 16])[0]
            hdr = 16
        elif size == 0:
            size = n - pos
        if size < hdr or pos + size > n:
            return
        yield kind, pos, pos + size
        pos += size


def split_init_segment(data: bytes) -> Tuple[bytes, bytes]:
    """(init, media): everything up to and including ``moov``, then the rest."""
    for kind, _s, e in iter_boxes(data):
        if kind == "moov":
            return data[:e], data[e:]
    return b"", data


def is_valid_fmp4(data: bytes) -> bool:
    kinds = [k for k, _s, _e in iter_boxes(data)]
    return (bool(kinds) and kinds[0] == "ftyp" and "moov" in kinds
            and "moof" in kinds and "mdat" in kinds)


# ── segment store ───────────────────────────────────────────────────────────

_EXT_BY_CONTAINER = {"mp4": "mp4", "webm": "webm", "mp2t": "ts", "mpeg": "ts"}


class SegmentStore:
    """Segments per SourceBuffer, keyed by the page-assigned buffer id."""

    def __init__(self) -> None:
        self._buffers: Dict[int, Dict[str, Any]] = {}
        self._segments: Dict[int, Dict[int, bytes]] = {}
        self.dropped = 0

    def ingest(self, payload: Any) -> int:
        """Absorb one drain. Returns the number of NEW segments stored;
        replayed (same buffer, same seq) segments are ignored."""
        if not isinstance(payload, dict):
            return 0
        for b in payload.get("buffers") or []:
            try:
                bid = int(b["id"])
            except (KeyError, TypeError, ValueError):
                continue
            self._buffers.setdefault(bid, {"mime": str(b.get("mime") or ""),
                                           "source": b.get("source")})
            self._segments.setdefault(bid, {})
        added = 0
        for s in payload.get("segments") or []:
            try:
                bid, seq = int(s["sb"]), int(s["seq"])
                raw = base64.b64decode(s.get("b64") or "")
            except (KeyError, TypeError, ValueError):
                continue
            self._buffers.setdefault(bid, {"mime": "", "source": None})
            segs = self._segments.setdefault(bid, {})
            if seq in segs:
                continue
            segs[seq] = raw
            added += 1
        try:
            self.dropped += int(payload.get("dropped") or 0)
        except (TypeError, ValueError):
            pass
        return added

    def buffer_ids(self) -> List[int]:
        return sorted(self._buffers)

    def mime(self, buffer_id: int) -> str:
        return self._buffers.get(buffer_id, {}).get("mime", "")

    def segment_count(self, buffer_id: int) -> int:
        return len(self._segments.get(buffer_id, {}))

    def assemble(self, buffer_id: int) -> bytes:
        segs = self._segments.get(buffer_id) or {}
        return b"".join(segs[k] for k in sorted(segs))

    def container_ext(self, buffer_id: int) -> str:
        mime = self.mime(buffer_id).split(";")[0].strip().lower()
        sub = mime.split("/", 1)[1] if "/" in mime else ""
        return _EXT_BY_CONTAINER.get(sub, "bin")

    def write(self, buffer_id: int, dest_stem) -> Optional[Path]:
        """Write the assembled buffer to ``<dest_stem>.<ext>``; None if empty."""
        data = self.assemble(buffer_id)
        if not data:
            return None
        dest = Path(str(dest_stem) + "." + self.container_ext(buffer_id))
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)
        return dest

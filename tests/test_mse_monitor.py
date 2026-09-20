"""Row 929: MSE (Media Source Extensions) buffer monitor and assembler.

Pure half (no browser): the segment store, container assembly and the ISO-BMFF
box walker are exercised on a real fragmented MP4 produced by ffmpeg, and the
assembled output is validated by ffprobe. Live half: the document-start hook is
installed in real Chromium against a local MSE player fixture (webm/vp9, the
codec open Chromium builds decode); captured segments are assembled and
validated the same way. No site, no login (Fleet Rule 21).
"""
import base64
import json
import os
import subprocess
import tempfile
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
BD_GATE_SCOPE = "module"

os.environ.setdefault("BD_HOME", tempfile.mkdtemp(prefix="bd_mse_"))
os.environ.setdefault("BD_DISABLE_KEEPALIVE", "1")


def _load():
    import sys
    if str(REPO) not in sys.path:
        sys.path.insert(0, str(REPO))
    from bulk_downloader import mse_monitor as mod
    return mod


def _ffmpeg():
    from bulk_downloader import ffmpeg_bin
    exe = ffmpeg_bin.ffmpeg()
    if not exe:
        pytest.skip("no local ffmpeg binary")
    return exe


def _ffprobe_frames(path) -> int:
    from bulk_downloader import ffmpeg_bin
    exe = ffmpeg_bin.ffprobe()
    if not exe:
        pytest.skip("no local ffprobe binary")
    r = subprocess.run([exe, "-v", "error", "-count_frames", "-select_streams", "v:0",
                        "-show_entries", "stream=nb_read_frames", "-of", "json", str(path)],
                       capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    return int(json.loads(r.stdout)["streams"][0]["nb_read_frames"])


@pytest.fixture(scope="module")
def fmp4(tmp_path_factory):
    exe = _ffmpeg()
    p = tmp_path_factory.mktemp("fmp4") / "frag.mp4"
    subprocess.run([exe, "-y", "-v", "error", "-f", "lavfi", "-i",
                    "testsrc2=duration=2:size=160x120:rate=25", "-c:v", "libx264", "-g", "25",
                    "-pix_fmt", "yuv420p", "-movflags",
                    "frag_keyframe+empty_moov+default_base_moof", "-f", "mp4", str(p)],
                   check=True, capture_output=True)
    return p.read_bytes()


@pytest.fixture(scope="module")
def webm(tmp_path_factory):
    exe = _ffmpeg()
    p = tmp_path_factory.mktemp("webm") / "v.webm"
    subprocess.run([exe, "-y", "-v", "error", "-f", "lavfi", "-i",
                    "testsrc2=duration=2:size=160x120:rate=25", "-c:v", "libvpx-vp9",
                    "-b:v", "200k", "-g", "25", "-f", "webm", "-dash", "1", str(p)],
                   check=True, capture_output=True)
    return p.read_bytes()


# ── pure half: box walker ───────────────────────────────────────────────────

def test_iter_boxes_walks_a_fragmented_mp4(fmp4):
    m = _load()
    types = [t for t, _s, _e in m.iter_boxes(fmp4)]
    assert types[:2] == ["ftyp", "moov"]
    assert "moof" in types and "mdat" in types
    assert types.count("moof") == types.count("mdat") >= 2
    # the walk covers the file exactly, no gaps
    end = 0
    for _t, s, e in m.iter_boxes(fmp4):
        assert s == end
        end = e
    assert end == len(fmp4)


def test_iter_boxes_stops_cleanly_on_garbage():
    m = _load()
    assert list(m.iter_boxes(b"")) == []
    assert list(m.iter_boxes(b"\x00\x00\x00\x08free" + b"\xff" * 3)) == [("free", 0, 8)]
    # a box claiming to be larger than the buffer is not yielded
    assert list(m.iter_boxes(b"\x00\x00\x10\x00mdat" + b"\x00" * 16)) == []


def test_split_init_segment_isolates_ftyp_moov(fmp4):
    m = _load()
    init, media = m.split_init_segment(fmp4)
    assert [t for t, _s, _e in m.iter_boxes(init)] == ["ftyp", "moov"]
    assert init + media == fmp4
    assert [t for t, _s, _e in m.iter_boxes(media)][:2] == ["moof", "mdat"]


# ── pure half: store + assembly ────────────────────────────────────────────

def _drain_payload(segments, *, buffer_id=1, mime='video/mp4; codecs="avc1.42E01E"',
                   source=1, dropped=0, seq0=0):
    return {
        "buffers": [{"id": buffer_id, "mime": mime, "source": source}],
        "segments": [{"sb": buffer_id, "seq": seq0 + i,
                      "b64": base64.b64encode(s).decode("ascii")}
                     for i, s in enumerate(segments)],
        "dropped": dropped,
    }


def test_store_reassembles_the_exact_fmp4_from_appended_segments(fmp4, tmp_path):
    m = _load()
    init, media = m.split_init_segment(fmp4)
    # one append per moof+mdat pair; the trailing mfra (ffmpeg writes one at
    # EOF) is appended on its own, exactly as a player would push it
    frags, boxes, i = [], list(m.iter_boxes(media)), 0
    while i < len(boxes):
        if boxes[i][0] == "moof":
            assert boxes[i + 1][0] == "mdat"
            frags.append(media[boxes[i][1]:boxes[i + 1][2]])
            i += 2
        else:
            frags.append(media[boxes[i][1]:boxes[i][2]])
            i += 1
    assert len(frags) >= 3 and b"".join(frags) == media
    store = m.SegmentStore()
    # segments arrive across several drains, in append order
    store.ingest(_drain_payload([init, frags[0]]))
    store.ingest(_drain_payload(frags[1:], seq0=2))
    assert store.buffer_ids() == [1]
    assert store.segment_count(1) == 1 + len(frags)
    out = store.assemble(1)
    assert out == fmp4
    assert store.container_ext(1) == "mp4"
    dest = store.write(1, tmp_path / "out")
    assert dest == tmp_path / "out.mp4" and dest.read_bytes() == fmp4
    assert _ffprobe_frames(dest) == 50


def test_store_orders_by_seq_and_dedups_replayed_segments(fmp4):
    m = _load()
    init, media = m.split_init_segment(fmp4)
    store = m.SegmentStore()
    store.ingest(_drain_payload([media], seq0=1))
    store.ingest(_drain_payload([init], seq0=0))
    store.ingest(_drain_payload([init], seq0=0))       # replayed drain
    assert store.segment_count(1) == 2
    assert store.assemble(1) == fmp4


def test_store_is_valid_container_verdict(fmp4):
    m = _load()
    assert m.is_valid_fmp4(fmp4) is True
    init, media = m.split_init_segment(fmp4)
    assert m.is_valid_fmp4(media) is False            # no init: nothing to decode
    assert m.is_valid_fmp4(init) is False             # no media
    assert m.is_valid_fmp4(b"garbage") is False


def test_store_tracks_dropped_and_separate_buffers():
    m = _load()
    store = m.SegmentStore()
    store.ingest({"buffers": [{"id": 1, "mime": "video/webm; codecs=vp9", "source": 7},
                              {"id": 2, "mime": "audio/webm; codecs=opus", "source": 7}],
                  "segments": [{"sb": 1, "seq": 0, "b64": base64.b64encode(b"v0").decode()},
                               {"sb": 2, "seq": 0, "b64": base64.b64encode(b"a0").decode()}],
                  "dropped": 3})
    assert store.buffer_ids() == [1, 2]
    assert store.assemble(1) == b"v0" and store.assemble(2) == b"a0"
    assert store.container_ext(1) == "webm" and store.container_ext(2) == "webm"
    assert store.dropped == 3
    assert store.mime(1).startswith("video/webm")
    assert store.assemble(99) == b""


def test_store_ingest_rejects_non_dict_payload():
    m = _load()
    store = m.SegmentStore()
    assert store.ingest(None) == 0
    assert store.ingest("nope") == 0
    assert store.buffer_ids() == []


# ── live half: stub page ───────────────────────────────────────────────────

def test_read_mse_records_drains_via_page_evaluate():
    m = _load()

    class _Page:
        def __init__(self):
            self.calls = []

        def evaluate(self, expression):
            self.calls.append(expression)
            return {"buffers": [], "segments": [], "dropped": 0}

    pg = _Page()
    assert m.read_mse_records(pg) == {"buffers": [], "segments": [], "dropped": 0}
    assert len(pg.calls) == 1 and "__bd_mse" in pg.calls[0]


def test_read_mse_records_fails_open_on_bad_page():
    m = _load()

    class _Bad:
        def evaluate(self, expression):
            raise RuntimeError("page closed")

    assert m.read_mse_records(_Bad()) == {"buffers": [], "segments": [], "dropped": 0}


def test_hook_js_is_capture_only():
    m = _load()
    js = m.MSE_INIT_JS
    for hooked in ("createObjectURL", "addSourceBuffer", "appendBuffer"):
        assert hooked in js
    # observation: every hook calls straight through; nothing is suppressed
    assert "fetch(" not in js and "XMLHttpRequest" not in js and "WebSocket" not in js
    assert "removeSourceBuffer" not in js and "endOfStream" not in js
    assert "enumerable: false" in js


# ── live half: real Chromium + MSE player fixture ───────────────────────────

_PLAYER_HTML = """<!doctype html><html><body>
<video id="v" muted></video>
<script>
window.__fixture = {appended: 0, done: false, error: ""};
(async () => {
  try {
    const mime = 'video/webm; codecs="vp9"';
    if (!('MediaSource' in window) || !MediaSource.isTypeSupported(mime)) {
      window.__fixture.error = "unsupported"; window.__fixture.done = true; return;
    }
    const buf = await (await fetch('/v.webm')).arrayBuffer();
    const ms = new MediaSource();
    const v = document.getElementById('v');
    v.src = URL.createObjectURL(ms);
    await new Promise(r => ms.addEventListener('sourceopen', r, {once: true}));
    const sb = ms.addSourceBuffer(mime);
    const CH = 8192;
    for (let off = 0; off < buf.byteLength; off += CH) {
      const piece = new Uint8Array(buf, off, Math.min(CH, buf.byteLength - off));
      sb.appendBuffer(piece);
      await new Promise(r => sb.addEventListener('updateend', r, {once: true}));
      window.__fixture.appended++;
    }
    window.__fixture.done = true;
  } catch (e) { window.__fixture.error = String(e); window.__fixture.done = true; }
})();
</script></body></html>"""


@pytest.fixture(scope="module")
def browser_capture(webm, tmp_path_factory):
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright
    m = _load()
    out = {}
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        try:
            ctx = browser.new_context()
            ctx.add_init_script(m.MSE_INIT_JS)
            page = ctx.new_page()
            page.route("**/v.webm", lambda route: route.fulfill(
                status=200, content_type="video/webm", body=webm))
            page.route("**/player.html", lambda route: route.fulfill(
                status=200, content_type="text/html", body=_PLAYER_HTML))
            page.goto("http://mse.fixture.invalid/player.html")
            page.wait_for_function("window.__fixture.done", timeout=30000)
            out["fixture"] = page.evaluate("window.__fixture")
            out["leak"] = page.evaluate("""() => ({
                keys: Object.keys(window).filter(k => k.indexOf('bd') !== -1),
                forin: (() => { const o = []; for (const k in window) if (k.indexOf('bd') !== -1) o.push(k); return o; })(),
                present: '__bd_mse' in window,
                enumerable: !!Object.getOwnPropertyDescriptor(window, '__bd_mse').enumerable,
                appendToString: SourceBuffer.prototype.appendBuffer.toString(),
                appendName: SourceBuffer.prototype.appendBuffer.name,
                appendLength: SourceBuffer.prototype.appendBuffer.length,
                addToString: MediaSource.prototype.addSourceBuffer.toString(),
                urlToString: URL.createObjectURL.toString(),
                toStringToString: Function.prototype.toString.toString(),
                stateOnWindow: Object.getOwnPropertyNames(window).filter(k => /segment|mse|bd_/i.test(k)),
            })""")
            out["drain1"] = m.read_mse_records(page)
            out["drain2"] = m.read_mse_records(page)
        finally:
            browser.close()
    return out


def test_browser_fixture_played_through_mse(browser_capture):
    fx = browser_capture["fixture"]
    if fx["error"] == "unsupported":
        pytest.skip("this Chromium build has no vp9 MSE support")
    assert fx["error"] == "", fx
    assert fx["appended"] >= 2


def test_browser_segments_are_captured_and_assemble_to_the_served_bytes(browser_capture, webm, tmp_path):
    m = _load()
    fx = browser_capture["fixture"]
    if fx["error"] == "unsupported":
        pytest.skip("this Chromium build has no vp9 MSE support")
    d = browser_capture["drain1"]
    assert len(d["buffers"]) == 1 and d["buffers"][0]["mime"].startswith("video/webm")
    assert len(d["segments"]) == fx["appended"]
    assert d["dropped"] == 0
    store = m.SegmentStore()
    assert store.ingest(d) == fx["appended"]
    (bid,) = store.buffer_ids()
    assert store.assemble(bid) == webm
    dest = store.write(bid, tmp_path / "captured")
    assert dest.suffix == ".webm"
    assert _ffprobe_frames(dest) == 50


def test_browser_hook_leaves_no_sandbox_leak(browser_capture):
    fx = browser_capture["fixture"]
    if fx["error"] == "unsupported":
        pytest.skip("this Chromium build has no vp9 MSE support")
    leak = browser_capture["leak"]
    assert leak["present"] is True and leak["enumerable"] is False
    assert leak["keys"] == [] and leak["forin"] == []
    assert leak["stateOnWindow"] == ["__bd_mse"]
    assert "[native code]" in leak["appendToString"] and "appendBuffer" in leak["appendToString"]
    assert leak["appendName"] == "appendBuffer" and leak["appendLength"] == 1
    assert "[native code]" in leak["addToString"] and "[native code]" in leak["urlToString"]
    assert "[native code]" in leak["toStringToString"]
    # the page-side buffer is emptied by a drain: nothing accumulates
    assert browser_capture["drain2"]["segments"] == []
    assert browser_capture["drain2"]["buffers"] == browser_capture["drain1"]["buffers"]

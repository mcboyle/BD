"""O1789 bughunt deep-bench-misc (finder S56-C) -- fixes for findings F4, F10, F11, F12, F13.

F4   embeddings tokenized ``[a-z0-9]+`` only: Cyrillic / CJK / Greek text embedded to the zero vector, so an
     exact-name non-Latin query scored 0.0, and 'café' became 'caf'. Tokens are now Unicode letters/digits after
     NFKC + casefold; stored vectors carry the tokenizer version and an older index is rebuilt before scoring.
F10  viewport_clipping_filter accepted a 0x0 / negative / bool viewport (every node dropped) and coerced bool rect
     coordinates (all-False rect -> 0x0 -> dropped).
F11  viewport_telemetry_bridge acknowledged an unattributed resume (admin_id "" / None), accepted bool frame
     dimensions, a pixel buffer of the wrong size and an unknown format, and held an unbounded inbox.
F12  TelemetryChannel.receive_all lost messages sent between list() and clear(); FrameEncoder handed the same
     sequence number to concurrent encode() calls.
F13  embeddings_client.embed ("never raises") raised ZeroDivisionError for dims=0 and IndexError for dims<0.

Pure-function tests: no network, no app, no DB (the live capture pull is monkeypatched).
"""

from __future__ import annotations

import math
import re
import sys
import threading

import pytest

from bulk_downloader import embeddings as emb
from bulk_downloader import embeddings_client as emb_client
from bulk_downloader import semantic_search as ss
from bulk_downloader import vector_index as vi
from bulk_downloader import viewport_telemetry_bridge as vtb
from bulk_downloader.viewport_clipping_filter import ViewportBounds, filter_off_canvas, is_off_canvas

BD_GATE_SCOPE = "module"

# Finder fixture (probe_c.py S17c) plus the PM's Greek and accented cases.
CAPTURES = [
    {"rel_path": "a", "name": "Видео_про_кошек.mp4", "host": "example.ru", "kind": "video"},
    {"rel_path": "b", "name": "日本語の動画.mp4", "host": "example.jp", "kind": "video"},
    {"rel_path": "c", "name": "cats_compilation.mp4", "host": "example.com", "kind": "video"},
    {"rel_path": "d", "name": "καλημέρα κόσμε.mp4", "host": "example.gr", "kind": "video"},
    {"rel_path": "e", "name": "café crème.mp4", "host": "example.fr", "kind": "video"},
]


def _top(base, query):
    res = ss.search(query, k=5, base_dir=base)
    assert res["ok"], res
    return res["results"][0]


# ---------------------------------------------------------------------------
# F4 Unicode tokens
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("query,want", [
    ("Видео про кошек", "a"),
    ("日本語の動画", "b"),
    ("καλημέρα κόσμε", "d"),
    ("café crème", "e"),
])
def test_f4_non_latin_exact_name_query_scores_above_zero(tmp_path, query, want):
    assert ss.reindex(captures=CAPTURES, templates=[], base_dir=tmp_path)["ok"]
    top = _top(tmp_path, query)
    assert top["score"] > 0.0 and top["id"] == want, (
        f"F4: {query!r} must rank its exact-name capture {want!r} first with score > 0, got {top}")


def test_f4_control_cats_compilation_keeps_its_score(tmp_path):
    assert ss.reindex(captures=CAPTURES[:3], templates=[], base_dir=tmp_path)["ok"]
    top = _top(tmp_path, "cats compilation")
    assert top["id"] == "c" and round(top["score"], 3) == 0.703, top


def test_f4_cafe_is_one_token_precomposed_or_decomposed():
    pre, dec = "café", "café"
    assert emb._tokens(pre)[0] == "café"
    assert emb._tokens(dec) == emb._tokens(pre)
    assert emb.embed("CAFÉ") == emb.embed(pre)


def _old_tokens(text):
    out = []
    for w in re.findall(r"[a-z0-9]+", (text or "").lower()):
        out.append(w)
        if len(w) >= 3:
            p = f"^{w}$"
            out.extend("#" + p[i:i + 3] for i in range(len(p) - 2))
        else:
            out.append("#^" + w + "$")
    return out


@pytest.mark.parametrize("text", ["cats_compilation.mp4 example.com video", "JWPlayer HLS 1080p",
                                  "foo_bar-baz/qux 42", ""])
def test_f4_ascii_tokens_unchanged(text):
    assert emb._tokens(text) == _old_tokens(text)


def test_f4_old_format_index_is_rebuilt_before_scoring(tmp_path, monkeypatch):
    # An index written before the tokenizer stamp: vectors under the old tokenizer, plus a row the live
    # store no longer has. The first search must rebuild from the live pull, not score these.
    old_rows = [(f"capture:{c['rel_path']}", emb.embed(c["name"]),
                 {"kind": "capture", "id": c["rel_path"], "summary": c["name"]}) for c in CAPTURES]
    old_rows.append(("capture:gone", emb.embed("gone"), {"kind": "capture", "id": "gone", "summary": "gone"}))
    assert vi.upsert_many(old_rows, base_dir=tmp_path) == len(CAPTURES) + 1
    monkeypatch.setattr(ss, "_live_captures", lambda: CAPTURES)
    monkeypatch.setattr(ss, "_live_templates", lambda: [])

    top = _top(tmp_path, "Видео про кошек")
    assert top["id"] == "a" and top["score"] > 0.0, top
    ids = {h["id"] for h in vi.search(emb.embed("gone"), k=100, base_dir=tmp_path)}
    assert "capture:gone" not in ids, "F4: an old-format index must be rebuilt, not served"
    assert all(h["meta"].get("tokenizer_version") == emb.TOKENIZER_VERSION
               for h in vi.search(emb.embed("x"), k=100, base_dir=tmp_path))


def test_f4_old_format_index_never_scored_when_rebuild_fails(tmp_path, monkeypatch):
    assert vi.upsert_many([("capture:old", emb.embed("cats"), {"kind": "capture", "id": "old"})],
                          base_dir=tmp_path) == 1

    def boom():
        raise OSError("live store unavailable")

    monkeypatch.setattr(ss, "_live_captures", boom)
    res = ss.search("cats", k=5, base_dir=tmp_path)
    assert res["ok"] is False and res["results"] == [], (
        f"F4: never serve scores from an old-format index, got {res}")


def test_f4_current_index_is_not_rebuilt(tmp_path, monkeypatch):
    assert ss.reindex(captures=CAPTURES, templates=[], base_dir=tmp_path)["ok"]
    monkeypatch.setattr(ss, "reindex", lambda **kw: pytest.fail("current-format index rebuilt"))
    assert _top(tmp_path, "cats compilation")["id"] == "c"


# ---------------------------------------------------------------------------
# F13 embed never raises on a bad dims
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("dims", [0, -1, -256, True])
def test_f13_client_embed_never_raises_on_bad_dims(dims):
    vec = emb_client.embed("hello world", endpoint="", dims=dims)
    assert len(vec) == emb.DEFAULT_DIMS
    assert math.isclose(math.sqrt(sum(x * x for x in vec)), 1.0)


def test_f13_control_valid_dims_honoured():
    assert len(emb_client.embed("hello world", endpoint="", dims=64)) == 64


# ---------------------------------------------------------------------------
# F10 viewport bounds and bool rects
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("w,h", [(0, 0), (-1, -1), (True, True), (1280, 0), (0, 720)])
def test_f10_degenerate_viewport_refused(w, h):
    with pytest.raises(ValueError, match="Invalid viewport"):
        ViewportBounds(width=w, height=h)


def test_f10_bool_rect_is_unmeasurable_and_kept():
    vp = ViewportBounds(width=1280, height=720)
    node = {"rect": {"x": False, "y": False, "width": False, "height": False}}
    assert is_off_canvas(node, vp) is False
    assert filter_off_canvas([node], vp) == [node]


def test_f10_control_numeric_rects_still_judged():
    vp = ViewportBounds(width=1280, height=720)
    on = {"rect": {"x": 10, "y": 10, "width": 100, "height": 100}}
    off = {"rect": {"x": 2000, "y": 10, "width": 100, "height": 100}}
    zero = {"rect": {"x": 0, "y": 0, "width": 0, "height": 0}}
    assert filter_off_canvas([on, off, zero], vp) == [on]


# ---------------------------------------------------------------------------
# F11 bridge validation
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("admin_id", ["", "   ", None])
def test_f11_unattributed_resume_refused(admin_id):
    bridge = vtb.ViewportTelemetryBridge()
    bridge.pause_pipeline("challenge")
    ack = bridge.resume_pipeline(admin_id=admin_id)
    assert ack["acknowledged"] is False and bridge.is_paused is True, ack
    assert bridge.pause_reason == "challenge"


def test_f11_control_attributed_resume_acknowledged():
    bridge = vtb.ViewportTelemetryBridge()
    bridge.pause_pipeline("challenge")
    ack = bridge.resume_pipeline(admin_id="op1")
    assert ack == {"acknowledged": True, "admin_id": "op1", "was_paused": True}
    assert bridge.is_paused is False


@pytest.mark.parametrize("pixels,w,h,fmt", [
    (b"\x00\x00\x00", True, True, "rgb24"),
    (b"", 1920, 1080, "rgb24"),
    (b"\x00" * 7, 1920, 1080, "rgb24"),
    (b"\x00" * 12, 2, 2, "rgba32"),
    (b"\x00" * 12, 2, 2, "no-such-fmt"),
])
def test_f11_bad_frame_refused(pixels, w, h, fmt):
    with pytest.raises(ValueError):
        vtb.encode_frame(pixels, width=w, height=h, fmt=fmt)


def test_f11_control_well_formed_frames_encode():
    assert vtb.encode_frame(b"\x00" * 12, width=2, height=2, fmt="rgb24").width == 2
    assert vtb.encode_frame(b"\x00" * 16, width=2, height=2, fmt="rgba32").fmt == "rgba32"


def test_f11_undrained_inbox_is_bounded():
    ch = vtb.TelemetryChannel()
    for i in range(200000):
        ch.send({"i": i})
    got = ch.receive_all()
    assert 0 < len(got) <= 4096, f"F11: undrained inbox held {len(got)} messages"
    assert got[-1]["i"] == 199999, "the newest message must survive the bound"


# ---------------------------------------------------------------------------
# F12 races (finder probe_c.py S18a/S18b harness and counts)
# ---------------------------------------------------------------------------

@pytest.fixture
def fast_switch():
    old = sys.getswitchinterval()
    sys.setswitchinterval(1e-6)
    yield
    sys.setswitchinterval(old)


def test_f12_receive_all_loses_nothing_under_concurrent_send(fast_switch, monkeypatch):
    # The inbox bound is not under test here: lift it so only the race can lose messages.
    monkeypatch.setattr(vtb, "_INBOX_MAXLEN", None, raising=False)
    sent = received = 0
    for _trial in range(5):
        ch = vtb.TelemetryChannel()
        n, producers = 20000, 4
        got = []
        done = threading.Event()

        def prod():
            for i in range(n):
                ch.send({"i": i})

        def cons():
            while not done.is_set():
                got.extend(ch.receive_all())
            got.extend(ch.receive_all())

        ts = [threading.Thread(target=prod) for _ in range(producers)]
        c = threading.Thread(target=cons)
        c.start()
        for t in ts:
            t.start()
        for t in ts:
            t.join(60)
        done.set()
        c.join(60)
        sent += n * producers
        received += len(got)
    assert received == sent, f"F12: sent {sent} received {received} lost {sent - received}"


def test_f12_interleaved_encode_gets_its_own_sequence(monkeypatch):
    enc = vtb.FrameEncoder()
    real = vtb.EncodedFrame
    inner = []

    def racing_frame(**kw):
        # another encode() runs after this one took its sequence but before it returns
        if not inner:
            inner.append(None)  # the interleaved encode() below must not interleave again
            inner[0] = enc.encode(b"\x01\x02\x03", width=1, height=1).sequence
        return real(**kw)

    monkeypatch.setattr(vtb, "EncodedFrame", racing_frame)
    outer = enc.encode(b"\x04\x05\x06", width=1, height=1).sequence
    monkeypatch.setattr(vtb, "EncodedFrame", real)
    nxt = enc.encode(b"\x07\x08\x09", width=1, height=1).sequence
    assert len({outer, inner[0], nxt}) == 3, f"F12: duplicate sequence outer={outer} inner={inner[0]} next={nxt}"


def test_f12_concurrent_encode_sequences_unique(fast_switch):
    dups = total = 0
    for _trial in range(5):
        enc = vtb.FrameEncoder()
        seqs = []
        lock = threading.Lock()

        def w():
            local = [enc.encode(b"p" * 192, width=8, height=8).sequence for _ in range(5000)]
            with lock:
                seqs.extend(local)

        ts = [threading.Thread(target=w) for _ in range(4)]
        for t in ts:
            t.start()
        for t in ts:
            t.join(60)
        total += len(seqs)
        dups += len(seqs) - len(set(seqs))
    assert total == 5 * 4 * 5000 and dups == 0, f"F12: {dups} duplicate sequences in {total}"


def test_f12_control_single_thread_channel_and_sequence():
    ch = vtb.TelemetryChannel()
    for i in range(1000):
        ch.send({"i": i})
    assert [m["i"] for m in ch.receive_all()] == list(range(1000)) and ch.receive_all() == []
    enc = vtb.FrameEncoder()
    assert [enc.encode(b"xyz", width=1, height=1).sequence for _ in range(100)] == list(range(100))

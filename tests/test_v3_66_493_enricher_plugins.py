"""v3.66.493 K4 (plugin-v3 kind, final): enricher plugins.

Post-download.done sidecar metadata fetch + attach (TPDB / StashDB lookup ->
.nfo/JSON sidecar). Runs AFTER the processor stage. The plugin owns its network
fetch; the framework writes the returned sidecars next to the download with
traversal-safe paths and treats any failure as NON-FATAL (the download already
succeeded).

Contract:
  enrich(payload, ctx) -> {sidecar_files?, tags?, metadata?}
    sidecar_files: {relname: content} | [{name, content}]

K4 raises PLUGIN_API_MAX to 8 (the last of the K-series kinds).

Runner-safe: zero-arg fns, no pytest builtins, tempfile, globals restored.
"""
import os
import sys
import tempfile
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from bulk_downloader import plugins as P  # noqa: E402


def _payload_with_download(tmp, fname="video.mp4"):
    fp = Path(tmp) / fname
    fp.write_bytes(b"\x00\x00")
    return {"site_id": "s", "url": "u", "filename": fname, "path": str(fp)}


def test_api_max_raised_to_8_keeps_prior_compatible():
    assert P.PLUGIN_API_MAX >= 8
    for v in (2, 3, 4, 5, 6, 7, 8):
        ok, _ = P.api_compatible({"api_version": v})
        assert ok


def test_enricher_capability_documented():
    assert getattr(P, "CAP_ENRICHER", None) == "enricher"
    ke = P.known_events()
    assert P.CAP_ENRICHER in ke["capabilities"]
    assert ke["api_max"] >= 8


def test_register_list_status_reset():
    P.reset()
    try:
        P.register_enricher(lambda p, c: {}, name="e1", priority=10)
        assert "e1" in [e["name"] for e in P.list_enrichers()]
        assert "enrichers" in P.status()
        P.reset()
        assert P.list_enrichers() == []
    finally:
        P.reset()


# ── (a) sidecar written next to the download (mocked lookup) ──────────
def test_sidecar_written_next_to_download():
    P.reset()
    d = tempfile.mkdtemp()
    try:
        payload = _payload_with_download(d)

        def tpdb(p, c):
            return {"sidecar_files": {"video.nfo": "<movie><title>X</title></movie>"},
                    "tags": ["enriched"], "metadata": {"title": "X"}}
        P.register_enricher(tpdb, name="tpdb")
        res = P.run_enrichers(payload, {})
        row = [r for r in res if r["name"] == "tpdb"][0]
        assert row["ok"] is True
        nfo = Path(d) / "video.nfo"
        assert nfo.is_file()
        assert "title" in nfo.read_text("utf-8")
        assert str(nfo) in row["sidecars"]
        assert "enriched" in row["tags"]
    finally:
        P.reset()


def test_sidecar_files_list_form():
    P.reset()
    d = tempfile.mkdtemp()
    try:
        payload = _payload_with_download(d)
        P.register_enricher(
            lambda p, c: {"sidecar_files": [{"name": "video.json", "content": "{}"}]},
            name="json")
        P.run_enrichers(payload, {})
        assert (Path(d) / "video.json").is_file()
    finally:
        P.reset()


# ── (b) lookup failure is non-fatal ──────────────────────────────────
def test_lookup_failure_is_non_fatal():
    P.reset()
    d = tempfile.mkdtemp()
    try:
        payload = _payload_with_download(d)

        def fails(p, c):
            raise RuntimeError("network down")
        P.register_enricher(fails, name="fails")
        # must NOT raise; the download (path) is untouched
        res = P.run_enrichers(payload, {})
        row = [r for r in res if r["name"] == "fails"][0]
        assert row["ok"] is False
        assert (Path(d) / "video.mp4").is_file()   # download intact
    finally:
        P.reset()


def test_empty_result_non_fatal():
    P.reset()
    d = tempfile.mkdtemp()
    try:
        payload = _payload_with_download(d)
        P.register_enricher(lambda p, c: {}, name="empty")
        res = P.run_enrichers(payload, {})  # no sidecars, no raise
        assert [r for r in res if r["name"] == "empty"][0]["sidecars"] == []
    finally:
        P.reset()


# ── (c) sidecar path is traversal-safe ────────────────────────────────
def test_sidecar_path_traversal_rejected():
    P.reset()
    d = tempfile.mkdtemp()
    try:
        payload = _payload_with_download(d)
        P.register_enricher(
            lambda p, c: {"sidecar_files": {"../../evil.nfo": "pwned"}},
            name="evil")
        res = P.run_enrichers(payload, {})
        row = [r for r in res if r["name"] == "evil"][0]
        # nothing escaped the download dir
        assert row["sidecars"] == []
        assert not (Path(d).parent.parent / "evil.nfo").exists()
    finally:
        P.reset()


def test_absolute_sidecar_rejected():
    P.reset()
    d = tempfile.mkdtemp()
    other = tempfile.mkdtemp()
    try:
        payload = _payload_with_download(d)
        target = os.path.join(other, "abs.nfo")
        P.register_enricher(
            lambda p, c: {"sidecar_files": {target: "x"}}, name="abs")
        P.run_enrichers(payload, {})
        assert not Path(target).exists()
    finally:
        P.reset()


# ── (d) exception isolation ───────────────────────────────────────────
def test_throwing_enricher_isolated_others_run():
    P.reset()
    d = tempfile.mkdtemp()
    try:
        payload = _payload_with_download(d)

        def boom(p, c):
            raise RuntimeError("nope")
        P.register_enricher(boom, name="boom", priority=10)
        P.register_enricher(
            lambda p, c: {"sidecar_files": {"video.nfo": "ok"}},
            name="good", priority=20)
        res = P.run_enrichers(payload, {})  # must not raise
        assert (Path(d) / "video.nfo").is_file()
        assert [r for r in res if r["name"] == "boom"][0]["ok"] is False
    finally:
        P.reset()


# ── priority order + decorator parity ─────────────────────────────────
def test_priority_order():
    P.reset()
    d = tempfile.mkdtemp()
    try:
        order = []
        P.register_enricher(lambda p, c: order.append("hi") or {}, name="hi", priority=20)
        P.register_enricher(lambda p, c: order.append("lo") or {}, name="lo", priority=10)
        P.run_enrichers(_payload_with_download(d), {})
        assert order == ["lo", "hi"]
    finally:
        P.reset()


def test_enricher_decorator():
    P.reset()
    d = tempfile.mkdtemp()
    try:
        @P.enricher(priority=5, name="deco")
        def _e(payload, ctx):
            return {"sidecar_files": {"video.nfo": "d"}}
        P.run_enrichers(_payload_with_download(d), {})
        assert (Path(d) / "video.nfo").is_file()
    finally:
        P.reset()

"""o1673-t154-edge1: detect.py must not import the runner to ask a URL question.

T154 measured five new cross-subsystem edges; four run runner -> core, the
expected direction. Edge #1 ran the other way: detect.py (recognizer) imported
runner_transport (runner) for two PURE predicates of (href, page_url) --
TransportMixin._stream_route / _direct_media_route. Those predicates now live in
the core module bulk_downloader/media_route.py; TransportMixin keeps thin
staticmethod delegates so the transport and its tests are unchanged.

Asserted three ways:
  1. detect.py's AST imports no runner_transport (and does import media_route);
  2. media_route answers exactly what TransportMixin answers over an href table;
  3. bd-coupling-meter's own edge derivation has no detect -> runner_transport
     edge, with runner_transport -> detect (the expected direction) as the
     positive control that the derivation sees this pair at all.
"""
import ast
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from bulk_downloader.runner_transport import TransportMixin  # noqa: E402

BD_GATE_SCOPE = "module"

PAGE = "https://example.invalid/video/watch/42"
METER = ROOT / "toolchain" / "bin" / "bd-coupling-meter"

HREFS = [
    "/hls/scene/2.m3u8",                                  # relative HLS
    "https://cdn.example.invalid/v/master.m3u8?token=a",  # absolute HLS
    "/dash/scene/2.mpd",                                  # DASH
    "https://cdn.example.invalid/a/b_3840.mp4?dl=Scene%20One.mp4",  # direct, dl= name
    "/get_file/1/abc/42/42_720p.mp4/?download=true",      # KVS trailing slash
    "/direct/media/2.mkv",                                # relative direct
    "/video/watch/43",                                    # page
    "/download?id=7",                                     # no extension
    "#",
    "javascript:void(0)",
    "",
    None,
]


def _imports(path):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    mods = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.ImportFrom):
            mods.add(("." * n.level) + (n.module or ""))
            if n.level and not n.module:
                mods.update("." + a.name for a in n.names)
        elif isinstance(n, ast.Import):
            mods.update(a.name for a in n.names)
    return mods


def test_detect_does_not_import_the_runner_transport():
    mods = _imports(ROOT / "bulk_downloader" / "detect.py")
    rt = {m for m in mods if m.split(".")[-1] == "runner_transport"}
    assert not rt, (
        f"detect.py imports {sorted(rt)} -- a recognizer -> runner edge for two pure "
        f"URL predicates; they belong to bulk_downloader/media_route.py")
    assert ".media_route" in mods, (
        "detect.py no longer imports .media_route -- _fetched_without_click must "
        "still ask the transport's routing rules, not keep a second copy")


@pytest.mark.parametrize("href", HREFS)
def test_media_route_answers_what_the_transport_answers(href):
    from bulk_downloader import media_route
    assert media_route.stream_route(href, PAGE) == TransportMixin._stream_route(href, PAGE)
    assert (media_route.direct_media_route(href, PAGE)
            == TransportMixin._direct_media_route(href, PAGE))


def test_the_table_exercises_every_verdict():
    """Positive control for the equality table: it must contain a stream, a
    direct file and a refusal, or equal-on-(None, None) proves nothing."""
    from bulk_downloader import media_route
    streams = [h for h in HREFS if media_route.stream_route(h, PAGE)[0]]
    direct = [h for h in HREFS if media_route.direct_media_route(h, PAGE)[0]]
    neither = [h for h in HREFS if h not in streams and h not in direct]
    assert len(streams) >= 3 and len(direct) >= 3 and len(neither) >= 4, (streams, direct)
    assert media_route.direct_media_route(HREFS[3], PAGE)[1] == "Scene One.mp4"
    assert media_route.stream_route(HREFS[0], PAGE) == (
        "https://example.invalid/hls/scene/2.m3u8", "2.mp4")


def test_the_transport_delegates_are_staticmethods():
    for name in ("_stream_route", "_direct_media_route"):
        assert isinstance(TransportMixin.__dict__.get(name), staticmethod), name


def _meter():
    """The meter's own edge derivation, not a re-implementation of it. The script
    ends in an unguarded `sys.exit(main())`, so exec its body without that line."""
    src = METER.read_text(encoding="utf-8")
    tail = "sys.exit(main())"
    assert src.rstrip().endswith(tail), "bd-coupling-meter changed shape; re-derive this loader"
    ns = {"__name__": "bd_coupling_meter_t154", "__file__": str(METER)}
    sys.path.insert(0, str(METER.parent))
    try:
        exec(compile(src.rstrip()[: -len(tail)], str(METER), "exec"), ns)
    finally:
        sys.path.remove(str(METER.parent))
    return ns


def test_the_coupling_meter_sees_no_detect_to_runner_transport_edge():
    meter = _meter()
    edges = set(meter["edges_from_ast"](str(ROOT)))
    assert ("runner_transport", "detect") in edges, (
        "positive control: the meter no longer derives runner_transport -> detect, "
        "so its silence on detect -> runner_transport would prove nothing")
    assert ("detect", "runner_transport") not in edges, (
        "bd-coupling-meter still derives detect -> runner_transport (T154 edge #1)")
    assert ("detect", "media_route") in edges
    assert meter["subsys_of"]("media_route") == "core"


def test_the_coupling_meter_json_runs_on_this_tree():
    out = subprocess.run([sys.executable, str(METER), "--json", "--work", str(ROOT)],
                         capture_output=True, text=True, timeout=120)
    assert out.returncode == 0, out.stderr[-500:]
    assert json.loads(out.stdout)["source"] == "ast_rederive"

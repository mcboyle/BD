"""Row 439: a segmented (HLS) transfer must honour the same fail-closed egress
contract every sibling download arm already honours.

THE DEFECT, measured 2026-08-31 at v3.66.1362 and re-measured on this tree.
Every sibling outbound arm resolves ``_download_proxy_url()`` BEFORE it builds a
client or spawns a subprocess -- the yt-dlp fallback
(runner_extractors.py:319), the gallery-dl fallback (runner_extractors.py:420),
the direct-HTTP download (runner_transport.py:304) and the media probe
(runner_transport.py:864) -- so a ``vpn_required`` site whose tunnel is down
raises ``VPNRequiredError`` and NOTHING egresses.

The six segmented arms did not.  ``_hls.download(...)`` was called directly by
``_try_jsonapi_extractor``, ``_try_vixen_extractor``, ``_try_aylo_extractor``,
``_try_plugin_extractor``, ``_try_library_extractor`` and ``_do_download``; the
enclosing functions contained ZERO references to ``_download_proxy_url``,
``VPNRequiredError``, ``proxy`` or ``vpn``.  ``hls_downloader.download()`` had
no ``proxy_url`` parameter, built no proxy argument, and its ``Popen`` passed no
``env=``, so ffmpeg inherited the whole ambient environment.  Three wrong
outcomes followed: a required-but-down tunnel still had its media fetched on the
clear interface, BD's configured per-site proxy never reached a segmented
transfer at all, and any ambient ``http_proxy`` in the service environment
silently rerouted ffmpeg's segment fetches.

WHY THE ASSERTIONS ARE AT THE PROCESS BOUNDARY.  Reading the source cannot tell
you what ffmpeg actually received.  Every test here pins ``ffmpeg`` to a stub
that records its own ``sys.argv`` and ``os.environ`` to disk, so the spawn count
and the argv/env carriage are MEASURED, not inferred.  A refusal is proved by a
spawn count of exactly 0 -- an absent process, not a mocked return value.

RED on the defective parent: ``test_vpn_required_tunnel_down_refuses`` records
exactly 1 spawn whose environment still carries the ambient proxy.

FAIL-CLOSED, INCLUDING THE UNHONOURABLE PROXY.  BD's tunnels expose a local
SOCKS5 CONNECT proxy (``vpn_socks``); ffmpeg's HTTP protocol has no SOCKS
support.  Per CLAUDE.md A7 an egress claim that cannot be established is
UNKNOWN, never OK, so a proxy whose scheme ffmpeg cannot honour REFUSES the
transfer rather than proceeding on the clear interface.
"""
from __future__ import annotations

import ast
import json
import os
import sys
import threading
from pathlib import Path

import pytest

BD_GATE_SCOPE = "module"

_REPO = Path(__file__).resolve().parents[1]
_URL = "https://example.invalid/row439/scene"
_MANIFEST = "https://cdn.example.invalid/row439/master.m3u8"
_AMBIENT_PROXY = "http://ambient-leak.invalid:9999"
_PROXY_ENV_NAMES = (
    "http_proxy", "https_proxy", "all_proxy", "no_proxy", "ftp_proxy",
    "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY", "FTP_PROXY",
)

# The complete population of segmented-transfer arms this gate is about.
_ARMS = (
    ("bulk_downloader/runner_extractors.py", "_try_jsonapi_extractor"),
    ("bulk_downloader/runner_extractors.py", "_try_vixen_extractor"),
    ("bulk_downloader/runner_extractors.py", "_try_aylo_extractor"),
    ("bulk_downloader/runner_extractors.py", "_try_plugin_extractor"),
    ("bulk_downloader/runner_extractors.py", "_try_library_extractor"),
    ("bulk_downloader/runner_transport.py", "_do_download"),
)


# ─── The process boundary ───────────────────────────────────────────

class _FfmpegRecorder:
    """A stub ffmpeg pinned in front of the real one.

    It records the argv and environment of EVERY spawn to its own directory,
    which is how spawn counts and argv/env carriage become measurements rather
    than claims.  It writes 1 KiB to the argv's final element so a successful
    transfer is distinguishable from ``empty_output``.
    """

    def __init__(self, tmp_path: Path):
        self.bin_dir = tmp_path / "ffmpeg-pin"
        self.rec_dir = tmp_path / "ffmpeg-spawns"
        self.bin_dir.mkdir(parents=True, exist_ok=True)
        self.rec_dir.mkdir(parents=True, exist_ok=True)
        stub = self.bin_dir / "ffmpeg"
        stub.write_text(
            "#!" + sys.executable + "\n"
            "import json, os, sys, uuid\n"
            "rec = " + repr(str(self.rec_dir)) + "\n"
            "with open(os.path.join(rec, uuid.uuid4().hex + '.json'), 'w') as fh:\n"
            "    json.dump({'argv': sys.argv, 'env': dict(os.environ)}, fh)\n"
            "out = sys.argv[-1]\n"
            "try:\n"
            "    open(out, 'wb').write(b'R' * 1024)\n"
            "except Exception:\n"
            "    pass\n"
        )
        stub.chmod(0o755)

    @property
    def spawns(self) -> list[dict]:
        out = []
        for p in sorted(self.rec_dir.iterdir()):
            out.append(json.loads(p.read_text()))
        return out

    @property
    def spawn_count(self) -> int:
        return len(list(self.rec_dir.iterdir()))


@pytest.fixture()
def ffmpeg_boundary(tmp_path, monkeypatch):
    """Pin ffmpeg to the recording stub and prove the pin actually resolved."""
    from bulk_downloader import ffmpeg_bin, hls_downloader

    rec = _FfmpegRecorder(tmp_path)
    monkeypatch.setattr(ffmpeg_bin, "_pinned_dir", lambda: str(rec.bin_dir))
    hls_downloader._reset_ffmpeg_cache_for_tests()

    # PRECONDITION: the stub, not the host ffmpeg, is what BD will run.
    resolved = hls_downloader._find_ffmpeg()
    assert resolved == str(rec.bin_dir / "ffmpeg"), (
        f"the ffmpeg pin did not take effect (resolved {resolved!r}); "
        "every spawn assertion below would be measuring the host binary"
    )
    assert rec.spawn_count == 0, "the recorder must start empty"

    # An ambient proxy in the service environment: the third wrong outcome.
    monkeypatch.setenv("http_proxy", _AMBIENT_PROXY)
    monkeypatch.setenv("HTTPS_PROXY", _AMBIENT_PROXY)

    try:
        yield rec
    finally:
        hls_downloader._reset_ffmpeg_cache_for_tests()


# ─── The arm under test ─────────────────────────────────────────────

def _plugin_runner(site_id, config, monkeypatch):
    """A real SiteRunner (so the real mixin methods run) with only the
    reporting/persistence seams replaced."""
    from bulk_downloader import runner as runner_mod
    from bulk_downloader import runner_extractors

    r = runner_mod.SiteRunner.__new__(runner_mod.SiteRunner)
    r.site_id = site_id
    r.config = config
    r._stop = threading.Event()
    r.jobs = {}
    r._update_job = lambda *a, **k: None
    r.log_event = lambda *a, **k: None

    rows: list[tuple] = []
    monkeypatch.setattr(runner_extractors, "db_log",
                        lambda *a, **k: rows.append((a, k)))
    monkeypatch.setattr(runner_extractors, "history_title_kwargs",
                        lambda *a, **k: {})
    r._row439_history = rows
    return r


def _register_hls_plugin(site_id):
    from bulk_downloader import plugins as P
    P.register_extractor(site_id, lambda u, ctx: {
        "video_url": _MANIFEST, "is_hls": True, "title": "row439"})
    return lambda: P.unregister_extractor(site_id)


def _drive_segmented_arm(runner, url=_URL):
    from bulk_downloader.runner_extractors import ExtractorsMixin
    return ExtractorsMixin._try_plugin_extractor(runner, url)


def _configure_tunnel(monkeypatch, site_id, *, required, tunnel_state):
    """Point the REAL vpn_runtime resolver at a synthetic tunnel.

    Only module-global dicts and one backend lookup are replaced -- nothing is
    written to any VPN config file (see
    tests/test_no_test_writes_the_real_vpn_config.py).
    """
    from bulk_downloader import vpn, vpn_kill_switch, vpn_runtime

    monkeypatch.delenv("BD_DISABLE_VPN_RUNTIME", raising=False)
    tid = "row439-tunnel"
    monkeypatch.setitem(vpn_runtime._site_to_tunnel, site_id, tid)
    monkeypatch.setitem(vpn_runtime._site_required, site_id, required)
    monkeypatch.setattr(vpn_runtime, "_global_tunnel_id", None)

    if tunnel_state == "missing":
        monkeypatch.setattr(vpn, "get_tunnel", lambda t: None)
    elif tunnel_state == "up":
        class _T:
            state = "up"
            socks_port = 41439
            last_error = ""
        monkeypatch.setattr(vpn, "get_tunnel", lambda t: _T())
        monkeypatch.setattr(vpn, "get_socks_url",
                            lambda t: "socks5://127.0.0.1:41439")
        monkeypatch.setattr(vpn_kill_switch, "is_killed", lambda t: False)
    else:  # pragma: no cover - guard against a typo in a caller
        raise AssertionError(f"unknown tunnel_state {tunnel_state!r}")
    return tid


def _proxy_args(argv: list[str]) -> list[str]:
    return [a for a in argv if a == "-http_proxy"]


def _env_proxy_values(env: dict) -> dict:
    return {k: v for k, v in env.items() if k in _PROXY_ENV_NAMES}


# ─── 1. RED / GREEN: required tunnel down must refuse ────────────────

def test_vpn_required_tunnel_down_refuses_the_segmented_transfer(
        ffmpeg_boundary, tmp_path, monkeypatch, capsys):
    """A vpn_required site with no usable tunnel must not spawn ffmpeg.

    RED on the defective parent: spawn_count == 1 and the recorded environment
    still carries the ambient proxy.
    """
    site = "row439-required-down"
    unregister = _register_hls_plugin(site)
    try:
        from bulk_downloader import runner_transport, vpn_runtime

        _configure_tunnel(monkeypatch, site, required=True,
                          tunnel_state="missing")
        runner = _plugin_runner(
            site, {"download_dir": str(tmp_path / "dl")}, monkeypatch)

        # PRECONDITIONS, asserted before any verdict.
        assert runner_transport._VPN_RUNTIME_AVAILABLE is True, (
            "the VPN runtime must be importable or this fixture proves nothing")
        assert vpn_runtime.is_vpn_required_for_site(site) is True, (
            "fixture did not build a vpn_required site")
        with pytest.raises(vpn_runtime.VPNRequiredError):
            runner._download_proxy_url()
        assert ffmpeg_boundary.spawn_count == 0

        ok = _drive_segmented_arm(runner)

        assert ffmpeg_boundary.spawn_count == 0, (
            "a vpn_required site with a down tunnel spawned ffmpeg -- the "
            "segmented transfer egressed outside the tunnel: "
            f"{ffmpeg_boundary.spawns}")
        assert ok is False, "a refused transfer must not report success"
        assert runner._row439_history == [], (
            "a refused transfer must not write a history row")
        err = capsys.readouterr().err
        assert "VPN required" in err and site in err, (
            f"the refusal must carry the distinctive VPN diagnostic; got {err!r}")
    finally:
        unregister()


def test_a_down_tunnel_refusal_is_not_an_ffmpeg_absence(
        ffmpeg_boundary, tmp_path, monkeypatch):
    """Negative control on the REASON: the refusal above must come from the VPN
    gate, not from a missing binary or an unregistered plugin."""
    from bulk_downloader import hls_downloader

    assert hls_downloader.is_available() is True, (
        "ffmpeg resolves in this fixture, so 'ffmpeg not on PATH' cannot be "
        "what makes the refusal test pass")
    site = "row439-reason-control"
    unregister = _register_hls_plugin(site)
    try:
        from bulk_downloader import plugins as P
        assert P.get_extractor(site) is not None, (
            "the arm must actually reach its segmented branch")
    finally:
        unregister()


# ─── 2. The configured proxy must reach the invocation ───────────────

def test_configured_http_proxy_reaches_the_ffmpeg_invocation(
        ffmpeg_boundary, tmp_path, monkeypatch):
    """An explicit per-site proxy ffmpeg CAN honour is carried in argv and env."""
    site = "row439-explicit-proxy"
    proxy = "http://127.0.0.1:38731"
    unregister = _register_hls_plugin(site)
    try:
        runner = _plugin_runner(
            site, {"download_dir": str(tmp_path / "dl"), "proxy": proxy},
            monkeypatch)
        assert runner._download_proxy_url() == proxy, (
            "precondition: the explicit proxy must be what the gate resolves")

        ok = _drive_segmented_arm(runner)

        assert ffmpeg_boundary.spawn_count == 1, (
            f"expected exactly one ffmpeg spawn, got "
            f"{ffmpeg_boundary.spawn_count}")
        spawn = ffmpeg_boundary.spawns[0]
        argv = spawn["argv"]
        assert _proxy_args(argv) == ["-http_proxy"], (
            f"expected exactly one -http_proxy flag in {argv}")
        idx = argv.index("-http_proxy")
        assert argv[idx + 1] == proxy, (
            f"-http_proxy carried {argv[idx + 1]!r}, expected {proxy!r}")
        assert idx < argv.index("-i"), (
            "-http_proxy is an INPUT option and must precede -i")
        env_proxies = _env_proxy_values(spawn["env"])
        assert env_proxies == {"http_proxy": proxy, "https_proxy": proxy}, (
            "the child environment must carry the resolved proxy and nothing "
            f"else; got {env_proxies}")
        assert _AMBIENT_PROXY not in json.dumps(env_proxies), (
            "the ambient proxy survived into the ffmpeg environment")
        assert ok is True
    finally:
        unregister()


# ─── 3. Negative control: an unconfigured host still downloads ───────

def test_unconfigured_host_downloads_with_zero_proxy_arguments(
        ffmpeg_boundary, tmp_path, monkeypatch):
    """No VPN, no proxy: the arm must work exactly as before -- and the ambient
    proxy must NOT silently reroute the segment fetches."""
    from bulk_downloader import vpn_runtime

    site = "row439-unconfigured"
    unregister = _register_hls_plugin(site)
    try:
        monkeypatch.setattr(vpn_runtime, "_site_to_tunnel", {})
        monkeypatch.setattr(vpn_runtime, "_global_tunnel_id", None)
        assert vpn_runtime.get_tunnel_for_site(site) is None, (
            "negative-control fixture accidentally configured a tunnel")

        runner = _plugin_runner(
            site, {"download_dir": str(tmp_path / "dl")}, monkeypatch)
        assert runner._download_proxy_url() is None, (
            "precondition: an unconfigured site resolves no proxy")

        ok = _drive_segmented_arm(runner)

        assert ok is True, "an unconfigured host must still download"
        assert ffmpeg_boundary.spawn_count == 1
        spawn = ffmpeg_boundary.spawns[0]
        assert _proxy_args(spawn["argv"]) == [], (
            f"an unproxied transfer must carry zero proxy arguments: "
            f"{spawn['argv']}")
        assert _env_proxy_values(spawn["env"]) == {}, (
            "the ambient proxy must be scrubbed from the ffmpeg environment; "
            f"got {_env_proxy_values(spawn['env'])}")
        assert spawn["argv"][-1].endswith(".mp4")
        assert len(runner._row439_history) == 1, (
            "the successful transfer must still log its history row")
    finally:
        unregister()


# ─── 4. Negative control: a proxy ffmpeg cannot honour refuses ───────

def test_socks_tunnel_ffmpeg_cannot_honour_refuses_rather_than_egressing(
        ffmpeg_boundary, tmp_path, monkeypatch, capsys):
    """A tunnel that is UP still yields socks5://, which ffmpeg has no support
    for.  Unestablished egress safety is UNKNOWN, so the transfer refuses."""
    site = "row439-socks-up"
    unregister = _register_hls_plugin(site)
    try:
        _configure_tunnel(monkeypatch, site, required=True, tunnel_state="up")
        runner = _plugin_runner(
            site, {"download_dir": str(tmp_path / "dl")}, monkeypatch)
        assert runner._download_proxy_url() == "socks5://127.0.0.1:41439", (
            "precondition: an up tunnel must resolve to its SOCKS url")

        ok = _drive_segmented_arm(runner)

        assert ffmpeg_boundary.spawn_count == 0, (
            "ffmpeg cannot honour a socks5 proxy, so spawning it egresses "
            f"outside the tunnel: {ffmpeg_boundary.spawns}")
        assert ok is False
        err = capsys.readouterr().err
        assert "socks5" in err, (
            f"the refusal must name the scheme it cannot honour; got {err!r}")
    finally:
        unregister()


def test_hls_downloader_refuses_an_unhonourable_proxy_at_the_module_seam(
        ffmpeg_boundary, tmp_path):
    """The owner module refuses on its own, so the dev_suite / bd-dltest callers
    inherit the same contract."""
    from bulk_downloader import hls_downloader

    res = hls_downloader.download(
        _MANIFEST, str(tmp_path / "out.mp4"),
        proxy_url="socks5://127.0.0.1:41439")
    assert res.ok is False
    assert res.error == "proxy_unsupported", res
    assert ffmpeg_boundary.spawn_count == 0, (
        "the refusal must happen BEFORE the spawn")


# ─── 5. Every arm is on the guarded seam (AST, not text) ─────────────

def _call_census(rel_path: str):
    """Count guarded/unguarded segmented-download CALLS.

    AST, never grep: ``runner_extractors.py`` mentions ``_hls.download`` inside
    two docstrings, and a text scan would count that prose in its denominator.
    """
    tree = ast.parse((_REPO / rel_path).read_text())
    guarded, direct = [], []
    funcs = [n for n in ast.walk(tree)
             if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]

    def enclosing(lineno):
        cand = [f for f in funcs if f.lineno <= lineno <= f.end_lineno]
        cand.sort(key=lambda f: f.end_lineno - f.lineno)
        return cand[0].name if cand else "<module>"

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        fn = node.func
        if not isinstance(fn, ast.Attribute) or fn.attr not in (
                "download", "_hls_download"):
            continue
        if (fn.attr == "_hls_download" and isinstance(fn.value, ast.Name)
                and fn.value.id == "self"):
            guarded.append(enclosing(node.lineno))
        elif (fn.attr == "download" and isinstance(fn.value, ast.Name)
                and fn.value.id in ("_hls", "hls_downloader")):
            direct.append((enclosing(node.lineno), node.lineno))
    return guarded, direct


def test_every_segmented_arm_routes_through_the_guarded_seam():
    """All six arms, counted exactly -- a new ungated arm fails this."""
    assert len(_ARMS) == 6
    guarded_total, direct_total = [], []
    for rel_path in sorted({p for p, _ in _ARMS}):
        guarded, direct = _call_census(rel_path)
        guarded_total += [(rel_path, name) for name in guarded]
        direct_total += [(rel_path, name, line) for name, line in direct]

    assert direct_total == [], (
        "these segmented transfers still call hls_downloader.download() "
        f"directly, outside the fail-closed gate: {direct_total}")
    assert sorted(guarded_total) == sorted(_ARMS), (
        f"expected exactly the six known arms on the guarded seam; "
        f"got {sorted(guarded_total)}")


def test_the_census_would_notice_a_direct_call():
    """Negative control for the census itself: it must not be vacuously green."""
    import tempfile

    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "probe.py"
        p.write_text(
            "class M:\n"
            "    def arm(self):\n"
            '        """A docstring naming _hls.download( must not count."""\n'
            "        return _hls.download('u', 'o')\n")
        tree = ast.parse(p.read_text())
        calls = [n for n in ast.walk(tree)
                 if isinstance(n, ast.Call)
                 and isinstance(n.func, ast.Attribute)
                 and n.func.attr == "download"]
        assert len(calls) == 1, (
            "the AST census must see exactly the executable call and not the "
            "docstring mention")

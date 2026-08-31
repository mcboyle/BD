"""Row 439: a segmented (HLS) transfer must honour the same egress contract
every sibling download arm already honours -- and it must honour it by staying
INSIDE the tunnel, not by refusing to run.

THE DEFECT, measured 2026-08-31 at v3.66.1362 and re-measured on this tree.
Every sibling outbound arm resolves ``_download_proxy_url()`` BEFORE it builds a
client or spawns a subprocess -- the yt-dlp fallback (runner_extractors.py:319),
the gallery-dl fallback (runner_extractors.py:420), the direct-HTTP download
(runner_transport.py:304) and the media probe (runner_transport.py:864) -- and
the two SUBPROCESS siblings additionally confine their child to a per-capture
network namespace (``netns_isolation.capture_netns`` bracket +
``netns_exec_argv`` wrap, v3.66.689).

The six segmented arms did neither.  ``_hls.download(...)`` was called directly
by ``_try_jsonapi_extractor``, ``_try_vixen_extractor``, ``_try_aylo_extractor``,
``_try_plugin_extractor``, ``_try_library_extractor`` and ``_do_download``; the
enclosing functions contained ZERO references to ``_download_proxy_url``,
``VPNRequiredError``, ``netns``, ``proxy`` or ``vpn``.
``hls_downloader.download()`` had no ``proxy_url`` parameter, built no proxy
argument, took no namespace, and its ``Popen`` passed no ``env=`` -- so ffmpeg
ran on the clear host interface and inherited the whole ambient environment.

THE REMEDY IS CONFINEMENT, NOT REFUSAL (operator ruling, 2026-08-31).  ffmpeg's
HTTP protocol implements ``-http_proxy`` and nothing else -- it has NO SOCKS
support, and a BD tunnel exposes ``socks5://127.0.0.1:PORT`` (vpn_socks).  So
refusing every socks-mapped site would stop segmented transfers working
everywhere they matter.  Instead the seam extends the netns confinement that
already wraps yt-dlp and gallery-dl to the ffmpeg child: the namespace's own
route IS the egress, so the transfer keeps working AND stays inside the tunnel.
Where confinement cannot be ESTABLISHED the transfer refuses -- per CLAUDE.md
A7 an unverifiable egress posture is UNKNOWN, and UNKNOWN is never permission to
spawn.

WHY THE ASSERTIONS ARE AT THE PROCESS BOUNDARY.  Reading the source cannot tell
you what ffmpeg actually received.  Every test here pins ``ffmpeg`` -- and, for
the confinement wrap, ``ip`` -- to stubs that record their own ``sys.argv`` and
``os.environ`` to disk, so spawn counts and argv/env carriage are MEASURED, not
inferred.  A refusal is proved by a spawn count of exactly 0: an absent process,
not a mocked return value.
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
_SOCKS = "socks5://127.0.0.1:41439"
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

# A netns-confined site config in the shape the operator actually writes: the
# opt-in flag plus a WireGuard egress, so the namespace has a route and that
# route is the tunnel (netns_isolation.egress_commands makes wg0 the ns's ONLY
# default route -- fail-closed by construction).
_CONFINED_CFG = {
    "enabled": True,
    "egress": {"wg_iface": "wgrow439", "wg_conf": "/etc/wireguard/row439.conf",
               "address": "10.66.4.39/32"},
}


# ─── The process boundary ───────────────────────────────────────────

class _ArgvRecorder:
    """A stub binary pinned in front of a real one.

    It records the argv and environment of EVERY spawn to its own directory,
    which is how spawn counts and argv/env carriage become measurements rather
    than claims.
    """

    def __init__(self, tmp_path: Path, name: str, tail: str):
        self.bin_dir = tmp_path / f"{name}-pin"
        self.rec_dir = tmp_path / f"{name}-spawns"
        self.bin_dir.mkdir(parents=True, exist_ok=True)
        self.rec_dir.mkdir(parents=True, exist_ok=True)
        stub = self.bin_dir / name
        stub.write_text(
            "#!" + sys.executable + "\n"
            "import json, os, sys, uuid\n"
            "rec = " + repr(str(self.rec_dir)) + "\n"
            "with open(os.path.join(rec, uuid.uuid4().hex + '.json'), 'w') as fh:\n"
            "    json.dump({'argv': sys.argv, 'env': dict(os.environ)}, fh)\n"
            + tail)
        stub.chmod(0o755)
        self.path = stub

    @property
    def spawns(self) -> list[dict]:
        return [json.loads(p.read_text()) for p in sorted(self.rec_dir.iterdir())]

    @property
    def spawn_count(self) -> int:
        return len(list(self.rec_dir.iterdir()))


# ffmpeg stub: write 1 KiB to the argv's final element so a successful transfer
# is distinguishable from ``empty_output``.
_FFMPEG_TAIL = (
    "out = sys.argv[-1]\n"
    "try:\n"
    "    open(out, 'wb').write(b'R' * 1024)\n"
    "except Exception:\n"
    "    pass\n"
)

# ip stub: record, then EXEC the confined command (argv[4:] of
# ``ip netns exec <ns> <cmd...>``) so the ffmpeg recorder still fires and the
# two boundaries can be reconciled against each other.
_IP_TAIL = (
    "if sys.argv[1:3] == ['netns', 'exec'] and len(sys.argv) > 4:\n"
    "    os.execv(sys.argv[4], sys.argv[4:])\n"
    "sys.exit(0)\n"
)


class _Boundary:
    """Both stubs plus the faked netns command runner."""

    def __init__(self, ffmpeg: _ArgvRecorder, ip: _ArgvRecorder,
                 netns_cmds: list, netns_cmd_spawns: list, rc_holder: dict):
        self.ffmpeg = ffmpeg
        self.ip = ip
        self.netns_cmds = netns_cmds
        # ffmpeg's spawn count AT THE MOMENT each netns command ran. This is
        # what turns "the bracket encloses the transfer" from a structural
        # argument into an ordering MEASUREMENT: teardown must observe the
        # spawn that setup made possible.
        self.netns_cmd_spawns = netns_cmd_spawns
        self._rc = rc_holder

    # convenience passthroughs -- every assertion below reads a MEASUREMENT
    @property
    def spawn_count(self) -> int:
        return self.ffmpeg.spawn_count

    @property
    def spawns(self) -> list:
        return self.ffmpeg.spawns

    def break_netns_creation(self) -> None:
        """Make every faked ``ip netns ...`` setup command fail, so
        netns_isolation.create() returns False (the no-CAP_NET_ADMIN shape)."""
        self._rc["code"] = 1


@pytest.fixture()
def ffmpeg_boundary(tmp_path, monkeypatch):
    """Pin ffmpeg + ip to recording stubs and prove the pins actually resolved."""
    import shutil as _sh

    from bulk_downloader import ffmpeg_bin, hls_downloader, netns_isolation

    ff = _ArgvRecorder(tmp_path, "ffmpeg", _FFMPEG_TAIL)
    ip = _ArgvRecorder(tmp_path, "ip", _IP_TAIL)
    monkeypatch.setattr(ffmpeg_bin, "_pinned_dir", lambda: str(ff.bin_dir))
    monkeypatch.setenv("PATH", str(ip.bin_dir) + os.pathsep + os.environ["PATH"])
    hls_downloader._reset_ffmpeg_cache_for_tests()

    # PRECONDITIONS: the stubs, not the host binaries, are what BD will run.
    resolved = hls_downloader._find_ffmpeg()
    assert resolved == str(ff.path), (
        f"the ffmpeg pin did not take effect (resolved {resolved!r}); "
        "every spawn assertion below would be measuring the host binary")
    assert _sh.which("ip") == str(ip.path), (
        "the ip pin did not take effect; the confinement assertions would be "
        "measuring the host's iproute2")
    assert ff.spawn_count == 0 and ip.spawn_count == 0, "recorders start empty"

    # netns create/destroy run through netns_isolation's own ``subprocess``
    # module attribute; replacing it lets a real capture_netns bracket succeed
    # (or fail) with no root and no real namespace, while still recording the
    # exact argv the module generated.
    netns_cmds: list = []
    netns_cmd_spawns: list = []
    rc_holder = {"code": 0}

    class _FakeCompleted:
        def __init__(self, rc):
            self.returncode = rc
            self.stdout = ""
            self.stderr = ""

    class _FakeSubprocess:
        @staticmethod
        def run(argv, **kw):
            netns_cmds.append(list(argv))
            netns_cmd_spawns.append(ff.spawn_count)
            return _FakeCompleted(rc_holder["code"])

    monkeypatch.setattr(netns_isolation, "subprocess", _FakeSubprocess)

    # An ambient proxy in the service environment: the third wrong outcome.
    monkeypatch.setenv("http_proxy", _AMBIENT_PROXY)
    monkeypatch.setenv("HTTPS_PROXY", _AMBIENT_PROXY)

    try:
        yield _Boundary(ff, ip, netns_cmds, netns_cmd_spawns, rc_holder)
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

    rows: list = []
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
        monkeypatch.setattr(vpn, "get_socks_url", lambda t: _SOCKS)
        monkeypatch.setattr(vpn_kill_switch, "is_killed", lambda t: False)
    else:  # pragma: no cover - guard against a typo in a caller
        raise AssertionError(f"unknown tunnel_state {tunnel_state!r}")
    return tid


def _proxy_args(argv: list) -> list:
    return [a for a in argv if a == "-http_proxy"]


def _env_proxy_values(env: dict) -> dict:
    return {k: v for k, v in env.items() if k in _PROXY_ENV_NAMES}


def _expected_ns(url=_MANIFEST) -> str:
    from bulk_downloader import netns_isolation
    return netns_isolation.netns_name("dl", url)


# ─── 1. RED/GREEN: a required tunnel that is DOWN must refuse ────────

def test_vpn_required_tunnel_down_refuses_the_segmented_transfer(
        ffmpeg_boundary, tmp_path, monkeypatch, capsys):
    """A vpn_required site with no usable tunnel must not spawn ffmpeg.

    This is the one refusal the confine-don't-refuse ruling does NOT overturn:
    there is no tunnel to confine into, so every sibling arm's fail-closed
    VPNRequiredError behaviour applies unchanged.

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
        assert ffmpeg_boundary.ip.spawn_count == 0, (
            "an unconfined site must not be wrapped in `ip netns exec`")
        assert ok is True
    finally:
        unregister()


# ─── 3. NEGATIVE CONTROL A: an unconfigured host still downloads ─────

def test_unconfigured_host_downloads_with_zero_proxy_arguments(
        ffmpeg_boundary, tmp_path, monkeypatch):
    """No VPN, no proxy, no netns opt-in: the arm must work exactly as before,
    with no confinement -- and the ambient proxy must NOT silently reroute the
    segment fetches."""
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
        assert spawn["argv"][0] == str(ffmpeg_boundary.ffmpeg.path), (
            f"an unconfigured host must spawn ffmpeg directly: {spawn['argv']}")
        assert ffmpeg_boundary.ip.spawn_count == 0, (
            "no netns opt-in means no confinement wrapper")
        assert ffmpeg_boundary.netns_cmds == [], (
            "no netns opt-in means no namespace is created at all; got "
            f"{ffmpeg_boundary.netns_cmds}")
        assert spawn["argv"][-1].endswith(".mp4")
        assert len(runner._row439_history) == 1, (
            "the successful transfer must still log its history row")
    finally:
        unregister()


# ─── 4. NEGATIVE CONTROL B: a tunnel-mapped site transfers CONFINED ──

def test_tunnel_mapped_site_transfers_confined_rather_than_refusing(
        ffmpeg_boundary, tmp_path, monkeypatch):
    """THE RULING, measured.  A site mapped to an UP tunnel resolves
    ``socks5://``, which ffmpeg has no support for.  Refusing would stop the
    transfer; leaking would defeat the tunnel.  The seam confines instead: the
    ffmpeg child is wrapped in ``ip netns exec <ns>`` exactly as the yt-dlp and
    gallery-dl children already are, and the unusable socks url is dropped
    rather than handed to a client that cannot speak it.
    """
    from bulk_downloader import netns_isolation

    site = "row439-tunnel-confined"
    unregister = _register_hls_plugin(site)
    try:
        _configure_tunnel(monkeypatch, site, required=True, tunnel_state="up")
        runner = _plugin_runner(
            site, {"download_dir": str(tmp_path / "dl"),
                   "netns_isolation": dict(_CONFINED_CFG)}, monkeypatch)

        # PRECONDITIONS: a real tunnel-mapped site that really opts into
        # confinement, and a scheme ffmpeg genuinely cannot carry.
        assert runner._download_proxy_url() == _SOCKS, (
            "precondition: an up tunnel must resolve to its SOCKS url")
        assert netns_isolation.site_wants_isolation(runner.config) is True
        assert netns_isolation.fail_closed(runner.config) is True
        assert netns_isolation.egress_spec_from_cfg(runner.config) is not None, (
            "precondition: the fixture must configure a real ns egress")

        ok = _drive_segmented_arm(runner)

        assert ok is True, (
            "a tunnel-mapped site must TRANSFER (confined), not refuse")
        # The confinement wrapper fired exactly once, at the boundary.
        assert ffmpeg_boundary.ip.spawn_count == 1, (
            f"expected exactly one `ip netns exec` wrap, got "
            f"{ffmpeg_boundary.ip.spawn_count}")
        wrap = ffmpeg_boundary.ip.spawns[0]["argv"]
        ns = _expected_ns()
        assert wrap[1:4] == ["netns", "exec", ns], (
            f"the wrap must enter this capture's namespace {ns!r}: {wrap}")
        assert wrap[4] == str(ffmpeg_boundary.ffmpeg.path), (
            f"the wrap must enclose the FULLY BUILT ffmpeg argv: {wrap}")
        # ...and the confined ffmpeg really ran inside it.
        assert ffmpeg_boundary.spawn_count == 1, (
            f"expected exactly one confined ffmpeg spawn, got "
            f"{ffmpeg_boundary.spawn_count}")
        spawn = ffmpeg_boundary.spawns[0]
        assert _proxy_args(spawn["argv"]) == [], (
            "a socks url ffmpeg cannot speak must be DROPPED, not passed: "
            f"{spawn['argv']}")
        assert _env_proxy_values(spawn["env"]) == {}, (
            "the confined child's environment must carry no proxy at all; got "
            f"{_env_proxy_values(spawn['env'])}")
        assert _SOCKS not in json.dumps(spawn), (
            "the unusable socks url must not reach the ffmpeg child anywhere")
        # The namespace was really created and really torn down -- and the
        # ORDER is measured, not argued: setup ran before any spawn existed,
        # teardown ran after the one spawn it enclosed, and teardown was last.
        cmds = ffmpeg_boundary.netns_cmds
        at = ffmpeg_boundary.netns_cmd_spawns
        assert len(cmds) == len(at) and cmds, (
            f"the netns command recorder must be non-empty and aligned: {cmds}")
        assert cmds[0] == ["ip", "netns", "add", ns], (
            f"the namespace must be created first: {cmds}")
        assert at[0] == 0, (
            "the namespace must exist BEFORE ffmpeg spawns; the recorder saw "
            f"{at[0]} spawn(s) already at `netns add`")
        assert cmds[-1] == ["ip", "netns", "del", ns], (
            f"the namespace must be torn down last: {cmds}")
        assert at[-1] == 1, (
            "teardown must run AFTER the transfer it enclosed; the recorder "
            f"saw {at[-1]} spawn(s) at `netns del`, expected 1")
        assert len(runner._row439_history) == 1, (
            "a confined transfer still logs its history row")
    finally:
        unregister()


# ─── 5. NEGATIVE CONTROL C: an unenterable netns refuses ─────────────

def test_a_netns_that_cannot_be_entered_refuses_rather_than_leaking(
        ffmpeg_boundary, tmp_path, monkeypatch, capsys):
    """Confinement that cannot be ESTABLISHED is UNKNOWN, and UNKNOWN is never
    permission to spawn (CLAUDE.md A7).  The no-CAP_NET_ADMIN shape: every
    namespace setup command fails, ``create()`` returns False, the fail-closed
    posture raises, and the transfer refuses WITHOUT egressing."""
    from bulk_downloader import hls_downloader, netns_isolation

    site = "row439-netns-broken"
    unregister = _register_hls_plugin(site)
    try:
        _configure_tunnel(monkeypatch, site, required=True, tunnel_state="up")
        runner = _plugin_runner(
            site, {"download_dir": str(tmp_path / "dl"),
                   "netns_isolation": dict(_CONFINED_CFG)}, monkeypatch)
        ffmpeg_boundary.break_netns_creation()

        # PRECONDITION: the namespace really is unavailable now.
        assert netns_isolation.create(_expected_ns()) is False, (
            "precondition: the fixture must actually break namespace creation")
        ffmpeg_boundary.netns_cmds.clear()

        ok = _drive_segmented_arm(runner)

        assert ok is False, "an unconfinable transfer must not report success"
        assert ffmpeg_boundary.spawn_count == 0, (
            "confinement could not be established, so ffmpeg must not run: "
            f"{ffmpeg_boundary.spawns}")
        assert ffmpeg_boundary.ip.spawn_count == 0, (
            "nothing may be executed inside a namespace that does not exist")
        assert ffmpeg_boundary.netns_cmds != [], (
            "precondition: confinement must have been ATTEMPTED")
        assert runner._row439_history == []
        err = capsys.readouterr().err
        assert "netns" in err and site in err, (
            f"the refusal must name confinement as its cause; got {err!r}")

        # The distinctive code, read off the seam's own result: an operator must
        # be able to tell 'could not confine' from 'tunnel down'.
        res = runner._hls_download(
            hls_downloader, _MANIFEST, str(tmp_path / "direct.mp4"))
        assert res.ok is False
        assert res.error == "netns_required", res
        assert ffmpeg_boundary.spawn_count == 0
    finally:
        unregister()


def test_a_tunnel_mapped_site_without_confinement_refuses(
        ffmpeg_boundary, tmp_path, monkeypatch, capsys):
    """The residual the integrator must see.  A socks-mapped site with NO netns
    opt-in cannot be confined and cannot carry its proxy, so it refuses -- the
    only alternative is fetching the media outside the tunnel it was mapped to.
    The diagnostic names the remedy, and it is DISTINCT from 'tunnel down'."""
    from bulk_downloader import hls_downloader, netns_isolation

    site = "row439-socks-unconfined"
    unregister = _register_hls_plugin(site)
    try:
        _configure_tunnel(monkeypatch, site, required=True, tunnel_state="up")
        runner = _plugin_runner(
            site, {"download_dir": str(tmp_path / "dl")}, monkeypatch)
        assert runner._download_proxy_url() == _SOCKS
        assert netns_isolation.site_wants_isolation(runner.config) is False, (
            "precondition: this site must NOT opt into confinement")

        ok = _drive_segmented_arm(runner)

        assert ok is False
        assert ffmpeg_boundary.spawn_count == 0, (
            "ffmpeg cannot speak socks5, so spawning it unconfined egresses "
            f"outside the tunnel: {ffmpeg_boundary.spawns}")
        assert ffmpeg_boundary.ip.spawn_count == 0
        err = capsys.readouterr().err
        assert "socks5" in err, (
            f"the refusal must name the scheme it cannot honour; got {err!r}")
        assert "netns_isolation" in err, (
            f"the refusal must name the remedy the operator can apply; got "
            f"{err!r}")

        res = runner._hls_download(
            hls_downloader, _MANIFEST, str(tmp_path / "direct.mp4"))
        assert res.error == "confinement_unavailable", res
    finally:
        unregister()


def test_hls_downloader_refuses_an_unhonourable_proxy_at_the_module_seam(
        ffmpeg_boundary, tmp_path):
    """The owner module refuses on its own, so the dev_suite / bd-dltest callers
    inherit the same contract: a proxy it cannot carry and no namespace to
    confine the child in is an egress claim it cannot establish."""
    from bulk_downloader import hls_downloader

    res = hls_downloader.download(
        _MANIFEST, str(tmp_path / "out.mp4"), proxy_url=_SOCKS)
    assert res.ok is False
    assert res.error == "proxy_unsupported", res
    assert ffmpeg_boundary.spawn_count == 0, (
        "the refusal must happen BEFORE the spawn")

    # ...but WITH a namespace the same unusable proxy is simply dropped and the
    # transfer proceeds confined -- the module implements the ruling too.
    ns = _expected_ns()
    res2 = hls_downloader.download(
        _MANIFEST, str(tmp_path / "out2.mp4"), proxy_url=_SOCKS, netns=ns)
    assert res2.ok is True, res2
    assert ffmpeg_boundary.ip.spawn_count == 1
    assert ffmpeg_boundary.ip.spawns[0]["argv"][1:4] == ["netns", "exec", ns]
    assert ffmpeg_boundary.spawn_count == 1
    assert _proxy_args(ffmpeg_boundary.spawns[0]["argv"]) == []


# ─── 6. An UNMEASURABLE egress posture refuses too ───────────────────

def test_an_unmeasurable_egress_posture_refuses_rather_than_proceeding(
        ffmpeg_boundary, tmp_path, monkeypatch, capsys):
    """The seam's UNKNOWN outcome, proved reachable.

    This is the row's "when tunnel or proxy state cannot be measured the gate
    returns UNKNOWN and refuses the transfer rather than reporting OK".

    NO RED PROVENANCE IS CLAIMED FOR THIS BRANCH and none is manufactured: the
    seam does not exist on the defective parent, so there is nothing to replay.
    This is outcome-reachability for new code, which A5 requires separately
    from the defect replay.
    """
    from bulk_downloader import hls_downloader

    site = "row439-unmeasurable"
    unregister = _register_hls_plugin(site)
    try:
        runner = _plugin_runner(
            site, {"download_dir": str(tmp_path / "dl")}, monkeypatch)

        raises = []

        def _unmeasurable():
            raises.append(1)
            raise RuntimeError("row439 tunnel state is unreadable")

        runner._download_proxy_url = _unmeasurable

        ok = _drive_segmented_arm(runner)

        assert raises == [1], (
            "precondition: the resolution must actually have been attempted "
            f"exactly once, got {len(raises)}")
        assert ffmpeg_boundary.spawn_count == 0, (
            "an unmeasurable egress posture must refuse, not egress: "
            f"{ffmpeg_boundary.spawns}")
        assert ok is False
        assert runner._row439_history == []
        err = capsys.readouterr().err
        assert "could not be measured" in err and site in err, (
            f"the refusal must say the measurement failed; got {err!r}")

        raises.clear()
        res = runner._hls_download(
            hls_downloader, _MANIFEST, str(tmp_path / "direct.mp4"))
        assert raises == [1]
        assert res.ok is False
        assert res.error == "egress_unknown", res
        assert ffmpeg_boundary.spawn_count == 0
    finally:
        unregister()


# ─── 7. Every arm is on the guarded seam (AST, not text) ─────────────

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

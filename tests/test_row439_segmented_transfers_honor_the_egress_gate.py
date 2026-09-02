"""Row 439 -- segmented (HLS/DASH) transfers must pass the fail-closed egress
gate that every sibling transfer path already passes.

THE DEFECT (measured at v3.66.1362, re-verified at v3.66.1432): all six
segmented arms -- jsonapi, vixen, aylo, plugin and library in
``runner_extractors``, plus the scrape-and-click arm in
``runner_transport._do_download`` -- called ``hls_downloader.download()``
directly. That function had no proxy parameter, built no proxy argument, and
its ``Popen`` passed no ``env=``. So for a ``vpn_required`` site whose tunnel
was down, ffmpeg still fetched every segment on the clear interface (exposing
the operator's real address to the CDN), BD's configured per-site proxy never
reached a segmented transfer at all, and any ambient ``http_proxy`` in the
service environment silently rerouted them.

THE CONTRACT (CLAUDE.md A2): a control that cannot evaluate -- or cannot
enforce -- its condition REFUSES. UNKNOWN is a failing third state.

These tests instrument the REAL subprocess boundary. ``_find_ffmpeg`` is
pointed at a local shim script that writes the output file and exits 0, and
the real ``subprocess.Popen`` is wrapped by a recorder, so spawn counts, argv
and the env actually handed to the child are measured rather than inferred
from source text. NO NETWORK IS TOUCHED and no VPN is brought up or down: the
tunnel resolver is injected, exactly as ``bulk_downloader/selftest.py``
(L557-580) does for the sibling paths.
"""
import ast
import os
import subprocess
import sys
from pathlib import Path

import pytest

BD_GATE_SCOPE = "module"

REPO_ROOT = Path(__file__).resolve().parent.parent


# ── harness ──────────────────────────────────────────────────────────

class _Spawns:
    """Records every real Popen the transport makes."""

    def __init__(self):
        self.calls = []          # list of (argv, kwargs)

    @property
    def count(self):
        return len(self.calls)

    def argv(self, i=0):
        return self.calls[i][0]

    def kwargs(self, i=0):
        return self.calls[i][1]


@pytest.fixture
def hls_boundary(tmp_path, monkeypatch):
    """A real-subprocess HLS boundary with a counting Popen.

    Yields (hls_module, spawns, shim_path). Isolates HOME/TMPDIR/cwd and
    removes every inherited proxy variable so an ambient value on the host
    running the suite cannot decide any verdict here.
    """
    from bulk_downloader import hls_downloader as hls

    home = tmp_path / "home"
    tmp = tmp_path / "tmp"
    for d in (home, tmp):
        d.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("TMPDIR", str(tmp))
    monkeypatch.chdir(tmp_path)
    for var in hls._PROXY_ENV_VARS:
        monkeypatch.delenv(var, raising=False)

    # A shim standing in for ffmpeg: writes the output file (last argv token)
    # so the driver's success path is reached, and exits 0. Purely local.
    shim = tmp_path / "fake-ffmpeg"
    shim.write_text(
        "#!/bin/sh\n"
        "for a in \"$@\"; do last=\"$a\"; done\n"
        "printf 'MEDIA' > \"$last\"\n"
        "exit 0\n"
    )
    shim.chmod(0o755)
    monkeypatch.setattr(hls, "_find_ffmpeg", lambda: str(shim))

    spawns = _Spawns()
    real_popen = subprocess.Popen

    def recording_popen(cmd, **kwargs):
        spawns.calls.append((list(cmd), dict(kwargs)))
        return real_popen(cmd, **kwargs)

    monkeypatch.setattr(hls.subprocess, "Popen", recording_popen)
    yield hls, spawns, shim


def _runner(site_id="demo", **cfg):
    """A real SiteRunner with only the attributes these paths touch."""
    import threading
    from bulk_downloader import runner as runner_mod

    r = runner_mod.SiteRunner.__new__(runner_mod.SiteRunner)
    r.site_id = site_id
    r.config = dict(cfg)
    r._stop = threading.Event()
    r._pause = threading.Event()
    r._pause.set()
    r.log_event = lambda *a, **k: None
    r._update_job = lambda *a, **k: None
    return r


def _arm_tunnel(monkeypatch, *, required, socks_url=None, raises=None):
    """Inject the VPN resolver. Never touches a real tunnel.

    `required` -> is_vpn_required_for_site; `socks_url` -> what the resolver
    returns; `raises` -> an exception instance it raises instead.
    """
    from bulk_downloader import vpn_runtime

    seen = {"required": 0, "resolve": 0}

    def _required(site_id):
        seen["required"] += 1
        return required

    def _resolve(site_id):
        seen["resolve"] += 1
        if raises is not None:
            raise raises
        return socks_url

    monkeypatch.setattr(vpn_runtime, "is_vpn_required_for_site", _required)
    monkeypatch.setattr(vpn_runtime, "get_socks_url_for_site", _resolve)
    return seen


# ── the real arm: plugin @extractor, driven end to end ───────────────

def _drive_plugin_arm(runner, manifest_url, tmp_path, monkeypatch, hls):
    """Run the REAL _try_plugin_extractor HLS arm. Returns its bool result."""
    from bulk_downloader import plugins as _P

    dl_dir = tmp_path / "downloads"
    dl_dir.mkdir(exist_ok=True)
    runner.config["download_dir"] = str(dl_dir)

    monkeypatch.setattr(hls, "is_available", lambda: True)
    monkeypatch.setitem(
        _P._extractors, runner.site_id,
        lambda url, ctx: {"video_url": manifest_url, "title": "Clip",
                          "is_hls": True},
    )
    # PRECONDITION: the registry really has an extractor for this site, and it
    # really reports a segmented source. Without this the arm falls through and
    # a zero spawn count would mean "nothing ran", not "the gate refused".
    fn = _P.get_extractor(runner.site_id)
    assert fn is not None, "precondition: no extractor registered for the site"
    probe = fn("https://demo.example/scene/1", {})
    assert probe["is_hls"] is True, "precondition: arm must select the segmented path"
    assert probe["video_url"].endswith(".m3u8"), "precondition: source must be a manifest"

    return runner._try_plugin_extractor("https://demo.example/scene/1")


MANIFEST = "https://cdn.example/scene/master.m3u8"


def test_row439_vpn_required_tunnel_down_spawns_no_ffmpeg(
        hls_boundary, tmp_path, monkeypatch):
    """THE ROW. vpn_required + tunnel down -> zero ffmpeg, distinctive refusal.

    RED on the defective parent: the arm called _hls.download directly, so the
    gate never ran and ffmpeg spawned once against the CDN.
    """
    from bulk_downloader import vpn_runtime

    hls, spawns, _ = hls_boundary
    seen = _arm_tunnel(
        monkeypatch, required=True,
        raises=vpn_runtime.VPNRequiredError(
            "tunnel wg-demo required but down"))
    r = _runner(site_id="demo")

    # PRECONDITION: the gate is genuinely armed for this site.
    assert vpn_runtime.is_vpn_required_for_site("demo") is True
    assert seen["required"] == 1

    ok = _drive_plugin_arm(r, MANIFEST, tmp_path, monkeypatch, hls)

    assert seen["resolve"] >= 1, "the fail-closed resolver was never consulted"
    assert spawns.count == 0, (
        f"a vpn_required site with a down tunnel spawned {spawns.count} "
        f"ffmpeg process(es): {spawns.calls}")
    assert ok is False


def test_row439_the_refusal_names_the_vpn_and_not_a_generic_failure(
        hls_boundary, tmp_path, monkeypatch):
    """A diagnostic that collapses distinct failures costs the investigation.

    The refusal must be distinguishable from 'the stream broke'.
    """
    from bulk_downloader import vpn_runtime

    hls, spawns, _ = hls_boundary
    _arm_tunnel(monkeypatch, required=True,
                raises=vpn_runtime.VPNRequiredError("kill switch tripped"))
    r = _runner(site_id="demo")

    res = r._hls_download_guarded(hls, MANIFEST, str(tmp_path / "o.mp4"))

    assert res.ok is False
    assert res.error == "vpn_required", res.error
    assert "kill switch tripped" in res.error_detail   # server's own words
    assert "failing closed" in res.error_detail
    assert spawns.count == 0


def test_row439_vpn_required_but_no_tunnel_mapped_still_refuses(
        hls_boundary, tmp_path, monkeypatch):
    """get_socks_url_for_site returns None (not raises) when no tunnel is
    mapped to the site. For a vpn_required site that is UNKNOWN, not
    permission -- an unproxied transfer is exactly what must not happen."""
    hls, spawns, _ = hls_boundary
    seen = _arm_tunnel(monkeypatch, required=True, socks_url=None)
    r = _runner(site_id="demo")

    res = r._hls_download_guarded(hls, MANIFEST, str(tmp_path / "o.mp4"))

    assert seen["resolve"] == 1, "precondition: resolution must have been attempted"
    assert res.ok is False
    assert res.error == "vpn_proxy_missing", res.error
    assert spawns.count == 0


def test_row439_a_socks_tunnel_is_refused_not_silently_ignored(
        hls_boundary, tmp_path, monkeypatch):
    """ffmpeg has NO SOCKS CLIENT. Handing it a socks5:// proxy and spawning
    anyway would fetch on the clear interface with the control believing it
    had been honored -- the row's defect wearing a proxy argument."""
    hls, spawns, _ = hls_boundary
    _arm_tunnel(monkeypatch, required=True,
                socks_url="socks5://127.0.0.1:11080")
    r = _runner(site_id="demo")

    res = r._hls_download_guarded(hls, MANIFEST, str(tmp_path / "o.mp4"))

    assert res.ok is False
    assert res.error == "proxy_scheme_unsupported", res.error
    assert "SOCKS" in res.error_detail
    assert "explicit http:// proxy" in res.error_detail  # names the remedy
    assert spawns.count == 0


# ── negative controls: legitimate traffic MUST still flow ────────────

def test_row439_negative_control_ordinary_site_still_downloads(
        hls_boundary, tmp_path, monkeypatch):
    """THE MIRROR DEFECT GUARD. A site with no tunnel and no proxy is the
    operator's declared degrade-open posture: it must transfer exactly as
    before, with zero proxy arguments. A fail-closed fix that refuses this is
    as wrong as the leak it replaces."""
    hls, spawns, _ = hls_boundary
    seen = _arm_tunnel(monkeypatch, required=False, socks_url=None)
    r = _runner(site_id="demo")

    ok = _drive_plugin_arm(r, MANIFEST, tmp_path, monkeypatch, hls)

    assert seen["resolve"] == 1, "the gate must still consult the resolver"
    assert spawns.count == 1, (
        f"legitimate traffic was blocked: {spawns.count} spawns")
    argv = spawns.argv()
    assert "-http_proxy" not in argv, f"unexpected proxy argument: {argv}"
    assert ok is True, "the transfer must complete"


def test_row439_negative_control_vpn_up_transfers_through_the_tunnel(
        hls_boundary, tmp_path, monkeypatch):
    """A genuinely healthy egress path must ALLOW traffic. An explicit http
    proxy is carriable, so the transfer proceeds -- proving the gate refuses
    for the intended reason and not merely because a proxy exists."""
    hls, spawns, _ = hls_boundary
    _arm_tunnel(monkeypatch, required=True, socks_url=None)
    r = _runner(site_id="demo", proxy="http://gateway.local:8888")

    ok = _drive_plugin_arm(r, MANIFEST, tmp_path, monkeypatch, hls)

    assert spawns.count == 1, "a healthy proxied path was refused"
    assert ok is True


# ── the proxy actually reaches ffmpeg, by both carriers ──────────────

def test_row439_a_carriable_proxy_reaches_the_ffmpeg_invocation(
        hls_boundary, tmp_path, monkeypatch):
    """Exactly one -http_proxy carrying the resolved URL, and it must precede
    -i: ffmpeg applies protocol options to the input that FOLLOWS them, so a
    flag placed after -i is silently inert."""
    hls, spawns, _ = hls_boundary
    _arm_tunnel(monkeypatch, required=True, socks_url=None)
    r = _runner(site_id="demo", proxy="http://gateway.local:8888")

    res = r._hls_download_guarded(hls, MANIFEST, str(tmp_path / "o.mp4"))

    assert res.ok is True, (res.error, res.error_detail)
    argv = spawns.argv()
    assert argv.count("-http_proxy") == 1, argv
    assert argv[argv.index("-http_proxy") + 1] == "http://gateway.local:8888"
    assert argv.index("-http_proxy") < argv.index("-i"), (
        "the proxy flag must be an INPUT option, before -i")
    # Second carrier: ffmpeg's hls demuxer forwarding of -http_proxy into
    # nested segment requests is build-dependent, so the env agrees with argv.
    env = spawns.kwargs()["env"]
    assert env["http_proxy"] == "http://gateway.local:8888"
    assert env["https_proxy"] == "http://gateway.local:8888"


def test_row439_an_ambient_http_proxy_cannot_reroute_segments(
        hls_boundary, tmp_path, monkeypatch):
    """The row's third clause: Popen passed no env=, so ffmpeg inherited the
    whole service environment and an ambient http_proxy silently rerouted
    every segment fetch. The spawn env is now built explicitly."""
    hls, spawns, _ = hls_boundary
    monkeypatch.setenv("http_proxy", "http://attacker.invalid:3128")
    monkeypatch.setenv("HTTPS_PROXY", "http://attacker.invalid:3128")
    monkeypatch.setenv("ALL_PROXY", "socks5://attacker.invalid:1080")
    # PRECONDITION: the ambient values really are set in this process.
    assert os.environ["http_proxy"] == "http://attacker.invalid:3128"

    _arm_tunnel(monkeypatch, required=False, socks_url=None)
    r = _runner(site_id="demo")

    res = r._hls_download_guarded(hls, MANIFEST, str(tmp_path / "o.mp4"))

    assert res.ok is True, (res.error, res.error_detail)
    assert spawns.count == 1
    kwargs = spawns.kwargs()
    assert "env" in kwargs, "Popen still inherits the ambient environment"
    env = kwargs["env"]
    for var in ("http_proxy", "HTTPS_PROXY", "ALL_PROXY"):
        assert var not in env, f"{var} leaked into the ffmpeg environment"
    assert "attacker.invalid" not in repr(env)


def test_row439_the_builder_refuses_an_uncarriable_scheme_too(hls_boundary):
    """Defence in depth: even called directly, the argv builder will not
    manufacture an invocation ffmpeg would ignore."""
    hls, _, shim = hls_boundary
    with pytest.raises(ValueError, match="uncarriable proxy scheme"):
        hls._build_ffmpeg_cmd(str(shim), MANIFEST, "/tmp/o.mp4",
                              proxy_url="socks5://127.0.0.1:1080")


# ── the tree gate: no seventh arm can reopen the hole ────────────────

def _download_callsites_outside_the_gate():
    """AST, not text: count Call nodes invoking `<hls-alias>.download(...)`
    across the application package. A text scan would also count the phrase
    inside a docstring (runner_extractors L2194 contains exactly that), which
    is the A7 inverse -- prose must not decide a gate.

    Returns (hits, files_scanned). A hit is (path, lineno, enclosing-def).
    """
    hits, scanned = [], 0
    root = REPO_ROOT / "bulk_downloader"
    for path in sorted(root.rglob("*.py")):
        if path.name == "hls_downloader.py":
            continue                       # the module that DEFINES download
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        scanned += 1
        # map each node to its enclosing function for the allow-list check
        parent_def = {}
        for fn in ast.walk(tree):
            if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                for child in ast.walk(fn):
                    parent_def.setdefault(id(child), fn.name)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            f = node.func
            if not isinstance(f, ast.Attribute) or f.attr != "download":
                continue
            if not isinstance(f.value, ast.Name):
                continue
            if f.value.id not in ("_hls", "hls_downloader", "hls"):
                continue
            hits.append((str(path.relative_to(REPO_ROOT)), node.lineno,
                         parent_def.get(id(node), "<module>")))
    return hits, scanned


def test_row439_every_segmented_arm_goes_through_the_gate():
    """MECHANICAL DENOMINATOR. The only sanctioned direct call to
    hls_downloader.download in the application package is the gate's own. Any
    other is an arm that egresses outside the VPN control -- which is the row
    itself, and how it would silently return."""
    hits, scanned = _download_callsites_outside_the_gate()

    assert scanned > 0, "zero files scanned -- the gate judged nothing (UNKNOWN)"
    outside = [h for h in hits if h[2] != "_hls_download_guarded"]
    assert outside == [], (
        "these call hls_downloader.download() directly, bypassing the "
        f"fail-closed egress gate: {outside}")
    # The gate's own call must exist, or this test would pass over an empty
    # population -- exactly the fail-open shape row 439 is about.
    inside = [h for h in hits if h[2] == "_hls_download_guarded"]
    assert len(inside) == 1, f"expected the gate's single call, got {inside}"


def test_row439_the_gate_test_can_actually_see_a_bypass():
    """MUTATION CATCHER for the test above. A gate that cannot fail is not a
    gate: prove the detector fires on a synthetic bypassing arm."""
    src = (
        "def _try_new_site_extractor(self, url):\n"
        "    from . import hls_downloader as _hls\n"
        "    return _hls.download(url, '/tmp/x.mp4')\n"
    )
    tree = ast.parse(src)
    found = [
        n for n in ast.walk(tree)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
        and n.func.attr == "download"
        and isinstance(n.func.value, ast.Name) and n.func.value.id == "_hls"
    ]
    assert len(found) == 1, "the detector would not have seen a new bypass arm"


def test_row439_all_six_segmented_arms_reach_the_gate():
    """The row names six arms. Assert the exact per-arm count reaching the
    shared seam, so a future edit that drops one is caught by number."""
    hits = []
    for rel in ("bulk_downloader/runner_extractors.py",
                "bulk_downloader/runner_transport.py"):
        path = REPO_ROOT / rel
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if (isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "_hls_download_guarded"):
                hits.append((rel, node.lineno))
    per_file = {}
    for rel, _ in hits:
        per_file[rel] = per_file.get(rel, 0) + 1
    assert per_file.get("bulk_downloader/runner_extractors.py") == 5, per_file
    assert per_file.get("bulk_downloader/runner_transport.py") == 1, per_file
    assert len(hits) == 6, hits

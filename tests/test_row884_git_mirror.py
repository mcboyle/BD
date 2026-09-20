"""Tests for Row 884: GIT-SUBMODULE-HERMETIC-CACHE-AND-OFFLINE-MIRROR.

Acceptance criteria:
(1) Git submodule fetch resolves from local Forgejo mirror in <1s.
(2) Zero WAN egress during dependency sync (loopback only).
(3) Automatic fallback to origin if mirror is unavailable.
"""
from __future__ import annotations

import os
import pathlib
import subprocess
import sys
import time

BD_GATE_SCOPE = "module"

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
GIT_MIRROR_BIN = REPO_ROOT / "toolchain" / "bin" / "bd-git-mirror"


def _run_cmd(cmd: list[str], cwd: pathlib.Path | None = None, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        cmd,
        cwd=str(cwd or REPO_ROOT),
        capture_output=True,
        text=True,
        check=check,
    )


def test_git_mirror_binary_exists_and_is_executable():
    """The bd-git-mirror tool must exist and have executable permissions."""
    assert GIT_MIRROR_BIN.is_file(), f"Expected binary at {GIT_MIRROR_BIN}"
    assert os.access(GIT_MIRROR_BIN, os.X_OK), f"{GIT_MIRROR_BIN} must be executable"


def test_probe_forgejo_mirror_under_one_second():
    """Acceptance (1): the probe answers in <1.0 s and says exactly which.
    Positive control: a live loopback HTTP server (what a Forgejo mirror is to
    the probe: an HTTP endpoint answering 200/301/302/401/403) -> a line
    starting "ONLINE:". Negative control: a closed loopback port -> a line
    starting "OFFLINE:" (never "ONLINE"). Exact prefixes: "reachable" is a
    substring of "unreachable", so substring matching proved nothing."""
    mirror = _Mirror200()
    try:
        t0 = time.perf_counter()
        res = _run_cmd([str(GIT_MIRROR_BIN), "--probe", "--url", mirror.url])
        elapsed = time.perf_counter() - t0
        assert res.returncode == 0
        assert elapsed < 1.0, f"Probe latency {elapsed:.3f}s exceeded 1.0s limit"
        assert res.stdout.startswith("ONLINE:"), res.stdout
        assert mirror.hits == ["/"], mirror.hits          # the probe really asked
    finally:
        mirror.shutdown(); mirror.server_close()

    t0 = time.perf_counter()
    res = _run_cmd([str(GIT_MIRROR_BIN), "--probe", "--url", "http://127.0.0.1:59999"])
    elapsed = time.perf_counter() - t0
    assert res.returncode == 0 and elapsed < 1.0
    assert res.stdout.startswith("OFFLINE:") and "ONLINE:" not in res.stdout, res.stdout




import http.server
import socketserver
import threading


def _bare_with_readme(tmp_path: pathlib.Path, name: str) -> pathlib.Path:
    """A bare repo holding one commit with README.md (the 'dependency')."""
    bare = tmp_path / f"{name}.git"
    _run_cmd(["git", "init", "--bare", str(bare)])
    work = tmp_path / f"{name}-work"
    _run_cmd(["git", "clone", str(bare), str(work)])
    (work / "README.md").write_text(f"# {name}\n", encoding="utf-8")
    _run_cmd(["git", "-C", str(work), "add", "README.md"])
    _run_cmd(["git", "-C", str(work), "-c", "user.name=Test", "-c", "user.email=test@example.com",
              "commit", "-m", "initial"])
    _run_cmd(["git", "-C", str(work), "push", "origin", "HEAD"])
    return bare


class _RecordingOrigin(socketserver.TCPServer):
    """A loopback 'origin' that records every request and answers 404: any hit
    means traffic left the mirror route."""
    allow_reuse_address = True

    def __init__(self):
        self.hits: list[str] = []
        outer = self

        class H(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                outer.hits.append(self.path)
                self.send_response(404); self.send_header("Content-Length", "0"); self.end_headers()
            do_POST = do_GET
            def log_message(self, *a): pass

        super().__init__(("127.0.0.1", 0), H)
        threading.Thread(target=self.serve_forever, daemon=True).start()

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.server_address[1]}"


class _Mirror200(socketserver.TCPServer):
    """A loopback endpoint answering 200 (what the probe calls ONLINE),
    recording every path asked."""
    allow_reuse_address = True

    def __init__(self):
        self.hits: list[str] = []
        outer = self

        class H(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                outer.hits.append(self.path)
                self.send_response(200); self.send_header("Content-Length", "0"); self.end_headers()
            def log_message(self, *a): pass

        super().__init__(("127.0.0.1", 0), H)
        threading.Thread(target=self.serve_forever, daemon=True).start()

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.server_address[1]}"


def _consumer_with_submodule(tmp_path: pathlib.Path, name: str, dep_url: str, seed_bare: pathlib.Path) -> pathlib.Path:
    """A superproject whose .gitmodules names dep_url (the ORIGIN); the gitlink is
    seeded from seed_bare so the consumer commits a real submodule pointer, and
    the checkout is then dropped so a sync must FETCH it again."""
    consumer = tmp_path / name
    consumer.mkdir()
    _run_cmd(["git", "-C", str(consumer), "init"])
    _run_cmd(["git", "-C", str(consumer), "-c", "user.name=Test", "-c", "user.email=test@example.com",
              "commit", "--allow-empty", "-m", "init consumer"])
    _run_cmd(["git", "-C", str(consumer), "-c", "protocol.file.allow=always",
              "submodule", "add", str(seed_bare), "deps/dep"])
    _run_cmd(["git", "-C", str(consumer), "config", "-f", ".gitmodules", "submodule.deps/dep.url", dep_url])
    _run_cmd(["git", "-C", str(consumer), "add", ".gitmodules"])
    _run_cmd(["git", "-C", str(consumer), "-c", "user.name=Test", "-c", "user.email=test@example.com",
              "commit", "-m", "add dep"])
    # forget the seeded checkout: a fresh clone through the configured route is now required
    _run_cmd(["git", "-C", str(consumer), "submodule", "deinit", "-f", "deps/dep"])
    import shutil
    shutil.rmtree(consumer / ".git" / "modules", ignore_errors=True)
    _run_cmd(["git", "-C", str(consumer), "config", "--remove-section", "submodule.deps/dep"], check=False)
    return consumer


def test_submodule_fetch_from_local_mirror_under_one_second(tmp_path: pathlib.Path):
    """Acceptance (1)+(2): with ORIGIN remapped to a local mirror, a FRESH submodule
    fetch (nothing cached) completes through the mirror in <1s and the origin
    receives zero requests. .gitmodules keeps the original URL."""
    origin = _RecordingOrigin()
    try:
        mirror_bare = _bare_with_readme(tmp_path, "dep-mirror")
        origin_url = f"{origin.url}/example/dep.git"
        consumer = _consumer_with_submodule(tmp_path, "consumer", origin_url, mirror_bare)
        assert not (consumer / "deps" / "dep" / "README.md").exists()

        res = _run_cmd([str(GIT_MIRROR_BIN), "--repo", str(consumer), "--remap",
                        f"{origin_url}=file://{mirror_bare.resolve()}"])
        assert res.returncode == 0
        t0 = time.perf_counter()
        res = _run_cmd([str(GIT_MIRROR_BIN), "--repo", str(consumer), "--sync"], check=False)
        elapsed = time.perf_counter() - t0
        assert res.returncode == 0, res.stdout + res.stderr
        assert "mirror rewrite" in res.stdout, res.stdout
        assert elapsed < 1.0, f"Submodule fetch {elapsed:.3f}s exceeded 1.0s limit"
        assert (consumer / "deps" / "dep" / "README.md").is_file()
        assert origin.hits == [], origin.hits
        assert origin_url in (consumer / ".gitmodules").read_text(encoding="utf-8")
    finally:
        origin.shutdown(); origin.server_close()


def test_zero_wan_egress_during_dependency_sync(tmp_path: pathlib.Path):
    """Acceptance (2) with a github-style prefix rewrite: the rewrite reaches the
    submodule clone itself (a fresh clone honours it), so nothing is asked of the
    'WAN' origin -- here a loopback recorder standing in for github.com."""
    origin = _RecordingOrigin()
    try:
        mirror_root = tmp_path / "mirror-root"
        mirror_root.mkdir()
        mirror_bare = _bare_with_readme(mirror_root, "dep")
        origin_url = f"{origin.url}/example/dep.git"
        consumer = _consumer_with_submodule(tmp_path, "consumer_wan", origin_url, mirror_bare)
        # prefix rewrite: <origin>/example/ -> file://<mirror-root>/  (dep.git resolves under it)
        res = _run_cmd([str(GIT_MIRROR_BIN), "--repo", str(consumer), "--configure-insteadof",
                        "--url", f"file://{mirror_root.resolve()}", "--target-prefix", f"{origin.url}/example/"])
        assert res.returncode == 0
        res_cfg = _run_cmd(["git", "-C", str(consumer), "config", "--get-regexp", "url\\..*insteadof"])
        assert f"{origin.url}/example/" in res_cfg.stdout
        res = _run_cmd([str(GIT_MIRROR_BIN), "--repo", str(consumer), "--url", f"file://{mirror_root.resolve()}", "--sync"], check=False)
        assert res.returncode == 0, res.stdout + res.stderr
        assert (consumer / "deps" / "dep" / "README.md").is_file()
        assert origin.hits == [], origin.hits
    finally:
        origin.shutdown(); origin.server_close()


def test_automatic_fallback_to_origin_when_mirror_unavailable(tmp_path: pathlib.Path):
    """Acceptance (3): the mirror is gone; the sync REALLY pulls from the original
    origin (a live bare repo), exits 0 with the payload present, and the
    .gitmodules / insteadOf configuration is preserved for when the mirror is back."""
    offline_url = "http://127.0.0.1:59999"  # Port guaranteed closed
    res = _run_cmd([str(GIT_MIRROR_BIN), "--probe", "--url", offline_url], check=False)
    assert res.returncode == 0
    assert "OFFLINE" in res.stdout or "fallback" in res.stdout.lower()

    origin_bare = _bare_with_readme(tmp_path, "dep-origin")
    origin_url = f"file://{origin_bare.resolve()}"
    consumer = _consumer_with_submodule(tmp_path, "consumer_fallback", origin_url, origin_bare)
    dead_mirror = f"file://{(tmp_path / 'no-such-mirror.git').resolve()}"
    _run_cmd([str(GIT_MIRROR_BIN), "--repo", str(consumer), "--remap", f"{origin_url}={dead_mirror}"])

    res = _run_cmd([str(GIT_MIRROR_BIN), "--repo", str(consumer), "--url", dead_mirror, "--sync"], check=False)
    assert res.returncode == 0, res.stdout + res.stderr
    assert "falling back to direct origin" in res.stdout and "synced from direct origin" in res.stdout
    assert (consumer / "deps" / "dep" / "README.md").is_file()
    assert origin_url in (consumer / ".gitmodules").read_text(encoding="utf-8")
    res_cfg = _run_cmd(["git", "-C", str(consumer), "config", "--get-regexp", "url\\..*insteadof"])
    assert dead_mirror in res_cfg.stdout and origin_url in res_cfg.stdout  # restored after the fallback run

    # Negative control: mirror dead AND origin dead -> rc 1, says so
    (tmp_path / "gone").mkdir()
    import shutil
    shutil.move(str(origin_bare), str(tmp_path / "gone" / "dep-origin.git"))
    consumer2 = _consumer_with_submodule(tmp_path, "consumer_dead", origin_url, tmp_path / "gone" / "dep-origin.git")
    _run_cmd([str(GIT_MIRROR_BIN), "--repo", str(consumer2), "--remap", f"{origin_url}={dead_mirror}"])
    res = _run_cmd([str(GIT_MIRROR_BIN), "--repo", str(consumer2), "--url", dead_mirror, "--sync"], check=False)
    assert res.returncode == 1 and "failed against origin too" in res.stderr


def test_selftest_passes():
    """Verify toolchain --selftest convention passes with 0 exit code."""
    res = _run_cmd([str(GIT_MIRROR_BIN), "--selftest"])
    assert res.returncode == 0
    assert "SELFTEST PASS" in res.stdout


# ---- fixer (O928) controls: correctness REFUTE E1 / E2 / E3 ----

def _load_tool():
    """Import the extensionless script as a module (SourceFileLoader)."""
    import importlib.machinery, importlib.util
    loader = importlib.machinery.SourceFileLoader("bd_git_mirror", str(GIT_MIRROR_BIN))
    spec = importlib.util.spec_from_loader("bd_git_mirror", loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


def _insteadof_pairs(repo: pathlib.Path) -> set[tuple[str, str]]:
    res = _run_cmd(["git", "-C", str(repo), "config", "--local", "--get-regexp", "url\\..*insteadof"], check=False)
    out = set()
    for line in res.stdout.splitlines():
        key, _, origin = line.partition(" ")
        out.add((key[len("url."):-len(".insteadof")], origin))
    return out


def test_one_mirror_for_several_origins_survives_a_fallback_run(tmp_path: pathlib.Path):
    """E1: a mirror mapped to two origins (multi-valued insteadOf) kept only the
    last origin after the origin-route restore."""
    origin_bare = _bare_with_readme(tmp_path, "dep-origin")
    origin_url = f"file://{origin_bare.resolve()}"
    consumer = _consumer_with_submodule(tmp_path, "consumer_multi", origin_url, origin_bare)
    dead_mirror = f"file://{(tmp_path / 'no-such-mirror.git').resolve()}"
    _run_cmd([str(GIT_MIRROR_BIN), "--repo", str(consumer), "--remap", f"{origin_url}={dead_mirror}"])
    _run_cmd([str(GIT_MIRROR_BIN), "--repo", str(consumer), "--remap", f"https://github.com/={dead_mirror}"])
    _run_cmd([str(GIT_MIRROR_BIN), "--repo", str(consumer), "--remap", f"https://github.com/={dead_mirror}"])  # idempotent
    before = _insteadof_pairs(consumer)
    assert before == {(dead_mirror, origin_url), (dead_mirror, "https://github.com/")}
    res = _run_cmd([str(GIT_MIRROR_BIN), "--repo", str(consumer), "--url", dead_mirror, "--sync"], check=False)
    assert res.returncode == 0 and "synced from direct origin" in res.stdout, res.stdout + res.stderr
    assert _insteadof_pairs(consumer) == before


def test_default_mirror_is_probed_before_any_fetch(tmp_path: pathlib.Path, monkeypatch):
    """E2: --sync without --url skipped probe_mirror() entirely. Now every
    mirror the configured rewrites point at is probed before a fetch, and an
    unreachable one is announced; an explicit --url is probed as given."""
    mod = _load_tool()
    probed, synced = [], []
    monkeypatch.setattr(mod, "probe_mirror", lambda url, timeout=1.0: probed.append(url) or False)
    monkeypatch.setattr(mod, "_run_all", lambda cmds: synced.append(cmds) or None)
    repo = tmp_path / "r"
    repo.mkdir()
    _run_cmd(["git", "-C", str(repo), "init"])
    mirror = "http://127.0.0.1:3000/mirror/"
    _run_cmd([str(GIT_MIRROR_BIN), "--repo", str(repo), "--remap", f"https://github.com/={mirror}"])
    monkeypatch.setattr(sys, "argv", ["bd-git-mirror", "--repo", str(repo), "--sync"])
    assert mod.main() == 0
    assert probed == [mirror]
    assert len(synced) == 1 and not any("insteadOf" in " ".join(c) for c in synced[0])  # origin route
    probed.clear()
    monkeypatch.setattr(sys, "argv", ["bd-git-mirror", "--repo", str(repo), "--url", "http://127.0.0.1:59999", "--sync"])
    assert mod.main() == 0
    assert probed == ["http://127.0.0.1:59999"]


def test_interrupted_fallback_run_is_repaired_on_the_next_run(tmp_path: pathlib.Path):
    """E3: a run killed between lifting and restoring the rewrites leaves the
    on-disk config stripped; the journal makes the next run restore it first."""
    import json
    mod = _load_tool()
    repo = tmp_path / "r"
    repo.mkdir()
    _run_cmd(["git", "-C", str(repo), "init"])
    mirror, origin = "http://127.0.0.1:3000/mirror/", "https://github.com/"
    _run_cmd([str(GIT_MIRROR_BIN), "--repo", str(repo), "--remap", f"{origin}={mirror}"])
    # simulate the crash: journal written, keys lifted, process gone
    journal = mod._journal_path(repo)
    journal.write_text(json.dumps([[mirror, origin]]), encoding="utf-8")
    _run_cmd(["git", "-C", str(repo), "config", "--local", "--unset-all", f"url.{mirror}.insteadOf"])
    assert _insteadof_pairs(repo) == set()
    assert mod._recover_interrupted_run(repo) is True
    assert _insteadof_pairs(repo) == {(mirror, origin)}
    assert not journal.exists()
    assert mod._recover_interrupted_run(repo) is False  # nothing left to repair

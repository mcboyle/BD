"""cli-tools-fix r1: deep-cli-toolchain findings F1 F2 F3 F6 F8 F9 F10 F11 F12 F13 F14 F15.

Each test drives the shipped tool the way an operator or a gate does and pins
the exit code / output contract the finding showed broken. Everything a tool
might write (HOME, TMPDIR, BD_HOME, caches, install targets) lives under
tmp_path; external commands that would provision or hit the network (gh,
apt-get, pip, git clone, curl) are PATH shims.
"""
from __future__ import annotations

import ast
import http.server
import json
import os
import shutil
import stat
import subprocess
import sys
import threading
import uuid
from pathlib import Path

import pytest

BD_GATE_SCOPE = "module"

REPO = Path(__file__).resolve().parents[1]
BIN = REPO / "toolchain" / "bin"
INSTALLER = REPO / "toolchain" / "install_bdsuite.sh"
PY = sys.executable

unprivileged = pytest.mark.skipif(os.geteuid() == 0, reason="root reads/writes any mode")


def _env(tmp_path: Path, **extra: str) -> dict:
    env = {k: v for k, v in os.environ.items()
           if not k.startswith(("BD_", "BDTOOLS_")) and k not in ("AST_SERVER_URL", "PYTHONPATH")}
    home = tmp_path / "home"
    tmp = tmp_path / "tmp"
    home.mkdir(exist_ok=True)
    tmp.mkdir(exist_ok=True)
    env.update({"LC_ALL": "C", "HOME": str(home), "TMPDIR": str(tmp),
                "BD_HOME": str(home / ".bd"), "BDTOOLS_CACHE_DIR": str(tmp / "cache"),
                "XDG_STATE_HOME": str(home / "state"), "XDG_CACHE_HOME": str(home / "cache"),
                "PLAYWRIGHT_BROWSERS_PATH": str(home / "pw")})
    env.update(extra)
    return env


def _run(argv, env, cwd=None, timeout=120):
    return subprocess.run([str(a) for a in argv], env=env, cwd=str(cwd or env["TMPDIR"]),
                          capture_output=True, text=True, timeout=timeout)


def _shim(dirpath: Path, name: str, body: str) -> Path:
    dirpath.mkdir(parents=True, exist_ok=True)
    p = dirpath / name
    p.write_text("#!/usr/bin/env bash\n" + body, encoding="utf-8")
    p.chmod(0o755)
    return p


def _git_tree(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q", str(path)], check=True, timeout=30)
    return path


def _tool(path: Path, text: str, mode: int = 0o755) -> Path:
    path.write_text(text, encoding="utf-8")
    path.chmod(mode)
    return path


# ---------------------------------------------------------------- F1 installer

def _install(tmp_path: Path, *args: str):
    dest = tmp_path / "suite"
    link = tmp_path / "links"
    env = _env(tmp_path, BD_SUITE_BIN=str(dest), BD_SUITE_LINK_BIN=str(link),
               BD_WORK_TREE=str(REPO))
    return _run(["bash", INSTALLER, *args], env, cwd=REPO, timeout=180), dest, link, env


def test_f1a_unrelated_file_in_suite_bin_is_refused_and_survives(tmp_path):
    dest = tmp_path / "suite"
    dest.mkdir()
    mine = _tool(dest / "my_custom_user_binary", "#!/bin/sh\necho mine\n")
    proc, dest, link, _ = _install(tmp_path)
    out = proc.stdout + proc.stderr
    assert proc.returncode == 2, out
    assert "refusing unrelated file in BD_SUITE_BIN" in proc.stderr, out
    assert "my_custom_user_binary" in proc.stderr, out
    assert mine.read_text(encoding="utf-8") == "#!/bin/sh\necho mine\n"
    assert not (dest / ".bdsuite-manifest.json").exists(), sorted(os.listdir(dest))
    assert not (link / "bd").exists() and not (link / "bd").is_symlink()


def test_f1a_control_reinstall_over_own_generation_and_pycache(tmp_path):
    first, dest, _link, env = _install(tmp_path)
    assert first.returncode == 0, first.stdout + first.stderr
    pyc = dest / "__pycache__"
    pyc.mkdir(exist_ok=True)
    (pyc / "bdtools_sec.cpython-312.pyc").write_bytes(b"\0")
    second = _run(["bash", INSTALLER], env, cwd=REPO, timeout=180)
    assert second.returncode == 0, second.stdout + second.stderr
    assert (dest / ".bdsuite-manifest.json").is_file()


def test_f1b_installed_suite_passes_its_own_selfcheck(tmp_path):
    proc, dest, _link, env = _install(tmp_path)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    for name in ("bdtools_ast.py", "bdtools_cache.py", "bdtools_sec.py"):
        assert stat.S_IMODE((dest / name).stat().st_mode) == 0o755, name
    assert stat.S_IMODE((dest / "_bd_work_tree.py").stat().st_mode) == 0o644
    chk = _run([PY, dest / "bd-selfcheck", "--bin", dest, "--pk", tmp_path / "no-pk"], env)
    assert chk.returncode == 0, chk.stdout + chk.stderr
    assert "not executable" not in chk.stdout


def test_f1c_help_prints_usage_and_installs_nothing(tmp_path):
    proc, dest, link, _ = _install(tmp_path, "--help")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "Usage" in proc.stdout
    assert not dest.exists() and not link.exists()


def test_f1c_unknown_arg_is_refused_before_any_install(tmp_path):
    proc, dest, link, _ = _install(tmp_path, "--dry-run")
    assert proc.returncode == 2, proc.stdout + proc.stderr
    assert "unknown arg" in proc.stderr and "--dry-run" in proc.stderr
    assert not dest.exists() and not link.exists()


# ---------------------------------------------------------------- F2 bd-tool-lint --json

_CLEAN_TOOL = ("#!/usr/bin/env python3\nimport sys\ndef selftest():\n"
               " print('SELFTEST PASS' if 1 else 'SELFTEST FAIL'); return 0\n"
               "def main(): return 0\n# --selftest\nif __name__=='__main__': sys.exit(main())\n")


def _lint_bins(tmp_path: Path):
    clean = tmp_path / "clean"
    clean.mkdir()
    for name in ("bd-cut", "bd-bump", "bd-regen", "bd-guardcheck"):
        _tool(clean / name, _CLEAN_TOOL)
    dirty = tmp_path / "dirty"
    shutil.copytree(clean, dirty)
    bad = dirty / "bd-bad-lint"
    bad.write_bytes(b"#!/usr/bin/env python3\nimport sys, bdtools_sec\n"
                    b"# non-ascii \xe2\x80\x93 and no selftest/json\nsys.exit(0)\n")
    bad.chmod(0o755)
    return clean, dirty


def _lint(tmp_path, binpath, *mode):
    return _run([PY, BIN / "bd-tool-lint", "--no-runtime", "--bin", binpath, *mode],
                _env(tmp_path), timeout=180)


def test_f2_json_without_gate_blocks_lint_errors_like_text_mode(tmp_path):
    _clean, dirty = _lint_bins(tmp_path)
    text = _lint(tmp_path, dirty)
    assert text.returncode == 3, text.stdout + text.stderr
    res = _lint(tmp_path, dirty, "--json")
    payload = json.loads(res.stdout)
    assert payload["clean"] is False and payload["error_count"] > 0
    assert res.returncode == 3, res.stderr


def test_f2_json_gate_uses_the_tier1_policy_code_3(tmp_path):
    _clean, dirty = _lint_bins(tmp_path)
    res = _lint(tmp_path, dirty, "--json", "--gate")
    json.loads(res.stdout)
    assert res.returncode == 3, res.stderr


def test_f2_json_gate_runs_when_coverage(tmp_path):
    clean, _dirty = _lint_bins(tmp_path)
    text = _lint(tmp_path, clean, "--gate")
    assert text.returncode == 3, text.stdout + text.stderr
    res = _lint(tmp_path, clean, "--json", "--gate")
    payload = json.loads(res.stdout)
    assert payload["clean"] is True
    assert payload["when_coverage"]["blocked"] is True, payload.get("when_coverage")
    assert res.returncode == 3, res.stderr


def test_f2_control_clean_json_without_gate_is_0(tmp_path):
    clean, _dirty = _lint_bins(tmp_path)
    res = _lint(tmp_path, clean, "--json")
    payload = json.loads(res.stdout)
    assert payload["clean"] is True
    assert res.returncode == 0, res.stderr


# ---------------------------------------------------------------- F3 bd-tool-smoke

_UNDEF = "#!/usr/bin/env python3\ndef f(): return undefined_var_xyz\nf()\n"
_OK = "#!/usr/bin/env python3\nimport sys\nsys.exit(0)\n"


def _smoke(tmp_path, binpath, *args, work=None):
    env = _env(tmp_path, BD_WORK=str(work or tmp_path / "nowork"))
    return _run([PY, BIN / "bd-tool-smoke", *args, "--bin", binpath], env, timeout=180)


@unprivileged
def test_f3a_unreadable_tool_is_cannot_evaluate_not_clean(tmp_path):
    b = tmp_path / "bin"
    b.mkdir()
    _tool(b / "bd-ok", _OK)
    undef = _tool(b / "bd-undef", _UNDEF, mode=0)
    try:
        res = _smoke(tmp_path, b, "--gate")
    finally:
        undef.chmod(0o755)
    assert res.returncode == 2, res.stdout + res.stderr
    assert "CANNOT-EVALUATE" in res.stderr and "reason=UNREADABLE" in res.stderr
    assert "bd-undef" in res.stderr
    assert "CLEAN" not in res.stdout


def test_f3a_control_readable_undefined_name_fails_gate(tmp_path):
    b = tmp_path / "bin"
    b.mkdir()
    _tool(b / "bd-ok", _OK)
    _tool(b / "bd-undef", _UNDEF)
    res = _smoke(tmp_path, b, "--gate")
    assert res.returncode == 3, res.stdout + res.stderr
    assert "WOULD CRASH" in res.stdout and "bd-undef" in res.stdout


def test_f3b_run_json_stdout_is_only_json(tmp_path):
    b = tmp_path / "bin"
    b.mkdir()
    _tool(b / "bd-ok", _OK)
    work = tmp_path / "work"
    work.mkdir()
    (work / "dummy.txt").write_text("clean\n", encoding="utf-8")
    res = _smoke(tmp_path, b, "--run", "--json", work=work)
    payload = json.loads(res.stdout)
    assert payload["tools_scanned"] == 1
    assert payload["restore_incomplete"] == []
    assert "work tree restored" in res.stderr
    assert res.returncode == 0, res.stderr


@unprivileged
@pytest.mark.parametrize("mode", ["--json", "--text"])
def test_f3c_restore_incomplete_is_not_exit_0(tmp_path, mode):
    b = tmp_path / "bin"
    b.mkdir()
    _tool(b / "bd-dirtier", "#!/usr/bin/env python3\nimport os\n"
          "p = os.path.join(os.environ['BD_WORK'], 'dummy.txt')\n"
          "open(p, 'w').write('dirty\\n')\nos.chmod(p, 0o444)\n")
    work = tmp_path / "work"
    work.mkdir()
    dummy = work / "dummy.txt"
    dummy.write_text("clean\n", encoding="utf-8")
    try:
        res = _smoke(tmp_path, b, "--run", *([mode] if mode == "--json" else []), work=work)
    finally:
        dummy.chmod(0o644)
    assert dummy.read_text(encoding="utf-8") == "dirty\n", "probe did not leave the tree dirty"
    if mode == "--json":
        payload = json.loads(res.stdout)
        assert payload["restore_incomplete"] == ["dummy.txt"], payload
        assert "RESTORE INCOMPLETE" in res.stderr
    else:
        assert "RESTORE INCOMPLETE" in res.stdout, res.stdout
    assert res.returncode == 2, res.stdout + res.stderr


# ---------------------------------------------------------------- F6 bdtools_cache

@pytest.fixture
def bdcache(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(BIN))
    import bdtools_cache
    monkeypatch.setattr(bdtools_cache, "CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.delenv("BDTOOLS_CACHE", raising=False)
    return bdtools_cache


def test_f6_s14_save_does_not_use_a_fixed_shared_tmp_name(bdcache, tmp_path):
    cdir = tmp_path / "cache"
    (cdir / "ns.json.tmp").mkdir(parents=True)   # another writer's fixed temp name
    c = bdcache.Cache("ns", "L1")
    c.get_or_compute_sha("sha1", lambda: {"ok": 1})
    c.save()
    calls = []
    again = bdcache.Cache("ns", "L1").get_or_compute_sha("sha1", lambda: calls.append(1))
    assert again == {"ok": 1} and calls == []
    assert sorted(p.name for p in cdir.iterdir()) == ["ns.json", "ns.json.tmp"]


def test_f6_s15_values_that_do_not_round_trip_keep_their_type(bdcache):
    c = bdcache.Cache("types", "L1")
    assert c.get_or_compute_sha("t", lambda: ("line", 10)) == ("line", 10)
    assert c.get_or_compute_sha("d", lambda: {1: "one"}) == {1: "one"}
    c.save()
    r = bdcache.Cache("types", "L1")
    t = r.get_or_compute_sha("t", lambda: ("line", 10))
    d = r.get_or_compute_sha("d", lambda: {1: "one"})
    assert type(t) is tuple and t == ("line", 10)
    assert d == {1: "one"}


def test_f6_s15_none_result_is_cached(bdcache):
    calls = []

    def compute():
        calls.append(1)

    c = bdcache.Cache("none", "L1")
    c.get_or_compute_sha("n", compute)
    c.get_or_compute_sha("n", compute)
    assert len(calls) == 1 and c.hits == 1
    c.save()
    r = bdcache.Cache("none", "L1")
    assert r.get_or_compute_sha("n", compute) is None
    assert len(calls) == 1 and r.hits == 1


@pytest.mark.parametrize("entries", [None, [], "x", 3])
def test_f6_s16_non_dict_entries_degrade_to_empty(bdcache, tmp_path, entries):
    cdir = tmp_path / "cache"
    cdir.mkdir()
    (cdir / "bad.json").write_text(json.dumps({"schema": bdcache.SCHEMA, "logic": "L1",
                                               "entries": entries}), encoding="utf-8")
    c = bdcache.Cache("bad", "L1")
    assert c.get_or_compute_sha("s", lambda: [42]) == [42]
    assert c.misses == 1


def test_f6_s17_logic_key_separates_source_boundaries(bdcache, tmp_path):
    a, b = tmp_path / "a.py", tmp_path / "b.py"
    a.write_bytes(b"ab")
    b.write_bytes(b"c")
    k1 = bdcache.logic_key(str(a), str(b))
    a.write_bytes(b"a")
    b.write_bytes(b"bc")
    k2 = bdcache.logic_key(str(a), str(b))
    assert k1 != k2
    assert k2 == bdcache.logic_key(str(a), str(b))


def test_f6_control_round_trip_hit(bdcache):
    c = bdcache.Cache("ctl", "L1")
    c.get_or_compute_sha("s", lambda: {"v": [1, "a"]})
    c.save()
    r = bdcache.Cache("ctl", "L1")
    assert r.get_or_compute_sha("s", lambda: None) == {"v": [1, "a"]} and r.hits == 1


# ---------------------------------------------------------------- F8 bdtools_ast

class _Stub(http.server.BaseHTTPRequestHandler):
    body = b"null"
    code = 200

    def _reply(self):
        self.send_response(self.code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(self.body)))
        self.end_headers()
        self.wfile.write(self.body)

    def do_POST(self):
        self.rfile.read(int(self.headers.get("Content-Length") or 0))
        self._reply()

    do_GET = _reply

    def log_message(self, *a):
        pass


@pytest.fixture
def stub_url():
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Stub)
    th = threading.Thread(target=srv.serve_forever, daemon=True)
    th.start()
    try:
        yield "http://127.0.0.1:%d" % srv.server_address[1]
    finally:
        srv.shutdown()
        srv.server_close()


@pytest.fixture
def bdast(monkeypatch):
    monkeypatch.syspath_prepend(str(BIN))
    import bdtools_ast
    return bdtools_ast


def _ast_cli(tmp_path, *args):
    env = _env(tmp_path, AST_SERVER_URL="http://127.0.0.1:9")
    return _run([PY, BIN / "bdtools_ast.py", *args], env, timeout=120)


def test_f8_s18_parse_missing_file_is_cannot_evaluate(tmp_path):
    res = _ast_cli(tmp_path, "parse", tmp_path / "nope.py")
    assert res.returncode == 2, res.stderr
    assert "Traceback" not in res.stderr
    assert "CANNOT-EVALUATE" in res.stderr and "UNREADABLE" in res.stderr


def test_f8_s18_benchmark_offline_is_cannot_evaluate(tmp_path):
    res = _ast_cli(tmp_path, "benchmark")
    assert res.returncode == 2, res.stderr
    assert "Traceback" not in res.stderr
    assert "CANNOT-EVALUATE" in res.stderr


def test_f8_control_parse_offline_falls_back(tmp_path):
    good = tmp_path / "good.py"
    good.write_text("x = 1\n", encoding="utf-8")
    res = _ast_cli(tmp_path, "parse", good)
    assert res.returncode == 0, res.stderr
    assert "source: fallback" in res.stdout


@pytest.mark.parametrize("url", ["http://127.0.0.1:notaport", "http://127.0.0.1:99999"])
def test_f8_s19_bad_port_falls_back_or_raises_runtimeerror(bdast, url):
    res = bdast.parse_code("x = 1", server_url=url, fallback=True)
    assert res.status == "ok" and res.source == "fallback"
    with pytest.raises(RuntimeError):
        bdast.parse_code("x = 1", server_url=url, fallback=False)
    assert bdast.is_server_available(url) is False
    assert bdast.query_pattern("p", "x = 1", server_url=url)["status"] == "offline"


@pytest.mark.parametrize("body", [b"null", b"[]", b'"ok"',
                                  b'{"status": "ok", "node_count": "many"}'])
def test_f8_s19_malformed_server_reply_falls_back(bdast, stub_url, monkeypatch, body):
    monkeypatch.setattr(_Stub, "body", body)
    monkeypatch.setattr(_Stub, "code", 200)
    res = bdast.parse_code("x = 1", server_url=stub_url, fallback=True, timeout=5)
    assert res.status == "ok" and res.source == "fallback"
    with pytest.raises(RuntimeError):
        bdast.parse_code("x = 1", server_url=stub_url, fallback=False, timeout=5)
    # /health only judges {"status": "ok"}; a non-dict reply is not healthy (and must not raise).
    healthy = body.startswith(b"{")
    assert bdast.is_server_available(stub_url, timeout=5) is healthy


def test_f8_control_http_500_falls_back_and_ok_reply_is_remote(bdast, stub_url, monkeypatch):
    monkeypatch.setattr(_Stub, "body", b'{"status": "error"}')
    monkeypatch.setattr(_Stub, "code", 500)
    res = bdast.parse_code("x = 1", server_url=stub_url, fallback=True, timeout=5)
    assert res.source == "fallback"
    monkeypatch.setattr(_Stub, "body", b'{"status": "ok", "node_count": 7}')
    monkeypatch.setattr(_Stub, "code", 200)
    res = bdast.parse_code("x = 1", server_url=stub_url, fallback=True, timeout=5)
    assert res.source == "remote" and res.node_count == 7


# ---------------------------------------------------------------- F9 bd

def _bd(tmp_path, *args, envfile=None, **extra):
    env = _env(tmp_path, **extra)
    if envfile is not None:
        env["BD_ENV_FILE"] = str(envfile)
    return _run(["bash", BIN / "bd", *args], env, timeout=60)


def test_f9_help_prints_usage(tmp_path):
    res = _bd(tmp_path, "--help", BD_SKIP_ENV_CHECK="1")
    assert res.returncode == 0, res.stderr
    assert "Usage" in res.stdout


def test_f9_dash_c_without_snippet_names_the_problem(tmp_path):
    res = _bd(tmp_path, "-c", BD_SKIP_ENV_CHECK="1")
    assert res.returncode == 2
    assert "bd: -c needs a shell snippet" in res.stderr, res.stderr


def test_f9_leading_dash_command_is_executed_not_parsed_by_exec(tmp_path):
    shim = tmp_path / "shim"
    _shim(shim, "-dashcmd", "echo DASHCMD-RAN\n")
    res = _bd(tmp_path, "-dashcmd", BD_SKIP_ENV_CHECK="1",
              PATH="%s:%s" % (shim, os.environ["PATH"]))
    assert res.returncode == 0, res.stderr
    assert "DASHCMD-RAN" in res.stdout


@pytest.mark.parametrize("tool", ["bd", "bd-status"])
def test_f9_mid_file_env_failure_is_unevaluable(tmp_path, tool):
    envf = tmp_path / "partial.env"
    envf.write_text("export PART1=ok\nfalse\nexport PART2=after_fail\n", encoding="utf-8")
    env = _env(tmp_path, BD_ENV_FILE=str(envf), BD_WORK_TREE=str(REPO))
    argv = ["bash", BIN / tool] + (["sh", "-c", "echo RAN-$PART2"] if tool == "bd" else [])
    res = _run(argv, env, timeout=60)
    assert res.returncode == 2, res.stdout + res.stderr
    assert "BD-UNEVALUABLE" in res.stderr and "BD_SKIP_ENV_CHECK" in res.stderr
    assert "RAN-" not in res.stdout and "=== ENV ===" not in res.stdout


def test_f9_control_guarded_failures_and_real_bdenv_still_load(tmp_path):
    envf = tmp_path / "guarded.env"
    envf.write_text("no_such_cmd_xyz 2>/dev/null || true\nif false; then :; fi\n"
                    "f() { false; }\nf || :\nexport GUARDED=yes\n", encoding="utf-8")
    res = _bd(tmp_path, "sh", "-c", "echo G-$GUARDED", envfile=envf)
    assert res.returncode == 0, res.stderr
    assert "G-yes" in res.stdout
    real = _bd(tmp_path, "sh", "-c", "echo WT-$BD_WORK_TREE", envfile=REPO / "toolchain" / "bdenv.sh",
               BD_ENV_NO_SERVICES="1", BD_WORK_TREE=str(REPO))
    assert real.returncode == 0, real.stderr
    assert "WT-%s" % REPO in real.stdout


# ---------------------------------------------------------------- F10 bd-status

def _status_env(tmp_path, **extra):
    envf = tmp_path / "fast.env"
    envf.write_text("sleep() { :; }\nexport DISPLAY=:99 BD_HOME=/nonexistent "
                    "PLAYWRIGHT_BROWSERS_PATH=/nonexistent\n", encoding="utf-8")
    wt = _git_tree(tmp_path / "wt")
    return _env(tmp_path, BD_ENV_FILE=str(envf), BD_WORK_TREE=str(wt), **extra)


def test_f10_help_prints_usage_without_probing(tmp_path):
    res = _run(["bash", BIN / "bd-status", "--help"], _status_env(tmp_path), timeout=60)
    assert res.returncode == 0, res.stderr
    assert "Usage" in res.stdout and "=== ENV ===" not in res.stdout


def test_f10_unknown_arg_is_refused(tmp_path):
    res = _run(["bash", BIN / "bd-status", "--bogus"], _status_env(tmp_path), timeout=60)
    assert res.returncode == 2, res.stdout + res.stderr
    assert "unknown arg" in res.stderr and "=== ENV ===" not in res.stdout


def test_f10_missing_checks_fail_the_exit_code(tmp_path):
    res = _run(["bash", BIN / "bd-status"], _status_env(tmp_path), timeout=120)
    assert "service venv" in res.stdout and "MISSING" in res.stdout
    failed = [line for line in res.stdout.splitlines()
              if "\x1b[31m-- " in line or "MISSING --" in line]
    assert failed, res.stdout
    assert "bd-status: %d check(s) failed" % len(failed) in res.stdout, res.stdout
    assert res.returncode == 1, res.stdout + res.stderr


def _status_env_with_venv(tmp_path):
    env = _status_env(tmp_path)
    venv_bin = Path(env["BD_WORK_TREE"]) / "venv" / "bin"
    venv_bin.mkdir(parents=True)
    (venv_bin / "python").symlink_to(PY)        # the service-runtime check now passes
    return env


def test_f10_passing_checks_are_not_counted(tmp_path):
    res = _run(["bash", BIN / "bd-status", ""], _status_env_with_venv(tmp_path), timeout=120)
    lines = res.stdout.splitlines()
    assert any("\x1b[32mOK " in ln and "service runtime" in ln for ln in lines), res.stdout
    failed = [ln for ln in lines if "\x1b[31m-- " in ln or "MISSING --" in ln]
    assert "bd-status: %d check(s) failed" % len(failed) in res.stdout, res.stdout


def test_f10_control_selftest_exits_0_and_venv_check_can_pass(tmp_path):
    env = _status_env_with_venv(tmp_path)
    res = _run(["bash", BIN / "bd-status", "--selftest"], env, timeout=60)
    assert res.returncode == 0 and "SELFTEST PASS" in res.stdout, res.stdout + res.stderr
    res = _run(["bash", BIN / "bd-status"], env, timeout=120)
    assert any("\x1b[32mOK " in ln and "service runtime" in ln
               for ln in res.stdout.splitlines()), res.stdout


# ---------------------------------------------------------------- F11 bd-reindex

_GENERATORS = ("build_pin_index.py", "build_route_index.py", "gui_parity_inventory.py",
               "build_function_index.py", "dependency_graph.py")


def _reindex_tree(tmp_path, facing="true", fail_marker=None):
    wt = _git_tree(tmp_path / "wt")
    (wt / "tools").mkdir()
    route = json.dumps({"routes": [{"endpoint": "scrape_listing.api_scrape_listing",
                                    "operator_facing": facing == "true"}]})
    for t in _GENERATORS:
        body = "import pathlib\npathlib.Path('ROUTE_INDEX.json').write_text(%r)\n" % route
        if fail_marker:
            body += "print(%r)\nraise SystemExit(1)\n" % fail_marker
        (wt / "tools" / t).write_text(body, encoding="utf-8")
    fake_py = _tool(tmp_path / "fake_python",
                    '#!/bin/sh\nif [ "$1" = "-c" ]; then exit 0; fi\nexec %s "$@"\n' % PY)
    return wt, _env(tmp_path, BD_VENV_PY=str(fake_py))


def test_f11_equals_form_work_option_is_accepted(tmp_path):
    wt, env = _reindex_tree(tmp_path)
    res = _run(["bash", BIN / "bd-reindex", "--work=%s" % wt], env, timeout=120)
    assert res.returncode == 0, res.stdout + res.stderr
    assert "(OK)" in res.stdout


def test_f11_sanity_check_failure_fails_the_exit_code(tmp_path):
    wt, env = _reindex_tree(tmp_path, facing="false")
    res = _run(["bash", BIN / "bd-reindex", "--work", wt], env, timeout=120)
    assert "(CHECK)" in res.stdout
    assert res.returncode == 1, res.stdout + res.stderr
    assert "route-index-sanity" in res.stderr


def test_f11_generator_log_is_private_and_removed(tmp_path):
    marker = "REINDEX-MARKER-%s" % uuid.uuid4().hex
    wt, env = _reindex_tree(tmp_path, fail_marker=marker)
    res = _run(["bash", BIN / "bd-reindex", "--work", wt], env, timeout=120)
    assert res.returncode == 1
    assert marker in res.stdout, res.stdout
    shared = Path("/tmp/bd-reindex.log")
    if shared.is_file():
        try:
            assert marker not in shared.read_text(encoding="utf-8", errors="replace")
        except PermissionError:
            pass
    assert os.listdir(env["TMPDIR"]) == []


def test_f11_control_space_form_and_ok_sanity(tmp_path):
    wt, env = _reindex_tree(tmp_path)
    res = _run(["bash", BIN / "bd-reindex", "--work", wt], env, timeout=120)
    assert res.returncode == 0, res.stdout + res.stderr
    assert "(OK)" in res.stdout and "ERR" not in res.stdout


# ---------------------------------------------------------------- F12 bd-ci-wait

_GH = r'''echo "$*" >> "$GH_LOG"
if [ "$1" = "pr" ] && [ "$2" = "view" ]; then
  if [ -f "$GH_FAIL_SECOND" ]; then exit 1; fi
  [ -n "${GH_FAIL_AFTER_FIRST:-}" ] && touch "$GH_FAIL_SECOND"
  echo "deadbeef1234"; exit 0
fi
if [ "$1" = "run" ] && [ "$2" = "list" ]; then echo "999 completed success"; exit 0; fi
exit 1
'''


def _ciwait(tmp_path, *args, **extra):
    shim = tmp_path / "shim"
    _shim(shim, "gh", _GH)
    env = _env(tmp_path, PATH="%s:%s" % (shim, os.environ["PATH"]),
               GH_LOG=str(tmp_path / "gh.log"), GH_FAIL_SECOND=str(tmp_path / "fail2"), **extra)
    res = _run(["bash", BIN / "bd-ci-wait", *args], env, timeout=60)
    log = tmp_path / "gh.log"
    return res, (log.read_text(encoding="utf-8") if log.exists() else "")


def test_f12_help_prints_usage_and_never_calls_gh(tmp_path):
    res, calls = _ciwait(tmp_path, "--help")
    assert res.returncode == 0, res.stdout + res.stderr
    assert "usage: bd-ci-wait" in (res.stdout + res.stderr).lower()
    assert calls == "" and "pinned" not in res.stdout


@pytest.mark.parametrize("bad_max", ["0", "bogus", "-3"])
def test_f12_bad_max_is_a_usage_error(tmp_path, bad_max):
    res, calls = _ciwait(tmp_path, "42", bad_max)
    assert res.returncode == 2, res.stdout + res.stderr
    assert "MAX" in res.stderr and "TIMED OUT" not in res.stdout
    assert calls == ""


def test_f12_head_reread_failure_is_not_reported_as_moved(tmp_path):
    res, _calls = _ciwait(tmp_path, "42", "1", GH_FAIL_AFTER_FIRST="1")
    assert res.returncode == 2
    assert "head moved" not in res.stdout
    assert "cannot re-read the head of PR 42" in res.stdout, res.stdout


def test_f12_control_completed_success_exits_0(tmp_path):
    res, calls = _ciwait(tmp_path, "42", "1")
    assert res.returncode == 0, res.stdout + res.stderr
    assert "conclusion=success" in res.stdout and "pr view 42" in calls


# ---------------------------------------------------------------- F13 bd-sbcap

def _sbcap(tmp_path, *args, display_up=True):
    shim = tmp_path / "shim"
    real_git = shutil.which("git")
    if display_up:
        _shim(shim, "curl", "exit 0\n")        # display fast path: "already up"
    else:
        # noVNC not answering: the script walks the bring-up tail. pgrep reports every
        # daemon running and each daemon is a no-op shim, so nothing is ever spawned.
        _shim(shim, "curl", "exit 7\n")
        for name in ("pgrep", "Xvfb", "fluxbox", "x11vnc", "websockify"):
            _shim(shim, name, "exit 0\n")
        (tmp_path / "sbcap").mkdir()
        (tmp_path / "sbcap" / "x11vnc.passwd").write_text("pw\n", encoding="utf-8")
    _shim(shim, "apt-get", "exit 1\n")
    _shim(shim, "pip", "exit 1\n")
    _shim(shim, "git", '[ "$1" = clone ] && exit 1\nexec %s "$@"\n' % real_git)
    wt = _git_tree(tmp_path / "wt")           # no venv/: nothing to pip into
    env = _env(tmp_path, PATH="%s:%s" % (shim, os.environ["PATH"]),
               BD_SBCAP_ROOT=str(tmp_path / "sbcap"), BD_WORK_TREE=str(wt),
               BD_RENDER_KIT=str(tmp_path / "nokit"))
    return _run(["bash", BIN / "bd-sbcap", *args], env, timeout=180)


@pytest.mark.parametrize("display_up", [True, False], ids=["fast-path", "bring-up"])
def test_f13_default_mode_problems_fail_the_exit_code(tmp_path, display_up):
    res = _sbcap(tmp_path, display_up=display_up)
    assert ("already up" in res.stdout) is display_up, res.stdout
    xx = [line for line in res.stdout.splitlines() if "XX" in line]
    assert xx, res.stdout
    assert "== bd-sbcap: %d problem(s) ==" % len(xx) in res.stdout, res.stdout
    assert res.returncode == 1, res.stdout + res.stderr


def test_f13_empty_arg_is_refused(tmp_path):
    res = _sbcap(tmp_path, "--check", "")
    assert res.returncode == 2, res.stdout + res.stderr
    assert "unknown arg ''" in res.stderr


def test_f13_control_check_mode_still_exits_1(tmp_path):
    res = _sbcap(tmp_path, "--check")
    assert res.returncode == 1, res.stdout + res.stderr
    assert "== check done:" in res.stdout


# ---------------------------------------------------------------- F14 bd-venv

def test_f14_help_needs_no_work_tree(tmp_path):
    env = _env(tmp_path, BD_WORK_TREE=str(tmp_path / "nonexistent"))
    res = _run(["bash", BIN / "bd-venv", "--help"], env, timeout=60)
    assert res.returncode == 0, res.stderr
    assert "USAGE" in res.stdout and "UNRUNNABLE" not in res.stderr
    res = _run(["bash", BIN / "bd-venv", "--bogus"], env, timeout=60)
    assert res.returncode == 2 and "unknown arg '--bogus'" in res.stderr, res.stderr


def test_f14_control_valid_tree_help_and_bad_tree_check(tmp_path):
    res = _run(["bash", BIN / "bd-venv", "--help"], _env(tmp_path, BD_WORK_TREE=str(REPO)), timeout=60)
    assert res.returncode == 0 and "USAGE" in res.stdout, res.stderr
    env = _env(tmp_path, BD_WORK_TREE=str(tmp_path / "nonexistent"))
    res = _run(["bash", BIN / "bd-venv", "--check"], env, timeout=60)
    assert res.returncode == 2 and "BD-WORK-TREE-UNRUNNABLE" in res.stderr, res.stderr


# ---------------------------------------------------------------- F15 bd-selfcheck / bd-tools

@unprivileged
def test_f15_selfcheck_unreadable_tool_is_cannot_evaluate(tmp_path):
    b = tmp_path / "bin"
    b.mkdir()
    _tool(b / "bd-ok", "#!/usr/bin/env python3\nimport sys\ndef main(): return 0\n"
          "if __name__=='__main__': sys.exit(main())\n")
    unr = _tool(b / "bd-unreadable", "#!/usr/bin/env python3\nimport sys\n", mode=0)
    try:
        res = _run([PY, BIN / "bd-selfcheck", "--bin", b, "--pk", tmp_path / "pk"],
                   _env(tmp_path), timeout=120)
    finally:
        unr.chmod(0o644)
    assert "Traceback" not in res.stderr, res.stderr
    assert res.returncode == 2, res.stdout + res.stderr
    assert "CANNOT-EVALUATE" in res.stderr and "reason=UNREADABLE" in res.stderr
    assert "bd-unreadable" in res.stderr


def test_f15_control_selfcheck_readable_bin_is_sound(tmp_path):
    b = tmp_path / "bin"
    b.mkdir()
    _tool(b / "bd-ok", "#!/usr/bin/env python3\nimport sys\ndef main(): return 0\n"
          "if __name__=='__main__': sys.exit(main())\n")
    res = _run([PY, BIN / "bd-selfcheck", "--bin", b, "--pk", tmp_path / "pk"],
               _env(tmp_path), timeout=120)
    assert res.returncode == 0, res.stdout + res.stderr


def _catalog():
    tree = ast.parse((BIN / "bd-tools").read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.Assign) and getattr(node.targets[0], "id", "") == "CATEGORY":
            return sorted({t for v in ast.literal_eval(node.value).values() for t in v})
    raise AssertionError("bd-tools has no CATEGORY literal")


def _catalog_bin(tmp_path):
    b = tmp_path / "catbin"
    b.mkdir()
    for name in _catalog():
        _tool(b / name, "#!/bin/sh\n")
    return b


def _tools(tmp_path, b, *args):
    return _run([PY, BIN / "bd-tools", *args, "--bin", b], _env(tmp_path), timeout=120)


def test_f15_tools_check_json_blocks_uncategorized(tmp_path):
    b = _catalog_bin(tmp_path)
    _tool(b / "bd-uncategorized-probe", "#!/bin/sh\n")
    res = _tools(tmp_path, b, "--check", "--json")
    assert json.loads(res.stdout)["uncategorized"] == ["bd-uncategorized-probe"]
    assert res.returncode == 3, res.stdout + res.stderr


def test_f15_tools_directory_is_not_a_tool(tmp_path):
    b = _catalog_bin(tmp_path)
    (b / "bd-dir-entry").mkdir()
    res = _tools(tmp_path, b, "--check")
    assert res.returncode == 0, res.stdout + res.stderr
    payload = json.loads(_tools(tmp_path, b, "--json").stdout)
    assert "bd-dir-entry" not in payload["uncategorized"]


def test_f15_control_catalog_bin_checks_clean_and_json_listing_is_0(tmp_path):
    b = _catalog_bin(tmp_path)
    res = _tools(tmp_path, b, "--check")
    assert res.returncode == 0, res.stdout + res.stderr
    _tool(b / "bd-uncategorized-probe", "#!/bin/sh\n")
    assert _tools(tmp_path, b, "--check").returncode == 3
    listing = _tools(tmp_path, b, "--json")
    assert listing.returncode == 0, listing.stderr

"""Bug-hunt ct-fix (cli-toolchain sweep findings 1-6).

1  cli_dashboard crashed on /api/capacity {"disk": {"free_gb": null}} -- the
   shape capacity_report() sends on an install with no download dir.
2  cli_dashboard --once showed "no sites" and exited 0 when BD was down or
   answered 401/500.
3  cli_dashboard read queue.eta_minutes / bottleneck.kind; the API sends
   queue.eta_hours / bottleneck.bottleneck, so neither figure ever rendered.
4  bd-band / bd-precut --help needed a battery slot (refused rc 75).
5  bd-axtree-distill / bd-dag-schedule died with a traceback where their
   out-of-repo package is absent.
6  bd-timeit --help ran "--help" as a command (traceback).

Tools are run with --help or a trivial command only; the dashboard talks to a
stub on 127.0.0.1 or to a closed loopback port. No app, no DB.
"""
from __future__ import annotations

import http.server
import importlib.util
import io
import json
import os
import socket
import subprocess
import sys
import threading
from pathlib import Path

import pytest

from bulk_downloader import capacity, cli_dashboard

BD_GATE_SCOPE = "module"

REPO = Path(__file__).resolve().parents[1]
BIN = REPO / "toolchain" / "bin"


# ── helpers ───────────────────────────────────────────────────────────

def _panel_text(cap: dict) -> str:
    from rich.console import Console
    buf = io.StringIO()
    Console(file=buf, width=140, color_system=None).print(
        cli_dashboard._build_capacity_panel(cap))
    return buf.getvalue()


def _table_text(status: dict) -> str:
    from rich.console import Console
    buf = io.StringIO()
    Console(file=buf, width=140, color_system=None).print(
        cli_dashboard._build_site_table(status))
    return buf.getvalue()


def _plain(monkeypatch, capsys, status: dict, cap: dict):
    monkeypatch.setattr(cli_dashboard, "_fetch_status", lambda _b: status)
    monkeypatch.setattr(cli_dashboard, "_fetch_capacity", lambda _b: cap)
    rc = cli_dashboard._run_once_plain("http://127.0.0.1:9")
    return rc, capsys.readouterr().out


@pytest.fixture
def stub():
    """A loopback BD stand-in. stub.mode picks the /api/status answer."""
    state = {"mode": "ok", "cap": "ok"}

    class H(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a):  # noqa: D401 - silence
            pass

        def do_GET(self):
            path = self.path.split("?")[0]
            code, body, raw = 200, {}, None
            if path == "/api/status":
                if state["mode"] == "ok":
                    body = {"s1": {"name": "s1", "counts": {
                        "running": 1, "pending": 2, "done": 5}}}
                elif state["mode"] == "empty":
                    body = {}
                elif state["mode"] == "badjson":
                    raw = b"<html>not json</html>"
                elif state["mode"] == "list":
                    body = ["s1"]
                elif state["mode"] == "null":
                    body = None
                elif state["mode"].startswith("3"):
                    # a redirect status with NO Location header and an empty
                    # JSON object: requests does not follow it and .ok is True
                    code, body = int(state["mode"]), {}
                else:
                    code, body = int(state["mode"]), {"error": "nope"}
            elif path == "/api/capacity":
                body = {"disk": {"free_gb": 100.0}}
                if state["cap"] != "ok":
                    code = int(state["cap"])
            elif path == "/api/events_all":
                body = {"events": [{"ts": 1, "kind": "done", "site_id": "s1",
                                    "message": "EVENT-MARK"}]}
                if state["cap"] != "ok":
                    code = int(state["cap"])
            if raw is None:
                raw = json.dumps(body).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    state["url"] = f"http://127.0.0.1:{srv.server_address[1]}"
    try:
        yield state
    finally:
        srv.shutdown()
        srv.server_close()


def _closed_port_url() -> str:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return f"http://127.0.0.1:{port}"


def _run_tool(args, *, env_extra=None, timeout=120, cwd=None):
    env = {k: v for k, v in os.environ.items() if k != "BD_BATTERY_HELD"}
    env["LC_ALL"] = "C"
    env.update(env_extra or {})
    return subprocess.run([sys.executable, "-B", *map(str, args)],
                          capture_output=True, text=True, timeout=timeout,
                          env=env, cwd=cwd)


# ── finding 1: disk.free_gb null ──────────────────────────────────────

def test_f1_producer_really_sends_null_free_gb():
    """The premise: with no download dir the report carries free_gb None."""
    report = capacity.capacity_report({}, "")
    assert report["disk"]["free_gb"] is None
    assert "Disk: free space unknown" in _panel_text(report)


def test_f1_rich_panel_survives_null_free_gb():
    text = _panel_text({"disk": {"free_gb": None}})
    assert "Disk: free space unknown" in text
    assert "0 GB free" not in text          # unknown must not read as empty


def test_f1_plain_survives_null_free_gb(monkeypatch, capsys):
    rc, out = _plain(monkeypatch, capsys, {"sites": {}},
                     {"disk": {"free_gb": None}})
    assert rc == 0
    assert "  Disk: free space unknown" in out
    assert "GB free" not in out


@pytest.mark.parametrize("bad", ["12", True, float("nan"), [], {"a": 1}])
def test_f1_non_numeric_free_gb_is_unknown_not_a_crash(monkeypatch, capsys, bad):
    cap = {"disk": {"free_gb": bad}}
    assert "Disk: free space unknown" in _panel_text(cap)
    assert "Disk: free space unknown" in _plain(
        monkeypatch, capsys, {"sites": {}}, cap)[1]


def test_f1_measured_free_gb_unchanged(monkeypatch, capsys):
    cap = {"disk": {"free_gb": 100.0, "runway_days": 3.0}}
    assert "Disk: 100 GB free (3.0d runway)" in _panel_text(cap)
    assert "  Disk: 100 GB free" in _plain(
        monkeypatch, capsys, {"sites": {}}, cap)[1]
    assert "Disk: 0 GB free" in _panel_text({"disk": {"free_gb": 0}})


@pytest.mark.parametrize("cap", [{}, {"disk": None}, {"disk": "x"}, None, []])
def test_f1_absent_or_malformed_capacity_is_unavailable(cap):
    assert "(capacity data unavailable)" in _panel_text(cap)


# ── finding 2: BD down is not an empty install ────────────────────────

def test_f2_fetch_status_names_the_refusal(stub):
    for code in ("500", "401", "404"):
        stub["mode"] = code
        assert cli_dashboard._fetch_status(stub["url"]) == {
            "error": f"HTTP {code}"}
    err = cli_dashboard._fetch_status(_closed_port_url())
    assert set(err) == {"error"} and err["error"]


@pytest.mark.parametrize("code", ["300", "301", "302", "303", "307", "308"])
def test_f2_r2_unfollowed_redirect_is_not_a_successful_read(stub, code):
    """r2 (lens cx175): Response.ok is true below 400."""
    stub["mode"] = code
    assert cli_dashboard._fetch_status(stub["url"]) == {"error": f"HTTP {code}"}


@pytest.mark.parametrize("mode", ["badjson", "list", "null"])
def test_f2_r2_2xx_that_is_not_a_status_map_is_an_error(stub, mode):
    stub["mode"] = mode
    out = cli_dashboard._fetch_status(stub["url"])
    assert set(out) == {"error"} and out["error"], out


@pytest.mark.parametrize("code,want", [
    (200, True), (204, True), (299, True), (199, False), (300, False),
    (302, False), (399, False), (400, False), (500, False), ("200", True),
    ("abc", False), ([], False)])
def test_f2_r2_is_2xx_table(code, want):
    import types
    reply = types.SimpleNamespace(status_code=code, ok=True)
    assert cli_dashboard._is_2xx(reply) is want


def test_f2_r2_reply_without_a_status_code_is_judged_by_ok():
    import types
    assert cli_dashboard._is_2xx(types.SimpleNamespace(ok=True)) is True
    assert cli_dashboard._is_2xx(types.SimpleNamespace(ok=False)) is False
    assert cli_dashboard._is_2xx(types.SimpleNamespace()) is False


def test_f2_r2_timeout_is_an_error(monkeypatch):
    import requests

    def slow(*a, **k):
        raise requests.exceptions.ReadTimeout("read timed out")

    monkeypatch.setattr(requests, "get", slow)
    out = cli_dashboard._fetch_status("http://127.0.0.1:9")
    assert set(out) == {"error"} and "timed out" in out["error"]


@pytest.mark.parametrize("mode,needle", [
    ("300", "BD unreachable: HTTP 300"), ("302", "BD unreachable: HTTP 302"),
    ("badjson", "BD unreachable"), ("list", "BD unreachable")])
def test_f2_r2_once_exits_1_for_3xx_and_unreadable_2xx(stub, tmp_path, mode, needle):
    stub["mode"] = mode
    r = _run_tool(["-m", "bulk_downloader.cli_dashboard", "--once",
                   "--api", stub["url"]],
                  env_extra={"BD_HOME": str(tmp_path), "COLUMNS": "160",
                             "PYTHONPATH": str(REPO)}, cwd=tmp_path)
    assert r.returncode == 1, r.stdout + r.stderr
    assert needle in r.stdout
    assert "no sites configured" not in r.stdout
    assert "Traceback" not in r.stderr


def test_f2_r2_capacity_and_events_3xx_are_not_read(stub):
    """Same predicate at the two sibling fetchers: a 3xx body is not data."""
    assert cli_dashboard._fetch_capacity(stub["url"]) == {
        "disk": {"free_gb": 100.0}}
    assert cli_dashboard._fetch_events(stub["url"])[0]["message"] == "EVENT-MARK"
    stub["cap"] = "300"
    assert cli_dashboard._fetch_capacity(stub["url"]) == {}
    assert cli_dashboard._fetch_events(stub["url"]) == []


def test_f2_fetch_status_ok_and_empty_are_not_errors(stub):
    assert cli_dashboard._fetch_status(stub["url"])["sites"]["s1"]["name"] == "s1"
    stub["mode"] = "empty"
    assert cli_dashboard._fetch_status(stub["url"]) == {"sites": {}}


def test_f2_rich_table_says_unreachable():
    text = _table_text({"error": "HTTP 500"})
    assert "(BD unreachable: HTTP 500)" in text
    assert "no sites configured" not in text
    assert "(no sites configured)" in _table_text({"sites": {}})


def test_f2_plain_says_unreachable_and_returns_1(monkeypatch, capsys):
    rc, out = _plain(monkeypatch, capsys, {"error": "HTTP 401"}, {})
    assert rc == 1
    assert "  BD unreachable: HTTP 401" in out
    assert "Sites:" not in out
    rc, out = _plain(monkeypatch, capsys, {"sites": {}}, {})
    assert rc == 0 and "  Sites: 0" in out


@pytest.mark.parametrize("mode,rc,needle", [
    ("ok", 0, "s1"), ("empty", 0, "no sites configured"),
    ("500", 1, "BD unreachable: HTTP 500"),
    ("401", 1, "BD unreachable: HTTP 401")])
def test_f2_once_exit_code(stub, tmp_path, mode, rc, needle):
    stub["mode"] = mode
    r = _run_tool(["-m", "bulk_downloader.cli_dashboard", "--once",
                   "--api", stub["url"]],
                  env_extra={"BD_HOME": str(tmp_path), "COLUMNS": "160",
                             "PYTHONPATH": str(REPO)}, cwd=tmp_path)
    assert r.returncode == rc, r.stdout + r.stderr
    assert needle in r.stdout
    assert "Traceback" not in r.stderr


def test_f2_once_closed_port_exits_1(tmp_path):
    r = _run_tool(["-m", "bulk_downloader.cli_dashboard", "--once",
                   "--api", _closed_port_url()],
                  env_extra={"BD_HOME": str(tmp_path), "COLUMNS": "160",
                             "PYTHONPATH": str(REPO)}, cwd=tmp_path)
    assert r.returncode == 1, r.stdout + r.stderr
    assert "BD unreachable" in r.stdout
    assert "no sites configured" not in r.stdout


# ── finding 3: the keys /api/capacity really sends ────────────────────

def test_f3_producer_key_names():
    """Pin the contract the panel reads, from the producer itself."""
    report = capacity.capacity_report({}, "")
    assert "eta_hours" in report["queue"]
    assert "eta_minutes" not in report["queue"]
    assert "bottleneck" in report["bottleneck"]
    assert "kind" not in report["bottleneck"]


def test_f3_real_shape_renders_eta_and_bottleneck():
    cap = {"disk": {"free_gb": 100.0, "runway_days": 3.0},
           "queue": {"eta_hours": 2.5, "pending": 10},
           "bottleneck": {"bottleneck": "rate_limit",
                          "detail": "429", "severity": 2}}
    text = _panel_text(cap)
    assert "Queue ETA: 2.5h" in text
    assert "Bottleneck: rate_limit" in text
    assert "Disk: 100 GB free (3.0d runway)" in text


def test_f3_eta_units_and_unknowns():
    assert "Queue ETA: 30m" in _panel_text({"queue": {"eta_hours": 0.5}})
    assert "Queue ETA: 0m" in _panel_text({"queue": {"eta_hours": 0.0}})
    for unknown in (None, "soon", float("inf")):
        assert "Queue ETA" not in _panel_text(
            {"disk": {"free_gb": 1.0}, "queue": {"eta_hours": unknown}})


def test_f3_idle_is_not_a_bottleneck():
    text = _panel_text({"disk": {"free_gb": 1.0}, "bottleneck": {
        "bottleneck": "idle", "detail": "no active load", "severity": 0}})
    assert "Bottleneck" not in text
    assert "Bottleneck: disk" in _panel_text(
        {"bottleneck": {"bottleneck": "disk"}})


# ── finding 4: --help needs no battery slot ───────────────────────────

def _battery():
    spec = importlib.util.spec_from_file_location(
        "bd_battery_under_test", BIN / "bd_battery.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.mark.parametrize("argv,takes_slot", [
    (["--help"], False), (["-h"], False), (["tests/x.py", "--help"], False),
    (["tests/x.py"], True), ([], True),
    (["tests/x.py", "--", "-h"], True), (["--", "--help"], True),
    (["--helpful"], True)])
def test_f4_help_skips_the_slot_and_only_help(tmp_path, monkeypatch, argv,
                                              takes_slot):
    bat = _battery()
    monkeypatch.setattr(bat, "LOCK_BASE", str(tmp_path / "lock"))
    monkeypatch.delenv("BD_BATTERY_HELD", raising=False)
    monkeypatch.setenv("BD_BATTERY_MAX", "1")
    seen = {}

    @bat.battery_limited
    def main(argv=None):
        seen["held"] = os.environ.get("BD_BATTERY_HELD")
        return 7

    assert main(argv) == 7
    assert (tmp_path / "lock.0").exists() is takes_slot
    assert (seen["held"] is not None) is takes_slot


def test_f4_help_reads_sys_argv_when_main_gets_none(tmp_path, monkeypatch):
    bat = _battery()
    monkeypatch.setattr(bat, "LOCK_BASE", str(tmp_path / "lock"))
    monkeypatch.delenv("BD_BATTERY_HELD", raising=False)
    monkeypatch.setenv("BD_BATTERY_MAX", "0")       # invalid: would refuse
    wrapped = bat.battery_limited(lambda argv=None: 0)
    monkeypatch.setattr(sys, "argv", ["bd-band", "--help"])
    assert wrapped() == 0
    monkeypatch.setattr(sys, "argv", ["bd-band", "tests/x.py"])
    assert wrapped() == bat.REFUSED                  # the control still refuses


@pytest.mark.parametrize("tool", ["bd-band", "bd-precut"])
def test_f4_tool_help_with_no_usable_slots(tool, tmp_path):
    r = _run_tool([BIN / tool, "--help"], env_extra={"BD_BATTERY_MAX": "0"},
                  cwd=tmp_path)
    assert r.returncode == 0, r.stdout + r.stderr
    assert f"usage: {tool}" in r.stdout
    assert "REFUSED-QUEUE" not in r.stdout + r.stderr


def test_f4_tool_run_with_no_usable_slots_still_refuses(tmp_path):
    r = _run_tool([BIN / "bd-band", "tests/test_does_not_exist.py"],
                  env_extra={"BD_BATTERY_MAX": "0"}, cwd=tmp_path)
    assert r.returncode == 75
    assert "REFUSED-QUEUE" in r.stderr


# ── finding 5: hub-only tools say so ──────────────────────────────────

_BLOCK_DRIVER = r"""
import runpy, sys
BLOCKED = set(sys.argv[2].split(","))
class _Absent:
    def find_spec(self, name, path=None, target=None):
        if name.split(".")[0] in BLOCKED:
            raise ImportError(f"No module named {name!r}")
        return None
sys.meta_path.insert(0, _Absent())
tool = sys.argv[1]
sys.argv = [tool] + sys.argv[3:]
runpy.run_path(tool, run_name="__main__")
"""

_HUB_ONLY = [
    ("bd-axtree-distill", "axtree_distill", {
        "distiller": "def distill_axtree(*a, **k): return []\n"
                     "def serialize_axtree(*a, **k): return ''\n",
        "models": "class AXNode: pass\n"}),
    ("bd-dag-schedule", "dag_task_compiler", {
        "compiler": "class DAGCompiler: pass\n",
        "models": "class TaskDAG: pass\nclass TaskNode: pass\n",
        "scheduler": "class LeafDispatcher: pass\n"}),
]


@pytest.mark.parametrize("tool,pkg,_mods", _HUB_ONLY)
@pytest.mark.parametrize("args", [["--help"], []])
def test_f5_absent_package_is_a_clean_refusal(tool, pkg, _mods, args, tmp_path):
    r = _run_tool(["-c", _BLOCK_DRIVER, BIN / tool, pkg, *args], cwd=tmp_path)
    assert r.returncode == 69, r.stdout + r.stderr
    assert f"{tool}: HUB-ONLY tool, unavailable on this host" in r.stderr
    assert pkg in r.stderr
    assert "Traceback" not in r.stderr


@pytest.mark.parametrize("tool,pkg,mods", _HUB_ONLY)
def test_f5_present_package_still_gives_usage(tool, pkg, mods, tmp_path):
    root = tmp_path / "site" / pkg
    root.mkdir(parents=True)
    (root / "__init__.py").write_text("")
    for name, src in mods.items():
        (root / f"{name}.py").write_text(src)
    r = _run_tool([BIN / tool, "--help"], cwd=tmp_path,
                  env_extra={"PYTHONPATH": str(tmp_path / "site")})
    assert r.returncode == 0, r.stdout + r.stderr
    assert "usage:" in r.stdout
    assert "HUB-ONLY" not in r.stderr


# ── finding 6: bd-timeit option handling ──────────────────────────────

@pytest.mark.parametrize("flag", ["--help", "-h"])
def test_f6_help_prints_usage(flag, tmp_path):
    r = _run_tool([BIN / "bd-timeit", flag], cwd=tmp_path)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "Usage: bd-timeit [--label L] -- <command ...>" in r.stdout
    assert "Traceback" not in r.stderr


@pytest.mark.parametrize("args,needle", [
    (["--bogus"], "unknown option '--bogus'"),
    (["--label"], "--label needs a value"),
    (["--label", "L", "-x", "true"], "unknown option '-x'")])
def test_f6_bad_options_are_usage_errors(args, needle, tmp_path):
    r = _run_tool([BIN / "bd-timeit", *args], cwd=tmp_path)
    assert r.returncode == 2
    assert needle in r.stderr
    assert "usage: bd-timeit" in r.stderr
    assert "Traceback" not in r.stderr


def test_f6_missing_command_is_127_not_a_traceback(tmp_path):
    r = _run_tool([BIN / "bd-timeit", "--", "bd-no-such-command-xyz"],
                  cwd=tmp_path)
    assert r.returncode == 127
    assert "cannot run 'bd-no-such-command-xyz'" in r.stderr
    assert "Traceback" not in r.stderr


def test_f6_wrapped_command_contract_unchanged(tmp_path):
    code = "import sys; sys.exit(3)"
    r = _run_tool([BIN / "bd-timeit", "--label", "L", "--",
                   sys.executable, "-c", code], cwd=tmp_path)
    assert r.returncode == 3
    assert "L:" in r.stdout and "(exit 3)" in r.stdout
    # A command whose own flags follow "--" is run, not parsed as ours.
    r = _run_tool([BIN / "bd-timeit", "--", sys.executable, "--version"],
                  cwd=tmp_path)
    assert r.returncode == 0 and "(exit 0)" in r.stdout
    # No "--": the first word is the command, as before.
    r = _run_tool([BIN / "bd-timeit", sys.executable, "-c", "pass"],
                  cwd=tmp_path)
    assert r.returncode == 0 and "(exit 0)" in r.stdout

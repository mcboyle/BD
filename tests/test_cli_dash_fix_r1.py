"""cli-dash-fix r1: deep-cli-toolchain F4 F5 F7 + deep-adapters-browser F5.

F4  terminal_dashboard / cli_dashboard.run_live: sort and filter reach the
    table (S05), [r] polls while paused (S06), a whole escape sequence is one
    key (S07).
F5  shell_completion: registered completers and option choices complete an
    option's value, which is never taken for a subcommand (S10, S11); add_parser
    help reaches the fish script (S12).
F7  cli_dashboard: --refresh is a finite positive number (S01); an event with
    an out-of-range ts or a null entry renders (S02, S03). S04 is closed on BASE
    (control only).
A5  scrapling_adapter._fetch_legacy_bounded: a timed-out legacy fetch is never
    joined by a second one.
"""
from __future__ import annotations

import contextlib
import io
import math
import os
import pty
import socket
import subprocess
import sys
import threading
import time
import types
import argparse

import pytest

from bulk_downloader import cli_dashboard, scrapling_adapter, shell_completion, terminal_dashboard

BD_GATE_SCOPE = "module"

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _status(**running):
    return {"sites": {sid: {"name": sid.capitalize(), "counts": {"running": n}}
                      for sid, n in running.items()}}


def _site_rows(frame: str) -> list[str]:
    """Site names in a render_plain_frame SITES table, top to bottom."""
    rows = []
    for line in frame.splitlines():
        body = line[2:] if line[:2] in ("> ", "  ") else ""
        name = body[:18].strip()
        if name in ("Alpha", "Beta", "Gamma"):
            rows.append(name)
    return rows


# ───────────────────────── F4 S05: sort / filter ─────────────────────────
def test_f4_s05_sort_key_orders_the_table_and_the_cursor_keeps_its_site():
    ctrl = terminal_dashboard.TerminalDashboardController()
    ctrl.assemble_snapshot(_status(alpha=1, beta=10, gamma=5), {}, [])
    assert ctrl.current_site_ids[ctrl.state.selected_site_index] == "alpha"
    ctrl.handle_key("s")
    assert ctrl.state.sort_field == terminal_dashboard.SortField.RUNNING
    assert _site_rows(ctrl.render_plain_frame()) == ["Beta", "Gamma", "Alpha"]
    assert ctrl.current_site_ids == ["beta", "gamma", "alpha"]
    # the cursor stayed on alpha, so [x] acts on the site the operator chose
    sent = []
    ctrl.execute_site_action = lambda action, sid: sent.append((action, sid)) or True
    ctrl.handle_key("x")
    assert sent == [("pause", "alpha")]
    ctrl.state.sort_reverse = True
    assert _site_rows(ctrl.render_plain_frame()) == ["Alpha", "Gamma", "Beta"]


def test_f4_s05_filter_hides_other_sites_and_c_clears_it():
    ctrl = terminal_dashboard.TerminalDashboardController()
    ctrl.assemble_snapshot(_status(alpha=1, beta=10), {}, [])
    ctrl.state.set_filter("BET")
    frame = ctrl.render_plain_frame()
    assert _site_rows(frame) == ["Beta"] and "Alpha" not in frame
    ctrl.handle_key("j")
    assert ctrl.current_site_ids == ["beta"]
    ctrl.handle_key("c")
    assert _site_rows(ctrl.render_plain_frame()) == ["Alpha", "Beta"]


def test_f4_s05_rich_frame_uses_the_same_order():
    pytest.importorskip("rich")
    from rich.console import Console
    ctrl = terminal_dashboard.TerminalDashboardController()
    ctrl.assemble_snapshot(_status(alpha=1, beta=10), {}, [])
    ctrl.handle_key("s")
    buf = io.StringIO()
    Console(file=buf, width=120, height=30, color_system=None).print(ctrl.render_rich_frame())
    text = buf.getvalue()
    assert text.index("Beta") < text.index("Alpha")
    assert ctrl.current_site_ids == ["beta", "alpha"]  # j/k/x walk the rows drawn


def test_f4_control_name_sort_and_j_move():
    ctrl = terminal_dashboard.TerminalDashboardController()
    ctrl.assemble_snapshot(_status(beta=10, alpha=1), {}, [])
    assert _site_rows(ctrl.render_plain_frame()) == ["Alpha", "Beta"]
    ctrl.handle_key("j")
    assert ctrl.state.selected_site_index == 1


# ───────────────────────── F4 S06: [r] while paused ─────────────────────────
def _run_live_with_keys(monkeypatch, keys, refresh_seconds=1000.0):
    polls = []
    script = list(keys)

    @contextlib.contextmanager
    def fake_reader():
        yield lambda timeout: script.pop(0) if script else "q"

    class FakeLive:
        def __init__(self, *a, **k):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def update(self, renderable):
            pass

    monkeypatch.setattr(cli_dashboard, "_HAS_RICH", True)
    monkeypatch.setattr(cli_dashboard, "Console", lambda *a, **k: None, raising=False)
    monkeypatch.setattr(cli_dashboard, "Live", FakeLive, raising=False)
    monkeypatch.setattr(cli_dashboard, "terminal_key_reader", fake_reader)
    monkeypatch.setattr(cli_dashboard, "_fetch_status", lambda api: polls.append(api) or _status(alpha=1))
    monkeypatch.setattr(cli_dashboard, "_fetch_capacity", lambda api: {})
    monkeypatch.setattr(cli_dashboard, "_fetch_events", lambda api: [])
    made = []
    real = terminal_dashboard.TerminalDashboardController

    def make(**kw):
        made.append(real(**kw))
        return made[-1]

    monkeypatch.setattr(cli_dashboard, "TerminalDashboardController", make)
    cli_dashboard.run_live(api_base="http://fixture.invalid", refresh_seconds=refresh_seconds)
    return polls, made[0]


def test_f4_s06_r_polls_while_paused(monkeypatch):
    polls, dash = _run_live_with_keys(monkeypatch, ["p", "r", "q"])
    assert dash.state.paused is True
    assert len(polls) == 2, "[r] while paused must poll once"
    assert dash.refresh_requested is False


def test_f4_control_paused_timer_holds_and_unpaused_r_polls(monkeypatch):
    polls, _ = _run_live_with_keys(monkeypatch, ["r", "q"])
    assert len(polls) == 2
    # paused: the timed refresh (0.01 s) does not poll
    polls, dash = _run_live_with_keys(monkeypatch, ["p", None, None, None, "q"], refresh_seconds=0.01)
    assert dash.state.paused is True and len(polls) == 1


# ───────────────────────── F4 S07: escape sequences ─────────────────────────
def _keys_from_pty(data: bytes, stop: str = "j", limit: int = 12) -> list:
    master, slave = pty.openpty()
    try:
        stream = types.SimpleNamespace(fileno=lambda: slave)
        with terminal_dashboard.terminal_key_reader(stream) as read_key:
            os.write(master, data)
            keys = []
            for _ in range(limit):
                key = read_key(0.5)
                keys.append(key)
                if key == stop:
                    break
            return keys
    finally:
        os.close(master)
        os.close(slave)


@pytest.mark.parametrize("seq", [b"\x1b[1;5x", b"\x1b[5~", b"\x1b[200~", b"\x1bx"])
def test_f4_s07_a_whole_escape_sequence_is_one_key(seq):
    keys = _keys_from_pty(seq + b"j")
    assert keys == [None, "j"], f"{seq!r} leaked keys {keys!r}"


def test_f4_control_arrows_still_map():
    assert _keys_from_pty(b"\x1b[A\x1b[Bj") == ["up", "down", "j"]
    assert _keys_from_pty(b"\x1bOPj") == [None, "j"]  # a 2-byte SS3 key: whole on BASE too


# ───────────────────────── F5 shell completion ─────────────────────────
def _parser():
    p = argparse.ArgumentParser(prog="bdctl")
    p.add_argument("--site")
    p.add_argument("--format", choices=["json", "table"])
    p.add_argument("--json", action="store_true")
    sub = p.add_subparsers(dest="cmd")
    run = sub.add_parser("run", help="run downloads")
    run.add_argument("--limit", type=int)
    sub.add_parser("status", description="show status")
    sub.add_parser("library", aliases=["lib"], help="library tools")
    return p


@pytest.mark.parametrize("name", ["--site", "site"])
def test_f5_s10_a_registered_completer_completes_the_option_value(name):
    eng = shell_completion.ShellCompletionEngine(_parser())
    eng.register_dynamic_completer(name, lambda: ["alpha", "beta"])
    assert eng.complete_command_line("bdctl --site ") == ["alpha", "beta"]
    assert eng.complete_command_line("bdctl --site a") == ["alpha"]


def test_f5_s11_choices_complete_and_an_option_value_is_not_a_subcommand():
    eng = shell_completion.ShellCompletionEngine(_parser())
    assert eng.complete_command_line("bdctl --format ") == ["json", "table"]
    assert eng.complete_command_line("bdctl --format t") == ["table"]
    assert eng.complete_command_line("bdctl --site ") == []
    # "run" is --site's value: still at the top level, no --limit
    assert eng.complete_command_line("bdctl --site run --") == ["--format", "--help", "--json", "--site"]
    assert eng.complete_command_line("bdctl --site run ") == sorted(
        ["run", "status", "library", "lib", "--format", "--help", "--json", "--site", "-h"])


def test_f5_control_flags_and_subcommands_complete_as_before():
    eng = shell_completion.ShellCompletionEngine(_parser())
    assert eng.complete_command_line("bdctl run --") == ["--help", "--limit"]
    assert eng.complete_command_line("bdctl --json run --") == ["--help", "--limit"]
    assert eng.complete_command_line("bdctl r") == ["run"]


def test_f5_s12_add_parser_help_reaches_fish():
    fish = shell_completion.generate_shell_completion(_parser(), "fish")
    assert "-a 'run' -d 'run downloads'" in fish
    assert "-a 'library' -d 'library tools'" in fish
    assert "-a 'lib' -d 'library tools'" in fish


def test_f5_control_description_reaches_fish():
    fish = shell_completion.generate_shell_completion(_parser(), "fish")
    assert "-a 'status' -d 'show status'" in fish


# ───────────────────────── F7 cli_dashboard ─────────────────────────
@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf"), -1.0, 1e300, "x"])
def test_f7_s01_run_live_refuses_a_refresh_it_cannot_wait_out(monkeypatch, value):
    # the fakes quit at once, so a run_live that took it returns instead
    with pytest.raises(ValueError):
        _run_live_with_keys(monkeypatch, ["q"], refresh_seconds=value)


@pytest.mark.parametrize("text", ["nan", "inf", "-inf", "-1", "0", "1e300", "x"])
def test_f7_s01_refresh_arg_must_be_finite_and_positive(text):
    with pytest.raises(argparse.ArgumentTypeError):
        cli_dashboard.refresh_seconds_arg(text)


def _closed_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _cli(tmp_path, *args):
    env = dict(os.environ, HOME=str(tmp_path), BD_HOME=str(tmp_path / ".bd"), PYTHONDONTWRITEBYTECODE="1")
    return subprocess.run([sys.executable, "-B", "-m", "bulk_downloader.cli_dashboard", *args],
                          cwd=REPO, env=env, capture_output=True, text=True, timeout=120)


def test_f7_s01_cli_refuses_nan_refresh(tmp_path):
    r = _cli(tmp_path, "--once", "--refresh", "nan", "--api", f"http://127.0.0.1:{_closed_port()}")
    assert r.returncode == 2 and "--refresh" in r.stderr, (r.returncode, r.stderr[-400:])


def test_f7_control_run_live_takes_zero_refresh(monkeypatch):
    polls, _ = _run_live_with_keys(monkeypatch, [None, "q"], refresh_seconds=0)
    assert len(polls) == 2  # row985's tests drive the loop with 0: a poll per pass


def test_f7_control_cli_valid_refresh_runs(tmp_path):
    r = _cli(tmp_path, "--once", "--refresh", "0.5", "--api", f"http://127.0.0.1:{_closed_port()}")
    assert r.returncode == 1 and "--refresh" not in r.stderr, (r.returncode, r.stdout[-400:], r.stderr[-400:])


def _render(table) -> str:
    from rich.console import Console
    buf = io.StringIO()
    Console(file=buf, width=160, color_system=None).print(table)
    return buf.getvalue()


@pytest.mark.parametrize("ts", [1e20, float("nan"), -1e20])
def test_f7_s02_out_of_range_ts_renders(ts):
    pytest.importorskip("rich")
    text = _render(cli_dashboard._build_events_table(
        [{"ts": ts, "site_id": "s1", "kind": "done", "message": "m1"}]))
    assert "m1" in text


def test_f7_s03_null_and_non_text_events_render():
    pytest.importorskip("rich")
    table = cli_dashboard._build_events_table(
        [None, ["x"], {"ts": 1700000000, "site_id": 5, "kind": 3, "message": 7}])
    text = _render(table)
    assert table.row_count == 3
    assert "(unreadable event: NoneType)" in text and "(unreadable event: list)" in text


def test_f7_control_event_row_and_s04_closed_capacity():
    pytest.importorskip("rich")
    text = _render(cli_dashboard._build_events_table(
        [{"ts": 1700000000, "site_id": "s1", "kind": "done", "message": "m1"}]))
    assert time.strftime("%H:%M:%S", time.localtime(1700000000)) in text and "m1" in text
    for cap in ([1], {"bottleneck": None}, {"disk": {"free_gb": 5, "runway_days": "x"}}):
        cli_dashboard._build_capacity_panel(cap)  # S04: closed on BASE


# ───────────────────────── A5 scrapling legacy fetch ─────────────────────────
def _legacy_fetcher(release, calls):
    def fetch(*args, **kwargs):
        if kwargs:
            raise TypeError("fixture only accepts positional url")
        calls.append(args)
        release.wait(10.0)
        return types.SimpleNamespace(html_content="<main>gallery</main>", cookies=[])

    return types.SimpleNamespace(StealthyFetcher=types.SimpleNamespace(fetch=fetch))


def _legacy_threads():
    return [t for t in threading.enumerate() if t.name == "scrapling-legacy-fetch" and t.is_alive()]


def _drain(release):
    release.set()
    for t in _legacy_threads():
        t.join(5.0)


def test_a5_a_timed_out_legacy_fetch_is_not_joined_by_another(monkeypatch):
    release, calls = threading.Event(), []
    monkeypatch.setitem(sys.modules, "scrapling", _legacy_fetcher(release, calls))
    monkeypatch.setattr(scrapling_adapter, "is_stealthy_fetcher_available", lambda: True)
    try:
        first = scrapling_adapter.bypass_turnstile("https://fixture.test/a", timeout_s=0.2)
        second = scrapling_adapter.bypass_turnstile("https://fixture.test/b", timeout_s=0.2)
        assert first.error == "legacy_fetch_timeout"
        assert len(calls) == 1 and len(_legacy_threads()) == 1, (
            f"each timed-out bypass left one more fetch: calls={len(calls)} threads={len(_legacy_threads())}")
        assert second.ok is False and second.error == "legacy_fetch_busy"
    finally:
        _drain(release)


def test_a5_control_a_fetch_that_ends_inside_the_budget_lets_the_next_run(monkeypatch):
    release, calls = threading.Event(), []
    monkeypatch.setitem(sys.modules, "scrapling", _legacy_fetcher(release, calls))
    monkeypatch.setattr(scrapling_adapter, "is_stealthy_fetcher_available", lambda: True)
    try:
        assert scrapling_adapter.bypass_turnstile("https://fixture.test/a", timeout_s=0.2).error == "legacy_fetch_timeout"
        threading.Timer(0.2, release.set).start()
        result = scrapling_adapter.bypass_turnstile("https://fixture.test/b", timeout_s=5.0)
        assert result.ok is True and len(calls) == 2
    finally:
        _drain(release)
    assert math.isfinite(result.elapsed_s) and result.elapsed_s < 5.0


# ───────────────────────── r2 (R201-C REFUTE) ─────────────────────────
def _sleeping_fetcher(delay):
    def fetch(*args, **kwargs):
        if kwargs:
            raise TypeError("fixture only accepts positional url")
        time.sleep(delay)
        return types.SimpleNamespace(html_content="<main>gallery</main>", cookies=[])
    return types.SimpleNamespace(StealthyFetcher=types.SimpleNamespace(fetch=fetch))


@pytest.mark.parametrize("callers,delay", [(2, 0.6), (3, 0.4)])
def test_a5_r2_healthy_concurrent_fetches_run_side_by_side(monkeypatch, callers, delay):
    # R201-C F1: only a fetch that outlived its budget may gate the next one; healthy
    # concurrent bypasses (each inside its 1.0 s budget; R201-C CASE1, CASE2) all succeed, as on BASE.
    monkeypatch.setitem(sys.modules, "scrapling", _sleeping_fetcher(delay))
    monkeypatch.setattr(scrapling_adapter, "is_stealthy_fetcher_available", lambda: True)
    out = [None] * callers

    def go(i):
        r = scrapling_adapter.bypass_turnstile(f"https://fixture{i}.test/", timeout_s=1.0)
        out[i] = (r.ok, r.error)

    threads = [threading.Thread(target=go, args=(i,)) for i in range(callers)]
    for t in threads:
        t.start()
        time.sleep(0.02)
    for t in threads:
        t.join(10.0)
    assert out == [(True, "")] * callers, f"a healthy fetch gated the others: {out}"


_HANGUP_PROBE = r"""
import os, pty, signal, sys, threading, time, types
sys.path.insert(0, sys.argv[1])
from bulk_downloader import terminal_dashboard
signal.signal(signal.SIGHUP, signal.SIG_IGN)
master, slave = pty.openpty()
res = []
with terminal_dashboard.terminal_key_reader(types.SimpleNamespace(fileno=lambda: slave)) as read_key:
    t = threading.Thread(target=lambda: res.append(read_key(2.0)), daemon=True)
    t.start()
    time.sleep(0.05)
    os.write(master, bytes.fromhex(sys.argv[2]))
    time.sleep(0.01)
    os.close(master)
    t.join(3.0)
    print("HANG" if t.is_alive() else "returned %r" % (res,), flush=True)
    os._exit(0)
"""


def _read_key_after_hangup(data: bytes) -> str:
    r = subprocess.run([sys.executable, "-B", "-c", _HANGUP_PROBE, REPO, data.hex()],
                       capture_output=True, text=True, timeout=30)
    return r.stdout.strip() or r.stderr.strip()[-300:]


@pytest.mark.parametrize("data", [b"\x1b", b"\x1b["])
def test_f4_r2_hangup_inside_an_escape_sequence_returns(data):
    # R201-C F2: the escape loop must stop on an empty read (pty hangup), not spin.
    assert _read_key_after_hangup(data) == "returned [None]", f"read_key spun after hangup on {data!r}"


def test_f4_control_r2_key_then_hangup_returns_the_key():
    assert _read_key_after_hangup(b"j") == "returned ['j']"

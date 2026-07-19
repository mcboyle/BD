"""v3.66.108 — debug log console, opt-in interactive shell, top-bar layout.

The shell is a DELIBERATE departure from recognition-only, enabled by explicit
operator decision. These tests prove it is SAFELY GATED and ISOLATED:
  * ON by default (v3.66.183); BD_COCKPIT_SHELL=0 hard-disables every entry point
  * cockpit_core never imports it — the recognition allowlist guarantee
    (test_v3_66_98) is untouched and still holds
  * when enabled it runs real commands and audits them
The debug log console is read-only and redacted (lines that trip posture_scan are
withheld). The layout toggle is pure front-end.
"""
import os
import re
import sys
import time
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from tools import cockpit_core as cc
from tools import cockpit_shell as sh

_CONSOLE_SRC = (_ROOT / "tools" / "cockpit_console.py").read_text()
_CORE_SRC = (_ROOT / "tools" / "cockpit_core.py").read_text()


@pytest.fixture(autouse=True)
def _roots(tmp_path, monkeypatch):
    for s in ("cap", "rep", "task"):
        (tmp_path / s).mkdir()
    monkeypatch.setenv("BD_CAPTURES_ROOT", str(tmp_path / "cap"))
    monkeypatch.setenv("BD_FRAMEWORK_REPORTS", str(tmp_path / "rep"))
    monkeypatch.setenv("BD_COCKPIT_TASKS", str(tmp_path / "task"))
    monkeypatch.delenv("BD_COCKPIT_SHELL", raising=False)  # v3.66.183: default ON
    yield


# ── the recognition allowlist guarantee is NOT weakened by the shell ──────────

class TestShellIsolatedFromCore:
    def test_core_does_not_import_shell(self):
        assert "cockpit_shell" not in _CORE_SRC

    def test_core_still_has_no_shell_true(self):
        # the v98 contract: the recognition surface never uses shell=True
        assert "shell=True" not in _CORE_SRC

    def test_console_lazy_imports_shell_only_in_handlers(self):
        # the shell is imported inside route handlers, never at module top level
        top = _CONSOLE_SRC.split("def ", 1)[0]
        assert "import cockpit_shell" not in top
        assert "from tools import cockpit_shell" in _CONSOLE_SRC  # but present in handlers


# ── the shell is ON by default (v3.66.183); BD_COCKPIT_SHELL=0 opts out ──────

class TestShellOnByDefault:
    """v3.66.183: default-on. With no BD_COCKPIT_SHELL set, the shell is enabled
    (pty permitting). This reverses the pre-183 off-by-default contract; the
    opt-out path is covered by TestShellOptOut."""

    def test_status_enabled_by_default(self):
        # enabled iff pty is available (the only remaining hard gate)
        assert sh.shell_enabled() is sh._PTY_OK
        assert sh.shell_status()["enabled"] is sh._PTY_OK

    @pytest.mark.skipif(not sh._PTY_OK, reason="pty unavailable on this OS")
    def test_open_succeeds_by_default(self):
        sid = sh.shell_open()["session"]
        assert sid
        sh.shell_close(sid)

    @pytest.mark.skipif(not sh._PTY_OK, reason="pty unavailable on this OS")
    def test_endpoint_open_ok_by_default(self):
        from flask import Flask
        from tools.cockpit_console import bp
        app = Flask(__name__); app.register_blueprint(bp); c = app.test_client()
        r = c.post("/cockpit/api/shell/open", json={})
        assert r.status_code == 200
        sid = r.get_json().get("session")
        assert sid
        assert c.get("/cockpit/api/shell/status").get_json()["enabled"] is True
        c.post("/cockpit/api/shell/close", json={"session": sid})


class TestShellOptOut:
    """BD_COCKPIT_SHELL=0 hard-disables every entry point (the opt-out gate)."""

    def test_status_disabled_when_opted_out(self, monkeypatch):
        monkeypatch.setenv("BD_COCKPIT_SHELL", "0")
        assert sh.shell_enabled() is False
        assert sh.shell_status()["enabled"] is False

    def test_open_refused_when_opted_out(self, monkeypatch):
        monkeypatch.setenv("BD_COCKPIT_SHELL", "0")
        with pytest.raises(sh.ShellError):
            sh.shell_open()

    def test_input_refused_when_opted_out(self, monkeypatch):
        monkeypatch.setenv("BD_COCKPIT_SHELL", "0")
        with pytest.raises(sh.ShellError):
            sh.shell_input("anything", "ls\n")

    def test_poll_refused_when_opted_out(self, monkeypatch):
        monkeypatch.setenv("BD_COCKPIT_SHELL", "0")
        with pytest.raises(sh.ShellError):
            sh.shell_poll("anything", 0)

    def test_endpoint_returns_403_when_opted_out(self, monkeypatch):
        monkeypatch.setenv("BD_COCKPIT_SHELL", "0")
        from flask import Flask
        from tools.cockpit_console import bp
        app = Flask(__name__); app.register_blueprint(bp); c = app.test_client()
        assert c.post("/cockpit/api/shell/open", json={}).status_code == 403
        assert c.get("/cockpit/api/shell/status").get_json()["enabled"] is False


# ── when explicitly enabled it works and audits ─────────────────────────────

class TestShellWhenEnabled:
    @pytest.mark.skipif(not sh._PTY_OK, reason="pty unavailable on this OS")
    def test_runs_command_and_audits(self, monkeypatch, tmp_path):
        monkeypatch.setenv("BD_COCKPIT_SHELL", "1")
        assert sh.shell_enabled() is True
        sid = sh.shell_open()["session"]
        try:
            sh.shell_input(sid, "echo BD_SHELL_PROOF\n")
            # poll up to ~3s for the PTY to echo the result (timing-tolerant)
            out = ""
            for _ in range(15):
                time.sleep(0.2)
                out = sh.shell_poll(sid, 0)["data"]
                if "BD_SHELL_PROOF" in out:
                    break
            assert "BD_SHELL_PROOF" in out
            root = os.environ.get("BD_COCKPIT_TASKS") or os.environ.get("BD_HOME") or "."
            audit = Path(root) / "shell_audit.log"
            assert audit.is_file()
            assert "echo BD_SHELL_PROOF" in audit.read_text()
        finally:
            sh.shell_close(sid)

    @pytest.mark.skipif(not sh._PTY_OK, reason="pty unavailable on this OS")
    def test_poll_strips_ansi_keeps_offset(self, monkeypatch):
        monkeypatch.setenv("BD_COCKPIT_SHELL", "1")
        sid = sh.shell_open()["session"]
        try:
            sh.shell_input(sid, "printf 'x\\n'\n")
            for _ in range(10):
                time.sleep(0.2)
                p = sh.shell_poll(sid, 0)
                if p["data"]:
                    break
            assert "\x1b[" not in p["data"]            # ANSI stripped for display
            assert isinstance(p["offset"], int) and p["offset"] >= 0
        finally:
            sh.shell_close(sid)


# ── debug log console: read-only + redacted ─────────────────────────────────

class TestDebugLog:
    def test_no_log_present_is_graceful(self, monkeypatch):
        monkeypatch.delenv("BD_LOG_FILE", raising=False)
        d = cc.debug_log()
        assert d["present"] in (True, False)  # depends on cwd; must not raise
        assert "Read-only" in d["_note"]

    def test_redacts_and_withholds_leaky_lines(self, monkeypatch, tmp_path):
        log = tmp_path / "app.log"
        log.write_text("INFO normal line\n"
                       "WARN token=supersecretvalue here\n"
                       "ERROR Cookie: session=abc.def.ghi here\n"
                       "INFO done\n")
        monkeypatch.setenv("BD_LOG_FILE", str(log))
        d = cc.debug_log(50)
        assert d["present"] is True
        joined = "\n".join(d["lines"])
        # neither secret value may surface in clear
        assert "supersecretvalue" not in joined
        assert "abc.def.ghi" not in joined
        # v3.66.574: redact() now routes through the canonical redactor, which
        # masks the I0008 floor incl. cookie session= -- both leaky lines are
        # masked IN PLACE and shown, rather than the cookie line being withheld
        # as the old narrow redactor required (strict improvement, withheld->0).
        assert "token=<scrubbed>" in joined
        assert "session=<scrubbed>" in joined
        assert d["withheld"] == 0

    def test_endpoint_serves(self):
        from flask import Flask
        from tools.cockpit_console import bp
        app = Flask(__name__); app.register_blueprint(bp); c = app.test_client()
        assert c.get("/cockpit/api/debug-log?lines=50").status_code == 200

    def test_console_page_cannot_execute(self):
        # the console page is read-only: it has no POST, no command input
        body_i = _CONSOLE_SRC.find("PAGES.console=")
        nxt = _CONSOLE_SRC.find("PAGES.", body_i + 6)
        body = _CONSOLE_SRC[body_i:nxt]
        assert "method:'POST'" not in body
        assert "shell" not in body.lower()


# ── layout toggle (front-end only) ───────────────────────────────────────────

class TestLayoutToggle:
    def test_toggle_present_and_persisted(self):
        assert "layout_sel" in _CONSOLE_SRC
        assert "bd_cockpit_layout" in _CONSOLE_SRC      # localStorage key
        assert ".app.topnav" in _CONSOLE_SRC            # top-bar CSS

    def test_pages_wired(self):
        nav = set(re.findall(r'data-p="([a-z0-9]+)"', _CONSOLE_SRC))
        pages = set(re.findall(r"PAGES\.([a-z]+)=", _CONSOLE_SRC))
        for p in ("console", "shell"):
            assert p in nav and p in pages


class TestRouteShape108:
    def test_shell_status_is_get_open_is_post(self):
        from flask import Flask
        from tools.cockpit_console import bp
        app = Flask(__name__); app.register_blueprint(bp)
        rules = {r.rule: r.methods for r in app.url_map.iter_rules()
                 if r.rule.startswith("/cockpit/api/shell")}
        assert "GET" in rules["/cockpit/api/shell/status"]
        assert "POST" in rules["/cockpit/api/shell/open"]

    def test_route_count(self):
        from flask import Flask
        from tools.cockpit_console import bp
        app = Flask(__name__); app.register_blueprint(bp)
        rules = {r.rule for r in app.url_map.iter_rules() if r.rule.startswith("/cockpit")}
        assert len(rules) >= 162

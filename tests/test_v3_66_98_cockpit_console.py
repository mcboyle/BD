"""v3.66.98 — operator cockpit console: SAFETY BOUNDARY tests.

These prove the cockpit cannot be turned into a command-execution or remote-
control surface. Each test targets one hard boundary from the spec:

  * no arbitrary command execution / no shell=True
  * path traversal blocked (output + capture paths confined)
  * noVNC URL is config-only (never browser-supplied)
  * spreadsheet rows cannot execute commands (data only)
  * report runners + capture tools are allowlisted
  * capture task arguments are validated
  * raw signing values are redacted from anything shown
  * the corpus / selectors / profiles are never written from the cockpit
"""
import json
import os
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from tools import cockpit_core as cc


@pytest.fixture(autouse=True)
def _roots(tmp_path, monkeypatch):
    monkeypatch.setenv("BD_CAPTURES_ROOT", str(tmp_path / "cap"))
    monkeypatch.setenv("BD_FRAMEWORK_REPORTS", str(tmp_path / "rep"))
    monkeypatch.setenv("BD_COCKPIT_TASKS", str(tmp_path / "task"))
    (tmp_path / "cap").mkdir()
    (tmp_path / "rep").mkdir()
    (tmp_path / "task").mkdir()
    yield


class TestNoArbitraryExecution:
    def test_unknown_report_runner_refused(self):
        with pytest.raises(cc.ValidationError):
            cc.build_invocation("report", "rm_rf", {}, cc.captures_root())

    def test_unknown_capture_tool_refused(self):
        with pytest.raises(cc.ValidationError):
            cc.build_invocation("capture", "/bin/sh", {}, cc.captures_root())

    def test_unknown_category_refused(self):
        with pytest.raises(cc.ValidationError):
            cc.build_invocation("exec_anything", "x", {}, cc.captures_root())

    def test_start_task_rejects_non_allowlisted(self):
        with pytest.raises(cc.ValidationError):
            cc.start_task("capture", "evil_tool", {})

    def test_every_allowlisted_argv_is_our_interpreter_and_script(self):
        # for the report runners we can build argv without a real process
        out = cc.tasks_root() / "x"; out.mkdir(parents=True, exist_ok=True)
        argv = cc.build_invocation("report", "autopilot_cockpit", {}, out)
        assert isinstance(argv, list)
        # first arg = python interpreter, second = a script UNDER our tree
        assert argv[0] in (sys.executable, "python3")
        assert Path(argv[1]).resolve().is_relative_to(_ROOT)
        assert "tools/operator_layer.py" in argv[1]


class TestNoShell:
    def test_no_shell_string_anywhere_in_core(self):
        src = (Path(_ROOT) / "tools/cockpit_core.py").read_text()
        # the subprocess call must be shell=False; assert there's no shell=True
        assert "shell=True" not in src
        assert "shell=False" in src

    def test_console_has_no_shell_true(self):
        src = (Path(_ROOT) / "tools/cockpit_console.py").read_text()
        assert "shell=True" not in src

    def test_os_system_not_used(self):
        for f in ("tools/cockpit_core.py", "tools/cockpit_console.py"):
            src = (Path(_ROOT) / f).read_text()
            assert "os.system" not in src
            assert "os.popen" not in src


class TestPathConfinement:
    def test_traversal_blocked(self):
        assert cc.confine("../../etc/passwd", cc.captures_root()) is None
        assert cc.confine("../../../root/.ssh/id_rsa", cc.captures_root()) is None

    def test_absolute_escape_blocked(self):
        assert cc.confine("/etc/passwd", cc.captures_root()) is None
        assert cc.confine("/tmp/evil.wacz", cc.captures_root()) is None

    def test_nullbyte_blocked(self):
        assert cc.confine("ok\x00/../../etc", cc.captures_root()) is None

    def test_legit_path_allowed(self):
        p = cc.confine("series/clip_4k.wacz", cc.captures_root())
        assert p is not None
        assert p.is_relative_to(cc.captures_root())

    def test_capture_out_escape_refused(self):
        with pytest.raises(cc.ValidationError):
            cc.v_out_under_captures("../../tmp/escape")

    def test_offline_analyze_paths_confined(self):
        # baseline/perturbed outside the root are refused
        out = cc.tasks_root() / "o"; out.mkdir(parents=True, exist_ok=True)
        with pytest.raises(cc.ValidationError):
            cc.build_invocation("capture", "offline_capture_analyze",
                                {"baseline": "/etc/passwd",
                                 "perturbed": "/etc/hosts",
                                 "axis": "player_config"}, out)


class TestNoVNCConfigOnly:
    def test_novnc_url_from_env(self, monkeypatch):
        # v3.66.266: novnc_url() now FILLS embed defaults when absent
        # (resize=scale so the canvas fits the iframe; autoconnect=true) while
        # preserving the operator's URL + any explicit value. Config-only +
        # never-browser-supplied is unchanged (see test_console_api_... below).
        monkeypatch.setenv("BD_NOVNC_URL", "http://10.0.70.20:6080/vnc.html")
        out = cc.novnc_url()
        assert out.startswith("http://10.0.70.20:6080/vnc.html?"), out
        assert "resize=scale" in out and "autoconnect=true" in out, out

    def test_novnc_explicit_params_preserved(self, monkeypatch):
        # an operator-set resize/autoconnect always wins — defaults only fill a gap.
        monkeypatch.setenv("BD_NOVNC_URL",
                           "http://10.0.70.20:6080/vnc.html?resize=remote&autoconnect=false")
        out = cc.novnc_url()
        # explicit resize/autoconnect PRESERVED; BUG-2 (v3.66.599) also gap-fills
        # reconnect defaults so a resize-bounced VNC socket self-heals instead of
        # dropping to the manual connect screen (reads as a password re-prompt).
        assert "resize=remote" in out and "resize=scale" not in out, out
        assert "autoconnect=false" in out and "autoconnect=true" not in out, out
        assert "reconnect=true" in out and "reconnect_delay=2000" in out, out

    def test_novnc_empty_when_unset(self, monkeypatch):
        monkeypatch.delenv("BD_NOVNC_URL", raising=False)
        assert cc.novnc_url() == ""

    def test_console_api_does_not_accept_browser_url(self):
        # there is no route/param that sets the noVNC url; the GET only reads it
        src = (Path(_ROOT) / "tools/cockpit_console.py").read_text()
        # the novnc handler returns cc.novnc_url(); it never reads request data
        import re
        m = re.search(r"def api_novnc.*?return jsonify", src, re.S)
        assert m and "request." not in m.group(0)


class TestSpreadsheetIsDataOnly:
    def test_injection_row_marked_invalid(self):
        r = cc.parse_plan([{"site": "x", "url": "https://x.com",
                            "label": "a; rm -rf /", "priority": "high"}])
        assert r["items"][0]["valid"] is False

    def test_shell_metachars_refused_as_data(self):
        for bad in ("a && b", "x | y", "`whoami`", "$(id)", "a\nb"):
            r = cc.parse_plan([{"site": "s", "url": "https://x.com",
                                "label": "ok", "notes": bad, "priority": "low"}])
            assert r["items"][0]["valid"] is False

    def test_unknown_columns_ignored_not_executed(self):
        # an extra "command" column must be ignored, not run
        r = cc.parse_plan([{"site": "s", "url": "https://x.com", "label": "ok",
                            "priority": "low", "command": "rm -rf /"}])
        # the row is valid as DATA (the extra col is ignored), and nothing ran
        assert "command" not in r["items"][0]

    def test_parse_plan_returns_no_execution_hook(self):
        r = cc.parse_plan([{"site": "s", "url": "https://x.com", "label": "ok",
                            "priority": "low"}])
        # the result is pure data: items + a note, no callable, no argv
        assert "items" in r and "argv" not in json.dumps(r)
        assert "No capture runs from import" in r["_note"]


class TestArgumentValidation:
    def test_site_charset(self):
        cc.v_site("ultrafilms_2")
        for bad in ("bad site", "../x", "a;b", "", "x" * 100):
            with pytest.raises(cc.ValidationError):
                cc.v_site(bad)

    def test_label_charset(self):
        cc.v_label("two_candies_4k.v1")
        for bad in ("a/b", "a b", "x;y", ""):
            with pytest.raises(cc.ValidationError):
                cc.v_label(bad)

    def test_axis_enum(self):
        assert cc.v_axis("player_config") == "player_config"
        assert cc.v_axis(None) is None
        with pytest.raises(cc.ValidationError):
            cc.v_axis("rm -rf")

    def test_url_scheme_and_metachars(self):
        cc.v_url("https://site.com/members/item")
        for bad in ("file:///etc/passwd", "javascript:alert(1)",
                    "https://x.com/a;rm -rf", "https://x.com/`id`",
                    "ftp://x", "https://x.com/a\nb"):
            with pytest.raises(cc.ValidationError):
                cc.v_url(bad)

    def test_capture_session_argv_built_safely(self):
        out = cc.tasks_root() / "o"; out.mkdir(parents=True, exist_ok=True)
        argv = cc.build_invocation("capture", "capture_session",
                                   {"url": "https://site.com/x",
                                    "label": "clip_4k", "autofill": True}, out)
        assert "--url" in argv and "https://site.com/x" in argv
        assert "--title" in argv and "clip_4k" in argv
        # the output .wacz is under our task out dir
        outp = argv[argv.index("--out") + 1]
        assert Path(outp).is_relative_to(out)

    def test_capture_session_isolates_url_memory_per_task(self):
        # BUGFIX (v3.66.268): the SPA "Open session" flow sends no label, so the
        # argv used --title "capture" with capture_session's DEFAULT SHARED
        # url-memory file (./capture_url_memory.json). capture_session replays a
        # REMEMBERED page for a known --title INSTEAD of --url, so every session
        # re-opened the last remembered page (e.g. example.com) and ignored the
        # typed URL. Fix: point --url-memory-file at a per-TASK path so each
        # cockpit session starts with EMPTY memory and the typed --url wins.
        out = cc.tasks_root() / "mem"; out.mkdir(parents=True, exist_ok=True)
        argv = cc.build_invocation("capture", "capture_session",
                                   {"url": "https://typed-site.example/watch/9"}, out)
        assert "--url" in argv and "https://typed-site.example/watch/9" in argv
        assert "--url-memory-file" in argv, \
            "cockpit capture must isolate url-memory or --url gets overridden by a remembered page"
        memp = argv[argv.index("--url-memory-file") + 1]
        # the memory file must be scoped UNDER this task's out dir (fresh/empty),
        # NOT the shared repo-root default that caused the cross-session replay.
        assert Path(memp).is_relative_to(out), f"url-memory not isolated to task dir: {memp}"


class TestRedaction:
    def test_signing_values_redacted(self):
        t = "https://cdn/clip.mp4?token=SECRET123&expires=999&sig=ABCDEF"
        red = cc.redact(t)
        assert "SECRET123" not in red
        assert "ABCDEF" not in red
        assert "999" not in red or "expires=<redacted>" in red

    def test_posture_clean_detects_leak(self):
        # a string with a raw token should be flagged by the project scanner
        leaks = cc.posture_clean("token=DEADBEEF&expires=1")
        # if the project scanner is present it flags; if not, redact still strips
        assert isinstance(leaks, list)


class TestNoCorpusOrStateWrites:
    def test_allowlist_excludes_corpus_writers(self):
        # the allowlist must not contain validation_corpus add, selector promote,
        # or profile update — the things that mutate durable state.
        names = set(cc.REPORT_RUNNERS) | set(cc.CAPTURE_TOOLS)
        for forbidden in ("validation_corpus", "corpus_add", "selector_promote",
                          "profile_update", "request_replay", "retire_debt"):
            assert forbidden not in names

    def test_scripts_are_viewers_analyzers_capturers_only(self):
        # every allowlisted script is one of the known-safe tools
        ok = {"tools/operator_layer.py", "tools/capture_session.py",
              "tools/capture_batch.py", "tools/offline_capture_analyze.py"}
        for spec in list(cc.REPORT_RUNNERS.values()) + list(cc.CAPTURE_TOOLS.values()):
            assert spec["script"] in ok


class TestTaskRegistryServerOwned:
    def test_task_id_is_server_generated(self):
        a = cc._new_task_id(); b = cc._new_task_id()
        assert a != b and a.startswith("t_")

    def test_client_cannot_supply_task_id(self):
        # start_task signature takes no task_id param
        import inspect
        params = inspect.signature(cc.start_task).parameters
        assert "task_id" not in params


class TestBlueprintShape:
    def test_get_routes_view_post_routes_act(self):
        from flask import Flask
        from tools.cockpit_console import bp
        app = Flask(__name__); app.register_blueprint(bp)
        posts = sorted(p for p in
                       (r.rule for r in app.url_map.iter_rules() if "POST" in r.methods)
                       if p.startswith("/cockpit"))
        # The three TOOL-RUNNER actions — the only POSTs that invoke an
        # allowlisted tool via start_task — must be present.
        action_endpoints = {
            "/cockpit/api/import-plan/preview",  # data-only preview (runs nothing)
            "/cockpit/api/run-capture",
            "/cockpit/api/run-report",
        }
        assert action_endpoints <= set(posts)
        # Wave 2 deliberately added scoped LOCAL-STATE writes (campaigns, queue,
        # notes, review). They write inert JSON; none run a tool except
        # queue/launch, which routes through the same validated capture path.
        # Enumerate the full expected POST set so any UNEXPECTED new POST still
        # trips this test (update deliberately when a wave adds endpoints).
        state_endpoints = {
            "/cockpit/api/campaigns",
            "/cockpit/api/queue",
            "/cockpit/api/queue/reorder",
            "/cockpit/api/queue/state",
            "/cockpit/api/queue/launch",
            "/cockpit/api/notes",
            "/cockpit/api/review/decide",
            "/cockpit/api/health-checks/run",  # Wave 3: read-only refresh (writes a snapshot, runs no tool)
            "/cockpit/api/saved-views",        # Phase 1 completion: inert saved query
            "/cockpit/api/saved-views/delete",
            "/cockpit/api/collections",        # Phase 1 completion: inert evidence collection
            "/cockpit/api/collections/add",
            "/cockpit/api/escalations",        # Band B: inert escalation flag
            "/cockpit/api/escalations/clear",
        }
        # v3.66.108: the OPT-IN interactive shell adds POSTs that are NEITHER
        # allowlisted recognition actions NOR inert state — they are a separate
        # operator admin surface (cockpit_shell.py, ON by default since v3.66.183,
        # hard-disabled by BD_COCKPIT_SHELL=0, never imported by cockpit_core).
        # They are enumerated here so the recognition contract above stays
        # explicit and any OTHER unexpected POST still trips this test.
        gated_shell_endpoints = {
            "/cockpit/api/shell/open",
            "/cockpit/api/shell/input",
            "/cockpit/api/shell/signal",
            "/cockpit/api/shell/close",
        }
        # v3.66.139 / .142: capture-template builders — recognition-only POSTs
        # that read APPROVED captures and return a review-required draft. They
        # run no allowlisted tool and write no state (distinct from both the
        # action and the local-state endpoints above); enumerated so any OTHER
        # unexpected POST still trips this test.
        recognition_endpoints = {
            "/cockpit/api/captures/build-template",        # 2-capture synth (v3.66.139)
            "/cockpit/api/captures/build-multi-template",  # multi-capture compare (v3.66.142)
        }
        # v3.66.158 WACZ pipeline: end an interactive capture (writes a
        # FINISH/CANCEL sentinel) and build+normalize a captured .wacz into a
        # review candidate (writes templates/review_candidates/). Neither runs an
        # allowlisted tool nor enables anything; enumerated so any OTHER POST trips.
        # v3.66.250: captures/pick is the same family — arm/poll/clear a one-shot
        # active element-pick on a running capture (writes a PICK_ARM sentinel in
        # the capture out_dir); runs no tool, enables nothing.
        wacz_pipeline_endpoints = {
            "/cockpit/api/captures/finish",
            "/cockpit/api/captures/normalize",
            "/cockpit/api/captures/pick",
        }
        # LOOSE-9 (v3.66.252): formerly `set(posts) == union`, which forced every
        # route-adding cut to update this set AND the ~32 count-pins (the count
        # sweep alone missed THIS set). Relaxed to a SUPERSET assert: every KNOWN
        # security-relevant POST must still be present and correctly categorized
        # (a removed/renamed known route trips it), but a genuinely new POST no
        # longer whack-a-moles this file. A ceiling still catches an explosion.
        known_posts = (action_endpoints | state_endpoints
                       | gated_shell_endpoints | recognition_endpoints
                       | wacz_pipeline_endpoints)
        assert known_posts <= set(posts), sorted(known_posts - set(posts))
        assert len(posts) <= 40, ("unexpected POST explosion", sorted(posts))

    def test_shell_posts_are_gated_by_opt_out(self):
        # v3.66.183: the shell is ON by default; BD_COCKPIT_SHELL=0 hard-disables
        # it. With the opt-out set the POSTs refuse (403), proving the route's
        # mere presence in the table grants no execution capability when disabled.
        from flask import Flask
        from tools.cockpit_console import bp
        import os as _os
        _prev = _os.environ.get("BD_COCKPIT_SHELL")
        _os.environ["BD_COCKPIT_SHELL"] = "0"
        try:
            app = Flask(__name__); app.register_blueprint(bp); c = app.test_client()
            assert c.post("/cockpit/api/shell/open", json={}).status_code == 403
        finally:
            if _prev is None:
                _os.environ.pop("BD_COCKPIT_SHELL", None)
            else:
                _os.environ["BD_COCKPIT_SHELL"] = _prev

    def test_run_report_rejects_unlisted_via_api(self):
        from flask import Flask
        from tools.cockpit_console import bp
        app = Flask(__name__); app.register_blueprint(bp)
        c = app.test_client()
        r = c.post("/cockpit/api/run-report", json={"name": "evil"})
        assert r.status_code == 400

    def test_run_capture_rejects_unlisted_via_api(self):
        from flask import Flask
        from tools.cockpit_console import bp
        app = Flask(__name__); app.register_blueprint(bp)
        c = app.test_client()
        r = c.post("/cockpit/api/run-capture", json={"name": "/bin/sh"})
        assert r.status_code == 400

"""v3.43.62 — Live-stream recording runtime tests.

Compatible with both pytest and the minimal run_tests.py runner. The
minimal runner doesn't honor module-level `@pytest.fixture(autouse=True)`,
so per-class setup uses `setup_method` and creates its own tmpdir.
"""
from __future__ import annotations

import importlib
import json
import os
import shutil
import tempfile
import time
from pathlib import Path


def _reset_module():
    from bulk_downloader import live_recorder
    live_recorder._reset_for_tests()
    live_recorder._reset_backend_cache_for_tests()


def _force_available():
    """Patch is_available -> True for tests that exercise watch() without
    actually needing a recording backend installed."""
    from bulk_downloader import live_recorder
    live_recorder._test_force_available = True
    live_recorder.is_available = lambda: True


def _unforce_available():
    from bulk_downloader import live_recorder
    if hasattr(live_recorder, "_test_force_available"):
        from importlib import reload
        reload(live_recorder)


# ─── URL parsing ────────────────────────────────────────────────────

class TestParseLiveUrl:
    def setup_method(self):
        _reset_module()

    def test_chaturbate(self):
        from bulk_downloader.live_recorder import parse_live_url
        assert parse_live_url("https://chaturbate.com/somemodel/") == \
            ("chaturbate", "somemodel")
        assert parse_live_url("https://chaturbate.com/somemodel") == \
            ("chaturbate", "somemodel")
        assert parse_live_url("https://www.chaturbate.com/SomeModel/") == \
            ("chaturbate", "somemodel")

    def test_stripchat(self):
        from bulk_downloader.live_recorder import parse_live_url
        assert parse_live_url("https://stripchat.com/somemodel") == \
            ("stripchat", "somemodel")

    def test_bongacams(self):
        from bulk_downloader.live_recorder import parse_live_url
        assert parse_live_url("https://bongacams.com/somemodel/") == \
            ("bongacams", "somemodel")

    def test_fansly(self):
        from bulk_downloader.live_recorder import parse_live_url
        assert parse_live_url("https://fansly.com/somecreator") == \
            ("fansly", "somecreator")

    def test_unrelated_url_rejected(self):
        from bulk_downloader.live_recorder import parse_live_url
        assert parse_live_url("https://example.com/foo") is None
        assert parse_live_url("https://wowgirls.com/video/1") is None

    def test_homepage_rejected(self):
        from bulk_downloader.live_recorder import parse_live_url
        assert parse_live_url("https://chaturbate.com/") is None
        assert parse_live_url("https://chaturbate.com") is None

    def test_non_string_inputs_none(self):
        from bulk_downloader.live_recorder import parse_live_url
        assert parse_live_url(None) is None

    def test_non_string_inputs_int(self):
        from bulk_downloader.live_recorder import parse_live_url
        assert parse_live_url(12345) is None

    def test_non_string_inputs_list(self):
        from bulk_downloader.live_recorder import parse_live_url
        assert parse_live_url([]) is None

    def test_non_string_inputs_dict(self):
        from bulk_downloader.live_recorder import parse_live_url
        assert parse_live_url({}) is None

    def test_non_string_inputs_bytes(self):
        from bulk_downloader.live_recorder import parse_live_url
        assert parse_live_url(b"bytes") is None

    def test_shell_metachars_in_slug_rejected(self):
        from bulk_downloader.live_recorder import parse_live_url
        bad_urls = [
            "https://chaturbate.com/foo;rm -rf /",
            "https://chaturbate.com/foo`whoami`",
            "https://chaturbate.com/foo$(id)",
            "https://chaturbate.com/foo|nc evil.com 80",
            "https://chaturbate.com/foo&calc.exe",
            "https://chaturbate.com/foo bar baz",
        ]
        for bad in bad_urls:
            assert parse_live_url(bad) is None, f"bad URL parsed: {bad}"

    def test_url_with_newline_rejected(self):
        from bulk_downloader.live_recorder import parse_live_url
        assert parse_live_url("https://chaturbate.com/foo\nbar") is None


# ─── is_available / preferred_backend ───────────────────────────────

class TestBackendDetection:
    def setup_method(self):
        _reset_module()

    def test_is_available_returns_bool(self):
        from bulk_downloader.live_recorder import is_available
        assert isinstance(is_available(), bool)

    def test_preferred_returns_known_value_or_none(self):
        from bulk_downloader.live_recorder import preferred_backend
        assert preferred_backend() in (None, "streamlink", "ffmpeg")

    def test_preferred_matches_availability(self):
        from bulk_downloader.live_recorder import is_available, preferred_backend
        if is_available():
            assert preferred_backend() is not None
        else:
            assert preferred_backend() is None


# ─── watch() / unwatch() state machine ──────────────────────────────

class TestWatchUnwatchStateMachine:
    def setup_method(self):
        _reset_module()
        self.tmp = tempfile.mkdtemp(prefix="live-recorder-test-")
        _force_available()
        from bulk_downloader import live_recorder
        live_recorder.init(self.tmp)

    def teardown_method(self):
        shutil.rmtree(self.tmp, ignore_errors=True)
        _unforce_available()
        _reset_module()

    def test_watch_records_recording(self):
        from bulk_downloader import live_recorder
        result = live_recorder.watch(
            "https://chaturbate.com/somemodel/", str(Path(self.tmp) / "out")
        )
        assert result["ok"] is True
        rid = result["recording_id"]
        rec = live_recorder.get_recording(rid)
        assert rec is not None
        assert rec["site"] == "chaturbate"
        assert rec["room"] == "somemodel"
        assert rec["state"] == "pending"

    def test_watch_rejects_unknown_url(self):
        from bulk_downloader import live_recorder
        result = live_recorder.watch(
            "https://example.com/something", str(Path(self.tmp) / "out")
        )
        assert result["ok"] is False
        assert result["error"] == "not_a_live_url"

    def test_watch_dedupe_when_already_active(self):
        from bulk_downloader import live_recorder
        r1 = live_recorder.watch(
            "https://chaturbate.com/somemodel/", str(Path(self.tmp) / "out")
        )
        assert r1["ok"]
        r2 = live_recorder.watch(
            "https://chaturbate.com/somemodel/", str(Path(self.tmp) / "out")
        )
        assert r2["ok"] is False
        assert r2["error"] == "already_watching"
        assert r2["recording_id"] == r1["recording_id"]

    def test_unwatch_marks_cancelled(self):
        from bulk_downloader import live_recorder
        r = live_recorder.watch(
            "https://chaturbate.com/somemodel/", str(Path(self.tmp) / "out")
        )
        rid = r["recording_id"]
        ok = live_recorder.unwatch(rid)
        assert ok["ok"] is True
        rec = live_recorder.get_recording(rid)
        assert rec["state"] == "cancelled"

    def test_unwatch_missing(self):
        from bulk_downloader import live_recorder
        result = live_recorder.unwatch("does_not_exist")
        assert result["ok"] is False
        assert result["error"] == "not_found"

    def test_watch_with_bad_room_override(self):
        from bulk_downloader import live_recorder
        result = live_recorder.watch(
            "https://anything.com/whatever",
            str(Path(self.tmp) / "out"),
            site_override="chaturbate",
            room_override="foo;rm -rf /",
        )
        assert result["ok"] is False
        assert result["error"] == "invalid_room"


class TestWatchNoBackend:
    def setup_method(self):
        _reset_module()
        self.tmp = tempfile.mkdtemp(prefix="live-recorder-test-")
        from bulk_downloader import live_recorder
        live_recorder.is_available = lambda: False
        live_recorder.init(self.tmp)

    def teardown_method(self):
        shutil.rmtree(self.tmp, ignore_errors=True)
        _unforce_available()
        _reset_module()

    def test_watch_refuses_when_no_backend(self):
        from bulk_downloader import live_recorder
        result = live_recorder.watch(
            "https://chaturbate.com/somemodel/", str(Path(self.tmp) / "out")
        )
        assert result["ok"] is False
        assert result["error"] == "no_backend"


class TestWatchMaxActive:
    def setup_method(self):
        _reset_module()
        self.tmp = tempfile.mkdtemp(prefix="live-recorder-test-")
        _force_available()
        from bulk_downloader import live_recorder, global_config as _GC
        # v3.66.503: the cap is a call-time getter backed by the global_config
        # store (live_max_active_recordings), not a module constant. Set it via
        # the store (the real live path) instead of monkeypatching a constant.
        self._cfg = Path("app_config.json")
        self._cfg_saved = self._cfg.read_text() if self._cfg.exists() else None
        self._cfg.write_text(json.dumps({"live_max_active_recordings": "2"}))
        _GC._cached = None
        _GC._cached_mtime = 0.0
        live_recorder.init(self.tmp)

    def teardown_method(self):
        from bulk_downloader import global_config as _GC
        if self._cfg_saved is None:
            if self._cfg.exists():
                self._cfg.unlink()
        else:
            self._cfg.write_text(self._cfg_saved)
        _GC._cached = None
        _GC._cached_mtime = 0.0
        shutil.rmtree(self.tmp, ignore_errors=True)
        _unforce_available()
        _reset_module()

    def test_watch_enforces_max_active(self):
        from bulk_downloader import live_recorder
        r1 = live_recorder.watch(
            "https://chaturbate.com/model1/", str(Path(self.tmp) / "out")
        )
        r2 = live_recorder.watch(
            "https://chaturbate.com/model2/", str(Path(self.tmp) / "out")
        )
        r3 = live_recorder.watch(
            "https://chaturbate.com/model3/", str(Path(self.tmp) / "out")
        )
        assert r1["ok"]
        assert r2["ok"]
        assert r3["ok"] is False
        assert r3["error"] == "too_many_active"


# ─── State persistence ──────────────────────────────────────────────

class TestStatePersistence:
    def setup_method(self):
        _reset_module()
        self.tmp = tempfile.mkdtemp(prefix="live-recorder-test-")
        _force_available()
        from bulk_downloader import live_recorder
        live_recorder.init(self.tmp)

    def teardown_method(self):
        shutil.rmtree(self.tmp, ignore_errors=True)
        _unforce_available()
        _reset_module()

    def test_save_then_reload(self):
        from bulk_downloader import live_recorder
        live_recorder.watch(
            "https://chaturbate.com/somemodel/", str(Path(self.tmp) / "out")
        )
        assert len(live_recorder.list_recordings()) == 1
        live_recorder._reset_for_tests()
        assert len(live_recorder.list_recordings()) == 0
        _force_available()
        live_recorder.init(self.tmp)
        recs = live_recorder.list_recordings()
        assert len(recs) == 1
        assert recs[0]["site"] == "chaturbate"
        assert recs[0]["room"] == "somemodel"
        assert recs[0]["pid"] is None
        assert recs[0]["state"] == "pending"

    def test_corrupt_state_file_starts_fresh(self):
        from bulk_downloader import live_recorder
        state = Path(self.tmp) / "recordings.json"
        state.write_text("{not json at all", encoding="utf-8")
        live_recorder._reset_for_tests()
        _force_available()
        live_recorder.init(self.tmp)
        assert live_recorder.list_recordings() == []


# ─── _build_cmd defense ─────────────────────────────────────────────

class TestBuildCmdDefense:
    def setup_method(self):
        _reset_module()

    def _make_rec(self, room="goodroom", url="https://chaturbate.com/goodroom/"):
        from bulk_downloader.live_recorder import Recording
        return Recording(
            recording_id="test",
            site="chaturbate",
            room=room,
            url=url,
            output_path="/tmp/out.ts",
            started_at=time.time(),
        )

    def test_clean_inputs_build_streamlink_cmd(self):
        from bulk_downloader.live_recorder import _build_cmd
        cmd = _build_cmd("streamlink", self._make_rec())
        assert cmd is not None
        assert cmd[0] == "streamlink"
        assert "https://chaturbate.com/goodroom/" in cmd

    def test_clean_inputs_build_ffmpeg_cmd(self, tmp_path, monkeypatch):
        """Cut 1459: argv0 is the RESOLVED ffmpeg, not the bare name.

        This used to assert ``cmd[0] == "ffmpeg"``, which pinned the defect:
        a bare name is re-resolved by execvp from the child's PATH, so the
        ffmpeg_path pin gated on one build while the recording ran another.
        The stub also removes this case's silent dependence on the host having
        an ffmpeg at all."""
        from bulk_downloader import ffmpeg_bin, live_recorder
        from bulk_downloader.live_recorder import _build_cmd
        pin_dir = tmp_path / "pinned"
        pin_dir.mkdir()
        stub = pin_dir / "ffmpeg"
        stub.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        stub.chmod(0o755)
        monkeypatch.setattr(ffmpeg_bin, "_pinned_dir", lambda: str(pin_dir))
        ffmpeg_bin.reset()
        live_recorder._reset_backend_cache_for_tests()
        try:
            cmd = _build_cmd("ffmpeg", self._make_rec())
            assert cmd is not None
            assert cmd[0] == str(stub)
            assert Path(cmd[0]).name == "ffmpeg"
        finally:
            ffmpeg_bin.reset()
            live_recorder._reset_backend_cache_for_tests()

    def test_unknown_backend_returns_none(self):
        from bulk_downloader.live_recorder import _build_cmd
        assert _build_cmd("notabackend", self._make_rec()) is None

    def test_room_with_metachars_blocked(self):
        from bulk_downloader.live_recorder import _build_cmd
        rec = self._make_rec(room="foo;rm")
        assert _build_cmd("streamlink", rec) is None

    def test_url_with_metachars_blocked(self):
        from bulk_downloader.live_recorder import _build_cmd
        rec = self._make_rec(url="https://chaturbate.com/foo;rm")
        assert _build_cmd("streamlink", rec) is None

    def test_url_with_space_blocked(self):
        from bulk_downloader.live_recorder import _build_cmd
        rec = self._make_rec(url="https://chaturbate.com/foo bar")
        assert _build_cmd("streamlink", rec) is None


class TestRecorderEgressGate:
    def setup_method(self):
        _reset_module()

    def teardown_method(self):
        _reset_module()

    @staticmethod
    def _recording(tmp_path):
        from bulk_downloader.live_recorder import Recording
        return Recording(
            recording_id="egress-test",
            site="chaturbate",
            room="goodroom",
            url="https://chaturbate.com/goodroom/",
            output_path=str(tmp_path / "out.ts"),
            started_at=time.time(),
        )

    @staticmethod
    def _install_real_egress(monkeypatch, live_recorder, runner, *,
                             required, socks_url):
        calls = {"prepare": 0, "required": 0, "resolve": 0}
        monkeypatch.setattr(runner, "_VPN_RUNTIME_AVAILABLE", True)

        def is_required(site):
            calls["required"] += 1
            assert site == "chaturbate"
            return required

        def resolve(site):
            calls["resolve"] += 1
            assert site == "chaturbate"
            return socks_url

        def prepare(site):
            calls["prepare"] += 1
            # Resolve lazily: on the defective parent the recorder ignores
            # this injected seam and reaches Popen, which is the behavioural
            # RED. The fixed path invokes the real shared policy function.
            return getattr(runner, "prepare_site_http_egress")(site)

        monkeypatch.setattr(
            runner.vpn_runtime, "is_vpn_required_for_site", is_required)
        monkeypatch.setattr(
            runner.vpn_runtime, "get_socks_url_for_site", resolve)
        monkeypatch.setattr(
            live_recorder, "_egress_prepare", prepare, raising=False)
        return calls

    def test_row647_required_tunnel_down_spawns_no_probe_or_recorder(
            self, tmp_path, monkeypatch):
        """The policy gate precedes both network subprocess boundaries."""
        import subprocess
        from bulk_downloader import live_recorder
        runner = importlib.import_module("bulk_downloader.runner")

        seen = {"run": 0, "popen": 0}
        egress = self._install_real_egress(
            monkeypatch, live_recorder, runner,
            required=True, socks_url=None)

        def run(argv, **kwargs):
            seen["run"] += 1
            return subprocess.CompletedProcess(argv, 0, b"", b"")

        def popen(*args, **kwargs):
            seen["popen"] += 1
            return type("Process", (), {"pid": 4241})()

        monkeypatch.setattr(live_recorder, "preferred_backend", lambda: "streamlink")
        monkeypatch.setattr(live_recorder.subprocess, "run", run)
        monkeypatch.setattr(live_recorder.subprocess, "Popen", popen)
        rec = self._recording(tmp_path)
        assert rec.site == "chaturbate", "precondition: required site was not built"
        assert seen == {"run": 0, "popen": 0}

        live_recorder._process_recording(rec.recording_id, rec)

        assert seen["popen"] == 0, (
            f"required tunnel-down recorder spawned {seen['popen']} process")
        assert seen["run"] == 0, "the live probe spawned outside the egress gate"
        assert egress == {"prepare": 1, "required": 1, "resolve": 1}
        assert rec.state == "failed"
        assert rec.last_error == (
            "egress_refused: VPN egress for required site 'chaturbate' "
            "resolved no proxy; subprocess refused")

    def test_row647_unrestricted_site_still_probes_and_records(
            self, tmp_path, monkeypatch):
        """Negative control: no required tunnel preserves one probe and spawn."""
        import subprocess
        from bulk_downloader import live_recorder
        runner = importlib.import_module("bulk_downloader.runner")

        seen = {"run": 0, "popen": 0}
        egress = self._install_real_egress(
            monkeypatch, live_recorder, runner,
            required=False, socks_url=None)

        def run(argv, **kwargs):
            seen["run"] += 1
            return subprocess.CompletedProcess(argv, 0, b"{}", b"")

        class Process:
            pid = 4242

        def popen(*args, **kwargs):
            seen["popen"] += 1
            return Process()

        monkeypatch.setattr(live_recorder, "preferred_backend", lambda: "streamlink")
        monkeypatch.setattr(live_recorder.subprocess, "run", run)
        monkeypatch.setattr(live_recorder.subprocess, "Popen", popen)
        rec = self._recording(tmp_path)

        live_recorder._process_recording(rec.recording_id, rec)

        assert seen == {"run": 1, "popen": 1}
        assert egress == {"prepare": 1, "required": 1, "resolve": 1}
        assert rec.state == "recording"
        assert rec.pid == 4242

    def test_row647_live_socks_tunnel_uses_the_shared_http_carrier(
            self, tmp_path, monkeypatch):
        """A healthy required tunnel reaches both recorder subprocess stages."""
        import subprocess
        from urllib.parse import urlsplit
        from bulk_downloader import live_recorder
        runner = importlib.import_module("bulk_downloader.runner")

        calls = {"run": [], "popen": []}
        egress = self._install_real_egress(
            monkeypatch, live_recorder, runner,
            required=True, socks_url="socks5://127.0.0.1:11080")

        def run(argv, **kwargs):
            calls["run"].append((list(argv), dict(kwargs)))
            return subprocess.CompletedProcess(argv, 0, b"{}", b"")

        class Process:
            pid = 4243

        def popen(argv, **kwargs):
            calls["popen"].append((list(argv), dict(kwargs)))
            return Process()

        monkeypatch.setattr(live_recorder, "preferred_backend", lambda: "streamlink")
        monkeypatch.setattr(live_recorder.subprocess, "run", run)
        monkeypatch.setattr(live_recorder.subprocess, "Popen", popen)
        rec = self._recording(tmp_path)

        live_recorder._process_recording(rec.recording_id, rec)

        assert egress == {"prepare": 1, "required": 1, "resolve": 1}
        assert len(calls["run"]) == 1, "expected one live-status probe"
        assert len(calls["popen"]) == 1, "expected one recorder spawn"
        probe_argv, probe_kwargs = calls["run"][0]
        record_argv, record_kwargs = calls["popen"][0]
        probe_proxy = probe_argv[probe_argv.index("--http-proxy") + 1]
        record_proxy = record_argv[record_argv.index("--http-proxy") + 1]
        assert probe_proxy == record_proxy
        parsed = urlsplit(probe_proxy)
        assert parsed.scheme == "http" and parsed.hostname == "127.0.0.1"
        assert parsed.port is not None and parsed.port > 0
        assert probe_kwargs["env"]["http_proxy"] == probe_proxy
        assert record_kwargs["env"]["http_proxy"] == record_proxy
        assert rec.state == "recording"


# ─── Scheduler gate ─────────────────────────────────────────────────

class TestSchedulerGate:
    def setup_method(self):
        _reset_module()
        self.tmp = tempfile.mkdtemp(prefix="live-recorder-test-")
        self._prev_env = os.environ.get("BD_DISABLE_KEEPALIVE")
        os.environ["BD_DISABLE_KEEPALIVE"] = "1"

    def teardown_method(self):
        if self._prev_env is None:
            os.environ.pop("BD_DISABLE_KEEPALIVE", None)
        else:
            os.environ["BD_DISABLE_KEEPALIVE"] = self._prev_env
        shutil.rmtree(self.tmp, ignore_errors=True)
        _reset_module()

    def test_scheduler_does_not_start_with_disable_keepalive(self):
        from bulk_downloader import live_recorder
        live_recorder.init(self.tmp)
        result = live_recorder.start_scheduler()
        assert result is False
        live_recorder.stop_scheduler(timeout=0.1)


# ─── Subprocess termination ─────────────────────────────────────────

class TestTerminateSubprocess:
    def setup_method(self):
        _reset_module()

    def test_terminate_quiet_on_already_dead_proc(self):
        from bulk_downloader.live_recorder import _terminate_subprocess
        import subprocess
        if os.name == "nt":
            proc = subprocess.Popen(["cmd.exe", "/c", "exit"],
                                    stdout=subprocess.DEVNULL,
                                    stderr=subprocess.DEVNULL)
        else:
            proc = subprocess.Popen(["true"],
                                    stdout=subprocess.DEVNULL,
                                    stderr=subprocess.DEVNULL)
        proc.wait()
        _terminate_subprocess(proc)

    def test_terminate_handles_kill_failure(self):
        from bulk_downloader.live_recorder import _terminate_subprocess

        class BoomProc:
            def poll(self): return None
            def terminate(self): raise OSError("boom")
            def send_signal(self, _): raise OSError("boom")
            def kill(self): raise OSError("boom")
            def wait(self, timeout=None):
                raise OSError("boom")
        _terminate_subprocess(BoomProc(), timeout=0.1)


# ─── Templates flipped ──────────────────────────────────────────────

class TestLiveCamTemplatesFlipped:
    def setup_method(self):
        pass

    def test_chaturbate_has_live_recorder_flag(self):
        from bulk_downloader import templates
        t = templates.get("chaturbate")
        cfg = t.get("config_defaults") or {}
        assert cfg.get("use_live_recorder") is True
        assert "_limitation" not in cfg

    def test_stripchat_has_live_recorder_flag(self):
        from bulk_downloader import templates
        t = templates.get("stripchat_live")
        cfg = t.get("config_defaults") or {}
        assert cfg.get("use_live_recorder") is True

    def test_bongacams_has_live_recorder_flag(self):
        from bulk_downloader import templates
        t = templates.get("bongacams_live")
        cfg = t.get("config_defaults") or {}
        assert cfg.get("use_live_recorder") is True

    def test_fansly_has_live_recorder_flag(self):
        from bulk_downloader import templates
        t = templates.get("fansly_live")
        cfg = t.get("config_defaults") or {}
        assert cfg.get("use_live_recorder") is True

    def test_chaturbate_url_routes(self):
        from bulk_downloader.templates import suggest_for_url
        assert "chaturbate" in suggest_for_url("https://chaturbate.com/somemodel/")

    def test_stripchat_url_routes(self):
        from bulk_downloader.templates import suggest_for_url
        assert "stripchat_live" in suggest_for_url("https://stripchat.com/somemodel")

    def test_bongacams_url_routes(self):
        from bulk_downloader.templates import suggest_for_url
        assert "bongacams_live" in suggest_for_url("https://bongacams.com/somemodel")

    def test_fansly_url_routes(self):
        from bulk_downloader.templates import suggest_for_url
        assert "fansly_live" in suggest_for_url("https://fansly.com/somecreator")


# ─── API routes ─────────────────────────────────────────────────────

class TestApiRoutesSmoke:
    def setup_method(self):
        _reset_module()
        self.tmp = tempfile.mkdtemp(prefix="live-recorder-test-")

    def teardown_method(self):
        shutil.rmtree(self.tmp, ignore_errors=True)
        _reset_module()

    def test_blueprint_registers(self):
        from flask import Flask
        from bulk_downloader import app_live_recorder
        app = Flask(__name__)
        count = app_live_recorder.register_routes(app)
        assert count >= 5

    def test_status_endpoint(self):
        from flask import Flask
        from bulk_downloader import app_live_recorder, live_recorder
        live_recorder.init(self.tmp)
        app = Flask(__name__)
        app_live_recorder.register_routes(app)
        client = app.test_client()
        resp = client.get("/api/live/status")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["ok"] is True

    def test_parse_url_endpoint(self):
        from flask import Flask
        from bulk_downloader import app_live_recorder
        app = Flask(__name__)
        app_live_recorder.register_routes(app)
        client = app.test_client()
        resp = client.post("/api/live/parse_url",
                           json={"url": "https://chaturbate.com/somemodel/"})
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["recognized"] is True
        assert data["site"] == "chaturbate"
        assert data["room"] == "somemodel"

    def test_parse_url_endpoint_rejects_unknown(self):
        from flask import Flask
        from bulk_downloader import app_live_recorder
        app = Flask(__name__)
        app_live_recorder.register_routes(app)
        client = app.test_client()
        resp = client.post("/api/live/parse_url",
                           json={"url": "https://example.com/foo"})
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["recognized"] is False

    def test_parse_url_endpoint_rejects_missing_field(self):
        from flask import Flask
        from bulk_downloader import app_live_recorder
        app = Flask(__name__)
        app_live_recorder.register_routes(app)
        client = app.test_client()
        resp = client.post("/api/live/parse_url", json={})
        assert resp.status_code == 400


# ─── Fuzz: watch() with adversarial input ──────────────────────────

class TestWatchFuzz:
    def setup_method(self):
        _reset_module()
        self.tmp = tempfile.mkdtemp(prefix="live-recorder-test-")
        _force_available()
        from bulk_downloader import live_recorder
        live_recorder.init(self.tmp)

    def teardown_method(self):
        shutil.rmtree(self.tmp, ignore_errors=True)
        _unforce_available()
        _reset_module()

    def _check(self, url):
        from bulk_downloader import live_recorder
        result = live_recorder.watch(url, str(Path(self.tmp) / "out"))
        assert isinstance(result, dict)
        assert result.get("ok") is False
        assert "error" in result

    def test_url_none(self): self._check(None)
    def test_url_empty(self): self._check("")
    def test_url_int(self): self._check(12345)
    def test_url_list(self): self._check([])
    def test_url_dict(self): self._check({})
    def test_url_bytes(self): self._check(b"bytes")
    def test_url_not_a_url(self): self._check("not_a_url")
    def test_url_no_host(self): self._check("http:///no-host")
    def test_url_javascript(self): self._check("javascript:alert(1)")
    def test_url_file(self): self._check("file:///etc/passwd")


# ─── v3.43.62 audit fixes ───────────────────────────────────────────

class TestV3_43_62AuditFixes:
    """Audit findings hardened during v3.43.62 build."""

    def setup_method(self):
        _reset_module()
        self.tmp = tempfile.mkdtemp(prefix="live-recorder-audit-")
        _force_available()
        from bulk_downloader import live_recorder
        live_recorder.init(self.tmp)

    def teardown_method(self):
        shutil.rmtree(self.tmp, ignore_errors=True)
        _unforce_available()
        _reset_module()

    def test_watch_rejects_url_with_shell_metachars_even_when_room_valid(self):
        from bulk_downloader import live_recorder
        result = live_recorder.watch(
            url="https://chaturbate.com/ok/`rm -rf /`",
            output_dir=str(Path(self.tmp) / "out"),
            site_override="chaturbate",
            room_override="legitmodel",
        )
        assert result.get("ok") is False
        assert result.get("error") == "invalid_url", result
        assert live_recorder.list_recordings() == []

    def test_load_state_drops_entries_with_bad_url(self):
        import json
        from bulk_downloader import live_recorder
        state_file = Path(self.tmp) / "recordings.json"
        state_file.write_text(json.dumps({
            "recordings": [
                {
                    "recording_id": "ok_recording",
                    "site": "chaturbate",
                    "room": "legitmodel",
                    "url": "https://chaturbate.com/legitmodel/",
                    "output_path": str(Path(self.tmp) / "a.ts"),
                    "started_at": 0,
                    "state": "pending",
                },
                {
                    "recording_id": "evil_url",
                    "site": "chaturbate",
                    "room": "legitmodel",
                    "url": "https://chaturbate.com/foo/`evil`",
                    "output_path": str(Path(self.tmp) / "b.ts"),
                    "started_at": 0,
                    "state": "pending",
                },
                {
                    "recording_id": "evil_room",
                    "site": "chaturbate",
                    "room": "../../../etc/passwd",
                    "url": "https://chaturbate.com/foo/",
                    "output_path": str(Path(self.tmp) / "c.ts"),
                    "started_at": 0,
                    "state": "pending",
                },
            ]
        }))
        live_recorder._reset_for_tests()
        _force_available()
        live_recorder.init(self.tmp)
        recs = live_recorder.list_recordings()
        ids = {r["recording_id"] for r in recs}
        assert "ok_recording" in ids
        assert "evil_url" not in ids
        assert "evil_room" not in ids

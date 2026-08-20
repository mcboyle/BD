"""v3.56 (Phase 8) — regression tests for the queue/download backlog.

Covers: #125 stuck-job watchdog endpoint, #79 token-rotation script,
and the #99/#55 env-flag wiring in downloader_ui.py (static checks —
the watcher/banner can't be exercised without a real server boot).
"""
import json
import os
import subprocess
import sys

import pytest



# v3.66.13: this file used to define its own autouse isolated_bd_home
# fixture; the canonical one lives in tests/conftest.py. The marker
# opts this file back into the sys.modules wipe (needed so the package
# re-reads env vars at import; see conftest.py for the full rationale).
pytestmark = pytest.mark.bd_module_wipe

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


# ── #125 stuck-job watchdog ─────────────────────────────────────────────

def test_stuck_endpoint_empty_when_no_jobs():
    from bulk_downloader import app as a
    c = a.app.test_client()
    r = c.get("/api/jobs/stuck")
    body = r.get_json()
    assert body["ok"] is True
    assert body["count"] == 0
    assert body["threshold_minutes"] == 30.0


def test_stuck_endpoint_custom_threshold():
    from bulk_downloader import app as a
    c = a.app.test_client()
    r = c.get("/api/jobs/stuck?minutes=10")
    assert r.get_json()["threshold_minutes"] == 10.0


def test_stuck_endpoint_detects_stale_running_job():
    """A job in a running state with an old last_progress_at should be
    reported as stuck."""
    import time
    from bulk_downloader import app as a
    c = a.app.test_client()
    sid = c.post("/api/sites", json={"name": "StuckSite"}).get_json()["id"]
    # Inject a stale running job directly into the runner's job table
    runner = a.runners[sid]
    runner.jobs["https://x.com/stuck"] = {
        "status": "running",
        "last_progress_at": time.time() - 3600,  # 1h ago
        "filename": "", "retries": 0,
    }
    r = c.get("/api/jobs/stuck?minutes=30")
    body = r.get_json()
    assert body["count"] == 1
    assert body["stuck"][0]["url"] == "https://x.com/stuck"
    assert body["stuck"][0]["idle_minutes"] >= 59


def test_stuck_endpoint_ignores_pending_jobs():
    """A pending job (waiting its turn) is not stuck, however old."""
    import time
    from bulk_downloader import app as a
    c = a.app.test_client()
    sid = c.post("/api/sites", json={"name": "PendSite"}).get_json()["id"]
    a.runners[sid].jobs["https://x.com/p"] = {
        "status": "pending",
        "last_progress_at": time.time() - 99999,
    }
    r = c.get("/api/jobs/stuck")
    assert r.get_json()["count"] == 0


def test_stuck_endpoint_ignores_fresh_running_job():
    import time
    from bulk_downloader import app as a
    c = a.app.test_client()
    sid = c.post("/api/sites", json={"name": "FreshSite"}).get_json()["id"]
    a.runners[sid].jobs["https://x.com/fresh"] = {
        "status": "running",
        "last_progress_at": time.time(),  # just now
    }
    r = c.get("/api/jobs/stuck")
    assert r.get_json()["count"] == 0


# ── #79 token-rotation script ───────────────────────────────────────────

_ROTATE = os.path.join(_REPO, "tools", "rotate_token.py")


def test_rotate_token_creates_config(tmp_path):
    cfg = tmp_path / "app_config.json"
    r = subprocess.run(
        [sys.executable, _ROTATE, "--config", str(cfg)],
        capture_output=True, text=True, timeout=15,
        env={**{k: v for k, v in os.environ.items()
                if k != "BD_AUTH_TOKEN"},
             "LC_ALL": "C"})  # row 178: collation must not depend on the host locale
    assert r.returncode == 0, r.stderr
    assert cfg.exists()
    data = json.loads(cfg.read_text(encoding="utf-8"))
    assert len(data["auth_token"]) >= 40


def test_rotate_token_preserves_other_keys(tmp_path):
    cfg = tmp_path / "app_config.json"
    cfg.write_text(json.dumps({"auth_token": "old", "theme": "dark",
                               "some_setting": 42}))
    r = subprocess.run(
        [sys.executable, _ROTATE, "--config", str(cfg)],
        capture_output=True, text=True, timeout=15,
        env={**{k: v for k, v in os.environ.items()
                if k != "BD_AUTH_TOKEN"},
             "LC_ALL": "C"})  # row 178: collation must not depend on the host locale
    assert r.returncode == 0
    data = json.loads(cfg.read_text(encoding="utf-8"))
    assert data["auth_token"] != "old"  # rotated
    assert data["theme"] == "dark"      # preserved
    assert data["some_setting"] == 42


def test_rotate_token_print_only_writes_nothing(tmp_path):
    cfg = tmp_path / "app_config.json"
    r = subprocess.run(
        [sys.executable, _ROTATE, "--config", str(cfg), "--print-only"],
        capture_output=True, text=True, timeout=15,
        env=dict(os.environ))
    assert r.returncode == 0
    assert not cfg.exists()  # nothing written
    # The token is on stdout
    assert len(r.stdout.strip().splitlines()[0]) >= 40


def test_rotate_token_rejects_malformed_config(tmp_path):
    cfg = tmp_path / "app_config.json"
    cfg.write_text("{ this is not json")
    r = subprocess.run(
        [sys.executable, _ROTATE, "--config", str(cfg)],
        capture_output=True, text=True, timeout=15,
        env=dict(os.environ))
    assert r.returncode == 1
    assert "malformed" in r.stderr.lower()


def test_rotate_token_two_runs_differ(tmp_path):
    """Two rotations produce different tokens."""
    cfg = tmp_path / "app_config.json"
    env = {k: v for k, v in os.environ.items() if k != "BD_AUTH_TOKEN"}
    env["LC_ALL"] = "C"  # row 178: collation must not depend on the host locale
    subprocess.run([sys.executable, _ROTATE, "--config", str(cfg)],
                   capture_output=True, timeout=15, env=env)
    t1 = json.loads(cfg.read_text(encoding="utf-8"))["auth_token"]
    subprocess.run([sys.executable, _ROTATE, "--config", str(cfg)],
                   capture_output=True, timeout=15, env=env)
    t2 = json.loads(cfg.read_text(encoding="utf-8"))["auth_token"]
    assert t1 != t2


# ── #99 / #55 env-flag wiring (static checks) ───────────────────────────

def test_downloader_ui_handles_headless_flag():
    src = open(os.path.join(_REPO, "downloader_ui.py"),
               encoding="utf-8").read()
    assert "BD_HEADLESS" in src
    assert "headless/daemon mode" in src


def test_downloader_ui_handles_exit_when_idle():
    src = open(os.path.join(_REPO, "downloader_ui.py"),
               encoding="utf-8").read()
    assert "BD_EXIT_WHEN_IDLE" in src
    assert "_idle_watcher" in src


def test_idle_watcher_has_grace_period():
    """The one-shot watcher must use a grace period, not exit on the
    first idle poll — otherwise it false-exits between jobs."""
    src = open(os.path.join(_REPO, "downloader_ui.py"),
               encoding="utf-8").read()
    assert "BD_IDLE_GRACE_SECONDS" in src
    assert "idle_since" in src

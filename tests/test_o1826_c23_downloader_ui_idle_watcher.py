"""O1826 C23 (TECH_DEBT H02): BD_EXIT_WHEN_IDLE must start the idle watcher.

The `__main__` block of downloader_ui.py referenced `_idle_watcher` before the
function was defined further down the file. Evaluating `target=_idle_watcher`
raised NameError, the surrounding `except Exception` printed
"idle-watcher failed to start: name '_idle_watcher' is not defined", and the
one-shot mode silently never exited.

Each case runs downloader_ui.py as `__main__` in a subprocess with the app,
the WSGI server and `threading.Thread` stubbed, so no port is bound, no app
boots and the real watcher (which sleeps and then calls os._exit) never runs.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

BD_GATE_SCOPE = "module"

_REPO = Path(__file__).resolve().parent.parent

_DRIVER = r"""
import json, runpy, sys, threading, types

pkg = types.ModuleType("bulk_downloader")
pkg.__path__ = []
pkg.__version__ = "0.0.0-test"
app_mod = types.ModuleType("bulk_downloader.app")
served = []

class _App:
    def run(self, **kw):
        served.append("werkzeug")

app_mod.app = _App()
app_mod.boot_once = lambda: None
waitress = types.ModuleType("waitress")
waitress.serve = lambda app, **kw: served.append("waitress")
sys.modules.update({"bulk_downloader": pkg,
                    "bulk_downloader.app": app_mod,
                    "waitress": waitress})

threads = []

class _RecordingThread:
    def __init__(self, target=None, daemon=None, name=None, **kw):
        self.rec = {"target": getattr(target, "__name__", repr(target)),
                    "daemon": daemon, "name": name, "started": False}
        threads.append(self.rec)

    def start(self):
        self.rec["started"] = True

threading.Thread = _RecordingThread
runpy.run_path(sys.argv[1], run_name="__main__")
sys.stdout.flush()
print("RESULT=" + json.dumps({"threads": threads, "served": served}))
"""


def _run_main(tmp_path, exit_when_idle):
    env = {k: v for k, v in os.environ.items()
           if k not in ("BD_EXIT_WHEN_IDLE", "BULK_DOWNLOADER_DEBUG")}
    env.update({
        "LC_ALL": "C.UTF-8",
        "PYTHONIOENCODING": "utf-8",
        "BD_HEADLESS": "1",
        "BULK_DOWNLOADER_LOG_DIR": str(tmp_path / "logs"),
    })
    if exit_when_idle:
        env["BD_EXIT_WHEN_IDLE"] = "1"
    cp = subprocess.run(
        [sys.executable, "-c", _DRIVER, str(_REPO / "downloader_ui.py")],
        cwd=str(tmp_path), env=env, capture_output=True, text=True,
        encoding="utf-8", timeout=60)
    assert cp.returncode == 0, (
        f"downloader_ui __main__ driver rc={cp.returncode}\n"
        f"stdout:\n{cp.stdout}\nstderr:\n{cp.stderr}")
    lines = [l for l in cp.stdout.splitlines() if l.startswith("RESULT=")]
    assert len(lines) == 1, f"no RESULT line\nstdout:\n{cp.stdout}"
    result = json.loads(lines[0][len("RESULT="):])
    # The block ran to the end: the server call after the watcher is reached.
    assert result["served"], f"server never called\nstdout:\n{cp.stdout}"
    return cp.stdout, result["threads"]


def test_exit_when_idle_starts_the_idle_watcher_thread(tmp_path):
    stdout, threads = _run_main(tmp_path, exit_when_idle=True)
    assert "idle-watcher failed to start" not in stdout, (
        "C23-IDLE-WATCHER-NOT-STARTED: BD_EXIT_WHEN_IDLE=1 did not start "
        f"the watcher\nstdout:\n{stdout}")
    watchers = [t for t in threads if t["name"] == "bd-idle-watcher"]
    assert watchers == [{"target": "_idle_watcher", "daemon": True,
                         "name": "bd-idle-watcher", "started": True}], (
        f"C23-IDLE-WATCHER-NOT-STARTED: threads={threads}\nstdout:\n{stdout}")


def test_without_exit_when_idle_no_watcher_thread(tmp_path):
    stdout, threads = _run_main(tmp_path, exit_when_idle=False)
    assert [t for t in threads if t["name"] == "bd-idle-watcher"] == [], (
        f"watcher started without BD_EXIT_WHEN_IDLE: threads={threads}")
    assert "One-shot" not in stdout
    assert "idle-watcher failed to start" not in stdout

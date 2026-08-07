#!/usr/bin/env python3
"""
Bulk Downloader UI  -  see bulk_downloader/__init__.py::__version__
pip install flask playwright httpx && playwright install chromium
Run: python downloader_ui.py  ->  http://localhost:5555

Logs:
  Everything written to stderr (which is most of the app's output) is
  ALSO appended to logs/bulk_downloader.log, with rotation at 10 MB
  per file and 10 historical files retained. Standard `logging` module
  output goes to the same file via a RotatingFileHandler.
  Set BULK_DOWNLOADER_LOG_DIR env var to override the location.

Debug mode:
  Create a file called `debug.flag` in this directory (or run
  enable_debug.bat / disable_debug.bat on Windows). When present:
    - Flask runs with debug=True (interactive traceback page on errors)
    - Werkzeug access log is shown at DEBUG (every request, every header)
    - httpx, urllib3, asyncio, playwright loggers all set to DEBUG
"""
import logging
import logging.handlers
import os
import sys
from pathlib import Path
from datetime import datetime


def _is_debug_mode():
    """Debug mode is on when `debug.flag` exists next to this script,
    OR when the BULK_DOWNLOADER_DEBUG env var is set to anything truthy."""
    here = Path(__file__).parent
    if (here / "debug.flag").exists():
        return True
    return os.environ.get("BULK_DOWNLOADER_DEBUG", "").lower() in ("1", "true", "yes", "on")


def _resolve_log_dir():
    """Determine where to write log files. Order of precedence:
      1. BULK_DOWNLOADER_LOG_DIR env var (full path)
      2. ./logs/ next to this script (created if absent)
    Returns a Path. Falls back to None if creation fails."""
    env = os.environ.get("BULK_DOWNLOADER_LOG_DIR", "").strip()
    if env:
        d = Path(env)
    else:
        d = Path(__file__).parent / "logs"
    try:
        d.mkdir(parents=True, exist_ok=True)
        probe = d / ".write_check"
        probe.write_text("ok"); probe.unlink()
        return d
    except Exception as e:
        sys.stderr.write(f"  warning: could not prepare log dir {d}: {e}\n")
        return None


class _StreamTee:
    """File-like wrapper that writes to multiple underlying streams.
    Used to tee sys.stderr — which the runner uses extensively for
    progress logs — into a log file without changing any caller code.

    Each write is best-effort to each stream; a failure on one stream
    (e.g. log file gets locked) doesn't break the others."""

    def __init__(self, *streams):
        self._streams = streams

    def write(self, data):
        for s in self._streams:
            try: s.write(data); s.flush()
            except Exception: pass
        return len(data) if isinstance(data, str) else 0

    def flush(self):
        for s in self._streams:
            try: s.flush()
            except Exception: pass

    def isatty(self):
        try: return self._streams[0].isatty()
        except Exception: return False

    def fileno(self):
        return self._streams[0].fileno()

    def __getattr__(self, name):
        return getattr(self._streams[0], name)


def _configure_logging(debug):
    """Wire up the standard logging module + tee stderr to a file.

    Two log streams are produced:
      1. logs/bulk_downloader.log — everything written to stderr or via
         logging.* calls. Rotates at 10 MB, keeps 10 files.
      2. console stderr — same content, for live observation."""
    level = logging.DEBUG if debug else logging.INFO
    log_dir = _resolve_log_dir()
    real_stderr = sys.stderr

    handlers = []
    log_path = None
    if log_dir is not None:
        log_path = log_dir / "bulk_downloader.log"
        try:
            file_handler = logging.handlers.RotatingFileHandler(
                str(log_path),
                maxBytes=10 * 1024 * 1024,
                backupCount=10,
                encoding="utf-8",
            )
            file_handler.setFormatter(logging.Formatter(
                "%(asctime)s %(levelname)-7s [%(name)s] %(message)s",
                datefmt="%Y-%m-%d %H:%M:%S",
            ))
            file_handler.setLevel(level)
            handlers.append(file_handler)
        except Exception as e:
            sys.stderr.write(f"  warning: file logging disabled: {e}\n")
            log_path = None

    console_handler = logging.StreamHandler(real_stderr)
    console_handler.setFormatter(logging.Formatter(
        "%(asctime)s %(levelname)-7s [%(name)s] %(message)s",
        datefmt="%H:%M:%S",
    ))
    console_handler.setLevel(level)
    handlers.append(console_handler)

    root = logging.getLogger()
    root.setLevel(level)
    for h in list(root.handlers): root.removeHandler(h)
    for h in handlers: root.addHandler(h)

    if log_path is not None:
        try:
            log_fh = open(str(log_path), "a", encoding="utf-8", buffering=1)
            log_fh.write(f"\n=== {datetime.now().isoformat()}  process started (pid={os.getpid()})  ===\n")
            log_fh.flush()
            sys.stderr = _StreamTee(real_stderr, log_fh)
        except Exception as e:
            real_stderr.write(f"  warning: stderr tee disabled: {e}\n")

    if debug:
        for name in ("werkzeug", "httpx", "httpcore", "urllib3",
                     "asyncio", "playwright", "flask", "flask.app"):
            logging.getLogger(name).setLevel(logging.DEBUG)
        os.environ.setdefault("HTTPX_LOG_LEVEL", "debug")
    else:
        for name in ("werkzeug", "httpx", "httpcore", "urllib3", "asyncio"):
            logging.getLogger(name).setLevel(logging.WARNING)

    return log_path


def _serve_wsgi(app, host, port, debug, *, waitress_serve=None,
                werkzeug_run=None, threads=None):
    """v3.66.794 (F0.4): choose the WSGI server and start it.

    Production (debug off) serves under **waitress** -- a real threaded WSGI
    server that survives the concurrent load the single-process werkzeug dev
    server crashes under (Task-E fuzz recon: ffuf `Errors: 64`, nuclei hangs
    it). Debug keeps werkzeug so its interactive reloader/traceback still work.

    Fail-open: if waitress is not installed the app still boots on the werkzeug
    dev server (with a one-line notice) rather than refusing to start. Note we
    do NOT pass `_quiet=True` -- waitress 3.0.2 rejects it (`ValueError: Unknown
    adjustment`), and since that is not an ImportError it would escape the
    fallback and crash boot. Waitress is quiet by default.

    Thread sizing (`BD_WSGI_THREADS`, default 30) matters: each long-lived
    `/api/stream` SSE connection occupies one waitress worker thread for its
    entire lifetime, so N concurrent dashboards need > N threads to avoid
    starving normal requests. The default is set well above the werkzeug pool
    (measured: threads=8 starved at 8 concurrent dashboards; 30 gives generous
    headroom for multi-tab/multi-user use). The app's own runner/scheduler/
    keepalive threads are separate daemon threads and do NOT draw from this
    pool.

    Returns the name of the server actually used -- the serve callables block
    forever, so they are injected in tests and the return value makes the
    selection assertable without binding a port.
    """
    import os as _os
    if threads is None:
        try:
            threads = int(_os.environ.get("BD_WSGI_THREADS", "30") or "30")
        except ValueError:
            threads = 30
    if not debug:
        serve = waitress_serve
        if serve is None:
            try:
                from waitress import serve as serve  # noqa: F811
            except ImportError:
                serve = None
        if serve is not None:
            serve(app, host=host, port=port, threads=threads)
            return "waitress"
        print("  (waitress not installed -- using the werkzeug dev server; "
              "install waitress for production concurrency)")
    # Resolve the werkzeug runner lazily -- only when we actually fall through
    # to it, so the waitress path never touches app.run.
    if werkzeug_run is None:
        werkzeug_run = app.run
    werkzeug_run(host=host, port=port, debug=debug, use_reloader=False)
    return "werkzeug"


if __name__ == "__main__":
    debug = _is_debug_mode()
    log_path = _configure_logging(debug)
    # Import AFTER logging is configured so the import-time stderr writes
    # (watcher thread start, restored sites count, etc.) get teed.
    from bulk_downloader.app import app, boot_once
    # v3.66.926 (item 11): this used to be a bare db_init(). The startup DB
    # work no longer runs at import, so the SERVICE must ask for it explicitly
    # rather than wait for the first request -- bg_scheduler starts at import
    # and its periodic tasks touch the DB on timers, so a request-triggered
    # boot would leave a window where a scheduled task meets an unmigrated
    # schema. boot_once() is a superset of db_init() and is idempotent, so the
    # before_request hook downstream is a no-op after this.
    boot_once()
    # v3.43.16: respect BD_HOST and BD_PORT env vars so deployments can
    # restrict the bind interface (e.g. 127.0.0.1 for ssh-tunnel-only)
    # or pick a different port without code changes. Defaults preserve
    # legacy behavior (0.0.0.0:5555 — accessible from LAN).
    import os as _os
    bind_host = _os.environ.get("BD_HOST", "0.0.0.0").strip() or "0.0.0.0"
    try:
        bind_port = int(_os.environ.get("BD_PORT", "5555") or "5555")
    except ValueError:
        bind_port = 5555
    # Banner shows the URL the user should actually open. 0.0.0.0 isn't a
    # real address you can paste — show localhost in that case.
    _display_host = "localhost" if bind_host in ("0.0.0.0", "") else bind_host
    _url = f"http://{_display_host}:{bind_port}"
    # v3.47.0: pull the version from the package rather than hardcoding
    # in the banner — the banner used to lag the actual version.
    try:
        from bulk_downloader import __version__ as _bd_ver
    except Exception:
        _bd_ver = "?.?.?"
    _ver_line = f"|   Bulk Downloader  v{_bd_ver}".ljust(38) + "|"

    # v3.56 (Phase 8, #99): daemon-only mode. When BD_HEADLESS=1 the
    # process is running as a background service (systemd, Task
    # Scheduler, nohup) — there's no operator watching a terminal, so
    # the box-drawing banner is just noise in a service log. Print one
    # clean line instead and skip the LAN-IP probe.
    _headless = _os.environ.get("BD_HEADLESS", "").strip().lower() in (
        "1", "true", "yes", "on")

    # v3.56 (Phase 8, #55): one-shot mode. When BD_EXIT_WHEN_IDLE=1 a
    # background watcher polls every runner; once all queues are empty
    # and no downloads have been active for the grace period, the
    # process exits 0. Lets the app be driven from cron / a script:
    # "queue some URLs, run BD, it exits when done."
    _exit_when_idle = _os.environ.get(
        "BD_EXIT_WHEN_IDLE", "").strip().lower() in (
        "1", "true", "yes", "on")

    if _headless:
        print(f"Bulk Downloader v{_bd_ver} — listening on {_url} "
              f"(headless/daemon mode)")
        if log_path is not None:
            print(f"Log: {log_path}")
        if _exit_when_idle:
            print("One-shot: will exit when all queues drain.")
    else:
        print(r"""
  +====================================+""")
        print("  " + _ver_line)
        print("  +====================================+")
        print(f"  |  Open: {_url}".ljust(38) + "|")
        print( "  |  Stop: Ctrl+C                      |")
        print( "  +====================================+")
        if bind_host == "0.0.0.0":
            # Show LAN-reachable URLs too so the user knows where to
            # connect from other devices. Best-effort.
            try:
                import socket as _sock
                hn = _sock.gethostname()
                with _sock.socket(_sock.AF_INET,
                                  _sock.SOCK_DGRAM) as _s:
                    _s.settimeout(0)
                    try:
                        _s.connect(("8.8.8.8", 80))
                        lan_ip = _s.getsockname()[0]
                    except Exception:
                        lan_ip = ""
                if lan_ip and lan_ip not in ("127.0.0.1", "0.0.0.0"):
                    print(f"  Remote: http://{lan_ip}:{bind_port}"
                          f"   (from other devices on the LAN)")
                if hn and hn not in ("localhost", lan_ip):
                    print(f"  Hostname: http://{hn}:{bind_port}")
            except Exception:
                pass
        if log_path is not None:
            print(f"  Log: {log_path}")
        if debug:
            print("  DEBUG MODE ON  -  verbose logging, "
                  "interactive tracebacks")
            print("     (delete debug.flag or run disable_debug.bat "
                  "to turn off)")
        if _exit_when_idle:
            print("  One-shot: will exit when all queues drain.")
    print()

    # v3.56 (Phase 8, #55): start the idle-watcher thread before the
    # server. It imports lazily so a missing app module can't break
    # normal startup.
    if _exit_when_idle:
        try:
            import threading as _thr
            _thr.Thread(target=_idle_watcher, daemon=True,
                        name="bd-idle-watcher").start()
        except Exception as _e:
            print(f"  (idle-watcher failed to start: {_e})")

    # v3.66.794 (F0.4): waitress in production, werkzeug in debug. See
    # _serve_wsgi -- fail-open to the dev server if waitress is absent.
    _serve_wsgi(app, bind_host, bind_port, debug)


def _idle_watcher():
    """v3.56 (Phase 8, #55): poll runners; exit the process once every
    queue is empty and nothing has been downloading for the grace
    period.

    Grace period (default 30s) guards against a false exit in the gap
    between one job finishing and the next starting. Overridable via
    BD_IDLE_GRACE_SECONDS.
    """
    import os as _os
    import time as _time
    try:
        grace = float(_os.environ.get("BD_IDLE_GRACE_SECONDS", "30"))
    except ValueError:
        grace = 30.0
    # Don't even start measuring idleness until the app has had a
    # moment to load runners + restore queues.
    _time.sleep(10.0)
    idle_since = None
    while True:
        _time.sleep(5.0)
        try:
            from bulk_downloader import app as _app
            runners = getattr(_app, "runners", {}) or {}
            busy = False
            for r in runners.values():
                try:
                    st = r.get_status(light=True)
                    if (int(st.get("queued") or 0) > 0
                            or int(st.get("active") or 0) > 0
                            or getattr(r, "_state", "") == "running"):
                        busy = True
                        break
                except Exception:
                    # A runner we can't read — treat as busy, don't
                    # exit on uncertain state.
                    busy = True
                    break
        except Exception:
            busy = True  # app not ready yet
        now = _time.time()
        if busy:
            idle_since = None
            continue
        if idle_since is None:
            idle_since = now
            continue
        if now - idle_since >= grace:
            print(f"\n  All queues drained — one-shot mode exiting "
                  f"(idle {grace:.0f}s).")
            _os._exit(0)

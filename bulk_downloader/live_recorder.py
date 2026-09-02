"""v3.43.62: Live-stream recording runtime.

Several adult sites (Chaturbate, Stripchat, BongaCams, Fansly Live)
broadcast as live streams rather than serving downloadable files. The
v3.43.61 template catalog routed their URLs to limitation stubs --
this module makes those stubs functional.

# Architecture

The standard SiteRunner worker model expects a page-then-extract flow:
load URL, find a download button, fetch the file, done. Live streams
don't fit -- the "URL" is a model's room and the "file" is a continuous
HLS stream that may or may not be live at any given moment, and may
disconnect and resume mid-recording.

We model live recording as a separate runtime alongside SiteRunner:

  - A `LiveRecorder` instance manages 0-N `Recording` subprocesses.
  - Each `Recording` represents one (site_id, room/model) pair under
    active capture. The subprocess is `streamlink` (preferred) or
    `ffmpeg` (fallback), recording into a `.ts` or `.mp4` file.
  - Disconnect/reconnect is the normal case -- a model going offline
    for 30s and coming back is not a failure. We tolerate up to
    DISCONNECT_TOLERANCE_S of "stream not live" before declaring the
    recording finished.
  - A scheduler thread polls each active room every POLL_INTERVAL_S to
    check live status; brings up a recorder when a watched room goes
    live, tears it down when it stays offline past the tolerance window.

# State persistence

Active recordings persist to disk at `recordings.json` so a server
restart doesn't lose pending captures. On startup we re-read this and
re-arm any rooms that were being watched.

# External dependencies

`streamlink` is the preferred backend (well-tested for adult cam sites,
auto-handles HLS variant selection, supports many cam-site plugins).
`ffmpeg` is the fallback if streamlink isn't installed but ffmpeg is.

If neither is present, this module is fail-open: it logs a warning at
init time and `is_available()` returns False. The UI surfaces this and
the limitation stubs in templates.py stay limitation stubs.

# Process safety

Subprocesses are started with `subprocess.Popen` and tracked in `_active`
keyed by recording_id. On shutdown we send SIGTERM, wait, then SIGKILL.
On Windows we use `CTRL_BREAK_EVENT` via `CREATE_NEW_PROCESS_GROUP`.

# Disk-pressure guard

Recording 8K HEVC at multi-GB/hour can fill a disk fast. Before starting
a new recording, we check `disk_threshold_gb` on the site config; if the
target volume has less than that free, we refuse to start and emit a
push notification. (Reuses the same per-site setting workers already use.)
"""
from __future__ import annotations

import json
import os
import re
import shutil
import signal
import subprocess
import sys
import threading
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable, Optional


# ─── Tunables ───────────────────────────────────────────────────────

# Tunables read at CALL TIME (store key > env seed > default) so a Settings
# write takes effect on the next poll/launch without a restart (v3.66.503 GUI
# parity). Previously module-level constants bound once at import.

# Poll interval per watched room to check if it's live. Cam sites get
# touchy about request frequency; 60s is a safe default.
def _poll_interval_s() -> int:
    from .runtime_flags import num
    return num("live_poll_interval_s", "BD_LIVE_POLL_INTERVAL_S", 60, int)


# How long a recording can be "stream not live" before we declare it
# done and tear down. Tolerates short disconnects mid-broadcast.
def _disconnect_tolerance_s() -> int:
    from .runtime_flags import num
    return num("live_disconnect_tolerance_s",
               "BD_LIVE_DISCONNECT_TOLERANCE_S", 180, int)


# Hard cap on simultaneous active recordings. Each subprocess uses ~50MB
# RAM and one network socket; with 768GB RAM the cap is for sanity, not
# resources.
def _max_active_recordings() -> int:
    from .runtime_flags import num
    return num("live_max_active_recordings",
               "BD_LIVE_MAX_ACTIVE_RECORDINGS", 32, int)


# Streamlink/ffmpeg launch timeout -- if the subprocess hasn't written
# anything to its output file in this window after launch, we consider
# it stuck and kill it. (No live consumer yet; getter ready for one.)
def _launch_timeout_s() -> int:
    from .runtime_flags import num
    return num("live_launch_timeout_s", "BD_LIVE_LAUNCH_TIMEOUT_S", 45, int)

# Where active-recording metadata persists across restarts.
STATE_FILE_NAME = "recordings.json"


# ─── Backend detection ──────────────────────────────────────────────

_BACKEND_CHECK_LOCK = threading.Lock()
_backend_cache: Optional[dict] = None


def _ffmpeg_bin():
    """MOD-4: the one resolver. Imported lazily -- live_recorder is imported early
    and must not pull the config store in at module import time."""
    from . import ffmpeg_bin
    return ffmpeg_bin


def _detect_backends() -> dict:
    """Returns {'streamlink': path|None, 'ffmpeg': path|None}."""
    global _backend_cache
    with _BACKEND_CHECK_LOCK:
        if _backend_cache is not None:
            return _backend_cache
        _backend_cache = {
            "streamlink": shutil.which("streamlink"),
            "ffmpeg": _ffmpeg_bin().ffmpeg(),   # MOD-4: honours the pin
        }
        return _backend_cache


def _reset_backend_cache_for_tests() -> None:
    """Test hook -- forces re-detection on next call."""
    global _backend_cache
    with _BACKEND_CHECK_LOCK:
        _backend_cache = None


def is_available() -> bool:
    """True if at least one recording backend (streamlink or ffmpeg) is
    on PATH. The live-recording feature is fail-open: if False, the UI
    shows a "not configured" hint and the limitation stubs stay limitations."""
    b = _detect_backends()
    return bool(b.get("streamlink") or b.get("ffmpeg"))


def preferred_backend() -> Optional[str]:
    """Returns 'streamlink' if available, else 'ffmpeg' if available,
    else None. Streamlink wins because it has cam-site-specific plugins
    that handle the HLS variant selection automatically."""
    b = _detect_backends()
    if b.get("streamlink"):
        return "streamlink"
    if b.get("ffmpeg"):
        return "ffmpeg"
    return None


# ─── URL → site/room parsing ────────────────────────────────────────

# Patterns to extract (site, room_id) from a URL the user pastes. Each
# entry: (host_regex, room_extractor). The host regex matches the URL's
# netloc; the extractor pulls the room/model identifier from the path.
_SITE_PATTERNS: list[tuple[re.Pattern, Callable[[str], Optional[str]]]] = [
    (re.compile(r"(?:^|\.)chaturbate\.com$", re.I),
     lambda path: _first_path_segment(path)),
    (re.compile(r"(?:^|\.)stripchat\.com$", re.I),
     lambda path: _first_path_segment(path)),
    (re.compile(r"(?:^|\.)bongacams\.com$", re.I),
     lambda path: _first_path_segment(path)),
    (re.compile(r"(?:^|\.)fansly\.com$", re.I),
     lambda path: _first_path_segment(path)),
]


def _first_path_segment(path: str) -> Optional[str]:
    """Pull the first non-empty path segment. None if path is just '/'."""
    parts = [p for p in (path or "").split("/") if p]
    if not parts:
        return None
    # Reject if it looks like a generic page (e.g., /tags, /search) --
    # real room slugs are alphanumeric+underscore, no special chars.
    candidate = parts[0]
    # Cam-site room/model slugs: a-z, 0-9, _, length 2-40. Reject
    # anything that doesn't fit -- those are probably category pages.
    if not re.match(r"^[A-Za-z0-9_-]{2,40}$", candidate):
        return None
    return candidate.lower()


def parse_live_url(url: str) -> Optional[tuple[str, str]]:
    """Parse a URL like https://chaturbate.com/somemodel/ into
    (site_id, room_id). Returns None if the URL doesn't match any
    known live-cam pattern.

    site_id is the canonical bulk-downloader template id (e.g.,
    'chaturbate', 'stripchat_live') -- but here we use the short form
    matching the URL's eTLD+1 prefix because that's what the recorder
    cares about (one streamlink plugin per site)."""
    if not isinstance(url, str):
        return None
    # Lightweight URL parse -- avoid stdlib urlparse for the host match
    # because we just want netloc + path.
    m = re.match(r"^https?://([^/]+)(/.*)?$", url.strip(), re.I)
    if not m:
        return None
    host = m.group(1).lower()
    path = m.group(2) or "/"
    # Strip port if present.
    if ":" in host:
        host = host.split(":", 1)[0]
    for host_re, extractor in _SITE_PATTERNS:
        if host_re.search(host):
            room = extractor(path)
            if room:
                # Map host to a canonical short site id. Strip
                # subdomain (www.) and TLD components.
                site_id = _canonical_site_id(host)
                return (site_id, room)
            return None
    return None


def _host_on_cam_allowlist(url: str) -> bool:
    """F-CAP01-01: True iff url's host matches a known live-cam site pattern
    (_SITE_PATTERNS) -- the SAME host allowlist parse_live_url enforces. The
    site_override/room_override path skips parse_live_url, so watch() calls this
    to keep an override from aiming streamlink/ffmpeg at an arbitrary host."""
    if not isinstance(url, str):
        return False
    m = re.match(r"^https?://([^/]+)(/.*)?$", url.strip(), re.I)
    if not m:
        return False
    host = m.group(1).lower().split(":", 1)[0]
    return any(host_re.search(host) for host_re, _ in _SITE_PATTERNS)


def _canonical_site_id(host: str) -> str:
    """Reduce 'www.chaturbate.com' / 'chaturbate.com' both to 'chaturbate'."""
    # Strip any leading www. then take the second-to-last label.
    h = host.lower().lstrip(".")
    if h.startswith("www."):
        h = h[4:]
    labels = h.split(".")
    if len(labels) >= 2:
        return labels[-2]
    return h


# ─── State model ────────────────────────────────────────────────────

@dataclass
class Recording:
    """One actively-watched (site, room) pair."""
    recording_id: str
    site: str           # canonical short id, e.g., "chaturbate"
    room: str           # model/room slug
    url: str            # the original URL the user pasted
    output_path: str    # where the file lands
    started_at: float
    last_live_ts: Optional[float] = None  # last time the room was confirmed live
    last_offline_ts: Optional[float] = None
    backend: str = ""                     # "streamlink" or "ffmpeg"
    pid: Optional[int] = None
    state: str = "pending"
    # pending  -- watch armed, not yet recording (room offline)
    # recording -- subprocess running
    # finished -- naturally ended (offline past tolerance, success)
    # failed   -- subprocess exited with error
    # cancelled -- user-cancelled
    last_error: Optional[str] = None
    bytes_written: int = 0
    poll_count: int = 0

    def to_dict(self) -> dict:
        d = asdict(self)
        # Don't persist pid -- it's only meaningful for live subprocesses.
        # On restart, all PIDs are stale.
        d["pid"] = None
        return d


_recordings: dict[str, Recording] = {}
_subprocesses: dict[str, subprocess.Popen] = {}  # recording_id -> Popen
_lock = threading.RLock()

# Set to True once the scheduler thread has started.
_scheduler_started = False
_scheduler_stop = threading.Event()
_scheduler_thread: Optional[threading.Thread] = None

# Path where recordings.json lives (set on init).
_state_dir: Optional[Path] = None

# Hook for push notifications (set by app.py at startup).
_push_notifier: Optional[Callable[[str, str], None]] = None

# Hook for disk-pressure check (set by app.py at startup).
_disk_check: Optional[Callable[[str], Optional[float]]] = None


# ─── Public init / hooks ────────────────────────────────────────────

def init(state_dir: str, push_notifier: Optional[Callable] = None,
         disk_check: Optional[Callable] = None) -> None:
    """Wire up persistence directory and optional hooks. Idempotent --
    safe to call multiple times. Must be called before start_scheduler()."""
    global _state_dir, _push_notifier, _disk_check
    p = Path(state_dir)
    p.mkdir(parents=True, exist_ok=True)
    _state_dir = p
    if push_notifier is not None:
        _push_notifier = push_notifier
    if disk_check is not None:
        _disk_check = disk_check
    _load_state()


def _state_file() -> Optional[Path]:
    if _state_dir is None:
        return None
    return _state_dir / STATE_FILE_NAME


def _load_state() -> None:
    """Re-read recordings.json on startup; re-arm any watches that were
    active when the server stopped. Subprocesses are NOT restored (PIDs
    are stale across restarts) -- they get re-spawned when the scheduler
    next sees the room go live."""
    f = _state_file()
    if not f or not f.exists():
        return
    try:
        text = f.read_text(encoding="utf-8")
        data = json.loads(text)
    except Exception as e:
        sys.stderr.write(
            f"[live-recorder] failed to read {f}: {e} -- starting fresh\n"
        )
        return
    if not isinstance(data, dict):
        return
    items = data.get("recordings", [])
    if not isinstance(items, list):
        return
    with _lock:
        _recordings.clear()
        for raw in items:
            try:
                if not isinstance(raw, dict):
                    continue
                rid = raw.get("recording_id")
                if not rid or not isinstance(rid, str):
                    continue
                # Audit 2026-05 (v3.43.62): re-validate room + url when
                # restoring. recordings.json lives in the bulk-downloader
                # data dir which is user-writable; we don't trust its
                # contents on restart, especially across upgrades where
                # the schema may have tightened. Reject entries whose
                # room or url wouldn't pass watch() today.
                room_v = raw.get("room")
                if not _looks_like_safe_room(room_v):
                    sys.stderr.write(
                        f"[live-recorder] dropping recording {rid!r}: bad room\n"
                    )
                    continue
                url_v = raw.get("url")
                if not isinstance(url_v, str) or not re.match(
                        r"^https?://[A-Za-z0-9.\-/_?=&%:]+$", url_v):
                    sys.stderr.write(
                        f"[live-recorder] dropping recording {rid!r}: bad url\n"
                    )
                    continue
                # Recovered recordings always start as 'pending' --
                # we'll re-spawn when the room next goes live.
                raw["state"] = "pending"
                raw["pid"] = None
                # Defensive: drop unknown keys to avoid TypeError on
                # Recording(...) construction if schema changes.
                kwargs = {k: v for k, v in raw.items()
                          if k in Recording.__dataclass_fields__}
                _recordings[rid] = Recording(**kwargs)
            except Exception as e:
                sys.stderr.write(
                    f"[live-recorder] skipping malformed entry: {e}\n"
                )


def _save_state() -> None:
    """Atomic write of current recordings to disk."""
    f = _state_file()
    if not f:
        return
    with _lock:
        payload = {
            "recordings": [r.to_dict() for r in _recordings.values()],
            "saved_at": time.time(),
        }
    try:
        tmp = f.with_suffix(f.suffix + ".tmp")
        tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        os.replace(str(tmp), str(f))
    except Exception as e:
        sys.stderr.write(f"[live-recorder] state save failed: {e}\n")


# ─── Public API: watch / unwatch / list ─────────────────────────────

def watch(url: str, output_dir: str,
          site_override: Optional[str] = None,
          room_override: Optional[str] = None) -> dict:
    """Arm a watch for a live URL. Returns a status dict.

    If the URL doesn't parse as a known live-cam URL, returns
    {'ok': False, 'error': 'not_a_live_url', ...}. Caller can fall back
    to standard SiteRunner.

    If the recording backend (streamlink/ffmpeg) isn't available,
    returns {'ok': False, 'error': 'no_backend', ...}.

    Otherwise creates a Recording in 'pending' state. The scheduler
    will detect when the room goes live and start the subprocess."""
    if not is_available():
        return {
            "ok": False,
            "error": "no_backend",
            "message": (
                "No live-stream recording backend found. Install "
                "`streamlink` (preferred) or `ffmpeg` and restart."
            ),
        }
    parsed = parse_live_url(url) if not (site_override and room_override) else None
    if parsed is None and not (site_override and room_override):
        return {"ok": False, "error": "not_a_live_url",
                "message": "URL doesn't match any known live-cam pattern."}
    site, room = (site_override, room_override) if site_override and room_override else parsed
    if not _looks_like_safe_room(room):
        return {"ok": False, "error": "invalid_room",
                "message": "Room slug contains disallowed characters."}
    # Audit 2026-05 (v3.43.62): validate url shape at the entry point
    # rather than waiting for _build_cmd. Otherwise a malformed URL
    # (e.g. via API mis-call) gets stored, persisted to recordings.json,
    # surfaced in the UI list, and only fails when the scheduler tries
    # to spawn a subprocess -- by which time we've already de-duped any
    # subsequent watch() of the SAME room with a valid URL.
    if not isinstance(url, str) or not re.match(
            r"^https?://[A-Za-z0-9.\-/_?=&%:]+$", url):
        return {"ok": False, "error": "invalid_url",
                "message": "URL contains characters not permitted for live URLs."}
    # F-CAP01-01: the override path skipped parse_live_url (which host-gates via
    # _SITE_PATTERNS), so enforce the SAME cam-site host allowlist here. An
    # override may relabel site/room but must not aim streamlink/ffmpeg at an
    # arbitrary (e.g. internal / cloud-metadata) host. The non-override path
    # already matched a pattern in parse_live_url.
    if (site_override and room_override) and not _host_on_cam_allowlist(url):
        return {"ok": False, "error": "host_not_allowed",
                "message": "URL host is not a recognized live-cam site."}
    out_path = _build_output_path(output_dir, site, room)
    with _lock:
        if len(_recordings) >= _max_active_recordings():
            return {"ok": False, "error": "too_many_active",
                    "message": f"Cap reached ({_max_active_recordings()})."}
        # Dedupe -- if a recording for this (site, room) is already
        # active (any non-terminal state), refuse to add a second one.
        for existing in _recordings.values():
            if (existing.site == site and existing.room == room
                    and existing.state in ("pending", "recording")):
                return {"ok": False, "error": "already_watching",
                        "recording_id": existing.recording_id}
        rid = _new_recording_id(site, room)
        rec = Recording(
            recording_id=rid,
            site=site,
            room=room,
            url=url,
            output_path=out_path,
            started_at=time.time(),
        )
        _recordings[rid] = rec
    _save_state()
    return {"ok": True, "recording_id": rid, "site": site, "room": room,
            "output_path": out_path}


def _looks_like_safe_room(room: str) -> bool:
    """Strict allowlist on room slugs since they get interpolated into
    streamlink/ffmpeg command lines. Same constraint as the URL parser."""
    return bool(room and isinstance(room, str)
                and re.match(r"^[A-Za-z0-9_-]{2,40}$", room))


def _new_recording_id(site: str, room: str) -> str:
    # Compose a stable id but include a millisecond timestamp so re-
    # watching the same room after a previous recording finished gets
    # a fresh id.
    return f"{site}_{room}_{int(time.time() * 1000)}"


def _build_output_path(output_dir: str, site: str, room: str) -> str:
    """Pick a filename in output_dir. Extension is .ts for streamlink
    (raw HLS segments) and .ts for ffmpeg's mpegts container -- both
    are losslessly remuxable to .mp4 with `ffmpeg -c copy`."""
    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = time.strftime("%Y%m%d_%H%M%S")
    name = f"{site}__{room}__{ts}.ts"
    return str(out_dir / name)


def unwatch(recording_id: str) -> dict:
    """Cancel a watch. If a subprocess is running, terminate it. The
    partial file is left on disk for the user."""
    with _lock:
        rec = _recordings.get(recording_id)
        if rec is None:
            return {"ok": False, "error": "not_found"}
        if rec.state in ("finished", "failed", "cancelled"):
            return {"ok": True, "already": rec.state}
        rec.state = "cancelled"
        proc = _subprocesses.pop(recording_id, None)
    if proc is not None:
        _terminate_subprocess(proc)
    _save_state()
    return {"ok": True}


def list_recordings(include_finished: bool = True) -> list[dict]:
    """Return all recordings as dicts, sorted newest-first."""
    with _lock:
        items = list(_recordings.values())
    if not include_finished:
        items = [r for r in items
                 if r.state not in ("finished", "cancelled", "failed")]
    items.sort(key=lambda r: r.started_at, reverse=True)
    return [r.to_dict() for r in items]


def get_recording(recording_id: str) -> Optional[dict]:
    with _lock:
        rec = _recordings.get(recording_id)
        return rec.to_dict() if rec else None


# ─── Scheduler thread ───────────────────────────────────────────────

def start_scheduler() -> bool:
    """Spin up the scheduler thread. Idempotent. Returns False if
    BD_DISABLE_KEEPALIVE is set (test harness disables daemon threads)."""
    global _scheduler_started, _scheduler_thread
    if os.environ.get("BD_DISABLE_KEEPALIVE", "").lower() in ("1", "true", "yes"):
        return False
    with _lock:
        if _scheduler_started:
            return True
        _scheduler_started = True
    _scheduler_stop.clear()
    _scheduler_thread = threading.Thread(
        target=_scheduler_loop, name="live-recorder-scheduler", daemon=True
    )
    _scheduler_thread.start()
    return True


def stop_scheduler(timeout: float = 5.0) -> None:
    """Signal the scheduler to stop and tear down all subprocesses.
    Used on shutdown and in tests."""
    global _scheduler_started
    _scheduler_stop.set()
    if _scheduler_thread is not None:
        _scheduler_thread.join(timeout=timeout)
    # Tear down any running subprocesses.
    with _lock:
        rids = list(_subprocesses.keys())
    for rid in rids:
        with _lock:
            proc = _subprocesses.pop(rid, None)
            rec = _recordings.get(rid)
        if proc is not None:
            _terminate_subprocess(proc)
        if rec is not None and rec.state == "recording":
            with _lock:
                rec.state = "finished"
    _scheduler_started = False


def _scheduler_loop() -> None:
    """Main loop: every POLL_INTERVAL_S, walk pending+recording rooms
    and react to state changes."""
    while not _scheduler_stop.is_set():
        try:
            _scheduler_tick()
        except Exception as e:
            sys.stderr.write(
                f"[live-recorder] scheduler tick failed: {e}\n"
            )
        # Sleep in small chunks so stop() is responsive.
        for _ in range(max(1, _poll_interval_s())):
            if _scheduler_stop.is_set():
                return
            time.sleep(1.0)


def _scheduler_tick() -> None:
    """One pass: check each pending/recording entry and update."""
    with _lock:
        snapshot = [(rid, rec) for rid, rec in _recordings.items()
                    if rec.state in ("pending", "recording")]
    for rid, rec in snapshot:
        try:
            _process_recording(rid, rec)
        except Exception as e:
            sys.stderr.write(
                f"[live-recorder] process_recording failed for {rid}: {e}\n"
            )


def _process_recording(rid: str, rec: Recording) -> None:
    """Decide what to do for one recording on this tick."""
    rec.poll_count += 1
    if rec.state == "recording":
        _check_recording_health(rid, rec)
        return
    # state == "pending" -- check if room is live; if so, spawn.
    if _is_room_live(rec.site, rec.room, rec.url):
        _spawn_recording(rid, rec)


def _check_recording_health(rid: str, rec: Recording) -> None:
    """Subprocess sanity: still running? Output growing? Past tolerance?"""
    with _lock:
        proc = _subprocesses.get(rid)
    if proc is None:
        # Race: state says recording but proc is gone. Treat as failed.
        with _lock:
            rec.state = "failed"
            rec.last_error = "subprocess vanished"
        _save_state()
        return
    ret = proc.poll()
    if ret is not None:
        # Subprocess exited. ret==0 is clean (stream ended). Non-zero
        # might be a real failure or just the stream going offline.
        # For cam sites both look the same to streamlink, so we treat
        # 0 and 1 as clean and others as failure.
        with _lock:
            _subprocesses.pop(rid, None)
            rec.bytes_written = _safe_file_size(rec.output_path)
            if ret in (0, 1):
                rec.state = "finished"
            else:
                rec.state = "failed"
                rec.last_error = f"exit code {ret}"
        _save_state()
        return
    # Still running -- update bytes_written for UI display, check
    # disconnect tolerance based on file growth.
    size = _safe_file_size(rec.output_path)
    if size > rec.bytes_written:
        rec.bytes_written = size
        rec.last_live_ts = time.time()
        rec.last_offline_ts = None
    else:
        # File not growing -- possibly offline.
        now = time.time()
        if rec.last_offline_ts is None:
            rec.last_offline_ts = now
        elif now - rec.last_offline_ts > _disconnect_tolerance_s():
            # Past tolerance window: terminate and mark finished.
            sys.stderr.write(
                f"[live-recorder] {rid}: no file growth for "
                f"{_disconnect_tolerance_s()}s -- finalizing\n"
            )
            with _lock:
                proc2 = _subprocesses.pop(rid, None)
                rec.state = "finished"
            if proc2 is not None:
                _terminate_subprocess(proc2)
            _save_state()


def _is_room_live(site: str, room: str, url: str) -> bool:
    """Probe whether a cam room is currently live.

    For chaturbate/stripchat/bongacams we'd ideally hit a JSON endpoint,
    but those endpoints change frequently and aren't documented. As a
    robust fallback we use streamlink itself: `streamlink --json <url>`
    exits 0 if a stream is available, non-zero if not. This is more
    request-heavy than an HTML probe but it's the canonical signal.

    Returns False on any error -- conservative is correct: a false
    negative means we wait POLL_INTERVAL_S and try again; a false
    positive would spawn a doomed subprocess.
    """
    backend = preferred_backend()
    if backend != "streamlink":
        # Without streamlink we can't reliably check live status; the
        # caller will spawn the subprocess optimistically and it'll
        # exit quickly if the room is offline.
        return True
    try:
        result = subprocess.run(
            ["streamlink", "--json", url],
            capture_output=True,
            timeout=20,
            check=False,
        )
        return result.returncode == 0
    except subprocess.TimeoutExpired:
        return False
    except Exception:
        return False


def _spawn_recording(rid: str, rec: Recording) -> None:
    """Launch the recording subprocess for `rec`. Updates state to
    'recording' on success or 'failed' on launch error."""
    backend = preferred_backend()
    if backend is None:
        with _lock:
            rec.state = "failed"
            rec.last_error = "no backend"
        _save_state()
        return
    # Disk-pressure check.
    if _disk_check is not None:
        try:
            out_dir = str(Path(rec.output_path).parent)
            free_gb = _disk_check(out_dir)
            if free_gb is not None and free_gb < 5.0:
                sys.stderr.write(
                    f"[live-recorder] {rid}: low disk ({free_gb:.1f}GB)"
                    f" -- refusing to start\n"
                )
                with _lock:
                    rec.state = "failed"
                    rec.last_error = f"low_disk: {free_gb:.1f}GB free"
                _save_state()
                _maybe_push(
                    "Live recording skipped",
                    f"{rec.site}/{rec.room}: only {free_gb:.1f}GB free",
                )
                return
        except Exception as e:
            sys.stderr.write(f"[live-recorder] disk check failed: {e}\n")
    cmd = _build_cmd(backend, rec)
    if cmd is None:
        # Resolved BEFORE taking _lock: _refusal_reason consults the backend
        # cache behind its own lock, and nothing needs the two held together.
        reason = _refusal_reason(backend)
        with _lock:
            rec.state = "failed"
            rec.last_error = reason
        _save_state()
        return
    try:
        kwargs: dict[str, Any] = dict(
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            stdin=subprocess.DEVNULL,
        )
        if sys.platform.startswith("win"):
            # CREATE_NEW_PROCESS_GROUP so we can send CTRL_BREAK on stop.
            kwargs["creationflags"] = 0x00000200  # CREATE_NEW_PROCESS_GROUP
        else:
            kwargs["start_new_session"] = True
        proc = subprocess.Popen(cmd, **kwargs)
    except FileNotFoundError as e:
        with _lock:
            rec.state = "failed"
            rec.last_error = f"backend not found: {e}"
        _save_state()
        return
    except Exception as e:
        with _lock:
            rec.state = "failed"
            rec.last_error = f"spawn failed: {e}"
        _save_state()
        return
    with _lock:
        _subprocesses[rid] = proc
        rec.backend = backend
        rec.pid = proc.pid
        rec.state = "recording"
        rec.last_live_ts = time.time()
        rec.last_offline_ts = None
    _save_state()


def _refusal_reason(backend: str) -> str:
    """Name the step that made :func:`_build_cmd` refuse.

    Two unrelated conditions share that ``None``: a room/URL that failed the
    metachar re-check, and a backend binary that resolves nowhere. They lead to
    opposite actions -- fix the request, or install/repoint the binary -- so a
    single "unable to build command" sends the operator to inspect a URL that is
    fine (CLAUDE.md A7)."""
    if not _detect_backends().get(backend):
        return f"backend_unresolved: {backend}"
    return "unable to build command"


def _build_cmd(backend: str, rec: Recording) -> Optional[list[str]]:
    """Construct the subprocess command line. Returns None on bad input.

    Defense: rec.url is validated by parse_live_url; rec.output_path is
    constructed by _build_output_path so it can't contain shell metachars.
    Both flow through subprocess.Popen with shell=False, so injection
    isn't possible anyway -- but we still re-check rec.room / rec.url
    here as belt-and-braces."""
    if not _looks_like_safe_room(rec.room):
        return None
    # URL must be a plain http(s) URL -- no shell tricks even though
    # we use shell=False. Defense in depth.
    if not re.match(r"^https?://[A-Za-z0-9.\-/_?=&%:]+$", rec.url):
        return None
    if backend == "streamlink":
        return [
            "streamlink",
            "--retry-streams", "5",
            "--retry-max", "3",
            "--hls-live-restart",
            "-o", rec.output_path,
            rec.url,
            "best",
        ]
    if backend == "ffmpeg":
        # Direct ffmpeg recording assumes we have an HLS URL.
        # For cam sites we don't -- need streamlink to resolve.
        # Fallback ffmpeg-only mode treats the URL as an HLS playlist
        # which works only if rec.url itself points to a .m3u8.
        #
        # Cut 1459 (MOD-4; the shape rows 440/441/442 fixed elsewhere): argv0 is
        # the path _detect_backends RESOLVED, not the bare name. A bare "ffmpeg"
        # is resolved AGAIN by execvp from the child's PATH, so an ffmpeg_path
        # pin gated on one build while the recording ran another -- and what is
        # recorded here is HLS over HTTPS, the exact case the pin exists for
        # (the static build SEGFAULTs on it). On a host where ffmpeg lives ONLY
        # under the pin the bare name did not resolve at all, so the
        # availability gate answered True over an exec that could never run.
        # Unresolvable now REFUSES rather than emitting an argv that cannot
        # launch; the caller names the step (see _refusal_reason).
        ffmpeg_path = _detect_backends().get("ffmpeg")
        if not ffmpeg_path:
            return None
        return [
            ffmpeg_path,
            "-hide_banner",
            "-loglevel", "warning",
            "-i", rec.url,
            "-c", "copy",
            "-bsf:a", "aac_adtstoasc",
            rec.output_path,
        ]
    return None


def _terminate_subprocess(proc: subprocess.Popen, timeout: float = 10.0) -> None:
    """Send SIGTERM (or CTRL_BREAK on Windows), wait, then SIGKILL.
    Quiet on errors -- the worst case is a zombie that the OS cleans
    up on Linux or that Windows reaps on its own."""
    if proc.poll() is not None:
        return
    try:
        if sys.platform.startswith("win"):
            try:
                proc.send_signal(signal.CTRL_BREAK_EVENT)
            except Exception:
                proc.terminate()
        else:
            proc.terminate()
    except Exception:
        pass
    try:
        proc.wait(timeout=timeout)
        return
    except subprocess.TimeoutExpired:
        pass
    except Exception:
        pass
    try:
        proc.kill()
        proc.wait(timeout=2.0)
    except Exception:
        pass


def _safe_file_size(path: str) -> int:
    """Return file size in bytes, or 0 if the file doesn't exist."""
    try:
        return os.path.getsize(path)
    except Exception:
        return 0


def _maybe_push(title: str, body: str) -> None:
    """Best-effort push notification."""
    if _push_notifier is None:
        return
    try:
        _push_notifier(title, body)
    except Exception as e:
        sys.stderr.write(f"[live-recorder] push notifier raised: {e}\n")


# ─── Test hooks ─────────────────────────────────────────────────────

def _reset_for_tests() -> None:
    """Wipe in-memory state. Tests only."""
    global _scheduler_started
    _scheduler_stop.set()
    with _lock:
        for proc in _subprocesses.values():
            try:
                proc.kill()
            except Exception:
                pass
        _subprocesses.clear()
        _recordings.clear()
        _scheduler_started = False
    _scheduler_stop.clear()


__all__ = [
    "is_available",
    "preferred_backend",
    "parse_live_url",
    "init",
    "watch",
    "unwatch",
    "list_recordings",
    "get_recording",
    "start_scheduler",
    "stop_scheduler",
    "_max_active_recordings",
    "_poll_interval_s",
    "_disconnect_tolerance_s",
    "_launch_timeout_s",
]

"""Row 1064: Continuous Background Bit-Rot Scrubber with Adaptive I/O Pacing.

Verifies:
1. ContinuousBitRotScrubber lifecycle (start, stop, pause, resume, status, scrub_once).
2. AdaptiveIOPacer token bucket pacing, rate controls, and dynamic load-adaptive backoff.
3. Three-state lifecycle disposition (O1224: unmeasured metrics are None, never 0).
4. compute_sha256_paced streaming hash with progress reporting and cancellation.
5. Production caller wiring: POST /api/bitrot/scan (actions/continuous), GET /api/bitrot/stats.
6. bg_scheduler nightly scan integration with pacer.
7. Negative control: bit-rot corruption detected under paced verification.
"""
from __future__ import annotations

import contextlib
import hashlib
import sqlite3
import threading
import time

import pytest
from bulk_downloader import bitrot as _br

BD_GATE_SCOPE = "repo-wide"


# ── Fixtures & Positive Control ─────────────────────────────────────────────

@pytest.fixture
def test_db(monkeypatch, tmp_path):
    """Provide an isolated SQLite database with provenance and integrity_issues."""
    monkeypatch.setattr(_br.os, "getloadavg", lambda: (0.1, 0.0, 0.0))
    _br.reset_scrubber()
    db_file = tmp_path / "test_bitrot.db"

    from bulk_downloader import db as _db

    @contextlib.contextmanager
    def _mock_conn():
        cx = sqlite3.connect(str(db_file))
        cx.row_factory = sqlite3.Row
        try:
            yield cx
            cx.commit()
        finally:
            cx.close()

    monkeypatch.setattr(_db, "db_conn", _mock_conn)
    _br._ensure_integrity_table()

    with _mock_conn() as cx:
        cx.execute("""CREATE TABLE IF NOT EXISTS provenance(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_url TEXT DEFAULT '',
            final_filename TEXT NOT NULL,
            file_size INTEGER DEFAULT 0,
            sha256 TEXT NOT NULL,
            last_verified_ts REAL DEFAULT 0,
            ts REAL NOT NULL,
            chain_hash TEXT DEFAULT ''
        )""")
    yield db_file
    _br.reset_scrubber()


def test_positive_control_baseline_bitrot(test_db):
    """Positive control: prove the test DB fixture builds valid provenance rows."""
    from bulk_downloader import db as _db
    with _db.db_conn() as cx:
        cx.execute(
            "INSERT INTO provenance(final_filename, file_size, sha256, ts) "
            "VALUES (?, ?, ?, ?)",
            ("sample.mkv", 1024, "abc123", time.time() - 86400 * 10),
        )
    candidates = _br._candidates(min_age_days=7, limit=10)
    assert len(candidates) == 1, f"expected 1 candidate, got {len(candidates)}"
    assert candidates[0]["final_filename"] == "sample.mkv"


# ── RED First Test: Scrubber and Pacer Symbols Exist ────────────────────────

def test_red_continuous_scrubber_and_pacer_symbols():
    """Verify Scrubber and Pacer exist in bitrot module."""
    assert hasattr(_br, "ContinuousBitRotScrubber"), (
        "ContinuousBitRotScrubber not defined in bulk_downloader.bitrot"
    )
    assert hasattr(_br, "AdaptiveIOPacer"), (
        "AdaptiveIOPacer not defined in bulk_downloader.bitrot"
    )
    assert hasattr(_br, "compute_sha256_paced"), (
        "compute_sha256_paced not defined in bulk_downloader.bitrot"
    )
    assert hasattr(_br, "get_scrubber"), (
        "get_scrubber accessor not defined in bulk_downloader.bitrot"
    )
    assert hasattr(_br, "PacingMode"), (
        "PacingMode not defined in bulk_downloader.bitrot"
    )
    assert hasattr(_br, "ScrubberState"), (
        "ScrubberState not defined in bulk_downloader.bitrot"
    )


# ── Adaptive I/O Pacer Unit Tests ───────────────────────────────────────────

def test_adaptive_io_pacer_nominal_pacing():
    """Verify AdaptiveIOPacer rate enforcement and token consumption."""
    pacer = _br.AdaptiveIOPacer(
        nominal_bytes_per_sec=50_000,
        high_load_threshold=0.8,
        load_getter=lambda: 0.1,  # Low load
    )
    st = pacer.status()
    assert st["mode"] == _br.PacingMode.NOMINAL
    assert st["throttled"] is False
    assert st["paused_high_load"] is False
    assert st["nominal_bytes_per_sec"] == 50_000

    # Consume small chunk without sleeping
    slept = pacer.pace_read(1024, block=False)
    assert slept >= 0.0
    st2 = pacer.status()
    assert st2["total_bytes_paced"] == 1024


def test_adaptive_io_pacer_load_adaptive_backoff():
    """Verify dynamic transition between nominal, throttled, and paused modes."""
    current_load = 0.2
    pacer = _br.AdaptiveIOPacer(
        nominal_bytes_per_sec=100_000,
        throttle_bytes_per_sec=20_000,
        high_load_threshold=0.75,
        pause_load_threshold=0.90,
        load_getter=lambda: current_load,
    )
    # Mode 1: Nominal
    pacer.pace_read(100, block=False)
    assert pacer.status()["mode"] == _br.PacingMode.NOMINAL
    assert pacer.status()["throttled"] is False

    # Mode 2: Elevated load -> Throttled
    current_load = 0.82
    pacer._last_load_check = 0.0  # Force immediate check
    pacer.pace_read(100, block=False)
    st_throttled = pacer.status()
    assert st_throttled["mode"] == _br.PacingMode.THROTTLED
    assert st_throttled["throttled"] is True
    assert st_throttled["effective_bytes_per_sec"] == 20_000

    # Mode 3: Critical load -> Paused
    current_load = 0.96
    pacer._last_load_check = 0.0  # Force immediate check
    pacer.pace_read(100, block=False)
    st_paused = pacer.status()
    assert st_paused["mode"] == _br.PacingMode.PAUSED_HIGH_LOAD
    assert st_paused["paused_high_load"] is True
    assert st_paused["effective_bytes_per_sec"] == 0.0

    # Recovery: Load returns to low -> Nominal restored
    current_load = 0.15
    pacer._last_load_check = 0.0
    pacer.pace_read(100, block=False)
    assert pacer.status()["mode"] == _br.PacingMode.NOMINAL


# ── Three-State Lifecycle Disposition (O1224) ───────────────────────────────

def test_three_state_lifecycle_disposition_o1224(test_db):
    """Fleet Rule O1224: unmeasured/idle states report None, never 0."""
    scrubber = _br.ContinuousBitRotScrubber()
    st = scrubber.status()

    # When idle / unstarted:
    assert st["state"] == _br.ScrubberState.IDLE
    assert st["disposition"] == "idle"
    assert st["running"] is False
    assert st["paused"] is False
    assert st["bytes_per_sec"] is None, "unmeasured rate must report None, never 0"
    assert st["current_file_bytes"] is None, "unmeasured file bytes must report None, never 0"
    assert st["current_file_total_bytes"] is None, "unmeasured total bytes must report None, never 0"
    assert st["bytes_scrubbed_session"] is None, "unmeasured session bytes must report None, never 0"
    assert st["eta_seconds"] is None, "unmeasured ETA must report None, never 0"

    # Pause transitions disposition
    scrubber.pause()
    assert scrubber.status()["state"] == _br.ScrubberState.PAUSED
    assert scrubber.status()["disposition"] == "paused"
    assert scrubber.status()["bytes_per_sec"] is None

    # Unavailable disposition
    scrubber.state = _br.ScrubberState.UNAVAILABLE
    st_unavail = scrubber.status()
    assert st_unavail["state"] == _br.ScrubberState.UNAVAILABLE
    assert st_unavail["disposition"] == "unavailable"
    assert st_unavail["ok"] is False
    assert st_unavail["bytes_per_sec"] is None


# ── compute_sha256_paced Unit Tests ─────────────────────────────────────────

def test_compute_sha256_paced_correctness(tmp_path):
    """Verify compute_sha256_paced matches standard SHA-256 and reports progress."""
    sample = tmp_path / "data.bin"
    content = b"Continuous background bit-rot scrubbing test content " * 1000
    sample.write_bytes(content)
    expected_hash = hashlib.sha256(content).hexdigest()

    progress_log = []
    pacer = _br.AdaptiveIOPacer(load_getter=lambda: 0.1)

    result = _br.compute_sha256_paced(
        str(sample),
        pacer=pacer,
        chunk_size=1024,
        on_progress=lambda done, total: progress_log.append((done, total)),
    )
    assert result == expected_hash
    assert len(progress_log) > 0
    assert progress_log[-1] == (len(content), len(content))


def test_compute_sha256_paced_stop_event(tmp_path):
    """Verify compute_sha256_paced cleanly aborts when stop_event is set."""
    sample = tmp_path / "large.bin"
    sample.write_bytes(b"A" * 65536)

    stop_event = threading.Event()
    stop_event.set()

    result = _br.compute_sha256_paced(str(sample), stop_event=stop_event)
    assert result is None, "paced hashing must abort when stop_event is set"


# ── Negative Control: Bit-Rot Corruption Detected While Paced ───────────────

def test_negative_control_corrupted_file_detected_while_paced(test_db, tmp_path):
    """Negative control: corrupted file on disk is flagged as modified."""
    from bulk_downloader import db as _db

    file_path = tmp_path / "corrupted_media.mp4"
    original_data = b"Original pristine file content before bit rot"
    file_path.write_bytes(original_data)
    pristine_hash = hashlib.sha256(original_data).hexdigest()

    with _db.db_conn() as cx:
        cur = cx.execute(
            "INSERT INTO provenance(final_filename, file_size, sha256, ts) "
            "VALUES (?, ?, ?, ?)",
            (file_path.name, len(original_data), pristine_hash, time.time() - 86400 * 10),
        )
        prov_id = cur.lastrowid

    # Inject bit rot: modify 1 byte on disk
    corrupted_data = b"Original pristine file content BEFore bit rot"
    file_path.write_bytes(corrupted_data)

    pacer = _br.AdaptiveIOPacer(load_getter=lambda: 0.1)
    res = _br.verify_one(
        {"id": prov_id, "final_filename": file_path.name, "file_size": len(original_data), "sha256": pristine_hash},
        download_dir=str(tmp_path),
        pacer=pacer,
    )
    assert res["ok"] is False
    assert res["kind"] == "modified"
    assert "≠ recorded" in res["message"]

    # Verify issue was persisted in integrity_issues table
    issues = _br.list_issues(kind="modified")
    assert len(issues) == 1
    assert issues[0]["provenance_id"] == prov_id
    assert issues[0]["actual_sha256"] == hashlib.sha256(corrupted_data).hexdigest()


# ── Continuous Scrubber Lifecycle & Single Scrub ────────────────────────────

def test_continuous_scrubber_lifecycle_and_scrub_once(test_db, tmp_path):
    """Verify start, pause, resume, stop, and scrub_once execution."""
    from bulk_downloader import db as _db

    # Create 2 valid files
    f1 = tmp_path / "f1.mkv"
    f2 = tmp_path / "f2.mkv"
    f1.write_bytes(b"content 1")
    f2.write_bytes(b"content 2")

    with _db.db_conn() as cx:
        cx.execute(
            "INSERT INTO provenance(final_filename, file_size, sha256, ts) VALUES (?, ?, ?, ?)",
            ("f1.mkv", len(b"content 1"), hashlib.sha256(b"content 1").hexdigest(), time.time() - 86400 * 10),
        )
        cx.execute(
            "INSERT INTO provenance(final_filename, file_size, sha256, ts) VALUES (?, ?, ?, ?)",
            ("f2.mkv", len(b"content 2"), hashlib.sha256(b"content 2").hexdigest(), time.time() - 86400 * 10),
        )

    scrubber = _br.ContinuousBitRotScrubber()
    scrubber._download_dirs = [str(tmp_path)]

    # Test synchronous scrub_once
    summary = scrubber.scrub_once(download_dirs=[str(tmp_path)], min_age_days=1, max_files=5)
    assert summary["ok"] is True
    assert summary["checked"] == 2
    assert summary["intact"] == 2

    # Test thread lifecycle start -> pause -> resume -> stop
    started = scrubber.start(continuous=False, download_dirs=[str(tmp_path)], min_age_days=1)
    assert started is True
    assert scrubber.status()["running"] is True

    scrubber.pause()
    assert scrubber.status()["paused"] is True

    scrubber.resume()
    assert scrubber.status()["paused"] is False

    scrubber.stop(timeout=1.0)
    assert scrubber.status()["running"] is False
    assert scrubber.status()["state"] in (_br.ScrubberState.IDLE, _br.ScrubberState.STOPPED)


# ── Production Caller Wiring: app_bitrot & bg_scheduler ─────────────────────

def test_app_bitrot_api_scan_and_stats(monkeypatch, test_db, tmp_path):
    """Verify /api/bitrot/scan continuous actions and /api/bitrot/stats telemetry."""
    from bulk_downloader import app_bitrot as _ab
    from bulk_downloader import app_state
    from flask import Flask

    # Bypass CSRF for test requests
    monkeypatch.setattr(_ab, "_check_csrf", lambda *a, **k: None)

    app = Flask("test_bitrot_app")
    app.register_blueprint(_ab.bitrot_bp)
    client = app.test_client()

    # 1. Action: status query
    r_status = client.post("/api/bitrot/scan", json={"action": "status"})
    assert r_status.status_code == 200
    data = r_status.get_json()
    assert data["ok"] is True
    assert "scrubber" in data
    assert data["scrubber"]["state"] in (_br.ScrubberState.IDLE, _br.ScrubberState.STOPPED, _br.ScrubberState.UNAVAILABLE)

    # 2. Action: start scrubber without configured roots -> returns 503
    monkeypatch.setattr(app_state, "s_cfg", {})
    r_start_no_roots = client.post("/api/bitrot/scan", json={"action": "start"})
    assert r_start_no_roots.status_code == 503
    assert r_start_no_roots.get_json()["error"] == "download roots unavailable"

    # 3. Action: start scrubber with roots configured via app_state.s_cfg
    monkeypatch.setattr(app_state, "s_cfg", {"site_test": {"download_dir": str(tmp_path)}})
    r_start = client.post("/api/bitrot/scan", json={"action": "start", "target_mbps": 5.0})
    assert r_start.status_code == 200
    data_start = r_start.get_json()
    assert data_start["ok"] is True
    assert data_start["scrubber"]["running"] is True

    # 4. Action: pause & resume
    r_pause = client.post("/api/bitrot/scan", json={"action": "pause"})
    assert r_pause.status_code == 200
    assert r_pause.get_json()["scrubber"]["paused"] is True

    r_resume = client.post("/api/bitrot/scan", json={"action": "resume"})
    assert r_resume.status_code == 200
    assert r_resume.get_json()["scrubber"]["paused"] is False

    # 5. Action: stop scrubber
    r_stop = client.post("/api/bitrot/scan", json={"action": "stop"})
    assert r_stop.status_code == 200
    assert r_stop.get_json()["scrubber"]["running"] is False

    # 6. GET /api/bitrot/stats includes scrubber payload
    r_stats = client.get("/api/bitrot/stats")
    assert r_stats.status_code == 200
    stats_data = r_stats.get_json()
    assert "scrubber" in stats_data
    assert "disposition" in stats_data["scrubber"]


def test_bg_scheduler_passes_pacer_to_scan(monkeypatch):
    """Verify bg_scheduler._run_bitrot connects pacer to run_scan."""
    from bulk_downloader import bg_scheduler as _sched

    captured_kwargs = {}

    def _fake_run_scan(**kw):
        captured_kwargs.update(kw)
        return {"checked": 0}

    monkeypatch.setattr(_br, "run_scan", _fake_run_scan)
    captured_tasks = {}
    monkeypatch.setattr(
        _sched, "register",
        lambda name, fn, **kw: captured_tasks.setdefault(name, fn)
    )

    _sched.register_default_tasks(s_cfg_getter=lambda: {"s1": {"download_dir": "/srv/media"}})
    assert "bitrot.nightly_scan" in captured_tasks
    captured_tasks["bitrot.nightly_scan"]()

    assert "pacer" in captured_kwargs, "bg_scheduler must pass pacer to run_scan"
    assert isinstance(captured_kwargs["pacer"], _br.AdaptiveIOPacer)


# ── Refutation & Invariant Regression Controls (Findings A, B, C) ───────────

def test_red_hot_loop_refutation_bounded_queries_and_measured_counters(monkeypatch, test_db, tmp_path):
    """RED test for Finding A: ContinuousBitRotScrubber must not hot-loop on unresolvable rows
    or unconfigured roots, must bound candidate queries, and must only increment session counters
    for measured rows (intact/modified/truncated).
    """
    from bulk_downloader import app_bitrot as _ab
    from bulk_downloader import app_state
    from bulk_downloader import db as _db
    from flask import Flask

    # Setup 3 files in tmp_path
    f1 = tmp_path / "video1.mp4"
    f2 = tmp_path / "video2.mp4"
    f3 = tmp_path / "video3.mp4"
    data = b"X" * 10000
    for f in (f1, f2, f3):
        f.write_bytes(data)

    sh = hashlib.sha256(data).hexdigest()
    with _db.db_conn() as cx:
        for f in (f1, f2, f3):
            cx.execute(
                "INSERT INTO provenance(final_filename, file_size, sha256, last_verified_ts, ts) "
                "VALUES (?, ?, ?, 0, ?)",
                (f.name, len(data), sh, time.time() - 86400 * 10),
            )

    # Wrap _candidates to count queries exactly
    orig_candidates = _br._candidates
    candidate_calls = 0
    candidate_lock = threading.Lock()

    def counting_candidates(*a, **k):
        nonlocal candidate_calls
        with candidate_lock:
            candidate_calls += 1
        return orig_candidates(*a, **k)

    monkeypatch.setattr(_br, "_candidates", counting_candidates)
    monkeypatch.setattr(_ab, "_check_csrf", lambda *a, **k: None)

    app = Flask("test_hot_loop_app")
    app.register_blueprint(_ab.bitrot_bp)
    client = app.test_client()

    # Part 1: Start with unconfigured roots fails fast with 503, does not hot loop
    monkeypatch.setattr(app_state, "s_cfg", {})
    r = client.post("/api/bitrot/scan", json={"action": "start"})
    assert r.status_code == 503
    assert r.get_json()["error"] == "download roots unavailable"
    time.sleep(0.1)
    assert candidate_calls == 0, "scrubber must not execute candidate queries when unavailable"

    # Part 2: Start with roots configured via s_cfg
    monkeypatch.setattr(app_state, "s_cfg", {"s1": {"download_dir": str(tmp_path)}})
    r2 = client.post("/api/bitrot/scan", json={"action": "start", "target_mbps": 100.0, "batch_size": 10})
    assert r2.status_code == 200
    assert r2.get_json()["scrubber"]["running"] is True

    # Let it run for 0.4 seconds
    time.sleep(0.4)

    # Stop the scrubber
    client.post("/api/bitrot/scan", json={"action": "stop"})

    # Exact count / bounded assertions:
    # 1. Candidate queries must be strictly bounded (<= 5 calls, not thousands)
    assert candidate_calls <= 5, f"hot loop detected: _candidates called {candidate_calls} times in 0.4s"

    # 2. All 3 rows were verified intact and stamped
    with _db.db_conn() as cx:
        stamped = cx.execute("SELECT COUNT(*) FROM provenance WHERE last_verified_ts > 0").fetchone()[0]
    assert stamped == 3, f"expected 3 stamped rows, got {stamped}"

    # 3. Scrubber status reported exact measured counts, not thousands
    status = _br.get_scrubber().status()
    assert status["files_scrubbed_session"] == 3
    assert status["running"] is False


def test_adaptive_io_pacer_low_bitrate_debt_pacing():
    """RED test for Finding B: AdaptiveIOPacer must accurately enforce low bitrates
    (< 1 Mbps) and not truncate sleep debt to 0.5s per chunk.
    """
    # 16 KiB/s nominal rate
    pacer = _br.AdaptiveIOPacer(
        nominal_bytes_per_sec=16_384,
        burst_seconds=0.0,  # Zero burst to force immediate debt calculation
        load_getter=lambda: 0.1,
    )
    pacer._tokens = 0.0  # Drain initial tokens

    # Reading 64 KiB (65536 bytes) at 16 KiB/s (16384 B/s) requires ~4.0 seconds of sleep.
    # On tree 38a745db, non-blocking check or slice sleep was truncated to 0.5s.
    sleep_needed = pacer.pace_read(65536, block=False)
    assert sleep_needed >= 3.9, (
        f"low-bitrate pacing failed: needed >= 3.9s for 64 KiB at 16 KiB/s, got {sleep_needed:.2f}s"
    )

    # Test that blocking pace_read actually sleeps beyond the 0.5s chunk cap when needed.
    # At 16,384 B/s, reading 12,288 bytes requires 12288 / 16384 = 0.75 seconds.
    pacer._tokens = 0.0
    t0 = time.perf_counter()
    slept = pacer.pace_read(12288, block=True)
    elapsed = time.perf_counter() - t0
    assert slept >= 0.70, f"expected >= 0.70s sleep debt for 12 KiB at 16 KiB/s, got {slept:.3f}s"
    assert elapsed >= 0.70, f"actual elapsed time {elapsed:.3f}s less than required sleep"


def test_negative_control_unresolvable_files_not_counted_as_scrubbed(monkeypatch, test_db, tmp_path):
    """Negative control: candidates that cannot be resolved against download_dirs
    return 'unknown', are NOT counted as scrubbed files or bytes, and trigger idle backoff.
    """
    from bulk_downloader import db as _db

    # Add candidate that does not exist in tmp_path
    with _db.db_conn() as cx:
        cx.execute(
            "INSERT INTO provenance(final_filename, file_size, sha256, last_verified_ts, ts) "
            "VALUES (?, ?, ?, 0, ?)",
            ("ghost_file_missing.mkv", 50000, "abc123hash", time.time() - 86400 * 10),
        )

    # Provide empty download dir so it cannot resolve
    empty_dir = tmp_path / "empty_dir"
    empty_dir.mkdir()

    scrubber = _br.ContinuousBitRotScrubber(pacer=_br.AdaptiveIOPacer(load_getter=lambda: 0.1))
    started = scrubber.start(continuous=False, download_dirs=[str(empty_dir)], idle_sleep_seconds=0.1)
    assert started is True

    # Wait for completion of non-continuous run
    time.sleep(0.3)
    scrubber.stop()

    status = scrubber.status()
    assert status["files_scrubbed_session"] == 0, "unresolvable rows must not increment files_scrubbed"


# ── Exact Count Assertions ──────────────────────────────────────────────────

def test_exact_count_scrubber_states_and_pacing_modes():
    """Exact count assertion: verify all states and modes are registered."""
    valid_states = {
        _br.ScrubberState.IDLE,
        _br.ScrubberState.SCRUBBING,
        _br.ScrubberState.PAUSED,
        _br.ScrubberState.STOPPED,
        _br.ScrubberState.UNAVAILABLE,
    }
    assert len(valid_states) == 5, f"expected 5 ScrubberStates, got {len(valid_states)}"

    valid_modes = {
        _br.PacingMode.NOMINAL,
        _br.PacingMode.THROTTLED,
        _br.PacingMode.PAUSED_HIGH_LOAD,
    }
    assert len(valid_modes) == 3, f"expected 3 PacingModes, got {len(valid_modes)}"


# ── Singleton Reset ─────────────────────────────────────────────────────────

def test_reset_scrubber_stops_old_and_installs_fresh_instance(monkeypatch):
    """Control: a clean reset stops the old scrubber and installs a new idle one."""
    old = _br.ContinuousBitRotScrubber(pacer=_br.AdaptiveIOPacer(load_getter=lambda: 0.1))
    monkeypatch.setattr(_br, "_SCRUBBER_INSTANCE", old)

    fresh = _br.reset_scrubber()

    assert old.state == _br.ScrubberState.STOPPED
    assert fresh is not old
    assert fresh.state == _br.ScrubberState.IDLE
    assert _br.get_scrubber() is fresh


def test_reset_scrubber_propagates_stop_failure_and_keeps_old_instance(monkeypatch):
    """A failed stop() of the old scrubber must reach reset_scrubber's caller.

    Swallowing it (except Exception: pass) installed a fresh singleton over a
    scrubber whose thread was never joined, and the caller never saw the error.
    """
    old = _br.ContinuousBitRotScrubber(pacer=_br.AdaptiveIOPacer(load_getter=lambda: 0.1))
    stop_calls: list[float] = []

    def _stop_join_fails(timeout: float = 2.0) -> None:
        stop_calls.append(timeout)
        raise RuntimeError("scrubber thread join failed")

    monkeypatch.setattr(old, "stop", _stop_join_fails)
    monkeypatch.setattr(_br, "_SCRUBBER_INSTANCE", old)

    with pytest.raises(RuntimeError, match="scrubber thread join failed"):
        _br.reset_scrubber()

    assert stop_calls == [1.0], f"expected exactly one stop(timeout=1.0), got {stop_calls}"
    assert _br.get_scrubber() is old, "reset replaced a scrubber that failed to stop"


# ── Abort vs Failure, One Loop, One Charge (P2-B F1-F3 / N5-A E1-E2) ────────

def _insert_due_row(name: str, data: bytes) -> int:
    from bulk_downloader import db as _db
    with _db.db_conn() as cx:
        return cx.execute(
            "INSERT INTO provenance(final_filename, file_size, sha256, last_verified_ts, ts) "
            "VALUES (?, ?, ?, 0, ?)",
            (name, len(data), hashlib.sha256(data).hexdigest(), time.time() - 86400 * 10),
        ).lastrowid


def _last_verified(prov_id: int) -> float:
    from bulk_downloader import db as _db
    with _db.db_conn() as cx:
        return cx.execute(
            "SELECT last_verified_ts FROM provenance WHERE id = ?", (prov_id,)
        ).fetchone()[0]


def _scrub_loops() -> set:
    return {t for t in threading.enumerate() if t.name == "bd-bitrot-scrubber" and t.is_alive()}


def test_scrubber_stop_mid_file_records_no_integrity_issue(test_db, tmp_path):
    """F1/E1: an operator stop() while a file is mid-hash is not evidence of rot.

    It must not persist an integrity_issues row (stats().open_issues feeds the
    bitrot_growing alert), must not stamp the row verified, and must not count it.
    """
    data = b"R" * (1024 * 1024)
    (tmp_path / "intact_big.mkv").write_bytes(data)
    prov_id = _insert_due_row("intact_big.mkv", data)

    # 64 KiB/s: the first 64 KiB chunk is admitted at once, the rest takes ~15 s.
    scrubber = _br.ContinuousBitRotScrubber(
        pacer=_br.AdaptiveIOPacer(nominal_bytes_per_sec=65536, load_getter=lambda: 0.1)
    )
    assert scrubber.start(continuous=False, download_dirs=[str(tmp_path)]) is True
    loop = scrubber._thread
    deadline = time.monotonic() + 5.0
    while not scrubber.current_file_bytes and time.monotonic() < deadline:
        time.sleep(0.01)
    assert 0 < (scrubber.current_file_bytes or 0) < len(data), "precondition: stop must land mid-file"

    scrubber.stop(timeout=3.0)
    assert not loop.is_alive()

    recorded = [(i["kind"], i["notes"]) for i in _br.list_issues()]
    assert recorded == [], f"stop() mid-file persisted {recorded} for an intact file"
    assert _br.stats()["open_issues"] == 0
    assert _last_verified(prov_id) == 0, "an aborted hash must not stamp the row verified"
    assert scrubber.files_scrubbed_session == 0


def test_verify_one_abort_is_not_an_error_but_a_failure_still_is(monkeypatch, test_db, tmp_path):
    """F1/E1 at verify_one: stop mid-hash -> kind 'aborted', nothing recorded or stamped.

    Controls: the same file unstopped is intact, and a hash that fails with no
    stop requested still records kind 'error' (the probe can say yes).
    """
    data = b"I" * (4 * 65536)
    (tmp_path / "intact_small.mkv").write_bytes(data)
    prov_id = _insert_due_row("intact_small.mkv", data)
    row = {"id": prov_id, "final_filename": "intact_small.mkv",
           "file_size": len(data), "sha256": hashlib.sha256(data).hexdigest()}
    stop = threading.Event()

    def _stop_after_first_chunk(done: int, total: int) -> None:
        if done >= 65536:
            stop.set()

    res = _br.verify_one(row, download_dir=str(tmp_path),
                         pacer=_br.AdaptiveIOPacer(load_getter=lambda: 0.1),
                         on_progress=_stop_after_first_chunk, stop_event=stop)
    recorded = [(i["kind"], i["notes"]) for i in _br.list_issues()]
    assert recorded == [], f"an aborted hash of an intact file was recorded as {recorded}"
    assert (res["ok"], res["kind"]) == (False, "aborted"), res
    assert _last_verified(prov_id) == 0

    intact = _br.verify_one(row, download_dir=str(tmp_path),
                            pacer=_br.AdaptiveIOPacer(load_getter=lambda: 0.1),
                            stop_event=threading.Event())
    assert intact["kind"] == "intact", intact

    monkeypatch.setattr(_br, "compute_sha256_paced", lambda *a, **k: None)
    failed = _br.verify_one(row, download_dir=str(tmp_path), stop_event=threading.Event())
    assert failed["kind"] == "error", failed
    assert [i["kind"] for i in _br.list_issues()] == ["error"]


def test_start_while_paused_resumes_the_one_live_loop(test_db, tmp_path):
    """F2/E2: pause() then start() resumes the live loop; it never spawns a second one."""
    before = _scrub_loops()
    scrubber = _br.ContinuousBitRotScrubber(pacer=_br.AdaptiveIOPacer(load_getter=lambda: 0.1))
    try:
        assert scrubber.start(download_dirs=[str(tmp_path)], idle_sleep_seconds=0.05) is True
        first = scrubber._thread
        assert _scrub_loops() - before == {first}, "control: one start() -> one loop"

        scrubber.pause()
        assert scrubber.start(download_dirs=[str(tmp_path)], idle_sleep_seconds=0.05) is True
        mine = _scrub_loops() - before
        assert len(mine) == 1, f"pause() then start() left {len(mine)} scrub loops alive"
        assert scrubber._thread is first
        assert scrubber.status()["state"] == _br.ScrubberState.SCRUBBING

        # The resumed loop really runs: a row that falls due is verified.
        data = b"resume me"
        (tmp_path / "resumed.mkv").write_bytes(data)
        prov_id = _insert_due_row("resumed.mkv", data)
        deadline = time.monotonic() + 5.0
        while not _last_verified(prov_id) and time.monotonic() < deadline:
            time.sleep(0.02)
        assert _last_verified(prov_id) > 0, "the resumed loop never verified the due row"
    finally:
        scrubber.stop(timeout=2.0)
    assert not (_scrub_loops() - before), "stop() left a scrub loop alive"


def test_start_after_the_loop_ended_while_paused_spawns_a_fresh_loop(monkeypatch, test_db, tmp_path):
    """F2 control: PAUSED with no live loop is started afresh, never 'resumed' into nothing."""
    release = threading.Event()

    def _held_candidates(**kwargs):
        release.wait(timeout=5.0)
        return []

    monkeypatch.setattr(_br, "_candidates", _held_candidates)
    before = _scrub_loops()
    scrubber = _br.ContinuousBitRotScrubber(pacer=_br.AdaptiveIOPacer(load_getter=lambda: 0.1))
    try:
        assert scrubber.start(continuous=False, download_dirs=[str(tmp_path)]) is True
        first = scrubber._thread
        scrubber.pause()
        release.set()                      # the one-shot loop finds nothing and ends, still PAUSED
        first.join(timeout=2.0)
        assert not first.is_alive()
        assert scrubber.state == _br.ScrubberState.PAUSED
        assert scrubber._thread is None, "an ended loop must deregister itself, or start() could resume it"

        assert scrubber.start(download_dirs=[str(tmp_path)], idle_sleep_seconds=0.05) is True
        second = scrubber._thread
        assert second is not None and second is not first and second.is_alive()
        assert _scrub_loops() - before == {second}
    finally:
        release.set()
        scrubber.stop(timeout=2.0)


def test_pacer_charges_a_read_once_across_a_high_load_pause():
    """F3: a read held by PAUSED_HIGH_LOAD is charged once, after the wait,
    not once per 0.2 s pause loop (status().total_bytes_paced was inflated ~6x)."""
    checks: list[float] = []

    def _load() -> float:
        checks.append(time.perf_counter())
        return 0.99 if len(checks) == 1 else 0.1   # paused until the next check, >= 1 s later

    pacer = _br.AdaptiveIOPacer(nominal_bytes_per_sec=10 * 1024 * 1024, load_getter=_load)
    slept = pacer.pace_read(65536, block=True)
    st = pacer.status()
    assert st["total_bytes_paced"] == 65536, (
        f"one 64 KiB read across a high-load pause was charged {st['total_bytes_paced']} bytes"
    )
    assert slept >= 0.8, f"the read did not wait out the pause (slept {slept:.2f}s)"
    assert st["mode"] == _br.PacingMode.NOMINAL

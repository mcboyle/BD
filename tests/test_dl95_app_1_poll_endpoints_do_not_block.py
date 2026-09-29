"""dl95-app-1: /api/health and /api/sites/v2 must not block on per-site locks.

THE DEFECT. test2 under ~6 running sites measured /api/health at 56.6 s and
/api/sites/v2 at 10.6 s (FINDINGS-A1-A.md#A7). Both handlers asked every
runner for ``SiteRunner.get_status(light=True)``: that waits for the runner's
``_lock`` (held by its workers) and then runs every per-site probe -- disk
statvfs, the progress-tracker lock, the worker-heartbeat lock, bottleneck
detector, JD/qB health, cookie info. The two polls only read job counts.

THE CONTRACT pinned here: with the job lock or a probe lock of all six real
SiteRunners held by another thread, each poll answers within 1 s and still
reports the runners' true counts.

CONTROLS. ``test_fixture_really_blocks_get_status`` proves the held locks do
stall the old path (the probe could say "blocked"). The unheld positive
control proves the poll counts equal ``get_status`` counts.
"""
from __future__ import annotations

import threading

import pytest

BD_GATE_SCOPE = "module"
pytestmark = pytest.mark.bd_module_wipe

N_SITES = 6
BOUND_S = 1.0
# GEN 2 (lens B16-B F2): /api/health/v2 is fixed too, so it is pinned too.
PATHS = ["/api/health", "/api/health/v2", "/api/sites/v2"]
JOBS = {"u1": "pending", "u2": "pending", "u3": "running",
        "u4": "done", "u5": "done", "u6": "done", "u7": "failed"}


@pytest.fixture
def six_runners(tmp_path):
    from bulk_downloader import app as a
    from bulk_downloader.runner import SiteRunner

    saved_runners, saved_cfg = dict(a.runners), dict(a.s_cfg)
    made = {}
    for i in range(N_SITES):
        sid = f"dl95app1_{i}"
        cfg = {"name": f"DL95 app1 {i}", "download_dir": str(tmp_path / sid)}
        r = SiteRunner(sid, cfg)
        with r._lock:
            for url, status in JOBS.items():
                r.jobs[f"https://example.invalid/{sid}/{url}"] = {"status": status}
        made[sid] = r
    a.runners.clear()
    a.runners.update(made)
    a.s_cfg.clear()
    a.s_cfg.update({sid: r.config for sid, r in made.items()})
    try:
        yield a, made
    finally:
        a.runners.clear()
        a.runners.update(saved_runners)
        a.s_cfg.clear()
        a.s_cfg.update(saved_cfg)


def _job_lock(r):
    return r._lock


def _probe_lock(r):
    # A per-site probe get_status runs AFTER the job lock: progress rollup.
    return r.get_progress_tracker()._lock


class _Held:
    """Hold one lock per runner from a separate thread until released."""

    def __init__(self, locks):
        self.locks = locks
        self.held = threading.Event()
        self.release = threading.Event()
        self.thread = threading.Thread(target=self._run, daemon=True)

    def _run(self):
        for lk in self.locks:
            lk.acquire()
        self.held.set()
        try:
            self.release.wait(30)
        finally:
            for lk in reversed(self.locks):
                lk.release()

    def __enter__(self):
        self.thread.start()
        assert self.held.wait(5), "DL95_APP_1_FIXTURE: could not take the locks"
        return self

    def __exit__(self, *exc):
        self.release.set()
        self.thread.join(10)
        assert not self.thread.is_alive(), "DL95_APP_1_FIXTURE: holder stuck"


def _get_within(a, path, bound):
    """GET ``path`` on a worker thread; return (finished, status, body)."""
    out, done = [], threading.Event()

    def call():
        try:
            resp = a.app.test_client().get(path)
            out.append((resp.status_code, resp.get_json()))
        finally:
            done.set()

    t = threading.Thread(target=call, daemon=True)
    t.start()
    finished = done.wait(bound)
    return finished, t, done, out


def _expected():
    running = sum(1 for s in JOBS.values() if s == "running")
    pending = sum(1 for s in JOBS.values() if s == "pending")
    done = sum(1 for s in JOBS.values() if s == "done")
    return pending, running, done


def _assert_counts(path, body, a, check_active=True):
    pending, running, done = _expected()
    if path.startswith("/api/health"):
        assert body["queue_depth"] == N_SITES * pending, (
            f"DL95_APP_1_HEALTH_COUNTS: queue_depth {body.get('queue_depth')}")
        assert body["active_downloads"] == N_SITES * running, (
            f"DL95_APP_1_HEALTH_COUNTS: active {body.get('active_downloads')}")
        assert body.get("degraded") != "runner_status_error", (
            "DL95_APP_1_HEALTH_COUNTS: runner status reported as an error")
    else:
        rows = {e["site_id"]: e for e in body["sites"]}
        assert set(rows) == set(a.runners), "DL95_APP_1_SITES_ROWS: site set"
        for sid, e in rows.items():
            assert e["downloaded_total"] == done, (
                f"DL95_APP_1_SITES_COUNTS: {sid} downloaded_total "
                f"{e['downloaded_total']} != {done}")
            assert not check_active or e["active_workers"] == running, (
                f"DL95_APP_1_SITES_COUNTS: {sid} active_workers "
                f"{e['active_workers']} != {running}")


@pytest.mark.parametrize("which_lock", [_job_lock, _probe_lock],
                         ids=["job_lock", "probe_lock"])
@pytest.mark.parametrize("path", PATHS)
def test_poll_answers_within_bound_while_six_site_locks_are_held(
        six_runners, path, which_lock):
    a, made = six_runners
    # Warm the route (imports, first DB lease) with nothing held.
    assert a.app.test_client().get(path).status_code in (200, 503)
    with _Held([which_lock(r) for r in made.values()]) as held:
        finished, t, done, out = _get_within(a, path, BOUND_S)
        assert finished, (
            f"DL95_APP_1_POLL_BLOCKED: GET {path} did not answer within "
            f"{BOUND_S}s while {N_SITES} runners' {which_lock.__name__} "
            f"were held by another thread")
    t.join(10)
    status, body = out[0]
    assert status in (200, 503), f"DL95_APP_1_STATUS: {status} {body}"
    _assert_counts(path, body, a)


def test_fixture_really_blocks_get_status(six_runners):
    """Negative control: the held locks DO stall the full status path, so a
    passing poll test is not the fixture failing to hold anything."""
    _a, made = six_runners
    r = next(iter(made.values()))
    for which in (_job_lock, _probe_lock):
        with _Held([which(r)]):
            done = threading.Event()
            t = threading.Thread(
                target=lambda: (r.get_status(light=True), done.set()),
                daemon=True)
            t.start()
            assert not done.wait(0.3), (
                f"DL95_APP_1_CONTROL: get_status did not wait for "
                f"{which.__name__}; the fixture proves nothing")
        t.join(10)
        assert done.is_set(), "DL95_APP_1_CONTROL: get_status never resumed"


@pytest.mark.parametrize("path", PATHS)
def test_unheld_poll_counts_match_full_status(six_runners, path):
    """Positive control: with nothing held the poll reports what
    get_status(light=True) reports for the same runners."""
    a, made = six_runners
    for r in made.values():
        c = r.get_status(light=True)["counts"]
        assert (c["pending"], c["running"], c["done"]) == _expected(), (
            "DL95_APP_1_CONTROL: fixture counts drifted")
    resp = a.app.test_client().get(path)
    # active_workers is excluded: the base path never carried "active".
    _assert_counts(path, resp.get_json(), a, check_active=False)


# GEN 2 (lens B16-B F1): a per-site probe failure must still reach the polls.
# The polls no longer run the probes (that is what stalled them), so the
# runner records get_status's last failure and the polls report it.
def _break_probe(monkeypatch, r):
    def boom(*a, **k):
        raise RuntimeError("dl95-app-1 probe boom")
    monkeypatch.setattr(r, "progress_telemetry_status", boom)


def _signal(path, body, sid):
    if path.startswith("/api/health"):
        return body.get("degraded") == "runner_status_error" and body.get("ok") is False
    row = {e["site_id"]: e for e in body["sites"]}[sid]
    return row["downloaded_total"] == 0


@pytest.mark.parametrize("path", PATHS)
def test_a_probe_failure_is_still_reported(six_runners, monkeypatch, path):
    a, made = six_runners
    sid, r = next(iter(made.items()))
    _break_probe(monkeypatch, r)
    with pytest.raises(RuntimeError):
        r.get_status(light=True)          # the UI's /api/status poll
    body = a.app.test_client().get(path).get_json()
    assert _signal(path, body, sid), (
        f"DL95_APP_1_STATUS_ERROR_LOST: {path} hid a failing runner: "
        f"ok={body.get('ok')} degraded={body.get('degraded')}")
    monkeypatch.undo()
    r.get_status(light=True)              # recovers -> the signal clears
    body = a.app.test_client().get(path).get_json()
    assert not _signal(path, body, sid), f"DL95_APP_1_STATUS_ERROR_STUCK: {path}"
    _assert_counts(path, body, a, check_active=False)


@pytest.mark.parametrize("path", PATHS)
def test_a_stale_probe_failure_ages_out(six_runners, path):
    import time as _time
    a, made = six_runners
    sid, r = next(iter(made.items()))
    ttl = getattr(r, "_STATUS_ERROR_TTL_S", 300.0)   # control: passes on BASE too
    r._status_error = (_time.time() - ttl - 1, "RuntimeError: old")
    body = a.app.test_client().get(path).get_json()
    assert not _signal(path, body, sid), f"DL95_APP_1_STATUS_ERROR_STALE: {path}"


def test_a_recorded_failure_is_reported_while_locks_are_held(six_runners, monkeypatch):
    """The signal needs no lock either: reported within the bound."""
    a, made = six_runners
    sid, r = next(iter(made.items()))
    _break_probe(monkeypatch, r)
    with pytest.raises(RuntimeError):
        r.get_status(light=True)
    with _Held([_job_lock(x) for x in made.values()]):
        finished, t, done, out = _get_within(a, "/api/health", BOUND_S)
        assert finished, "DL95_APP_1_POLL_BLOCKED: /api/health with a recorded failure"
    t.join(10)
    assert _signal("/api/health", out[0][1], sid), out[0][1]

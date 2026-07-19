"""v3.66.760 -- MOD-1 A-5b: takeover idle-timeout + the A5-R3 no-orphan sweep.

Two hardening legs on the remote-takeover lifecycle:

  * IDLE-TIMEOUT captcha_takeover_idle_timeout_s (~300s): a `solving` session
    with no operator input for that long is finalized (dismissed + ender +
    channel closed). The clock RESETS on each accepted input. The SSE viewer
    stream honors the same bound, and a viewer DISCONNECT tears the channel
    down (frees the A-5a concurrency slot; a reconnect re-opens it).

  * NO-ORPHAN SWEEP (A5-R3, the PROJECT-CANONICAL shape): a reaper whose
    denominator excludes an orphan reports "0 orphans" truthfully and
    uselessly. Fix: ONE atomic registry (_pending under _lock) is the source
    of truth; the sweep DERIVES its active set from it AND cross-checks the
    live surfaces (open takeover channels + live solve browsers via a census
    hook); anything live that the registry does not bind is an orphan and is
    reaped; a surface the sweep CANNOT verify (census unregistered / raising)
    is reported as `unverified` -- unknown-fails-to-reap, never a silent "0".

  * THE REAPER NOBODY RUNS: sweep_expired had zero production callers.
    start_sweeper() (daemon, BD_DISABLE_KEEPALIVE-guarded) + app.py wiring.

RED-first on pristine v3.66.759 (key/fields/helpers/wiring all ABSENT).
"""
from __future__ import annotations

import os
import time
from pathlib import Path

import pytest


_ROOT = Path(__file__).resolve().parents[1]


class _RelayReset:
    def setup_method(self):
        from bulk_downloader import captcha_relay
        captcha_relay._reset_for_tests()
        # channels are module state in takeover; clear any leftovers
        from bulk_downloader import takeover
        with takeover._channels_lock:
            for sid in list(takeover._channels):
                takeover._channels[sid].closed.set()
            takeover._channels.clear()

    def teardown_method(self):
        self.setup_method()


# ════════════════════════════════════════════════════════════════════
#  Config key (CONFIG-KEY-ADD footgun: declared + FE-wired + manifest)
# ════════════════════════════════════════════════════════════════════

def test_idle_timeout_key_declared():
    from bulk_downloader import global_config as gc
    e = gc.GLOBAL_CONFIG_SCHEMA.get("captcha_takeover_idle_timeout_s")
    assert e is not None, "captcha_takeover_idle_timeout_s not declared"
    assert e.get("type") is str
    assert e.get("safe_default") == "300"


def test_idle_timeout_runtime_read_default():
    from bulk_downloader import captcha_relay
    assert captcha_relay._takeover_idle_timeout_s() == 300


def test_idle_timeout_fe_wired_and_manifest_rowed():
    # The quoted key literal must be in the FE source (settingsSchema + a
    # Settings.tsx setField control + api-types) and the manifest must carry
    # an exposed row -- else open_runtime_tunable goes RED on stash.
    key = "captcha_takeover_idle_timeout_s"
    fe = _ROOT / "frontend" / "src"
    for rel in ("lib/settingsSchema.ts", "lib/api-types.ts", "routes/Settings.tsx"):
        src = (fe / rel).read_text(encoding="utf-8", errors="replace")
        assert key in src, f"{rel} does not carry {key}"
    tsx = (fe / "routes" / "Settings.tsx").read_text(encoding="utf-8", errors="replace")
    assert f'setField("{key}"' in tsx, "Settings.tsx has no setField control for the key"
    import json
    man = json.loads((_ROOT / "reports" / "config_gui_manifest.json").read_text())
    assert man.get("exposed", {}).get(key) == "full", "manifest row missing"


# ════════════════════════════════════════════════════════════════════
#  Idle-timeout behavior
# ════════════════════════════════════════════════════════════════════

class TestIdleTimeout(_RelayReset):
    def _solving(self, url="https://x.com/1", sid="sid-1"):
        from bulk_downloader import captcha_relay
        captcha_relay.mark_captcha_needed("wg", url, "turnstile")
        with captcha_relay._lock:
            p = captcha_relay._pending[url]
            p.status = "solving"
            p.solve_session_id = sid
        return p

    def test_pending_entry_carries_last_input_at(self):
        from bulk_downloader.captcha_relay import PendingCaptcha
        p = PendingCaptcha(url="u", site_id="s", captcha_type="turnstile",
                           detected_at=time.time())
        assert hasattr(p, "last_input_at")

    def test_start_solve_baselines_idle_clock(self):
        from bulk_downloader import captcha_relay
        captcha_relay.mark_captcha_needed("wg", "https://x.com/1", "turnstile")
        captcha_relay.register_takeover_starter(lambda s, u: {"session_id": "sid-9"})
        captcha_relay.start_solve("https://x.com/1")
        with captcha_relay._lock:
            p = captcha_relay._pending["https://x.com/1"]
        assert p.last_input_at is not None, "start_solve must start the idle clock"

    def test_idle_solving_session_is_swept(self):
        from bulk_downloader import captcha_relay, takeover
        p = self._solving()
        takeover.open_channel("sid-1")
        ended = []
        captcha_relay.register_takeover_ender(lambda s, u, r: ended.append((s, u, r)))
        now = time.time()
        p.last_input_at = now - captcha_relay._takeover_idle_timeout_s() - 1
        rep = captcha_relay.sweep_report(now=now)
        assert rep["expired_or_idle"] == 1
        assert captcha_relay.get_pending("https://x.com/1")["status"] == "dismissed"
        assert ended == [("wg", "https://x.com/1", "dismissed")]
        assert takeover.get_channel("sid-1") is None, "sweep must close the reaped session's channel"

    def test_accepted_input_resets_idle_clock(self):
        from bulk_downloader import captcha_relay, takeover
        p = self._solving()
        takeover.open_channel("sid-1")
        old = time.time() - captcha_relay._takeover_idle_timeout_s() - 1
        p.last_input_at = old
        r = captcha_relay.submit_takeover_input(
            "sid-1", {"type": "mouseMoved", "x": 1, "y": 1})
        assert r == "ok"
        with captcha_relay._lock:
            assert captcha_relay._pending["https://x.com/1"].last_input_at > old, \
                "an accepted input must reset the idle clock"
        # ...and the refreshed session survives the sweep
        assert captcha_relay.sweep_expired() == 0

    def test_fresh_solving_session_not_reaped(self):
        from bulk_downloader import captcha_relay
        p = self._solving()
        p.last_input_at = time.time()
        assert captcha_relay.sweep_expired() == 0
        assert captcha_relay.get_pending("https://x.com/1")["status"] == "solving"


# ════════════════════════════════════════════════════════════════════
#  SSE-disconnect teardown
# ════════════════════════════════════════════════════════════════════

class TestSseTeardown(_RelayReset):
    def _solving_gen(self):
        from bulk_downloader import captcha_relay
        captcha_relay.mark_captcha_needed("wg", "https://x.com/1", "turnstile")
        with captcha_relay._lock:
            p = captcha_relay._pending["https://x.com/1"]
            p.status = "solving"
            p.solve_session_id = "sid-1"
        gen = captcha_relay.takeover_screencast("sid-1")
        assert gen is not None
        return gen

    def test_viewer_disconnect_closes_channel(self):
        from bulk_downloader import takeover
        gen = self._solving_gen()
        next(gen)  # open comment; channel is now live
        assert takeover.get_channel("sid-1") is not None
        gen.close()  # client disconnect -> GeneratorExit -> finally teardown
        assert takeover.get_channel("sid-1") is None, \
            "SSE disconnect must close the channel (frees the concurrency slot)"
        assert takeover.active_channel_count() == 0

    def test_viewer_reconnect_reopens(self):
        from bulk_downloader import captcha_relay, takeover
        gen = self._solving_gen()
        next(gen)
        gen.close()
        gen2 = captcha_relay.takeover_screencast("sid-1")
        assert gen2 is not None
        next(gen2)
        assert takeover.get_channel("sid-1") is not None
        gen2.close()

    def test_stream_honors_config_idle_timeout(self, monkeypatch):
        # takeover_screencast must pass the CONFIG idle bound into sse_frames
        # (not the hardcoded 300s default): with a tiny configured timeout the
        # stream self-terminates fast AND the finally closes the channel.
        from bulk_downloader import captcha_relay, takeover
        monkeypatch.setattr(captcha_relay, "_takeover_idle_timeout_s", lambda: 0)
        gen = self._solving_gen()
        t0 = time.monotonic()
        chunks = list(gen)  # runs to idle-timeout termination
        assert time.monotonic() - t0 < 10, "stream must honor the configured idle bound"
        assert any("idle-timeout" in c for c in chunks)
        assert takeover.get_channel("sid-1") is None


# ════════════════════════════════════════════════════════════════════
#  A5-R3 no-orphan sweep
# ════════════════════════════════════════════════════════════════════

class TestNoOrphanSweep(_RelayReset):
    def test_orphan_channel_is_reaped(self):
        # A channel open for a sid the registry does not bind to an ACTIVE
        # solving entry is an orphan. On pristine, the sweep's denominator is
        # only _pending -- it cannot see the channel and reports 0.
        from bulk_downloader import captcha_relay, takeover
        takeover.open_channel("ghost-sid")
        rep = captcha_relay.sweep_report()
        assert rep["orphan_channels"] == 1
        assert takeover.get_channel("ghost-sid") is None

    def test_terminal_status_channel_leak_is_reaped(self):
        from bulk_downloader import captcha_relay, takeover
        captcha_relay.mark_captcha_needed("wg", "https://x.com/1", "turnstile")
        with captcha_relay._lock:
            p = captcha_relay._pending["https://x.com/1"]
            p.status = "resolved"
            p.solve_session_id = "sid-r"
        takeover.open_channel("sid-r")  # leaked past mark_resolved
        rep = captcha_relay.sweep_report()
        assert rep["orphan_channels"] == 1
        assert takeover.get_channel("sid-r") is None

    def test_bound_channel_of_active_solve_survives(self):
        from bulk_downloader import captcha_relay, takeover
        captcha_relay.mark_captcha_needed("wg", "https://x.com/1", "turnstile")
        with captcha_relay._lock:
            p = captcha_relay._pending["https://x.com/1"]
            p.status = "solving"
            p.solve_session_id = "sid-live"
            p.last_input_at = time.time()
        takeover.open_channel("sid-live")
        rep = captcha_relay.sweep_report()
        assert rep["orphan_channels"] == 0
        assert takeover.get_channel("sid-live") is not None

    def test_orphan_browser_is_reaped_via_census(self):
        # A live solve browser whose URL the registry does not carry is a
        # worker-gone/registry-lost orphan: the census sees it, the sweep
        # cross-checks and enders it.
        from bulk_downloader import captcha_relay
        ended = []
        captcha_relay.register_takeover_ender(lambda s, u, r: ended.append((s, u, r)))
        captcha_relay.register_session_census(
            lambda: [("wg", "https://orphan.example/1")])
        rep = captcha_relay.sweep_report()
        assert rep["orphan_browsers"] == 1
        assert ended == [("wg", "https://orphan.example/1", "dismissed")]

    def test_registered_browser_of_active_entry_survives(self):
        from bulk_downloader import captcha_relay
        captcha_relay.mark_captcha_needed("wg", "https://x.com/1", "turnstile")
        ended = []
        captcha_relay.register_takeover_ender(lambda s, u, r: ended.append((s, u, r)))
        captcha_relay.register_session_census(lambda: [("wg", "https://x.com/1")])
        rep = captcha_relay.sweep_report()
        assert rep["orphan_browsers"] == 0
        assert ended == []

    def test_unknown_fails_to_reap_census_unregistered(self):
        # The canonical A5-R3 discipline: a surface the sweep CANNOT verify is
        # a loud third state, never a silent "0 orphans".
        from bulk_downloader import captcha_relay
        rep = captcha_relay.sweep_report()
        assert "browsers" in rep["unverified"]

    def test_unknown_fails_to_reap_census_raising(self):
        from bulk_downloader import captcha_relay
        def _boom():
            raise RuntimeError("census backend gone")
        captcha_relay.register_session_census(_boom)
        rep = captcha_relay.sweep_report()
        assert "browsers" in rep["unverified"]

    def test_verified_census_not_flagged(self):
        from bulk_downloader import captcha_relay
        captcha_relay.register_session_census(lambda: [])
        rep = captcha_relay.sweep_report()
        assert "browsers" not in rep["unverified"]

    def test_reset_for_tests_clears_census(self):
        from bulk_downloader import captcha_relay
        captcha_relay.register_session_census(lambda: [])
        captcha_relay._reset_for_tests()
        assert "browsers" in captcha_relay.sweep_report()["unverified"]

    def test_sweep_expired_back_compat_int(self):
        # The legacy surface keeps its contract: int count, expired pendings
        # reaped, fresh untouched (test_v3_43_60 pins this too).
        from bulk_downloader import captcha_relay
        captcha_relay.mark_captcha_needed("wg", "https://x.com/1", "turnstile")
        future = time.time() + captcha_relay._pending_timeout_s() + 1
        n = captcha_relay.sweep_expired(now=future)
        assert isinstance(n, int) and n == 1


# ════════════════════════════════════════════════════════════════════
#  The reaper actually runs: start_sweeper + app.py wiring
# ════════════════════════════════════════════════════════════════════

class TestSweeperWiring(_RelayReset):
    def test_start_sweeper_keepalive_guarded(self, monkeypatch):
        from bulk_downloader import captcha_relay
        monkeypatch.setenv("BD_DISABLE_KEEPALIVE", "1")
        assert captcha_relay.start_sweeper() is False

    def test_app_wires_census_and_sweeper(self):
        # Source-level pin (the app import is heavy; 757 pattern): the captcha
        # wiring block must register the live-browser census AND start the
        # periodic sweeper -- a reaper nobody runs protects nothing.
        src = (_ROOT / "bulk_downloader" / "app.py").read_text(
            encoding="utf-8", errors="replace")
        assert "register_session_census" in src
        assert "start_sweeper" in src

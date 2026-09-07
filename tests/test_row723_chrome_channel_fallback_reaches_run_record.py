"""Row 723 -- a silent real-Chrome -> bundled-Chromium fallback must reach the
SITE'S RUN RECORD, not only the service log.

`use_real_chrome` sets Playwright channel="chrome", which ONLY Google Chrome
satisfies. On a host without it the launch raises and every launch seam retries
without the channel, writing one line to stderr. A capability that degrades
without telling the run record is indistinguishable from one that worked, so
this gate pins the degradation onto the runner's own event log (the record the
site's UI and history read) and onto cloak's drainable note ledger.

Offline: no browser, no network. Both launch entry points are monkeypatched.
"""
import sys

import pytest
import types
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

import bulk_downloader.cloak as cloak
from bulk_downloader.runner_browser import BrowserMixin

BD_GATE_SCOPE = "module"

# Documented zero-entropy fixture value -- not a secret.
_SITE = "row723site"


class _FakeCtx:
    def __init__(self):
        self.pages = []

    def add_init_script(self, *a, **k):
        pass


class _Runner(BrowserMixin):
    """Minimal SiteRunner stand-in: the mixin only touches self.*."""

    def __init__(self, config):
        self.config = dict(config)
        self.site_id = _SITE
        self.events = []

    def log_event(self, kind, message, url=None, extra=None):
        self.events.append({"kind": kind, "message": str(message),
                            "url": url, "extra": dict(extra or {})})

    def _profile_dir(self, worker_idx=None):
        return "/tmp/row723-profile-does-not-launch"

    def _apply_persistent_cookie_file(self, ctx):
        pass

    def _install_stealth(self, ctx):
        pass


def _chrome_absent_persistent(monkeypatch, ctx):
    """cloak.open_persistent_context: raise iff channel="chrome" is passed."""
    calls = []

    def _open(**kw):
        calls.append(dict(kw))
        if kw.get("channel") == "chrome":
            raise RuntimeError(
                "Chromium distribution 'chrome' is not found at "
                "/opt/google/chrome/chrome")
        return ctx, None, "playwright"

    monkeypatch.setattr(cloak, "open_persistent_context", _open)
    return calls


def _chrome_absent_launch(monkeypatch, sentinel):
    calls = []

    def _launch(**kw):
        calls.append(dict(kw))
        if kw.get("channel") == "chrome":
            raise RuntimeError(
                "Chromium distribution 'chrome' is not found at "
                "/opt/google/chrome/chrome")
        return sentinel, None, "playwright"

    monkeypatch.setattr(cloak, "launch_browser", _launch)
    return calls


def _fallback_events(runner):
    """Events in the run record that name the real-Chrome degradation."""
    out = []
    for ev in runner.events:
        blob = (ev["message"] + " " + repr(ev["extra"])).lower()
        if "chrome" in blob and ("bundled" in blob or "fallback" in blob):
            out.append(ev)
    return out


def test_persistent_fallback_is_recorded_in_the_site_run_record(monkeypatch):
    cloak.reset_cache_for_tests()
    ctx = _FakeCtx()
    calls = _chrome_absent_persistent(monkeypatch, ctx)
    r = _Runner({"use_real_chrome": True, "use_persistent_profile": True,
                 "browser_backend": "playwright", "headless": True})

    got_browser, got_ctx, _pw, _backend = r._launch_browser()

    # PRECONDITION: the fixture really exercised the hazard -- exactly two
    # launch attempts, the first WITH channel="chrome" and the second without.
    assert len(calls) == 2, f"expected 2 launch attempts, got {len(calls)}: {calls}"
    assert calls[0].get("channel") == "chrome"
    assert "channel" not in calls[1]
    assert got_ctx is ctx and got_browser is None

    evs = _fallback_events(r)
    assert len(evs) == 1, (
        "the real-Chrome fallback reached the run record 0 times; "
        f"run record holds {len(r.events)} events: {r.events}")
    ev = evs[0]
    assert ev["extra"].get("requested_channel") == "chrome"
    assert ev["extra"].get("recovered") is True
    assert "chrome" in ev["extra"].get("error", "").lower()


def test_non_persistent_fallback_is_recorded_in_the_site_run_record(monkeypatch):
    cloak.reset_cache_for_tests()
    sentinel = object()
    calls = _chrome_absent_launch(monkeypatch, sentinel)
    r = _Runner({"use_real_chrome": True, "use_persistent_profile": False,
                 "browser_backend": "playwright", "headless": True})

    got_browser, got_ctx, _pw, _backend = r._launch_browser()

    assert len(calls) == 2, f"expected 2 launch attempts, got {len(calls)}: {calls}"
    assert calls[0].get("channel") == "chrome"
    assert "channel" not in calls[1]
    assert got_browser is sentinel and got_ctx is None

    evs = _fallback_events(r)
    assert len(evs) == 1, (
        "the real-Chrome fallback reached the run record 0 times; "
        f"run record holds {len(r.events)} events: {r.events}")
    assert evs[0]["extra"].get("recovered") is True


def test_cloak_ledger_records_the_degradation_for_flows_with_no_runner(monkeypatch):
    """submit/replay login flows have no SiteRunner. cloak keeps a drainable
    note so the owner of the run record can surface it."""
    cloak.reset_cache_for_tests()
    ctx = _FakeCtx()
    _chrome_absent_persistent(monkeypatch, ctx)
    r = _Runner({"use_real_chrome": True, "use_persistent_profile": True,
                 "browser_backend": "playwright", "headless": True})
    r._launch_browser()

    notes = cloak.drain_channel_fallbacks(_SITE)
    assert len(notes) == 1, f"cloak ledger holds {len(notes)} notes: {notes}"
    assert notes[0]["channel"] == "chrome"
    assert notes[0]["recovered"] is True
    assert notes[0]["site_id"] == _SITE
    # Draining is destructive: a second drain is empty, so a later run cannot
    # re-report a degradation that did not happen in it.
    assert cloak.drain_channel_fallbacks(_SITE) == []


def test_no_fallback_no_event_and_no_note(monkeypatch):
    """NEGATIVE CONTROL: Google Chrome present -> channel launch succeeds ->
    the run record carries ZERO degradation events. Fails for the intended
    reason if the patch reports a fallback unconditionally."""
    cloak.reset_cache_for_tests()
    ctx = _FakeCtx()
    calls = []

    def _open(**kw):
        calls.append(dict(kw))
        return ctx, None, "playwright"

    monkeypatch.setattr(cloak, "open_persistent_context", _open)
    r = _Runner({"use_real_chrome": True, "use_persistent_profile": True,
                 "browser_backend": "playwright", "headless": True})
    r._launch_browser()

    assert len(calls) == 1 and calls[0].get("channel") == "chrome"
    assert _fallback_events(r) == []
    assert cloak.drain_channel_fallbacks(_SITE) == []


def test_a_site_that_never_asked_for_real_chrome_reports_nothing(monkeypatch):
    """NEGATIVE CONTROL 2: use_real_chrome unset -> no channel, no note, even
    though the launch path is identical."""
    cloak.reset_cache_for_tests()
    ctx = _FakeCtx()
    calls = _chrome_absent_persistent(monkeypatch, ctx)
    r = _Runner({"use_persistent_profile": True,
                 "browser_backend": "playwright", "headless": True})
    r._launch_browser()

    assert len(calls) == 1 and "channel" not in calls[0]
    assert _fallback_events(r) == []
    assert cloak.drain_channel_fallbacks(_SITE) == []


def test_a_bundled_retry_that_also_fails_is_recorded_not_dropped(monkeypatch):
    """The arm that is easiest to forget: BOTH launches fail. That is a harder
    failure, not the absence of one, so it must still reach the run record with
    recovered=False. Fails if the not-recovered branch is removed."""
    cloak.reset_cache_for_tests()
    calls = []

    def _open(**kw):
        calls.append(dict(kw))
        raise RuntimeError("Chromium distribution 'chrome' is not found")

    def _launch(**kw):
        raise RuntimeError("no display")

    monkeypatch.setattr(cloak, "open_persistent_context", _open)
    monkeypatch.setattr(cloak, "launch_browser", _launch)
    r = _Runner({"use_real_chrome": True, "use_persistent_profile": True,
                 "browser_backend": "playwright", "headless": True})
    try:
        r._launch_browser()
    except Exception:
        pass

    assert len(calls) == 2, f"expected 2 launch attempts, got {len(calls)}"
    # Persistent fails twice, then the product falls through to the
    # non-persistent path, which also fails: TWO distinct launch attempts,
    # each of which asked for real Chrome and did not get it.
    evs = _fallback_events(r)
    assert len(evs) == 2, (
        "the failed bundled retry reached the run record "
        f"{len(evs)} times, expected 2; run record holds "
        f"{len(r.events)} events: {r.events}")
    assert [e["extra"].get("recovered") for e in evs] == [False, False]
    notes = cloak.drain_channel_fallbacks(_SITE)
    assert len(notes) == 2 and not any(n["recovered"] for n in notes)


def test_a_caller_with_no_event_log_still_leaves_a_drainable_note(monkeypatch):
    """Login/replay flows own no event log. The degradation must survive in
    cloak's ledger so whoever owns the run record can surface it."""
    cloak.reset_cache_for_tests()
    ctx = _FakeCtx()
    _chrome_absent_persistent(monkeypatch, ctx)

    class _NoLog(_Runner):
        log_event = None

    r = _NoLog({"use_real_chrome": True, "use_persistent_profile": True,
                "browser_backend": "playwright", "headless": True})
    r.log_event = None
    r._launch_browser()

    notes = cloak.drain_channel_fallbacks(_SITE)
    assert len(notes) == 1, f"ledger holds {len(notes)} notes: {notes}"
    assert notes[0]["recovered"] is True


def test_pending_notes_from_a_login_flow_are_surfaced_into_the_run_record():
    """cloak.persistent_context (login verify / capture) has no runner. Its
    note is drained into THIS site's run record, and another site's note is
    left alone for its own owner."""
    cloak.reset_cache_for_tests()
    cloak.note_channel_fallback(site_id=_SITE, flow="login submit",
                                channel="chrome", error="not found",
                                recovered=True)
    cloak.note_channel_fallback(site_id="other-site", flow="login submit",
                                channel="chrome", error="not found",
                                recovered=True)
    r = _Runner({"use_real_chrome": True})
    n = r._surface_pending_channel_fallbacks()

    assert n == 1, f"surfaced {n} notes, expected exactly 1"
    evs = _fallback_events(r)
    assert len(evs) == 1 and evs[0]["extra"]["flow"] == "login submit"
    # The other site's note is untouched -- draining is per-site.
    assert len(cloak.drain_channel_fallbacks("other-site")) == 1
    assert cloak.drain_channel_fallbacks(_SITE) == []


# ── the cloak.persistent_context arm itself (lens L2 refusal: the arm was
#    unguarded -- test 8 drove the ledger PRIMITIVE, never this arm) ────────

class _FakePW:
    def __init__(self):
        self.stopped = False

    def stop(self):
        self.stopped = True


def _chrome_absent_opc(monkeypatch, ctx, *, retry_also_fails=False):
    """Patch the name persistent_context resolves at call time. Raises on
    channel="chrome"; the bundled retry succeeds unless asked to fail."""
    calls = []
    pw = _FakePW()

    def _open(**kw):
        calls.append(dict(kw))
        if kw.get("channel") == "chrome":
            raise RuntimeError(
                "Chromium distribution 'chrome' is not found at "
                "/opt/google/chrome/chrome")
        if retry_also_fails:
            raise RuntimeError("bundled Chromium failed too: no display")
        return ctx, pw, "playwright"

    monkeypatch.setattr(cloak, "open_persistent_context", _open)
    return calls, pw


def test_persistent_context_records_its_recovered_channel_fallback(monkeypatch):
    """DRIVES THE ARM. cloak.persistent_context is the seam login verify and
    capture reach (login_impl/replay.py:413). Its channel_fallback retry must
    leave a note, or the degradation is invisible to whoever owns the record."""
    cloak.reset_cache_for_tests()
    ctx = _FakeCtx()
    ctx.closed = False
    ctx.close = lambda: setattr(ctx, "closed", True)
    calls, pw = _chrome_absent_opc(monkeypatch, ctx)

    with cloak.persistent_context(
            user_data_dir="/tmp/row723-cloak-arm", headless=True,
            config={"site_id": _SITE}, channel="chrome") as (got, backend):
        assert got is ctx
        assert backend == "playwright"

    # PRECONDITION: the hazard really fired -- two attempts, first WITH the
    # channel and second without.
    assert len(calls) == 2, f"expected 2 launch attempts, got {len(calls)}: {calls}"
    assert calls[0].get("channel") == "chrome"
    assert "channel" not in calls[1]
    assert ctx.closed is True and pw.stopped is True

    notes = cloak.drain_channel_fallbacks(_SITE)
    assert len(notes) == 1, (
        "the persistent_context channel fallback left "
        f"{len(notes)} notes, expected exactly 1: {notes}")
    n = notes[0]
    assert n["recovered"] is True
    assert n["flow"] == "persistent_context"
    assert n["channel"] == "chrome"
    assert "chrome" in n["error"].lower()


def test_persistent_context_records_a_retry_that_also_fails(monkeypatch):
    """The arm's OTHER outcome: the bundled retry fails too and the error
    propagates. That is a harder failure, not the absence of one, so the note
    must still be there for the caller that owns the record."""
    cloak.reset_cache_for_tests()
    ctx = _FakeCtx()
    calls, _pw = _chrome_absent_opc(monkeypatch, ctx, retry_also_fails=True)

    with pytest.raises(RuntimeError) as excinfo:
        with cloak.persistent_context(
                user_data_dir="/tmp/row723-cloak-arm", headless=True,
                config={"site_id": _SITE}, channel="chrome") as _:
            pass
    assert "bundled Chromium failed too" in str(excinfo.value)

    assert len(calls) == 2, f"expected 2 launch attempts, got {len(calls)}"
    notes = cloak.drain_channel_fallbacks(_SITE)
    assert len(notes) == 1, (
        f"the failed retry left {len(notes)} notes, expected exactly 1: {notes}")
    assert notes[0]["recovered"] is False
    assert notes[0]["flow"] == "persistent_context"


def test_persistent_context_without_a_channel_records_nothing(monkeypatch):
    """NEGATIVE CONTROL for the arm: no channel was ever asked for, so a
    launch failure is NOT a real-Chrome degradation and must not be recorded.
    The original error propagates unchanged."""
    cloak.reset_cache_for_tests()
    ctx = _FakeCtx()

    def _open(**kw):
        raise RuntimeError("profile is locked by another process")

    monkeypatch.setattr(cloak, "open_persistent_context", _open)

    with pytest.raises(RuntimeError) as excinfo:
        with cloak.persistent_context(
                user_data_dir="/tmp/row723-cloak-arm", headless=True,
                config={"site_id": _SITE}) as _:
            pass
    assert "profile is locked" in str(excinfo.value)
    assert cloak.drain_channel_fallbacks(_SITE) == []


def test_persistent_context_channel_fallback_disabled_records_nothing(monkeypatch):
    """NEGATIVE CONTROL 2: channel_fallback=False means no retry happens, so
    there is no fallback to report -- exactly one attempt, no note."""
    cloak.reset_cache_for_tests()
    ctx = _FakeCtx()
    calls, _pw = _chrome_absent_opc(monkeypatch, ctx)

    with pytest.raises(RuntimeError):
        with cloak.persistent_context(
                user_data_dir="/tmp/row723-cloak-arm", headless=True,
                config={"site_id": _SITE}, channel="chrome",
                channel_fallback=False) as _:
            pass

    assert len(calls) == 1, f"expected exactly 1 attempt, got {len(calls)}"
    assert cloak.drain_channel_fallbacks(_SITE) == []

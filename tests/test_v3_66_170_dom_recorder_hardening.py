"""v3.66.170 — dom_recorder hardening (H1/H2/H3).

Surface previously-silent capture failures:
  H1 — drain-time drop-counter surfacing (truncated dom_log is no longer silent)
  H2 — persistent arm/injection-failure streak warning (empty dom_log gets a cause)
  H3 — oversize DOM-snapshot guard (drop + warn once, don't store/OOM)

Synthetic/mocked pages only — no real browser. Zero-arg functions (custom
runner convention); also pytest-compatible.
"""
import io
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

import bulk_downloader.dom_recorder as d  # noqa: E402


# ── fakes ────────────────────────────────────────────────────────────────────
class _FakeCapture:
    def __init__(self):
        self.events = []
        self.snapshots = []

    def record_dom_event(self, **kw):
        self.events.append(kw)

    def record_dom_snapshot(self, data_url, label=None):
        self.snapshots.append((data_url, label))


class _DrainPage:
    """page.evaluate(_DRAIN_JS) -> configured result; anything else -> None."""
    def __init__(self, result):
        self._result = result

    def evaluate(self, js):
        return self._result if js == d._DRAIN_JS else None


class _FailPage:
    """Every probe fails -> _arm_dom_recorder_impl returns False."""
    def evaluate(self, js):
        raise RuntimeError("boom")

    def add_script_tag(self, content=None):
        raise RuntimeError("csp blocked")


class _StartedPage:
    """Reports recording already started -> impl returns True."""
    def evaluate(self, js):
        return True if js == d._STARTED_JS else None


class _SnapPage:
    def __init__(self, data_url, has_snapdom=True):
        self._data = data_url
        self._has = has_snapdom

    def evaluate(self, js):
        if js == d._SNAPDOM_SNAPSHOT:
            return self._data
        if "typeof window.snapdom" in js:
            return self._has
        return None

    def add_script_tag(self, content=None):
        pass


def _reset():
    d._DOM_DROPPED_TOTAL = 0
    d._DOM_DROPPED_WARNED = False
    d._ARM_FAIL_STREAK = 0
    d._ARM_WARNED = False
    d._SNAPSHOT_WARNED = False


def _stderr_of(fn):
    buf = io.StringIO()
    old = sys.stderr
    sys.stderr = buf
    try:
        fn()
    finally:
        sys.stderr = old
    return buf.getvalue()


_EV = {"type": 2, "data": {"node": 1}, "timestamp": 1}  # full snapshot -> maps


# ── H1: drop-counter surfacing ───────────────────────────────────────────────
def test_h1_drop_count_surfaced_warns_once_and_accumulates():
    _reset()
    cap = _FakeCapture()
    out = _stderr_of(lambda: d.drain_dom_events(_DrainPage({"events": [_EV, _EV], "dropped": 3}), cap))
    assert len(cap.events) == 2
    assert d.get_status()["dom_events_dropped"] == 3
    assert "TRUNCATED" in out
    # second drain with more drops accumulates the total but does NOT re-warn
    out2 = _stderr_of(lambda: d.drain_dom_events(_DrainPage({"events": [], "dropped": 2}), cap))
    assert d.get_status()["dom_events_dropped"] == 5
    assert out2 == ""


def test_h1_tolerates_bare_list_shape():
    _reset()
    cap = _FakeCapture()
    n = d.drain_dom_events(_DrainPage([_EV]), cap)   # pre-upgrade page: bare list
    assert n == 1 and len(cap.events) == 1
    assert d.get_status()["dom_events_dropped"] == 0


def test_h1_no_drops_is_silent():
    _reset()
    cap = _FakeCapture()
    out = _stderr_of(lambda: d.drain_dom_events(_DrainPage({"events": [_EV], "dropped": 0}), cap))
    assert out == "" and d.get_status()["dom_events_dropped"] == 0


# ── H2: persistent arm-failure streak ────────────────────────────────────────
def test_h2_arm_failure_warns_once_at_threshold_then_resets_on_success():
    _reset()
    page = _FailPage()
    below = _stderr_of(lambda: [d.arm_dom_recorder(page) for _ in range(d._ARM_FAIL_WARN_AT - 1)])
    assert d.get_status()["arm_fail_streak"] == d._ARM_FAIL_WARN_AT - 1
    assert below == ""                                   # below threshold: quiet
    at = _stderr_of(lambda: d.arm_dom_recorder(page))    # the one that hits the cap
    assert "could not be armed" in at
    again = _stderr_of(lambda: d.arm_dom_recorder(page))  # past threshold: warn-once
    assert again == ""
    assert d.arm_dom_recorder(_StartedPage()) is True     # success ...
    assert d.get_status()["arm_fail_streak"] == 0         # ... resets the streak


# ── H3: oversize snapshot guard ──────────────────────────────────────────────
def test_h3_oversize_snapshot_dropped_not_stored_warns_once():
    _reset()
    cap = _FakeCapture()
    page = _SnapPage("d" * (d._SNAPSHOT_MAX_CHARS + 1))
    res = {}
    out = _stderr_of(lambda: res.__setitem__("v", d.snapshot_dom(page, cap)))
    assert res["v"] is None
    assert cap.snapshots == []                            # not stored
    assert "oversize" in out
    out2 = _stderr_of(lambda: d.snapshot_dom(page, cap))  # warn-once
    assert out2 == ""


def test_h3_under_cap_snapshot_is_stored():
    _reset()
    cap = _FakeCapture()
    small = "data:image/png;base64,AAAA"
    res = d.snapshot_dom(_SnapPage(small), cap, label="t")
    assert res == small
    assert cap.snapshots == [(small, "t")]

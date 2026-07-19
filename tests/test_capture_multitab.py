"""capture_session._attach_recorders — multi-tab recorder attachment.

Pins the v3.66.158.3 fix: both recorders (network CDP + rrweb DOM) must attach to
EVERY page the context opens — the initial page AND any new tab/popup (oauth login
redirect, popped playback window, download-modal tab) — not just the first. The old
single-page binding recorded the abandoned initial page (0 dom / 0 segments) while the
real session happened in a spawned page.

Browser-free: fakes the Playwright context + pages and monkeypatches the two attachers
in their source modules, restoring them in finally (the custom runner's monkeypatch is
unreliable, so we restore module globals by hand).
"""
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))
sys.path.insert(0, str(_ROOT / "tools"))

import capture_session as cs
import bulk_downloader.session_capture as _sc
import bulk_downloader.dom_recorder as _dr


class _FakeContext:
    """Minimal Playwright-context stand-in: records on() handlers and can emit."""
    def __init__(self):
        self._handlers = {}

    def on(self, event, handler):
        self._handlers.setdefault(event, []).append(handler)

    def emit_page(self, page):
        for h in self._handlers.get("page", []):
            h(page)


class _FakePage:
    def __init__(self, name):
        self.name = name

    def __repr__(self):
        return f"<page {self.name}>"


def _patched(cdp_calls, dom_calls, *, dom_raises_for=None):
    """Swap the two attachers for counters. Returns a restore() callable."""
    orig_cdp = _sc.capture_via_cdp
    orig_dom = _dr.attach_dom_recorder

    def fake_cdp(pg, capture=None, redact=True):
        cdp_calls.append(pg)

    def fake_dom(pg, capture, *, redact=True):
        if dom_raises_for is not None and pg is dom_raises_for:
            raise RuntimeError("boom on this page")
        dom_calls.append(pg)

    _sc.capture_via_cdp = fake_cdp
    _dr.attach_dom_recorder = fake_dom

    def restore():
        _sc.capture_via_cdp = orig_cdp
        _dr.attach_dom_recorder = orig_dom
    return restore


def test_initial_page_gets_both_recorders():
    cdp, dom = [], []
    restore = _patched(cdp, dom)
    try:
        ctx, p0 = _FakeContext(), _FakePage("initial")
        cs._attach_recorders(ctx, p0, capture=object(), redact=True)
    finally:
        restore()
    assert cdp == [p0]
    assert dom == [p0]


def test_new_tab_after_launch_is_wired():
    """The oauth-redirect target / popped playback tab must get both recorders."""
    cdp, dom = [], []
    restore = _patched(cdp, dom)
    try:
        ctx, p0 = _FakeContext(), _FakePage("initial")
        cs._attach_recorders(ctx, p0, capture=object(), redact=True)
        # app opens a new tab (e.g. oauth login redirect / playback popup)
        p1 = _FakePage("playback")
        ctx.emit_page(p1)
    finally:
        restore()
    assert p1 in cdp and p1 in dom
    assert cdp == [p0, p1] and dom == [p0, p1]


def test_multiple_new_tabs_all_wired():
    cdp, dom = [], []
    restore = _patched(cdp, dom)
    try:
        ctx, p0 = _FakeContext(), _FakePage("initial")
        cs._attach_recorders(ctx, p0, capture=object(), redact=True)
        pages = [_FakePage(f"tab{i}") for i in range(3)]
        for p in pages:
            ctx.emit_page(p)
    finally:
        restore()
    for p in pages:
        assert p in cdp and p in dom


def test_same_page_wired_once():
    """A page emitted twice (or the initial page re-emitted) is wired once."""
    cdp, dom = [], []
    restore = _patched(cdp, dom)
    try:
        ctx, p0 = _FakeContext(), _FakePage("initial")
        cs._attach_recorders(ctx, p0, capture=object(), redact=True)
        ctx.emit_page(p0)          # same as initial — must not double-wire
        p1 = _FakePage("tab1")
        ctx.emit_page(p1)
        ctx.emit_page(p1)          # duplicate
    finally:
        restore()
    assert cdp.count(p0) == 1 and dom.count(p0) == 1
    assert cdp.count(p1) == 1 and dom.count(p1) == 1


def test_one_bad_page_does_not_crash_capture():
    """An attacher raising on one page must not stop other pages from wiring."""
    cdp, dom = [], []
    bad = _FakePage("bad")
    restore = _patched(cdp, dom, dom_raises_for=bad)
    try:
        ctx, p0 = _FakeContext(), _FakePage("initial")
        cs._attach_recorders(ctx, p0, capture=object(), redact=True)
        ctx.emit_page(bad)         # dom attacher raises here — swallowed
        good = _FakePage("good")
        ctx.emit_page(good)        # must still wire
    finally:
        restore()
    # bad page: cdp still ran, dom raised+swallowed (not recorded)
    assert bad in cdp and bad not in dom
    # good page after the bad one is fully wired
    assert good in cdp and good in dom


def test_context_without_on_is_tolerated():
    """If ctx.on isn't available, the initial page must still be wired."""
    cdp, dom = [], []
    restore = _patched(cdp, dom)

    class _NoOnContext:
        pass
    try:
        p0 = _FakePage("initial")
        cs._attach_recorders(_NoOnContext(), p0, capture=object(), redact=True)
    finally:
        restore()
    assert cdp == [p0] and dom == [p0]


def test_returns_wired_set():
    cdp, dom = [], []
    restore = _patched(cdp, dom)
    try:
        ctx, p0 = _FakeContext(), _FakePage("initial")
        wired = cs._attach_recorders(ctx, p0, capture=object(), redact=True)
        p1 = _FakePage("tab1")
        ctx.emit_page(p1)
    finally:
        restore()
    assert p0 in wired and p1 in wired

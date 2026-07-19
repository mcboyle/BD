"""v3.66.165 — DOM recorder arm-after-load + buffer-drain transport.

Root cause (confirmed by on-stash runtime bisection, eight probes):
  1. ``rrweb.record()`` armed at document-start (the old ``add_init_script``
     path) over a not-yet-built SPA DOM **SIGTRAPped the renderer** ("Aw, Snap")
     on reptyle. Arming AFTER load (via ``add_script_tag``) is crash-free.
  2. Even when record started, rrweb's ``emit`` closure could not call the
     Playwright-exposed binding (``TypeError: this._global[this._globalBindingName]
     is not a function``), so every event was dropped and ``dom_log`` stayed
     empty. Emitting into an in-page buffer that Python drains over
     ``page.evaluate`` delivers the events (full snapshot + incrementals).

These tests are browser-free: they pin the static script shape and exercise
``arm_dom_recorder`` / ``drain_dom_events`` / ``attach_dom_recorder`` /
``_wait_for_finish(on_tick=...)`` with a fake page. The live behaviour is
proven on stash and re-checked in the (browser-gated) behavioral tier of
``test_dom_recorder_asi.py``.
"""
from pathlib import Path

from bulk_downloader import dom_recorder as dr
from bulk_downloader.dom_capture import DomCapture
from tools import capture_session as cs


# ----------------------------------------------------------------- static tier

def test_bootstrap_emits_to_buffer_not_binding():
    """Emit targets the in-page buffer; the old binding name is gone."""
    s = dr.recorder_script()
    assert "__bd_dom_buf" in s, "bootstrap must emit into window.__bd_dom_buf"
    assert "__bd_dom_event" not in s, "old Playwright binding must be removed"
    assert "rrweb.record" in s
    # redaction classes still configured browser-side
    assert "bd-mask" in s and "bd-block" in s


def test_bootstrap_has_buffer_safety_cap():
    """The in-page buffer must have a bounded safety valve (drop-and-count)
    so a long session between drains can't OOM the renderer."""
    assert "__bd_dom_dropped" in dr._BOOTSTRAP
    assert "200000" in dr._BOOTSTRAP


def test_recorder_script_separator_preserved():
    """The Finding-D ASI separator (load-bearing ';') is unchanged."""
    s = dr.recorder_script()
    i = s.rindex(dr._BOOTSTRAP)
    assert s[:i].rstrip().endswith(";")
    assert s != dr.rrweb_js() + "\n" + dr._BOOTSTRAP  # not the broken join


# --------------------------------------------------------------- fake page

class FakePage:
    """Minimal Playwright-page stand-in for the arm/drain probes."""

    def __init__(self, *, started=False, readystate="complete", batch=None,
                 evaluate_raises=False, add_tag_raises=False):
        self._started = started
        self._readystate = readystate
        self._batch = list(batch) if batch is not None else []
        self._evaluate_raises = evaluate_raises
        self._add_tag_raises = add_tag_raises
        self.script_tags = []
        self.init_scripts = []
        self.bindings = []

    def evaluate(self, expr):
        if self._evaluate_raises:
            raise RuntimeError("evaluate boom")
        if "__bd_rrweb_started" in expr:
            return self._started
        if "readyState" in expr:
            return self._readystate
        if "__bd_dom_buf" in expr:          # the drain splice
            b, self._batch = self._batch, []
            return b
        return None

    def add_script_tag(self, content=None):
        if self._add_tag_raises:
            raise RuntimeError("CSP blocked add_script_tag")
        self.script_tags.append(content)
        self._started = True                 # bootstrap runs → record() starts

    def add_init_script(self, *a, **k):
        self.init_scripts.append(a)

    def expose_binding(self, *a, **k):
        self.bindings.append(("binding",) + a)

    def expose_function(self, *a, **k):
        self.bindings.append(("function",) + a)


# ------------------------------------------------------------- arm_dom_recorder

def test_arm_injects_and_starts_when_usable():
    page = FakePage(started=False, readystate="complete")
    assert dr.arm_dom_recorder(page) is True
    assert len(page.script_tags) == 1
    assert page.script_tags[0] == dr.recorder_script()  # lib + ';' + bootstrap
    # never the crashing document-start path
    assert page.init_scripts == []


def test_arm_is_idempotent_when_already_started():
    page = FakePage(started=True)
    assert dr.arm_dom_recorder(page) is True
    assert page.script_tags == []            # no re-injection once active


def test_arm_skips_when_document_not_usable():
    page = FakePage(started=False, readystate="loading")
    assert dr.arm_dom_recorder(page) is False
    assert page.script_tags == []


def test_arm_returns_false_on_evaluate_failure_without_raising():
    page = FakePage(evaluate_raises=True)
    assert dr.arm_dom_recorder(page) is False
    assert page.script_tags == []


def test_arm_returns_false_when_injection_blocked():
    # readyState usable, but add_script_tag is blocked (e.g. page CSP)
    page = FakePage(started=False, readystate="complete", add_tag_raises=True)
    assert dr.arm_dom_recorder(page) is False


# ------------------------------------------------------------ drain_dom_events

def _evt(t, data=None, ts=1):
    return {"type": t, "data": data or {}, "timestamp": ts}


def test_drain_feeds_events_into_capture():
    cap = DomCapture(url="x", redact=True)
    batch = [
        _evt(4, {"href": "https://x", "width": 800, "height": 600}),  # meta
        _evt(2, {"node": {"id": 1}}),                                 # full snapshot
        _evt(3, {"source": 0}),                                       # incremental
    ]
    page = FakePage(batch=batch)
    n = dr.drain_dom_events(page, cap)
    assert n == 3
    assert len(cap.dom_log) == 3
    # record_dom_event stores full snapshots as type == "full_snapshot"
    assert any(e.get("type") == "full_snapshot" for e in cap.dom_log)


def test_drain_empty_buffer_returns_zero():
    cap = DomCapture(url="x", redact=True)
    assert dr.drain_dom_events(FakePage(batch=[]), cap) == 0
    assert cap.dom_log == []


def test_drain_skips_unmappable_events_without_raising():
    cap = DomCapture(url="x", redact=True)
    batch = [_evt(2, {"node": {}}), "not-a-dict", _evt(99), _evt(3, {"source": 1})]
    n = dr.drain_dom_events(FakePage(batch=batch), cap)
    # type 2 + type 3 map; the non-dict and type-99 are skipped (None mapping)
    assert n == 2
    assert len(cap.dom_log) == 2


def test_drain_returns_zero_on_evaluate_failure():
    cap = DomCapture(url="x", redact=True)
    assert dr.drain_dom_events(FakePage(evaluate_raises=True), cap) == 0


# ----------------------------------------------------------- attach_dom_recorder

def test_attach_does_not_arm_or_bind():
    """The registrar must NOT install the document-start init script or any
    Playwright binding — both caused the live failures."""
    cap = DomCapture(url="x", redact=True)
    page = FakePage()
    out = dr.attach_dom_recorder(page, cap, redact=True)
    assert out is cap
    assert page.init_scripts == []
    assert page.bindings == []
    assert page.script_tags == []            # arming is the pump's job, not attach's


# ---------------------------------------------------- _wait_for_finish on_tick

def test_wait_for_finish_invokes_on_tick(tmp_path):
    ticks = {"n": 0}
    finish = tmp_path / "FINISH"

    def _tick():
        ticks["n"] += 1
        if ticks["n"] >= 2:          # create the sentinel after a couple ticks
            finish.write_text("x")

    reason = cs._wait_for_finish(tmp_path, max_wait=10, finish_file=str(finish),
                                 on_tick=_tick)
    assert reason == "finish"
    assert ticks["n"] >= 1            # the drain hook fired while waiting


def test_wait_for_finish_without_on_tick_still_works(tmp_path):
    finish = tmp_path / "FINISH"
    finish.write_text("x")
    assert cs._wait_for_finish(tmp_path, max_wait=2, finish_file=str(finish)) == "finish"

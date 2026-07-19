"""P5-3b tests — runner.log_event routing for honeypot_filtered (v3.66.29).

R-P5-3 (v3.66.28) emitted the honeypot-filter summary via
``sys.stderr.write``. The original P5-3 spec asked for
``log_event("honeypot_filtered", count=N, reasons=[...])`` instead,
so the summary participates in the runner's structured event log
(carries site_id, persists in the bounded deque, broadcasts via SSE,
and still mirrors to stderr automatically via log_event itself).

P5-3b (this release) adds a ``runner=None`` kwarg to
``find_best_download``. When set, the summary is emitted via
``runner.log_event(...)``; when None or when ``log_event`` raises,
the v3.66.28 stderr behavior is preserved (fail-open).

Acceptance criteria pinned here:

  - runner=None (default) → stderr emission unchanged (back-compat
    with v3.66.28 — auto_detect.py path must not break)
  - runner=<mock> with a filtered candidate → log_event called once
    with kind="honeypot_filtered", a message string carrying the
    same fields as the v3.66.28 stderr line, and an extra dict with
    {count, mode, reasons, all_dropped}. NO stderr line.
  - All-candidates-dropped path with runner=<mock> → same routing,
    extra.all_dropped=True, return value still None
  - runner.log_event raises → fall back to stderr (operator signal
    must not be silenced by a logging bug)
  - No decoys filtered → neither log_event nor stderr called
  - Filter off (env unset) but runner passed → unchanged behavior
  - reasons truncated to first 10 (matches v3.66.28 contract)

Polluter-isolation note: this file does not mutate sys.modules.
``find_best_download`` is imported fresh; the helper is a closure
created per call so the runner argument doesn't survive across
tests.
"""

import sys

import pytest


# --------------------------------------------------------------------------
# Test doubles
# --------------------------------------------------------------------------

class _MockLocator:
    """Same shape as the locator stand-in in
    test_v3_66_28_phase5_dom_honeypot.py — kept in this file so the
    two test files don't have a cross-import."""

    def __init__(self, *, visible=True, attrs=None, text=""):
        self._visible = visible
        self._attrs = attrs or {}
        self._text = text

    def is_visible(self, timeout=None):
        return self._visible

    def get_attribute(self, name):
        return self._attrs.get(name)

    def inner_text(self, timeout=None):
        return self._text


_MockLocator.all = lambda self: [self]
_MockLocator.count = lambda self: 1
_MockLocator.first = property(lambda self: self)


class _MockLocatorList:
    def __init__(self, elements):
        self._elements = elements

    def all(self):
        return list(self._elements)

    def count(self):
        return len(self._elements)

    @property
    def first(self):
        return _MockLocatorList(self._elements[:1] if self._elements else [])

    def nth(self, i):
        return self._elements[i]


class _MockPage:
    def __init__(self, selector_map):
        self._map = selector_map

    def locator(self, sel):
        return _MockLocatorList(self._map.get(sel, []))


class _RecordingRunner:
    """Minimal stand-in for the Runner class — captures log_event
    invocations for assertion. Mirrors the real ``log_event`` signature:
        log_event(kind, message, url=None, extra=None)
    """

    def __init__(self, *, raises=False):
        self.events = []
        self.raises = raises

    def log_event(self, kind, message, url=None, extra=None):
        if self.raises:
            raise RuntimeError("event log offline")
        self.events.append({
            "kind": kind,
            "message": message,
            "url": url,
            "extra": extra,
        })


def _make_visible_clean():
    return _MockLocator(
        visible=True,
        attrs={"href": "https://cdn.example.com/video.mp4"},
        text="Download 1080p",
    )


def _make_invisible_decoy(*, href="https://cdn.example.com/decoy.mp4",
                          text="Download HD"):
    return _MockLocator(
        visible=False,
        attrs={"href": href},
        text=text,
    )


def _page_with(*locs):
    # Wide-scan's first selector that catches our .mp4 locators.
    return _MockPage({"a[href*='.mp4']": list(locs)})


# --------------------------------------------------------------------------
# A. runner=None preserves the v3.66.28 stderr contract
# --------------------------------------------------------------------------

class TestRunnerNonePreservesStderr:
    """When no runner is passed (the auto_detect.py path), the
    v3.66.28 stderr emission must be byte-for-byte unchanged."""

    def test_runner_none_emits_to_stderr_when_filtered(
            self, monkeypatch, capsys):
        from bulk_downloader.detect import find_best_download
        monkeypatch.setenv("BD_DOM_HONEYPOT_FILTER", "cheap")
        page = _page_with(_make_visible_clean(), _make_invisible_decoy())
        # No runner argument — defaults to None.
        result = find_best_download(page)
        assert result is not None
        err = capsys.readouterr().err
        assert "honeypot_filtered" in err
        assert "count=" in err
        assert "mode=cheap" in err
        assert "reasons=" in err

    def test_runner_none_all_dropped_emits_to_stderr(
            self, monkeypatch, capsys):
        from bulk_downloader.detect import find_best_download
        monkeypatch.setenv("BD_DOM_HONEYPOT_FILTER", "cheap")
        page = _page_with(
            _make_invisible_decoy(href="https://x.com/clk?id=1",
                                  text="Download 1080p"),
            _make_invisible_decoy(href="https://x.com/clk?id=2",
                                  text="Download HD"),
        )
        result = find_best_download(page)
        assert result is None
        err = capsys.readouterr().err
        assert "honeypot_filtered" in err
        assert "all candidates dropped" in err

    def test_runner_none_default_kwarg_no_break(
            self, monkeypatch, capsys):
        """Positional + keyword call shape from auto_detect.py path
        must still work without explicitly passing runner."""
        from bulk_downloader.detect import find_best_download
        monkeypatch.delenv("BD_DOM_HONEYPOT_FILTER", raising=False)
        page = _page_with(_make_visible_clean())
        # Mirrors auto_detect.py:271 — custom="" learned=None, no runner.
        result = find_best_download(page, custom="", learned=None)
        assert result is not None
        # Filter off → nothing on stderr.
        assert "honeypot_filtered" not in capsys.readouterr().err


# --------------------------------------------------------------------------
# B. runner=<mock> routes through log_event
# --------------------------------------------------------------------------

class TestRunnerLogEventRouting:
    """When a runner is passed, the summary goes to log_event,
    NOT stderr."""

    def test_log_event_called_when_runner_set(
            self, monkeypatch, capsys):
        from bulk_downloader.detect import find_best_download
        monkeypatch.setenv("BD_DOM_HONEYPOT_FILTER", "cheap")
        runner = _RecordingRunner()
        page = _page_with(_make_visible_clean(), _make_invisible_decoy())
        result = find_best_download(page, runner=runner)
        assert result is not None
        # Exactly one log_event call.
        assert len(runner.events) == 1
        ev = runner.events[0]
        assert ev["kind"] == "honeypot_filtered"

    def test_no_stderr_when_runner_handles_it(
            self, monkeypatch, capsys):
        """The whole point of P5-3b — the stderr line disappears
        when log_event takes responsibility."""
        from bulk_downloader.detect import find_best_download
        monkeypatch.setenv("BD_DOM_HONEYPOT_FILTER", "cheap")
        runner = _RecordingRunner()
        page = _page_with(_make_visible_clean(), _make_invisible_decoy())
        find_best_download(page, runner=runner)
        # find_best_download itself must not write to stderr — the
        # runner's log_event handles mirroring to stderr internally
        # (which the mock does NOT do, so capsys.err stays clean).
        err = capsys.readouterr().err
        assert "honeypot_filtered" not in err

    def test_log_event_message_contains_summary_fields(
            self, monkeypatch):
        from bulk_downloader.detect import find_best_download
        monkeypatch.setenv("BD_DOM_HONEYPOT_FILTER", "cheap")
        runner = _RecordingRunner()
        page = _page_with(_make_visible_clean(), _make_invisible_decoy())
        find_best_download(page, runner=runner)
        msg = runner.events[0]["message"]
        # The same shape the v3.66.28 stderr line carried.
        assert "honeypot_filtered" in msg
        assert "count=" in msg
        assert "mode=cheap" in msg
        assert "reasons=" in msg

    def test_log_event_extra_dict_shape(self, monkeypatch):
        from bulk_downloader.detect import find_best_download
        monkeypatch.setenv("BD_DOM_HONEYPOT_FILTER", "cheap")
        runner = _RecordingRunner()
        page = _page_with(_make_visible_clean(), _make_invisible_decoy())
        find_best_download(page, runner=runner)
        extra = runner.events[0]["extra"]
        assert isinstance(extra, dict)
        # The structured payload the SSE subscribers and future
        # learning pipelines can consume.
        assert set(extra.keys()) >= {
            "count", "mode", "reasons", "all_dropped"}
        assert extra["mode"] == "cheap"
        assert extra["count"] >= 1
        assert isinstance(extra["reasons"], list)
        assert extra["all_dropped"] is False

    def test_log_event_extra_count_matches_message(self, monkeypatch):
        """The count in extra MUST match the count rendered in
        the message string — UI consumers may use either field."""
        from bulk_downloader.detect import find_best_download
        monkeypatch.setenv("BD_DOM_HONEYPOT_FILTER", "cheap")
        runner = _RecordingRunner()
        page = _page_with(
            _make_visible_clean(),
            _make_invisible_decoy(href="https://x.com/clk?id=1",
                                  text="Download HD"),
            _make_invisible_decoy(href="https://x.com/clk?id=2",
                                  text="Download 720p"),
        )
        find_best_download(page, runner=runner)
        ev = runner.events[0]
        # Message has count=N for some N >= 2.
        import re
        m = re.search(r"count=(\d+)", ev["message"])
        assert m is not None
        count_from_msg = int(m.group(1))
        assert count_from_msg == ev["extra"]["count"]

    def test_log_event_mode_strict_routes_through(self, monkeypatch):
        """strict mode is currently the same code path as cheap;
        confirm log_event still fires when the env value is strict."""
        from bulk_downloader.detect import find_best_download
        monkeypatch.setenv("BD_DOM_HONEYPOT_FILTER", "strict")
        runner = _RecordingRunner()
        page = _page_with(_make_visible_clean(), _make_invisible_decoy())
        find_best_download(page, runner=runner)
        assert len(runner.events) == 1
        assert runner.events[0]["extra"]["mode"] == "strict"


# --------------------------------------------------------------------------
# C. All-dropped path with runner set
# --------------------------------------------------------------------------

class TestAllDroppedWithRunner:
    """When every candidate is filtered, the function returns None
    but the runner still receives the summary — and extra.all_dropped
    is True so consumers can distinguish."""

    def _all_decoys_page(self):
        return _page_with(
            _make_invisible_decoy(href="https://x.com/clk?id=1",
                                  text="Download 1080p"),
            _make_invisible_decoy(href="https://x.com/clk?id=2",
                                  text="Download HD"),
        )

    def test_all_dropped_still_calls_log_event(self, monkeypatch):
        from bulk_downloader.detect import find_best_download
        monkeypatch.setenv("BD_DOM_HONEYPOT_FILTER", "cheap")
        runner = _RecordingRunner()
        result = find_best_download(self._all_decoys_page(), runner=runner)
        assert result is None
        assert len(runner.events) == 1
        assert runner.events[0]["kind"] == "honeypot_filtered"

    def test_all_dropped_extra_all_dropped_true(self, monkeypatch):
        from bulk_downloader.detect import find_best_download
        monkeypatch.setenv("BD_DOM_HONEYPOT_FILTER", "cheap")
        runner = _RecordingRunner()
        find_best_download(self._all_decoys_page(), runner=runner)
        assert runner.events[0]["extra"]["all_dropped"] is True

    def test_all_dropped_message_marker(self, monkeypatch):
        """The "(all candidates dropped)" suffix that v3.66.28
        appended to its stderr line must still appear in the
        log_event message string (for operators grepping the log)."""
        from bulk_downloader.detect import find_best_download
        monkeypatch.setenv("BD_DOM_HONEYPOT_FILTER", "cheap")
        runner = _RecordingRunner()
        find_best_download(self._all_decoys_page(), runner=runner)
        assert "all candidates dropped" in runner.events[0]["message"]

    def test_all_dropped_runner_none_marker_preserved(
            self, monkeypatch, capsys):
        """Symmetric to the runner case — the all-dropped marker
        must still appear on stderr when no runner is wired."""
        from bulk_downloader.detect import find_best_download
        monkeypatch.setenv("BD_DOM_HONEYPOT_FILTER", "cheap")
        result = find_best_download(self._all_decoys_page())
        assert result is None
        assert "all candidates dropped" in capsys.readouterr().err


# --------------------------------------------------------------------------
# D. Fail-open: log_event raising falls back to stderr
# --------------------------------------------------------------------------

class TestLogEventFailureFallback:
    """A bug in log_event must NOT silence the operator signal —
    we fall back to the v3.66.28 stderr path. Mirrors the fail-open
    pattern from is_link_decoy_playwright (R-P5-3) and
    _apply_honeypot_filter (R-P5-2)."""

    def test_log_event_raises_falls_back_to_stderr(
            self, monkeypatch, capsys):
        from bulk_downloader.detect import find_best_download
        monkeypatch.setenv("BD_DOM_HONEYPOT_FILTER", "cheap")
        runner = _RecordingRunner(raises=True)
        page = _page_with(_make_visible_clean(), _make_invisible_decoy())
        result = find_best_download(page, runner=runner)
        # The filter still works — return value is unaffected by
        # the logging bug.
        assert result is not None
        err = capsys.readouterr().err
        assert "honeypot_filtered" in err
        # No event was recorded (the mock raises before append).
        assert len(runner.events) == 0

    def test_log_event_raises_all_dropped_still_stderr(
            self, monkeypatch, capsys):
        from bulk_downloader.detect import find_best_download
        monkeypatch.setenv("BD_DOM_HONEYPOT_FILTER", "cheap")
        runner = _RecordingRunner(raises=True)
        page = _page_with(
            _make_invisible_decoy(href="https://x.com/clk?id=1",
                                  text="Download 1080p"),
            _make_invisible_decoy(href="https://x.com/clk?id=2",
                                  text="Download HD"),
        )
        result = find_best_download(page, runner=runner)
        assert result is None
        err = capsys.readouterr().err
        assert "honeypot_filtered" in err
        assert "all candidates dropped" in err


# --------------------------------------------------------------------------
# E. No-emission cases — silence when there's nothing to report
# --------------------------------------------------------------------------

class TestSilentWhenNothingFiltered:
    """When the filter doesn't drop anything (because it's off, or
    because there are no decoys), no log_event call and no stderr
    line. Symmetric with v3.66.28 behavior."""

    def test_filter_off_no_log_event(self, monkeypatch, capsys):
        from bulk_downloader.detect import find_best_download
        monkeypatch.delenv("BD_DOM_HONEYPOT_FILTER", raising=False)
        runner = _RecordingRunner()
        page = _page_with(_make_visible_clean(), _make_invisible_decoy())
        find_best_download(page, runner=runner)
        assert runner.events == []
        assert "honeypot_filtered" not in capsys.readouterr().err

    def test_filter_on_but_no_decoys_no_log_event(
            self, monkeypatch, capsys):
        from bulk_downloader.detect import find_best_download
        monkeypatch.setenv("BD_DOM_HONEYPOT_FILTER", "cheap")
        runner = _RecordingRunner()
        # Two clean candidates, no decoys.
        page = _page_with(
            _make_visible_clean(),
            _MockLocator(
                visible=True,
                attrs={"href": "https://cdn.example.com/v2.mp4"},
                text="Download 720p"),
        )
        find_best_download(page, runner=runner)
        assert runner.events == []
        assert "honeypot_filtered" not in capsys.readouterr().err

    def test_filter_off_value_no_log_event(self, monkeypatch, capsys):
        """Explicit "off" — same as unset env."""
        from bulk_downloader.detect import find_best_download
        monkeypatch.setenv("BD_DOM_HONEYPOT_FILTER", "off")
        runner = _RecordingRunner()
        page = _page_with(_make_visible_clean(), _make_invisible_decoy())
        find_best_download(page, runner=runner)
        assert runner.events == []
        assert "honeypot_filtered" not in capsys.readouterr().err


# --------------------------------------------------------------------------
# F. Truncation contract — reasons capped at 10
# --------------------------------------------------------------------------

class TestReasonsTruncation:
    """v3.66.28 capped the reasons list at 10 for the stderr line.
    The log_event path must preserve the same cap so the message
    string doesn't bloat the bounded event-log deque (entries are
    truncated to 500 chars by log_event itself; long reason lists
    would lose the trailing fields)."""

    def test_reasons_capped_at_ten_in_log_event_extra(
            self, monkeypatch):
        from bulk_downloader.detect import find_best_download
        monkeypatch.setenv("BD_DOM_HONEYPOT_FILTER", "cheap")
        runner = _RecordingRunner()
        # 15 invisible decoys — should produce 15 dropped, but
        # reasons list capped at 10.
        decoys = [
            _make_invisible_decoy(href=f"https://x.com/clk?id={i}",
                                  text=f"Download {i}p")
            for i in range(15)
        ]
        page = _page_with(_make_visible_clean(), *decoys)
        find_best_download(page, runner=runner)
        ev = runner.events[0]
        assert ev["extra"]["count"] >= 10
        assert len(ev["extra"]["reasons"]) <= 10

    def test_count_reflects_full_total_not_truncated(
            self, monkeypatch):
        """count is the full count of dropped candidates, NOT the
        truncated reasons-list length. Operators rely on the count
        for accuracy even when the reasons list is sampled."""
        from bulk_downloader.detect import find_best_download
        monkeypatch.setenv("BD_DOM_HONEYPOT_FILTER", "cheap")
        runner = _RecordingRunner()
        decoys = [
            _make_invisible_decoy(href=f"https://x.com/clk?id={i}",
                                  text=f"Download {i}p")
            for i in range(15)
        ]
        page = _page_with(_make_visible_clean(), *decoys)
        find_best_download(page, runner=runner)
        ev = runner.events[0]
        # Should be 15 (each decoy hits the .mp4 selector once).
        assert ev["extra"]["count"] >= 15


# --------------------------------------------------------------------------
# G. Signature compatibility — every existing call shape still works
# --------------------------------------------------------------------------

class TestSignatureBackCompat:
    """Defensive: the v3.66.28 callers passed find_best_download
    with positional / keyword combinations. Confirm every shape
    still works after the new runner kwarg landed."""

    def test_positional_page_only(self, monkeypatch):
        from bulk_downloader.detect import find_best_download
        monkeypatch.delenv("BD_DOM_HONEYPOT_FILTER", raising=False)
        page = _page_with(_make_visible_clean())
        result = find_best_download(page)
        assert result is not None

    def test_positional_page_custom(self, monkeypatch):
        from bulk_downloader.detect import find_best_download
        monkeypatch.delenv("BD_DOM_HONEYPOT_FILTER", raising=False)
        page = _page_with(_make_visible_clean())
        result = find_best_download(page, "")
        assert result is not None

    def test_keyword_learned(self, monkeypatch):
        """Mirrors auto_detect.py:271 — custom="", learned=None."""
        from bulk_downloader.detect import find_best_download
        monkeypatch.delenv("BD_DOM_HONEYPOT_FILTER", raising=False)
        page = _page_with(_make_visible_clean())
        result = find_best_download(page, custom="", learned=None)
        assert result is not None

    def test_keyword_runner_kwarg(self, monkeypatch):
        from bulk_downloader.detect import find_best_download
        monkeypatch.delenv("BD_DOM_HONEYPOT_FILTER", raising=False)
        runner = _RecordingRunner()
        page = _page_with(_make_visible_clean())
        result = find_best_download(page, runner=runner)
        assert result is not None

    def test_all_kwargs_together(self, monkeypatch):
        from bulk_downloader.detect import find_best_download
        monkeypatch.delenv("BD_DOM_HONEYPOT_FILTER", raising=False)
        runner = _RecordingRunner()
        page = _page_with(_make_visible_clean())
        result = find_best_download(
            page, custom="", learned=None, runner=runner)
        assert result is not None


# --------------------------------------------------------------------------
# H. Module-load sanity
# --------------------------------------------------------------------------

class TestModuleLoad:
    """Sanity that the kwarg is actually in the signature
    (catches accidental rename / drop in a future refactor)."""

    def test_runner_kwarg_in_signature(self):
        import inspect
        from bulk_downloader.detect import find_best_download
        sig = inspect.signature(find_best_download)
        assert "runner" in sig.parameters
        # Default must be None — every existing caller without the
        # kwarg must continue to work.
        assert sig.parameters["runner"].default is None

    def test_runner_kwarg_position(self):
        """runner should be the LAST parameter — adding it earlier
        would break any positional callers that exist (and might
        exist in third-party scripts even if not in tree)."""
        import inspect
        from bulk_downloader.detect import find_best_download
        params = list(inspect.signature(
            find_best_download).parameters.keys())
        assert params[-1] == "runner"

"""Replay the attributed leaking test and audit every cleanup exit path."""
import sys
import types
from pathlib import Path

import pytest

BD_GATE_SCOPE = "module"
SOURCE = Path(__file__).with_name(
    "test_row723_login_flow_channel_fallback_is_filed_under_its_site.py")
NODE = "test_row723_a_run_launch_surfaces_the_notes_a_keeper_login_left_behind"


@pytest.mark.parametrize("failure", ["none", "assertion", "close", "assertion-and-close"])
def test_attributed_node_stops_its_only_playwright_handle(monkeypatch, failure):
    monkeypatch.setattr(sys, "path", list(sys.path))
    module = types.ModuleType("row816_subject")
    module.__file__ = str(SOURCE)
    source = SOURCE.read_text()
    assert source.count("def " + NODE + "(") == 1
    exec(compile(source, str(SOURCE), "exec"), module.__dict__)
    handles, events = [], []

    class TrackedPW:
        def __init__(self):
            self.stopped = False
            handles.append(self)

        def stop(self):
            events.append("stop")
            self.stopped = True

    def namespace(**kwargs):
        if "close" in kwargs:
            def close():
                events.append("close")
                if "close" in failure:
                    raise RuntimeError("row816 injected close failure")
            kwargs["close"] = close
        return types.SimpleNamespace(**kwargs)

    monkeypatch.setattr(module, "_FakePW", TrackedPW)
    monkeypatch.setattr(module, "SimpleNamespace", namespace)
    # The old test mocked the wrong launcher.  This outer guard keeps its
    # RED replay offline while recording the actual persistent handle leak.
    monkeypatch.setattr(module.cloak, "open_persistent_context",
                        lambda **kw: (namespace(close=lambda: None), TrackedPW(), "fixture"))
    if "assertion" in failure:
        monkeypatch.setattr(module, "_degradation_events", lambda runner: [])
    error = None
    try:
        getattr(module, NODE)(monkeypatch)
    except Exception as exc:
        error = exc
    finally:
        module.cloak.reset_cache_for_tests()
    assert len(handles) == 1, "the attributed node must acquire exactly one handle"
    assert handles[0].stopped, f"Playwright handle leaked on {failure}; events={events}"
    assert events == ["close", "stop"], "close and stop each run exactly once, in order"
    if "close" in failure:
        assert isinstance(error, RuntimeError) and str(error) == "row816 injected close failure"
    elif "assertion" in failure:
        assert isinstance(error, AssertionError) and "pending login degradation" in str(error)
    else:
        assert error is None

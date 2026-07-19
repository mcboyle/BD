"""v3.66.268 — headed capture browser fills the Xvfb framebuffer.

Bug: the live-capture noVNC pane showed a small, default-size Chrome window on a
large black desktop, because tools/capture_session.py launched the headed
browser with no window sizing. resize=scale faithfully scaled the whole desktop
into the pane, so the browser looked tiny. Fix: _headed_browser_args supplies
``--start-maximized`` (the window fills the framebuffer) plus ``no_viewport``
(set on the context / persistent launch so the page tracks the maximized
window), so the noVNC pane shows a full-size browser.
"""
import tools.capture_session as cs


def test_headed_browser_args_maximizes_and_fills():
    browser_args, ctx_kwargs = cs._headed_browser_args([])
    assert "--start-maximized" in browser_args, browser_args
    assert ctx_kwargs.get("no_viewport") is True, ctx_kwargs


def test_headed_browser_args_preserves_extra_args():
    browser_args, ctx_kwargs = cs._headed_browser_args(["--password-store=basic"])
    assert "--start-maximized" in browser_args
    assert "--password-store=basic" in browser_args
    assert ctx_kwargs.get("no_viewport") is True


def test_headed_browser_args_does_not_mutate_input():
    extra = ["--foo"]
    cs._headed_browser_args(extra)
    assert extra == ["--foo"], "input list must not be mutated"

"""Row 786: a click-only grant must never be spent a second time on fallback."""
from __future__ import annotations

import logging
import threading
from contextlib import contextmanager
from pathlib import Path

from bulk_downloader import runner_transport as transport
from bulk_downloader.constants import _HTTPDownloadFailed


BD_GATE_SCOPE = "module"


class _Download:
    url = "https://cdn.example.invalid/video.mp4?grant=single-use-token"
    suggested_filename = "scene.mp4"

    def cancel(self):
        return None


class _SingleUseLocator:
    """Clicking once issues the grant; a second click is the real refusal."""

    def __init__(self):
        self.issues = 0

    def get_attribute(self, name):
        assert name == "href"
        return None

    def click(self):
        self.issues += 1
        if self.issues > 1:
            raise AssertionError("single-use token issued twice")


class _StaticLocator(_SingleUseLocator):
    def get_attribute(self, name):
        assert name == "href"
        return "/download/scene"

    def click(self):
        self.issues += 1


class _Page:
    url = "https://members.example.invalid/scenes/7"

    @contextmanager
    def expect_download(self, *, timeout):
        assert timeout == 60000
        yield type("_Event", (), {"value": _Download()})()

    def title(self):
        return "Scene 7"


class _Runner(transport.TransportMixin):
    def __init__(self):
        self.site_id = "row786"
        self.config = {"name": "row786", "use_http_dl": True,
                       "verify_hash": False, "verify_integrity": False}
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self.jobs = {}
        self.log = logging.getLogger("row786")
        self.failures = []

    def _http_download(self, *_args):
        raise _HTTPDownloadFailed("grant was already spent by HTTP")

    def _probe_for_higher_tier(self, url, **_kwargs):
        return url

    def _build_mirror_urls(self, _url):
        return []

    def _update_job(self, *_args, **_kwargs):
        return None

    def _handle_failure(self, url, message):
        self.failures.append((url, message))

    def _size_on_disk_after_tagging(self, _path, downloaded_size):
        return downloaded_size


def test_http_failure_does_not_reissue_a_click_only_single_use_grant(tmp_path, monkeypatch):
    """The fixture forbids a second issue instead of merely counting one."""
    locator = _SingleUseLocator()
    page = _Page()
    runner = _Runner()
    assert locator.get_attribute("href") is None, "fixture must be click-only"
    monkeypatch.setattr(transport, "_HTTPX_AVAILABLE", True)
    monkeypatch.setattr(transport, "db_skip_identity", lambda *_a: ("different", ""))
    monkeypatch.setattr(transport.staging_claim, "reserve",
                        lambda path, _identity: (path, path.with_suffix(".part")))
    monkeypatch.setattr(transport.staging_claim, "release", lambda *_a: None)

    runner._do_download(
        page, object(), page.url,
        {"locator": locator, "score": 1080, "size": 0, "text": "Download"},
        Path(tmp_path), "1080p")

    assert locator.issues == 1, "the fixture refused a second issue of the same token"
    assert len(runner.failures) == 1
    assert "single-use" in runner.failures[0][1]


def test_static_url_keeps_browser_fallback_after_http_failure(tmp_path, monkeypatch):
    """Negative control: an ordinary static trigger still receives its retry."""
    locator = _StaticLocator()
    page = _Page()
    runner = _Runner()
    runner._pw_save = lambda _dl, _path: (1, 1)
    monkeypatch.setattr(transport, "_HTTPX_AVAILABLE", True)
    monkeypatch.setattr(transport, "db_skip_identity", lambda *_a: ("different", ""))
    monkeypatch.setattr(transport, "db_log", lambda *_a, **_kw: None)
    monkeypatch.setattr(transport.staging_claim, "reserve",
                        lambda path, _identity: (path, path.with_suffix(".part")))
    monkeypatch.setattr(transport.staging_claim, "release", lambda *_a: None)

    runner._do_download(
        page, object(), page.url,
        {"locator": locator, "score": 1080, "size": 0, "text": "Download"},
        Path(tmp_path), "1080p")

    assert locator.issues == 2
    assert runner.failures == []


# ── the dispatch that reaches the guard ─────────────────────────────────
#
# Everything above drives ``_do_download`` DIRECTLY, which is the right way to
# test the failure arm but leaves the arm defended only where it is defined.
# The guard this row adds is worth nothing if ``_process_one`` stops handing a
# download to ``_do_download`` at all: the click-only grant would then be spent
# by some other path and this file would stay green. The prep gate's seam
# census measured exactly that -- deleting either ``self._do_download(...)``
# call in ``_process_one`` escaped every test this patch ships.
#
# So the population below is closed STRUCTURALLY, never by line number. Row
# 786's first two rounds were refuted for re-pinning nothing when they moved
# six line-pinned constants in runner_transport.py, and a gate that pins a
# CALL by line would be the same mistake one file over. The parse asks what the
# dispatch DOES, so an insertion anywhere above it changes nothing here.


def _download_calls_in(source: str):
    """Every ``self._do_download(...)`` CALL in ``source``.

    Parsed, not grepped, so a mention in a docstring or a comment is not
    counted as a dispatch. ``test_the_probe_can_tell_a_call_from_a_mention``
    below is the control that proves this distinction actually holds.
    """
    import ast
    import textwrap

    tree = ast.parse(textwrap.dedent(source))
    return [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "_do_download"
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "self"
    ]


def _process_one_source() -> str:
    import inspect

    from bulk_downloader.runner import SiteRunner

    return inspect.getsource(SiteRunner._process_one)


def _process_one_download_calls():
    return _download_calls_in(_process_one_source())


def _kwarg(call, name):
    for kw in call.keywords:
        if kw.arg == name:
            return kw.value
    return None


def test_process_one_still_dispatches_to_the_guarded_download():
    """Both dispatch arms must still reach ``_do_download``.

    The denominator is the call population of the method itself, so this fails
    if either arm is deleted, and it fails LOUDLY rather than by an absent
    assertion: a zero here would otherwise look exactly like a passing gate.
    """
    calls = _process_one_download_calls()
    assert len(calls) == 2, (
        "SiteRunner._process_one must dispatch to _do_download on exactly two "
        "arms -- the probe arm and the download-dir arm. Found "
        f"{len(calls)}. The single-use-grant guard added by row 786 lives "
        "INSIDE _do_download, so an arm that no longer calls it spends the "
        "click-only grant somewhere this file cannot see."
    )


def test_the_probe_arm_reaches_the_guard_without_a_download_dir():
    """The probe arm passes no dl_dir and asks for probe=True."""
    calls = _process_one_download_calls()
    probe = [c for c in calls if _kwarg(c, "probe") is not None]
    assert len(probe) == 1, (
        "exactly one _do_download dispatch may be the probe arm; found "
        f"{len(probe)} carrying a probe= keyword"
    )
    call = probe[0]
    import ast
    value = _kwarg(call, "probe")
    assert isinstance(value, ast.Constant) and value.value is True, (
        "the probe arm must dispatch with probe=True, not "
        f"{ast.dump(value)} -- a probe that is not flagged writes a file"
    )
    assert len(call.args) == 6, (
        "the probe arm's positional shape changed; dl_dir is the 5th argument "
        f"and this call takes {len(call.args)}"
    )
    dl_dir = call.args[4]
    assert isinstance(dl_dir, ast.Constant) and dl_dir.value is None, (
        "the probe arm must pass dl_dir=None -- it samples first bytes and "
        f"writes nothing; got {ast.dump(dl_dir)}"
    )


def test_the_download_arm_reaches_the_guard_with_a_resolved_dir():
    """The ordinary arm passes a resolved directory and does not probe."""
    calls = _process_one_download_calls()
    full = [c for c in calls if _kwarg(c, "probe") is None]
    assert len(full) == 1, (
        "exactly one _do_download dispatch may be the ordinary download arm; "
        f"found {len(full)} without a probe= keyword"
    )
    call = full[0]
    import ast
    assert len(call.args) == 6, (
        "the download arm's positional shape changed; dl_dir is the 5th "
        f"argument and this call takes {len(call.args)}"
    )
    dl_dir = call.args[4]
    assert (
        isinstance(dl_dir, ast.Call)
        and isinstance(dl_dir.func, ast.Name)
        and dl_dir.func.id == "Path"
    ), (
        "the download arm must hand _do_download a resolved Path(dl_dir); got "
        f"{ast.dump(dl_dir)}. A bare string here is the shape that produced "
        "zero-byte 'done' rows before the download_dir default was resolved."
    )


def test_the_probe_can_tell_a_call_from_a_mention():
    """Positive AND negative control for the parser this file depends on.

    A gate that counts text would report the same number for a dispatch and for
    a docstring naming it, and every assertion above would then be measuring
    prose. Feed the same helper a source that holds one real call and three
    mentions, and it must return exactly the one.
    """
    sample = (
        '''
def _fake_process_one(self):
    """Docstring mentioning self._do_download twice: self._do_download."""
    # a comment naming self._do_download(...)
    self._do_download(page, ctx, url, best, None, lbl, probe=True)
'''
    )
    assert sample.count("_do_download") == 4, "the control fixture lost its mentions"
    found = _download_calls_in(sample)
    assert len(found) == 1, (
        "the parser must see exactly the one CALL among four mentions; it "
        f"returned {len(found)}. Until this holds, the dispatch assertions "
        "above are counting text, not behaviour."
    )
    assert _download_calls_in("def _fake(self):\n    pass\n") == [], (
        "the parser reports a dispatch in a body that has none"
    )

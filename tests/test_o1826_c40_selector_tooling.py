"""O1826 C40 -- selector tooling: real playground status, honest version saves,
post-click checks that wait for their timeout.

M156: _fetch_playwright reported ok/200 for every page, ignoring the navigation
response, so a 404/500 looked like a success.
M157: record_template_version returned a version id when _save failed, and its
load-append-save had no lock, so two concurrent saves lost one version.
M152: _text_present checked the page once despite timeout_ms, and the
navigation fallback compared page.url once right after the click.
r2 (correctness REFUTE F1-F3): the url poll must pump the page (sync Playwright
refreshes page.url only then); the version lock must hold across processes; an
unusable flock must fall back to the in-process lock, not drop the version.
"""
import errno
import os
import subprocess
import sys
import threading
import time
import types
from pathlib import Path

import pytest

from bulk_downloader import cloak
from bulk_downloader import selector_chains as sc
from bulk_downloader import selector_playground as sp
from bulk_downloader import selector_versions as sv

BD_GATE_SCOPE = "module"


# --- M156: playground reports the real navigation status ---------------------

class _Resp:
    def __init__(self, status):
        self.status = status
        self.ok = 200 <= status < 400


class _PwPage:
    def __init__(self, status):
        self._status = status
        self.url = "https://example.com/x"

    def route(self, pattern, handler):
        pass

    def set_extra_http_headers(self, headers):
        pass

    def goto(self, url, timeout=None):
        return _Resp(self._status)

    def wait_for_load_state(self, state, timeout=None):
        pass

    def content(self):
        return "<html>%d</html>" % self._status


class _PwBrowser:
    def __init__(self, status):
        self._status = status

    def new_context(self):
        return self

    def new_page(self):
        return _PwPage(self._status)

    def add_cookies(self, cookies):
        pass

    def close(self):
        pass


def _fetch_with_status(monkeypatch, status):
    monkeypatch.setattr(
        cloak, "launch_browser",
        lambda **kw: (_PwBrowser(status), None, "fake"))
    return sp._fetch_playwright("https://example.com/x", timeout=5)


def test_playwright_404_is_not_success(monkeypatch):
    out = _fetch_with_status(monkeypatch, 404)
    assert out.get("status") == 404 and out.get("ok") is False, (
        f"C40-PLAYWRIGHT-STATUS: a 404 navigation reported {out}")


def test_playwright_500_is_not_success(monkeypatch):
    out = _fetch_with_status(monkeypatch, 500)
    assert out.get("status") == 500 and out.get("ok") is False, (
        f"C40-PLAYWRIGHT-STATUS: a 500 navigation reported {out}")


def test_playwright_200_unchanged(monkeypatch):
    out = _fetch_with_status(monkeypatch, 200)
    assert out["ok"] is True and out["status"] == 200
    assert out["html"] == "<html>200</html>"


# --- M157: version save failure and lost update ------------------------------

def _tpl(sel):
    return {"id": "t1", "learned": {"download": [sel]}}


def test_failed_save_returns_none(tmp_path, monkeypatch):
    monkeypatch.setattr(sv, "_save", lambda doc, base_dir=None: False)
    vid = sv.record_template_version(_tpl("#a"), base_dir=tmp_path)
    assert vid is None, (
        f"C40-SAVE-FAIL: _save returned False but a version id came back: {vid}")


def test_successful_save_returns_id(tmp_path):
    vid = sv.record_template_version(_tpl("#a"), base_dir=tmp_path)
    assert vid and [v["version"] for v in sv.list_versions("t1", base_dir=tmp_path)] == [vid]


def test_concurrent_saves_keep_both_versions(tmp_path, monkeypatch):
    # A loads, then waits for B to load; without a lock B loads the same
    # snapshot and one save overwrites the other. With the lock B cannot
    # load until A has saved, so A's wait times out and both survive.
    real_load = sv._load
    a_loaded = threading.Event()
    b_loaded = threading.Event()

    def load(base_dir=None):
        doc = real_load(base_dir)
        if threading.current_thread().name == "A":
            a_loaded.set()
            b_loaded.wait(timeout=1.0)
        else:
            b_loaded.set()
        return doc

    monkeypatch.setattr(sv, "_load", load)
    out = {}

    def run(name, sel):
        out[name] = sv.record_template_version(_tpl(sel), base_dir=tmp_path)

    ta = threading.Thread(target=run, args=("A", "#a"), name="A")
    tb = threading.Thread(target=run, args=("B", "#b"), name="B")
    ta.start()
    assert a_loaded.wait(timeout=5)
    tb.start()
    ta.join(5)
    tb.join(5)
    kept = {v["version"] for v in sv.list_versions("t1", base_dir=tmp_path)}
    assert out["A"] and out["B"]
    assert {out["A"], out["B"]} <= kept, (
        f"C40-LOST-UPDATE: returned {out}, history kept {sorted(kept)}")


# --- M152: post-click checks wait for their timeout --------------------------

class _Loc:
    first = property(lambda self: self)

    def count(self):
        return 1

    def click(self, timeout=None):
        pass


class _DelayedPage:
    """Text and URL change `delay` seconds after construction."""

    def __init__(self, delay, *, text="Welcome", url_after="http://t/b"):
        self._t0 = time.monotonic()
        self._delay = delay
        self._text = text
        self._url_after = url_after

    def _late(self):
        return time.monotonic() - self._t0 >= self._delay

    def locator(self, sel):
        return _Loc()

    def content(self):
        return "<p>%s</p>" % (self._text if self._late() else "loading")

    @property
    def url(self):
        return self._url_after if self._late() else "http://t/a"


def test_text_appearing_at_500ms_within_2s_timeout():
    page = _DelayedPage(0.5)
    step = sc.parse_step({"selector": "#s", "timeout_ms": 2000,
                          "post_condition": "text_appeared:Welcome"})
    outcome, detail = sc.try_step(step, page, "click")
    assert outcome == "ok", f"C40-TEXT-ONCE: text at 500 ms missed: {detail}"


def test_nav_at_500ms_within_2s_timeout():
    page = _DelayedPage(0.5)
    step = sc.parse_step({"selector": "#s", "timeout_ms": 2000,
                          "post_condition": "navigation_occurred"})
    outcome, detail = sc.try_step(step, page, "click")
    assert outcome == "ok", f"C40-NAV-ONCE: url change at 500 ms missed: {detail}"


def test_immediate_text_unchanged():
    page = _DelayedPage(0.0)
    step = sc.parse_step({"selector": "#s", "timeout_ms": 2000,
                          "post_condition": "text_appeared:Welcome"})
    t0 = time.monotonic()
    assert sc.try_step(step, page, "click")[0] == "ok"
    assert time.monotonic() - t0 < 1.0


def test_text_never_appearing_still_fails_after_timeout():
    page = _DelayedPage(60.0)
    step = sc.parse_step({"selector": "#s", "timeout_ms": 300,
                          "post_condition": "text_appeared:Welcome",
                          "advance_on": ["no_text"]})
    outcome, detail = sc.try_step(step, page, "click")
    assert outcome == "advance" and "no_text" in detail


# --- r2 F1: the url fallback must pump the page, not time.sleep ---------------

class _PumpedPage(_DelayedPage):
    """Sync-Playwright shape: .url is a cache refreshed only when a page call
    pumps events; time.sleep never refreshes it."""

    def __init__(self, delay):
        super().__init__(delay)
        self._cached_url = "http://t/a"

    @property
    def url(self):
        return self._cached_url

    def wait_for_timeout(self, ms):
        time.sleep(ms / 1000.0)
        if self._late():
            self._cached_url = self._url_after


def test_nav_seen_only_when_page_is_pumped():
    page = _PumpedPage(0.5)
    step = sc.parse_step({"selector": "#s", "timeout_ms": 3000,
                          "post_condition": "navigation_occurred"})
    t0 = time.monotonic()
    outcome, detail = sc.try_step(step, page, "click")
    dt = time.monotonic() - t0
    assert outcome == "ok", (
        f"C40-NAV-PUMP: a navigation at 500 ms was never seen ({detail}, {dt:.2f}s)")
    assert dt < 2.0, f"C40-NAV-PUMP: navigation seen only after {dt:.2f}s"


def test_pumped_page_without_nav_still_fails_after_timeout():
    page = _PumpedPage(60.0)
    step = sc.parse_step({"selector": "#s", "timeout_ms": 300,
                          "post_condition": "navigation_occurred",
                          "advance_on": ["no_navigation"]})
    t0 = time.monotonic()
    outcome, detail = sc.try_step(step, page, "click")
    assert outcome == "advance" and "no_navigation" in detail
    assert time.monotonic() - t0 < 2.0


def test_real_chromium_late_navigation_seen(tmp_path):
    sync_playwright = pytest.importorskip("playwright.sync_api").sync_playwright
    (tmp_path / "b.html").write_text("<html><body>B page</body></html>")
    (tmp_path / "a.html").write_text(
        "<html><body><button id='nav' onclick=\"setTimeout(()=>"
        "{location.href='b.html'},500)\">go</button></body></html>")
    step = sc.parse_step({"selector": "#nav", "timeout_ms": 3000,
                          "post_condition": "navigation_occurred",
                          "advance_on": ["no_navigation"]})
    with sync_playwright() as p:
        br = p.chromium.launch()
        try:
            pg = br.new_page()
            pg.goto((tmp_path / "a.html").as_uri())
            t0 = time.monotonic()
            outcome, detail = sc.try_step(step, pg, "click")
            dt = time.monotonic() - t0
        finally:
            br.close()
    assert outcome == "ok" and dt < 2.0, (
        f"C40-NAV-CHROMIUM: navigation at 500 ms gave {outcome} {detail!r} "
        f"after {dt:.2f}s")


# --- r2 F2: two processes lose no update -------------------------------------

_MP_PROCS, _MP_SAVES = 4, 6  # 24 <= _MAX_PER_TEMPLATE, so nothing is trimmed

_MP_CHILD = r"""
import os, sys, time
from bulk_downloader import selector_versions as sv
base, go, pid, n = sys.argv[1], sys.argv[2], sys.argv[3], int(sys.argv[4])
real_load = sv._load
def slow_load(base_dir=None):
    doc = real_load(base_dir)
    time.sleep(0.03)  # widen load->save so an unlocked peer interleaves
    return doc
sv._load = slow_load
deadline = time.monotonic() + 60
while not os.path.exists(go) and time.monotonic() < deadline:
    time.sleep(0.01)
for i in range(n):
    vid = sv.record_template_version(
        {"id": "t1", "learned": {"download": ["#p%s-%d" % (pid, i)]}},
        base_dir=base)
    print(vid)
"""


def test_processes_lose_no_update(tmp_path):
    root = str(Path(__file__).resolve().parents[1])
    env = dict(os.environ, LC_ALL="C", PYTHONDONTWRITEBYTECODE="1",
               PYTHONPATH=root)
    go = tmp_path / "go"
    procs = [subprocess.Popen(
        [sys.executable, "-c", _MP_CHILD, str(tmp_path), str(go), str(p),
         str(_MP_SAVES)], cwd=root, env=env, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, text=True) for p in range(_MP_PROCS)]
    time.sleep(0.5)
    go.write_text("go")
    returned = set()
    for pr in procs:
        out, err = pr.communicate(timeout=120)
        assert pr.returncode == 0, err
        returned |= {v for v in out.split() if v != "None"}
    kept = {v["version"] for v in sv.list_versions("t1", base_dir=tmp_path)}
    want = _MP_PROCS * _MP_SAVES
    assert len(returned) == want and returned <= kept, (
        f"C40-MP-LOST-UPDATE: {want} saves, {len(returned)} returned an id, "
        f"history kept {len(kept)}; lost {len(returned - kept)}")


# --- r2 F3: flock unavailable must not drop the version ----------------------

def test_flock_enolck_falls_back_and_records(tmp_path, monkeypatch):
    def no_flock(fd, op):
        raise OSError(errno.ENOLCK, "No locks available")

    monkeypatch.setattr(sv, "fcntl", types.SimpleNamespace(
        LOCK_EX=2, LOCK_UN=8, flock=no_flock))
    vid = sv.record_template_version(_tpl("#a"), base_dir=tmp_path)
    kept = [v["version"] for v in sv.list_versions("t1", base_dir=tmp_path)]
    assert vid and kept == [vid], (
        f"C40-FLOCK-FALLBACK: flock ENOLCK gave {vid!r}, history {kept}")

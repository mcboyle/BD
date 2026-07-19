"""v3.66.285 cloak parity: every operator-facing path that fetches/renders a
TARGET SITE must launch through the canonical backend (CloakBrowser, else
vanilla Playwright as fail-open) — never a raw `sync_playwright().chromium
.launch()`. The TEST surfaces (template sandbox, selector playground), site
auto-detect, playlist expansion, and macro replay previously launched vanilla
Playwright, so they rendered un-cloaked while real captures rendered cloaked —
a stealth/parity gap (a fingerprinting site behaves differently in the test
than in production).

This pins the canonical helper (cloak.cloaked_page) and adds a SOURCE-AUDIT
gate so a new raw chromium.launch can't silently bypass cloak again.

Zero-arg functions; module globals restored in try/finally. Repo root via __file__.
"""
import re
from pathlib import Path

from bulk_downloader import cloak


_ROOT = Path(__file__).resolve().parent.parent
_PKG = _ROOT / "bulk_downloader"


class _FakePage:
    def __init__(self): self.closed = False


class _FakeContext:
    def __init__(self, kwargs): self.kwargs = kwargs; self.closed = False; self._page = _FakePage()
    def new_page(self): return self._page
    def close(self): self.closed = True


class _FakeBrowser:
    def __init__(self): self.contexts = []; self.closed = False
    def new_context(self, **kwargs):
        c = _FakeContext(kwargs); self.contexts.append(c); return c
    def close(self): self.closed = True


class _FakePW:
    def __init__(self): self.stopped = False
    def stop(self): self.stopped = True


def test_cloaked_page_helper_exists():
    assert hasattr(cloak, "cloaked_page")


def test_cloaked_page_cloak_path_no_ua_and_teardown():
    fb = _FakeBrowser()
    saved = cloak.launch_browser
    cloak.launch_browser = lambda **kw: (fb, None, cloak.CLOAKBROWSER)
    try:
        with cloak.cloaked_page(headless=True, user_agent="VanillaUA/1.0") as page:
            assert isinstance(page, _FakePage)
    finally:
        cloak.launch_browser = saved
    # CloakBrowser supplies its OWN fingerprint -> the vanilla UA must NOT be forced
    assert fb.contexts[0].kwargs.get("user_agent") is None
    # teardown: context + browser closed (pw is None on the cloak path)
    assert fb.contexts[0].closed is True
    assert fb.closed is True


def test_cloaked_page_playwright_fallback_applies_ua_and_stops_pw():
    fb = _FakeBrowser(); fpw = _FakePW()
    saved = cloak.launch_browser
    cloak.launch_browser = lambda **kw: (fb, fpw, cloak.PLAYWRIGHT)
    try:
        with cloak.cloaked_page(user_agent="VanillaUA/1.0") as page:
            assert isinstance(page, _FakePage)
    finally:
        cloak.launch_browser = saved
    # on the Playwright fallback the requested UA IS applied ...
    assert fb.contexts[0].kwargs.get("user_agent") == "VanillaUA/1.0"
    # ... and the owned sync_playwright is stopped (no leaked driver subprocess)
    assert fpw.stopped is True
    assert fb.closed is True


def test_cloaked_page_teardown_runs_even_when_body_raises():
    fb = _FakeBrowser(); fpw = _FakePW()
    saved = cloak.launch_browser
    cloak.launch_browser = lambda **kw: (fb, fpw, cloak.PLAYWRIGHT)
    raised = False
    try:
        try:
            with cloak.cloaked_page() as page:
                raise RuntimeError("boom")
        except RuntimeError:
            raised = True
    finally:
        cloak.launch_browser = saved
    assert raised
    assert fb.closed is True and fpw.stopped is True


# ─── SOURCE AUDIT GATE ───────────────────────────────────────────────────────
# No module in bulk_downloader/ may call a raw chromium.launch() EXCEPT cloak.py
# (which owns the canonical backend + its Playwright fallback). healthcheck.py
# uses sync_playwright() only to read executable_path (an install probe) and
# never .launch()es, so it is not flagged by this scan.
_LAUNCH_RE = re.compile(r"chromium\s*\.\s*launch\s*\(")
_ALLOWED = {"cloak.py"}


def test_no_module_calls_raw_chromium_launch_outside_cloak():
    offenders = []
    for py in sorted(_PKG.glob("*.py")):
        if py.name in _ALLOWED:
            continue
        text = py.read_text(encoding="utf-8", errors="ignore")
        for i, line in enumerate(text.splitlines(), 1):
            if line.lstrip().startswith("#"):
                continue
            if _LAUNCH_RE.search(line):
                offenders.append(f"{py.name}:{i}: {line.strip()[:80]}")
    assert not offenders, (
        "raw chromium.launch() must go through cloak.cloaked_page / "
        "cloak.launch_browser:\n  " + "\n  ".join(offenders))

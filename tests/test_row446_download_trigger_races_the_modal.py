"""Row 446 -- the download trigger races the modal it opened.

MEASURED CONTEXT.  ``_process_one``'s download-trigger path synchronised on a
fixed ``time.sleep(1.5)`` between the trigger click and a SINGLE DOM read: the
learned path clicked and slept, the Scrapling-recovered path clicked and slept,
and ``detect.find_best_download`` then scraped the page exactly once with no
settle condition of any kind.

The modal or tier list the click opens is appended by the site's handler at
Chromium's next rendering/XHR opportunity, which is NOT ordered against the CDP
round-trip that reads the DOM back.  On a contended host or a slow AJAX menu the
scrape therefore observes the PRE-CLICK page.  This is the identical
scroll-dispatch schedule race rows 385/397 named, which ``scene_crawler``
retired with a rendered-frame barrier plus a stability poll and which this path
never received.

IT STRIKES CAPTURE CORRECTNESS TWICE.  ``find_best_download`` scores whatever
anchors happen to exist, and on a page carrying direct media links OUTSIDE the
modal it selects one of those -- the 2026-08-29 A7 incident page exposed 159
media links, 6 of them the requested work, and 5,102,802,950 bytes of the wrong
scene were filed under the right title.  And every schedule-induced miss feeds
``_bump_learned_stat("download_misses")`` and ``_maybe_demote_selectors``, so
the race also demotes healthy learned selectors and corrupts per-site learning.

THE SEAM IS DISPATCH ORDER, NOT WALL TIME.  ``_FakePage`` below models Chromium
exactly as ``scene_crawler`` does: the click's handler is queued and runs at the
page's next RENDERING OPPORTUNITY, and the only construct that proves a frame
rendered is a frame callback.  A ``time.sleep`` in the runner's process proves
nothing about the renderer, which is precisely why the contended host loses the
race.  Nothing here depends on how much CPU the test gets.

RED on the defective parent, all four measured:
  * the delayed-modal case selects a DECOY (a related-scene .mp4), not the tier;
  * that same pass bumps ``download_misses`` once and demotes the learned
    selectors once, corrupting learning state on a page that was merely early;
  * the AJAX case, whose menu rows arrive across successive reads, is likewise
    scored from the stale page;
  * a page whose DOM never stops changing is scored anyway instead of being
    reported UNKNOWN and refused (CLAUDE.md A7).

NEGATIVE CONTROLS, which must pass on the parent AND after the fix:
  * a trigger that settles PROMPTLY still selects the tier, and pays less
    latency than the 1.5s it replaces (asserted in requested milliseconds and
    in wall clock, not by eye);
  * a trigger that genuinely opens NOTHING still fails with its own distinctive
    "No download button found" diagnostic -- it must not be laundered into
    either success or the settle refusal;
  * the fixture is proven to support the REAL ``detect.find_best_download`` on
    both its stale and its settled state, so no verdict rests on a page the
    production scorer could not read.
"""
from __future__ import annotations

import re
import threading
import time

import pytest


BD_GATE_SCOPE = "module"

_PAGE_URL = "https://example.test/works/row446-scene"
_TIER_HREF = "https://cdn.example.test/works/row446-scene/row446-scene-1080p.mp4"
_DECOY_COUNT = 6


# ── a DOM the real scorer can read ──────────────────────────────────────────

_ATTR_RE = re.compile(
    r"\[\s*([A-Za-z_:][-\w:.]*)\s*(?:([*^$~|]?=)\s*(['\"])(.*?)\3\s*(i)?\s*)?\]")


class _El:
    """One DOM node.  ``evaluate`` refuses, which is the documented stub path.

    ``detect``'s element helpers all fail OPEN on a raising ``evaluate``
    (``_candidate_has_own_affordance`` -> True, ``_is_wrapper_not_control`` ->
    False, ``_has_navigation_ancestor`` -> False), so a stub node is ranked on
    its text and attributes exactly as the docstrings in ``detect.py`` promise.
    """

    def __init__(self, page, tag, attrs, text):
        self._page = page
        self.tag = tag
        self.attrs = dict(attrs)
        self.text = text

    # -- Playwright element surface used by detect.py --------------------
    def get_attribute(self, name):
        return self.attrs.get(name)

    def inner_text(self):
        return self.text

    def is_visible(self):
        return True

    def evaluate(self, *_a, **_k):
        raise RuntimeError("the fake DOM cannot run page JS on an element")

    def locator(self, *_a, **_k):
        raise RuntimeError("the fake DOM has no live ancestry")

    # -- trigger surface used by runner.py -------------------------------
    def wait_for(self, **_k):
        return None

    def hover(self, **_k):
        return None

    def click(self, **_k):
        self._page.note_click(self)

    def __repr__(self):
        return "<El %s class=%r text=%r>" % (
            self.tag, self.attrs.get("class"), self.text[:40])


def _matches(el, selector):
    sel = selector.strip()
    if not sel or sel.startswith(":"):
        # :text-matches() and friends: the fake DOM cannot evaluate them, and
        # reporting NO match is the honest answer (detect.py treats an empty
        # ancestor-walk group as "nothing found", never as an error).
        return False
    if "," in sel:
        return any(_matches(el, part) for part in sel.split(","))
    rest = sel
    tag_match = re.match(r"^([A-Za-z][-\w]*)", rest)
    if tag_match:
        if el.tag != tag_match.group(1).lower():
            return False
        rest = rest[tag_match.end():]
    while rest:
        if rest.startswith("."):
            cls = re.match(r"^\.([-\w]+)", rest)
            if not cls:
                return False
            if cls.group(1) not in (el.attrs.get("class") or "").split():
                return False
            rest = rest[cls.end():]
            continue
        if rest.startswith("#"):
            ident = re.match(r"^#([-\w]+)", rest)
            if not ident:
                return False
            if el.attrs.get("id") != ident.group(1):
                return False
            rest = rest[ident.end():]
            continue
        if rest.startswith("["):
            m = _ATTR_RE.match(rest)
            if not m:
                return False
            name, op, _q, want, insensitive = m.groups()
            have = el.attrs.get(name)
            if have is None:
                return False
            if op:
                hay, needle = have, want
                if insensitive:
                    hay, needle = hay.lower(), needle.lower()
                if op == "=" and hay != needle:
                    return False
                if op == "*=" and needle not in hay:
                    return False
                if op in ("^=", "$=", "~=", "|=") and needle not in hay:
                    return False
            rest = rest[m.end():]
            continue
        return False
    return True


class _Locator:
    def __init__(self, page, selector):
        self._page = page
        self._selector = selector

    def _resolve(self):
        return [el for el in self._page.dom if _matches(el, self._selector)]

    def all(self):
        return self._resolve()

    def count(self):
        return len(self._resolve())

    def nth(self, i):
        return self._resolve()[i]

    @property
    def first(self):
        found = self._resolve()
        if not found:
            raise LookupError("no element for %r" % (self._selector,))
        return found[0]


# reveal modes
REVEAL_FRAME = "frame"      # the handler runs at the next rendered frame
REVEAL_AJAX = "ajax"        # ... and its rows arrive one read later still
REVEAL_NEVER = "never"      # the trigger genuinely opens nothing
REVEAL_UNSTABLE = "unstable"  # the page never stops changing


class _FakePage:
    """A page whose click handler runs at the next RENDERING OPPORTUNITY.

    A rendering opportunity is granted only when the page is asked to run a
    frame callback -- the one construct that PROVES a frame rendered.  Wall
    time in the runner's own process grants nothing, which is exactly the
    contended-host case row 446 is about.
    """

    def __init__(self, *, reveal=REVEAL_FRAME, decoys=_DECOY_COUNT,
                 ajax_reads=1, pre_open=False):
        self.url = _PAGE_URL
        self.dom = []
        self.reveal = reveal
        self.ajax_reads = ajax_reads
        self.clicked = []
        self.frames = 0
        self.metric_reads = 0
        self.waits_ms = []
        self._armed = False
        self._armed_at_read = None
        self._unstable_pad = 0
        self.tier_present_at_click = None

        # The trigger carries no resolution or download word of its own, so it
        # is never itself a candidate -- otherwise an empty page would score
        # its own button and the no-open control below could not be written.
        self.dom.append(_El(self, "button",
                            {"id": "dl-trigger", "class": "dl-trigger"},
                            "Open menu"))
        for i in range(decoys):
            self.dom.append(_El(
                self, "a",
                {"href": "https://cdn.example.test/related/other-scene-%d-1080p.mp4" % i,
                 "class": "related-mp4"},
                "Related Scene %d 1080p" % i))
        if pre_open:
            self._append_tier()

    # -- the fixture's own instrumentation --------------------------------
    def tier_count(self):
        return sum(1 for el in self.dom
                   if "tier-link" in (el.attrs.get("class") or "").split())

    def decoy_count(self):
        return sum(1 for el in self.dom
                   if "related-mp4" in (el.attrs.get("class") or "").split())

    def note_click(self, el):
        self.clicked.append(el)
        if el.attrs.get("id") == "dl-trigger":
            self._armed = True
            # PRECONDITION EVIDENCE: nothing is appended by the click itself.
            self.tier_present_at_click = self.tier_count()

    def _append_tier(self):
        self.dom.append(_El(
            self, "a",
            {"href": _TIER_HREF, "class": "tier-link", "download": ""},
            "Download 1080p MP4"))

    def _rendering_opportunity(self):
        self.frames += 1
        if not self._armed:
            return
        if self.reveal == REVEAL_FRAME:
            self._armed = False
            self._append_tier()
        elif self.reveal == REVEAL_AJAX:
            # The handler ran; its rows are still in flight.  They land after
            # ``ajax_reads`` further observations, so a single post-barrier
            # read is STILL stale and only a stability poll can see them.
            self._armed_at_read = self.metric_reads
            self._armed = False

    def _observe(self):
        self.metric_reads += 1
        if (self._armed_at_read is not None
                and self.metric_reads > self._armed_at_read + self.ajax_reads):
            self._armed_at_read = None
            self._append_tier()
        if self.reveal == REVEAL_UNSTABLE:
            self._unstable_pad += 1

    # -- Playwright page surface ------------------------------------------
    def locator(self, selector):
        return _Locator(self, selector)

    def evaluate(self, js, *_args):
        if "og:image" in js:
            return None
        if "requestAnimationFrame" in js:
            self._rendering_opportunity()
            return True
        if "scrollHeight" in js or "querySelectorAll" in js:
            self._observe()
            return [1000 + self._unstable_pad, len(self.dom)]
        return None

    def wait_for_timeout(self, ms):
        # Recorded, never slept: the settle contract is about OBSERVED
        # stability, and sleeping here would make the control below measure
        # the host's scheduler instead of the code under test.
        self.waits_ms.append(int(ms))

    def content(self):
        return "<html></html>"

    def goto(self, *_a, **_k):
        return None

    def close(self):
        return None


class _FakeContext:
    def __init__(self, page):
        self._page = page
        self.added = []

    def new_page(self):
        return self._page

    def add_cookies(self, cookies):
        self.added.append(cookies)


# ── the runner under test, with only external seams replaced ────────────────

_LEARNED = {
    "trigger_selectors": ["#dl-trigger"],
    "row_selectors": [".tier-link"],
}


class _Harness:
    def __init__(self):
        self.jobs = []
        self.failures = []
        self.events = []
        self.downloads = []
        self.stat_bumps = []
        self.demotions = []


def _drive(monkeypatch, page, tmp_path, *, learned=_LEARNED):
    """Run the REAL ``_process_one`` against ``page`` and report what it did."""
    from bulk_downloader import interstitial, selector_drift
    from bulk_downloader import runner as runner_mod

    h = _Harness()
    r = runner_mod.SiteRunner("row-446", {
        "name": "row-446",
        "max_concurrent": 1,
        "wait": 0,
        "delay": 0,
        "min_resolution": 0,
        "download_dir": str(tmp_path / "dl"),
        "learned": {"download": dict(learned)},
    })

    monkeypatch.setattr(runner_mod, "merge_template_download_hints",
                        lambda pg, ld, override_template=None: (ld, None))
    monkeypatch.setattr(interstitial, "clear_gates",
                        lambda *a, **k: {"cleared": []})
    monkeypatch.setattr(runner_mod, "_try_scrapling_turnstile",
                        lambda *a, **k: None)
    monkeypatch.setattr(runner_mod, "_FLARE_AVAILABLE", False)
    monkeypatch.setattr(runner_mod, "_AYLO_AVAILABLE", False)
    monkeypatch.setattr(runner_mod, "_VIXEN_AVAILABLE", False)
    monkeypatch.setattr(runner_mod, "_DL8_AVAILABLE", False)
    monkeypatch.setattr(runner_mod, "_SCRAPLING_AVAILABLE", False)
    monkeypatch.setattr(runner_mod, "_bump_learned_stat",
                        lambda cfg, stat: h.stat_bumps.append(stat))
    monkeypatch.setattr(runner_mod, "_bump_per_selector",
                        lambda *a, **k: None)
    monkeypatch.setattr(runner_mod, "_maybe_demote_selectors",
                        lambda cfg, kind, key: h.demotions.append((kind, key)))
    # Drift bookkeeping writes durable per-site state; keep this lane isolated.
    monkeypatch.setattr(selector_drift, "record_zero_match",
                        lambda *a, **k: None)
    monkeypatch.setattr(selector_drift, "record_success", lambda *a, **k: None)

    r._dedup_preflight = lambda url, job: None
    r._handle_auto_teach_check = lambda url, job: False
    r._check_cookies_or_relogin = lambda url: True
    r._stash_dedup_check = lambda url: False
    r._try_plugin_extractor = lambda *a, **k: False
    r._warm_session = lambda pg: None
    r._apply_stealth_library_to_page = lambda pg: None
    r._install_event_listeners = lambda pg, url: None
    r._handle_captcha_check = lambda pg, url: True
    r._check_redirect = lambda pg, url: ""
    r._run_pre_scrape_action = lambda pg: None
    r._flush_fingerprint_observation = lambda pg, url: None
    r._draft_override_template = lambda: None
    r._screenshot = lambda pg, url: ""
    r._handle_confirmed_no_video_page = lambda *a, **k: False
    r._try_deep_detect_fallback = lambda pg, url, learned_dl: None

    def update_job(url, status, message="", **kw):
        h.jobs.append((status, message))

    def handle_failure(url, message, **kw):
        h.failures.append(message)

    def log_event(kind, message, **kw):
        h.events.append((kind, message))

    def do_download(pg, ctx, url, best, dl_dir, lbl, probe=False):
        h.downloads.append(best)

    r._update_job = update_job
    r._handle_failure = handle_failure
    r.log_event = log_event
    r._do_download = do_download

    ctx = _FakeContext(page)
    started = time.monotonic()
    r._process_one(None, _PAGE_URL, persistent_ctx=ctx)
    h.elapsed = time.monotonic() - started
    h.runner = r
    return h


# ── the fixture must be readable by the REAL scorer ─────────────────────────

def test_the_fixture_page_is_readable_by_the_real_scorer():
    """PRECONDITION for every verdict below.

    Both states of the fake page are handed to the production
    ``detect.find_best_download``: the stale state must yield a DECOY (that is
    the wrong-file shape), and the settled state must yield the tier through
    the LEARNED selector.  A fake the real scorer could not read would make
    every assertion in this file vacuous.
    """
    from bulk_downloader.detect import find_best_download

    page = _FakePage(reveal=REVEAL_FRAME)
    assert page.decoy_count() == _DECOY_COUNT, (
        "the pre-click page must carry a nonzero exact count of decoy media "
        "links: %d" % (page.decoy_count(),))
    assert page.tier_count() == 0, "the pre-click page already carries the tier"

    stale = find_best_download(page, "", learned=_LEARNED, runner=None)
    assert stale, "the real scorer found nothing at all on the stale page"
    assert not stale.get("_via_learned"), (
        "the stale page cannot produce a learned hit -- the tier is absent")
    assert stale["locator"].attrs.get("class") == "related-mp4", (
        "the stale page must score a DECOY, which is the wrong-file shape: %r"
        % (stale["locator"],))

    page._armed = True
    page._rendering_opportunity()
    assert page.tier_count() == 1, "the settled fixture did not append the tier"

    settled = find_best_download(page, "", learned=_LEARNED, runner=None)
    assert settled and settled.get("_via_learned") is True, (
        "the settled page must produce a LEARNED hit: %r" % (settled,))
    assert settled["_learned_sel"] == ".tier-link"
    assert settled["locator"].attrs.get("href") == _TIER_HREF, (
        "the settled page must select the tier link: %r" % (settled,))


# ── the verdicts ────────────────────────────────────────────────────────────

def test_a_modal_appended_at_the_next_frame_is_not_scraped_early(
        monkeypatch, tmp_path):
    page = _FakePage(reveal=REVEAL_FRAME)
    h = _drive(monkeypatch, page, tmp_path)

    # -- preconditions ----------------------------------------------------
    assert len(page.clicked) == 1, (
        "the trigger must be clicked exactly once: %r" % (page.clicked,))
    assert page.tier_present_at_click == 0, (
        "the fixture did not delay: the tier existed at click time")
    assert page.decoy_count() == _DECOY_COUNT

    # -- verdict ----------------------------------------------------------
    assert len(h.downloads) == 1, (
        "exactly one download must be dispatched; got %r (jobs=%r failures=%r)"
        % (h.downloads, h.jobs, h.failures))
    best = h.downloads[0]
    assert best["locator"].attrs.get("href") == _TIER_HREF, (
        "the scrape read the PRE-CLICK page and selected %r instead of the "
        "tier the click opened -- this is the wrong-file shape, filed under "
        "the right title" % (best["locator"],))
    assert best.get("_via_learned") is True, (
        "the learned selector should have hit the settled modal: %r" % (best,))

    # -- and the learning state must not have been corrupted ---------------
    assert "download_misses" not in h.stat_bumps, (
        "a schedule-induced miss bumped download_misses: %r" % (h.stat_bumps,))
    assert h.stat_bumps == ["download_hits"], (
        "the settled hit must record exactly one learned hit: %r"
        % (h.stat_bumps,))
    assert h.demotions == [], (
        "healthy learned selectors were demoted by a page that was merely "
        "read early: %r" % (h.demotions,))


def test_a_menu_whose_rows_arrive_across_reads_is_waited_for(
        monkeypatch, tmp_path):
    """The slow-AJAX half: the handler ran, but its rows land one read later.

    A single post-barrier read is still stale here, so this case separates a
    rendered-frame barrier alone from a real stability poll.
    """
    page = _FakePage(reveal=REVEAL_AJAX, ajax_reads=1)
    h = _drive(monkeypatch, page, tmp_path)

    assert len(page.clicked) == 1
    assert page.tier_present_at_click == 0, "the fixture did not delay"

    assert len(h.downloads) == 1, (
        "exactly one download must be dispatched; got %r (jobs=%r failures=%r)"
        % (h.downloads, h.jobs, h.failures))
    assert h.downloads[0]["locator"].attrs.get("href") == _TIER_HREF, (
        "a menu whose rows arrived one observation later was scored from the "
        "stale page: %r" % (h.downloads[0]["locator"],))
    assert h.demotions == [], (
        "the AJAX race demoted learned selectors: %r" % (h.demotions,))


def test_a_page_that_never_settles_is_refused_not_scored(
        monkeypatch, tmp_path):
    """CLAUDE.md A7: an unavailable measurement returns UNKNOWN, never OK."""
    from bulk_downloader import runner as runner_mod

    monkeypatch.setattr(runner_mod, "DOWNLOAD_SETTLE_BUDGET_S", 0.05,
                        raising=False)
    page = _FakePage(reveal=REVEAL_UNSTABLE)
    h = _drive(monkeypatch, page, tmp_path)

    assert len(page.clicked) == 1
    assert h.downloads == [], (
        "a page whose DOM never stopped changing was scored anyway and a "
        "download was dispatched from it: %r" % (h.downloads,))
    assert any(status == "pending" for status, _m in h.jobs), (
        "the refusal must leave the URL pending for a later pass: %r"
        % (h.jobs,))
    kinds = [k for k, _m in h.events]
    assert "download_trigger_unsettled" in kinds, (
        "the refusal must carry its own distinctive diagnostic, "
        "distinguishable from the no-button refusal: %r" % (h.events,))
    assert h.failures == [], (
        "an unobservable page is not a download failure: %r" % (h.failures,))
    assert h.stat_bumps == [] and h.demotions == [], (
        "an unobservable page must not touch learning state: %r / %r"
        % (h.stat_bumps, h.demotions))


# ── negative controls ───────────────────────────────────────────────────────

def test_a_menu_that_is_already_open_still_selects_the_tier(
        monkeypatch, tmp_path):
    """NEGATIVE CONTROL, and it passes on the defective parent too.

    An ordinary click on a page whose tier list is already present must keep
    working.  A settle condition that refused, waited for growth that will
    never come, or lost the learned hit would fail here.
    """
    page = _FakePage(reveal=REVEAL_NEVER, pre_open=True)
    h = _drive(monkeypatch, page, tmp_path)

    assert len(page.clicked) == 1, "the trigger was not clicked"
    assert page.tier_count() == 1, "the fixture did not pre-open the menu"
    assert len(h.downloads) == 1, (
        "an already-open menu must still download: %r / %r"
        % (h.jobs, h.failures))
    assert h.downloads[0]["locator"].attrs.get("href") == _TIER_HREF, (
        "an already-open menu lost its tier: %r" % (h.downloads[0]["locator"],))
    assert h.downloads[0].get("_via_learned") is True
    assert h.stat_bumps == ["download_hits"], (
        "the already-open hit must record exactly one learned hit: %r"
        % (h.stat_bumps,))
    assert h.demotions == []


def test_the_settle_costs_far_less_than_the_sleep_it_replaces(
        monkeypatch, tmp_path):
    """A settled menu must not pay the 1.5s the fixed sleep charged every time.

    Measured in the milliseconds the code ASKS the page to wait and in wall
    clock -- never by eye.  The fake page records waits without sleeping, so
    this measures the code under test rather than the host's scheduler.
    """
    page = _FakePage(reveal=REVEAL_NEVER, pre_open=True)
    h = _drive(monkeypatch, page, tmp_path)

    assert len(h.downloads) == 1, (
        "the cost control must reach a download: %r / %r" % (h.jobs, h.failures))
    assert sum(page.waits_ms) <= 500, (
        "the settle asked the page to wait %dms; it replaces a 1500ms sleep "
        "and must cost far less on a settled menu" % (sum(page.waits_ms),))
    assert h.elapsed < 1.0, (
        "a settled trigger paid %.2fs of wall clock; the fixed 1.5s sleep "
        "between the click and the scrape is supposed to be gone"
        % (h.elapsed,))


def test_a_trigger_that_opens_nothing_keeps_its_own_diagnostic(
        monkeypatch, tmp_path):
    """NEGATIVE CONTROL. No laundering in either direction.

    A trigger that opens nothing on a page with no other candidates must fail
    as "No download button found" -- NOT as a settle refusal (which would
    misreport a broken selector as a slow page) and NOT as a success.
    """
    page = _FakePage(reveal=REVEAL_NEVER, decoys=0)
    h = _drive(monkeypatch, page, tmp_path)

    assert len(page.clicked) == 1, "the trigger was not clicked"
    assert page.tier_count() == 0, "the fixture opened something after all"
    assert h.downloads == [], (
        "a page with nothing on it produced a download: %r" % (h.downloads,))
    assert h.failures == ["No download button found"], (
        "the no-button refusal lost its distinctive diagnostic: %r"
        % (h.failures,))
    kinds = [k for k, _m in h.events]
    assert "download_trigger_unsettled" not in kinds, (
        "a settled-but-empty page was misreported as unobservable: %r"
        % (h.events,))

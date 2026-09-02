"""ROW 446 -- the download trigger must not race the modal it opened.

``loc.click()`` tells the site's handler to run; the handler appends its tier
list at Chromium's next rendering or XHR opportunity, which is NOT ordered
against the CDP round-trip that reads the DOM back.  The old path waited a
fixed ``time.sleep(1.5)`` and then scraped exactly once, so a slow AJAX menu
was scraped PRE-CLICK: ``find_best_download`` scored whatever anchors already
existed -- on a page carrying direct media links outside the modal that is a
wrong or lower-tier file recorded under the requested title -- and every
schedule-induced miss additionally bumped ``download_misses`` and demoted
healthy learned selectors.

No browser and no network: the page is a scripted fake driven by a virtual
clock, so the 2.5s modal delay costs no wall time and cannot flake.  The
fixture's own numbers mirror the 2026-08-29 incident page (159 media links,
6 of them the requested work).
"""
from __future__ import annotations

import ast
import importlib
from pathlib import Path

import pytest

BD_GATE_SCOPE = "module"

runner = importlib.import_module("bulk_downloader.runner")
REPO = Path(__file__).resolve().parents[1]

PRE_HEIGHT = 4000
POST_HEIGHT = 4600
DECOY_LINKS = 159       # related-videos grid already on the pre-click page
TIER_LINKS = 6          # the six download tiers the modal appends
MODAL_AT_S = 2.5        # later than the old fixed 1.5s sleep, deliberately
LEGACY_SLEEP_S = 1.5    # the defective parent's entire settle mechanism


class _VirtualClock:
    """Monotonic time the test advances by hand; no wall-clock sleeping."""

    def __init__(self):
        self.t = 0.0
        self.sleeps = 0

    def monotonic(self):
        return self.t

    def sleep(self, seconds):
        self.sleeps += 1
        self.t += float(seconds)


class _ScriptedTriggerPage:
    """A page whose click handler appends the tier list at ``modal_at``."""

    def __init__(self, clock, *, modal_at=MODAL_AT_S, raise_until=None,
                 raise_always=False, churn=False):
        self.clock = clock
        self.modal_at = modal_at
        self.raise_until = raise_until
        self.raise_always = raise_always
        self.churn = churn
        self.reads = []          # virtual timestamps of every evaluate()
        self.raises = 0

    # --- what a scraper would find, at this instant --------------------
    def anchors(self):
        found = [f"/decoy/{i}.mp4" for i in range(DECOY_LINKS)]
        if self.modal_open():
            found += [f"/tier/{i}/requested.mp4" for i in range(TIER_LINKS)]
        return found

    def modal_open(self):
        return self.clock.t >= self.modal_at

    def evaluate(self, _js):
        self.reads.append(self.clock.t)
        if self.raise_always or (
                self.raise_until is not None
                and self.clock.t < self.raise_until):
            self.raises += 1
            raise RuntimeError("Execution context was destroyed")
        if self.churn:
            # A page that never stops moving: an ad carousel, a live counter.
            return [PRE_HEIGHT + int(self.clock.t * 100),
                    DECOY_LINKS + int(self.clock.t * 10)]
        if self.modal_open():
            return [POST_HEIGHT, DECOY_LINKS + TIER_LINKS]
        return [PRE_HEIGHT, DECOY_LINKS]


def _settle(page, before, clock, **kw):
    kw.setdefault("budget_s", runner.TRIGGER_SETTLE_BUDGET_S)
    return runner._settle_after_trigger(
        page, before, sleep=clock.sleep, clock=clock.monotonic, **kw)


# ── preconditions: the fixture really is the race, not a green shape ────

def test_row446_precondition_fixture_delays_past_the_old_fixed_sleep():
    clock = _VirtualClock()
    page = _ScriptedTriggerPage(clock)

    before = runner._safe_trigger_metrics(page)
    assert before == (PRE_HEIGHT, DECOY_LINKS), (
        f"pre-click page did not build as intended: {before!r}")
    assert before[1] == DECOY_LINKS and DECOY_LINKS > 0, (
        "the pre-click page must carry a nonzero exact count of decoy "
        "anchors, or a stale scrape would find nothing and fail loudly "
        "instead of silently selecting the wrong file")
    assert not page.modal_open(), "the modal is open before the trigger"
    assert len(page.anchors()) == DECOY_LINKS

    clock.sleep(LEGACY_SLEEP_S)
    assert not page.modal_open(), (
        f"the fixture did not delay past the old {LEGACY_SLEEP_S}s sleep")

    clock.sleep(MODAL_AT_S - LEGACY_SLEEP_S)
    assert page.modal_open(), "the fixture never opened the modal at all"
    assert len(page.anchors()) == DECOY_LINKS + TIER_LINKS
    assert runner._trigger_page_metrics(page) == (
        POST_HEIGHT, DECOY_LINKS + TIER_LINKS)


def test_row446_mutation_catcher_the_old_fixed_sleep_reads_the_stale_page():
    """RED provenance: the defective parent's algorithm -- sleep 1.5s, read
    once -- replayed against this exact fixture, observing the PRE-CLICK
    page.  Without this the settle assertions could be green for want of a
    race rather than for want of a defect."""
    clock = _VirtualClock()
    page = _ScriptedTriggerPage(clock)
    before = runner._safe_trigger_metrics(page)

    clock.sleep(LEGACY_SLEEP_S)                 # the entire old settle
    observed = runner._trigger_page_metrics(page)   # the single scrape

    assert observed == before, (
        "the old fixed sleep did NOT observe the stale page; the fixture "
        f"does not reproduce row 446 (observed {observed!r})")
    stale_anchors = page.anchors()
    assert len(stale_anchors) == DECOY_LINKS
    assert not any("/tier/" in a for a in stale_anchors), (
        "the stale scrape somehow saw the tier links")
    assert any("/decoy/" in a for a in stale_anchors), (
        "the stale scrape had nothing to mis-select from, so the incident "
        "shape (a wrong file recorded as done) is not reproduced")


# ── the verdict ─────────────────────────────────────────────────────────

def test_row446_settle_observes_the_modal_that_lands_after_the_old_budget():
    clock = _VirtualClock()
    page = _ScriptedTriggerPage(clock)
    before = runner._safe_trigger_metrics(page)

    state, metrics, reads = _settle(page, before, clock)

    assert state == runner.TRIGGER_SETTLE_SETTLED, (
        f"settle returned {state}, expected SETTLED (reads at {page.reads})")
    assert metrics == (POST_HEIGHT, DECOY_LINKS + TIER_LINKS), (
        f"settled on {metrics!r}, not the post-modal page")
    assert metrics != before, "settled on the pre-click page"
    assert reads >= runner.TRIGGER_SETTLE_QUIET_POLLS, (
        f"only {reads} read(s); stability was never actually observed")
    assert clock.t >= MODAL_AT_S, (
        f"returned at virtual t={clock.t}, before the modal could exist")
    assert clock.t <= runner.TRIGGER_SETTLE_BUDGET_S, (
        f"the settle overran its budget (t={clock.t})")
    assert page.modal_open(), "the page is settled but the modal is shut"
    assert sum(1 for a in page.anchors() if "/tier/" in a) == TIER_LINKS, (
        "the settled page does not carry the six tier links a scrape needs")


def test_row446_a_page_that_settles_early_costs_less_than_the_old_sleep():
    """The fix is not just a longer wait: a modal that lands immediately
    returns FASTER than the fixed 1.5s the parent always paid."""
    clock = _VirtualClock()
    page = _ScriptedTriggerPage(clock, modal_at=0.0)
    before = (PRE_HEIGHT, DECOY_LINKS)   # what the page looked like pre-click

    state, metrics, _reads = _settle(page, before, clock)

    assert state == runner.TRIGGER_SETTLE_SETTLED
    assert metrics == (POST_HEIGHT, DECOY_LINKS + TIER_LINKS)
    assert clock.t < LEGACY_SLEEP_S, (
        f"an immediate modal still cost {clock.t}s, no better than the old "
        f"fixed {LEGACY_SLEEP_S}s")


# ── negative controls ───────────────────────────────────────────────────

def test_row446_negative_control_a_trigger_that_opens_nothing_is_UNCHANGED():
    """A trigger that genuinely opens nothing must NOT be reported settled.
    The caller still scrapes -- some triggers legitimately change no DOM --
    but the state says the wait proved no modal, so a stale page is never
    laundered into a SETTLED claim."""
    clock = _VirtualClock()
    page = _ScriptedTriggerPage(clock, modal_at=1e9)
    before = runner._safe_trigger_metrics(page)

    state, metrics, reads = _settle(page, before, clock, budget_s=2.0)

    assert state == runner.TRIGGER_SETTLE_UNCHANGED, (
        f"a page that never moved was reported {state}")
    assert state != runner.TRIGGER_SETTLE_SETTLED
    assert metrics == before, f"UNCHANGED reported {metrics!r}, not what it saw"
    assert reads > 1, "the budget expired without actually polling"
    assert 2.0 <= clock.t <= 2.0 + runner.TRIGGER_SETTLE_POLL_S, (
        f"the unchanged wait was not bounded by its budget (t={clock.t})")


def test_row446_negative_control_an_unreadable_page_is_UNOBSERVED():
    """Every read raised: there is no evidence to scrape against, so the
    wait refuses rather than proceeding (CLAUDE.md A7)."""
    clock = _VirtualClock()
    page = _ScriptedTriggerPage(clock, raise_always=True)
    before = runner._safe_trigger_metrics(page)

    assert before is None, "the pre-trigger read should also have failed"
    state, metrics, reads = _settle(page, before, clock, budget_s=1.0)

    assert state == runner.TRIGGER_SETTLE_UNOBSERVED, (
        f"an entirely unreadable page was reported {state}")
    assert metrics is None
    assert reads == 0, f"reported {reads} observed read(s) on a raising page"
    assert page.raises > 0, "the fixture never actually raised"


def test_row446_a_transient_navigation_raise_does_not_become_UNOBSERVED():
    """A click that starts a navigation destroys the execution context
    transiently.  Refusing on the first raise would regress real captures, so
    only a budget in which NOTHING was read is UNOBSERVED."""
    clock = _VirtualClock()
    page = _ScriptedTriggerPage(clock, raise_until=1.0)
    before = None  # the pre-trigger read lost the race too

    state, metrics, reads = _settle(page, before, clock)

    assert page.raises > 0, "the fixture never raised; the control is vacuous"
    assert state == runner.TRIGGER_SETTLE_SETTLED, (
        f"a transient raise degraded the settle to {state}")
    assert reads >= runner.TRIGGER_SETTLE_QUIET_POLLS
    assert metrics is not None


def test_row446_negative_control_a_page_that_never_stops_moving_is_UNSTABLE():
    """Bounded, and reported as its own state -- never as SETTLED."""
    clock = _VirtualClock()
    page = _ScriptedTriggerPage(clock, churn=True)
    before = (0, 0)

    state, metrics, reads = _settle(page, before, clock, budget_s=3.0)

    assert state == runner.TRIGGER_SETTLE_UNSTABLE, (
        f"a permanently churning page was reported {state}")
    assert state != runner.TRIGGER_SETTLE_SETTLED
    assert metrics is not None and reads > 1
    assert 3.0 <= clock.t <= 3.0 + runner.TRIGGER_SETTLE_POLL_S, (
        f"the churn wait was not bounded by its budget (t={clock.t})")


def test_row446_states_are_four_distinct_values():
    """A diagnostic that collapses distinct failures costs the investigation
    (CLAUDE.md A7): UNCHANGED, UNSTABLE and UNOBSERVED lead to different
    actions and must not share a value."""
    states = {runner.TRIGGER_SETTLE_SETTLED, runner.TRIGGER_SETTLE_UNCHANGED,
              runner.TRIGGER_SETTLE_UNSTABLE,
              runner.TRIGGER_SETTLE_UNOBSERVED}
    assert len(states) == 4, f"settle states collapse: {states}"


# ── both trigger paths, derived from source rather than assumed ─────────

def _process_one_body():
    path = REPO / "bulk_downloader" / "runner.py"
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_process_one":
            return node
    raise AssertionError("_process_one not found")


def _calls_named(scope, name):
    out = []
    for node in ast.walk(scope):
        if isinstance(node, ast.Call):
            fn = node.func
            if isinstance(fn, ast.Name) and fn.id == name:
                out.append(node.lineno)
            elif isinstance(fn, ast.Attribute) and fn.attr == name:
                out.append(node.lineno)
    return out


def _trigger_locator_names(scope):
    """Names bound from ``page.locator(...)`` -- the trigger locators.

    Derived, not listed: a third trigger added later enters this denominator
    automatically.  ``best["locator"].click()`` further down is deliberately
    NOT a trigger: it is the DOWNLOAD click, it is followed by a `done` row
    and a return rather than by a scrape, and its delay is the operator's
    configured post-download `delay`, not a settle before a DOM read.
    """
    names = set()
    for node in ast.walk(scope):
        if not isinstance(node, ast.Assign):
            continue
        call = node.value
        while isinstance(call, ast.Attribute):
            call = call.value
        if not (isinstance(call, ast.Call)
                and isinstance(call.func, ast.Attribute)
                and call.func.attr == "locator"):
            continue
        for target in node.targets:
            if isinstance(target, ast.Name):
                names.add(target.id)
    return names


def _trigger_click_lines(scope):
    names = _trigger_locator_names(scope)
    assert names, "no page.locator(...) binding found; denominator is zero"
    return [n.lineno for n in ast.walk(scope)
            if isinstance(n, ast.Call)
            and isinstance(n.func, ast.Attribute) and n.func.attr == "click"
            and isinstance(n.func.value, ast.Name)
            and n.func.value.id in names]


def test_row446_the_trigger_click_denominator_excludes_the_download_click():
    """Pin the denominator itself: exactly two trigger locators are bound,
    and the download click is not one of them."""
    fn = _process_one_body()
    names = _trigger_locator_names(fn)
    assert names == {"loc", "rloc"}, (
        f"trigger locator bindings changed: {sorted(names)}")
    all_clicks = [n.lineno for n in ast.walk(fn) if isinstance(n, ast.Call)
                  and isinstance(n.func, ast.Attribute)
                  and n.func.attr == "click"]
    trigger_clicks = _trigger_click_lines(fn)
    assert len(all_clicks) == 3 and len(trigger_clicks) == 2, (
        f"clicks {all_clicks} / trigger clicks {trigger_clicks}: the "
        "download click must be excluded and everything else included")
    excluded = set(all_clicks) - set(trigger_clicks)
    assert len(excluded) == 1


def test_row446_both_trigger_paths_settle_and_neither_sleeps_a_fixed_budget():
    """The denominator: BOTH the learned and the recovered trigger paths.
    Fixing one and leaving the other is not a fix -- a drifted selector is
    exactly when the site is least predictable."""
    fn = _process_one_body()

    clicks = sorted(_trigger_click_lines(fn))
    assert len(clicks) == 2, (
        f"expected the learned and recovered trigger clicks, found "
        f"{len(clicks)} at {clicks}")

    settles = _calls_named(fn, "_settle_after_trigger")
    assert len(settles) == 2, (
        f"expected one settle per trigger path, found {len(settles)} at "
        f"{settles}")
    for click_line in clicks:
        assert any(s > click_line for s in settles), (
            f"the click at line {click_line} has no settle after it")

    befores = _calls_named(fn, "_safe_trigger_metrics")
    assert len(befores) == 2, (
        f"expected a pre-trigger metrics read per path, found {len(befores)}")
    for click_line, before_line in zip(clicks, befores):
        assert before_line < click_line, (
            "the pre-trigger read must happen BEFORE the click, or the "
            "settle has no change anchor and calls the untouched page settled")

    # No fixed sleep survives as a settle mechanism on either path.
    fixed = [n.lineno for n in ast.walk(fn)
             if isinstance(n, ast.Call)
             and isinstance(n.func, ast.Attribute) and n.func.attr == "sleep"
             and n.args and isinstance(n.args[0], ast.Constant)
             and isinstance(n.args[0].value, (int, float))
             and float(n.args[0].value) >= 1.0]
    assert fixed == [], (
        f"a fixed settle sleep of >=1s survives at line(s) {fixed}")


def test_row446_an_unobserved_settle_returns_before_any_learning_is_touched():
    """Structural: the UNOBSERVED refusal must return before
    find_best_download, _bump_learned_stat and _maybe_demote_selectors, so a
    page the runner never saw cannot corrupt per-site learning state -- the
    second half of the damage this row names."""
    fn = _process_one_body()

    refusals = [n for n in ast.walk(fn)
                if isinstance(n, ast.Compare)
                and isinstance(n.left, ast.Name)
                and n.left.id == "trigger_settle_state"]
    assert len(refusals) == 1, (
        f"expected exactly one settle-state refusal, found {len(refusals)}")
    refusal_line = refusals[0].lineno

    # The refusal is UNOBSERVED-only. Refusing on UNCHANGED or UNSTABLE too
    # would be the over-correction: a trigger that legitimately opens nothing,
    # and a page with a live counter on it, would both stop downloading.
    node = refusals[0]
    assert len(node.ops) == 1 and isinstance(node.ops[0], ast.Eq), (
        "the settle refusal is not an equality test; it may catch states "
        "other than UNOBSERVED")
    comparator = node.comparators[0]
    assert isinstance(comparator, ast.Name) and \
        comparator.id == "TRIGGER_SETTLE_UNOBSERVED", (
        f"the refusal compares against {ast.dump(comparator)}, not "
        "TRIGGER_SETTLE_UNOBSERVED")

    for name in ("find_best_download", "_bump_learned_stat",
                 "_maybe_demote_selectors"):
        lines = _calls_named(fn, name)
        assert lines, f"{name} is not called in _process_one at all"
        assert all(line > refusal_line for line in lines), (
            f"{name} is reachable at line(s) "
            f"{[l for l in lines if l < refusal_line]} before the UNOBSERVED "
            f"refusal at line {refusal_line}")


def test_row446_the_refusal_uses_an_established_job_status():
    """A refusal readers cannot classify is a new defect, not a fix.

    The UNOBSERVED path publishes a job status; that status must already be
    in the vocabulary the rest of the runner emits, or the frontend's status
    maps, history accounting and retry logic all meet a value they have never
    seen. The vocabulary is derived from the source, not listed here."""
    import re

    src_dir = REPO / "bulk_downloader"
    pattern = re.compile(r'_update_job\([^,]+,\s*"([a-z_]+)"')
    vocabulary = {}
    for path in sorted(src_dir.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        for status in pattern.findall(path.read_text(encoding="utf-8")):
            vocabulary.setdefault(status, set()).add(path.name)
    assert len(vocabulary) > 3, (
        f"zero-ish status denominator: {sorted(vocabulary)}")

    fn = _process_one_body()
    refusal_line = [n.lineno for n in ast.walk(fn)
                    if isinstance(n, ast.Compare)
                    and isinstance(n.left, ast.Name)
                    and n.left.id == "trigger_settle_state"][0]
    published = [n for n in ast.walk(fn) if isinstance(n, ast.Call)
                 and isinstance(n.func, ast.Attribute)
                 and n.func.attr == "_update_job"
                 and refusal_line <= n.lineno <= refusal_line + 12
                 and len(n.args) >= 2
                 and isinstance(n.args[1], ast.Constant)]
    assert len(published) == 1, (
        f"expected one status publication in the refusal, found "
        f"{len(published)}")
    status = published[0].args[1].value
    others = vocabulary.get(status, set()) - {"runner.py"}
    assert others, (
        f"the refusal publishes status {status!r}, which no other module "
        f"emits -- known vocabulary: {sorted(vocabulary)}")

"""v3.66.786 -- A-DISCO cut 2: host_enumerator + EnumBudget (AR4 caps).

A standalone, bounded, host-confined crawler. It enumerates an ALREADY-APPROVED
host to depth and returns candidate URLs -- it does NOT fetch off-host, does NOT
enqueue (cut 3), and does NOT run unbounded. The fetch / link-extraction / seen /
sleep seams are injected so cut 3 can wire the real fetch, discovery dedup, and
politeness clock without this module re-implementing them.

THE INVARIANT THIS FILE PINS (the dominant failure shape, AR4 flavour): a budget
that is 'active' only when the operator configured one means an unconfigured run
enumerates UNBOUNDED -- the guardrail that isn't there by default. AR4 caps matter
MORE at full enumeration, so ``enumerate_host`` with no budget uses a bounded
default, and a large/looping host can never flood the frontier. HALT is visible:
on a breach the crawl stops and says which cap broke.
"""
import time

from bulk_downloader import host_enumerator as he


# -- fixtures: an in-memory "site" so the crawl is deterministic + offline ----

def _site_fetch(pages):
    """fetch_fn over a dict {url: html}. Missing url -> None (fetch failed)."""
    def _fetch(url):
        return pages.get(url)
    return _fetch


def _links_html(*urls):
    return "".join(f'<a href="{u}">x</a>' for u in urls)


# -- host confinement --------------------------------------------------------

def test_enumerate_stays_within_host():
    root = "https://approved.com/lib"
    pages = {root: _links_html(
        "https://approved.com/v/1",
        "https://approved.com/v/2",
        "https://evil.com/v/3",            # off-host: must be rejected
        "https://cdn.other.net/x",         # off-host: must be rejected
    )}
    out = he.enumerate_host(root, fetch_fn=_site_fetch(pages),
                            budget=he.EnumBudget(max_depth=1, delay_s=0))
    cands = set(out["candidates"])
    assert "https://approved.com/v/1" in cands
    assert "https://approved.com/v/2" in cands
    assert "https://evil.com/v/3" not in cands
    assert "https://cdn.other.net/x" not in cands
    assert out["off_host_rejected"] >= 2


def test_subdomains_of_approved_host_allowed():
    # cdn.approved.com shares the registrable host approved.com -> same site.
    root = "https://approved.com/lib"
    pages = {root: _links_html("https://cdn.approved.com/v/1")}
    out = he.enumerate_host(root, fetch_fn=_site_fetch(pages),
                            budget=he.EnumBudget(max_depth=1, delay_s=0))
    assert "https://cdn.approved.com/v/1" in set(out["candidates"])


# -- AR4 budget caps: HALT (not skip), reason visible ------------------------

def test_max_pages_halts():
    # A host that links on forever; max_pages must stop the crawl.
    def _fetch(url):
        n = int(url.rsplit("/", 1)[-1]) if url.rsplit("/", 1)[-1].isdigit() else 0
        return _links_html(f"https://h.com/{n + 1}")
    out = he.enumerate_host("https://h.com/0", fetch_fn=_fetch,
                            budget=he.EnumBudget(max_pages=3, max_depth=0, delay_s=0))
    assert out["pages_fetched"] <= 3
    assert out["halted"] and out["halt_reason"] == "max_pages"


def test_max_candidates_halts():
    root = "https://h.com/lib"
    pages = {root: _links_html(*[f"https://h.com/v/{i}" for i in range(50)])}
    out = he.enumerate_host(root, fetch_fn=_site_fetch(pages),
                            budget=he.EnumBudget(max_candidates=10, max_depth=1,
                                                 delay_s=0))
    assert len(out["candidates"]) <= 10
    assert out["halted"] and out["halt_reason"] == "max_candidates"


def test_max_depth_bounds_the_crawl():
    # depth is hops-from-root, root = depth 1. max_depth=1 = fetch root only; its
    # links are candidates but are NOT fetched (they are depth 2 > 1).
    root = "https://h.com/a"
    pages = {
        root: _links_html("https://h.com/b"),
        "https://h.com/b": _links_html("https://h.com/c"),  # depth 2: not fetched
    }
    out = he.enumerate_host(root, fetch_fn=_site_fetch(pages),
                            budget=he.EnumBudget(max_depth=1, delay_s=0))
    assert out["pages_fetched"] == 1
    assert "https://h.com/b" in set(out["candidates"])
    assert "https://h.com/c" not in set(out["candidates"])


# -- the AR4 fail-safe: no budget is STILL bounded ---------------------------

def test_default_budget_is_bounded_not_unbounded():
    # A branching host: every page links to 20 fresh in-host children, so the
    # frontier explodes -> UNBOUNDED if the default were uncapped. With no budget
    # passed, enumerate_host must self-bound on its default_safe caps and halt.
    # If this hangs or fetches a huge number of pages, the guardrail is missing.
    def _fetch(url):
        base = url.rstrip("/")
        return _links_html(*[f"{base}/{i}" for i in range(20)])
    b = he.EnumBudget.default_safe()
    started = time.time()
    out = he.enumerate_host("https://loop.com/0", fetch_fn=_fetch)  # NO budget
    assert time.time() - started < 30, "default enumeration did not self-bound"
    assert out["halted"], "unbounded default: a runaway host was not capped"
    assert out["pages_fetched"] <= b.max_pages
    assert len(out["candidates"]) <= b.max_candidates


def test_default_safe_budget_is_active_and_bounded():
    b = he.EnumBudget.default_safe()
    assert b.is_active()
    assert b.max_pages > 0 and b.max_candidates > 0 and b.max_depth > 0
    assert b.wall_s > 0


# -- dedup, politeness, fail-open --------------------------------------------

def test_seen_urls_are_skipped():
    root = "https://h.com/lib"
    pages = {root: _links_html("https://h.com/v/1", "https://h.com/v/2",
                               "https://h.com/v/3")}
    seen = {"https://h.com/v/2"}
    out = he.enumerate_host(root, fetch_fn=_site_fetch(pages),
                            seen_fn=lambda u: u in seen,
                            budget=he.EnumBudget(max_depth=1, delay_s=0))
    cands = set(out["candidates"])
    assert "https://h.com/v/2" not in cands
    assert "https://h.com/v/1" in cands
    assert out["seen_skipped"] >= 1


def test_politeness_delay_is_applied_between_fetches():
    calls = []
    root = "https://h.com/a"
    pages = {root: _links_html("https://h.com/b"),
             "https://h.com/b": _links_html("https://h.com/c")}
    he.enumerate_host(root, fetch_fn=_site_fetch(pages),
                      sleep_fn=lambda s: calls.append(s),
                      budget=he.EnumBudget(max_depth=2, delay_s=1.5))
    # at least one inter-fetch delay of the configured length was requested
    assert any(abs(s - 1.5) < 1e-9 for s in calls), calls


def test_fetch_failure_is_skipped_not_fatal():
    root = "https://h.com/a"
    pages = {root: _links_html("https://h.com/dead", "https://h.com/live"),
             "https://h.com/live": _links_html("https://h.com/v/1")}
    # /dead returns None (fetch fail); crawl must continue to /live.
    out = he.enumerate_host(root, fetch_fn=_site_fetch(pages),
                            budget=he.EnumBudget(max_depth=2, delay_s=0))
    assert out.get("fetch_failures", 0) >= 1
    assert "https://h.com/v/1" in set(out["candidates"])


def test_bad_root_is_graceful():
    out = he.enumerate_host("", fetch_fn=lambda u: None,
                            budget=he.EnumBudget(delay_s=0))
    assert out["candidates"] == []
    assert not out.get("error_raised")


# -- EnumBudget mirrors the AutoBudget idiom ---------------------------------

def test_enum_budget_breach_reasons():
    b = he.EnumBudget(max_pages=5, max_candidates=10, max_depth=3, wall_s=60.0)
    assert b.breach(pages=5) == "max_pages"
    assert b.breach(candidates=10) == "max_candidates"
    assert b.breach(elapsed_s=60.0) == "wall_s"
    assert b.breach(pages=1, candidates=1, elapsed_s=1.0) is None


def test_enum_budget_inactive_when_unset():
    assert not he.EnumBudget().is_active()
    assert he.EnumBudget().breach(pages=99999) is None  # uncapped fields never breach

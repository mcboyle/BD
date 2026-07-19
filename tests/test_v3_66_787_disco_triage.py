"""v3.66.787 -- A-DISCO cut 3: disco_triage -- the triage + auto-queue orchestrator.

Composes cut 2 (host_enumerator) + cut 1 (candidate_filter tier) + the queue:
enumerate an approved host -> score each candidate URL -> auto-queue the
high-confidence ones (capped, best-first) -> stage the uncertain ones for review
-> drop the junk. DEFAULT-OFF and dark (imported by nothing yet; cut 4 wires the
toggle, scheduler, route, and persistence).

Triage of an enumerated PAGE URL (distinct from a template download control):
  * classify() supplies the junk rejections (nav / account / search / social /
    homepage / generic / off-host / internal) -> reject;
  * a strong-signal media/download URL -> high (cut 1 tier);
  * a URL matching the operator's per-host content pattern -> high (the same
    url_pattern mechanism discovery.py uses);
  * anything else clean-but-unsignalled -> review. FAIL-TO-REVIEW: a clean page
    with no content-pattern match is never auto-queued.

SAFETY the orchestrator inherits: the master off-switch (default =
automation_controller.off_switch_engaged) dominates -- engaged (or unreadable) ->
INERT, nothing enumerated or queued; host confinement (cut 2) means off-host URLs
never reach the queue; the AR4 per-run enqueue cap bounds how many are queued.
"""
from bulk_downloader import disco_triage as dt
from bulk_downloader import candidate_filter as cf


def _site_fetch(pages):
    def _fetch(url):
        return pages.get(url)
    return _fetch


def _links_html(*urls):
    return "".join(f'<a href="{u}">x</a>' for u in urls)


# ── triage_url: the per-URL tier decision ────────────────────────────────────

def test_triage_nav_url_is_reject():
    tier, _ = dt.triage_url("https://h.com/categories/all", page_host="h.com")
    assert tier == cf.TIER_REJECT


def test_triage_login_is_reject():
    tier, _ = dt.triage_url("https://h.com/login", page_host="h.com")
    assert tier == cf.TIER_REJECT


def test_triage_homepage_is_reject():
    tier, _ = dt.triage_url("https://h.com/", page_host="h.com")
    assert tier == cf.TIER_REJECT


def test_triage_direct_media_url_is_high():
    tier, _ = dt.triage_url("https://h.com/get/9/x.mp4", page_host="h.com")
    assert tier == cf.TIER_HIGH


def test_triage_clean_content_page_defaults_to_review():
    # A plausible content page with no media signal and no content pattern: it
    # survived the junk filters but must NOT be auto-queued -> review.
    tier, _ = dt.triage_url("https://h.com/v/123", page_host="h.com")
    assert tier == cf.TIER_REVIEW


def test_triage_content_pattern_promotes_to_high():
    tier, _ = dt.triage_url("https://h.com/v/123", page_host="h.com",
                            content_match_fn=lambda u: "/v/" in u)
    assert tier == cf.TIER_HIGH


def test_triage_returns_tier_and_numeric_score():
    tier, score = dt.triage_url("https://h.com/get/9/x.mp4", page_host="h.com")
    assert tier in (cf.TIER_HIGH, cf.TIER_REVIEW, cf.TIER_REJECT)
    assert isinstance(score, float) and 0.0 <= score <= 1.0


# ── run_discovery_triage: the orchestrator ───────────────────────────────────

def _run(pages, root="https://h.com/lib", **kw):
    enq = []
    kw.setdefault("enqueue_fn", lambda u: enq.append(u) or 1)
    out = dt.run_discovery_triage(root, fetch_fn=_site_fetch(pages), **kw)
    return out, enq


def test_off_switch_engaged_is_inert():
    fetched = []
    pages = {"https://h.com/lib": _links_html("https://h.com/get/1.mp4")}
    out = dt.run_discovery_triage(
        "https://h.com/lib",
        fetch_fn=lambda u: fetched.append(u) or pages.get(u),
        enqueue_fn=lambda u: (_ for _ in ()).throw(AssertionError("enqueued while off")),
        off_switch_fn=lambda: True)
    assert out["inert"] is True
    assert out["enqueued"] == 0
    assert fetched == []                      # nothing was even enumerated


def test_off_switch_unreadable_fails_safe_inert():
    def _boom():
        raise RuntimeError("cannot read off-switch")
    out = dt.run_discovery_triage(
        "https://h.com/lib", fetch_fn=lambda u: None,
        enqueue_fn=lambda u: 1, off_switch_fn=_boom)
    assert out["inert"] is True               # unreadable -> treated as engaged


def test_only_high_tier_is_enqueued():
    pages = {"https://h.com/lib": _links_html(
        "https://h.com/get/1.mp4",         # high (media)
        "https://h.com/v/1",               # review (clean content page)
        "https://h.com/categories/x",      # reject (nav)
        "https://h.com/login",             # reject (account)
    )}
    out, enq = _run(pages, off_switch_fn=lambda: False,
                    budget=dt.he.EnumBudget(max_depth=1, delay_s=0))
    assert enq == ["https://h.com/get/1.mp4"]
    assert "https://h.com/v/1" in out["review"]
    assert out["reject"] >= 2
    assert out["enqueued"] == 1


def test_review_tier_is_staged_not_queued():
    pages = {"https://h.com/lib": _links_html("https://h.com/v/1", "https://h.com/v/2")}
    out, enq = _run(pages, off_switch_fn=lambda: False,
                    budget=dt.he.EnumBudget(max_depth=1, delay_s=0))
    assert enq == []                          # nothing auto-queued
    assert set(out["review"]) == {"https://h.com/v/1", "https://h.com/v/2"}


def test_content_pattern_enables_auto_queue():
    pages = {"https://h.com/lib": _links_html(
        "https://h.com/v/1", "https://h.com/v/2", "https://h.com/about")}
    out, enq = _run(pages, off_switch_fn=lambda: False,
                    content_match_fn=lambda u: "/v/" in u,
                    budget=dt.he.EnumBudget(max_depth=1, delay_s=0))
    assert set(enq) == {"https://h.com/v/1", "https://h.com/v/2"}
    assert out["enqueued"] == 2


def test_ar4_per_run_enqueue_cap():
    # Many high candidates (content pattern matches all); max_enqueue caps them.
    links = [f"https://h.com/v/{i}" for i in range(20)]
    pages = {"https://h.com/lib": _links_html(*links)}
    out, enq = _run(pages, off_switch_fn=lambda: False,
                    content_match_fn=lambda u: "/v/" in u, max_enqueue=5,
                    budget=dt.he.EnumBudget(max_depth=1, delay_s=0))
    assert len(enq) == 5
    assert out["enqueued"] == 5
    assert out["capped"] is True


def test_off_host_never_enqueued():
    pages = {"https://h.com/lib": _links_html(
        "https://h.com/get/1.mp4", "https://evil.com/get/2.mp4")}
    out, enq = _run(pages, off_switch_fn=lambda: False,
                    budget=dt.he.EnumBudget(max_depth=1, delay_s=0))
    assert enq == ["https://h.com/get/1.mp4"]   # evil.com never reaches the queue
    assert all("evil.com" not in u for u in enq)


def test_enqueue_failure_is_isolated():
    pages = {"https://h.com/lib": _links_html(
        "https://h.com/get/1.mp4", "https://h.com/get/2.mp4")}
    seen_calls = []

    def _enqueue(u):
        seen_calls.append(u)
        if u.endswith("1.mp4"):
            raise RuntimeError("queue full")
        return 1
    out = dt.run_discovery_triage(
        "https://h.com/lib", fetch_fn=_site_fetch(pages), enqueue_fn=_enqueue,
        off_switch_fn=lambda: False,
        budget=dt.he.EnumBudget(max_depth=1, delay_s=0))
    assert len(seen_calls) == 2                # both attempted despite the throw
    assert out["enqueued"] == 1               # only the successful one counted


def test_persist_receives_the_run_record():
    recs = []
    pages = {"https://h.com/lib": _links_html("https://h.com/get/1.mp4")}
    dt.run_discovery_triage(
        "https://h.com/lib", fetch_fn=_site_fetch(pages), enqueue_fn=lambda u: 1,
        off_switch_fn=lambda: False, persist_fn=recs.append,
        budget=dt.he.EnumBudget(max_depth=1, delay_s=0))
    assert len(recs) == 1
    assert recs[0]["enqueued"] == 1 and recs[0]["host"] == "h.com"


def test_default_off_switch_delegates_to_master():
    # The default off-switch IS the automation master off-switch, so A-DISCO
    # inherits the single kill path rather than inventing its own.
    from bulk_downloader import automation_controller as ac
    assert dt._default_off_switch() == ac.off_switch_engaged()

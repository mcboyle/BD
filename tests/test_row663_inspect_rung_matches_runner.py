"""Row 663 — candidates-inspect must not name a different rung than the
runner saves.

MEASURED on origin/main 06a1ee2e: `dry_run.inspect_candidates` sorted its
accepted rows on ``(score, size)`` only. On the recorded wowgirls page the
heuristic scorer gives 3840x2160 and 7680x4320 the SAME score (130) and no
size at all, so the tie fell to document order and the inspector reported
4K while the runner saved 8K. `resolution_tier` — which the scorer already
computes and which `_score_all` itself sorts on — was dropped on the floor.

The inspector also took no site config, so `quality_preference` and
`min_resolution` (both of which the runner applies at runner.py:4800-4807)
could not be honoured at all.
"""
from __future__ import annotations

import pathlib

import pytest

BD_GATE_SCOPE = "repo-wide"

from bulk_downloader import dry_run  # noqa: E402
from bulk_downloader import heuristic_scoring as hs  # noqa: E402

_PAGE_URL = "https://www.wowgirls.com/video/1/"
_FIXTURE = (pathlib.Path(__file__).parent / "corpus"
            / "row663_wowgirls_rungs.html")

# The rung the runner actually saved for this recorded page (ffprobe-verified
# in the row's evidence). This is the page's own top rung, NOT a value this
# patch sets anywhere.
_RUNNER_SAVED_RUNG = 4320


@pytest.fixture(scope="module")
def html() -> str:
    return _FIXTURE.read_text()


def _accepted(payload):
    return [c for c in payload["candidates"] if c["accepted"]]


# ── the hazard is in the fixture ─────────────────────────────────────────

def test_fixture_contains_the_tie_that_is_the_defect(html):
    """PRECONDITION. The fixture must carry the divergence the row is about:
    five rungs, and the 4K and 8K rows scoring EQUALLY, so the sort key is
    the only thing that can separate them."""
    payload = dry_run.inspect_candidates(html, page_url=_PAGE_URL)
    acc = _accepted(payload)
    assert len(acc) == 5, f"expected exactly 5 accepted rungs, got {len(acc)}"

    by_rung = {}
    for c in acc:
        for label in ("7680x4320", "3840x2160", "1920x1080",
                      "1280x720", "960x540"):
            if label in c["url"]:
                by_rung[label] = c
    assert set(by_rung) == {"7680x4320", "3840x2160", "1920x1080",
                            "1280x720", "960x540"}, sorted(by_rung)

    tied = [by_rung["7680x4320"]["score"], by_rung["3840x2160"]["score"]]
    assert tied[0] == tied[1], (
        "the fixture no longer carries the tie this row is about: "
        f"8K scored {tied[0]}, 4K scored {tied[1]}")
    assert (by_rung["7680x4320"]["size"] or 0) == 0
    assert (by_rung["3840x2160"]["size"] or 0) == 0


# ── RED on the base tree ─────────────────────────────────────────────────

def test_inspect_winner_is_the_rung_the_runner_saves(html):
    """THE ROW'S ACCEPTANCE. Base tree reports 3840x2160; the runner saved
    7680x4320. Two-argument call, so this runs unchanged on the base."""
    payload = dry_run.inspect_candidates(html, page_url=_PAGE_URL)
    winner = payload["winner"]
    assert winner is not None
    assert "7680x4320" in winner["url"], (
        "inspect named a different rung than the runner saved: winner is "
        f"{winner['url']!r}, runner saved 7680x4320")


def test_every_row_carries_the_rung_it_names(html):
    """The reported rung is a pixel height derived from the page, not from
    the heuristic score."""
    payload = dry_run.inspect_candidates(html, page_url=_PAGE_URL)
    seen = {}
    for c in _accepted(payload):
        seen[c["rung"]] = seen.get(c["rung"], 0) + 1
    assert seen == {4320: 1, 2160: 1, 1080: 1, 720: 1, 540: 1}, seen


# ── quality_preference is honoured, in the runner's own order ────────────

def test_quality_preference_selects_a_lower_rung_over_the_top_one(html):
    """NEGATIVE CONTROL for "the fix just picks the biggest number". With
    quality_preference 1080 the inspector must report 1080, not 4320."""
    payload = dry_run.inspect_candidates(
        html, page_url=_PAGE_URL,
        site_config={"quality_preference": "1080", "min_resolution": 720})
    assert payload["rung_selection"] == "selected_work_unknown", payload["rung_reason"]
    assert payload["winner"]["rung"] == 1080, payload["winner"]["url"]


def test_quality_preference_order_falls_through_to_the_next_entry(html):
    """A preference naming a rung the page does not serve falls through to
    the next entry, as runner_integrity._apply_quality_preference does."""
    payload = dry_run.inspect_candidates(
        html, page_url=_PAGE_URL,
        site_config={"quality_preference": "1440,720", "min_resolution": 0})
    assert payload["winner"]["rung"] == 720, payload["winner"]["url"]


def test_quality_preference_best_takes_the_top_rung(html):
    payload = dry_run.inspect_candidates(
        html, page_url=_PAGE_URL,
        site_config={"quality_preference": "best", "min_resolution": 720})
    assert payload["winner"]["rung"] == _RUNNER_SAVED_RUNG


# ── min_resolution is honoured ───────────────────────────────────────────

def test_min_resolution_above_every_rung_refuses_rather_than_names_one(html):
    """The runner holds a below-floor best for review instead of saving it;
    the inspector must not report a winner the runner would refuse."""
    payload = dry_run.inspect_candidates(
        html, page_url=_PAGE_URL,
        site_config={"quality_preference": "best", "min_resolution": 4321})
    assert payload["winner"] is None
    assert payload["rung_selection"] == "refused", payload["rung_selection"]
    assert "min_resolution" in payload["rung_reason"]
    assert payload["safe_candidate_available"] is False


def test_min_resolution_below_the_chosen_rung_does_not_refuse(html):
    """NEGATIVE CONTROL for the refusal: it must not fire for every call."""
    payload = dry_run.inspect_candidates(
        html, page_url=_PAGE_URL,
        site_config={"quality_preference": "best", "min_resolution": 720})
    assert payload["rung_selection"] == "selected_work_unknown"
    assert payload["winner"] is not None


def test_min_resolution_refuses_the_rung_the_preference_chose_not_the_top(html):
    """The floor is applied to the CHOSEN rung, exactly as the runner does
    (quality preference first, then the gate) — not to the page's best."""
    payload = dry_run.inspect_candidates(
        html, page_url=_PAGE_URL,
        site_config={"quality_preference": "540", "min_resolution": 720})
    assert payload["winner"] is None, payload["winner"]
    assert payload["rung_selection"] == "refused"


# ── the UNKNOWN branch this patch adds ───────────────────────────────────

def test_no_site_config_reports_UNKNOWN_rather_than_claiming_a_rung(html):
    """CLAUDE.md A2: an unavailable measurement is never permission. Without
    a site config the inspector does not know quality_preference or
    min_resolution, and must say so rather than claim the rung is settled."""
    payload = dry_run.inspect_candidates(html, page_url=_PAGE_URL)
    assert payload["rung_selection"] == "UNKNOWN"
    assert "min_resolution" in payload["rung_reason"]


def test_empty_accepted_set_reports_none_not_a_winner():
    """An empty iterable must not manufacture a selected verdict."""
    payload = dry_run.inspect_candidates(
        "<html><body><p>nothing here</p></body></html>",
        page_url=_PAGE_URL, site_config={"quality_preference": "best"})
    assert payload["winner"] is None
    assert payload["rung_selection"] == "none"


# ── the height table is a mapping over the shipped buckets ───────────────

def test_every_resolution_tier_bucket_has_a_pixel_height():
    """DENOMINATOR: the population is every bucket RESOLUTION_TIERS ships,
    derived from the module at check time, not from this test's own list."""
    buckets = {tier for _pat, tier, _label in hs.RESOLUTION_TIERS}
    assert len(buckets) >= 10, len(buckets)
    missing = sorted(b for b in buckets if hs.tier_pixel_height(b) == 0)
    assert missing == [], f"buckets with no pixel height: {missing}"


def test_tier_pixel_height_is_zero_for_an_unknown_bucket():
    assert hs.tier_pixel_height(0) == 0
    assert hs.tier_pixel_height(999) == 0
    assert hs.tier_pixel_height(None) == 0


# ── the second seam: template_dry_run passes the config through ──────────

def test_template_dry_run_passes_the_site_config_to_the_inspector(html):
    """SEAM 2. dry_run.py:222 is the other in-tree caller; a config that
    stops at the wrapper leaves the rung unknowable through this route."""
    out = dry_run.template_dry_run(
        _PAGE_URL, html=html,
        site_config={"quality_preference": "1080", "min_resolution": 720})
    view = out["candidate_classification"]
    assert view["rung_selection"] == "selected_work_unknown", view["rung_reason"]
    assert view["winner"]["rung"] == 1080


def test_template_dry_run_without_a_config_reports_UNKNOWN(html):
    out = dry_run.template_dry_run(_PAGE_URL, html=html)
    assert out["candidate_classification"]["rung_selection"] == "UNKNOWN"


# ── the runner's same-work subset, mirrored (shape lens b2, second pass) ──
#
# The first version of this cut reimplemented the runner's preference walk and
# omitted the FIRST thing the runner does: subset to same-work candidates
# (runner_integrity._apply_quality_preference, row 388).  On a scene page
# carrying a higher-tier RELATED CARD the two implementations then disagreed,
# and the inspector still answered "selected".

from bulk_downloader.runner_integrity import IntegrityMixin  # noqa: E402

_OWN_RUNG, _RELATED_RUNG = 1080, 2160


def _two_work_rows():
    """Ranked as inspect_candidates ranks: the related card OUTRANKS the page's
    own scene on resolution_tier, so only the subset can change the answer."""
    return [
        {"url": "related", "score": 100, "resolution_tier": 60,
         "rung": _RELATED_RUNG, "size": 0, "work": 0},
        {"url": "own", "score": 100, "resolution_tier": 40,
         "rung": _OWN_RUNG, "size": 0, "work": 1},
    ]


def _two_work_candidates():
    """The same page in the RUNNER's candidate shape, where score is a height."""
    return {"locator": "seed", "_all_candidates": [
        {"locator": "related", "score": _RELATED_RUNG, "work": 0},
        {"locator": "own", "score": _OWN_RUNG, "work": 1},
    ]}


def test_the_fixture_carries_a_higher_tier_foreign_candidate():
    """PRECONDITION for the drift guard: the related card must OUTRANK the
    page's own scene, or the subset could not change any answer."""
    rows = _two_work_rows()
    assert rows[0]["rung"] > rows[1]["rung"]
    assert rows[0]["work"] == 0 and rows[1]["work"] == 1
    assert len([r for r in rows if r["work"] > 0]) == 1


def test_inspector_and_runner_choose_the_same_candidate_on_a_related_card_page():
    """DRIFT GUARD, required by the shape lens. Drives BOTH real implementations
    over the same page. Deleting the same-work subset from EITHER side makes
    them disagree, so this fails whichever copy drifts."""
    runner_choice = IntegrityMixin._apply_quality_preference(
        None, _two_work_candidates(), "best")
    decision = dry_run._rung_decision(
        _two_work_rows(), {"quality_preference": "best"})

    assert runner_choice["locator"] == "own", (
        "the runner's same-work subset is gone: it chose %r"
        % (runner_choice.get("locator"),))
    assert decision["winner"]["url"] == "own", (
        "the inspector's same-work subset is gone: it chose %r"
        % (decision["winner"] or {}).get("url"))
    assert runner_choice["score"] == decision["winner"]["rung"] == _OWN_RUNG


def test_the_subset_also_holds_for_a_numeric_preference():
    """Not just 'best': a 2160 preference must still refuse the foreign card
    when a same-work candidate exists, exactly as the runner does."""
    decision = dry_run._rung_decision(
        _two_work_rows(), {"quality_preference": "2160,1080"})
    assert decision["winner"]["url"] == "own"
    assert decision["winner"]["rung"] == _OWN_RUNG


def test_work_affinity_known_reports_selected_without_the_caveat():
    """The 'selected' state is REACHABLE: it is what a work-bearing page gets."""
    decision = dry_run._rung_decision(
        _two_work_rows(), {"quality_preference": "best"})
    assert decision["rung_selection"] == "selected"
    assert "work affinity UNKNOWN" not in decision["rung_reason"]


def test_a_page_with_no_work_signal_qualifies_its_claim(html):
    """THE ESCAPE THE SHAPE LENS FOUND. inspect_candidates supplies no work
    signal on any real page, so the rung is still named but the claim that it
    is the rung the runner saves is qualified rather than asserted."""
    payload = dry_run.inspect_candidates(
        html, page_url=_PAGE_URL,
        site_config={"quality_preference": "best", "min_resolution": 720})
    assert payload["rung_selection"] == "selected_work_unknown"
    assert "work affinity UNKNOWN" in payload["rung_reason"]
    assert payload["winner"]["rung"] == _RUNNER_SAVED_RUNG
    # the precondition that makes the qualification honest
    assert all("work" not in c for c in payload["candidates"])


def test_the_subset_is_a_no_op_when_no_candidate_proves_affinity():
    """NEGATIVE CONTROL: the subset must not empty the list. When nothing is
    same-work the runner keeps every candidate, and so must the copy."""
    rows = [dict(r, work=0) for r in _two_work_rows()]
    assert dry_run._same_work_subset(rows) == rows
    decision = dry_run._rung_decision(rows, {"quality_preference": "best"})
    assert decision["winner"]["url"] == "related"
    assert decision["rung_selection"] == "selected"

"""Item D (register 15.76): the download row pool is screened through the
repo's OWN honeypot evidence before ``row_selectors`` are emitted.

``login_extract._login_is_honeypot`` has screened the LOGIN side since it was
written: inline style (display:none / left:-10000 / left:-9999 / 1px box /
opacity:0 / visibility:hidden), tabindex=-1, aria-hidden=true, the same inline
styles on up to 3 ancestors, and a name/id containing "website"/"honeypot".
The DOWNLOAD path never called it: ``candidates._build_template`` emitted
``row_selectors`` from the top 3 row-pool candidates with zero visibility
screening. Measured on pristine source (this file's RED run): a
``display:none`` decoy anchor OUTRANKED the real download link (score 105 vs
85), took ``row_selectors[0]``, and drove ``min_resolution`` to 2160.

The screen must live here and not in template_normalize: the normalizer sees
only selector STRINGS, while the emission site still holds each candidate's
DOM element (``_el``, kept by ``_walk_for_candidates`` for selector
generalization). Pattern-matching selector strings would be a different and
weaker check wearing the same name.

THE SCREEN'S LIMIT, pinned below so nobody reads it as complete: it sees
INLINE evidence only. A decoy hidden by a class or a stylesheet rule
(``class="hidden"``, a CSS file's display:none) is INVISIBLE to it and
survives. A candidate that no longer carries its DOM element cannot be
screened and also survives.

Both directions are proven: a honeypot row must be DROPPED and a real row
must SURVIVE. A screen that drops everything passes the first half and
destroys the extractor.
"""

from __future__ import annotations

import pytest

from bulk_downloader.template_extractor import extract_from_html


def _page(body: str) -> str:
    return f"<html><body><div class='video-tools'>{body}</div></body></html>"


# A visible, well-evidenced download row. Present in every fixture so the
# survive-direction is asserted alongside every drop-direction.
REAL_ROW = (
    "<a id='dlreal' class='download-btn' "
    "href='/files/video_1080p.mp4'>Download 1080p MP4</a>"
)


def _extract(body: str):
    r = extract_from_html(_page(body), page_url="https://example.com/video/1")
    assert r["ok"], r.get("error")
    return r


# ── drop direction ──────────────────────────────────────────────────


def test_inline_hidden_decoy_is_dropped_from_row_selectors():
    r = _extract(
        REAL_ROW
        + "<a id='dltrap' class='trap-link' style='display:none' "
          "href='/files/decoy_2160p.mp4'>Download 4K MP4</a>"
    )
    rows = r["template"]["row_selectors"]
    assert "#dltrap" not in rows, (
        "an inline display:none decoy was emitted as a download row: "
        f"{rows}"
    )


def test_real_row_survives_the_screen():
    # The other direction: a screen that drops everything would pass the
    # test above and destroy the extractor. The real row must still be
    # emitted, from the same fixture.
    r = _extract(
        REAL_ROW
        + "<a id='dltrap' class='trap-link' style='display:none' "
          "href='/files/decoy_2160p.mp4'>Download 4K MP4</a>"
    )
    rows = r["template"]["row_selectors"]
    assert "#dlreal" in rows, f"the real visible row was dropped: {rows}"


@pytest.mark.parametrize(
    "decoy_attrs",
    [
        "style='display:none'",
        "style='position:absolute;left:-10000px'",
        "style='position:absolute;left:-9999px'",
        "style='opacity:0'",
        "style='visibility:hidden'",
        "style='width:1px;height:1px;overflow:hidden'",
        "tabindex='-1'",
        "aria-hidden='true'",
        "id='honeypot_dl'",
    ],
    ids=[
        "display_none",
        "offscreen_10000",
        "offscreen_9999",
        "opacity_zero",
        "visibility_hidden",
        "one_px_box",
        "tabindex_minus_one",
        "aria_hidden",
        "honeypot_ident",
    ],
)
def test_every_inline_evidence_class_drops_the_decoy(decoy_attrs):
    # The runtime learned-row path (detect.py, v3.66.247) skips rows that
    # Playwright reports non-visible -- but that only covers display:none /
    # visibility:hidden. The offscreen, opacity:0, 1px, tabindex and
    # aria-hidden classes all read as VISIBLE to Playwright and would be
    # clicked or harvested, so the extraction-time screen is load-bearing
    # for them, not belt-and-braces.
    ident = "id='dltrap' " if "id=" not in decoy_attrs else ""
    r = _extract(
        REAL_ROW
        + f"<a {ident}class='trap-link' {decoy_attrs} "
          "href='/files/decoy_2160p.mp4'>Download 4K MP4</a>"
    )
    rows = r["template"]["row_selectors"]
    trap_sel = "#dltrap" if ident else "#honeypot_dl"
    assert trap_sel not in rows, f"{decoy_attrs}: decoy emitted: {rows}"
    assert "#dlreal" in rows, f"{decoy_attrs}: real row dropped: {rows}"


def test_ancestor_inline_hiding_drops_the_decoy():
    r = _extract(
        REAL_ROW
        + "<div style='display:none'>"
          "<a id='dltrap' class='trap-link' "
          "href='/files/decoy_2160p.mp4'>Download 4K MP4</a></div>"
    )
    rows = r["template"]["row_selectors"]
    assert "#dltrap" not in rows, f"ancestor-hidden decoy emitted: {rows}"
    assert "#dlreal" in rows


# ── the decoy must not steer the rest of the template either ────────


def test_hidden_decoy_no_longer_drives_min_resolution_or_url_attribute():
    # The decoy is the only 4K-tier candidate and the only data-href one.
    # On pristine source it was row_pool[0], so min_resolution read 2160
    # and url_attribute read data-href -- template fields steered by an
    # element no real user can see.
    r = _extract(
        REAL_ROW
        + "<a id='dltrap' class='trap-link' style='display:none' "
          "data-href='/files/decoy_2160p.mp4'>Download 4K MP4</a>"
    )
    t = r["template"]
    assert "#dltrap" not in t["row_selectors"]
    assert t["min_resolution"] == 1080, (
        "a hidden decoy set min_resolution: %r" % t["min_resolution"]
    )
    assert t["url_attribute"] == "href", (
        "a hidden decoy set url_attribute: %r" % t["url_attribute"]
    )


def test_all_rows_hidden_yields_empty_rows_and_review_required():
    r = _extract(
        "<a id='dltrap1' class='trap-link' style='display:none' "
        "href='/files/decoy1_2160p.mp4'>Download 4K MP4</a>"
        "<a id='dltrap2' class='trap-link' style='visibility:hidden' "
        "href='/files/decoy2_1080p.mp4'>Download 1080p MP4</a>"
    )
    t = r["template"]
    assert t["row_selectors"] == [], (
        "hidden-only candidates were emitted as rows: %r" % t["row_selectors"]
    )
    assert t["review_required"] is True
    # The empty-pool return path must carry the drop record too, so the
    # operator sees WHY the row list is empty and not only "no candidates
    # found". Without this, the all-hidden case -- the one where the
    # screen did the most work -- would be the one case it goes silent.
    joined = " ".join(r["warnings"]).lower()
    assert "honeypot" in joined, r["warnings"]


# ── the drop is surfaced, with its limit stated ─────────────────────


def test_warning_names_the_drop_and_states_the_inline_only_limit():
    r = _extract(
        REAL_ROW
        + "<a id='dltrap' class='trap-link' style='display:none' "
          "href='/files/decoy_2160p.mp4'>Download 4K MP4</a>"
    )
    joined = " ".join(r["warnings"]).lower()
    assert "honeypot" in joined, (
        "dropping a candidate silently is the unreachable-audit-trail "
        "defect; warnings were: %r" % r["warnings"]
    )
    assert "inline" in joined, (
        "the warning must state the screen's limit (inline evidence only) "
        "so nobody reads it as a complete visibility check: %r"
        % r["warnings"]
    )


# ── the limit itself is pinned ──────────────────────────────────────


def test_limit_class_hidden_decoy_survives_the_screen():
    # DELIBERATE: this decoy is hidden by CLASS, which only a stylesheet
    # gives meaning to. The screen reads INLINE evidence only, so this
    # decoy SURVIVES -- pinned here so the limit stays visible. If this
    # test starts failing because the screen learned to read classes or
    # stylesheets, that is a DIFFERENT check: state its evidence and
    # rewrite this pin deliberately, don't patch it green.
    r = _extract(
        REAL_ROW
        + "<a id='dlhid' class='hidden is-hidden' "
          "href='/files/sneaky_2160p.mp4'>Download 4K MP4</a>"
    )
    rows = r["template"]["row_selectors"]
    assert "#dlhid" in rows, (
        "the class-hidden decoy was dropped -- the screen claims more "
        "than inline evidence now; re-state its limit: %r" % rows
    )


def test_candidate_without_dom_element_is_not_screened():
    # A candidate that no longer carries ``_el`` (synthetic callers of
    # _build_template) cannot be screened; the screen must pass it
    # through rather than guess. Unknown is not hidden. Imported lazily
    # so the behavioral tests above show their own RED on a tree where
    # the helper does not exist yet, instead of one collection error.
    from bulk_downloader.template_extractor_impl.candidates import (
        _row_is_inline_hidden,
    )

    assert _row_is_inline_hidden({"tag": "a", "score": 99}) is False
    assert _row_is_inline_hidden({"tag": "a", "_el": None}) is False

    # Same principle for a screen that RAISES mid-question: fail open,
    # keep the row. The screen drops on positive evidence, never on
    # ignorance -- the inverse (drop on any error) is the over-sensitive
    # direction that destroys the extractor to look safe.
    class _Raises:
        def get(self, *_a, **_k):
            raise RuntimeError("boom")

    assert _row_is_inline_hidden({"tag": "a", "_el": _Raises()}) is False

"""Row 821: the row-722 verified template family keeps 4K-first; 5K/8K stay opt-in.

The standing rule (tests/test_v3_43_54_resolution.py, O823) is that a site
template does not auto-prefer the 5K/8K tiers -- they are served from CDNs that
time out or 502 for some accounts -- and a user who wants them appends them via
the site edit form. v3.66.1563 (abecef79) landed every row-722 verified template
with quality_preference "4320,3160,2880,2160,1440,1080,720" (8K first), which
reverted 11 templates that already carried "2160,1080,720" and turned the
wowgirls-only standing gate RED. The standing gate pins ONE template; this one
pins the whole family so the class cannot regress a template at a time.

Denominator: every template in bulk_downloader.site_templates.TEMPLATES whose
description starts with the row-722 verified stamp, measured from the loaded
package (not a handed list) and asserted nonzero.
"""
from __future__ import annotations

import pytest

from bulk_downloader.site_templates import TEMPLATES

BD_GATE_SCOPE = "repo-wide"

_STAMP = "VERIFIED 2026-09-15 (row 722"
# Tiers above 4K: the flaky-CDN class named by test_wow_template_prefers_4k
# (8K 4320, 6K 3240/3160, 5K 3132, 5K-cinema 2880) -- never a default.
_OPT_IN_TIERS = frozenset({4320, 3240, 3160, 3132, 2880})
_RELIABLE_TOP = 2160


def _family() -> list[dict]:
    fam = [t for t in TEMPLATES if t.get("description", "").startswith(_STAMP)]
    assert fam, "no row-722 verified template found: the denominator is empty"
    return fam


def _tiers(qpref: str) -> list[int]:
    return [int(p.strip()) for p in qpref.split(",") if p.strip().isdigit()]


def test_the_family_is_measured_from_the_package():
    ids = sorted(t["id"] for t in _family())
    assert len(ids) == len(set(ids)), f"duplicate template id in the family: {ids}"
    # The family is the templates row 722 / 722s verified; a template that gains
    # or loses the stamp changes the denominator on purpose, so the count is pinned.
    assert len(ids) == 23, f"row-722 family changed size ({len(ids)}): {ids}"


def test_no_family_template_auto_prefers_a_5k_or_8k_tier():
    offenders = {}
    checked = 0
    for t in _family():
        qpref = (t.get("config_defaults") or {}).get("quality_preference")
        if not qpref:
            continue
        checked += 1
        bad = sorted(set(_tiers(qpref)) & _OPT_IN_TIERS)
        if bad:
            offenders[t["id"]] = (qpref, bad)
    assert checked, "no family template carries a quality_preference"
    assert not offenders, (
        f"{len(offenders)} row-722 template(s) auto-prefer a flaky 5K/8K tier "
        f"(opt-in per test_v3_43_54_resolution / O823): {offenders}")


def test_family_ladders_lead_with_the_reliable_top_tier():
    """The ladder starts at 4K (or lower where the site never served 4K) and
    descends: 2160 -> 1080 -> 720. An ascending or shuffled ladder would make
    the worker chase a tier the site never offered before falling back."""
    wrong = {}
    for t in _family():
        qpref = (t.get("config_defaults") or {}).get("quality_preference")
        if not qpref:
            continue
        tiers = _tiers(qpref)
        if not tiers or tiers[0] > _RELIABLE_TOP or tiers != sorted(tiers, reverse=True):
            wrong[t["id"]] = qpref
    assert not wrong, f"ladder does not start at <= {_RELIABLE_TOP} and descend: {wrong}"


@pytest.mark.parametrize("qpref", ["4320,3160,2880,2160,1440,1080,720", "3132,2160", "720,1080"])
def test_the_probe_rejects_a_known_bad_ladder(qpref):
    """Positive control: the two predicates above can say NO."""
    tiers = _tiers(qpref)
    flaky = set(tiers) & _OPT_IN_TIERS
    descending = tiers[0] <= _RELIABLE_TOP and tiers == sorted(tiers, reverse=True)
    assert flaky or not descending

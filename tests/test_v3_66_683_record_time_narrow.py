"""v3.66.683 (F1 / CAP-1) — record-time selector auto-narrow.

`api_prune_selectors` narrows a site's learned selectors by RUNTIME
miss-ratio (needs accumulated hit/miss data). The complementary gap this
cut closes: at RECORD time a fresh detection produces a *spray* of
candidate selectors per role with no runtime data yet. `narrow_selectors`
/ `narrow_detected_block` reduce that spray to a minimal, stable set using
record-time signals only (structural dedupe + non-fragility), so a fresh
site starts lean instead of carrying redundant/fragile candidates.

Pure + offline; the wiring test uses the fresh_app harness to prove
`_apply_detected_selectors` narrows before merge (default-on, opt-out via
the `record_time_narrow` site-cfg key), and that the row_selectors <->
url_attribute parallel-list alignment (merge_learned v3.43.10) is kept.
"""
import copy

import pytest

_ALLOWED_REASONS = {"duplicate_shape", "fragile", "over_cap"}


# ── narrow_selectors: the pure candidate-list narrower ──────────────

def test_narrow_dedupes_same_shape_candidates():
    from bulk_downloader import auto_detect
    # two hashed-class variants collapse to one structural shape
    kept, dropped = auto_detect.narrow_selectors(["div.aXbYz", "div.kPmRn"])
    assert kept == ["div.aXbYz"]           # first occurrence wins
    assert len(dropped) == 1
    assert dropped[0]["selector"] == "div.kPmRn"
    assert dropped[0]["reason"] == "duplicate_shape"


def test_narrow_prefers_stable_over_fragile():
    from bulk_downloader import auto_detect
    kept, dropped = auto_detect.narrow_selectors(["#dl-main", "button.xQvWz"])
    assert kept == ["#dl-main"]             # stable id kept
    assert [d["selector"] for d in dropped] == ["button.xQvWz"]
    assert dropped[0]["reason"] == "fragile"


def test_narrow_caps_to_max_keep():
    from bulk_downloader import auto_detect
    kept, dropped = auto_detect.narrow_selectors(
        ["#a", "#b", "#c", "#d"], max_keep=2)
    assert kept == ["#a", "#b"]
    assert [d["reason"] for d in dropped] == ["over_cap", "over_cap"]


def test_narrow_is_non_destructive_when_all_fragile():
    from bulk_downloader import auto_detect
    # only fragile candidates -> keep the single best rather than empty
    kept, dropped = auto_detect.narrow_selectors(["div.aXbYz"])
    assert kept == ["div.aXbYz"]
    assert dropped == []


def test_narrow_all_fragile_keeps_first_still_dedupes():
    from bulk_downloader import auto_detect
    kept, dropped = auto_detect.narrow_selectors(
        ["li.qRtVw", "span.zBnMk"])   # distinct shapes, both fragile
    assert kept == ["li.qRtVw", "span.zBnMk"]
    assert dropped == []


def test_narrow_empty_input():
    from bulk_downloader import auto_detect
    assert auto_detect.narrow_selectors([]) == ([], [])


def test_narrow_every_dropped_has_valid_reason():
    from bulk_downloader import auto_detect
    _, dropped = auto_detect.narrow_selectors(
        ["#keep", "button.xQvWz", "div.pMqRs", "#a", "#b", "#c"], max_keep=2)
    for d in dropped:
        assert set(d.keys()) == {"selector", "reason"}
        assert d["reason"] in _ALLOWED_REASONS


# ── narrow_detected_block: block-level, alignment-aware ─────────────

def test_block_narrows_list_roles_leaves_scalars():
    from bulk_downloader import auto_detect
    block = {
        "row_selectors": ["#dl", "div.xQvWz"],
        "trigger_selectors": ["#go", "a.kPmRn"],
        "url_attribute": "data-src",       # scalar -> untouched
        "tier_label": "1080p",             # scalar -> untouched
    }
    new_block, report = auto_detect.narrow_detected_block(block)
    assert new_block["row_selectors"] == ["#dl"]
    assert new_block["trigger_selectors"] == ["#go"]
    assert new_block["url_attribute"] == "data-src"
    assert new_block["tier_label"] == "1080p"
    assert "row_selectors" in report and "trigger_selectors" in report


def test_block_does_not_mutate_input():
    from bulk_downloader import auto_detect
    block = {"row_selectors": ["#dl", "div.xQvWz"], "url_attribute": "data-src"}
    snapshot = copy.deepcopy(block)
    auto_detect.narrow_detected_block(block)
    assert block == snapshot


def test_block_keeps_parallel_url_attribute_aligned():
    # merge_learned v3.43.10: url_attribute in parallel-list form must stay
    # index-aligned with row_selectors. Narrowing rows must drop the aligned
    # url_attribute slots in lockstep, never desynchronize them.
    from bulk_downloader import auto_detect
    block = {
        "row_selectors": ["#dl", "div.xQvWz", "span.pMqRs"],
        "url_attribute": ["data-a", "data-b", "data-c"],
    }
    new_block, _ = auto_detect.narrow_detected_block(block)
    assert new_block["row_selectors"] == ["#dl"]
    # aligned slot for the surviving row only
    assert new_block["url_attribute"] == ["data-a"]
    assert len(new_block["row_selectors"]) == len(new_block["url_attribute"])


# ── wiring: _apply_detected_selectors narrows before merge ──────────

class _StubRunner:
    def update_config(self, cfg):
        self.cfg = cfg


def test_apply_detected_selectors_narrows_by_default(fresh_app):
    from bulk_downloader import app as app_mod
    sid = "s-narrow"
    app_mod.runners[sid] = _StubRunner()
    app_mod.s_cfg[sid] = {}
    ok, _msg = app_mod._apply_detected_selectors(
        sid, download_block={"row_selectors": ["#dl", "div.xQvWz"],
                             "url_attribute": "data-src"})
    assert ok is True
    row = app_mod.s_cfg[sid]["learned"]["download"]["row_selectors"]
    assert "div.xQvWz" not in row          # fragile duplicate narrowed away
    assert "#dl" in row
    # the dropped report is recorded for audit/reversibility
    assert app_mod.s_cfg[sid].get("_record_time_narrow", {}).get("download")


def test_apply_detected_selectors_narrow_opt_out(fresh_app):
    from bulk_downloader import app as app_mod
    sid = "s-nonarrow"
    app_mod.runners[sid] = _StubRunner()
    app_mod.s_cfg[sid] = {"record_time_narrow": False}
    app_mod._apply_detected_selectors(
        sid, download_block={"row_selectors": ["#dl", "div.xQvWz"],
                             "url_attribute": "data-src"})
    row = app_mod.s_cfg[sid]["learned"]["download"]["row_selectors"]
    assert "div.xQvWz" in row              # opt-out keeps the full spray

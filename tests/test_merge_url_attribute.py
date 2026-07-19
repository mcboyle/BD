"""Tests for merge_learned's url_attribute handling (v3.42.4).

url_attribute can be str/list/dict and must stay aligned with row_selectors.
Merging is special-cased to never break that alignment.
"""
from bulk_downloader.learn import merge_learned


def test_merge_str_into_empty():
    """First teach on a fresh site: just stores the string."""
    cfg = {}
    merge_learned(cfg, {"url_attribute": "href"}, kind="download")
    assert cfg["learned"]["download"]["url_attribute"] == "href"


def test_merge_str_overwrites_existing_str():
    """Successive teach commits with str url_attribute → most recent wins."""
    cfg = {"learned": {"download": {"url_attribute": "data-href"}}}
    merge_learned(cfg, {"url_attribute": "href"}, kind="download")
    assert cfg["learned"]["download"]["url_attribute"] == "href"


def test_template_list_overwrites_legacy_string():
    """Applying a template with parallel-slot list url_attribute on top of
    a legacy single-string config: list wins as a unit."""
    cfg = {"learned": {"download": {"url_attribute": "href"}}}
    merge_learned(cfg, {"url_attribute": ["data-href", "href", ""]}, kind="download")
    assert cfg["learned"]["download"]["url_attribute"] == ["data-href", "href", ""]


def test_teach_string_extends_existing_list():
    """User teaches one new row_selector on top of a template that ships
    a list. The new url_attribute must be PREPENDED to the list so it
    stays aligned with the prepended row_selector. This is the critical
    correctness case — overwriting the list with a string would break
    every other variant the template covered."""
    cfg = {"learned": {"download": {
        "url_attribute": ["data-href", "href"],
        "row_selectors": ["div.dl[data-href]", "a.legacy[href]"],
    }}}
    merge_learned(cfg, {
        "url_attribute": "data-download",
        "row_selectors": ["a.new[data-download]"],
    }, kind="download")
    # url_attribute should now be ["data-download", "data-href", "href"]
    # — matching the row_selectors list which is also prepended.
    ua = cfg["learned"]["download"]["url_attribute"]
    rs = cfg["learned"]["download"]["row_selectors"]
    assert ua == ["data-download", "data-href", "href"]
    assert rs[0] == "a.new[data-download]"
    # Length match is the invariant
    assert len(ua) == len(rs)


def test_merge_dict_shallow_merges():
    """Two dict shapes get merged shallowly (new keys win on overlap)."""
    cfg = {"learned": {"download": {"url_attribute": {"a": "href"}}}}
    merge_learned(cfg, {"url_attribute": {"b": "data-href"}}, kind="download")
    assert cfg["learned"]["download"]["url_attribute"] == {
        "a": "href", "b": "data-href",
    }


def test_merge_dict_new_value_wins_on_overlap():
    cfg = {"learned": {"download": {"url_attribute": {"a": "href"}}}}
    merge_learned(cfg, {"url_attribute": {"a": "data-href"}}, kind="download")
    assert cfg["learned"]["download"]["url_attribute"] == {"a": "data-href"}


def test_row_selectors_still_dedupe_normally():
    """url_attribute is special; row_selectors uses the normal merge path."""
    cfg = {"learned": {"download": {"row_selectors": ["a", "b"]}}}
    merge_learned(cfg, {"row_selectors": ["a", "c"]}, kind="download")
    # New row 'a' prepends and dedupes; 'c' is new; existing 'b' kept.
    assert cfg["learned"]["download"]["row_selectors"] == ["a", "c", "b"]


def test_extended_list_capped_at_max():
    """If the merged list would exceed MAX_PER_ROLE (5), cap it."""
    cfg = {"learned": {"download": {
        "url_attribute": ["a1", "a2", "a3", "a4", "a5"],
        "row_selectors": ["s1", "s2", "s3", "s4", "s5"],
    }}}
    merge_learned(cfg, {
        "url_attribute": "a6",
        "row_selectors": ["s6"],
    }, kind="download")
    # Both lists are capped at 5
    ua = cfg["learned"]["download"]["url_attribute"]
    rs = cfg["learned"]["download"]["row_selectors"]
    assert len(ua) == 5
    assert len(rs) == 5
    assert ua[0] == "a6"  # new entry prepended
    assert rs[0] == "s6"


# ── v3.43.10: regression tests ─────────────────────────────────────

def test_teach_row_only_prepends_empty_url_attr_slot():
    """v3.43.10 bug:
    user applies wowgirls_network template (4 selectors with parallel
    url_attribute list), then commits a teach with only a row_selector
    (no url_attribute string). Before the fix, row_selectors grew to 5
    but url_attribute stayed at 4 — INDEX MISMATCH caused the resolver
    to read the wrong attribute and fall through to click-and-capture.

    The symptom seen by the user: file downloaded via Chrome's download
    bar to %USERPROFILE%\\Downloads instead of via httpx to the
    configured download_dir.
    """
    cfg = {"learned": {"download": {
        "row_selectors": [
            "div.download-button[data-href]",
            "a.ct_dl_button[href]",
            "a.download__item[data-download]",
            "#exDownloadMenu .exp-menu-item",
        ],
        "url_attribute": ["data-href", "href", "data-download", ""],
    }}}
    # Teach commits a NEW row_selector but no url_attribute change
    merge_learned(cfg, {
        "row_selectors": ["a:has-text('7680 x 4320')"],
    }, kind="download")
    block = cfg["learned"]["download"]
    # row_selectors has the new one prepended (5 total)
    assert block["row_selectors"][0] == "a:has-text('7680 x 4320')"
    assert len(block["row_selectors"]) == 5
    # url_attribute must have grown to match, with an empty slot at
    # position 0 for the new selector (= click-and-capture).
    # Critically: the ORIGINAL slots at positions 1-4 must be preserved.
    assert block["url_attribute"] == ["", "data-href", "href", "data-download", ""]
    assert len(block["url_attribute"]) == len(block["row_selectors"])

    # And the index lookup for the user's matched selector now works:
    from bulk_downloader.runner import resolve_url_attribute
    result = resolve_url_attribute(
        block["url_attribute"], block["row_selectors"],
        "a.ct_dl_button[href]",
    )
    # Before fix: would return "data-download" (WRONG — that's for
    # the download__item selector). After fix: returns "href".
    assert result == "href", \
        f"resolver returned {result!r} — alignment is broken"


def test_invariant_pads_when_url_attribute_short():
    """If url_attribute is shorter than row_selectors for any reason
    (corrupt config, partial migration, etc.), the final invariant
    check pads it with empty strings to match."""
    cfg = {"learned": {"download": {
        "row_selectors": ["a", "b", "c"],
        "url_attribute": ["href"],  # only one slot, three selectors
    }}}
    # Trigger merge to apply the invariant check
    merge_learned(cfg, {"row_selectors": ["d"]}, kind="download")
    block = cfg["learned"]["download"]
    assert len(block["url_attribute"]) == len(block["row_selectors"])


def test_invariant_truncates_when_url_attribute_long():
    """If url_attribute is longer than row_selectors, truncate to match.
    Wouldn't normally happen but defense in depth."""
    cfg = {"learned": {"download": {
        "row_selectors": ["a"],
        "url_attribute": ["href", "data-href", "src"],
    }}}
    merge_learned(cfg, {"row_selectors": ["b"]}, kind="download")
    block = cfg["learned"]["download"]
    assert len(block["url_attribute"]) == len(block["row_selectors"])

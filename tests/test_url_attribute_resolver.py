"""Regression tests for resolve_url_attribute (v3.42.4).

Background: a single site config now supports multiple HTML variants
via per-selector url_attribute. Examples:

  Site uses two variants — modern uses data-href, legacy uses href:
    row_selectors:  ["div.dl-button[data-href]", "a.ct_dl_button[href]"]
    url_attribute:  ["data-href", "href"]      # parallel list shape

  Or equivalently via dict:
    url_attribute:  {"div.dl-button[data-href]": "data-href",
                     "a.ct_dl_button[href]":    "href"}

  Or legacy single-attribute form (unchanged):
    url_attribute:  "href"

When the matched selector points to a click-and-capture target (no
URL attribute to read, worker should click and let Playwright catch
the file), the slot is empty string.
"""
from bulk_downloader.runner import resolve_url_attribute


# ── Legacy string form: unchanged behavior ────────────────────────────────
def test_string_form_returns_string():
    assert resolve_url_attribute("href", ["a"], "a") == "href"


def test_string_form_empty_returns_empty():
    assert resolve_url_attribute("", ["a"], "a") == ""


def test_string_form_no_matched_selector_still_works():
    # Legacy: string applies regardless of which selector matched
    assert resolve_url_attribute("data-href", ["a", "b"], "") == "data-href"


def test_string_form_none_returns_empty():
    assert resolve_url_attribute(None, ["a"], "a") == ""


# ── List form: parallel to row_selectors ──────────────────────────────────
def test_list_form_first_selector():
    assert resolve_url_attribute(
        ["data-href", "href"],
        ["div.dl-button[data-href]", "a.ct_dl_button[href]"],
        "div.dl-button[data-href]",
    ) == "data-href"


def test_list_form_second_selector():
    assert resolve_url_attribute(
        ["data-href", "href"],
        ["div.dl-button[data-href]", "a.ct_dl_button[href]"],
        "a.ct_dl_button[href]",
    ) == "href"


def test_list_form_empty_slot_means_click_capture():
    """Variant 4 site (floating menu, no URL attribute) shares a config
    with three URL-attribute variants. Its slot is empty — caller falls
    through to the click-and-capture path."""
    assert resolve_url_attribute(
        ["data-href", "href", ""],
        [
            "div.dl-button[data-href]",
            "a.ct_dl_button[href]",
            "#exDownloadMenu .exp-menu-item",
        ],
        "#exDownloadMenu .exp-menu-item",
    ) == ""


def test_list_form_unknown_selector_returns_empty():
    # Selector matched isn't in the list — return empty (click-capture).
    assert resolve_url_attribute(
        ["data-href"],
        ["div.dl-button"],
        "a.something-else[href]",
    ) == ""


def test_list_form_no_matched_selector_returns_empty():
    assert resolve_url_attribute(["data-href"], ["div.dl-button"], "") == ""


# ── Dict form: explicit selector→attribute mapping ────────────────────────
def test_dict_form_known_selector():
    assert resolve_url_attribute(
        {"div.dl-button[data-href]": "data-href",
         "a.ct_dl_button[href]": "href"},
        ["div.dl-button[data-href]", "a.ct_dl_button[href]"],
        "div.dl-button[data-href]",
    ) == "data-href"


def test_dict_form_unknown_selector_returns_empty():
    assert resolve_url_attribute(
        {"div.dl-button[data-href]": "data-href"},
        ["div.dl-button[data-href]", "a.other"],
        "a.other",
    ) == ""


def test_dict_form_no_matched_selector_returns_empty():
    assert resolve_url_attribute(
        {"a": "href"},
        ["a"],
        "",
    ) == ""


# ── Robustness ────────────────────────────────────────────────────────────
def test_unexpected_type_returns_empty():
    # int, float, tuple, etc — anything we don't recognize is safe
    assert resolve_url_attribute(42, ["a"], "a") == ""
    assert resolve_url_attribute(["x"], None, "a") == ""


def test_list_shorter_than_row_selectors_index_out_of_range():
    # url_attribute list is shorter than row_selectors — selector matched
    # the last one but no slot for it. Return empty (click-capture).
    assert resolve_url_attribute(
        ["data-href"],  # only one slot
        ["div.dl-button[data-href]", "a.ct_dl_button"],  # two selectors
        "a.ct_dl_button",
    ) == ""

"""Row 126 (ledger item 31, sub-row 2c-DATA): the five reptyle selector kinds
are resolved against RECORDED reptyle DOM with exact counts.

The row asked for "a test against the live DOM". A6 forbids a repository test
from contacting a live or authenticated site, so the two halves are split:

* THIS gate resolves every committed selector against three page states
  reconstructed from a real capture (``Reptyle-2.redacted.scrubbed.wacz``,
  app.reptyle.com, 2026-06-15) by replaying its rrweb log -- full snapshot plus
  incremental mutations -- with ``tools/build_recorded_dom_fixture.py``. The
  fixtures are recorded evidence: every tag, attribute and text node was
  produced by the site, not authored here.
* A genuinely LIVE resolution stays an operator instrument (``bd-shoot.py`` in
  the operator harness, per CLAUDE.md A7). It is never wired into CI.

Five selector kinds are carried by the reviewed template and each is measured
on the recorded state that actually contains its subject:

  login            -> the recorded auth.reptyle.com login page
  player           -> the recorded movie page
  quality          -> the recorded movie page with the quality menu open
  download trigger -> the recorded movie page with the download modal open
  download rows    -> the same modal state

WHAT THIS FOUND (row 361/362 shape, "valid but wrong"): the twelve row
selectors committed before this cut were syntactically perfect CSS that
resolved ZERO elements on the recorded modal -- the rows are
``button.modal-download-button``, not anchors, and each button's own text is
Standard/High/Ultra while the height label lives in a sibling div, so
``button:has-text("1080")`` could never match. They were replaced by selectors
measured on that DOM, and the counts below are the measurement.
"""
from __future__ import annotations

import functools
import hashlib
import json
from pathlib import Path

import pytest

BD_GATE_SCOPE = "module"

_REPO = Path(__file__).resolve().parent.parent
_FIXTURES = _REPO / "tests" / "fixtures" / "row126"
_TEMPLATE = _REPO / "templates" / "reviewed" / "app.reptyle.com.template.json"
_CAPTURE = "Reptyle-2.redacted.scrubbed.wacz"

_LOGIN_PAGE = "reptyle_login_page"
_MODAL_PAGE = "reptyle_download_modal"
_QUALITY_PAGE = "reptyle_quality_menu"

# Exact expected resolution counts, measured on the recorded DOM. A count is
# pinned per (fixture, selector path) so a drifted selector cannot pass by
# resolving "something".
_EXPECTED: dict[str, dict[str, int]] = {
    _LOGIN_PAGE: {
        "login.user_field": 1,
        "login.pass_field": 1,
        "login.submit_btn": 2,
        "player.player_selectors.[0]": 0,
        "player.player_selectors.[1]": 0,
        "download.trigger_selectors.[0]": 0,
        "download.trigger_selectors.[1]": 0,
    },
    _MODAL_PAGE: {
        "login.user_field": 0,
        "login.pass_field": 0,
        "login.submit_btn": 0,
        "player.player_selectors.[0]": 7,
        "player.player_selectors.[1]": 2,
        "download.trigger_selectors.[0]": 1,
        "download.trigger_selectors.[1]": 2,
        "download.row_selectors.[0]": 1,
        "download.row_selectors.[1]": 1,
        "download.row_selectors.[2]": 1,
        "download.row_selectors.[3]": 3,
    },
    _QUALITY_PAGE: {
        "login.user_field": 0,
        "player.player_selectors.[0]": 12,
        "player.player_selectors.[1]": 4,
        "download.trigger_selectors.[0]": 0,
        "download.trigger_selectors.[1]": 10,
        "download.row_selectors.[0]": 1,
        "download.row_selectors.[1]": 1,
        "download.row_selectors.[2]": 1,
        "download.row_selectors.[3]": 3,
    },
}

# The {resolution} quality pattern, expanded by the PRODUCTION adapter
# (template_assist.template_to_learned_download) into trigger_selectors[2:].
# 1440 and 540 are the negative controls of identical shape: the recorded
# player offered no such rung, so they must resolve zero while their siblings
# resolve exactly one.
_QUALITY_OPTION_COUNTS = {
    2160: 1, 1440: 0, 1080: 1, 720: 1, 540: 0, 480: 1, 360: 1, 240: 1,
}

_ABSENT = "a.row126-absent-control[href]"


def _template() -> dict:
    assert _TEMPLATE.is_file(), f"precondition: reviewed template absent: {_TEMPLATE}"
    data = json.loads(_TEMPLATE.read_text(encoding="utf-8"))
    assert data.get("host") == "app.reptyle.com", data.get("host")
    return data


def _provenance(name: str) -> dict:
    path = _FIXTURES / f"{name}.provenance.json"
    assert path.is_file(), f"precondition: provenance sidecar absent: {path}"
    return json.loads(path.read_text(encoding="utf-8"))


def _fixture_text(name: str) -> str:
    path = _FIXTURES / f"{name}.html"
    assert path.is_file(), f"precondition: recorded fixture absent: {path}"
    raw = path.read_bytes()
    assert raw, f"precondition: recorded fixture is empty: {path}"
    provenance = _provenance(name)
    # The fixture is evidence, so it must still be the bytes the generator
    # recorded -- an edited fixture is authored markup wearing a capture's name.
    assert hashlib.sha256(raw).hexdigest() == provenance["output_sha256"], (
        f"fixture {name} no longer matches its recorded digest"
    )
    assert len(raw) == provenance["output_bytes"], (name, len(raw))
    return raw.decode("utf-8")


def _probe_template() -> dict:
    """The committed template in the shape the production adapter emits."""
    from bulk_downloader.template_assist import (
        selector_group,
        template_to_learned_download,
    )

    template = _template()
    learned = template_to_learned_download(template)
    login = selector_group(template, "login")
    player = selector_group(template, "player")
    return {
        "id": "row126_reptyle_recorded_dom",
        "learned": {
            "download": learned,
            "login": {
                "user_field": login["email"],
                "pass_field": login["password"],
                "submit_btn": login["submit"],
            },
            "player": {
                "player_selectors": [player["container"], player["play_button"]],
            },
        },
    }


@functools.lru_cache(maxsize=None)
def _report(name: str) -> dict:
    from bulk_downloader.template_selector_verifier import verify_template_source

    _fixture_text(name)  # digest precondition before the subject is rendered
    return verify_template_source(
        _probe_template(), _FIXTURES / f"{name}.html", timeout=20
    )


def _row(report: dict, path_suffix: str) -> dict:
    matches = [
        item for item in report["selectors"]
        if item["path"].endswith(path_suffix)
    ]
    assert len(matches) == 1, (
        f"precondition: {path_suffix!r} occurs {len(matches)} times in the report"
    )
    return matches[0]


def _committed_selector_strings(template: dict) -> list[str]:
    """Independent walk of the reviewed-JSON selector block.

    Deliberately NOT the production enumerator: the denominator this gate
    reconciles against must not come from the code under measurement.
    """
    found: list[str] = []

    def walk(value: object) -> None:
        if isinstance(value, dict):
            for child in value.values():
                walk(child)
        elif isinstance(value, list):
            for child in value:
                walk(child)
        elif isinstance(value, str):
            found.append(value)

    walk(template.get("selectors") or {})
    return found


# --- the fixtures are recorded evidence, with a nonzero denominator ------


def test_every_fixture_is_replayed_reptyle_dom_with_a_reconciled_replay():
    for name in (_LOGIN_PAGE, _MODAL_PAGE, _QUALITY_PAGE):
        provenance = _provenance(name)
        assert provenance["source_capture"] == _CAPTURE, provenance
        assert provenance["capture_host"] == "app.reptyle.com", provenance
        assert provenance["generator"] == "tools/build_recorded_dom_fixture.py"
        replay = provenance["replay"]
        # Every recorded operation the log asked for was applied: a fixture
        # built from a partially replayed log is a different page.
        assert replay["adds_applied"] == replay["adds_requested"], replay
        assert replay["removes_applied"] == replay["removes_requested"], replay
        assert replay["attributes_applied"] == replay["attributes_requested"], replay
        assert replay["texts_applied"] == replay["texts_requested"], replay
        assert replay["adds_dangling"] == 0, replay
        assert replay["removes_missing"] == 0, replay
        assert replay["attributes_missing"] == 0, replay
        assert replay["texts_missing"] == 0, replay
        assert replay["nodes_in_snapshot"] > 0, replay
        # The login state IS the snapshot (stop_seq 1, no mutations to apply);
        # the two movie states exist only after the mutations are replayed.
        if name == _LOGIN_PAGE:
            assert replay["adds_requested"] == 0, replay
        else:
            assert replay["adds_applied"] > 15000, replay
        text = _fixture_text(name)
        assert text.startswith("<!doctype html>"), name
        assert "<script" not in text.lower(), f"{name} still carries executable script"


def test_each_recorded_state_carries_the_subject_its_kind_is_measured_on():
    login = _fixture_text(_LOGIN_PAGE)
    assert login.count('type="email"') == 1, login.count('type="email"')
    assert login.count('type="password"') == 1
    assert login.count('type="submit"') == 2
    assert login.count("modal-download-button") == 0

    modal = _fixture_text(_MODAL_PAGE)
    assert modal.count("modal-download-button") == 3, modal.count("modal-download-button")
    assert modal.count("ant-modal-wrap") == 1
    assert modal.count('data-tooltip="Download Full Movie"') == 1
    assert modal.count("2160p") == 1 and modal.count("1080p") == 1
    assert modal.count("1440p") == 0, "the recorded modal offered no 1440p rung"
    assert modal.count('type="email"') == 0

    quality = _fixture_text(_QUALITY_PAGE)
    assert quality.count("Set video quality to 1080p") == 1
    assert quality.count("Set video quality to 1440p") == 0
    assert quality.count("vjs-menu-item") == 169, quality.count("vjs-menu-item")


# --- the five kinds, resolved with exact counts --------------------------


@pytest.mark.parametrize("page", [_LOGIN_PAGE, _MODAL_PAGE, _QUALITY_PAGE])
def test_the_five_selector_kinds_resolve_with_their_measured_counts(page: str):
    report = _report(page)
    measured = {}
    for suffix, expected in _EXPECTED[page].items():
        row = _row(report, suffix)
        assert row["status"] in {"HIT", "MISS"}, (page, suffix, row)
        assert row["count"] == expected, (page, suffix, row)
        assert row["status"] == ("HIT" if expected else "MISS"), (page, suffix, row)
        measured[suffix] = row["count"]
    assert measured == _EXPECTED[page], (page, measured)
    assert sum(measured.values()) > 0, (
        f"precondition: {page} resolved nothing at all -- the fixture, not the "
        f"selectors, is the failure"
    )


def test_every_committed_row_selector_resolves_on_the_recorded_modal():
    """The row's core claim: no committed download row is a dead selector."""
    template = _template()
    committed = template["selectors"]["download"]["row_selectors"]
    assert len(committed) == 4, committed
    report = _report(_MODAL_PAGE)
    rows = [item for item in report["selectors"] if item["role"] == "row"]
    assert [item["selector"] for item in rows] == list(committed), rows
    for item in rows:
        assert item["status"] == "HIT", item
        assert item["count"] >= 1, item
    assert [item["count"] for item in rows] == [1, 1, 1, 3], rows
    # The modal was replayed already open, so the verifier never had to click:
    # the counts are of the recorded state, not of an interaction it drove.
    assert report["interaction"]["clicked"] is False, report["interaction"]
    assert report["verdict"] == "HIT" and report["ok"] is True, report["verdict"]


def test_the_quality_pattern_is_expanded_by_production_and_pinned_per_rung():
    from bulk_downloader.template_assist import preferred_resolutions

    template = _template()
    pattern = template["selectors"]["quality"]["resolution_option"]
    assert "{resolution}" in pattern, pattern
    assert list(_QUALITY_OPTION_COUNTS) == preferred_resolutions(template), (
        "precondition: the pinned rungs are not the template's own priority list"
    )
    report = _report(_QUALITY_PAGE)
    for offset, (resolution, expected) in enumerate(_QUALITY_OPTION_COUNTS.items()):
        row = _row(report, f"download.trigger_selectors.[{offset + 2}]")
        assert row["selector"] == pattern.replace("{resolution}", str(resolution)), row
        assert row["count"] == expected, (resolution, row)
    assert sum(_QUALITY_OPTION_COUNTS.values()) == 6, _QUALITY_OPTION_COUNTS


def test_no_committed_selector_escapes_measurement():
    template = _template()
    committed = _committed_selector_strings(template)
    assert len(committed) == 12, committed
    probe = _probe_template()["learned"]
    measured = set()
    for group in probe.values():
        for value in group.values():
            if isinstance(value, str):
                measured.add(value)
            else:
                measured.update(value)
    for selector in committed:
        if "{resolution}" in selector:
            expansions = {
                selector.replace("{resolution}", str(r))
                for r in _QUALITY_OPTION_COUNTS
            }
            assert expansions <= measured, selector
            continue
        assert selector in measured, f"committed selector never measured: {selector}"


# --- discriminating power: negative controls in both directions ----------


def test_the_qualified_modal_scope_beats_the_broad_dialog_scope():
    """Row 361's lesson, measured: a bare scope over-matches, so it is not used."""
    from bulk_downloader.template_selector_verifier import verify_template_source

    broad = '[role="dialog"] button'
    qualified = ".ant-modal.download-modal button.modal-download-button"
    template = _template()
    assert any(qualified in item
               for item in template["selectors"]["download"]["row_selectors"]), (
        "precondition: the committed template no longer uses the qualified scope"
    )
    report = verify_template_source(
        {"id": "row126_scope_control",
         "learned": {"download": {"row_selectors": [broad, qualified, _ABSENT]}}},
        _FIXTURES / f"{_MODAL_PAGE}.html",
        timeout=20,
    )
    counts = {item["selector"]: item["count"] for item in report["selectors"]}
    assert counts[broad] == 6, counts
    assert counts[qualified] == 3, counts
    assert counts[_ABSENT] == 0, counts
    absent = _row(report, "row_selectors.[2]")
    assert absent["status"] == "MISS", absent


def test_a_page_without_the_modal_cannot_manufacture_a_pass(tmp_path: Path):
    """An empty document must fail loudly, never read as a pass."""
    from bulk_downloader.template_selector_verifier import verify_template_source

    blank = tmp_path / "no-modal.html"
    blank.write_text(
        "<!doctype html><html><body><div id='empty'></div></body></html>",
        encoding="utf-8",
    )

    report = verify_template_source(_probe_template(), blank, timeout=20)

    assert report["ok"] is False, report["verdict"]
    assert report["verdict"] != "HIT", report["verdict"]
    for item in report["selectors"]:
        assert item["count"] in (0, None), item
        assert item["status"] != "HIT", item


def test_an_unavailable_subject_is_unknown_and_never_ok(tmp_path: Path):
    from bulk_downloader.template_selector_verifier import verify_template_source

    missing = tmp_path / "not-a-recorded-page.html"
    assert not missing.exists()

    report = verify_template_source(_probe_template(), missing, timeout=20)

    assert report["verdict"] == "UNKNOWN", report["verdict"]
    assert report["ok"] is False, report
    assert report["selector_count"] > 0, report
    assert all(item["status"] == "UNKNOWN" for item in report["selectors"]), report


# --- the replayer that MADE the fixtures has its own denominator ---------


def _generator():
    """Import the fixture generator the way tests/test_a6_dom_derivations does."""
    import sys

    tools = str(_REPO / "tools")
    if tools not in sys.path:
        sys.path.insert(0, tools)
    import build_recorded_dom_fixture  # noqa: PLC0415

    return build_recorded_dom_fixture


def _synthetic_log() -> list[dict]:
    """A four-event rrweb log whose every mutation kind is observable."""
    root = {
        "id": 1, "type": 2, "tagName": "html", "attributes": {},
        "childNodes": [{
            "id": 2, "type": 2, "tagName": "body", "attributes": {},
            "childNodes": [{
                "id": 3, "type": 2, "tagName": "div",
                "attributes": {"class": "old"},
                "childNodes": [{"id": 4, "type": 3, "textContent": "before"}],
            }],
        }],
    }
    return [
        {"type": "meta", "dom_seq": 0, "data": {"href": "https://example.invalid/"}},
        {"type": "full_snapshot", "dom_seq": 1, "data": {"node": root}},
        {"type": "incremental", "dom_seq": 2, "data": {
            "source": 0,
            # deliberately child-before-parent: a single sweep would drop it
            "adds": [
                {"parentId": 5, "nextId": None,
                 "node": {"id": 6, "type": 3, "textContent": "1080p"}},
                {"parentId": 2, "nextId": None,
                 "node": {"id": 5, "type": 2, "tagName": "span",
                          "attributes": {"class": "new"}, "childNodes": []}},
            ],
            "attributes": [{"id": 3, "attributes": {"class": "changed"}}],
            "texts": [{"id": 4, "value": "after"}],
        }},
        {"type": "incremental", "dom_seq": 3, "data": {
            "source": 0, "removes": [{"parentId": 2, "id": 3}],
        }},
        {"type": "incremental", "dom_seq": 4, "data": {
            "source": 0,
            "adds": [{"parentId": 2, "nextId": None,
                      "node": {"id": 7, "type": 2, "tagName": "footer",
                               "attributes": {"class": "too-late"},
                               "childNodes": []}}],
        }},
    ]


def test_the_replayer_applies_every_mutation_kind_and_reconciles_them():
    from bulk_downloader.dom_serialize import nodes_to_html

    generator = _generator()
    log = _synthetic_log()

    root, stats = generator.replay_dom(log, 1, stop_seq=2)
    html = nodes_to_html(root)
    assert stats["adds_requested"] == stats["adds_applied"] == 2, stats
    assert stats["adds_dangling"] == 0, stats
    assert stats["attributes_applied"] == 1 and stats["texts_applied"] == 1, stats
    assert stats["removes_requested"] == 0, stats
    # the child added before its parent still landed inside it
    assert '<span class="new">1080p</span>' in html, html
    assert '<div class="changed">after</div>' in html, html
    assert "before" not in html and 'class="old"' not in html, html
    assert "too-late" not in html, "stop_seq did not bound the replay"

    removed, removed_stats = generator.replay_dom(log, 1, stop_seq=3)
    removed_html = nodes_to_html(removed)
    assert removed_stats["removes_applied"] == 1, removed_stats
    assert removed_stats["removes_missing"] == 0, removed_stats
    assert 'class="changed"' not in removed_html, removed_html
    assert '<span class="new">1080p</span>' in removed_html, removed_html


def test_the_replayer_refuses_a_start_that_is_not_a_full_snapshot():
    generator = _generator()

    with pytest.raises(ValueError, match="not a full_snapshot"):
        generator.replay_dom(_synthetic_log(), 2, stop_seq=3)


def test_the_generator_prunes_script_but_keeps_the_rest():
    generator = _generator()
    node = {
        "id": 1, "type": 2, "tagName": "body", "attributes": {},
        "childNodes": [
            {"id": 2, "type": 2, "tagName": "script", "attributes": {},
             "childNodes": [{"id": 3, "type": 3, "textContent": "alert(1)"}]},
            {"id": 4, "type": 2, "tagName": "button",
             "attributes": {"class": "modal-download-button", "_cssText": "a{}"},
             "childNodes": []},
        ],
    }
    pruned = generator.prune_inert(node)
    stripped = generator.strip_inline_css(node)

    assert pruned["pruned"] == 1, pruned
    assert stripped["attributes"] == 1 and stripped["bytes"] == 3, stripped
    assert len(node["childNodes"]) == 1, node
    assert node["childNodes"][0]["attributes"] == {"class": "modal-download-button"}

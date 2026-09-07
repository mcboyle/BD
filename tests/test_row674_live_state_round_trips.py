"""Row 674: a live page state has a supported route to a committed fixture.

``tools/build_recorded_dom_fixture.py`` is the only fixture generator and it
required an rrweb ``dom_log``. The capture path that produced run 1's WACZ armed
rrweb and recorded ZERO events, so the only generator could not be fed and the
page state an operator actually saw had no way into the tree.

Two things are measured here:

* THE ROUTE. A saved page state (``page.content()`` on a live page, a page
  record lifted out of a WACZ) goes through the SAME pipeline as an rrweb
  replay -- one node tree, one prune/strip pass, one provenance sidecar with
  its digests -- and comes back as a fixture that still carries the page's
  identity, not merely its shape. The round trip below runs against a SYNTHETIC
  page: A6 forbids a repository test from touching a live or authenticated
  site, and nothing here does.
* THE THIRD STATE. "rrweb recorded nothing" is REPORTED as UNKNOWN with its own
  exit code, distinguishable from "this file is not a capture at all". Those two
  answers are repaired in opposite places, and a diagnostic that collapses them
  costs the investigation (CLAUDE.md A7).
"""
from __future__ import annotations

import hashlib
import json
from html.parser import HTMLParser
from pathlib import Path

BD_GATE_SCOPE = "repo-wide"

_REPO = Path(__file__).resolve().parent.parent

_PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>Synthetic Player Page</title>
<script>window.__row674 = 1;</script></head>
<body class="synthetic">
<div id="player" class="video-shell" data-scene="row674"><button class="play-button" aria-label="Play">Play</button><img src="poster.png" alt="poster"><br></div>
<div class="download-modal"><a href="/dl/1080p.mp4" class="dl-row">1080p</a></div>
</body></html>
"""

_INERT = {"script", "noscript"}


def _tool():
    import sys

    tools = str(_REPO / "tools")
    if tools not in sys.path:
        sys.path.insert(0, tools)
    import build_recorded_dom_fixture  # noqa: PLC0415

    return build_recorded_dom_fixture


class _IndependentCensus(HTMLParser):
    """Elements and their attributes, read WITHOUT the tool's own parser.

    MEASURED, NOT ASSUMED: the first draft of this gate built both sides of the
    comparison with ``build_recorded_dom_fixture._PageStateNodes``, and the
    mutant that drops every attribute on the way in moved BOTH sides
    identically -- so it ESCAPED a test whose whole subject is that the page's
    identity survives. Deriving the expected set from the artifact under
    measurement is the defect CLAUDE.md A7 names, and this is what it looks
    like when a fix reproduces it.
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.found: list[tuple] = []

    def handle_starttag(self, tag: str, attrs: list) -> None:
        self.found.append((
            tag,
            tuple(sorted((name, "" if value is None else value)
                         for name, value in attrs)),
        ))


def _element_census(html: str) -> list[tuple]:
    """Every element with its attributes, in document order.

    A census of IDENTITY, not of shape: a route that wrote the right tags with
    the attributes stripped would satisfy a tag-only comparison while losing
    every id, class and href a selector claim is actually about.
    """
    parser = _IndependentCensus()
    parser.feed(html)
    parser.close()
    return parser.found


def _capture_json(dom_log: list) -> dict:
    return {
        "capture_version": 1,
        "captured_at": "2026-01-01T00:00:00+00:00",
        "url": "https://synthetic.example/scene/1",
        "host": "synthetic.example",
        "network_log": [],
        "dom_log": dom_log,
    }


_RRWEB_NODE = {
    "type": 0, "id": 0, "childNodes": [{
        "type": 2, "id": 1, "tagName": "html", "attributes": {}, "childNodes": [{
            "type": 2, "id": 2, "tagName": "body",
            "attributes": {"class": "recorded"},
            "childNodes": [{"type": 3, "id": 3, "textContent": "recorded"}],
        }],
    }],
}


# --- 674-1: the route ------------------------------------------------------


def test_a_saved_page_state_round_trips_into_a_committed_fixture(tmp_path):
    module = _tool()
    page = tmp_path / "page.html"
    page.write_text(_PAGE, encoding="utf-8")
    out = tmp_path / "fixture.html"
    sidecar = tmp_path / "fixture.provenance.json"

    # Deliberately only flags that ALREADY EXIST, so the base failure is the
    # ABSENCE OF THE ROUTE -- the tool taking the one route it has and refusing
    # a page state as a malformed archive -- and not argparse rejecting a flag.
    rc = module.main([
        str(page), "--out", str(out), "--provenance", str(sidecar),
    ])
    assert rc == 0, (
        "there is no supported route from a saved page state to a fixture: "
        f"tools/build_recorded_dom_fixture.py exited {rc} for {page}"
    )
    assert out.is_file(), f"no fixture was written to {out}"

    fixture = out.read_text(encoding="utf-8")
    assert fixture.startswith("<!doctype html>"), fixture[:40]

    # THE ROUND TRIP: every element of the page comes back with its identity,
    # and only the inert ones are gone.
    source = _element_census(_PAGE)
    expected = [item for item in source if item[0] not in _INERT]
    assert len(source) - len(expected) == 1, source
    assert _element_census(fixture) == expected, _element_census(fixture)
    # Text nodes are part of the page's identity too: a selector that matches on
    # visible text has nothing to match if the route carried tags alone.
    for text in ("Synthetic Player Page", "Play", "1080p"):
        assert text in fixture, text
    assert "window.__row674" not in fixture, "the inert script survived"

    provenance = json.loads(sidecar.read_text(encoding="utf-8"))
    assert provenance["route"] == "page_html", provenance["route"]
    assert provenance["generator"] == "tools/build_recorded_dom_fixture.py"
    assert provenance["pruned_inert_nodes"] == 1, provenance
    assert provenance["dom_log_count"] == 1, provenance
    assert provenance["replay"]["nodes_in_snapshot"] > 0, provenance["replay"]
    # The sidecar is the fixture's identity, exactly as it is for the rrweb
    # route: a fixture that no longer matches its digest is authored markup.
    raw = out.read_bytes()
    assert hashlib.sha256(raw).hexdigest() == provenance["output_sha256"]
    assert len(raw) == provenance["output_bytes"]


def test_the_page_state_route_records_where_the_page_came_from(tmp_path):
    module = _tool()
    page = tmp_path / "page.html"
    page.write_text(_PAGE, encoding="utf-8")
    sidecar = tmp_path / "fixture.provenance.json"
    rc = module.main([
        str(page), "--out", str(tmp_path / "fixture.html"),
        "--provenance", str(sidecar),
        "--source-url", "https://synthetic.example/scene/1",
    ])
    assert rc == 0, rc
    provenance = json.loads(sidecar.read_text(encoding="utf-8"))
    assert provenance["capture_url"] == "https://synthetic.example/scene/1"
    assert provenance["capture_host"] == "synthetic.example"


def test_the_route_is_decided_by_the_input_and_named_in_the_receipt(tmp_path):
    module = _tool()
    assert module.route_for(Path("page.html")) == "page_html"
    assert module.route_for(Path("PAGE.HTM")) == "page_html"
    assert module.route_for(Path("capture.wacz")) == "rrweb_replay"
    assert module.route_for(Path("capture.json")) == "rrweb_replay"


def test_the_void_element_set_matches_the_serializer():
    from bulk_downloader import dom_serialize

    module = _tool()
    assert set(module._VOID_ELEMENTS) == set(dom_serialize._VOID_TAGS)
    assert len(module._VOID_ELEMENTS) == 14, len(module._VOID_ELEMENTS)


# --- 674-2: the third state, and the negative controls ---------------------


def test_an_empty_rrweb_log_reports_unknown_and_writes_no_fixture(tmp_path, capsys):
    module = _tool()
    capture = tmp_path / "empty.json"
    capture.write_text(json.dumps(_capture_json([])), encoding="utf-8")
    out = tmp_path / "empty-fixture.html"

    rc = module.main([str(capture), "--out", str(out)])
    err = capsys.readouterr().err
    assert rc == 4, (rc, err)
    assert err.startswith("UNKNOWN: "), err
    assert "rrweb recorded nothing" in err, err
    # NO EMPTY FIXTURE ON DISK -- asserted on the filesystem, not on a raised
    # exception: the row's acceptance is that nothing is produced, and a call
    # that raised after writing would satisfy an exception-only assertion.
    assert not out.exists(), f"an empty fixture was written to {out}"


def test_an_empty_rrweb_log_is_distinguishable_from_a_file_that_is_not_a_capture(
    tmp_path, capsys
):
    module = _tool()
    empty = tmp_path / "empty.json"
    empty.write_text(json.dumps(_capture_json([])), encoding="utf-8")
    foreign = tmp_path / "notacapture.json"
    foreign.write_text(json.dumps({"hello": 1}), encoding="utf-8")

    rc_empty = module.main([str(empty), "--out", str(tmp_path / "a.html")])
    err_empty = capsys.readouterr().err
    rc_foreign = module.main([str(foreign), "--out", str(tmp_path / "b.html")])
    err_foreign = capsys.readouterr().err

    assert rc_empty == 4, (rc_empty, err_empty)
    assert rc_foreign == 2, (rc_foreign, err_foreign)
    assert rc_empty != rc_foreign
    assert "rrweb recorded nothing" in err_empty, err_empty
    assert "not a capture" in err_foreign, err_foreign
    assert "not a capture" not in err_empty, err_empty
    assert "rrweb recorded nothing" not in err_foreign, err_foreign
    assert not (tmp_path / "a.html").exists()
    assert not (tmp_path / "b.html").exists()
    # The shape predicate is the decision, measured directly.
    assert module.is_capture(json.loads(empty.read_text())) is True
    assert module.is_capture(json.loads(foreign.read_text())) is False


def test_an_empty_page_state_is_refused_and_writes_no_fixture(tmp_path, capsys):
    module = _tool()
    blank = tmp_path / "blank.html"
    blank.write_text("   \n", encoding="utf-8")
    out = tmp_path / "blank-fixture.html"
    rc = module.main([str(blank), "--out", str(out)])
    err = capsys.readouterr().err
    assert rc == 2, (rc, err)
    assert "page state is empty" in err, err
    assert not out.exists()


def test_a_page_state_that_rendered_only_text_is_reported_unknown(tmp_path, capsys):
    """A saved page with no ELEMENT in it is UNKNOWN, not a 29-byte fixture.

    This is the node the first round of this cut did not have. The guard was
    written as ``if not root["childNodes"]`` and a TEXT node IS a childNode, so
    the branch never fired for the input its own message names: ``hello world``
    exited 0 and wrote a fixture with nothing in it. Deleting the guard changed
    nothing, which is how a refusing branch with no test looks from the outside.
    """
    module = _tool()
    page = tmp_path / "text-only.html"
    page.write_text("hello world\n", encoding="utf-8")
    out = tmp_path / "text-only-fixture.html"

    # PRECONDITION, measured independently of the tool's own predicate: this
    # page state is NOT empty (so the rc-2 arm cannot be what refuses it) and it
    # carries no element at all (so the rc-4 arm is the one under test).
    assert page.read_text(encoding="utf-8").strip(), "the fixture wrote an empty page"
    assert _element_census(page.read_text(encoding="utf-8")) == []

    rc = module.main([str(page), "--out", str(out)])
    err = capsys.readouterr().err
    assert rc == 4, (rc, err)
    assert err.startswith("UNKNOWN: "), err
    assert "carries no elements" in err, err
    # NO FIXTURE ON DISK -- asserted on the filesystem, exactly as the sibling
    # empty-rrweb-log node does: the shipped defect wrote 29 bytes and exited 0.
    assert not out.exists(), f"a fixture with no elements was written to {out}"

    # And it is the ELEMENT question that decides, not the child-list question:
    # the tool's own parse of this page has a non-empty child list.
    root = module._PageStateNodes()
    root.feed(page.read_text(encoding="utf-8"))
    root.close()
    assert root.root["childNodes"], "the page state parsed to no children at all"
    assert module._carries_an_element(root.root) is False

    # Distinguishable from the empty-page-state arm, which stays rc 2.
    blank = tmp_path / "blank.html"
    blank.write_text("   \n", encoding="utf-8")
    rc_blank = module.main([str(blank), "--out", str(tmp_path / "blank.html.out")])
    err_blank = capsys.readouterr().err
    assert rc_blank == 2, (rc_blank, err_blank)
    assert "carries no elements" not in err_blank, err_blank


def test_the_rrweb_route_is_unchanged_and_still_names_itself(tmp_path):
    module = _tool()
    capture = tmp_path / "recorded.json"
    capture.write_text(
        json.dumps(_capture_json([
            {"type": "full_snapshot", "dom_seq": 0, "data": {"node": _RRWEB_NODE}}
        ])),
        encoding="utf-8",
    )
    out = tmp_path / "recorded.html"
    sidecar = tmp_path / "recorded.provenance.json"
    rc = module.main([
        str(capture), "--out", str(out), "--provenance", str(sidecar)
    ])
    assert rc == 0, rc
    provenance = json.loads(sidecar.read_text(encoding="utf-8"))
    assert provenance["route"] == "rrweb_replay", provenance["route"]
    assert provenance["capture_host"] == "synthetic.example", provenance
    assert provenance["replay"]["nodes_in_snapshot"] == 4, provenance["replay"]
    assert 'class="recorded"' in out.read_text(encoding="utf-8")


def test_row674_transform_control_import_only():
    """Imports the generator without driving either route through it.

    Band for ``row674_live_state_round_trips_transform_control.json``: a
    transform of the page-state parser that this test cannot observe MUST
    ESCAPE it.
    """
    module = _tool()
    assert callable(module.main)
    assert callable(module.page_html_to_capture)
    assert issubclass(module.CaptureUnknown, Exception)

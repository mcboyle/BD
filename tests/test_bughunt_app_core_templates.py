"""O1789 bug-hunt app-core-surface F8 F9 F10: Template Manager page, drift route and inventory scan.

Findings (harness-work/O1789/bughunt/app-core-surface/FINDINGS.md, finder bd-worker-S69-A; probes P13 P14 P15 in
ev/test_probe_app_core.py, measured OPEN on 2b7ca835 by S54-C):
F8  /cockpit/template-manager joined a draft's ``resolutions`` raw into the HTML; every other cell is escaped.
F9  /api/template_manager/drift?file= is basename-confined, but the gold path is built from the draft's ``host``, so
    host "../../../outside/y" read and returned a template outside templates/.
F10 tools/template_inventory.scan(): a draft whose JSON root is a list reached assess() -> AttributeError -> /inventory 500.
Rig mirrors the probes' _tm_root: a scratch repo root with the four template dirs, _REPO_ROOT patched, cwd = that root.
"""
from __future__ import annotations

BD_GATE_SCOPE = "module"

import json

import pytest
from flask import Flask

PAYLOAD = "<img src=x onerror=alert(1)>"
MARKER = "OUTSIDE-TREE-MARKER"


@pytest.fixture
def tm(tmp_path, monkeypatch):
    """(client, repo root) for the template-manager blueprint over a scratch template tree."""
    from bulk_downloader import app_template_manager_ui as TM
    monkeypatch.setenv("BD_HOME", str(tmp_path / "home"))
    root = tmp_path / "repo"
    for sub in ("drafts", "reviewed", "enabled", "review_candidates"):
        (root / "templates" / sub).mkdir(parents=True)
    monkeypatch.setattr(TM, "_REPO_ROOT", root)
    monkeypatch.chdir(root)
    app = Flask(__name__)
    TM.register_routes(app)
    return app.test_client(), root


def _draft(root, doc, name="x.json"):
    (root / "templates" / "drafts" / name).write_text(json.dumps(doc))


# ---- F8 -------------------------------------------------------------------------------------------------------------

def test_f8_resolutions_cell_is_escaped(tm):
    client, root = tm
    _draft(root, {"host": "x.example", "resolutions": [PAYLOAD, "720p"], "status": "draft_requires_review"})
    page = client.get("/cockpit/template-manager").get_data(as_text=True)
    assert "x.example" in page, "ACS-F8-NOT-ARMED: the draft row is not on the page"
    assert PAYLOAD not in page, "ACS-F8-RESOLUTIONS-RAW: draft resolutions rendered verbatim into the page HTML"
    assert "&lt;img src=x onerror=alert(1)&gt;, 720p" in page, "ACS-F8: escaped resolutions cell missing"


def test_f8_control_plain_resolutions_render_unchanged(tm):
    client, root = tm
    _draft(root, {"host": "x.example", "resolutions": ["1080p", 720], "status": "draft_requires_review"})
    page = client.get("/cockpit/template-manager").get_data(as_text=True)
    assert "1080p, 720" in page, "ACS-F8 control: plain resolutions cell changed"


# ---- F9 -------------------------------------------------------------------------------------------------------------

def _outside_gold(root):
    outside = root.parent / "outside"
    outside.mkdir()
    (outside / "y.template.json").write_text(json.dumps({"host": "y", "selectors": {"listing": {"title": MARKER}}}))


def test_f9_draft_host_cannot_name_a_gold_outside_reviewed(tm):
    client, root = tm
    _outside_gold(root)
    _draft(root, {"host": "../../../outside/y", "selectors": {"listing": {"title": "h1"}}})
    # the same file through the tree root, as P14 reached it (cwd and _REPO_ROOT are both the scratch root)
    assert (root / "templates" / "reviewed" / "../../../outside/y.template.json").resolve().is_file(), "ACS-F9-NOT-ARMED"
    r = client.get("/api/template_manager/drift?file=x.json")
    body = r.get_data(as_text=True)
    assert MARKER not in body, "ACS-F9-GOLD-OUTSIDE-TREE: drift read and returned a gold outside templates/reviewed: %r" % body[:200]
    assert r.status_code == 400 and "not a plain hostname" in r.get_json()["error"], (r.status_code, body[:200])


@pytest.mark.parametrize("host", ["../../../outside/y", "..", ".", "/abs/y", "a/b", "a\\b", ["y"], {"h": 1}])
def test_f9_default_gold_takes_a_plain_host_only(host):
    from tools.template_drift_report import _default_gold
    assert _default_gold({"host": host}) is None, "ACS-F9-HOST-PATH: _default_gold(%r) built a path" % (host,)


def test_f9_route_contains_the_gold_even_if_the_tool_builds_a_path(tm, monkeypatch):
    """Second layer: the route itself refuses a gold outside templates/reviewed, whatever _default_gold returns."""
    from tools import template_drift_report as DR
    client, root = tm
    _outside_gold(root)
    _draft(root, {"host": "y", "selectors": {"listing": {"title": "h1"}}})
    monkeypatch.setattr(DR, "_default_gold", lambda cand: "templates/reviewed/../../../outside/y.template.json")
    r = client.get("/api/template_manager/drift?file=x.json")
    body = r.get_data(as_text=True)
    assert MARKER not in body, "ACS-F9-ROUTE-UNCONTAINED: drift returned a gold outside templates/reviewed: %r" % body[:200]
    assert r.status_code == 400 and r.get_json()["error"] == "gold path escapes templates/reviewed", body[:200]


def test_f9_control_in_tree_gold_still_drifts(tm):
    client, root = tm
    (root / "templates" / "reviewed" / "y.template.json").write_text(
        json.dumps({"host": "y", "selectors": {"listing": {"title": MARKER}}}))
    _draft(root, {"host": "y", "selectors": {"listing": {"title": "h1"}}})
    r = client.get("/api/template_manager/drift?file=x.json")
    assert r.status_code == 200 and MARKER in r.get_data(as_text=True), "ACS-F9 control: in-tree gold no longer drifts"
    from tools.template_drift_report import _default_gold
    assert _default_gold({"host": "app.reptyle.com"}) == "templates/reviewed/app.reptyle.com.template.json"


def test_f9_control_no_host_keeps_its_message(tm):
    client, root = tm
    _draft(root, {"selectors": {}})
    r = client.get("/api/template_manager/drift?file=x.json")
    assert r.status_code == 400 and r.get_json()["error"] == "candidate has no host; cannot locate gold"


# ---- F10 ------------------------------------------------------------------------------------------------------------

def test_f10_list_root_draft_is_a_sanity_line_not_a_500(tm):
    client, root = tm
    _draft(root, [])
    _draft(root, {"host": "z", "selectors": {"title": "h1"}}, name="z.json")
    r = client.get("/api/template_manager/inventory")
    assert r.status_code == 200, "ACS-F10-LIST-ROOT-500: /inventory -> %s %r" % (r.status_code, r.get_data(as_text=True)[:160])
    data = r.get_json()
    assert [row["host"] for row in data["dirs"]["drafts"]] == ["z"], "ACS-F10: the valid draft beside it was dropped"
    assert any("drafts/x.json: unreadable (root is list" in s for s in data["sanity"]), data["sanity"]


def test_f10_control_valid_inventory(tm):
    client, root = tm
    _draft(root, {"host": "z", "selectors": {"title": "h1"}})
    r = client.get("/api/template_manager/inventory")
    assert r.status_code == 200 and r.get_json()["counts"]["drafts"] == 1

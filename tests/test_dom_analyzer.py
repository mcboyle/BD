"""F2.6 DOM Analyzer Workbench — server-core positive controls.

The load-bearing concern is the redaction gate: a captured DOM can carry
secrets in attributes / hrefs / text nodes that class-PII redaction does not
reach. These tests plant a secret of every kind in every location and prove
(a) each is stripped from the emitted tree+html, (b) the independent scan is
clean, (c) the gate FAILS CLOSED (no tree) if a residual survives, and the
workbench round-trips (serialize → selector test, click → selector).

Harness: run_tests.py chdirs per run; zero-arg test functions; no pytest
builtins; module globals restored in try/finally (monkeypatch unreliable).
"""

import json
import tempfile
from pathlib import Path

from bulk_downloader import dom_analyzer as da
from bulk_downloader import dom_serialize as ds


# ── synthetic capture with a planted secret in every location ────────────────

def _planted_capture():
    """rrweb-shaped capture. Planted secrets:
      - bd-block subtree            (class-PII block → dropped)
      - bd-mask text                (class-PII mask → masked)
      - hidden input value (token)  (input-value redaction)
      - <a href signed URL>         (value-content, NOT class/input)
      - data-session JWT attribute  (value-content)
      - <pre> email + JWT text      (value-content)
    Plus a CLEAN button for the click→selector round-trip.
    """
    def el(tag, attrs=None, children=None):
        return {"type": 2, "tagName": tag, "attributes": attrs or {},
                "childNodes": children or []}

    def txt(s):
        return {"type": 3, "textContent": s}

    body = el("body", {}, [
        el("div", {"class": "bd-block secret-box"}, [txt("TOPSECRET-block-text-should-vanish")]),
        el("span", {"class": "bd-mask"}, [txt("supersecretmaskedvalue")]),
        el("input", {"type": "hidden", "name": "csrf",
                     "value": "eyJhbGciOiJIUzI1NiJ9.eyJ1aWQiOjF9.SflKxwRJSMeKKF2QT4"}),
        el("a", {"href": "https://cdn.example.com/v.mp4?signature=ABCDEF123&Expires=99999",
                 "id": "dl-link", "class": "download"}, [txt("Download")]),
        el("div", {"data-session": "eyJhbGciOiJIUzI1NiJ9.eyJzZXNzIjoiYWJjIn0.ZZZsignedZZZ"},
           [txt("region")]),
        el("pre", {}, [txt("contact user@example.com token eyJhbGciOiJIJ9.eyJhIjoxfQ.QQQ")]),
        el("button", {"id": "dl-btn", "class": "download primary"}, [txt("Download")]),
    ])
    html = el("html", {}, [body])
    document = {"type": 0, "childNodes": [html]}
    return {"dom_log": [{"type": 2, "data": {"node": document}}]}


# ── the gate: clean emit + every planted secret stripped ─────────────────────

def test_redacted_dom_emits_clean_tree():
    res = da.redacted_dom(_planted_capture())
    assert res["ok"] is True, res
    assert res["has_dom"] is True
    assert res["residual_count"] == 0, res["residual_kinds"]
    html = res["html"]
    # Every planted raw secret is gone from the emitted html.
    for needle in ("TOPSECRET-block-text", "supersecretmaskedvalue",
                   "SflKxwRJSMeKKF2QT4", "signature=ABCDEF123",
                   "ZZZsignedZZZ", "user@example.com", "QQQ"):
        assert needle not in html, f"LEAK: {needle!r} survived into html"


def test_independent_scan_is_clean():
    # The proof mechanism: scanning the emitted tree finds nothing.
    from bulk_downloader.capture_artifact_redact import scan_artifact_secrets
    res = da.redacted_dom(_planted_capture())
    assert res["ok"] is True
    assert scan_artifact_secrets(res["tree"]) == []


def test_block_subtree_dropped():
    res = da.redacted_dom(_planted_capture())
    # bd-block drops the subtree entirely — its text must not appear at all.
    assert "block-text-should-vanish" not in res["html"]


# ── fail-closed: a residual survivor withholds the tree ──────────────────────

def test_gate_fails_closed_on_residual():
    """If the scan reports a residual, the gate must withhold tree+html and
    surface counts-by-kind only — never the value. Force the scan to report a
    residual to prove the gate logic (detectors are shared, so a genuine
    residual is hard to manufacture; this tests the GATE, not the detector)."""
    orig = da.scan_artifact_secrets
    try:
        da.scan_artifact_secrets = lambda obj, _path="$": [("$.x", "jwt"), ("$.y", "jwt")]
        res = da.redacted_dom(_planted_capture())
        assert res["ok"] is False, "gate did not fail closed on residual"
        assert res["tree"] is None and res["html"] is None
        assert res["residual_count"] == 2
        assert res["residual_kinds"] == {"jwt": 2}
        # counts only — no path/value leakage in the surfaced fields
        assert "x" not in json.dumps(res["residual_kinds"])
    finally:
        da.scan_artifact_secrets = orig


# ── DOM-less capture: graceful "no DOM", not an error ────────────────────────

def test_domless_capture_is_graceful():
    res = da.redacted_dom({"dom_log": []})
    assert res["ok"] is True
    assert res["has_dom"] is False
    assert res["tree"] is None


# ── serializer round-trip ────────────────────────────────────────────────────

def test_serializer_roundtrip():
    node = {"type": 2, "tagName": "div", "attributes": {"id": "x", "class": "a b"},
            "childNodes": [{"type": 3, "textContent": "hi"}]}
    html = ds.nodes_to_html(node)
    assert html == '<div id="x" class="a b">hi</div>', html
    # void element: no closing tag
    img = {"type": 2, "tagName": "img", "attributes": {"src": "/a.png"}, "childNodes": []}
    assert ds.nodes_to_html(img) == '<img src="/a.png">'
    # text is escaped
    assert ds.nodes_to_html({"type": 3, "textContent": "<b>&"}) == "&lt;b&gt;&amp;"


# ── click → candidate selector round-trips against the rendered html ─────────

def test_candidate_selector_round_trips():
    res = da.redacted_dom(_planted_capture())
    html = res["html"]
    # the clean button node
    btn = {"type": 2, "tagName": "button",
           "attributes": {"id": "dl-btn", "class": "download primary"}, "childNodes": []}
    cand = da.candidate_selector_for(btn)
    assert cand["basis"] == "id"
    assert cand["selector"] == "button#dl-btn"
    out = da.test_selectors(html, [cand["selector"]])
    assert out and out[0].get("count", 0) >= 1, out


def test_candidate_selector_preference_order():
    # name beats class; input-type when no id/name; data-attr; bare tag fallback
    assert da.candidate_selector_for(
        {"type": 2, "tagName": "input",
         "attributes": {"name": "email", "class": "form-x9f2a3"}})["basis"] == "name"
    assert da.candidate_selector_for(
        {"type": 2, "tagName": "input", "attributes": {"type": "password"}})["selector"] \
        == 'input[type="password"]'
    assert da.candidate_selector_for(
        {"type": 2, "tagName": "div", "attributes": {"data-role": "row"}})["basis"] == "data-attr"
    assert da.candidate_selector_for(
        {"type": 2, "tagName": "section", "attributes": {}})["selector"] == "section"


# ── pin lands review-only, never enabled ─────────────────────────────────────

def test_pin_candidate_is_review_only():
    d = tempfile.mkdtemp()
    res = da.pin_candidate("button#dl-btn", "download", host="t.example.com",
                           drafts_dir=d, name="button", capture_name="cap.wacz")
    assert res["ok"] is True
    assert res["status"] == "draft_review_required"
    assert res["enabled"] is False
    fp = Path(d) / "t.example.com.template-draft.json"
    assert fp.is_file()
    on_disk = json.loads(fp.read_text("utf-8"))
    assert on_disk["status"] == "draft_review_required"
    assert on_disk["review_required"] is True
    assert on_disk["status"] != "enabled"
    assert on_disk["selectors"]["download"]["button"] == "button#dl-btn"


def test_pin_merges_not_clobbers():
    d = tempfile.mkdtemp()
    da.pin_candidate("button#dl-btn", "download", host="t.example.com",
                     drafts_dir=d, name="button")
    da.pin_candidate('input[type="email"]', "login", host="t.example.com",
                     drafts_dir=d, name="email")
    fp = Path(d) / "t.example.com.template-draft.json"
    on_disk = json.loads(fp.read_text("utf-8"))
    # both roles survive the second write
    assert "download" in on_disk["selectors"]
    assert "login" in on_disk["selectors"]
    assert on_disk["status"] == "draft_review_required"


# ── capture enumeration + safe basename resolution (hermetic via root=) ──────

def _stage_capture(root: Path, name: str, capture: dict):
    cdir = root / "captures"
    cdir.mkdir(parents=True, exist_ok=True)
    (cdir / name).write_text(json.dumps(capture), "utf-8")


def test_list_and_resolve_captures():
    root = Path(tempfile.mkdtemp())
    _stage_capture(root, "capture_alpha.json", _planted_capture())
    caps = da.list_captures(root=root)
    names = [c["name"] for c in caps]
    assert "capture_alpha.json" in names
    p = da.resolve_capture("capture_alpha.json", root=root)
    assert p is not None and p.is_file()


def test_resolve_rejects_traversal():
    root = Path(tempfile.mkdtemp())
    _stage_capture(root, "capture_alpha.json", _planted_capture())
    for bad in ("../etc/passwd", "/etc/passwd", "a/b.json", "..", ".hidden",
                "captures/capture_alpha.json"):
        assert da.resolve_capture(bad, root=root) is None, bad
    # an unknown but well-formed basename also resolves to None
    assert da.resolve_capture("capture_nope.json", root=root) is None


def test_analyze_capture_by_name():
    root = Path(tempfile.mkdtemp())
    _stage_capture(root, "capture_alpha.json", _planted_capture())
    res = da.analyze_capture("capture_alpha.json", root=root)
    assert res["ok"] is True
    assert res["capture"] == "capture_alpha.json"
    assert "supersecretmaskedvalue" not in res["html"]
    # unknown capture → ok False, error
    bad = da.analyze_capture("capture_missing.json", root=root)
    assert bad["ok"] is False and bad["error"] == "unknown capture"


def test_tree_view_limits_display():
    root = Path(tempfile.mkdtemp())
    _stage_capture(root, "capture_alpha.json", _planted_capture())
    res = da.tree_view("capture_alpha.json", root=root, max_depth=1, max_children=1)
    assert res["ok"] is True
    assert res["tree"] is not None


def test_analyze_test_against_captured_dom():
    root = Path(tempfile.mkdtemp())
    _stage_capture(root, "capture_alpha.json", _planted_capture())
    res = da.analyze_test("capture_alpha.json", ["button#dl-btn", ".nope-xyz"], root=root)
    assert res["ok"] is True
    by_sel = {r["selector"]: r for r in res["results"]}
    assert by_sel["button#dl-btn"].get("count", 0) >= 1
    assert by_sel[".nope-xyz"].get("count", 0) == 0
    # unknown capture → ok False
    assert da.analyze_test("capture_missing.json", ["a"], root=root)["ok"] is False

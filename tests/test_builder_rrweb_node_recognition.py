"""Builder recognition of the rrweb serialized-node capture format (H2 hardening).

Real captures store the DOM as rrweb serialized nodes (data.node on full snapshots,
data.adds[].node on incrementals), NOT an `html` string. These SYNTHETIC fixtures
verify the builder now derives selectors and modal-scoped row candidates from that
format — including subtrees added by interaction — while persisting no secrets and
staying backward compatible with old html-string captures.
"""
import json
import os
import shutil
import sys
import tempfile
import zipfile
from pathlib import Path

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_REPO, "tools"))

import build_template_from_wacz as btw  # noqa: E402
from bulk_downloader.capture_artifact_redact import scan_artifact_secrets  # noqa: E402


# ── rrweb node helpers (synthetic) ──────────────────────────────────────────
def el(tag, attrs=None, children=None, nid=1):
    return {"type": 2, "id": nid, "tagName": tag,
            "attributes": attrs or {}, "childNodes": children or []}

def txt(s, nid=99):
    return {"type": 3, "id": nid, "textContent": s}

def doc(children, nid=0):
    return {"type": 0, "id": nid, "childNodes": children}


def _make_wacz(capture: dict) -> Path:
    d = Path(tempfile.mkdtemp())
    wacz = d / "synthetic.wacz"
    with zipfile.ZipFile(wacz, "w") as z:
        z.writestr("archive/capture.json", json.dumps(capture))
    return wacz


# ── 1. node -> html serializer ───────────────────────────────────────────────
def test_node_to_html_serializes_elements_text_and_voids():
    node = el("div", {"class": "wrap"}, [
        el("input", {"id": "email", "type": "email"}),
        txt("hello"),
    ])
    html = btw._node_to_html(node)
    assert '<div class="wrap">' in html
    assert '<input id="email" type="email">' in html  # void tag, no close
    assert "hello" in html
    assert "</div>" in html


# ── 2. selectors from rrweb-node capture with NO html field ──────────────────
def _node_capture_no_html():
    snapshot = doc([el("html", {}, [el("body", {}, [
        el("input", {"id": "email", "type": "email"}),
        el("input", {"id": "password", "type": "password"}),
        el("button", {"type": "submit", "name": "submit"}, [txt("Login")]),
        el("div", {"aria-label": "video player"}, [
            el("button", {"class": "vjs-big-play-button"}),
        ]),
        el("div", {"class": "theo-settings-control-menu",
                   "aria-label": "Open the video quality settings menu"}),
    ])])])
    return {
        "url": "https://app.example.com/movie/1",
        "host": "app.example.com",
        "captured_at": "2026-06-08T00:00:00Z",
        "dom_log_count": 2,
        "network_log_count": 0,
        "dom_log": [
            {"type": "meta", "data": {}},
            {"type": "full_snapshot", "data": {"node": snapshot}},
        ],
        "network_log": [],
    }


def test_build_template_derives_selectors_from_rrweb_nodes():
    wacz = _make_wacz(_node_capture_no_html())
    try:
        draft = btw.build_template(wacz)
    finally:
        shutil.rmtree(wacz.parent, ignore_errors=True)
    sel = draft.get("selectors") or {}
    assert sel, "expected non-empty selectors from rrweb-node capture"
    assert sel.get("login", {}).get("email") == "input#email"
    assert sel.get("login", {}).get("password") == "input#password"
    assert sel.get("player", {}).get("container") == '[aria-label="video player"]'
    assert "quality" in sel


# ── 3. modal rows mined from INCREMENTAL adds (modal opened after snapshot) ──
def _capture_modal_in_adds():
    base = doc([el("html", {}, [el("body", {}, [el("div", {"id": "app"})])])])
    modal = el("div", {"class": "ant-modal", "aria-modal": "true"}, [
        el("ul", {}, [
            el("li", {"role": "row"}, [txt("1080p")], nid=11),
            el("li", {"role": "row"}, [txt("720p")], nid=12),
            el("li", {"role": "row"}, [txt("480p")], nid=13),
        ]),
    ])
    return {
        "url": "https://app.reptyle.com/movies/1",
        "host": "app.reptyle.com",
        "captured_at": "2026-06-08T00:00:00Z",
        "dom_log_count": 3,
        "network_log_count": 0,
        "dom_log": [
            {"type": "full_snapshot", "data": {"node": base}},
            {"type": "incremental",
             "data": {"adds": [{"parentId": 1, "nextId": None, "node": modal}]}},
        ],
        "network_log": [],
    }


def test_modal_rows_mined_from_incremental_adds():
    wacz = _make_wacz(_capture_modal_in_adds())
    try:
        draft = btw.build_template(wacz)
    finally:
        shutil.rmtree(wacz.parent, ignore_errors=True)
    dl = (draft.get("selectors") or {}).get("download") or {}
    rows = dl.get("row_selectors") or []
    assert rows, "expected modal-scoped row selectors mined from incremental adds"
    assert all(('ant-modal' in r) or ('aria-modal' in r) for r in rows), rows
    assert any('li[role="row"]' in r for r in rows), rows


# ── 4. backward compatibility: old html-string capture still works ───────────
def test_backward_compat_html_string_capture():
    cap = {
        "url": "https://app.example.com/x", "host": "app.example.com",
        "captured_at": "2026-06-08T00:00:00Z", "dom_log_count": 1, "network_log_count": 0,
        "dom_log": [{"type": "full_snapshot",
                     "html": '<input id="email"><div aria-label="video player"></div>'}],
        "network_log": [],
    }
    wacz = _make_wacz(cap)
    try:
        draft = btw.build_template(wacz)
    finally:
        shutil.rmtree(wacz.parent, ignore_errors=True)
    sel = draft.get("selectors") or {}
    assert sel.get("login", {}).get("email") == "input#email"
    assert sel.get("player", {}).get("container") == '[aria-label="video player"]'


# ── 5. F2: secret-looking attribute value never persists into the draft ──────
def test_no_secret_persisted_from_node_values():
    snap = doc([el("html", {}, [el("body", {}, [
        # worst case: an attribute carrying a secret-looking value
        el("input", {"id": "email", "type": "email",
                     "value": "syn.user@example.com"}),
        el("a", {"href": "https://cdn.example.com/v/clip.mp4?Signature=SYN_SIG&Expires=9"}),
    ])])])
    cap = {
        "url": "https://app.example.com/x", "host": "app.example.com",
        "captured_at": "2026-06-08T00:00:00Z", "dom_log_count": 1, "network_log_count": 0,
        "dom_log": [{"type": "full_snapshot", "data": {"node": snap}}],
        "network_log": [],
    }
    wacz = _make_wacz(cap)
    try:
        draft = btw.build_template(wacz)
    finally:
        shutil.rmtree(wacz.parent, ignore_errors=True)
    assert scan_artifact_secrets(draft) == [], scan_artifact_secrets(draft)
    # selector shape still derived
    assert (draft.get("selectors") or {}).get("login", {}).get("email") == "input#email"

"""v3.66.248 — direct-download-link rows.

build_template must file an ``<a>`` download anchor (a direct media link) under
``selectors.download.row_selectors``, not as a modal ``trigger``.

Some sites expose downloads as direct media links inline on the film page — no
modal, no resolution API — e.g. wowgirls' ``a.ct_dl_button[data-framerate]``
inside ``div.content_download``. The builder derives ``row_selectors`` from a
modal (``_modal_row_selectors_from_dom``); with no modal it found none, and the
operator action_timeline's download click (an ``<a>`` whose effect produced
direct media) was misfiled as ``trigger`` — so the template shipped with a
download link as its "opener" and ZERO rows. The fix makes the timeline
placement tag-aware: an ``<a>`` is the row, a ``<button>`` is the opener.

These build a minimal synthetic WACZ (``archive/capture.json``) and assert
build_template's download selectors. The real wowgirls capture is verified
out-of-band (a real WACZ is not shippable as a fixture)."""

import json
import tempfile
import zipfile
from pathlib import Path

from tools.build_template_from_wacz import build_template


def _wacz(capture: dict) -> Path:
    d = Path(tempfile.mkdtemp(prefix="bt248_"))
    z = d / "cap.wacz"
    with zipfile.ZipFile(z, "w") as zf:
        zf.writestr("archive/capture.json", json.dumps(capture))
        zf.writestr("datapackage.json", "{}")
    return z


def _el(tag, attrs=None, children=None):
    return {"type": 2, "tagName": tag, "attributes": attrs or {},
            "childNodes": children or []}


def _text(s):
    return {"type": 3, "textContent": s}


def _inline_download_dom():
    # div.content_download > ct_dl_fps_container > ul > li > a.ct_dl_button
    # NO modal-scope container anywhere -> _modal_row_selectors_from_dom == [].
    def row(res):
        return _el("li", {}, [_el(
            "a",
            {"class": "ct_dl_button", "data-framerate": "60fps",
             "href": "https://content-video.example-direct.com/download/abc/"
                     f"{res}_60FPS.mp4"},
            [_text(res + " 60fps")])])
    body = _el("body", {"id": "page_video", "class": "site user_logged"}, [
        _el("div", {"class": "content_download video_downloads"}, [
            _el("div", {"class": "ct_dl_fps_container ct_dl_fps_60"}, [
                _el("ul", {"class": "ct_dl_columns_2"},
                    [row("1920x1080"), row("3840x2160")])
            ])
        ])
    ])
    root = {"type": 0, "childNodes": [_el("html", {}, [body])]}
    return [{"type": "full_snapshot", "data": {"node": root}}]


def test_direct_link_anchor_becomes_row():
    cap = {
        "capture_kind": "dom",
        "host": "auth.example-direct.com",
        "url": "https://auth.example-direct.com/film/abc/title",
        "action_timeline": [
            {"selector": 'a.ct_dl_button[data-framerate="60fps"]',
             "role": "download link", "tag": "a",
             "effect": {"direct_media": 1, "nav": True, "signed": True}},
        ],
        "dom_log": _inline_download_dom(),
    }
    t = build_template(_wacz(cap))
    dn = (t.get("selectors") or {}).get("download") or {}
    rows = dn.get("row_selectors") or []
    assert "a.ct_dl_button[data-framerate]" in rows, \
        f"direct-link anchor should be a row; rows={rows} trigger={dn.get('trigger')}"
    assert dn.get("trigger") != "a.ct_dl_button[data-framerate]", \
        f"the download anchor must not be the trigger; trigger={dn.get('trigger')}"


def test_button_download_stays_trigger():
    # A <button> that produces a download is an OPENER -> trigger, not a row.
    cap = {
        "capture_kind": "dom",
        "host": "ex.example-modal.com",
        "url": "https://ex.example-modal.com/v/1",
        "action_timeline": [
            {"selector": "button.dl-now", "role": "download", "tag": "button",
             "effect": {"direct_media": 1}},
        ],
        "dom_log": [{"type": "full_snapshot", "data": {"node":
            {"type": 0, "childNodes": [_el("html", {}, [
                _el("body", {}, [_el("button", {"class": "dl-now"},
                                     [_text("Download")])])
            ])]}}}],
    }
    t = build_template(_wacz(cap))
    dn = (t.get("selectors") or {}).get("download") or {}
    assert dn.get("trigger"), "a <button> download opener should set a trigger"
    assert "button" in dn["trigger"], f"trigger should be the button; got {dn.get('trigger')}"
    assert "a.ct_dl_button[data-framerate]" not in (dn.get("row_selectors") or []), \
        "button opener must not populate an anchor row"

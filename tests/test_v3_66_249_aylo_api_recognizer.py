"""v3.66.249 — Aylo/Project1Service API-download recognizer + username login.

Two NON-guard builder fixes (``tools/build_template_from_wacz.py``):

FIX A — ``_merge_supplemental_api``: extraction_core (a GUARD) only flags an
``observed_api_host`` when the path matches the hardcoded reptyle shape
``/api/v{n}/movie/{id}/download-resolution/{res}``. Aylo platforms (bangbros /
brazzers / realitykings) expose downloads as ``GET .../v1/video-download/{id}``
on a DIFFERENT host than the content CDN — so the guard returns
``observed_api_hosts=[]`` and ``_download_api_template`` can build nothing, AND
``_download_api_template`` bails on >1 API host anyway. The builder-side
recognizer mines ``network_log`` for a download-API shape independently,
identifies the single download-API host SPECIFICALLY (multi-host safe), and
augments ``download_api_host`` + ``api_patterns`` so a review-only ``api_template``
candidate surfaces. It NEVER fabricates an API for a direct/HLS-only site, is
review-only, and is never the runtime base / never enabled. extraction_core
stays byte-identical.

FIX B — username login fallback: ``_html_selectors`` recognized the credential
field only by an email-ish id/name/type, so bangbros' ``#username`` (no email
token) was missed and the login shipped with password+submit only. A last-resort
fallback fills the canonical ``login['email']`` slot from a username/login id or
name — without grabbing a ``*-password`` field.

Synthetic WACZ fixtures (shippable). The real bang247/wow247 strict captures are
verified out-of-band and only if present (skip otherwise — F2: never embedded).
"""

import json
import os
import tempfile
import zipfile
from pathlib import Path

from tools.build_template_from_wacz import build_template, _html_selectors


# ── synthetic WACZ helpers (mirror test_v3_66_248) ───────────────────────────
def _wacz(capture: dict) -> Path:
    d = Path(tempfile.mkdtemp(prefix="bt249_"))
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


def _min_dom(body_children=None):
    body = _el("body", {"class": "site user_logged"}, body_children or [])
    root = {"type": 0, "childNodes": [_el("html", {}, [body])]}
    return [{"type": "full_snapshot", "data": {"node": root}}]


def _net(url, method="GET", rtype="xhr"):
    return {"url": url, "method": method, "resourceType": rtype}


def _dl(t):
    return ((t.get("selectors") or {}).get("download") or {})


# ── FIX A: the Aylo / multi-host download-API recognizer ─────────────────────
def test_aylo_multihost_download_api_becomes_review_candidate():
    # Aylo shape: download API on site-api.project1service.com, HLS + assets on
    # a DIFFERENT host (project1content.com) -> multi-host. The guard flags no
    # observed_api_host for /v1/video-download/{id}; pre-fix => no api_template.
    cap = {
        "capture_kind": "dom",
        "host": "members.bangbros.com",
        "url": "https://members.bangbros.com/video/12345/title",
        "network_log": [
            _net("https://site-api.project1service.com/v1/videos/12345"),
            _net("https://site-api.project1service.com/v1/video-download/12345"),
            _net("https://project1content.com/m/abc/master.m3u8", rtype="fetch"),
            _net("https://project1content.com/m/abc/seg-1.ts", rtype="media"),
        ],
        "dom_log": _min_dom([
            _el("span", {"class": "vjs-download", "title": "Download"},
                [_text("Download")])
        ]),
    }
    dn = _dl(build_template(_wacz(cap)))
    assert dn.get("api_template") == \
        "https://site-api.project1service.com/v1/video-download/{video_download_id}", \
        f"expected the identified download-API host candidate; got {dn.get('api_template')}"


def test_recognizer_dormant_on_direct_link_site():
    # wowgirls shape: inline direct .mp4 links, NO download API. The recognizer
    # must NOT fabricate an api_template (assets are skipped).
    cap = {
        "capture_kind": "dom",
        "host": "auth.wowgirls.com",
        "url": "https://auth.wowgirls.com/film/abc",
        "network_log": [
            _net("https://content-video2.wowgirls.com/download/abc/x1080_60FPS.mp4",
                 rtype="media"),
            _net("https://cdn.wowgirls.com/m/abc/master.m3u8", rtype="fetch"),
        ],
        "dom_log": _min_dom([
            _el("a", {"class": "ct_dl_button", "data-framerate": "60",
                      "href": "https://content-video2.wowgirls.com/download/abc/"
                              "x1080_60FPS.mp4"}, [_text("1080p")])
        ]),
    }
    dn = _dl(build_template(_wacz(cap)))
    assert not dn.get("api_template"), \
        f"direct-link site must not get a fabricated api_template; got {dn.get('api_template')}"


def test_recognizer_dormant_on_ambiguous_multi_download_host():
    # Two DIFFERENT hosts both expose a /download/ shape -> ambiguous which is
    # the real one -> conservative no-op (never guess the host).
    cap = {
        "capture_kind": "dom",
        "host": "ex.example.com",
        "url": "https://ex.example.com/v/1",
        "network_log": [
            _net("https://a.example.com/v1/video-download/1"),
            _net("https://b.example.com/api/download/1"),
        ],
        "dom_log": _min_dom([_el("span", {"class": "dl"}, [_text("Download")])]),
    }
    dn = _dl(build_template(_wacz(cap)))
    assert not dn.get("api_template"), \
        f"ambiguous multi download-host must no-op; got {dn.get('api_template')}"


# ── FIX B: username login fallback ───────────────────────────────────────────
def test_login_username_fallback_fills_credential():
    html = ('<form class="loginform">'
            '<input id="username" type="text" name="username">'
            '<input id="password" type="password" name="password">'
            '<button type="submit">Sign in</button></form>')
    login = (_html_selectors(html).get("login") or {})
    assert login.get("email") == "input#username", \
        f"username field should fill the credential slot; login={login}"
    assert login.get("password"), f"password still recognized; login={login}"


def test_login_username_fallback_does_not_grab_password_field():
    # A bare 'user-password' id must never be mistaken for the credential input.
    html = ('<form><input id="user-password" type="password">'
            '<input type="email" name="email"></form>')
    login = (_html_selectors(html).get("login") or {})
    cred = login.get("email") or ""
    assert "email" in cred and "password" not in cred, \
        f"real email field must win; the password id must not be the credential; login={login}"


# ── real-data verification (only if the strict captures are present) ─────────
_CORPUS = Path("/home/claude/corpus/wacz")


def test_real_bang247_yields_api_candidate():
    cap = _CORPUS / "bang247_redacted_strict.wacz"
    if not cap.exists():
        import pytest
        pytest.skip("bang247 strict capture not present")
    dn = _dl(build_template(cap))
    api = dn.get("api_template") or ""
    assert "video-download" in api and "{" in api, \
        f"bang247 should surface a templated video-download candidate; got {api!r}"
    assert "project1service" in api, f"host should be the download API host; got {api!r}"


def test_real_wow247_rows_no_api():
    cap = _CORPUS / "wow247_redacted_strict.wacz"
    if not cap.exists():
        import pytest
        pytest.skip("wow247 strict capture not present")
    dn = _dl(build_template(cap))
    assert dn.get("row_selectors"), f"wow247 should derive direct-link rows; dn={dn}"
    assert not dn.get("api_template"), \
        f"wow247 is direct-link, must not get an api_template; got {dn.get('api_template')}"

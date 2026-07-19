"""v3.66.8 #4 — POST /api/queue/v2/add_url contract.

Covers:
  - Endpoint registered, accepts POST only.
  - Rejects missing site_id with 400.
  - Rejects missing url with 400.
  - Rejects unknown site_id with 400.
  - Happy path: load_urls() is called on the right runner and the
    response carries (added, dupes, skipped) faithfully.
  - load_urls raising surfaces as 500 with a typed error message,
    not a bare 500 from Flask.

Style modeled on tests/test_d3_u5_queue.py — same isolated_bd_home
fixture (autouse chdir + env restore + sys.modules purge) and same
StubRunner pattern from tests/test_v3_43_51_wizard.py.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest



# v3.66.13: this file used to define its own autouse isolated_bd_home
# fixture; the canonical one lives in tests/conftest.py. The marker
# opts this file back into the sys.modules wipe (needed so the package
# re-reads env vars at import; see conftest.py for the full rationale).
pytestmark = pytest.mark.bd_module_wipe

_REPO = Path(__file__).resolve().parent.parent


# ── Stub runner ─────────────────────────────────────────────────────


class _StubRunner:
    """Minimal SiteRunner stand-in.

    The endpoint only touches `load_urls(urls)`, which should return
    `(added, dupes, skipped)` per the real signature. Other methods
    aren't exercised by this endpoint.
    """
    def __init__(self, ret=(1, 0, 0), raises=None):
        self._ret = ret
        self._raises = raises
        self.calls = []

    def load_urls(self, urls):
        self.calls.append(list(urls))
        if self._raises is not None:
            raise self._raises
        return self._ret


def _install_stub(site_id="sid_a", **kwargs):
    """Build a stub runner and register it on the app module under
    site_id. Returns the stub for assertion."""
    from bulk_downloader import app as a
    stub = _StubRunner(**kwargs)
    a.runners[site_id] = stub
    a.s_cfg[site_id] = {"name": site_id}
    return stub


# ── Route registration ──────────────────────────────────────────────


def test_route_registered():
    from bulk_downloader import app as a
    rules = {
        (r.rule, tuple(sorted(r.methods or ())))
        for r in a.app.url_map.iter_rules()
    }
    found = any(
        rule == "/api/queue/v2/add_url" and "POST" in methods
        for rule, methods in rules
    )
    assert found, "POST /api/queue/v2/add_url missing"


def test_rejects_get():
    from bulk_downloader import app as a
    r = a.app.test_client().get("/api/queue/v2/add_url")
    assert r.status_code == 405


# ── Validation ──────────────────────────────────────────────────────


def test_rejects_missing_site_id():
    from bulk_downloader import app as a
    r = a.app.test_client().post(
        "/api/queue/v2/add_url",
        json={"url": "https://example.com/v/1"},
    )
    assert r.status_code == 400
    body = r.get_json() or {}
    assert body.get("ok") is False
    assert "site_id" in (body.get("error") or "")


def test_rejects_empty_site_id():
    """Whitespace-only site_id is the same as missing — stripped first."""
    from bulk_downloader import app as a
    r = a.app.test_client().post(
        "/api/queue/v2/add_url",
        json={"site_id": "   ", "url": "https://example.com/v/1"},
    )
    assert r.status_code == 400


def test_rejects_missing_url():
    from bulk_downloader import app as a
    r = a.app.test_client().post(
        "/api/queue/v2/add_url",
        json={"site_id": "sid_a"},
    )
    assert r.status_code == 400
    body = r.get_json() or {}
    assert "url" in (body.get("error") or "")


def test_rejects_unknown_site():
    from bulk_downloader import app as a
    r = a.app.test_client().post(
        "/api/queue/v2/add_url",
        json={"site_id": "does-not-exist", "url": "https://example.com/v/1"},
    )
    assert r.status_code == 400
    body = r.get_json() or {}
    assert "unknown site_id" in (body.get("error") or "")


def test_rejects_empty_body():
    from bulk_downloader import app as a
    # No JSON body at all: should still 400, not crash to 500.
    r = a.app.test_client().post("/api/queue/v2/add_url")
    assert r.status_code == 400


# ── Happy path ──────────────────────────────────────────────────────


def test_happy_path_calls_load_urls():
    stub = _install_stub("sid_a", ret=(1, 0, 0))
    from bulk_downloader import app as a
    r = a.app.test_client().post(
        "/api/queue/v2/add_url",
        json={"site_id": "sid_a", "url": "https://example.com/v/1"},
    )
    assert r.status_code == 200, r.get_data(as_text=True)
    body = r.get_json()
    assert body == {
        "ok": True,
        "site_id": "sid_a",
        "url": "https://example.com/v/1",
        "added": 1,
        "dupes": 0,
        "skipped": 0,
    }
    assert stub.calls == [["https://example.com/v/1"]]


def test_happy_path_returns_dupes():
    """The endpoint surfaces load_urls's (added, dupes, skipped)
    tuple verbatim so the UI can show 'Already queued'."""
    _install_stub("sid_a", ret=(0, 1, 0))
    from bulk_downloader import app as a
    r = a.app.test_client().post(
        "/api/queue/v2/add_url",
        json={"site_id": "sid_a", "url": "https://example.com/v/1"},
    )
    assert r.status_code == 200
    body = r.get_json()
    assert body["added"] == 0
    assert body["dupes"] == 1
    assert body["skipped"] == 0


def test_happy_path_returns_skipped():
    _install_stub("sid_a", ret=(0, 0, 1))
    from bulk_downloader import app as a
    r = a.app.test_client().post(
        "/api/queue/v2/add_url",
        json={"site_id": "sid_a", "url": "https://example.com/v/1"},
    )
    body = r.get_json()
    assert body["added"] == 0
    assert body["dupes"] == 0
    assert body["skipped"] == 1


def test_picker_hint_fields_accepted_not_required():
    """The picker may pass click_selector/resolution/codec/fps/source_type
    as informational hints. The endpoint must accept them without
    erroring even though it doesn't (yet) consume them — forward
    compat with the planned per-job hint passthrough."""
    stub = _install_stub("sid_a", ret=(1, 0, 0))
    from bulk_downloader import app as a
    r = a.app.test_client().post(
        "/api/queue/v2/add_url",
        json={
            "site_id": "sid_a",
            "url": "https://example.com/v/1",
            "click_selector": "a.download.high",
            "resolution": "1080p",
            "codec": "h264",
            "fps": 30,
            "source_type": "resolution_download_card",
        },
    )
    assert r.status_code == 200, r.get_data(as_text=True)
    # Until a hint-slot lands in load_urls, we just call it with the
    # bare URL — assert that contract holds.
    assert stub.calls == [["https://example.com/v/1"]]


# ── Error path ──────────────────────────────────────────────────────


def test_load_urls_raise_surfaces_as_500():
    """If load_urls() raises, the endpoint must return a typed 500
    with a truncated message — not crash the worker and not 200."""
    _install_stub("sid_a", raises=RuntimeError("downstream went boom"))
    from bulk_downloader import app as a
    r = a.app.test_client().post(
        "/api/queue/v2/add_url",
        json={"site_id": "sid_a", "url": "https://example.com/v/1"},
    )
    assert r.status_code == 500
    body = r.get_json() or {}
    assert body.get("ok") is False
    err = body.get("error") or ""
    assert "RuntimeError" in err
    assert "downstream went boom" in err

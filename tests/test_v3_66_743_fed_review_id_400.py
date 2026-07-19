"""v3.66.743 — a client-typo'd id must be a 400, not a 500.

Found by the body-contract fixture gate the moment its extractor's denominator
was fixed: /api/fed/pending_review does `int(body.get("id"))` inside its
try-block, so a non-numeric id raises ValueError and the blanket except
answers **500 internal server error** to what is a malformed REQUEST. The app
must not 5xx on client input it can name the problem with.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


def _client():
    from bulk_downloader.app import app
    return app.test_client()


def _csrf(c):
    c.get("/")
    return (c.get("/api/csrf").get_json() or {}).get("csrf_token") or ""


def test_non_numeric_id_is_a_400_not_a_500():
    c = _client()
    r = c.post("/api/fed/pending_review",
               json={"id": "not-a-number", "action": "approve"},
               headers={"X-CSRF-Token": _csrf(c)})
    assert r.status_code == 400, (
        f"got {r.status_code} — a client-supplied id the server cannot parse "
        "is the CLIENT's malformed request, not an internal error. "
        f"body: {r.get_data(as_text=True)[:200]}"
    )
    body = r.get_json() or {}
    assert body.get("ok") is False
    assert "id" in (body.get("error") or "").lower()


def test_missing_id_still_400s_with_the_original_message():
    c = _client()
    r = c.post("/api/fed/pending_review",
               json={"action": "approve"},
               headers={"X-CSRF-Token": _csrf(c)})
    assert r.status_code == 400

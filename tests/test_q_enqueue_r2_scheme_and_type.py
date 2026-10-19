"""q-enqueue-r2: the two enqueue routes refuse what they cannot queue.

M6  POST /api/queue/v2/add_url with a non-http(s) URL (javascript:, file://)
    answered 200 {"added": 0}: the route never looked at the scheme and the
    runner's load_urls gate (runner_queue._is_queueable_url, ddr-fix F8)
    dropped it silently. The route now answers 400 before the runner is
    called, using the runner's own predicate.
M7  POST /api/bulk/enqueue stringified every element (`str(u).strip()`), so
    null / {} / 1 were queued as "None" / "{}" / "1". A non-string element now
    rejects the whole batch with 400 and the runner is never called.

RED at BASE 42825f50e: M6 answers 200, M7 answers 200.
"""
from __future__ import annotations

import pytest

import bulk_downloader.runner_queue as rq

BD_GATE_SCOPE = "module"

pytestmark = pytest.mark.bd_module_wipe


class _GateRunner:
    """Runner stand-in whose load_urls applies the real runner's F8 gate, so
    BASE reproduces the reported 200 {"added": 0}."""

    def __init__(self):
        self.calls = []

    def load_urls(self, urls):
        self.calls.append(list(urls))
        # Mirrors load_urls: a Phase 68 `url<TAB>Header=value` line is gated on
        # its url part only (runner_queue.load_urls tab split).
        added = sum(1 for u in urls if isinstance(u, str)
                    and rq._is_queueable_url(u.split("\t", 1)[0].strip()))
        return added, 0, 0

    def retryable_urls(self, urls):
        return []


def _install(sid):
    from bulk_downloader import app as a
    runner = _GateRunner()
    a.runners[sid] = runner
    a.s_cfg[sid] = {"name": sid}
    return runner


def _paired_client():
    from bulk_downloader import app as a
    from bulk_downloader.db import db_init
    db_init()
    c = a.app.test_client()
    token = c.get("/api/pair").get_json()["token"]
    csrf = c.post("/api/pair/redeem", json={"token": token}).get_json()["csrf_token"]
    return c, {"X-CSRF-Token": csrf}


# -- M6: add_url scheme gate ------------------------------------------------


@pytest.mark.parametrize("url", [
    "javascript:alert(1)",
    "file:///etc/passwd",
    "ftp://example.com/v/1",
    "http:///no-host",
])
def test_m6_add_url_refuses_non_http_scheme(url):
    runner = _install("q_m6")
    from bulk_downloader import app as a
    r = a.app.test_client().post(
        "/api/queue/v2/add_url", json={"site_id": "q_m6", "url": url})
    body = r.get_json() or {}
    assert r.status_code == 400, (r.status_code, body)
    assert body.get("ok") is False
    assert "url must be an http(s) URL with a host" in (body.get("error") or "")
    assert runner.calls == []


def test_m6_control_mixed_case_https_still_queued():
    runner = _install("q_m6_ok")
    from bulk_downloader import app as a
    r = a.app.test_client().post(
        "/api/queue/v2/add_url",
        json={"site_id": "q_m6_ok", "url": "HTTPS://Example.com/v/1"})
    assert r.status_code == 200, r.get_data(as_text=True)
    assert r.get_json()["added"] == 1
    assert runner.calls == [["HTTPS://Example.com/v/1"]]


# -- r3 F1: add_url gates the url part the runner gates, not the raw line ----


@pytest.mark.parametrize("url", ["http\t://x/", "ht\ttp://x/", "http:\t//x/"])
def test_f1_add_url_refuses_tab_split_non_url(url):
    runner = _install("q_f1")
    from bulk_downloader import app as a
    r = a.app.test_client().post(
        "/api/queue/v2/add_url", json={"site_id": "q_f1", "url": url})
    body = r.get_json() or {}
    assert r.status_code == 400, (r.status_code, body)
    assert "url must be an http(s) URL with a host" in (body.get("error") or "")
    assert runner.calls == []


@pytest.mark.parametrize("url", ["http://x\tCookie=a[b", "http://x/p\tReferer=http://y/"])
def test_f1_add_url_tab_header_line_still_queued(url):
    runner = _install("q_f1_ok")
    from bulk_downloader import app as a
    r = a.app.test_client().post(
        "/api/queue/v2/add_url", json={"site_id": "q_f1_ok", "url": url})
    assert r.status_code == 200, r.get_data(as_text=True)
    assert r.get_json()["added"] == 1
    assert runner.calls == [[url]]


# -- M7: bulk_enqueue element type gate -------------------------------------


@pytest.mark.parametrize("bad", [None, {"u": "http://x/1"}, 1, ["http://x/1"]])
def test_m7_bulk_enqueue_refuses_non_string_url(bad):
    c, h = _paired_client()
    runner = _install("q_m7")
    r = c.post("/api/bulk/enqueue",
               json={"site_id": "q_m7", "urls": ["http://x/ok", bad]}, headers=h)
    body = r.get_json() or {}
    assert r.status_code == 400, (r.status_code, body)
    assert body.get("ok") is False
    assert "urls must be a list of strings" in (body.get("error") or "")
    assert runner.calls == []


def test_m7_control_string_batch_still_enqueued_blanks_skipped():
    c, h = _paired_client()
    runner = _install("q_m7_ok")
    r = c.post("/api/bulk/enqueue",
               json={"site_id": "q_m7_ok", "urls": [" http://x/1 ", "", "http://x/2"]},
               headers=h)
    assert r.status_code == 200, r.get_data(as_text=True)
    d = r.get_json()
    assert d["added"] == 2 and d["requested"] == 3
    assert runner.calls == [["http://x/1", "http://x/2"]]

"""sec2-job-url-token r1 (BD-SEC-2 withhold r3, PM item b).

A job URL carrying ?token= keys a queue/history row. The 8 queue/history
routes serve a display URL (every non-plain query value masked) plus the
row id; retry, re-queue and dedupe key on the row id server-side; the queue
export redacts tokens unless a local backup asks for them.
"""
from __future__ import annotations

import json
from urllib.parse import unquote

import pytest

BD_GATE_SCOPE = "module"

CANARY = "bdsecJUTQ7"
STUCK_CANARY = "bdsecJUTstuckQ7"
JOB = f"https://r.example/dl/a.mp4?token={CANARY}&page=2"
STUCK = f"https://r.example/dl/b.mp4?sig={STUCK_CANARY}"
PLAIN = "https://r.example/gallery/1?page=2"

ROUTES = (
    "/api/sites/{sid}/queue", "/api/sites/{sid}/queue/search", "/api/sites/{sid}/queue/search?q=r.example",
    "/api/sites/{sid}/queue/grouped", "/api/sites/{sid}/queue/grouped?by=path",
    "/api/sites/{sid}/queue/export", "/api/sites/{sid}/history/export",
    "/api/sites/{sid}/history/export?fmt=csv", "/api/sites/{sid}/export_watchlist?status=all&include_message=1",
    "/api/queue/v2", "/api/data/queue_analytics",
)


def _has(text: str, canary: str) -> bool:
    for _ in range(6):
        if canary in text:
            return True
        text, prev = unquote(text), text
        if text == prev:
            break
    return canary in text


def _plant(A, client) -> str:
    from bulk_downloader.db import db_log, queue_upsert
    sid = client.post("/api/sites", json={"name": "jut-r1"}).get_json()["id"]
    runner = A.runners[sid]
    for u in (JOB, STUCK, PLAIN):
        runner.add_url(u)
    runner._update_job(JOB, "failed", f"fetch failed for {JOB}")
    runner._update_job(PLAIN, "failed", f"fetch failed for {PLAIN}")
    queue_upsert(sid, STUCK, status="pending", retries=5)
    db_log(sid, "jut-r1", JOB, "failed", message=f"fetch failed for {JOB}")
    return sid


def _get(client, path: str, **kw) -> str:
    r = client.get(path, **kw)
    assert r.status_code == 200, f"BD_SEC2_JOB_URL_TOKEN_PROBE_BLIND: {path} -> {r.status_code}"
    return r.get_data(as_text=True)


def test_no_token_reaches_any_queue_or_history_body(fresh_app):
    from bulk_downloader import app as A
    sid = _plant(A, fresh_app)
    served = {p: _get(fresh_app, p.replace("{sid}", sid)) for p in ROUTES}
    leaks = sorted(p for p, t in served.items() if _has(t, CANARY) or _has(t, STUCK_CANARY))
    assert not leaks, f"BD_SEC2_JOB_URL_TOKEN_LEAK: {leaks}"
    # positive controls: every route answered with the planted rows, not an empty body
    blind = sorted(p for p, t in served.items() if "r.example" not in t)
    assert not blind, f"BD_SEC2_JOB_URL_TOKEN_PROBE_BLIND: rows absent on {blind}"
    from bulk_downloader.db import queue_row_id
    rid = queue_row_id(JOB)
    unkeyed = sorted(p for p, t in served.items() if t.lstrip().startswith("{")
                     and "/queue_analytics" not in p and rid not in t)
    assert not unkeyed, f"BD_SEC2_JOB_URL_TOKEN_UNKEYED: row id {rid} absent on {unkeyed}"


def test_display_url_masks_each_token_value_and_keeps_plain_urls():
    from bulk_downloader.db import queue_display_url
    assert queue_display_url(JOB) == "https://r.example/dl/a.mp4?token=***&page=2"
    assert queue_display_url("https://u:pw@r.example/x?a=1&sig=2") == "https://***@r.example/x?a=***&sig=***"
    for v in (PLAIN, "https://plain.example/a/b", "", "not a url"):
        assert queue_display_url(v) == v, f"BD_SEC2_JOB_URL_TOKEN_PLAIN: {v!r} -> {queue_display_url(v)!r}"


def test_queue_export_includes_tokens_only_on_a_local_opt_in(fresh_app):
    from bulk_downloader import app as A
    sid = _plant(A, fresh_app)
    path = f"/api/sites/{sid}/queue/export?include_tokens=1"
    assert _has(_get(fresh_app, path), CANARY), "BD_SEC2_JOB_URL_TOKEN_PROBE_BLIND: local opt-in"
    remote = fresh_app.get(path, environ_base={"REMOTE_ADDR": "10.0.0.5"})
    assert remote.status_code == 403 and not _has(remote.get_data(as_text=True), CANARY), (
        f"BD_SEC2_JOB_URL_TOKEN_LEAK: include_tokens from a remote client -> {remote.status_code}")


@pytest.mark.parametrize("key", ["row_id", "display_url", "url"])
def test_retry_by_row_still_works(fresh_app, key):
    from bulk_downloader import app as A
    from bulk_downloader.db import queue_display_url, queue_row_id
    sid = _plant(A, fresh_app)
    body = {"row_id": {"row_id": queue_row_id(JOB)}, "display_url": {"url": queue_display_url(JOB)},
            "url": {"url": JOB}}[key]
    r = fresh_app.post(f"/api/sites/{sid}/retry_one", json=body)
    assert r.status_code == 200 and A.runners[sid].jobs[JOB]["status"] == "pending", (
        f"BD_SEC2_JOB_URL_TOKEN_RETRY: {key} -> {r.status_code} {r.get_json()}")


def test_bulk_retry_and_cancel_key_on_the_row(fresh_app):
    from bulk_downloader import app as A
    from bulk_downloader.db import queue_row_id
    sid = _plant(A, fresh_app)
    r = fresh_app.post(f"/api/sites/{sid}/bulk_retry", json={"urls": [queue_row_id(JOB)]})
    assert r.get_json().get("retried") == 1, f"BD_SEC2_JOB_URL_TOKEN_RETRY: bulk {r.get_json()}"
    v2 = json.loads(_get(fresh_app, "/api/queue/v2"))
    shown = [j for b in ("running", "waiting", "terminal") for j in v2.get(b) or [] if j.get("site_id") == sid]
    row = [j for j in shown if j.get("row_id") == queue_row_id(JOB)]
    assert row, f"BD_SEC2_JOB_URL_TOKEN_UNKEYED: v2 {shown}"
    c = fresh_app.post("/api/queue/v2/cancel", json={"site_id": sid, "url": row[0]["url"]})
    assert c.status_code == 200 and A.runners[sid].jobs[JOB]["status"] == "stopped", (
        f"BD_SEC2_JOB_URL_TOKEN_RETRY: v2 cancel by display url -> {c.status_code} {c.get_json()}")


def test_redacted_export_imports_back_without_masked_rows(fresh_app):
    from bulk_downloader import app as A
    sid = _plant(A, fresh_app)
    rows = json.loads(_get(fresh_app, f"/api/sites/{sid}/queue/export"))["rows"]
    again = fresh_app.post(f"/api/sites/{sid}/queue/import", json={"rows": rows}).get_json()
    assert again.get("added") == 0, f"BD_SEC2_JOB_URL_TOKEN_DEDUPE: same-site import {again}"
    other = fresh_app.post("/api/sites", json={"name": "jut-r1-b"}).get_json()["id"]
    got = fresh_app.post(f"/api/sites/{other}/queue/import", json={"rows": rows}).get_json()
    jobs = list(A.runners[other].jobs)
    assert PLAIN in jobs and not any("***" in u for u in jobs) and got.get("tokens_dropped") == 2, (
        f"BD_SEC2_JOB_URL_TOKEN_DEDUPE: masked row imported as a job {jobs} {got}")


# sec2-job-url-token r2 (A6 security REFUTE F1/F2/F3): the shipped Caddyfile
# proxies from 127.0.0.1, so a forwarding header from a loopback peer is a
# remote client; jobs/detail serves the job's remediation advice, which quotes
# the job URL; a scheme-relative URL's userinfo is a credential too.
EXPORTS = ("/api/sites/{sid}/queue/export?include_tokens=1",
           "/api/sites/{sid}/history/export?include_tokens=1",
           "/api/sites/{sid}/history/export?fmt=csv&include_tokens=1")
PROXIED = {"X-Forwarded-For": {"X-Forwarded-For": "203.0.113.9"},
           "Forwarded": {"Forwarded": "for=203.0.113.9"},
           "X-Real-IP": {"X-Real-IP": "203.0.113.9"}}


@pytest.mark.parametrize("hdr", sorted(PROXIED))
@pytest.mark.parametrize("path", EXPORTS)
def test_include_tokens_refuses_a_client_behind_a_loopback_proxy(fresh_app, path, hdr):
    from bulk_downloader import app as A
    p = path.replace("{sid}", _plant(A, fresh_app))
    local = {"REMOTE_ADDR": "127.0.0.1"}
    assert _has(_get(fresh_app, p, environ_base=local), CANARY), "BD_SEC2_JOB_URL_TOKEN_PROBE_BLIND: local"
    r = fresh_app.get(p, environ_base=local, headers=PROXIED[hdr])
    assert r.status_code == 403 and not _has(r.get_data(as_text=True), CANARY), (
        f"BD_SEC2_JOB_URL_TOKEN_PROXIED_LOOPBACK: {hdr} via a loopback proxy -> {r.status_code}")


DETAIL_FORMS = {  # message -> the advisor branch it takes
    "fallback": ("https://r.example/a?key={c}", "fetch failed for {u}", "bdsecJUTdetQ7"),
    "dns": ("https://u:{c}@r.example/a", "getaddrinfo failed for {u}", "bdsecJUTdnsQ7"),
    "tls": ("https://r.example/a?X-Amz-Signature={c}", "SSL: CERTIFICATE_VERIFY_FAILED at {u}", "bdsecJUTtlsQ7"),
}


@pytest.mark.parametrize("form", sorted(DETAIL_FORMS))
def test_job_detail_by_row_id_withholds_the_token_in_its_remediation(fresh_app, form):
    from bulk_downloader import app as A
    from bulk_downloader.db import queue_row_id
    f, msg, canary = DETAIL_FORMS[form]
    url = f.format(c=canary)
    sid = fresh_app.post("/api/sites", json={"name": f"jut-r2-{form}"}).get_json()["id"]
    A.runners[sid].add_url(url)
    A.runners[sid]._update_job(url, "failed", msg.format(u=url))
    assert canary in json.dumps(A.runners[sid].jobs[url].get("remediation")), (
        "BD_SEC2_JOB_URL_TOKEN_PROBE_BLIND: the advice does not quote the url")
    text = _get(fresh_app, f"/api/sites/{sid}/jobs/detail?row_id={queue_row_id(url)}")
    assert '"remediation"' in text, "BD_SEC2_JOB_URL_TOKEN_PROBE_BLIND: no remediation served"
    assert not _has(text, canary), f"BD_SEC2_JOB_URL_TOKEN_DETAIL_REMEDIATION: {form}"


def test_display_url_masks_scheme_relative_userinfo():
    from bulk_downloader.db import queue_display_url
    assert queue_display_url("//u:bdsecJUTslQ7@r.example/a") == "//***@r.example/a", (
        "BD_SEC2_JOB_URL_TOKEN_SCHEMELESS")
    assert queue_display_url("//r.example/a") == "//r.example/a", "BD_SEC2_JOB_URL_TOKEN_PLAIN: schemeless"


USERINFO_FORMS = {  # r3: a token carried as the userinfo USER (GitHub PAT form) or alone
    "user-is-token": "https://{c}:x-oauth-basic@r.example/a",
    "user-only": "https://{c}@r.example/a",
    "user-and-pw-port": "https://{c}:pw@r.example:8443/a",
    "scheme-relative": "//{c}:x@r.example/a",
}


@pytest.mark.parametrize("form", sorted(USERINFO_FORMS))
def test_display_url_masks_the_whole_userinfo(form):
    from bulk_downloader.db import queue_display_url, queue_url_masked
    shown = queue_display_url(USERINFO_FORMS[form].format(c="bdsecJUTuiQ7"))
    assert not _has(shown, "bdsecJUTuiQ7"), f"BD_SEC2_JOB_URL_TOKEN_USERINFO: {form} -> {shown!r}"
    assert "r.example" in shown and queue_url_masked(shown), (
        f"BD_SEC2_JOB_URL_TOKEN_PROBE_BLIND: {form} -> {shown!r}")


def test_token_as_userinfo_user_is_withheld_over_http(fresh_app):
    from bulk_downloader import app as A
    from bulk_downloader.db import queue_row_id
    canary = "bdsecJUTuserQ7"
    url = f"https://{canary}:x-oauth-basic@r.example/a"
    sid = fresh_app.post("/api/sites", json={"name": "jut-r3"}).get_json()["id"]
    fresh_app.post(f"/api/sites/{sid}/load_urls", json={"text": url})
    assert url in A.runners[sid].jobs, "BD_SEC2_JOB_URL_TOKEN_PROBE_BLIND: load_urls refused the form"
    A.runners[sid]._update_job(url, "failed", f"fetch failed for {url}")
    paths = ("/api/queue/v2", f"/api/sites/{sid}/queue", f"/api/sites/{sid}/queue/export",
             f"/api/sites/{sid}/jobs/detail?row_id={queue_row_id(url)}")
    served = {p: _get(fresh_app, p) for p in paths}
    blind = sorted(p for p, t in served.items() if "r.example" not in t)
    assert not blind, f"BD_SEC2_JOB_URL_TOKEN_PROBE_BLIND: rows absent on {blind}"
    leaks = sorted(p for p, t in served.items() if _has(t, canary))
    assert not leaks, f"BD_SEC2_JOB_URL_TOKEN_USERINFO: {leaks}"

"""Bug-hunt ddr-fix f8-f11 r1 (deep-dl-runner-state sweep, findings F8, F10, F11).

Rules from RULING-ddr-F8-F10-F11-adjB-20261014T0443Z:
  F8  only http/https with a non-empty host is queued; anything else is
      refused visibly (an ``invalid_url`` event naming it, a count the caller
      can see) and is never queued or counted as a duplicate.
  F10 the dedupe KEY is RFC 3986 6.2.2/6.2.3 only: scheme and host lowercased
      (userinfo kept), default port dropped; path, trailing slash, query and
      fragment exact. The job keeps the first-seen string.
  F11 a duplicate of a live job carrying different headers is reported and
      the live job keeps its own; a re-armed 'stopped' job is a fresh job and
      takes the new entry's headers, none when it has none (row 722).

Each ``*_ctl`` test is a control that passes on the base as well. Real
SiteRunner objects over the isolated per-test DB; no browser runs.
"""
import pytest

from bulk_downloader import db
from bulk_downloader.runner import SiteRunner

BD_GATE_SCOPE = "module"

U = "https://ex.test/scene/1"
REF_A = {"Referer": "https://a.test/"}
REF_B = {"Referer": "https://b.test/"}


@pytest.fixture
def mk():
    db.db_init()
    live = []
    n = [0]

    def make(sid=None, **cfg):
        n[0] += 1
        sid = sid or f"ddrf{n[0]}"
        r = SiteRunner(sid, dict({"name": sid}, **cfg))
        live.append(r)
        return r

    yield make
    for r in live:
        r._sched_stop.set()


def rows(sid):
    return {x["url"]: x for x in db.queue_load(sid)}


def record(r):
    events = []
    orig = r.log_event
    r.log_event = lambda kind, msg, **k: events.append((kind, msg, k)) or orig(kind, msg, **k)
    return events


def of(events, kind):
    return [(m, k) for kd, m, k in events if kd == kind]


def with_headers(url, hdrs):
    return url + "".join(f"\t{k}={v}" for k, v in hdrs.items())


def stop(r, url):
    r.jobs[url].update(status="stopped", message="Stopped")
    db.queue_upsert(r.site_id, url, status="stopped", message="Stopped")


# ── F8 S08: only http(s) with a host is queued ───────────────────────────
@pytest.mark.parametrize("bad", [
    "", "   ", "ftp://ex.test/a.mp4", "javascript:alert(1)", "http://",
    "magnet:?xt=urn:btih:abc", "data:text/plain,hi", "file:///etc/passwd",
])
def test_f8_s08_non_http_url_refused_and_counted(mk, bad):
    r = mk()
    ev = record(r)
    res = r.load_urls([bad, U])
    assert list(r.jobs) == [U], f"F8: {bad!r} was queued: {list(r.jobs)!r}"
    assert list(rows(r.site_id)) == [U]
    imp = of(ev, "import")
    assert "1 invalid" in imp[0][0], f"F8: refusal of {bad!r} not counted: {imp[0][0]!r}"
    assert imp[0][1]["extra"]["invalid"] == 1 and imp[0][1]["extra"]["dupes"] == 0
    assert [k["url"] for _m, k in of(ev, "invalid_url")] == [bad.strip()]
    assert res == (1, 0, 0), "F8: an invalid URL must not be counted as a duplicate"
    assert res.invalid == 1


def test_f8_s08_webhook_rewrite_to_non_http_refused(mk, monkeypatch):
    from bulk_downloader import hooks
    monkeypatch.setattr(hooks, "webhook_pre_url_added",
                        lambda cfg, u: (True, "ftp://ex.test/x", None))
    r = mk(webhook_events="pre_url_added", webhook_urls="http://hook.test/")
    res = r.load_urls([U])
    assert r.jobs == {}, f"F8: webhook rewrite to ftp was queued: {list(r.jobs)!r}"
    assert res == (0, 0, 0) and res.invalid == 1


def test_f8_s08_ctl_http_and_https_with_host_queued(mk):
    r = mk()
    ev = record(r)
    urls = ["http://ex.test/a", "https://ex.test/b", "HTTPS://EX.TEST/c"]
    assert r.load_urls(urls) == (3, 0, 0)
    assert list(r.jobs) == urls
    assert not of(ev, "invalid_url")


# ── F10 S03/S09: the dedupe key is the RFC 3986 normal form ──────────────
@pytest.mark.parametrize("first,second", [
    ("HTTP://X.COM:80/a", "http://x.com/a"),
    ("https://Ex.Test:443/a?q=1", "HTTPS://ex.test/a?q=1"),
])
def test_f10_s03_case_and_default_port_are_one_job(mk, first, second):
    r = mk()
    assert r.load_urls([first]) == (1, 0, 0)
    res = r.load_urls([second])
    assert list(r.jobs) == [first], f"F10: {second!r} queued beside {first!r}"
    assert list(rows(r.site_id)) == [first]
    assert res == (0, 1, 0)


def test_f10_s03_in_batch_spellings_are_one_job(mk):
    r = mk()
    assert r.load_urls(["HTTP://X.COM:80/a", "http://x.com/a"]) == (1, 1, 0)
    assert list(r.jobs) == ["HTTP://X.COM:80/a"]


def test_f10_s09_stopped_job_rearmed_by_another_spelling(mk):
    r = mk()
    r.load_urls(["HTTP://X.COM/a"])
    stop(r, "HTTP://X.COM/a")
    assert r.load_urls(["http://x.com:80/a"]) == (1, 0, 0)
    assert list(r.jobs) == ["HTTP://X.COM/a"], f"F10: second job {list(r.jobs)!r}"
    assert r.jobs["HTTP://X.COM/a"]["status"] == "pending"
    assert rows(r.site_id)["HTTP://X.COM/a"]["status"] == "pending"


@pytest.mark.parametrize("a,b", [
    ("http://x.com/a#1", "http://x.com/a#2"),
    ("http://x.com/a", "http://x.com/a/"),
    ("http://u:Pw@x.com/a", "http://u:pw@x.com/a"),
    ("http://x.com/a?q=1", "http://x.com/a?Q=1"),
    ("http://x.com/A", "http://x.com/a"),
    ("http://x.com:8080/a", "http://x.com/a"),
    ("http://x.com:443/a", "https://x.com/a"),
    ("http://bücher.example/a", "http://xn--bcher-kva.example/a"),
])
def test_f10_ctl_not_one_job(mk, a, b):
    r = mk()
    assert r.load_urls([a, b]) == (2, 0, 0)
    assert list(r.jobs) == [a, b]


# ── r2 (R163-A F1): RFC 3986 6.2.3 -- an empty query/fragment delimiter
# is kept; "/a" and "/a?" are not one job ─────────────────────────────────
@pytest.mark.parametrize("a,b", [
    ("http://x.test/a", "http://x.test/a?"),
    ("http://x.test/a", "http://x.test/a#"),
    ("http://x.test/a?", "http://x.test/a"),
    ("http://x.test/a#", "http://x.test/a"),
    ("http://x.test/a", "http://x.test/a?#"),
    ("http://x.test/a?", "http://x.test/a?#"),
    ("http://x.test", "http://x.test?"),
    ("http://x.test/a#", "http://x.test/a#?"),
])
def test_r2_f1_empty_delimiter_is_not_dropped(mk, a, b):
    r = mk()
    res = r.load_urls([a, b])
    assert list(r.jobs) == [a, b], (
        f"R163-A F1: {b!r} keyed as a dupe of {a!r} (empty delimiter dropped)")
    assert res == (2, 0, 0)


@pytest.mark.parametrize("first,second", [
    ("HTTP://X.TEST:80/a?", "http://x.test/a?"),
    ("HTTP://X.TEST/a#", "http://x.test:80/a#"),
    ("http://X.test/a#x?y", "http://x.test/a#x?y"),
])
def test_r2_f1_ctl_empty_delimiter_spellings_still_one_job(mk, first, second):
    r = mk()
    assert r.load_urls([first, second]) == (1, 1, 0)
    assert list(r.jobs) == [first]


# ── F11 S04: headers on a duplicate are reported, never merged ───────────
def test_f11_s04_live_dupe_keeps_headers_and_is_reported(mk):
    r = mk()
    r.load_urls([with_headers(U, REF_A)])
    ev = record(r)
    res = r.load_urls([with_headers(U, REF_B)])
    assert r.jobs[U]["custom_headers"] == REF_A
    assert db.queue_headers_decode(rows(r.site_id)[U]["custom_headers"]) == REF_A
    conf = of(ev, "header_conflict")
    assert [k["url"] for _m, k in conf] == [U], "F11: new headers dropped silently"
    imp = of(ev, "import")
    assert "1 duplicate(s) with different headers" in imp[0][0]
    assert imp[0][1]["extra"]["header_conflicts"] == 1
    assert res == (0, 1, 0) and res.header_conflicts == 1


def test_f11_s04_key_dupe_with_other_headers_is_reported(mk):
    r = mk()
    r.load_urls([with_headers("HTTP://X.COM/a", REF_A)])
    res = r.load_urls([with_headers("http://x.com/a", REF_B)])
    assert list(r.jobs) == ["HTTP://X.COM/a"]
    assert r.jobs["HTTP://X.COM/a"]["custom_headers"] == REF_A
    assert res.header_conflicts == 1


@pytest.mark.parametrize("again", [with_headers(U, REF_A), U])
def test_f11_ctl_same_or_no_headers_not_reported(mk, again):
    r = mk()
    r.load_urls([with_headers(U, REF_A)])
    ev = record(r)
    assert r.load_urls([again]) == (0, 1, 0)
    assert r.jobs[U]["custom_headers"] == REF_A
    assert not of(ev, "header_conflict")


def test_f11_row722_stopped_rearm_takes_new_headers(mk):
    r = mk()
    r.load_urls([with_headers(U, REF_A)])
    stop(r, U)
    ev = record(r)
    assert r.load_urls([with_headers(U, REF_B)]) == (1, 0, 0)
    assert r.jobs[U]["custom_headers"] == REF_B, "F11: re-armed job kept old headers"
    assert db.queue_headers_decode(rows(r.site_id)[U]["custom_headers"]) == REF_B
    assert mk(r.site_id).jobs[U]["custom_headers"] == REF_B
    assert not of(ev, "header_conflict")


def test_f11_row722_stopped_rearm_without_headers_clears_them(mk):
    r = mk()
    r.load_urls([with_headers(U, REF_A)])
    stop(r, U)
    assert r.load_urls([U]) == (1, 0, 0)
    assert "custom_headers" not in r.jobs[U], "F11: re-armed job kept old headers"
    assert rows(r.site_id)[U]["custom_headers"] is None
    assert "custom_headers" not in mk(r.site_id).jobs[U]


def test_f11_ctl_stopped_rearm_plain_stays_plain(mk):
    r = mk()
    r.load_urls([U])
    stop(r, U)
    assert r.load_urls([U]) == (1, 0, 0)
    assert r.jobs[U]["status"] == "pending"
    assert "custom_headers" not in r.jobs[U]

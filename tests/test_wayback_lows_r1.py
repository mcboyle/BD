"""wayback-lows r1: outbound-integrations FINDINGS F7, F8, F9 (LOW).

F7  wayback_cdx.find_snapshots cached by URL alone and pinned [] for the
    process life: a query with other filters got the first rows (S22) and
    one empty answer hid every later capture (S23). The key now holds every
    input that shapes the answer, and empty answers are not cached.
F8  limit was sent to CDX but not applied to the answer (300k rows -> 300k
    snapshots, S24) and a negative limit was sent as is (S25). limit is
    clamped to 1..500 and the answer holds at most limit rows.
F9  availability returned closest.url with any scheme (javascript:, S28);
    build_archive_url took "../../x" and javascript: (S30). Both now need an
    http(s) URL; build_archive_url also needs an all-digit timestamp and no
    "." / ".." path segment in the original URL.

No archive.org traffic: wayback_cdx.requests is a fake per test.
"""

# An ordinary module test: its subject is the module under test, not the
# tree, so it is not a repo-wide CI gate.
BD_GATE_SCOPE = "module"

import types
from collections import OrderedDict

import pytest
import requests

from bulk_downloader import wayback_cdx

_HDR = ["timestamp", "original", "statuscode", "mimetype"]


class _Resp:
    def __init__(self, data=None, ok=True):
        self.ok = ok
        self._data = data

    def json(self):
        return self._data


@pytest.fixture
def cdx(monkeypatch):
    """Queue answers; record (url, params) of every outbound call."""
    answers, calls = [], []

    def fake_get(url, params=None, headers=None, timeout=None):
        calls.append(
            (url, list(params.items()) if isinstance(params, dict) else list(params))
        )
        return answers.pop(0) if answers else _Resp(data=[])

    monkeypatch.setattr(
        wayback_cdx,
        "requests",
        types.SimpleNamespace(
            get=fake_get,
            Timeout=requests.Timeout,
            ConnectionError=requests.ConnectionError,
        ),
    )
    monkeypatch.setattr(wayback_cdx, "_HAS_REQUESTS", True)
    monkeypatch.setattr(wayback_cdx, "_cache", OrderedDict())
    return answers, calls


def _rows(host, n, ts="2020"):
    return [_HDR] + [
        [f"{ts}{i:010d}", f"http://{host}/{i}", "200", "text/html"] for i in range(n)
    ]


def _sent(call, name):
    return [v for k, v in call[1] if k == name]


# ---- F7 -----------------------------------------------------------------


def test_cache_key_holds_every_filter(cdx):
    answers, calls = cdx
    answers.append(_Resp(data=_rows("a.test", 1, "2020")))
    a = wayback_cdx.find_snapshots("http://a.test/", from_ts="2020")
    answers.append(
        _Resp(data=[_HDR, ["20100101000000", "http://a.test/", "404", "text/html"]])
    )
    b = wayback_cdx.find_snapshots(
        "http://a.test/", from_ts="2009", to_ts="2011", only_success=False, limit=5
    )
    assert len(calls) == 2 and b[0]["status_code"] == "404", (
        f"WBL1-F7-KEY-IGNORES-FILTERS: {len(calls)} calls, second got {b}"
    )
    assert a[0]["timestamp"] == "20200000000000"


@pytest.mark.parametrize(
    "field,kw",
    [
        ("from_ts", {"from_ts": "2019"}),
        ("to_ts", {"to_ts": "2021"}),
        ("only_success", {"only_success": False}),
        ("limit", {"limit": 7}),
    ],
)
def test_each_filter_alone_misses_the_cache(cdx, field, kw):
    answers, calls = cdx
    answers.extend([_Resp(data=_rows("f.test", 1)), _Resp(data=_rows("f.test", 2))])
    wayback_cdx.find_snapshots("http://f.test/", limit=50)
    wayback_cdx.find_snapshots("http://f.test/", **{"limit": 50, **kw})
    assert len(calls) == 2, f"WBL1-F7-KEY-{field}: {len(calls)} outbound calls"


def test_control_same_query_is_cached(cdx):
    answers, calls = cdx
    answers.append(_Resp(data=_rows("c.test", 2)))
    a = wayback_cdx.find_snapshots("http://c.test/", from_ts="2020", limit=50)
    b = wayback_cdx.find_snapshots("http://c.test/", from_ts="2020", limit=50)
    assert (len(calls), a, len(b)) == (1, b, 2), f"WBL1-F7-CONTROL-HIT: {len(calls)}"


@pytest.mark.parametrize(
    "case,empty",
    [("empty-list", []), ("header-only", [_HDR]), ("not-a-list", {"x": 1})],
)
def test_empty_answer_is_not_pinned(cdx, case, empty):
    answers, calls = cdx
    answers.extend([_Resp(data=empty), _Resp(data=_rows("e.test", 1))])
    a = wayback_cdx.find_snapshots("http://e.test/")
    b = wayback_cdx.find_snapshots("http://e.test/")
    assert (a, len(b), len(calls)) == ([], 1, 2), (
        f"WBL1-F7-EMPTY-PINNED-{case}: second={b} calls={len(calls)}"
    )


def test_control_error_not_cached(cdx):
    answers, calls = cdx
    answers.extend([_Resp(ok=False), _Resp(data=_rows("e.test", 1))])
    assert wayback_cdx.find_snapshots("http://e.test/") == []
    assert len(wayback_cdx.find_snapshots("http://e.test/")) == 1 and len(calls) == 2


# ---- F8 -----------------------------------------------------------------


def test_limit_applied_to_answer(cdx):
    answers, calls = cdx
    answers.append(_Resp(data=_rows("big.test", 200)))
    got = wayback_cdx.find_snapshots("http://big.test/", limit=50)
    assert len(got) == 50 and got[-1]["original"] == "http://big.test/49", (
        f"WBL1-F8-LIMIT-NOT-APPLIED: {len(got)} snapshots for limit=50"
    )
    assert _sent(calls[0], "limit") == ["50"]


def test_answer_capped_at_500(cdx):
    answers, calls = cdx
    answers.append(_Resp(data=_rows("big.test", 800)))
    got = wayback_cdx.find_snapshots("http://big.test/", limit=10_000)
    assert (len(got), _sent(calls[0], "limit")) == (500, ["500"]), (
        f"WBL1-F8-ROWS-UNCAPPED: {len(got)} snapshots"
    )


@pytest.mark.parametrize("limit", [-5, 0])
def test_limit_floor_is_one(cdx, limit):
    answers, calls = cdx
    answers.append(_Resp(data=_rows("neg.test", 3)))
    got = wayback_cdx.find_snapshots("http://neg.test/", limit=limit)
    assert (_sent(calls[0], "limit"), len(got)) == (["1"], 1), (
        f"WBL1-F8-LIMIT-FLOOR {limit}: sent {_sent(calls[0], 'limit')}, got {len(got)}"
    )


def test_route_answer_holds_fifty(cdx):
    from flask import Flask

    from bulk_downloader import app_wayback

    answers, calls = cdx
    answers.append(_Resp(data=_rows("r.test", 300)))
    app = Flask(__name__)
    app_wayback.register_routes(app)
    r = app.test_client().get("/api/wayback/snapshots?url=http://r.test/")
    assert (r.status_code, len(r.get_json()["snapshots"])) == (200, 50), (
        f"WBL1-F8-ROUTE: {r.status_code} {len(r.get_json()['snapshots'])}"
    )


# ---- F9 -----------------------------------------------------------------


def _avail(cdx, url):
    answers, _ = cdx
    answers.append(
        _Resp(
            data={
                "archived_snapshots": {
                    "closest": {"available": True, "url": url, "timestamp": "1"}
                }
            }
        )
    )
    return wayback_cdx.availability("http://a.test/")


@pytest.mark.parametrize(
    "url",
    [
        "javascript:alert(1)",
        "data:text/html,<b>x</b>",
        "file:///etc/passwd",
        "//evil.test/x",
        "",
    ],
)
def test_availability_rejects_non_http_closest(cdx, url):
    got = _avail(cdx, url)
    assert got == {"available": False, "timestamp": "", "archive_url": ""}, (
        f"WBL1-F9-CLOSEST-SCHEME {url!r}: {got}"
    )


@pytest.mark.parametrize(
    "url",
    ["http://archive.org/wayback/x", "https://web.archive.org/web/1/http://a.test/"],
)
def test_control_availability_http_closest(cdx, url):
    got = _avail(cdx, url)
    assert got == {
        "available": True,
        "timestamp": "1",
        "archive_url": url,
        "status": "",
    }, f"WBL1-F9-CONTROL {url}: {got}"


@pytest.mark.parametrize(
    "case,ts,orig",
    [
        ("ts-traversal", "../../x", "https://example.com/p"),
        ("ts-slash", "2020/../..", "https://example.com/p"),
        ("ts-arabic-digits", "٢٠٢٠", "https://example.com/p"),
        ("js-scheme", "20200101000000", "javascript:alert(1)"),
        ("no-host", "20200101000000", "http:///p"),
        ("dotdot-segment", "20200101000000", "http://a.test/../../../../x"),
        ("dot-segment", "20200101000000", "http://a.test/./x"),
    ],
)
def test_build_archive_url_rejects(case, ts, orig):
    got = wayback_cdx.build_archive_url(ts, orig)
    assert got == "", f"WBL1-F9-BUILD-{case}: {got}"


@pytest.mark.parametrize(
    "ts,orig,identity,want",
    [
        (
            "20200101000000",
            "https://example.com/path",
            True,
            "https://web.archive.org/web/20200101000000id_/https://example.com/path",
        ),
        (
            20200101,
            "http://example.com/a..b/c.d?q=../x",
            False,
            "https://web.archive.org/web/20200101/http://example.com/a..b/c.d?q=../x",
        ),
        ("20200101000000", None, True, ""),
        ("", "https://example.com/", True, ""),
    ],
)
def test_control_build_archive_url(ts, orig, identity, want):
    got = wayback_cdx.build_archive_url(ts, orig, identity=identity)
    assert got == want, f"WBL1-F9-BUILD-CONTROL: {got}"

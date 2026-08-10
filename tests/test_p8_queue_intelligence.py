"""Tests for queue_intelligence (#22 / P8).

Seeds a temp BD_HOME queue DB via the real db helpers and asserts the read-only
diagnostics. Runs under run_tests.py: zero-arg functions, repo root from __file__.
"""
import sys
from datetime import datetime, timedelta
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO / "tools"))
sys.path.insert(0, str(_REPO))

import queue_intelligence as QI  # noqa: E402
import bulk_downloader.db as DB  # noqa: E402
import pytest


@pytest.fixture(autouse=True)
def _isolate_db(clean_workdir):
    """Give every test in this module its OWN database.

    v3.66.992. These tests read the AMBIENT database and assert on global
    counts, so they passed serially only because nothing had seeded it yet in
    that process. Measured across five xdist widths on the same tree: green at
    -n 3 and -n 4, red at -n 2, -n 6 and -n 8 with `assert 6 == 0` -- another
    file landed on the same worker first and its rows were still there. xdist
    assigns files to workers by count, so the width decides who shares a worker
    and the failure looked like flakiness.

    `clean_workdir` chdirs to a tmpdir AND sets BD_INSTALL_DIR, which is what
    `db._resolve_db_path()` actually consults -- chdir alone is not enough,
    because subsequent code may chdir away.
    """
    return clean_workdir

# ── pure helpers ───────────────────────────────────────────────────

def test_categorize_buckets():
    cases = {
        "Request timed out after 30s": "timeout",
        "HTTP 404 Not Found": "not_found",
        "401 Unauthorized: login required": "auth_or_forbidden",
        "Cloudflare challenge detected": "challenge",
        "HTTP 429 Too Many Requests": "rate_limit",
        "Connection refused": "network",
        "selector did not match any element": "parse_or_extract",
        "No space left on device": "disk_io",
        "job cancelled by operator": "cancelled",
        "weird gremlins": "unknown",
        "": "none",
    }
    for msg, cat in cases.items():
        assert QI.categorize(msg) == cat, (msg, QI.categorize(msg))


def test_age_hours():
    now = datetime(2026, 6, 5, 12, 0, 0)
    ts = (now - timedelta(hours=30)).strftime("%Y-%m-%dT%H:%M:%S")
    assert QI._age_hours(ts, now=now) == 30.0
    assert QI._age_hours("garbage") is None
    assert QI._age_hours(None) is None


# ── empty DB ───────────────────────────────────────────────────────

def test_analyze_empty_db_no_crash():
    a = QI.analyze()
    assert a["totals"]["total"] == 0
    assert a["failure_rate"] is None
    assert a["stuck"]["count"] == 0


# ── seeded DB ──────────────────────────────────────────────────────

def _seed():
    DB.db_init()
    DB.queue_upsert("siteA", "http://a/1", status="done")
    DB.queue_upsert("siteA", "http://a/2", status="error",
                    message="HTTP 404 Not Found", retries=1)
    DB.queue_upsert("siteA", "http://a/3", status="failed",
                    message="Request timed out", retries=4)
    DB.queue_upsert("siteB", "http://b/1", status="error",
                    message="Cloudflare challenge", retries=2)
    DB.queue_upsert("siteB", "http://b/2", status="pending", retries=5)  # stuck by retries
    DB.queue_upsert("siteB", "http://b/old", status="running", retries=0)
    # backdate the running row so it is stuck by age (ts_updated is otherwise 'now')
    with DB.db_conn() as cx:
        cx.execute("UPDATE queue SET ts_updated=? WHERE url=?",
                   ("2000-01-01T00:00:00", "http://b/old"))


def test_analyze_seeded_counts_and_failures():
    _seed()
    a = QI.analyze(stuck_retries=3, stuck_age_hours=24)
    c = a["counts_by_status"]
    assert c.get("done") == 1
    assert c.get("error") == 2 and c.get("failed") == 1
    assert a["totals"]["failures"] == 3
    assert a["failure_rate"] is not None
    cats = a["failure_categories"]
    assert cats.get("not_found") == 1
    assert cats.get("timeout") == 1
    assert cats.get("challenge") == 1


def test_analyze_seeded_retry_and_stuck():
    _seed()
    a = QI.analyze(stuck_retries=3, stuck_age_hours=24)
    # error/failed row with retries=4 is over threshold 3
    assert a["retry"]["max"] >= 4
    assert a["retry"]["over_threshold"] >= 1
    # stuck: pending retries=5 (by retries) + running backdated (by age)
    stuck_urls = {s["url"] for s in a["stuck"]["items"]}
    assert "http://b/2" in stuck_urls   # by retries
    assert "http://b/old" in stuck_urls  # by age
    assert a["stuck"]["count"] >= 2


def test_per_site_breakdown_present():
    _seed()
    a = QI.analyze()
    assert "siteA" in a["per_site"] and "siteB" in a["per_site"]

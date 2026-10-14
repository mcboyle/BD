"""O1789 bug-hunt ssqh-f3: History search "N matches" capped at the fetch limit.

Finding F3 (harness-work/O1789/bughunt/spa-sites-queue-history/FINDINGS.md, finder bd-worker-S21-C): /api/search
returned count=len(results), capped by ``limit`` (default 100), while /api/search/facets counts every match, so
q=sweep over 150 rows read "100 matches" beside a facets total of 150. Ruling (RULING-pm-A3-A-dispatch-decisions-1540Z
item 6): count and facets count the same set; ``limit`` caps only the rows returned.
Seed mirrors the finder's rig (evidence/p6.sh): 150 rows zebra{i}.mp4, message "sweep".
"""
from __future__ import annotations

BD_GATE_SCOPE = "module"

import pytest

pytestmark = pytest.mark.bd_module_wipe


@pytest.fixture
def client(clean_workdir):
    from bulk_downloader import app as app_mod
    from bulk_downloader.db import db_init, db_log
    from bulk_downloader.migrations import apply_pending

    db_init()
    assert apply_pending(backup_first=False)["errors"] == 0
    for i in range(150):
        db_log("ba24ef11", "SweepT1", f"http://127.0.0.1:9/zebra{i}.mp4", "done",
               filename=f"zebra{i}.mp4", file_size=10, message="sweep")
    return app_mod.app.test_client()


def _search(client, q, limit=100):
    body = client.get(f"/api/search?q={q}&limit={limit}").get_json()
    facets = client.get(f"/api/search/facets?query={q}").get_json()["facets"]
    return body["count"], len(body["results"]), facets["total"]


def test_f3_count_is_the_match_set_not_the_page(client):
    count, rows, total = _search(client, "sweep")
    assert (count, rows, total) == (150, 100, 150), (
        f"F3: /api/search?q=sweep&limit=100 count={count}, results={rows}, facets.total={total}"
        " (want count=150 = facets.total over 150 rows, results capped at 100)")


@pytest.mark.parametrize("q,expected", [("zebra7", (1, 1, 1)), ("zebra", (0, 0, 0))])
def test_f3_controls_unchanged(client, q, expected):
    got = _search(client, q)
    assert got == expected, f"F3 control: /api/search?q={q!r} (count, results, facets.total) = {got}"


def test_f3_empty_query_returns_nothing(client):
    body = client.get("/api/search?q=&limit=100").get_json()
    assert (body["count"], body["results"]) == (0, []), f"F3 control: empty q returned {body['count']} matches"

"""F1.6 (v3.66.226): per-site queue-drain ETA on /api/queue/v2.

_m2_site_drain_eta is a pure function (pending_total, per_min) -> Optional[int]
seconds, fail-soft to None. These pin the math + the fail-soft contract
directly (no app boot), plus one route-shape check that /api/queue/v2 now
carries the additive `per_site` list.

run_tests.py conventions: zero-arg functions, no pytest builtins, repo root
via Path(__file__).resolve().parent.parent.
"""
from pathlib import Path
import sys

_REPO = Path(__file__).resolve().parent.parent
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))


def test_drain_eta_computes_from_rate():
    from bulk_downloader.app import _m2_site_drain_eta
    # 10 pending at 5 jobs/min -> 2 min -> 120s
    assert _m2_site_drain_eta(10, 5.0) == 120
    # 3 pending at 1/min -> 180s
    assert _m2_site_drain_eta(3, 1.0) == 180


def test_drain_eta_none_when_nothing_pending():
    from bulk_downloader.app import _m2_site_drain_eta
    assert _m2_site_drain_eta(0, 5.0) is None


def test_drain_eta_none_when_no_rate():
    """No completion rate yet -> None, never a divide-by-zero or bogus eta."""
    from bulk_downloader.app import _m2_site_drain_eta
    assert _m2_site_drain_eta(10, 0.0) is None
    assert _m2_site_drain_eta(10, -1.0) is None


def test_drain_eta_fail_soft_on_garbage():
    from bulk_downloader.app import _m2_site_drain_eta
    assert _m2_site_drain_eta(None, 5.0) is None
    assert _m2_site_drain_eta(10, "fast") is None


def test_queue_v2_payload_carries_per_site():
    """The additive key is present and a list (empty with no runners)."""
    from bulk_downloader.db import db_init
    from bulk_downloader import app as A
    db_init()
    c = A.app.test_client()
    r = c.get("/api/queue/v2")
    assert r.status_code == 200, r.get_json()
    body = r.get_json()
    assert "per_site" in body, f"per_site missing from queue/v2: {list(body)}"
    assert isinstance(body["per_site"], list)

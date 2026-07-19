"""BUG-3/7 follow-up -- the picker endpoint's scan_captures must be BOUNDED.

594 changed /api/analyzer/captures from a shallow list_captures glob to an
UNBOUNDED recursive scan_captures walk. On a large capture store (operator moved
a lot of captures in) that turned the endpoint into a multi-minute single-core
walk -- and any route-scanning test (test_secret_display_never) hung on it.

Fix: scan_captures(limit=N) descends newest-first and stops after N captures, so
the picker is O(N) not O(all captures). Unbounded (limit=None) is preserved for
token resolution / summaries.
"""
import os
import time
import tempfile
from pathlib import Path

os.environ.setdefault("BD_HOME", tempfile.mkdtemp())
os.environ.setdefault("BD_DISABLE_KEEPALIVE", "1")

from bulk_downloader import dom_analyzer as da


def _big_store(n_sub=3000):
    root = Path(tempfile.mkdtemp(prefix="bound_"))
    cap = root / "captures"
    cap.mkdir(parents=True, exist_ok=True)
    for i in range(3):
        (cap / f"top_{i}.wacz").write_bytes(b"PK")
    base = time.time() - n_sub
    for i in range(n_sub):
        d = cap / "template_onboarding" / f"h{i%40}_{i}_ts"
        d.mkdir(parents=True, exist_ok=True)
        f = d / f"cap_{i}.wacz"
        f.write_bytes(b"PK")
        os.utime(f, (base + i, base + i))   # capture i is newer than i-1
        os.utime(d, (base + i, base + i))
    newest = f"captures/template_onboarding/h{(n_sub-1)%40}_{n_sub-1}_ts/cap_{n_sub-1}.wacz"
    return root, n_sub, newest


def test_bounded_scan_is_capped():
    root, n, _ = _big_store()
    rows = da.scan_captures(root=root, limit=200)
    assert len(rows) == 200, f"limit=200 must cap; got {len(rows)}"


def test_bounded_scan_is_fast():
    root, n, _ = _big_store()
    # Best-of-3 to shrug off transient scheduler contention under --workers=N
    # (the old 0.5s absolute bound flaked on a loaded box). The bound makes this
    # O(limit) not O(store); a regressed (unbounded) scan would be
    # seconds-to-minutes at n on EVERY attempt, far above this 3s ceiling.
    best = None
    for _ in range(3):
        t = time.time()
        da.scan_captures(root=root, limit=200)
        dt = time.time() - t
        best = dt if best is None else min(best, dt)
    assert best < 3.0, f"bounded scan too slow: {best*1000:.0f}ms best-of-3 (n={n})"


def test_bounded_scan_surfaces_newest():
    # the just-made capture (newest mtime) must be in the bounded result -- this
    # is the BUG-3/7 case: an onboarding capture you just finished must show.
    root, n, newest = _big_store()
    rows = da.scan_captures(root=root, limit=200)
    rels = {r["rel_path"] for r in rows}
    assert newest in rels, "newest onboarding capture missing from bounded picker result"


def test_unbounded_scan_still_returns_all():
    # token resolution / summaries rely on the full enumeration
    root, n, _ = _big_store(n_sub=200)
    rows = da.scan_captures(root=root)      # no limit
    assert len(rows) == 200 + 3, f"unbounded must return all; got {len(rows)}"


if __name__ == "__main__":
    import traceback
    for k in [x for x in sorted(dict(globals())) if x.startswith("test_")]:
        try:
            globals()[k](); print(f"PASS  {k}")
        except AssertionError as e:
            print(f"FAIL  {k}: {e}")
        except Exception as e:
            print(f"ERROR {k}: {type(e).__name__}: {e}")

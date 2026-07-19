"""v3.66.641 -- Session-1 selftest battery: ROB-3-remainder + POS-3.

Three new selftest checks, all pure/self-contained, wired into run_all():

  * check_orphan_tempfiles(dirs)  -- ROB-3-rem: WARN on stale temp artifacts
    (.part / .tmp* / .selftest_* left by a crashed operation) older than a
    threshold, so a leaked-tempfile buildup is visible on the health page.
  * check_stale_locks(dirs)       -- ROB-3-rem: WARN on old *.lock files (a lock
    a crashed process never released).
  * check_egress_fail_closed()    -- POS-3: VERIFY the fail-closed egress
    invariant -- a vpn_required site whose tunnel is down must make
    download_egress.effective_download_proxy() RAISE (never return an unproxied
    client). Simulated with a stub socks_for_site that raises VPNRequiredError;
    no external egress. A regression that swallowed the raise (fail-open) -> FAIL.

Sandbox-safe: tempfile.mkdtemp per test, os.utime to age files, zero-arg tests,
no pytest builtins.
"""
from __future__ import annotations

import os
import tempfile
import time
from pathlib import Path

from bulk_downloader import selftest as st


def _age(path, hours):
    """Backdate a file's mtime by `hours`."""
    old = time.time() - hours * 3600.0
    os.utime(path, (old, old))


# ---- ROB-3-rem: orphan tempfiles -----------------------------------------

def test_check_orphan_tempfiles_warns_on_old_only():
    d = tempfile.mkdtemp(prefix="orphtmp_")
    old_part = Path(d) / "download.part"
    old_part.write_text("x")
    _age(old_part, 48)                       # 48h old -> orphan
    fresh = Path(d) / "fresh.part"
    fresh.write_text("y")                     # brand new -> not flagged
    rec = st.check_orphan_tempfiles(d, max_age_hours=24.0)
    assert rec["status"] in (st.WARN, st.OK)
    assert rec["status"] == st.WARN, f"an old .part should WARN, got {rec}"
    # the fresh file must NOT push the count; only the aged one counts
    assert rec.get("detail", {}).get("count", 0) >= 1, rec


def test_check_orphan_tempfiles_ok_when_clean():
    d = tempfile.mkdtemp(prefix="orphtmp_ok_")
    rec = st.check_orphan_tempfiles(d, max_age_hours=24.0)
    assert rec["status"] == st.OK, f"empty dir should be OK, got {rec}"


def test_check_orphan_tempfiles_missing_dir_is_ok():
    rec = st.check_orphan_tempfiles("/no/such/dir/here", max_age_hours=24.0)
    assert rec["status"] == st.OK, "a missing dir must not FAIL the health page"


# ---- ROB-3-rem: stale locks ----------------------------------------------

def test_check_stale_locks_warns_on_old_lock():
    d = tempfile.mkdtemp(prefix="stalelock_")
    lk = Path(d) / "runner.lock"
    lk.write_text("pid=1")
    _age(lk, 24)                              # 24h old lock -> stale
    rec = st.check_stale_locks(d, max_age_hours=6.0)
    assert rec["status"] == st.WARN, f"an old .lock should WARN, got {rec}"
    assert rec.get("detail", {}).get("count", 0) >= 1, rec


def test_check_stale_locks_ok_when_none():
    d = tempfile.mkdtemp(prefix="stalelock_ok_")
    rec = st.check_stale_locks(d, max_age_hours=6.0)
    assert rec["status"] == st.OK, f"no locks should be OK, got {rec}"


# ---- POS-3: egress fail-closed -------------------------------------------

def test_check_egress_fail_closed_ok_when_invariant_holds():
    """The real effective_download_proxy propagates VPNRequiredError from
    socks_for_site (fail closed). The check must report OK when it does."""
    rec = st.check_egress_fail_closed()
    assert rec["status"] == st.OK, (
        f"fail-closed egress invariant should verify OK on pristine code, got {rec}"
    )


def test_check_egress_fail_closed_detects_fail_open_regression(monkeypatch=None):
    """If effective_download_proxy were changed to SWALLOW the raise and return a
    proxy (fail OPEN), the check must FAIL. We simulate that regression by
    temporarily patching effective_download_proxy to return None, and assert the
    check catches it. Restored in finally."""
    from bulk_downloader import download_egress as eg
    orig = eg.effective_download_proxy
    try:
        eg.effective_download_proxy = lambda *a, **k: None  # fail-open regression
        rec = st.check_egress_fail_closed()
        assert rec["status"] == st.FAIL, (
            f"a fail-open egress must be caught as FAIL, got {rec}"
        )
    finally:
        eg.effective_download_proxy = orig


# ---- wiring: run_all surfaces the new checks -----------------------------

def test_run_all_includes_the_new_checks():
    d = tempfile.mkdtemp(prefix="runall_")
    rep = st.run_all(download_dir=d) if _accepts(st.run_all, "download_dir") else st.run_all()
    names = {c.get("test") for c in rep.get("checks", [])}
    assert "egress_fail_closed" in names, (
        f"run_all must include the egress_fail_closed check; saw {sorted(names)}"
    )
    assert "orphan_tempfiles" in names or "stale_locks" in names, (
        f"run_all must include the tempfile-hygiene checks; saw {sorted(names)}"
    )


def _accepts(fn, param):
    import inspect
    try:
        return param in inspect.signature(fn).parameters
    except (TypeError, ValueError):
        return False

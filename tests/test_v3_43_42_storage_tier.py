"""v3.43.42 regression tests — storage tier migration.

Coverage:
  - Module surface
  - is_eligible: file age + size threshold + defensive on bad inputs
  - plan_destination: relative-path mirror + safety against
    out-of-root sources
  - migrate_file: move / symlink_after_move / dry_run modes
  - find_candidates: SQL filter on done status + age + min size
  - run_site_migration: end-to-end with tmpdir
  - StorageTierScheduler: lifecycle, BD_DISABLE_KEEPALIVE gate
  - App.py wiring: scheduler started, CFG fields, defaults,
    endpoints registered
  - UI: load/save/clear for the 5 new fields, status badge
    function, run-now button
"""
from __future__ import annotations

import errno
import os
# [SAST 3:13pm 13 may] removed unused: import sqlite3
import time
from pathlib import Path
# [SAST 3:13pm 13 may] removed unused: from unittest import mock

import pytest


_REPO_ROOT = Path(__file__).resolve().parent.parent
_ST_PY = _REPO_ROOT / "bulk_downloader" / "storage_tier.py"
_APP_PY = _REPO_ROOT / "bulk_downloader" / "app.py"


# ── Module surface ────────────────────────────────────────────────────


def _APP_SRC():
    """app.py + extracted app_*.py blueprint modules (Phase 4 thin-core-shell)."""
    import bulk_downloader as _bd, pathlib as _pl
    _pkg = _pl.Path(_bd.__file__).parent
    _parts = [(_pkg / 'app.py').read_text(encoding='utf-8')]
    _parts += [p.read_text(encoding='utf-8') for p in sorted(_pkg.glob('app_*.py'))]
    return '\n'.join(_parts)


def test_module_importable():
    import bulk_downloader.storage_tier as st  # noqa: F401


def test_required_exports():
    from bulk_downloader import storage_tier as st
    for name in ("is_eligible", "plan_destination", "migrate_file",
                  "find_candidates", "run_site_migration",
                  "StorageTierScheduler", "get_scheduler",
                  "DEFAULT_INTERVAL_S", "MAX_FILES_PER_RUN"):
        assert hasattr(st, name), f"missing export: {name}"


def test_constants_sensible():
    from bulk_downloader import storage_tier as st
    # 1 hour cadence
    assert st.DEFAULT_INTERVAL_S == 3600
    # Bounded per-run to avoid I/O storms
    assert 50 <= st.MAX_FILES_PER_RUN <= 5000


# ── is_eligible ──────────────────────────────────────────────────────

def test_is_eligible_nonexistent_returns_false():
    from bulk_downloader.storage_tier import is_eligible
    assert is_eligible("/no/such/file.mp4", 30) is False


def test_is_eligible_young_file_returns_false(tmp_path=None):
    """A freshly-written file is NOT eligible for 30-day migration."""
    from bulk_downloader.storage_tier import is_eligible
    import tempfile
    with tempfile.NamedTemporaryFile(delete=False, suffix=".mp4") as f:
        f.write(b"x" * 1024)
        p = f.name
    try:
        assert is_eligible(p, age_days=30) is False
    finally:
        os.unlink(p)


def test_is_eligible_old_file_returns_true():
    """Mock a file's mtime to 60 days ago — should be eligible
    for the 30-day threshold."""
    from bulk_downloader.storage_tier import is_eligible
    import tempfile
    with tempfile.NamedTemporaryFile(delete=False, suffix=".mp4") as f:
        f.write(b"x" * 1024)
        p = f.name
    try:
        old = time.time() - (60 * 86400)
        os.utime(p, (old, old))
        assert is_eligible(p, age_days=30) is True
    finally:
        os.unlink(p)


def test_is_eligible_below_min_size_returns_false():
    """A 1KB file is too small if min_size_bytes = 10MB."""
    from bulk_downloader.storage_tier import is_eligible
    import tempfile
    with tempfile.NamedTemporaryFile(delete=False, suffix=".mp4") as f:
        f.write(b"x" * 1024)
        p = f.name
    try:
        old = time.time() - (60 * 86400)
        os.utime(p, (old, old))
        # Eligible without size filter
        assert is_eligible(p, age_days=30) is True
        # Not eligible with 10MB min
        assert is_eligible(p, age_days=30,
                            min_size_bytes=10 * 1024 * 1024) is False
    finally:
        os.unlink(p)


def test_is_eligible_defensive_inputs():
    """Bad path types must return False, not crash."""
    from bulk_downloader.storage_tier import is_eligible
    for bad in (None, 12345, [], {}, ""):
        assert is_eligible(bad, 30) is False


# ── plan_destination ─────────────────────────────────────────────────

def test_plan_destination_mirrors_subpath():
    from bulk_downloader.storage_tier import plan_destination
    import tempfile
    src_root = tempfile.mkdtemp()
    dst_root = tempfile.mkdtemp()
    try:
        # Create the source path layout
        os.makedirs(os.path.join(src_root, "Episodes"), exist_ok=True)
        src_file = os.path.join(src_root, "Episodes", "ep01.mp4")
        Path(src_file).touch()
        result = plan_destination(src_file, src_root, dst_root)
        # Expected: dst_root/Episodes/ep01.mp4
        expected_suffix = os.path.join("Episodes", "ep01.mp4")
        assert result.endswith(expected_suffix)
    finally:
        import shutil
        shutil.rmtree(src_root, ignore_errors=True)
        shutil.rmtree(dst_root, ignore_errors=True)


def test_plan_destination_out_of_root_falls_back_to_basename():
    """When the source isn't under source_root, the helper falls
    back to using just the basename — better than refusing entirely."""
    from bulk_downloader.storage_tier import plan_destination
    import tempfile
    dst_root = tempfile.mkdtemp()
    try:
        # /tmp/something.mp4 is NOT under /etc — should fall back
        with tempfile.NamedTemporaryFile(delete=False, suffix=".mp4") as f:
            src = f.name
        try:
            result = plan_destination(src, "/etc", dst_root)
            assert result is not None
            assert result.endswith(os.path.basename(src))
        finally:
            os.unlink(src)
    finally:
        import shutil
        shutil.rmtree(dst_root, ignore_errors=True)


def test_plan_destination_defensive_inputs():
    from bulk_downloader.storage_tier import plan_destination
    # Missing args
    assert plan_destination("", "", "") is None
    assert plan_destination(None, "/a", "/b") is None
    # Non-string types
    assert plan_destination(12345, "/a", "/b") is None
    assert plan_destination("/a", 12345, "/b") is None


# ── migrate_file ─────────────────────────────────────────────────────

def test_migrate_file_dry_run():
    """dry_run=True must NOT touch the filesystem."""
    from bulk_downloader.storage_tier import migrate_file
    import tempfile
    with tempfile.NamedTemporaryFile(delete=False, suffix=".mp4") as f:
        f.write(b"x" * 1024)
        src = f.name
    dst = os.path.join(tempfile.gettempdir(), "nonexistent_xyz.mp4")
    try:
        result = migrate_file(src, dst, dry_run=True)
        assert result["ok"] is True
        assert result["action"] == "would_move"
        # Source still exists, dest does not
        assert os.path.exists(src)
        assert not os.path.exists(dst)
    finally:
        os.unlink(src)


def test_migrate_file_real_move():
    """Standard move: source disappears, dest appears."""
    from bulk_downloader.storage_tier import migrate_file
    import tempfile
    with tempfile.NamedTemporaryFile(delete=False, suffix=".mp4") as f:
        f.write(b"hello")
        src = f.name
    dst_dir = tempfile.mkdtemp()
    dst = os.path.join(dst_dir, "moved.mp4")
    try:
        result = migrate_file(src, dst, mode="move")
        assert result["ok"] is True
        assert result["action"] == "moved"
        assert result["bytes_moved"] == 5
        assert not os.path.exists(src)
        assert os.path.exists(dst)
    finally:
        if os.path.exists(src):
            os.unlink(src)
        import shutil
        shutil.rmtree(dst_dir, ignore_errors=True)


def test_migrate_file_refuses_overwrite():
    """If the destination already exists, the migration aborts
    — don't silently clobber existing files."""
    from bulk_downloader.storage_tier import migrate_file
    import tempfile
    with tempfile.NamedTemporaryFile(delete=False, suffix=".mp4") as f:
        f.write(b"hello")
        src = f.name
    with tempfile.NamedTemporaryFile(delete=False, suffix=".mp4") as f:
        f.write(b"existing")
        dst = f.name
    try:
        result = migrate_file(src, dst, mode="move")
        assert result["ok"] is False
        assert "exists" in result["error"].lower()
        # Source unchanged
        assert os.path.exists(src)
    finally:
        if os.path.exists(src):
            os.unlink(src)
        if os.path.exists(dst):
            os.unlink(dst)


def _exercise_migrate_file_claim_handoff(tmp_path, monkeypatch,
                                          *, force_claim_drop=False,
                                          force_cross_device=False):
    """Publish competitor bytes iff the destination claim is absent at move."""
    from bulk_downloader import storage_tier

    source = tmp_path / "migrator-a.mp4"
    destination = tmp_path / "claimed.mp4"
    source.write_bytes(b"migrator-A")
    real_move = storage_tier.shutil.move
    real_rename = storage_tier.shutil.os.rename
    counts = {
        "move_entries": 0,
        "forced_claim_drops": 0,
        "claim_present_entries": 0,
        "competitor_publications": 0,
        "delegated_moves": 0,
        "rename_attempts": 0,
        "forced_cross_device_errors": 0,
    }

    def observed_rename(source_path, dest_path):
        counts["rename_attempts"] += 1
        if force_cross_device:
            counts["forced_cross_device_errors"] += 1
            raise OSError(errno.EXDEV, "forced cross-device move")
        return real_rename(source_path, dest_path)

    def observed_move(source_path, dest_path):
        counts["move_entries"] += 1
        if force_claim_drop:
            if os.path.exists(dest_path):
                os.remove(dest_path)
            counts["forced_claim_drops"] += 1
        if os.path.exists(dest_path):
            counts["claim_present_entries"] += 1
        else:
            Path(dest_path).write_bytes(b"writer-B")
            counts["competitor_publications"] += 1
        counts["delegated_moves"] += 1
        return real_move(source_path, dest_path)

    monkeypatch.setattr(storage_tier.shutil.os, "rename", observed_rename)
    monkeypatch.setattr(storage_tier.shutil, "move", observed_move)
    result = storage_tier.migrate_file(str(source), str(destination), mode="move")
    return result, destination, counts


def _assert_exclusive_claim_reaches_move(counts):
    assert counts["move_entries"] == 1
    assert counts["claim_present_entries"] == 1, (
        "exclusive destination claim absent at the single move entry"
    )
    assert counts["forced_claim_drops"] == 0
    assert counts["competitor_publications"] == 0
    assert counts["delegated_moves"] == 1
    assert counts["rename_attempts"] == 1
    assert counts["forced_cross_device_errors"] == 0


def test_migrate_file_holds_exclusive_destination_claim_through_move(
        tmp_path, monkeypatch):
    """No absent-path interval may let a competing publisher be overwritten."""
    result, destination, counts = _exercise_migrate_file_claim_handoff(
        tmp_path, monkeypatch,
    )

    assert result["ok"] is True
    assert result["action"] == "moved"
    _assert_exclusive_claim_reaches_move(counts)
    assert destination.read_bytes() == b"migrator-A"


def test_migrate_file_holds_claim_through_cross_device_copy(
        tmp_path, monkeypatch):
    """The EXDEV fallback writes through the claim instead of dropping it."""
    result, destination, counts = _exercise_migrate_file_claim_handoff(
        tmp_path, monkeypatch, force_cross_device=True,
    )

    assert result["ok"] is True
    assert result["action"] == "moved"
    assert counts == {
        "move_entries": 1,
        "forced_claim_drops": 0,
        "claim_present_entries": 1,
        "competitor_publications": 0,
        "delegated_moves": 1,
        "rename_attempts": 1,
        "forced_cross_device_errors": 1,
    }
    assert destination.read_bytes() == b"migrator-A"


def test_migrate_file_claim_probe_detects_a_dropped_claim(
        tmp_path, monkeypatch):
    """Negative control: one forced claim drop exposes exactly one lost writer."""
    result, destination, counts = _exercise_migrate_file_claim_handoff(
        tmp_path, monkeypatch, force_claim_drop=True,
    )

    assert result["ok"] is True
    assert result["action"] == "moved"
    assert counts == {
        "move_entries": 1,
        "forced_claim_drops": 1,
        "claim_present_entries": 0,
        "competitor_publications": 1,
        "delegated_moves": 1,
        "rename_attempts": 1,
        "forced_cross_device_errors": 0,
    }
    assert destination.read_bytes() == b"migrator-A"
    with pytest.raises(
            AssertionError,
            match="exclusive destination claim absent at the single move entry"):
        _assert_exclusive_claim_reaches_move(counts)


def test_migrate_file_claim_transform_control_imports_subject():
    """The reversion mutant imports without exercising destination ownership."""
    from bulk_downloader import storage_tier

    assert callable(storage_tier.migrate_file)


def test_migrate_file_symlink_mode():
    """symlink_after_move: file lives at dest, symlink remains at src."""
    from bulk_downloader.storage_tier import migrate_file
    import tempfile
    # Skip on Windows where symlinks need special privileges
    if os.name == "nt":
        import pytest
        try:
            pytest.skip("symlinks need admin on Windows")
        except Exception:
            return
    with tempfile.NamedTemporaryFile(delete=False, suffix=".mp4") as f:
        f.write(b"hello")
        src = f.name
    dst_dir = tempfile.mkdtemp()
    dst = os.path.join(dst_dir, "moved.mp4")
    try:
        result = migrate_file(src, dst, mode="symlink_after_move")
        assert result["ok"] is True
        # Either moved_with_symlink (best) or moved_no_symlink
        # (acceptable degradation when symlink fails)
        assert result["action"] in ("moved_with_symlink", "moved_no_symlink")
        assert os.path.exists(dst)
        if result["action"] == "moved_with_symlink":
            assert os.path.islink(src)
    finally:
        if os.path.exists(src) or os.path.islink(src):
            os.unlink(src)
        import shutil
        shutil.rmtree(dst_dir, ignore_errors=True)


def test_migrate_file_handles_vanished_source():
    """If the source disappeared between candidate-selection and
    move, return ok=False rather than crashing."""
    from bulk_downloader.storage_tier import migrate_file
    result = migrate_file("/no/such/file.mp4", "/tmp/elsewhere.mp4",
                            mode="move")
    assert result["ok"] is False
    assert "vanish" in result["error"].lower() or \
           "source" in result["error"].lower()


# ── find_candidates (DB query) ──────────────────────────────────────

def test_find_candidates_returns_list():
    """Basic smoke — empty DB returns empty list, not crash."""
    from bulk_downloader.storage_tier import find_candidates
    # Wraps in try so DB-not-initialized state returns []
    result = find_candidates("nonexistent_site", age_days=30)
    assert isinstance(result, list)


# ── run_site_migration ──────────────────────────────────────────────

def test_run_site_migration_disabled():
    """When storage_tier_enabled=False, return early."""
    from bulk_downloader.storage_tier import run_site_migration
    result = run_site_migration("sid", {}, "/tmp/foo")
    assert result["ok"] is False
    assert "disabled" in result["error"].lower()


def test_run_site_migration_no_dest():
    from bulk_downloader.storage_tier import run_site_migration
    result = run_site_migration("sid",
        {"storage_tier_enabled": True}, "/tmp/foo")
    assert result["ok"] is False
    assert "storage_tier_dir" in result["error"].lower()


def test_run_site_migration_dest_not_directory():
    """If storage_tier_dir doesn't exist, refuse."""
    from bulk_downloader.storage_tier import run_site_migration
    result = run_site_migration("sid",
        {"storage_tier_enabled": True,
         "storage_tier_dir": "/no/such/dir"},
        "/tmp/foo")
    assert result["ok"] is False
    assert "directory" in result["error"].lower() or \
           "dest" in result["error"].lower()


def test_run_site_migration_handles_bad_age_days():
    """Garbage in storage_tier_age_days must default to 30, not crash."""
    from bulk_downloader.storage_tier import run_site_migration
    import tempfile
    dst = tempfile.mkdtemp()
    try:
        # bad age value
        for bad in ("not a number", None, []):
            result = run_site_migration("sid",
                {"storage_tier_enabled": True,
                 "storage_tier_dir": dst,
                 "storage_tier_age_days": bad,
                 "download_dir": "/tmp"},
                "/tmp")
            # ok=True (no candidates → migrated_count=0) or
            # ok=False with a clear error — but never an unhandled
            # exception
            assert "migrated_count" in result
    finally:
        import shutil
        shutil.rmtree(dst, ignore_errors=True)


# ── Scheduler ────────────────────────────────────────────────────────

def test_scheduler_singleton():
    from bulk_downloader.storage_tier import get_scheduler
    a = get_scheduler()
    b = get_scheduler()
    assert a is b


def test_scheduler_respects_disable_env():
    """BD_DISABLE_KEEPALIVE must prevent thread creation."""
    from bulk_downloader.storage_tier import StorageTierScheduler
    old = os.environ.get("BD_DISABLE_KEEPALIVE")
    os.environ["BD_DISABLE_KEEPALIVE"] = "1"
    try:
        sched = StorageTierScheduler()
        sched.start()
        assert sched._thread is None
    finally:
        if old is None:
            os.environ.pop("BD_DISABLE_KEEPALIVE", None)
        else:
            os.environ["BD_DISABLE_KEEPALIVE"] = old


def test_scheduler_get_status_shape():
    """get_status returns a dict with the expected keys."""
    from bulk_downloader.storage_tier import StorageTierScheduler
    sched = StorageTierScheduler()
    status = sched.get_status()
    assert status["ok"] is True
    assert "running" in status
    assert "interval_seconds" in status
    assert "per_site" in status
    assert isinstance(status["per_site"], dict)


# ── App.py wiring ────────────────────────────────────────────────────

def test_cfg_fields_has_storage_tier_entries():
    src = _APP_SRC()
    for field in ("storage_tier_enabled", "storage_tier_dir",
                    "storage_tier_age_days",
                    "storage_tier_min_size_mb",
                    "storage_tier_mode"):
        assert f'"{field}"' in src, f"missing CFG field: {field}"


def test_defaults_has_storage_tier_entries():
    src = _APP_SRC()
    assert '"storage_tier_enabled": False' in src
    assert '"storage_tier_dir": ""' in src
    assert '"storage_tier_age_days": 30' in src
    assert '"storage_tier_mode": "move"' in src


def test_scheduler_init_at_startup():
    src = _APP_PY.read_text(encoding="utf-8")
    assert "_start_storage_tier_scheduler" in src


def test_storage_tier_status_endpoint_registered():
    src = _APP_SRC()
    assert "/api/sites/<sid>/storage_tier/status" in src
    assert "def api_storage_tier_status" in src


def test_storage_tier_run_now_endpoint_registered():
    src = _APP_SRC()
    assert "/api/sites/<sid>/storage_tier/run_now" in src
    assert "def api_storage_tier_run_now" in src


# ── UI ───────────────────────────────────────────────────────────────





# ── Adversarial regression locks ────────────────────────────────────

def test_find_candidates_handles_none_age():
    """Audit catch: age_days=None crashed with TypeError on
    None * int. Must default to 30 days rather than crash."""
    from bulk_downloader.storage_tier import find_candidates
    # Must not raise
    result = find_candidates("sid", age_days=None)
    assert isinstance(result, list)


def test_find_candidates_handles_string_age():
    """Audit catch: age_days='bogus' crashed with TypeError on
    float - str. Must default to 30 rather than crash."""
    from bulk_downloader.storage_tier import find_candidates
    result = find_candidates("sid", age_days="bogus")
    assert isinstance(result, list)


def test_find_candidates_handles_bad_min_size():
    """Same defense applies to min_size_mb."""
    from bulk_downloader.storage_tier import find_candidates
    for bad in (None, "big", []):
        result = find_candidates("sid", age_days=30, min_size_mb=bad)
        assert isinstance(result, list)


def test_find_candidates_handles_non_string_site_id():
    """Bad site_id types skip cleanly."""
    from bulk_downloader.storage_tier import find_candidates
    for bad in (None, 12345, [], {}):
        result = find_candidates(bad, age_days=30)
        assert result == []


def test_storage_tier_module_no_syntax_warnings():
    """The module must compile clean — no escape-sequence warnings
    from docstring contents. Caught at build because Python 3.12+
    promotes these from DeprecationWarning to SyntaxWarning."""
    import warnings
    import importlib
    import bulk_downloader.storage_tier as st
    # Force-reload to make sure the .pyc cache isn't masking
    # warnings
    with warnings.catch_warnings():
        warnings.simplefilter("error", SyntaxWarning)
        importlib.reload(st)

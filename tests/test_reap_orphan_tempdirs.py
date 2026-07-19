"""Cut 606 (0.4 defensive janitor): reap_orphan_tempdirs reaps ONLY stale
BD-prefixed temp dirs (bdback_/bdrestore_/bd_plugin_upload_/bd-diag-/...), which
BD can leak on error paths, and leaves fresh BD dirs and non-BD dirs untouched.
Dry-run by default. (build_template no longer leaks per 605; this is defense-in-depth
for the /tmp-flood class the operator hit.)"""
import os
import sys
import time
import tempfile
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
if str(_REPO / "tools") not in sys.path:
    sys.path.insert(0, str(_REPO / "tools"))
import reap_orphan_tempdirs as R  # noqa: E402


def _mk(root, name, age_h):
    p = os.path.join(root, name)
    os.makedirs(p, exist_ok=True)
    t = time.time() - age_h * 3600
    os.utime(p, (t, t))
    return p


def test_finds_only_stale_bd_prefixed():
    root = tempfile.mkdtemp()
    stale_bd = _mk(root, "bdrestore_abc", 48)      # stale + BD  -> target
    fresh_bd = _mk(root, "bdback_xyz", 1)          # fresh + BD  -> keep
    stale_other = _mk(root, "someoneelse_x", 48)   # stale, non-BD -> keep
    found = R.find_orphans(root=root, max_age_h=24)
    assert stale_bd in found
    assert fresh_bd not in found
    assert stale_other not in found


def test_apply_deletes_only_targets():
    root = tempfile.mkdtemp()
    stale_bd = _mk(root, "bd_plugin_upload_old", 48)
    keep = _mk(root, "keepme", 48)
    R.reap(R.find_orphans(root=root, max_age_h=24), apply=True)
    assert not os.path.exists(stale_bd)
    assert os.path.exists(keep)


def test_dry_run_deletes_nothing():
    root = tempfile.mkdtemp()
    stale_bd = _mk(root, "bd-diag-old", 48)
    R.reap(R.find_orphans(root=root, max_age_h=24), apply=False)
    assert os.path.exists(stale_bd)

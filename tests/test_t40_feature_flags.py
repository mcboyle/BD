"""T40 (Tier 4) — D-121 feature-flag console.

Mutating side: bulk_downloader.feature_flags (set_flag, delete_flag,
is_on, list_flags). Read-side: dev_suite.feature_flags_status().

Per-test isolation: bulk_downloader.constants.INSTALL_DIR is captured
at module import time, so chdir alone doesn't give us a fresh state
file. Each test monkeypatches feature_flags.state_path() to point at
its own tmp_path. This is the same pattern test_i18n_phase191.py uses
for locale isolation.
"""
import json
from pathlib import Path

from bulk_downloader import dev_suite as ds
from bulk_downloader import feature_flags as ff


# ── Isolation helper ────────────────────────────────────────────────

class _Isolated:
    """Context manager that patches feature_flags.state_path to a fresh
    file under the given tmp_path, plus resets the in-process cache."""
    def __init__(self, tmp_path):
        self.tmp_path = tmp_path
        self._orig_state_path = None
        self._orig_cache = None
        self._orig_mtime = None

    def __enter__(self):
        target = Path(self.tmp_path) / "feature_flags.json"
        self._orig_state_path = ff.state_path
        ff.state_path = lambda: target
        self._orig_cache = ff._CACHE
        self._orig_mtime = ff._CACHE_MTIME
        ff._CACHE = {}
        ff._CACHE_MTIME = 0.0
        return target

    def __exit__(self, *exc):
        ff.state_path = self._orig_state_path
        ff._CACHE = self._orig_cache
        ff._CACHE_MTIME = self._orig_mtime


# ── Basic state ─────────────────────────────────────────────────────

def test_list_flags_empty(clean_workdir, tmp_path):
    with _Isolated(tmp_path):
        assert ff.list_flags() == {}


def test_is_on_unknown_flag_returns_false(clean_workdir, tmp_path):
    with _Isolated(tmp_path):
        assert ff.is_on("nothing") is False


def test_set_flag_then_is_on(clean_workdir, tmp_path):
    with _Isolated(tmp_path):
        r = ff.set_flag("alpha_feature", True)
        assert r["ok"] is True
        assert ff.is_on("alpha_feature") is True


def test_set_flag_false_then_is_on(clean_workdir, tmp_path):
    with _Isolated(tmp_path):
        ff.set_flag("beta", True)
        ff.set_flag("beta", False)
        assert ff.is_on("beta") is False


def test_set_flag_rejects_non_bool_value(clean_workdir, tmp_path):
    with _Isolated(tmp_path):
        r = ff.set_flag("c_flag", "true")  # string, not bool
        assert r["ok"] is False
        assert "bool" in r["error"]


def test_set_flag_rejects_bad_name(clean_workdir, tmp_path):
    with _Isolated(tmp_path):
        # Caps not allowed
        r = ff.set_flag("HasUppercase", True)
        assert r["ok"] is False
        # Empty
        r = ff.set_flag("", True)
        assert r["ok"] is False
        # Single char (need at least 2)
        r = ff.set_flag("a", True)
        assert r["ok"] is False
        # Starts with digit
        r = ff.set_flag("1starts_with_digit", True)
        assert r["ok"] is False
        # Hyphens
        r = ff.set_flag("has-hyphen", True)
        assert r["ok"] is False


def test_set_flag_accepts_valid_names(clean_workdir, tmp_path):
    with _Isolated(tmp_path):
        for name in ("ab", "aa_bb", "feature_123", "x" * 60):
            r = ff.set_flag(name, True)
            assert r["ok"] is True, \
                f"rejected: {name}: {r.get('error')}"


def test_delete_flag(clean_workdir, tmp_path):
    with _Isolated(tmp_path):
        ff.set_flag("kill_me", True)
        assert ff.is_on("kill_me") is True
        r = ff.delete_flag("kill_me")
        assert r["ok"] is True
        assert r["removed"] is True
        assert ff.is_on("kill_me") is False


def test_delete_flag_idempotent_when_absent(clean_workdir, tmp_path):
    with _Isolated(tmp_path):
        r = ff.delete_flag("never_existed")
        assert r["ok"] is True
        assert r["removed"] is False


def test_list_flags_returns_all(clean_workdir, tmp_path):
    with _Isolated(tmp_path):
        ff.set_flag("aa", True)
        ff.set_flag("bb", False)
        ff.set_flag("cc", True)
        flags = ff.list_flags()
        assert flags == {"aa": True, "bb": False, "cc": True}


def test_state_file_atomic_no_tmp_left(clean_workdir, tmp_path):
    with _Isolated(tmp_path) as p:
        ff.set_flag("aa", True)
        assert p.exists()
        tmp = p.with_suffix(p.suffix + ".tmp")
        # .tmp must not linger after a successful write
        assert not tmp.exists()


# ── dev_suite read-side wrapper ─────────────────────────────────────

def test_feature_flags_status_when_empty(clean_workdir, tmp_path):
    with _Isolated(tmp_path):
        r = ds.feature_flags_status()
        assert r["ok"] is True
        assert r["tool"] == "feature_flags_status"
        assert r["flags"] == {}


def test_feature_flags_status_after_set(clean_workdir, tmp_path):
    with _Isolated(tmp_path):
        ff.set_flag("a_thing", True)
        ff.set_flag("b_thing", False)
        r = ds.feature_flags_status()
        assert r["flags"] == {"a_thing": True, "b_thing": False}
        assert "1 enabled" in r["verdict"]


def test_feature_flags_status_is_json_serializable(clean_workdir,
                                                     tmp_path):
    with _Isolated(tmp_path):
        ff.set_flag("a_one", True)
        r = ds.feature_flags_status()
        json.dumps(r, default=str)


def test_cache_invalidates_on_mtime_change(clean_workdir, tmp_path):
    """If the state file's mtime changes (e.g. operator edited it
    directly), the cache must reload on the next read."""
    with _Isolated(tmp_path) as p:
        ff.set_flag("xx", True)
        assert ff.is_on("xx") is True
        # Hand-edit the file
        p.write_text(json.dumps({"xx": False, "yy": True}),
                      encoding="utf-8")
        # Bump mtime explicitly — write_text may not update it enough
        # to differ from the cached value within a single second on
        # some filesystems
        import os as _os, time as _time
        _time.sleep(0.05)
        _os.utime(p, None)
        assert ff.is_on("xx") is False
        assert ff.is_on("yy") is True


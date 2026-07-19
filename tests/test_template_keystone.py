"""Tests for template_keystone (A5 sub-wave 2): backup-before-overwrite.

Synthetic fixtures ONLY — every test builds throwaway template JSON in a tmp
reviewed dir; no real template is touched. The load-bearing safety assertions:

  * a swap NEVER happens without a gold snapshot taken first;
  * the live file only changes via the atomic swap, and a gate-reject leaves
    LIVE BYTE-IDENTICAL (the safety property that lets auto-* gate without risk);
  * rollback restores the gold exactly;
  * path traversal in `host` is rejected.
"""
import json
import os
from pathlib import Path

from bulk_downloader import template_keystone as tk


def _tpl(version, **extra):
    t = {"host": "example.com", "status": "enabled", "version": version,
         "selectors": {"player": {"play_button": f".p{version}"}},
         "api": {}, "network_patterns": []}
    t.update(extra)
    return t


def _setup(tmp_path, live=None):
    rd = tmp_path / "templates" / "reviewed"
    rd.mkdir(parents=True)
    if live is not None:
        (rd / "example.com.template.json").write_text(json.dumps(live), "utf-8")
    return rd


def _read(rd, suffix=".template.json"):
    p = rd / f"example.com{suffix}"
    return json.loads(p.read_text("utf-8")) if p.is_file() else None


class TestSnapshotFirst:
    def test_snapshot_creates_gold_from_live(self, tmp_path):
        rd = _setup(tmp_path, live=_tpl(1))
        r = tk.snapshot_gold("example.com", reviewed_dir=rd)
        assert r["ok"] and r["snapshotted"] is True
        assert _read(rd, ".template.json.bak") == _tpl(1)

    def test_snapshot_noop_when_no_live(self, tmp_path):
        rd = _setup(tmp_path, live=None)
        r = tk.snapshot_gold("example.com", reviewed_dir=rd)
        assert r["ok"] and r["snapshotted"] is False

    def test_snapshot_does_not_clobber_existing_gold(self, tmp_path):
        rd = _setup(tmp_path, live=_tpl(2))
        (rd / "example.com.template.json.bak").write_text(json.dumps(_tpl(1)), "utf-8")
        tk.snapshot_gold("example.com", reviewed_dir=rd)
        # gold stays the original last-known-good, not the newer live
        assert _read(rd, ".template.json.bak") == _tpl(1)


class TestSafeOverwrite:
    def test_overwrite_snapshots_then_swaps(self, tmp_path):
        rd = _setup(tmp_path, live=_tpl(1))
        r = tk.safe_overwrite("example.com", _tpl(2), reviewed_dir=rd)
        assert r["ok"] and r["swapped"] is True
        assert _read(rd) == _tpl(2)                       # live updated
        assert _read(rd, ".template.json.bak") == _tpl(1)  # gold = old live (rollback point)

    def test_gate_reject_leaves_live_untouched(self, tmp_path):
        rd = _setup(tmp_path, live=_tpl(1))
        before = (rd / "example.com.template.json").read_bytes()
        # gate rejects everything → no swap
        r = tk.safe_overwrite("example.com", _tpl(2), reviewed_dir=rd,
                              gate=lambda drift: False)
        assert r["ok"] and r["swapped"] is False
        after = (rd / "example.com.template.json").read_bytes()
        assert before == after, "LIVE must be byte-identical when the gate rejects"
        # but the gold snapshot still happened (safety) and stage retained
        assert _read(rd, ".template.json.bak") == _tpl(1)
        assert (rd / "example.com.template.json.stage").is_file()

    def test_gate_pass_swaps(self, tmp_path):
        rd = _setup(tmp_path, live=_tpl(1))
        r = tk.safe_overwrite("example.com", _tpl(2), reviewed_dir=rd,
                              gate=lambda drift: drift <= 999)
        assert r["swapped"] is True
        assert _read(rd) == _tpl(2)

    def test_first_version_no_baseline(self, tmp_path):
        rd = _setup(tmp_path, live=None)
        r = tk.safe_overwrite("example.com", _tpl(1), reviewed_dir=rd)
        assert r["ok"] and r["swapped"] is True
        assert _read(rd) == _tpl(1)


class TestRollback:
    def test_rollback_restores_gold(self, tmp_path):
        rd = _setup(tmp_path, live=_tpl(1))
        tk.safe_overwrite("example.com", _tpl(2), reviewed_dir=rd)  # gold=v1, live=v2
        assert _read(rd) == _tpl(2)
        r = tk.rollback_to_gold("example.com", reviewed_dir=rd)
        assert r["ok"]
        assert _read(rd) == _tpl(1), "rollback must restore the gold exactly"

    def test_rollback_without_gold_fails_safe(self, tmp_path):
        rd = _setup(tmp_path, live=_tpl(1))
        r = tk.rollback_to_gold("example.com", reviewed_dir=rd)
        assert r["ok"] is False and "no gold" in r["error"]


class TestDrift:
    def test_drift_detects_selector_change(self, tmp_path):
        rd = _setup(tmp_path, live=_tpl(1))
        tk.snapshot_gold("example.com", reviewed_dir=rd)        # gold = v1
        d = tk.drift_against_gold("example.com", _tpl(2), reviewed_dir=rd)
        assert d["ok"] and d["drift"] > 0                       # .p1 vs .p2 differs

    def test_no_drift_identical(self, tmp_path):
        rd = _setup(tmp_path, live=_tpl(1))
        tk.snapshot_gold("example.com", reviewed_dir=rd)
        d = tk.drift_against_gold("example.com", _tpl(1), reviewed_dir=rd)
        assert d["ok"] and d["drift"] == 0


class TestPathSafety:
    def test_traversal_host_rejected(self, tmp_path):
        rd = _setup(tmp_path, live=_tpl(1))
        for bad in ("../etc/passwd", "a/b", "..", ".hidden", "x\\y"):
            r = tk.safe_overwrite(bad, _tpl(2), reviewed_dir=rd)
            assert r["ok"] is False and "invalid host" in r["error"], bad


class TestKeystonePresent:
    def test_present_probe(self):
        # The capability probe lifecycle_automation gates the mutators on.
        assert tk.keystone_present() is True

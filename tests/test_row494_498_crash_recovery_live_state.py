"""Rows 494 and 498: crash recovery consumes the staging claim it mutates.

The sequential transport writes resume validators to ``<part>.meta`` and the
page-URL identity to ``<part>.owner``.  Crash recovery must therefore decide
whether an old partial is live from the owner record, not from a ``url`` key
that no production metadata writer emits.  Its operator delete path is also a
writer against that ownership contract and must remove the claim it invalidates.
"""
from __future__ import annotations

import json
import os
import time
from types import SimpleNamespace

import pytest

from bulk_downloader import crash_recovery as cr
from bulk_downloader import staging_claim as sc


BD_GATE_SCOPE = "module"

_ACTIVE_URL = "https://example.test/scene/active"
_AGE_THRESHOLD_S = 60
_OLD_AGE_S = 3600


def _site_config(tmp_path):
    return {"site": {"name": "Test Site", "download_dir": str(tmp_path)}}


def _write_production_meta(part_path):
    meta_path = part_path.with_suffix(part_path.suffix + ".meta")
    meta_path.write_text(json.dumps({"etag": '"row494-etag"'}),
                         encoding="utf-8")
    return meta_path


def _age_part(part_path):
    old = time.time() - _OLD_AGE_S
    os.utime(part_path, (old, old))


def _isolate_ignored_paths(monkeypatch):
    fired = {"count": 0}

    def no_ignored_paths():
        fired["count"] += 1
        return set()

    monkeypatch.setattr(cr, "_ignored_paths", no_ignored_paths)
    return fired


def test_scan_withholds_a_claim_owned_by_exactly_one_active_job(
        tmp_path, monkeypatch):
    """RED on the parent: the live partial is returned exactly once."""
    identity = sc.job_identity(_ACTIVE_URL)
    final = tmp_path / "Active.mp4"
    part = sc.claim(final, identity)
    part.write_bytes(b"active-transfer-bytes")
    meta_path = _write_production_meta(part)
    _age_part(part)
    owner = sc.owner_path_for(part)
    runners = {
        "site": SimpleNamespace(jobs={_ACTIVE_URL: {"status": "running"}}),
    }
    ignored_fired = _isolate_ignored_paths(monkeypatch)

    # Preconditions precede the scanner's verdict.  This is the exact durable
    # shape the product writes, including zero occurrences of the invented URL
    # metadata key and one claim joined to one active page URL.
    assert part.is_file(), "precondition: the fixture did not create the .part"
    assert time.time() - part.stat().st_mtime > _AGE_THRESHOLD_S, (
        "precondition: the .part is not old enough to enter the orphan branch")
    stored_meta = json.loads(meta_path.read_text(encoding="utf-8"))
    assert stored_meta == {"etag": '"row494-etag"'}
    assert sum(key == "url" for key in stored_meta) == 0, (
        "precondition: production metadata unexpectedly contains a url key")
    assert owner.is_file(), "precondition: staging_claim did not publish a claim"
    assert list(tmp_path.glob("*.owner")) == [owner], (
        "precondition: the fixture did not create exactly one claim")
    assert sc._read_owner_identity(owner) == identity
    active_urls = cr._active_urls(runners)
    assert active_urls == {_ACTIVE_URL}
    assert len(active_urls) == 1, (
        "precondition: the fixture did not build exactly one active job")

    orphans = cr.scan_for_orphans(
        s_cfg=_site_config(tmp_path), runners=runners,
        age_threshold_s=_AGE_THRESHOLD_S)

    assert ignored_fired["count"] == 1, (
        "precondition: scan did not consult the ignored-path population once")
    hits = [row for row in orphans if row["path"] == str(part)]
    assert len(hits) == 0, (
        "a claimed .part whose owner matches exactly 1 active job was returned "
        f"exactly {len(hits)} time(s) as operator-deletable: {orphans}")


def test_scan_withholds_a_part_whose_claim_cannot_be_measured(
        tmp_path, monkeypatch):
    """UNKNOWN is withheld; a malformed claim is not measured absence."""
    part = sc.staging_path_for(tmp_path / "Unknown.mp4")
    part.write_bytes(b"unmeasurable-claim-bytes")
    meta_path = _write_production_meta(part)
    _age_part(part)
    owner = sc.owner_path_for(part)
    owner.write_text("{not-a-claim", encoding="utf-8")
    runners = {"site": SimpleNamespace(jobs={})}
    ignored_fired = _isolate_ignored_paths(monkeypatch)

    assert part.is_file(), "precondition: the fixture did not create the .part"
    assert time.time() - part.stat().st_mtime > _AGE_THRESHOLD_S
    stored_meta = json.loads(meta_path.read_text(encoding="utf-8"))
    assert sum(key == "url" for key in stored_meta) == 0
    assert owner.is_file(), "precondition: the malformed claim is absent"
    assert list(tmp_path.glob("*.owner")) == [owner]
    with pytest.raises(sc.StagingUnavailable, match="does not parse"):
        sc._read_owner_identity(owner)
    assert cr._active_urls(runners) == set(), (
        "precondition: the fixture unexpectedly contains an active job")

    orphans = cr.scan_for_orphans(
        s_cfg=_site_config(tmp_path), runners=runners,
        age_threshold_s=_AGE_THRESHOLD_S)

    assert ignored_fired["count"] == 1
    hits = [row for row in orphans if row["path"] == str(part)]
    assert len(hits) == 0, (
        "a .part with an unmeasurable staging claim was offered for deletion "
        f"exactly {len(hits)} time(s): {orphans}")


def test_scan_withholds_when_the_active_job_population_is_unmeasurable(
        tmp_path, monkeypatch):
    """A7: ``None`` jobs is UNKNOWN, not a measured empty mapping."""
    part = sc.staging_path_for(tmp_path / "UnknownJobs.mp4")
    part.write_bytes(b"unmeasurable-runner-bytes")
    meta_path = _write_production_meta(part)
    _age_part(part)
    owner = sc.owner_path_for(part)
    runners = {"site": SimpleNamespace(jobs=None)}
    ignored_fired = _isolate_ignored_paths(monkeypatch)

    assert part.is_file(), "precondition: the fixture did not create the .part"
    assert time.time() - part.stat().st_mtime > _AGE_THRESHOLD_S
    stored_meta = json.loads(meta_path.read_text(encoding="utf-8"))
    assert sum(key == "url" for key in stored_meta) == 0
    assert not owner.exists(), "precondition: the .part unexpectedly has a claim"
    assert runners["site"].jobs is None, (
        "precondition: the runner population is accidentally measurable")

    orphans = cr.scan_for_orphans(
        s_cfg=_site_config(tmp_path), runners=runners,
        age_threshold_s=_AGE_THRESHOLD_S)

    assert ignored_fired["count"] == 1
    hits = [row for row in orphans if row["path"] == str(part)]
    assert len(hits) == 0, (
        "a .part was offered for deletion after None runner.jobs was treated "
        f"as measured absence; exact hit count was {len(hits)}: {orphans}")


def test_scan_still_returns_exactly_one_measured_abandoned_part(
        tmp_path, monkeypatch):
    """NEGATIVE CONTROL: measured absence is still an orphan, not UNKNOWN."""
    part = sc.staging_path_for(tmp_path / "Abandoned.mp4")
    part.write_bytes(b"abandoned-transfer-bytes")
    meta_path = _write_production_meta(part)
    _age_part(part)
    owner = sc.owner_path_for(part)
    runners = {"site": SimpleNamespace(jobs={})}
    ignored_fired = _isolate_ignored_paths(monkeypatch)

    assert part.is_file(), "precondition: the fixture did not create the .part"
    assert time.time() - part.stat().st_mtime > _AGE_THRESHOLD_S
    stored_meta = json.loads(meta_path.read_text(encoding="utf-8"))
    assert sum(key == "url" for key in stored_meta) == 0
    assert not owner.exists(), "precondition: the abandoned .part has a claim"
    assert len(list(tmp_path.glob("*.owner"))) == 0, (
        "precondition: the fixture created an unexpected claim")
    assert cr._active_urls(runners) == set(), (
        "precondition: the fixture unexpectedly contains an active job")

    orphans = cr.scan_for_orphans(
        s_cfg=_site_config(tmp_path), runners=runners,
        age_threshold_s=_AGE_THRESHOLD_S)

    assert ignored_fired["count"] == 1
    hits = [row for row in orphans if row["path"] == str(part)]
    assert len(hits) == 1, (
        "the guard widened into hiding a measured abandoned .part; expected "
        f"exactly 1 hit, got {len(hits)} from {orphans}")


def test_delete_orphan_removes_the_exact_claim_guarding_its_part(
        tmp_path, monkeypatch):
    """Row 498: deleting claimed bytes must also invalidate their claim."""
    identity = sc.job_identity(_ACTIVE_URL)
    part = sc.claim(tmp_path / "Delete.mp4", identity)
    part.write_bytes(b"delete-me")
    meta_path = _write_production_meta(part)
    owner = sc.owner_path_for(part)
    decisions = []

    def record_decision(path, decision, **kwargs):
        decisions.append((path, decision, kwargs))
        return True

    monkeypatch.setattr(cr, "mark_decision", record_decision)

    assert part.is_file(), "precondition: the fixture did not create the .part"
    assert meta_path.is_file(), "precondition: the fixture did not create .meta"
    assert owner.is_file(), "precondition: staging_claim did not create .owner"
    assert list(tmp_path.glob("*.owner")) == [owner], (
        "precondition: the fixture did not create exactly one claim")
    assert sc._read_owner_identity(owner) == identity

    result = cr.delete_orphan(str(part))

    assert result == {"ok": True, "deleted_bytes": len(b"delete-me")}
    assert decisions == [(str(part), "deleted", {})], (
        "delete_orphan did not record its decision exactly once")
    assert not part.exists(), "delete_orphan left the .part behind"
    assert not meta_path.exists(), "delete_orphan left the .meta behind"
    assert not owner.exists(), (
        "delete_orphan removed .part and .meta but left the exact .owner claim "
        "guarding those bytes")

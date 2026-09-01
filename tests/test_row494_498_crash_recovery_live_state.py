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
from pathlib import Path
import subprocess
import sys
import time
from types import SimpleNamespace

import pytest

from bulk_downloader import crash_recovery as cr
from bulk_downloader import staging_claim as sc


BD_GATE_SCOPE = "module"

_ACTIVE_URL = "https://example.test/scene/active"
_AGE_THRESHOLD_S = 60
_OLD_AGE_S = 3600
_REPO = Path(__file__).resolve().parents[1]
_DELETE_CLAIM_URL = "urn:bulk-downloader:crash-recovery:operator-delete"


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


def test_scan_withholds_a_broken_owner_entry_as_unknown(
        tmp_path, monkeypatch):
    """A dangling owner name exists even though ``Path.exists`` is false."""
    part = sc.staging_path_for(tmp_path / "BrokenOwner.mp4")
    part.write_bytes(b"broken-owner-bytes")
    _write_production_meta(part)
    _age_part(part)
    owner = sc.owner_path_for(part)
    owner.symlink_to(tmp_path / "missing-owner-target")
    runners = {"site": SimpleNamespace(jobs={})}
    ignored_fired = _isolate_ignored_paths(monkeypatch)

    assert part.is_file(), "precondition: the fixture did not create the .part"
    assert time.time() - part.stat().st_mtime > _AGE_THRESHOLD_S
    assert owner.is_symlink(), "precondition: the owner entry is not a symlink"
    assert os.path.lexists(owner), "precondition: no owner directory entry exists"
    assert not owner.exists(), (
        "precondition: the owner target unexpectedly exists; this would not "
        "reproduce the Path.exists fail-open")
    assert cr._active_urls(runners) == set()

    orphans = cr.scan_for_orphans(
        s_cfg=_site_config(tmp_path), runners=runners,
        age_threshold_s=_AGE_THRESHOLD_S)

    assert ignored_fired["count"] == 1
    hits = [row for row in orphans if row["path"] == str(part)]
    assert len(hits) == 0, (
        "a .part with a broken-but-present owner entry was treated as measured "
        f"claim absence and returned exactly {len(hits)} time(s): {orphans}")


def test_scan_withholds_a_surviving_claim_without_a_live_runner(
        tmp_path, monkeypatch):
    """A claim is not absence merely because its holder is not in memory."""
    identity = sc.job_identity("https://example.test/scene/restart-residue")
    part = sc.claim(tmp_path / "RestartResidue.mp4", identity)
    part.write_bytes(b"claimed-crash-residue")
    _write_production_meta(part)
    _age_part(part)
    owner = sc.owner_path_for(part)
    runners = {"site": SimpleNamespace(jobs={})}
    ignored_fired = _isolate_ignored_paths(monkeypatch)

    assert part.is_file(), "precondition: the fixture did not create the .part"
    assert time.time() - part.stat().st_mtime > _AGE_THRESHOLD_S
    assert owner.is_file(), "precondition: staging_claim did not create .owner"
    assert sc._read_owner_identity(owner) == identity
    assert cr._active_urls(runners) == set()

    orphans = cr.scan_for_orphans(
        s_cfg=_site_config(tmp_path), runners=runners,
        age_threshold_s=_AGE_THRESHOLD_S)

    assert ignored_fired["count"] == 1
    hits = [row for row in orphans if row["path"] == str(part)]
    assert len(hits) == 0, (
        "a surviving staging claim was treated as claim absence and returned "
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


def test_delete_refuses_a_worker_claim_created_after_the_scan(
        tmp_path, monkeypatch):
    """A worker winning the claim race makes a stale scan non-authoritative."""
    identity = sc.job_identity(_ACTIVE_URL)
    final = tmp_path / "DeleteRace.mp4"
    part = sc.staging_path_for(final)
    part.write_bytes(b"old-abandoned-bytes")
    meta_path = _write_production_meta(part)
    _age_part(part)
    runners = {"site": SimpleNamespace(jobs={})}
    ignored_fired = _isolate_ignored_paths(monkeypatch)

    assert part.is_file()
    assert not sc.owner_path_for(part).exists()
    scanned = cr.scan_for_orphans(
        s_cfg=_site_config(tmp_path), runners=runners,
        age_threshold_s=_AGE_THRESHOLD_S)
    hits = [row for row in scanned if row["path"] == str(part)]
    assert ignored_fired["count"] == 1
    assert len(hits) == 1, (
        "precondition: the unclaimed old .part was not scanned exactly once")

    claimed_part = sc.claim(final, identity)
    assert claimed_part == part
    claimed_part.write_bytes(b"new-live-worker-bytes")
    owner = sc.owner_path_for(part)
    decisions = []

    def record_decision(path, decision, **kwargs):
        decisions.append((path, decision, kwargs))
        return True

    monkeypatch.setattr(cr, "mark_decision", record_decision)

    assert part.read_bytes() == b"new-live-worker-bytes", (
        "precondition: the worker did not write nonzero live bytes after scan")
    assert owner.is_file(), "precondition: the worker did not claim the path"
    assert list(tmp_path.glob("*.owner")) == [owner], (
        "precondition: the fixture did not create exactly one claim")
    assert sc._read_owner_identity(owner) == identity

    result = cr.delete_orphan(str(part))

    assert result["ok"] is False, (
        "delete_orphan accepted a stale scan after a worker claimed the path")
    assert "claim" in result["error"].lower()
    assert decisions == [], "a refused delete was recorded as completed"
    assert part.read_bytes() == b"new-live-worker-bytes", (
        "delete_orphan removed or changed the worker's live bytes")
    assert owner.is_file(), "delete_orphan removed the worker's live claim"
    assert sc._read_owner_identity(owner) == identity
    assert meta_path.is_file(), "delete_orphan removed live resume metadata"


def test_delete_orphan_releases_exactly_the_claim_it_acquires(
        tmp_path, monkeypatch):
    """Row 498: the operator reservation must not survive its deletion."""
    part = sc.staging_path_for(tmp_path / "Delete.mp4")
    part.write_bytes(b"delete-me")
    meta_path = _write_production_meta(part)
    owner = sc.owner_path_for(part)
    decisions = []
    releases = []
    real_release = sc.release

    def record_release(staging_path, identity=None, *, force=False):
        releases.append((Path(staging_path), identity, force))
        return real_release(staging_path, identity, force=force)

    def record_decision(path, decision, **kwargs):
        decisions.append((path, decision, kwargs))
        return True

    monkeypatch.setattr(sc, "release", record_release)
    monkeypatch.setattr(cr, "mark_decision", record_decision)

    assert part.is_file(), "precondition: the fixture did not create the .part"
    assert meta_path.is_file(), "precondition: the fixture did not create .meta"
    assert not owner.exists(), "precondition: the abandoned part has a claim"

    result = cr.delete_orphan(str(part))

    expected_identity = sc.job_identity(_DELETE_CLAIM_URL)
    assert releases == [(part, expected_identity, False)], (
        "delete_orphan did not release exactly once with the identity of its "
        f"own operator reservation: {releases}")
    assert result == {"ok": True, "deleted_bytes": len(b"delete-me")}
    assert decisions == [(str(part), "deleted", {})], (
        "delete_orphan did not record its decision exactly once")
    assert not part.exists(), "delete_orphan left the .part behind"
    assert not meta_path.exists(), "delete_orphan left the .meta behind"
    assert not owner.exists(), (
        "delete_orphan removed .part and .meta but left the exact .owner claim "
        "guarding those bytes")


def test_delete_reports_a_retained_operator_claim_instead_of_false_success(
        tmp_path, monkeypatch):
    """A failed release is a partial failure and is never audited as deleted."""
    part = sc.staging_path_for(tmp_path / "RetainedClaim.mp4")
    part.write_bytes(b"delete-before-release-failure")
    _write_production_meta(part)
    owner = sc.owner_path_for(part)
    decisions = []
    releases = {"count": 0}
    real_release = sc.release

    def retain_claim(staging_path, identity=None, *, force=False):
        releases["count"] += 1
        assert Path(staging_path) == part
        assert identity == sc.job_identity(_DELETE_CLAIM_URL)
        assert force is False
        assert owner.is_file(), (
            "precondition: delete_orphan never acquired the claim it reports")
        return False

    monkeypatch.setattr(sc, "release", retain_claim)
    monkeypatch.setattr(
        cr, "mark_decision",
        lambda *args, **kwargs: decisions.append((args, kwargs)) or True)

    assert part.is_file()
    assert not owner.exists(), "precondition: the part starts unclaimed"

    result = cr.delete_orphan(str(part))

    assert releases["count"] == 1
    assert result["ok"] is False
    assert "claim" in result["error"].lower()
    assert decisions == [], "a retained claim was audited as a completed delete"
    assert owner.is_file(), "the mocked retained claim did not remain observable"

    monkeypatch.setattr(sc, "release", real_release)
    retry = cr.delete_orphan(str(part))

    assert retry["ok"] is True
    assert "interrupted delete" in retry["note"]
    assert not owner.exists(), (
        "an idempotent retry treated the absent .part as complete while its "
        "operator claim still survived")
    assert decisions == [((str(part), "deleted"), {})], (
        "the delete was not audited exactly once after its retained claim was "
        f"successfully cleaned up: {decisions}")


def test_band_names_the_structural_staging_contract_signal():
    """Row 498: comment text is not evidence for a consumer contract edge."""
    graph_path = _REPO / "tools/decomp/import_graph_baseline.json"
    graph = json.loads(graph_path.read_text(encoding="utf-8"))["edges"]
    gate = "tests/test_part_staging_collision.py"
    consumer = "bulk_downloader/crash_recovery.py"
    provider = "bulk_downloader/staging_claim.py"

    assert graph[consumer].count(provider) == 1, (
        "precondition: the generated graph does not contain exactly one "
        "crash-recovery -> staging-claim edge")
    assert graph[gate].count(provider) == 1, (
        "precondition: the contract gate does not consume staging_claim once")
    assert (_REPO / gate).is_file(), "precondition: the contract gate is absent"

    run = subprocess.run(
        [sys.executable, str(_REPO / "toolchain/bin/bd-band-derive"),
         "--work", str(_REPO), "--file", consumer, "--json"],
        cwd=_REPO, text=True, capture_output=True, check=False,
    )
    assert run.returncode == 0, (
        f"precondition: band derivation did not complete: {run.stderr}")
    payload = json.loads(run.stdout)
    assert payload["band"].count(gate) == 1, (
        "the staging contract gate was not selected exactly once")
    expected = f"IMPORT-CONTRACT: {consumer} -> {provider} -> {gate}"
    assert payload.get("selecting_signals", []).count(expected) == 1, (
        "the gate appears only through a text/comment match; no structural "
        f"import-contract signal named the edge: {payload}")
    assert payload.get("import_contract_unknown", []) == [], (
        "the derivation returned a band while its import edge was UNKNOWN")


def test_import_contract_signal_is_scoped_and_refuses_unknown(tmp_path):
    """NEGATIVE CONTROL: the staging gate is not added to every module."""
    gate = "tests/test_part_staging_collision.py"

    def derive(subject):
        run = subprocess.run(
            [sys.executable, str(_REPO / "toolchain/bin/bd-band-derive"),
             "--work", str(_REPO), "--file", subject, "--json"],
            cwd=_REPO, text=True, capture_output=True, check=False,
        )
        assert run.returncode == 0, (
            f"precondition: {subject} band did not resolve: {run.stderr}")
        payload = json.loads(run.stdout)
        assert payload["import_contract_unknown"] == []
        return payload

    staging = derive("bulk_downloader/staging_claim.py")
    runner = derive("bulk_downloader/runner_transport.py")
    unrelated = derive("bulk_downloader/enrichment.py")
    assert staging["band"].count(gate) == 1
    assert runner["band"].count(gate) == 1
    assert unrelated["band"].count(gate) == 0, (
        "the structural contract signal widened into selecting the staging "
        "gate for an unrelated module")

    unknown_root = tmp_path / "unknown-graph"
    (unknown_root / "bulk_downloader").mkdir(parents=True)
    unknown_subject = unknown_root / "bulk_downloader" / "consumer.py"
    unknown_subject.write_text("VALUE = 1\n", encoding="utf-8")
    unknown = subprocess.run(
        [sys.executable, str(_REPO / "toolchain/bin/bd-band-derive"),
         "--work", str(unknown_root), "--file",
         "bulk_downloader/consumer.py", "--json"],
        cwd=_REPO, text=True, capture_output=True, check=False,
    )
    assert unknown.returncode == 2, (
        "an unresolved import graph returned a permission-like success: "
        f"stdout={unknown.stdout!r} stderr={unknown.stderr!r}")
    unknown_payload = json.loads(unknown.stdout)
    assert len(unknown_payload["import_contract_unknown"]) == 1
    assert "IMPORT-CONTRACT UNKNOWN" in unknown.stderr

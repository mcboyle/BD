"""Row 1068: Multi-Tenant Ledger Partition Sharding by Epoch & Domain.

Validates:
1. Multi-tenant ledger partition sharding isolating ledger records by tenant ID, domain, and epoch.
2. Epoch boundary tracking and partition sealing with independent Merkle/hash chains.
3. Tamper-evident chain verification per partition shard (corruption in one shard does not invalidate other shards).
4. Direct caller integration through bulk_downloader.provenance; no HTTP route (RESCOPED: the
   row's spec names none, and the F5.1 route surface stays at its baseline).
5. High-concurrency thread safety across multi-tenant writes without cross-tenant lock contention.

RED on baseline: fails with explicit semantic AssertionError (capability missing),
never an unhandled ImportError or ModuleNotFoundError.
Includes positive control test passing on baseline to prove the probe can say YES.
"""
from __future__ import annotations

import concurrent.futures
import pytest

BD_GATE_SCOPE = "module"

try:
    from bulk_downloader import ledger_sharding
except ImportError:
    ledger_sharding = None


@pytest.fixture(autouse=True)
def _isolated_ledger(tmp_path, monkeypatch):
    """Each test gets its own durable ledger (sqlite) and a fresh shard
    router: shards are rebuilt from the durable ledger, so a shared database
    would leak one test's rows into the next test's partitions."""
    from bulk_downloader import db
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "provenance-1068.db"))
    if ledger_sharding is not None:
        ledger_sharding.reset_ledger_router()
    yield
    if ledger_sharding is not None:
        ledger_sharding.reset_ledger_router()


def test_positive_control_provenance_baseline():
    """Positive control (Rule 7): proves test runner and probe can say YES on baseline capabilities."""
    from bulk_downloader import provenance

    assert hasattr(provenance, "record"), "provenance.record must exist on baseline"
    assert hasattr(provenance, "query"), "provenance.query must exist on baseline"
    assert callable(provenance.record), "provenance.record must be callable"


def test_ledger_sharding_capability_implemented():
    """RED assertion: capability and product callers must be implemented with semantic AssertionError on base."""
    from bulk_downloader import provenance

    assert ledger_sharding is not None, (
        "Row 1068 capability missing: Multi-Tenant Ledger Partition Sharding by Epoch & Domain "
        "not implemented in bulk_downloader.ledger_sharding"
    )
    assert hasattr(provenance, "query_sharded"), (
        "Row 1068 caller missing: bulk_downloader.provenance.query_sharded"
    )
    assert hasattr(provenance, "get_sharded_partitions"), (
        "Row 1068 caller missing: bulk_downloader.provenance.get_sharded_partitions"
    )
    assert hasattr(provenance, "verify_sharded_chains"), (
        "Row 1068 caller missing: bulk_downloader.provenance.verify_sharded_chains"
    )


def test_module_exports():
    """Verify bulk_downloader.ledger_sharding exports core architecture components."""
    assert ledger_sharding is not None, "ledger_sharding capability missing"

    assert hasattr(ledger_sharding, "ShardKey")
    assert hasattr(ledger_sharding, "PartitionMetadata")
    assert hasattr(ledger_sharding, "PartitionedLedgerEntry")
    assert hasattr(ledger_sharding, "LedgerPartitionShard")
    assert hasattr(ledger_sharding, "MultiTenantLedgerRouter")
    assert hasattr(ledger_sharding, "get_ledger_router")
    assert hasattr(ledger_sharding, "reset_ledger_router")


def test_multi_tenant_partition_isolation():
    """Verify that distinct tenants and domains write to isolated partition shards with distinct hash chains."""
    from bulk_downloader import provenance

    if ledger_sharding is not None:
        ledger_sharding.reset_ledger_router()

    t1 = 1700000000.0  # Epoch A
    t2 = t1 + 10.0

    # Tenant 1 (site_alpha), domain alpha.example.org
    provenance.record(
        site_id="site_alpha",
        source_url="https://alpha.example.org/files/doc1.pdf",
        final_filename="doc1.pdf",
        sha256="aaa111",
        file_size=1024,
        ts_finished=t1,
    )

    # Tenant 2 (site_beta), domain beta.example.org
    provenance.record(
        site_id="site_beta",
        source_url="https://beta.example.org/files/doc2.pdf",
        final_filename="doc2.pdf",
        sha256="bbb222",
        file_size=2048,
        ts_finished=t2,
    )

    partitions = provenance.get_sharded_partitions()
    assert len(partitions) >= 2, f"Expected at least 2 partitioned shards, got {partitions}"

    tenants = {p["tenant_id"] for p in partitions}
    assert "site_alpha" in tenants
    assert "site_beta" in tenants

    # Query Tenant 1 specifically through provenance
    res_alpha = provenance.query_sharded(tenant_id="site_alpha")
    assert len(res_alpha) == 1
    assert res_alpha[0]["sha256"] == "aaa111"
    assert res_alpha[0]["tenant_id"] == "site_alpha"

    # Query Tenant 2 specifically through provenance
    res_beta = provenance.query_sharded(tenant_id="site_beta")
    assert len(res_beta) == 1
    assert res_beta[0]["sha256"] == "bbb222"
    assert res_beta[0]["tenant_id"] == "site_beta"


def test_epoch_boundary_rotation_and_sealing():
    """Verify that ledger entries cross epoch boundaries into separate shards and can be sealed."""
    from bulk_downloader import provenance

    if ledger_sharding is not None:
        ledger_sharding.reset_ledger_router()

    epoch_len = 3600.0  # 1 hour
    router = ledger_sharding.get_ledger_router() if ledger_sharding else None
    if router:
        router.epoch_duration_seconds = epoch_len

    base_time = 1700000000.0
    time_epoch_0 = base_time + 100.0
    time_epoch_1 = base_time + epoch_len + 100.0

    # Write in Epoch 0
    provenance.record(
        site_id="site_gamma",
        source_url="https://cdn.example.org/v1/item1.bin",
        final_filename="item1.bin",
        sha256="c01",
        ts_finished=time_epoch_0,
    )

    # Write in Epoch 1
    provenance.record(
        site_id="site_gamma",
        source_url="https://cdn.example.org/v1/item2.bin",
        final_filename="item2.bin",
        sha256="c02",
        ts_finished=time_epoch_1,
    )

    partitions = provenance.get_sharded_partitions(tenant_id="site_gamma")
    epochs = {p["epoch"] for p in partitions}
    assert len(epochs) == 2, f"Expected 2 distinct epochs, got {epochs}"

    # Seal epoch 0
    epoch_0 = min(epochs)
    sealed_shard = router.seal_partition("site_gamma", "cdn.example.org", epoch_0)
    assert sealed_shard is not None
    assert sealed_shard.is_sealed

    # Sealing produces a deterministic root hash and rejects further appends
    with pytest.raises(RuntimeError):
        sealed_shard.append({"test": "mutation"}, timestamp=time_epoch_0)


def test_tamper_evident_partition_verification():
    """Verify that tamper verification works per-partition and localizes corruption."""
    from bulk_downloader import provenance

    if ledger_sharding is not None:
        ledger_sharding.reset_ledger_router()

    t0 = 1700000000.0
    for i in range(3):
        provenance.record(
            site_id="site_delta",
            source_url=f"https://delta.example.org/item_{i}",
            final_filename=f"item_{i}.dat",
            sha256=f"hash_{i}",
            ts_finished=t0 + i,
        )

    # Verification passes initially
    report = provenance.verify_sharded_chains(tenant_id="site_delta")
    assert report["valid"] is True
    assert report["tampered_partitions"] == []
    assert report["verified_count"] >= 3

    # Tamper with an entry in site_delta's partition
    router = ledger_sharding.get_ledger_router()
    key = router.resolve_shard_key("site_delta", "https://delta.example.org/item_0", t0)
    shard = router.get_shard(key)
    assert shard is not None

    # Corrupt entry content in memory
    shard._entries[1].data["final_filename"] = "corrupted.dat"

    # Re-verify: detection must flag exactly the tampered partition
    report_tampered = provenance.verify_sharded_chains(tenant_id="site_delta")
    assert report_tampered["valid"] is False
    assert len(report_tampered["tampered_partitions"]) == 1
    assert key.partition_id in report_tampered["tampered_partitions"]


def test_concurrent_multi_tenant_partition_writes():
    """Verify thread-safety and lack of cross-tenant lock contention under high concurrency."""
    from bulk_downloader import provenance

    if ledger_sharding is not None:
        ledger_sharding.reset_ledger_router()

    num_tenants = 8
    writes_per_tenant = 25
    errors = []

    def tenant_worker(t_idx: int):
        tenant_id = f"tenant_{t_idx}"
        domain = f"tenant-{t_idx}.domain.com"
        base_ts = 1700000000.0 + (t_idx * 1000)
        try:
            for w in range(writes_per_tenant):
                provenance.record(
                    site_id=tenant_id,
                    source_url=f"https://{domain}/resource/{w}",
                    final_filename=f"file_{w}.bin",
                    sha256=f"sha_{t_idx}_{w}",
                    ts_finished=base_ts + w,
                )
        except Exception as exc:
            errors.append((t_idx, exc))

    with concurrent.futures.ThreadPoolExecutor(max_workers=num_tenants) as executor:
        futures = [executor.submit(tenant_worker, idx) for idx in range(num_tenants)]
        concurrent.futures.wait(futures)

    assert not errors, f"Concurrent execution encountered errors: {errors}"

    for t_idx in range(num_tenants):
        rows = provenance.query_sharded(tenant_id=f"tenant_{t_idx}", limit=100)
        assert len(rows) == writes_per_tenant, f"Tenant {t_idx} wrote {len(rows)} expected {writes_per_tenant}"

    # All shards must be internally consistent
    report = provenance.verify_sharded_chains()
    assert report["valid"] is True
    assert report["verified_count"] == num_tenants * writes_per_tenant


def test_partitions_are_read_through_provenance_not_an_http_route():
    """RESCOPED (POLICY-0010 s3): register row 1068 names no HTTP endpoint, so a
    tenant's partitions are read through provenance.get_sharded_partitions()
    and the provenance blueprint adds no route (the F5.1 url_map baseline in
    tests/test_route_map_invariant.py stays at its count)."""
    from flask import Flask
    from bulk_downloader.app_provenance import provenance_bp
    from bulk_downloader import provenance

    provenance.record(
        site_id="site_route_test",
        source_url="https://route.example.org/item.zip",
        final_filename="item.zip",
        sha256="ziphash",
    )
    provenance.record(
        site_id="site_route_other",
        source_url="https://route.example.org/other.zip",
        final_filename="other.zip",
        sha256="otherhash",
    )

    parts = provenance.get_sharded_partitions(tenant_id="site_route_test")
    assert [(p["tenant_id"], p["row_count"]) for p in parts] == [("site_route_test", 1)], parts

    app = Flask(__name__)
    app.register_blueprint(provenance_bp)
    with app.test_client() as client:
        resp = client.get("/api/provenance/shards?tenant_id=site_route_test")
    assert resp.status_code == 404, (
        f"row1068: /api/provenance/shards answered {resp.status_code}; the row's spec "
        f"names no HTTP endpoint, so the url_map must stay at the F5.1 baseline")


# ── r2 (N6-A E1-E4, B3 1-5 on 977efcb1) ──────────────────────────────────

def _record(site, i, t0=1700000000.0, host="ledger.example.org"):
    from bulk_downloader import provenance
    return provenance.record(site_id=site, source_url=f"https://{host}/item_{i}",
                             final_filename=f"item_{i}.dat", sha256=f"h{i}",
                             ts_finished=t0 + i)


def _fresh_process():
    """What a restart does to the in-process shard mirror."""
    ledger_sharding.reset_ledger_router()


def test_shards_survive_a_restart():
    # E1: the shards were a per-process mirror nothing persisted, so after a
    # restart verification answered valid over nothing.
    from bulk_downloader import provenance
    for i in range(3):
        _record("site_restart", i)
    _fresh_process()
    report = provenance.verify_sharded_chains(tenant_id="site_restart")
    assert report["verified_count"] == 3, (
        f"row1068: after a restart the partitions held "
        f"{report['verified_count']} of 3 recorded entries -- shards are "
        f"not durable ({report!r})")
    assert report["valid"] is True
    parts = provenance.get_sharded_partitions(tenant_id="site_restart")
    assert [p["row_count"] for p in parts] == [3]
    assert len(provenance.query_sharded(tenant_id="site_restart")) == 3


def test_tampering_of_the_durable_ledger_is_localized_to_its_partition():
    # E1: the durable sqlite rows are the ledger; editing one must be caught
    # and pinned to the partition it belongs to, before and after a restart.
    from bulk_downloader import db, provenance
    ids = [_record("site_durable", i) for i in range(3)]
    _record("site_other", 0, host="other.example.org")
    assert provenance.verify_sharded_chains()["valid"] is True
    key = ledger_sharding.get_ledger_router().resolve_shard_key(
        "site_durable", "https://ledger.example.org/item_0", 1700000000.0)
    with db.db_conn() as cx:
        cx.execute("UPDATE provenance SET final_filename='swapped.dat' "
                   "WHERE id=?", (ids[1],))
    for when in ("live", "after restart"):
        report = provenance.verify_sharded_chains()
        assert report["valid"] is False, (
            f"row1068 ({when}): an edited durable ledger row verified "
            f"valid ({report!r})")
        assert report["tampered_partitions"] == [key.partition_id], report
        _fresh_process()


def test_verify_does_not_answer_valid_when_it_verified_nothing(monkeypatch):
    # E3 / B3-1: zero entries and sharding-unavailable are not "valid".
    from bulk_downloader import provenance
    empty = provenance.verify_sharded_chains(tenant_id="nobody")
    assert (empty.get("valid"), empty.get("status"), empty.get("verified_count")) == (
        None, "empty", 0), (
        f"row1068: verification of zero entries answered {empty!r}")
    monkeypatch.setattr(provenance, "_SHARDING_AVAILABLE", False)
    off = provenance.verify_sharded_chains()
    assert off.get("valid") is None and off.get("status") == "unavailable", (
        f"row1068: verification with sharding unavailable answered {off!r}")


def test_lookups_do_not_report_found_none_when_they_could_not_look(monkeypatch):
    # B3-4/5: [] means "looked, found none"; unavailable must be distinct.
    from bulk_downloader import provenance
    assert provenance.query_sharded(tenant_id="nobody") == []
    assert provenance.get_sharded_partitions(tenant_id="nobody") == []
    monkeypatch.setattr(provenance, "_SHARDING_AVAILABLE", False)
    for lookup in (provenance.query_sharded, provenance.get_sharded_partitions):
        try:
            got = lookup(tenant_id="nobody")
        except Exception as exc:
            assert type(exc).__name__ == "ShardingUnavailable", exc
        else:
            pytest.fail(f"row1068: {lookup.__name__} answered {got!r} (found "
                        f"none) when the shards could not be consulted")


def test_a_failed_shard_write_is_observable(monkeypatch, capsys):
    # E2 / B3-2: the durable row lands; the divergence must be counted and
    # logged, not swallowed.
    from bulk_downloader import provenance
    provenance.verify_sharded_chains()  # mirror built

    def broken(**_kw):
        raise RuntimeError("shard backend down")

    monkeypatch.setattr(ledger_sharding, "record_sharded_ledger_entry", broken)
    failures = getattr(provenance, "shard_write_failures", lambda: 0)
    before = failures()
    assert _record("site_diverge", 0) is not None
    assert failures() == before + 1, (
        "row1068: a failed shard write left no counter behind")
    assert "shard backend down" in capsys.readouterr().err
    # The mirror now lacks a row the durable ledger holds: verification must
    # name that partition instead of vouching for the mirror.
    report = provenance.verify_sharded_chains(tenant_id="site_diverge")
    assert report.get("valid") is not True and report.get("tampered_partitions"), (
        f"row1068: a shard mirror missing a durable row verified valid ({report!r})")


def test_chain_linkage_detects_deleted_and_reordered_entries():
    # E4: per-entry content hashes alone cannot see a dropped or swapped
    # entry; only the chain can.
    router = ledger_sharding.MultiTenantLedgerRouter()
    for i in range(4):
        router.record_entry("t", "https://chain.example.org/x", {"n": i},
                            timestamp=1700000000.0 + i)
    shard = next(iter(router._shards.values()))
    assert shard.verify_chain()[0] is True
    entries = list(shard._entries)
    shard._entries[1], shard._entries[2] = entries[2], entries[1]
    assert shard.verify_chain()[0] is False, "row1068: reordered entries verified"
    shard._entries[:] = [entries[0], entries[2], entries[3]]
    assert shard.verify_chain()[0] is False, "row1068: a deleted entry verified"


def test_import_guard_only_hides_an_absent_module(monkeypatch, capsys):
    # B3-3: a module that fails to import for another reason must not be
    # reported as "sharding not installed"; an absent one is logged.
    import importlib
    import sys
    import bulk_downloader
    from bulk_downloader import provenance

    def reload_with(exc):
        class _Finder:
            def find_spec(self, name, path=None, target=None):
                if name == "bulk_downloader.ledger_sharding":
                    raise exc
                return None
        raised = None
        with monkeypatch.context() as m:
            m.delitem(sys.modules, "bulk_downloader.ledger_sharding")
            m.delattr(bulk_downloader, "ledger_sharding")
            m.setattr(sys, "meta_path", [_Finder()] + sys.meta_path)
            try:
                importlib.reload(provenance)
            except Exception as exc:
                raised = exc
            reload_with.state = (provenance._SHARDING_AVAILABLE,
                                 getattr(provenance, "_SHARDING_IMPORT_ERROR", None))
        importlib.reload(provenance)  # the real module is back on sys.modules
        return raised

    raised = reload_with(ValueError("broken shard module"))
    assert isinstance(raised, ValueError), (
        f"row1068: a broken ledger_sharding module was swallowed as 'not "
        f"available' (state={reload_with.state!r})")
    assert reload_with(ImportError("no shard module")) is None
    assert reload_with.state[0] is False
    assert "no shard module" in (reload_with.state[1] or "")
    assert "no shard module" in capsys.readouterr().err
    assert provenance._SHARDING_AVAILABLE is True


# ── r3 (bd-fixer-A self-lens on the rebased cut) ────────────────────────────

def _offline_edit(row_ids):
    """Rows edited while no process held a mirror, then a restart: the
    rebuilt mirror agrees with the edited rows, so only verify_chain() can
    see the edit."""
    from bulk_downloader import db
    with db.db_conn() as cx:
        for row_id in row_ids:
            cx.execute("UPDATE provenance SET final_filename='swapped.dat' "
                       "WHERE id=?", (row_id,))
    _fresh_process()


def test_a_tenant_is_not_verified_past_a_durable_break_it_cannot_see():
    # verify_chain() stops at the first bad row. site_a's edited row lies
    # after site_b's, so it was never checked, and site_a answered "verified".
    from bulk_downloader import provenance
    b0 = _record("site_b", 0, host="b.example.org")
    _record("site_a", 1, host="a.example.org")
    a2 = _record("site_a", 2, host="a.example.org")
    _record("site_b", 3, host="b.example.org")
    control = provenance.verify_sharded_chains(tenant_id="site_a")
    assert (control["valid"], control["status"], control["verified_count"]) == (
        True, "verified", 2), control
    _offline_edit([b0, a2])
    report = provenance.verify_sharded_chains(tenant_id="site_a")
    assert report["valid"] is not True, (
        f"row1068: site_a's edited row lies past site_b's durable break, was "
        f"never checked, and site_a verified valid ({report!r})")
    assert (report["valid"], report["status"]) == (None, "unavailable"), report
    assert f"row {b0}:" in report["error"], report
    whole = provenance.verify_sharded_chains()
    assert (whole["valid"], whole["status"]) == (False, "tampered"), whole


def test_a_durable_break_pinned_to_no_partition_is_never_verified(monkeypatch):
    # A durable break whose row could not be mapped to a partition, or a
    # durable walk that could not run, answered "verified".
    from bulk_downloader import provenance
    ids = [_record("site_pin", i) for i in range(2)]
    _offline_edit([ids[1]])
    monkeypatch.setattr(provenance, "_partition_of_row", lambda router, row_id: None)
    for scope in (None, "", " "):  # the router reads all three as "every tenant"
        whole = provenance.verify_sharded_chains(tenant_id=scope)
        assert (whole["valid"], whole["status"]) == (False, "tampered"), (
            f"row1068: a durable break mapped to no partition answered "
            f"{whole['status']!r} for the whole ledger (tenant_id={scope!r}: {whole!r})")
    one = provenance.verify_sharded_chains(tenant_id="site_pin")
    assert (one["valid"], one["status"]) == (None, "unavailable"), one
    monkeypatch.setattr(provenance, "verify_chain", lambda **_kw: {
        "ok": False, "checked": 0, "first_bad_id": None,
        "message": "db unavailable: probe"})
    blind = provenance.verify_sharded_chains()
    assert (blind["valid"], blind["status"]) == (None, "unavailable"), (
        f"row1068: a durable walk that could not run answered "
        f"{blind['status']!r}, not 'unavailable' ({blind!r})")
    assert "db unavailable: probe" in blind["error"], blind


def test_a_tenant_is_selected_by_the_routers_key_not_by_sql():
    # The durable rebuild selected a tenant with SQL lower(), which neither
    # strips nor folds non-ASCII case, so an intact ledger answered "tampered".
    from bulk_downloader import provenance
    for i, site in enumerate((" Padded ", "Ärzte")):
        _record(site, i, host="n.example.org")
    for tenant in ("padded", " Padded ", "ärzte", "Ärzte"):
        report = provenance.verify_sharded_chains(tenant_id=tenant)
        assert (report["valid"], report["status"], report["verified_count"]) == (
            True, "verified", 1), (
            f"row1068: an intact ledger answered {report['status']!r} for "
            f"tenant {tenant!r} ({report!r})")

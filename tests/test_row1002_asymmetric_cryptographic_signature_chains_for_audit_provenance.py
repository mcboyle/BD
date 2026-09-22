"""Row 1002: Asymmetric Cryptographic Signature Chains for Audit Provenance.

Validates asymmetric key generation, cryptographic signing of audit blocks,
cryptographic hash linkage (Merkle-style chain), multi-signer provenance chains,
JSON/JSONL export/import, and robust tamper detection across payloads, hashes,
signatures, and sequence linkages.

RED on baseline: bulk_downloader.signature_chains does not exist.
"""
from __future__ import annotations

import json

import pytest
from typing import Any, Dict

try:
    from bulk_downloader.signature_chains import (
        AuditSigner,
        AuditVerifier,
        ChainVerificationResult,
        ProvenanceBlock,
        SignatureProvenanceChain,
        create_audit_signer,
        create_provenance_chain,
    )
except (ImportError, ModuleNotFoundError):
    AuditSigner = None
    AuditVerifier = None
    ChainVerificationResult = None
    ProvenanceBlock = None
    SignatureProvenanceChain = None
    create_audit_signer = None
    create_provenance_chain = None

BD_GATE_SCOPE = "module"


def test_module_exports():
    """RED assertion 1: bulk_downloader.signature_chains must be importable and export components."""
    assert SignatureProvenanceChain is not None, "SignatureProvenanceChain capability must be present (bulk_downloader.signature_chains)"
    assert AuditSigner is not None, "AuditSigner capability must be present"
    assert AuditVerifier is not None, "AuditVerifier capability must be present"
    assert ProvenanceBlock is not None, "ProvenanceBlock capability must be present"
    assert ChainVerificationResult is not None, "ChainVerificationResult capability must be present"
    assert create_audit_signer is not None, "create_audit_signer capability must be present"
    assert create_provenance_chain is not None, "create_provenance_chain capability must be present"


def test_keypair_generation_signing_and_verification():
    """Verify Ed25519 keypair generation, block signing, and cryptographic verification."""
    from bulk_downloader.signature_chains import AuditSigner, AuditVerifier

    signer = AuditSigner(signer_id="seat-worker-1")
    public_key_hex = signer.public_key_hex
    assert len(public_key_hex) == 64  # 32 bytes hex encoded

    verifier = AuditVerifier(signer_id="seat-worker-1", public_key_hex=public_key_hex)

    data = b"audit-log-entry-payload-data"
    sig = signer.sign(data)
    assert len(sig) == 64  # Ed25519 signature is 64 bytes

    assert verifier.verify(data, sig) is True
    # Tampered data must fail
    assert verifier.verify(data + b"-tampered", sig) is False


def test_provenance_chain_genesis_and_append():
    """Verify genesis block creation and strict cryptographic chaining of subsequent events."""
    from bulk_downloader.signature_chains import create_audit_signer, create_provenance_chain

    signer = create_audit_signer("seat-worker-g13")
    chain = create_provenance_chain(signer=signer)

    assert len(chain) == 1
    genesis = chain[0]
    assert genesis.sequence == 0
    assert genesis.event_type == "GENESIS"
    assert genesis.prev_hash == "0" * 64

    # Append events
    b1 = chain.append_event("ARTIFACT_PRODUCED", {"artifact": "file_1.mp4", "sha256": "abc"}, signer)
    assert b1.sequence == 1
    assert b1.prev_hash == genesis.block_hash
    assert b1.signature is not None

    b2 = chain.append_event("STATUS_MUTATION", {"new_status": "VERIFIED"}, signer)
    assert b2.sequence == 2
    assert b2.prev_hash == b1.block_hash
    assert len(chain) == 3


def test_chain_integrity_verification_pass():
    """Verify clean provenance chain verifies with zero errors."""
    from bulk_downloader.signature_chains import create_audit_signer, create_provenance_chain

    signer = create_audit_signer("auditor-primary")
    chain = create_provenance_chain(signer=signer)

    for i in range(5):
        chain.append_event("TEST_EVENT", {"iteration": i, "val": i * 10}, signer)

    res = chain.verify_chain()
    assert res.valid is True
    assert res.block_count == 6
    assert len(res.errors) == 0


def test_tamper_detection_payload_modification():
    """Verify modifying a block payload invalidates the block hash and triggers tamper error."""
    from bulk_downloader.signature_chains import create_audit_signer, create_provenance_chain

    signer = create_audit_signer("auditor-primary")
    chain = create_provenance_chain(signer=signer)
    chain.append_event("CONFIG_EDIT", {"key": "BD_LIMIT", "val": 100}, signer)
    chain.append_event("CONFIG_EDIT", {"key": "BD_TIMEOUT", "val": 30}, signer)

    # Tamper with block 1 payload directly
    chain.blocks[1].payload["val"] = 999

    res = chain.verify_chain()
    assert res.valid is False
    assert any("PAYLOAD_HASH_MISMATCH" in err or "HASH_MISMATCH" in err for err in res.errors)
    assert any("sequence 1" in err.lower() or "block 1" in err.lower() for err in res.errors)


def test_tamper_detection_signature_forgery():
    """Verify invalid or forged signature on a block is detected."""
    from bulk_downloader.signature_chains import create_audit_signer, create_provenance_chain

    signer = create_audit_signer("auditor-primary")
    chain = create_provenance_chain(signer=signer)
    chain.append_event("EVENT_A", {"code": 1}, signer)
    chain.append_event("EVENT_B", {"code": 2}, signer)

    # Corrupt block 2 signature
    sig_bytes = bytearray(chain.blocks[2].signature)
    sig_bytes[0] ^= 0xFF
    chain.blocks[2].signature = bytes(sig_bytes)

    res = chain.verify_chain()
    assert res.valid is False
    assert any("SIGNATURE_INVALID" in err for err in res.errors)
    assert any("sequence 2" in err.lower() or "block 2" in err.lower() for err in res.errors)


def test_tamper_detection_broken_chain_linkage():
    """Verify broken prev_hash linkage across blocks is detected."""
    from bulk_downloader.signature_chains import create_audit_signer, create_provenance_chain

    signer = create_audit_signer("auditor-primary")
    chain = create_provenance_chain(signer=signer)
    chain.append_event("EVENT_1", {"v": 1}, signer)
    chain.append_event("EVENT_2", {"v": 2}, signer)

    # Break chain linkage on block 2
    chain.blocks[2].prev_hash = "f" * 64

    res = chain.verify_chain()
    assert res.valid is False
    assert any("LINKAGE_BROKEN" in err or "PREV_HASH_MISMATCH" in err for err in res.errors)


def test_serialization_and_multi_signer_chain():
    """Verify multi-signer provenance chains, serialization to JSON, and round-trip re-verification."""
    from bulk_downloader.signature_chains import (
        AuditVerifier,
        create_audit_signer,
        create_provenance_chain,
    )

    signer_worker = create_audit_signer("worker-seat")
    signer_reviewer = create_audit_signer("reviewer-seat")

    chain = create_provenance_chain(signer=signer_worker)
    chain.append_event("TASK_STARTED", {"job": 42}, signer_worker)
    chain.append_event("TASK_PATCHED", {"tree": "abc1234"}, signer_worker)
    chain.append_event("REVIEW_APPROVED", {"verdict": "PATCH"}, signer_reviewer)

    # Serialize to JSON
    raw_json = chain.to_json()
    assert "REVIEW_APPROVED" in raw_json

    # Multi-signer key registry
    verifiers = {
        "worker-seat": AuditVerifier("worker-seat", signer_worker.public_key_hex),
        "reviewer-seat": AuditVerifier("reviewer-seat", signer_reviewer.public_key_hex),
    }

    # Reconstitute into fresh chain
    restored = create_provenance_chain().from_json(raw_json)
    assert len(restored) == 4

    res = restored.verify_chain(verifiers=verifiers)
    assert res.valid is True
    assert res.block_count == 4
    assert len(res.errors) == 0


def test_log_module_audit_provenance_capability():
    """Verify bulk_downloader.log exposes get_audit_provenance_chain and records signed audit events."""
    from bulk_downloader import log

    has_audit_chain = hasattr(log, "get_audit_provenance_chain")
    assert has_audit_chain is True, "bulk_downloader.log must expose get_audit_provenance_chain for asymmetric signature audit provenance"

    chain = log.get_audit_provenance_chain("production-logger")
    assert len(chain) >= 1

    event = log.record_audit_provenance_event("CONFIG_MUTATION", {"key": "BD_CONCURRENCY", "val": 4})
    assert event.event_type == "CONFIG_MUTATION"
    assert event.signature is not None

    verification = chain.verify_chain()
    assert verification.valid is True



# ---------------------------------------------------------------------------
# E2 (bd-worker-A1-A, after correctness REFUTE R1/F1/F2 + pm-orphan ruling):
# deletion is detectable against a persisted head, appending never confers
# trust, the committed timestamp is the stored timestamp, and the chain has a
# real caller -- audit.audit_log() -- reached through /api/audit/recent.
# ---------------------------------------------------------------------------

def _chain_of(n):
    s = create_audit_signer("auditor")
    c = create_provenance_chain(signer=s)
    for i in range(n):
        c.append_event("DOWNLOAD", {"i": i}, s)
    return s, c


def test_r1_truncation_is_detected_against_the_persisted_head():
    s, c = _chain_of(5)
    head = c.head_hash
    assert c.verify_chain(expected_head=head).valid is True
    c.blocks = c.blocks[:-2]                       # attacker deletes the last two records
    res = c.verify_chain(expected_head=head)
    assert res.valid is False
    assert any(e.startswith("TRUNCATED_OR_DIVERGED") for e in res.errors), res.errors
    # Across the serialization boundary with the out-of-band trust anchor
    # (public key + head) -- the only path a real auditor takes.
    c2 = create_provenance_chain().from_json(c.to_json())
    c2.register_verifier(AuditVerifier("auditor", public_key_bytes=s.public_key_bytes))
    assert c2.verify_chain(expected_head=head).valid is False
    # Negative control: without the anchor the truncated chain still verifies
    # (documented limitation, which is why the head must be persisted).
    assert c2.verify_chain().valid is True


def test_f1_appending_does_not_make_a_signer_trusted():
    s, c = _chain_of(2)
    rogue = create_audit_signer("rogue")
    c.append_event("DOWNLOAD", {"note": "injected"}, rogue)
    res = c.verify_chain()
    assert res.valid is False
    assert any("SIGNER_KEY_MISSING" in e and "rogue" in e for e in res.errors), res.errors
    # Explicit registration is the only way in.
    c.register_verifier(AuditVerifier("rogue", public_key_bytes=rogue.public_key_bytes))
    assert c.verify_chain().valid is True


def test_f2_block_hash_commits_to_the_stored_timestamp_bytes():
    import math
    s, c = _chain_of(1)
    b = c.blocks[-1]
    # One ulp up or down (~2.4e-7 s here): at most one microsecond boundary
    # can lie between the three values, so one neighbour keeps the ".6f"
    # rendering the old header hashed while its repr -- the stored bytes -- differs.
    up, down = math.nextafter(b.timestamp, math.inf), math.nextafter(b.timestamp, -math.inf)
    nudged = up if f"{up:.6f}" == f"{b.timestamp:.6f}" else down
    assert f"{nudged:.6f}" == f"{b.timestamp:.6f}" and repr(nudged) != repr(b.timestamp)
    b.timestamp = nudged
    res = c.verify_chain()
    assert res.valid is False
    assert any(e.startswith("BLOCK_HASH_MISMATCH") for e in res.errors), res.errors


def test_positive_control_audit_log_writes_a_row():
    """Passes at base and on the cut: proves the audit harness below can say yes."""
    from bulk_downloader import audit
    assert isinstance(audit.audit_log("test", "row1002-control", "target:x"), int)


def test_caller_audit_log_mirrors_into_the_signed_chain():
    from bulk_downloader import audit, log
    chain = log.get_audit_provenance_chain()
    before = len(chain)
    head_before = chain.head_hash
    row_id = audit.audit_log("api", "update", "sites_config:row1002", actor="tester")
    assert isinstance(row_id, int)
    assert len(chain) == before + 1
    blk = chain.blocks[-1]
    assert blk.event_type == "AUDIT:update"
    assert blk.payload == {"id": row_id, "source": "api",
                           "target": "sites_config:row1002", "actor": "tester"}
    assert blk.prev_hash == head_before
    assert chain.verify_chain(expected_head=chain.head_hash).valid is True


def test_caller_api_audit_recent_reports_the_chain_head():
    from flask import Flask
    from bulk_downloader import app_audit, audit, log
    audit.audit_log("api", "update", "sites_config:row1002-b")
    app = Flask("row1002")
    app.register_blueprint(app_audit.audit_bp)
    r = app.test_client().get("/api/audit/recent?limit=5")
    assert r.status_code == 200
    body = r.get_json()
    assert body["ok"] is True and body["count"] >= 1
    chain = body.get("chain")
    assert chain is not None, "row1002: /api/audit/recent carries no signed-chain head"
    assert chain["valid"] is True and chain["errors"] == []
    assert chain["head_hash"] == log.get_audit_provenance_chain().head_hash
    assert chain["block_count"] == len(log.get_audit_provenance_chain())


# ---------------------------------------------------------------- r2 REFUTE (B2): persistence across restart

def _store():
    """The on-disk (key, chain, head) paths, or the row's RED: the E2
    generation kept the chain in process memory only, so tampering between
    restarts was undetectable (r2 REFUTE H1)."""
    from bulk_downloader import log
    paths = getattr(log, "audit_chain_paths", None)
    if paths is None:
        pytest.fail("row1002: the signed audit chain persists nothing -- no key, "
                    "chain or head anchor on disk; tampering across a restart is undetectable")
    return paths()


def _restart():
    """Forget the in-process chain the way a process exit does; the next
    get_audit_provenance_chain() must come back from disk."""
    from bulk_downloader import log
    log._GLOBAL_AUDIT_CHAIN = log._GLOBAL_AUDIT_SIGNER = None
    log._GLOBAL_AUDIT_STATE = None


def test_h2_zero_block_chain_is_invalid_not_valid():
    from bulk_downloader.signature_chains import SignatureProvenanceChain
    res = SignatureProvenanceChain().verify_chain()
    assert res.valid is False and res.block_count == 0
    assert any(e.startswith("NO_GENESIS") for e in res.errors), res.errors


def test_h1_chain_key_and_anchor_persist_across_restart():
    from bulk_downloader import audit, log
    row_id = audit.audit_log("api", "update", "sites_config:row1002-persist")
    chain = log.get_audit_provenance_chain()
    head, n, pub = chain.head_hash, len(chain), log._GLOBAL_AUDIT_SIGNER.public_key_bytes
    key_path, chain_path, head_path = _store()
    assert key_path.exists() and chain_path.exists(), "row1002: nothing persisted to disk"
    assert oct(key_path.stat().st_mode & 0o777) == "0o600"
    assert head_path.read_text().strip() == head
    _restart()
    again = log.get_audit_provenance_chain()
    assert again is not chain
    assert len(again) == n and again.head_hash == head
    assert again.blocks[-1].payload["id"] == row_id
    assert log._GLOBAL_AUDIT_SIGNER.public_key_bytes == pub, "row1002: signer key not reloaded"
    st = audit.audit_chain_status()
    assert st["valid"] is True and st["head_hash"] == head == st["anchor"], st
    # appends after the restart still verify against the reloaded signer
    audit.audit_log("api", "update", "sites_config:row1002-after")
    assert audit.audit_chain_status()["valid"] is True


def test_h1_truncated_chain_file_is_detected_after_restart():
    from bulk_downloader import audit, log
    for i in range(3):
        audit.audit_log("api", "update", f"sites_config:row1002-t{i}")
    _key, chain_path, _head = _store()
    lines = chain_path.read_text().splitlines()
    assert len(lines) >= 4
    chain_path.write_text("\n".join(lines[:-1]) + "\n")   # drop the newest block on disk
    _restart()
    st = audit.audit_chain_status()
    assert st["valid"] is False, st
    assert any(e.startswith("TRUNCATED_OR_DIVERGED") for e in st["errors"]), st["errors"]


def test_h1_rewritten_block_is_detected_after_restart():
    from bulk_downloader import audit, log
    audit.audit_log("api", "update", "sites_config:row1002-x")
    audit.audit_log("api", "update", "sites_config:row1002-y")
    _key, chain_path, _head = _store()
    lines = chain_path.read_text().splitlines()
    rec = json.loads(lines[-2])
    rec["payload"]["target"] = "sites_config:forged"
    lines[-2] = json.dumps(rec, sort_keys=True)
    chain_path.write_text("\n".join(lines) + "\n")
    _restart()
    st = audit.audit_chain_status()
    assert st["valid"] is False, st
    assert st["errors"], "row1002: forged block on disk read as valid"


def test_h1_negative_control_untouched_files_verify_after_restart():
    from bulk_downloader import audit, log
    audit.audit_log("api", "update", "sites_config:row1002-nc")
    _restart()
    st = audit.audit_chain_status()
    assert st["valid"] is True and st["errors"] == [], st
    assert st["block_count"] == len(log.get_audit_provenance_chain())


def test_m4_audit_chain_status_fails_closed_when_the_chain_cannot_load(monkeypatch):
    from bulk_downloader import audit, log

    def boom():
        raise RuntimeError("row1002: chain store unreadable")
    monkeypatch.setattr(log, "get_audit_provenance_chain", boom)
    st = audit.audit_chain_status()
    assert st["valid"] is False and st["block_count"] == 0 and st["head_hash"] == ""
    assert any("unreadable" in e for e in st["errors"]), st


# ---------------------------------------------------------------- r3 REFUTE (B7-B): whole-store wipe

def _wipe_store():
    for p in _store():
        if p.exists():
            p.unlink()


def test_r3_wiping_the_whole_store_is_not_a_clean_bill():
    """B7-B: delete key + chain + anchor, restart: a fresh genesis must not
    verify as a plain valid=True -- the history DB still remembers the head."""
    from bulk_downloader import audit, log
    for i in range(4):
        audit.audit_log("api", "update", f"sites_config:row1002-w{i}", actor="alice")
    before = audit.audit_chain_status()
    assert before["valid"] is True and before["block_count"] == 5   # BASELINE (B7-B)
    _wipe_store()
    _restart()
    st = audit.audit_chain_status()
    assert st["block_count"] == 1                      # fresh genesis was minted...
    assert not (st["valid"] is True and st.get("state") in (None, "VERIFIED")), (
        f"row1002: 4 audited actions wiped with the store and the verifier answers {st}")
    assert st["valid"] is False and st["state"] == "MISMATCH", st
    assert any(e.startswith("ANCHOR_MISMATCH") for e in st["errors"]), st["errors"]
    assert before.get("state") == "VERIFIED", before


def test_r3_first_run_reports_new_chain_not_a_bare_valid():
    from bulk_downloader import audit
    st = audit.audit_chain_status()
    assert st["block_count"] == 1 and st["valid"] is True
    assert st.get("state") == "NEW_CHAIN", f"row1002: first run indistinguishable from a wipe: {st}"


def test_r3_negative_control_db_anchor_follows_every_append():
    from bulk_downloader import audit, log
    audit.audit_log("api", "update", "sites_config:row1002-db")
    chain = log.get_audit_provenance_chain()
    assert log.audit_chain_db_anchor() == (chain.head_hash, len(chain))
    _restart()
    st = audit.audit_chain_status()
    assert st["valid"] is True and st["state"] == "VERIFIED", st


# ---------------------------------------------------------------- r4 REFUTE (N4-B): fail-open cross-check

def test_r4_full_wipe_including_db_anchor_is_not_new_chain():
    """N4-B P1: wipe chain files AND delete the DB anchor row; audit_log rows
    survive. A genesis-only chain with orphaned audit rows is not a clean first
    run."""
    from bulk_downloader import audit, log
    from bulk_downloader.db import db_conn
    for i in range(4):
        audit.audit_log("api", "update", f"sites_config:row1002-p1-{i}", actor="alice")
    before = audit.audit_chain_status()
    assert before["valid"] is True and before["block_count"] == 5
    _wipe_store()
    with db_conn() as cx:
        cx.execute("DELETE FROM audit_chain_anchor")
    _restart()
    st = audit.audit_chain_status()
    assert st["valid"] is not True or st.get("state") not in (None, "VERIFIED", "NEW_CHAIN"), (
        f"row1002 P1: full wipe + DB anchor delete accepted as {st['state']} "
        f"with 4 audit_log rows orphaned: {st}")


def test_r4_audit_log_deletion_is_detected():
    """N4-B P2: delete all audit_log rows; chain blocks still reference them.
    The verifier must not answer VERIFIED."""
    from bulk_downloader import audit
    from bulk_downloader.db import db_conn
    for i in range(4):
        audit.audit_log("api", "update", f"sites_config:row1002-p2-{i}", actor="bob")
    before = audit.audit_chain_status()
    assert before["valid"] is True and before["state"] == "VERIFIED"
    with db_conn() as cx:
        cx.execute("DELETE FROM audit_log")
    st = audit.audit_chain_status()
    assert st["valid"] is not True or st.get("state") != "VERIFIED", (
        f"row1002 P2: all audit_log rows deleted but verifier answers {st}")


def test_r4_silent_mirror_outage_is_detected():
    """N4-B P3: the chain file is unwritable for some audit actions (the
    try/except swallows); after restart the verifier must not answer VERIFIED
    when blocks < audit rows."""
    import os
    from bulk_downloader import audit, log
    from bulk_downloader.db import db_conn
    audit.audit_log("api", "update", "sites_config:row1002-p3-baseline", actor="carol")
    before = audit.audit_chain_status()
    assert before["valid"] is True and before["block_count"] == 2
    key_path, chain_path, head_path = _store()
    old_mode = os.stat(chain_path).st_mode
    os.chmod(chain_path, 0o444)
    try:
        for i in range(3):
            audit.audit_log("api", "update", f"sites_config:row1002-p3-{i}", actor="carol")
    finally:
        os.chmod(chain_path, old_mode)
    _restart()
    st = audit.audit_chain_status()
    with db_conn() as cx:
        audit_rows = cx.execute("SELECT COUNT(*) FROM audit_log").fetchone()[0]
    assert audit_rows == 4, f"expected 4 audit rows, got {audit_rows}"
    assert st["block_count"] == 2, f"expected 2 blocks (1 survived + genesis), got {st['block_count']}"
    assert st["valid"] is not True or st.get("state") != "VERIFIED", (
        f"row1002 P3: {st['block_count']} blocks for {audit_rows} audit rows but verifier answers {st}")


def test_r4_negative_control_matching_counts_still_verify():
    """Positive control: when audit blocks == audit rows, VERIFIED holds."""
    from bulk_downloader import audit
    for i in range(3):
        audit.audit_log("api", "update", f"sites_config:row1002-neg-{i}", actor="dave")
    st = audit.audit_chain_status()
    assert st["valid"] is True and st["state"] == "VERIFIED", (
        f"row1002 neg: normal operation should verify: {st}")

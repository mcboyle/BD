"""Row 1002 ADVERSARY RED test (D1, bd-worker-W3-B).

N8-B finding A (HIGH): audit_chain_status returns VERIFIED + valid=True when the
DB query for audit_log count fails on a genesis-only chain.  The except at
audit.py:189 conflates "DB unreachable" with "0 rows", and 0 == 0 skips the
GAPPED branch.

This test reproduces the exact path: genesis-only chain with a valid DB anchor,
then DB unavailable for the audit_log count query.  Three-state (O1224): the
result must be valid=False with a non-VERIFIED state (e.g. UNVERIFIABLE).

The fixer (bd-worker-W1-A) must make this test pass without weakening it.
"""
from __future__ import annotations

import pytest

BD_GATE_SCOPE = "module"

try:
    from bulk_downloader import audit
except ImportError:
    audit = None


def test_adversary_genesis_only_db_unreachable_is_not_verified(monkeypatch, tmp_path):
    """A genesis-only chain whose DB audit_log count query fails must NOT
    report VERIFIED / valid=True.  Three-state: the result must indicate the
    check could not complete (O1224)."""
    assert audit is not None, "bulk_downloader.audit missing"

    from bulk_downloader import log
    from bulk_downloader.db import db_conn

    log._GLOBAL_AUDIT_CHAIN = log._GLOBAL_AUDIT_SIGNER = None
    log._GLOBAL_AUDIT_STATE = None

    monkeypatch.setenv("BD_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(log, "_GLOBAL_AUDIT_CHAIN", None)
    monkeypatch.setattr(log, "_GLOBAL_AUDIT_SIGNER", None)
    monkeypatch.setattr(log, "_GLOBAL_AUDIT_STATE", None)

    chain = log.get_audit_provenance_chain()
    assert chain.blocks[0].event_type == "GENESIS"
    audit.audit_log("api", "update", "sites_config:adversary-setup", actor="adversary")
    chain = log.get_audit_provenance_chain()
    audit_blocks = sum(1 for b in chain.blocks if b.event_type.startswith("AUDIT:"))
    assert audit_blocks == 1, f"expected 1 AUDIT block, got {audit_blocks}"

    anchor = log.audit_chain_anchor()
    db_anchor = log.audit_chain_db_anchor()
    assert db_anchor is not None, "DB anchor must be mirrored for VERIFIED path"
    assert db_anchor == (chain.head_hash, len(chain.blocks))

    before = audit.audit_chain_status()
    assert before["state"] == "VERIFIED" and before["valid"] is True, (
        f"precondition: chain must verify before DB break: {before}")

    orig_ensure = audit._ensure_schema

    def _broken_ensure():
        with db_conn() as cx:
            cx.execute("DROP TABLE IF EXISTS audit_log")

    monkeypatch.setattr(audit, "_ensure_schema", _broken_ensure)

    st = audit.audit_chain_status()
    assert st["valid"] is not True, (
        f"row1002 ADVERSARY: DB unreachable for audit_log count but verifier "
        f"answers valid=True state={st.get('state')!r}. A check that cannot "
        f"complete must not report success (O1224 three-state).")
    assert st.get("state") not in (None, "VERIFIED", "NEW_CHAIN"), (
        f"row1002 ADVERSARY: state must reflect incomplete verification, "
        f"got {st.get('state')!r}")

"""METERING-M1: Multi-host cross-dedup view, AGY token telemetry, and host-coordination flocking.

Harness candidate test (bd-harness-cut shape): opt in with
BD_METERING_M1_CANDIDATE=/home/mboyle/bd-persist/harness-work/FIX/metering-m1-bd-agy-worker-2/bd_cost_accounting.py
"""

from __future__ import annotations

import importlib.util
import json
import os
import sqlite3
import sys
import types
from pathlib import Path

import pytest

BD_GATE_SCOPE = "module"
CANDIDATE = os.environ.get("BD_METERING_M1_CANDIDATE", "")
pytestmark = pytest.mark.skipif(not CANDIDATE, reason="candidate opt-in required")


def _load_module(path_str: str) -> types.ModuleType:
    p = Path(path_str)
    assert p.is_file(), f"candidate missing: {path_str}"
    spec = importlib.util.spec_from_file_location("candidate_accounting", str(p))
    assert spec is not None and spec.loader is not None, f"cannot load {path_str}"
    mod = importlib.util.module_from_spec(spec)
    sys.modules["candidate_accounting"] = mod
    spec.loader.exec_module(mod)
    return mod


def test_001_candidate_exists_and_is_valid() -> None:
    p = Path(CANDIDATE)
    assert p.is_file(), f"candidate missing: {CANDIDATE}"
    first_line = p.read_text(encoding="utf-8").splitlines()[0]
    assert first_line == "#!/usr/bin/env python3"


def test_002_agy_normalize_meters_cached_tokens() -> None:
    """AGY provider in normalize() must meter cached_input_tokens and cache_read_input_tokens."""
    mod = _load_module(CANDIDATE)
    data = {
        "input_tokens": 50000,
        "output_tokens": 120,
        "reasoning_output_tokens": 0,
        "cached_input_tokens": 42000,
    }
    result = mod.normalize(data, "agy")
    assert result["input_tokens"] == 50000
    assert result["output_tokens"] == 120
    assert result["cached_input_tokens"] == 42000
    assert result["cache_read_input_tokens"] == 42000
    assert result["total_tokens"] == 50120


def test_003_agy_normalize_cached_content_token_count_fallback() -> None:
    """normalize() handles cachedContentTokenCount key when cached_input_tokens is omitted."""
    mod = _load_module(CANDIDATE)
    data = {
        "input_tokens": 30000,
        "output_tokens": 50,
        "cachedContentTokenCount": 25000,
    }
    result = mod.normalize(data, "agy")
    assert result["input_tokens"] == 30000
    assert result["cached_input_tokens"] == 25000
    assert result["cache_read_input_tokens"] == 25000


def test_004_agy_parse_extracts_usage_metadata() -> None:
    """parse() extracts cachedContentTokenCount from usageMetadata for agy provider."""
    mod = _load_module(CANDIDATE)
    record = {
        "type": "PLANNER_RESPONSE",
        "created_at": "2026-09-28T08:00:00.000000Z",
        "step_index": 4,
        "usageMetadata": {
            "promptTokenCount": 35000,
            "candidatesTokenCount": 80,
            "cachedContentTokenCount": 28000,
        },
    }
    metadata: dict[str, str] = {"thread": "test-thread-001"}
    parsed = mod.parse(record, metadata, "agy")
    assert parsed is not None
    response_id, thread, timestamp, model, value = parsed
    assert response_id == "test-thread-001:4"
    assert thread == "test-thread-001"
    assert timestamp == "2026-09-28T08:00:00.000000Z"
    assert model == "agy-metered"
    assert value["input_tokens"] == 35000
    assert value["output_tokens"] == 80
    assert value["cached_input_tokens"] == 28000
    assert value["cache_read_input_tokens"] == 28000


def test_005_v_usage_distinct_view_deduplicates_multi_host_occurrences(
    tmp_path: Path,
) -> None:
    """v_usage_distinct view must deduplicate multi-host occurrences for identical responses."""
    mod = _load_module(CANDIDATE)
    db_path = tmp_path / "test_usage.sqlite"
    db = mod.connect(str(db_path))

    # Insert one response
    response_id = "test-resp-multihost-001"
    usage_data = {
        "input_tokens": 10000,
        "output_tokens": 200,
        "cached_input_tokens": 8000,
        "cache_read_input_tokens": 8000,
        "total_tokens": 10200,
    }
    encoded = json.dumps(usage_data, sort_keys=True)
    db.execute(
        "INSERT INTO usage_responses (provider, response_id, timestamp, model, usage, fingerprint, quarantined) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            "codex",
            response_id,
            "2026-09-28T05:00:00.000000Z",
            "gpt-6-astra",
            encoded,
            encoded,
            0,
        ),
    )

    # Insert occurrences from two distinct hosts (e.g. test5 and hub-mesh01)
    db.execute(
        "INSERT INTO usage_occurrences (provider, response_id, host, thread, source) VALUES (?, ?, ?, ?, ?)",
        ("codex", response_id, "test5", "thread-1", "/path/a.jsonl"),
    )
    db.execute(
        "INSERT INTO usage_occurrences (provider, response_id, host, thread, source) VALUES (?, ?, ?, ?, ?)",
        ("codex", response_id, "hub-mesh01", "thread-1", "/path/b.jsonl"),
    )

    # Also insert a quarantined response (R1: must be filtered out by v_usage_distinct)
    q_resp_id = "test-resp-quarantined-002"
    db.execute(
        "INSERT INTO usage_responses (provider, response_id, timestamp, model, usage, fingerprint, quarantined) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            "codex",
            q_resp_id,
            "2026-09-28T05:05:00.000000Z",
            "gpt-6-astra",
            encoded,
            encoded,
            1,
        ),
    )
    db.execute(
        "INSERT INTO usage_occurrences (provider, response_id, host, thread, source) VALUES (?, ?, ?, ?, ?)",
        ("codex", q_resp_id, "test5", "thread-1", "/path/q.jsonl"),
    )
    db.commit()

    # Naive join: 3 rows (2 for resp1, 1 for q_resp)
    naive_rows = db.execute(
        "SELECT count(*), sum(cast(json_extract(r.usage, '$.total_tokens') as int)) "
        "FROM usage_responses r JOIN usage_occurrences o USING(provider, response_id)"
    ).fetchone()
    assert naive_rows[0] == 3
    assert naive_rows[1] == 30600

    # Deduplicated view: exactly 1 row (deduped across hosts AND quarantined response excluded per R1)
    distinct_rows = db.execute(
        "SELECT count(*), sum(cast(json_extract(usage, '$.total_tokens') as int)), "
        "min(host), min(source) FROM v_usage_distinct"
    ).fetchone()
    assert distinct_rows[0] == 1
    assert distinct_rows[1] == 10200  # Exactly 1x tokens, 0 quarantined
    assert distinct_rows[2] == "hub-mesh01"  # min(host)
    assert distinct_rows[3] == "/path/a.jsonl"  # min(source)

    # Assert quarantined response is absent from v_usage_distinct
    q_check = db.execute(
        "SELECT 1 FROM v_usage_distinct WHERE response_id = ?", (q_resp_id,)
    ).fetchone()
    assert q_check is None


def test_006_collect_host_coordination_and_agy_ingest(tmp_path: Path) -> None:
    """collect() locks db with host-coordination flock and ingests agy transcript with telemetry."""
    mod = _load_module(CANDIDATE)
    db_path = tmp_path / "collect_usage.sqlite"

    # Create mock AGY transcript directory
    agy_root = tmp_path / "mock_agy"
    transcript_dir = (
        agy_root / "brain" / "mock-session-1" / ".system_generated" / "logs"
    )
    transcript_dir.mkdir(parents=True)
    transcript_file = transcript_dir / "transcript_full.jsonl"

    record_line = (
        json.dumps(
            {
                "type": "PLANNER_RESPONSE",
                "created_at": "2026-09-28T06:00:00.000000Z",
                "step_index": 1,
                "usageMetadata": {
                    "promptTokenCount": 20000,
                    "candidatesTokenCount": 100,
                    "cachedContentTokenCount": 16000,
                },
            }
        )
        + "\n"
    )
    transcript_file.write_text(record_line, encoding="utf-8")

    args = types.SimpleNamespace(
        db=str(db_path),
        host="hub-mesh01",
        from_now=False,
        codex_root=[],
        claude_root=[],
        grok_root=[],
        kimi_root=[],
        agy_root=[str(agy_root)],
        codex_state_db=None,
    )

    receipt = mod.collect(args)
    assert receipt["status"] == "FOUND"
    assert receipt["new_responses"] == 1

    # Verify lock file was created
    lock_file = Path(str(db_path) + ".lock")
    assert lock_file.is_file()

    # Query usage_responses
    db = sqlite3.connect(str(db_path))
    db.row_factory = sqlite3.Row
    row = db.execute("SELECT * FROM usage_responses WHERE provider='agy'").fetchone()
    assert row is not None
    assert row["response_id"] == "mock-session-1:1"
    usage = json.loads(row["usage"])
    assert usage["input_tokens"] == 20000
    assert usage["output_tokens"] == 100
    assert usage["cached_input_tokens"] == 16000
    assert usage["cache_read_input_tokens"] == 16000
    assert usage["total_tokens"] == 20100


def test_007_collect_lock_contention_non_blocking_skip(tmp_path: Path) -> None:
    """Note N1: When another process holds lock, collect() immediately skips without blocking."""
    import fcntl

    mod = _load_module(CANDIDATE)
    db_path = tmp_path / "contended.sqlite"
    lock_path = Path(str(db_path) + ".lock")

    args = types.SimpleNamespace(
        db=str(db_path),
        host="hub-mesh01",
        from_now=False,
        codex_root=[],
        claude_root=[],
        grok_root=[],
        kimi_root=[],
        agy_root=[],
        codex_state_db=None,
    )

    # Hold lock with open file
    with open(lock_path, "a") as f:
        fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        receipt = mod.collect(args)
        assert receipt["status"] == "SKIPPED-LOCKED"
        assert receipt["reason"] == "another collector holds lock"
        fcntl.flock(f.fileno(), fcntl.LOCK_UN)


def test_008_collect_auto_discovers_default_agy_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """R2: collect() auto-discovers default ~/.gemini/antigravity-cli when agy_root is empty."""
    mod = _load_module(CANDIDATE)
    fake_home = tmp_path / "home"
    default_agy = fake_home / ".gemini" / "antigravity-cli"
    transcript_dir = (
        default_agy / "brain" / "session-001" / ".system_generated" / "logs"
    )
    transcript_dir.mkdir(parents=True)
    transcript_file = transcript_dir / "transcript_full.jsonl"

    record_line = (
        json.dumps(
            {
                "type": "PLANNER_RESPONSE",
                "created_at": "2026-09-28T07:00:00.000000Z",
                "step_index": 2,
                "usageMetadata": {
                    "promptTokenCount": 15000,
                    "candidatesTokenCount": 50,
                    "cachedContentTokenCount": 12000,
                },
            }
        )
        + "\n"
    )
    transcript_file.write_text(record_line, encoding="utf-8")

    monkeypatch.setattr(Path, "home", staticmethod(lambda: fake_home))
    db_path = tmp_path / "default_agy.sqlite"

    args = types.SimpleNamespace(
        db=str(db_path),
        host="hub-mesh01",
        from_now=False,
        codex_root=[],
        claude_root=[],
        grok_root=[],
        kimi_root=[],
        agy_root=[],  # Empty, as passed by live crontab
        codex_state_db=None,
    )

    receipt = mod.collect(args)
    assert receipt["status"] == "FOUND"
    assert receipt["new_responses"] == 1

    db = sqlite3.connect(str(db_path))
    row = db.execute(
        "SELECT response_id FROM usage_responses WHERE provider='agy'"
    ).fetchone()
    assert row is not None
    assert row[0] == "session-001:2"


def test_009_missing_usage_metadata_registers_as_malformed_not_zero_tokens(
    tmp_path: Path,
) -> None:
    """R1: Missing usageMetadata must raise ValueError and register as malformed, not zero tokens."""
    mod = _load_module(CANDIDATE)
    db_path = tmp_path / "missing_usage.sqlite"

    # parse() must raise ValueError if usageMetadata is missing
    record_no_usage = {
        "type": "PLANNER_RESPONSE",
        "created_at": "2026-09-28T09:00:00.000000Z",
        "step_index": 1,
    }
    with pytest.raises(ValueError, match="missing usageMetadata"):
        mod.parse(record_no_usage, {"thread": "test-thread"}, "agy")

    # collect() on transcript without usageMetadata must record malformed diagnostic, not 0-token response
    agy_root = tmp_path / "mock_agy"
    transcript_dir = (
        agy_root / "brain" / "no-usage-session" / ".system_generated" / "logs"
    )
    transcript_dir.mkdir(parents=True)
    transcript_file = transcript_dir / "transcript_full.jsonl"
    transcript_file.write_text(json.dumps(record_no_usage) + "\n", encoding="utf-8")

    args = types.SimpleNamespace(
        db=str(db_path),
        host="hub-mesh01",
        from_now=False,
        codex_root=[],
        claude_root=[],
        grok_root=[],
        kimi_root=[],
        agy_root=[str(agy_root)],
        codex_state_db=None,
    )

    receipt = mod.collect(args)
    assert receipt["new_responses"] == 0
    assert receipt["malformed_or_missing_usage"] == 1
    assert receipt["status"] == "COULD NOT LOOK"

    # Verify zero responses inserted into database
    db = sqlite3.connect(str(db_path))
    count = db.execute(
        "SELECT count(*) FROM usage_responses WHERE provider='agy'"
    ).fetchone()[0]
    assert count == 0


def test_010_agy_multi_session_step_identity_no_collision(
    tmp_path: Path,
) -> None:
    """R2: Steps with same step_index across different sessions do not collide."""
    mod = _load_module(CANDIDATE)
    db_path = tmp_path / "multi_session.sqlite"

    agy_root = tmp_path / "mock_agy"
    for session_id in ("session-alpha", "session-beta"):
        d = agy_root / "brain" / session_id / ".system_generated" / "logs"
        d.mkdir(parents=True)
        rec = {
            "type": "PLANNER_RESPONSE",
            "created_at": "2026-09-28T09:00:00.000000Z",
            "step_index": 1,
            "usageMetadata": {
                "promptTokenCount": 1000,
                "candidatesTokenCount": 50,
                "cachedContentTokenCount": 800,
            },
        }
        (d / "transcript_full.jsonl").write_text(
            json.dumps(rec) + "\n", encoding="utf-8"
        )

    args = types.SimpleNamespace(
        db=str(db_path),
        host="hub-mesh01",
        from_now=False,
        codex_root=[],
        claude_root=[],
        grok_root=[],
        kimi_root=[],
        agy_root=[str(agy_root)],
        codex_state_db=None,
    )

    receipt = mod.collect(args)
    assert receipt["status"] == "FOUND"
    assert receipt["new_responses"] == 2
    assert receipt["duplicates"] == 0

    db = sqlite3.connect(str(db_path))
    ids = [
        r[0]
        for r in db.execute(
            "SELECT response_id FROM usage_responses WHERE provider='agy' ORDER BY response_id"
        ).fetchall()
    ]
    assert ids == ["session-alpha:1", "session-beta:1"]


def test_011_agy_deduplicates_transcript_chunks_and_copies(
    tmp_path: Path,
) -> None:
    """R2: collect() skips redundant transcript.jsonl and chunks/ copies within same session."""
    mod = _load_module(CANDIDATE)
    db_path = tmp_path / "dedup_copies.sqlite"

    agy_root = tmp_path / "mock_agy"
    logs_dir = agy_root / "brain" / "session-gamma" / ".system_generated" / "logs"
    chunks_dir = logs_dir / "chunks" / "transcript_full"
    chunks_dir.mkdir(parents=True)

    rec = {
        "type": "PLANNER_RESPONSE",
        "created_at": "2026-09-28T09:00:00.000000Z",
        "step_index": 1,
        "usageMetadata": {
            "promptTokenCount": 2000,
            "candidatesTokenCount": 40,
            "cachedContentTokenCount": 1500,
        },
    }
    line = json.dumps(rec) + "\n"
    # Write transcript_full.jsonl, duplicate transcript.jsonl, and chunk copy
    (logs_dir / "transcript_full.jsonl").write_text(line, encoding="utf-8")
    (logs_dir / "transcript.jsonl").write_text(line, encoding="utf-8")
    (chunks_dir / "00000000.jsonl").write_text(line, encoding="utf-8")

    args = types.SimpleNamespace(
        db=str(db_path),
        host="hub-mesh01",
        from_now=False,
        codex_root=[],
        claude_root=[],
        grok_root=[],
        kimi_root=[],
        agy_root=[str(agy_root)],
        codex_state_db=None,
    )

    receipt = mod.collect(args)
    assert receipt["status"] == "FOUND"
    # Only 1 file ingested (transcript_full.jsonl), transcript.jsonl and chunk skipped
    assert receipt["files"] == 1
    assert receipt["new_responses"] == 1
    assert receipt["duplicates"] == 0


def test_012_v_usage_distinct_consumers_report_and_audit(
    tmp_path: Path,
) -> None:
    """R3: report() and audit() read from v_usage_distinct view for deduplicated responses."""
    mod = _load_module(CANDIDATE)
    db_path = tmp_path / "consumer_test.sqlite"
    db = mod.connect(str(db_path))

    # Insert 1 distinct response with 2 occurrences across hosts
    resp1 = "resp-consumer-1"
    usage1 = mod.zeros() | {
        "input_tokens": 5000,
        "output_tokens": 100,
        "cached_input_tokens": 4000,
        "cache_read_input_tokens": 4000,
        "total_tokens": 5100,
    }
    enc1 = json.dumps(usage1, sort_keys=True)
    db.execute(
        "INSERT INTO usage_responses (provider, response_id, timestamp, model, usage, fingerprint, quarantined) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        ("codex", resp1, "2026-09-28T09:00:00.000000Z", "gpt-6-astra", enc1, enc1, 0),
    )
    db.execute(
        "INSERT INTO usage_occurrences (provider, response_id, host, thread, source) VALUES (?, ?, ?, ?, ?)",
        ("codex", resp1, "host-a", "thread-1", "/path/1.jsonl"),
    )
    db.execute(
        "INSERT INTO usage_occurrences (provider, response_id, host, thread, source) VALUES (?, ?, ?, ?, ?)",
        ("codex", resp1, "host-b", "thread-1", "/path/2.jsonl"),
    )

    # Insert 1 quarantined response with occurrence
    resp_q = "resp-consumer-q"
    db.execute(
        "INSERT INTO usage_responses (provider, response_id, timestamp, model, usage, fingerprint, quarantined) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        ("codex", resp_q, "2026-09-28T09:01:00.000000Z", "gpt-6-astra", enc1, enc1, 1),
    )
    db.execute(
        "INSERT INTO usage_occurrences (provider, response_id, host, thread, source) VALUES (?, ?, ?, ?, ?)",
        ("codex", resp_q, "host-a", "thread-1", "/path/q.jsonl"),
    )

    # Assign cut
    db.execute(
        "INSERT INTO usage_assignments VALUES (?, ?, ?, ?, ?, ?, NULL, NULL)",
        (
            "host-a",
            "thread-1",
            "test-cut",
            "fix",
            "worker-1",
            "2026-09-28T08:00:00.000000Z",
        ),
    )
    receipt_meta = json.dumps({"status": "FOUND", "host": "host-a"})
    db.execute("INSERT INTO usage_collection VALUES (?, ?)", ("host-a", receipt_meta))
    db.commit()

    # Test audit() reads v_usage_distinct
    audit_args = types.SimpleNamespace(db=str(db_path), provider=None)
    audit_result = mod.audit(audit_args)
    assert audit_result["distinct_responses"] == 1
    assert audit_result["responses"][0]["response_id"] == resp1

    # Test report() reads v_usage_distinct
    report_args = types.SimpleNamespace(db=str(db_path), cut="test-cut", out=None)
    rep = mod.report(report_args)
    assert rep["coverage"]["distinct_response_population"] == 1
    assert rep["coverage"]["quarantined_conflicts"] == 1
    assert rep["request_count"] == 1
    assert rep["attributed"]["total_tokens"] == 5100


def test_013_hermetic_collect_does_not_autodiscover_host_home_in_tests(
    tmp_path: Path,
) -> None:
    """Note N1: When agy_root is empty and not on host cron path, collect does not scan host home."""
    mod = _load_module(CANDIDATE)
    db_path = tmp_path / "hermetic.sqlite"

    args = types.SimpleNamespace(
        db=str(db_path),
        host="test-runner",
        from_now=False,
        codex_root=[],
        claude_root=[],
        grok_root=[],
        kimi_root=[],
        agy_root=[],
        codex_state_db=None,
    )

    receipt = mod.collect(args)
    assert receipt["status"] == "COULD NOT LOOK"
    assert receipt["files"] == 0
    assert len(receipt["roots"]) == 0

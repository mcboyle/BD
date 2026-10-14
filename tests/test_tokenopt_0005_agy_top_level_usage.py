"""TOKENOPT-0005 r4: the cost collector must meter the usage the AGY CLI really writes.

The AGY CLI's transcript records carry usage as TOP-LEVEL ``input_tokens`` /
``output_tokens`` / ``cache_read_tokens`` on ``PLANNER_RESPONSE`` records and no
``usageMetadata`` object (every ingested transcript, 2026-10-13: 71267 of 72292
PLANNER_RESPONSE records; ``GENERIC`` tool-step records carry no usage at all).
The landed parser required ``usageMetadata``, so a real session metered nothing:
0 responses, every record counted malformed, receipt status COULD NOT LOOK.

Top-level ``input_tokens`` EXCLUDES cache reads: ``cache_read_tokens`` is larger
than ``input_tokens`` on 67886 of those 71267 records. The prompt total is
input + cache_read (r3 treated the cache read as a subset of input and so
rejected 95% of real records as "cache exceeds input").

Harness candidate test (bd-harness-cut shape): opt in with
BD_TOKENOPT_0005_AGY_CANDIDATE=/home/mboyle/bd-persist/harness-work/FIX/tokenopt-0005-agy-top-level-usage-r4/bd_cost_accounting.py

The fixture below is the record SHAPE of one real session (keys, types, order,
token numbers) with the conversation text replaced. The candidate runs as a
subprocess against a temp database; no ledger outside tmp_path is opened.
"""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

BD_GATE_SCOPE = "module"
CANDIDATE = os.environ.get("BD_TOKENOPT_0005_AGY_CANDIDATE", "")
pytestmark = pytest.mark.skipif(not CANDIDATE, reason="candidate opt-in required (BD_TOKENOPT_0005_AGY_CANDIDATE)")

SESSION = "08a47609-fixture"
# (input_tokens, output_tokens) of the six PLANNER_RESPONSE records, steps 1,3,..,11.
REAL_USAGE = [(6240, 326), (6666, 191), (7072, 121), (7370, 109), (8131, 125), (12233, 232)]
# (input_tokens, cache_read_tokens, output_tokens) of six consecutive PLANNER_RESPONSE records of
# a long real session (d61a0f86, steps 1965-1975): a cache hit moves the prompt out of
# input_tokens into cache_read_tokens, and input + cache_read is the prompt size throughout.
REAL_CACHED_USAGE = [(104073, 8749, 581), (7821, 109103, 2005), (119541, 0, 456), (120950, 0, 569),
                     (5474, 117959, 1849), (3173, 122192, 1161)]


def _real_shape_records() -> list[dict]:
    recs: list[dict] = [{"step_index": 0, "source": "SYSTEM", "type": "SYSTEM_MESSAGE", "status": "DONE",
                         "created_at": "2026-10-13T05:05:59Z", "content": "fixture"}]
    step = 1
    for n, (inp, out) in enumerate(REAL_USAGE):
        recs.append({"step_index": step, "source": "MODEL", "type": "PLANNER_RESPONSE", "status": "DONE",
                     "created_at": f"2026-10-13T05:06:{n:02d}Z", "content": "fixture", "thinking": "fixture",
                     "tool_calls": [], "input_tokens": inp, "cache_read_tokens": 0, "output_tokens": out})
        step += 1
        if n < len(REAL_USAGE) - 1:  # a tool step follows every generation but the last
            recs.append({"step_index": step, "source": "TOOL", "type": "GENERIC", "status": "DONE",
                         "created_at": f"2026-10-13T05:06:{n:02d}Z", "content": "fixture", "truncated_fields": []})
            step += 1
    return recs


def _collect(tmp_path: Path, records: list[dict], name: str = "transcript_full.jsonl") -> tuple[dict, list[dict]]:
    logs = tmp_path / "agy" / "brain" / SESSION / ".system_generated" / "logs"
    logs.mkdir(parents=True)
    (logs / name).write_text("".join(json.dumps(r) + "\n" for r in records), encoding="utf-8")
    db = tmp_path / "usage.sqlite"
    run = subprocess.run(
        [sys.executable, "-B", CANDIDATE, "collect", "--db", str(db), "--host", "fixture-host",
         "--agy-root", str(tmp_path / "agy")],
        capture_output=True, text=True, timeout=120, cwd=tmp_path)
    assert run.returncode == 0, f"TOKENOPT5-COLLECT-RC: rc={run.returncode} stderr={run.stderr[-600:]}"
    con = sqlite3.connect(db)
    try:
        rows = [dict(response_id=r[0], model=r[1], usage=json.loads(r[2])) for r in con.execute(
            "SELECT response_id, model, usage FROM usage_responses WHERE provider='agy' ORDER BY timestamp, response_id")]
    finally:
        con.close()
    return json.loads(run.stdout), rows


def test_fixture_has_the_real_shape() -> None:
    recs = _real_shape_records()
    kinds = [r["type"] for r in recs]
    assert (kinds.count("PLANNER_RESPONSE"), kinds.count("GENERIC"), kinds.count("SYSTEM_MESSAGE")) == (6, 5, 1)
    assert not any("usageMetadata" in r for r in recs)
    assert all("input_tokens" not in r for r in recs if r["type"] != "PLANNER_RESPONSE")


def test_a_real_shape_session_is_metered(tmp_path: Path) -> None:
    receipt, rows = _collect(tmp_path, _real_shape_records())
    assert receipt["new_responses"] == 6, (
        f"TOKENOPT5-AGY-NOT-METERED: {receipt['new_responses']} of 6 real-shape PLANNER_RESPONSE records metered "
        f"(malformed={receipt['malformed_or_missing_usage']}, status={receipt['status']})")
    assert receipt["malformed_or_missing_usage"] == 0, (
        f"TOKENOPT5-TOOL-STEP-MALFORMED: {receipt['malformed_or_missing_usage']} records counted malformed; "
        "a GENERIC tool step has no usage to miss")
    assert receipt["status"] == "FOUND", f"TOKENOPT5-STATUS: {receipt['status']}"
    assert [(r["usage"]["input_tokens"], r["usage"]["output_tokens"]) for r in rows] == REAL_USAGE, rows
    assert sum(r["usage"]["input_tokens"] for r in rows) == 47712
    assert sum(r["usage"]["output_tokens"] for r in rows) == 1104
    assert {r["model"] for r in rows} == {"agy-metered"}
    assert [r["response_id"] for r in rows] == [f"{SESSION}:{i}" for i in (1, 3, 5, 7, 9, 11)]


def test_top_level_cache_read_tokens_are_metered(tmp_path: Path) -> None:
    recs = _real_shape_records()
    recs[1]["cache_read_tokens"] = 4000
    _receipt, rows = _collect(tmp_path, recs)
    assert rows and rows[0]["usage"]["cached_input_tokens"] == 4000, (
        f"TOKENOPT5-CACHE-READ-DROPPED: {rows[:1]}")
    assert rows[0]["usage"]["cache_read_input_tokens"] == 4000
    assert [r["usage"]["cached_input_tokens"] for r in rows[1:]] == [0] * 5
    assert rows[0]["usage"]["input_tokens"] == 6240 + 4000, (
        f"TOKENOPT5-CACHE-READ-NOT-ADDED: input_tokens={rows[0]['usage']['input_tokens']}, expected 10240")


def test_real_cache_reads_larger_than_input_are_metered_and_added(tmp_path: Path) -> None:
    """The r3 REFUTE: on real records the cache read exceeds input_tokens; it is disjoint, not a subset."""
    recs = _real_shape_records()
    planner = [r for r in recs if r["type"] == "PLANNER_RESPONSE"]
    for rec, (inp, read, out) in zip(planner, REAL_CACHED_USAGE, strict=True):
        rec.update(input_tokens=inp, cache_read_tokens=read, output_tokens=out)
    assert sum(read > inp for inp, read, _ in REAL_CACHED_USAGE) == 3
    receipt, rows = _collect(tmp_path, recs)
    assert receipt["malformed_or_missing_usage"] == 0 and receipt["new_responses"] == 6, (
        f"TOKENOPT5-CACHE-EXCEEDS-INPUT-REJECTED: {receipt['new_responses']} of 6 metered, "
        f"malformed={receipt['malformed_or_missing_usage']}, status={receipt['status']}")
    got = [(r["usage"]["input_tokens"], r["usage"]["cached_input_tokens"], r["usage"]["cache_read_input_tokens"],
            r["usage"]["output_tokens"], r["usage"]["total_tokens"]) for r in rows]
    want = [(inp + read, read, read, out, inp + read + out) for inp, read, out in REAL_CACHED_USAGE]
    assert got == want, f"TOKENOPT5-CACHE-READ-NOT-ADDED: got {got} want {want}"
    assert sum(g[0] for g in got) == 719035 and sum(g[1] for g in got) == 358003


def test_usage_metadata_shape_still_meters_and_wins(tmp_path: Path) -> None:
    """Control: the shape the landed parser read keeps working, and wins over top-level fields."""
    recs = [{"step_index": 1, "source": "MODEL", "type": "PLANNER_RESPONSE", "status": "DONE",
             "created_at": "2026-10-13T05:06:00Z", "input_tokens": 1, "output_tokens": 1,
             "usageMetadata": {"promptTokenCount": 900, "candidatesTokenCount": 70, "cachedContentTokenCount": 300}}]
    receipt, rows = _collect(tmp_path, recs)
    assert receipt["new_responses"] == 1 and receipt["malformed_or_missing_usage"] == 0, receipt
    u = rows[0]["usage"]
    assert (u["input_tokens"], u["output_tokens"], u["cached_input_tokens"]) == (900, 70, 300), (
        f"TOKENOPT5-METADATA-PATH-CHANGED: promptTokenCount is inclusive and wins: {u}")


def test_a_generation_with_no_usage_is_still_malformed(tmp_path: Path) -> None:
    """Negative control: the fix must not hide a PLANNER_RESPONSE that carries no usage in either form."""
    recs = _real_shape_records()
    for key in ("input_tokens", "output_tokens", "cache_read_tokens"):
        del recs[1][key]
    receipt, rows = _collect(tmp_path, recs)
    assert receipt["malformed_or_missing_usage"] == 1, (
        f"TOKENOPT5-MISSING-USAGE-HIDDEN: malformed={receipt['malformed_or_missing_usage']}, expected exactly 1")
    assert receipt["new_responses"] == 5 and receipt["status"] == "PARTIAL", receipt
    assert len(rows) == 5


def test_a_non_object_usage_metadata_is_malformed_not_bypassed(tmp_path: Path) -> None:
    recs = _real_shape_records()
    recs[1]["usageMetadata"] = "corrupt"
    receipt, rows = _collect(tmp_path, recs)
    assert receipt["malformed_or_missing_usage"] == 1 and len(rows) == 5, (
        f"TOKENOPT5-CORRUPT-METADATA-BYPASSED: {receipt}")


def test_bad_top_level_numbers_are_malformed(tmp_path: Path) -> None:
    recs = _real_shape_records()
    recs[1]["input_tokens"] = "6240"
    recs[3]["output_tokens"] = -1
    recs[5]["cache_read_tokens"] = 1.5
    recs[7]["cache_read_tokens"] = None
    receipt, rows = _collect(tmp_path, recs)
    assert receipt["malformed_or_missing_usage"] == 4 and len(rows) == 2, f"TOKENOPT5-BAD-NUMBER-ACCEPTED: {receipt}"

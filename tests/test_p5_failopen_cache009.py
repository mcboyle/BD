"""P5 fail-open: the CACHE-009 prefix-stability audit must fail CLOSED on every error path.

TRIAGE-FAIL-OPEN-PATTERN (bd-sentinel-A 12:15Z): CACHE-009 claude_prefix_audit.py G3 (manifest 7aac9f16) still passes on its
own error paths. rc 0 = FOUND NONE, rc 1 = FOUND n churn, rc 2 = COULD NOT LOOK. Measured on G3 (5744c7fe):
  - an unparseable --until is silently replaced by "now" (audits an unrequested window; FOUND NONE rc 0);
  - a corrupt usage row is read as zeros, which hides the next turn's miss -> FOUND NONE rc 0;
  - a cache_miss_reason type it does not know is neither churn nor unknown -> FOUND NONE rc 0;
  - any exception (missing table, non-dict reason) is a traceback with rc 1, the FOUND-churn exit code.
Every such input must give rc 2 with a COULD NOT LOOK verdict. Controls: a clean window stays FOUND NONE rc 0, a
system_changed miss stays FOUND 1 rc 1, and the script's own --induced-miss-control still passes.

Opt in with BD_P5_FAILOPEN_CACHE009_CANDIDATE=<path to claude_prefix_audit.py>.
"""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

BD_GATE_SCOPE = "module"

CANDIDATE = os.environ.get("BD_P5_FAILOPEN_CACHE009_CANDIDATE", "")
pytestmark = pytest.mark.skipif(not CANDIDATE, reason="candidate opt-in required")

T0 = datetime.now(timezone.utc).replace(microsecond=0) - timedelta(hours=2)
SINCE = (T0 - timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
UNTIL = (T0 + timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
GOOD = {
    "input_tokens": 50000,
    "cache_read_input_tokens": 49950,
    "cache_creation_input_tokens": 50,
}
MISS = {
    "input_tokens": 51000,
    "cache_read_input_tokens": 0,
    "cache_creation_input_tokens": 51000,
}


def _script() -> Path:
    path = Path(CANDIDATE)
    assert path.is_file(), f"P5: candidate supplied but {path} is missing"
    return path


def _fixture(
    tmp_path: Path, usages: list, reason: object = None, tables: bool = True
) -> Path:
    """One stream (thread t, one transcript) with len(usages) turns one minute apart. `reason` is written as the
    transcript cache_miss_reason of the LAST turn (None: no diagnostics line)."""
    db = tmp_path / "usage.sqlite"
    transcript = tmp_path / "t.jsonl"
    con = sqlite3.connect(db)
    if tables:
        con.execute(
            "CREATE TABLE usage_responses(provider TEXT,response_id TEXT,timestamp TEXT,model TEXT,usage TEXT,"
            "fingerprint TEXT,quarantined INTEGER DEFAULT 0,PRIMARY KEY(provider,response_id))"
        )
        con.execute(
            "CREATE TABLE usage_occurrences(provider TEXT,response_id TEXT,host TEXT,thread TEXT,source TEXT,"
            "PRIMARY KEY(provider,response_id,host,thread,source))"
        )
        for i, u in enumerate(usages):
            ts = (T0 + timedelta(minutes=i)).strftime("%Y-%m-%dT%H:%M:%SZ")
            raw = u if isinstance(u, str) else json.dumps(u)
            con.execute(
                "INSERT INTO usage_responses VALUES('claude',?,?,'claude-opus-5-5',?,'',0)",
                (f"m{i}", ts, raw),
            )
            con.execute(
                "INSERT INTO usage_occurrences VALUES('claude',?,'h','t',?)",
                (f"m{i}", str(transcript)),
            )
    con.commit()
    con.close()
    lines = []
    if reason is not None:
        lines.append(
            json.dumps(
                {
                    "message": {
                        "id": f"m{len(usages) - 1}",
                        "diagnostics": {"cache_miss_reason": reason},
                    }
                }
            )
        )
    transcript.write_text("\n".join(lines) + "\n")
    return db


def _run(db: Path, *extra: str, since: str = SINCE, until: str = UNTIL):
    args = [sys.executable, str(_script()), "--db", str(db)]
    if since:
        args += ["--since", since]
    if until:
        args += ["--until", until]
    return subprocess.run(
        [*args, *extra], capture_output=True, text=True, timeout=120, check=False
    )


def _verdict(res) -> str:
    lines = [x for x in res.stdout.splitlines() if x.startswith("VERDICT:")]
    return (
        lines[-1]
        if lines
        else f"<no VERDICT line> rc={res.returncode} err={res.stderr[-300:]}"
    )


def _assert_could_not_look(res, why: str) -> None:
    assert res.returncode == 2, (
        f"P5 FAIL-OPEN ({why}): rc {res.returncode}, {_verdict(res)}"
    )
    assert _verdict(res).startswith("VERDICT: COULD NOT LOOK"), (
        f"P5 ({why}): {_verdict(res)}"
    )


# --- fail-closed: every error path is COULD NOT LOOK rc 2 --------------------------------------------------------------


@pytest.mark.parametrize("bound", ["until", "since"])
def test_unparseable_window_bound_refuses(tmp_path, bound):
    db = _fixture(tmp_path, [GOOD, GOOD])
    res = _run(db, **{bound: "not-a-date"})
    _assert_could_not_look(res, f"bad --{bound} audited some other window")
    assert bound in _verdict(res), _verdict(res)


@pytest.mark.parametrize(
    "corrupt",
    [
        "{not json",
        '"a string"',
        json.dumps({"input_tokens": 50000}),
        json.dumps({**MISS, "input_tokens": "50000"}),
    ],
)
def test_corrupt_usage_row_is_not_clean(tmp_path, corrupt):
    # the corrupt row is the PREVIOUS turn of a real churn miss: read as zeros (prev input 0) it hides that miss
    db = _fixture(tmp_path, [corrupt, MISS], reason={"type": "system_changed"})
    res = _run(db)
    assert res.returncode != 0, (
        f"P5 FAIL-OPEN (corrupt usage row read as zeros): {_verdict(res)}"
    )
    assert "FOUND NONE" not in _verdict(res), _verdict(res)


def test_corrupt_usage_row_alone_is_could_not_look(tmp_path):
    db = _fixture(tmp_path, ["{not json", GOOD, GOOD])
    _assert_could_not_look(_run(db), "unreadable usage counted as a clean turn")


@pytest.mark.parametrize(
    "reason",
    [{"type": "prefix_rotated"}, {"kind": "system_changed"}, "system_changed", 7],
)
def test_unclassified_miss_reason_is_could_not_look(tmp_path, reason):
    db = _fixture(tmp_path, [GOOD, MISS], reason=reason)
    _assert_could_not_look(_run(db), f"unclassified cache_miss_reason {reason!r}")


def test_missing_tables_is_could_not_look(tmp_path):
    db = _fixture(tmp_path, [], tables=False)
    _assert_could_not_look(_run(db), "sqlite error exits 1 == FOUND churn")


def test_not_a_database_is_could_not_look(tmp_path):
    db = tmp_path / "usage.sqlite"
    db.write_text("this is not sqlite\n" * 50)
    _assert_could_not_look(_run(db), "corrupt DB file")


# --- controls: the probe still says YES and NO where it should ---------------------------------------------------------


@pytest.mark.parametrize(
    "reason", ["messages_changed", "previous_message_not_found", "model_changed"]
)
def test_control_explained_miss_is_found_none(tmp_path, reason):
    db = _fixture(tmp_path, [GOOD, GOOD, MISS], reason={"type": reason})
    res = _run(db)
    assert res.returncode == 0 and "FOUND NONE" in _verdict(res), _verdict(res)


@pytest.mark.parametrize("reason", ["system_changed", "tools_changed"])
def test_control_churn_is_found(tmp_path, reason):
    db = _fixture(
        tmp_path,
        [GOOD, MISS],
        reason={"type": reason, "cache_missed_input_tokens": 50000},
    )
    res = _run(db)
    assert res.returncode == 1 and _verdict(res).startswith("VERDICT: FOUND 1"), (
        _verdict(res)
    )


def test_control_undiagnosed_miss_is_could_not_look(tmp_path):
    db = _fixture(tmp_path, [GOOD, MISS])
    _assert_could_not_look(_run(db), "undiagnosed miss (G3 behaviour, must hold)")


def test_control_induced_miss_positive_control_passes(tmp_path):
    res = subprocess.run(
        [sys.executable, str(_script()), "--induced-miss-control"],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert res.returncode == 0 and "PASSED" in res.stdout, res.stdout[-500:]


@pytest.mark.parametrize("corrupt", ["{bad json", "{}"])
def test_unreadable_singleton_does_not_hide_behind_healthy_stream(tmp_path, corrupt):
    db = _fixture(tmp_path, [GOOD, GOOD])
    control = _run(db)
    assert control.returncode == 0 and "FOUND NONE" in _verdict(control), control.stdout
    with sqlite3.connect(db) as con:
        con.execute("INSERT INTO usage_responses VALUES('claude','bad',?,'claude-opus-5-5',?,'',0)",
                    (T0.strftime("%Y-%m-%dT%H:%M:%SZ"), corrupt))
        con.execute("INSERT INTO usage_occurrences VALUES('claude','bad','h','other',?)", (str(tmp_path / "other.jsonl"),))
        assert con.execute("SELECT count(*) FROM usage_responses").fetchone()[0] == 3
    result = _run(db)
    assert result.returncode == 2 and "COULD NOT LOOK" in _verdict(result), "CACHE009_UNREADABLE_SINGLETON_FALSE_CLEAN: " + result.stdout


def test_readable_singleton_is_allowed(tmp_path):
    db = _fixture(tmp_path, [GOOD, GOOD])
    with sqlite3.connect(db) as con:
        con.execute("INSERT INTO usage_responses VALUES('claude','solo',?,'claude-opus-5-5',?,'',0)",
                    (T0.strftime("%Y-%m-%dT%H:%M:%SZ"), json.dumps(GOOD)))
        con.execute("INSERT INTO usage_occurrences VALUES('claude','solo','h','other',?)", (str(tmp_path / "other.jsonl"),))
    result = _run(db)
    assert result.returncode == 0 and "FOUND NONE" in _verdict(result), result.stdout

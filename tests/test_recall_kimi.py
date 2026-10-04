"""O1670 R3 recall-kimi: bd-recall indexes Kimi CLI transcripts (~/.kimi-code/sessions/*/*/agents/main/wire.jsonl)
with the same extraction kinds and the same secret scrub as Claude/Codex. Opt-in: BD_RECALL_KIMI_CANDIDATE=<bd-recall.py>."""
import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

BD_GATE_SCOPE = "module"
CANDIDATE = os.environ.get("BD_RECALL_KIMI_CANDIDATE", "")
pytestmark = pytest.mark.skipif(not CANDIDATE, reason="candidate opt-in required (BD_RECALL_KIMI_CANDIDATE)")
FAKE_PW = "Zq9!fakeKimiPass"  # stands in for a live credential value; never a real one
SID = "0f1e2d3c-4b5a-6978-8a9b-acbdcedfe0f1"
MS = 1790473357984  # 2026-09-27T01:42:37Z


def _ama(role, source, text, kind="event"):
    return {"type": "agent.message.appended", "time": MS, "kind": kind,
            "message": {"message": {"role": role, "content": [{"type": "text", "text": text}]}, "meta": {"source": source}}}


def _loop(ev):
    return {"type": "context.append_loop_event", "agentId": "main", "time": MS, "event": ev}


def _wire(home, agent="main"):
    d = home / ".kimi-code" / "sessions" / "wd_seat_abc123" / f"session_{SID}" / "agents" / agent
    d.mkdir(parents=True)
    lines = [
        {"type": "metadata", "protocol_version": "1", "created_at": MS},
        _ama("user", "input", f"[from bd-pm-D] use vCenter password {FAKE_PW} per O1670 now"),
        {"type": "context.append_message", "time": MS, "message": {"role": "user", "origin": {"kind": "injection"},
                                                                   "content": [{"type": "text", "text": "<system-reminder> noise"}]}},
        _ama("user", "notify", "<notification id=\"task:bash-x:completed\"> notify-noise"),
        _ama("assistant", "llm", "Recorded as O1671 for the queue.\nplain prose without a ruling"),
        _loop({"type": "tool.call", "toolCallId": "c1", "name": "Bash", "args": {"command": "crontab -l > /tmp/x; sed -i s/a/b/ f"}}),
        _loop({"type": "tool.result", "toolCallId": "c1", "result": {"output": "Process exited with code 1\nboom", "isError": True}}),
        _loop({"type": "tool.call", "toolCallId": "c2", "name": "Write", "args": {"path": "/tmp/kimi-out.md", "content": "x"}}),
        _loop({"type": "tool.result", "toolCallId": "c2", "result": {"output": "ok", "durationMs": 3}}),
        _loop({"type": "tool.call", "toolCallId": "c3", "name": "Read", "args": {"path": "/x/inbox/seat/BATCH-20261002T1704Z.md"}}),
        _loop({"type": "tool.result", "toolCallId": "c3", "result": {"output": "[from bd-dispatch-B] R3 answer: KIMI-FOLD"}}),
        _loop({"type": "tool.call", "toolCallId": "c4", "name": "Edit", "args": {"path": "/tmp/f.py", "old_string": "a", "new_string": "b"}}),
        _loop({"type": "tool.result", "toolCallId": "c4", "result": {"output": "old_string not found in /tmp/f.py", "isError": True}}),
        _loop({"type": "tool.call", "toolCallId": "c5", "name": "Grep", "args": {"pattern": "a", "path": "/tmp/f.py"}}),
        _ama("user", "input", "token: tok-x rest of line kept"),
    ]
    w = d / "wire.jsonl"
    w.write_text("".join(json.dumps(x) + "\n" for x in lines))
    return w


@pytest.fixture
def env(tmp_path):
    sec = tmp_path / "VC.txt"
    sec.write_text(f"Password: {FAKE_PW}\n")
    home = tmp_path / "home"
    e = {**os.environ, "HOME": str(home), "BD_TMEM_STORE": str(tmp_path / "store" / "tmem.sqlite"),
         "BD_TMEM_SECRET_FILES": str(sec), "BD_TMEM_EMBED_URL": "http://127.0.0.1:9/v1/embeddings",
         "LITELLM_MASTER_KEY": "test", "BD_TMEM_QUERY_TIMEOUT": "2"}
    return {"env": e, "home": home, "store": Path(e["BD_TMEM_STORE"])}


def run(env, *a):
    return subprocess.run([sys.executable, CANDIDATE, *a], capture_output=True, text=True, env=env["env"], timeout=120)


def rows(store):
    return sqlite3.connect(store).execute("SELECT kind, session, src, line, ts, text FROM recs ORDER BY id").fetchall()


def test_candidate_exists():
    assert Path(CANDIDATE).is_file(), f"BD_RECALL_KIMI_CANDIDATE absent: {CANDIDATE}"


def test_kimi_wire_extracts_kinds_and_scrubs_secrets(env):
    w = _wire(env["home"])
    r = run(env, "index", "--src", str(w), "--no-embed")
    assert r.returncode == 0, r.stderr
    got = rows(env["store"])
    assert w.stat().st_size > 0 and len(w.read_text().splitlines()) == 15, "data control: fixture must hold 15 lines"
    kinds = {k for k, *_ in got}
    assert {"user", "ruling", "live", "errfix", "file", "inbox"} <= kinds, f"KIMI_NOT_INDEXED: kinds={sorted(kinds)}"
    dump = json.dumps(got)
    assert "<system-reminder>" not in dump and "notify-noise" not in dump, "KIMI_NOISE_INDEXED"
    assert {s for _, s, *_ in got} == {SID}, "KIMI_SESSION_ID: id must come from the session_<id> path"
    assert all(ts == "2026-09-27T01:42:37Z" for *_, ts, _ in got), "KIMI_TS: epoch-ms time -> ISO UTC"
    assert any(k == "file" and "/tmp/kimi-out.md" in t for k, *_, t in got)
    assert any(k == "inbox" and "KIMI-FOLD" in t and "BATCH-20261002T1704Z.md" in t for k, *_, t in got)
    assert any(k == "errfix" and "Process exited with code 1" in t and "NEXT Write" in t for k, *_, t in got)
    assert any(k == "errfix" and "old_string not found" in t and "NEXT Grep" in t for k, *_, t in got), \
        "KIMI_ISERROR_IGNORED: a failed call with only the isError flag must still pair with the next call"
    assert any(k == "ruling" and "O1671" in t for k, *_, t in got) and "plain prose" not in dump
    blob = env["store"].read_bytes()
    assert FAKE_PW.encode() not in blob and b"tok-x" not in blob, "KIMI_SECRET_STORED"
    assert any("<REDACTED>" in t and "O1670" in t for *_, t in got), "scrub must keep the rest of the line verbatim"
    s = json.loads(run(env, "scrubcheck", "--src", str(w)).stdout)
    assert s["source_files_with_secret"] == 1 and s["store_rows_with_secret"] == 0 and s["store_bytes_with_secret"] == 0


def test_default_src_takes_kimi_main_agent_only(env):
    _wire(env["home"])
    _wire(env["home"], agent="agent-0")  # a swarm subagent: same shape, must not be a default source
    r = run(env, "index", "--no-embed")
    assert r.returncode == 0, r.stderr
    assert json.loads(r.stdout)["files"] == 1, f"KIMI_DEFAULT_SRC: {r.stdout.strip()}"
    srcs = {src for _, _, src, *_ in rows(env["store"])}
    assert len(srcs) == 1 and srcs.pop().endswith("/agents/main/wire.jsonl"), "KIMI_DEFAULT_SRC: main agent only"

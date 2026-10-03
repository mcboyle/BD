"""Opt-in candidate controls for census, freshness, and repo-only landing indexing."""
import csv
import importlib.util
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import types

import pytest

BD_GATE_SCOPE = "module"
CANDIDATE = os.environ.get("BD_O1698_C2_INDEXED_SEARCH_DEFAULT_CANDIDATE", "")
pytestmark = pytest.mark.skipif(not CANDIDATE, reason="candidate opt-in required")


def run(command, env=None):
    return subprocess.run(command, capture_output=True, text=True, env=env, timeout=30)


def census_fixture(tmp_path, empty=False, missing=False):
    root = tmp_path / "sessions"
    root.mkdir()
    ledger = tmp_path / "ledger.tsv"
    ledger.write_text("2026-10-01T00:00:00Z\tbd-worker-A1\tcut-one\tdispatched\n2026-10-02T00:00:00Z\tbd-worker-A1\tcut-one\tdone\n")
    landed = tmp_path / "landed.txt"
    landed.write_text("cut-one\n")
    records = [{"type": "session_meta", "payload": {"id": "session-one", "seat": "bd-worker-A1"}}]
    if not empty:
        for number, command in enumerate(("rg identifier file.py", "grep -n identifier file.py", "find tests -name '*.py'")):
            records.append({"type": "response_item", "timestamp": "2026-10-01T01:00:00Z", "payload": {"type": "function_call", "call_id": f"call-{number}", "name": "exec_command", "arguments": json.dumps({"cmd": command})}})
            if not missing:
                records.append({"type": "response_item", "payload": {"type": "function_call_output", "call_id": f"call-{number}", "output": json.dumps({"output": "é\n"})}})
    transcript = root / "one.jsonl"
    transcript.write_text("\n".join(json.dumps(record) for record in records) + "\n")
    (root / "copy.jsonl").write_bytes(transcript.read_bytes())
    out = tmp_path / "census.tsv"
    return [sys.executable, str(Path(CANDIDATE) / "bd-search-census.py"), "--ledger", str(ledger), "--landed-cuts", str(landed), "--root", str(root), "--start", "2026-09-30T00:00:00Z", "--end", "2026-10-03T00:00:00Z", "--out", str(out)], out


@pytest.mark.parametrize("empty, expected", [(False, (3, 9)), (True, (0, 0))])
def test_census_counts_three_and_dedupes_session_copies(tmp_path, empty, expected):
    command, out = census_fixture(tmp_path, empty=empty)
    result = run(command)
    assert result.returncode == 0, result.stderr
    rows = list(csv.DictReader(out.open(), delimiter="\t"))
    assert len(rows) == 1
    assert (int(rows[0]["grep_turns"]), int(rows[0]["grep_bytes"])) == expected
    assert rows[0]["ask_calls"] == rows[0]["ctx_calls"] == "0"


def test_census_missing_output_is_unknown_not_zero(tmp_path):
    command, out = census_fixture(tmp_path, missing=True)
    result = run(command)
    assert result.returncode == 2
    assert "COULD NOT LOOK: incomplete census: missing_outputs=3" in result.stderr
    assert not out.exists()


def test_census_empty_denominator_is_unknown(tmp_path):
    command, out = census_fixture(tmp_path)
    (tmp_path / "landed.txt").write_text("")
    result = run(command)
    assert result.returncode == 2
    assert "no closed landed dispatch windows" in result.stderr
    assert not out.exists()


def test_census_resolves_begin_seat_from_user_prompt(tmp_path):
    command, out = census_fixture(tmp_path)
    for path in (tmp_path / "sessions").glob("*.jsonl"):
        records = [json.loads(line) for line in path.read_text().splitlines()]
        records[0] = {"type": "user", "sessionId": "session-one", "message": {"content": "BEGIN. SEAT: bd-worker-A1. ROLE: worker."}}
        path.write_text("\n".join(json.dumps(record) for record in records) + "\n")
    result = run(command)
    assert result.returncode == 0, result.stderr
    assert list(csv.DictReader(out.open(), delimiter="\t"))[0]["grep_turns"] == "3"


def test_landing_adapter_dry_run_and_check_only_note(tmp_path):
    probe = tmp_path / "probe"
    probe.write_text("#!/bin/bash\necho 'STALE 3'\necho 'NOTE: index stale over 2h' >&2\n")
    probe.chmod(0o755)
    inbox = tmp_path / "inbox"
    script = Path(CANDIDATE) / "bd-index-landed.sh"
    env = dict(os.environ, BD_INDEX_LANDED_PROBE=str(probe), BD_INDEX_LANDED_INBOX=str(inbox), DRY_RUN="1")
    result = run(["bash", str(script)], env)
    assert result.returncode == 0, result.stderr
    assert "PLAN repo-delta" in result.stdout
    assert not inbox.exists()
    env.pop("DRY_RUN")
    env["CHECK_ONLY"] = "1"
    result = run(["bash", str(script)], env)
    assert result.returncode == 0 and result.stdout == "STALE 3\n", result.stderr
    notes = list(inbox.glob("*.md"))
    assert len(notes) == 1 and "NOTE: index stale over 2h" in notes[0].read_text()
    probe.write_text("#!/bin/bash\necho 'UNKNOWN: fixture unavailable' >&2\nexit 2\n")
    result = run(["bash", str(script)], env)
    assert result.returncode == 2 and "UNKNOWN: freshness probe failed" in result.stderr


def git(repo, *args):
    result = run(["git", "-C", str(repo), "-c", "core.hooksPath=/dev/null", "-c", "commit.gpgsign=false", "-c", "user.name=fixture", "-c", "user.email=fixture@example.invalid", *args])
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


def test_fresh_stale_and_missing_store(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init")
    git(repo, "commit", "--allow-empty", "-m", "base")
    base = git(repo, "rev-parse", "HEAD")
    git(repo, "commit", "--allow-empty", "-m", "tip")
    tip = git(repo, "rev-parse", "HEAD")
    git(repo, "update-ref", "refs/remotes/origin/main", tip)
    store = tmp_path / "store.sqlite"
    with sqlite3.connect(store) as db:
        db.execute("CREATE TABLE meta(k TEXT PRIMARY KEY,v TEXT)")
        db.execute("INSERT INTO meta VALUES('commit',?)", (base,))
    env = dict(os.environ, BD_ASK_FRESH_REPO=str(repo), BD_RAG_STORE=str(store), BD_ASK_FRESH_PYTHON=sys.executable)
    command = ["bash", str(Path(CANDIDATE) / "bd-ask-fresh.sh")]
    result = run(command, env)
    assert result.returncode == 0 and result.stdout == "STALE 1\n", result.stderr
    with sqlite3.connect(store) as db:
        db.execute("UPDATE meta SET v=? WHERE k='commit'", (tip,))
    before = store.read_bytes()
    result = run(command, env)
    assert result.returncode == 0 and result.stdout == "FRESH 0\n", result.stderr
    assert store.read_bytes() == before
    env["BD_RAG_STORE"] = str(tmp_path / "absent.sqlite")
    result = run(command, env)
    assert result.returncode == 2
    assert "UNKNOWN: index store absent" in result.stderr
    assert not Path(env["BD_RAG_STORE"]).exists()


def load_harness_modules(monkeypatch):
    support = Path(os.environ.get("BD_C2_RAG_SUPPORT", "/home/mboyle/bd-persist/harness/bd-rag"))
    monkeypatch.syspath_prepend(str(support))
    modules = []
    for name, path in (("common2", support / "common2.py"), ("corpus2", Path(CANDIDATE) / "corpus2.py")):
        spec = importlib.util.spec_from_file_location(name, path)
        module = importlib.util.module_from_spec(spec)
        monkeypatch.setitem(sys.modules, name, module)
        spec.loader.exec_module(module)
        modules.append(module)
    return modules


@pytest.mark.parametrize("shape", ["normal", "shared", "delete-alias"])
def test_repo_delta_never_walks_harness(monkeypatch, tmp_path, shape):
    # Run the real index main with deterministic existing corpus/embedding seams.
    module_path = Path(CANDIDATE) / "index2.py"
    common2, corpus2 = load_harness_modules(monkeypatch)
    import numpy

    store = tmp_path / "index.sqlite"
    base, target = "a" * 40, "b" * 40
    old = common2.db(str(store))
    body = "unchanged indexed body"
    digest = common2.norm_hash(body)
    emb = numpy.zeros(768, dtype=numpy.float32).tobytes()
    old.execute("INSERT INTO meta VALUES('commit',?)", (base,))
    old.execute("INSERT INTO chunks(path,heading,body,hash,commit_id,emb) VALUES(?,?,?,?,?,?)", ("tests/unchanged.py", "heading", body, digest, base, emb))
    old.execute("INSERT INTO chunks_fts(rowid,body,heading,path) VALUES(1,?,?,?)", (body, "heading", "tests/unchanged.py"))
    old.execute("INSERT INTO files VALUES(?,?,?,?,?)", ("tests/unchanged.py", "repo-test", base, digest, 1))
    old.execute("INSERT INTO emb_cache VALUES(?,?)", (digest, emb))
    if shape == "delete-alias":
        old.execute("INSERT INTO files VALUES(?,?,?,?,?)", ("tests/alias.py", "repo-test", base, digest, 0))
        old.execute("INSERT INTO aliases VALUES(?,?,?,?)", (digest, "tests/alias.py", "alias heading", "tests/unchanged.py"))
    if shape == "shared":
        for number, (text, com) in enumerate((("repo contract body", base), ("live contract body", "fs")), 2):
            hh = common2.norm_hash(text)
            old.execute("INSERT INTO chunks(path,heading,body,hash,commit_id,emb) VALUES(?,?,?,?,?,?)", ("CLAUDE.md", "contract", text, hh, com, emb))
            old.execute("INSERT INTO chunks_fts(rowid,body,heading,path) VALUES(?,?,?,?)", (number, text, "contract", "CLAUDE.md"))
            old.execute("INSERT INTO emb_cache VALUES(?,?)", (hh, emb))
        old.execute("INSERT INTO files VALUES(?,?,?,?,?)", ("CLAUDE.md", "live", "fs", hh, 1))
    old.commit()
    old.close()
    walks = []
    def full_docs(commit):
        walks.append("harness")
        return [("harness/old.py", "harness", "fs", "live harness body")]
    monkeypatch.setattr(corpus2, "all_docs", full_docs)
    monkeypatch.setattr(common2, "STORE", str(store))
    monkeypatch.setattr(common2, "repo_head", lambda: target)
    monkeypatch.setenv("BD_RAG_REPO_DELTA", "1")
    spec = importlib.util.spec_from_file_location("candidate_index2", module_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.LOCK = str(tmp_path / "index.lock")
    module.repo_head = lambda: target
    changed = "tests/changed.py\0" + ("tests/unchanged.py\0" if shape == "delete-alias" else "")
    module.run = lambda *args, **kwargs: types.SimpleNamespace(returncode=0, stdout=target if "rev-parse" in args else changed, stderr="")
    module.embed = lambda texts, prefix: [numpy.ones(768, dtype=numpy.float32) for _ in texts]
    reads = []
    def repo_docs(commit, paths=None):
        reads.append(paths)
        return [("tests/changed.py", "repo-test", commit, "new distinctive indexed body")]
    monkeypatch.setattr(corpus2, "repo_docs", repo_docs)
    module.main()
    assert walks == [], "REPO_DELTA_WALKED_LIVE_HARNESS"
    assert reads == [set(filter(None, changed.split("\0")))], "REPO_DELTA_READ_UNCHANGED_BLOBS"
    with sqlite3.connect(store) as db:
        assert db.execute("SELECT v FROM meta WHERE k='commit'").fetchone() == (target,)
        if shape == "delete-alias":
            assert db.execute("SELECT count(*) FROM chunks WHERE path='tests/unchanged.py'").fetchone() == (0,)
            assert db.execute("SELECT body FROM chunks WHERE path='tests/alias.py'").fetchone() == (body,)
        else:
            assert db.execute("SELECT body FROM chunks WHERE path='tests/unchanged.py'").fetchone() == (body,)
        if shape == "shared":
            assert set(db.execute("SELECT body,commit_id FROM chunks WHERE path='CLAUDE.md'")) == {("repo contract body", target), ("live contract body", "fs")}
        assert db.execute("SELECT count(*) FROM chunks WHERE path='tests/changed.py'").fetchone() == (1,)


@pytest.mark.parametrize("new_file", [True, False])
def test_full_delta_full_uses_fetched_tip(monkeypatch, tmp_path, new_file):
    # Keep checkout HEAD at A while origin/main advances to B. Read real git blobs.
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init")
    (repo / "tests").mkdir()
    (repo / "tests/test_old.py").write_text("def test_old():\n    assert True\n")
    git(repo, "add", "tests/test_old.py")
    git(repo, "commit", "-m", "base")
    base = git(repo, "rev-parse", "HEAD")
    git(repo, "update-ref", "refs/remotes/origin/main", base)
    common2, corpus = load_harness_modules(monkeypatch)
    import numpy

    store = tmp_path / "index.sqlite"
    monkeypatch.setattr(common2, "REPO", str(repo))
    monkeypatch.setattr(common2, "STORE", str(store))
    # Full mode retains the production repo corpus reader; only the live half is excluded.
    monkeypatch.setattr(corpus, "all_docs", lambda commit: list(corpus.repo_docs(commit)))
    spec = importlib.util.spec_from_file_location("full_delta_index", Path(CANDIDATE) / "index2.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.LOCK = str(tmp_path / "index.lock")
    module.embed = lambda texts, prefix: [numpy.ones(768, dtype=numpy.float32) for _ in texts]
    monkeypatch.delenv("BD_RAG_REPO_DELTA", raising=False)
    module.main()  # Seed A, including the full-mode corpus fingerprint.
    if new_file:
        (repo / "tests/test_new.py").write_text("def test_distinctive_new():\n    assert True\n")
        git(repo, "add", "tests/test_new.py")
    target = git(repo, "commit-tree", git(repo, "write-tree"), "-p", base, "-m", "landed")
    git(repo, "update-ref", "refs/remotes/origin/main", target)
    assert git(repo, "rev-parse", "HEAD") == base != target
    env = dict(os.environ, BD_ASK_FRESH_REPO=str(repo), BD_RAG_STORE=str(store), BD_ASK_FRESH_PYTHON=sys.executable)
    observations = []
    for mode in ("0", "1", "0"):
        monkeypatch.setenv("BD_RAG_REPO_DELTA", mode)
        module.main()
        probe = run(["bash", str(Path(CANDIDATE) / "bd-ask-fresh.sh")], env)
        with sqlite3.connect(store) as db:
            commit = db.execute("SELECT v FROM meta WHERE k='commit'").fetchone()[0]
            count = db.execute("SELECT count(*) FROM chunks WHERE path='tests/test_new.py'").fetchone()[0]
        observations.append((commit, count, probe.returncode, probe.stdout))
    assert observations == [(target, int(new_file), 0, "FRESH 0\n")] * 3, f"FULL_REBUILD_REVERTS_FETCHED_TIP: {observations}"
    before = store.read_bytes()
    git(repo, "update-ref", "-d", "refs/remotes/origin/main")
    for mode in ("0", "1"):
        monkeypatch.setenv("BD_RAG_REPO_DELTA", mode)
        with pytest.raises(ValueError, match="^REPO_TIP_UNAVAILABLE$"):
            module.main()
        assert store.read_bytes() == before, "MISSING_TIP_MUTATED_INDEX"

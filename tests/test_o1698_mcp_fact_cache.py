import hashlib
import importlib.util
import json
import os
import subprocess
import sys
import time
from importlib.machinery import SourceFileLoader
from pathlib import Path

import pytest

BD_GATE_SCOPE = "module"
CANDIDATE = os.environ.get("BD_O1698_MCP_FACT_CACHE_CANDIDATE", "")
pytestmark = pytest.mark.skipif(not CANDIDATE, reason="candidate opt-in required")


@pytest.fixture
def server(tmp_path, monkeypatch):
    path = Path(CANDIDATE)
    assert path.is_file(), "FACT-CANDIDATE-MISSING: supplied candidate must exist"
    spec = importlib.util.spec_from_file_location("fact_cache_subject", path, loader=SourceFileLoader("fact_cache_subject", str(path)))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.PERSIST = tmp_path
    module.HOME = tmp_path
    module.REPO = tmp_path / "repo"
    module.MCP_LOG = tmp_path / "mcp.log"
    monkeypatch.setenv("BD_MCP_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setenv("BD_MCP_FETCH_TTL", "120")
    monkeypatch.delenv("BD_MCP_CACHE_BYPASS", raising=False)
    return module


def cache(server, name, value, age=0):
    path = server.PERSIST / "cache" / name
    path.parent.mkdir(exist_ok=True)
    def source_state(cut=None):
        ledger = Path(os.environ.get("BD_OBJECT_CLAIMS", str(server.PERSIST / "review-claims.tsv")))
        sources = [ledger] if cut is None else [Path(cut) / "DONE.md", Path(cut) / ".review", Path(cut) / ".review/traps.txt", Path(cut) / ".review/MECHANICAL.md", *sorted((Path(cut) / ".review").glob("VERDICT-*.md"))]
        state = {}
        for source in sources:
            try:
                stat = source.stat()
                digest = hashlib.sha256(source.read_bytes()).hexdigest() if source.is_file() else None
                state[str(source.resolve())] = [stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns, digest]
            except FileNotFoundError:
                state[str(source.resolve())] = None
        return state

    if name == "claims.json":
        value = {**value, "_source_state": source_state()}
    elif name == "collect-gate.json":
        value = {cut: {**data, "_source_state": source_state(cut)} for cut, data in value.items()}
    path.write_text(json.dumps(value))
    now = time.time() - age
    os.utime(path, (now, now))
    assert path.stat().st_size > 0
    return path


def claim_live(server, monkeypatch):
    path = server.PERSIST / "review-claims.tsv"
    path.write_text("CLAIM\t/cut\tcorrectness\tworker5\t2026-10-03\n")
    monkeypatch.setenv("BD_OBJECT_CLAIMS", str(path))
    calls = []

    def run(cmd, **kwargs):
        calls.append(cmd)
        return {"rc": 0, "out": "/cut\tcorrectness\tworker5\t2026-10-03\n", "err": ""}

    monkeypatch.setattr(server, "_run", run)
    return calls


def test_fresh_claims_cache_avoids_fold(server, monkeypatch):
    calls = claim_live(server, monkeypatch)
    cache(server, "claims.json", {"live": "cached claim", "live_count": 1, "source": "claim history"})
    answer = server.claims()
    assert answer["live"] == "cached claim", "FACT-CACHE-MISS: fresh claims executed live fold"
    assert answer["source"] == "cache"
    assert 0 <= answer["cache_age_s"] < 60
    assert calls == []
    assert "_source_state" not in answer


@pytest.mark.parametrize("age", [61, 179])
def test_middle_aged_claims_refuse_stale(server, monkeypatch, age):
    calls = claim_live(server, monkeypatch)
    cache(server, "claims.json", {"live": "stale claim"}, age)
    answer = server.claims()
    assert answer["note"].startswith("COULD NOT LOOK"), "FACT-STALE-AS-FRESH: middle-aged cache must refuse"
    assert "live" not in answer
    assert answer["cache_age_s"] >= age
    assert calls == []


@pytest.mark.parametrize("kind", ["missing", "ancient", "invalid", "future"])
def test_claims_unavailable_cache_runs_fold(server, monkeypatch, kind):
    calls = claim_live(server, monkeypatch)
    if kind != "missing":
        path = cache(server, "claims.json", {"live": "cached"}, 601 if kind == "ancient" else -100 if kind == "future" else 0)
        if kind == "invalid":
            path.write_text("{broken-json")
    answer = server.claims()
    assert "worker5" in answer["live"]
    assert len(calls) == 1, "FACT-LIVE-FALLBACK: absent/unusable/ancient cache must run fold"
    assert answer["source"] == "live"
    if kind == "ancient":
        assert answer["cache_age_s"] >= 600


def test_failed_claims_fold_is_labelled_raw(server, monkeypatch):
    claim_live(server, monkeypatch)
    monkeypatch.setattr(server, "_run", lambda *a, **k: {"rc": 7, "out": "", "err": "FOLD-REFUSED"})
    answer = server.claims()
    assert "FOLD-REFUSED" in answer["how"]
    assert "raw_tail" in answer and "live" not in answer


def test_collect_cache_is_keyed_by_cut(server, tmp_path):
    cut = tmp_path / "cut-a"
    other = tmp_path / "cut-b"
    cut.mkdir()
    other.mkdir()
    (other / "DONE.md").write_text("VERDICT: UNKNOWN\n")
    cache(server, "collect-gate.json", {str(cut.resolve()): {"cut": str(cut.resolve()), "done_line1": "VERDICT: PATCH", "verdicts": []}})
    answer = server.collect_gate(str(cut))
    assert answer["done_line1"] == "VERDICT: PATCH", "FACT-COLLECT-CACHE-MISS"
    assert answer["source"] == "cache"
    assert server.collect_gate(str(other))["done_line1"] == "VERDICT: UNKNOWN"


@pytest.mark.parametrize("age, expected", [(121, "refuse"), (361, "live")])
def test_collect_age_thresholds(server, tmp_path, age, expected):
    cut = tmp_path / "cut"
    cut.mkdir()
    (cut / "DONE.md").write_text("VERDICT: PATCH\n")
    cache(server, "collect-gate.json", {str(cut.resolve()): {"cut": str(cut.resolve()), "done_line1": "cached"}}, age)
    answer = server.collect_gate(str(cut))
    if expected == "refuse":
        assert answer["note"].startswith("COULD NOT LOOK"), "FACT-COLLECT-STALE-AS-FRESH"
        assert "done_line1" not in answer
    else:
        assert answer["source"] == "live"
        assert answer["done_line1"] == "VERDICT: PATCH"
    assert answer["cache_age_s"] >= age


def landed_fixture(server, monkeypatch):
    calls = []
    monkeypatch.setattr(server, "_git", lambda *a: "same-blob")

    def run(cmd, **kwargs):
        calls.append(cmd)
        return {"rc": 0, "out": "", "err": ""}

    monkeypatch.setattr(server, "_run", run)
    return calls


def test_landed_fetch_once_and_after_expiry(server, monkeypatch):
    calls = landed_fixture(server, monkeypatch)
    first = server.landed("file.py")
    second = server.landed("file.py")
    assert first["head_matches_main"] and second["head_matches_main"]
    assert len(calls) == 1, "FACT-FETCH-STAMPEDE: two landed calls within TTL must fetch once"
    assert second["fetch_source"] == "cache"
    stamp = server.PERSIST / "cache" / "fetch.stamp"
    assert stamp.is_file()
    payload = json.loads(stamp.read_text())
    payload["fetched_at"] -= 121
    stamp.write_text(json.dumps(payload))
    assert server.landed("file.py")["head_matches_main"]
    assert len(calls) == 2


def test_failed_fetch_never_certifies_landed(server, monkeypatch):
    landed_fixture(server, monkeypatch)
    monkeypatch.setattr(server, "_run", lambda *a, **k: {"rc": 7, "out": "", "err": "FETCH-REFUSED"})
    answer = server.landed("file.py")
    assert answer.get("note", "").startswith("COULD NOT LOOK"), "FACT-FETCH-FAIL-OPEN: failed fetch must refuse"
    assert "FETCH-REFUSED" in answer["note"]
    assert not answer.get("head_matches_main", False)
    assert not (server.PERSIST / "cache" / "fetch.stamp").exists()


def test_unwritable_cache_dir_falls_to_the_source(server, monkeypatch, tmp_path):
    # B1 lens r2: an unwritable cache dir must fail to the source (a live fetch), never to a cached value and never
    # to a blanket COULD NOT LOOK: landed() answered nothing and fetched 0 times.
    calls = landed_fixture(server, monkeypatch)
    ro = tmp_path / "ro"
    ro.mkdir()
    ro.chmod(0o555)
    monkeypatch.setenv("BD_MCP_CACHE_DIR", str(ro / "cache"))
    try:
        answer = server.landed("file.py")
    finally:
        ro.chmod(0o755)
    assert len(calls) == 1, "FACT-CACHE-UNWRITABLE-NO-SOURCE: unwritable cache must still fetch live"
    assert answer["fetch_source"] == "live" and answer["head_matches_main"], answer
    assert "cache_error" in answer


def test_future_or_other_repo_fetch_stamp_is_not_fresh(server, monkeypatch):
    calls = landed_fixture(server, monkeypatch)
    cache(server, "fetch.stamp", {"fetched_at": time.time() + 1000, "repo": str(server.REPO.resolve())})
    server.landed("file.py")
    assert len(calls) == 1
    cache(server, "fetch.stamp", {"fetched_at": time.time(), "repo": "another-repo"})
    server.landed("file.py")
    assert len(calls) == 2


def test_snapshot_producer_and_timer_contract(tmp_path, monkeypatch):
    producer = Path(CANDIDATE).parent / "bd-mcp-facts.sh"
    timer = producer.parent / "bd-mcp-facts.timer"
    if not producer.exists():
        pytest.fail("FACT-PRODUCER-MISSING: candidate must include producer")
    assert os.access(producer, os.X_OK), "FACT-PRODUCER-NOT-EXECUTABLE"
    cut = tmp_path / "cut"
    cut.mkdir()
    (cut / "DONE.md").write_text("VERDICT: PATCH\n")
    env = dict(os.environ, BD_MCP_CACHE_DIR=str(tmp_path / "cache"), BD_MCP_FACTS_SERVER=CANDIDATE,
               BD_MCP_FACTS_PYTHON=sys.executable, BD_OBJECT_CLAIMS=str(tmp_path / "absent-claims"))
    result = subprocess.run([str(producer), "--cut", str(cut), "--no-fetch"], env=env, capture_output=True, text=True, timeout=30, check=False)
    assert result.returncode == 0, result.stderr
    assert "FACTS-WRITTEN claims=1 cuts=1 fetch=disabled" in result.stdout
    assert json.loads((tmp_path / "cache" / "claims.json").read_text())["live"] == ""
    answer = json.loads((tmp_path / "cache" / "collect-gate.json").read_text())[str(cut.resolve())]
    assert answer["done_line1"] == "VERDICT: PATCH"
    assert "OnUnitActiveSec=60s" in timer.read_text()
    service = producer.parent / "bd-mcp-facts.service"
    assert "bd-mcp-facts.sh" in service.read_text()


def concurrent_landed(server, barrier, _):
    barrier.wait(timeout=15)
    return server.landed("file.py")


def test_concurrent_landed_calls_share_fetch(server, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from functools import partial
    from threading import Barrier

    calls = landed_fixture(server, monkeypatch)
    barrier = Barrier(8)

    with ThreadPoolExecutor(max_workers=8) as pool:
        answers = list(pool.map(partial(concurrent_landed, server, barrier), range(8)))
    assert len(answers) == 8 and all(answer["head_matches_main"] for answer in answers)
    assert len(calls) == 1, "FACT-CONCURRENT-STAMPEDE: lock must serialize fetch and freshness check"


def test_real_fetch_logs_once_then_touch_expires(server, monkeypatch, tmp_path):
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", os.devnull)
    origin = tmp_path / "origin"
    repo = tmp_path / "checkout"
    subprocess.run(["git", "init", "-b", "main", str(origin)], check=True, capture_output=True, timeout=15)
    (origin / "file.py").write_text("x = 1\n")
    subprocess.run(["git", "-C", str(origin), "add", "--", "file.py"], check=True, capture_output=True, timeout=15)
    subprocess.run(["git", "-C", str(origin), "-c", "user.name=Test", "-c", "user.email=test@example.com", "-c", "commit.gpgsign=false", "commit", "-m", "fixture"], check=True, capture_output=True, timeout=15)
    subprocess.run(["git", "clone", str(origin), str(repo)], check=True, capture_output=True, timeout=15)
    server.REPO = repo
    for _ in range(2):
        assert server.landed("file.py")["head_matches_main"]
    records = [json.loads(line) for line in server.MCP_LOG.read_text().splitlines()]
    assert len(records) == 1 and records[0]["rc"] == 0, "FACT-REAL-FETCH-COUNT"
    stamp = server.PERSIST / "cache" / "fetch.stamp"
    old = time.time() - 601
    os.utime(stamp, (old, old))
    assert server.landed("file.py")["head_matches_main"]
    assert len(server.MCP_LOG.read_text().splitlines()) == 2


@pytest.mark.parametrize("change", ["rewrite", "delete"])
def test_claim_source_change_invalidates_fresh_cache(server, monkeypatch, change):
    claim_live(server, monkeypatch)
    ledger = server.PERSIST / "review-claims.tsv"
    ledger.write_text("CLAIM\t/cut\tcorrectness\tworker-OLD\t2026-10-03\n")
    cache(server, "claims.json", {"live": "worker-OLD", "live_count": 1})
    assert ledger.is_file() and ledger.stat().st_size > 0
    if change == "rewrite":
        ledger.write_text("CLAIM\t/cut\tcorrectness\tworker-NEW\t2026-10-04\n")
        monkeypatch.setattr(server, "_run", lambda *a, **k: {"rc": 0, "out": "/cut\tcorrectness\tworker-NEW\t2026-10-04\n", "err": ""})
    else:
        ledger.unlink()
    answer = server.claims()
    assert answer["source"] == "live", "FACT-SOURCE-STALE-CLAIM: changed ledger must invalidate even inside TTL"
    assert "worker-OLD" not in answer.get("live", "")
    if change == "rewrite":
        assert "worker-NEW" in answer["live"]
    else:
        assert "FOUND NONE" in answer["note"]


@pytest.mark.parametrize("change", ["rewrite", "delete", "add", "done_delete"])
def test_collect_source_change_invalidates_fresh_cache(server, tmp_path, change):
    cut = tmp_path / "cut"
    review = cut / ".review"
    review.mkdir(parents=True)
    verdict = review / "VERDICT-correctness-fixture.md"
    verdict.write_text("VERDICT: BOARD\n")
    done = cut / "DONE.md"
    done.write_text("VERDICT: PATCH\n")
    cache(server, "collect-gate.json", {str(cut.resolve()): {"cut": str(cut.resolve()), "verdicts": [{"line1": "VERDICT: BOARD"}], "done_line1": "VERDICT: PATCH"}})
    assert verdict.stat().st_size > 0
    if change == "rewrite":
        verdict.write_text("VERDICT: REFUTE\n")
    elif change == "delete":
        verdict.unlink()
    elif change == "add":
        (review / "VERDICT-shape-fixture.md").write_text("VERDICT: REFUTE\n")
    else:
        done.unlink()
    answer = server.collect_gate(str(cut))
    assert answer["source"] == "live", "FACT-SOURCE-STALE-VERDICT: changed review must invalidate even inside TTL"
    lines = [v["line1"] for v in answer["verdicts"]]
    if change in ("rewrite", "add"):
        assert "VERDICT: REFUTE" in lines
    elif change == "delete":
        assert answer["verdicts"] == []
    else:
        assert answer["done_line1"] is None



def changing_claim_reader(ledger):
    ledger.write_text("worker-NEW\n")
    return {"live": "worker-OLD"}


def test_snapshot_refuses_source_change_during_read(server, monkeypatch):
    ledger = server.PERSIST / "review-claims.tsv"
    ledger.write_text("worker-OLD\n")
    freeze_source_stat(monkeypatch, ledger)

    with pytest.raises(RuntimeError, match="COULD NOT LOOK: claims.json source changed during snapshot"):
        server._capture_fact("claims.json", lambda: changing_claim_reader(ledger))
    assert ledger.read_text() == "worker-NEW\n"
    assert not (server.PERSIST / "cache/claims.json").exists()


def test_unbound_legacy_cache_uses_live_fold(server, monkeypatch):
    calls = claim_live(server, monkeypatch)
    path = cache(server, "claims.json", {"live": "legacy-OLD"})
    data = json.loads(path.read_text())
    data.pop("_source_state")
    path.write_text(json.dumps(data))
    answer = server.claims()
    assert answer["source"] == "live"
    assert len(calls) == 1 and "worker5" in answer["live"]
    assert "_source_state" not in answer


def freeze_source_stat(monkeypatch, source):
    original = Path.stat
    frozen = source.stat()

    def stat(path, *args, **kwargs):
        return frozen if path == source else original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "stat", stat)


@pytest.mark.parametrize("kind", ["claims", "collect"])
def test_same_stat_content_change_invalidates_cache(server, monkeypatch, tmp_path, kind):
    if kind == "claims":
        claim_live(server, monkeypatch)
        source = server.PERSIST / "review-claims.tsv"
        cache_name = "claims.json"
        source.write_text("worker-OLD\n")
        capture = lambda: server._capture_fact(cache_name, server._claims_live)
        read = server.claims
    else:
        cut = tmp_path / "cut"
        review = cut / ".review"
        review.mkdir(parents=True)
        source = review / "VERDICT-correctness-fixture.md"
        source.write_text("VERDICT: BOARD\n")
        cache_name = "collect-gate.json"
        capture = lambda: {str(cut.resolve()): server._capture_fact(cache_name, lambda: server._collect_gate_live(str(cut)), str(cut.resolve()))}
        read = lambda: server.collect_gate(str(cut))
    freeze_source_stat(monkeypatch, source)
    server._write_fact_json(cache_name, capture())
    assert read()["source"] == "cache"
    before = source.stat()
    source.write_text("worker-NEW\n" if kind == "claims" else "VERDICT: BOUNCE\n")
    assert source.stat() == before, "FACT-STAT-CONTROL: source metadata must remain identical"
    answer = read()
    assert answer["source"] == "live", "FACT-SAME-STAT-STALE: content changed with identical stat"
    if kind == "collect":
        assert answer["verdicts"][0]["line1"] == "VERDICT: BOUNCE"


def fail_fact_log(*args, **kwargs):
    raise OSError(28, "FACT-LOG-FULL")


@pytest.mark.parametrize("fetch_rc", [0, 1])
def test_stale_stamp_log_error_preserves_fetch_result(server, monkeypatch, fetch_rc):
    calls = landed_fixture(server, monkeypatch)
    cache(server, "fetch.stamp", {"fetched_at": time.time() - 601, "repo": str(server.REPO.resolve())})

    def run(cmd, **kwargs):
        calls.append(cmd)
        return {"rc": fetch_rc, "out": "", "err": "FACT-FETCH-REFUSED" if fetch_rc else ""}

    monkeypatch.setattr(server, "_run", run)
    monkeypatch.setattr(server, "_log", fail_fact_log)
    answer = server.landed("file.py")
    assert len(calls) == 1, "FACT-FETCH-REPLAY: logging failure must not repeat a fetch"
    assert "FACT-LOG-FULL" in answer["cache_error"]
    if fetch_rc:
        assert answer.get("note", "").startswith("COULD NOT LOOK"), "FACT-FAILED-FETCH-AS-LIVE: stamp time cannot certify failed fetch"
        assert "FACT-FETCH-REFUSED" in answer["note"]
        assert not answer.get("head_matches_main", False)
    else:
        assert answer["head_matches_main"] and answer["fetch_source"] == "live"


@pytest.mark.parametrize("fetch_rc", [0, 1])
def test_unusable_cache_and_log_error_returns_fact(server, monkeypatch, tmp_path, fetch_rc):
    blocker = tmp_path / "cache-blocker"
    blocker.write_text("not a directory")
    monkeypatch.setenv("BD_MCP_CACHE_DIR", str(blocker / "cache"))
    calls = landed_fixture(server, monkeypatch)

    def run(cmd, **kwargs):
        calls.append(cmd)
        return {"rc": fetch_rc, "out": "", "err": "FACT-FETCH-REFUSED" if fetch_rc else ""}

    monkeypatch.setattr(server, "_run", run)
    monkeypatch.setattr(server, "_log", fail_fact_log)
    try:
        answer = server.landed("file.py")
    except OSError as exc:
        pytest.fail(f"FACT-FALLBACK-LOG-ESCAPE: tool raised instead of returning fact: {exc}")
    assert len(calls) == 1, "FACT-FALLBACK-FETCH-COUNT"
    assert "FACT-LOG-FULL" in answer["cache_error"]
    if fetch_rc:
        assert answer.get("note", "").startswith("COULD NOT LOOK")
        assert "FACT-FETCH-REFUSED" in answer["note"]
        assert not answer.get("head_matches_main", False)
    else:
        assert answer["head_matches_main"] and answer["fetch_source"] == "live"

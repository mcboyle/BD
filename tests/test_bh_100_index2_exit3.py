"""harness/bd-rag/index2.py: an exit-3 REFUSED run must not leave STORE.building behind (BH-100 residual, finding-011 class).

A STORE.building left by a refusal makes the next run take the killed-run salvage path (H337) and skip the H463
UNCHANGED check, so every refused hour forces a full rebuild. Two exit-3 paths exist:
  H316   live file shrank by more than half -> refusal must happen before any building is created, and a prior
         killed run's building (real salvage) must survive it;
  post-build emb_cache < distinct chunks (council's 101 guard) -> this run's building is removed.
The harness is deployed from bd-persist, not this repo: BD_BH_100_INDEX2_CANDIDATE is an absolute directory holding
the candidate index2.py beside common2.py and corpus2.py. Runs are hermetic: temp STORE/LOCK, stubbed corpus/embedder.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

BD_GATE_SCOPE = "module"

CANDIDATE = os.environ.get("BD_BH_100_INDEX2_CANDIDATE", "")
pytestmark = pytest.mark.skipif(not CANDIDATE, reason="candidate opt-in required")

_DRIVER = textwrap.dedent(
    """
    import json, os, sys
    cand, work, docs_file = sys.argv[1:4]
    os.environ["BD_RAG_STORE"] = os.path.join(work, "store.sqlite")
    sys.path.insert(0, cand)
    import index2
    index2.LOCK = os.path.join(work, "index.lock")
    index2.repo_head = lambda: "c" * 40
    docs = json.load(open(docs_file))
    index2.all_docs = lambda commit=None: [tuple(d) for d in docs]
    def embed(texts, prefix, _depth=0):
        if os.environ.get("FAKE_EMBED_FAIL"): raise RuntimeError("embed down")
        return [[float(len(t)), 1.0] for t in texts]
    index2.embed = embed
    if os.environ.get("FAKE_DROP_CACHE"):  # make the post-build emb_cache guard fire
        real_db = index2.db
        def db(path=None):
            c = real_db(path)
            c.execute("CREATE TEMP TRIGGER drop_cache AFTER INSERT ON emb_cache BEGIN "
                      "DELETE FROM emb_cache WHERE hash = NEW.hash; END")
            return c
        index2.db = db
    try:
        index2.main(); code = 0
    except SystemExit as e:
        code = e.code
    print("EXIT", code)
    """
)


def _cand() -> Path:
    d = Path(CANDIDATE)
    for name in ("index2.py", "common2.py", "corpus2.py"):
        assert (d / name).is_file(), f"candidate file missing: {d / name}"
    return d


def _index(work: Path, docs: list[list[str]], **env: str) -> tuple[int, list[str]]:
    work.mkdir(exist_ok=True)
    docs_file = work / "docs.json"
    docs_file.write_text(json.dumps(docs))
    res = subprocess.run(
        [sys.executable, "-c", _DRIVER, str(_cand()), str(work), str(docs_file)],
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
        env={**os.environ, **env},
    )
    lines = res.stdout.splitlines()
    assert lines and lines[-1].startswith("EXIT "), res
    return int(lines[-1].split()[1]), lines[:-1]


def _embedded(lines: list[str]) -> int:
    hit = [ln for ln in lines if "INDEXED " in ln]
    assert len(hit) == 1, lines
    return int(re.search(r"embedded=(\d+)", hit[0]).group(1))  # type: ignore[union-attr]


def _big_live(n: int) -> list[str]:
    body = "".join(f"# H{i}\n" + ("x" * 1400) + f" {i}\n" for i in range(n))
    return ["LIVE.md", "live", "fs", body]


TINY = ["LIVE.md", "live", "fs", "tiny"]
DOC_A = ["a.md", "repo-doc", "c" * 40, "# A1\nalpha one\n# A2\nalpha two\n"]
DOC_B = ["b.md", "repo-doc", "c" * 40, "# B1\nbeta one\n"]


def _building(work: Path) -> list[str]:
    return sorted(p.name for p in work.glob("store.sqlite.building*"))


def test_h316_refusal_leaves_no_building(tmp_path: Path) -> None:
    work = tmp_path / "w"
    assert _index(work, [_big_live(20)])[0] == 0  # ~28 KB indexed
    store_before = (work / "store.sqlite").read_bytes()
    code, lines = _index(work, [TINY], FAKE_EMBED_FAIL="1")
    assert code == 3, lines
    assert any("REFUSED: live file LIVE.md shrank" in ln for ln in lines)
    assert _building(work) == []
    assert (
        work / "store.sqlite"
    ).read_bytes() == store_before  # existing store untouched


def test_h316_refusal_keeps_prior_killed_build(tmp_path: Path) -> None:
    work = tmp_path / "w"
    assert _index(work, [_big_live(20)])[0] == 0
    marker = work / "store.sqlite.building"
    marker.write_bytes(
        b"killed-run-salvage"
    )  # a prior run's building (H337) must survive a refusal
    assert _index(work, [TINY])[0] == 3
    assert marker.read_bytes() == b"killed-run-salvage"


def test_post_build_cache_refusal_leaves_no_building(tmp_path: Path) -> None:
    work = tmp_path / "w"
    assert _index(work, [DOC_A])[0] == 0
    store_before = (work / "store.sqlite").read_bytes()
    code, lines = _index(work, [DOC_A, DOC_B], FAKE_DROP_CACHE="1")
    assert code == 3, lines
    assert any(
        "REFUSED: emb_cache (0) smaller than distinct chunks (3)" in ln for ln in lines
    ), lines
    assert _building(work) == []
    assert (work / "store.sqlite").read_bytes() == store_before


def test_not_shrunk_live_file_is_indexed(tmp_path: Path) -> None:
    work = tmp_path / "w"
    assert _index(work, [_big_live(20)])[0] == 0
    code, lines = _index(work, [_big_live(21)])
    assert code == 0 and _embedded(lines) >= 1
    assert _building(work) == []


def test_rebuild_keeps_whole_emb_cache(tmp_path: Path) -> None:
    work = tmp_path / "w"
    assert _embedded(_index(work, [DOC_A])[1]) == 2
    assert _embedded(_index(work, [DOC_A, DOC_B])[1]) == 1
    assert _embedded(_index(work, [DOC_A])[1]) == 0
    assert (
        _embedded(_index(work, [DOC_A, DOC_B])[1]) == 0
    )  # B survived the rebuild that dropped it (101, live)

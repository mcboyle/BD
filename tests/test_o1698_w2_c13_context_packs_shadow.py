"""o1698-w2-c13-context-packs-shadow (O1698 wave2, lever C13, SHADOW per O1697): a context pack beside each brief.

`pack <BRIEF>` writes state/shadow/c13/<row>/pack.json: the files a builder will likely need, each with a reason
(named-in-brief | seam-callsite | importer | importee | covering-test | prior-verdict) and bytes. It only adds
files, never trims them. `score <row>` reads the builder's transcript (claim owner seat -> pool sessions/ registry
-> transcript, listed not searched) and its DONE diff, then appends recall / unused / pre-edit discovery bytes
to score.tsv. The pack is never shown to a seat.

Harness-cut shape (O1045): opt in with BD_TEST_O1698_W2_C13=1. Candidate: BD_O1698_W2_C13_CANDIDATE (absolute
path, no fallback to a live script). Hermetic: a temp git repo with 3 modules + 1 test. State, claims, cut roots,
pool config dir and hub samples are all temp paths. The only outside read is bd-token-heatmap.py
(parse_read_target, BD_C13_HEATMAP), which is imported, not run.
"""

from __future__ import annotations

import calendar
import hashlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

BD_GATE_SCOPE = "module"

pytestmark = pytest.mark.skipif(
    os.environ.get("BD_TEST_O1698_W2_C13") != "1", reason="opt-in: BD_TEST_O1698_W2_C13=1"
)
CANDIDATE = os.environ.get("BD_O1698_W2_C13_CANDIDATE", "")
HEATMAP = os.environ.get("BD_C13_HEATMAP", "/home/mboyle/bd-persist/harness/bd-token-heatmap.py")
ROW = "fx-c13-row"
SEAT = "bd-worker-C9"

A_PY = "def fetch_widget(n):\n    return n + 1\n\n\ndef other():\n    return 0\n"
B_PY = "import pkg.a as a_mod\n\n\ndef use():\n    return a_mod.other()\n"
C_PY = "def unrelated():\n    return 'c'\n"
T_PY = "from pkg.a import fetch_widget\n\n\ndef test_fetch_widget():\n    assert fetch_widget(1) == 2\n"
GRAPH = {"package": {"edge_count": 1, "in": {"pkg/a.py": ["pkg/b.py"]}, "out": {"pkg/b.py": ["pkg/a.py"]}}}


def _git(repo: Path, *args: str) -> str:
    env = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t", GIT_COMMITTER_NAME="t",
               GIT_COMMITTER_EMAIL="t@t", GIT_CONFIG_GLOBAL="/dev/null", GIT_CONFIG_SYSTEM="/dev/null", LC_ALL="C")
    return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True,
                          env=env).stdout.strip()


@pytest.fixture(scope="module")
def candidate() -> str:
    if not CANDIDATE:
        pytest.fail("BD_O1698_W2_C13_CANDIDATE unset: opt-in requires the candidate path (no live fallback)")
    if not os.path.isfile(CANDIDATE):
        pytest.fail(f"candidate absent: {CANDIDATE}")
    if not os.path.isfile(HEATMAP):
        pytest.fail(f"bd-token-heatmap.py absent: {HEATMAP}")
    return CANDIDATE


@pytest.fixture()
def fx(tmp_path: Path) -> dict:
    repo = tmp_path / "repo"
    (repo / "pkg").mkdir(parents=True)
    (repo / "tests").mkdir()
    (repo / "pkg" / "__init__.py").write_text("")
    (repo / "pkg" / "a.py").write_text(A_PY)
    (repo / "pkg" / "b.py").write_text(B_PY)
    (repo / "pkg" / "c.py").write_text(C_PY)
    (repo / "tests" / "test_a.py").write_text(T_PY)
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "no graph")
    nograph = _git(repo, "rev-parse", "HEAD")
    (repo / "DEPENDENCY_GRAPH.json").write_text(json.dumps(GRAPH))
    _git(repo, "add", "DEPENDENCY_GRAPH.json")
    _git(repo, "commit", "-q", "-m", "graph")
    base = _git(repo, "rev-parse", "HEAD")
    briefs = tmp_path / "briefs"
    briefs.mkdir()
    brief = briefs / "BRIEF-fx.md"
    brief.write_text(f"# BRIEF {ROW}\nQUEUE_ROW: {ROW}\nREPO-BASE: origin/main {base}\n"
                     "TASK: fix `fetch_widget()` in pkg/a.py so it returns n + 2.\n")
    hub = tmp_path / "hub"
    hub.mkdir()
    hook = tmp_path / "fake-memory-hook.sh"  # stands in for bd-cut-start-memory-hook.sh --json
    hook.write_text('#!/bin/bash\necho \'{"matched": true, "count": 1, "classes": ["parsing"]}\'\n')
    env = dict(os.environ, BD_C13_HOME=str(tmp_path / "home"), BD_C13_STATE=str(tmp_path / "state"),
               BD_C13_REPO=str(repo), BD_C13_CLAIMS=str(tmp_path / "claims"),
               BD_C13_CUT_ROOTS=str(tmp_path / "cuts"), BD_C13_HARNESS=str(tmp_path / "harness"),
               BD_C13_HEATMAP=HEATMAP, BD_C13_MEMORY_HOOK=str(hook),
               BD_HUB_SAMPLES_DIR=str(hub), LC_ALL="C", PYTHONDONTWRITEBYTECODE="1")
    _hub_row(hub, load1=1.5, iowait=0.0)
    return {"tmp": tmp_path, "repo": repo, "base": base, "nograph": nograph, "brief": brief, "env": env,
            "state": tmp_path / "state", "hub": hub}


def _hub_row(hub: Path, load1: float, iowait: float, age_s: int = 0) -> None:
    ts = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - age_s))
    (hub / "2026-10-03.tsv").write_text(f"utc\tload1\tprocs\tblocked\tiowait_pct\tmem_pct\n"
                                        f"{ts}\t{load1}\t100\t0\t{iowait}\t30.0\n")


def _run(candidate: str, fx: dict, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, candidate, *args], capture_output=True, text=True, env=fx["env"],
                          timeout=120)


def _pack(candidate: str, fx: dict, *extra: str) -> dict:
    r = _run(candidate, fx, "pack", str(fx["brief"]), *extra)
    assert r.returncode == 0, r.stdout + r.stderr
    p = fx["state"] / ROW / "pack.json"
    assert p.is_file(), f"no pack.json: {r.stdout}{r.stderr}"
    return json.loads(p.read_text())


def _by_path(pack: dict) -> dict:
    return {e["path"]: e for e in pack["files"]}


# (a) ------------------------------------------------------------------------------------------------
def test_a_pack_holds_named_file_covering_test_and_importer(candidate, fx):
    pack = _pack(candidate, fx)
    files = _by_path(pack)
    assert "pkg/a.py" in files and "named-in-brief" in files["pkg/a.py"]["reasons"], files
    assert "tests/test_a.py" in files and "covering-test" in files["tests/test_a.py"]["reasons"], files
    assert "pkg/b.py" in files and files["pkg/b.py"]["reasons"] == ["importer"], files
    assert "pkg/c.py" not in files, "an unrelated module entered the pack"
    assert files["memory-hook:parsing"]["reasons"] == ["prior-verdict"], files
    assert files["pkg/a.py"]["bytes"] == len(A_PY.encode())
    assert pack["total_bytes"] == sum(e["bytes"] or 0 for e in pack["files"])
    assert pack["base"] == fx["base"] and pack["row"] == ROW and pack["build_seconds"] >= 0
    assert pack["history"] and pack["history"][0]["function"] == "fetch_widget"


# (b) ------------------------------------------------------------------------------------------------
def test_b_never_trims_a_brief_named_file(candidate, fx):
    repo = fx["repo"]
    named = []
    for i in range(30):
        p = repo / "docs" / f"note_{i:02d}.md"
        p.parent.mkdir(exist_ok=True)
        p.write_text("x" * (1000 * (i + 1)))
        named.append(f"docs/note_{i:02d}.md")
    _git(repo, "add", "docs")
    _git(repo, "commit", "-q", "-m", "docs")
    base = _git(repo, "rev-parse", "HEAD")
    abs_file = fx["tmp"] / "harness" / "bd-tool.sh"
    abs_file.parent.mkdir()
    abs_file.write_text("#!/bin/bash\n")
    fx["brief"].write_text(f"QUEUE_ROW: {ROW}\nREPO-BASE: {base}\nREAD: " + " ".join(named)
                           + f" pkg/c.py {abs_file}\nTASK: fix `fetch_widget()` in pkg/a.py\n")
    files = _by_path(_pack(candidate, fx))
    for p in named + ["pkg/c.py", "pkg/a.py", str(abs_file)]:
        assert p in files, f"brief-named file trimmed from the pack: {p}"
        assert "named-in-brief" in files[p]["reasons"]
    assert files["docs/note_29.md"]["bytes"] == 30000


# (c) ------------------------------------------------------------------------------------------------
def _use(tid: str, name: str, inp: dict, ts: str) -> str:
    return json.dumps({"type": "assistant", "timestamp": ts, "message": {"role": "assistant", "content": [
        {"type": "tool_use", "id": tid, "name": name, "input": inp}]}})


def _res(tid: str, nbytes: int, ts: str) -> str:
    return json.dumps({"type": "user", "timestamp": ts, "message": {"role": "user", "content": [
        {"type": "tool_result", "tool_use_id": tid, "content": "r" * nbytes}]}})


def _score_fixture(fx: dict, registry: bool = True, wt_name: str = ROW) -> Path:
    """Claim owner, a cut worktree with DONE.md, and (optionally) the seat's pool registry + transcript."""
    tmp = fx["tmp"]
    claim = tmp / "claims" / ROW
    claim.mkdir(parents=True)
    (claim / "owner").write_text(SEAT + "\n")
    t0 = time.time() - 600
    os.utime(claim / "owner", (t0, t0))
    wt = tmp / "cuts" / wt_name
    subprocess.run(["git", "clone", "-q", str(fx["repo"]), str(wt)], check=True, capture_output=True)
    (wt / "DONE.md").write_text(f"VERDICT: PATCH\nSEAT: {SEAT}\nBASE: {fx['base']}\n")
    if not registry:
        return wt
    pool = tmp / "home" / ".claude-c"
    (pool / "sessions").mkdir(parents=True)
    (pool / "sessions" / "4242.json").write_text(json.dumps(
        {"pid": 4242, "sessionId": "sess-1", "cwd": "/var/tmp/bd-seats/worker", "name": SEAT}))
    # another seat in the same pool: must not be read
    (pool / "sessions" / "4243.json").write_text(json.dumps(
        {"pid": 4243, "sessionId": "sess-2", "cwd": "/var/tmp/bd-seats/worker", "name": "bd-worker-C10"}))
    proj = pool / "projects" / "-var-tmp-bd-seats-worker"
    proj.mkdir(parents=True)
    iso = lambda s: time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime(s))  # noqa: E731
    before, inside = iso(t0 - 3600), iso(t0 + 60)
    lines = [
        _use("old", "Read", {"file_path": f"{wt}/pkg/b.py"}, before),  # before the claim: out of window
        _res("old", 999, before),
        _use("t1", "Read", {"file_path": f"{wt}/pkg/a.py"}, inside),
        _res("t1", 100, inside),
        _use("t2", "Bash", {"command": "cat tests/test_a.py"}, inside),
        _res("t2", 200, inside),
        _use("t3", "Bash", {"command": f"sed -n 1,5p {wt}/pkg/c.py"}, inside),
        _res("t3", 300, inside),
        _use("e1", "Edit", {"file_path": f"{wt}/pkg/a.py", "old_string": "1", "new_string": "2"}, inside),
        _use("t4", "Read", {"file_path": f"{wt}/tests/test_a.py"}, inside),  # after the edit: not discovery
        _res("t4", 50, inside),
    ]
    (proj / "sess-1.jsonl").write_text("\n".join(lines) + "\n")
    (proj / "sess-2.jsonl").write_text(_use("x", "Read", {"file_path": f"{wt}/pkg/b.py"}, inside) + "\n")
    return wt


def _score_row(fx: dict) -> dict:
    lines = (fx["state"] / "score.tsv").read_text().splitlines()
    return dict(zip(lines[0].split("\t"), lines[-1].split("\t")))


def test_c_score_recall_two_of_three_lists_missed_file(candidate, fx):
    _pack(candidate, fx)
    _score_fixture(fx)
    r = _run(candidate, fx, "score", ROW)
    assert r.returncode == 0, r.stdout + r.stderr
    row = _score_row(fx)
    assert row["seat"] == SEAT
    assert (row["used"], row["in_pack"], row["recall"]) == ("3", "2", "0.667"), row
    assert row["missed"] == "pkg/c.py", row
    assert row["unused_n"] == "1", row  # pkg/b.py: packed, never read in the window
    assert row["pack_files"] == "4", row  # a, test_a, b + memory-hook:parsing (recall's denominator is used, not pack)
    assert row["read_bytes"] == str(100 + 200 + 300 + 50), row
    assert row["preedit_unnamed_bytes"] == str(200 + 300), row
    assert row["preedit_unnamed_packed_bytes"] == "200", row
    assert row["could_not_look"] == "-", row
    assert "recall=0.667 (2/3) missed=pkg/c.py" in r.stdout


def test_c_score_without_transcript_is_could_not_look_exit_0(candidate, fx):
    _pack(candidate, fx)
    _score_fixture(fx, registry=False)
    r = _run(candidate, fx, "score", ROW)
    assert r.returncode == 0, r.stdout + r.stderr
    row = _score_row(fx)
    assert "transcript" in row["could_not_look"].split(","), row
    assert row["read_bytes"] == "0"


def test_c_score_counts_modified_files_from_the_done_diff(candidate, fx):
    _pack(candidate, fx)
    wt = _score_fixture(fx, registry=False)
    (wt / "pkg" / "c.py").write_text(C_PY + "# edit\n")
    subprocess.run(["git", "-C", str(wt), "add", "pkg/c.py"], check=True)
    assert _run(candidate, fx, "score", ROW).returncode == 0
    row = _score_row(fx)
    assert (row["used"], row["in_pack"], row["missed"]) == ("1", "0", "pkg/c.py"), row


def _stage_edit(wt: Path, rel: str) -> None:
    (wt / rel).write_text((wt / rel).read_text() + "# edit\n")
    subprocess.run(["git", "-C", str(wt), "add", rel], check=True)


def test_c_score_finds_the_round_worktree_not_a_prefix_sibling(candidate, fx):
    _pack(candidate, fx)
    wt = _score_fixture(fx, registry=False, wt_name=f"{ROW}-r2-{SEAT}")  # bd-wt-new naming
    _stage_edit(wt, "pkg/b.py")
    decoy = fx["tmp"] / "cuts" / f"{ROW}-static-{SEAT}"  # another row sharing the prefix, newer DONE.md
    subprocess.run(["git", "clone", "-q", str(fx["repo"]), str(decoy)], check=True, capture_output=True)
    (decoy / "DONE.md").write_text(f"VERDICT: PATCH\nSEAT: {SEAT}\nBASE: {fx['base']}\n")
    _stage_edit(decoy, "pkg/c.py")
    later = time.time() + 5
    os.utime(decoy / "DONE.md", (later, later))
    assert _run(candidate, fx, "score", ROW).returncode == 0
    row = _score_row(fx)
    assert (row["used"], row["in_pack"], row["missed"]) == ("1", "1", "-"), row


def test_c_score_counts_a_file_the_cut_added(candidate, fx):
    """r1 lens F2 (HIGH): a staged A path is in the DONE diff, so it is used; the pack missed it."""
    _pack(candidate, fx)
    wt = _score_fixture(fx)
    (wt / "pkg" / "new_file.py").write_text("X = 1\n")
    subprocess.run(["git", "-C", str(wt), "add", "pkg/new_file.py"], check=True)
    assert _run(candidate, fx, "score", ROW).returncode == 0
    row = _score_row(fx)
    assert (row["used"], row["in_pack"], row["recall"]) == ("4", "2", "0.500"), row
    assert row["missed"] == "pkg/c.py,pkg/new_file.py", row


def test_pack_reports_a_named_input_that_does_not_exist(candidate, fx):
    """r1 lens F1: a READ path that resolves to no file is COULD-NOT-LOOK, never silently dropped."""
    gone = fx["tmp"] / "harness" / "explicitly_required_missing.sh"
    fx["brief"].write_text(f"QUEUE_ROW: {ROW}\nREPO-BASE: {fx['base']}\n"
                           f"READ: pkg/a.py pkg/explicitly_required_missing.py {gone} DEPENDENCY_GRAPH.json\n"
                           "OWNS: tests/test_brand_new.py (new)\nTASK: fix `fetch_widget()` in pkg/a.py\n")
    pack = _pack(candidate, fx)
    assert pack["named_unresolved"] == ["pkg/explicitly_required_missing.py", str(gone)], pack["named_unresolved"]
    assert "named-path:pkg/explicitly_required_missing.py" in pack["could_not_look"], pack["could_not_look"]
    assert f"named-path:{gone}" in pack["could_not_look"], pack["could_not_look"]
    assert "DEPENDENCY_GRAPH.json" in pack["named_in_brief"], "a .json name was cut to .js"
    assert "pkg/a.py" in _by_path(pack)


def test_pack_resolves_persist_relative_and_bare_harness_names(candidate, fx):
    note = fx["tmp"] / "home" / "bd-persist" / "harness-work" / "NOTE-x.md"
    note.parent.mkdir(parents=True)
    note.write_text("note\n")
    tool = fx["tmp"] / "harness" / "bd-tool.sh"
    tool.parent.mkdir()
    tool.write_text("#!/bin/bash\n")
    fx["brief"].write_text(f"QUEUE_ROW: {ROW}\nREPO-BASE: {fx['base']}\n"
                           "READ: harness-work/NOTE-x.md bd-tool.sh and <wt>/DONE.md\nTASK: fix `fetch_widget()` in pkg/a.py\n")
    pack = _pack(candidate, fx)
    files = _by_path(pack)
    assert files[str(note)]["reasons"] == ["named-in-brief"] and files[str(tool)]["reasons"] == ["named-in-brief"], files
    assert pack["named_unresolved"] == [], "a <placeholder>/x fragment or a resolvable name was reported missing"


def test_pack_existing_inputs_only_has_no_named_path_uncertainty(candidate, fx):
    pack = _pack(candidate, fx)
    assert pack["named_unresolved"] == [] and not any(c.startswith("named-path:") for c in pack["could_not_look"])


def test_plain_word_and_file_stem_are_not_symbols(candidate, fx):
    """`score` in prose and the stem of test_a.py must not pull in unrelated defs or their tests."""
    repo = fx["repo"]
    (repo / "pkg" / "e.py").write_text("def score():\n    return 1\n")
    (repo / "tests" / "test_e.py").write_text("from pkg.e import score\n\n\ndef test_score():\n    assert score()\n")
    # test_z names neither fetch_widget nor imports pkg.a; "a" appears only as a bare word in a comment
    (repo / "tests" / "test_z.py").write_text("# a stray word, not an import\ndef test_a():\n    pass\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "e")
    base = _git(repo, "rev-parse", "HEAD")
    fx["brief"].write_text(f"QUEUE_ROW: {ROW}\nREPO-BASE: {base}\nTASK: fix `fetch_widget()` in pkg/a.py; "
                           "then run `score` on it; see test_a.py.\n")
    files = _by_path(_pack(candidate, fx))
    assert "pkg/a.py" in files and "tests/test_a.py" in files, files
    for p in ("pkg/e.py", "tests/test_e.py", "tests/test_z.py"):
        assert p not in files, f"{p} entered via a non-symbol: {files.get(p)}"


# (d) ------------------------------------------------------------------------------------------------
def test_d_pack_leaves_brief_and_repo_byte_identical(candidate, fx):
    brief_before = hashlib.sha256(fx["brief"].read_bytes()).hexdigest()
    dir_before = sorted(os.listdir(fx["brief"].parent))
    head_before = _git(fx["repo"], "rev-parse", "HEAD")
    pack = _pack(candidate, fx)
    assert hashlib.sha256(fx["brief"].read_bytes()).hexdigest() == brief_before
    assert pack["brief_sha256"] == brief_before
    assert sorted(os.listdir(fx["brief"].parent)) == dir_before
    assert _git(fx["repo"], "status", "--porcelain") == ""
    assert _git(fx["repo"], "rev-parse", "HEAD") == head_before
    written = sorted(str(p.relative_to(fx["state"])) for p in fx["state"].rglob("*"))
    assert written == ["CLOCK", ROW, f"{ROW}/pack.json"], written


def test_a_call_site_outside_the_graph_is_a_seam_callsite(candidate, fx):
    repo = fx["repo"]
    (repo / "pkg" / "d.py").write_text("from pkg.a import fetch_widget\n\nX = fetch_widget(0)\n")
    _git(repo, "add", "pkg/d.py")
    _git(repo, "commit", "-q", "-m", "d")
    files = _by_path(_pack(candidate, fx, "--base", _git(repo, "rev-parse", "HEAD")))
    assert files["pkg/d.py"]["reasons"] == ["seam-callsite"], files.get("pkg/d.py")
    assert "calls:fetch_widget" in files["pkg/d.py"]["via"]


# (e) ------------------------------------------------------------------------------------------------
def test_e_missing_import_graph_is_could_not_look_and_pack_still_built(candidate, fx):
    pack = _pack(candidate, fx, "--base", fx["nograph"])
    assert "import-graph" in pack["could_not_look"], pack["could_not_look"]
    files = _by_path(pack)
    assert "pkg/a.py" in files and "tests/test_a.py" in files
    assert "pkg/b.py" not in files  # the importer edge came only from the graph


def test_prior_round_receipt_is_a_prior_verdict(candidate, fx):
    prior = fx["tmp"] / "cuts" / f"{ROW}-r1" / ".review"
    prior.mkdir(parents=True)
    (prior / "VERDICT-lens.md").write_text("VERDICT: REFUTE\n")
    files = _by_path(_pack(candidate, fx))
    assert files[str(prior / "VERDICT-lens.md")]["reasons"] == ["prior-verdict"]


# hub gate --------------------------------------------------------------------------------------------
@pytest.mark.parametrize("load1,iowait,age,rc,needle", [
    (14.0, 0.0, 0, 75, "HUB-GATE BUSY"),
    (2.0, 10.0, 0, 75, "HUB-GATE BUSY"),
    (2.0, 0.0, 3600, 75, "HUB-GATE COULD-NOT-LOOK"),
    (13.99, 9.99, 0, 0, None),
])
def test_hub_gate(candidate, fx, load1, iowait, age, rc, needle):
    _hub_row(fx["hub"], load1, iowait, age)
    r = _run(candidate, fx, "pack", str(fx["brief"]))
    assert r.returncode == rc, r.stdout + r.stderr
    if needle:
        assert needle in r.stderr
        assert not (fx["state"] / ROW / "pack.json").exists(), "gated run still wrote a pack"
    else:
        assert (fx["state"] / ROW / "pack.json").is_file()


# signal: the measurement clock (r2, PM RULING-pm-C-shadow-24h-clock) ------------------------------------
HEAD = ("scored_at\trow\tseat\tused\tin_pack\trecall\tmissed\tunused_n\tpack_files\tread_bytes\t"
        "preedit_unnamed_bytes\tpreedit_unnamed_packed_bytes\tcould_not_look\n")


def _utc(age_h: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - age_h * 3600))


def _signal(candidate, fx, n: int, clock_age_h: float | None, recall: str = "0.900", packed: int = 300) -> str:
    st = fx["state"]
    st.mkdir(exist_ok=True)
    if clock_age_h is not None:
        (st / "CLOCK").write_text(_utc(clock_age_h) + "\n")
    rows = "".join(f"{_utc(0)}\tr{i}\ts\t5\t4\t{recall}\t-\t1\t9\t1000\t400\t{packed}\t-\n" for i in range(n))
    (st / "score.tsv").write_text(HEAD + rows)
    r = _run(candidate, fx, "signal")
    assert r.returncode == 0, r.stdout + r.stderr
    return r.stdout


def test_signal_nine_cuts_aged_25h_closes_with_n9_and_a_verdict(candidate, fx):
    out = _signal(candidate, fx, 9, 25)
    assert out.startswith("SIGNAL window=CLOSED n=9 "), out
    assert "closed_by=age>=24h" in out and "verdict=KEEP" in out, out
    assert "verdict=DROP" in _signal(candidate, fx, 9, 25, recall="0.500"), "a closed window computes its verdict"


def test_signal_ten_cuts_at_2h_closed_three_cuts_at_2h_open(candidate, fx):
    out = _signal(candidate, fx, 10, 2)
    assert out.startswith("SIGNAL window=CLOSED n=10 ") and "closed_by=n>=10" in out and "verdict=KEEP" in out, out
    out = _signal(candidate, fx, 3, 2)
    assert out.startswith("SIGNAL window=OPEN n=3 ") and "verdict=-" in out, out


def test_signal_zero_cuts_after_24h_closes_drop_not_pending(candidate, fx):
    st = fx["state"]
    st.mkdir()
    (st / "CLOCK").write_text(_utc(30) + "\n")  # shadow ran, nothing ever scored
    out = _run(candidate, fx, "signal").stdout
    assert out.startswith("SIGNAL window=CLOSED n=0 ") and "verdict=DROP" in out, out


def test_signal_verdict_needs_both_recall_and_discovery(candidate, fx):
    assert "verdict=DROP" in _signal(candidate, fx, 10, 2, recall="0.700")
    assert "verdict=DROP" in _signal(candidate, fx, 10, 2, packed=100)


def test_clock_is_written_by_the_first_run_and_never_moved(candidate, fx):
    _pack(candidate, fx)
    clock = fx["state"] / "CLOCK"
    first = clock.read_text()
    assert abs(time.time() - calendar.timegm(time.strptime(first.strip(), "%Y-%m-%dT%H:%M:%SZ"))) < 120, first
    clock.write_text(_utc(25) + "\n")  # the first run, 25 h ago
    aged = clock.read_text()
    _pack(candidate, fx)
    _score_fixture(fx, registry=False)
    assert _run(candidate, fx, "score", ROW).returncode == 0
    assert clock.read_text() == aged, "a later pack/score moved the clock"
    out = _run(candidate, fx, "signal").stdout
    assert "window=CLOSED" in out and f"clock={aged.strip()} clock_src=CLOCK" in out, out

"""o1698-w2-l13-dupes-shadow (O1698 wave2 L13, SHADOW per O1697): near-duplicate briefs flagged by nomic cosine, log only.

The candidate scans BRIEF-*.md (plus DONE/VERDICT heads named in INSTALL-LOG/LENS-ROUTER), strips the shared
CONTRACT/RAILS/CLAIM/RETURN boilerplate, embeds through bd-search-index.py's embed() and logs pairs >= 0.85 to
state/shadow/l13/<day>.tsv. It posts nothing. `score` resolves logged pairs from the logs and says KEEP or DROP.

Harness-cut shape (O1045): opt in with BD_TEST_O1698_W2_L13=1. Candidate: BD_O1698_W2_L13_CANDIDATE.
Hermetic: the embed endpoint is a stub on 127.0.0.1 (hashed bag-of-words vectors, or HTTP 503); every root, log,
hub sample and store is under tmp_path. bd-search-index.py is imported read-only (BD_O1698_W2_L13_SEARCH_INDEX).
"""

from __future__ import annotations

import calendar
import hashlib
import json
import math
import os
import re
import stat
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import ClassVar

import pytest

BD_GATE_SCOPE = "module"

CANDIDATE = os.environ.get(
    "BD_O1698_W2_L13_CANDIDATE",
    "/home/mboyle/bd-persist/harness-work/FIX/o1698-w2-l13-dupes-shadow/bd-l13-dupes-shadow.py",
)
SEARCH_INDEX = os.environ.get(
    "BD_O1698_W2_L13_SEARCH_INDEX",
    "/home/mboyle/bd-persist/harness-work/AGY-SWARM-20261001/claude/search-index/bd-search-index.py",
)
pytestmark = pytest.mark.skipif(
    os.environ.get("BD_TEST_O1698_W2_L13") != "1",
    reason="opt-in harness cut: set BD_TEST_O1698_W2_L13=1",
)

DIM = 512
BOILER = (
    "CONTRACT: Rule 22; candidate under FIX/. Band and precut WITHOUT test-shims on PATH; band where=remote.\n"
    "  Installed worker-done check: python3 bd-worker-done.sh --check wt/DONE.md wt must PASS before the return.\n"
    "RAILS: adapter-only launches; hub gate in the launch command; LAN calls to the existing proxy only; no crontab.\n"
    "CLAIM FIRST: C=/queues/O1670-CLAIMS/row; mkdir C and echo seat to C/owner before any other step is taken.\n"
    "RETURN: bd-say bd-dispatch-D2-D seat row DONE.md path, nothing else, thirty chars plus the path only.\n"
)


def _vec(text: str) -> list[float]:
    v = [0.0] * DIM
    for tok in re.findall(r"[a-z0-9]+", text.lower()):
        v[int(hashlib.sha256(tok.encode()).hexdigest(), 16) % DIM] += 1.0
    n = math.sqrt(sum(x * x for x in v)) or 1.0
    return [x / n for x in v]


class _Stub(BaseHTTPRequestHandler):
    mode = "ok"
    calls: ClassVar[list] = []

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        type(self).calls.append(body)
        if type(self).mode == "503":
            self.send_response(503)
            self.end_headers()
            self.wfile.write(b"upstream down")
            return
        data = [{"index": i, "embedding": _vec(t)} for i, t in enumerate(body["input"])]
        out = json.dumps({"data": data}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)

    def log_message(self, *a):
        pass


@pytest.fixture
def stub():
    _Stub.mode, _Stub.calls = "ok", []
    srv = HTTPServer(("127.0.0.1", 0), _Stub)
    th = threading.Thread(target=srv.serve_forever, daemon=True)
    th.start()
    yield srv
    srv.shutdown()
    srv.server_close()


def _hub(root: Path, load1: str = "3.0", iowait: str = "0.0") -> None:
    d = root / "state" / "hub-samples"
    d.mkdir(parents=True, exist_ok=True)
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    (d / (now[:10] + ".tsv")).write_text(
        f"utc\tload1\tprocs\tblocked\tiowait_pct\tmem_pct\n{now}\t{load1}\t100\t0\t{iowait}\t20.0\n"
    )


@pytest.fixture
def root(tmp_path):
    r = tmp_path / "bd-persist"
    for d in (
        "harness-work/O1698/briefs",
        "harness-work/O1698/wave2",
        "harness-work/O1672",
    ):
        (r / d).mkdir(parents=True)
    (r / "harness-work/O1672/INSTALL-LOG-wave1.md").write_text("# install log\n")
    (r / "harness-work/O1698/LENS-ROUTER.log").write_text("")
    (r / "DISPATCH-LEDGER.tsv").write_text(
        "row\tseat\tstate\no1698-x\tbd-worker-C1\tDONE\n"
    )
    (r / "say.log").write_text(
        "2026-10-03T00:00:00Z bd-worker-C1 -> bd-dispatch-D2-D hello\n"
    )
    _hub(r)
    return r


def _brief(root: Path, row: str, body: str, where: str = "briefs") -> Path:
    p = root / "harness-work/O1698" / where / f"BRIEF-{row}.md"
    p.write_text(f"# BRIEF {row}\nQUEUE_ROW: {row}\n{body}")
    return p


def _run(
    root: Path, srv, *args: str, store: bool = True, helper: str = SEARCH_INDEX
) -> subprocess.CompletedProcess:
    env = {k: v for k, v in os.environ.items() if not k.lower().endswith("_proxy")}
    env.update(
        LC_ALL="C",
        NO_PROXY="*",
        BD_L13_ROOT=str(root),
        BD_L13_SEARCH_INDEX=helper,
        BD_SEARCH_PROXY=f"http://127.0.0.1:{srv.server_address[1]}"
        if srv
        else "http://127.0.0.1:9",
        BD_SEARCH_LIVE_RAG_DIR=str(root / "rag"),
        LITELLM_MASTER_KEY="test-only-key",
        BD_LITELLM_ENV=str(root / "no-such-env"),
    )
    env.pop("BD_SEARCH_STORE", None)
    env.pop(
        "PYTHONDONTWRITEBYTECODE", None
    )  # the candidate must not write bytecode on its own (r2 E3)
    if store:
        env["BD_SEARCH_STORE"] = str(root / "state/shadow/l13/store")
    assert os.path.isfile(CANDIDATE), f"L13-RED: no shadow tool at {CANDIDATE}"
    return subprocess.run(
        [sys.executable, CANDIDATE, *args],
        env=env,
        capture_output=True,
        check=False,
        text=True,
        timeout=60,
    )


def _pairs(root: Path) -> list[list[str]]:
    d = root / "state/shadow/l13"
    rows = []
    for f in sorted(d.glob("????-??-??.tsv")):
        rows += [ln.split("\t") for ln in f.read_text().splitlines()[1:]]
    return rows


SINCE = "2000-01-01T00:00:00Z"
TASK_A = (
    "TASK: rotate the vsphere session cookie before expiry and retry the inventory pull once with a fresh token;\n"
    "  RED when the cookie expires mid-walk and the walk returns a partial host list without saying so.\n"
)
TASK_B = (
    "TASK: teach the turn-watch loop to notice a seat whose pane title changed while its transcript stayed silent,\n"
    "  log the stall with the seat name and minutes idle, and never restart the seat itself.\n"
)


def test_a_identical_but_row_name_logs_one_pair(root, stub):
    _brief(root, "o1698-w2-alpha", TASK_A + BOILER)
    _brief(root, "o1698-w2-alpha-again", TASK_A + BOILER, where="wave2")
    r = _run(root, stub, "scan", "--since", SINCE)
    assert r.returncode == 0, r.stdout + r.stderr
    assert r.stdout.startswith("FOUND 1 "), r.stdout
    got = [(p[1], p[2]) for p in _pairs(root)]
    assert (
        sorted(got[0]) == ["o1698-w2-alpha", "o1698-w2-alpha-again"] and len(got) == 1
    ), got
    assert float(_pairs(root)[0][3]) >= 0.85
    # the stub saw the nomic group with the document prefix, and no boilerplate line reached the embedder
    assert {c["model"] for c in _Stub.calls} == {"nomic-embed-text"}
    sent = [t for c in _Stub.calls for t in c["input"]]
    assert all(t.startswith("search_document: ") for t in sent)
    assert not any(
        re.search(r"^(CONTRACT|RAILS|CLAIM FIRST|RETURN):", t, re.MULTILINE)
        for t in sent
    ), sent
    # a second run logs nothing new (pair already logged; vectors come from the store)
    n = len(_Stub.calls)
    r2 = _run(root, stub, "scan", "--since", SINCE)
    assert r2.stdout.startswith("FOUND NONE"), r2.stdout
    assert len(_Stub.calls) == n and len(_pairs(root)) == 1


def test_b_unrelated_briefs_log_nothing(root, stub):
    _brief(root, "o1698-w2-alpha", TASK_A)
    _brief(root, "o1698-w2-beta", TASK_B)
    r = _run(root, stub, "scan", "--since", SINCE)
    assert r.returncode == 0, r.stdout + r.stderr
    assert r.stdout.startswith("FOUND NONE (docs=2)"), r.stdout
    assert _pairs(root) == []


def test_c_briefs_sharing_only_boilerplate_are_filtered(root, stub):
    _brief(root, "o1698-w2-alpha", TASK_A + BOILER)
    _brief(root, "o1698-w2-beta", TASK_B + BOILER)
    r = _run(root, stub, "scan", "--since", SINCE)
    assert r.returncode == 0, r.stdout + r.stderr
    assert r.stdout.startswith("FOUND NONE (docs=2)"), r.stdout
    assert _pairs(root) == []
    # positive control: the same two briefs are a pair when the boilerplate is NOT stripped (ordinary heading lines)
    unfiltered = BOILER.replace("CONTRACT:", "Contract -").replace("RAILS:", "Rails -")
    unfiltered = unfiltered.replace("CLAIM FIRST:", "Claim -").replace(
        "RETURN:", "Return -"
    )
    _brief(root, "o1698-w2-gamma", TASK_A + unfiltered * 3)
    _brief(root, "o1698-w2-delta", TASK_B + unfiltered * 3)
    r = _run(root, stub, "scan", "--since", SINCE)
    assert [{p[1], p[2]} for p in _pairs(root)] == [
        {"o1698-w2-gamma", "o1698-w2-delta"}
    ], r.stdout


def test_d_proxy_503_is_could_not_look_exit_0(root, stub):
    _brief(root, "o1698-w2-alpha", TASK_A)
    _brief(root, "o1698-w2-alpha-again", TASK_A)
    _Stub.mode = "503"
    r = _run(root, stub, "scan", "--since", SINCE)
    assert r.returncode == 0, r.stdout + r.stderr
    assert r.stdout.startswith("COULD NOT LOOK: HTTPError"), r.stdout
    day = (
        next((root / "state/shadow/l13").glob("????-??-??.tsv"))
        .read_text()
        .splitlines()
    )
    assert len(day) == 2 and day[1].split("\t")[1] == "COULD-NOT-LOOK", day
    assert _Stub.calls, "the stub was never asked: the 503 path was not exercised"


def test_d_proxy_unreachable_is_could_not_look_exit_0(root):
    _brief(root, "o1698-w2-alpha", TASK_A)
    r = _run(root, None, "scan", "--since", SINCE)
    assert r.returncode == 0 and r.stdout.startswith("COULD NOT LOOK: "), (
        r.stdout + r.stderr
    )


def _hashes(root: Path) -> dict[str, str]:
    out = {}
    for p in sorted(root.rglob("*")):
        if p.is_file() and "state/shadow/l13" not in p.as_posix():
            out[str(p.relative_to(root))] = hashlib.sha256(p.read_bytes()).hexdigest()
    return out


def test_e_shadow_run_changes_no_brief_ledger_or_say_log(root, stub):
    _brief(root, "o1698-w2-alpha", TASK_A + BOILER)
    _brief(root, "o1698-w2-alpha-again", TASK_A + BOILER, where="wave2")
    _brief(root, "o1698-w2-beta", TASK_B + BOILER)
    (root / "harness-work/O1698/LENS-ROUTER.log").write_text(
        "2026-10-03T01:00:00Z VERDICT REFUTE o1698-w2-alpha-again r1 dup of o1698-w2-alpha -> PM\n"
    )
    before = _hashes(root)
    assert {"DISPATCH-LEDGER.tsv", "say.log"} <= set(before)
    assert _run(root, stub, "scan", "--since", SINCE).stdout.startswith("FOUND 1 ")
    assert _run(root, stub, "score").returncode == 0
    assert _hashes(root) == before
    written = {p.name for p in (root / "state/shadow/l13").iterdir()}
    assert written <= {"store", "score.tsv", "CLOCK"} | {
        p.name for p in (root / "state/shadow/l13").glob("????-??-??.tsv")
    }
    assert stat.S_IMODE(os.stat(root / "state/shadow/l13/store").st_mode) == 0o600


def test_f_store_unset_is_refused(root, stub):
    _brief(root, "o1698-w2-alpha", TASK_A)
    r = _run(root, stub, "scan", "--since", SINCE, store=False)
    assert r.returncode != 0 and "REFUSED: set BD_SEARCH_STORE" in r.stderr, (
        r.stdout + r.stderr
    )
    assert _Stub.calls == [] and not (root / "state/shadow/l13").exists()


def test_hub_gate_busy_skips_run_exit_75(root, stub):
    _brief(root, "o1698-w2-alpha", TASK_A)
    _hub(root, load1="14.0")
    r = _run(root, stub, "scan", "--since", SINCE)
    assert r.returncode == 75 and r.stdout.startswith("SKIP: hub busy"), (
        r.stdout + r.stderr
    )
    _hub(root, iowait="10.0")
    assert _run(root, stub, "scan", "--since", SINCE).returncode == 75
    assert _Stub.calls == []


def test_since_logs_only_pairs_touching_a_changed_doc(root, stub):
    a = _brief(root, "o1698-w2-alpha", TASK_A)
    b = _brief(root, "o1698-w2-alpha-again", TASK_A)
    for p in (a, b):
        os.utime(p, (1_000_000_000, 1_000_000_000))
    r = _run(root, stub, "scan", "--since", "2020-01-01T00:00:00Z")
    assert r.stdout.startswith("FOUND NONE"), r.stdout
    os.utime(b, None)
    assert _run(
        root, stub, "scan", "--since", "2020-01-01T00:00:00Z"
    ).stdout.startswith("FOUND 1 ")


def test_score_resolves_from_logs_and_keeps_or_drops(root, stub):
    out = root / "state/shadow/l13"
    out.mkdir(parents=True)
    rows = ["utc\tbrief_a\tbrief_b\tscore\tpath_a\tpath_b"]
    rows += [
        f"2026-10-03T00:00:00Z\to1698-t{i}\to1698-u{i}\t0.9\t-\t-" for i in range(6)
    ]
    rows += [
        f"2026-10-03T00:00:00Z\to1698-f{i}\to1698-g{i}\t0.9\t-\t-" for i in range(4)
    ]
    rows += [
        "2026-10-03T00:00:00Z\to1698-open\to1698-other\t0.9\t-\t-",
        "2026-10-03T00:00:01Z\tCOULD-NOT-LOOK\t-\t-\tx\t-",
    ]
    (out / "2026-10-03.tsv").write_text("\n".join(rows) + "\n")
    lr = [
        f"2026-10-03T01:00:00Z VERDICT REFUTE o1698-u{i}-bd-worker-C1 r1 duplicate of o1698-t{i} -> PM"
        for i in range(3)
    ]
    lr += [
        f"2026-10-03T01:00:00Z NOTE PM CLOSED o1698-u{i} as dup of o1698-t{i}"
        for i in range(3, 6)
    ]
    # a dup verdict naming only one side of a pair (its dup is another row) does not resolve the pair
    lr += [
        "2026-10-03T01:00:00Z VERDICT REFUTE o1698-open r1 duplicate of o1698-elsewhere -> PM"
    ]
    (root / "harness-work/O1698/LENS-ROUTER.log").write_text("\n".join(lr) + "\n")
    il = [
        f"2026-10-03T02:00:00Z CUT {i} END o1698-{x}{i} r1 INSTALLED harness/x"
        for i in range(4)
        for x in "fg"
    ]
    il += [
        "2026-10-03T02:00:00Z CUT 9 END o1698-other r1 INSTALLED harness/y"
    ]  # one side only: still open
    (root / "harness-work/O1672/INSTALL-LOG-wave1.md").write_text("\n".join(il) + "\n")
    r = _run(root, stub, "score")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "resolved=10 true=6 false=4 precision=0.60 window=CLOSED n=10" in r.stdout, (
        r.stdout
    )
    assert r.stdout.split(" -> ")[0].endswith("by=n>=10 signal=KEEP"), r.stdout
    sc = (out / "score.tsv").read_text()
    assert (
        "o1698-t0\to1698-u0\t0.9\tTRUE:REFUTE-as-dup" in sc
        and "o1698-t3\to1698-u3\t0.9\tTRUE:PM-closed-dup" in sc
    )
    assert (
        "o1698-f0\to1698-g0\t0.9\tFALSE:both-INSTALLED" in sc
        and "o1698-open\to1698-other\t0.9\topen" in sc
    )
    # one fewer TRUE: 9 resolved and no shadow run yet (no CLOCK) -> window OPEN, PENDING, not a verdict
    (root / "harness-work/O1698/LENS-ROUTER.log").write_text("\n".join(lr[1:]) + "\n")
    r = _run(root, stub, "score")
    assert (
        "resolved=9 true=5 false=4 precision=0.56 window=OPEN n=9 clock=none"
        in r.stdout
    )
    assert r.stdout.split(" -> ")[0].endswith("signal=PENDING"), r.stdout


def _window_fixture(
    root: Path, n_true: int, n_false: int, clock_age_h: float | None
) -> str:
    """n_true REFUTE-as-dup pairs + n_false both-INSTALLED pairs; CLOCK = now - clock_age_h. Returns the score line."""
    out = root / "state/shadow/l13"
    out.mkdir(parents=True, exist_ok=True)
    rows = ["utc\tbrief_a\tbrief_b\tscore\tpath_a\tpath_b"]
    rows += [
        f"2026-10-03T00:00:00Z\to1698-t{i}\to1698-u{i}\t0.9\t-\t-"
        for i in range(n_true)
    ]
    rows += [
        f"2026-10-03T00:00:00Z\to1698-f{i}\to1698-g{i}\t0.9\t-\t-"
        for i in range(n_false)
    ]
    (out / "2026-10-03.tsv").write_text("\n".join(rows) + "\n")
    lr = [
        f"2026-10-03T01:00:00Z VERDICT REFUTE o1698-u{i} r1 dup of o1698-t{i}"
        for i in range(n_true)
    ]
    (root / "harness-work/O1698/LENS-ROUTER.log").write_text("\n".join(lr) + "\n")
    il = [
        f"2026-10-03T02:00:00Z CUT END o1698-{x}{i} INSTALLED"
        for i in range(n_false)
        for x in "fg"
    ]
    (root / "harness-work/O1672/INSTALL-LOG-wave1.md").write_text("\n".join(il) + "\n")
    if clock_age_h is not None:
        t = time.gmtime(time.time() - clock_age_h * 3600)
        (out / "CLOCK").write_text(time.strftime("%Y-%m-%dT%H:%M:%SZ", t) + "\n")
    return _run(root, None, "score").stdout.split(" -> ")[0]


def test_window_closes_at_24h_with_whatever_n_and_computes_a_verdict(root):
    line = _window_fixture(root, 4, 5, 25)
    assert "resolved=9 true=4 false=5 precision=0.44 window=CLOSED n=9" in line, line
    assert line.endswith("by=age>=24h signal=DROP"), line
    line = _window_fixture(root, 5, 4, 25)
    assert "window=CLOSED n=9" in line and line.endswith("by=age>=24h signal=KEEP"), (
        line
    )


def test_window_paired_controls_10_at_2h_closed_3_at_2h_open(root):
    line = _window_fixture(root, 6, 4, 2)
    assert "window=CLOSED n=10" in line and line.endswith("by=n>=10 signal=KEEP"), line
    line = _window_fixture(root, 2, 1, 2)
    assert "resolved=3 true=2 false=1 precision=0.67 window=OPEN n=3" in line, line
    assert line.endswith("signal=PENDING"), line


def test_window_unreadable_clock_is_could_not_look(root):
    (root / "state/shadow/l13").mkdir(parents=True)
    (root / "state/shadow/l13/CLOCK").write_text("yesterday-ish\n")
    line = _window_fixture(root, 2, 1, None)
    assert "window=UNREADABLE n=3 clock=unreadable signal=COULD-NOT-LOOK" in line, line


def test_first_scan_starts_the_clock_and_later_scans_never_move_it(root, stub):
    _brief(root, "o1698-w2-alpha", TASK_A)
    clock = root / "state/shadow/l13/CLOCK"
    _hub(root, load1="20.0")
    assert _run(root, stub, "scan", "--since", SINCE).returncode == 75
    assert not clock.exists(), "a gated (skipped) run must not start the clock"
    _hub(root)
    t0 = time.time()
    assert _run(root, stub, "scan", "--since", SINCE).returncode == 0
    first = clock.read_text()
    assert re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ\n", first), first
    stamp = calendar.timegm(time.strptime(first.strip(), "%Y-%m-%dT%H:%M:%SZ"))
    assert abs(stamp - t0) < 60
    time.sleep(1.1)
    assert _run(root, stub, "scan", "--since", SINCE).returncode == 0
    assert clock.read_text() == first


def _head(
    root: Path,
    cut: str,
    header: str,
    body: str,
    name: str = ".review/VERDICT-correctness-x.md",
) -> Path:
    p = root / "cuts" / cut / name
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(header + body)
    return p


def test_r2_e1_verdict_heads_keep_their_cut_identity(root, stub):
    """r1 E1: an OBJECT-only VERDICT head fell back to '.review', so two different cuts were one row and never paired."""
    a = _head(
        root,
        "o1698-w2-a-bd-worker-C1",
        "VERDICT: BOARD\nSEAT: s; LENS: correctness\nOBJECT: o1698-w2-a-bd-worker-C1\n",
        TASK_A,
    )
    b = _head(
        root,
        "o1698-w2-b-bd-worker-D2",
        "VERDICT: REFUTE\nSEAT: s; LENS: correctness\n",
        TASK_A,
    )
    b2 = _head(
        root, "o1698-w2-b-r2-bd-worker-D2", "VERDICT: BOARD\n", TASK_A
    )  # b's own r2: same row, no pair
    _head(
        root, "o1698-w2-c-bd-worker-C3", "VERDICT: BOARD\n", TASK_A
    )  # not named in any log: never read
    (root / "harness-work/O1698/LENS-ROUTER.log").write_text(
        f"2026-10-03T01:00:00Z VERDICT BOARD o1698-w2-a {a}\n2026-10-03T01:00:01Z VERDICT REFUTE o1698-w2-b {b} {b2}\n"
    )
    r = _run(root, stub, "scan", "--since", SINCE)
    assert r.stdout.startswith("FOUND 1 (docs=3)"), r.stdout + r.stderr
    assert [{p[1], p[2]} for p in _pairs(root)] == [{"o1698-w2-a", "o1698-w2-b"}]


def test_r2_e2_dup_inside_an_identifier_is_not_a_disposition(root):
    out = root / "state/shadow/l13"
    out.mkdir(parents=True)
    rows = ["utc\tbrief_a\tbrief_b\tscore\tpath_a\tpath_b"]
    for a, b in (
        ("o1698-l13-dupes-shadow", "o1698-c13-close-packs"),
        ("o1698-n1", "o1698-n2"),
        ("o1698-m1", "o1698-m2"),
        ("o1698-k1", "o1698-k2"),
    ):
        rows.append(f"2026-10-03T00:00:00Z\t{a}\t{b}\t0.9\t-\t-")
    (out / "2026-10-03.tsv").write_text("\n".join(rows) + "\n")
    (root / "harness-work/O1698/LENS-ROUTER.log").write_text(
        "2026-10-03T01:00:00Z VERDICT REFUTE o1698-l13-dupes-shadow r1 distinct defect; compare o1698-c13-close-packs\n"
        "2026-10-03T01:00:00Z VERDICT REFUTE o1698-n1 r1 is not a duplicate of o1698-n2 (own defect)\n"
        "2026-10-03T01:00:00Z VERDICT REFUTE o1698-m1 r1 same file as o1698-dupe-guard, unlike o1698-m2\n"
        "2026-10-03T01:00:00Z VERDICT REFUTE o1698-k1-bd-worker-C1 r1 duplicate of o1698-k2 -> PM\n"
    )
    (root / "harness-work/O1672/INSTALL-LOG-wave1.md").write_text(
        "".join(
            f"2026-10-03T02:00:00Z CUT END {r} r1 INSTALLED x\n"
            for r in (
                "o1698-l13-dupes-shadow",
                "o1698-c13-close-packs",
                "o1698-k1",
                "o1698-k2",
            )
        )
    )
    r = _run(root, None, "score")
    sc = (out / "score.tsv").read_text()
    assert (
        "o1698-l13-dupes-shadow\to1698-c13-close-packs\t0.9\tFALSE:both-INSTALLED" in sc
    ), sc
    assert "o1698-n1\to1698-n2\t0.9\topen\n" in sc, sc
    assert "o1698-m1\to1698-m2\t0.9\topen\n" in sc, (
        sc
    )  # a dup word inside a third identifier
    assert "o1698-k1\to1698-k2\t0.9\topen:conflict" in sc, sc
    assert "resolved=1 true=0 false=1 precision=0.00" in r.stdout, r.stdout


def test_r2_e3_importing_the_helper_writes_no_bytecode(root, stub, tmp_path):
    helper = tmp_path / "helper" / "bd-search-index.py"
    helper.parent.mkdir()
    helper.write_bytes(Path(SEARCH_INDEX).read_bytes())
    _brief(root, "o1698-w2-alpha", TASK_A)
    r = _run(root, stub, "scan", "--since", SINCE, helper=str(helper))
    assert r.returncode == 0 and r.stdout.startswith("FOUND NONE"), r.stdout + r.stderr
    assert sorted(x.name for x in helper.parent.iterdir()) == ["bd-search-index.py"]
    assert not list(root.rglob("__pycache__"))


def test_r3_e1_real_head_header_shapes_never_merge_cuts(root, stub):
    """r2 E1: OBJECT/CUT/ROW in real heads are free text. Lens A1 r2: 4 real bh2 heads all read row 'DONE.md' (from
    'OBJECT: <cut>/DONE.md') and never paired; 'OBJECT: DONE sha ...' gave row 'DONE'. Shapes copied from those heads."""
    cuts = root / "cuts"
    body = TASK_A * 3
    heads = [
        _head(
            root,
            "bh2-05-bd-agy-sonnet-1",
            f"VERDICT: BOARD\nOBJECT: {cuts}/bh2-05-bd-agy-sonnet-1/DONE.md\n",
            body,
        ),
        _head(
            root,
            "bh2-12-bd-agy-sonnet-2",
            f"VERDICT: BOARD\nOBJECT: {cuts}/bh2-12-bd-agy-sonnet-2/DONE.md\n",
            body,
        ),
        _head(
            root,
            "bh2-21-bd-agy-worker-2",
            "VERDICT: REFUTE\nOBJECT: DONE sha 0123abcd\nROW: 825\n",
            body,
        ),
        _head(
            root,
            "bh2-35-bd-agy-sonnet-2",
            "VERDICT: PATCH\nOBJECT: DONE sha 4567ef01\n",
            body,
            name="DONE.md",
        ),
        # a lens review copy of a seatless cut (<cut>-local, e.g. o1698-w2-l13-dupes-shadow-local): the same row as bh2-05
        _head(
            root,
            "bh2-05-local",
            f"VERDICT: BOARD\nOBJECT: WT {cuts}/bh2-05-bd-agy-sonnet-1\n",
            body,
        ),
    ]
    (root / "harness-work/O1698/LENS-ROUTER.log").write_text(
        "".join(f"2026-10-03T01:00:0{i}Z LENS head {h}\n" for i, h in enumerate(heads))
    )
    r = _run(root, stub, "scan", "--since", SINCE)
    assert r.stdout.startswith("FOUND 6 (docs=5)"), r.stdout + r.stderr
    got = {frozenset((p[1], p[2])) for p in _pairs(root)}
    rows = ["bh2-05", "bh2-12", "bh2-21", "bh2-35"]
    assert got == {
        frozenset((a, b)) for i, a in enumerate(rows) for b in rows[i + 1 :]
    }, got

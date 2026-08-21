"""bd-fleet-run may never reach a network, and must never lose a host's result.

WHY, MEASURED 2026-08-15. The v3.66.1140 implementation shipped with a green
suite and a fixture that could have opened an ssh connection from GitHub CI:

    bd-fleet-run:350   logdir.mkdir(...)          <- the ONLY guard
            :364-368   submit(run_one, "alpha", "192.0.2.10", ...)
    run_one :94        "alpha" != socket.gethostname()
            :98-103    ssh -o BatchMode=yes -o ConnectTimeout=10 192.0.2.10 ...

What kept it off the network was `mkdir /proc/cannot/write/here` failing --
procfs semantics, not a code guard -- and test_toolchain_534.py runs that
selftest inside ci.yml's `toolchain` shard. The test written to catch a bypass
could not have: it asserted a LOCAL marker path stayed absent while the `touch`
it guarded would have run on the REMOTE host.

v3.66.1143 closes six further ways to lose a result or destroy something, each
pinned below: `--only` failing open on a typo and being applied after address
de-duplication; an exception anywhere in execution discarding the whole run's
record including hosts that had already executed; prune deleting the CURRENT
run, and reporting "removed" for a deletion that ignore_errors had swallowed;
`" ".join()` destroying command quoting; ssh 255 collapsing auth and
unreachable into one bucket; and the local-HEAD probe calling subprocess
directly, so execute-mode tests could not honestly claim zero real process
calls.

v3.66.1144 closes six more, found by a second review: rc=0 with no per-host
log reported `ok` (no evidence the command ran); UNKNOWN reconciliation ran
AFTER the summary was written so those rows were never persisted; a failed
summary write was a warning and exit 0; executor construction/submission were
outside the guarded region; the plan PRINTED an argv without the
commit-recording prefix while the runner received one WITH it, and the test
that compared them passed --no-record-commit, the flag that hides it;
build_command(...).strip() altered a deliberate trailing space; the address
filter was a metacharacter blacklist rather than an accepted grammar; and host
key verification failure was classified UNREACHABLE rather than as the trust
failure it is.

WHY THESE TESTS ARE DIFFERENT IN KIND. Every process launch goes through an
INJECTED seam -- `main(argv, runner=..., probe=...)`. The `guarded` fixture
below replaces `subprocess.run` inside the module under test and FAILS on any
launch at all, so "these tests cannot reach the network" is a property of the
wiring rather than of an address being unroutable.
"""

from __future__ import annotations

import ast
import hashlib
import importlib.machinery
import importlib.util
import json
import os
import pathlib
import subprocess
import tempfile
import threading
import time

import pytest

# Its subject is one tool's safety envelope. It is nevertheless pinned into a
# CI shard (see _DECLARED in test_v3_66_939): the property it guards is a
# safety boundary that must run on every PR regardless of what the diff
# touched, because the failure mode is someone reintroducing a network-capable
# fixture. Declaring it "repo-wide" to buy shard membership would be the
# dishonest answer that gate's own docstring says nothing catches.
BD_GATE_SCOPE = "module"

REPO = pathlib.Path(__file__).resolve().parent.parent
TOOL = REPO / "toolchain" / "bin" / "bd-fleet-run"

FORBIDDEN_LAUNCHERS = ("ssh", "scp", "sftp", "rsync", "bash", "sh", "ssh.exe")


def _load():
    loader = importlib.machinery.SourceFileLoader("bd_fleet_run_uut", str(TOOL))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


def _write_exact_permit(mod, path: pathlib.Path, stage="pre-fleet"):
    """Mint and first validate a synthetic permit for this exact checkout."""
    policy_path = REPO / "toolchain" / "cut_quality_policy.json"
    policy = json.loads(policy_path.read_text(encoding="utf-8"))

    def git(*args):
        result = subprocess.run(
            ["git", "-C", str(REPO), *args], capture_output=True, text=True,
            check=True,
        )
        return result.stdout.strip()

    status = git("status", "--porcelain=v1", "--untracked-files=all")
    assert not status, "the exact-permit fixture requires a clean checkout"
    head = git("rev-parse", "--verify", "HEAD^{commit}")
    tree = git("rev-parse", "--verify", "HEAD^{tree}")
    common = pathlib.Path(git("rev-parse", "--git-common-dir"))
    if not common.is_absolute():
        common = REPO / common
    submodules = subprocess.run(
        ["git", "-C", str(REPO), "submodule", "status", "--recursive"],
        capture_output=True, check=True,
    ).stdout
    runtime_rel = "toolchain/bin/bd_cut_quality.py"
    now = int(time.time())
    digest = "1" * 64
    payload = {
        "stage": stage,
        "identity": {
            "kind": "final-candidate/1", "base_sha": head,
            "candidate_sha": head, "candidate_tree": tree,
        },
        "requirements_sha256": "2" * 64,
        "contract_sha256": "3" * 64,
        "tool": dict(policy["trusted_validators"][-1]),
        "policy_sha256": hashlib.sha256(policy_path.read_bytes()).hexdigest(),
        "environment_sha256": "4" * 64,
        "source_obligations_sha256": "8" * 64,
        "floor_selection_sha256": "9" * 64,
        "delivery_sha256": "a" * 64,
        "delivery_classification": "non-runtime",
        "repository": {
            "realpath": str(REPO.resolve()),
            "git_common_dir_realpath": str(common.resolve()),
            "submodules_sha256": hashlib.sha256(submodules).hexdigest(),
        },
        "runtime_inputs": [{
            "path": runtime_rel,
            "sha256": hashlib.sha256((REPO / runtime_rel).read_bytes()).hexdigest(),
        }],
        "risk_sha256": "5" * 64,
        "audit_sha256": "6" * 64,
        "evidence_graph_root": "7" * 64,
        "artifact_hashes": {
            key: [digest] for key in
            ("red", "green", "mutation", "regeneration", "review")
        },
        "issued_at": now - 10,
        "expires_at": now + 3600,
        "invalidators": [
            "identity-change", "policy-change", "tool-trust-change",
            "environment-change", "source-obligation-change",
            "floor-selection-change", "delivery-change", "artifact-change", "expiry",
        ],
    }
    value = {
        "schema": "cut-quality-permit/1",
        "permit_id": mod.cut_quality.canonical_sha256(payload),
        "payload": payload,
    }
    path.write_text(json.dumps(value), encoding="utf-8")
    mod.cut_quality.validate_permit(REPO, path, stage)
    return path, head, tree, str(common.resolve()), submodules


@pytest.fixture()
def mod():
    return _load()


@pytest.fixture()
def guarded(monkeypatch, tmp_path):
    """Returns (mod, launches). Makes "no network" an ASSERTION, not a hope.

    Since v3.66.1143 both seams are injectable, so a correctly-wired execute
    run starts NOTHING -- not even git. Any launch is recorded, and a
    remote-capable one raises immediately.
    """
    m = _load()
    permit, candidate_head, candidate_tree, common_dir, submodules = _write_exact_permit(
        m, tmp_path / "permit.json")
    monkeypatch.setenv("BD_CUT_QUALITY_PERMIT", str(permit))
    launches = []

    def fake_run(argv, *a, **k):
        seq = list(argv) if not isinstance(argv, str) else [argv]
        # After the real permit is validated above, simulate only its exact
        # local Git probes. No process is launched, preserving this fixture's
        # central assertion while still exercising the permit consumer.
        if seq[:3] == ["git", "-C", str(REPO)]:
            tail = seq[3:]
            if tail == ["rev-parse", "--verify", "HEAD^{commit}"]:
                return subprocess.CompletedProcess(seq, 0, candidate_head, "")
            if tail == ["rev-parse", "--verify", "HEAD^{tree}"]:
                return subprocess.CompletedProcess(seq, 0, candidate_tree, "")
            if tail == ["rev-parse", "--git-common-dir"]:
                return subprocess.CompletedProcess(seq, 0, common_dir, "")
            if tail == ["submodule", "status", "--recursive"]:
                return subprocess.CompletedProcess(seq, 0, submodules, b"")
            if tail == ["status", "--porcelain=v1", "-z",
                        "--untracked-files=all"]:
                return subprocess.CompletedProcess(seq, 0, b"", b"")
            if tail[:2] == ["merge-base", "--is-ancestor"]:
                return subprocess.CompletedProcess(seq, 0, b"", b"")
            raise AssertionError(f"unexpected permit Git probe: {seq}")
        launches.append(seq)
        head = os.path.basename(str(seq[0])) if seq else ""
        if head in FORBIDDEN_LAUNCHERS:
            raise AssertionError(
                f"bd-fleet-run attempted to launch {head!r} during a test: {seq}")
        raise AssertionError(f"unexpected real subprocess during a test: {seq}")

    monkeypatch.setattr(m.subprocess, "run", fake_run)
    return m, launches


def _fleet(tmp: pathlib.Path, body: str) -> pathlib.Path:
    p = tmp / "hosts"
    p.write_text(body)
    return p


def _base(tmp: pathlib.Path) -> pathlib.Path:
    return tmp / "artifacts" / "runs"


def _seeded_run(base: pathlib.Path, name: str, mod, sentinel=True):
    d = base / name
    d.mkdir(parents=True, exist_ok=True)
    if sentinel:
        (d / mod.SENTINEL).write_text("{}")
    return d


# ------------------------------------------------------------- preconditions

def test_execute_without_permit_refuses_before_runner_or_artifacts(tmp_path):
    """The negative path is import-level, one-host, and cannot reach SSH."""
    m = _load()
    hosts = _fleet(tmp_path, "alpha 192.0.2.1\n")
    root = _base(tmp_path)

    class BombRunner:
        calls = []

        def run(self, argv, log_path, timeout):
            self.calls.append(list(argv))
            raise AssertionError("a missing permit reached the runner")

    runner = BombRunner()
    missing = tmp_path / "absent-permit.json"
    rc = m.main([
        "--hosts", str(hosts), "--root", str(root),
        "--cut-quality-permit", str(missing), "--execute", "--", "true",
    ], runner=runner, probe=m._FakeProbe())
    assert rc == 2
    assert runner.calls == []
    assert not root.exists()


def test_the_tool_exists_and_parses(mod):
    assert TOOL.is_file()
    for name in ("main", "SubprocessRunner", "SubprocessProbe", "prune",
                 "build_command", "classify", "resolve_targets"):
        assert hasattr(mod, name), f"bd-fleet-run has no {name}"


def test_only_the_two_named_seams_can_start_a_process(mod):
    """STRUCTURAL: execution is confined, so injection controls everything."""
    tree = ast.parse(TOOL.read_text(encoding="utf-8"))
    holders = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) \
                and node.value.id == "subprocess":
            for parent in ast.walk(tree):
                if isinstance(parent, (ast.FunctionDef, ast.AsyncFunctionDef)) \
                        and node in ast.walk(parent):
                    holders.add(parent.name)
    assert holders <= {"run", "local_head"}, (
        f"subprocess is reachable from {sorted(holders)}; it must be confined "
        "to SubprocessRunner.run and SubprocessProbe.local_head so the "
        "injected seams are the only execution a test has to control")


# ------------------------------------------------------------- no egress

def test_dry_run_is_the_default_and_launches_nothing(guarded):
    m, launches = guarded
    with tempfile.TemporaryDirectory() as td:
        tmp = pathlib.Path(td)
        hosts = _fleet(tmp, "alpha 10.0.0.1\nbeta 10.0.0.2\n")
        fake = m._FakeRunner()
        rc = m.main(["--hosts", str(hosts), "--root", str(_base(tmp)),
                     "--", "echo", "hi"], runner=fake, probe=m._FakeProbe())
        assert rc == 0
        assert fake.calls == [] and launches == []
        assert not _base(tmp).exists(), "a plan run created artifacts"


def test_execute_makes_no_real_process_call_at_all(guarded):
    """With both seams injected, a correct execute run starts NOTHING."""
    m, launches = guarded
    with tempfile.TemporaryDirectory() as td:
        tmp = pathlib.Path(td)
        hosts = _fleet(tmp, "alpha 10.0.0.1\nbeta 10.0.0.2\n")
        fake = m._FakeRunner()
        rc = m.main(["--hosts", str(hosts), "--root", str(_base(tmp)),
                     "--execute", "--", "echo", "hi"],
                    runner=fake, probe=m._FakeProbe())
        assert rc == 0
        assert len(fake.calls) == 4, "each host has a provenance and payload phase"
        assert launches == [], f"a real subprocess was started: {launches}"


def test_ssh_host_key_verification_is_never_disabled(mod):
    argv = mod.build_argv("alpha", "10.0.0.1", False, "echo hi")
    joined = " ".join(argv)
    assert "StrictHostKeyChecking" not in joined
    assert "BatchMode=yes" in joined and "-n" in argv


# ------------------------------------------------------------- --only

def test_a_partial_only_match_refuses_before_anything_runs(guarded):
    """FAIL CLOSED. A typo must not silently narrow the fleet."""
    m, launches = guarded
    with tempfile.TemporaryDirectory() as td:
        tmp = pathlib.Path(td)
        hosts = _fleet(tmp, "test5 10.0.0.1\ntest6 10.0.0.2\n")
        fake = m._FakeRunner()
        rc = m.main(["--hosts", str(hosts), "--root", str(_base(tmp)),
                     "--only", "test5,TYPO", "--execute", "--", "true"],
                    runner=fake, probe=m._FakeProbe())
        assert rc == 2, "a partial --only match was accepted"
        assert fake.calls == [] and launches == []
        assert not _base(tmp).exists(), "artifacts were created before refusing"


def test_only_names_the_missing_labels_and_the_known_ones(guarded, capsys):
    m, _ = guarded
    with tempfile.TemporaryDirectory() as td:
        tmp = pathlib.Path(td)
        hosts = _fleet(tmp, "test5 10.0.0.1\ntest6 10.0.0.2\n")
        m.main(["--hosts", str(hosts), "--root", str(_base(tmp)),
                "--only", "TYPO,test5", "--execute", "--", "true"],
               runner=m._FakeRunner(), probe=m._FakeProbe())
        err = capsys.readouterr().err
        assert "TYPO" in err and "test5" in err and "test6" in err


def test_only_is_applied_before_address_deduplication(guarded):
    """A SELECTED alias stays targetable.

    Before v3.66.1143 selection ran after dedup, so asking for the label that
    had been collapsed matched nothing and the run reported success over an
    empty fleet.
    """
    m, _ = guarded
    with tempfile.TemporaryDirectory() as td:
        tmp = pathlib.Path(td)
        # beta is the alias that dedup would collapse if it ran first.
        hosts = _fleet(tmp, "alpha 10.0.0.1\nbeta 10.0.0.1\n")
        fake = m._FakeRunner()
        rc = m.main(["--hosts", str(hosts), "--root", str(_base(tmp)),
                     "--only", "beta", "--execute", "--", "true"],
                    runner=fake, probe=m._FakeProbe())
        assert rc == 0, "selecting a collapsed alias failed"
        assert len(fake.calls) == 2
        run = next(d for d in _base(tmp).iterdir() if d.is_dir())
        assert (run / "beta.log").is_file(), "the selected alias was not the target"


def test_a_valid_only_selection_still_works(guarded):
    """THE OVER-SENSITIVE DIRECTION."""
    m, _ = guarded
    with tempfile.TemporaryDirectory() as td:
        tmp = pathlib.Path(td)
        hosts = _fleet(tmp, "test5 10.0.0.1\ntest6 10.0.0.2\n")
        fake = m._FakeRunner()
        rc = m.main(["--hosts", str(hosts), "--root", str(_base(tmp)),
                     "--only", "test6", "--execute", "--", "true"],
                    runner=fake, probe=m._FakeProbe())
        assert rc == 0 and len(fake.calls) == 2
        assert "10.0.0.2" in fake.calls[0]["argv"]


# ------------------------------------------------------------- result loss

def test_a_runner_exception_becomes_an_error_row_not_a_lost_run(guarded):
    """A generic exception from the runner must not discard the record."""
    m, _ = guarded

    class Exploding:
        name = "exploding"

        def run(self, argv, log_path, timeout):
            raise RuntimeError("runner defect")

    with tempfile.TemporaryDirectory() as td:
        tmp = pathlib.Path(td)
        hosts = _fleet(tmp, "alpha 10.0.0.1\n")
        rc = m.main(["--hosts", str(hosts), "--root", str(_base(tmp)),
                     "--execute", "--", "true"],
                    runner=Exploding(), probe=m._FakeProbe())
        assert rc == 1, "an exploding runner reported success"
        run = next(d for d in _base(tmp).iterdir() if d.is_dir())
        summary = json.loads((run / "summary.json").read_text())
        assert len(summary) == 1
        assert summary[0]["status"] == "ERROR"
        assert "RuntimeError" in summary[0]["error"]


def test_one_host_exploding_preserves_the_other_hosts_record(guarded):
    """The partial-fleet record is the thing that must never be lost."""
    m, _ = guarded

    class Half:
        name = "half"

        def run(self, argv, log_path, timeout):
            if "10.0.0.2" in argv:
                raise OSError("boom")
            pathlib.Path(log_path).write_text("fine\n")
            return 0, None

    with tempfile.TemporaryDirectory() as td:
        tmp = pathlib.Path(td)
        hosts = _fleet(tmp, "alpha 10.0.0.1\nbeta 10.0.0.2\n")
        rc = m.main(["--hosts", str(hosts), "--root", str(_base(tmp)),
                     "--no-record-commit", "--execute", "--", "true"],
                    runner=Half(), probe=m._FakeProbe())
        assert rc == 1
        run = next(d for d in _base(tmp).iterdir() if d.is_dir())
        summary = {r["label"]: r for r in
                   json.loads((run / "summary.json").read_text())}
        assert summary["alpha"]["status"] == "ok", "a good host's result was lost"
        assert summary["beta"]["status"] == "ERROR"


def test_rc0_with_no_log_is_an_error_not_a_success(guarded):
    """REVERSED at v3.66.1144. A runner that returns 0 without creating a log
    produced NO evidence the command ran; reporting `ok` was a false green.

    The prior revision of this test asserted rc == 0 for exactly this case, so
    the suite pinned the defect in place.
    """
    m, _ = guarded

    class NoLog:
        name = "nolog"

        def run(self, argv, log_path, timeout):
            return 0, None            # writes nothing at all

    with tempfile.TemporaryDirectory() as td:
        tmp = pathlib.Path(td)
        hosts = _fleet(tmp, "alpha 10.0.0.1\n")
        rc = m.main(["--hosts", str(hosts), "--root", str(_base(tmp)),
                     "--execute", "--", "true"],
                    runner=NoLog(), probe=m._FakeProbe())
        assert rc == 1, "rc=0 with no log reported success"
        run = next(d for d in _base(tmp).iterdir() if d.is_dir())
        summary = json.loads((run / "summary.json").read_text())
        assert summary[0]["status"] == "ERROR"
        assert "no per-host log" in summary[0]["error"]


def test_an_empty_but_existing_log_is_valid(guarded):
    """THE OVER-SENSITIVE DIRECTION. A command may legitimately print nothing;
    the test is existence and regular-file-ness, never size."""
    m, _ = guarded

    class Silent:
        name = "silent"

        def run(self, argv, log_path, timeout):
            pathlib.Path(log_path).write_text("")
            return 0, None

    with tempfile.TemporaryDirectory() as td:
        tmp = pathlib.Path(td)
        hosts = _fleet(tmp, "alpha 10.0.0.1\n")
        rc = m.main(["--hosts", str(hosts), "--root", str(_base(tmp)),
                     "--no-record-commit", "--execute", "--", "true"],
                    runner=Silent(), probe=m._FakeProbe())
        assert rc == 0, "a zero-byte existing log was treated as failure"
        run = next(d for d in _base(tmp).iterdir() if d.is_dir())
        summary = json.loads((run / "summary.json").read_text())
        assert summary[0]["status"] == "ok" and summary[0]["bytes"] == 0


def test_a_log_that_is_not_a_regular_file_is_an_error(guarded):
    """A directory where the log should be leaves no usable evidence."""
    m, _ = guarded

    class DirLog:
        name = "dirlog"

        def run(self, argv, log_path, timeout):
            pathlib.Path(log_path).mkdir()
            return 0, None

    with tempfile.TemporaryDirectory() as td:
        tmp = pathlib.Path(td)
        hosts = _fleet(tmp, "alpha 10.0.0.1\n")
        rc = m.main(["--hosts", str(hosts), "--root", str(_base(tmp)),
                     "--execute", "--", "true"],
                    runner=DirLog(), probe=m._FakeProbe())
        assert rc == 1
        run = next(d for d in _base(tmp).iterdir() if d.is_dir())
        summary = json.loads((run / "summary.json").read_text())
        assert summary[0]["status"] == "ERROR"
        assert "not a regular file" in summary[0]["error"]


def test_a_read_failure_on_the_log_is_an_error(guarded, monkeypatch):
    """stat succeeds, read raises -- directly exercised, not inferred."""
    m, _ = guarded

    class Fine:
        name = "fine"

        def run(self, argv, log_path, timeout):
            pathlib.Path(log_path).write_text("data\n")
            return 0, None

    real_read = pathlib.Path.read_text

    def boom(self, *a, **k):
        if self.name.endswith(".log"):
            raise OSError(5, "Input/output error")
        return real_read(self, *a, **k)

    monkeypatch.setattr(pathlib.Path, "read_text", boom)
    with tempfile.TemporaryDirectory() as td:
        tmp = pathlib.Path(td)
        hosts = _fleet(tmp, "alpha 10.0.0.1\n")
        rc = m.main(["--hosts", str(hosts), "--root", str(_base(tmp)),
                     "--execute", "--", "true"],
                    runner=Fine(), probe=m._FakeProbe())
        assert rc == 1, "an unreadable log reported success"


def test_summary_json_is_written_even_when_every_host_errors(guarded):
    m, _ = guarded

    class AllBad:
        name = "allbad"

        def run(self, argv, log_path, timeout):
            raise RuntimeError("nope")

    with tempfile.TemporaryDirectory() as td:
        tmp = pathlib.Path(td)
        hosts = _fleet(tmp, "alpha 10.0.0.1\nbeta 10.0.0.2\n")
        rc = m.main(["--hosts", str(hosts), "--root", str(_base(tmp)),
                     "--execute", "--", "true"],
                    runner=AllBad(), probe=m._FakeProbe())
        assert rc == 1
        run = next(d for d in _base(tmp).iterdir() if d.is_dir())
        summary = json.loads((run / "summary.json").read_text())
        assert len(summary) == 2 and all(r["status"] == "ERROR" for r in summary)


# ------------------------------------------------------------- concurrency

def test_concurrency_is_capped_by_jobs(guarded):
    """--jobs is a cap, and it must actually bound simultaneous execution."""
    m, _ = guarded
    lock = threading.Lock()
    state = {"now": 0, "peak": 0}

    class Slow:
        name = "slow"

        def run(self, argv, log_path, timeout):
            with lock:
                state["now"] += 1
                state["peak"] = max(state["peak"], state["now"])
            time.sleep(0.05)
            with lock:
                state["now"] -= 1
            pathlib.Path(log_path).write_text("x\n")
            return 0, None

    with tempfile.TemporaryDirectory() as td:
        tmp = pathlib.Path(td)
        body = "".join(f"h{i} 10.0.0.{i}\n" for i in range(1, 9))
        hosts = _fleet(tmp, body)
        rc = m.main(["--hosts", str(hosts), "--root", str(_base(tmp)),
                     "--no-record-commit", "--jobs", "2", "--execute", "--", "true"],
                    runner=Slow(), probe=m._FakeProbe())
        assert rc == 0
        assert state["peak"] <= 2, f"peak concurrency was {state['peak']}, cap was 2"
        assert state["peak"] >= 2, "the fixture never ran two at once; vacuous"


# ------------------------------------------------------------- retention

def test_prune_never_removes_the_current_run(mod):
    """--keep 1, a future-dated sibling, and a same-second sibling.

    Ordering is by NAME and the name carries a wall-clock stamp, so a
    future-dated directory or a clock rollback can sort the live run into the
    discard tail. Exclusion must be explicit.
    """
    with tempfile.TemporaryDirectory() as td:
        base = pathlib.Path(td) / "artifacts" / "runs"
        base.mkdir(parents=True)
        cur = "20260815T000000Z-11111111"
        _seeded_run(base, cur, mod)
        _seeded_run(base, "29991231T235959Z-ffffffff", mod)   # future-dated
        _seeded_run(base, "20260815T000000Z-22222222", mod)   # same second
        _seeded_run(base, "20200101T000000Z-00000000", mod)   # clock rollback

        dropped, failures = mod.prune(base, 1, protect=cur)
        assert (base / cur).is_dir(), "prune removed the CURRENT run"
        assert cur not in dropped
        assert not failures


def test_prune_reports_failures_and_never_claims_a_removal_it_did_not_make(mod):
    """No ignore_errors-then-report-removed."""
    with tempfile.TemporaryDirectory() as td:
        base = pathlib.Path(td) / "artifacts" / "runs"
        base.mkdir(parents=True)
        _seeded_run(base, "20260101T000000Z-aaaaaaaa", mod)
        doomed = _seeded_run(base, "20250101T000000Z-bbbbbbbb", mod)

        remover = mod._owned_remover_module()
        fired = {"count": 0}

        def failing(path, identity, held_fd):
            fired["count"] += 1
            assert os.fstat(held_fd).st_nlink != 0
            return False, "[unproven-removal] Permission denied"

        original = remover._remove_owned_dir
        remover._remove_owned_dir = failing
        try:
            dropped, failures = mod.prune(base, 1)
        finally:
            remover._remove_owned_dir = original

        assert fired["count"] == 1, "the object-bound failure seam did not fire"
        assert doomed.is_dir(), "fixture precondition: the doomed dir survives"
        assert doomed.name not in dropped, (
            "prune reported removing a directory that is still on disk")
        assert any(doomed.name in f for f in failures), failures


def test_prune_removes_owned_runs_and_never_touches_anything_else(mod):
    with tempfile.TemporaryDirectory() as td:
        base = pathlib.Path(td) / "artifacts" / "runs"
        base.mkdir(parents=True)
        owned = [_seeded_run(base, f"2026010{i+1}T000000Z-abcdef0{i}", mod)
                 for i in range(4)]
        unowned = _seeded_run(base, "20260101T000000Z-deadbeef", mod,
                              sentinel=False)
        foreign = base / "my-important-data"
        foreign.mkdir()
        (foreign / "keepme.txt").write_text("payload")
        stray = base / "notes.txt"
        stray.write_text("payload")

        dropped, failures = mod.prune(base, 2)

        assert not failures
        assert len(dropped) == 2, dropped
        assert not owned[0].exists() and not owned[1].exists()
        assert owned[2].is_dir() and owned[3].is_dir()
        assert unowned.is_dir(), "prune removed a directory with no sentinel"
        assert foreign.is_dir() and (foreign / "keepme.txt").read_text() == "payload"
        assert stray.is_file()


def test_prune_ignores_a_symlinked_directory(mod):
    """The symlink must be present while pruning ACTUALLY runs.

    A previous version called prune(base, 0), which returns immediately -- the
    assertion passed over a no-op and proved nothing (CLAUDE.md section 6).
    Here keep=1 with two owned runs guarantees a deletion pass happens.
    """
    with tempfile.TemporaryDirectory() as td:
        root = pathlib.Path(td)
        base = root / "artifacts" / "runs"
        base.mkdir(parents=True)
        outside = root / "precious"
        outside.mkdir()
        (outside / "data").write_text("payload")

        _seeded_run(base, "20260102T000000Z-aaaaaaaa", mod)
        _seeded_run(base, "20260101T000000Z-bbbbbbbb", mod)
        link = base / "20250101T000000Z-cccccccc"
        os.symlink(outside, link)

        dropped, failures = mod.prune(base, 1)

        assert dropped, "fixture precondition: pruning must actually have run"
        assert not failures
        assert outside.is_dir() and (outside / "data").read_text() == "payload"
        assert link.is_symlink(), "prune followed or removed a symlink"


def test_a_run_does_not_prune_itself_end_to_end(guarded):
    """The whole path, with --keep 1 and an existing older run present."""
    m, _ = guarded
    with tempfile.TemporaryDirectory() as td:
        tmp = pathlib.Path(td)
        base = _base(tmp)
        base.mkdir(parents=True)
        _seeded_run(base, "20200101T000000Z-00000000", m)
        hosts = _fleet(tmp, "alpha 10.0.0.1\n")
        rc = m.main(["--hosts", str(hosts), "--root", str(base), "--keep", "1",
                     "--execute", "--", "true"],
                    runner=m._FakeRunner(), probe=m._FakeProbe())
        assert rc == 0
        live = [d for d in base.iterdir() if d.is_dir()
                and (d / m.SENTINEL).is_file()]
        assert len(live) == 1, [d.name for d in live]
        assert (live[0] / "summary.json").is_file(), (
            "the run pruned its own artifacts")


# ------------------------------------------------------------- command semantics

@pytest.mark.parametrize("parts,expected", [
    (["hostname"], "hostname"),
    (['python3 -c "print(1)"'], 'python3 -c "print(1)"'),
    (["echo", "a b"], "echo 'a b'"),
    (["python3", "-c", "print('a b')"], "python3 -c 'print('\"'\"'a b'\"'\"')'"),
    (["sh", "-c", "echo $HOME && ls | wc -l"],
     "sh -c 'echo $HOME && ls | wc -l'"),
    (["grep", "-r", "foo bar", "."], "grep -r 'foo bar' ."),
])
def test_command_quoting_is_preserved(mod, parts, expected):
    """`" ".join()` destroyed meaning; a single arg is verbatim, many are joined."""
    assert mod.build_command(parts) == expected


def test_the_printed_effective_command_matches_what_is_sent(guarded, capsys):
    m, _ = guarded
    with tempfile.TemporaryDirectory() as td:
        tmp = pathlib.Path(td)
        hosts = _fleet(tmp, "alpha 10.0.0.1\n")
        fake = m._FakeRunner()
        m.main(["--hosts", str(hosts), "--root", str(_base(tmp)), "--execute",
                "--no-record-commit", "--", "python3", "-c", "print('a b')"],
               runner=fake, probe=m._FakeProbe())
        out = capsys.readouterr().out
        effective = "python3 -c 'print('\"'\"'a b'\"'\"')'"
        assert f"effective command: {effective}" in out

        # RECOVER the command rather than substring-matching it. argv[-1] is
        # `bash -c <shlex.quote(command)>`, so the command's own quoting is
        # re-escaped by the outer quote and the literal string is NOT present.
        # A substring assertion here fails against correct behaviour -- which is
        # exactly what it did on first run.
        import shlex as _shlex
        sent = fake.calls[0]["argv"][-1]
        recovered = _shlex.split(sent)
        assert recovered[:2] == ["bash", "-c"], recovered
        assert recovered[2] == effective, (
            f"the command that reached the host is not the printed one:\n"
            f"  printed:   {effective!r}\n  recovered: {recovered[2]!r}")

        # And the semantic property the quoting exists for: one shell word.
        assert _shlex.split(recovered[2]) == ["python3", "-c", "print('a b')"]


# ------------------------------------------------------------- ssh states

@pytest.mark.parametrize("text,expected", [
    ("Permission denied (publickey).", "AUTH_FAILURE"),
    ("user@h: Permission denied (publickey,password).", "AUTH_FAILURE"),
    ("Received disconnect: Too many authentication failures", "AUTH_FAILURE"),
    ("ssh: connect to host h port 22: Connection refused", "UNREACHABLE"),
    ("ssh: connect to host h port 22: No route to host", "UNREACHABLE"),
    ("ssh: Could not resolve hostname h", "UNREACHABLE"),
    # HOST_KEY_FAILURE since v3.66.1144: a trust failure is not a network one.
    ("Host key verification failed.", "HOST_KEY_FAILURE"),
    ("some unmatched diagnostic", "SSH_UNKNOWN"),
    ("", "SSH_UNKNOWN"),
])
def test_ssh_255_is_split_and_never_success(mod, text, expected):
    assert mod.classify(255, None, text) == expected
    assert mod.classify(255, None, text) != "ok"


def test_the_other_states_are_distinct(mod):
    assert mod.classify(0, None, "") == "ok"
    assert mod.classify(3, None, "") == "FAIL"
    assert mod.classify(None, "TIMEOUT after 5s", "") == "TIMEOUT"
    assert mod.classify(None, "OSError: boom", "") == "ERROR"
    assert mod.classify(None, None, "") == "UNKNOWN"
    # A LOCAL command exiting 255 is not an ssh diagnostic.
    assert mod.classify(255, None, "", is_local=True) == "FAIL"


def test_a_timeout_never_becomes_success(guarded):
    m, _ = guarded
    with tempfile.TemporaryDirectory() as td:
        tmp = pathlib.Path(td)
        hosts = _fleet(tmp, "alpha 10.0.0.1\n")
        rc = m.main(["--hosts", str(hosts), "--root", str(_base(tmp)),
                     "--execute", "--", "true"],
                    runner=m._FakeRunner(rc=None, err="TIMEOUT after 5s"),
                    probe=m._FakeProbe())
        assert rc == 1


def test_partial_fleet_failure_is_explicit(guarded, capsys):
    m, _ = guarded

    class Mixed:
        name = "mixed"

        def run(self, argv, log_path, timeout):
            pathlib.Path(log_path).write_text("out\n")
            return (0, None) if "10.0.0.1" in argv else (7, None)

    with tempfile.TemporaryDirectory() as td:
        tmp = pathlib.Path(td)
        hosts = _fleet(tmp, "alpha 10.0.0.1\nbeta 10.0.0.2\n")
        rc = m.main(["--hosts", str(hosts), "--root", str(_base(tmp)),
                     "--no-record-commit", "--execute", "--", "true"],
                    runner=Mixed(), probe=m._FakeProbe())
        out = capsys.readouterr().out
        assert rc == 1 and "NOT OK" in out and "beta" in out
        assert "1/2 host(s) ok" in out


# ------------------------------------------------------------- capture fidelity

def test_a_large_log_is_retained_complete(guarded):
    """Whole capture, whatever the size. The caller still sees one line."""
    m, _ = guarded
    n = 20000

    class Chatty:
        name = "chatty"

        def run(self, argv, log_path, timeout):
            pathlib.Path(log_path).write_text(
                "".join(f"line{i}\n" for i in range(1, n + 1)))
            return 0, None

    with tempfile.TemporaryDirectory() as td:
        tmp = pathlib.Path(td)
        hosts = _fleet(tmp, "alpha 10.0.0.1\n")
        rc = m.main(["--hosts", str(hosts), "--root", str(_base(tmp)),
                     "--no-record-commit", "--execute", "--", "true"],
                    runner=Chatty(), probe=m._FakeProbe())
        assert rc == 0
        run = next(d for d in _base(tmp).iterdir() if d.is_dir())
        stored = (run / "alpha.log").read_text().splitlines()
        assert len(stored) == n, f"stored {len(stored)} lines, expected {n}"
        assert stored[0] == "line1" and stored[-1] == f"line{n}"
        summary = json.loads((run / "summary.json").read_text())
        assert summary[0]["bytes"] == (run / "alpha.log").stat().st_size


# ------------------------------------------------------------- label/address

@pytest.mark.parametrize("label", [
    "/absolute", "../traversal", "a/b", "a\\b", "..", ".",
    "ctrl\x01char", "", "-leading-dash" * 8,
])
def test_unsafe_labels_refuse_with_zero_launches(guarded, label):
    m, launches = guarded
    with tempfile.TemporaryDirectory() as td:
        tmp = pathlib.Path(td)
        hosts = _fleet(tmp, f"{label} 10.0.0.1\n" if label else "  10.0.0.1\n")
        fake = m._FakeRunner()
        rc = m.main(["--hosts", str(hosts), "--root", str(_base(tmp)),
                     "--execute", "--", "true"],
                    runner=fake, probe=m._FakeProbe())
        assert rc == 2, f"label {label!r} was accepted"
        assert fake.calls == [] and launches == []


def test_whitespace_bearing_values_are_rejected_by_the_validator(mod):
    """Tested at the VALIDATOR: the inventory is whitespace-delimited, so a
    file-driven fixture cannot build this shape (CLAUDE.md section 6)."""
    for label in ("with space", "tab\there", "new\nline"):
        with pytest.raises(mod.Refusal):
            mod.resolve_targets([(label, "10.0.0.1")], {})
    for addr in ("a b", "host\tname"):
        with pytest.raises(mod.Refusal):
            mod.resolve_targets([("alpha", addr)], {})


def test_duplicate_labels_refuse(guarded):
    m, launches = guarded
    with tempfile.TemporaryDirectory() as td:
        tmp = pathlib.Path(td)
        hosts = _fleet(tmp, "alpha 10.0.0.1\nalpha 10.0.0.2\n")
        fake = m._FakeRunner()
        rc = m.main(["--hosts", str(hosts), "--root", str(_base(tmp)),
                     "--execute", "--", "true"],
                    runner=fake, probe=m._FakeProbe())
        assert rc == 2 and fake.calls == [] and launches == []


def test_duplicate_addresses_are_collapsed_not_run_twice(guarded):
    m, _ = guarded
    with tempfile.TemporaryDirectory() as td:
        tmp = pathlib.Path(td)
        hosts = _fleet(tmp, "alpha 10.0.0.1\nbeta 10.0.0.1\n")
        fake = m._FakeRunner()
        rc = m.main(["--hosts", str(hosts), "--root", str(_base(tmp)),
                     "--execute", "--", "true"],
                    runner=fake, probe=m._FakeProbe())
        assert rc == 0 and len(fake.calls) == 2


@pytest.mark.parametrize("addr", ["10.0.0.1; rm -rf /", "-oProxyCommand=x",
                                  "$(whoami)", "`id`"])
def test_unsafe_addresses_refuse(guarded, addr):
    m, launches = guarded
    with tempfile.TemporaryDirectory() as td:
        tmp = pathlib.Path(td)
        hosts = _fleet(tmp, f"alpha {addr}\n")
        fake = m._FakeRunner()
        rc = m.main(["--hosts", str(hosts), "--root", str(_base(tmp)),
                     "--execute", "--", "true"],
                    runner=fake, probe=m._FakeProbe())
        assert rc == 2 and fake.calls == [] and launches == []


# ------------------------------------------------------------- artifact roots

def test_forbidden_artifact_roots_refuse(guarded):
    m, launches = guarded
    with tempfile.TemporaryDirectory() as td:
        tmp = pathlib.Path(td)
        hosts = _fleet(tmp, "alpha 10.0.0.1\n")
        for root in ["/", str(pathlib.Path.home()), "/tmp",
                     str(tmp / ".." / "escape"), str(REPO),
                     str(REPO / "artifacts")]:
            fake = m._FakeRunner()
            rc = m.main(["--hosts", str(hosts), "--root", root, "--execute",
                         "--", "true"], runner=fake, probe=m._FakeProbe())
            assert rc == 2, f"artifact root {root!r} was accepted"
            assert fake.calls == [] and launches == []


def test_a_symlink_escape_is_refused(mod):
    with tempfile.TemporaryDirectory() as td:
        link = pathlib.Path(td) / "sneaky"
        os.symlink("/", link)
        with pytest.raises(mod.Refusal):
            mod.validate_root(str(link))


def test_a_dedicated_root_is_accepted(mod):
    with tempfile.TemporaryDirectory() as td:
        tmp = pathlib.Path(td)
        assert mod.validate_root(str(_base(tmp))) == \
            pathlib.Path(os.path.realpath(_base(tmp)))


# ------------------------------------------------------------- run ids

def test_run_ids_do_not_collide_within_the_same_second(mod):
    import datetime
    now = datetime.datetime(2026, 1, 1, tzinfo=datetime.timezone.utc)
    ids = {mod.new_run_id(now) for _ in range(200)}
    assert len(ids) == 200
    assert all(mod.RUN_RE.match(i) for i in ids)
    assert all(i.startswith("20260101T000000Z-") for i in ids)


# ------------------------------------------------------------- locality

def test_locality_comes_from_the_inventory_not_the_hostname(guarded):
    """A fleet label is not a hostname."""
    m, _ = guarded
    import socket
    me = socket.gethostname()
    with tempfile.TemporaryDirectory() as td:
        tmp = pathlib.Path(td)
        hosts = _fleet(tmp, f"{me} 10.0.0.9\nbeta 10.0.0.2 local\n")
        fake = m._FakeRunner()
        rc = m.main(["--hosts", str(hosts), "--root", str(_base(tmp)),
                     "--execute", "--", "true"],
                    runner=fake, probe=m._FakeProbe())
        assert rc == 0
        assert {c["argv"][0] for c in fake.calls} == {"ssh", "bash"}
        ssh_call = [c for c in fake.calls if c["argv"][0] == "ssh"][0]
        assert "10.0.0.9" in ssh_call["argv"], (
            "a host matching this machine's hostname ran LOCALLY with no "
            "inventory entry saying so")


def test_an_inventory_local_entry_runs_locally(guarded):
    m, _ = guarded
    with tempfile.TemporaryDirectory() as td:
        tmp = pathlib.Path(td)
        hosts = _fleet(tmp, "solo 10.0.0.1 local\n")
        fake = m._FakeRunner()
        rc = m.main(["--hosts", str(hosts), "--root", str(_base(tmp)),
                     "--execute", "--", "true"],
                    runner=fake, probe=m._FakeProbe())
        assert rc == 0 and fake.calls[0]["argv"][0] == "bash"


# ------------------------------------------------------------- refusals/artifacts

def test_every_refusal_names_a_distinct_reason(guarded):
    m, _ = guarded
    with tempfile.TemporaryDirectory() as td:
        tmp = pathlib.Path(td)
        good = _fleet(tmp, "alpha 10.0.0.1\n")
        empty = tmp / "empty"
        empty.write_text("# only a comment\n")
        cases = {
            "no command": ["--hosts", str(good)],
            "missing file": ["--hosts", str(tmp / "nope"), "--", "true"],
            "empty fleet": ["--hosts", str(empty), "--", "true"],
            "bad only": ["--hosts", str(good), "--only", "zzz", "--", "true"],
            "empty only": ["--hosts", str(good), "--only", ",", "--", "true"],
            "bad root": ["--hosts", str(good), "--root", "/", "--", "true"],
            "bad jobs": ["--hosts", str(good), "--jobs", "0", "--", "true"],
        }
        seen = {}
        for name, argv in cases.items():
            import contextlib
            import io
            fake = m._FakeRunner()
            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                rc = m.main(argv, runner=fake, probe=m._FakeProbe())
            assert rc == 2, f"{name} did not refuse (rc={rc})"
            assert fake.calls == []
            seen[name] = err.getvalue().strip()
        assert len(set(seen.values())) == len(seen), f"identical wording: {seen}"
        assert all(t.startswith("bd-fleet-run: REFUSED:") for t in seen.values())


def test_the_plan_prints_the_exact_command_and_targets(guarded, capsys):
    m, _ = guarded
    with tempfile.TemporaryDirectory() as td:
        tmp = pathlib.Path(td)
        hosts = _fleet(tmp, "alpha 10.0.0.1\nbeta 10.0.0.2 local\n")
        m.main(["--hosts", str(hosts), "--root", str(_base(tmp)),
                "--", "echo", "hello"],
               runner=m._FakeRunner(), probe=m._FakeProbe())
        out = capsys.readouterr().out
        assert "PLAN (dry-run)" in out and "echo hello" in out
        assert "alpha" in out and "10.0.0.1" in out
        assert "beta" in out and "LOCAL" in out
        assert "No host was contacted" in out


def test_artifacts_record_the_run(guarded):
    m, _ = guarded
    with tempfile.TemporaryDirectory() as td:
        tmp = pathlib.Path(td)
        hosts = _fleet(tmp, "alpha 10.0.0.1\n")
        base = _base(tmp)
        rc = m.main(["--hosts", str(hosts), "--root", str(base), "--execute",
                     "--", "echo", "hi"],
                    runner=m._FakeRunner(), probe=m._FakeProbe())
        assert rc == 0
        run = next(d for d in base.iterdir() if d.is_dir())
        assert (run / m.SENTINEL).is_file() and (run / "alpha.log").is_file()
        manifest = json.loads((run / "manifest.json").read_text())
        assert manifest["command"] == "echo hi"
        assert manifest["targets"][0]["label"] == "alpha"
        assert manifest["local_head"] == "fakehead", "the probe was not injected"
        assert manifest["workers_used"] == 1, (
            "the manifest records the configured cap rather than the workers used")
        summary = json.loads((run / "summary.json").read_text())
        assert summary[0]["status"] == "ok" and summary[0]["argv"][0] == "ssh"


def test_the_selftest_is_hermetic_and_clean(guarded):
    m, launches = guarded
    assert m.selftest() == 0
    assert launches == [], f"the selftest started a real process: {launches}"


# ==================== v3.66.1144: durability, argv identity, grammar =========

def test_an_outer_future_failure_still_records_that_host(guarded, monkeypatch):
    """future.result() raising must not lose the host or the run."""
    m, _ = guarded
    real_as_completed = m.concurrent.futures.as_completed

    class Poisoned:
        def __init__(self, inner):
            self._inner = inner

        def result(self, *a, **k):
            raise RuntimeError("future exploded")

    def poisoned_as_completed(fs, *a, **k):
        for f in real_as_completed(fs, *a, **k):
            fs_map = fs
            p = Poisoned(f)
            if isinstance(fs_map, dict):
                fs_map[p] = fs_map[f]
            yield p

    monkeypatch.setattr(m.concurrent.futures, "as_completed", poisoned_as_completed)
    with tempfile.TemporaryDirectory() as td:
        tmp = pathlib.Path(td)
        hosts = _fleet(tmp, "alpha 10.0.0.1\n")
        rc = m.main(["--hosts", str(hosts), "--root", str(_base(tmp)),
                     "--execute", "--", "true"],
                    runner=m._FakeRunner(), probe=m._FakeProbe())
        assert rc == 1
        run = next(d for d in _base(tmp).iterdir() if d.is_dir())
        summary = json.loads((run / "summary.json").read_text())
        assert len(summary) == 1
        assert summary[0]["status"] == "ERROR"
        assert "future exploded" in summary[0]["error"]


def test_a_coordinator_failure_preserves_partial_results(mod):
    """execute_plan is driven directly: a failure inside the executor region
    must still return the rows already collected, plus UNKNOWN for the rest."""
    with tempfile.TemporaryDirectory() as td:
        logdir = pathlib.Path(td)
        plan = mod.build_plan([("alpha", "10.0.0.1", False),
                               ("beta", "10.0.0.2", False)], "true", False)

        class HalfThenDie:
            name = "half"
            n = 0

            def run(self, argv, log_path, timeout):
                HalfThenDie.n += 1
                if HalfThenDie.n > 1:
                    raise KeyboardInterrupt("coordinator interrupted")
                pathlib.Path(log_path).write_text("ok\n")
                return 0, None

        rows, coord = mod.execute_plan(HalfThenDie(), plan, logdir, 30, 1)
        by = {r["label"]: r for r in rows}
        assert set(by) == {"alpha", "beta"}, "a requested target vanished"
        assert by["alpha"]["status"] == "ok", "an executed host's row was lost"
        assert by["beta"]["status"] in ("ERROR", "UNKNOWN")


def test_unknown_rows_reach_the_persisted_summary(mod):
    """Reconciliation happens BEFORE the write, so UNKNOWN is durable."""
    with tempfile.TemporaryDirectory() as td:
        logdir = pathlib.Path(td)
        plan = mod.build_plan([("alpha", "10.0.0.1", False),
                               ("beta", "10.0.0.2", False)], "true", False)

        class OnlyAlpha:
            name = "onlyalpha"

            def run(self, argv, log_path, timeout):
                if "10.0.0.2" in argv:
                    raise SystemExit(9)      # not caught as a normal exception
                pathlib.Path(log_path).write_text("ok\n")
                return 0, None

        rows, _ = mod.execute_plan(OnlyAlpha(), plan, logdir, 30, 2)
        labels = {r["label"] for r in rows}
        assert labels == {"alpha", "beta"}
        beta = [r for r in rows if r["label"] == "beta"][0]
        assert beta["status"] in ("ERROR", "UNKNOWN")
        assert beta["error"], "the reconciled row carries no reason"


def test_a_summary_write_failure_makes_the_run_nonzero(guarded, monkeypatch):
    """Losing the record of a fleet that already executed is a failure."""
    m, _ = guarded
    real_write = pathlib.Path.write_text

    def boom(self, *a, **k):
        if self.name == "summary.json":
            raise OSError(28, "No space left on device")
        return real_write(self, *a, **k)

    monkeypatch.setattr(pathlib.Path, "write_text", boom)
    with tempfile.TemporaryDirectory() as td:
        tmp = pathlib.Path(td)
        hosts = _fleet(tmp, "alpha 10.0.0.1\n")
        rc = m.main(["--hosts", str(hosts), "--root", str(_base(tmp)),
                     "--execute", "--", "true"],
                    runner=m._FakeRunner(), probe=m._FakeProbe())
        assert rc == 1, "a failed summary persist reported success"


def test_the_default_path_prints_exactly_what_it_executes(guarded, capsys):
    """NO --no-record-commit. The commit-recording prefix is on, and the
    printed argv must be byte-identical to the one handed to the runner.

    The earlier printed-vs-executed test passed --no-record-commit, which is
    precisely the flag that hides this divergence.
    """
    m, _ = guarded
    with tempfile.TemporaryDirectory() as td:
        tmp = pathlib.Path(td)
        hosts = _fleet(tmp, "alpha 10.0.0.1\n")
        fake = m._FakeRunner()
        m.main(["--hosts", str(hosts), "--root", str(_base(tmp)), "--execute",
                "--", "echo", "hi"], runner=fake, probe=m._FakeProbe())
        out = capsys.readouterr().out
        import shlex as _shlex
        sent_provenance = fake.calls[0]["argv"]
        sent_payload = fake.calls[1]["argv"]
        provenance_line = [l for l in out.splitlines()
                           if l.strip().startswith("provenance argv:")][0]
        payload_line = [l for l in out.splitlines()
                        if l.strip().startswith("payload argv:")][0]
        printed_provenance = _shlex.split(
            provenance_line.strip()[len("provenance argv:"):].strip())
        printed_payload = _shlex.split(
            payload_line.strip()[len("payload argv:"):].strip())
        assert printed_provenance == sent_provenance
        assert printed_payload == sent_payload
        assert "rev-parse" in sent_provenance[-1]
        assert "echo hi" in sent_payload[-1]


def test_a_single_command_argument_is_not_stripped_end_to_end(guarded):
    """Escaped trailing whitespace is deliberate and must reach the host."""
    m, _ = guarded
    with tempfile.TemporaryDirectory() as td:
        tmp = pathlib.Path(td)
        hosts = _fleet(tmp, "alpha 10.0.0.1\n")
        fake = m._FakeRunner()
        cmd = "printf 'x' && echo done\\ "     # trailing escaped space
        rc = m.main(["--hosts", str(hosts), "--root", str(_base(tmp)),
                     "--execute", "--no-record-commit", "--", cmd],
                    runner=fake, probe=m._FakeProbe())
        assert rc == 0
        import shlex as _shlex
        recovered = _shlex.split(fake.calls[0]["argv"][-1])
        assert recovered[2] == cmd, (
            f"the command was altered in transit: {recovered[2]!r} != {cmd!r}")
        run = next(d for d in _base(tmp).iterdir() if d.is_dir())
        assert json.loads((run / "manifest.json").read_text())["command"] == cmd


@pytest.mark.parametrize("addr", [
    "/etc/passwd", "../up", "a,b", "@", "user@", "@host", "h//x",
    # NOTE: whitespace-bearing addresses are UNREPRESENTABLE in a
    # whitespace-delimited inventory, so they are asserted at the validator
    # in test_whitespace_bearing_values_are_rejected_by_the_validator instead
    # of through a file that cannot build the shape (CLAUDE.md section 6).
    "-oProxyCommand=x", "http://h/", "h:22", "h\x01x",
    "user@@host", "[10.0.0.1]", "[notv6]",
])
def test_malformed_targets_refuse_with_zero_calls(guarded, addr):
    """An accepted GRAMMAR, not a rejected character list."""
    m, launches = guarded
    with tempfile.TemporaryDirectory() as td:
        tmp = pathlib.Path(td)
        hosts = _fleet(tmp, f"alpha {addr}\n")
        fake = m._FakeRunner()
        rc = m.main(["--hosts", str(hosts), "--root", str(_base(tmp)),
                     "--execute", "--", "true"],
                    runner=fake, probe=m._FakeProbe())
        assert rc == 2, f"target {addr!r} was accepted"
        assert fake.calls == [] and launches == []


@pytest.mark.parametrize("raw,canon", [
    ("10.0.0.1", "10.0.0.1"), ("Test6.", "test6"),
    ("host.Example.COM", "host.example.com"),
    ("2001:db8::1", "[2001:db8::1]"), ("[2001:DB8::1]", "[2001:db8::1]"),
    ("mboyle@test6", "mboyle@test6"),
])
def test_accepted_targets_normalise(mod, raw, canon):
    assert mod.normalise_address(raw) == canon


def test_normalisation_drives_deduplication(guarded):
    """`Test6.` and `test6` are one host, contacted once."""
    m, _ = guarded
    with tempfile.TemporaryDirectory() as td:
        tmp = pathlib.Path(td)
        hosts = _fleet(tmp, "a Test6.\nb test6\n")
        fake = m._FakeRunner()
        rc = m.main(["--hosts", str(hosts), "--root", str(_base(tmp)),
                     "--execute", "--", "true"],
                    runner=fake, probe=m._FakeProbe())
        assert rc == 0 and len(fake.calls) == 2, (
            "the same host did not receive exactly one provenance and payload phase")


def test_host_key_failure_is_its_own_state(mod):
    """A trust failure is not a network failure."""
    for text in ("Host key verification failed.",
                 "WARNING: REMOTE HOST IDENTIFICATION HAS CHANGED!",
                 "no matching host key type found"):
        assert mod.classify(255, None, text) == "HOST_KEY_FAILURE", text
    assert mod.classify(255, None, "No route to host") == "UNREACHABLE"
    assert mod.classify(255, None, "Permission denied (publickey).") == "AUTH_FAILURE"


def test_prune_refuses_when_the_held_identity_cannot_be_proved(mod, monkeypatch):
    """An unknown descriptor result cannot become a claimed removal."""
    with tempfile.TemporaryDirectory() as td:
        base = pathlib.Path(td) / "artifacts" / "runs"
        base.mkdir(parents=True)
        _seeded_run(base, "20260102T000000Z-aaaaaaaa", mod)
        victim = _seeded_run(base, "20250101T000000Z-bbbbbbbb", mod)

        remover = mod._owned_remover_module()
        seen = {"n": 0}

        def unknown(path, identity, held_fd):
            seen["n"] += 1
            return False, "[unproven-removal] descriptor state unknown"

        monkeypatch.setattr(remover, "_remove_owned_dir", unknown)
        dropped, failures = mod.prune(base, 1)
        assert seen["n"] == 1, "fixture precondition: removal seam must run"
        assert victim.is_dir(), "prune deleted a directory with unknown identity"
        assert victim.name not in dropped
        assert any("unknown" in f for f in failures), failures


def test_the_blind_spots_do_not_claim_the_repaired_pathname_remover(mod):
    joined = " ".join(mod.BLIND_SPOTS).upper()
    assert "RESIDUAL TOCTOU" not in joined
    assert "RMTREE WALKS" not in joined

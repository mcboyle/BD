"""bd-fleet-run may never reach a network, and that must be a property of the code.

WHY, MEASURED 2026-08-15 at v3.66.1141. The v3.66.1140 implementation shipped
with a test suite that was green and a fixture that could have opened an ssh
connection from GitHub CI:

    bd-fleet-run:350   logdir.mkdir(...)          <- the ONLY guard
            :351-353   manifest write             <- no further guard existed
            :364-368   submit(run_one, "alpha", "192.0.2.10", ...)
    run_one :94        "alpha" != socket.gethostname()
            :98-103    ssh -o BatchMode=yes -o ConnectTimeout=10 192.0.2.10 ...

The refusal that kept it off the network was `mkdir /proc/cannot/write/here`
failing -- procfs semantics, not a code guard. `tests/test_toolchain_534.py`
runs `bd-fleet-run --selftest` and sits in ci.yml's `toolchain` shard, so that
fixture executed on GitHub-hosted runners on every PR. No egress ever occurred,
because procfs held on the box and on the runners. The property was simply
never enforced by anything in the repository.

Worse, the test written to catch a bypass could not have: it asserted a LOCAL
marker path stayed absent, while the `touch` it guarded would have executed on
the REMOTE host. CLAUDE.md section 6 -- a test cannot catch what its harness
cannot distinguish.

WHAT CHANGED, AND WHY THESE TESTS ARE DIFFERENT IN KIND. Every process launch
now goes through an INJECTED runner. `main(argv, runner=...)` is the only entry
point, and nothing else in the module calls subprocess except `_local_head()`,
which runs `git rev-parse` locally. So "these tests cannot reach the network" is
a property of the wiring, not of an address being unroutable or a directory
being unwritable.

The `_egress_guard` fixture below is autouse and makes that an ASSERTION rather
than an argument: it replaces `subprocess.run` inside the module under test and
FAILS the test if anything tries to launch ssh, scp, sftp, rsync or bash. `git`
is permitted and named explicitly, because the tool records the local commit.
"""

from __future__ import annotations

import importlib.machinery
import importlib.util
import json
import os
import pathlib
import re
import stat
import subprocess
import sys
import tempfile

import pytest

# Its subject is one tool's safety envelope, not the tree.
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


@pytest.fixture()
def mod():
    return _load()


@pytest.fixture()
def guarded(monkeypatch):
    """Returns (mod, launches). Makes "no network" an ASSERTION, not a hope.

    Replaces subprocess.run inside the module under test and raises on any
    remote-capable launcher. CLAUDE.md section 0: derive reachability rather
    than assert it -- and never rely on an address being unroutable or a
    directory being unwritable, which is exactly how the v3.66.1140 fixture
    stayed off the network by accident.
    """
    m = _load()
    launches = []

    real = subprocess.run

    def fake_run(argv, *a, **k):
        launches.append(list(argv) if not isinstance(argv, str) else [argv])
        head = os.path.basename(str(argv[0])) if argv else ""
        if head in FORBIDDEN_LAUNCHERS:
            raise AssertionError(
                f"bd-fleet-run attempted to launch {head!r} during a test: {argv}")
        if head != "git":
            raise AssertionError(f"unexpected launcher {head!r}: {argv}")
        return real(argv, *a, **k)

    monkeypatch.setattr(m.subprocess, "run", fake_run)
    return m, launches


def _fleet(tmp: pathlib.Path, body: str) -> pathlib.Path:
    p = tmp / "hosts"
    p.write_text(body)
    return p


def _base(tmp: pathlib.Path) -> pathlib.Path:
    # validate_root requires a dedicated directory, not a top-level one.
    return tmp / "artifacts" / "runs"


# ------------------------------------------------------------- preconditions

def test_the_tool_exists_and_parses(mod):
    """PRECONDITION -- without it every assertion below is vacuous."""
    assert TOOL.is_file()
    assert hasattr(mod, "main") and hasattr(mod, "SubprocessRunner")


def test_the_module_starts_no_process_at_import_time(guarded):
    """Importing must not launch anything. The fixture proves it."""
    _m, launches = guarded
    assert launches == []


def test_only_one_place_in_the_module_can_start_a_process(mod):
    """STRUCTURAL: subprocess use is confined, so the injected runner is the
    single execution seam rather than one of several."""
    import ast
    tree = ast.parse(TOOL.read_text(encoding="utf-8"))
    holders = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) \
                and node.value.id == "subprocess":
            for parent in ast.walk(tree):
                if isinstance(parent, (ast.FunctionDef, ast.AsyncFunctionDef)) \
                        and node in ast.walk(parent):
                    holders.add(parent.name)
    assert holders <= {"run", "_local_head"}, (
        f"subprocess is reachable from {sorted(holders)}; execution must be "
        "confined to SubprocessRunner.run and the local-commit probe so the "
        "injected runner is the only seam a test has to control")


# ------------------------------------------------------------- no egress

def test_dry_run_is_the_default_and_launches_nothing(guarded):
    """The single most important behavior: planning contacts no host."""
    m, launches = guarded
    with tempfile.TemporaryDirectory() as td:
        tmp = pathlib.Path(td)
        hosts = _fleet(tmp, "alpha 10.0.0.1\nbeta 10.0.0.2\n")
        fake = m._FakeRunner()
        rc = m.main(["--hosts", str(hosts), "--root", str(_base(tmp)),
                     "--", "echo", "hi"], runner=fake)
        assert rc == 0
        assert fake.calls == [], "a plan run called the runner"
        assert launches == [], f"a plan run started a process: {launches}"
        assert not _base(tmp).exists(), "a plan run created artifacts"


def test_execute_calls_only_the_injected_runner(guarded):
    m, launches = guarded
    with tempfile.TemporaryDirectory() as td:
        tmp = pathlib.Path(td)
        hosts = _fleet(tmp, "alpha 10.0.0.1\nbeta 10.0.0.2\n")
        fake = m._FakeRunner()
        rc = m.main(["--hosts", str(hosts), "--root", str(_base(tmp)),
                     "--execute", "--", "echo", "hi"], runner=fake)
        assert rc == 0
        assert len(fake.calls) == 2
        # git rev-parse for the local head is the ONLY permitted real launch.
        assert all(os.path.basename(c[0]) == "git" for c in launches), launches


def test_ssh_host_key_verification_is_never_disabled(mod):
    argv = mod.build_argv("alpha", "10.0.0.1", False, "echo hi")
    joined = " ".join(argv)
    assert "StrictHostKeyChecking" not in joined, (
        "host-key verification was weakened; a fleet runner that skips it will "
        "execute the operator's commands against whatever answers on that "
        "address")
    assert "BatchMode=yes" in joined and "-n" in argv


# ------------------------------------------------------------- label validation

@pytest.mark.parametrize("label", [
    "/absolute", "../traversal", "a/b", "a\\b", "..", ".",
    "ctrl\x01char", "", "-leading-dash" * 8,
])
def test_unsafe_labels_refuse_with_zero_launches(guarded, label):
    """A label becomes a path component. Every rejection must launch nothing."""
    m, launches = guarded
    with tempfile.TemporaryDirectory() as td:
        tmp = pathlib.Path(td)
        hosts = _fleet(tmp, f"{label} 10.0.0.1\n" if label else "  10.0.0.1\n")
        fake = m._FakeRunner()
        rc = m.main(["--hosts", str(hosts), "--root", str(_base(tmp)),
                     "--execute", "--", "true"], runner=fake)
        assert rc == 2, f"label {label!r} was accepted"
        assert fake.calls == [] and launches == []


def test_duplicate_labels_refuse(guarded):
    """Two hosts sharing a label would overwrite each other's log."""
    m, launches = guarded
    with tempfile.TemporaryDirectory() as td:
        tmp = pathlib.Path(td)
        hosts = _fleet(tmp, "alpha 10.0.0.1\nalpha 10.0.0.2\n")
        fake = m._FakeRunner()
        rc = m.main(["--hosts", str(hosts), "--root", str(_base(tmp)),
                     "--execute", "--", "true"], runner=fake)
        assert rc == 2 and fake.calls == [] and launches == []


def test_duplicate_addresses_are_collapsed_not_run_twice(guarded):
    """Deduplicated, and the collapse is REPORTED rather than silent."""
    m, _ = guarded
    with tempfile.TemporaryDirectory() as td:
        tmp = pathlib.Path(td)
        hosts = _fleet(tmp, "alpha 10.0.0.1\nbeta 10.0.0.1\n")
        fake = m._FakeRunner()
        rc = m.main(["--hosts", str(hosts), "--root", str(_base(tmp)),
                     "--execute", "--", "true"], runner=fake)
        assert rc == 0
        assert len(fake.calls) == 1, "the same address was contacted twice"


def test_whitespace_bearing_values_are_rejected_by_the_validator(mod):
    """Tested at the VALIDATOR, because the file format cannot carry them.

    `with space 10.0.0.1` splits into label='with', addr='space' -- the
    inventory is whitespace-delimited, so a whitespace-bearing label is
    unrepresentable there and a file-driven fixture would be asserting against
    a shape it cannot build (CLAUDE.md section 6: assert the precondition
    before the verdict). The grammar must still reject them, because
    resolve_targets is reachable from any future caller.
    """
    for label in ("with space", "tab\there", "new\nline"):
        with pytest.raises(mod.Refusal):
            mod.resolve_targets([(label, "10.0.0.1")], {})
    for addr in ("a b", "host\tname"):
        with pytest.raises(mod.Refusal):
            mod.resolve_targets([("alpha", addr)], {})


@pytest.mark.parametrize("addr", ["10.0.0.1; rm -rf /", "-oProxyCommand=x",
                                  "$(whoami)", "`id`"])
def test_unsafe_addresses_refuse(guarded, addr):
    m, launches = guarded
    with tempfile.TemporaryDirectory() as td:
        tmp = pathlib.Path(td)
        hosts = _fleet(tmp, f"alpha {addr}\n")
        fake = m._FakeRunner()
        rc = m.main(["--hosts", str(hosts), "--root", str(_base(tmp)),
                     "--execute", "--", "true"], runner=fake)
        assert rc == 2, f"address {addr!r} was accepted"
        assert fake.calls == [] and launches == []


# ------------------------------------------------------------- artifact roots

def test_forbidden_artifact_roots_refuse(guarded):
    """/, $HOME, a git work tree, traversal, and top-level directories."""
    m, launches = guarded
    with tempfile.TemporaryDirectory() as td:
        tmp = pathlib.Path(td)
        hosts = _fleet(tmp, "alpha 10.0.0.1\n")
        candidates = [
            "/", str(pathlib.Path.home()), "/tmp",
            str(tmp / ".." / "escape"), str(REPO), str(REPO / "artifacts"),
        ]
        for root in candidates:
            fake = m._FakeRunner()
            rc = m.main(["--hosts", str(hosts), "--root", root, "--execute",
                         "--", "true"], runner=fake)
            assert rc == 2, f"artifact root {root!r} was accepted"
            assert fake.calls == [] and launches == []


def test_a_symlink_escape_is_refused(guarded):
    """realpath is what is judged, so a symlink cannot smuggle the base out."""
    m, _ = guarded
    with tempfile.TemporaryDirectory() as td:
        tmp = pathlib.Path(td)
        link = tmp / "sneaky"
        os.symlink("/", link)
        with pytest.raises(m.Refusal):
            m.validate_root(str(link))


def test_a_dedicated_root_is_accepted(guarded):
    """THE OVER-SENSITIVE DIRECTION. A gate that refuses everything is useless."""
    m, _ = guarded
    with tempfile.TemporaryDirectory() as td:
        tmp = pathlib.Path(td)
        got = m.validate_root(str(_base(tmp)))
        assert got == pathlib.Path(os.path.realpath(_base(tmp)))


# ------------------------------------------------------------- pruning

def test_prune_removes_owned_runs_and_never_touches_anything_else(mod):
    """The v3.66.1140 prune would rmtree ANY subdirectory of --root."""
    with tempfile.TemporaryDirectory() as td:
        base = pathlib.Path(td) / "artifacts" / "runs"
        base.mkdir(parents=True)
        owned = []
        for i in range(4):
            d = base / f"2026010{i+1}T000000Z-abcdef0{i}"
            d.mkdir()
            (d / mod.SENTINEL).write_text("{}")
            owned.append(d)
        # Three things prune must refuse to touch:
        unowned = base / "20260101T000000Z-deadbeef"   # right name, NO sentinel
        unowned.mkdir()
        foreign = base / "my-important-data"            # wrong name
        foreign.mkdir()
        (foreign / "keepme.txt").write_text("payload")
        stray = base / "notes.txt"
        stray.write_text("payload")

        dropped = mod.prune(base, 2)

        assert len(dropped) == 2, f"expected 2 owned runs dropped, got {dropped}"
        assert owned[0].exists() is False and owned[1].exists() is False
        assert owned[2].is_dir() and owned[3].is_dir(), "newest were not kept"
        assert unowned.is_dir(), "prune removed a directory with no sentinel"
        assert foreign.is_dir() and (foreign / "keepme.txt").read_text() == "payload"
        assert stray.is_file()


def test_prune_ignores_a_symlinked_directory(mod):
    with tempfile.TemporaryDirectory() as td:
        root = pathlib.Path(td)
        base = root / "artifacts" / "runs"
        base.mkdir(parents=True)
        outside = root / "precious"
        outside.mkdir()
        (outside / "data").write_text("payload")
        link = base / "20260101T000000Z-aaaaaaaa"
        os.symlink(outside, link)
        mod.prune(base, 0)
        assert outside.is_dir() and (outside / "data").read_text() == "payload"


# ------------------------------------------------------------- run ids

def test_run_ids_do_not_collide_within_the_same_second(mod):
    import datetime
    now = datetime.datetime(2026, 1, 1, 0, 0, 0, tzinfo=datetime.timezone.utc)
    ids = {mod.new_run_id(now) for _ in range(200)}
    assert len(ids) == 200, "run ids collided inside one second"
    for i in ids:
        assert mod.RUN_RE.match(i), f"{i} does not match the run-id grammar"
    assert all(i.startswith("20260101T000000Z-") for i in ids)


# ------------------------------------------------------------- locality

def test_locality_comes_from_the_inventory_not_the_hostname(guarded):
    """A fleet label is not a hostname.

    CLAUDE.md's front matter records bd-jobs refusing a real host because a
    LABEL had no DNS entry. Inferring locality from `label == gethostname()` is
    wrong in both directions and silently decides whether a command runs here
    or on another machine.
    """
    m, _ = guarded
    import socket
    me = socket.gethostname()
    with tempfile.TemporaryDirectory() as td:
        tmp = pathlib.Path(td)
        # This machine's own hostname, with NO local marker -> must go remote.
        hosts = _fleet(tmp, f"{me} 10.0.0.9\nbeta 10.0.0.2 local\n")
        fake = m._FakeRunner()
        rc = m.main(["--hosts", str(hosts), "--root", str(_base(tmp)),
                     "--execute", "--", "true"], runner=fake)
        assert rc == 0
        by_head = {c["argv"][0] for c in fake.calls}
        assert by_head == {"ssh", "bash"}, by_head
        ssh_call = [c for c in fake.calls if c["argv"][0] == "ssh"][0]
        assert "10.0.0.9" in ssh_call["argv"], (
            "a host matching this machine's hostname was run LOCALLY without an "
            "inventory entry saying so")


def test_an_inventory_local_entry_runs_locally(guarded):
    m, _ = guarded
    with tempfile.TemporaryDirectory() as td:
        tmp = pathlib.Path(td)
        hosts = _fleet(tmp, "solo 10.0.0.1 local\n")
        fake = m._FakeRunner()
        rc = m.main(["--hosts", str(hosts), "--root", str(_base(tmp)),
                     "--execute", "--", "true"], runner=fake)
        assert rc == 0 and fake.calls[0]["argv"][0] == "bash"


# ------------------------------------------------------------- outcomes

def test_a_timeout_is_reported_and_never_becomes_success(guarded):
    m, _ = guarded
    with tempfile.TemporaryDirectory() as td:
        tmp = pathlib.Path(td)
        hosts = _fleet(tmp, "alpha 10.0.0.1\n")
        fake = m._FakeRunner(rc=None, err="TIMEOUT after 5s")
        rc = m.main(["--hosts", str(hosts), "--root", str(_base(tmp)),
                     "--execute", "--", "true"], runner=fake)
        assert rc == 1, "a timed-out host produced overall success"


def test_partial_fleet_failure_is_explicit(guarded, capsys):
    """One bad host must make the whole run non-zero and be NAMED."""
    m, _ = guarded

    class Mixed:
        name = "mixed"

        def __init__(self):
            self.calls = []

        def run(self, argv, log_path, timeout):
            self.calls.append(argv)
            pathlib.Path(log_path).write_text("out\n")
            return (0, None) if "10.0.0.1" in argv else (7, None)

    with tempfile.TemporaryDirectory() as td:
        tmp = pathlib.Path(td)
        hosts = _fleet(tmp, "alpha 10.0.0.1\nbeta 10.0.0.2\n")
        rc = m.main(["--hosts", str(hosts), "--root", str(_base(tmp)),
                     "--execute", "--", "true"], runner=Mixed())
        out = capsys.readouterr().out
        assert rc == 1
        assert "NOT OK" in out and "beta" in out
        assert "1/2 host(s) ok" in out


def test_ssh_255_is_reported_as_unreachable_or_auth(mod):
    assert mod.classify(255, None) == "UNREACHABLE_OR_AUTH"
    assert mod.classify(0, None) == "ok"
    assert mod.classify(None, "TIMEOUT after 5s") == "TIMEOUT"
    assert mod.classify(3, None) == "FAIL"


def test_every_refusal_names_a_distinct_reason(guarded):
    """When every refusal shares exit 2, a test asserting the CODE passes when
    any of them fires -- four mutants escaped exactly that way in bd-jobs."""
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
            "bad root": ["--hosts", str(good), "--root", "/", "--", "true"],
            "bad jobs": ["--hosts", str(good), "--jobs", "0", "--", "true"],
        }
        seen = {}
        for name, argv in cases.items():
            fake = m._FakeRunner()
            import io, contextlib
            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                rc = m.main(argv, runner=fake)
            assert rc == 2, f"{name} did not refuse (rc={rc})"
            assert fake.calls == []
            seen[name] = err.getvalue().strip()
        assert len(set(seen.values())) == len(seen), (
            f"two refusals are worded identically: {seen}")
        for name, text in seen.items():
            assert text.startswith("bd-fleet-run: REFUSED:"), (name, text)


def test_the_plan_prints_the_exact_command_and_targets(guarded, capsys):
    m, _ = guarded
    with tempfile.TemporaryDirectory() as td:
        tmp = pathlib.Path(td)
        hosts = _fleet(tmp, "alpha 10.0.0.1\nbeta 10.0.0.2 local\n")
        m.main(["--hosts", str(hosts), "--root", str(_base(tmp)),
                "--", "echo", "hello"], runner=m._FakeRunner())
        out = capsys.readouterr().out
        assert "PLAN (dry-run)" in out
        assert "echo hello" in out
        assert "alpha" in out and "10.0.0.1" in out
        assert "beta" in out and "LOCAL" in out
        assert "No host was contacted" in out


def test_artifacts_record_the_run(guarded):
    """Per-host log, sentinel, manifest and summary all present."""
    m, _ = guarded
    with tempfile.TemporaryDirectory() as td:
        tmp = pathlib.Path(td)
        hosts = _fleet(tmp, "alpha 10.0.0.1\n")
        base = _base(tmp)
        rc = m.main(["--hosts", str(hosts), "--root", str(base), "--execute",
                     "--", "echo", "hi"], runner=m._FakeRunner())
        assert rc == 0
        runs = [d for d in base.iterdir() if d.is_dir()]
        assert len(runs) == 1
        run = runs[0]
        assert (run / m.SENTINEL).is_file(), "no ownership sentinel"
        assert (run / "alpha.log").is_file()
        manifest = json.loads((run / "manifest.json").read_text())
        assert manifest["command"] == "echo hi"
        assert manifest["targets"][0]["label"] == "alpha"
        assert "local_head" in manifest, "the run does not record which commit"
        summary = json.loads((run / "summary.json").read_text())
        assert summary[0]["status"] == "ok" and summary[0]["argv"][0] == "ssh"


def test_the_selftest_is_hermetic_and_clean(guarded):
    """The selftest is what CI runs; it must start nothing either."""
    m, launches = guarded
    rc = m.selftest()
    assert rc == 0, "bd-fleet-run --selftest failed"
    assert all(os.path.basename(c[0]) == "git" for c in launches), launches

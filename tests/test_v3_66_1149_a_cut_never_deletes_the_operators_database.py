"""Five blockers that all reduce to one sentence: a GATE MUST NOT MUTATE ITS SUBJECT.

MEASURED 2026-08-15 at v3.66.1148, on test5, where the deployed tree and the
agent's working tree are the same directory:

  * `bd-cut --rm-runtime-db` defaulted to **True**, RUNTIME_DB_GLOBS names
    downloader_history.db plus its -wal/-shm/-journal companions, and
    check_runtime_db() does an unconditional `os.remove` on every hit. The
    service's DB_PATH is RELATIVE and systemd's WorkingDirectory is the
    checkout, so that glob resolves to the LIVE database. An ordinary cut
    would have deleted it, by default, with no backup and no prompt. The
    function's own docstring said "Non-destructive by default"; it was not.

  * Step 0's printed remedy is `bd-footguns --check --tree <work>`, and running
    that directly still wrote a 7.5MB downloader_history.db into the caller's
    cwd -- bd-cut sandboxes the checker it launches, but the checker does not
    sandbox the delegates IT launches, and _run_insync ran run_tests.py with
    cwd=<the subject tree> and BD_INSTALL_DIR unset. db._resolve_db_path then
    falls back to a relative path resolved against cwd. So the remedy for a
    blocked cut clobbered the tree the cut was about.

  * step0_gate chdirs into its sandbox and passes the subject through
    UNCHANGED, so `bd-cut --work .` certified the sandbox rather than the tree
    the operator asked about -- a gate reporting clean over a denominator that
    structurally excludes its subject (CLAUDE.md section 0, exactly).

  * --resume-zip re-opened the mutable external archive four separate times
    (identity, extract, band's stale check, verify, summary). One identity
    check at the start does not bind the later opens: a swap between them makes
    the band test A while verify reports on B.

  * Three cleanup paths used rmtree(ignore_errors=True) and then FORGOT the
    path -- extract_and_attest's BaseException handler even unregistered the
    directory from _TEMPDIRS before knowing the removal worked, so a failed
    cleanup became an unreportable leak.

WHY THE ASSERTIONS BELOW ARE SHAPED THIS WAY:

  * The runtime-DB regression drives main() through REAL argparse. The defect
    was `default=True` on the option, so a unit test calling
    check_runtime_db(work, auto_rm=False) passes on the broken tree and proves
    nothing. CLAUDE.md section 10: test the seam, not only the components.

  * Preservation is asserted over BYTES, never over filenames. The measured
    defect overwrote an EXISTING database; a directory listing finds the same
    name before and after and reports clean.

  * Every refusal is asserted on its distinctive WORDS. bd-cut shares exit 3
    across every step-0 refusal and exit 1 across every die(), so a test
    asserting the code alone passes when any other guard fires (CLAUDE.md
    section 10: four mutants escaped exactly that way).

  * The bd-footguns isolation tests drive the REAL CLI against a synthetic
    registry, not the seed one. A minimal tree makes every seed detector skip,
    so a sentinel that survives would prove only that nothing ran -- the
    empty-denominator green CLAUDE.md section 0 is about. The synthetic
    registry guarantees a delegate executes, and a precondition assertion
    proves it did.
"""

from __future__ import annotations

import hashlib
import json
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import zipfile

import pytest

# Its subject is two tools, not the tree.
BD_GATE_SCOPE = "module"

REPO = pathlib.Path(__file__).resolve().parent.parent
BIN = REPO / "toolchain" / "bin"
BDCUT = BIN / "bd-cut"
FOOTGUNS = BIN / "bd-footguns"


def _load(path, name):
    import importlib.machinery
    import importlib.util
    loader = importlib.machinery.SourceFileLoader(name, str(path))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    mod = importlib.util.module_from_spec(spec)
    sys.path.insert(0, str(path.parent))
    try:
        loader.exec_module(mod)
    finally:
        sys.path.pop(0)
    return mod


def _load_bdcut():
    return _load(BDCUT, "bd_cut_uut_1149")


def _load_footguns():
    return _load(FOOTGUNS, "bd_footguns_uut_1149")


def _sha(p) -> str:
    return hashlib.sha256(pathlib.Path(p).read_bytes()).hexdigest()


def _tmp_snapshot():
    t = pathlib.Path(tempfile.gettempdir())
    return (set(t.glob("bdcut_*")) | set(t.glob("bdfg_*"))
            | set(t.glob("tmp*")))


def _purge(m):
    """Remove every directory the module still owns, sealed or not.

    v3.66.1150. These teardowns used `shutil.rmtree(d, ignore_errors=True)`,
    and once snapshot_archive began sealing its directory to 0500 that call
    started FAILING SILENTLY -- which is the precise defect the flag exists to
    create and the one this whole cut is about. Measured with
    KEEP_TEST_TMPDIRS=1: two unremovable bdcut_archive_* per run of this file.
    The chmod is what makes the removal possible; dropping ignore_errors is
    what would have made the failure visible.
    """
    for d in list(getattr(m, "_TEMPDIRS", [])):
        try:
            os.chmod(d, 0o700)
        except OSError:
            pass
        shutil.rmtree(d, ignore_errors=True)
        if d in m._TEMPDIRS:
            m._TEMPDIRS.remove(d)


# =========================================================================
# 1 | THE DEFAULT CUT MUST NOT DELETE A DATABASE
# =========================================================================

# The exact production payload shape: an existing file with real bytes, so the
# assertion can only be satisfied by NOT TOUCHING IT. A zero-length sentinel
# would be satisfied by a truncate-and-recreate.
_PAYLOAD = b"SQLite format 3\x00" + b"SENTINEL-PRODUCTION-ROWS" * 512


def _tree_with_databases(tmp_path, globs):
    """A work tree carrying one file per RUNTIME_DB_GLOBS entry, each with
    DISTINCT bytes so a partial deletion cannot hide behind a shared hash."""
    w = tmp_path / "work"
    (w / "bulk_downloader").mkdir(parents=True)
    (w / "bulk_downloader" / "__init__.py").write_text('__version__ = "3.66.0"\n')
    (w / "anything.py").write_text("x = 1\n")
    (w / "frontend" / "dist").mkdir(parents=True)
    before = {}
    for i, pat in enumerate(globs):
        p = w / pat
        p.write_bytes(_PAYLOAD + bytes([i]) * 97)
        before[pat] = (_sha(p), p.stat().st_size)
    return w, before


def _drive_cut(m, monkeypatch, work, out, extra=()):
    """main() with only the SLOW steps stubbed, so real argparse, real
    check_runtime_db and the real control flow all execute."""
    for name in ("precut", "regen_order", "regen_inventory", "_fe_src_touched",
                 "build_release", "band", "verify", "max_summary"):
        monkeypatch.setattr(m, name, lambda *a, **k: None)
    monkeypatch.setattr(m, "step0_gate", lambda s, **k: [])
    argv = ["--work", str(work), "--out", str(out), "--skip-fe", *extra]
    try:
        return m.main(argv), None
    except SystemExit as e:
        return e.code, e


def test_the_default_invocation_preserves_every_runtime_database(tmp_path, monkeypatch, capsys):
    """THE REGRESSION. Full driver, real argparse, bytes compared.

    A unit test on check_runtime_db(work, auto_rm=False) passes on the DEFECTIVE
    tree, because the defect is the option's default -- so the only test that
    can see it is one that goes through the parser.
    """
    m = _load_bdcut()
    work, before = _tree_with_databases(tmp_path, m.RUNTIME_DB_GLOBS)
    rc, _ = _drive_cut(m, monkeypatch, work, tmp_path / "out")
    out = capsys.readouterr()

    for pat, (sha_before, size_before) in before.items():
        p = work / pat
        assert p.exists(), (
            f"the DEFAULT cut DELETED {pat}. On test5 this glob resolves to the "
            "live service database.")
        assert _sha(p) == sha_before, f"{pat} was rewritten: {sha_before[:12]} -> {_sha(p)[:12]}"
        assert p.stat().st_size == size_before, f"{pat} changed size"

    assert rc != 0, "the default cut continued past a runtime DB instead of refusing"
    # The REASON, not the code: bd-cut's die() is exit 1 for every abort.
    blob = out.out + out.err
    assert "runtime DB artifact" in blob, blob[-800:]


def test_the_option_default_is_non_destructive(tmp_path):
    """Assert the PARSED default directly, so a mutant flipping it names itself
    rather than surfacing as some downstream behaviour change."""
    m = _load_bdcut()
    captured = {}
    import argparse
    real = argparse.ArgumentParser.parse_args

    def spy(self, args=None, namespace=None):
        ns = real(self, args, namespace)
        captured["ns"] = ns
        raise SystemExit(0)

    argparse.ArgumentParser.parse_args = spy
    try:
        with pytest.raises(SystemExit):
            m.main(["--work", str(tmp_path)])
    finally:
        argparse.ArgumentParser.parse_args = real
    assert captured["ns"].rm_runtime_db is False, (
        "--rm-runtime-db still defaults to True; an ordinary cut deletes "
        "downloader_history.db and its WAL")


def test_the_destructive_override_is_explicit_and_loud(tmp_path, monkeypatch, capsys):
    """Opting in still works -- and cannot happen quietly.

    The over-sensitive direction matters here (CLAUDE.md section 0): a fix that
    simply refused always would pass the preservation test and destroy the
    option.
    """
    m = _load_bdcut()
    work, before = _tree_with_databases(tmp_path, m.RUNTIME_DB_GLOBS)
    rc, _ = _drive_cut(m, monkeypatch, work, tmp_path / "out",
                       extra=["--rm-runtime-db"])
    out = capsys.readouterr()
    for pat in before:
        assert not (work / pat).exists(), (
            f"--rm-runtime-db was explicitly requested and {pat} survived")
    assert rc == 0, f"the explicit opt-in did not complete (rc={rc})"
    # LOUD means stderr, and it means naming what was destroyed.
    assert "DELETING RUNTIME DATABASE" in out.err.upper(), (
        "the destructive path is silent on stderr:\n" + out.err[-600:])
    for pat in before:
        assert pat in out.err, f"{pat} was deleted without being named on stderr"


def test_keep_runtime_db_still_refuses(tmp_path, monkeypatch, capsys):
    """Backward compatibility: the explicit spelling of the new default."""
    m = _load_bdcut()
    work, before = _tree_with_databases(tmp_path, m.RUNTIME_DB_GLOBS)
    rc, _ = _drive_cut(m, monkeypatch, work, tmp_path / "out",
                       extra=["--keep-runtime-db"])
    assert rc != 0
    for pat, (sha_before, _) in before.items():
        assert _sha(work / pat) == sha_before


def test_the_refusal_names_the_explicit_opt_in(tmp_path, monkeypatch, capsys):
    """A refusal without a remedy gets overridden rather than fixed."""
    m = _load_bdcut()
    work, _ = _tree_with_databases(tmp_path, m.RUNTIME_DB_GLOBS)
    _drive_cut(m, monkeypatch, work, tmp_path / "out")
    blob = capsys.readouterr().out
    assert "--rm-runtime-db" in blob, blob[-600:]


def test_the_docstring_agrees_with_the_parsed_default(tmp_path):
    """The claim that shipped the defect, asserted AGAINST THE PARSER.

    check_runtime_db's docstring read "Non-destructive by default" while the
    option defaulted to True. Asserting the prose alone cannot see that -- the
    sentence was already there and already false. So compare the two.
    """
    m = _load_bdcut()
    # NORMALISE WHITESPACE FIRST. The claim is wrapped as "Non-destructive by\n
    # default:" in the source, so the literal phrase does not occur and a naive
    # grep returns clean -- CLAUDE.md section 1's line-wrap trap, which this
    # test hit on its own first run.
    doc = " ".join((m.check_runtime_db.__doc__ or "").lower().split())
    claims_safe = "non-destructive by default" in doc

    import argparse
    captured = {}
    real = argparse.ArgumentParser.parse_args

    def spy(self, args=None, namespace=None):
        captured["ns"] = real(self, args, namespace)
        raise SystemExit(0)

    argparse.ArgumentParser.parse_args = spy
    try:
        with pytest.raises(SystemExit):
            m.main(["--work", str(tmp_path)])
    finally:
        argparse.ArgumentParser.parse_args = real

    destructive_default = captured["ns"].rm_runtime_db is True
    assert not (claims_safe and destructive_default), (
        "the docstring says 'Non-destructive by default' and the parser "
        "defaults --rm-runtime-db to True. One of them is lying, and the "
        "measured answer is that the tool deletes.")


# =========================================================================
# 2 | bd-footguns MUST SANDBOX THE DELEGATES IT LAUNCHES
# =========================================================================

_RECORDER = """#!/usr/bin/env python3
import json, os, sys
row = {"tool": os.path.basename(sys.argv[0]), "cwd": os.getcwd(),
       "install": os.environ.get("BD_INSTALL_DIR"),
       "home": os.environ.get("BD_HOME"), "argv": sys.argv[1:]}
open(os.environ["FG_REC"], "a").write(json.dumps(row) + chr(10))
# EXACTLY what db._resolve_db_path does: BD_INSTALL_DIR if set, else a relative
# path resolved against the CURRENT WORKING DIRECTORY.
base = os.environ.get("BD_INSTALL_DIR") or os.getcwd()
open(os.path.join(base, "downloader_history.db"), "wb").write(b"CLOBBERED-BY-DELEGATE")
sys.exit(0)
"""


def _footgun_tree(tmp_path, m):
    """A subject tree with a synthetic registry: every SEED footgun retired, two
    of ours active, so exactly two delegates run and the run is fast."""
    tree = tmp_path / "subject"
    (tree / "bulk_downloader").mkdir(parents=True)
    (tree / "bulk_downloader" / "__init__.py").write_text("x = 1\n")

    rec = tmp_path / "rec.jsonl"
    tool = tmp_path / "fake-delegate"
    tool.write_text(_RECORDER)
    tool.chmod(0o755)

    # The in-sync detector's harness lives IN the tree, which is why the old
    # code ran it with cwd=<tree>: that is what put the DB in the subject.
    (tree / "run_tests.py").write_text(_RECORDER)
    (tree / "tests").mkdir()
    (tree / "tests" / "test_probe.py").write_text("def test_x():\n    pass\n")

    entries = [{"id": f["id"], "status": "retired"} for f in m.SEED]
    entries += [
        {"id": "FG-PROBE-TOOL", "severity": "blocking", "status": "active",
         "rule": "synthetic", "fix": "synthetic",
         "detector": {"kind": "tool",
                      "cmd": [sys.executable, str(tool), "--tree", "{tree}"],
                      "block_on_exit": [3]}},
        {"id": "FG-PROBE-INSYNC", "severity": "blocking", "status": "active",
         "rule": "synthetic", "fix": "synthetic",
         "detector": {"kind": "insync", "test": "tests/test_probe.py"}},
    ]
    (tree / "FOOTGUNS.json").write_text(json.dumps(
        {"version": 999999, "footguns": entries}))
    return tree, rec


def _run_footguns(tree, rec, caller):
    env = dict(os.environ, FG_REC=str(rec))
    env.pop("BD_INSTALL_DIR", None)          # the measured production condition
    env.pop("BD_HOME", None)
    r = subprocess.run([sys.executable, str(FOOTGUNS), "--check", "--tree", str(tree)],
                       cwd=str(caller), capture_output=True, text=True,
                       timeout=300, env=env)
    rows = [json.loads(l) for l in rec.read_text().splitlines() if l.strip()] \
        if rec.exists() else []
    return r, rows


def test_the_real_footguns_cli_leaves_a_sentinel_database_untouched(tmp_path):
    """THE MEASURED DEFECT, driven through the real CLI, judged by BYTES.

    This is step 0's own printed remedy. An operator whose cut is blocked runs
    it by hand, from the checkout, and it wrote 7.5MB over whatever was there.
    """
    m = _load_footguns()
    tree, rec = _footgun_tree(tmp_path, m)
    caller = tmp_path / "caller"
    caller.mkdir()
    sentinel = caller / "downloader_history.db"
    sentinel.write_bytes(_PAYLOAD)
    before = _sha(sentinel)

    tree_db = tree / "downloader_history.db"
    tree_db.write_bytes(_PAYLOAD + b"IN-THE-SUBJECT-TREE")
    tree_before = _sha(tree_db)

    r, rows = _run_footguns(tree, rec, caller)

    # PRECONDITION FIRST (CLAUDE.md section 6): without a delegate having run,
    # a surviving sentinel proves only that the denominator was empty.
    assert len(rows) >= 2, (
        "no delegate executed -- the sentinel assertion below would be vacuous.\n"
        + (r.stdout + r.stderr)[-1200:])
    assert {row["tool"] for row in rows} >= {"fake-delegate", "run_tests.py"}, rows

    assert _sha(sentinel) == before, (
        "bd-footguns OVERWROTE a database in the CALLER'S cwd "
        f"({before[:12]} -> {_sha(sentinel)[:12]})")
    assert _sha(tree_db) == tree_before, (
        "bd-footguns OVERWROTE a database in the SUBJECT TREE "
        f"({tree_before[:12]} -> {_sha(tree_db)[:12]})")


def test_every_delegate_runs_with_an_owned_install_dir_and_home(tmp_path):
    """Both seams, both variables. _run_insync set BD_HOME and NOT
    BD_INSTALL_DIR, which is the one that decides where the database lands."""
    m = _load_footguns()
    tree, rec = _footgun_tree(tmp_path, m)
    caller = tmp_path / "caller"
    caller.mkdir()
    r, rows = _run_footguns(tree, rec, caller)
    assert len(rows) >= 2, (r.stdout + r.stderr)[-1200:]
    for row in rows:
        assert row["install"], f"{row['tool']} ran with BD_INSTALL_DIR unset: {row}"
        assert row["home"], f"{row['tool']} ran with BD_HOME unset: {row}"
        assert "bdfg_" in row["install"], (
            f"{row['tool']}'s BD_INSTALL_DIR is not an owned bd-footguns "
            f"sandbox: {row}")
        assert row["home"] == row["install"] or "bdfg_" in row["home"], row
        assert not os.path.realpath(row["install"]).startswith(
            os.path.realpath(str(tree))), f"the sandbox is inside the subject: {row}"
        assert not os.path.exists(row["install"]), (
            f"{row['tool']}'s sandbox leaked: {row['install']}")


def test_the_subject_reaches_every_delegate_as_an_absolute_path(tmp_path):
    """A relative --tree is resolved against whatever cwd the delegate gets, and
    the delegates now get a sandbox cwd."""
    m = _load_footguns()
    tree, rec = _footgun_tree(tmp_path, m)
    caller = tmp_path / "caller"
    caller.mkdir()
    rel = os.path.relpath(str(tree), str(caller))
    env = dict(os.environ, FG_REC=str(rec))
    env.pop("BD_INSTALL_DIR", None)
    env.pop("BD_HOME", None)
    r = subprocess.run([sys.executable, str(FOOTGUNS), "--check", "--tree", rel],
                       cwd=str(caller), capture_output=True, text=True,
                       timeout=300, env=env)
    rows = [json.loads(l) for l in rec.read_text().splitlines() if l.strip()]
    assert rows, (r.stdout + r.stderr)[-1200:]
    tool_rows = [x for x in rows if x["tool"] == "fake-delegate"]
    assert tool_rows, rows
    passed = tool_rows[0]["argv"][tool_rows[0]["argv"].index("--tree") + 1]
    assert os.path.isabs(passed), f"--tree reached the delegate relative: {passed!r}"
    assert os.path.realpath(passed) == os.path.realpath(str(tree)), passed


def test_a_failed_footguns_cleanup_is_reported_not_swallowed(tmp_path, monkeypatch, capsys):
    """rmtree(ignore_errors=True) made a failed removal indistinguishable from a
    successful one."""
    m = _load_footguns()
    tree, rec = _footgun_tree(tmp_path, m)
    monkeypatch.setenv("FG_REC", str(rec))
    real_rmtree = m.shutil.rmtree
    t = pathlib.Path(tempfile.gettempdir())
    before = set(t.glob("bdfg_*"))
    monkeypatch.setattr(
        m.shutil, "rmtree",
        lambda *a, **k: (_ for _ in ()).throw(OSError(13, "Permission denied")))
    try:
        m._run_insync("tests/test_probe.py", str(tree))
        err = capsys.readouterr().err
    finally:
        monkeypatch.undo()
        # THIS TEST DELIBERATELY BREAKS CLEANUP, so it owns the residue it
        # created. Without this the file is not leak-free when run alone --
        # and being leak-free alone is one of the properties under test.
        for d in set(t.glob("bdfg_*")) - before:
            real_rmtree(d, ignore_errors=True)
    assert "NOT REMOVED" in err.upper(), (
        "a failed sandbox removal was silent:\n" + err[-600:])


# =========================================================================
# 3 | THE GATE SUBJECT MUST BE ABSOLUTE BEFORE THE CHECKER LAUNCHES
# =========================================================================

_SUBJECT_RECORDER = """#!/usr/bin/env python3
import json, os, sys
open(os.environ["BD_STEP0_REC"], "a").write(json.dumps(
    {"tool": os.path.basename(sys.argv[0]), "argv": sys.argv[1:],
     "cwd": os.getcwd()}) + chr(10))
sys.exit(0)
"""


def _recording_checkers(tmp_path, m, rec):
    b = tmp_path / "bin"
    b.mkdir(exist_ok=True)
    for name in m.STEP0_CHECKERS:
        p = b / name
        p.write_text(_SUBJECT_RECORDER)
        p.chmod(0o755)
    os.environ["BD_STEP0_REC"] = str(rec)
    return b


def test_a_relative_subject_certifies_the_intended_tree(tmp_path, monkeypatch):
    """THE DEFECT: step0_gate chdirs into its sandbox, so `--work .` reached the
    checker as '.' and resolved to the SANDBOX -- a gate certifying an empty
    directory it had just created, and reporting clean."""
    m = _load_bdcut()
    rec = tmp_path / "subj.jsonl"
    b = _recording_checkers(tmp_path, m, rec)
    intended = tmp_path / "intended"
    (intended / "bulk_downloader").mkdir(parents=True)
    (intended / "bulk_downloader" / "__init__.py").write_text("x = 1\n")

    monkeypatch.chdir(intended)
    assert m.step0_gate(".", checker_dir=str(b)) == []

    rows = [json.loads(l) for l in rec.read_text().splitlines() if l.strip()]
    assert len(rows) == len(m.STEP0_CHECKERS), rows
    for row in rows:
        passed = row["argv"][row["argv"].index("--tree") + 1]
        assert os.path.isabs(passed), f"the subject reached the checker relative: {passed!r}"
        assert os.path.realpath(passed) == os.path.realpath(str(intended)), (
            f"the gate certified {passed!r}, not the intended tree")
        assert "bdcut_gate_" not in passed, (
            "the gate certified its own sandbox -- section 0's denominator failure")


def test_the_gate_refuses_a_subject_that_does_not_resolve(tmp_path):
    """UNKNOWN is a third state and it fails. A subject that is not a directory
    cannot be certified, and must not be silently handed to a checker that will
    resolve it against a sandbox."""
    m = _load_bdcut()
    rec = tmp_path / "subj.jsonl"
    b = _recording_checkers(tmp_path, m, rec)
    refusals = m.step0_gate(str(tmp_path / "no-such-tree"), checker_dir=str(b))
    assert refusals, "a nonexistent subject was certified"
    assert any("subject" in r for r in refusals), refusals
    assert not rec.exists(), "a checker was launched against an unresolvable subject"


def test_a_file_is_not_a_subject(tmp_path):
    m = _load_bdcut()
    rec = tmp_path / "subj.jsonl"
    b = _recording_checkers(tmp_path, m, rec)
    f = tmp_path / "not-a-tree.py"
    f.write_text("x = 1\n")
    refusals = m.step0_gate(str(f), checker_dir=str(b))
    assert refusals and any("subject" in r for r in refusals), refusals


def test_the_driver_passes_an_absolute_work_to_the_gate(tmp_path, monkeypatch):
    """End to end through main(): `--work .` must certify the tree the operator
    is standing in."""
    m = _load_bdcut()
    seen = {}
    monkeypatch.setattr(m, "step0_gate",
                        lambda s, **k: seen.setdefault("subject", s) and [])
    for name in ("precut", "regen_order", "regen_inventory", "_fe_src_touched",
                 "build_release", "band", "verify", "max_summary"):
        monkeypatch.setattr(m, name, lambda *a, **k: None)
    work, _ = _tree_with_databases(tmp_path, [])
    monkeypatch.chdir(work)
    m.main(["--work", ".", "--out", str(tmp_path / "out"), "--skip-fe"])
    subject = seen.get("subject")
    assert subject is not None
    # ABSOLUTE is the property under test. Comparing realpaths alone passes on
    # the defective tree, because realpath('.') resolves against the CALLER's
    # cwd -- and the caller is standing in the tree. Only the checker, which
    # runs after a chdir into the sandbox, sees the difference.
    assert os.path.isabs(subject), (
        f"the gate was handed a RELATIVE subject ({subject!r}); it chdirs into "
        "its sandbox before launching, so this resolves to the sandbox")
    assert os.path.realpath(subject) == os.path.realpath(str(work)), subject


# =========================================================================
# 4 | ONE OWNED, IMMUTABLE ARCHIVE SNAPSHOT
# =========================================================================

def _zip_with(path, marker):
    with zipfile.ZipFile(path, "w") as zf:
        for i in range(6):
            zf.writestr(f"pkg/mod{i}.py", f"MARKER = {marker!r}\nVALUE = {i}\n" * 20)
        zf.writestr("run_tests.py", f"print({marker!r})\n")
    return path


def test_the_archive_is_snapshotted_into_an_owned_copy(tmp_path):
    m = _load_bdcut()
    src = _zip_with(tmp_path / "r.zip", "A")
    snap, ident, _fd = m.snapshot_archive(str(src))
    try:
        assert os.path.realpath(snap) != os.path.realpath(str(src))
        assert pathlib.Path(snap).read_bytes() == src.read_bytes()
        assert ident["sha256"] == _sha(src)
        assert ident["size"] == src.stat().st_size
        assert any(os.path.realpath(snap).startswith(os.path.realpath(d) + os.sep)
                   for d in m._TEMPDIRS), (
            "the snapshot is not inside a directory registered for cleanup")
    finally:
        _purge(m)


def test_the_snapshot_is_not_writable(tmp_path):
    """`immutable` is the whole point: nothing downstream, and nothing outside,
    may edit the object the band and verify agree on."""
    m = _load_bdcut()
    src = _zip_with(tmp_path / "r.zip", "A")
    snap, _, _fd = m.snapshot_archive(str(src))
    try:
        assert not (os.stat(snap).st_mode & 0o222), oct(os.stat(snap).st_mode)
    finally:
        _purge(m)


def _drive_resume(m, monkeypatch, tmp_path, src, on_band=None, on_verify=None):
    """--resume-zip through main(), recording what each consumer actually READ.

    The bytes are read AFTER the injected swap, never before: reading them on
    entry would make every assertion below trivially true and the test would
    pass on the defective tree. That is exactly how the first draft of this
    fixture passed on pristine source.
    """
    seen = {}
    work = tmp_path / "work"
    (work / "bulk_downloader").mkdir(parents=True, exist_ok=True)
    (work / "bulk_downloader" / "__init__.py").write_text('__version__ = "3.66.0"\n')
    (work / "anything.py").write_text("x = 1\n")

    def _band(zippath, suites, w, extracted=None):
        seen["band"] = zippath
        seen["extracted"] = extracted
        if on_band:
            on_band()
        seen["band_bytes"] = pathlib.Path(zippath).read_bytes()

    def _verify(w, z, pass_fds=()):
        seen["verify"] = z
        if on_verify:
            on_verify()
        seen["verify_bytes"] = pathlib.Path(z).read_bytes()

    monkeypatch.setattr(m, "step0_gate", lambda s, **k: [])
    monkeypatch.setattr(m, "band", _band)
    monkeypatch.setattr(m, "verify", _verify)
    monkeypatch.setattr(m, "max_summary", lambda z, b: seen.update(
        summary=z, summary_bytes=pathlib.Path(z).read_bytes()))
    rc = m.main(["--work", str(work), "--out", str(tmp_path / "o"),
                 "--resume-zip", str(src)])
    return rc, seen


def test_extraction_band_verify_and_summary_all_consume_one_owned_snapshot(tmp_path, monkeypatch):
    m = _load_bdcut()
    src = _zip_with(tmp_path / "r.zip", "A")
    rc, seen = _drive_resume(m, monkeypatch, tmp_path, src)
    assert rc == 0, rc
    for stage in ("band", "verify", "summary"):
        assert stage in seen, f"{stage} never ran"
        assert os.path.realpath(seen[stage]) != os.path.realpath(str(src)), (
            f"{stage} consumed the MUTABLE external archive, not the snapshot")
    assert seen["band"] == seen["verify"] == seen["summary"], (
        "the three consumers did not agree on one object: " + repr(seen))


def test_a_swap_during_the_band_cannot_change_what_verify_consumes(tmp_path, monkeypatch):
    """SWAP REGRESSION. The external archive is replaced while the band runs."""
    m = _load_bdcut()
    src = _zip_with(tmp_path / "r.zip", "A")
    original = src.read_bytes()

    def swap():
        _zip_with(src, "B-THE-REPLACEMENT")

    rc, seen = _drive_resume(m, monkeypatch, tmp_path, src, on_band=swap)
    assert seen["band_bytes"] == original, (
        "the object the band was handed became the REPLACEMENT mid-run -- the "
        "band's own subject is not stable")
    # And the run must not report success over an artifact that moved: the
    # operator ships the EXTERNAL file, so a changed source is a refusal.
    assert rc == 3, f"a mid-run swap of the operator's archive was not refused (rc={rc})"


def test_a_swap_during_verify_cannot_change_what_the_summary_reports(tmp_path, monkeypatch, capsys):
    """SWAP-DURING-VERIFY. The window between verify() and max_summary() was the
    last unbound one: both re-opened the mutable external path.

    THE REFUSAL IS ASSERTED HERE TOO (v3.66.1150). The first version of this
    test checked only that the summary read the snapshot's bytes -- which the
    snapshot guarantees STRUCTURALLY, so the assertion could not fail once the
    snapshot existed. Deleting the post-summary _source_moved check left it
    green. The bytes and the verdict are two different claims and both need
    saying; and the reason is asserted, not just the code, because bd-cut
    answers 3 for every step-0 refusal.
    """
    m = _load_bdcut()
    src = _zip_with(tmp_path / "r.zip", "A")
    original = src.read_bytes()

    rc, seen = _drive_resume(m, monkeypatch, tmp_path, src,
                             on_verify=lambda: _zip_with(src, "B-DURING-VERIFY"))
    err = capsys.readouterr().err
    assert "verify" in seen, "verify never ran, so this proves nothing"
    assert seen["verify_bytes"] == original, (
        "verify's own subject was replaced underneath it")
    assert "summary" in seen, "max_summary never ran"
    assert seen["summary_bytes"] == original, (
        "the MAX summary described the REPLACEMENT archive while the band and "
        "verify had judged the original")
    assert rc == 3, (
        f"the operator's archive changed during verify and the run returned "
        f"{rc}. The verdict describes the snapshot; the file they ship is now "
        "something else.")
    assert "during verify/summary" in err, (
        "the refusal does not name the window the archive moved in:\n"
        + err[-500:])


def test_an_aba_swap_with_identical_size_and_mtime_cannot_fool_the_run(tmp_path, monkeypatch):
    """ABA REGRESSION. The replacement is padded to the SAME size and its mtime
    is restored, so any stat-only identity check reports 'unchanged'."""
    m = _load_bdcut()
    src = _zip_with(tmp_path / "r.zip", "A")
    original = src.read_bytes()
    st = src.stat()

    def aba():
        _zip_with(src, "B")
        b = src.read_bytes()
        if len(b) < len(original):
            b += b"\x00" * (len(original) - len(b))
        else:
            b = b[:len(original)]
        src.write_bytes(b)
        os.utime(src, ns=(st.st_atime_ns, st.st_mtime_ns))
        assert src.stat().st_size == st.st_size
        assert src.stat().st_mtime_ns == st.st_mtime_ns
        assert src.read_bytes() != original      # only the CONTENT differs

    rc, seen = _drive_resume(m, monkeypatch, tmp_path, src, on_band=aba)
    assert seen["band_bytes"] == original, (
        "the band's subject became the ABA replacement -- size and mtime were "
        "identical, so only owning the bytes could have prevented it")
    assert rc == 3, (
        "an ABA swap that preserved size and mtime was not detected -- the "
        "identity check is reading stat, not content")


def test_the_snapshot_is_removed_on_every_exit_path(tmp_path, monkeypatch):
    m = _load_bdcut()
    src = _zip_with(tmp_path / "r.zip", "A")
    before = _tmp_snapshot()
    rc, _ = _drive_resume(m, monkeypatch, tmp_path, src)
    leaked = _tmp_snapshot() - before
    assert not leaked, sorted(str(p) for p in leaked)


def test_a_snapshot_that_cannot_be_made_blocks_rather_than_falling_back(tmp_path, monkeypatch):
    """Failing to snapshot must not degrade into using the external path."""
    m = _load_bdcut()
    src = _zip_with(tmp_path / "r.zip", "A")
    monkeypatch.setattr(m, "snapshot_archive",
                        lambda p: (_ for _ in ()).throw(OSError(28, "No space left")))
    seen = {}
    monkeypatch.setattr(m, "step0_gate", lambda s, **k: [])
    monkeypatch.setattr(m, "band", lambda *a, **k: seen.update(band=1))
    monkeypatch.setattr(m, "verify", lambda *a, **k: seen.update(verify=1))
    monkeypatch.setattr(m, "max_summary", lambda *a, **k: seen.update(summary=1))
    work = tmp_path / "work"
    (work / "bulk_downloader").mkdir(parents=True)
    (work / "bulk_downloader" / "__init__.py").write_text('__version__ = "3.66.0"\n')
    rc = m.main(["--work", str(work), "--out", str(tmp_path / "o"),
                 "--resume-zip", str(src)])
    assert rc == 3, rc
    assert not seen, f"the run continued on the external archive: {seen}"


# =========================================================================
# 5 | A CLEANUP THAT DID NOT HAPPEN IS NEVER SILENT
# =========================================================================

def test_a_failed_subject_cleanup_stays_registered_and_preserves_the_error(tmp_path, monkeypatch):
    """extract_and_attest unregistered the directory from _TEMPDIRS BEFORE
    knowing the removal worked, so a failed cleanup became unreportable."""
    m = _load_bdcut()
    src = _zip_with(tmp_path / "r.zip", "A")
    real_rmtree = m.shutil.rmtree
    monkeypatch.setattr(
        m.shutil, "rmtree",
        lambda *a, **k: (_ for _ in ()).throw(OSError(13, "Permission denied")))

    def boom(self, path=None, *a, **k):
        raise RuntimeError("extraction exploded")

    monkeypatch.setattr(zipfile.ZipFile, "extractall", boom)
    before = list(m._TEMPDIRS)
    with pytest.raises(RuntimeError, match="extraction exploded"):
        m.extract_and_attest(str(src))
    added = [d for d in m._TEMPDIRS if d not in before]
    assert added, (
        "a directory whose cleanup FAILED was unregistered -- main()'s finally "
        "can no longer see it, so the leak is unreportable")
    monkeypatch.undo()
    for d in added:
        real_rmtree(d, ignore_errors=True)
        if d in m._TEMPDIRS:
            m._TEMPDIRS.remove(d)


def test_a_failed_gate_sandbox_cleanup_is_reported(tmp_path, monkeypatch, capsys):
    m = _load_bdcut()
    rec = tmp_path / "subj.jsonl"
    b = _recording_checkers(tmp_path, m, rec)
    work = tmp_path / "w"
    work.mkdir()
    real_rmtree = m.shutil.rmtree
    monkeypatch.setattr(
        m.shutil, "rmtree",
        lambda *a, **k: (_ for _ in ()).throw(OSError(13, "Permission denied")))
    m.step0_gate(str(work), checker_dir=str(b))
    err = capsys.readouterr().err
    monkeypatch.undo()
    assert "NOT REMOVED" in err.upper(), (
        "the gate sandbox removal failed silently:\n" + err[-600:])
    for d in list(m._TEMPDIRS):
        real_rmtree(d, ignore_errors=True)
        m._TEMPDIRS.remove(d)


def test_the_gate_sandbox_is_owned_so_the_driver_can_report_it(tmp_path, monkeypatch):
    """A bare mkdtemp is invisible to main()'s finally. Ownership is what makes
    a failed removal reportable AT ALL."""
    m = _load_bdcut()
    rec = tmp_path / "subj.jsonl"
    b = _recording_checkers(tmp_path, m, rec)
    work = tmp_path / "w"
    work.mkdir()
    seen = []
    real = m._owned_tempdir

    def spy(prefix):
        d = real(prefix)
        seen.append(prefix)
        return d

    monkeypatch.setattr(m, "_owned_tempdir", spy)
    m.step0_gate(str(work), checker_dir=str(b))
    assert any(p.startswith("bdcut_gate_") for p in seen), (
        f"the gate sandbox did not go through _owned_tempdir: {seen}")


def test_no_temporary_directory_survives_the_whole_resume_path(tmp_path, monkeypatch):
    """Includes the DEFAULT-prefix family: two of the three leaks this cut's
    predecessor found used /tmp/tmp*, which a bdcut_* glob cannot see."""
    m = _load_bdcut()
    src = _zip_with(tmp_path / "r.zip", "A")
    before = _tmp_snapshot()
    _drive_resume(m, monkeypatch, tmp_path, src)
    leaked = _tmp_snapshot() - before
    assert not leaked, sorted(str(p) for p in leaked)

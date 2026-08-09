"""A container restart must stay visible after the hook re-records.

@969, item 17. `bd-restart-check` has three states and its selftest drives all
three, so the tool was called complete -- what remained was "purely empirical:
does a mid-session container restart fire SessionStart". That framing was
wrong, and the register's proposed fix (move the state somewhere a restart
preserves) addressed the wrong mechanism.

MEASURED at v3.66.968 in this container: uptime 6 minutes, `$HOME/.bd_boot_state`
written at the boot minute with `source=resume`, its boot id EQUAL to the
current one, and `bd-restart-check` returning `OK, exit 0`. The hook writes the
record with a truncating redirect and no comparison, so the sequence

    container restarts -> session resumes -> SessionStart fires
                       -> hook overwrites the baseline with the NEW boot id
                       -> bd-restart-check compares new-against-new -> OK

destroys the very evidence the tool exists to read. The tool's own comment says
the mid-session read is "the only moment the reading is unambiguous"; correct,
and a resume fires the hook immediately, so for the case the item cares about
that moment has zero width.

The fix preserves the PRIOR boot id when it differs, so the transition is
readable after the fact. Note which assertion carries the weight: the hook
WRITING the field is the load-bearing one. A field declared by the reader and
never written by the writer is a recorded failure in this repo (a residue
report's `blind_pages` said no page went blind while the verdict beside it read
`conclusive: false`), so the reader-side test alone would certify nothing.
"""

import os
import pathlib
import subprocess
import sys

REPO = pathlib.Path(__file__).resolve().parent.parent
HOOK = REPO / ".claude" / "hooks" / "session-start.sh"
TOOL = REPO / "toolchain" / "bin" / "bd-restart-check"
BOOT_ID = pathlib.Path("/proc/sys/kernel/random/boot_id")

_OTHER_BOOT = "00000000-1111-2222-3333-444444444444"


def _current_boot():
    try:
        return BOOT_ID.read_text().strip()
    except OSError:
        return ""


def _run_hook(tmp_path, seed_state):
    """Run the REAL hook against a throwaway clone with a fake HOME.

    seed_state is the text to plant as the pre-existing record, or None.
    Returns the state file's lines after the hook has run.
    """
    sys.path.insert(0, str(REPO / "tests"))
    from test_v3_66_879_provision_trigger_sees_its_subject import _origin_and_clone
    _origin, clone = _origin_and_clone(tmp_path)
    fake_home = tmp_path / "home"
    fake_home.mkdir(exist_ok=True)
    state = fake_home / ".bd_boot_state"
    if seed_state is not None:
        state.write_text(seed_state, encoding="utf-8")
    env = dict(os.environ)
    env["CLAUDE_CODE_REMOTE"] = "true"
    env["CLAUDE_PROJECT_DIR"] = str(clone)
    env["HOME"] = str(fake_home)
    env.pop("CLAUDE_ENV_FILE", None)
    r = subprocess.run(["bash", str(HOOK)], cwd=str(clone),
                       input='{"source":"resume"}', capture_output=True,
                       text=True, timeout=300, env=env)
    assert r.returncode == 0, "the hook failed: %s" % r.stderr[-800:]
    assert state.exists(), "the hook wrote no record at all"
    return state.read_text().splitlines()


def test_the_hook_PRESERVES_a_boot_id_it_is_about_to_overwrite(tmp_path):
    """THE LOAD-BEARING ASSERTION. Without this the transition is unrecoverable.

    A resume after a restart is the exact case: the prior record names a boot
    that no longer exists, and the hook is about to replace it. If it does not
    carry that value forward, no later reader can tell a restarted container
    from one that never moved.
    """
    cur = _current_boot()
    assert cur, "BD-GATE-UNRUNNABLE: no boot_id on this kernel"
    lines = _run_hook(tmp_path, "%s\n2020-01-01T00:00:00Z\nstartup\n" % _OTHER_BOOT)
    assert lines[0] == cur, (
        "the hook did not record the CURRENT boot: %r" % lines)
    joined = "\n".join(lines)
    assert _OTHER_BOOT in joined, (
        "the hook overwrote a DIFFERENT prior boot id (%s) without preserving "
        "it, so the restart is now undetectable -- which is exactly what a "
        "resume does to the evidence bd-restart-check exists to read. "
        "record=%r" % (_OTHER_BOOT, lines))


def test_an_UNCHANGED_boot_records_no_transition(tmp_path):
    """The over-sensitive direction, asserted in the same cut.

    Green before the fix and required to stay green after it: a change that
    recorded a transition unconditionally would satisfy the test above while
    making every ordinary session look like a restarted one. Section 0 counts
    that as a soundness bug equal to a false clean.
    """
    cur = _current_boot()
    assert cur, "BD-GATE-UNRUNNABLE: no boot_id on this kernel"
    lines = _run_hook(tmp_path, "%s\n2020-01-01T00:00:00Z\nstartup\n" % cur)
    assert lines[0] == cur, "the hook did not record the current boot: %r" % lines
    assert _OTHER_BOOT not in "\n".join(lines)
    assert len(lines) <= 3, (
        "the boot did NOT change, yet the hook recorded transition fields %r "
        "-- an ordinary session must not read as a restarted container"
        % lines[3:])


def test_a_FIRST_run_records_no_transition(tmp_path):
    """No prior record is not a restart. It is the absence of evidence."""
    cur = _current_boot()
    assert cur, "BD-GATE-UNRUNNABLE: no boot_id on this kernel"
    lines = _run_hook(tmp_path, None)
    assert lines[0] == cur
    assert len(lines) <= 3, (
        "a first run with no prior record invented transition fields %r"
        % lines[3:])


def test_bd_restart_check_SURFACES_the_preserved_transition():
    """The reader half: a preserved transition must reach the operator.

    Kept separate from the writer test on purpose -- these are the two halves
    that a declared-but-never-written field lets drift apart.
    """
    import importlib.machinery
    import importlib.util
    spec = importlib.util.spec_from_loader(
        "bdrc", importlib.machinery.SourceFileLoader("bdrc", str(TOOL)))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    rec = {"boot_id": "abc", "when": "w", "source": "resume",
           "prev_boot_id": _OTHER_BOOT, "prev_when": "2020-01-01T00:00:00Z"}
    v = mod.classify("abc", rec)
    assert v["exit"] == mod.EXIT_OK, (
        "a transition the hook already handled must NOT be reported as a live "
        "unrepaired restart -- the hook ran, so the repair path fired. got %r" % v)
    assert _OTHER_BOOT in v["detail"] or "restart" in v["detail"].lower(), (
        "the tool read a preserved transition and said nothing about it, so "
        "the field is written and never surfaced: %r" % v["detail"])


def test_bd_restart_check_claims_no_restart_when_there_was_none():
    """Over-sensitivity, reader side."""
    import importlib.machinery
    import importlib.util
    spec = importlib.util.spec_from_loader(
        "bdrc2", importlib.machinery.SourceFileLoader("bdrc2", str(TOOL)))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    v = mod.classify("abc", {"boot_id": "abc", "when": "w", "source": "s"})
    assert v["exit"] == mod.EXIT_OK
    assert "restart" not in v["detail"].lower(), (
        "no transition was recorded, yet the tool mentioned a restart: %r"
        % v["detail"])


def test_the_tool_reads_a_preserved_transition_from_a_REAL_FILE(tmp_path):
    """Closes the seam between the two halves above.

    The writer test reads the state file's lines directly; the reader test hands
    `classify()` a dict it built itself. `recorded()`'s parsing of lines 3 and 4
    sits BETWEEN them with nothing driving it, so a mutation that dropped those
    two keys would escape both while breaking the feature completely. This runs
    the whole chain in one process boundary: file -> recorded -> classify ->
    stdout -> exit code.

    Found by asking what the two tests share rather than what each covers, which
    is the question a per-test reading never asks.
    """
    cur = _current_boot()
    assert cur, "BD-GATE-UNRUNNABLE: no boot_id on this kernel"
    state = tmp_path / "state"
    state.write_text(
        "%s\n2026-01-01T00:00:00Z\nresume\n%s\n2025-12-31T00:00:00Z\n"
        % (cur, _OTHER_BOOT), encoding="utf-8")
    r = subprocess.run([sys.executable, str(TOOL), "--state", str(state)],
                       capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, (
        "a transition the hook already handled must exit 0, not %d: %s"
        % (r.returncode, r.stdout + r.stderr))
    assert _OTHER_BOOT in r.stdout, (
        "the preserved boot id never reached stdout, so recorded() is not "
        "parsing it: %r" % r.stdout)


def test_an_OLD_three_line_record_still_parses(tmp_path):
    """Backward compatibility, and it is not hypothetical.

    Every session that started before @969 left a 3-line record. A reader that
    raised or mis-parsed on the short form would turn the first run after this
    landed into an UNEVALUABLE, which reads identically to "the hook never ran".
    """
    cur = _current_boot()
    assert cur, "BD-GATE-UNRUNNABLE: no boot_id on this kernel"
    state = tmp_path / "state"
    state.write_text("%s\n2026-01-01T00:00:00Z\nstartup\n" % cur, encoding="utf-8")
    r = subprocess.run([sys.executable, str(TOOL), "--state", str(state)],
                       capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, (
        "a pre-@969 three-line record did not parse: %s" % (r.stdout + r.stderr))
    assert "RESTART was observed" not in r.stdout, (
        "a record with no transition fields claimed a transition: %r" % r.stdout)

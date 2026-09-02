"""ROW 444 -- the hook injection defense must not be POSIX quoting on cmd.exe.

``run_command_hook`` renders its template with ``shell_quote=True`` and runs the
result with ``shell=True``.  The only quoting is ``shlex.quote``, which emits
POSIX single-quoting.  On Windows ``shell=True`` is cmd.exe, where the single
quote is ORDINARY DATA: ``&``, ``|``, ``^`` and ``%VAR%`` split and expand
straight through it.  So the Phase 41.7 comment's claim -- that a downloaded
filename cannot break out -- held on POSIX and was false on the platform the
docstring committed to, making a website-influenced filename a capture-time
arbitrary-command-execution vector on a Windows operator host.

The Linux fleet cannot run cmd.exe, so this module models its tokenisation and
proves the model faithful with a known-answer battery before using it as an
oracle.  A model that fails its battery FAILS this lane (UNKNOWN is a failing
state, CLAUDE.md A7); it never skips or passes by default.

No shell is spawned at all: ``subprocess.run`` is stubbed inside the hooks
namespace on BOTH platform paths, so the Windows tests assert it is never
reached and the POSIX controls inspect the command line that would have
run without executing it.
"""
from __future__ import annotations

import ast
import importlib
import subprocess
import types
from pathlib import Path

import pytest

BD_GATE_SCOPE = "module"

hooks = importlib.import_module("bulk_downloader.hooks")

REPO = Path(__file__).resolve().parents[1]

# The injected program name is a marker, not a real command; nothing in this
# module ever executes it.
INJECTED = "row444-injected-command"
# The row's own shape: a literal ampersand in a website-influenced filename.
ADVERSARIAL_FILENAME = f"clip.mp4 & {INJECTED} & tail.mp4"
# The classic POSIX break-out the Phase 41.7 comment names verbatim.
POSIX_BREAKOUT_FILENAME = "bad.mp4'; rm -rf /home/; '.mp4"
TEMPLATE = "ffprobe {filename}"


# ── a cmd.exe tokenisation model ────────────────────────────────────────

_CMD_OPERATORS = ("&&", "||", ">>", "&", "|", ">", "<")


def cmd_exe_segments(line: str) -> list[str]:
    """Split ``line`` the way cmd.exe splits a command line into commands.

    cmd.exe's rules, which are NOT POSIX's:
      * the double quote is the only quote character; it toggles quote mode;
      * the single quote is ordinary data and quotes nothing;
      * ``^`` outside quote mode escapes the next character;
      * ``& && | || > >> <`` separate commands / redirect at top level.
    """
    segments: list[str] = []
    current: list[str] = []
    in_quotes = False
    i = 0
    while i < len(line):
        ch = line[i]
        if ch == '"':
            in_quotes = not in_quotes
            current.append(ch)
            i += 1
            continue
        if ch == "^" and not in_quotes:
            # The caret escapes the next character (and is consumed).
            if i + 1 < len(line):
                current.append(line[i + 1])
                i += 2
            else:
                i += 1
            continue
        if not in_quotes:
            for op in _CMD_OPERATORS:
                if line.startswith(op, i):
                    segments.append("".join(current))
                    current = []
                    i += len(op)
                    break
            else:
                current.append(ch)
                i += 1
            continue
        current.append(ch)
        i += 1
    segments.append("".join(current))
    return segments


def cmd_exe_would_expand_var(line: str) -> bool:
    """cmd.exe expands ``%NAME%`` everywhere, including inside double quotes."""
    depth = 0
    for idx, ch in enumerate(line):
        if ch == "%":
            rest = line[idx + 1:]
            close = rest.find("%")
            if close > 0 and rest[:close].replace("_", "").isalnum():
                depth += 1
    return depth > 0


def injected_invocations(line: str, program: str) -> int:
    """How many cmd.exe command segments would invoke ``program``."""
    count = 0
    for seg in cmd_exe_segments(line):
        tokens = seg.strip().split()
        if tokens and tokens[0].strip('"') == program:
            count += 1
    return count


# ── the model must prove itself before it is used as an oracle ──────────

_CMD_MODEL_BATTERY = [
    ("a & b", 2, "a bare ampersand separates two commands"),
    ("a && b", 2, "&& separates two commands"),
    ("a | b", 2, "a pipe separates two commands"),
    ("a > f", 2, "redirection ends the command"),
    ('a "x & y" b', 1, "cmd.exe DOUBLE quotes protect the ampersand"),
    ("a ^& b", 1, "the caret escapes the ampersand"),
    ("a 'x & y' b", 2,
     "SINGLE quotes protect nothing in cmd.exe -- the crux of row 444"),
    ("plain command", 1, "no operator means one command"),
]


def test_row444_cmd_exe_model_is_faithful_before_it_is_trusted():
    """The oracle's own denominator.  If this battery fails, every verdict
    below is UNKNOWN, so it fails the lane rather than passing by default."""
    assert _CMD_MODEL_BATTERY, "empty model battery is a zero denominator"
    for line, expected, why in _CMD_MODEL_BATTERY:
        got = len(cmd_exe_segments(line))
        assert got == expected, (
            f"cmd.exe model is not faithful: {line!r} -> {got} segment(s), "
            f"expected {expected} ({why})")
    assert cmd_exe_would_expand_var('echo "%PATH%"'), (
        "model must know cmd.exe expands %VAR% inside double quotes")
    assert not cmd_exe_would_expand_var("echo 'plain text'")


# ── preconditions that hold on parent and fixed tree alike ──────────────

def test_row444_precondition_shell_quoting_actually_fires():
    """shell_quote=True is not a no-op: it changes the rendered string, and
    what it emits is POSIX single-quoting."""
    vars = {"filename": ADVERSARIAL_FILENAME}
    raw = hooks._render_template(TEMPLATE, vars, shell_quote=False)
    quoted = hooks._render_template(TEMPLATE, vars, shell_quote=True)
    assert raw != quoted, "shell_quote=True did not change the rendering"
    assert quoted == "ffprobe 'clip.mp4 & row444-injected-command & tail.mp4'", (
        f"unexpected POSIX rendering: {quoted!r}")
    assert "'" in quoted, "the defense under test is POSIX single-quoting"


def test_row444_mutation_catcher_posix_quoting_is_defeated_by_cmd_exe():
    """The oracle is not vacuous: the OLD behaviour, replayed exactly, DOES
    hand cmd.exe an injected command.  Without this the RED below could be
    green for want of an attack rather than for want of a defect."""
    rendered = hooks._render_template(
        TEMPLATE, {"filename": ADVERSARIAL_FILENAME}, shell_quote=True)
    fired = injected_invocations(rendered, INJECTED)
    assert fired == 1, (
        "the adversarial filename does not defeat POSIX quoting under the "
        f"cmd.exe model ({fired} invocation(s)); the RED assertion would be "
        "vacuous")
    assert len(cmd_exe_segments(rendered)) == 3, (
        "expected cmd.exe to split the single-quoted value into three "
        f"commands, got {cmd_exe_segments(rendered)!r}")


# ── the Windows verdict ─────────────────────────────────────────────────

class _NtOs:
    """``os`` with ``name == 'nt'``; everything else delegates to the real
    module so nothing else in hooks changes behaviour."""

    name = "nt"

    def __getattr__(self, item):
        import os as _os
        return getattr(_os, item)


@pytest.fixture
def nt_hooks(monkeypatch):
    """Put hooks on the Windows branch and make any shell launch observable.

    ``subprocess.run`` is replaced inside the hooks namespace: if the Windows
    path ever reaches it, the call is recorded rather than executed."""
    calls: list[str] = []

    def _record(cmd, **kwargs):
        calls.append(cmd)
        return types.SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(hooks, "os", _NtOs())
    monkeypatch.setattr(hooks.subprocess, "run", _record)
    return calls


def test_row444_windows_branch_is_exercised_at_runtime(nt_hooks):
    """Precondition for the verdict: the os.name read happens at CALL time,
    so patching it changes what the function does -- source reading is not
    runtime evidence."""
    assert hooks.os.name == "nt", "the fixture did not reach the hooks module"
    ok, msg = hooks.run_command_hook(TEMPLATE, {"filename": "benign.mp4"})
    assert (ok, msg) != (True, "(no command configured)"), (
        "the empty-template short circuit ran instead of the platform branch")
    assert msg != "", "the platform branch produced no diagnostic at all"


def test_row444_website_influenced_filename_cannot_execute_on_windows(
        nt_hooks):
    """RED on the defective parent.

    Parent: the rendered string reaches subprocess.run, and the cmd.exe model
    reports the injected command executing exactly once."""
    ok, msg = hooks.run_command_hook(
        TEMPLATE, {"filename": ADVERSARIAL_FILENAME})

    launched = nt_hooks
    fired = sum(injected_invocations(c, INJECTED) for c in launched)
    assert fired == 0, (
        f"the injected command would execute {fired} time(s) on cmd.exe; "
        f"launched command line(s): {launched!r}")
    assert launched == [], (
        "a website-influenced value was handed to cmd.exe: "
        f"{launched!r}")
    assert ok is False, "an unsafe Windows hook reported success"
    assert msg == hooks.WINDOWS_SHELL_HOOK_REFUSAL, (
        f"refusal used a non-distinctive diagnostic: {msg!r}")
    assert "cmd.exe" in msg and "refused" in msg, (
        "the diagnostic must name the step and the reason")


def test_row444_percent_expansion_also_cannot_reach_cmd_exe(nt_hooks):
    """The second cmd.exe shape the row names: %VAR% expands regardless of
    POSIX quoting."""
    rendered = hooks._render_template(
        TEMPLATE, {"filename": "clip-%USERPROFILE%.mp4"}, shell_quote=True)
    assert cmd_exe_would_expand_var(rendered), (
        "precondition: POSIX quoting must leave %VAR% expandable by cmd.exe")
    ok, _msg = hooks.run_command_hook(
        TEMPLATE, {"filename": "clip-%USERPROFILE%.mp4"})
    assert nt_hooks == [], "an expandable %VAR% reached cmd.exe"
    assert ok is False


def test_row444_windows_refusal_is_blanket_and_says_so(nt_hooks):
    """The refusal does not depend on inspecting the value: a benign filename
    is refused too.  This is the deliberate product cost of shipping no
    unverifiable cmd.exe quoter, and the test pins it so it is a decision
    rather than an accident."""
    ok, msg = hooks.run_command_hook(TEMPLATE, {"filename": "benign.mp4"})
    assert ok is False
    assert msg == hooks.WINDOWS_SHELL_HOOK_REFUSAL
    assert nt_hooks == []


def test_row444_windows_empty_template_is_still_a_noop(nt_hooks):
    """An unconfigured hook is not a refusal -- there is nothing to refuse."""
    ok, msg = hooks.run_command_hook("", {"filename": ADVERSARIAL_FILENAME})
    assert ok is True
    assert msg == "(no command configured)"
    assert nt_hooks == []


# ── POSIX negative controls: the fleet's behaviour must not regress ─────

class _PosixOs:
    name = "posix"

    def __getattr__(self, item):
        import os as _os
        return getattr(_os, item)


@pytest.fixture
def posix_hooks(monkeypatch):
    calls: list[str] = []

    def _record(cmd, **kwargs):
        calls.append(cmd)
        return types.SimpleNamespace(returncode=0, stdout="ran", stderr="")

    monkeypatch.setattr(hooks, "os", _PosixOs())
    monkeypatch.setattr(hooks.subprocess, "run", _record)
    return calls


def test_row444_posix_still_blocks_the_classic_breakout(posix_hooks):
    """Negative control: shlex.quote still neutralises the exact payload the
    Phase 41.7 comment names, and the whole value stays one POSIX token."""
    import shlex

    ok, _msg = hooks.run_command_hook(
        TEMPLATE, {"filename": POSIX_BREAKOUT_FILENAME})
    assert ok is True, "POSIX hook refused a command it should have run"
    assert len(posix_hooks) == 1, (
        f"expected exactly one launch, got {len(posix_hooks)}")
    line = posix_hooks[0]
    tokens = shlex.split(line)
    assert tokens == ["ffprobe", POSIX_BREAKOUT_FILENAME], (
        f"POSIX quoting no longer contains the break-out: {tokens!r}")
    assert "rm" not in tokens, "the break-out escaped its quoted slot"


def test_row444_posix_benign_filename_runs_exactly_once(posix_hooks):
    """Negative control: the intended command still runs, exactly once, with
    exactly the rendered string."""
    vars = {"filename": "benign.mp4"}
    expected = hooks._render_template(TEMPLATE, vars, shell_quote=True)
    ok, out = hooks.run_command_hook(TEMPLATE, vars)
    assert ok is True
    assert posix_hooks == [expected], (
        f"the string reaching subprocess.run was {posix_hooks!r}, not the "
        f"rendered output {expected!r}")
    assert out == "ran"


# ── the quoting denominator, derived mechanically ───────────────────────

def _application_python() -> list[Path]:
    """The application-Python population, tracked names first.

    ``git ls-files`` is the contract's denominator, but a detached scratch
    copy (bd-mutate refuses to work anywhere sharing Git authority with the
    repository) has no Git. The filesystem fallback is a SUPERSET -- it can
    only add untracked files -- so it cannot hide a shell=True site, and the
    nonzero check below applies to whichever population was used."""
    try:
        out = subprocess.run(
            ["git", "ls-files", "bulk_downloader"],
            cwd=REPO, capture_output=True, text=True, check=True)
        files = [REPO / line for line in out.stdout.splitlines()
                 if line.endswith(".py")]
        if files:
            return files
    except (OSError, subprocess.CalledProcessError):
        pass
    return [p for p in (REPO / "bulk_downloader").rglob("*.py")
            if "__pycache__" not in p.parts]


def _calls_with_true_kwarg(tree: ast.AST, kwarg: str) -> list[tuple[str, int]]:
    """Return (enclosing function name, lineno) for every call passing
    ``kwarg=True``.  AST, not text: a comment or a docstring mentioning
    ``shell=True`` must not enter this denominator (CLAUDE.md A7)."""
    found: list[tuple[str, int]] = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for sub in ast.walk(node):
            if not isinstance(sub, ast.Call):
                continue
            for kw in sub.keywords:
                if (kw.arg == kwarg and isinstance(kw.value, ast.Constant)
                        and kw.value.value is True):
                    found.append((node.name, sub.lineno))
    return found


def test_row444_shell_quoting_denominator_is_exactly_the_guarded_hook():
    """Every place the POSIX quoting defense is applied, and every place a
    rendered string reaches a shell, is inside the one function this cut
    guards.  A new site added later fails here rather than shipping
    undefended."""
    files = _application_python()
    assert len(files) > 10, (
        f"zero-ish denominator: only {len(files)} application file(s) found")

    quote_sites: dict[str, list[tuple[str, int]]] = {}
    shell_sites: dict[str, list[tuple[str, int]]] = {}
    for path in files:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        rel = str(path.relative_to(REPO))
        q = _calls_with_true_kwarg(tree, "shell_quote")
        s = _calls_with_true_kwarg(tree, "shell")
        if q:
            quote_sites[rel] = q
        if s:
            shell_sites[rel] = s

    assert set(quote_sites) == {"bulk_downloader/hooks.py"}, (
        f"shell_quote=True is applied outside the guarded hook: {quote_sites}")
    assert [n for n, _ in quote_sites["bulk_downloader/hooks.py"]] == \
        ["run_command_hook"], (
        f"unexpected shell_quote=True call sites: {quote_sites}")
    assert set(shell_sites) == {"bulk_downloader/hooks.py"}, (
        f"shell=True appears outside the guarded hook: {shell_sites}")
    assert [n for n, _ in shell_sites["bulk_downloader/hooks.py"]] == \
        ["run_command_hook"], (
        f"unexpected shell=True call sites: {shell_sites}")


def test_row444_platform_guard_precedes_every_shell_launch():
    """Structural: inside run_command_hook the os.name test must come before
    the shell launch, so no website-influenced value is rendered towards
    cmd.exe first."""
    path = REPO / "bulk_downloader" / "hooks.py"
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    fn = next((n for n in ast.walk(tree)
               if isinstance(n, ast.FunctionDef)
               and n.name == "run_command_hook"), None)
    assert fn is not None, "run_command_hook is not where this row measures it"

    guard_lines = [
        n.lineno for n in ast.walk(fn)
        if isinstance(n, ast.Compare)
        and isinstance(n.left, ast.Attribute)
        and n.left.attr == "name"
        and isinstance(n.left.value, ast.Name)
        and n.left.value.id == "os"
    ]
    assert len(guard_lines) == 1, (
        f"expected exactly one os.name guard, found {len(guard_lines)}")

    shell_lines = [n.lineno for n in ast.walk(fn) if isinstance(n, ast.Call)
                   for kw in n.keywords
                   if kw.arg == "shell" and isinstance(kw.value, ast.Constant)
                   and kw.value.value is True]
    assert len(shell_lines) == 1, (
        f"expected exactly one shell=True launch, found {len(shell_lines)}")
    assert guard_lines[0] < shell_lines[0], (
        f"the platform guard (line {guard_lines[0]}) does not precede the "
        f"shell launch (line {shell_lines[0]})")

    render_lines = [n.lineno for n in ast.walk(fn) if isinstance(n, ast.Call)
                    for kw in n.keywords
                    if kw.arg == "shell_quote"
                    and isinstance(kw.value, ast.Constant)
                    and kw.value.value is True]
    assert len(render_lines) == 1
    assert guard_lines[0] < render_lines[0], (
        "the platform guard must precede the render, so no website-influenced "
        "value is quoted towards cmd.exe at all")

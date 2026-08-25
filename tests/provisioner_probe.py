"""Run scripts/provision_test_host.sh and observe what it actually dispatched.

Backlog row 228. The gate this replaces reads the provisioner's TEXT and asks
whether the two optional-capability functions appear in command position. That
is a proxy for a runtime property it cannot see: whether the running shell ever
reaches those lines, and whether the library that defines the names was really
loaded. A branch that is never taken, a block placed after `exit`, and a
`.` guarded by a false condition all leave the text intact.

HOW THE SHELL IS INSTRUMENTED, and why it needs no rewriting of the script.
`readonly -f` marks a function so a later definition of the same name FAILS --
measured on this host, bash 5.2.21: the redefinition prints "readonly function",
returns 1, and, because the provisioner deliberately runs without `set -e`,
execution continues with the FIRST definition still installed. A prelude can
therefore install its own `run_step` and have the script's own definition bounce
off it. `readonly` on a variable behaves the same way, which is what pins
`LOGDIR` inside the fixture instead of /tmp/bd_provision.

So the copy under test is BYTE-IDENTICAL to the shipped script. Nothing here
edits shell source, which is the whole hazard CLAUDE.md A7 names for source
rewriters: no anchor to go stale, no mutation to prove, nothing to restore.

WHAT IS STUBBED, and why each one has to be:

  run_step            recorded, and executes its command ONLY when the first
                      word resolves to a shell function or a shell dispatch
                      builtin. Every other run_step command in this file is the
                      host-mutating tier -- apt-get, install_linux.sh, pip,
                      playwright -- and the two capabilities are the only shell
                      functions dispatched through it. Executing the function
                      is the point: it is what a wrapper, a variable holding the
                      name, or an `eval` all still have to do.

  the two capability  replaced by recorders. bd_mod3_pg_provision really does
  functions           install and start PostgreSQL; running it is the side
                      effect this harness exists to stop before.

  bd_start_display    replaced. It is called DIRECTLY, not through run_step,
                      and it starts an Xvfb server on :99.

  LOGDIR              pinned into the fixture, so `mkdir -p "$LOGDIR"` and every
                      per-step log land there and never in /tmp/bd_provision.

  PATH                a shim directory ahead of the system one: `sudo` (so the
                      elevation check does not hard-exit 2 for a non-root
                      runner), and `node`/`npm` refusing to run, so the SPA
                      capability probe -- which executes them directly -- gives
                      the same answer on every host.

WHAT PROVES THE LIBRARY WAS REALLY SOURCED. The capability recorders are
installed BEFORE the script runs, so the names exist whether or not
scripts/lib/dev_capabilities.sh was ever loaded: dispatch alone cannot see a
`.` that did not happen. The EXIT trap therefore reports `bd_mod3_env_persist`,
a sibling the library defines and this harness never touches. Its presence at
exit is the runtime evidence that the real fragment loaded.

WHAT THIS HARNESS DELIBERATELY DOES NOT DO. It does not execute the real
provisioning bodies, so it proves dispatch reaches the name and that the real
library defining that name was loaded -- not that PostgreSQL comes up. That is
a live-host property and stays with the provisioner's own verdict rows.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

SCRIPT = "scripts/provision_test_host.sh"
LIBRARY = "scripts/lib/dev_capabilities.sh"
DEPS = "scripts/lib/system_deps.sh"
MARKER = "bulk_downloader/__init__.py"

# The files the fixture checkout must carry for the provisioner to get past its
# own [1/9] and [2/9] hard exits. A missing one is a FATAL exit 2, which would
# stop the run long before the subject and hand this harness a green for a
# reason that has nothing to do with dispatch.
MIRRORED = (SCRIPT, LIBRARY, DEPS, MARKER)

# The two optional capabilities. This tuple is the harness's denominator and is
# reconciled against the library's own definitions before any verdict, so a
# third capability cannot enter dev_capabilities.sh and stay unexercised here.
CAPABILITIES = ("bd_mod3_pg_provision", "bd_dev_inspect_provision")

# Defined by dev_capabilities.sh and never overridden by this harness: its
# presence at exit is the only runtime evidence that the fragment was sourced.
SOURCE_WITNESS = "bd_mod3_env_persist"

# MEASURED on test5, this base, worst of 5 consecutive probe runs: 0.75s wall
# (0.64/0.70/0.75/0.69/0.75, load ~6).
# The rule is max(30, 6 x measured) = max(30, 4.5) = 30s, comfortably under the
# 240s bound the sanctioned suite command imposes, so the TimeoutExpired arm
# stays reachable rather than being dead code that can never fire.
PROBE_BUDGET_S = 30

_SHIMS = {
    # Elevation: a non-root runner with no sudo makes the script exit 2 at
    # [3/9], before the subject. This shim answers the `command -v` and the
    # `sudo -n true` probe and runs nothing.
    "sudo": '#!/bin/sh\ncase "$1" in -n) exit 0 ;; esac\nexit 0\n',
    # The SPA probe EXECUTES these rather than parsing a version, so the host's
    # own toolchain would otherwise decide which branch this run takes.
    "node": "#!/bin/sh\nexit 127\n",
    "npm": "#!/bin/sh\nexit 127\n",
}


@dataclass
class Dispatch:
    """One run_step call, as the running shell handed it over."""

    slug: str
    label: str
    kind: str
    argv: tuple[str, ...]
    executed: bool

    @property
    def command(self) -> str:
        return self.argv[0] if self.argv else ""


@dataclass
class Call:
    """One arrival at a capability function, and the step that was running."""

    name: str
    slug: str
    kind: str

    @property
    def graded(self) -> bool:
        """Reached from inside a run_step, so its outcome becomes a row."""
        return bool(self.slug)


@dataclass
class Probe:
    """The complete observation of one provisioner run."""

    returncode: int
    stdout: str
    stderr: str
    dispatches: list[Dispatch] = field(default_factory=list)
    calls: list[Call] = field(default_factory=list)
    defined_at_exit: dict[str, bool] = field(default_factory=dict)

    def steps_for(self, function: str) -> list[Dispatch]:
        """Every run_step whose command word is exactly this function."""
        return [d for d in self.dispatches if d.command == function]

    def calls_to(self, function: str) -> list[Call]:
        """Every arrival at this function, however it was spelled."""
        return [c for c in self.calls if c.name == function]

    def reached_the_verdict(self) -> bool:
        """The run traversed the whole script rather than exiting early."""
        return "=== [9/9] VERDICT ===" in self.stdout

    def describe(self) -> str:
        return (
            f"rc={self.returncode} verdict_reached={self.reached_the_verdict()} "
            f"dispatches={[(d.slug, d.kind, d.command, d.executed) for d in self.dispatches]} "
            f"calls={[(c.name, c.slug, c.kind) for c in self.calls]} "
            f"defined_at_exit={self.defined_at_exit} "
            f"stdout_tail={self.stdout.strip()[-300:]!r}"
        )


def _prelude(probe_dir: Path, logdir: Path, fail_capabilities: bool) -> str:
    """The instrumentation, as bash. Sourced before the script, never into it."""
    rc = "1" if fail_capabilities else "0"
    recorders = "\n".join(
        "%s(){ printf '%%s|%%s\\n' %s \"${_probe_step:-<no run_step>}\" "
        ">> \"$_probe_calls\"; return %s; }" % (name, name, rc)
        for name in CAPABILITIES
    )
    return f"""
_probe_step=""
_probe_steps={json.dumps(str(probe_dir / "steps.jsonl"))}
_probe_calls={json.dumps(str(probe_dir / "calls.txt"))}
_probe_exit={json.dumps(str(probe_dir / "defined.txt"))}
: > "$_probe_steps"
: > "$_probe_calls"
: > "$_probe_exit"

# run_step <slug> <label> <core|optional> <command...>
#
# Records the call, then executes the command ONLY when its first word is a
# shell function or a shell dispatch builtin. Everything else run_step is given
# in this script is the host-mutating tier and is recorded, not run.
run_step() {{
    local slug="$1" label="$2" kind="$3"; shift 3
    local executed=0
    case "${1:-}" in
        eval|command|builtin) executed=1 ;;
        *) if declare -F "$1" >/dev/null 2>&1; then executed=1; fi ;;
    esac
    _probe_emit "$slug" "$label" "$kind" "$executed" "$@"
    if [ "$executed" = 1 ]; then
        # The command runs with the grade it was handed in scope, so a recorder
        # reached through a wrapper, a variable or an `eval` still reports WHICH
        # run_step was executing when it was reached. Attributing the call to
        # the step is the whole difference between "the name is the command
        # word" -- a spelling -- and "this graded step really got there".
        _probe_step="$slug|$kind"
        "$@"
        local _rc=$?
        _probe_step=""
        return $_rc
    fi
    return 0
}}

# One JSON object per call, written by python so quoting in a label or an
# argument cannot corrupt the record this gate then reads.
_probe_emit() {{
    "$_probe_python" -c 'import json,sys
slug,label,kind,executed=sys.argv[1:5]
print(json.dumps({{"slug":slug,"label":label,"kind":kind,
                  "executed":executed=="1","argv":sys.argv[5:]}}))' "$@" >> "$_probe_steps"
}}

{recorders}

# Called directly, not through run_step, and it starts an X server.
bd_start_display(){{ printf '%s|%s\\n' bd_start_display "${{_probe_step:-<no run_step>}}" >> "$_probe_calls"; return 1; }}

# The names the script defines later must bounce off these.
readonly -f run_step _probe_emit bd_start_display {' '.join(CAPABILITIES)}

# Keeps `mkdir -p "$LOGDIR"` and every per-step log inside the fixture.
LOGDIR={json.dumps(str(logdir))}
readonly LOGDIR

# The provisioner exits from the middle of itself on its FATAL paths, so the
# only place this observation can be made is a trap.
_probe_report_definitions() {{
    local name
    for name in {' '.join(CAPABILITIES)} {SOURCE_WITNESS} bd_system_pkgs; do
        if declare -F "$name" >/dev/null 2>&1; then
            printf '%s=1\\n' "$name" >> "$_probe_exit"
        else
            printf '%s=0\\n' "$name" >> "$_probe_exit"
        fi
    done
}}
trap _probe_report_definitions EXIT
"""


def build_tree(base: Path, script_text: str | None = None) -> Path:
    """A minimal checkout: the marker, the script, and the two fragments."""
    root = base / "tree"
    for relative in MIRRORED:
        destination = root / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / relative, destination)
        assert destination.is_file(), f"the fixture is missing {relative}"
    if script_text is not None:
        (root / SCRIPT).write_text(script_text, encoding="utf-8")
    os.chmod(root / SCRIPT, 0o755)
    return root


def run(tree: Path, scratch: Path, fail_capabilities: bool = False) -> Probe:
    """Source the provisioner under instrumentation and report what ran."""
    import sys

    probe_dir = scratch / "probe"
    logdir = scratch / "logs"
    home = scratch / "home"
    temporary = scratch / "tmp"
    shim = scratch / "shim"
    for directory in (probe_dir, logdir, home, temporary, shim):
        directory.mkdir(parents=True, exist_ok=True)
    for name, body in _SHIMS.items():
        path = shim / name
        path.write_text(body, encoding="utf-8")
        os.chmod(path, 0o755)
        assert os.access(path, os.X_OK), f"the {name} shim is not executable"

    harness = scratch / "harness.sh"
    harness.write_text(
        _prelude(probe_dir, logdir, fail_capabilities)
        + f'\n. {json.dumps(str(tree / SCRIPT))} {json.dumps(str(tree))}\n',
        encoding="utf-8",
    )
    # Built from scratch rather than inherited (CLAUDE.md A7), and LC_ALL is
    # pinned so nothing here depends on the caller's collation (row 178/1197).
    environment = {
        "PATH": os.pathsep.join([str(shim), "/usr/bin", "/bin"]),
        "HOME": str(home),
        "TMPDIR": str(temporary),
        "LC_ALL": "C",
        "_probe_python": sys.executable,
    }
    completed = subprocess.run(
        ["bash", str(harness)],
        cwd=str(scratch),
        env=environment,
        capture_output=True,
        text=True,
        timeout=PROBE_BUDGET_S,
    )
    return _read(probe_dir, completed)


def _read(probe_dir: Path, completed: subprocess.CompletedProcess) -> Probe:
    probe = Probe(
        returncode=completed.returncode,
        stdout=completed.stdout,
        stderr=completed.stderr,
    )
    steps = probe_dir / "steps.jsonl"
    if steps.is_file():
        for line in steps.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            record = json.loads(line)
            probe.dispatches.append(
                Dispatch(
                    slug=record["slug"],
                    label=record["label"],
                    kind=record["kind"],
                    argv=tuple(record["argv"]),
                    executed=bool(record["executed"]),
                )
            )
    calls = probe_dir / "calls.txt"
    if calls.is_file():
        for line in calls.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            name, _, context = line.partition("|")
            slug, _, kind = context.partition("|")
            probe.calls.append(
                Call(name=name, slug="" if slug.startswith("<") else slug, kind=kind)
            )
    defined = probe_dir / "defined.txt"
    if defined.is_file():
        for line in defined.read_text(encoding="utf-8").splitlines():
            if "=" in line:
                name, _, value = line.partition("=")
                probe.defined_at_exit[name] = value == "1"
    return probe


def dispatch_failures(probe: Probe) -> list[str]:
    """Every behavioural reason this run did not really provision.

    Empty means: the run reached the end of the script, the real fragment was
    loaded, and each capability was dispatched exactly once, as `optional`, and
    the dispatch actually arrived at the function.
    """
    failures: list[str] = []
    if not probe.reached_the_verdict():
        failures.append(
            "the run never reached [9/9]; it exited early with rc="
            f"{probe.returncode}: {(probe.stdout + probe.stderr).strip()[-300:]}"
        )
    if not probe.defined_at_exit.get(SOURCE_WITNESS):
        failures.append(
            f"{SOURCE_WITNESS} is undefined at exit, so {LIBRARY} was never "
            "sourced; the capability names would resolve to nothing on a real host"
        )
    graded: dict[str, str] = {}
    for name in CAPABILITIES:
        calls = probe.calls_to(name)
        if len(calls) != 1:
            failures.append(
                f"{name} was reached {len(calls)} times, expected exactly 1"
                f" (arrivals: {[(c.slug, c.kind) for c in calls]})"
            )
            continue
        call = calls[0]
        # WHAT IS GRADED IS THE ARRIVAL, NOT THE SPELLING. A wrapper function, a
        # variable holding the name and an `eval` all reach the body from inside
        # the run_step that was executing, and all three are correct. What is
        # NOT correct is reaching it from outside any run_step: that outcome is
        # never logged, never graded, and never appears in the verdict.
        if not call.graded:
            failures.append(
                f"{name} ran outside any run_step, so its outcome is not "
                "logged, not graded and never reaches the verdict table"
            )
            continue
        if call.kind != "optional":
            failures.append(
                f"{name} is dispatched as {call.kind!r}, not 'optional', so "
                "a host that cannot install it FAILS provisioning instead of "
                "warning"
            )
            continue
        if call.slug in graded:
            failures.append(
                f"{name} and {graded[call.slug]} share the run_step {call.slug!r}, "
                "so one WARN row covers both and a host missing exactly one of "
                "them cannot be told from a host missing neither"
            )
            continue
        graded[call.slug] = name
    return failures

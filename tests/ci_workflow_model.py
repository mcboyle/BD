"""A parsed model of the GitHub Actions workflow, for gates that must judge what
a CI lane WILL DO rather than what its source text happens to contain.

WHY THIS EXISTS. Backlog row 186. The guard-authority gate asserted that CI runs
the guard checker with one substring test against the raw workflow text. Every
evasion that matters leaves that substring intact: disabling the step with a
false condition, marking it continue-on-error, narrowing the trigger so the job
never starts on a pull request, moving the command into a YAML comment, or
echoing it instead of running it. The substring is a proxy for a scheduling and
execution property, and a proxy cannot see any of that.

WHAT THIS MODULE DOES AND DOES NOT DECIDE. It answers exactly one question:
WILL GITHUB SCHEDULE THIS STEP, AND WILL A NONZERO EXIT FROM THE TOOL FAIL THE
JOB? That question is answerable from the parsed workflow and from nothing else,
because triggers, job conditions and step conditions have no local runtime.

It deliberately does NOT decide whether the command does anything useful. A step
that runs the tool against an empty directory, or against a manifest it has just
rewritten down to one entry, is scheduled and failure-propagating and this module
reports it as qualifying. That question is behavioural and is answered by
executing the lane's own script against tampered fixtures --- see
tests/test_v3_66_1234_ci_really_executes_the_guard_lane.py. Splitting it this way
keeps each half judging what it can actually see, instead of building a cleverer
text scan and calling it behaviour.

THE MASKING RULES ARE MEASURED, NOT ASSUMED. GitHub's default shell for a `run`
block is `bash -e {0}` --- errexit WITHOUT pipefail. Measured on this host with
`bash -e` over a failing command: `cmd | cat` exits 0, `cmd || true` exits 0,
`cmd &` exits 0, `set -o pipefail` before the pipe restores exit 1 --- and
`cmd; true` exits 1, because errexit fires at `cmd` before the separator is
reached. So `;` is NOT a masking operator and is NOT disqualified here; treating
it as one would reject a correct lane, and a gate that cries wolf gets switched
off. Only the three measured-masking forms disqualify.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import shlex

import yaml

TOOL = "toolchain/bin/bd-guardcheck"
WORKFLOW = ".github/workflows/ci.yml"
CHECKOUT_ACTION = "actions/checkout"
EXPRESSION = "${{"

# GitHub's default pull_request activity types. A `types:` list that is not a
# superset of these has NARROWED the trigger; a longer list has widened it and
# is fine. Supersetting rather than presence is what keeps this from rejecting a
# workflow that adds ready_for_review.
DEFAULT_PR_TYPES = frozenset({"opened", "synchronize", "reopened"})

_NARROWING_KEYS = ("paths", "paths-ignore", "branches-ignore")


@dataclass(frozen=True)
class Lane:
    """One workflow step whose parsed `run` scalar mentions the tool."""

    job: str
    step_index: int
    name: str
    script: str
    command: str
    env: dict
    working_directory: str | None
    disqualifiers: tuple[str, ...]
    expressions: tuple[str, ...]

    @property
    def qualifies(self) -> bool:
        return not self.disqualifiers

    def describe(self) -> str:
        return (f"{self.job}[{self.step_index}] {self.name!r}: "
                f"{list(self.disqualifiers)}")


def load(root: Path) -> dict:
    """Parse the workflow. A missing or malformed file RAISES rather than
    returning an empty model: an unreadable workflow is UNKNOWN, and UNKNOWN
    must never be laundered into 'nothing wrong here'."""
    text = (Path(root) / WORKFLOW).read_text(encoding="utf-8")
    document = yaml.safe_load(text)
    if not isinstance(document, dict):
        raise ValueError(f"{WORKFLOW} did not parse to a mapping")
    return document


def triggers(document: dict) -> dict:
    """YAML 1.1 resolves the bare key `on` to the BOOLEAN True, so the top-level
    keys of a real workflow read as ['name', True, 'permissions', 'jobs'].
    Reading document["on"] alone finds nothing and every trigger rule below
    would silently pass over an empty mapping."""
    raw = document.get("on", document.get(True))
    return raw if isinstance(raw, dict) else {}


def _tokens(line: str) -> list[str]:
    """Shell tokens including the operators, with `#` comments dropped.

    punctuation_chars keeps `|`, `||`, `&` and `;` as their own tokens while
    leaving a quoted "a|b" intact, so an operator inside an argument is not
    mistaken for a chain --- which is exactly the mistake a text scan makes."""
    lexer = shlex.shlex(line, punctuation_chars=True)
    lexer.whitespace_split = True
    lexer.commenters = "#"
    return list(lexer)


def _logical_lines(script: str) -> list[str]:
    return script.replace("\\\n", " ").splitlines()


def _invocation(script: str, tool: str) -> tuple[str, list[str]] | None:
    """The first uncommented line that really invokes `tool`.

    The tool path must appear as a STANDALONE token. `echo
    'toolchain/bin/bd-guardcheck --tree'` tokenises to two tokens and the second
    is the quoted string, not the tool, so the echo form finds no invocation.
    `--selftest` short-circuits before the tool reads any tree, so a line
    carrying it is not an invocation against the work tree either."""
    for line in _logical_lines(script):
        try:
            tokens = _tokens(line)
        except ValueError:
            continue
        if tool in tokens and "--selftest" not in tokens:
            return line, tokens
    return None


def _masking(tokens: list[str], script: str) -> list[str]:
    reasons = []
    if "||" in tokens:
        reasons.append("the command masks a nonzero exit with `||`")
    if "&" in tokens:
        reasons.append("the command is backgrounded with `&`, so the step "
                       "cannot see its exit status")
    if "|" in tokens and "pipefail" not in script:
        reasons.append("the command pipes without `set -o pipefail`, and "
                       "GitHub's default shell is `bash -e` with no pipefail")
    for line in _logical_lines(script):
        try:
            words = _tokens(line)
        except ValueError:
            continue
        if words[:1] == ["set"] and any(
                word.startswith("+") and "e" in word for word in words[1:]):
            reasons.append("the script turns errexit off with `set +e`")
            break
    return reasons


def _trigger_disqualifiers(document: dict) -> list[str]:
    on = triggers(document)
    if not on:
        return ["the workflow declares no triggers at all"]
    reasons = []
    if "pull_request" not in on:
        reasons.append("no pull_request trigger, so the lane never runs before "
                       "a merge")
    else:
        # `pull_request:` with an empty value parses to None and means "every
        # default". Absent and present-but-None are opposite facts.
        pull = on["pull_request"] or {}
        if not isinstance(pull, dict):
            reasons.append(f"pull_request trigger is not a mapping: {pull!r}")
        else:
            reasons += _narrowed(pull, "pull_request")
            branches = pull.get("branches")
            if branches is not None and "main" not in branches:
                reasons.append(f"pull_request.branches excludes main: {branches!r}")
            types = pull.get("types")
            if types is not None and not DEFAULT_PR_TYPES <= set(types):
                reasons.append(
                    f"pull_request.types {sorted(set(types))} is not a superset "
                    f"of the default {sorted(DEFAULT_PR_TYPES)}")
    push = on.get("push")
    if isinstance(push, dict):
        reasons += _narrowed(push, "push")
        branches = push.get("branches")
        if branches is not None and "main" not in branches:
            reasons.append(f"push.branches excludes main: {branches!r}")
    return reasons


def _narrowed(trigger: dict, label: str) -> list[str]:
    return [f"{label} trigger is narrowed by {key}: {trigger[key]!r}"
            for key in _NARROWING_KEYS if key in trigger]


def _job_disqualifiers(document: dict, name: str) -> list[str]:
    jobs = document.get("jobs") or {}
    job = jobs.get(name) or {}
    reasons = []
    if "if" in job:
        reasons.append(f"job {name} is conditional: if={job['if']!r}")
    if job.get("continue-on-error"):
        reasons.append(f"job {name} is continue-on-error")
    needs = job.get("needs")
    if isinstance(needs, str):
        needs = [needs]
    for dependency in needs or []:
        upstream = jobs.get(dependency) or {}
        if "if" in upstream:
            reasons.append(f"job {name} needs {dependency}, which is "
                           f"conditional: if={upstream['if']!r}")
        if upstream.get("continue-on-error"):
            reasons.append(f"job {name} needs {dependency}, which is "
                           f"continue-on-error")
    return reasons


def _shell(document: dict, job: dict, step: dict) -> str | None:
    for holder in (step, job.get("defaults") or {}, document.get("defaults") or {}):
        if holder is step:
            value = step.get("shell")
        else:
            value = (holder.get("run") or {}).get("shell")
        if value:
            return value
    return None


def _expressions(step: dict) -> list[str]:
    found = []
    if EXPRESSION in (step.get("run") or ""):
        found.append("run")
    if EXPRESSION in str(step.get("working-directory") or ""):
        found.append("working-directory")
    for key, value in (step.get("env") or {}).items():
        if EXPRESSION in str(value):
            found.append(f"env[{key}]")
    return found


def guard_lanes(root: Path, tool: str = TOOL) -> list[Lane]:
    """Every step in every job whose PARSED `run` scalar mentions `tool`.

    Discovery is deliberately generous --- a mere mention makes a candidate ---
    so that a step which only pretends to run the tool is still REPORTED with
    the reason it does not count, rather than vanishing from the denominator."""
    document = load(root)
    trigger_reasons = _trigger_disqualifiers(document)
    lanes: list[Lane] = []
    for job_name, job in (document.get("jobs") or {}).items():
        job = job or {}
        steps = job.get("steps") or []
        checked_out = False
        job_reasons = _job_disqualifiers(document, job_name)
        for index, step in enumerate(steps):
            step = step or {}
            uses = str(step.get("uses") or "")
            if uses.startswith(CHECKOUT_ACTION):
                checked_out = True
            script = step.get("run")
            if not isinstance(script, str) or tool not in script:
                continue
            reasons = list(trigger_reasons) + list(job_reasons)
            if "if" in step:
                reasons.append(f"step is conditional: if={step['if']!r}")
            if step.get("continue-on-error"):
                reasons.append("step is continue-on-error")
            shell = _shell(document, job, step)
            if shell not in (None, "bash"):
                reasons.append(f"step shell {shell!r} is not the default bash, "
                               "so its failure semantics are unmodelled")
            if not checked_out:
                reasons.append(f"no {CHECKOUT_ACTION} step precedes it in job "
                               f"{job_name}, so there is no tree to judge")
            invocation = _invocation(script, tool)
            if invocation is None:
                reasons.append("no uncommented line invokes the tool as a "
                               "command against the work tree")
                command, tokens = "", []
            else:
                command, tokens = invocation
                reasons += _masking(tokens, script)
            lanes.append(Lane(
                job=job_name,
                step_index=index,
                name=str(step.get("name") or f"step {index}"),
                script=script,
                command=command,
                env=dict(step.get("env") or {}),
                working_directory=step.get("working-directory"),
                disqualifiers=tuple(reasons),
                expressions=tuple(_expressions(step)),
            ))
    return lanes


def enabled_guard_lanes(root: Path, tool: str = TOOL) -> list[Lane]:
    return [lane for lane in guard_lanes(root, tool) if lane.qualifies]

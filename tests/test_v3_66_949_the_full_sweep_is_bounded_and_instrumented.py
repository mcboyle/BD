"""v3.66.949 -- the full-suite prohibition becomes a bounded, instrumented procedure.

WHY THE RULE IS CHANGING, and it is not because running everything turned out to
be harmless. CLAUDE.md section 5 forbade running the whole `tests/` directory and
rested that on two named files. @948 measured both away: `test_perf_lab.py`
passes in 2.5s with and without BD_DISABLE_KEEPALIVE, and the second hanger
names a file that does not exist in any variant. A 79-file sweep of hang-prone
shapes found nothing either.

The operator then granted a one-time exemption to run the suite whole. Measured
2026-08-08 at v3.66.948, with `pytest-timeout` armed specifically to name a
hanging test and dump its stack:

    tests/  -n 4 --dist loadfile --timeout=240 --timeout-method=signal
    -> 14 failed, 14943 passed, 91 skipped in 635.42s (10m35s)
    -> tests exceeding 240s: ZERO. The timeout guard never fired.

The 14 are the documented container-only set (test_e2e_smoke x7, the
`no_backend` body-contract case, absent-interpreter exec_bridge x5, a no-tunnel
vpn probe). Item 34's four order-dependent failures are ABSENT, which is @945's
fix holding at full denominator.

WHAT THIS FILE GUARDS, AND IT IS NOT "THE SUITE PASSES". The relaxation is
narrow on purpose:

  * ONE ORDERING was measured. `-p no:randomly` with `--dist loadfile` keeps
    each file whole on one worker, so an interleaving-dependent hang was never
    given the chance. One green run is not a proof of absence.
  * IT WAS PARALLEL, NOT SERIAL. The prohibition most plausibly concerned a
    serial local run; that is a different denominator and remains untested.
  * THE INSTRUMENT WAS INSTALLED BY HAND and dies with the session. CLAUDE.md
    section 5 records that anything installed by hand lives only until the
    session ends -- so a relaxed rule depending on the timeout guard, with the
    guard undeclared, hands the next agent the sweep with NO instrument, and a
    hang becomes an unexplained stall again. That is the whole reason
    `pytest-timeout` is declared in this cut rather than merely used.

So the assertions are about the PROCEDURE being reachable and instrumented, not
about any run's verdict:

  1. pytest-timeout is DECLARED in requirements-test.txt and IMPORTABLE. Both,
     because either alone is the section 0 gap -- declared-but-absent is a
     dependency that never installs, present-but-undeclared is one that vanishes
     on the next container rebuild. pyflakes is the precedent recorded in that
     manifest: declaring it in requirements-dev.txt, which nothing on the deploy
     path reads, was "the fix reproducing the shape of the defect it was fixing".
  2. Every documented whole-directory invocation carries `--timeout`, so a later
     edit cannot sanction an uninstrumented sweep by relaxing the prose.
  3. requirements-test.txt no longer advertises a bare `pytest tests/`. It did:
     the manifest told every reader to run exactly what the contract forbade,
     which is the two-agent-facing-instructions defect section 8 exists to stop.
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
_REQ_TEST = _REPO / "requirements-test.txt"
_CONTRACT = _REPO / "CLAUDE.md"

# Documents that instruct an agent. A whole-suite command in any of these is a
# sanctioned invocation, whether or not it was meant as one.
_INSTRUCTING = (_CONTRACT, _REQ_TEST)


def _declared_names(text: str) -> set[str]:
    """Direct requirement names, comments and `-r` includes stripped.

    `-r` is skipped for the reason tools/check_requirements.py skips it (:73):
    each manifest is resolved on its own names, and deploy.sh checks them
    separately rather than relying on the include.
    """
    out = set()
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line or line.startswith("-"):
            continue
        out.add(re.split(r"[<>=!~\[;]", line, 1)[0].strip().lower())
    return out


def _code_block_lines(text: str) -> list[str]:
    """Lines inside fenced code blocks only.

    PROSE IS INSIDE THE DENOMINATOR OF EVERY GATE THAT READS SOURCE TEXT
    (CLAUDE.md section 0, recorded four times). This file's own docstring and
    section 5's prohibition both contain the string `pytest tests/` while
    FORBIDDING it -- a line-scan would read the prohibition as the offence.
    Only fenced commands count as instructions to run something.
    """
    out, inside = [], False
    for line in text.splitlines():
        if line.lstrip().startswith("```"):
            inside = not inside
            continue
        if inside:
            out.append(line)
    return out


def _instruction_lines(path: Path, text: str) -> list[str]:
    """Lines that INSTRUCT a reader to run something, per document kind.

    FENCE-ONLY WAS A GATE THAT COULD NOT SEE ITS SUBJECT, and this file shipped
    it for one run. `requirements-test.txt` carries no fences at all, so the
    fenced-block denominator structurally excluded the one document that was
    actually advertising a bare `pytest tests/` -- in a comment, which in a
    requirements manifest IS the documentation. The scan reported clean over the
    offence it was written to find (CLAUDE.md section 0).

    Markdown: fenced blocks. Everything else: comment lines, which is where a
    manifest tells you how to use it.
    """
    raw = (_code_block_lines(text) if path.suffix == ".md"
           else [ln.split("#", 1)[1] for ln in text.splitlines() if "#" in ln])
    return _join_continuations(raw)


def _join_continuations(lines: list[str]) -> list[str]:
    """Fold backslash-continued lines into one command.

    A LINE-SCOPED CHECK IS WRONG IN BOTH DIRECTIONS, and this file proved it on
    itself: section 5's sanctioned command is continued across three lines, so
    the `--timeout` that makes it legal sits on line two and a per-line scan
    reported the CORRECT command as an offence. CLAUDE.md section 0 records that
    exact shape ("a per-line check therefore fails a CORRECT implementation for
    its form") three times over shell loops.

    tests/shell_source.blocks_containing is NOT reused here on purpose: its
    subject is enclosing shell CONSTRUCTS (for/if bodies), and this one is line
    continuation. Reaching for it would be using the right tool on the wrong
    question rather than avoiding a fourth copy of anything.
    """
    out, buf = [], ""
    for line in lines:
        stripped = line.rstrip()
        if stripped.endswith("\\"):
            buf += stripped[:-1] + " "
            continue
        out.append(buf + line)
        buf = ""
    if buf:
        out.append(buf)
    return out


_WHOLE_DIR = re.compile(r"pytest\s+(?:[^\n|]*\s)?tests/?(?:\s|$)")


def _uninstrumented_sweeps(text: str, path: Path = Path("x.md")) -> list[str]:
    """Instructions that run the whole directory without a per-test timeout."""
    bad = []
    for line in _instruction_lines(path, text):
        if "pytest" not in line:
            continue
        # a specific file or node id is not a whole-directory sweep
        if re.search(r"tests/\S+\.py", line):
            continue
        if not _WHOLE_DIR.search(line):
            continue
        if "--timeout" not in line:
            bad.append(line.strip())
    return bad


# ── the instrument, before anything that depends on it ───────────────────────

def test_the_sweep_scanner_actually_discriminates():
    """Positive control. Synthetic text whose answer is not in doubt.

    Written before the gate for the reason @944's battery taught: a check that
    must return an EMPTY list on a clean tree is indistinguishable from one that
    always returns empty, unless something proves it can return an item.
    """
    bad = _uninstrumented_sweeps("```\nvenv/bin/python -m pytest tests/ -q\n```")
    assert bad, "the scanner did not flag a bare whole-directory sweep"

    ok = _uninstrumented_sweeps(
        "```\nvenv/bin/python -m pytest tests/ -n 4 --timeout=240\n```")
    assert ok == [], f"the scanner flagged an INSTRUMENTED sweep: {ok!r}"

    single = _uninstrumented_sweeps("```\npytest tests/test_one.py -q\n```")
    assert single == [], (
        f"the scanner flagged a single-file run: {single!r}. Banding one file is "
        f"the normal way to work here and must never trip this.")

    manifest = _uninstrumented_sweeps("#   pytest tests/   # all tests\n",
                                      Path("requirements-test.txt"))
    assert manifest, (
        "the scanner cannot see a COMMENT in a requirements manifest. That is "
        "where such a file documents itself, and fence-only scanning missed the "
        "one real offence in this repo -- a gate blind to its own subject.")

    continued = _uninstrumented_sweeps(
        "```\npytest tests/ \\\n    --timeout=240\n```")
    assert continued == [], (
        f"the scanner flagged a CONTINUED command whose --timeout is on the "
        f"next line: {continued!r}. A line-scoped check fails a correct "
        f"implementation for its form -- CLAUDE.md section 0, three times over.")

    prose = _uninstrumented_sweeps("Never run `pytest tests/` locally.\n")
    assert prose == [], (
        "the scanner read PROSE as a command -- section 5's own prohibition "
        "contains the string it forbids, so a line-scan reports the rule as a "
        "violation of itself")


# ── the dependency, declared AND present ─────────────────────────────────────

def test_pytest_timeout_is_declared_in_the_test_manifest():
    """RED on pristine. Present-but-undeclared vanishes on the next rebuild."""
    names = _declared_names(_REQ_TEST.read_text("utf-8"))
    assert names, "no requirement names parsed -- the gate below would be vacuous"
    assert "pytest-timeout" in names or "pytest_timeout" in names, (
        f"pytest-timeout is not declared in requirements-test.txt. The relaxed "
        f"full-sweep procedure depends on it to NAME a hanging test; without the "
        f"declaration the next container rebuild loses it silently and the sweep "
        f"runs with no instrument, which is how a hang became an unexplained "
        f"stall in the first place. Declared names: {sorted(names)}")


def test_pytest_timeout_is_actually_importable():
    """The other half. Declared-but-absent is a dependency that never installs.

    Separate from the declaration test on purpose: pyflakes is recorded in that
    manifest as having been declared in a file nothing on the deploy path read,
    so the declaration was true and reached nobody.
    """
    proc = subprocess.run(
        [sys.executable, "-c", "import pytest_timeout"],
        capture_output=True, text=True, timeout=120)
    assert proc.returncode == 0, (
        "pytest-timeout is not importable by the interpreter running the suite: "
        + (proc.stderr or "")[-400:])


# ── no document may sanction an uninstrumented sweep ─────────────────────────

def test_no_instructing_document_sanctions_an_uninstrumented_sweep():
    """RED on pristine: requirements-test.txt advertises `pytest tests/`.

    That manifest told every reader to run exactly what CLAUDE.md forbade. Two
    agent-facing instructions in conflict is the defect section 8 exists to stop,
    and here the losing one sat in the file you read to set the environment up.
    """
    offences = []
    for doc in _INSTRUCTING:
        for line in _uninstrumented_sweeps(doc.read_text("utf-8"), doc):
            offences.append(f"{doc.relative_to(_REPO)}: {line}")
    assert not offences, (
        "document(s) sanction a whole-directory run with no per-test timeout. "
        "The sweep is permitted ONLY in its bounded, instrumented form -- an "
        "unbounded one cannot tell a hang from a slow run, which is the entire "
        "reason the prohibition existed:\n  " + "\n  ".join(offences))


def test_the_contract_documents_the_sanctioned_form():
    """The relaxation must be actionable, not merely permissive.

    A rule that says "you may, carefully" without giving the command is one an
    agent will re-derive wrongly -- and the wrong derivation here is the
    uninstrumented run.
    """
    text = _CONTRACT.read_text("utf-8")
    fenced = "\n".join(_code_block_lines(text))
    assert _WHOLE_DIR.search(fenced), (
        "CLAUDE.md documents no whole-suite command at all, so the relaxation "
        "is unusable and an agent needing one will invent it")
    for flag in ("--timeout", "--timeout-method", "--dist loadfile"):
        assert flag in fenced, (
            f"the sanctioned command omits {flag!r}. Every one of these was "
            f"load-bearing in the measured run: the timeout names a hang, the "
            f"method dumps its stack, and loadfile is the distribution that was "
            f"actually measured -- a different one is a different experiment.")

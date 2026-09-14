"""Row 709: a shell state seed must not impersonate a measured outcome.

scripts/deploy.sh seeded ``CLOAK_STATE=UNKNOWN`` and then assigned the SAME
literal ``UNKNOWN`` on the measured-but-unresolvable branches.  A later reader
of ``cloak=UNKNOWN`` -- the deploy summary, step [12]/[13], and the durable
capability record beside the graph pin -- therefore could not tell "the probe
ran and could not resolve" from "the probe never ran at all".  That is the
conflation this gate refuses: the seed of a state variable that is later read
must be a literal no measurement can also produce.

The gate states its population (tracked POSIX-shell sources under ``scripts/``
and ``toolchain/bin``), states why everything else is excluded, and reports
UNKNOWN rather than clean when that population cannot be measured -- an
unmeasurable population is not an absent finding.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import subprocess


BD_GATE_SCOPE = "repo-wide"

_REPO = Path(__file__).resolve().parents[1]

# The stated population, and the reason for every exclusion from it.  A gate
# that excludes silently publishes a denominator nobody can check.
_POPULATION_ROOTS = ("scripts", "toolchain/bin")
_EXCLUSION_REASONS = (
    ("not tracked by git", "an untracked file is not a repo surface"),
    ("no POSIX-shell shebang", "shell assignment syntax is not the state "
                               "model of a Python or data file"),
)

# A state variable is one whose NAME declares it holds a verdict.  Counters,
# paths and flags are excluded by the same rule: they are not read as outcomes.
_STATE_SUFFIXES = ("_STATE", "_STATUS", "_VERDICT", "_RESULT", "_OUTCOME")

_DECLARATORS = ("local ", "export ", "declare ", "readonly ", "typeset ")


@dataclass(frozen=True)
class Census:
    """A measurement with its own state: OK or UNKNOWN, never silently clean."""

    state: str
    files: tuple[str, ...]
    findings: tuple[tuple[str, int, str, str], ...]


def _population_roots() -> tuple[str, ...]:
    """The stated roots, read through one seam so a narrowing is mutable."""
    return _POPULATION_ROOTS


def _shell_population(root: Path) -> Census:
    listing = subprocess.run(
        ["git", "ls-files", "-z", "--", *_population_roots()],
        cwd=root, capture_output=True, text=True)
    if listing.returncode != 0:
        return Census("UNKNOWN", (), ())
    files: list[str] = []
    for rel in filter(None, listing.stdout.split("\0")):
        try:
            head = (root / rel).read_text(encoding="utf-8").splitlines()
        except (OSError, UnicodeDecodeError):
            return Census("UNKNOWN", (), ())
        if not head:
            continue
        first = head[0]
        if first.startswith("#!") and ("/sh" in first or "bash" in first):
            files.append(rel)
    if not files:
        # Zero is not a denominator: say so instead of reporting no findings.
        return Census("UNKNOWN", (), ())
    return Census("OK", tuple(sorted(files)), ())


def _assignment(line: str) -> tuple[str, str] | None:
    """Return (NAME, LITERAL) for a literal shell state assignment, else None."""
    line = line.strip()
    for declarator in _DECLARATORS:
        if line.startswith(declarator):
            line = line[len(declarator):].strip()
    if not line or line.startswith("#") or "=" not in line:
        return None
    name, value = line.split("=", 1)
    if not name.endswith(_STATE_SUFFIXES):
        return None
    if not name.replace("_", "").isalnum() or not name.isupper():
        return None
    value = value.strip().strip('"\'')
    # Only a bare literal is a verdict; an expansion or command substitution
    # carries no comparable literal at all.
    if not value or not value.replace("_", "").isalnum() or not value.isupper():
        return None
    return name, value


def seeded_verdicts(root: Path) -> Census:
    """Find every state seed whose literal a later measurement can also produce."""
    population = _shell_population(root)
    if population.state != "OK":
        return population
    findings: list[tuple[str, int, str, str]] = []
    for rel in population.files:
        try:
            text = (root / rel).read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            return Census("UNKNOWN", (), ())
        lines = text.splitlines()
        assignments: dict[str, list[tuple[int, str]]] = {}
        for line_no, line in enumerate(lines, 1):
            parsed = _assignment(line)
            if parsed is not None:
                assignments.setdefault(parsed[0], []).append((line_no, parsed[1]))
        for name, values in assignments.items():
            seed_line, seed = values[0]
            outcomes = {literal for _, literal in values[1:]}
            if seed not in outcomes:
                continue
            # Only a state something later READS can mislead a reader; a value
            # no surface consumes is not a verdict.  The fixture below proves
            # this conjunct discriminates rather than always admitting.
            if ("$" + name) not in text and ("${" + name) not in text:
                continue
            findings.append((rel, seed_line, name, seed))
    return Census("OK", population.files, tuple(findings))


def _fixture_repo(tmp_path: Path, seed: str, read_state: bool = True) -> Path:
    (tmp_path / "scripts").mkdir()
    (tmp_path / "toolchain" / "bin").mkdir(parents=True)
    reader = 'echo "cloak=$CLOAK_STATE"\n' if read_state else 'echo done\n'
    (tmp_path / "scripts" / "deploy.sh").write_text(
        "#!/usr/bin/env bash\n"
        f"CLOAK_STATE={seed}\n"
        "if probe; then\n"
        "  CLOAK_STATE=OK\n"
        "else\n"
        "  CLOAK_STATE=UNKNOWN\n"
        "fi\n"
        + reader,
        encoding="utf-8")
    # Excluded by reason: a Python tool, whose UNKNOWN literal must NOT be read
    # as a shell state seed.
    (tmp_path / "toolchain" / "bin" / "bd-tool").write_text(
        "#!/usr/bin/env python3\nSTATE = 'UNKNOWN'\nSTATE = 'UNKNOWN'\n",
        encoding="utf-8")
    # In population, and clean: its seed literal is one no branch assigns.
    (tmp_path / "toolchain" / "bin" / "bd-shell").write_text(
        "#!/usr/bin/env bash\n"
        "OTHER_STATE=UNMEASURED\n"
        "OTHER_STATE=OK\n"
        'echo "$OTHER_STATE"\n',
        encoding="utf-8")
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "add", "scripts/deploy.sh",
                    "toolchain/bin/bd-tool", "toolchain/bin/bd-shell"],
                   cwd=tmp_path, check=True)
    return tmp_path


def test_restored_row709_defect_names_its_seed_and_location(tmp_path):
    """Positive control: the row-709 shape is found, by file and line."""
    census = seeded_verdicts(_fixture_repo(tmp_path, "UNKNOWN"))

    assert census.state == "OK"
    assert len(census.files) == 2, "precondition: the Python tool is excluded"
    assert census.findings == (
        ("scripts/deploy.sh", 2, "CLOAK_STATE", "UNKNOWN"),
    )


def test_a_distinct_seed_is_not_a_measured_outcome(tmp_path):
    """Negative control: the same fixture with a seed no branch assigns."""
    census = seeded_verdicts(_fixture_repo(tmp_path, "UNMEASURED"))

    assert census.state == "OK"
    assert len(census.files) == 2, "precondition: the same population is measured"
    assert census.findings == ()


def test_a_state_nothing_reads_is_not_a_verdict(tmp_path):
    """The later-read conjunct discriminates: it is not always satisfied."""
    census = seeded_verdicts(_fixture_repo(tmp_path, "UNKNOWN", read_state=False))

    assert census.state == "OK"
    assert len(census.files) == 2
    assert census.findings == ()


def test_unmeasurable_population_is_unknown_not_clean(tmp_path):
    """A zero denominator reports UNKNOWN, never an absence of findings."""
    (tmp_path / "scripts").mkdir()
    census = seeded_verdicts(tmp_path)

    assert census.state == "UNKNOWN"
    assert census.files == ()
    assert census.findings == ()


def test_live_shell_population_seeds_no_measured_outcome():
    census = seeded_verdicts(_REPO)

    assert census.state == "OK", (
        "the scripts/toolchain shell population was not measurable; "
        "exclusion reasons: "
        + "; ".join("%s -- %s" % reason for reason in _EXCLUSION_REASONS))
    assert len(census.files) > 0, "precondition: the denominator is nonzero"
    assert census.findings == (), (
        "a state seed is a literal a later measurement also produces, so "
        "never-ran is indistinguishable from measured-and-unresolved: "
        + "; ".join("%s:%d %s=%s" % finding for finding in census.findings))

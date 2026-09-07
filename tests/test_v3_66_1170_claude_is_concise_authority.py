"""Cut 10 leaves one concise agent contract and retires its temporary freezer."""

from __future__ import annotations

import os
from pathlib import Path
import re
import subprocess


BD_GATE_SCOPE = "repo-wide"

ROOT = Path(__file__).resolve().parents[1]
EXPECTED_HEADINGS = (
    "## A1 | Authority and scope",
    "## A2 | Authorization and state",
    "## A3 | Change lifecycle",
    "## A4 | Writer and Git safety",
    "## A5 | Verification",
    "## A6 | Release and deployment",
    "## A7 | Engineering invariants",
    "## A8 | Focused authorities and commands",
)
RETIRED = (
    "toolchain/bin/bd-contract-rules",
    "project-knowledge/CONTRACT_RULES.baseline",
    "tests/test_v3_66_1141_no_paragraph_leaves_undeclared.py",
)
FOCUSED = (
    "project-knowledge/IMPROVEMENT_BACKLOG.md",
    "project-knowledge/TOUCHED_FILE_TO_TEST.md",
    "docs/repo/ENVIRONMENT_PROVISIONING.md",
    "docs/repo/FRESH_HOST_BRINGUP.md",
    "scripts/deploy.sh",
)
SECOND_CONTRACT_MARKERS = (
    "paste it at the top of every session",
    "read this first in a fresh conversation",
    "fresh sandbox -- please bootstrap it",
    "cowork execution prompt",
    "codex execution prompt",
    "project operating instructions",
    "next-session bootstrap",
)


def _tracked_paths(root: Path = ROOT) -> set[str]:
    proc = subprocess.run(
        ["git", "-C", str(root), "ls-files", "-z"],
        capture_output=True,
        check=True,
        timeout=15,
    )
    paths = {part.decode() for part in proc.stdout.split(b"\0") if part}
    assert len(paths) > (1000 if root == ROOT else 0), (
        "tracked-path denominator is unexpectedly empty"
    )
    return paths


def _retired_residue(root: Path, tracked: set[str]) -> list[str]:
    return [
        rel for rel in RETIRED
        if rel in tracked or os.path.lexists(root / rel)
    ]


def _section_bodies(text: str) -> dict[str, str]:
    matches = list(re.finditer(r"(?m)^## (A[1-8]) \| ([^\n]+)$", text))
    ids = [match.group(1) for match in matches]
    assert len(ids) == len(set(ids)), f"duplicate final section IDs: {ids}"
    out = {}
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        out[match.group(1)] = text[match.start():end]
    return out


def _current_agent_surfaces(root: Path, tracked: set[str]) -> list[str]:
    offenders = []
    for rel in sorted(tracked):
        if not rel.endswith((".md", ".txt")) or rel in {"CLAUDE.md", "CHANGELOG.md"}:
            continue
        if rel.startswith(("docs/archive/", "tests/")):
            continue
        try:
            text = (root / rel).read_text(encoding="utf-8").casefold()
        except (OSError, UnicodeError):
            continue
        if any(marker in text for marker in SECOND_CONTRACT_MARKERS):
            offenders.append(rel)
    return offenders


def _retired_references(root: Path, tracked: set[str]) -> dict[str, list[str]]:
    allowed = {
        "CHANGELOG.md",
        "docs/repo/DOC_HYGIENE_AUDIT_v3_66_811.md",
        Path(__file__).relative_to(ROOT).as_posix(),
    }
    offenders = {}
    for retired in RETIRED:
        tokens = {retired, Path(retired).name, Path(retired).stem}
        hits = []
        for rel in sorted(tracked - allowed):
            try:
                text = (root / rel).read_text(encoding="utf-8")
            except (OSError, UnicodeError):
                continue
            if any(token in text for token in tokens):
                hits.append(rel)
        if hits:
            offenders[retired] = sorted(set(hits))
    return offenders


def test_contract_has_exactly_the_eight_approved_sections():
    text = (ROOT / "CLAUDE.md").read_text(encoding="utf-8")
    sections = _section_bodies(text)
    assert tuple(f"## {key} | {sections[key].splitlines()[0].split(' | ', 1)[1]}"
                 for key in sections) == EXPECTED_HEADINGS


def test_load_bearing_semantic_families_remain_in_their_owners():
    sections = _section_bodies((ROOT / "CLAUDE.md").read_text(encoding="utf-8"))
    expected = {
        "A1": ("sole agent-facing contract", "host", "commit", "tree", "measure"),
        "A2": ("hold", "wait", "UNKNOWN", "machine-visible", "authorization"),
        "A3": ("RED-first", "one coherent feature per cut", "merge", "deploy", "review"),
        "A4": ("sole writer", "git add -A", "force-with-lease", "gitleaks", "merge commits"),
        "A5": ("real pytest", "floor", "nonzero", "split", "exact SHA"),
        "A6": ("__version__", "CHANGELOG", "PIN_INDEX", "inode", "service down"),
        "A7": ("denominator", "negative control", "assert the precondition", "environment"),
        "A8": ("IMPROVEMENT_BACKLOG.md", "TOUCHED_FILE_TO_TEST.md", "toolchain/bin"),
    }
    assert set(sections) == set(expected)
    missing = {
        section: [token for token in tokens if token.casefold() not in sections[section].casefold()]
        for section, tokens in expected.items()
    }
    assert not any(missing.values()), f"mandatory semantic families missing: {missing}"


def test_exact_canonical_commands_and_environment_contract_remain():
    text = (ROOT / "CLAUDE.md").read_text(encoding="utf-8")
    tokens = (
        "env -u BD_INSTALL_DIR",
        "BD_DISABLE_KEEPALIVE=1",
        "PYTHONUNBUFFERED=1",
        "venv/bin/python -m pytest tests/",
        "-n 24",
        "--dist loadfile",
        "--timeout=240",
        "--timeout-method=signal",
        "--max-worker-restart=0",
        "-p no:randomly",
        'venv/bin/python toolchain/bin/bd-regen-order --work "$PWD"',
    )
    missing = [token for token in tokens if token not in text]
    assert not missing, f"canonical command tokens missing: {missing}"


def test_focused_destinations_exist_and_are_linked_once_or_more():
    text = (ROOT / "CLAUDE.md").read_text(encoding="utf-8")
    missing = [rel for rel in FOCUSED if not (ROOT / rel).is_file()]
    unlinked = [rel for rel in FOCUSED if rel not in text]
    assert not missing, f"focused authority is absent: {missing}"
    assert not unlinked, f"focused authority is not linked: {unlinked}"


def test_temporary_conservation_subsystem_is_physically_retired():
    tracked = _tracked_paths()
    assert len(RETIRED) == len(set(RETIRED)) == 3
    assert not _retired_residue(ROOT, tracked), "temporary Cut 3 machinery remains"


def test_no_current_reader_invokes_temporary_conservation_subsystem():
    offenders = _retired_references(ROOT, _tracked_paths())
    assert not offenders, f"current readers still invoke retired machinery: {offenders}"


def test_claude_is_the_only_current_agent_contract():
    offenders = _current_agent_surfaces(ROOT, _tracked_paths())
    assert not offenders, f"second agent-facing contract exists: {offenders}"


def test_helpers_fail_closed_on_duplicate_missing_renamed_and_dangling(tmp_path: Path):
    root = tmp_path / "repo"
    root.mkdir()
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    (root / "CLAUDE.md").write_text(
        "## A1 | Authority and scope\n\nfirst\n\n"
        "## A1 | Authority and scope\n\nduplicate\n",
        encoding="utf-8",
    )
    invocation = root / "README.md"
    invocation.write_text("Run toolchain/bin/bd-contract-rules now.\n", encoding="utf-8")
    renamed = root / "AGENT_START.txt"
    renamed.write_text("Read this first in a fresh conversation.\n", encoding="utf-8")
    dangling = root / RETIRED[1]
    dangling.parent.mkdir(parents=True)
    dangling.symlink_to("missing-baseline")
    subprocess.run(["git", "-C", str(root), "add", "-A"], check=True)

    tracked = _tracked_paths(root)
    assert len(tracked) == 4
    assert os.path.lexists(dangling) and not dangling.exists()
    assert "toolchain/bin/bd-contract-rules" in invocation.read_text(encoding="utf-8")
    assert "Read this first in a fresh conversation" in renamed.read_text(encoding="utf-8")
    assert RETIRED[1] in _retired_residue(root, tracked)
    assert _retired_references(root, tracked)[RETIRED[0]] == ["README.md"]
    assert _current_agent_surfaces(root, tracked) == ["AGENT_START.txt"]
    try:
        _section_bodies((root / "CLAUDE.md").read_text(encoding="utf-8"))
    except AssertionError as exc:
        assert "duplicate final section IDs" in str(exc)
    else:
        raise AssertionError("duplicate final section was accepted")


# --------------------------------------------------------------------------
# A5 said "CI is a tree-wide denominator independent of the diff". Measured,
# that was false: ci.yml hands pytest an ENUMERATED LIST of file paths and
# hands it a directory zero times, so most tracked test files are named by
# nothing. This gate re-derives the two populations and constrains the prose
# to whichever of them is true -- it is not a string pin. If CI is ever taught
# to run the tree, the measurement flips and the gate demands the opposite
# sentence.
# --------------------------------------------------------------------------

CI_WORKFLOW = ".github/workflows/ci.yml"
_TEST_PATH = re.compile(r"tests/[A-Za-z0-9_/]+\.py")
# A bare `tests` or `tests/` standing alone as an argument. The lookarounds
# keep `tests/foo.py` and `${{ ... }}` from matching.
_DIRECTORY_ARG = re.compile(r"(?<![\w/.${-])tests/?(?![\w/.-])")


def _ci_pytest_denominator(root: Path = ROOT) -> tuple[set[str], int]:
    """What ci.yml actually hands pytest: (named test files, directory args).

    PyYAML is a declared test dependency (requirements-test.txt), so an import
    failure here is an unavailable measurement and must fail the gate rather
    than skip it -- an unread workflow is UNKNOWN, and UNKNOWN is not
    permission (CLAUDE.md A2).
    """
    import yaml

    workflow = yaml.safe_load((root / CI_WORKFLOW).read_text(encoding="utf-8"))
    named: set[str] = set()
    directory_args = 0

    # (a) the gate-suite matrix: every `suites` value is a whitespace list of
    #     paths handed to pytest through ${{ matrix.suites }}.
    for job in (workflow.get("jobs") or {}).values():
        include = ((job.get("strategy") or {}).get("matrix") or {}).get("include") or []
        for entry in include:
            if "suites" in entry:
                for token in str(entry["suites"]).split():
                    (named.add(token) if token.endswith(".py") else None)
                    directory_args += len(_DIRECTORY_ARG.findall(token))

    # (b) every `run:` step that invokes pytest directly. Shell comment lines
    #     inside the block scalar are NOT arguments and are excluded here --
    #     the whole defect this gate exists for is a needle that cannot tell an
    #     argument from a comment.
    runs: list[str] = []

    def _walk(node: object) -> None:
        if isinstance(node, dict):
            if isinstance(node.get("run"), str):
                runs.append(node["run"])
            for value in node.values():
                _walk(value)
        elif isinstance(node, list):
            for value in node:
                _walk(value)

    _walk(workflow)
    for body in runs:
        if not re.search(r"\bpytest\b", body):
            continue
        code = "\n".join(
            line for line in body.splitlines() if not line.strip().startswith("#")
        )
        for match in re.finditer(r"\bpytest\b(?P<args>.*)", code, re.S):
            args = match.group("args")
            named |= set(_TEST_PATH.findall(args))
            directory_args += len(_DIRECTORY_ARG.findall(args))
    return named, directory_args


def _tracked_test_modules(root: Path = ROOT) -> set[str]:
    return {
        rel for rel in _tracked_paths(root)
        if rel.startswith("tests/") and rel.endswith(".py")
    }


# Normalised before matching: the claim is prose and reflows, and "tree-wide"
# has three natural house spellings. This family is the gate's declared
# EVASION SURFACE -- a wording outside it (say "CI sees everything") would pass
# the scan, which is why the CI half of this gate is a structural PARSE of the
# workflow and only the CLAUDE.md half is a text match. CUT_TIERING's gate-
# subject rule exempts documentation-content checks and structural CI parsing;
# this node is one of each, and the blind spot is stated rather than implied.
_TREE_WIDE_CLAIM = re.compile(r"ci is a tree[- ]?wide denominator")


def test_a5_describes_cis_pytest_denominator_as_it_is_actually_measured():
    named, directory_args = _ci_pytest_denominator()
    tracked = _tracked_test_modules()
    # Preconditions before the verdict: both populations must be real.
    assert len(named) > 100, f"CI names implausibly few test files: {len(named)}"
    assert len(tracked) > 1000, f"tests/ denominator collapsed: {len(tracked)}"
    assert not (named - tracked), f"CI names untracked paths: {sorted(named - tracked)}"

    enumerated = directory_args == 0 and len(named) < len(tracked)
    a5 = _section_bodies((ROOT / "CLAUDE.md").read_text(encoding="utf-8"))["A5"]
    flat = " ".join(a5.split()).casefold()
    if enumerated:
        assert not _TREE_WIDE_CLAIM.search(flat), (
            "A5 claims CI is a tree-wide denominator, but ci.yml hands pytest "
            f"{len(named)} named files out of {len(tracked)} tracked test "
            f"modules and hands it a directory {directory_args} times: "
            f"{len(tracked) - len(named)} are named by nothing"
        )
        assert "enumerat" in flat, (
            "CI's pytest denominator is enumerated and A5 does not say so"
        )
    else:
        assert "enumerat" not in flat, (
            "A5 calls CI's denominator enumerated, but ci.yml hands pytest "
            f"{directory_args} directory argument(s) over {len(named)} names"
        )


def test_a5_leaves_the_requirement_standing_and_says_it_is_under_review():
    a5 = _section_bodies((ROOT / "CLAUDE.md").read_text(encoding="utf-8"))["A5"]
    # Compared on collapsed whitespace: these sentences are line-wrapped prose,
    # and a requirement must not be judged moved because a reflow put a newline
    # inside it.
    flat = " ".join(a5.split())
    # The BAR. Correcting the fact must not soften, strengthen or delete any of
    # these; the operator reserved the policy question ("Leave it -- I'll decide
    # later"), so the file must also say the question is open.
    for requirement in (
        "A gate CI does not run does not exist.",
        "Every new `tests/test*.py` file declares `BD_GATE_SCOPE` or is",
        "must be directly present in a shard and `_DECLARED`",
        "Read CI status from named status/conclusion fields, not positional CLI columns.",
        "Never trim a slow CI shard or omit a required test to regain green.",
    ):
        assert requirement in flat, f"A5 requirement was moved or lost: {requirement!r}"
    assert re.search(r"(?i)operator has reserved|under review by the operator", flat), (
        "A5 corrects the fact but does not record that the requirement is the "
        "operator's open question"
    )


def test_a_name_in_a_comment_is_not_an_argument_to_pytest():
    """The control for the parse above, and the reason it is a parse.

    A regex over ci.yml's raw text counts every `tests/*.py` it can see,
    including names that appear only in prose comments. Those files are not
    handed to pytest. This asserts the two counts can only differ in that one
    direction, and that every extra the text needle finds is comment-only.
    """
    named, _ = _ci_pytest_denominator()
    raw = (ROOT / CI_WORKFLOW).read_text(encoding="utf-8")
    text_needle = set(_TEST_PATH.findall(raw))
    assert named <= text_needle, (
        f"parse found arguments the text needle cannot see: {sorted(named - text_needle)}"
    )
    comment_only = text_needle - named
    code_lines = [
        line for line in raw.splitlines() if not line.strip().startswith("#")
    ]
    for rel in sorted(comment_only):
        assert not any(rel in line for line in code_lines), (
            f"{rel} appears on a non-comment line but the parse did not "
            "collect it as an argument"
        )


def test_the_ci_denominator_helper_fails_closed_on_an_unreadable_workflow(tmp_path):
    root = tmp_path / "repo"
    (root / CI_WORKFLOW).parent.mkdir(parents=True)
    (root / CI_WORKFLOW).write_text("jobs: [unclosed\n", encoding="utf-8")
    try:
        _ci_pytest_denominator(root)
    except Exception:
        pass
    else:
        raise AssertionError("a malformed workflow was accepted as a measurement")


def test_the_tree_wide_claim_matcher_catches_its_natural_respellings():
    """EVASION FIXTURE for the text half of the A5 fact gate.

    The hazard is the sentence coming back in a house respelling. Three are
    natural here -- the hyphen dropped, the hyphen spaced, and a reflow putting
    a newline mid-claim -- and the matcher normalises whitespace and case, so it
    must catch all three. It does NOT claim to catch a paraphrase: a wording
    outside this family ("CI sees the whole tree") evades it, which is the
    declared blind spot and the reason the CI side of the gate is a structural
    parse rather than a scan.
    """
    corrected = " ".join(
        _section_bodies((ROOT / "CLAUDE.md").read_text(encoding="utf-8"))["A5"].split()
    ).casefold()
    assert not _TREE_WIDE_CLAIM.search(corrected), "the shipped A5 already matches"

    for respelling in (
        "CI is a tree-wide denominator independent of the diff.",
        "CI is a treewide denominator independent of the diff.",
        "CI is a tree wide denominator independent of the diff.",
        "CI is a tree-wide\ndenominator independent of the diff.",
    ):
        evaded = " ".join((corrected + " " + respelling).split()).casefold()
        assert _TREE_WIDE_CLAIM.search(evaded), f"respelling evaded the gate: {respelling!r}"

    # Negative control: the matcher must not fire on the corrected prose merely
    # because it discusses tree-wideness and denominators in other sentences.
    assert not _TREE_WIDE_CLAIM.search(
        " ".join("CI's pytest denominator is not the tree; a tree-wide gate is "
                 "a different thing entirely.".split()).casefold()
    )

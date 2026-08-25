"""v3.66.1233 -- backlog row 186. CI really executes the guard lane.

WHAT THE OLD GATE SCANNED. test_v3_66_1167_safety_authorities_are_single_source
closed with one line::

    assert "toolchain/bin/bd-guardcheck --tree" in ci

against the raw text of .github/workflows/ci.yml. That substring is a proxy for
two runtime properties it cannot see: whether GitHub will SCHEDULE the step and
let a nonzero exit fail the job, and whether the command, once running, actually
detects a tampered guard file. Every evasion in this file leaves the substring
intact -- `if: false`, `continue-on-error`, a narrowed trigger, the run line
moved into a YAML comment, `echo` instead of execution, `|| true`, `--selftest`,
a tree with no guards in it, and a lane that rewrites its own manifest down to
one entry before running. The audit reported the first of those; the last two
are the ones that matter most, because no text scan of any sophistication can
reach them.

WHAT THIS GATE PROVES INSTEAD, in two halves that each judge what they can see:

  SCHEDULING is structural, because triggers and step conditions have no local
  runtime. tests/ci_workflow_model.py parses the workflow and reports, for every
  step that mentions the tool, the reasons it would not run or would not fail
  the job.

  EXECUTION is behavioural. This file takes the lane's OWN run script out of the
  parsed workflow and runs it, unmodified, against nine purpose-built fixture
  trees: one clean, and one per guarded file with that file tampered. A real
  guard lane exits 0 on the clean tree reporting every guard ok, and exits
  nonzero on each tampered tree naming exactly that file. Nothing short of
  running it can distinguish that from a lane that runs, prints a clean-looking
  table and is blind to six of the seven guards.

WHY A FRESH FIXTURE TREE PER INVOCATION. A lane is allowed to write. The
manifest-narrowing evasion below edits guards.json in place before running the
checker, so a shared fixture would carry that damage into every later
invocation and the battery would be measuring its own residue.

WHAT THIS STILL DOES NOT CONSTRAIN, stated rather than implied: whether the
`gates` job is a REQUIRED check on the branch, and whether Actions is enabled
for the repository at all. Both live on GitHub, not in the tree, and no
tree-side gate can see them. They stay procedural (CLAUDE.md A5 exact-head CI).
The behavioural half also runs under `bash -e` on this host with a python shim,
which reproduces GitHub's default `bash -e {0}` failure semantics but is not
ubuntu-latest; a lane carrying an unexpandable `${{ }}` expression is refused
with an UNKNOWN diagnostic rather than guessed at.
"""
from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from ci_workflow_model import (
    TOOL,
    WORKFLOW,
    Lane,
    enabled_guard_lanes,
    guard_lanes,
)

BD_GATE_SCOPE = "repo-wide"

ROOT = Path(__file__).resolve().parents[1]
GATE_UNDER_TEST = ROOT / "tests" / "test_v3_66_1167_safety_authorities_are_single_source.py"

# The three files test_v3_66_1167 reads besides the workflow. A mirror missing
# any of them would make that gate raise for a missing-file reason and launder
# the meta-node's verdict.
MIRRORED = ("guards.json", "CLAUDE.md", "scripts/cloud-setup.sh")

# The INDEPENDENT denominator for the behavioural battery: frozen here, and
# reconciled against guards.json before any verdict. Deriving the loop from the
# manifest alone is how an eighth guard gets added to the manifest while this
# battery silently keeps exercising seven.
GUARDED = (
    "bulk_downloader/extraction_core.py",
    "bulk_downloader/session_capture.py",
    "bulk_downloader/dom_capture.py",
    "bulk_downloader/dom_recorder.py",
    "bulk_downloader/capture_bodies.py",
    "tools/capture_session.py",
    "tools/build_release.py",
)

# The tool files the lane needs in a fixture tree. bd-guardcheck loads its work
# tree resolver by absolute path relative to its own realpath, so the sibling
# must travel with it.
TOOL_FILES = (TOOL, "toolchain/bin/_bd_work_tree.py")

# MEASURED, on this host, at this base: one invocation of the live lane against
# a fixture tree of 7 guards takes 0.19s wall, worst of 8 consecutive runs.
# The rule is max(30, 6 x measured) = max(30, 1.2) = 30, which sits well under
# the 240s pytest bound in the sanctioned suite command, so the TimeoutExpired
# arm below is reachable rather than dead code.
LANE_BUDGET_S = 30

# The exact two lines of the live workflow that the evasion fixtures rewrite.
# Their occurrence count is asserted before every use.
STEP_ANCHOR = (
    '      - name: Guard files unchanged\n'
    '        run: env -u BD_INSTALL_DIR python toolchain/bin/bd-guardcheck --tree "$PWD"\n'
)
NAME_LINE = "      - name: Guard files unchanged\n"
RUN_LINE = '        run: env -u BD_INSTALL_DIR python toolchain/bin/bd-guardcheck --tree "$PWD"'
COMMAND = 'env -u BD_INSTALL_DIR python toolchain/bin/bd-guardcheck --tree "$PWD"'
GATES_HEAD = "  gates:\n    runs-on: ubuntu-latest\n    steps:\n      - uses: actions/checkout@v4\n"
PULL_REQUEST = "  pull_request:\n"

# The literal the retired proxy tested for. Every fixture in this file is
# asserted to still contain it, which is what makes each one a genuine EVASION
# of the old gate rather than a broken workflow that anything would reject.
LEGACY_PROXY = 'toolchain/bin/bd-guardcheck --tree'


# --------------------------------------------------------------------------
# fixture construction
# --------------------------------------------------------------------------
def _workflow_text() -> str:
    return (ROOT / WORKFLOW).read_text(encoding="utf-8")


def _rewrite(anchor: str, replacement: str) -> str:
    """One workflow variant, refusing an ambiguous or absent anchor."""
    text = _workflow_text()
    count = text.count(anchor)
    assert count == 1, (
        f"the fixture anchor occurs {count} times in {WORKFLOW}, expected "
        f"exactly 1; the fixture would rewrite an unknown site: {anchor!r}"
    )
    variant = text.replace(anchor, replacement)
    assert variant != text, "the fixture is byte-identical to the real workflow"
    return variant


def _tree_with_workflow(base: Path, workflow_text: str) -> Path:
    root = base / "mirror"
    (root / ".github" / "workflows").mkdir(parents=True)
    (root / "scripts").mkdir(parents=True)
    (root / WORKFLOW).write_text(workflow_text, encoding="utf-8")
    for rel in MIRRORED:
        shutil.copy2(ROOT / rel, root / rel)
        assert (root / rel).is_file(), f"the mirror is missing {rel}"
    return root


def _python_shim(base: Path) -> Path:
    """`python` and `python3` on PATH, pointing at the interpreter running the
    suite. The runner image provides these; this host may only have python3."""
    shim = base / "shim"
    shim.mkdir(parents=True)
    for name in ("python", "python3"):
        (shim / name).symlink_to(sys.executable)
        assert (shim / name).exists(), f"the {name} shim did not resolve"
    return shim


def _guard_fixture(base: Path, tamper: str | None) -> Path:
    """A minimal checkout: the manifest, the guarded files, and the tool."""
    work = base / "work"
    (work / "toolchain" / "bin").mkdir(parents=True)
    shutil.copy2(ROOT / "guards.json", work / "guards.json")
    for rel in TOOL_FILES:
        shutil.copy2(ROOT / rel, work / rel)
    os.chmod(work / TOOL, 0o755)
    for rel in GUARDED:
        destination = work / rel
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / rel, destination)
        assert destination.is_file(), f"the fixture is missing {rel}"
        if rel == tamper:
            with destination.open("ab") as handle:
                handle.write(b"\n# an unannounced edit to a release guard\n")
    if tamper is not None:
        assert (work / tamper).read_bytes() != (ROOT / tamper).read_bytes(), (
            f"the tamper fixture for {tamper} did not change the file"
        )
    return work


def _execute(lane: Lane, work: Path, shim: Path, scratch: Path):
    """Run the lane's own script the way GitHub's default shell would."""
    script = scratch / "lane.sh"
    script.parent.mkdir(parents=True, exist_ok=True)
    body = lane.script if lane.script.endswith("\n") else lane.script + "\n"
    script.write_text(body, encoding="utf-8")
    home = scratch / "home"
    temporary = scratch / "tmp"
    home.mkdir(exist_ok=True)
    temporary.mkdir(exist_ok=True)
    cwd = work if not lane.working_directory else work / lane.working_directory
    environment = {
        "PATH": os.pathsep.join([str(shim), os.environ.get("PATH", "/usr/bin:/bin")]),
        "HOME": str(home),
        "TMPDIR": str(temporary),
        "LC_ALL": "C",
        "GITHUB_WORKSPACE": str(work),
    }
    environment.update({str(key): str(value) for key, value in lane.env.items()})
    return subprocess.run(
        ["bash", "-e", str(script)],
        cwd=str(cwd),
        env=environment,
        capture_output=True,
        text=True,
        timeout=LANE_BUDGET_S,
    )


def _battery(lane: Lane, base: Path) -> list[str]:
    """Return every behavioural failure of this lane; empty means it guards.

    One clean fixture and one per guarded file, each built fresh, because a lane
    is allowed to write to the tree it is checking.
    """
    shim = _python_shim(base)
    failures: list[str] = []
    clean_report = f"{len(GUARDED)} ok, 0 drifted, 0 missing, 0 unpinned."
    for index, tamper in enumerate((None, *GUARDED)):
        cell = base / f"run{index}"
        work = _guard_fixture(cell, tamper)
        try:
            proc = _execute(lane, work, shim, cell / "scratch")
        except subprocess.TimeoutExpired:
            failures.append(
                f"UNKNOWN: the lane exceeded {LANE_BUDGET_S}s on "
                f"{'the clean fixture' if tamper is None else tamper}"
            )
            continue
        combined = proc.stdout + proc.stderr
        if tamper is None:
            if proc.returncode != 0:
                failures.append(
                    f"the clean fixture must exit 0, got {proc.returncode}: "
                    f"{combined.strip()[-400:]}"
                )
            elif clean_report not in proc.stdout:
                failures.append(
                    "the clean fixture exited 0 without reporting every guard "
                    f"ok ({clean_report!r} absent): {proc.stdout.strip()[-400:]}"
                )
            continue
        drift = [line for line in proc.stdout.splitlines() if "DRIFT" in line]
        if proc.returncode == 0:
            failures.append(f"the lane did not detect drift in {tamper}: it exited 0")
        elif len(drift) != 1 or tamper not in drift[0]:
            failures.append(
                f"the lane did not detect drift in {tamper}: it exited "
                f"{proc.returncode} but reported drift lines {drift}"
            )
    return failures


def _reconcile_denominator(manifest_path: Path) -> set[str]:
    """The frozen battery denominator must equal the shipped manifest.

    Deriving the tamper loop from guards.json alone would let an eighth guard be
    added to the manifest while this battery silently keeps exercising seven,
    and the gate would report OK over a denominator that excludes its subject.
    """
    manifest = json.loads(Path(manifest_path).read_text(encoding="utf-8"))["guards"]
    assert len(GUARDED) == len(set(GUARDED)) and len(GUARDED) > 0, (
        "the frozen denominator is empty or carries a duplicate"
    )
    assert set(GUARDED) == set(manifest), (
        "the frozen battery denominator and guards.json disagree, so this loop "
        f"would silently skip a guard: frozen={sorted(GUARDED)} "
        f"manifest={sorted(manifest)}"
    )
    return set(manifest)


def _lane_of(root: Path) -> Lane:
    """The single lane of a fixture workflow, refusing an ambiguous fixture."""
    lanes = guard_lanes(root)
    assert len(lanes) == 1, f"fixture produced {len(lanes)} candidate lanes, expected 1"
    return lanes[0]


# --------------------------------------------------------------------------
# the structural half
# --------------------------------------------------------------------------
def test_the_workflow_declares_an_enabled_guardcheck_lane():
    """The live workflow schedules a guard lane that a nonzero exit can fail."""
    lanes = guard_lanes(ROOT)
    assert lanes, (
        f"no step in {WORKFLOW} mentions {TOOL} at all; the guard-drift gate "
        "does not exist in CI"
    )
    enabled = enabled_guard_lanes(ROOT)
    assert enabled, (
        f"{WORKFLOW} declares {len(lanes)} bd-guardcheck step(s) and none of "
        "them would run and fail the job: "
        + "; ".join(lane.describe() for lane in lanes)
    )


# --------------------------------------------------------------------------
# the behavioural half
# --------------------------------------------------------------------------
def test_the_ci_guard_lane_detects_drift_in_every_guarded_file(tmp_path):
    """Run the lane's own script against a tampered copy of each guarded file."""
    lanes = guard_lanes(ROOT)
    assert lanes, f"no step in {WORKFLOW} mentions {TOOL}; nothing to execute"
    enabled = enabled_guard_lanes(ROOT)
    assert enabled, (
        "no enabled bd-guardcheck lane to execute: "
        + "; ".join(lane.describe() for lane in lanes)
    )
    _reconcile_denominator(ROOT / "guards.json")
    survivors, rejected = [], []
    for index, lane in enumerate(enabled):
        assert not lane.expressions, (
            f"UNKNOWN: {lane.describe()} carries an unexpandable ${{{{ }}}} "
            f"expression in {list(lane.expressions)}; this gate refuses to "
            "guess what it would evaluate to rather than reporting OK"
        )
        failures = _battery(lane, tmp_path / f"lane{index}")
        (survivors if not failures else rejected).append((lane, failures))
    assert survivors, (
        "every enabled bd-guardcheck lane failed the drift battery: "
        + " || ".join(f"{lane.describe()} -> {failures}" for lane, failures in rejected)
    )


# --------------------------------------------------------------------------
# evasions the structural half must reject
# --------------------------------------------------------------------------
_SCHEDULING_EVASIONS = {
    "step-if-false": (
        lambda: _rewrite(STEP_ANCHOR, STEP_ANCHOR.replace(NAME_LINE, NAME_LINE + "        if: false\n")),
        "step is conditional",
    ),
    "step-continue-on-error": (
        lambda: _rewrite(STEP_ANCHOR, STEP_ANCHOR.replace(NAME_LINE, NAME_LINE + "        continue-on-error: true\n")),
        "step is continue-on-error",
    ),
    "job-if-false": (
        lambda: _rewrite(GATES_HEAD, GATES_HEAD.replace("  gates:\n", "  gates:\n    if: false\n")),
        "job gates is conditional",
    ),
    "job-continue-on-error": (
        lambda: _rewrite(GATES_HEAD, GATES_HEAD.replace("  gates:\n", "  gates:\n    continue-on-error: true\n")),
        "job gates is continue-on-error",
    ),
    "no-checkout": (
        lambda: _rewrite(GATES_HEAD, GATES_HEAD.replace(
            "      - uses: actions/checkout@v4\n", "      - uses: actions/setup-python@v5\n")),
        "actions/checkout step precedes it",
    ),
    "non-bash-shell": (
        lambda: _rewrite(STEP_ANCHOR, STEP_ANCHOR.replace(NAME_LINE, NAME_LINE + "        shell: pwsh\n")),
        "is not the default bash",
    ),
    "or-true": (
        lambda: _rewrite(RUN_LINE, RUN_LINE + " || true"),
        "masks a nonzero exit",
    ),
    "pipe-without-pipefail": (
        lambda: _rewrite(RUN_LINE, RUN_LINE + " | tee guard.log"),
        "pipes without",
    ),
    "backgrounded": (
        lambda: _rewrite(RUN_LINE, RUN_LINE + " &"),
        "backgrounded",
    ),
    "set-plus-e": (
        lambda: _rewrite(RUN_LINE, "        run: |\n          set +e\n          "
                         + COMMAND + "\n          echo done\n"),
        "errexit off",
    ),
    "echo-only": (
        lambda: _rewrite(RUN_LINE, "        run: echo '" + LEGACY_PROXY + "'"),
        "no uncommented line invokes the tool",
    ),
    "commented-out": (
        lambda: _rewrite(RUN_LINE, "        run: |\n          # " + COMMAND + "\n          true\n"),
        "no uncommented line invokes the tool",
    ),
    "selftest-instead-of-tree": (
        lambda: _rewrite(RUN_LINE, "        run: env -u BD_INSTALL_DIR python " + TOOL
                         + " --selftest  # " + LEGACY_PROXY),
        "no uncommented line invokes the tool",
    ),
    "pr-paths-ignore": (
        lambda: _rewrite(PULL_REQUEST, PULL_REQUEST + "    paths-ignore: ['bulk_downloader/**', 'tools/**']\n"),
        "pull_request trigger is narrowed by paths-ignore",
    ),
    "pr-branches-ignore": (
        lambda: _rewrite(PULL_REQUEST, PULL_REQUEST + "    branches-ignore: ['**']\n"),
        "pull_request trigger is narrowed by branches-ignore",
    ),
    "pr-types-labeled": (
        lambda: _rewrite(PULL_REQUEST, PULL_REQUEST + "    types: [labeled]\n"),
        "is not a superset of the default",
    ),
    "pr-trigger-removed": (
        lambda: _rewrite(PULL_REQUEST, ""),
        "no pull_request trigger",
    ),
}


@pytest.mark.parametrize("evasion", sorted(_SCHEDULING_EVASIONS))
def test_a_disabled_or_faked_lane_is_rejected(evasion, tmp_path):
    """Each fixture keeps the retired substring and must still be rejected."""
    build, expected = _SCHEDULING_EVASIONS[evasion]
    variant = build()
    assert LEGACY_PROXY in variant, (
        f"{evasion} no longer contains {LEGACY_PROXY!r}, so it does not prove "
        "an evasion of the substring gate this cut retired"
    )
    assert isinstance(yaml.safe_load(variant), dict), f"{evasion} is not valid YAML"
    root = _tree_with_workflow(tmp_path, variant)
    lane = _lane_of(root)
    assert lane.disqualifiers, f"{evasion} was accepted as a working guard lane"
    assert any(expected in reason for reason in lane.disqualifiers), (
        f"{evasion} was rejected for the wrong reason: expected {expected!r} in "
        f"{list(lane.disqualifiers)}"
    )
    assert not enabled_guard_lanes(root), f"{evasion} still leaves an enabled lane"


# --------------------------------------------------------------------------
# evasions ONLY execution can see
# --------------------------------------------------------------------------
_BLIND_SPOTS = {
    # Enabled, unchained, and pointed at a subtree that holds no guards. The
    # tool exits 2 ("no guards baseline") and the CLEAN control is the only
    # thing in the tree that notices.
    "tree-without-guards": lambda: _rewrite('--tree "$PWD"', '--tree "$PWD/toolchain"'),
    # The lane rewrites its own manifest down to one entry, then runs. It exits
    # 0 on a clean tree, prints a plausible table, and is blind to six of the
    # seven guards. Only the per-guard tamper loop can see that.
    "manifest-narrowed-to-one": lambda: _rewrite(
        RUN_LINE,
        "        run: |\n"
        "          python -c \"import json;d=json.load(open('guards.json'));"
        "d['guards']={'tools/build_release.py':d['guards']['tools/build_release.py']};"
        "json.dump(d,open('guards.json','w'))\"\n"
        "          " + COMMAND + "\n"),
    # The lane discards the report. Exit codes still propagate, so a rc-only
    # control passes -- but bd-guardcheck's own history is a run that printed
    # "0 ok, 0 drifted, 7 missing." and exited 0, certifying a tree it had never
    # read. Without the report there is no evidence the tree was read at all,
    # and unmeasurable is a failing third state, not a pass.
    "output-discarded": lambda: _rewrite(
        RUN_LINE,
        '        run: |\n          python3 toolchain/bin/bd-guardcheck --tree "$PWD" > guard.log\n'),
    # The lane fails on drift but filters the report, so nothing shows WHICH
    # guard drifted. A nonzero exit alone cannot distinguish "detected drift in
    # this file" from "failed for an unrelated reason on every fixture".
    "drift-not-attributed": lambda: _rewrite(
        RUN_LINE,
        '        run: |\n'
        '          if python3 toolchain/bin/bd-guardcheck --tree "$PWD" > guard.log; then\n'
        "            cat guard.log\n"
        "          else\n"
        "            grep -v DRIFT guard.log\n"
        "            exit 1\n"
        "          fi\n"),
}


@pytest.mark.parametrize("evasion", sorted(_BLIND_SPOTS))
def test_a_structurally_perfect_lane_can_still_fail_the_battery(evasion, tmp_path):
    """The evasions no parse can reach: a lane that is scheduled, unchained and
    failure-propagating, and is still not guarding anything."""
    variant = _BLIND_SPOTS[evasion]()
    assert LEGACY_PROXY in variant, f"{evasion} does not evade the retired substring gate"
    root = _tree_with_workflow(tmp_path / "tree", variant)
    lane = _lane_of(root)
    assert not lane.disqualifiers, (
        f"{evasion} was rejected structurally ({list(lane.disqualifiers)}); it "
        "is supposed to be invisible to the parse, which is why the behavioural "
        "half exists"
    )
    failures = _battery(lane, tmp_path / "battery")
    assert failures, f"{evasion} survived the drift battery"
    if evasion == "tree-without-guards":
        assert any("the clean fixture must exit 0" in failure for failure in failures), (
            f"{evasion} failed for the wrong reason: {failures}"
        )
    elif evasion == "output-discarded":
        assert any("without reporting every guard ok" in f for f in failures), (
            f"{evasion} must fail on the missing report specifically: {failures}"
        )
    elif evasion == "drift-not-attributed":
        unattributed = [f for f in failures if "reported drift lines" in f]
        assert len(unattributed) == len(GUARDED), (
            f"{evasion} must fail attribution on all {len(GUARDED)} tampers, "
            f"not on the exit code: {failures}"
        )
        assert not any("the clean fixture" in f for f in failures), (
            f"{evasion} must pass the clean control: {failures}"
        )
    else:
        blind = [f for f in failures if "it exited 0" in f]
        assert len(blind) == len(GUARDED) - 1, (
            f"{evasion} should be blind to exactly {len(GUARDED) - 1} guards, "
            f"got {len(blind)}: {failures}"
        )
        assert not any("tools/build_release.py" in f for f in blind), (
            "the one guard left in the narrowed manifest should still be seen: "
            f"{blind}"
        )


def test_an_eighth_guard_cannot_enter_the_manifest_unexercised(tmp_path):
    """The reconciliation that stops the battery's denominator going stale."""
    live = _reconcile_denominator(ROOT / "guards.json")
    assert live == set(GUARDED), "the live manifest and the frozen tuple disagree"
    grown = json.loads((ROOT / "guards.json").read_text(encoding="utf-8"))
    grown["guards"]["bulk_downloader/an_eighth_guard.py"] = "0" * 64
    path = tmp_path / "guards.json"
    path.write_text(json.dumps(grown), encoding="utf-8")
    assert len(json.loads(path.read_text(encoding="utf-8"))["guards"]) == len(GUARDED) + 1, (
        "the fixture manifest did not actually grow"
    )
    with pytest.raises(AssertionError, match="would silently skip a guard"):
        _reconcile_denominator(path)


# --------------------------------------------------------------------------
# the over-sensitivity control
# --------------------------------------------------------------------------
_BENIGN_VARIANTS = {
    # A folded scalar spelling the same command. The parsed run value is
    # byte-identical, so nothing about YAML spelling may matter.
    "folded-scalar": lambda: _rewrite(
        RUN_LINE,
        '        run: >-\n          env -u BD_INSTALL_DIR python\n'
        '          toolchain/bin/bd-guardcheck --tree "$PWD"'),
    # A different but entirely valid invocation: a leading comment, python3
    # instead of the env wrapper, the workspace instead of $PWD, and an explicit
    # manifest. An exact-token-tuple check would reject this and cry wolf.
    "different-valid-invocation": lambda: _rewrite(
        RUN_LINE,
        "        run: |\n          # the seven release guards must be byte-identical\n"
        '          python3 toolchain/bin/bd-guardcheck --tree "$GITHUB_WORKSPACE" --guards guards.json\n'),
    # A pipeline that genuinely propagates. The rule must test the semantics,
    # not the `|` character.
    "pipeline-with-pipefail": lambda: _rewrite(
        RUN_LINE,
        "        run: |\n          set -o pipefail\n          " + COMMAND + " | tee guard.log\n"),
    # errexit is fired at the failing command, before the separator is reached
    # -- measured `bash -e` behaviour -- so a trailing `; true` does NOT mask.
    "trailing-semicolon-true": lambda: _rewrite(RUN_LINE, RUN_LINE + "; true"),
    # A harmless step-level environment addition, which the runner honours.
    "step-env-block": lambda: _rewrite(
        STEP_ANCHOR,
        STEP_ANCHOR.replace(NAME_LINE, NAME_LINE + "        env:\n          LC_ALL: C\n")),
    # A WIDENED trigger. The types rule tests supersetting, not presence.
    "widened-pr-types": lambda: _rewrite(
        PULL_REQUEST,
        PULL_REQUEST + "    types: [opened, synchronize, reopened, ready_for_review]\n"),
    # An additional push branch. Narrowing is the hazard; adding is not.
    "extra-push-branch": lambda: _rewrite(
        "  push:\n    branches: [main]\n", "  push:\n    branches: [main, release]\n"),
}


@pytest.mark.parametrize("variant", sorted(_BENIGN_VARIANTS))
def test_benign_workflow_variants_are_still_accepted(variant, tmp_path):
    """Converting a scan into a behavioural check must not start failing on
    correct input. A gate that cries wolf gets switched off."""
    text = _BENIGN_VARIANTS[variant]()
    assert isinstance(yaml.safe_load(text), dict), f"{variant} is not valid YAML"
    root = _tree_with_workflow(tmp_path / "tree", text)
    lane = _lane_of(root)
    assert not lane.disqualifiers, (
        f"the benign variant {variant} was rejected: {list(lane.disqualifiers)}"
    )
    if variant == "folded-scalar":
        live = yaml.safe_load(_workflow_text())["jobs"][lane.job]["steps"][lane.step_index]["run"]
        assert lane.script == live, (
            "the folded spelling did not parse to the same command as the live "
            f"workflow: {lane.script!r} != {live!r}"
        )
    if variant in ("different-valid-invocation", "step-env-block", "trailing-semicolon-true"):
        failures = _battery(lane, tmp_path / "battery")
        assert not failures, (
            f"the benign variant {variant} was accepted structurally but failed "
            f"the drift battery: {failures}"
        )


# --------------------------------------------------------------------------
# the meta-node: the repository's own gate must reject an evaded workflow
# --------------------------------------------------------------------------
def _load_gate_under_test():
    """Load test_v3_66_1167 by path, WITHOUT pytest's assertion rewriting.

    That is deliberate and it is why every assertion this node depends on
    carries an explicit message: an unrewritten bare `assert x` raises
    AssertionError('') and would match any `pytest.raises(match=...)` clause
    never -- and, worse, the roster and pin-leak assertions in the same function
    could otherwise supply a green for the wrong reason.
    """
    spec = importlib.util.spec_from_file_location("_row186_gate_under_test", GATE_UNDER_TEST)
    assert spec is not None and spec.loader is not None, f"cannot load {GATE_UNDER_TEST}"
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_repository_gate_rejects_a_disabled_guard_step(tmp_path, monkeypatch):
    """Row 186 itself: point 1167 at a workflow whose guard step is `if: false`
    and require it to fail. The substring it used to test is still present."""
    evaded = _rewrite(STEP_ANCHOR, STEP_ANCHOR.replace(NAME_LINE, NAME_LINE + "        if: false\n"))
    assert LEGACY_PROXY in evaded, (
        "the fixture must still satisfy the retired substring proxy, or it "
        "proves nothing about row 186"
    )
    honest = _tree_with_workflow(tmp_path / "honest", _workflow_text())
    evaded_root = _tree_with_workflow(tmp_path / "evaded", evaded)
    module = _load_gate_under_test()
    subject = module.test_guards_json_is_the_only_embedded_guard_hash_authority

    # POSITIVE CONTROL FIRST. If the mirror itself were malformed the negative
    # arm below could fail for a reason that has nothing to do with row 186.
    monkeypatch.setattr(module, "ROOT", honest)
    subject()

    monkeypatch.setattr(module, "ROOT", evaded_root)
    with pytest.raises(AssertionError, match="bd-guardcheck"):
        subject()

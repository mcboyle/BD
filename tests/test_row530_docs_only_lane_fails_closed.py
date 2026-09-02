"""Row 530 -- the docs-only lane's classifier is a SAFETY BOUNDARY, so gate it.

WHAT THE ROW ACTUALLY ASKED FOR. Not a saving: a definition. "A lane that
decides for itself what counts as runtime is a lane a product change can be
smuggled through, and getting that denominator wrong is far worse than the tax
it avoids."  So the subject of this file is bd-docs-only's DENOMINATOR, and its
assertions are about the tree rather than about any one diff.

Its subject is which paths of THIS TREE can be proven inert, and every
population it checks is derived from `git ls-files`, so no changed path selects
it and bd-band-derive can never band it.  That is why it is repo-wide and
declared into a CI shard by hand.

THE THREE THINGS A GATE HERE HAS TO DO
--------------------------------------
1. Prove the ALLOW set is small, derived, and disjoint from every runtime and
   evidence population -- measured over the whole tracked tree with an exact
   nonzero denominator, not over a sample.  A classifier whose allow set has
   quietly grown to include a new top-level directory is the failure this row
   exists to prevent, and it would look like nothing at all.
2. Prove each refusal category answers with ITS OWN reason, against the REAL
   tree and not only against a fixture.  A7: a diagnostic that collapses
   distinct failures costs the investigation.  Each adversarial candidate below
   is the passing docs-only candidate plus EXACTLY ONE path, so the refusal it
   earns cannot be some unrelated earlier condition firing (A5).
3. Prove a genuinely docs-only candidate is still owed the four correctness
   gate families with a NONZERO count each.  The lane skips the release tax; a
   lane that also skipped the gates would be worse than the tax.

WHAT IS DELIBERATELY NOT ASSERTED: how long any of this takes, and how much the
lane saves.  Duration is a property of the runner (CLAUDE.md A7's identity-vs-
content rule) and the saving is a measurement recorded in the cut's evidence,
not a number a gate can defend.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

# Its subject is which tracked paths of this tree can be proven inert, which is
# a property of the tree and of no diff.
BD_GATE_SCOPE = "repo-wide"

REPO = Path(__file__).resolve().parent.parent
TOOL = REPO / "toolchain" / "bin" / "bd-docs-only"
SEC = REPO / "toolchain" / "bin" / "bdtools_sec.py"
DECLARATION = REPO / "tests" / "test_v3_66_939_ci_gate_shards_cover_every_gate.py"
CI = REPO / ".github" / "workflows" / "ci.yml"
THIS = "tests/" + Path(__file__).name

DOCS_ONLY, RUNTIME, UNKNOWN = 0, 1, 2

# One adversarial category per line of the row's acceptance clause, plus the
# three this tool's own shape adds: the declared import baseline, the classifier
# itself, and a path that is simply outside the documentation corpus.
#
# (label, repo-relative path to disturb, how to disturb it, expected reason)
ADVERSARIAL = [
    ("product source", "bulk_downloader/db.py", "append", "RUNTIME-PRODUCT-SOURCE"),
    ("a template", "bulk_downloader/static/manifest.json", "append", "RUNTIME-TEMPLATE"),
    ("a frontend asset", "frontend/src/main.tsx", "append", "RUNTIME-FRONTEND"),
    ("a generated artifact", "PIN_INDEX.json", "smuggle",
     "EVIDENCE-GENERATED-NOT-AT-FIXPOINT"),
    ("a CI workflow", ".github/workflows/ci.yml", "append", "EVIDENCE-GATE-OR-CI"),
    ("a test", "tests/test_contracts.py", "append", "EVIDENCE-TEST"),
    ("the deploy path", "scripts/deploy.sh", "append", "RUNTIME-DEPLOY-OR-INSTALL"),
    ("the import declaration", "tools/decomp/import_graph_baseline.json", "append",
     "DECLARED-IMPORT-BASELINE"),
    ("the classifier itself", "toolchain/bin/bd-docs-only", "append",
     "EVIDENCE-GATE-OR-CI"),
    ("an executable beside the docs", "project-knowledge/spa_shot.py", "append",
     "OUTSIDE-DOC-CORPUS"),
]

REQUIRED_FAMILIES = {"freshness", "register", "doc-truth", "gate-shard"}


def _python() -> str:
    """The repository interpreter, proven present. A6 forbids falling through."""
    for candidate in (REPO / "venv" / "bin" / "python", Path(sys.executable)):
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate)
    raise AssertionError("no usable interpreter; the measurement is UNKNOWN")


def _run(argv, cwd=None, timeout=900):
    return subprocess.run(argv, cwd=str(cwd or REPO), capture_output=True,
                          text=True, check=False, timeout=timeout)


def _git(cwd, *args, check=True):
    result = _run(["git", "-C", str(cwd), *args], cwd=REPO, timeout=300)
    if check:
        assert result.returncode == 0, "git %s failed: %s" % (" ".join(args), result.stderr)
    return result.stdout


def _classify(repo, base, head):
    result = _run([_python(), str(TOOL), "classify", "--repo", str(repo),
                   "--base", base, "--head", head, "--json"])
    assert result.returncode in (DOCS_ONLY, RUNTIME), (
        "classify returned %d (UNKNOWN or crash), which is never permission:\n%s\n%s"
        % (result.returncode, result.stdout[-2000:], result.stderr[-2000:]))
    return result.returncode, json.loads(result.stdout)


def _census(commit="HEAD"):
    result = _run([_python(), str(TOOL), "census", "--repo", str(REPO),
                   "--commit", commit, "--json"])
    assert result.returncode == DOCS_ONLY, (
        "census could not be taken, so the denominator is UNKNOWN:\n%s" % result.stderr[-2000:])
    return json.loads(result.stdout)


def _tracked():
    raw = _git(REPO, "ls-files", "-z")
    paths = [p for p in raw.split("\0") if p]
    assert len(paths) > 1000, (
        "the tracked denominator collapsed to %d paths; every assertion below "
        "would pass vacuously" % len(paths))
    return paths


@pytest.fixture(scope="module")
def replayed():
    """One disposable exact-HEAD worktree carrying a passing docs-only candidate
    and, on top of it, one adversarial sibling per category.

    Built from HEAD alone and never from a named branch: CLAUDE.md A7 records
    that in a shallow clone an ancestry question is UNKNOWN until history is
    deepened, and a gate that needs history it may not have is a gate that
    reports UNKNOWN in CI forever.
    """
    if not TOOL.is_file():
        pytest.fail("bd-docs-only is absent; this gate's subject does not exist")
    root = Path(tempfile.mkdtemp(prefix="bd-row530-"))
    checkout = root / "candidate"
    _git(REPO, "worktree", "add", "--quiet", "--detach", str(checkout), "HEAD")
    try:
        base = _git(checkout, "rev-parse", "HEAD").strip()
        venv = REPO / "venv"
        if venv.is_dir() and not (checkout / "venv").exists():
            os.symlink(str(venv), str(checkout / "venv"))

        # THE PASSING CANDIDATE. A new document under project-knowledge, so the
        # manifest that hashes that directory also moves -- the delta therefore
        # exercises BOTH allow paths this tool has, the documentation corpus and
        # the regeneration fixpoint, rather than only the easy one.
        doc = checkout / "project-knowledge" / "ROW530_REPLAY_FIXTURE.md"
        doc.write_text("# row 530 replay fixture\n\nA document and nothing else.\n",
                       encoding="utf-8")
        seed = _run([_python(), "toolchain/bin/bd-kb-sync", "seed", "project-knowledge"],
                    cwd=checkout)
        assert seed.returncode == 0, "the knowledge manifest could not be seeded: %s" % seed.stderr
        _git(checkout, "add", "--", "project-knowledge/ROW530_REPLAY_FIXTURE.md",
             "project-knowledge/STATIC_KB_MANIFEST.json")
        _git(checkout, "-c", "user.email=row530@example.invalid", "-c", "user.name=row530",
             "commit", "-q", "-m", "row530 replay: a document and its manifest")
        good = _git(checkout, "rev-parse", "HEAD").strip()

        heads = {}
        for label, relative, how, _reason in ADVERSARIAL:
            _git(checkout, "checkout", "-q", good)
            target = checkout / relative
            assert target.is_file(), (
                "the adversarial subject %s does not exist in this tree, so the case "
                "would prove nothing" % relative)
            before = target.read_bytes()
            if how == "append":
                target.write_bytes(before + b"\n")
            else:
                text = target.read_text(encoding="utf-8")
                assert text.lstrip().startswith("{"), relative
                target.write_text(text.replace("{", '{"row530_smuggled": 1,', 1),
                                  encoding="utf-8")
            assert target.read_bytes() != before, (
                "%s was not actually disturbed; the case would be a no-op" % relative)
            _git(checkout, "add", "--", relative)
            staged = _git(checkout, "diff", "--cached", "--name-only").strip().splitlines()
            assert staged == [relative], (
                "the adversarial delta must differ from the passing one by EXACTLY one "
                "path, got %r" % staged)
            _git(checkout, "-c", "user.email=row530@example.invalid",
                 "-c", "user.name=row530", "commit", "-q", "-m", "row530 adversarial: " + label)
            heads[label] = _git(checkout, "rev-parse", "HEAD").strip()
        _git(checkout, "checkout", "-q", good)
        yield {"repo": checkout, "base": base, "good": good, "heads": heads}
    finally:
        _run(["git", "-C", str(REPO), "worktree", "remove", "--force", "--", str(checkout)])
        shutil.rmtree(root, ignore_errors=True)


def test_the_classifier_selftest_passes_with_a_nonzero_denominator():
    """Its own fixture is a real git repository carrying the real trio authority."""
    result = _run([_python(), str(TOOL), "selftest"])
    passes = [line for line in result.stdout.splitlines() if line.startswith("PASS")]
    assert result.returncode == 0, result.stdout[-4000:] + result.stderr[-2000:]
    assert "SELFTEST PASS" in result.stdout
    assert len(passes) >= 15, (
        "the selftest reported only %d PASS lines; a shrinking battery is how a "
        "green result stops meaning anything" % len(passes))


def test_every_derived_population_is_nonzero_on_this_tree():
    """A population that has silently stopped seeing its subject reports EMPTY,
    and an empty deny population is a deny that can never fire."""
    census = _census()
    assert census["populations"], "the census named no populations at all"
    empty = sorted(name for name, count in census["populations"].items() if count <= 0)
    assert not empty, "these derived populations are EMPTY on this tree: %s" % empty
    assert census["doc_current"] > 0 and census["doc_historical"] > 0
    assert len(census["chain_outputs"]) > 0
    assert len(census["trio"]) == 3
    assert census["tracked"] > 1000, census["tracked"]


def test_the_roots_are_derived_from_the_trees_own_authorities():
    """Each root is located by CONTENT. A typed list is what the row refuses."""
    census = _census()
    roots = census["roots"]
    package = REPO / roots["package"] / "__init__.py"
    assert package.is_file() and '__version__ = "' in package.read_text(encoding="utf-8")
    assert (REPO / roots["tests"] / "conftest.py").is_file()
    assert (REPO / roots["spa"] / "package.json").is_file()
    assert (REPO / roots["deploy"] / "deploy.sh").is_file()
    assert ".github" in roots["gate"], roots["gate"]
    # The classifier lives inside a root the regeneration chain names, which is
    # what makes it un-editable through its own lane.
    assert any(str(TOOL.relative_to(REPO)).startswith(root + "/") for root in roots["gate"]), (
        "bd-docs-only is outside every gate root, so it could edit itself through "
        "its own lane: %s" % roots["gate"])


def test_the_allow_set_is_a_derived_minority_disjoint_from_every_runtime_root():
    """The whole-tree fail-closed proof, with an exact nonzero denominator.

    Not a sample and not the delta: every tracked path is checked, because the
    property being defended is that nothing outside three derived sets can ever
    be called inert.
    """
    tracked = _tracked()
    census = _census()
    roots = census["roots"]
    sys.path.insert(0, str(SEC.parent))
    import bdtools_sec as sec  # noqa: PLC0415 -- the authority, imported once

    current, historical = sec.markdown_corpus_from_listing(tracked)
    allow = set(current) | set(historical) | set(census["chain_outputs"]) | set(census["trio"])
    assert allow, "the allow set is empty; every assertion below is vacuous"
    assert len(allow) < len(tracked) / 2, (
        "the allow set covers %d of %d tracked paths -- that is not a minority and "
        "not a boundary" % (len(allow), len(tracked)))

    runtime_roots = [roots["package"] + "/", roots["spa"] + "/", roots["deploy"] + "/"]
    runtime_roots += [root + "/" for root in roots["gate"]]
    covered = 0
    leaked = []
    for path in tracked:
        if not any(path.startswith(root) for root in runtime_roots):
            continue
        covered += 1
        if path in allow and path not in set(census["trio"]) | set(census["chain_outputs"]):
            leaked.append(path)
    assert covered > 500, (
        "only %d tracked paths sit under a runtime or gate root; the denominator "
        "has collapsed" % covered)
    assert not leaked, (
        "these paths are under a runtime or gate root and are STILL in the allow "
        "set: %s" % leaked[:20])

    # Every test file except the release pin and the chain's own products must
    # be outside the allow set. tests/source_window_hashes.json is a chain
    # output that happens to live under the test root; it is judged by whether
    # this tree reproduces it, never by the directory it sits in.
    tests_root = roots["tests"] + "/"
    test_paths = [p for p in tracked if p.startswith(tests_root)]
    assert len(test_paths) > 500, len(test_paths)
    authored_allowed = (set(test_paths) & allow) - set(census["trio"]) \
        - set(census["chain_outputs"])
    assert not authored_allowed, sorted(authored_allowed)[:20]
    executable_tests = [p for p in test_paths if p.endswith(".py")]
    assert len(executable_tests) > 500, len(executable_tests)
    assert not (set(executable_tests) & allow) - set(census["trio"]), (
        "a tracked .py under the test root is in the allow set")


def test_the_markdown_corpus_rule_has_exactly_one_definition():
    """Two hand-maintained denominators drift (A8). This one must not be two."""
    source = SEC.read_text(encoding="utf-8")
    assert source.count('("project-knowledge",), ("docs",)') == 1, (
        "the documentation membership rule appears more than once in the shared "
        "authority; a second copy is the drift the row refuses")
    tool = TOOL.read_text(encoding="utf-8")
    assert "markdown_corpus_from_listing" in tool
    assert '("project-knowledge",)' not in tool, (
        "bd-docs-only has grown its own copy of the corpus rule instead of asking "
        "the shared authority")


def test_a_real_tree_docs_only_candidate_is_accepted(replayed):
    rc, verdict = _classify(replayed["repo"], replayed["base"], replayed["good"])
    assert rc == DOCS_ONLY, (verdict["verdict"], verdict["refusals"])
    assert verdict["verdict"] == "DOCS-ONLY"
    assert set(verdict["allowed"]) == set(verdict["changed"]), verdict
    reasons = set(verdict["allowed"].values())
    assert "DOC-CORPUS" in reasons and "REGENERATION-CHAIN-FIXPOINT" in reasons, reasons
    assert verdict["fixpoint_proved"], (
        "the generated artifact in this delta was allowed WITHOUT the fixpoint run, "
        "which would make it allowed by name")


def test_a_docs_only_candidate_still_owes_four_nonzero_gate_families(replayed):
    """The lane skips the release tax and never the correctness gates."""
    rc, verdict = _classify(replayed["repo"], replayed["base"], replayed["good"])
    assert rc == DOCS_ONLY
    gates = verdict["required_gates"]
    assert set(gates["families"]) == REQUIRED_FAMILIES, gates["families"].keys()
    assert gates["tests_examined"] > 500, gates["tests_examined"]
    for family, detail in gates["families"].items():
        assert detail["count"] > 0, "gate family %r has a ZERO denominator" % family
        for member in detail["members"]:
            assert (REPO / member).is_file() or (Path(replayed["repo"]) / member).is_file(), (
                "%s names %s, which is not a file" % (family, member))


@pytest.mark.parametrize("label,relative,how,reason",
                         ADVERSARIAL, ids=[case[0] for case in ADVERSARIAL])
def test_each_adversarial_category_is_refused_for_its_own_reason(
        replayed, label, relative, how, reason):
    """One path more than a candidate that PASSES, so nothing else can refuse it."""
    rc, verdict = _classify(replayed["repo"], replayed["base"], replayed["heads"][label])
    assert rc == RUNTIME, (label, verdict["verdict"], verdict.get("allowed"))
    reasons = {row["reason"] for row in verdict["refusals"]}
    assert reasons == {reason}, (
        "%s should refuse for %s alone, got %s -- a shared or extra reason sends the "
        "reader to the wrong repair" % (label, reason, sorted(reasons)))
    refused = {row["path"] for row in verdict["refusals"]}
    assert relative in refused, (relative, refused)


def test_every_reason_code_is_reachable_and_none_is_shared():
    """A code nothing can produce is a diagnostic that does not exist."""
    census = _census()
    declared = census["reasons"]
    assert len(declared) == len(set(declared)), declared
    exercised = {case[3] for case in ADVERSARIAL}
    selftest = _run([_python(), str(TOOL), "selftest"]).stdout
    for code in declared:
        assert code in exercised or code in selftest, (
            "reason %s is declared but no case in this tree produces it" % code)


def test_a_crash_exits_UNKNOWN_and_never_the_refused_code(tmp_path):
    """EXIT 1 MEANS REFUSED AND NOTHING ELSE.

    Python's own uncaught-exception status is 1, which is also this tool's
    "runtime-affecting" verdict. bd-verify-cut extracts the BASE commit's
    classifier so a candidate cannot approve itself, and the first draft of that
    extraction copied ONE file into a temp directory where the shared
    bdtools_sec authority is not. The ImportError exited 1 and the harness
    reported a clean RUNTIME-AFFECTING verdict for a candidate the classifier
    had never looked at -- a refusal for the wrong reason, which A7 counts as a
    cost to the investigation and not merely to the message.

    So the misplaced copy is reproduced here, exactly, and it must say UNKNOWN.
    """
    orphan = tmp_path / "bd-docs-only"
    orphan.write_bytes(TOOL.read_bytes())
    assert not (tmp_path / "bdtools_sec.py").exists(), "the fixture must be an ORPHAN copy"
    result = subprocess.run(
        [_python(), str(orphan), "census", "--repo", str(REPO)],
        capture_output=True, text=True, check=False, timeout=300,
        cwd=str(tmp_path), env={**os.environ, "PYTHONPATH": ""})
    assert result.returncode == UNKNOWN, (
        "an orphaned copy exited %d; 1 would be indistinguishable from a clean "
        "refusal:\n%s" % (result.returncode, (result.stderr or result.stdout)[-1500:]))
    assert "UNKNOWN, not permission" in result.stderr, result.stderr[-1000:]
    assert "bdtools_sec" in result.stderr, (
        "the refusal must name the missing authority, not collapse into a generic "
        "message: %s" % result.stderr[-500:])

    # POSITIVE CONTROL: the same argv from the tool's real home exits 0, so the
    # case above failed for the missing module and not for the arguments.
    control = subprocess.run(
        [_python(), str(TOOL), "census", "--repo", str(REPO)],
        capture_output=True, text=True, check=False, timeout=300)
    assert control.returncode == DOCS_ONLY, control.stderr[-1000:]


def test_this_gate_is_declared_and_scheduled_in_ci():
    """A gate CI does not run does not exist (A5)."""
    assert BD_GATE_SCOPE == "repo-wide"
    assert THIS in DECLARATION.read_text(encoding="utf-8"), (
        "%s is not in the independent _DECLARED set" % THIS)
    assert THIS in CI.read_text(encoding="utf-8"), (
        "%s is in no workflow shard, so it would leave a green tick having never run"
        % THIS)

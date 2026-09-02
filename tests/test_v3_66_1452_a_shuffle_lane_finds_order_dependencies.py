"""v3.66.1452 -- the shuffle lane exists, is CONTAINED, and cannot block a merge.

`toolchain/bin/bd-shuffle-lane` runs the test suite in a randomized order with a
recorded seed, so a cross-file order dependency surfaces on purpose instead of
by luck. Backlog rows 491, 516 and 610 are three such dependencies already
filed; row 610's note carries the giveaway shape -- the file "passes in
isolation" and fails beside one particular neighbour, and `--dist loadfile`
gives it a different neighbour every run.

THE MEASUREMENT THAT MADE THE CUT NECESSARY, and the one that made it dangerous.
At v3.66.1451 pytest-randomly was NOT INSTALLED, so the `-p no:randomly` token
in the CLAUDE.md A5 canonical command guarded a plugin that did not exist -- a
no-op that had never once changed a run. That is why nothing here had ever been
order-checked. But pytest AUTO-LOADS a plugin from its entry point, so merely
installing pytest-randomly into the repository venv would turn shuffling on for
every pytest invocation that does NOT carry that token: the affected-band lane,
`bd-precut --gate`, every ci.yml gate-suite shard, and most of the sub-pytest
children the tracked tests spawn. CLAUDE.md A5 is explicit that a different
plugin is a different experiment and cannot authorize merge. So the plugin is
declared in a manifest no default installer converges, and lives in a private
directory only the lane puts on PYTHONPATH.

THIS FILE IS THE CONTAINMENT, and every assertion in it is about the TREE rather
than about a diff -- which is why it declares itself repo-wide. Four things must
stay true or the lane stops being a second lane and starts being a change to the
first one:

  1. the A5 canonical command's exact BYTES are unchanged, `-p no:randomly`
     included, so the deterministic lane stays deterministic;
  2. pytest-randomly is declared ONLY in requirements-shuffle.txt, never in a
     manifest that deploy.sh, cloud-setup.sh, provision_test_host.sh or ci.yml
     installs;
  3. nothing on the merge path invokes the lane, which is what "advisory"
     actually means here -- see the tool's docstring for the promotion recipe,
     and note that promoting it DELETES an assertion in this file on purpose;
  4. the planted control pair really is order-dependent, and really is invisible
     to `pytest tests/`.

WHAT THIS FILE DELIBERATELY DOES NOT ASSERT. It does not run the shuffled suite
and it does not require pytest-randomly to be present: a gate that failed on a
host which had never provisioned the lane would be failing for an environmental
reason, the over-sensitive direction CLAUDE.md A7 counts as equal to a false
clean. The two assertions that genuinely need the plugin SKIP with a named
reason and say what to run. Everything else -- including a real subprocess proof
that an absent plugin returns UNKNOWN rather than OK, and a real subprocess
proof that the control pair is order-dependent -- runs everywhere, CI included.
"""
from __future__ import annotations

import hashlib
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

BD_GATE_SCOPE = "repo-wide"

_REPO = Path(__file__).resolve().parents[1]
_TOOL = _REPO / "toolchain" / "bin" / "bd-shuffle-lane"
_MANIFEST = _REPO / "requirements-shuffle.txt"
_CONTROL_DIR = _REPO / "tests" / "fixtures" / "shuffle_control"
_ALPHA = _CONTROL_DIR / "order_probe_alpha.py"
_BETA = _CONTROL_DIR / "order_probe_beta.py"
_PLUGIN = "pytest-randomly"
_CONTROL_MARKER = "BD-SHUFFLE-CONTROL: order dependency observed"

# The A5 canonical full-suite command, pinned by DIGEST rather than by a
# substring. A substring pin (which two other files already carry, at
# test_v3_66_939...:759 and test_v3_66_1170...:154) proves one token survived; a
# digest proves EVERY token did, which is what "every token is load-bearing"
# claims. Measured on the tree this cut was written against.
#
# TO RE-PIN DELIBERATELY: change the command in CLAUDE.md first, then run
#     venv/bin/python -c "import hashlib,pathlib; t=pathlib.Path('CLAUDE.md')\
#         .read_text(); a=t[t.index('## A5 |'):t.index('## A6 |')]; \
#         l=[x for x in a.splitlines() if x.startswith('env -u BD_INSTALL_DIR') \
#         and '-m pytest tests/' in x and '-n 24' in x][0]; \
#         print(hashlib.sha256(l.encode()).hexdigest())"
# and paste the result here IN THE SAME COMMIT. Never re-pin to make this green.
_CANONICAL_SHA256 = "e69da45ce10dc7d7365268a0af01bd018e20bb0f5d1aa1ac90d1e488afff4e59"
_CANONICAL_LEN = 194

# The installers that DO converge a manifest. requirements-shuffle.txt must
# appear in none of them; requirements-test.txt must appear in all of them,
# which is the over-sensitivity control -- without it a broken reader would
# report every file clean and this gate would pass vacuously.
_INSTALLERS = (
    Path("scripts") / "deploy.sh",
    Path("scripts") / "cloud-setup.sh",
    Path("scripts") / "provision_test_host.sh",
    Path(".github") / "workflows" / "ci.yml",
)

# The merge path. Nothing here may invoke the lane.
_MERGE_PATH = (
    Path("toolchain") / "bin" / "bd-precut",
    Path("toolchain") / "bin" / "bd-band-derive",
    Path(".github") / "workflows" / "ci.yml",
    Path("scripts") / "deploy.sh",
)


def _read(rel: Path) -> str:
    return (_REPO / rel).read_text(encoding="utf-8", errors="replace")


def _canonical_line() -> str:
    """The A5 full-suite command, located structurally and EXACTLY ONCE.

    A5 holds two lines beginning `env -u BD_INSTALL_DIR` -- the focused form and
    the full-suite form. Anchoring on the prefix alone would resolve twice, and
    an anchor that resolves twice is the defect CLAUDE.md A7 names first in its
    source-rewriter rules. `-n 24` is what separates them.
    """
    text = _read(Path("CLAUDE.md"))
    start, end = text.index("## A5 |"), text.index("## A6 |")
    section = text[start:end]
    hits = [ln for ln in section.splitlines()
            if ln.startswith("env -u BD_INSTALL_DIR")
            and "-m pytest tests/" in ln and "-n 24" in ln]
    assert len(hits) == 1, (
        "the A5 canonical full-suite command anchor resolved %d times, not "
        "once -- this gate cannot say which line it is judging: %r"
        % (len(hits), hits))
    return hits[0]


def _declaring_manifests(name: str) -> dict:
    """Which tracked requirements*.txt declare `name`, read as REQUIREMENT
    LINES rather than as raw text.

    Raw `in` would count the long WHY-header comments in these manifests, which
    name pytest-randomly repeatedly and on purpose. CLAUDE.md A7: if a gate
    scans source text, its comments are inside the denominator -- strip them.
    """
    found = {}
    for path in sorted(_REPO.glob("requirements*.txt")):
        declared = []
        for raw in path.read_text(encoding="utf-8").splitlines():
            line = raw.split("#")[0].strip()
            if not line or line.startswith("-"):
                continue
            stem = re.split(r"[<>=!~\[; ]", line, maxsplit=1)[0].strip()
            declared.append(stem.replace("_", "-").lower())
        if name in declared:
            found[path.name] = declared
    return found


def _run_tool(args, env_extra=None, timeout=600):
    env = dict(os.environ)
    env.pop("BD_INSTALL_DIR", None)
    env["BD_DISABLE_KEEPALIVE"] = "1"
    if env_extra:
        env.update(env_extra)
    return subprocess.run([sys.executable, str(_TOOL), *args], cwd=str(_REPO),
                          capture_output=True, text=True, timeout=timeout,
                          env=env)


def _plugin_available() -> bool:
    """RUNTIME evidence, in the shape the tool itself uses."""
    probe = os.environ.get("BD_SHUFFLE_PLUGIN_DIR")
    if not probe:
        state = os.environ.get("XDG_STATE_HOME") or os.path.join(
            os.path.expanduser("~"), ".local", "state")
        probe = os.path.join(state, "bulkdownloader", "shuffle-lane", "plugins")
    if not os.path.isdir(probe):
        return False
    env = dict(os.environ)
    env["PYTHONPATH"] = probe + os.pathsep + env.get("PYTHONPATH", "")
    done = subprocess.run([sys.executable, "-c", "import pytest_randomly"],
                          capture_output=True, text=True, timeout=120, env=env)
    return done.returncode == 0


_NEEDS_PLUGIN = pytest.mark.skipif(
    not _plugin_available(),
    reason="the private pytest-randomly directory is not provisioned on this "
           "host; run `venv/bin/python toolchain/bin/bd-shuffle-lane --install` "
           "to make this assertion run. It is SKIPPED, not passed: the lane's "
           "shuffle cannot be graded without the plugin (CLAUDE.md A2).")


# ── 1. the canonical command is untouched ────────────────────────────────────

def test_the_canonical_full_suite_command_is_byte_identical():
    """The hard constraint of this cut, and the one a future edit here is most
    likely to break: the shuffle lane must never become a change to the
    deterministic lane."""
    line = _canonical_line()
    assert len(line) == _CANONICAL_LEN, (
        "the A5 canonical command is %d bytes, pinned at %d -- it CHANGED. If "
        "that was deliberate, re-pin both constants in the same commit and say "
        "why in the changelog; never re-pin to regain green.\n  now: %r"
        % (len(line), _CANONICAL_LEN, line))
    digest = hashlib.sha256(line.encode("utf-8")).hexdigest()
    assert digest == _CANONICAL_SHA256, (
        "the A5 canonical command's bytes changed (sha256 %s, pinned %s). "
        "Every token in it is load-bearing (CLAUDE.md A5), so a change here is "
        "a change to the only command that authorizes a merge.\n  now: %r"
        % (digest, _CANONICAL_SHA256, line))


def test_the_canonical_command_still_blocks_the_plugin_this_cut_installs():
    """The specific token, asserted separately from the digest.

    The digest above would also fail if a comma moved, and a reader then cannot
    tell WHICH property broke. This names the one that matters: now that
    pytest-randomly can exist on a host, `-p no:randomly` is what keeps the
    canonical lane the same experiment it was before this cut shipped.
    """
    line = _canonical_line()
    assert "-p no:randomly" in line, (
        "the A5 canonical full-suite command no longer blocks pytest-randomly. "
        "Before v3.66.1452 that token was a no-op because the plugin was not "
        "installed anywhere; it is not a no-op any more, and without it the "
        "deterministic full suite silently becomes a shuffled one.")


def test_this_cut_did_not_add_a_second_full_suite_command_to_the_contract():
    """Over-sensitivity control for the anchor, and a scope guard.

    `_canonical_line` asserts its anchor resolves exactly once. This asserts the
    same thing from outside the helper, so a future edit that adds a second
    sanctioned form to A5 -- e.g. by pasting the shuffle lane's invocation in as
    though it were an equal -- is named here rather than silently changing which
    line every other assertion in this file is judging.
    """
    text = _read(Path("CLAUDE.md"))
    section = text[text.index("## A5 |"):text.index("## A6 |")]
    full_suite = [ln for ln in section.splitlines()
                  if "-m pytest tests/" in ln and "-n 24" in ln]
    assert len(full_suite) == 1, (
        "A5 now contains %d full-suite command lines. There is exactly one "
        "sanctioned form; a second one makes 'the canonical command' ambiguous: "
        "%r" % (len(full_suite), full_suite))


# ── 2. the plugin is declared only where nothing installs it ─────────────────

def test_the_shuffle_plugin_is_declared_in_its_own_private_manifest():
    assert _MANIFEST.is_file(), (
        "requirements-shuffle.txt is missing, so the lane's dependency is "
        "declared nowhere and a host rebuild loses it silently -- the exact "
        "failure requirements-test.txt's pyflakes entry exists to record")
    declaring = _declaring_manifests(_PLUGIN)
    assert _MANIFEST.name in declaring, (
        "%s is not declared as a requirement line in %s (comments do not "
        "count). Declared in: %s" % (_PLUGIN, _MANIFEST.name,
                                     sorted(declaring) or "NOTHING"))


def test_the_shuffle_plugin_is_declared_in_no_other_manifest():
    """THE CONTAINMENT ASSERTION. Moving this line into requirements.txt,
    requirements-test.txt or requirements-dev.txt would install pytest-randomly
    into the interpreter that runs every sanctioned lane, and pytest would then
    auto-load it into every invocation that does not pass `-p no:randomly`."""
    declaring = set(_declaring_manifests(_PLUGIN))
    stray = sorted(declaring - {_MANIFEST.name})
    assert not stray, (
        "%s is declared in %s as well as its private manifest. Any manifest "
        "that a default installer converges puts the plugin in the shared venv, "
        "where pytest auto-loads it into the affected-band lane, bd-precut and "
        "every ci.yml shard -- turning merge evidence into a different "
        "experiment (CLAUDE.md A5)." % (_PLUGIN, stray))


def test_the_manifest_reader_can_actually_see_a_declaration():
    """Over-sensitivity control. Without it, a reader broken so that it finds
    NOTHING would make the containment assertion above pass vacuously -- an
    empty iterable manufacturing green, which CLAUDE.md A7 forbids by name."""
    seen = _declaring_manifests("pytest-timeout")
    assert "requirements-test.txt" in seen, (
        "the requirement-line reader cannot find pytest-timeout in "
        "requirements-test.txt, where it is demonstrably pinned. Every "
        "containment assertion in this file rests on this reader, so a false "
        "clean here makes all of them vacuous. Saw: %s" % sorted(seen))
    assert "pytest-randomly" not in seen["requirements-test.txt"], (
        "requirements-test.txt declares pytest-randomly -- see the containment "
        "assertion above for why that is the defect this cut exists to avoid")


def test_no_default_installer_converges_the_shuffle_manifest():
    """The other half of containment: even a correctly-placed declaration leaks
    if an installer globs `requirements*.txt`."""
    for rel in _INSTALLERS:
        body = _read(rel)
        assert _MANIFEST.name not in body, (
            "%s names %s, so the plugin would be installed into the shared "
            "interpreter on that path" % (rel, _MANIFEST.name))
        assert "requirements*.txt" not in body, (
            "%s installs by GLOB, so requirements-shuffle.txt is swept in with "
            "the rest and containment is gone. Name the manifests explicitly, "
            "the way this file's own denominator does." % rel)


def test_the_installer_denominator_is_not_empty():
    """Over-sensitivity control for the assertion above. If _INSTALLERS listed
    paths that do not exist, or the reader returned empty text, the loop would
    pass over nothing at all."""
    for rel in _INSTALLERS:
        assert (_REPO / rel).is_file(), (
            "%s is not a file, so this gate's installer denominator excludes "
            "its own subject" % rel)
        body = _read(rel)
        assert "requirements-test.txt" in body, (
            "%s does not name requirements-test.txt, which every one of these "
            "paths installs. The reader is broken or the denominator is stale, "
            "and either way the containment loop above proves nothing." % rel)


# ── 3. advisory means STRUCTURALLY unreachable from the merge path ───────────

def test_the_lane_is_not_wired_into_any_merge_gate():
    """THE ADVISORY CONTRACT, made mechanical.

    The lane exits 3 on findings and 4 on an unmeasurable run -- an honest
    non-zero, because a refusal that merely reports is the fail-open shape this
    repository keeps re-learning. What makes it ADVISORY is that nothing which
    can fail a merge invokes it.

    DELETING THIS TEST IS STEP 3 OF THE PROMOTION RECIPE in the tool's
    docstring. It is a test rather than a comment precisely so that promoting
    the lane to blocking is a deliberate, reviewed edit and cannot happen by
    somebody adding one line to ci.yml.
    """
    for rel in _MERGE_PATH:
        body = _read(rel)
        assert "bd-shuffle-lane" not in body, (
            "%s invokes bd-shuffle-lane. The lane is ADVISORY until its "
            "findings are triaged; wiring it into the merge path makes an "
            "untriaged order dependency fail somebody's PR. If that promotion "
            "is intended, follow the three-step recipe in the tool's docstring "
            "-- of which deleting this test is step 3." % rel)


def test_the_merge_path_denominator_is_not_empty():
    """Over-sensitivity control: the loop above must be reading real files that
    really do invoke toolchain tools, or its silence means nothing."""
    for rel in _MERGE_PATH:
        assert (_REPO / rel).is_file(), "%s is not a file" % rel
    wired = [rel for rel in _MERGE_PATH
             if re.search(r"toolchain/bin/bd-|bd-band-derive|bd-freshcheck",
                          _read(rel))]
    assert wired, (
        "not one merge-path file names any toolchain tool, so the absence of "
        "bd-shuffle-lane is not evidence of anything")


# ── 4. the tool refuses distinctly, and the control really is order-dependent ─

def test_the_tool_is_present_executable_and_selftests_clean():
    assert _TOOL.is_file(), "%s is missing" % _TOOL
    assert os.access(_TOOL, os.X_OK), "%s is not executable" % _TOOL
    done = _run_tool(["--selftest"])
    out = done.stdout + done.stderr
    assert "SELFTEST PASS" in out, (
        "bd-shuffle-lane --selftest did not report PASS:\n%s" % out[-2000:])
    assert done.returncode == 0, (
        "the selftest printed PASS but exited %d -- exit code and verdict must "
        "not disagree" % done.returncode)


def test_an_absent_plugin_is_unknown_with_a_named_remedy_not_ok():
    """RUNTIME proof of the refusal path, and it runs on every host including a
    CI shard that has never provisioned the lane.

    CLAUDE.md A2: UNKNOWN is a failing third state. The distinctive part is the
    REMEDY -- `bd-vault-unlock` is the recorded case of a diagnostic that
    collapsed four different failures into one message and sent the
    investigation the wrong way, so this asserts the tool names the step that
    failed and what to run, not merely that it exited non-zero.
    """
    missing = str(_REPO / ("no-such-plugin-dir-%s" % os.urandom(6).hex()))
    done = _run_tool(["tests/test_pytest_runtime_requirement.py", "-n", "2"],
                     env_extra={"BD_SHUFFLE_PLUGIN_DIR": missing})
    out = done.stdout + done.stderr
    assert done.returncode == 4, (
        "an absent plugin directory exited %d; UNKNOWN is 4, and any other code "
        "either reports a shuffled run that never happened or is indistinguish"
        "able from a real finding.\n%s" % (done.returncode, out[-1500:]))
    assert "UNKNOWN" in out and "--install" in out, (
        "the refusal does not name the remedy, so a reader cannot act on it:\n%s"
        % out[-1500:])


def test_the_control_pair_cannot_pollute_the_real_suite():
    """Both halves exist and neither is collectable by `pytest tests/`.

    pytest applies its `python_files` pattern to everything it walks and exempts
    only a path handed to it explicitly on the command line, so a name that does
    not start with `test` is inert in the real suite and live in the harness.
    """
    for probe in (_ALPHA, _BETA):
        assert probe.is_file(), "the control probe %s is missing" % probe
        assert not probe.name.startswith("test"), (
            "%s matches pytest's default collection pattern, so the deliberately "
            "order-dependent control would run inside the REAL suite and fail it"
            % probe.name)
    tracked = subprocess.run(
        ["git", "ls-files", "--", "tests/test*.py"],
        cwd=str(_REPO), capture_output=True, text=True, timeout=120).stdout.split()
    assert tracked, "the tracked test population is empty -- this check is vacuous"
    leaked = [p for p in tracked if "shuffle_control" in p]
    assert not leaked, (
        "control probe(s) landed in the tracked tests/test*.py population: %s"
        % leaked)


def test_the_control_pair_is_genuinely_order_dependent(tmp_path):
    """THE PLANTED DEFECT IS REAL, proven by running it in both orders.

    No plugin needed: the shuffle is what CHOOSES an order, and this asserts the
    orders differ in outcome, which is the property the shuffle exists to reach.
    Without this, `--control` reporting a failing seed would be indistinguishable
    from `--control` finding a broken probe.
    """
    alpha = tmp_path / "test_probe_alpha.py"
    beta = tmp_path / "test_probe_beta.py"
    shutil.copy2(_ALPHA, alpha)
    shutil.copy2(_BETA, beta)
    env = dict(os.environ)
    env.pop("BD_INSTALL_DIR", None)
    env.pop("BD_SHUFFLE_CONTROL_TOKEN", None)   # A7: pop, never merely omit.
    env["BD_SHUFFLE_CONTROL_EXPECT"] = "gate-token-%s" % os.urandom(6).hex()
    env["BD_DISABLE_KEEPALIVE"] = "1"

    def run(first, second):
        return subprocess.run(
            [sys.executable, "-m", "pytest", str(first), str(second), "-q",
             "-p", "no:randomly", "-p", "no:cacheprovider", "--timeout=60"],
            cwd=str(tmp_path), capture_output=True, text=True, timeout=300,
            env=env)

    benign = run(alpha, beta)
    assert "2 passed" in benign.stdout, (
        "alpha-then-beta did not report 2 passed, so the control's BENIGN order "
        "is broken and a failing seed would prove nothing:\n%s"
        % (benign.stdout + benign.stderr)[-1500:])
    assert benign.returncode == 0, benign.stdout[-800:]

    hostile = run(beta, alpha)
    assert hostile.returncode != 0, (
        "beta-then-alpha PASSED -- the planted order dependency is not order-"
        "dependent, so the shuffle lane's positive control cannot fail:\n%s"
        % (hostile.stdout + hostile.stderr)[-1500:])
    assert _CONTROL_MARKER in (hostile.stdout + hostile.stderr), (
        "the hostile order failed, but not with the control's own diagnostic -- "
        "a collection error or an import failure would look identical. Expected "
        "%r in:\n%s" % (_CONTROL_MARKER,
                        (hostile.stdout + hostile.stderr)[-1500:]))


# ── 5. the assertions that genuinely need the plugin ─────────────────────────

@_NEEDS_PLUGIN
def test_the_lane_fails_on_the_planted_order_dependency():
    """THE POSITIVE CONTROL, end to end through the real tool.

    A lane that cannot fail is not a lane. `--control` searches a seed range for
    BOTH classes -- at least one seed where the planted pair fails with its own
    distinctive diagnostic, and at least one where it passes -- then replays the
    failing seed and requires the identical verdict. Requiring both classes is
    what stops a lane broken so that everything fails from certifying itself.
    """
    done = _run_tool(["--control", "--seed-range", "0:24"], timeout=900)
    out = done.stdout + done.stderr
    assert done.returncode == 0, (
        "bd-shuffle-lane --control did not pass:\n%s" % out[-3000:])
    assert "failing" in out and "passing" in out, out[-2000:]
    assert "the lane FAILS on a planted order dependency" in out, out[-2000:]


@_NEEDS_PLUGIN
def test_the_lane_records_and_honours_its_seed(tmp_path):
    """A finding nobody can reproduce is not a finding.

    Runs a tiny real lane at an explicit seed and asserts the evidence record
    carries that seed, that PYTEST ECHOED IT BACK (the seam proof that the
    plugin actually loaded, rather than the run silently happening in file
    order), and that the record names a replay command.
    """
    import json
    seed = 20260902
    done = _run_tool(["tests/test_pytest_runtime_requirement.py", "-n", "2",
                      "--seed", str(seed),
                      "--evidence-dir", str(tmp_path)], timeout=900)
    out = done.stdout + done.stderr
    assert done.returncode == 0, out[-3000:]
    records = sorted(tmp_path.glob("shuffle-*-seed%d.json" % seed))
    assert len(records) == 1, (
        "expected exactly one evidence record, found %s" % records)
    rec = json.loads(records[0].read_text(encoding="utf-8"))
    assert rec["seed"] == seed, rec
    assert rec["seed_echoed_by_pytest"] == seed, (
        "pytest did not echo the requested seed, so this run was not provably "
        "shuffled and the record is not a replay handle: %r" % rec)
    assert rec["expected_collected"] and rec["junit_counts"]["tests"], (
        "a zero denominator was recorded as a completed run: %r" % rec)
    assert "--seed %d" % seed in rec["replay_command"], rec["replay_command"]
    assert (records[0].parent / (records[0].name + ".complete")).is_file(), (
        "no atomic completion marker beside the record")

"""`scripts/deploy.sh` must not report OK for a tree it did not land.

MEASURED, 2026-08-29, backlog row 391. Two fleet hosts do not clone from
GitHub: each clones from a LOCAL BARE REPOSITORY on that same host. On such a
host `git fetch origin` SUCCEEDS, returns 0, updates nothing, and leaves the
checkout exactly where it was, because the mirror it reached is itself stale.
`scripts/deploy.sh` then reset to that unchanged ref, restarted the service,
read the version back from `/api/health`, found it equal to the version of the
tree on disk -- because both were the SAME STALE TREE -- and printed

    DEPLOY OK -- now running 3.66.1326

while `main` was at 3.66.1347. Twenty-one versions stale, reported as a
successful deployment. The script printed the version it landed and compared it
to NOTHING: there was no statement anywhere of which commit the deploy was
SUPPOSED to land, so no comparison could exist and no check could fail.

THE CONTRACT THIS FILE PINS. The deploy states its INTENDED COMMIT before it
mutates anything, lands that commit rather than whatever `origin/main` happens
to name, and proves the tree IS that commit before it prints a verdict. Where
the intended commit cannot be established -- the fetch reached a repository
that is not the official origin, so the currency of its `main` is unmeasured --
that is UNKNOWN and it REFUSES (CLAUDE.md A7: unavailable measurement returns
UNKNOWN, never OK). Where the intended commit is stated but the fetch did not
deliver it, that is the stale-mirror shape itself and it REFUSES.

EVERY REFUSAL HERE SHARES EXIT 2 with five older ones (dirty tree,
BD_RESTART_CMD, unknown argument, missing dir, live pytest), so no assertion
below is content with a nonzero exit: each names the DISTINCTIVE token
(`ORIGIN-NOT-AUTHORITATIVE`, `INTENDED-COMMIT-ABSENT`,
`DEPLOYED-TREE-IS-NOT-THE-INTENDED-COMMIT`). Against the pristine script
`--expect-commit` is itself an unknown argument and refuses with exit 2, which
is exactly why the token -- not the code -- is the subject.

SYNTHETIC, ALWAYS. The stale-mirror shape is built from two real bare
repositories in a temp directory (CLAUDE.md A6 forbids testing against a live
service or a real fleet host). `_stale_mirror()` asserts its own preconditions
before any verdict: that the mirror really is behind the truth repository, that
`git fetch --prune origin` really returns 0 against it, that the checkout
really does not move across that fetch, and that the intended commit really is
absent from the clone's object store. A fixture that built none of that would
manufacture green over exactly the defect this row is about.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import test_deploy_script as deploy_support

BD_GATE_SCOPE = "module"

SCRIPT = deploy_support.SCRIPT
BASH = deploy_support.BASH

_OLD_VERSION = "3.66.1326"          # what the stale mirror carries
_NEW_VERSION = "3.66.1347"          # what main carried at the time of the incident


# ────────────────────────────────────────────────────────── helpers


def _out(r):
    return r.stdout + r.stderr


def _ctx(r, extra=""):
    return "\n--- exit=%s\n--- stdout ---\n%s\n--- stderr ---\n%s\n%s" % (
        r.returncode, r.stdout, r.stderr, extra)


def _run_deploy(fx, *args, timeout=180):
    """Invoke the REPOSITORY script with no intended-commit flag of its own.

    Deliberately not `deploy_support._deploy`: that helper now supplies
    `--expect-commit` for the batteries whose fixture origin is a local bare
    repo, and these tests are precisely about what happens when the operator
    does or does not supply it.
    """
    argv = [BASH, str(SCRIPT), "--dir", fx.clone,
            "--health-url", "http://deploy-test.invalid/api/health",
            "--timeout", "5", "--interval", "1", *args]
    return subprocess.run(argv, env=fx.env, cwd=fx.work,
                          capture_output=True, text=True, timeout=timeout)


def _fetch(repo):
    return subprocess.run(["git", "fetch", "--prune", "origin"], cwd=str(repo),
                          capture_output=True, text=True)


def _has_commit(repo, sha):
    return subprocess.run(["git", "cat-file", "-e", "%s^{commit}" % sha],
                          cwd=str(repo), capture_output=True, text=True).returncode == 0


def _extract(name):
    """The shell function `name`, cut on brace balance from its own header."""
    src = SCRIPT.read_text(encoding="utf-8")
    start = src.index("%s()" % name)
    depth, i, opened = 0, start, False
    while i < len(src):
        if src[i] == "{":
            depth += 1
            opened = True
        elif src[i] == "}":
            depth -= 1
            if opened and depth == 0:
                return src[start:i + 1]
        i += 1
    raise AssertionError("unbalanced braces extracting %s()" % name)


def _stale_mirror():
    """The measured shape: origin is a LOCAL MIRROR that is behind the truth.

    Returns (fx, truth_sha, mirror_sha). Every precondition the verdicts below
    depend on is asserted HERE, so a fixture that silently built nothing fails
    as a fixture rather than as a green subject.
    """
    fx = deploy_support._setup(version=_OLD_VERSION)
    git = deploy_support._git

    truth = os.path.join(fx.work, "truth.git")
    git(fx.work, "init", "--bare", truth)
    git(fx.seed, "remote", "add", "truth", truth)
    git(fx.seed, "push", "truth", "HEAD:refs/heads/main")

    mirror_sha = git(fx.origin, "rev-parse", "refs/heads/main").strip()
    assert git(truth, "rev-parse", "refs/heads/main").strip() == mirror_sha, (
        "fixture error: the truth repo did not start level with the mirror")

    # main moves on. ONLY the truth repository hears about it -- that is the
    # whole shape: the mirror on the host never receives the push.
    deploy_support._write(
        os.path.join(fx.seed, "bulk_downloader", "__init__.py"),
        '__version__ = "%s"\n' % _NEW_VERSION)
    deploy_support._write(os.path.join(fx.seed, "docs", "NOTE.txt"),
                          "every download fix of that day\n")
    git(fx.seed, "add", "-A")
    git(fx.seed, "commit", "-m", "twenty-one versions of work")
    git(fx.seed, "push", "truth", "HEAD:refs/heads/main")
    truth_sha = git(fx.seed, "rev-parse", "HEAD").strip()

    # ── preconditions, asserted before any test may draw a verdict ──
    assert truth_sha != mirror_sha, "fixture error: the mirror is not behind"
    assert git(truth, "rev-parse", "refs/heads/main").strip() == truth_sha
    assert git(fx.origin, "rev-parse", "refs/heads/main").strip() == mirror_sha, (
        "fixture error: the push reached the MIRROR; it must reach only truth")
    assert deploy_support._head(fx.clone) == mirror_sha

    before = deploy_support._head(fx.clone)
    fetched = _fetch(fx.clone)
    assert fetched.returncode == 0, (
        "the defect requires a fetch that SUCCEEDS over a stale mirror; this "
        "one failed and would be caught by step [1] instead\n" + fetched.stderr)
    assert deploy_support._head(fx.clone) == before, (
        "fixture error: the fetch moved the checkout, so this is not the "
        "stale-mirror shape")
    assert deploy_support._head(fx.clone, "origin/main") == mirror_sha, (
        "fixture error: origin/main moved across a fetch from a stale mirror")
    assert not _has_commit(fx.clone, truth_sha), (
        "fixture error: the clone already carries the intended commit, so "
        "nothing here can be behind it")

    fx.env["CURL_VERSION"] = _OLD_VERSION   # the service serves the stale tree
    return fx, truth_sha, mirror_sha


# ────────────────────────────────────────────────────────────── tests


def test_the_stale_mirror_fixture_is_the_measured_shape():
    """PRECONDITION PROOF -- green before and after the fix, on purpose.

    It asserts nothing about deploy.sh. It asserts that the thing the other
    tests point the script at really is a successful fetch that delivers
    nothing, which is the one property the incident turned on.
    """
    fx, truth_sha, mirror_sha = _stale_mirror()
    assert len(truth_sha) == 40 and len(mirror_sha) == 40
    assert truth_sha != mirror_sha
    assert deploy_support._head(fx.clone) == mirror_sha
    assert not _has_commit(fx.clone, truth_sha)
    second = _fetch(fx.clone)
    assert second.returncode == 0
    assert deploy_support._head(fx.clone) == mirror_sha, (
        "a second successful fetch must still move nothing")


def test_a_non_authoritative_origin_refuses_instead_of_reporting_ok():
    """The measured invocation: bare `deploy.sh` on a mirror host.

    Nothing in the run can distinguish the mirror's `main` from the project's
    `main`, so the intended commit is UNMEASURED. Reporting OK over that is the
    incident. The refusal must happen before any mutation.
    """
    fx, truth_sha, mirror_sha = _stale_mirror()
    deploy_support._bundle_current(fx)

    r = _run_deploy(fx)

    assert "ORIGIN-NOT-AUTHORITATIVE" in _out(r), (
        "a fetch that cannot reach the official origin leaves the intended "
        "commit unmeasured; that is UNKNOWN and it must be named, not "
        "silently accepted" + _ctx(r))
    assert r.returncode == 2, (
        "an unmeasurable intended commit is a PRECONDITION refusal -- nothing "
        "may be mutated" + _ctx(r))
    assert "DEPLOY OK" not in _out(r) and "ALREADY CURRENT" not in _out(r), (
        "the script reported a successful deploy over a tree whose currency it "
        "never established" + _ctx(r))
    assert fx.origin in _out(r), (
        "the refusal must name the repository that was actually fetched from"
        + _ctx(r))
    assert deploy_support._lines(fx.logs["systemctl"]) == [], (
        "a precondition refusal touched the service" + _ctx(r))
    assert deploy_support._head(fx.clone) == mirror_sha


def test_an_intended_commit_the_fetch_did_not_deliver_refuses():
    """The operator states the sha; the mirror does not have it.

    This is the stale mirror seen from the other side, and it is the state the
    push-into-the-bare-repo workaround exists to clear. `git fetch` still
    returned 0, so nothing but the object's absence can report it.
    """
    fx, truth_sha, mirror_sha = _stale_mirror()
    deploy_support._bundle_current(fx)

    r = _run_deploy(fx, "--expect-commit", truth_sha)

    assert "INTENDED-COMMIT-ABSENT" in _out(r), (
        "the fetch succeeded and did not deliver the intended commit; that is "
        "the whole defect and it must be named" + _ctx(r))
    assert "unknown argument" not in _out(r), (
        "--expect-commit is how an operator on a mirror host states what the "
        "deploy is for; the script does not accept it" + _ctx(r))
    assert r.returncode == 2, _ctx(r)
    assert truth_sha in _out(r), (
        "the refusal must name the commit that was asked for" + _ctx(r))
    assert "DEPLOY OK" not in _out(r) and "ALREADY CURRENT" not in _out(r), _ctx(r)
    assert deploy_support._lines(fx.logs["systemctl"]) == [], (
        "a precondition refusal touched the service" + _ctx(r))
    assert deploy_support._head(fx.clone) == mirror_sha


def test_a_current_host_still_deploys_and_still_reports_ok():
    """NEGATIVE CONTROL. A refusal that fires on everything is not a fix.

    The mirror carries the intended commit, the operator names it, and the full
    shimmed pipeline must run end to end -- stop, sweep, regen, start, health --
    and print DEPLOY OK. The systemctl log is the discriminator: an early
    refusal that happened to exit 0 would leave it empty.
    """
    fx = deploy_support._setup()
    deploy_support._bundle_current(fx)
    target = deploy_support._advance_origin(fx, "a genuinely current host")

    r = _run_deploy(fx, "--expect-commit", target)

    assert r.returncode == 0, (
        "a host whose origin carries the intended commit must still deploy"
        + _ctx(r))
    assert "DEPLOY OK" in _out(r), _ctx(r)
    assert "ORIGIN-NOT-AUTHORITATIVE" not in _out(r), _ctx(r)
    assert "INTENDED-COMMIT-ABSENT" not in _out(r), _ctx(r)
    assert "DEPLOYED-TREE-IS-NOT-THE-INTENDED-COMMIT" not in _out(r), _ctx(r)
    assert target in _out(r), (
        "the verdict must name the commit the deploy set out to land" + _ctx(r))
    assert deploy_support._head(fx.clone) == target
    service_calls = deploy_support._lines(fx.logs["systemctl"])
    assert service_calls.count("stop bulkdownloader") == 1, (
        "the pipeline did not reach the stopped window, so exit 0 is not "
        "evidence a deploy happened" + str(service_calls) + _ctx(r))
    assert service_calls.count("start bulkdownloader") == 1, str(service_calls)


def test_the_reset_target_is_the_intended_commit_not_whatever_the_ref_names():
    """The one line the row turns on: `git reset --hard "$NEW"`.

    Every other test here has intended == origin/main, so a script that reset to
    `origin/main` again would satisfy all of them -- the refusals fire before
    step [4] and the negative control cannot tell the two targets apart. Here
    the remote's main is deliberately AHEAD of the commit the operator named, so
    the two targets are different commits and only one of them can be on disk
    afterwards.

    It also runs the post-reset handoff with the ref and the intent disagreeing:
    the parent resets to the intended commit and execs the child, which fetches
    again, sees a main it was not asked for, and must still land the intent.
    """
    fx = deploy_support._setup()
    deploy_support._bundle_current(fx)
    intended = deploy_support._advance_origin(fx, "the commit the operator named")
    moved_on = deploy_support._advance_origin(fx, "main moved on past the pin")
    assert intended != moved_on
    assert deploy_support._head(fx.origin, "refs/heads/main") == moved_on, (
        "fixture error: the remote's main is not ahead of the intended commit, "
        "so the two reset targets are indistinguishable here")

    r = _run_deploy(fx, "--expect-commit", intended)

    assert deploy_support._head(fx.clone) == intended, (
        "the deploy landed %s; the ref won over the stated intent, which is "
        "the whole defect" % deploy_support._head(fx.clone) + _ctx(r))
    assert deploy_support._head(fx.clone) != moved_on, _ctx(r)
    assert r.returncode == 0, _ctx(r)
    assert "DEPLOY OK" in _out(r), _ctx(r)
    assert "is NOT the intended commit" in _out(r), (
        "a remote whose main disagrees with the intended commit must be said "
        "out loud, not resolved silently in either direction" + _ctx(r))
    assert moved_on in _out(r), (
        "the disagreement must name the commit that was declined" + _ctx(r))
    assert "post-reset handoff" in _out(r), (
        "harness error, NOT a subject failure: the tree did not move, so this "
        "run never exercised the handoff with ref and intent disagreeing"
        + _ctx(r))
    assert "ORIGIN-NOT-AUTHORITATIVE" not in _out(r), _ctx(r)
    assert "INTENDED-COMMIT-ABSENT" not in _out(r), _ctx(r)
    assert "DEPLOYED-TREE-IS-NOT-THE-INTENDED-COMMIT" not in _out(r), _ctx(r)


def test_an_abbreviated_intended_commit_is_refused_before_anything_moves():
    """An abbreviation is ambiguous as history grows; it is not accepted.

    Refused in the pre-mutation block, so the cost of a typo is exit 2 and no
    side effect -- checked here because a short sha that happened to resolve
    would make every downstream identity assertion argue about a commit the
    operator never named.
    """
    fx = deploy_support._setup()
    before = deploy_support._head(fx.clone)

    r = _run_deploy(fx, "--expect-commit", before[:8])

    assert "40-character hex commit" in _out(r), (
        "the refusal must say what is wrong with the value" + _ctx(r))
    assert r.returncode == 2, _ctx(r)
    assert deploy_support._lines(fx.logs["systemctl"]) == [], _ctx(r)
    assert deploy_support._head(fx.clone) == before


def test_a_tree_moved_after_the_reset_cannot_be_reported_as_ok():
    """The verdict is checked against the CURRENT tree, not against step 4.

    Step [4] already asserted HEAD after its own reset. That says nothing about
    steps 5-12, which run pip, npm, a sweep and a regen inside the tree. Here
    the bundle build moves HEAD back to the previous commit -- which carries
    the SAME __version__, so the health gate is satisfied and only an identity
    check can catch it. Without the step-13 re-assert this run reports DEPLOY OK
    over the commit it was told not to land.
    """
    fx = deploy_support._setup()
    target = deploy_support._advance_origin(fx, "the commit that was intended")
    previous = deploy_support._head(fx.clone)
    assert previous != target

    fx.env["REVERT_DIR"] = fx.clone
    fx.env["REVERT_TO"] = previous
    deploy_support._write_exec(
        os.path.join(fx.binroot, "npm"),
        '#!/usr/bin/env bash\n'
        'printf \'%s\\n\' "$*" >> "$NPM_LOG"\n'
        'case " $* " in\n'
        '  *" run "*build*)\n'
        '    mkdir -p dist\n'
        '    printf \'<!doctype html><title>bd</title>\\n\' > dist/index.html\n'
        '    git -C "$REVERT_DIR" reset --hard "$REVERT_TO" >/dev/null 2>&1\n'
        '    ;;\n'
        'esac\n'
        'exit 0\n')

    r = _run_deploy(fx, "--expect-commit", target)

    assert deploy_support._head(fx.clone) == previous, (
        "harness error, NOT a subject failure: the shim did not move the tree, "
        "so this run cannot measure the step-13 re-assert" + _ctx(r))
    assert "DEPLOYED-TREE-IS-NOT-THE-INTENDED-COMMIT" in _out(r), (
        "a later step moved the tree off the intended commit and the deploy "
        "did not notice" + _ctx(r))
    assert r.returncode == 1, (
        "the tree moved after the reset: a verification FAILED, so the state "
        "is not known good" + _ctx(r))
    assert "DEPLOY OK" not in _out(r) and "ALREADY CURRENT" not in _out(r), _ctx(r)


def test_the_official_origin_predicate_accepts_only_the_official_origin():
    """The classifier, exercised directly on both classes.

    A predicate that answered "authoritative" to everything would make the
    refusal unreachable; one that answered "no" to everything would refuse the
    whole fleet. Both classes are non-empty and every row is asserted by name.
    """
    fn = _extract("_origin_is_authoritative")
    syntax = subprocess.run([BASH, "-n", "-c", fn + "\n"],
                            capture_output=True, text=True)
    assert syntax.returncode == 0, (
        "the EXTRACTION is broken, not the subject: %s" % syntax.stderr)

    authoritative = [
        "https://github.com/mcboyle/BD",
        "https://github.com/mcboyle/BD.git",
        "https://github.com/mcboyle/BD/",
        "git@github.com:mcboyle/BD.git",
        "ssh://git@github.com/mcboyle/BD.git",
        "HTTPS://GITHUB.COM/MCBOYLE/BD.GIT",
    ]
    mirrors = [
        "",
        "/home/mboyle/bd.git",
        "file:///home/mboyle/bd.git",
        "ssh://mboyle@10.0.70.51/home/mboyle/bd.git",
        "https://github.com/mcboyle/BD-fork.git",
        "https://github.com/someone-else/BD.git",
        "https://not-github.example/mcboyle/BD.git",
    ]
    assert authoritative and mirrors

    verdicts = {}
    for url in authoritative + mirrors:
        script = fn + '\nif _origin_is_authoritative "$1"; then echo YES; else echo NO; fi\n'
        got = subprocess.run([BASH, "-c", script, "bash", url],
                             capture_output=True, text=True)
        assert got.returncode == 0, got.stderr
        verdicts[url] = got.stdout.strip()

    for url in authoritative:
        assert verdicts[url] == "YES", (
            "the official origin was classified as a mirror, which refuses "
            "every ordinary deploy: %r -> %r" % (url, verdicts[url]))
    for url in mirrors:
        assert verdicts[url] == "NO", (
            "a repository that is NOT the official origin was accepted as "
            "authoritative, which is the defect: %r -> %r" % (url, verdicts[url]))

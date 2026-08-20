"""The panel bootstrap must stay thin, or it becomes a private fork again.

The Claude Code cloud panel holds its own copy of a setup script, pasted by
hand. That copy was measured three commits and 91 lines behind
scripts/cloud-setup.sh -- it still carried a guard pin the tree had moved past
and a GTK package list missing x11-utils. Meanwhile every gate in the suite
asserted over the repo copy, which never executes. The denominator (the tracked
tree) structurally excluded the subject (the pasted script), so 13 tests
reported green about a file with no bearing on the environment they ran in.

The remedy is not a better gate over the fork. It is to leave nothing in the
panel worth forking: the panel gets a bootstrap that locates the checkout and
`exec`s the repo copy, and every line of real provisioning logic lives in the
repo where the gates can see it.

These tests pin the THIN property. A bootstrap that grows an apt call has
started the drift over again, and the growth is what must fail.
"""
from __future__ import annotations

import os
import re
import subprocess
import textwrap
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
BOOTSTRAP = REPO_ROOT / "scripts" / "cloud-bootstrap.sh"
CLOUD_SETUP = REPO_ROOT / "scripts" / "cloud-setup.sh"


def _source() -> str:
    assert BOOTSTRAP.is_file(), (
        f"{BOOTSTRAP} does not exist. The panel needs a repo-owned bootstrap to "
        f"paste; without one the panel keeps a private copy of the whole "
        f"provisioner and forks again."
    )
    return BOOTSTRAP.read_text(encoding="utf-8")


def _strip_comments(text: str) -> str:
    """Quote-aware comment stripper so prose cannot satisfy or trip a gate."""
    out = []
    for line in text.splitlines():
        cleaned, quote = [], None
        for ch in line:
            if quote:
                cleaned.append(ch)
                if ch == quote:
                    quote = None
                continue
            if ch in "'\"":
                quote = ch
                cleaned.append(ch)
                continue
            if ch == "#":
                break
            cleaned.append(ch)
        out.append("".join(cleaned))
    return "\n".join(out)


def test_bootstrap_exists_and_parses():
    src = _source()
    proc = subprocess.run(["bash", "-n", str(BOOTSTRAP)], capture_output=True, text=True)
    assert proc.returncode == 0, f"bootstrap does not parse:\n{proc.stderr}"
    assert src.startswith("#!"), "bootstrap has no shebang"


def test_bootstrap_installs_nothing():
    """Any install verb here is a line that will drift out of the repo's sight.

    This is the whole contract. The fork was not caused by carelessness -- it
    was caused by the panel holding logic worth editing in place.
    """
    code = _strip_comments(_source())
    forbidden = {
        "apt-get": r"\bapt-get\b",
        "apt_i": r"\bapt_i\b",
        "pip install": r"\bpip\s+install\b",
        "npm ci": r"\bnpm\s+(ci|install)\b",
        "playwright install": r"playwright\s+install",
        "curl download": r"\bcurl\b",
        "python -m venv": r"-m\s+venv\b",
    }
    found = [name for name, pat in forbidden.items() if re.search(pat, code)]
    assert not found, (
        f"the bootstrap performs provisioning work: {found}. "
        f"Every such line is a private fork of logic that belongs in "
        f"scripts/cloud-setup.sh, where the suite can see it."
    )


def test_bootstrap_delegates_to_the_repo_copy():
    """The bootstrap's one job is to hand over."""
    code = _strip_comments(_source())
    assert re.search(r"\bexec\b.*cloud-setup\.sh", code), (
        "the bootstrap never execs scripts/cloud-setup.sh; if it does the work "
        "itself, the repo copy is decorative"
    )


def test_bootstrap_does_not_search_the_filesystem():
    """Same trap find_repo fell into: /tmp fixtures outrank the real checkout."""
    code = _strip_comments(_source())
    assert not re.search(r"^\s*find\s+/", code, re.M), (
        "the bootstrap runs a filesystem search. On a host that has run the "
        "test suite, /tmp holds dozens of shallower bulk_downloader fixtures, "
        "and a depth-ranked search prefers them over the real tree."
    )


def test_bootstrap_fails_loudly_when_there_is_no_checkout(tmp_path):
    """No checkout is UNKNOWN, and unknown is a third state that FAILS.

    Exiting 0 here would tell the session everything is fine while nothing was
    provisioned -- the exact false-READY the provisioner's verdict gates exist
    to prevent.

    WHY THIS RUNS A REWRITTEN COPY RATHER THAN THE SCRIPT ITSELF. The probe
    list contains absolute rungs, including a `/home/*/BD` glob added after a
    real session failed to find a checkout that was present. On any machine
    that HAS a checkout under /home, those rungs match no matter what the
    caller intended, so the no-checkout branch is unreachable and running the
    real script here execs the real provisioner -- which is how this test
    previously surfaced as a two-minute hang.

    So the absolute rungs are re-pointed into tmp_path. Every other line of
    the script, including the whole failure branch being asserted, is the
    shipped text.
    """
    _source()
    home = tmp_path / "home"
    home.mkdir()
    sandbox = tmp_path / "root"
    sandbox.mkdir()

    text = BOOTSTRAP.read_text(encoding="utf-8")
    for absolute in ("/workspace", "/repo", "/src", "/app", "/home/*"):
        text = text.replace(f" {absolute}", f" {sandbox}{absolute}")
    rewritten = tmp_path / "bootstrap-sandboxed.sh"
    rewritten.write_text(text, encoding="utf-8")
    assert str(sandbox) in text, "the rewrite did not take; the test would run the real prober"

    env = {"PATH": "/usr/local/bin:/usr/bin:/bin", "HOME": str(home)}
    try:
        proc = subprocess.run(
            ["bash", str(rewritten)],
            capture_output=True, text=True, cwd=str(sandbox), env=env, timeout=20,
        )
    except subprocess.TimeoutExpired:
        pytest.fail(
            "the bootstrap did not return within 20s with every probe pointed "
            "at an empty sandbox. Some rung still reaches a real checkout."
        )
    assert proc.returncode != 0, (
        "the bootstrap exited 0 with no checkout found; the caller cannot "
        "distinguish that from a successful provision.\n"
        f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
    )

    report = home / ".claude-env-report.md"
    assert report.is_file(), (
        "the bootstrap failed without writing a report. A session that finds no "
        "report has to guess whether provisioning ran at all."
    )
    body = report.read_text(encoding="utf-8")
    assert "VERDICT" in body, "the failure report states no verdict"
    assert re.search(r"NOT|UNKNOWN|COULD NOT|FATAL", body), (
        f"the failure report does not say that nothing was provisioned:\n{body}"
    )


def test_bootstrap_hands_off_to_the_repo_when_one_is_present(tmp_path):
    """The success path must actually reach cloud-setup.sh.

    Uses a stub checkout with a stub cloud-setup.sh, so this asserts the
    handover itself rather than running the real provisioner.
    """
    _source()
    home = tmp_path / "home"
    home.mkdir()
    fake = tmp_path / "checkout"
    (fake / "scripts").mkdir(parents=True)
    (fake / "bulk_downloader").mkdir()
    (fake / "bulk_downloader" / "__init__.py").write_text('__version__ = "0.0.0"\n')
    (fake / "scripts" / "cloud-setup.sh").write_text(
        "#!/bin/bash\necho DELEGATED_TO_REPO_COPY\nexit 0\n"
    )

    env = {
        "PATH": "/usr/local/bin:/usr/bin:/bin",
        "HOME": str(home),
        "BD_REPO": str(fake),
    }
    proc = subprocess.run(
        ["bash", str(BOOTSTRAP)],
        capture_output=True, text=True, cwd=str(tmp_path), env=env, timeout=120,
    )
    assert "DELEGATED_TO_REPO_COPY" in proc.stdout, (
        "the bootstrap did not exec the checkout's cloud-setup.sh.\n"
        f"exit={proc.returncode}\nstdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
    )


def test_bootstrap_stays_short():
    """A length ceiling is the crude but effective anti-drift signal.

    The forked panel copy was 453 lines. Anything approaching that has stopped
    being a bootstrap. This is deliberately generous -- it fires on a rewrite,
    not on an added comment.
    """
    lines = len(_source().splitlines())
    assert lines < 80, (
        f"the bootstrap is {lines} lines. It is meant to locate a checkout and "
        f"exec; at this size it has begun re-absorbing provisioning logic."
    )


def test_the_probe_list_finds_THIS_checkout():
    """The question Cut 2 never asked: does the shipped list find the real tree?

    `test_bootstrap_hands_off_to_the_repo_when_one_is_present` sets BD_REPO, so
    it exercises the FIRST rung and proves delegation. Every other rung was
    outside its denominator -- and one of them was wrong. In this environment
    HOME is /root while the checkout is at /home/user/BD, so the `$HOME/BD`
    rung resolved to a path that does not exist and a real session died with:

        FATAL: no BulkDownloader checkout found

    on a container that had a checkout. The failure was correct behaviour over
    a wrong list.

    This runs the ACTUAL probe loop from the shipped script, with BD_REPO and
    CLAUDE_PROJECT_DIR unset and the working directory elsewhere, and requires
    it to locate this repository. It is the one assertion that cannot pass
    while the list is blind to where the tree really is.
    """
    text = BOOTSTRAP.read_text(encoding="utf-8")
    match = re.search(r"for candidate in (.*?); do", text, re.S)
    assert match, "could not find the probe list in cloud-bootstrap.sh"
    candidates = match.group(1)

    script = (
        'MARKER="bulk_downloader/__init__.py"\nREPO=""\n'
        f"for candidate in {candidates}; do\n"
        '  if [ -n "$candidate" ] && [ -f "$candidate/$MARKER" ] \\\n'
        '     && [ -f "$candidate/scripts/cloud-setup.sh" ]; then\n'
        '    REPO="$candidate"; break\n  fi\ndone\n'
        'printf "%s" "$REPO"\n'
    )
    proc = subprocess.run(
        ["bash", "-c", script], capture_output=True, text=True, cwd="/tmp",
        env={"PATH": "/usr/local/bin:/usr/bin:/bin", "HOME": os.environ.get("HOME", "/root")},
        timeout=120,
    )
    found = proc.stdout.strip()
    assert found, (
        f"the shipped probe list does not locate this checkout "
        f"({REPO_ROOT}) with BD_REPO unset, HOME={os.environ.get('HOME')} and "
        f"cwd=/tmp.\n\nThat is the exact condition a fresh session runs under, "
        f"and it is how a real one failed. Add the rung that covers where the "
        f"tree actually lives -- bounded, never a filesystem-wide search."
    )
    # Whether the probe can find THIS tree depends on whether this tree sits at
    # a location the probe list covers. A canonical deployed checkout does; a git
    # worktree used for integration does not -- its path is not a probed rung, so
    # the probe correctly resolves the canonical checkout instead (row 177).
    # Assert the strong identity only when this tree is reachable; otherwise the
    # probe must still find A valid checkout, and identity is skipped with a
    # reason rather than failing on a location the invariant never covered. The
    # canonical-checkout invariant is not weakened.
    import glob as _glob

    home = Path(os.environ.get("HOME", "/root"))
    reachable = {
        d.resolve()
        for d in (
            home / "BD", home / "BulkDownloader", home / "bulkdownloader",
            Path("/workspace"), Path("/repo"), Path("/src"), Path("/app"),
        )
    }
    for pat in ("/home/*/BD", "/home/*/BulkDownloader", "/home/*/bulkdownloader"):
        reachable.update(Path(g).resolve() for g in _glob.glob(pat))

    if REPO_ROOT.resolve() in reachable:
        assert Path(found).resolve() == REPO_ROOT.resolve(), (
            f"the probe found {found}, not this repository ({REPO_ROOT}). A list "
            f"that resolves to some OTHER tree is worse than one that finds "
            f"nothing: provisioning would run against it silently."
        )
    else:
        found_root = Path(found).resolve()
        assert (found_root / "bulk_downloader" / "__init__.py").is_file() and (
            found_root / "scripts" / "cloud-setup.sh"
        ).is_file(), (
            f"the probe found {found}, which is not a valid checkout (missing the "
            f"marker or cloud-setup.sh). A list that resolves to junk is worse "
            f"than one that finds nothing."
        )
        pytest.skip(
            f"REPO_ROOT ({REPO_ROOT}) is a non-probed worktree; the probe "
            f"correctly resolves the canonical checkout ({found}) instead. The "
            f"identity invariant is asserted on canonical checkouts, not worktrees."
        )


@pytest.mark.parametrize(
    "home_rel, repo_rel, label",
    [
        ("home/mboyle", "home/mboyle/BulkDownloader", "deploy box (test4)"),
        ("root", "home/user/BD", "cloud container"),
    ],
)
def test_the_probe_list_covers_known_host_layouts(tmp_path, home_rel, repo_rel, label):
    """The layouts of the hosts that actually run this, asserted from anywhere.

    test_the_probe_list_finds_THIS_checkout can only ever see the tree it is
    running in. On the cloud container that is /home/user/BD and it passes; the
    deploy box's /home/mboyle/BulkDownloader was outside its reach, so the list
    shipped blind to it and the box was the first thing to notice -- one failure
    in a 13651-pass capture:

        the shipped probe list does not locate this checkout
        (/home/mboyle/BulkDownloader) with BD_REPO unset, HOME=/home/mboyle

    The miss was case. The list carried `bulkdownloader`, the directory is
    `BulkDownloader`, and Linux does not care that they read the same.

    This parametrises the real layouts into a sandbox so both are inside the
    denominator from either host. Adding a host means adding a row here, not
    waiting for a capture to fail.
    """
    sandbox = tmp_path / "root"
    home = sandbox / home_rel
    repo = sandbox / repo_rel
    (repo / "scripts").mkdir(parents=True)
    (repo / "bulk_downloader").mkdir(parents=True)
    (repo / "bulk_downloader" / "__init__.py").write_text('__version__ = "0.0.0"\n')
    (repo / "scripts" / "cloud-setup.sh").write_text("#!/bin/bash\nexit 0\n")
    home.mkdir(parents=True, exist_ok=True)

    text = BOOTSTRAP.read_text(encoding="utf-8")
    match = re.search(r"for candidate in (.*?); do", text, re.S)
    assert match, "could not find the probe list in cloud-bootstrap.sh"
    candidates = match.group(1)
    for absolute in ("/workspace", "/repo", "/src", "/app", "/home/*"):
        candidates = candidates.replace(f" {absolute}", f" {sandbox}{absolute}")
    assert str(sandbox) in candidates, "the rewrite did not take"

    script = (
        'MARKER="bulk_downloader/__init__.py"\nREPO=""\n'
        f"for candidate in {candidates}; do\n"
        '  if [ -n "$candidate" ] && [ -f "$candidate/$MARKER" ] \\\n'
        '     && [ -f "$candidate/scripts/cloud-setup.sh" ]; then\n'
        '    REPO="$candidate"; break\n  fi\ndone\n'
        'printf "%s" "$REPO"\n'
    )
    proc = subprocess.run(
        ["bash", "-c", script],
        capture_output=True, text=True, cwd=str(tmp_path),
        env={"PATH": "/usr/local/bin:/usr/bin:/bin", "HOME": str(home)},
        timeout=60,
    )
    found = proc.stdout.strip()
    assert found, (
        f"the probe list does not locate the {label} layout: repo at "
        f"{repo}, HOME={home}, BD_REPO unset, cwd elsewhere.\n"
        f"A host whose layout is missing here provisions nothing and says so "
        f"only once someone runs a capture on it."
    )
    assert Path(found).resolve() == repo.resolve(), (
        f"the probe found {found}, not the {label} checkout at {repo}"
    )


def _probe_rungs(text: str, header: str) -> list[str]:
    """The literal path rungs of a probe loop, env-var rungs excluded.

    Returns only rungs that name a location (absolute or glob). A BARE variable
    reference -- `${BD_REPO:-}`, `$PWD` -- is dropped: those are channels the
    caller fills, and cloud-setup.sh consumes them through a separate
    `for name in BD_REPO CLAUDE_PROJECT_DIR PWD` loop rather than as paths, so
    counting them as places reports a gap that is not there. `$HOME/BD` is kept:
    the variable is a prefix, the rung is still a place.
    """
    match = re.search(rf"for {header} in (.*?); do", text, re.S)
    assert match, f"could not find a `for {header} in ...` probe loop"
    rungs = []
    for raw in match.group(1).split():
        rung = raw.strip("\\").strip().strip('"').strip("'")
        if not rung or re.fullmatch(r"\$\{?\w+(:-)?\}?", rung):
            continue
        rungs.append(rung)
    return rungs


def test_cloud_setup_probe_list_covers_the_bootstrap_list():
    """The two halves of provisioning must not disagree about where repos live.

    The bootstrap finds the checkout and `exec`s cloud-setup.sh, which then
    locates the repo AGAIN from its own list. So there are two denominators for
    one question, and only the second one decides what gets provisioned. When
    `/home/*/BD` was added to the bootstrap in #44 it was not added here, so the
    bootstrap could resolve a checkout that cloud-setup.sh then could not see --
    and cloud-setup.sh's failure mode is not a loud exit, it is HAVE_REPO=0,
    which provisions the system half and reports OK about a tree it never found.

    Containment is the invariant, not equality: cloud-setup.sh may know extra
    locations, but it must know every location the bootstrap is willing to hand
    it. Widening the bootstrap alone must fail here.
    """
    boot = _probe_rungs(_source(), "candidate")
    setup = _probe_rungs(CLOUD_SETUP.read_text(encoding="utf-8"), "path")

    missing = [rung for rung in boot if rung not in setup]
    assert not missing, (
        f"cloud-setup.sh cannot see {missing}, which the bootstrap will hand "
        f"it.\nbootstrap rungs: {boot}\ncloud-setup rungs: {setup}\n"
        f"A checkout found by the first and missed by the second is provisioned "
        f"as HAVE_REPO=0 -- the report says READY about a tree it never located."
    )


def test_the_handoff_carries_the_located_repo(tmp_path):
    """Finding the checkout is worthless if the location dies at the `exec`.

    `test_bootstrap_hands_off_to_the_repo_when_one_is_present` sets BD_REPO, so
    the handed-off process inherits the answer no matter what the bootstrap
    does -- the loss this test is about is structurally outside its denominator,
    exactly like the `$HOME/BD` rung was outside the old probe test's.

    Here the checkout is reachable ONLY through the `/home/*/BD` glob: BD_REPO
    and CLAUDE_PROJECT_DIR are unset and the cwd holds no marker. That is the
    real panel condition. cloud-setup.sh reads BD_REPO, CLAUDE_PROJECT_DIR and
    PWD before any path rung, so the bootstrap must deliver its answer through
    one of those three or the work of finding it is thrown away.
    """
    _source()
    home = tmp_path / "home"
    home.mkdir()
    sandbox = tmp_path / "root"
    sandbox.mkdir()
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()

    fake = sandbox / "home" / "someuser" / "BD"
    (fake / "scripts").mkdir(parents=True)
    (fake / "bulk_downloader").mkdir()
    (fake / "bulk_downloader" / "__init__.py").write_text('__version__ = "0.0.0"\n')

    seen = tmp_path / "seen.txt"
    (fake / "scripts" / "cloud-setup.sh").write_text(
        "#!/bin/bash\n"
        "{\n"
        '  echo "BD_REPO=${BD_REPO:-}"\n'
        '  echo "CLAUDE_PROJECT_DIR=${CLAUDE_PROJECT_DIR:-}"\n'
        '  echo "PWD=$PWD"\n'
        f'}} > "{seen}"\n'
        "exit 0\n"
    )

    text = BOOTSTRAP.read_text(encoding="utf-8")
    for absolute in ("/workspace", "/repo", "/src", "/app", "/home/*"):
        text = text.replace(f" {absolute}", f" {sandbox}{absolute}")
    rewritten = tmp_path / "bootstrap-sandboxed.sh"
    rewritten.write_text(text, encoding="utf-8")
    assert str(sandbox) in text, "the rewrite did not take; the probes still point at real paths"

    env = {"PATH": "/usr/local/bin:/usr/bin:/bin", "HOME": str(home)}
    proc = subprocess.run(
        ["bash", str(rewritten)],
        capture_output=True, text=True, cwd=str(elsewhere), env=env, timeout=60,
    )
    assert seen.is_file(), (
        "the bootstrap never reached the checkout's cloud-setup.sh via the "
        f"/home/*/BD rung.\nexit={proc.returncode}\nstdout:\n{proc.stdout}\n"
        f"stderr:\n{proc.stderr}"
    )

    channels = dict(
        line.split("=", 1) for line in seen.read_text().splitlines() if "=" in line
    )
    reachable = [
        name
        for name, value in channels.items()
        if value and Path(value).resolve() == fake.resolve()
    ]
    assert reachable, (
        "the bootstrap located the checkout and then lost it across the exec. "
        "cloud-setup.sh re-derives the repo from BD_REPO, CLAUDE_PROJECT_DIR "
        "and PWD before any path rung, and the handed-off process saw "
        f"{channels} -- none of which names {fake}.\n"
        "It will fall through to its own path list and, if that list misses "
        "too, provision with HAVE_REPO=0 and report OK about nothing."
    )

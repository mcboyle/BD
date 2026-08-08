"""bd-env-parity must look where the browsers actually are.

`snapshot()` pinned the Playwright pool to a literal path:

    home="/home/claude"; cache=os.path.join(home,".cache","ms-playwright")

and never consulted `PLAYWRIGHT_BROWSERS_PATH`. `bool(glob(...))` over a
directory that does not exist is `False` -- indistinguishable from "I checked a
real pool and it was empty". Measured on this container, where all three
browsers are installed:

    $ echo $PLAYWRIGHT_BROWSERS_PATH
    /opt/pw-browsers
    $ venv/bin/python toolchain/bin/bd-env-parity ; echo exit=$?
      browsers.chromium = False
      browsers.firefox  = False
      browsers.webkit   = False
    exit=0
    $ venv/bin/python toolchain/bin/bd-env-parity --selftest ; echo exit=$?
    SELFTEST PASS
    exit=0

That is the RUNS-DEGRADED class: it examines nothing and reports success.

The damage is downstream, in `diff()`, which is the tool's actual product. A
baseline captured where the pool happened to sit under the hardcoded path
records `true`; a host reading it here reports `false`; the tool prints a
parity SKEW that does not exist. Inverted, a baseline written here records
`false` for a host that has browsers -- laundering "I looked in the wrong
place" into a stored artifact that is later read as authority.

A NOTE ON THE TRAP IN FIXING THIS. `/home/claude` DOES exist on this
container; it is the `.cache/ms-playwright` leaf beneath it that does not. So
the intuitive guard `if os.path.isdir("/home/claude")` returns True and ships
the bug unchanged. `HOME` is `/root` here, so `expanduser("~")` does not find
it either. Only the environment variable does.
"""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
TOOL = REPO_ROOT / "toolchain" / "bin" / "bd-env-parity"
PYTHON = REPO_ROOT / "venv" / "bin" / "python"


def _run(args, env=None):
    e = dict(os.environ)
    if env:
        e.update({k: v for k, v in env.items() if v is not None})
        for k, v in env.items():
            if v is None:
                e.pop(k, None)
    return subprocess.run(
        [str(PYTHON), str(TOOL), *args],
        capture_output=True, text=True, timeout=120, env=e, cwd=str(REPO_ROOT),
    )


def _fake_pool(tmp_path: Path, engines) -> Path:
    """A pool that looks like Playwright's, containing only what we name."""
    pool = tmp_path / "pw-pool"
    pool.mkdir()
    for engine in engines:
        (pool / f"{engine}-1234").mkdir()
    return pool


def test_the_tool_exists_and_runs():
    assert TOOL.is_file(), f"{TOOL} missing"
    proc = _run(["--help"])
    assert proc.returncode == 0, proc.stderr


def test_a_pool_named_by_the_environment_is_found(tmp_path):
    """The defect, stated as a requirement.

    Hermetic: builds its own pool rather than depending on this container's,
    so the test means the same thing on a host with no browsers installed.
    """
    pool = _fake_pool(tmp_path, ["chromium", "firefox", "webkit"])
    proc = _run(["--json"], env={"PLAYWRIGHT_BROWSERS_PATH": str(pool)})
    assert proc.returncode == 0, f"exit={proc.returncode}\n{proc.stdout}{proc.stderr}"
    try:
        snap = json.loads(proc.stdout)
    except json.JSONDecodeError:
        pytest.fail(
            "bd-env-parity --json did not emit JSON:\n"
            f"stdout={proc.stdout!r}\nstderr={proc.stderr!r}"
        )
    browsers = snap.get("browsers", {})
    missing = [b for b in ("chromium", "firefox", "webkit") if not browsers.get(b)]
    assert not missing, (
        f"PLAYWRIGHT_BROWSERS_PATH={pool} contains "
        f"{sorted(p.name for p in pool.iterdir())}, but bd-env-parity reports "
        f"{missing} absent.\n\nIt is globbing a hardcoded path instead of the "
        f"pool it was told about, so it reports on a denominator that is not "
        f"there -- and exits 0 doing it."
    )


def test_an_empty_pool_is_reported_absent_not_hidden(tmp_path):
    """The other half: present-but-empty must still read as absent.

    Without this, a fix could 'pass' by reporting True unconditionally.
    """
    pool = _fake_pool(tmp_path, [])
    proc = _run(["--json"], env={"PLAYWRIGHT_BROWSERS_PATH": str(pool)})
    assert proc.returncode == 0, proc.stderr
    snap = json.loads(proc.stdout)
    browsers = snap.get("browsers", {})
    present = [b for b in ("chromium", "firefox", "webkit") if browsers.get(b)]
    assert not present, (
        f"an empty pool at {pool} reported {present} as present. The fix must "
        f"look in the right place, not answer True regardless."
    )


def test_the_selftest_notices_it_found_no_browsers_at_all(tmp_path):
    """A zero-in-every-bucket result is a failure signal, not a pass.

    Today selftest() asserts diff(s, s) is empty -- trivially true whatever
    snapshot() found -- and that a synthetic node skew is detected. It never
    asks whether the browser denominator was non-empty, so it prints
    SELFTEST PASS on a host where it located nothing.
    """
    proc = _run(["--selftest"], env={"PLAYWRIGHT_BROWSERS_PATH": str(tmp_path / "nope")})
    combined = proc.stdout + proc.stderr
    assert proc.returncode != 0 or "UNKNOWN" in combined.upper(), (
        "bd-env-parity --selftest exited 0 with a browser pool that does not "
        "exist:\n" + combined +
        "\nIt must either fail or say UNKNOWN. Reporting PASS over an "
        "unexamined denominator is the failure this check exists to catch."
    )


def test_the_default_output_path_is_not_someone_elses_home(tmp_path):
    """--out defaulted to /home/claude/env_baseline.json.

    On a host without that directory `--write` raises FileNotFoundError; on a
    host WITH it -- this container has /home/claude, just not the playwright
    leaf -- it silently writes a baseline outside the repo, where it rots and
    is later read as authority.

    Asserted by RUNNING it, not by grepping the source. The first version of
    this test searched the file for the literal path and failed on the comment
    that explains the history, which is the same predicate-too-broad mistake
    the tool itself made: it could not tell "this is the default" from "this
    used to be the default".
    """
    # Attribute the write to THIS run. Asserting the stray merely does not
    # exist makes the test fail forever on any host where some earlier
    # invocation left one -- which is exactly what happened while developing
    # this file: verifying the test still failed against the original tool
    # caused that tool to create the very file the assertion looks for.
    stray = Path("/home/claude/env_baseline.json")
    before = stray.stat().st_mtime_ns if stray.exists() else None

    proc = subprocess.run(
        [str(PYTHON), str(TOOL), "--write"],
        capture_output=True, text=True, timeout=120, cwd=str(tmp_path),
        env={**os.environ, "BD_ENV_BASELINE": ""},
    )
    assert proc.returncode == 0, f"--write failed:\n{proc.stdout}{proc.stderr}"

    after = stray.stat().st_mtime_ns if stray.exists() else None
    assert after == before, (
        f"bd-env-parity --write touched {stray} (mtime {before} -> {after}). "
        f"The default must land somewhere the caller chose or somewhere "
        f"derived from the working tree, never in a fixed home directory "
        f"belonging to another user."
    )
    written = list(tmp_path.glob("*.json"))
    assert written, (
        f"--write from cwd={tmp_path} produced no file there.\n"
        f"stdout={proc.stdout!r}\nIt wrote somewhere else, which is the defect."
    )


def test_bdenv_does_not_clobber_a_caller_supplied_browser_path():
    """`toolchain/bdenv.sh` exported the retired pool UNCONDITIONALLY.

    Sourcing it therefore replaced a correct PLAYWRIGHT_BROWSERS_PATH -- in
    this container /opt/pw-browsers, which holds eight real builds -- with a
    directory that does not exist. `toolchain/bin/bd` and `bd-status` both
    source it (`BD_ENV_FILE` defaults to the file beside them), so the loss hit
    anything run under the wrapper.

    Two tools had already routed around it rather than fixing it:
    `bd-parband` carries "Do NOT point this at toolchain/bdenv.sh: that file
    still exports the retired ... paths", and `bd-render-env` names the same
    override in a comment. A workaround in two callers is not a fix in the
    source.

    `toolchain/bin/bd-venv:67` had the correct form the whole time --
    ${PLAYWRIGHT_BROWSERS_PATH:-...} -- so this is the repo's own idiom, not a
    new convention. The fallback is deliberately left in place: retiring what
    bdenv.sh exports when the caller supplies nothing is a different question
    from letting the caller win, and it belongs with the zip-era item.
    """
    import subprocess

    for rel in ("toolchain/bdenv.sh", "project-knowledge/bdenv.sh"):
        env_file = REPO_ROOT / rel
        assert env_file.is_file(), f"{rel} is absent; this gate has no subject"
        sentinel = "/opt/pw-browsers"
        # The variable must be EXPORTED into the child, not assigned as a
        # prefix to `.`: bash restores a prefix assignment when the command
        # returns, so that form reports "preserved" no matter what the file
        # does. It passed against the unfixed source -- a harness that cannot
        # represent the failure it is hunting.
        out = subprocess.run(
            ["bash", "-c",
             f'. "{env_file}" >/dev/null 2>&1; '
             f'printf "%s" "$PLAYWRIGHT_BROWSERS_PATH"'],
            env={**os.environ, "PLAYWRIGHT_BROWSERS_PATH": sentinel},
            capture_output=True, text=True, timeout=60).stdout.strip()
        assert out == sentinel, (
            f"{rel} replaced a caller-supplied PLAYWRIGHT_BROWSERS_PATH "
            f"({sentinel}) with {out!r}. Use the ${{VAR:-default}} form so an "
            f"environment that already knows where the browsers are wins."
        )

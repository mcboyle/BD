"""Shell scripts must resolve the work-tree interpreter, not bare `python3`.

In the cloud container `python3` is 3.11 WITHOUT the project dependencies,
while `venv/bin/python` is 3.12 -- the box/CI interpreter. Any ladder that
misses the `venv` rung silently selects 3.11.

Measured before this cut: tools/sast.sh and tools/dast.sh both probed
`$REPO_DIR/.venv/bin/python` (which does not exist here) and fell straight
through to `python3` -> Python 3.11.15. They were the only two ladders in the
tree omitting the `venv` rung.

A ladder is a denominator. A rung missing from it is a candidate the script can
never choose, and the failure is silent -- the script runs, under the wrong
interpreter, and reports success.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
FRAGMENT = REPO_ROOT / "scripts" / "lib" / "python_resolve.sh"

# Every tracked shell script that picks a Python interpreter for itself.
LADDER_CONSUMERS = (
    REPO_ROOT / "tools" / "sast.sh",
    REPO_ROOT / "tools" / "dast.sh",
)


def _run(snippet: str, timeout: int = 60) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", "-c", snippet], capture_output=True, text=True,
        cwd=str(REPO_ROOT), timeout=timeout,
    )


def test_the_resolver_fragment_exists_and_parses():
    assert FRAGMENT.is_file(), (
        f"{FRAGMENT} does not exist. Each script inventing its own ladder is "
        f"how two of them ended up missing the venv rung."
    )
    proc = subprocess.run(["bash", "-n", str(FRAGMENT)], capture_output=True, text=True)
    assert proc.returncode == 0, f"fragment does not parse:\n{proc.stderr}"


def test_the_resolver_selects_the_work_tree_venv():
    """Behavioural: source it, ask what it picked, check the version."""
    proc = _run(
        f'. "{FRAGMENT}" && bd_resolve_python "{REPO_ROOT}" && echo "PICKED:$BD_PYTHON_RESOLVED"'
    )
    assert proc.returncode == 0, (
        f"bd_resolve_python failed:\nstdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
    )
    picked = ""
    for line in proc.stdout.splitlines():
        if line.startswith("PICKED:"):
            picked = line.split(":", 1)[1].strip()
    assert picked, f"resolver produced no interpreter:\n{proc.stdout}"

    ver = subprocess.run([picked, "--version"], capture_output=True, text=True)
    assert "3.12" in ver.stdout + ver.stderr, (
        f"resolver selected {picked}, which reports "
        f"{(ver.stdout + ver.stderr).strip()!r}. The work tree's venv is 3.12; "
        f"anything else means the ladder skipped it."
    )


def test_the_resolver_refuses_rather_than_returning_a_wrong_interpreter():
    """A tree with no usable interpreter must fail, not fall back silently.

    Unknown is a third state and it fails. Returning bare python3 here is what
    produced the 3.11 selections in the first place.
    """
    proc = _run(
        f'. "{FRAGMENT}" && '
        f'if bd_resolve_python /nonexistent-tree-xyz; then echo "RETURNED:$BD_PYTHON_RESOLVED"; '
        f'else echo REFUSED; fi'
    )
    assert "REFUSED" in proc.stdout, (
        "the resolver accepted a tree with no venv and returned an interpreter "
        f"anyway:\n{proc.stdout}"
    )


def test_every_ladder_consumer_uses_the_shared_resolver():
    """Two copies of a ladder is a denominator that drifts."""
    offenders = []
    for path in LADDER_CONSUMERS:
        assert path.is_file(), f"{path} missing -- anchor stale"
        text = path.read_text(encoding="utf-8")
        if "python_resolve.sh" not in text:
            offenders.append(path.relative_to(REPO_ROOT).as_posix())
    assert not offenders, (
        f"these scripts still carry a private interpreter ladder: {offenders}. "
        f"Both previously omitted the `venv` rung and selected Python 3.11."
    )


def test_no_consumer_probes_only_the_dotted_venv():
    """`.venv` does not exist in this repo; probing only it means falling through.

    This is the specific regression that made sast.sh and dast.sh select 3.11:
    the ladder LOOKED like it preferred a venv, and did prefer one -- just not
    one that exists here.
    """
    offenders = []
    for path in LADDER_CONSUMERS:
        text = path.read_text(encoding="utf-8")
        if ".venv/bin/python" in text and "python_resolve.sh" not in text:
            offenders.append(path.relative_to(REPO_ROOT).as_posix())
    assert not offenders, (
        f"{offenders} probe `.venv/bin/python` without the shared resolver; "
        f"`.venv` does not exist here, so they fall through to bare python3"
    )

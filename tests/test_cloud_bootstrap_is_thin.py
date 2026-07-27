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
    provisioned -- the exact false-READY the provisioner's own verdict gates
    were built to prevent.
    """
    _source()
    home = tmp_path / "home"
    home.mkdir()
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()

    env = {
        "PATH": "/usr/local/bin:/usr/bin:/bin",
        "HOME": str(home),
    }
    # Short timeout on purpose. If a probe ever matches a real checkout here,
    # the bootstrap execs the REAL provisioner and starts installing packages.
    # An earlier draft did exactly that (a hardcoded /home/user/BD in the probe
    # list), and the failure surfaced as a two-minute hang rather than a clear
    # verdict. Fail fast and say why.
    try:
        proc = subprocess.run(
            ["bash", str(BOOTSTRAP)],
            capture_output=True, text=True, cwd=str(elsewhere), env=env, timeout=20,
        )
    except subprocess.TimeoutExpired:
        pytest.fail(
            "the bootstrap did not return within 20s from a directory with no "
            "checkout. It almost certainly matched a real checkout via an "
            "absolute probe path and exec'd the provisioner -- which means the "
            "no-checkout branch is unreachable on this machine."
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

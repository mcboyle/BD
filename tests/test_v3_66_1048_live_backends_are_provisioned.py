"""Every live-recording backend the app PROBES must be installed by some
provisioning path.

WHY THIS GATE EXISTS. `bulk_downloader/live_recorder.py` probes two backends and
documents streamlink as the preferred one ("Streamlink wins because it has
cam-site-specific plugins that handle the HLS variant selection automatically").
Measured 2026-08-12 at bb37142 across the whole fleet -- test5 (7b4ea932c297),
test4 (102b31c04e7b) and a freshly provisioned test/.84 (5b29e22f94aa) -- the
streamlink binary was absent on ALL of them, and `grep -rn streamlink` finds it
in no requirements manifest, no `bd_system_pkgs` group and no installer shell
script. Nothing has ever installed it.

The consequence is quiet, which is why it survived: `is_available()` returns
True on ffmpeg alone and `preferred_backend()` silently returns the FALLBACK, so
the feature works, reports itself configured, and never runs on the backend the
code says is right for the primary use case. A capability nobody has, reported
as a capability that is fine.

This is the same shape as the ffmpeg entry already in the `media` group, whose
comment records that a doc asserted it was "already present in the base image",
which was false, "and nobody re-derived it".

THE DENOMINATOR CONTAINS THE SUBJECT, DELIBERATELY (CLAUDE.md section 0). The
backend names are extracted from `_detect_backends`'s own returned dict by AST
rather than written here as literals: a hardcoded {"streamlink", "ffmpeg"} would
certify exactly the two backends someone thought of on the day, and a THIRD
backend added later would be outside the gate's denominator while the gate went
on reporting OK. Deriving them means adding a backend to the probe puts it in
scope automatically.
"""

from __future__ import annotations

import ast
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
RECORDER = REPO / "bulk_downloader" / "live_recorder.py"
SYSTEM_DEPS = REPO / "scripts" / "lib" / "system_deps.sh"


def _probed_backend_names() -> set[str]:
    """The keys of the dict `_detect_backends` assigns to `_backend_cache`.

    AST, not grep: the word "streamlink" appears a dozen times in that module in
    docstrings and comments, and a text scan cannot tell the probe's own key set
    from prose about it.
    """
    tree = ast.parse(RECORDER.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef) or node.name != "_detect_backends":
            continue
        for sub in ast.walk(node):
            if not isinstance(sub, ast.Dict):
                continue
            names = {
                k.value
                for k in sub.keys
                if isinstance(k, ast.Constant) and isinstance(k.value, str)
            }
            if names:
                return names
    return set()


def _provisioned_packages() -> set[str]:
    """Every package name any `bd_system_pkgs` group would install.

    `all` is asked for rather than `media` specifically: the question is whether
    ANY provisioning path installs the backend, not whether it sits in the group
    this test's author expected.
    """
    out = subprocess.run(
        ["bash", "-c", f'. "{SYSTEM_DEPS}" && bd_system_pkgs all'],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert out.returncode == 0, f"bd_system_pkgs all failed: {out.stderr!r}"
    return set(out.stdout.split())


def test_the_probe_declares_at_least_one_backend():
    """Non-empty denominator, asserted BEFORE the verdict.

    If `_detect_backends` is renamed or its dict restructured, every assertion
    below would pass over an empty set -- a gate reporting OK because nothing
    was examined, which CLAUDE.md section 0 calls worse than no gate.
    """
    names = _probed_backend_names()
    assert names, (
        "extracted no backend names from _detect_backends in "
        f"{RECORDER}; this gate cannot see its subject and must fail rather "
        "than certify an empty set"
    )


def test_the_package_lookup_returns_something():
    """The other half of the denominator: a broken source fragment would make
    every backend look un-provisioned, which is a different bug from the one
    this file hunts and must not be reported as it."""
    pkgs = _provisioned_packages()
    assert pkgs, f"bd_system_pkgs all returned no packages from {SYSTEM_DEPS}"


@pytest.mark.parametrize("backend", sorted(_probed_backend_names()))
def test_every_probed_backend_is_installed_by_a_provisioning_path(backend):
    """The gate itself.

    A backend the app probes but nothing installs is a capability the host does
    not have, silently downgraded at runtime.
    """
    pkgs = _provisioned_packages()
    assert backend in pkgs, (
        f"live_recorder probes {backend!r} but no bd_system_pkgs group installs "
        f"it, so no provisioning path ever puts it on PATH. Groups currently "
        f"install: {sorted(pkgs)}. Either add {backend!r} to the appropriate "
        f"group in {SYSTEM_DEPS}, or stop probing for it."
    )

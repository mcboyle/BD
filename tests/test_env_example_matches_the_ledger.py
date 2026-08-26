""".env.example must only name knobs that exist.

`.env.example` is the operator's source of truth for the Claude Code panel: it
is what gets copied into the environment config. A name in it that no code reads
is dead config that looks live -- and worse, it displaces the name that IS read.

Measured 2026-07-27: `.env.example` set `BD_INSTALL_BROWSERS=0`. Nothing in the
tree read it. The name `scripts/cloud-setup.sh` actually reads is
`BD_SKIP_BROWSERS`, with INVERTED polarity, so the operator's panel had been
requesting "do not install browsers" in a language nothing spoke, and ~150MB of
browsers were reinstalled every session.

`tools/config_surface_inventory.py` already builds the ledger of every
configuration knob the code reads (399 entries at time of writing). This gate
joins the two: every `BD_*` name offered to the operator must appear there.

Note the direction. This does NOT assert the ledger is a subset of
`.env.example` -- plenty of internal knobs are deliberately undocumented. It
asserts the documented set is a subset of the real one, which is the direction
where a mistake silently costs the operator something.
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
ENV_EXAMPLE = REPO_ROOT / ".env.example"
INVENTORY_TOOL = REPO_ROOT / "tools" / "config_surface_inventory.py"
INVENTORY_JSON = REPO_ROOT / "reports" / "config_surface_inventory.json"
PYTHON = Path(sys.executable)


def _documented_bd_names() -> list[str]:
    text = ENV_EXAMPLE.read_text(encoding="utf-8")
    return [
        m.group(1)
        for m in re.finditer(r"(?m)^(BD_[A-Z0-9_]*)=", text)
    ]


def _ledger_keys() -> set[str]:
    """The set of config keys the code actually reads.

    Regenerates rather than trusting a checked-in artifact: the inventory is
    build-time generated and gitignored, so a stale copy would make this gate
    assert over yesterday's tree. If it cannot be generated, that is UNKNOWN --
    and unknown fails rather than silently passing.
    """
    proc = subprocess.run(
        [str(PYTHON), str(INVENTORY_TOOL)],
        cwd=str(REPO_ROOT), capture_output=True, text=True, timeout=300,
    )
    if proc.returncode != 0 or not INVENTORY_JSON.is_file():
        pytest.fail(
            "cannot regenerate the config-surface ledger, so this gate cannot "
            "see its subject. Treating that as a pass would be the exact S0 "
            f"failure it exists to prevent.\nexit={proc.returncode}\n"
            f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
        )
    data = json.loads(INVENTORY_JSON.read_text(encoding="utf-8"))
    items = data if isinstance(data, list) else (
        data.get("settings") or data.get("items") or []
    )
    keys = {i.get("key") for i in items if isinstance(i, dict)}
    assert len(keys) > 50, (
        f"the ledger returned only {len(keys)} keys; that is implausibly small "
        f"and means the query is broken, not that the surface shrank"
    )
    return keys


def test_the_denominator_is_non_trivial():
    """Guard against a gate that passes because it examined nothing."""
    documented = _documented_bd_names()
    assert documented, (
        ".env.example declares no BD_* names at all -- either the file moved or "
        "the pattern is wrong. Either way this gate is blind."
    )


def test_every_documented_bd_var_is_read_by_the_code():
    documented = _documented_bd_names()
    ledger = _ledger_keys()
    orphans = sorted(n for n in documented if n not in ledger)
    assert not orphans, (
        f".env.example offers BD_* names that no code reads: {orphans}.\n"
        f"Dead config in the operator's own template is worse than none: it "
        f"looks live, and it displaces the name that is actually read. "
        f"(BD_INSTALL_BROWSERS was exactly this -- the executing name is "
        f"BD_SKIP_BROWSERS, with inverted polarity.)"
    )


def test_env_example_does_not_override_the_provisioner_git_identity():
    """GIT_*_NAME in the panel silently overrides the setup script.

    scripts/cloud-setup.sh deliberately sets `Claude <noreply@anthropic.com>`
    so in-session commits satisfy the verified-commit hook. Environment
    variables outrank `git config`, so a GIT_AUTHOR_NAME/GIT_COMMITTER_NAME in
    the operator's env produces a mismatched identity -- a human name on the
    automation's email -- on every commit. Observed on real commits in this
    repository.
    """
    text = ENV_EXAMPLE.read_text(encoding="utf-8")
    offenders = [
        name for name in ("GIT_AUTHOR_NAME", "GIT_COMMITTER_NAME")
        if re.search(rf"(?m)^{name}=", text)
    ]
    assert not offenders, (
        f".env.example sets {offenders}, which override the git identity "
        f"scripts/cloud-setup.sh installs for the verified-commit hook"
    )

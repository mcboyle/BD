"""Destructive cockpit-task cleanup confined to the active test root."""

from __future__ import annotations

import shutil
from pathlib import Path


def remove_test_governance(task_root: Path) -> None:
    """Remove governance only when it is strictly inside this test's cwd."""
    test_root = Path.cwd().resolve(strict=True)
    governance = (task_root / "governance").resolve()
    try:
        relative = governance.relative_to(test_root)
    except ValueError as exc:
        raise RuntimeError(
            "refusing to remove governance outside test-owned root: "
            f"{governance} is not under {test_root}"
        ) from exc
    if relative == Path("."):
        raise RuntimeError(
            f"refusing to remove the test-owned root itself: {test_root}"
        )
    if governance.exists():
        shutil.rmtree(governance)

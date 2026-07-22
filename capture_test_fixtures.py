"""Opt-in roots for tests that consume external capture artifacts.

Private capture corpora are integration evidence, not release-test fixtures.
No legacy host path is consulted unless the corresponding environment variable
is set explicitly for the test process.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


_CAPTURE_ROOT_ENV = "BD_TEST_CAPTURE_ROOT"
_STRICT_CAPTURE_ROOT_ENV = "BD_TEST_STRICT_CAPTURE_ROOT"


@dataclass(frozen=True)
class CaptureFixtureLane:
    """A disabled-by-default view of one external capture fixture root."""

    env_name: str
    root: Optional[Path]

    @property
    def enabled(self) -> bool:
        return self.root is not None

    @staticmethod
    def _validate_name(name: str) -> str:
        candidate = Path(name)
        if not name or candidate.name != name or name in (".", ".."):
            raise ValueError("capture artifact name must be a single file name")
        return name

    def path(self, name: str) -> Path:
        name = self._validate_name(name)
        if self.root is None:
            raise RuntimeError(
                f"external capture lane disabled; set {self.env_name} explicitly"
            )
        return self.root / name

    def has(self, *names: str) -> bool:
        if self.root is None:
            return False
        return all(self.path(name).is_file() for name in names)


def capture_fixture_lane(*, strict: bool = False) -> CaptureFixtureLane:
    """Resolve an explicitly configured external capture-fixture lane.

    The regular lane uses ``BD_TEST_CAPTURE_ROOT``.  The separately installed
    strict corpus uses ``BD_TEST_STRICT_CAPTURE_ROOT`` so enabling one private
    corpus cannot accidentally discover the other.
    """

    env_name = _STRICT_CAPTURE_ROOT_ENV if strict else _CAPTURE_ROOT_ENV
    raw = os.environ.get(env_name, "").strip()
    if not raw:
        return CaptureFixtureLane(env_name=env_name, root=None)
    root = Path(raw).expanduser()
    if not root.is_absolute():
        raise ValueError(f"{env_name} must be an absolute path")
    return CaptureFixtureLane(env_name=env_name, root=root.resolve(strict=False))


def validate_capture_fixture_roots() -> None:
    """Fail early when an explicitly configured fixture root is unusable."""

    for strict in (False, True):
        lane = capture_fixture_lane(strict=strict)
        if lane.enabled and not lane.root.is_dir():
            raise ValueError(f"{lane.env_name} directory not found: {lane.root}")

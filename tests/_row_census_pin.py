"""Refuse an expired human-review census before pytest executes a row.

A row that pins conclusions drawn from a whole-tree human review declares this
module global in one of its changed ``tests/test*.py`` files::

    BD_WHOLE_TREE_CENSUS_PIN = {
        "row": 292,
        "taken_at": "v3.66.1274",
    }

The version is evidence supplied by the reviewer.  This plugin only compares
and reports it; it deliberately has no writer or re-pin mode.  A declaration
introduced or modified by the active worker/candidate is checked against that
row's integration target during pytest collection, which is the first
executable phase of ``bd-qa-row.sh``.  Historical declarations are inert on
later cuts unless their carrier is modified again.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path
import re
import subprocess
from types import ModuleType

import pytest


_DECLARATION = "BD_WHOLE_TREE_CENSUS_PIN"
_INTEGRATION_TARGET = "refs/remotes/origin/main^{commit}"
_VERSION_FILE = "bulk_downloader/__init__.py"
_VERSION_LINE = re.compile(r'^__version__ = "(\d+\.\d+\.\d+)"$', re.MULTILINE)
_DECLARED_VERSION = re.compile(r"^v(\d+\.\d+\.\d+)$")


class CensusMeasurementError(RuntimeError):
    """The census expiry could not be measured safely."""


class CensusExpired(RuntimeError):
    """A declared human-review census predates the integration target."""


@dataclass(frozen=True)
class CensusPin:
    row: int
    taken_at: str
    carrier: Path


def _git(repo: Path, *args: str) -> str:
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=repo,
            capture_output=True,
            text=True,
            timeout=15,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise CensusMeasurementError(f"git {' '.join(args)} failed: {exc}") from exc
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip() or "no diagnostic"
        raise CensusMeasurementError(
            f"git {' '.join(args)} exited {result.returncode}: {detail}"
        )
    return result.stdout


def _version_at(repo: Path, revision: str) -> str:
    source = _git(repo, "show", f"{revision}:{_VERSION_FILE}")
    match = _VERSION_LINE.search(source)
    if match is None:
        raise CensusMeasurementError(
            f"{revision}:{_VERSION_FILE} declares no exact __version__"
        )
    return f"v{match.group(1)}"


def _literal_declaration(carrier: Path) -> dict[str, object] | None:
    try:
        source = carrier.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(carrier))
    except (OSError, UnicodeError, SyntaxError) as exc:
        raise CensusMeasurementError(
            f"{carrier}: cannot read literal census declaration: {exc}"
        ) from exc

    values: list[ast.expr] = []
    for statement in tree.body:
        if isinstance(statement, ast.Assign):
            marker_targets = [
                target
                for target in statement.targets
                if isinstance(target, ast.Name) and target.id == _DECLARATION
            ]
            if marker_targets:
                if len(statement.targets) != 1:
                    raise CensusMeasurementError(
                        f"{carrier}: {_DECLARATION} must be one literal declaration"
                    )
                values.append(statement.value)
        elif (
            isinstance(statement, ast.AnnAssign)
            and isinstance(statement.target, ast.Name)
            and statement.target.id == _DECLARATION
            and statement.value is not None
        ):
            values.append(statement.value)

    if not values:
        return None
    if len(values) != 1:
        raise CensusMeasurementError(
            f"{carrier}: {_DECLARATION} must be one literal declaration"
        )
    value = values[0]
    if not isinstance(value, ast.Dict) or any(
        not isinstance(key, ast.Constant)
        or not isinstance(key.value, str)
        or not isinstance(item, ast.Constant)
        for key, item in zip(value.keys, value.values, strict=True)
    ):
        raise CensusMeasurementError(
            f"{carrier}: {_DECLARATION} must be one literal declaration"
        )
    raw = {
        key.value: item.value
        for key, item in zip(value.keys, value.values, strict=True)
    }
    if len(raw) != len(value.keys):
        raise CensusMeasurementError(
            f"{carrier}: {_DECLARATION} must not repeat literal keys"
        )
    return raw


def _pin_from_raw(raw: dict[str, object], carrier: Path) -> CensusPin:
    if not isinstance(raw, dict) or set(raw) != {"row", "taken_at"}:
        raise CensusMeasurementError(
            f"{carrier}: {_DECLARATION} must contain exactly row and taken_at"
        )
    row = raw["row"]
    taken_at = raw["taken_at"]
    if not isinstance(row, int) or isinstance(row, bool) or row <= 0:
        raise CensusMeasurementError(f"{carrier}: census row must be a positive integer")
    if not isinstance(taken_at, str) or _DECLARED_VERSION.fullmatch(taken_at) is None:
        raise CensusMeasurementError(
            f"{carrier}: census taken_at must be an exact version such as v3.66.1274"
        )
    return CensusPin(row=row, taken_at=taken_at, carrier=carrier)


def _parse_pin(module: ModuleType, carrier: Path) -> CensusPin | None:
    namespace = vars(module)
    if _DECLARATION not in namespace:
        return None
    raw = _literal_declaration(carrier)
    if raw is None:
        raise CensusMeasurementError(
            f"{carrier}: {_DECLARATION} must be one literal declaration"
        )
    if namespace[_DECLARATION] != raw:
        raise CensusMeasurementError(
            f"{carrier}: runtime census value differs from its literal declaration"
        )
    return _pin_from_raw(raw, carrier)


def _active_base(repo: Path, carrier: Path) -> str | None:
    try:
        relative = carrier.resolve().relative_to(repo.resolve()).as_posix()
    except ValueError as exc:
        raise CensusMeasurementError(
            f"census carrier {carrier} is outside pytest root {repo}"
        ) from exc

    dirty = _git(
        repo, "status", "--porcelain=v1", "--untracked-files=all", "--", relative
    ).strip()
    if dirty:
        return _git(repo, "rev-parse", "--verify", _INTEGRATION_TARGET).strip()

    parent = _git(repo, "rev-parse", "--verify", "HEAD^1").strip()
    changed = _git(repo, "diff", "--name-only", parent, "HEAD", "--", relative)
    if not changed.strip():
        return None

    head_version = _version_at(repo, "HEAD")
    parent_version = _version_at(repo, parent)
    return parent if head_version != parent_version else "HEAD"


def _release_commit(repo: Path, base: str, version: str) -> str:
    revisions = _git(repo, "rev-list", "--first-parent", base, "--", _VERSION_FILE)
    for revision in revisions.splitlines():
        if _version_at(repo, revision) == version:
            return revision
    raise CensusMeasurementError(
        f"{version} is not a measurable release on {base}'s first-parent history"
    )


def _unreviewed_file_count(repo: Path, census: str, base: str) -> int:
    changed = _git(
        repo,
        "diff",
        "--no-renames",
        "--name-only",
        "--diff-filter=ACDMRT",
        census,
        base,
        "--",
    )
    paths = tuple(line for line in changed.splitlines() if line)
    if not paths:
        raise CensusMeasurementError(
            "tree versions differ but the changed-file denominator is empty"
        )
    return len(paths)


def _enforce_pin(repo: Path, pin: CensusPin, base: str) -> None:
    base_version = _version_at(repo, base)
    if pin.taken_at == base_version:
        return
    census = _release_commit(repo, base, pin.taken_at)
    unreviewed = _unreviewed_file_count(repo, census, base)
    raise CensusExpired(
        f"REFUSED row {pin.row}: census taken at {pin.taken_at}, "
        f"tree is now {base_version}, {unreviewed} files unreviewed"
    )


def _dirty_test_carriers(repo: Path) -> tuple[Path, ...]:
    if not (repo / "tests").is_dir():
        return ()
    try:
        _git(repo, "rev-parse", "--verify", "HEAD")
    except CensusMeasurementError as head_error:
        revisions = _git(repo, "rev-list", "--all", "--max-count=1")
        if revisions.strip():
            raise head_error
        # Disposable regen/idempotence fixtures may populate an intent-to-add
        # index without creating a commit.  They have no integration baseline,
        # so there is no active row whose human-review claim can be compared.
        return ()
    pathspec = ":(glob)tests/test*.py"
    tracked = _git(
        repo,
        "diff",
        "--no-renames",
        "--name-only",
        "--diff-filter=ACMRT",
        "HEAD",
        "--",
        pathspec,
    )
    untracked = _git(
        repo,
        "ls-files",
        "--others",
        "--exclude-standard",
        "--",
        pathspec,
    )
    relatives = {line for line in (tracked + untracked).splitlines() if line}
    return tuple(sorted((repo / relative).resolve() for relative in relatives))


def verify_dirty_candidate(repo_path: str | Path) -> int:
    """Verify literal pins in the integration patch before regen mutates it."""
    repo = Path(repo_path).resolve()
    pins: list[CensusPin] = []
    for carrier in _dirty_test_carriers(repo):
        raw = _literal_declaration(carrier)
        if raw is not None:
            pins.append(_pin_from_raw(raw, carrier))
    if not pins:
        return 0

    base = _git(repo, "rev-parse", "--verify", _INTEGRATION_TARGET).strip()
    for pin in sorted(pins, key=lambda item: (item.row, str(item.carrier))):
        _enforce_pin(repo, pin, base)
    return len(pins)


def _pins(items) -> tuple[CensusPin, ...]:
    found: dict[Path, CensusPin] = {}
    for item in items:
        module = getattr(item, "module", None)
        source = getattr(module, "__file__", None) if module is not None else None
        if source is None:
            continue
        carrier = Path(source).resolve()
        if carrier in found:
            continue
        pin = _parse_pin(module, carrier)
        if pin is not None:
            found[carrier] = pin
    return tuple(sorted(found.values(), key=lambda pin: (pin.row, str(pin.carrier))))


def pytest_collection_modifyitems(config, items) -> None:
    try:
        pins = _pins(items)
        # This is the negative-control fast path.  A row without a declaration
        # does no Git work and cannot be delayed or refused by this mechanism.
        if not pins:
            return

        repo = Path(config.rootpath).resolve()
        for pin in pins:
            base = _active_base(repo, pin.carrier)
            if base is None:
                continue
            _enforce_pin(repo, pin, base)
    except CensusExpired as exc:
        pytest.exit(str(exc), returncode=4)
    except CensusMeasurementError as exc:
        pytest.exit(f"UNKNOWN census expiry: {exc}", returncode=4)

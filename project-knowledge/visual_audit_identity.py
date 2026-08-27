"""Identity boundary shared by the visual-audit capture and artifact builders.

The release authority is parsed rather than imported so an evidence-only tool
does not run package startup code. The capture authority is `/api/health` from
the loopback render backend. Builders accept evidence only when every row in
the non-empty manifest carries that same release identity.
"""
from __future__ import annotations

import ast
import json
from pathlib import Path
import re
from typing import Callable
import urllib.request


CAPTURE_RELEASE_VERSION = "capture_release_version"
_VERSION_RE = re.compile(r"\d+\.\d+\.\d+\Z")


def release_version(repo_root: str | Path) -> str:
    """Return the one literal release version, or fail as UNKNOWN."""
    path = Path(repo_root) / "bulk_downloader" / "__init__.py"
    try:
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(path))
    except (OSError, SyntaxError) as exc:
        raise RuntimeError(
            f"UNKNOWN: release identity unavailable: {exc}"
        ) from exc

    values = []
    for statement in tree.body:
        if isinstance(statement, ast.Assign):
            targets = statement.targets
            value = statement.value
        elif isinstance(statement, ast.AnnAssign):
            targets = [statement.target]
            value = statement.value
        else:
            continue
        if not any(
            isinstance(target, ast.Name) and target.id == "__version__"
            for target in targets
        ):
            continue
        if isinstance(value, ast.Constant) and isinstance(value.value, str):
            values.append(value.value)
        else:
            values.append(None)

    if len(values) != 1:
        raise RuntimeError(
            "UNKNOWN: release identity unavailable: expected exactly one "
            f"literal __version__, found {len(values)}"
        )
    version = values[0]
    if not isinstance(version, str) or not _VERSION_RE.fullmatch(version):
        raise RuntimeError(
            f"UNKNOWN: release identity malformed: {version!r}"
        )
    return version


def health_release_version(
    base_url: str,
    repo_root: str | Path,
    *,
    opener: Callable = urllib.request.urlopen,
    timeout: float = 5.0,
) -> str:
    """Reconcile the loopback render backend with the release authority."""
    expected = release_version(repo_root)
    url = base_url.rstrip("/") + "/api/health"
    try:
        with opener(url, timeout=timeout) as response:
            status = getattr(response, "status", None)
            raw = response.read()
    except Exception as exc:
        raise RuntimeError(
            f"UNKNOWN: capture release identity unavailable: {exc}"
        ) from exc
    if status != 200:
        raise RuntimeError(
            f"UNKNOWN: capture release identity unavailable: HTTP {status!r}"
        )
    try:
        payload = json.loads(raw)
    except (TypeError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeError(
            f"UNKNOWN: capture release identity malformed: {exc}"
        ) from exc
    if not isinstance(payload, dict) or payload.get("ok") is not True:
        raise RuntimeError(
            "UNKNOWN: capture release identity unavailable: health is not ok"
        )
    observed = payload.get("version")
    if not isinstance(observed, str) or not _VERSION_RE.fullmatch(observed):
        raise RuntimeError(
            f"UNKNOWN: capture release identity malformed: {observed!r}"
        )
    if observed != expected:
        raise RuntimeError(
            "UNKNOWN: capture release identity mismatch: "
            f"health={observed!r} release={expected!r}"
        )
    return observed


def stamp_manifest(rows: list[dict], version: str) -> None:
    """Stamp every row after proving a non-empty, well-shaped population."""
    if not rows:
        raise RuntimeError(
            "UNKNOWN: capture release identity unavailable: manifest has zero rows"
        )
    if not _VERSION_RE.fullmatch(version):
        raise RuntimeError(
            f"UNKNOWN: capture release identity malformed: {version!r}"
        )
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            raise RuntimeError(
                "UNKNOWN: capture release identity malformed: "
                f"manifest row {index} is not an object"
            )
        row[CAPTURE_RELEASE_VERSION] = version


def validate_manifest_release(rows: object, repo_root: str | Path) -> str:
    """Require every manifest row to match the independent release source."""
    if not isinstance(rows, list):
        raise RuntimeError(
            "UNKNOWN: capture release identity malformed: manifest is not a list"
        )
    if not rows:
        raise RuntimeError(
            "UNKNOWN: capture release identity unavailable: manifest has zero rows"
        )

    identities = []
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            raise RuntimeError(
                "UNKNOWN: capture release identity malformed: "
                f"manifest row {index} is not an object"
            )
        if CAPTURE_RELEASE_VERSION not in row or row[CAPTURE_RELEASE_VERSION] in (None, ""):
            raise RuntimeError(
                "UNKNOWN: capture release identity unavailable: "
                f"manifest row {index} has no identity"
            )
        identity = row[CAPTURE_RELEASE_VERSION]
        if not isinstance(identity, str) or not _VERSION_RE.fullmatch(identity):
            raise RuntimeError(
                "UNKNOWN: capture release identity malformed: "
                f"manifest row {index} has {identity!r}"
            )
        identities.append(identity)

    expected = release_version(repo_root)
    observed = sorted(set(identities))
    if observed != [expected]:
        raise RuntimeError(
            "UNKNOWN: capture release identity mismatch: "
            f"manifest={observed!r} release={expected!r}"
        )
    return expected


def load_validated_manifest(
    manifest_path: str | Path, repo_root: str | Path
) -> tuple[list[dict], str]:
    """Load a manifest and return its rows plus validated release identity."""
    path = Path(manifest_path)
    try:
        rows = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise RuntimeError(
            f"UNKNOWN: capture release identity unavailable: {exc}"
        ) from exc
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeError(
            f"UNKNOWN: capture release identity malformed: {exc}"
        ) from exc
    return rows, validate_manifest_release(rows, repo_root)

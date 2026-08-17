"""Fail-closed reviewed suppressions for the two defect-scanner frontends."""

from __future__ import annotations

import ast
import hashlib
import json
import os
import stat
from pathlib import Path, PurePosixPath


SCHEMA = "bd-defect-suppressions/v1"
RELATIVE_LEDGER = "project-knowledge/DEFECT_PATTERN_SUPPRESSIONS.json"
_HEX = frozenset("0123456789abcdef")


class SuppressionError(ValueError):
    """The reviewed suppression authority could not be proved valid."""


def _pairs_no_duplicates(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise SuppressionError("duplicate JSON key %r" % key)
        result[key] = value
    return result


def handler_contexts(tree: ast.AST) -> dict[int, str]:
    """Map handlers to semantic lexical/try contexts, never to line numbers."""
    result: dict[int, str] = {}

    def visit(node: ast.AST, owners: tuple[str, ...]) -> None:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            owners = owners + (node.name,)
        if isinstance(node, ast.Try):
            owner = ".".join(owners) or "<module>"
            protected = ast.Module(body=node.body, type_ignores=[])
            protected_ast = ast.dump(
                protected, annotate_fields=True, include_attributes=False
            )
            for handler in node.handlers:
                result[id(handler)] = owner + "\0" + protected_ast
        for child in ast.iter_child_nodes(node):
            visit(child, owners)

    visit(tree, ())
    return result


def finding_fingerprint(dp: str, path: str, node: ast.AST, context: str) -> str:
    """Hash semantic node shape and lexical owner; formatting is not identity."""
    normalized = ast.dump(node, annotate_fields=True, include_attributes=False)
    payload = "\0".join((dp, path, context, normalized)).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _validated_relative_path(raw: object) -> str:
    if not isinstance(raw, str) or not raw or "\\" in raw:
        raise SuppressionError("suppression path must be a nonempty POSIX string")
    path = PurePosixPath(raw)
    if path.is_absolute() or raw != path.as_posix() or any(part in ("", ".", "..") for part in path.parts):
        raise SuppressionError("suppression path is not normalized repository-relative: %r" % raw)
    if len(path.parts) < 2 or path.parts[0] not in {"bulk_downloader", "tools"} or not raw.endswith(".py"):
        raise SuppressionError("suppression path is outside the scanned Python corpus: %r" % raw)
    return raw


def _regular_nonsymlink(root: Path, relative: str) -> Path:
    current = root
    for part in PurePosixPath(relative).parts:
        current = current / part
        try:
            metadata = current.lstat()
        except OSError as exc:
            raise SuppressionError("suppression path unreadable: %s" % relative) from exc
        if stat.S_ISLNK(metadata.st_mode):
            raise SuppressionError("suppression path traverses a symlink: %s" % relative)
    if not stat.S_ISREG(metadata.st_mode):
        raise SuppressionError("suppression path is not a regular file: %s" % relative)
    return current


def _read_utf8(path: Path, label: str) -> str:
    try:
        return path.read_bytes().decode("utf-8")
    except (OSError, UnicodeError) as exc:
        raise SuppressionError("%s unreadable as strict UTF-8" % label) from exc


def load_suppressions(root: str, detector_ids) -> dict[tuple[str, str, str], str]:
    """Load and validate the complete authority before any result is filtered."""
    root_path = Path(root).resolve(strict=True)
    ledger_path = _regular_nonsymlink(root_path, RELATIVE_LEDGER)
    try:
        document = json.loads(
            _read_utf8(ledger_path, "suppression ledger"),
            object_pairs_hook=_pairs_no_duplicates,
        )
    except SuppressionError:
        raise
    except (json.JSONDecodeError, TypeError, ValueError) as exc:
        raise SuppressionError("suppression ledger is malformed JSON") from exc
    if not isinstance(document, dict) or set(document) != {"schema", "entries"}:
        raise SuppressionError("suppression ledger must contain exactly schema and entries")
    if document["schema"] != SCHEMA or not isinstance(document["entries"], list):
        raise SuppressionError("suppression ledger schema is not %s" % SCHEMA)

    allowed = frozenset(detector_ids)
    loaded: dict[tuple[str, str, str], str] = {}
    paths: dict[str, tuple[ast.AST, dict[int, str]]] = {}
    for index, entry in enumerate(document["entries"]):
        if not isinstance(entry, dict) or set(entry) != {"dp", "path", "fingerprint", "rationale"}:
            raise SuppressionError("suppression entry %d has an invalid shape" % index)
        dp = entry["dp"]
        if not isinstance(dp, str) or dp not in allowed:
            raise SuppressionError("suppression entry %d has an unknown detector" % index)
        relative = _validated_relative_path(entry["path"])
        fingerprint = entry["fingerprint"]
        if not isinstance(fingerprint, str) or len(fingerprint) != 64 or any(c not in _HEX for c in fingerprint):
            raise SuppressionError("suppression entry %d has an invalid fingerprint" % index)
        rationale = entry["rationale"]
        if not isinstance(rationale, str) or not rationale.strip() or rationale != rationale.strip():
            raise SuppressionError("suppression entry %d has an invalid rationale" % index)
        identity = (dp, relative, fingerprint)
        if identity in loaded:
            raise SuppressionError("duplicate suppression identity: %s %s %s" % identity)
        if relative not in paths:
            source_path = _regular_nonsymlink(root_path, relative)
            source = _read_utf8(source_path, relative)
            try:
                tree = ast.parse(source, filename=relative)
            except (SyntaxError, ValueError) as exc:
                raise SuppressionError("suppression path does not parse: %s" % relative) from exc
            paths[relative] = (tree, handler_contexts(tree))
        loaded[identity] = rationale
    return loaded


def apply_suppressions(results, suppressions):
    """Return visible and suppressed maps, rejecting ambiguous identities."""
    occurrences: dict[tuple[str, str, str], int] = {}
    for path, findings in results.items():
        for finding in findings:
            fingerprint = finding.get("fingerprint")
            if fingerprint:
                identity = (finding.get("dp"), path, fingerprint)
                occurrences[identity] = occurrences.get(identity, 0) + 1
    collisions = sorted(identity for identity, count in occurrences.items() if count > 1 and identity in suppressions)
    stale = sorted(identity for identity in suppressions if identity not in occurrences)
    errors = []
    if collisions:
        errors.append("ambiguous suppression fingerprint: %r" % (collisions[0],))
    if stale:
        errors.append("stale suppression identity: %r" % (stale[0],))
    if errors:
        return results, {}, errors

    visible, suppressed = {}, {}
    for path, findings in results.items():
        for finding in findings:
            identity = (finding.get("dp"), path, finding.get("fingerprint"))
            target = suppressed if identity in suppressions else visible
            target.setdefault(path, []).append(finding)
    return visible, suppressed, []

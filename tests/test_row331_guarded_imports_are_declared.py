"""Row 331: every third-party import made by the application is declared.

The application deliberately guards many optional imports.  That is useful at
runtime, but it also means an undeclared dependency degrades a feature instead
of failing installation.  This gate derives both sides of the comparison from
the tracked tree: AST import roots from ``bulk_downloader/*.py`` and declared
distribution names from every root ``requirements*.txt`` manifest.
"""
from __future__ import annotations

import ast
import importlib.metadata as metadata
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest
from packaging.requirements import InvalidRequirement, Requirement


BD_GATE_SCOPE = "repo-wide"

_REPO = Path(__file__).resolve().parent.parent
_REQUIREMENTS_NAME = re.compile(r"requirements(?:-[A-Za-z0-9_-]+)?\.txt\Z")

# Stable import-name/distribution-name aliases that cannot be inferred when the
# optional distribution is absent.  Installed metadata augments this map; the
# explicit entries keep lean production and CI environments deterministic.
_DIST_ALIAS_OVERRIDES = {
    "PIL": "pillow",
    "bs4": "beautifulsoup4",
    "yaml": "PyYAML",
    "psycopg2": "psycopg2-binary",
}


class UnknownMeasurement(AssertionError):
    """The gate could not construct its population or declaration set."""


def _unknown(detail: str) -> None:
    raise UnknownMeasurement(f"UNKNOWN: {detail}")


def _canonical_name(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def _distribution_aliases(package_map=None) -> dict[str, str]:
    """Derive unambiguous import/distribution aliases, then apply overrides."""
    if package_map is None:
        try:
            package_map = metadata.packages_distributions()
        except Exception as exc:  # pragma: no cover - environment failure
            _unknown(f"installed distribution metadata cannot be read: {exc}")
    aliases: dict[str, str] = {}
    for import_name, distributions in package_map.items():
        if (len(distributions) == 1
                and _canonical_name(import_name)
                != _canonical_name(distributions[0])):
            aliases[import_name] = distributions[0]
    aliases.update(_DIST_ALIAS_OVERRIDES)
    return aliases


def _tracked_paths(repo: Path) -> tuple[str, ...]:
    try:
        result = subprocess.run(
            ["git", "ls-files", "-z"], cwd=repo, capture_output=True,
            check=False)
    except (OSError, subprocess.SubprocessError) as exc:
        _unknown(f"git ls-files could not run: {exc}")
    if result.returncode:
        detail = result.stderr.decode("utf-8", "replace").strip()
        _unknown(f"git ls-files failed with exit {result.returncode}: {detail}")
    if not result.stdout:
        _unknown("git ls-files returned zero tracked paths")
    if not result.stdout.endswith(b"\0"):
        _unknown("git ls-files -z output was truncated (missing final NUL)")
    try:
        paths = tuple(part.decode("utf-8")
                      for part in result.stdout[:-1].split(b"\0"))
    except UnicodeDecodeError as exc:
        _unknown(f"git ls-files emitted a non-UTF-8 path: {exc}")
    if not paths or any(not path for path in paths):
        _unknown("git ls-files produced an empty path entry")
    if len(paths) != len(set(paths)):
        _unknown("git ls-files produced duplicate tracked paths")
    return paths


def _read_utf8(repo: Path, relative: str, purpose: str) -> str:
    try:
        return (repo / relative).read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        _unknown(f"cannot read {purpose} {relative}: {type(exc).__name__}: {exc}")


def _repo_local_target(import_name: str, importer: str,
                       tracked: set[str]) -> str | None:
    """Return the tracked path satisfying a repo-local absolute import."""
    top_directories = {path.split("/", 1)[0]
                       for path in tracked if "/" in path}
    if import_name in top_directories:
        return import_name + "/"

    owner = importer.rsplit("/", 1)[0] if "/" in importer else ""
    bases = ("", "bulk_downloader", "tools", "toolchain/bin", owner)
    for base in dict.fromkeys(bases):
        prefix = f"{base}/" if base else ""
        for candidate in (f"{prefix}{import_name}.py",
                          f"{prefix}{import_name}/__init__.py"):
            if candidate in tracked:
                return candidate
    return None


def _scan_sources(repo: Path, source_paths: tuple[str, ...],
                  tracked: set[str]) -> dict:
    stdlib = set(sys.stdlib_module_names)
    roots: dict[str, set[str]] = {}
    parsed = 0
    import_nodes = 0
    for relative in source_paths:
        body = _read_utf8(repo, relative, "tracked Python source")
        try:
            tree = ast.parse(body, filename=relative)
        except SyntaxError as exc:
            _unknown(f"cannot parse tracked Python source {relative}: {exc}")
        parsed += 1
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [alias.name.split(".", 1)[0] for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                if node.level or not node.module:
                    continue
                names = [node.module.split(".", 1)[0]]
            else:
                continue
            import_nodes += 1
            for name in names:
                if name in stdlib:
                    continue
                if _repo_local_target(name, relative, tracked):
                    continue
                roots.setdefault(name, set()).add(relative)
    return {"parsed": parsed, "import_nodes": import_nodes, "roots": roots}


def _read_requirements(repo: Path,
                       manifest_paths: tuple[str, ...]) -> dict:
    declared: dict[str, set[str]] = {}
    read = 0
    declaration_lines = 0
    for relative in manifest_paths:
        body = _read_utf8(repo, relative, "requirements manifest")
        read += 1
        for line_number, raw in enumerate(body.splitlines(), 1):
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            if (line.startswith("-r ") or line.startswith("--requirement ")
                    or line.startswith("--requirement=")):
                continue
            if " #" in line:
                line = line.split(" #", 1)[0].rstrip()
            try:
                requirement = Requirement(line)
            except InvalidRequirement as exc:
                _unknown(
                    f"cannot parse requirement {relative}:{line_number}: "
                    f"{line!r}: {exc}")
            declaration_lines += 1
            declared.setdefault(
                _canonical_name(requirement.name), set()).add(relative)
    return {"read": read, "declaration_lines": declaration_lines,
            "declared": declared}


def _audit_repository(repo: Path, *, package_map=None) -> dict:
    tracked_paths = _tracked_paths(repo)
    tracked = set(tracked_paths)
    source_paths = tuple(
        path for path in tracked_paths
        if path.startswith("bulk_downloader/") and path.endswith(".py"))
    manifest_paths = tuple(
        path for path in tracked_paths
        if "/" not in path and _REQUIREMENTS_NAME.fullmatch(path))

    if not source_paths:
        _unknown("tracked bulk_downloader/*.py denominator is zero")
    if not manifest_paths:
        _unknown("tracked requirements*.txt denominator is zero")

    scanned = _scan_sources(repo, source_paths, tracked)
    requirements = _read_requirements(repo, manifest_paths)
    if scanned["parsed"] != len(source_paths):
        _unknown(
            f"parsed {scanned['parsed']} of {len(source_paths)} tracked "
            "bulk_downloader/*.py files")
    if requirements["read"] != len(manifest_paths):
        _unknown(
            f"read {requirements['read']} of {len(manifest_paths)} tracked "
            "requirements*.txt files")
    if scanned["import_nodes"] == 0:
        _unknown("AST scan found zero import nodes")
    if not scanned["roots"]:
        _unknown("stdlib/repo-local subtraction left zero third-party roots")
    if requirements["declaration_lines"] == 0 or not requirements["declared"]:
        _unknown("requirements manifests yielded zero declarations")

    aliases = _distribution_aliases(package_map)
    resolved = {
        root: aliases.get(root, root) for root in scanned["roots"]}
    joined = {
        root for root, distribution in resolved.items()
        if _canonical_name(distribution) in requirements["declared"]}
    if not joined:
        _unknown("zero import roots joined to requirement declarations")
    undeclared = {
        root: {
            "distribution": resolved[root],
            "importers": tuple(sorted(importers)),
        }
        for root, importers in sorted(scanned["roots"].items())
        if _canonical_name(resolved[root]) not in requirements["declared"]
    }
    return {
        "tracked_count": len(tracked_paths),
        "source_paths": source_paths,
        "parsed_sources": scanned["parsed"],
        "import_nodes": scanned["import_nodes"],
        "third_party_roots": scanned["roots"],
        "manifest_paths": manifest_paths,
        "read_manifests": requirements["read"],
        "declaration_lines": requirements["declaration_lines"],
        "declared": requirements["declared"],
        "resolved": resolved,
        "joined": joined,
        "undeclared": undeclared,
    }


def _assert_every_import_is_declared(report: dict) -> None:
    assert not report["undeclared"], (
        "third-party import root(s) declared in no tracked requirements*.txt: "
        f"{report['undeclared']}; measured "
        f"{report['parsed_sources']}/{len(report['source_paths'])} tracked "
        "bulk_downloader/*.py files, "
        f"{report['import_nodes']} AST import nodes, "
        f"{len(report['third_party_roots'])} third-party roots, and "
        f"{report['read_manifests']}/{len(report['manifest_paths'])} "
        "requirements manifests")


def _init_tracked_repo(tmp_path: Path, *, source: str,
                       requirements: str = "Werkzeug==3.1.8\n",
                       extra_sources: dict[str, str] | None = None) -> Path:
    package = tmp_path / "bulk_downloader"
    package.mkdir()
    (package / "sample.py").write_text(source, encoding="utf-8")
    (tmp_path / "requirements.txt").write_text(requirements, encoding="utf-8")
    for relative, body in (extra_sources or {}).items():
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "add", "bulk_downloader", "requirements.txt"],
                   cwd=tmp_path, check=True)
    return tmp_path


def test_every_third_party_application_import_is_declared():
    report = _audit_repository(_REPO)
    assert report["parsed_sources"] == len(report["source_paths"])
    assert report["read_manifests"] == len(report["manifest_paths"])
    assert report["parsed_sources"] > 0
    assert report["import_nodes"] > 0
    assert report["third_party_roots"]
    assert report["declared"]
    assert report["joined"]
    _assert_every_import_is_declared(report)


def test_a_synthetic_ninth_guarded_import_is_caught(tmp_path):
    repo = _init_tracked_repo(
        tmp_path,
        source=("try:\n"
                "    import ninth_guarded_dependency\n"
                "except ImportError:\n"
                "    pass\n"
                "import werkzeug\n"))
    report = _audit_repository(repo, package_map={})
    assert report["source_paths"] == ("bulk_downloader/sample.py",)
    assert report["parsed_sources"] == 1
    assert report["import_nodes"] == 2
    assert set(report["third_party_roots"]) == {
        "ninth_guarded_dependency", "werkzeug"}
    assert set(report["undeclared"]) == {"ninth_guarded_dependency"}
    with pytest.raises(AssertionError, match="ninth_guarded_dependency"):
        _assert_every_import_is_declared(report)


def test_distribution_name_case_does_not_make_werkzeug_a_false_positive(
        tmp_path):
    repo = _init_tracked_repo(tmp_path, source="import werkzeug\n")
    report = _audit_repository(repo, package_map={})
    assert set(report["third_party_roots"]) == {"werkzeug"}
    assert report["resolved"]["werkzeug"] == "werkzeug"
    assert report["joined"] == {"werkzeug"}
    assert report["undeclared"] == {}
    _assert_every_import_is_declared(report)


def test_repo_local_modules_are_subtracted_from_the_population(tmp_path):
    repo = _init_tracked_repo(
        tmp_path, source="import local_helper\nimport werkzeug\n",
        extra_sources={"bulk_downloader/local_helper.py": "VALUE = 1\n"})
    report = _audit_repository(repo, package_map={})
    assert report["parsed_sources"] == 2
    assert report["import_nodes"] == 2
    assert set(report["third_party_roots"]) == {"werkzeug"}
    assert "local_helper" not in report["resolved"]


def test_unreadable_requirements_manifest_is_unknown(tmp_path):
    repo = _init_tracked_repo(tmp_path, source="import werkzeug\n")
    manifest = repo / "requirements.txt"
    manifest.chmod(0)
    assert not os.access(manifest, os.R_OK), (
        "test precondition failed: requirements.txt is still readable")
    try:
        with pytest.raises(
                UnknownMeasurement,
                match=r"UNKNOWN: cannot read requirements manifest "
                      r"requirements\.txt: PermissionError"):
            _audit_repository(repo, package_map={})
    finally:
        manifest.chmod(0o644)


def test_git_ls_files_failure_is_unknown(tmp_path):
    assert not (tmp_path / ".git").exists()
    with pytest.raises(
            UnknownMeasurement,
            match=r"UNKNOWN: git ls-files failed with exit 128"):
        _audit_repository(tmp_path, package_map={})


def test_unparseable_tracked_source_is_unknown(tmp_path):
    repo = _init_tracked_repo(tmp_path, source="try import broken\n")
    with pytest.raises(
            UnknownMeasurement,
            match=r"UNKNOWN: cannot parse tracked Python source "
                  r"bulk_downloader/sample\.py"):
        _audit_repository(repo, package_map={})


def test_zero_source_denominator_is_unknown(tmp_path):
    (tmp_path / "requirements.txt").write_text(
        "Werkzeug==3.1.8\n", encoding="utf-8")
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "add", "requirements.txt"],
                   cwd=tmp_path, check=True)
    with pytest.raises(
            UnknownMeasurement,
            match=r"UNKNOWN: tracked bulk_downloader/\*\.py denominator is zero"):
        _audit_repository(tmp_path, package_map={})


def test_transform_control_imports_gate_without_judging_declarations():
    """Mutation control: exercise the census, but do not judge its verdict."""
    report = _audit_repository(_REPO)
    assert report["parsed_sources"] == len(report["source_paths"])
    assert report["read_manifests"] == len(report["manifest_paths"])

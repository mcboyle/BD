#!/usr/bin/env python3
"""Generate and consume the docs-only CI shard manifest.

The generator measures shard membership from ci.yml and the independent
``_DECLARED`` set.  Selection consumes only the generated manifest so the
early CI job needs no third-party packages before dependency installation.
"""

from __future__ import annotations

import argparse
import ast
import json
from pathlib import Path
import re
import sys


MANIFEST = Path("project-knowledge/CI_DOCSONLY_SHARDS.json")
WORKFLOW = Path(".github/workflows/ci.yml")
DECLARATION = Path("tests/test_v3_66_939_ci_gate_shards_cover_every_gate.py")


class ManifestError(RuntimeError):
    def __init__(self, stage: str, detail: str):
        self.stage = stage
        super().__init__(detail)


def _read_text(path: Path, stage: str) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise ManifestError(stage, f"cannot read {path}: {exc}") from exc


def _workflow(repo: Path) -> dict:
    source = _read_text(repo / WORKFLOW, "workflow")
    try:
        import yaml
    except ImportError as exc:
        raise ManifestError("workflow", "PyYAML is unavailable") from exc
    try:
        parsed = yaml.safe_load(source)
    except yaml.YAMLError as exc:
        raise ManifestError("workflow", f"cannot parse {WORKFLOW}: {exc}") from exc
    if not isinstance(parsed, dict) or not isinstance(parsed.get("jobs"), dict):
        raise ManifestError("workflow", f"{WORKFLOW} has no jobs mapping")
    return parsed


def _declared(repo: Path) -> set[str]:
    source = _read_text(repo / DECLARATION, "declaration")
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        raise ManifestError("declaration", f"cannot parse {DECLARATION}: {exc}") from exc

    # 1. Direct _DECLARED literal check (legacy format)
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        if any(isinstance(target, ast.Name) and target.id == "_DECLARED"
               for target in node.targets):
            try:
                val = ast.literal_eval(node.value)
                if isinstance(val, set) and val:
                    return val
            except (ValueError, TypeError):
                pass

    # 2. Partitioned format (row 810+): _derived_repo_wide() | _NON_DERIVABLE_DECLARED
    non_derivable = None
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "_NON_DERIVABLE_DECLARED":
                    try:
                        val = ast.literal_eval(node.value)
                        if isinstance(val, set):
                            non_derivable = val
                    except (ValueError, TypeError):
                        pass
                    break

    if non_derivable is not None:
        def _get_scope(p: Path) -> str | None:
            text = p.read_text(encoding="utf-8", errors="replace")
            if "BD_GATE_SCOPE" not in text:
                return None
            try:
                t = ast.parse(text)
            except Exception:
                return None
            for n in t.body:
                if isinstance(n, ast.Assign):
                    for tgt in n.targets:
                        if isinstance(tgt, ast.Name) and tgt.id == "BD_GATE_SCOPE":
                            try:
                                return ast.literal_eval(n.value)
                            except Exception:
                                pass
            return None

        derived = set()
        tests_dir = repo / "tests"
        if tests_dir.is_dir():
            for p in sorted(tests_dir.glob("test*.py")):
                if _get_scope(p) == "repo-wide":
                    derived.add(f"tests/{p.name}")
        declared = derived | non_derivable
        pt_path = repo / "tests" / "PROCESS_TESTS.txt"
        if pt_path.is_file():
            process = {
                line.strip() for line in pt_path.read_text("utf-8").splitlines()
                if line.strip() and not line.strip().startswith("#")
            }
            declared -= process
        if declared and all(isinstance(p, str) and p.startswith("tests/test") for p in declared):
            return declared

    # 3. Fallback: runpy if available in runtime environment
    import runpy
    try:
        mod = runpy.run_path(str(repo / DECLARATION))
        dec = mod.get("_DECLARED")
        if isinstance(dec, set) and dec:
            declared = dec
        else:
            raise ManifestError("declaration", "expected one nonempty _DECLARED set")
    except Exception as exc:
        if isinstance(exc, ManifestError):
            raise
        raise ManifestError("declaration", f"cannot resolve _DECLARED from {DECLARATION}: {exc}") from exc
    if not all(isinstance(path, str) and path.startswith("tests/test")
               for path in declared):
        raise ManifestError("declaration", "_DECLARED contains an invalid test path")
    return declared


def _ci_shards_module():
    try:
        from tools import ci_shards          # imported as a package module
    except ImportError:
        import ci_shards                     # run as tools/generate_ci_docsonly_shards.py
    return ci_shards


def _matrix_shards(workflow: dict, repo: Path) -> tuple[str, dict[str, list[str]]]:
    """(matrix job, {shard: [suites]}) -- O1264(d): the matrix carries names only,
    membership is what tools/ci_shards.py resolves for this tree."""
    candidates = []
    for job_name, job in workflow["jobs"].items():
        steps = job.get("steps") or []
        if any(isinstance(step, dict) and "ci_shards.py" in str(step.get("run", ""))
               for step in steps):
            candidates.append(str(job_name))
    if len(candidates) != 1:
        raise ManifestError(
            "workflow", f"expected one job running tools/ci_shards.py, found {candidates}")
    ci_shards = _ci_shards_module()
    try:
        shards = ci_shards.shards(repo)
    except ci_shards.ShardError as exc:
        raise ManifestError("workflow", str(exc)) from exc
    named = [entry.get("name") for entry in
             (((workflow["jobs"][candidates[0]].get("strategy") or {}).get("matrix") or {})
              .get("include") or []) if isinstance(entry, dict)]
    if sorted(named) != sorted(shards):
        raise ManifestError(
            "workflow", f"matrix names {sorted(named)} != resolver shards {sorted(shards)}")
    return candidates[0], shards


def _independent_test_shards(
        workflow: dict, matrix_job: str) -> dict[str, list[str]]:
    shards = {}
    for job_name, job in workflow["jobs"].items():
        if str(job_name) == matrix_job:
            continue
        job_if = str(job.get("if") or "")
        if "schedule" in job_if and "pull_request" not in job_if:
            continue
        body = "\n".join(str(step.get("run", ""))
                         for step in (job.get("steps") or [])
                         if isinstance(step, dict))
        if not re.search(r"\b(?:pytest|vitest)\b", body):
            continue
        suites = sorted(set(re.findall(
            r"tests/test[A-Za-z0-9_./-]*\.py", body)))
        shards[str(job_name)] = suites
    return shards


def derive_manifest(repo: Path) -> dict:
    workflow = _workflow(repo)
    declared = _declared(repo)
    matrix_job, matrix = _matrix_shards(workflow, repo)
    independent = _independent_test_shards(workflow, matrix_job)
    all_members = dict(matrix)
    overlap = sorted(set(all_members) & set(independent))
    if overlap:
        raise ManifestError("workflow", f"duplicate shard name(s): {overlap}")
    all_members.update(independent)
    if not all_members:
        raise ManifestError("workflow", "test-shard denominator is zero")
    scheduled = {suite for suites in all_members.values() for suite in suites}
    missing = sorted(declared - scheduled)
    if missing:
        raise ManifestError(
            "declaration", f"_DECLARED members absent from every shard: {missing}")
    docs_only = sorted(
        name for name, suites in all_members.items() if set(suites) & declared)
    if not docs_only:
        raise ManifestError("declaration", "docs-only shard denominator is zero")
    return {
        "schema": "ci-docsonly-shards-v1",
        "sources": [WORKFLOW.as_posix(), "tools/ci_shards.py",
                    f"{DECLARATION.as_posix()}:_DECLARED"],
        "all_shards": sorted(all_members),
        "docs_only_shards": docs_only,
    }


def render_manifest(manifest: dict) -> str:
    return json.dumps(manifest, indent=2, sort_keys=True) + "\n"


def read_manifest(repo: Path) -> dict:
    path = repo / MANIFEST
    source = _read_text(path, "manifest")
    try:
        manifest = json.loads(source)
    except json.JSONDecodeError as exc:
        raise ManifestError("manifest", f"cannot parse {MANIFEST}: {exc}") from exc
    all_shards = manifest.get("all_shards") if isinstance(manifest, dict) else None
    docs_only = manifest.get("docs_only_shards") if isinstance(manifest, dict) else None
    if (not isinstance(all_shards, list) or not all_shards
            or len(all_shards) != len(set(all_shards))
            or not all(isinstance(name, str) and name for name in all_shards)):
        raise ManifestError("manifest", "all_shards is empty, duplicated, or malformed")
    if (not isinstance(docs_only, list) or not docs_only
            or len(docs_only) != len(set(docs_only))
            or not all(isinstance(name, str) and name for name in docs_only)):
        raise ManifestError("manifest", "docs_only_shards is empty or malformed")
    if not set(docs_only) <= set(all_shards):
        raise ManifestError("manifest", "docs_only_shards names an unknown shard")
    return manifest


def select_shards(manifest: dict, classification_exit: int) -> tuple[bool, list[str]]:
    docs_only = classification_exit == 0
    if docs_only:
        selected = list(manifest["docs_only_shards"])
    else:
        selected = list(manifest["all_shards"])
    if not selected:
        raise ManifestError("selection", "selected shard denominator is zero")
    return docs_only, selected


def _write_github_output(path: Path, payload: dict) -> None:
    try:
        with path.open("a", encoding="utf-8") as stream:
            stream.write("docs_only=%s\n" % str(payload["docs_only"]).lower())
            stream.write("selected_shards=%s\n" % json.dumps(
                payload["selected_shards"], separators=(",", ":")))
    except OSError as exc:
        raise ManifestError("selection", f"cannot write GitHub output {path}: {exc}") from exc


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="generate_ci_docsonly_shards.py")
    sub = parser.add_subparsers(dest="command", required=True)
    for command in ("generate", "check"):
        item = sub.add_parser(command)
        item.add_argument("--repo", required=True)
    select = sub.add_parser("select")
    select.add_argument("--repo", required=True)
    select.add_argument("--classification-exit", required=True, type=int)
    select.add_argument("--github-output")
    args = parser.parse_args(argv)
    repo = Path(args.repo).resolve()
    try:
        if args.command == "generate":
            target = repo / MANIFEST
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(render_manifest(derive_manifest(repo)), encoding="utf-8")
            print(f"generated {MANIFEST}")
            return 0
        if args.command == "check":
            expected = render_manifest(derive_manifest(repo))
            actual = _read_text(repo / MANIFEST, "manifest")
            if actual != expected:
                print(
                    f"manifest step: {MANIFEST} is stale; run generate", file=sys.stderr)
                return 1
            print(f"manifest current: {MANIFEST}")
            return 0
        manifest = read_manifest(repo)
        docs_only, selected = select_shards(manifest, args.classification_exit)
        payload = {"docs_only": docs_only, "selected_shards": selected}
        if args.github_output:
            _write_github_output(Path(args.github_output), payload)
        print(json.dumps(payload, sort_keys=True))
        return 0
    except ManifestError as exc:
        print(f"UNKNOWN at {exc.stage} step: {exc}", file=sys.stderr)
        return 2
    except (OSError, ValueError, TypeError) as exc:
        print(f"UNKNOWN at {args.command} step: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

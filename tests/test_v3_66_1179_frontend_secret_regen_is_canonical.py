"""v3.66.1179: frontend secret generation has one chain and three producers."""
from __future__ import annotations

import ast
import importlib.machinery
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import re


BD_GATE_SCOPE = "repo-wide"

REPO = Path(__file__).resolve().parents[1]
GENERATOR = REPO / "tools" / "gen_frontend_secret_keys.py"
SYNC_GATE = "tests/test_frontend_secret_keys_in_sync.py"
EXPECTED_IMPORTS = {
    "bulk_downloader.site_editor": {"SECRET_FIELDS", "_CONFIG_SECRET_FLOOR"},
    "bulk_downloader.vpn": {"_SECRET_KEY_HINTS"},
    "bulk_downloader.capture_redact": {"SENSITIVE_QS_KEY"},
}


def _load(name: str, path: Path):
    loader = importlib.machinery.SourceFileLoader(name, str(path))
    spec = importlib.util.spec_from_loader(name, loader)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


def _imports_from_source(source: str) -> dict[str, set[str]]:
    tree = ast.parse(source)
    fn = next(
        node for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "_server_sources"
    )
    imports: dict[str, set[str]] = {}
    for node in ast.walk(fn):
        if (isinstance(node, ast.ImportFrom) and node.level == 0 and node.module
                and node.module.startswith("bulk_downloader.")):
            imports.setdefault(node.module, set()).update(
                alias.name for alias in node.names
            )
    return imports


def _producer_imports() -> dict[str, set[str]]:
    return _imports_from_source(GENERATOR.read_text(encoding="utf-8"))


def _producer_paths() -> tuple[str, ...]:
    imports = _producer_imports()
    assert imports == EXPECTED_IMPORTS
    paths = tuple(sorted(module.replace(".", "/") + ".py" for module in imports))
    assert len(paths) == 3
    assert all((REPO / path).is_file() for path in paths)
    return paths


def _band(path: str) -> dict:
    proc = subprocess.run(
        [str(REPO / "toolchain/bin/bd-band-derive"), "--work", str(REPO),
         "--file", path, "--json"],
        cwd=REPO, capture_output=True, text=True, check=True,
    )
    return json.loads(proc.stdout)


def test_generator_imports_define_the_exact_three_producers():
    assert _producer_imports() == EXPECTED_IMPORTS
    assert set(_producer_paths()) == {
        "bulk_downloader/capture_redact.py",
        "bulk_downloader/site_editor.py",
        "bulk_downloader/vpn.py",
    }


def test_split_imports_from_one_producer_are_unioned_not_overwritten():
    source = """
def _server_sources():
    from bulk_downloader.site_editor import SECRET_FIELDS
    from bulk_downloader.site_editor import _CONFIG_SECRET_FLOOR
"""
    assert _imports_from_source(source) == {
        "bulk_downloader.site_editor": {"SECRET_FIELDS", "_CONFIG_SECRET_FLOOR"}
    }


def test_band_engine_has_one_exact_ast_derived_producer_authority():
    band = _load("band_1179", REPO / "toolchain/bin/bd-band-derive")
    expected = set(_producer_paths())
    assert set(band.FRONTEND_SECRET_INPUTS) == expected
    rules = [rule for rule in band.COUNT_COUPLED
             if rule["id"] == "SC-FRONTEND-SECRET-KEYS"]
    assert len(rules) == 1
    assert rules[0]["when_any"] is band.FRONTEND_SECRET_INPUTS
    assert rules[0]["band"] == [SYNC_GATE]
    assert rules[0]["exact_paths"] is True

    for path in sorted(REPO.glob("bulk_downloader/*.py")):
        rel = path.relative_to(REPO).as_posix()
        if rel in expected:
            continue
        assert SYNC_GATE not in band.count_coupled(rel), rel
        labels = {label for label, _why in band.regen_flags([rel], work=str(REPO))}
        assert "FRONTEND_SECRET_KEYS" not in labels, rel


def test_canonical_regen_chain_runs_the_generator_exactly_once_and_first():
    regen = _load("regen_1179", REPO / "toolchain/bin/bd-regen-order")
    commands = [argv for _label, argv, _why in regen.CHAIN]
    command = ["tools/gen_frontend_secret_keys.py"]
    assert commands.count(command) == 1
    assert commands.index(command) < commands.index(["tools/gui_parity_inventory.py"])
    assert regen.CHAIN[-1][0] == "STATIC_KB"


def test_every_real_producer_bands_the_sync_gate_and_regen_obligation():
    for producer in _producer_paths():
        result = _band(producer)
        assert SYNC_GATE in result["band"], producer
        assert "FRONTEND_SECRET_KEYS" in result["regen_flags"], producer


def test_generator_and_generated_output_keep_banding_the_sync_gate():
    for path in (
        "tools/gen_frontend_secret_keys.py",
        "frontend/src/lib/secretKeys.generated.ts",
    ):
        assert SYNC_GATE in _band(path)["band"], path


def test_unrelated_backend_file_does_not_claim_secret_regeneration():
    result = _band("bulk_downloader/versioning.py")
    assert SYNC_GATE not in result["band"]
    assert "FRONTEND_SECRET_KEYS" not in result["regen_flags"]
    same_basename = _band("tools/site_editor.py")
    assert SYNC_GATE not in same_basename["band"]
    assert "FRONTEND_SECRET_KEYS" not in same_basename["regen_flags"]


def test_each_source_domain_mutation_changes_generated_bytes(monkeypatch):
    generator = _load("secret_generator_1179", GENERATOR)
    baseline = generator.generate()

    import bulk_downloader.capture_redact as capture_redact
    import bulk_downloader.site_editor as site_editor
    import bulk_downloader.vpn as vpn

    monkeypatch.setattr(
        site_editor, "SECRET_FIELDS", site_editor.SECRET_FIELDS | {"cut_h_sentinel"}
    )
    assert generator.generate() != baseline
    monkeypatch.undo()

    monkeypatch.setattr(
        site_editor,
        "_CONFIG_SECRET_FLOOR",
        site_editor._CONFIG_SECRET_FLOOR + ("cut_h_sentinel",),
    )
    assert generator.generate() != baseline
    monkeypatch.undo()

    monkeypatch.setattr(
        vpn, "_SECRET_KEY_HINTS", vpn._SECRET_KEY_HINTS + ("cut_h_sentinel",)
    )
    assert generator.generate() != baseline
    monkeypatch.undo()

    monkeypatch.setattr(
        capture_redact,
        "SENSITIVE_QS_KEY",
        generator.re.compile(
            capture_redact.SENSITIVE_QS_KEY.pattern.replace(
                "(token|", "(cut_h_sentinel|token|", 1
            ),
            generator.re.IGNORECASE,
        ),
    )
    assert generator.generate() != baseline


def test_query_regex_shape_or_case_semantics_cannot_be_partially_extracted(monkeypatch):
    generator = _load("secret_generator_shape_1179", GENERATOR)
    import bulk_downloader.capture_redact as capture_redact

    original = capture_redact.SENSITIVE_QS_KEY
    monkeypatch.setattr(
        capture_redact, "SENSITIVE_QS_KEY",
        re.compile("unmapped_prefix|" + original.pattern, re.IGNORECASE),
    )
    try:
        generator.generate()
    except RuntimeError as exc:
        assert "reviewed substring|anchored-exact shape" in str(exc)
    else:
        raise AssertionError("a partially-extracted top-level alternative passed")
    monkeypatch.undo()

    monkeypatch.setattr(
        capture_redact, "SENSITIVE_QS_KEY", re.compile(original.pattern)
    )
    try:
        generator.generate()
    except RuntimeError as exc:
        assert "flags changed" in str(exc)
    else:
        raise AssertionError("case-sensitive backend drift passed frontend generation")


def _ci_generated_members(workflow: str) -> list[str]:
    command = "generated_raw=$(python toolchain/bin/bd-regen-order --tracked-outputs)"
    assert workflow.count(command) == 1, (
        "CI must derive generated artifacts from the regeneration authority exactly once"
    )
    result = subprocess.run(
        [sys.executable, str(REPO / "toolchain/bin/bd-regen-order"), "--tracked-outputs"],
        cwd=REPO, capture_output=True, text=True, check=True,
    )
    members = result.stdout.splitlines()
    assert members and len(members) == len(set(members))
    return members


def test_ci_tracks_the_generated_output_inside_the_real_array_exactly_once():
    workflow = (REPO / ".github/workflows/ci.yml").read_text(encoding="utf-8")
    target = "frontend/src/lib/secretKeys.generated.ts"
    assert _ci_generated_members(workflow).count(target) == 1

    regen = _load("regen_outputs_1179", REPO / "toolchain/bin/bd-regen-order")
    assert target in regen.tracked_outputs()

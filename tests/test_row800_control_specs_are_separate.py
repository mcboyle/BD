"""Row 800: mutation controls must be declared in control-only specs."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path


BD_GATE_SCOPE = "module"
_ROOT = Path(__file__).resolve().parent.parent
_MIXED = {
    "row649_recovered_commits.json": "TRANSFORM CONTROL:",
    "v3_66_1098_assertion_gate.json": "TRANSFORM CONTROL:",
}


def _tracked_specs():
    result = subprocess.run(
        ["git", "ls-tree", "-r", "--name-only", "HEAD", "tests/mutants"],
        cwd=_ROOT, check=True, text=True, capture_output=True)
    staged = subprocess.run(
        ["git", "diff", "--cached", "--name-only", "--diff-filter=A", "--",
         "tests/mutants"],
        cwd=_ROOT, check=True, text=True, capture_output=True)
    return [Path(path) for path in sorted(
        set(result.stdout.splitlines()) | set(staged.stdout.splitlines())
    ) if path.endswith(".json")]


def test_controls_are_not_label_only_or_mixed_with_regressions():
    specs = _tracked_specs()
    assert len(specs) > 0, "fixture must enumerate a nonzero tracked denominator"
    documents = {path.name: json.loads((_ROOT / path).read_text()) for path in specs}
    offenders = [name for name, document in documents.items()
                 if name != "row708_no_nav_login_is_not_success.json"
                 and not (name.endswith("_transform_control.json") or document.get("control_spec") is True)
                 and any("transform control" in mutant["label"].lower() for mutant in document["mutants"])]
    assert offenders == [], f"label-only controls remain: {offenders}"
    for name, label in _MIXED.items():
        source = documents[name]
        assert all(label not in mutant["label"] for mutant in source["mutants"]), name
        sibling = name.removesuffix(".json") + "_transform_control.json"
        assert documents[sibling]["control_spec"] is True
        assert len(documents[sibling]["mutants"]) == 1
    legacy = documents["w2_toolchain_row612_legacy_tests_control.json"]
    assert legacy["control_spec"] is True

"""A static first-party import rewritten as a dynamic one, INSIDE THE CUT'S OWN DIFF, is refused
at the precut floor -- and an ordinary lazy load, a documented conversion, and a clean tree pass.

THE LAW (bd-codex-briefs/COMMON.md, heading "A REWRITE WHOSE PURPOSE IS TO STOP A GATE REPORTING
IS AN UNDECLARED EDGE, AND IS REFUSED AS ONE."). A worker on cx-ssrf turned two static test imports
into `importlib` dynamic imports; its own stated reason was "avoiding a baseline import-graph
expansion". The dependency was real, still executed, and became invisible to the only gate that
records it (tools/dependency_graph.py derives edges from ast.Import / ast.ImportFrom only).

WHY THIS IS A DIFF CHECK AND NOT A CENSUS (PM-B ruling 2026-09-07, "the tree-wide form is dead").
At origin/main dd959706 the tree holds ~500 first-party dynamic call sites, 194 of them plain
circular-avoidance, residue 307 -- a detector firing 307 times is suppressed inside a day. A DODGE IS
AN ACT, NOT A STATE: a long-standing lazy load and one written ten minutes ago to silence a gate are
byte-identical. The discriminating question is about the diff: did THIS cut convert a static
first-party import into a dynamic one? On a clean tree that population is zero by construction.

CONTROLS, BOTH DIRECTIONS, from the real artifacts: the cx-ssrf conversion (the gen1 static form
`from bulk_downloader import interstitial, templates` -> the gen3 `importlib.import_module` form,
fleet-run-artifacts/codex-remote/cx-ssrf/.gen1-9path-*/patch.diff and .gen3-10path-*/patch.diff)
must be CAUGHT; tools/decomp/templates_snapshot.py's documented in-function import, which was never
a conversion, must PASS; and an added-only dynamic import (the ordinary lazy load) must PASS.

Subprocess convention: the tool is run as `python bd-import-dodge --tree <repo> --json`, the way
bd-footguns' tool detector runs it (cwd is NEVER the tree; the sandbox is elsewhere).
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

BD_GATE_SCOPE = "repo-wide"

_REPO = Path(__file__).resolve().parent.parent
_TOOL = _REPO / "toolchain" / "bin" / "bd-import-dodge"
_LAW = "A REWRITE WHOSE PURPOSE IS TO STOP A GATE REPORTING IS AN UNDECLARED EDGE"
_FREE_PATH = "OWED TO THE INTEGRATOR -- NEW IMPORT EDGES"
_NO_RUNG = "CONSUMES NO RUNG"

# The real import regions, verbatim from the retained generations (see the module docstring).
_GEN1_STATIC = '''"""Row 771: comma-bearing interstitial controls remain safe and dismissible."""
from __future__ import annotations

import ast
import re
from dataclasses import dataclass
from pathlib import Path

from bulk_downloader import interstitial, templates


BD_GATE_SCOPE = "repo-wide"


def probe():
    return interstitial, templates
'''

_GEN3_DYNAMIC = '''"""Row 771: comma-bearing interstitial controls remain safe and dismissible."""
from __future__ import annotations

import ast
import importlib
import re
from dataclasses import dataclass
from pathlib import Path

interstitial = importlib.import_module("bulk_downloader.interstitial")
templates = importlib.import_module("bulk_downloader.templates")


BD_GATE_SCOPE = "repo-wide"


def probe():
    return interstitial, templates
'''


def _git(repo, *args):
    return subprocess.run(
        ["git", "-C", str(repo), *args], check=True, capture_output=True, text=True,
        env={**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.invalid",
             "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.invalid"},
    ).stdout


def _fixture_repo(tmp_path, test_source=_GEN1_STATIC, extra=None):
    """A committed base: a first-party package with two modules, a tools/ module and one test."""
    repo = tmp_path / "repo"
    (repo / "bulk_downloader").mkdir(parents=True)
    (repo / "bulk_downloader" / "__init__.py").write_text('__version__ = "0"\n')
    (repo / "bulk_downloader" / "interstitial.py").write_text("def clear_gates():\n    return 0\n")
    (repo / "bulk_downloader" / "templates.py").write_text("TEMPLATES = []\n")
    (repo / "bulk_downloader" / "runner.py").write_text("def run():\n    return 1\n")
    (repo / "tools").mkdir()
    (repo / "tools" / "dependency_graph.py").write_text("EDGES = 0\n")
    (repo / "tests").mkdir()
    (repo / "tests" / "test_row771_interstitial_comma.py").write_text(test_source)
    for rel, body in (extra or {}).items():
        p = repo / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body)
    _git(repo, "init", "-q")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "base")
    return repo


def _run(repo, *flags):
    proc = subprocess.run(
        [sys.executable, str(_TOOL), "--tree", str(repo), *flags],
        capture_output=True, text=True, timeout=120, cwd=_REPO,
    )
    return proc


def _payload(repo, *flags):
    proc = _run(repo, "--json", *flags)
    try:
        doc = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:  # pragma: no cover - the assertion names the defect
        raise AssertionError("--json did not print one JSON document:\n%s\n%s" % (proc.stdout, proc.stderr)) from exc
    return proc.returncode, doc


def _write_test(repo, source):
    (repo / "tests" / "test_row771_interstitial_comma.py").write_text(source)


def _precondition_both_sides_parse(repo):
    """The seam is nonzero only if both sides are real Python that the gate would read."""
    import ast
    base = _git(repo, "show", "HEAD:tests/test_row771_interstitial_comma.py")
    live = (repo / "tests" / "test_row771_interstitial_comma.py").read_text()
    ast.parse(base)
    ast.parse(live)
    assert base != live, "fixture precondition: the file must differ from its base"


# ------------------------------------------------------------------ CAUGHT --

def test_the_cx_ssrf_conversion_is_caught_with_the_law_and_the_free_path_named(tmp_path):
    repo = _fixture_repo(tmp_path)
    _write_test(repo, _GEN3_DYNAMIC)
    _precondition_both_sides_parse(repo)
    rc, doc = _payload(repo)
    assert doc["changed_py"] == 1, doc
    dodges = [f for f in doc["findings"] if f["kind"] == "dodge"]
    assert sorted(f["module"] for f in dodges) == [
        "bulk_downloader.interstitial", "bulk_downloader.templates"], doc
    assert all(f["file"] == "tests/test_row771_interstitial_comma.py" for f in dodges)
    assert doc["verdict"] == "DODGE" and rc == 1, (rc, doc)
    text = _run(repo)
    assert text.returncode == 1
    out = text.stdout + text.stderr
    for needle in (_LAW, _FREE_PATH, _NO_RUNG, "import_graph_gate.py --check",
                   "IMPORT-DODGE tests/test_row771_interstitial_comma.py"):
        assert needle in out, "refusal must carry %r:\n%s" % (needle, out)


def test_the_gen3_workers_own_stated_reason_names_the_gate_and_is_still_the_dodge(tmp_path):
    repo = _fixture_repo(tmp_path)
    _write_test(repo, _GEN3_DYNAMIC.replace(
        'interstitial = importlib.import_module("bulk_downloader.interstitial")',
        '# The test genuinely executes both modules, so preserve that runtime behaviour through\n'
        '# explicit dynamic imports, avoiding a baseline import-graph expansion.\n'
        'interstitial = importlib.import_module("bulk_downloader.interstitial")'))
    _precondition_both_sides_parse(repo)
    rc, doc = _payload(repo)
    assert rc == 1 and doc["verdict"] == "DODGE", doc
    named = [f for f in doc["findings"] if f["module"] == "bulk_downloader.interstitial"]
    assert len(named) == 1 and named[0]["kind"] == "dodge", doc
    assert named[0]["gate_word"], "the refusal must name the word that made the reason a gate reason: %r" % named[0]
    assert "baseline" in named[0]["reason"]


def test_a_bare_noqa_is_not_a_written_reason(tmp_path):
    repo = _fixture_repo(tmp_path)
    _write_test(repo, _GEN3_DYNAMIC.replace(
        'interstitial = importlib.import_module("bulk_downloader.interstitial")',
        'interstitial = importlib.import_module("bulk_downloader.interstitial")  # noqa: E402'))
    _precondition_both_sides_parse(repo)
    rc, doc = _payload(repo)
    assert rc == 1, doc
    short = [f for f in doc["findings"] if f["module"] == "bulk_downloader.interstitial"]
    assert short[0]["kind"] == "dodge" and "words" in short[0]["why"], doc


def test_the_four_word_floor_is_the_boundary_the_declaration_names(tmp_path):
    """FOOTGUNS.json declares "four words or more"; the tool is pinned TO that number from both
    sides. Three words beside the call is refused and the refusal says 4; four words passes.
    (bd-lens-L1, shape leg, round 1: `_MIN_REASON_WORDS = 4 -> 1` escaped every test in the tree.)"""
    single = (
        "import importlib\n"
        'interstitial = importlib.import_module("bulk_downloader.interstitial")  # %s\n'
        "from bulk_downloader import templates\n")
    # three words: refused, and the refusal names the declared floor
    repo = _fixture_repo(tmp_path / "three")
    _write_test(repo, _GEN1_STATIC.replace(
        "from bulk_downloader import interstitial, templates\n", single % "avoids circular import"))
    _precondition_both_sides_parse(repo)
    rc, doc = _payload(repo)
    assert rc == 1 and doc["verdict"] == "DODGE", doc
    assert [(f["module"], f["kind"]) for f in doc["findings"]] == [
        ("bulk_downloader.interstitial", "dodge")], doc
    assert doc["findings"][0]["why"] == (
        "3 words beside the call (a written reason is 4 words or more)"), doc
    # four words: the same call, one word longer, is documented and the tree is CLEAN
    repo = _fixture_repo(tmp_path / "four")
    _write_test(repo, _GEN1_STATIC.replace(
        "from bulk_downloader import interstitial, templates\n", single % "avoids a circular import"))
    _precondition_both_sides_parse(repo)
    rc, doc = _payload(repo)
    assert rc == 0 and doc["verdict"] == "CLEAN", doc
    assert [(f["module"], f["kind"], f["why"]) for f in doc["findings"]] == [
        ("bulk_downloader.interstitial", "documented", None)], doc


def test_the_dunder_import_form_is_the_same_conversion(tmp_path):
    repo = _fixture_repo(tmp_path)
    _write_test(repo, _GEN1_STATIC.replace(
        "from bulk_downloader import interstitial, templates",
        'interstitial = __import__("bulk_downloader.interstitial", fromlist=["clear_gates"])\n'
        'from bulk_downloader import templates'))
    _precondition_both_sides_parse(repo)
    rc, doc = _payload(repo)
    assert rc == 1, doc
    assert [f["module"] for f in doc["findings"]] == ["bulk_downloader.interstitial"], doc


def test_an_aliased_from_import_of_import_module_is_the_same_conversion(tmp_path):
    """`from importlib import import_module as _load` hides the callee's name, not the act."""
    repo = _fixture_repo(tmp_path)
    _write_test(repo, _GEN1_STATIC.replace(
        "from bulk_downloader import interstitial, templates\n",
        "from importlib import import_module as _load\n\n"
        "interstitial = _load(\"bulk_downloader.interstitial\")\n"
        "templates = _load(\"bulk_downloader.templates\")\n"))
    _precondition_both_sides_parse(repo)
    rc, doc = _payload(repo)
    assert rc == 1 and doc["verdict"] == "DODGE", doc
    assert sorted(f["module"] for f in doc["findings"] if f["kind"] == "dodge") == [
        "bulk_downloader.interstitial", "bulk_downloader.templates"], doc


def test_the_package_relative_import_module_form_is_the_same_module(tmp_path):
    repo = _fixture_repo(tmp_path)
    _write_test(repo, _GEN1_STATIC.replace(
        "from bulk_downloader import interstitial, templates",
        'import importlib\n'
        'interstitial = importlib.import_module(".interstitial", "bulk_downloader")\n'
        'from bulk_downloader import templates'))
    _precondition_both_sides_parse(repo)
    rc, doc = _payload(repo)
    assert rc == 1, doc
    assert [f["module"] for f in doc["findings"]] == ["bulk_downloader.interstitial"], doc


def test_a_conversion_inside_a_product_module_with_a_bare_stem_import_is_caught(tmp_path):
    """The gate resolves `import runner` inside the package to bulk_downloader/runner.py; so does this."""
    repo = _fixture_repo(tmp_path, extra={
        "bulk_downloader/admission.py": "import runner\n\ndef go():\n    return runner.run()\n"})
    (repo / "bulk_downloader" / "admission.py").write_text(
        "import importlib\n\ndef go():\n    runner = importlib.import_module('bulk_downloader.runner')\n"
        "    return runner.run()\n")
    rc, doc = _payload(repo)
    assert rc == 1, doc
    assert [(f["file"], f["module"]) for f in doc["findings"]] == [
        ("bulk_downloader/admission.py", "bulk_downloader.runner")], doc


# -------------------------------------------------------------------- PASS --

def test_a_conversion_with_a_written_engineering_reason_above_the_call_passes(tmp_path):
    repo = _fixture_repo(tmp_path)
    _write_test(repo, _GEN3_DYNAMIC.replace(
        'interstitial = importlib.import_module("bulk_downloader.interstitial")\n'
        'templates = importlib.import_module("bulk_downloader.templates")',
        '# lazy: interstitial imports the runner at module load and the runner imports this test helper,\n'
        '# so the module-level form is a circular import at collection time.\n'
        'interstitial = importlib.import_module("bulk_downloader.interstitial")\n'
        'templates = importlib.import_module("bulk_downloader.templates")  # same cycle, same reason as above'))
    _precondition_both_sides_parse(repo)
    rc, doc = _payload(repo)
    assert rc == 0 and doc["verdict"] == "CLEAN", doc
    documented = sorted((f["module"], f["kind"]) for f in doc["findings"])
    assert documented == [("bulk_downloader.interstitial", "documented"),
                          ("bulk_downloader.templates", "documented")], doc
    assert all("circular" in f["reason"] or "same cycle" in f["reason"] for f in doc["findings"]), doc
    text = _run(repo)
    assert text.returncode == 0 and "DOCUMENTED tests/test_row771_interstitial_comma.py" in text.stdout, text.stdout


def test_a_reason_in_the_enclosing_functions_docstring_passes(tmp_path):
    """templates_snapshot.py's shape: the reason lives in the function that holds the lazy import."""
    repo = _fixture_repo(tmp_path)
    _write_test(repo, _GEN1_STATIC.replace(
        "from bulk_downloader import interstitial, templates\n",
        "import importlib\n\n\ndef _load():\n"
        "    \"\"\"Lazy: interstitial pulls the runner in at import and the runner reads this test\n"
        "    helper, so importing it at module level is a cycle at collection time.\"\"\"\n"
        "    return importlib.import_module(\"bulk_downloader.interstitial\")\n\n\n"
        "interstitial = _load()\nfrom bulk_downloader import templates\n"))
    _precondition_both_sides_parse(repo)
    rc, doc = _payload(repo)
    assert rc == 0 and doc["verdict"] == "CLEAN", doc
    assert [(f["module"], f["kind"], f["location"]) for f in doc["findings"]] == [
        ("bulk_downloader.interstitial", "documented", "docstring of _load()")], doc


def test_an_ordinary_lazy_load_with_no_removal_passes(tmp_path):
    repo = _fixture_repo(tmp_path)
    _write_test(repo, _GEN1_STATIC + '\n\ndef lazy():\n    import importlib\n'
                '    return importlib.import_module("bulk_downloader.runner")\n')
    _precondition_both_sides_parse(repo)
    rc, doc = _payload(repo)
    assert rc == 0 and doc["verdict"] == "CLEAN" and doc["findings"] == [], doc
    assert doc["changed_py"] == 1, doc


def test_templates_snapshot_precedent_passes_when_its_file_is_in_the_diff(tmp_path):
    """The real tools/decomp/templates_snapshot.py: an in-function static import documented
    'lazy: no graph edge'. It was never a conversion, so an unrelated edit to that file is CLEAN."""
    real = _REPO / "tools" / "decomp" / "templates_snapshot.py"
    assert real.is_file(), "the named precedent has moved; re-point this control"
    body = real.read_text()
    assert "lazy: no graph edge" in body, "precondition: the documented reason is still in the file"
    repo = _fixture_repo(tmp_path, extra={"tools/decomp/templates_snapshot.py": body})
    (repo / "tools" / "decomp" / "templates_snapshot.py").write_text(
        body.replace('_BASELINE = os.path.join(_REPO, "tools", "decomp", "templates_snapshot_baseline.json")',
                     '_BASELINE = os.path.join(_REPO, "tools", "decomp", "templates_snapshot_baseline.json")  # unrelated edit'))
    changed = _git(repo, "diff", "HEAD", "--name-only").split()
    assert changed == ["tools/decomp/templates_snapshot.py"], changed
    rc, doc = _payload(repo)
    assert rc == 0 and doc["verdict"] == "CLEAN" and doc["findings"] == [], doc
    assert doc["changed_py"] == 1, doc


def test_a_third_party_conversion_is_out_of_scope(tmp_path):
    repo = _fixture_repo(tmp_path, extra={"tools/opt.py": "import yaml\n\ndef load():\n    return yaml\n"})
    (repo / "tools" / "opt.py").write_text(
        "import importlib\n\ndef load():\n    return importlib.import_module('yaml')\n")
    rc, doc = _payload(repo)
    assert rc == 0 and doc["findings"] == [], doc


def test_prose_cannot_fire_it(tmp_path):
    """Structure is parsed, never grepped: the dynamic form appearing only in a comment and a
    docstring is not a call. Dropping the import is the gate's business (a REMOVED edge), not this
    tool's."""
    repo = _fixture_repo(tmp_path)
    _write_test(repo, _GEN1_STATIC.replace(
        "from bulk_downloader import interstitial, templates\n",
        '# was: interstitial = importlib.import_module("bulk_downloader.interstitial")\n'
        '"""templates = importlib.import_module("bulk_downloader.templates")"""\n'
        'interstitial = templates = None\n'))
    _precondition_both_sides_parse(repo)
    rc, doc = _payload(repo)
    assert rc == 0 and doc["findings"] == [], doc


def test_a_no_op_diff_over_the_whole_current_tree_yields_zero_findings(tmp_path):
    """If it fires on an empty diff it is the tree-wide detector wearing a different name."""
    clone = tmp_path / "clone"
    subprocess.run(["git", "clone", "-q", "--no-hardlinks", "--shared", str(_REPO), str(clone)],
                   check=True, capture_output=True, text=True)
    assert (clone / "bulk_downloader").is_dir()
    assert _git(clone, "status", "--porcelain") == ""
    rc, doc = _payload(clone)
    assert rc == 0 and doc["verdict"] == "CLEAN", doc
    assert doc["changed_py"] == 0 and doc["findings"] == [], doc


# ----------------------------------------------------------- COULD NOT LOOK --

def test_a_computed_dynamic_target_beside_a_removed_static_import_is_could_not_look(tmp_path):
    repo = _fixture_repo(tmp_path)
    _write_test(repo, _GEN1_STATIC.replace(
        "from bulk_downloader import interstitial, templates",
        'import importlib\nimport os\n'
        'interstitial = importlib.import_module(os.environ.get("BD_TARGET", "bulk_downloader.interstitial"))\n'
        'from bulk_downloader import templates'))
    _precondition_both_sides_parse(repo)
    rc, doc = _payload(repo)
    assert rc == 2 and doc["verdict"] == "UNKNOWN", doc
    assert [f["kind"] for f in doc["findings"]] == ["unresolved"], doc
    text = _run(repo)
    assert text.returncode == 2 and "COULD NOT LOOK" in (text.stdout + text.stderr)


def test_an_unparseable_side_is_could_not_look_not_clean(tmp_path):
    repo = _fixture_repo(tmp_path)
    _write_test(repo, _GEN3_DYNAMIC + "\ndef broken(:\n")
    rc, doc = _payload(repo)
    assert rc == 2 and doc["verdict"] == "UNKNOWN", doc
    assert doc["findings"] and doc["findings"][0]["kind"] == "unparseable", doc


def test_an_absent_tree_is_cannot_evaluate(tmp_path):
    proc = _run(tmp_path / "absent")
    assert proc.returncode == 2
    assert "CANNOT-EVALUATE" in proc.stderr and "reason=ABSENT" in proc.stderr, proc.stderr


def test_a_tree_that_is_not_a_git_work_tree_is_could_not_look(tmp_path):
    plain = tmp_path / "plain"
    (plain / "bulk_downloader").mkdir(parents=True)
    (plain / "bulk_downloader" / "__init__.py").write_text("")
    rc, doc = _payload(plain)
    assert rc == 2 and doc["verdict"] == "UNKNOWN", doc


def test_an_unresolvable_base_is_could_not_look(tmp_path):
    repo = _fixture_repo(tmp_path)
    rc, doc = _payload(repo, "--base", "no-such-ref")
    assert rc == 2 and doc["verdict"] == "UNKNOWN", doc


def test_json_and_text_exit_codes_agree(tmp_path):
    repo = _fixture_repo(tmp_path)
    _write_test(repo, _GEN3_DYNAMIC)
    assert _run(repo).returncode == _run(repo, "--json").returncode == 1
    clean = _fixture_repo(tmp_path / "second")
    assert _run(clean).returncode == _run(clean, "--json").returncode == 0


# ------------------------------------------------------------- THE RUNNER --

def test_the_registry_names_this_detector_and_the_precut_gate_runs_the_registry():
    """NAME ITS RUNNER IN THE SAME EDIT: bd-precut --gate runs bd-footguns --check, which runs every
    active tool detector, and this entry is one. bd-collect-gate.sh (harness) runs bd-precut --gate
    on the applied patch, so the collect floor inherits it."""
    registry = json.loads((_REPO / "FOOTGUNS.json").read_text())
    hits = [row for row in registry["footguns"] if row["id"] == "FG-IMPORT-REWRITTEN-TO-DODGE-THE-GATE"]
    assert len(hits) == 1, "the registry must carry exactly one entry for this detector"
    row = hits[0]
    assert row["status"] == "active" and row["severity"] == "blocking", row
    det = row["detector"]
    assert det["kind"] == "tool", det
    assert det["cmd"][0] == "bd-import-dodge" and "{tree}" in det["cmd"], det
    assert 1 in det["block_on_exit"] and 2 in det["block_on_exit"], det
    assert (_REPO / "toolchain" / "bin" / det["cmd"][0]).is_file()
    assert os.access(_REPO / "toolchain" / "bin" / det["cmd"][0], os.X_OK)
    precut = (_REPO / "toolchain" / "bin" / "bd-precut").read_text()
    assert '"bd-footguns"' in precut and "--check" in precut, "bd-precut no longer runs the footgun registry"


def test_the_selftest_passes():
    proc = subprocess.run([sys.executable, str(_TOOL), "--selftest"], capture_output=True,
                          text=True, timeout=120, cwd=_REPO)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "SELFTEST PASS" in proc.stdout


# -------------------------------------------------------- TRANSFORM CONTROL --

def test_transform_control_only_loads_the_tool_without_judging_a_conversion():
    """Band member for the declared transform control: it loads the tool and reads a constant,
    and never runs a comparison, so a semantics-preserving rewrite of the comparison MUST ESCAPE."""
    import importlib.machinery
    import importlib.util
    loader = importlib.machinery.SourceFileLoader("_bd_import_dodge_control", str(_TOOL))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    assert mod.FIRST_PARTY == ("bulk_downloader", "tools", "toolchain")

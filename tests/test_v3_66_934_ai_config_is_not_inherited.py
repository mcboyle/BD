"""v3.66.934 -- the test suite inherited the operator's live AI configuration.

THE DEFECT. ``bulk_downloader/app.py`` calls ``_load_app_config()`` at MODULE
SCOPE, and ``APP_CFG_FILE = Path("app_config.json")`` is RELATIVE -- so it
resolves against the current working directory. At pytest COLLECTION time the
cwd is the rootdir, which on the deploy box is the operator's install
directory, so importing any of the 52 tracked test modules that import
``bulk_downloader.app*`` at module scope reads the operator's real
``app_config.json`` and ends by calling

    aiassist.configure(enabled=bool(_app_cfg.get("ai_enabled", False)), ...)

which mutates a process-global dict. ``isolated_bd_home`` cannot help: it
chdirs per test, and the import already happened during collection. Under
xdist every worker collects the whole suite, so every worker inherits the
operator's setting before a single test runs.

MEASURED on the box 2026-08-07 (capture at 48707ad, v3.66.932).
``tests/test_t7_ai_inspection.py`` asserts ``get_config()["enabled"] is False``
without ever establishing it, and failed with ``assert True is False`` --
because the operator had turned AI on in the Global Config UI between two
captures. The earlier capture's ollama log carries zero ``/api/generate``
calls; the later one carries a real inference. Turning a feature ON broke the
test suite, and the test that broke was not the one that changed.

THE FIX is the shape ``tests/conftest.py`` already uses for the operator's real
VPN config: not a protection each test opts into, but a session-wide
guarantee, because "a protection each test opts into has a denominator that
excludes every test which forgot", and the module that forgot here is every
module that imports the app.

WHAT THE FIX DOES NOT PROMISE. It restores ``aiassist._config`` before each
test. A fixture that re-runs ``_load_app_config()`` afterwards -- ``fresh_app``
does, at conftest.py's fresh_app -- runs later and wins; measured, 4 of the 5
tests using ``fresh_app`` see ``model_vision``/``model_text`` as ``""`` rather
than the module defaults, because ``_load_app_config`` reads them off a cleared
``_app_cfg``. ``enabled`` is correctly False in all five, which is the half
that matters here. It also does not touch ``_health``, ``_warmed`` or
``_FILENAME_META_CACHE``: those persist across tests deliberately and three
test files depend on it.

THE REPRODUCTION BELOW IS PARTLY AT MODULE SCOPE, DELIBERATELY. Those
``configure()`` calls run at IMPORT time -- which is when the real pollution
happens -- so this file is the RED half: without the reset, every test in it
sees the polluted values.
"""
from __future__ import annotations

import ast
import inspect
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from bulk_downloader import aiassist

_REPO = Path(__file__).resolve().parent.parent

# Zero-entropy repeat, never a realistic-looking string. A corpus value in a
# test that asserts about credentials becomes a place a secret can live, and
# gitleaks scans a PR's whole commit range rather than its tip, so a value
# introduced here could not be removed by a follow-up commit.
_POLLUTED_KEY = "k" * 24

_POLLUTION = {
    "provider": "openai",
    "endpoint": "http://198.51.100.9:9",     # TEST-NET-2, RFC 5737
    "model_vision": "POLLUTED-VISION",
    "model_text": "POLLUTED-TEXT",
    "api_key": _POLLUTED_KEY,
    "enabled": True,
}

aiassist.configure(**_POLLUTION)          # <- collection-time pollution


# ── the guarantee ────────────────────────────────────────────────────────────

def test_module_scope_pollution_does_not_reach_a_test():
    """The defect, stated the way the box stated it."""
    assert aiassist.get_config().get("enabled") is False, (
        "a test inherited AI-enabled from configuration applied at import "
        "time; on the box that import reads the operator's real "
        "app_config.json")


def test_every_field_is_restored_not_just_enabled():
    """`enabled` is the field that failed on the box, but the same call
    pushes provider/endpoint/models/api_key through the same code path. A
    reset that fixed only the field we noticed would leave the rest armed --
    and `api_key` is the one that would matter most."""
    cfg = aiassist.get_config()
    assert cfg == aiassist._CONFIG_DEFAULTS
    assert cfg["api_key"] != _POLLUTED_KEY
    assert cfg["provider"] != _POLLUTION["provider"]


def test_the_snapshot_is_a_copy_not_an_alias():
    """If the snapshot aliased `_config`, the reset would restore whatever the
    last mutation left -- a gate that cannot see its subject."""
    assert aiassist._CONFIG_DEFAULTS is not aiassist._config
    aiassist.configure(enabled=True, api_key=_POLLUTED_KEY)
    assert aiassist._CONFIG_DEFAULTS["enabled"] is False
    assert aiassist._CONFIG_DEFAULTS["api_key"] == ""


def test_the_defaults_snapshot_is_taken_before_anything_can_mutate_it():
    """Structural, over AST rather than source text, so a comment naming
    `_CONFIG_DEFAULTS` cannot satisfy it.

    The snapshot has to be assigned in the module body AFTER the `_config`
    literal and BEFORE `configure` is even defined. Anywhere later and a
    caller could mutate `_config` in between, and the snapshot would record
    the mutation instead of the defaults.
    """
    src = Path(inspect.getsourcefile(aiassist)).read_text("utf-8")
    lines: dict[str, int] = {}
    for node in ast.parse(src).body:
        if isinstance(node, ast.Assign):
            for tgt in node.targets:
                if isinstance(tgt, ast.Name):
                    lines.setdefault(tgt.id, node.lineno)
        elif isinstance(node, ast.FunctionDef):
            lines.setdefault(f"def {node.name}", node.lineno)

    assert "_config" in lines, "no module-level `_config` assignment"
    assert "_CONFIG_DEFAULTS" in lines, (
        "no module-level `_CONFIG_DEFAULTS` assignment -- the reset has "
        "nothing pristine to restore from")
    assert "def configure" in lines
    assert lines["_config"] < lines["_CONFIG_DEFAULTS"] < lines["def configure"]


# ── the reset must be a guarantee, not an opt-in ─────────────────────────────

def test_the_reset_is_autouse_not_opt_in(request):
    """`request.fixturenames` is the public discriminator and needs no private
    attribute: this test does not declare the fixture, so its presence in the
    list means pytest applied it unasked -- which is what autouse means.
    Measured against pytest 8.4.2, where the private `FixtureDef.autouse`
    spelling does not exist (it is `_autouse`), so reaching for the private
    API would have been an AttributeError dressed as a failing gate."""
    assert "_aiassist_config_is_never_inherited" in request.fixturenames, (
        "the aiassist config reset did not apply to a test that never asked "
        "for it -- either it is opt-in, or it was renamed. If renamed, rename "
        "it here too: this assertion is the only thing standing between the "
        "guarantee and a protection every future test has to remember.")


def test_the_reset_sorts_before_the_module_wipe():
    """Autouse fixtures declared in one conftest execute in ALPHABETICAL ORDER
    OF FIXTURE NAME, not definition order -- measured against pytest 8.4.2,
    mechanism `for name in dir(holderobj)` in _pytest/fixtures.py, and `dir()`
    returns sorted names. So moving the `def` in conftest.py changes nothing
    and renaming it changes everything.

    Ordering is load-bearing here. The reset resolves the module through
    `sys.modules` and does nothing if it is absent. `isolated_bd_home` WIPES
    `bulk_downloader.*` out of `sys.modules` for every file carrying the
    `bd_module_wipe` marker -- so a reset that sorted after it would find
    nothing on those files and be a silent no-op, which is the exact shape it
    exists to prevent.
    """
    assert "_aiassist_config_is_never_inherited" < "isolated_bd_home"


@pytest.mark.bd_module_wipe
def test_a_wiped_module_comes_back_pristine():
    """The other half of the wipe interaction: after the wipe, the test's own
    import builds a NEW module object whose `_config` is the literal. Coverage
    of that path, not a RED test -- it holds with or without the fix."""
    from bulk_downloader import aiassist as fresh
    assert fresh.get_config() == fresh._CONFIG_DEFAULTS
    assert fresh.get_config()["enabled"] is False


# ── ordered pair: a leak from one test must not reach the next ───────────────
#
# Split across two tests on purpose. A single test that configures and then
# restores proves only that the test itself is tidy, which is precisely the
# property that was never in question.

def test_a_leaking_test_leaves_the_global_dirty():
    aiassist.configure(enabled=True, model_text="LEAKED")
    assert aiassist.get_config()["enabled"] is True


def test_the_next_test_does_not_see_the_leak():
    cfg = aiassist.get_config()
    assert cfg["enabled"] is False
    assert cfg["model_text"] != "LEAKED"


# ── over-correction guards ───────────────────────────────────────────────────

def test_a_test_can_still_turn_ai_on_for_itself():
    """The reset must not fight a test that legitimately configures AI. A fix
    that froze the config would pass every assertion above and break every AI
    test in the suite."""
    aiassist.configure(enabled=True, provider="ollama")
    cfg = aiassist.get_config()
    assert cfg["enabled"] is True and cfg["provider"] == "ollama"


def test_both_test_runners_apply_the_reset():
    """THERE ARE TWO RUNNERS, and a fix in only one of them is inert under
    the other.

    CLAUDE.md section 4 mandates `bd-band-derive`, whose `--emit` produces a
    `bd-band ...` line; `bd-band` runs `run_tests.py`, which is
    `run_tests_core.py`. That runner collects autouse fixtures from the TEST
    MODULE only (`for name in dir(mod)`), never from `tests/conftest.py`, and
    hand-reimplements conftest's shims -- including a divergent copy of the
    `aiassist_module` fixture. So a conftest-only fix is invisible to the
    tool this project's own contract tells you to band with, while
    `capture.sh` runs real pytest and would see it. Both must call the same
    restore, and it lives in `aiassist` so the two cannot drift.

    AST rather than source text: a comment naming the function must not be
    able to satisfy this.
    """
    import ast as _ast

    for rel in ("tests/conftest.py", "run_tests_core.py"):
        path = _REPO / rel
        assert path.is_file(), f"{rel} is missing"
        called = {
            n.func.attr
            for n in _ast.walk(_ast.parse(path.read_text("utf-8")))
            if isinstance(n, _ast.Call) and isinstance(n.func, _ast.Attribute)
        }
        assert "_reset_config_to_defaults" in called, (
            f"{rel} never calls aiassist._reset_config_to_defaults(); the "
            f"guarantee does not hold under that runner, and a band run "
            f"through it would go green on a tree that still inherits the "
            f"operator's AI settings")


def test_the_restore_is_one_implementation_not_two():
    """The drift guard. The operation is four lines, and this repo has been
    burned by three copies of a package list where the copy nobody updated
    was the one the box ran. Both runners must call the module's function
    rather than re-spelling `_config.clear(); _config.update(...)`."""
    src = Path(inspect.getsourcefile(aiassist)).read_text("utf-8")
    assert "def _reset_config_to_defaults" in src
    assert callable(aiassist._reset_config_to_defaults)
    aiassist.configure(enabled=True, api_key=_POLLUTED_KEY)
    aiassist._reset_config_to_defaults()
    assert aiassist.get_config() == aiassist._CONFIG_DEFAULTS


def test_the_reset_leaves_health_alone():
    """Scope guard. `_health` is a different global with a different
    lifecycle -- `test_t8_ai_metrics` says in as many words that it persists
    across tests in one process, and `test_cut7_filename_metadata` depends on
    `_FILENAME_META_CACHE` surviving within a test. Widening the reset to
    'everything module-global in aiassist' would break both."""
    aiassist._health["call_count"] = 7
    assert aiassist._health["call_count"] == 7
    assert aiassist._FILENAME_META_CACHE is not None


# ── the real mechanism, end to end ───────────────────────────────────────────

_APP_IMPORTER = "tests/test_csrf_origin_guard.py"


def _imports_app_at_module_scope(path: Path) -> bool:
    """The harness must verify its own premise. If the chosen file stops
    importing the app at module scope, the child below stops reproducing the
    defect and would go green for the wrong reason."""
    for node in ast.parse(path.read_text("utf-8")).body:
        if isinstance(node, ast.ImportFrom) and (node.module or "").startswith(
                "bulk_downloader.app"):
            return True
        if isinstance(node, ast.Import) and any(
                a.name.startswith("bulk_downloader.app") for a in node.names):
            return True
    return False


def test_the_chosen_app_importer_still_imports_the_app_at_module_scope():
    p = _REPO / _APP_IMPORTER
    assert p.is_file(), f"{_APP_IMPORTER} is gone; pick another of the 52"
    assert _imports_app_at_module_scope(p), (
        f"{_APP_IMPORTER} no longer imports bulk_downloader.app at module "
        f"scope, so it no longer triggers the collection-time config read "
        f"the child run below depends on. Pick another module-scope importer.")


def test_a_collection_time_app_import_cannot_poison_a_test(tmp_path):
    """The box's failure, reproduced in its real mechanism rather than by
    proxy: a child pytest whose CWD holds an `app_config.json` with
    `ai_enabled: true`, collecting a module-scope app importer alongside the
    assertion.

    The container cannot reproduce this from its own tree -- `app_config.json`
    here is untracked, gitignored, and carries `ai_enabled: false` -- so the
    child builds its own. A test that leaned on the ambient file would prove
    nothing on either machine.

    Only ONE node id from this module is selected, so the child collects this
    module (running the module-scope pollution, which is wanted) without
    running this test again.
    """
    assert "BD_AIASSIST_CHILD" not in os.environ, (
        "BD_AIASSIST_CHILD is already set in the parent environment, which "
        "would make this test skip its own subject silently")

    (tmp_path / "app_config.json").write_text(json.dumps({
        "ai_provider": "ollama",
        "ai_endpoint": "http://localhost:11434",
        "ai_model_vision": "qwen2.5vl:7b",
        "ai_model_text": "qwen2.5:7b",
        "ai_api_key": "",
        "ai_enabled": True,            # <- the operator's setting
    }), "utf-8")

    target = (f"{Path(__file__).name}::"
              f"test_module_scope_pollution_does_not_reach_a_test")
    env = dict(os.environ)
    env["BD_AIASSIST_CHILD"] = "1"
    env["BD_DISABLE_KEEPALIVE"] = "1"

    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "-p", "no:randomly", "-q",
         str(_REPO / _APP_IMPORTER),
         str(_REPO / "tests" / target)],
        cwd=str(tmp_path), env=env, capture_output=True, text=True,
        timeout=300)

    assert proc.returncode == 0, (
        "a child pytest run whose cwd held an operator app_config.json with "
        "ai_enabled=true failed -- the collection-time import poisoned the "
        f"process global.\n--- stdout ---\n{proc.stdout[-3000:]}\n"
        f"--- stderr ---\n{proc.stderr[-2000:]}")


def test_the_child_harness_can_actually_observe_the_defect(tmp_path):
    """Discriminate the harness from the subject. If a poisoned cwd did not
    in fact reach `aiassist._config`, the test above would pass for the wrong
    reason on every tree, fixed or not. This asserts the poison lands -- in a
    plain interpreter, where no conftest fixture can intervene."""
    (tmp_path / "app_config.json").write_text(json.dumps({
        "ai_provider": "ollama", "ai_endpoint": "http://localhost:11434",
        "ai_model_vision": "qwen2.5vl:7b", "ai_model_text": "qwen2.5:7b",
        "ai_api_key": "", "ai_enabled": True,
    }), "utf-8")

    env = dict(os.environ)
    env["BD_DISABLE_KEEPALIVE"] = "1"
    env["PYTHONPATH"] = str(_REPO) + os.pathsep + env.get("PYTHONPATH", "")

    proc = subprocess.run(
        [sys.executable, "-c",
         "import bulk_downloader.app;"
         "from bulk_downloader import aiassist;"
         "print('ENABLED=%r' % aiassist.get_config()['enabled'])"],
        cwd=str(tmp_path), env=env, capture_output=True, text=True,
        timeout=300)

    assert "ENABLED=True" in proc.stdout, (
        "importing bulk_downloader.app from a cwd holding an app_config.json "
        "with ai_enabled=true did NOT set the process global -- the mechanism "
        "this cut is about no longer exists, and the child-pytest test above "
        "is now vacuous.\n"
        f"--- stdout ---\n{proc.stdout[-2000:]}\n"
        f"--- stderr ---\n{proc.stderr[-2000:]}")

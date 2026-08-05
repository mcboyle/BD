"""Regression tests for the custom runner's boundary with real pytest.

Every probe runs in a child interpreter so a broken runner cannot replace the
pytest module that is executing this test file.
"""
from __future__ import annotations

import ast
import os
from pathlib import Path
import subprocess
import sys
import textwrap


REPO = Path(__file__).resolve().parent.parent


def _python(source: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-c", textwrap.dedent(source)],
        cwd=REPO,
        env={**os.environ, "BD_DISABLE_KEEPALIVE": "1"},
        capture_output=True,
        text=True,
        timeout=60,
    )


def _assert_ok(result: subprocess.CompletedProcess[str]) -> None:
    assert result.returncode == 0, (
        f"child exited {result.returncode}\n"
        f"stdout:\n{result.stdout}\n"
        f"stderr:\n{result.stderr}"
    )


def test_importing_runner_modules_preserves_real_pytest_identity():
    result = _python(
        """
        import sys
        import pytest

        real_pytest = pytest
        import run_tests

        assert sys.modules["pytest"] is real_pytest
        assert pytest is real_pytest
        import run_tests_core
        """
    )
    _assert_ok(result)


def test_explicit_activation_refuses_loaded_real_pytest_without_mutation():
    result = _python(
        """
        import sys
        import pytest
        import run_tests_core

        real_pytest = pytest
        try:
            with run_tests_core.activated_pytest_stub():
                raise AssertionError("activation unexpectedly entered")
        except RuntimeError:
            pass
        else:
            raise AssertionError("activation accepted real pytest")
        assert sys.modules["pytest"] is real_pytest
        """
    )
    _assert_ok(result)


def test_explicit_activation_is_nestable_and_restores_an_absent_binding():
    result = _python(
        """
        import sys
        import run_tests_core

        prior = sys.modules.pop("pytest", None)
        try:
            with run_tests_core.activated_pytest_stub() as outer:
                assert sys.modules["pytest"] is outer
                with run_tests_core.activated_pytest_stub() as inner:
                    assert inner is outer
                    assert sys.modules["pytest"] is outer
                assert sys.modules["pytest"] is outer
            assert "pytest" not in sys.modules
        finally:
            if prior is not None:
                sys.modules["pytest"] = prior
        """
    )
    _assert_ok(result)


def test_standalone_entrypoint_scopes_stub_on_normal_return():
    result = _python(
        """
        import runpy
        import sys
        import run_tests_core

        prior = sys.modules.pop("pytest", None)
        try:
            def main():
                assert isinstance(sys.modules.get("pytest"),
                                  run_tests_core._PytestStub)
            run_tests_core.main = main
            runpy.run_path("run_tests.py", run_name="__main__")
            assert "pytest" not in sys.modules
        finally:
            if prior is not None:
                sys.modules["pytest"] = prior
        """
    )
    _assert_ok(result)


def test_standalone_entrypoint_restores_binding_on_exception():
    result = _python(
        """
        import runpy
        import sys
        import run_tests_core

        class Boom(Exception):
            pass

        prior = sys.modules.pop("pytest", None)
        try:
            def main():
                assert isinstance(sys.modules.get("pytest"),
                                  run_tests_core._PytestStub)
                raise Boom("expected")
            run_tests_core.main = main
            try:
                runpy.run_path("run_tests.py", run_name="__main__")
            except Boom:
                pass
            else:
                raise AssertionError("entrypoint swallowed exception")
            assert "pytest" not in sys.modules
        finally:
            if prior is not None:
                sys.modules["pytest"] = prior
        """
    )
    _assert_ok(result)


def test_contract_and_budget_pair_collects_with_real_pytest():
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "--collect-only",
            "-q",
            "tests/test_contracts.py",
            "tests/test_c4_timeout_budget.py",
        ],
        cwd=REPO,
        env={**os.environ, "BD_DISABLE_KEEPALIVE": "1"},
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, (
        f"collection exited {result.returncode}\n"
        f"stdout:\n{result.stdout}\n"
        f"stderr:\n{result.stderr}"
    )
    assert "error during collection" not in result.stdout.lower()


def test_runner_helper_consumers_target_import_safe_core():
    violations = []
    for path in sorted((REPO / "tests").glob("test*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                if any(alias.name == "run_tests" for alias in node.names):
                    violations.append(f"{path.name}:{node.lineno}: import")
            elif isinstance(node, ast.ImportFrom) and node.module == "run_tests":
                violations.append(f"{path.name}:{node.lineno}: from-import")
            elif (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id == "importlib"
                and node.func.attr == "import_module"
                and node.args
                and isinstance(node.args[0], ast.Constant)
                and node.args[0].value == "run_tests"
            ):
                violations.append(
                    f"{path.name}:{node.lineno}: dynamic-import"
                )
    assert violations == []

    harness = (REPO / "tests" / "test_harness_retry_timeout.py").read_text(
        encoding="utf-8"
    )
    assert '_REPO / "run_tests_core.py"' in harness
    for relative in ("project-knowledge/bd-cut", "toolchain/bin/bd-cut"):
        source = (REPO / relative).read_text(encoding="utf-8")
        assert 'os.path.join(d, "run_tests_core.py")' in source, relative


def test_dynamic_runner_helper_consumers_bind_core_under_real_pytest():
    result = _python(
        """
        import runpy
        import run_tests_core

        for path in (
            "tests/test_v3_66_660_mnt3_flake_expiry.py",
            "tests/test_v3_66_664_mnt3_quarantine.py",
        ):
            namespace = runpy.run_path(path)
            assert namespace["rt"] is run_tests_core, path
        """
    )
    _assert_ok(result)


def test_the_stub_serves_pytest_fail_with_its_message():
    """@870 -- _PytestStub had skip/skipif/approx but no `fail`.

    50 call sites across tests/ use pytest.fail, and under the minimal runner
    every one raised `AttributeError: '_PytestStub' object has no attribute
    'fail'`. The test still FAILED -- so this never hid a defect -- but the
    entire diagnostic was destroyed and replaced by a harness error. Measured
    on test_pk_mirrors_do_not_drift: pytest reported which mirrors disagreed
    and printed the `cp` remediation lines; run_tests.py reported an
    AttributeError. CLAUDE.md 2a -- a harness defect masquerading as the
    subject, and the shape that makes people debug the wrong thing.

    hasattr alone is NOT the assertion. It would pass against a no-op stub,
    which is a check that cannot see its subject; the message has to survive.
    """
    result = _python(
        """
        import sys
        sys.path.insert(0, ".")
        from run_tests_core import _PytestStub
        assert hasattr(_PytestStub, "fail"), "no fail on the stub"
        try:
            _PytestStub.fail("boom-diagnostic")
        except AssertionError as exc:
            assert "boom-diagnostic" in str(exc), (
                "fail() raised but ate its message: %r" % (str(exc),))
        else:
            raise SystemExit("fail() did not raise at all")
        print("ok")
        """
    )
    _assert_ok(result)
    assert "ok" in result.stdout, result.stdout + result.stderr

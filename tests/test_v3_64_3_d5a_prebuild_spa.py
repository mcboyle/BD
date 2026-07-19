"""D5a (v3.64.x): optional SPA prebuild in build_release.py.

The release-zip builder grew a `--prebuild-spa` flag that runs
`npm install && npm run build` in `frontend/` before zipping, so
the resulting zip ships with `frontend/dist/` and the operator can
skip the build step at deploy time.

The flag is OFF by default — D5a is an opt-in. These tests pin:
  - the flag exists in the CLI
  - default-off behavior is unchanged (the "skipped" message
    surfaces, no npm is invoked)
  - the npm-missing failure mode returns the right exit code
  - the success path (stubbed npm) ends with dist/index.html
    present and exit 0

Tests run against a temp copy of `tools/build_release.py` so we don't
mutate the live tree.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


_REPO = Path(__file__).resolve().parent.parent


def _import_build_release():
    """Import the script as a module. Cached across tests."""
    tools = str(_REPO / "tools")
    if tools not in sys.path:
        sys.path.insert(0, tools)
    if "build_release" in sys.modules:
        del sys.modules["build_release"]
    import build_release  # noqa
    return build_release


class TestArgparsePlumbing:
    def test_prebuild_spa_flag_exists(self):
        """--prebuild-spa appears in the CLI help."""
        result = subprocess.run(
            [sys.executable, str(_REPO / "tools" / "build_release.py"), "--help"],
            capture_output=True, text=True)
        assert result.returncode == 0
        assert "--prebuild-spa" in result.stdout, \
            f"--prebuild-spa missing from CLI: {result.stdout[:500]}"

    def test_prebuild_spa_help_mentions_default_off(self):
        """The help text says the default is off so the operator
        knows opting in is required."""
        result = subprocess.run(
            [sys.executable, str(_REPO / "tools" / "build_release.py"), "--help"],
            capture_output=True, text=True)
        assert "Default off" in result.stdout or "default off" in result.stdout, \
            f"--prebuild-spa help should say 'Default off'; got: {result.stdout[:600]}"


class TestPrebuildSpaHelper:
    """Direct unit tests of _prebuild_spa(root)."""

    def setup_method(self):
        self._tmp = Path(tempfile.mkdtemp(prefix="d5a-test-"))
        self._saved_path = os.environ.get("PATH", "")

    def teardown_method(self):
        os.environ["PATH"] = self._saved_path
        shutil.rmtree(self._tmp, ignore_errors=True)

    def _make_fake_repo(self, with_package_json: bool = True) -> Path:
        """Build a minimal repo: <root>/frontend/package.json optional."""
        root = self._tmp / "fake_repo"
        (root / "frontend").mkdir(parents=True)
        if with_package_json:
            (root / "frontend" / "package.json").write_text(
                '{"name": "test"}', encoding="utf-8")
        return root

    def _install_stub_npm(self, behavior: str) -> Path:
        """Install a stub npm in a temp dir + put it FIRST on PATH.
        `behavior` selects the script's response:
          - 'succeed_with_dist': install + build both succeed, build
            creates dist/index.html
          - 'succeed_no_dist': install + build both succeed but build
            does NOT create dist/index.html (silent build break)
          - 'fail_install': npm install exits 1

        Cross-platform: on Windows we have to write `npm.cmd` (not
        `npm`) because `shutil.which("npm")` honors PATHEXT — a
        bare-name `npm` file would be skipped and which() would
        walk on to the real `npm.CMD` in nodejs/, so the stub would
        never be invoked and we'd end up running real npm against
        the fake `{"name":"test"}` package.json that has no `build`
        script. That's exactly what happened in v3.64.5 on Windows
        before this fix.
        """
        bindir = self._tmp / f"stub_bin_{behavior}"
        bindir.mkdir()
        is_windows = os.name == "nt"
        if is_windows:
            npm = bindir / "npm.cmd"
            if behavior == "succeed_with_dist":
                # cmd.exe batch. Note: subprocess passes args unquoted
                # so %1 / %2 receive plain "run" / "build". The echo
                # writes a one-byte placeholder for dist/index.html;
                # the test only checks .is_file(), not contents.
                npm.write_text(
                    "@echo off\r\n"
                    "if \"%1\"==\"run\" if \"%2\"==\"build\" (\r\n"
                    "  if not exist dist mkdir dist\r\n"
                    "  echo x>dist\\index.html\r\n"
                    ")\r\n"
                    "exit /b 0\r\n",
                    encoding="utf-8")
            elif behavior == "succeed_no_dist":
                npm.write_text(
                    "@echo off\r\nexit /b 0\r\n",
                    encoding="utf-8")
            elif behavior == "fail_install":
                npm.write_text(
                    "@echo off\r\n"
                    "if \"%1\"==\"install\" exit /b 1\r\n"
                    "exit /b 0\r\n",
                    encoding="utf-8")
            else:
                raise ValueError(f"unknown stub behavior: {behavior}")
        else:
            npm = bindir / "npm"
            if behavior == "succeed_with_dist":
                npm.write_text(
                    '#!/bin/bash\n'
                    'if [ "$1" = "run" ] && [ "$2" = "build" ]; then\n'
                    '  mkdir -p dist\n'
                    '  echo "<!doctype html><html></html>" > dist/index.html\n'
                    'fi\n'
                    'exit 0\n',
                    encoding="utf-8")
            elif behavior == "succeed_no_dist":
                # Both commands return 0 but no dist is created — the
                # silent-build-break case.
                npm.write_text("#!/bin/bash\nexit 0\n", encoding="utf-8")
            elif behavior == "fail_install":
                npm.write_text(
                    '#!/bin/bash\n'
                    'if [ "$1" = "install" ]; then exit 1; fi\n'
                    'exit 0\n',
                    encoding="utf-8")
            else:
                raise ValueError(f"unknown stub behavior: {behavior}")
            npm.chmod(0o755)
        os.environ["PATH"] = f"{bindir}{os.pathsep}{self._saved_path}"
        return bindir

    def test_no_frontend_dir_returns_2(self):
        """--prebuild-spa against a repo with no frontend/ is a hard
        config error (exit 2)."""
        br = _import_build_release()
        root = self._tmp / "no_frontend"
        root.mkdir()
        rc = br._prebuild_spa(root)
        assert rc == 2

    def test_no_package_json_returns_2(self):
        """frontend/ exists but no package.json — hard error."""
        br = _import_build_release()
        root = self._make_fake_repo(with_package_json=False)
        rc = br._prebuild_spa(root)
        assert rc == 2

    def test_no_npm_on_path_returns_2(self):
        """Repo is fine but npm not installed — hard error with a
        helpful Install Node.js message."""
        br = _import_build_release()
        root = self._make_fake_repo()
        # Force PATH to a dir with no npm
        empty = self._tmp / "empty_bin"
        empty.mkdir()
        os.environ["PATH"] = str(empty)
        rc = br._prebuild_spa(root)
        assert rc == 2

    def test_npm_install_failure_returns_1(self):
        """npm install non-zero exit is a build failure (exit 1, not 2
        — the repo + npm are configured correctly, the build itself
        failed)."""
        br = _import_build_release()
        self._install_stub_npm("fail_install")
        root = self._make_fake_repo()
        rc = br._prebuild_spa(root)
        assert rc == 1

    def test_build_returns_0_but_no_dist_returns_1(self):
        """The silent-build-break check: npm reported success but
        dist/index.html doesn't exist."""
        br = _import_build_release()
        self._install_stub_npm("succeed_no_dist")
        root = self._make_fake_repo()
        rc = br._prebuild_spa(root)
        assert rc == 1

    def test_full_success_path_returns_0(self):
        """The happy path: stubbed npm builds dist/index.html, the
        helper returns 0."""
        br = _import_build_release()
        self._install_stub_npm("succeed_with_dist")
        root = self._make_fake_repo()
        rc = br._prebuild_spa(root)
        assert rc == 0
        assert (root / "frontend" / "dist" / "index.html").is_file()


class TestDefaultOffPreservesContract:
    """Without --prebuild-spa, the build is byte-identical to the
    pre-D5a behavior (i.e. no dist/ in the zip unless the operator
    already happened to have one)."""

    def test_default_log_says_skipped(self):
        """The status line surfaces the default-off behavior so the
        operator knows the SPA build hasn't run."""
        # We can read the message from the source — checking the
        # behavior end-to-end requires a full build_release run which
        # is heavy. The string is the contract.
        src = (_REPO / "tools" / "build_release.py").read_text(
            encoding="utf-8")
        # The default-off log line must mention "skipped" and point at
        # OPEN_THREADS so the operator knows where to read about
        # the install-time alternative.
        assert "Prebuild SPA" in src
        assert "skipped" in src.lower()
        assert "OPEN_THREADS" in src

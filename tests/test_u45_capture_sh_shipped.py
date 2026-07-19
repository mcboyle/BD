"""U45 -- capture.sh shipped-in-repo contract.

v3.63.6 moves `capture.sh` into the release zip. Pre-v3.63.6 it lived
only on stash, which meant KB-documented fixes to it (e.g. "step [3]
uses module-level app" in v3.63.5) couldn't actually be delivered by
deploying a release zip -- the file the operator unpacked never
contained those changes. The v3.63.5 stash capture demonstrated this:
the suite was green, but step [3] still ImportErrored on `create_app`
and step [6] still ran 28 live tests because L3 was missing from
LIVE_IDS.

This file pins five properties so future regressions cannot return
silently:

  1. `capture.sh` exists at the repo root and is non-empty.
  2. It parses cleanly under `bash -n` (syntax-only check; no
     execution).
  3. Step [3] imports the module-level `app` and does NOT call a
     nonexistent `create_app` factory. The codebase has no
     `create_app` -- it builds the Flask app at module scope
     (`app = Flask(...)` at `bulk_downloader/app.py:122`), so any
     `create_app` reference is a regression.
  4. Step [6]'s `LIVE_IDS` comma-string contains `L3`
     (mv3-extension-service-worker). The v3.63.5 extension fix
     (channel="chromium" + the three placeholder PNGs) makes L3
     PASS cleanly; pre-v3.63.5 it was correctly excluded as a known
     wedge. Dropping it from LIVE_IDS again would silently undercount
     live tests.
  5. Step [6]'s per-check timeout is >= 90s. The 60s default was too
     tight at v3.63.2 (L34 wedged within it); the 90s budget gives
     L34 a fair shot before T55's harness wall fires. A future
     "tighten the budget" cleanup would re-introduce the wedge.

Functional verification (running capture.sh against a real BD
install) is left to the operator's capture/diagnostic flow -- this
suite cannot run capture.sh because the suite is what capture.sh
runs.
"""
from __future__ import annotations

import os
import shutil
import subprocess


REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
CAPTURE_SH = os.path.join(REPO_ROOT, "capture.sh")


def _read_capture_sh() -> str:
    with open(CAPTURE_SH, "r", encoding="utf-8") as f:
        return f.read()


class TestCaptureShShipped:
    """capture.sh ships in the repo and is well-formed."""

    def test_capture_sh_exists_at_repo_root(self):
        assert os.path.isfile(CAPTURE_SH), (
            f"capture.sh missing from repo root ({CAPTURE_SH}). "
            "Pre-v3.63.6 it lived only on stash; v3.63.6+ ships it in "
            "the release zip so deploys carry KB-documented fixes."
        )

    def test_capture_sh_is_non_empty(self):
        size = os.path.getsize(CAPTURE_SH)
        assert size > 1000, (
            f"capture.sh is suspiciously small ({size} bytes). "
            "Expected ~7-8 KB for the full 9-step diagnostic."
        )

    def test_capture_sh_parses_under_bash_n(self):
        """`bash -n capture.sh` must not error.

        Syntax-only check; does not execute. Catches the case where
        a future edit produces an unbalanced quote, missing fi, etc.
        """
        bash = shutil.which("bash")
        if bash is None:
            # Sandbox-without-bash escape hatch: matches the pattern
            # used by other environment-coupled tests (see tests/_env.py).
            # On any real Linux deployment bash is present.
            return
        result = subprocess.run(
            [bash, "-n", CAPTURE_SH],
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert result.returncode == 0, (
            f"bash -n capture.sh failed:\nstdout: {result.stdout}\n"
            f"stderr: {result.stderr}"
        )


class TestCaptureShStep3CsrfDiag:
    """Step [3] CSRF diagnostic imports the module-level app."""

    def test_step_3_imports_module_level_app(self):
        body = _read_capture_sh()
        assert "from bulk_downloader.app import app" in body, (
            "Step [3] CSRF diag must import the module-level `app` -- "
            "the canonical shape. Pre-v3.63.6 it imported a nonexistent "
            "`create_app` factory and ImportErrored on every capture."
        )

    def test_step_3_does_not_execute_create_app(self):
        """No executable `create_app` reference in capture.sh.

        `create_app` is not a real symbol in this codebase --
        `bulk_downloader/app.py` builds the Flask app at module scope.
        Any executable reference (import, call) is a regression.
        Comment lines mentioning `create_app` are explicitly allowed
        and expected -- the file's history block documents why
        `create_app` was removed; that documentation has value.

        Convention: a comment line is one whose first non-whitespace
        character is `#`. This matches both bash-level and python
        comments (the python -c heredoc body uses `#` too).
        """
        body = _read_capture_sh()
        offending = [
            (i + 1, line)
            for i, line in enumerate(body.splitlines())
            if "create_app" in line and not line.lstrip().startswith("#")
        ]
        assert not offending, (
            "capture.sh has executable references to `create_app` -- "
            "there is no such symbol in this codebase. Use `from "
            "bulk_downloader.app import app` instead.\n"
            "Offending non-comment lines:\n" + "\n".join(
                f"  {ln}: {line}" for ln, line in offending
            )
        )


class TestCaptureShStep6LiveTests:
    """Step [6] LIVE_IDS includes L3, per-check timeout >= 90."""

    def test_live_ids_contains_l3(self):
        """L3 mv3-extension-service-worker is in LIVE_IDS.

        v3.63.5 made L3 pass cleanly (channel="chromium" plus the
        three placeholder icon PNGs in extension/). Pre-v3.63.5 L3
        was correctly excluded as a known wedge; from v3.63.6
        onward it must be present, or post-deploy live tests will
        silently undercount.
        """
        body = _read_capture_sh()
        live_ids_lines = [
            line for line in body.splitlines()
            if line.lstrip().startswith("LIVE_IDS=")
        ]
        assert live_ids_lines, "No LIVE_IDS= assignment found in capture.sh"
        # capture.sh defines LIVE_IDS exactly once.
        assert len(live_ids_lines) == 1, (
            f"Expected exactly one LIVE_IDS= line, got {len(live_ids_lines)}: "
            f"{live_ids_lines}"
        )
        line = live_ids_lines[0]
        # Word-boundary match: "L3" must appear as its own token in the
        # comma-string, not as a substring of "L30" / "L31" / etc.
        tokens = {
            tok.strip().strip('"').strip("'")
            for tok in line.split("=", 1)[1].strip().strip('"').split(",")
        }
        # The set may include the leading quote artefact -- strip and check.
        tokens = {t.strip('"').strip("'") for t in tokens}
        assert "L3" in tokens, (
            f"L3 missing from LIVE_IDS. v3.63.5 fixed the MV3-SW wedge "
            f"that previously justified its exclusion; from v3.63.6 it "
            f"must be in the runlist.\n"
            f"Current LIVE_IDS line: {line}\n"
            f"Parsed tokens: {sorted(tokens)}"
        )

    def test_live_tests_per_check_timeout_at_least_90(self):
        """--per-check-timeout >= 90s.

        T55 introduced the per-check harness wall at v3.63.2 with a
        60s default. L34 was wedging within 60s before the v3.63.4
        streaming-skip fix; the 90s budget in capture.sh gave it
        enough room. Tightening below 90s would re-introduce the
        fail-on-budget pattern this comment block was added to prevent.
        """
        body = _read_capture_sh()
        # Find the per-check-timeout flag's value. Shape:
        #   --per-check-timeout 90
        # or:
        #   --per-check-timeout=90
        import re
        matches = re.findall(
            r"--per-check-timeout[ =](\d+)",
            body,
        )
        assert matches, (
            "No --per-check-timeout flag found in capture.sh. "
            "Step [6] live tests must pass --per-check-timeout >= 90 "
            "to give L34 / future slow checks enough budget."
        )
        for m in matches:
            val = int(m)
            assert val >= 90, (
                f"--per-check-timeout {val} is below the 90s minimum. "
                f"T55 contains the wedge at this wall; tightening below "
                f"90s re-introduces the fail-on-budget pattern."
            )

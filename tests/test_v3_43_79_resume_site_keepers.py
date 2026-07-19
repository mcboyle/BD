"""v3.43.79: regression tests for the SAST hotfix.

  BUG-A — `resume_site_keepers` is intentionally absent from
          session_keeper.py; the keeper auto-reconnects via heartbeat.
          v3.43.79 removed two captcha-solve sites in runner.py that
          were calling the non-existent function (silently
          AttributeError'd via try/except: pass).

  BUG-B — runner.py:8716 parallel-chunk download worker pre-binds
          f=None before open() so the except branch can close the
          handle if open() succeeded but seek() raised.

Sentinel pattern: [SAST 3:13pm 13 may]
"""
from __future__ import annotations

import os
import re


_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_PKG = os.path.join(_REPO_ROOT, "bulk_downloader")


def _bd_runner_src():
    """v3.66.404: runner.py decomposed into runner_*.py mixins; aggregate the
    package so moved SiteRunner method bodies stay visible to source checks."""
    from pathlib import Path as _P
    from bulk_downloader import runner as _R
    _pd = _P(_R.__file__).parent
    return "\n".join(q.read_text(encoding="utf-8")
                     for q in [_pd / "runner.py"] + sorted(_pd.glob("runner_*.py")))


# ─── BUG-A: resume_site_keepers must NOT exist or be called ─────────


class TestBugAResumeSiteKeepersAbsent:
    """The function is intentionally absent. INV-001's anti_pattern
    clause states the keeper auto-reconnects on heartbeat; no resume
    call exists or should be invented."""

    def test_resume_site_keepers_not_exported(self):
        """session_keeper.py must not define resume_site_keepers."""
        from bulk_downloader import session_keeper
        assert not hasattr(session_keeper, "resume_site_keepers"), (
            "resume_site_keepers MUST NOT exist on session_keeper. "
            "The keeper auto-reconnects on next heartbeat after "
            "pause_site_keepers teardown. See INV-001 anti_pattern."
        )

    def test_pause_site_keepers_callable_returns_int(self):
        """Sanity: the function that DOES exist still works."""
        from bulk_downloader import session_keeper
        assert callable(session_keeper.pause_site_keepers)
        # Calling with no keepers registered returns 0
        result = session_keeper.pause_site_keepers("nonexistent-site-id")
        assert isinstance(result, int)
        assert result == 0

    def test_runner_does_not_call_resume_site_keepers(self):
        """Grep the runner source for the dead call.

        The phrase resume_site_keepers may appear in EXPLANATORY
        comments (e.g. runner.py:4045-4051 documenting why no call
        is needed), but it must NEVER appear as a callable
        reference like `_sk.resume_site_keepers(...)` or
        `session_keeper.resume_site_keepers(...)`."""
        with open(os.path.join(_PKG, "runner.py"), encoding="utf-8") as f:
            src = f.read()
        # Forbidden: any line that calls the function (not commented-out)
        # We look for the call syntax — `(` after the name — on a line
        # whose first non-whitespace char is not `#`.
        offenders = []
        for lineno, line in enumerate(src.splitlines(), 1):
            stripped = line.lstrip()
            if stripped.startswith("#"):
                continue  # comment line — allowed for documentation
            if re.search(r"\bresume_site_keepers\s*\(", line):
                offenders.append((lineno, line.strip()))
        assert offenders == [], (
            f"runner.py contains live calls to resume_site_keepers — "
            f"BUG-A would regress: {offenders}"
        )


# ─── BUG-B: parallel-chunk worker closes file on seek-failure ──────


class TestBugBChunkWorkerHandlesSeekFailure:
    """Verify the pre-bind-and-close pattern is present in the
    chunked download worker. If open() succeeds but seek() raises,
    f must be closed before the except returns."""

    def test_pre_binds_f_to_none_before_chunk_open(self):
        """The fix pattern: f = None before the try: open()."""
        src = _bd_runner_src()
        # Locate the parallel-chunk worker's open(tmp_path, "r+b")
        m = re.search(
            r'(?P<pre>(?:[^\n]*\n){0,8})'
            r'\s+f\s*=\s*open\(str\(tmp_path\),\s*"r\+b"\)',
            src,
        )
        assert m is not None, \
            "couldn't locate chunk worker's open(tmp_path, 'r+b')"
        # The 8 lines immediately above must contain `f = None`
        pre = m.group("pre")
        assert re.search(r"^\s*f\s*=\s*None\b", pre, re.MULTILINE), (
            "BUG-B regression: chunk worker no longer pre-binds f=None "
            "before opening — seek-failure path will leak the handle. "
            "See runner.py:8716 [SAST 3:13pm 13 may]"
        )

    def test_except_branch_closes_handle_when_bound(self):
        """The except after open()/seek() must close f if non-None."""
        src = _bd_runner_src()
        # Find the worker error line, then look at the preceding ~6 lines
        # for the close-if-bound pattern.
        m = re.search(
            r'(?P<pre>(?:[^\n]*\n){0,8})'
            r'\s+worker_errors\[idx\]\s*=\s*f"open failed:',
            src,
        )
        assert m is not None, "couldn't locate worker_errors[idx] open-failed line"
        pre = m.group("pre")
        # The except branch should contain: if f is not None: ... f.close()
        assert "f is not None" in pre and "f.close()" in pre, (
            "BUG-B regression: chunk worker's open-failed except branch "
            "no longer closes the handle on seek-failure path. "
            "Expected the [SAST 3:13pm 13 may] cleanup block."
        )

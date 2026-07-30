"""v3.43.23 regression tests — quick-wins UX bundle.

Covers five features shipped in v3.43.23:

  1. Stuck-URL amber highlighting — runner stamps `last_progress_at`
     on every meaningful state change; frontend adds `s-stuck` class
     when status='running' AND no progress in 60min
  2. Form draft auto-save — modal form values persisted to
     localStorage on every change, restored on open
  3. Context menu Retry action (+ context-aware Copy filename / error)
  4. Sticky queue table headers (CSS-only)
  5. /retry_one endpoint for per-URL retry
"""
from __future__ import annotations

# [SAST 3:13pm 13 may] removed unused: import collections
import time
from pathlib import Path


_REPO_ROOT = Path(__file__).resolve().parent.parent
_APP_JS = _REPO_ROOT / "bulk_downloader" / "static" / "app.js"
_APP_CSS = _REPO_ROOT / "bulk_downloader" / "static" / "app.css"
_APP_PY = _REPO_ROOT / "bulk_downloader" / "app.py"
class _AggregateSrc:
    """runner.py + every runner_*.py mixin module (PHASE 3 decomposition v3.66.397+
    moved SiteRunner methods into siblings); glob keeps source-coupled guards green
    across all current and future runner cuts."""
    def __init__(self, pkg_dir):
        self._paths = [pkg_dir / "runner.py"] + sorted(pkg_dir.glob("runner_*.py"))
    def read_text(self, encoding="utf-8"):
        return "\n".join(p.read_text(encoding=encoding) for p in self._paths)
_RUNNER_PY = _AggregateSrc(_REPO_ROOT / "bulk_downloader")


# ── 1. Stuck-URL tracking (runner side) ───────────────────────────────


def _APP_SRC():
    """app.py + extracted app_*.py blueprint modules (Phase 4 thin-core-shell)."""
    import bulk_downloader as _bd, pathlib as _pl
    _pkg = _pl.Path(_bd.__file__).parent
    _parts = [(_pkg / 'app.py').read_text(encoding='utf-8')]
    _parts += [p.read_text(encoding='utf-8') for p in sorted(_pkg.glob('app_*.py'))]
    return '\n'.join(_parts)


def _func_src(src: str, name: str) -> str:
    """Source of the function `name`, WHOLE -- not a fixed-width window.

    These guards used to read `src[src.find("def foo"): +N]` for a hand-picked
    N (3000 / 12000 / 2000). That denominator is a byte count, so it silently
    stops containing its subject the moment the function grows past N, and the
    guard then fails on a tree where the asserted line is still present and
    correct. Measured when cut #31 added ten lines to _update_job_current: the
    asserted condition moved to offset 3574 and the 3000-char window reported
    it missing -- a gate crying wolf, which CLAUDE.md section 0 calls a
    soundness bug rather than a safe default.

    Deriving the extent from the AST makes the denominator contain the subject
    by construction, at any function size.

    Accepts either one source string or an iterable of them. The callers pass a
    CONCATENATION of whole modules; that parses as a single unit today
    (measured, both the runner and app aggregates), and passing parts is
    supported so a caller can stay correct if it ever stops parsing.
    """
    import ast as _ast
    chunks = [src] if isinstance(src, str) else list(src)
    parsed_any = False
    for chunk in chunks:
        try:
            tree = _ast.parse(chunk)
        except SyntaxError:
            # A concatenation of whole modules parses today (measured), but it
            # would stop parsing if any concatenated module grew a
            # `from __future__` import. Skip the unparseable chunk rather than
            # die, and let the not-found path below report an honest failure.
            continue
        parsed_any = True
        for node in _ast.walk(tree):
            if isinstance(node, (_ast.FunctionDef, _ast.AsyncFunctionDef)) \
                    and node.name == name:
                seg = _ast.get_source_segment(chunk, node)
                if seg:
                    return seg
    raise AssertionError(
        f"{name} not found as a function definition in the scanned source "
        f"(parsed_any={parsed_any}) -- the guard cannot see its subject, "
        "which is a failure, not a pass")


def test_update_job_stamps_last_progress_at_on_status_change():
    """When _update_job changes the status, it must stamp
    last_progress_at. Without this, the frontend can't tell stuck
    from healthy-running."""
    src = _RUNNER_PY.read_text(encoding="utf-8")
    # Generation validation is a small wrapper; the mutation lives in the
    # current-generation implementation.
    upd_body = _func_src(src, "_update_job_current")
    assert "last_progress_at" in upd_body, (
        "_update_job must stamp last_progress_at"
    )
    # Specifically: stamped on status change OR byte advance
    assert "prev_status != status or new_bytes > prev_bytes" in upd_body, (
        "stamp condition must be (status changed) OR (bytes advanced) — "
        "not every message refresh"
    )


def test_load_urls_initializes_last_progress_at():
    """Newly-added URLs must have last_progress_at set or they'd
    immediately read as stuck (Unix epoch 0 = 1970 = obviously old)."""
    src = _RUNNER_PY.read_text(encoding="utf-8")
    lu_body = _func_src(src, "load_urls")
    assert '"last_progress_at": time.time()' in lu_body, (
        "load_urls must stamp last_progress_at at creation"
    )


# ── 2. Frontend stuck-URL rendering ───────────────────────────────────


# ── 3. Form draft auto-save ───────────────────────────────────────────

# ── 4. Context menu Retry + copy enhancements ─────────────────────────

# ── 5. /retry_one endpoint ─────────────────────────────────────────────

def test_retry_one_endpoint_registered():
    """The route + handler must exist."""
    src = _APP_SRC()
    assert '/api/sites/<sid>/retry_one' in src
    assert "def api_retry_one" in src


def test_retry_one_validates_state():
    """retry_one must reject retries from non-recoverable states:
    can't retry a 'done' (would lose data), 'pending' (no-op),
    or 'running' (worker is still on it)."""
    src = _APP_SRC()
    rt_body = _func_src(src, "api_retry_one")
    # Whitelist exactly the recoverable states
    assert '"failed", "needs_review", "stopped"' in rt_body


# ── 6. Sticky table headers ───────────────────────────────────────────

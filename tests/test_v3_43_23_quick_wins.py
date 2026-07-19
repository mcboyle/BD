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


def test_update_job_stamps_last_progress_at_on_status_change():
    """When _update_job changes the status, it must stamp
    last_progress_at. Without this, the frontend can't tell stuck
    from healthy-running."""
    src = _RUNNER_PY.read_text(encoding="utf-8")
    # The pattern must be in _update_job
    upd_start = src.find("def _update_job(self,url,status,message,**extra)")
    assert upd_start > 0
    upd_body = src[upd_start:upd_start + 3000]
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
    # Find the job-creation block in load_urls. The stamp is ~110 lines
    # past the def line because the function has a lot of pre-job parsing.
    lu_start = src.find("def load_urls(self,urls,dedupe=True")
    assert lu_start > 0
    lu_body = src[lu_start:lu_start + 12000]
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
    rt_start = src.find("def api_retry_one")
    assert rt_start > 0
    rt_body = src[rt_start:rt_start + 2000]
    # Whitelist exactly the recoverable states
    assert '"failed", "needs_review", "stopped"' in rt_body


# ── 6. Sticky table headers ───────────────────────────────────────────


"""BP-VH3 (v3.66.282): force re-download. The SPA sends force_download; the
test_extract handler stamps it on the job; _dedup_preflight bypasses history
dedup when set, so a previously-`done` URL is re-tested instead of skipped.
"""
from pathlib import Path

from bulk_downloader.runner import SiteRunner

REPO = Path(__file__).resolve().parent.parent
APP = "\n".join([(REPO / "bulk_downloader" / "app.py").read_text()]
               + [p.read_text() for p in sorted((REPO / "bulk_downloader").glob("app_*.py"))])
SRC = (REPO / "frontend" / "src" / "routes" / "CaptureWorkflow.tsx").read_text()


def test_backend_reads_and_stamps_force_download():
    assert 'body.get("force_download"' in APP
    assert 'jobs[url]["force_download"] = True' in APP


def test_spa_sends_force_download_full_literal():
    # full literal field (not concatenated) so the wiring is unambiguous
    assert "force_download: force" in SRC


def test_dedup_preflight_bypasses_on_force():
    # the bypass that makes Force meaningful: a force_download job is never
    # skipped as a duplicate, regardless of history.
    r = SiteRunner.__new__(SiteRunner)            # no __init__ — bypass is first
    assert r._dedup_preflight("http://x/clip", {"force_download": True}) is None

"""Phase-0 finish (sub-item A): healthcheck._check_disk -- which powers
/api/health/checklist -- checked only per-site download_dirs, the same
download-dir-only blind spot fixed for selftest.run_all in Cut 0.5. It must also
check the capture-store / install root (a full store is a distinct 'disk full'
class), and it must do so even when no sites are configured yet (first boot)."""
from bulk_downloader import healthcheck


def test_check_disk_covers_capture_store_with_no_sites():
    r = healthcheck._check_disk({})
    # was 'no sites configured' (SEV_OK, nothing checked); now the capture store
    # is always checked, so the healthy message names it.
    assert "capture" in r["message"].lower(), \
        f"capture store should be disk-checked even with no sites: {r}"

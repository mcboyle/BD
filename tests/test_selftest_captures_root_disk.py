"""Cut 0.5 (disk gauge gap): the health battery (selftest.run_all) must also
check free space on the CAPTURE-STORE root (PROJECT_ROOT / install dir), not
only the download dirs. The store filling up (7.1G/687 wacz this session) is a
distinct 'disk full' class the download-dir check misses when captures live on a
different path/volume than downloads."""
import tempfile
from bulk_downloader import selftest


def test_run_all_disk_checks_captures_root():
    croot = tempfile.mkdtemp(prefix="caproot_")
    r = selftest.run_all(captures_root=croot, download_dirs=[])
    disk = [c for c in r["checks"] if c.get("test") == "disk_space"]
    paths = [c.get("detail", {}).get("path") for c in disk]
    assert croot in paths, f"captures_root not disk-checked: {paths}"


def test_run_all_dedups_captures_root_against_download_dirs():
    shared = tempfile.mkdtemp(prefix="shared_")
    r = selftest.run_all(captures_root=shared, download_dirs=[shared])
    disk = [c for c in r["checks"]
            if c.get("test") == "disk_space" and c.get("detail", {}).get("path") == shared]
    assert len(disk) == 1, f"expected 1 dedup'd disk check, got {len(disk)}"

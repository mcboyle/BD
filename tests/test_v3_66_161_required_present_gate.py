"""v3.66.161 — preventive gate for the 158->158_2 data-restoration regression.

The validation corpus, root .bat files, and deep_detect/selector_chains/
recon_corpus fixtures were dropped from the source tree at the 158_2
repackaging and rode forward into 160 because zip_manifest_check() only did a
symmetric tree-vs-zip diff (blind when the file is gone from BOTH sides).
These tests pin the REQUIRED-PRESENT allowlist that is checked against the zip
namelist directly, so a future silent drop fails the build.
"""
import io
import os
import tempfile
import zipfile

from bulk_downloader import dev_suite as ds


_EXPECTED_REQUIRED = {
    "validation_corpus.jsonl",
    "gpu_check.bat",
    "install_ai_ollama.bat",
    "install_dev.bat",
    "install_windows.bat",
    "run_all_tests.bat",
    "run_test.bat",
    "start_fixture_site.bat",
    "start_fixture_site2.bat",
    "uninstall_windows.bat",
    "tests/fixtures/deep_detect/",
    "tests/fixtures/selector_chains/",
    "tests/fixtures/recon_corpus/",
}


def _write_zip(names):
    """Write a throwaway zip containing the given member names (each gets a
    trivial body) and return its path. Caller is in a temp dir."""
    d = tempfile.mkdtemp()
    zp = os.path.join(d, "candidate.zip")
    with zipfile.ZipFile(zp, "w") as zf:
        for n in names:
            zf.writestr(n, b"x")
    return zp


def test_required_set_is_the_expected_artifacts():
    assert set(ds._MANIFEST_REQUIRED_PRESENT) == _EXPECTED_REQUIRED


def test_nine_root_bats_required_not_eleven():
    bats = [r for r in ds._MANIFEST_REQUIRED_PRESENT if r.endswith(".bat")]
    assert len(bats) == 9
    assert "install_windows.bat" in bats


def test_helper_reports_nothing_when_all_present():
    present = []
    for req in ds._MANIFEST_REQUIRED_PRESENT:
        present.append(req + "f.json" if req.endswith("/") else req)
    assert ds._manifest_required_missing(present) == []


def test_helper_flags_dropped_corpus():
    present = [r + "f.json" if r.endswith("/") else r
               for r in ds._MANIFEST_REQUIRED_PRESENT
               if r != "validation_corpus.jsonl"]
    assert ds._manifest_required_missing(present) == ["validation_corpus.jsonl"]


def test_helper_flags_dropped_fixture_dir():
    present = [r + "f.json" if r.endswith("/") else r
               for r in ds._MANIFEST_REQUIRED_PRESENT
               if r != "tests/fixtures/deep_detect/"]
    assert ds._manifest_required_missing(present) == ["tests/fixtures/deep_detect/"]


def test_zip_check_passes_required_when_all_present():
    names = [r + "f.json" if r.endswith("/") else r
             for r in ds._MANIFEST_REQUIRED_PRESENT]
    zp = _write_zip(names)
    res = ds.zip_manifest_check(zp)
    # tree-vs-zip diff is noisy for a synthetic zip; we assert only on the
    # tree-independent required-presence field.
    assert res["required_missing"] == []


def test_zip_check_fails_when_required_silently_dropped():
    names = [r + "f.json" if r.endswith("/") else r
             for r in ds._MANIFEST_REQUIRED_PRESENT
             if r not in ("validation_corpus.jsonl", "install_windows.bat")]
    zp = _write_zip(names)
    res = ds.zip_manifest_check(zp)
    assert res["ok"] is False
    assert "install_windows.bat" in res["required_missing"]
    assert "validation_corpus.jsonl" in res["required_missing"]
    assert "REQUIRED artifact" in res["verdict"]

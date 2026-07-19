"""T42 (Tier 4) — D-75 golden-file manager.

Read-only inventory of tests/fixtures/golden/*.golden, with diff
against optional .current siblings. Synthetic tree per test via
the repo_root override.
"""
import json

from bulk_downloader import dev_suite as ds


def _make_goldens_tree(root, *, files=None):
    """files: dict of {stem: (golden_bytes, current_bytes_or_none,
    meta_dict_or_none)}."""
    gdir = root / "tests" / "fixtures" / "golden"
    gdir.mkdir(parents=True, exist_ok=True)
    for stem, payload in (files or {}).items():
        golden, current, meta = payload
        (gdir / f"{stem}.golden").write_bytes(golden)
        if current is not None:
            (gdir / f"{stem}.current").write_bytes(current)
        if meta is not None:
            (gdir / f"{stem}.golden.meta").write_text(
                json.dumps(meta), encoding="utf-8")


def test_golden_files_missing_dir(clean_workdir, tmp_path):
    # No goldens dir created → graceful empty report
    r = ds.golden_file_manager(repo_root=str(tmp_path))
    assert r["ok"] is True
    assert r["total"] == 0
    assert "no goldens dir" in r["verdict"]


def test_golden_files_empty_dir(clean_workdir, tmp_path):
    (tmp_path / "tests" / "fixtures" / "golden").mkdir(parents=True)
    r = ds.golden_file_manager(repo_root=str(tmp_path))
    assert r["ok"] is True
    assert r["total"] == 0


def test_golden_files_lists_committed_files(clean_workdir, tmp_path):
    _make_goldens_tree(tmp_path, files={
        "alpha": (b"alpha-baseline", None, None),
        "beta": (b"beta-baseline", None, None),
    })
    r = ds.golden_file_manager(repo_root=str(tmp_path))
    assert r["total"] == 2
    names = sorted(e["name"] for e in r["items"])
    assert names == ["alpha", "beta"]


def test_golden_files_no_drift_when_current_matches(
        clean_workdir, tmp_path):
    _make_goldens_tree(tmp_path, files={
        "match": (b"same-bytes", b"same-bytes", None),
    })
    r = ds.golden_file_manager(repo_root=str(tmp_path))
    item = r["items"][0]
    assert item["has_current"] is True
    assert item["drift"] is False
    assert r["drift_count"] == 0


def test_golden_files_drift_detected(clean_workdir, tmp_path):
    _make_goldens_tree(tmp_path, files={
        "drifted": (b"original-baseline", b"new-version", None),
    })
    r = ds.golden_file_manager(repo_root=str(tmp_path))
    item = r["items"][0]
    assert item["drift"] is True
    assert item["golden_sha256"] != item["current_sha256"]
    assert r["drift_count"] == 1


def test_golden_files_without_current_not_drift(clean_workdir, tmp_path):
    _make_goldens_tree(tmp_path, files={
        "no-current": (b"x", None, None),
    })
    r = ds.golden_file_manager(repo_root=str(tmp_path))
    item = r["items"][0]
    assert item["has_current"] is False
    assert item["drift"] is False


def test_golden_files_reports_byte_size(clean_workdir, tmp_path):
    _make_goldens_tree(tmp_path, files={
        "sized": (b"x" * 1234, None, None),
    })
    r = ds.golden_file_manager(repo_root=str(tmp_path))
    assert r["items"][0]["golden_bytes"] == 1234


def test_golden_files_meta_loaded(clean_workdir, tmp_path):
    _make_goldens_tree(tmp_path, files={
        "with-meta": (b"x", None,
                      {"purpose": "extraction sample",
                       "owner": "mboyle"}),
    })
    r = ds.golden_file_manager(repo_root=str(tmp_path))
    item = r["items"][0]
    assert item["meta"]["purpose"] == "extraction sample"


def test_golden_files_malformed_meta_caught(clean_workdir, tmp_path):
    gdir = tmp_path / "tests" / "fixtures" / "golden"
    gdir.mkdir(parents=True)
    (gdir / "broken.golden").write_bytes(b"x")
    (gdir / "broken.golden.meta").write_text(
        "{ not valid", encoding="utf-8")
    r = ds.golden_file_manager(repo_root=str(tmp_path))
    item = r["items"][0]
    assert "error" in item.get("meta", {})


def test_golden_files_verdict_reports_drift_count(
        clean_workdir, tmp_path):
    _make_goldens_tree(tmp_path, files={
        "a": (b"x", b"x", None),
        "b": (b"x", b"y", None),
        "c": (b"x", b"z", None),
    })
    r = ds.golden_file_manager(repo_root=str(tmp_path))
    assert r["drift_count"] == 2
    assert "2 drift" in r["verdict"]


def test_golden_files_is_json_serializable(clean_workdir, tmp_path):
    _make_goldens_tree(tmp_path, files={
        "x": (b"y", b"z", {"k": "v"}),
    })
    r = ds.golden_file_manager(repo_root=str(tmp_path))
    json.dumps(r)

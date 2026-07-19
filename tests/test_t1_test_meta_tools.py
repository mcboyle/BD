"""T1 (Tier 3) — D-73 parametrize fan-out counter + D-70 flaky-test
detector. Two read-only test-introspection tools extending the U26
test-meta cluster.
"""
import json
import os
import pathlib

from bulk_downloader import dev_suite as ds


# the repo's real tests/ dir, resolved absolutely — a test must not
# depend on cwd (the custom runner / a prior clean_workdir test can
# leave cwd elsewhere; lesson A7)
_REPO_TESTS = str(pathlib.Path(__file__).resolve().parent)


# ── D-73 parametrize_fanout ────────────────────────────────────────

def test_parametrize_fanout_scans_the_real_tests_dir():
    r = ds.parametrize_fanout(_REPO_TESTS)
    assert r["ok"] is True
    assert r["tool"] == "parametrize_fanout"
    # the suite genuinely uses @parametrize in a couple of files
    assert r["files_using_parametrize"] >= 1
    assert r["total_parametrize_decorators"] >= 1


def test_parametrize_fanout_counts_extra_cases(tmp_path):
    # a class method with 4 parametrize cases -> 3 extra cases
    f = tmp_path / "test_sample.py"
    f.write_text(
        "import pytest\n"
        "class TestX:\n"
        "    @pytest.mark.parametrize('v', [1, 2, 3, 4])\n"
        "    def test_thing(self, v):\n"
        "        assert v\n",
        encoding="utf-8")
    r = ds.parametrize_fanout(str(tmp_path))
    assert r["total_parametrize_decorators"] == 1
    assert r["total_extra_cases_from_fanout"] == 3
    assert r["module_level_misuse"] == []


def test_parametrize_fanout_flags_module_level_misuse(tmp_path):
    # a module-level parametrized function — the custom runner cannot
    # expand it, so it is a defect the tool must flag
    f = tmp_path / "test_bad.py"
    f.write_text(
        "import pytest\n"
        "@pytest.mark.parametrize('v', [1, 2])\n"
        "def test_loose(v):\n"
        "    assert v\n",
        encoding="utf-8")
    r = ds.parametrize_fanout(str(tmp_path))
    assert r["module_level_misuse"] == ["test_bad.py::test_loose"]


def test_parametrize_fanout_ignores_parametrize_in_comments(tmp_path):
    # a file that only MENTIONS parametrize in a docstring/comment
    # must not be counted
    f = tmp_path / "test_mentions.py"
    f.write_text(
        '"""We do not use @parametrize here, we loop inline."""\n'
        "def test_x():\n"
        "    for v in (1, 2, 3):\n"
        "        assert v\n",
        encoding="utf-8")
    r = ds.parametrize_fanout(str(tmp_path))
    assert r["total_parametrize_decorators"] == 0


def test_parametrize_fanout_missing_dir_is_error():
    r = ds.parametrize_fanout("/tmp/t1_no_such_tests_dir")
    assert r["ok"] is False
    assert "error" in r


# ── D-70 flaky_test_detector ───────────────────────────────────────

def _write_result(path, total, failed_tests):
    data = {"total": total,
            "failures": [{"test": t} for t in failed_tests]}
    path.write_text(json.dumps(data), encoding="utf-8")


def test_flaky_detector_finds_a_flaky_test(tmp_path):
    # test_flaky fails in 2 of 3 runs -> flaky; test_a fails in all 3
    # -> consistently failing, not flaky
    _write_result(tmp_path / "r0.json", 100, ["test_a", "test_flaky"])
    _write_result(tmp_path / "r1.json", 100, ["test_a"])
    _write_result(tmp_path / "r2.json", 100, ["test_a", "test_flaky"])
    r = ds.flaky_test_detector(str(tmp_path))
    assert r["ok"] is True
    assert r["runs_compared"] == 3
    flaky_names = [f["test"] for f in r["flaky_tests"]]
    assert flaky_names == ["test_flaky"]
    assert r["consistently_failing"] == ["test_a"]


def test_flaky_detector_clean_when_no_flakiness(tmp_path):
    _write_result(tmp_path / "r0.json", 50, [])
    _write_result(tmp_path / "r1.json", 50, [])
    r = ds.flaky_test_detector(str(tmp_path))
    assert r["ok"] is True
    assert r["flaky_tests"] == []
    assert "no flaky" in r["verdict"]


def test_flaky_detector_needs_two_artifacts(tmp_path):
    _write_result(tmp_path / "only.json", 10, ["test_x"])
    r = ds.flaky_test_detector(str(tmp_path))
    assert r["ok"] is False
    assert "need >=2" in r["error"]


def test_flaky_detector_missing_dir_is_error():
    r = ds.flaky_test_detector("/tmp/t1_no_such_artifacts")
    assert r["ok"] is False
    assert "error" in r


# ── dev routes ─────────────────────────────────────────────────────

def _dev_client():
    os.environ["BD_DEV_MODE"] = "1"
    os.environ["BD_DISABLE_KEEPALIVE"] = "1"
    from bulk_downloader.app import app
    return app.test_client()


def test_parametrize_fanout_route():
    # pass an absolute tests_dir so the test does not depend on the
    # runner's cwd
    r = _dev_client().get(
        "/api/dev/parametrize_fanout?tests_dir=" + _REPO_TESTS)
    assert r.status_code == 200
    j = r.get_json()
    assert j["ok"] is True
    assert "total_parametrize_decorators" in j


def test_flaky_tests_route():
    # no test_artifacts dir -> ok:false with a clear error, 200 status
    r = _dev_client().get("/api/dev/flaky_tests")
    assert r.status_code == 200
    j = r.get_json()
    assert j["tool"] == "flaky_test_detector"


def test_t1_routes_are_dev_gated():
    os.environ.pop("BD_DEV_MODE", None)
    os.environ["BD_DEV_MODE_DISABLE"] = "1"
    try:
        from bulk_downloader.app import app
        c = app.test_client()
        for ep in ("/api/dev/parametrize_fanout", "/api/dev/flaky_tests"):
            assert c.get(ep).status_code == 404
    finally:
        os.environ.pop("BD_DEV_MODE_DISABLE", None)

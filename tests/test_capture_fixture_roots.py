"""Explicit opt-in roots for private capture-corpus integration tests."""

from pathlib import Path

import pytest

import capture_test_fixtures
from capture_test_fixtures import capture_fixture_lane


_CAPTURE_MODULES = (
    "test_v3_66_77_sharded_collapse.py",
    "test_v3_66_82_confidence_admission.py",
    "test_v3_66_83_hls_manifest_preference.py",
    "test_v3_66_84_rendition_suffix.py",
    "test_v3_66_85_signing_in_path.py",
    "test_v3_66_87_temporal_harness.py",
    "test_v3_66_88_perturbation_harness.py",
    "test_v3_66_89_offline_capture_ingest.py",
    "test_v3_66_101_cockpit_wave3.py",
    "test_v3_66_249_aylo_api_recognizer.py",
)


def test_default_lane_is_disabled_without_explicit_root(monkeypatch):
    monkeypatch.delenv("BD_TEST_CAPTURE_ROOT", raising=False)

    lane = capture_fixture_lane()

    assert lane.enabled is False
    assert lane.has("capture.wacz") is False
    with pytest.raises(RuntimeError, match="BD_TEST_CAPTURE_ROOT"):
        lane.path("capture.wacz")


def test_explicit_root_enables_only_existing_artifacts(monkeypatch, tmp_path):
    artifact = tmp_path / "capture.wacz"
    artifact.write_bytes(b"fixture")
    monkeypatch.setenv("BD_TEST_CAPTURE_ROOT", str(tmp_path))

    lane = capture_fixture_lane()

    assert lane.enabled is True
    assert lane.root == tmp_path
    assert lane.has("capture.wacz") is True
    assert lane.has("missing.wacz") is False
    assert lane.path("capture.wacz") == artifact


def test_strict_lane_requires_its_own_explicit_root(monkeypatch, tmp_path):
    monkeypatch.setenv("BD_TEST_CAPTURE_ROOT", str(tmp_path))
    monkeypatch.delenv("BD_TEST_STRICT_CAPTURE_ROOT", raising=False)

    lane = capture_fixture_lane(strict=True)

    assert lane.enabled is False
    with pytest.raises(RuntimeError, match="BD_TEST_STRICT_CAPTURE_ROOT"):
        lane.path("strict.wacz")


def test_relative_root_is_rejected(monkeypatch):
    monkeypatch.setenv("BD_TEST_CAPTURE_ROOT", "relative/corpus")

    with pytest.raises(ValueError, match="absolute"):
        capture_fixture_lane()


def test_capture_root_validation_rejects_a_missing_directory(monkeypatch, tmp_path):
    missing = tmp_path / "missing-corpus"
    monkeypatch.setenv("BD_TEST_CAPTURE_ROOT", str(missing))
    monkeypatch.delenv("BD_TEST_STRICT_CAPTURE_ROOT", raising=False)

    with pytest.raises(ValueError, match="BD_TEST_CAPTURE_ROOT.*directory not found"):
        capture_test_fixtures.validate_capture_fixture_roots()


def test_artifact_name_cannot_escape_explicit_root(monkeypatch, tmp_path):
    monkeypatch.setenv("BD_TEST_CAPTURE_ROOT", str(tmp_path))
    lane = capture_fixture_lane()

    with pytest.raises(ValueError, match="artifact name"):
        lane.path("../outside.wacz")


def test_capture_modules_do_not_embed_legacy_private_roots():
    tests_root = Path(__file__).resolve().parent

    for name in _CAPTURE_MODULES:
        source = (tests_root / name).read_text(encoding="utf-8")
        assert "/mnt/user-data/uploads" not in source, name
        assert "/home/claude/corpus/wacz" not in source, name
        assert "capture_fixture_lane" in source, name


def test_capture_script_inherits_fixture_roots_without_promoting_test_settings():
    source = (Path(__file__).resolve().parent.parent / "capture.sh").read_text(
        encoding="utf-8"
    )

    suite_prefix = (
        'run_with_heartbeat "full test suite" "$OUT/02_suite_run.log" \\\n'
        '   env BD_DISABLE_KEEPALIVE=1 venv/bin/python -m pytest'
    )
    assert suite_prefix not in source
    assert 'run_with_heartbeat "parallel-safe pytest lane"' in source
    assert '"$OUT/02_pytest_parallel.log"' in source
    assert 'run_with_heartbeat "serial pytest lane"' in source
    assert '"$OUT/02_pytest_serial.log"' in source
    assert '-n "$WORKERS"' in source
    assert "--dist loadfile" in source
    assert "-m capture_parallel" in source
    assert '--junitxml="$OUT/02_pytest_parallel.xml"' in source
    assert "-m capture_serial" in source
    assert "-n 0" in source
    assert '--junitxml="$OUT/02_pytest_serial.xml"' in source
    assert "tools/pytest_capture_results.py" in source
    assert "BD_TEST_CAPTURE_ROOT" not in source
    assert "BD_TEST_STRICT_CAPTURE_ROOT" not in source
    assert ".wacz-stage" not in source


def test_capture_script_rejects_invalid_opt_in_fixture_roots_up_front():
    source = (Path(__file__).resolve().parent.parent / "capture.sh").read_text(
        encoding="utf-8"
    )

    assert "validate_capture_fixture_roots" in source
    assert source.index("validate_capture_fixture_roots") < source.index('rm -rf "$OUT"')

"""Explicit opt-in roots for private capture-corpus integration tests."""

from pathlib import Path

import pytest

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

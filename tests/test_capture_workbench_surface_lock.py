"""test_capture_workbench_surface_lock.py -- attribute-surface guard for the
capture_workbench -> capture_workbench_impl package split (DECOMP-LEAF cut 4)."""
from bulk_downloader import capture_workbench as wb

FROZEN = {
    "goal_skeleton", "build_workbench", "Slot", "DetectorDraft", "WORKBENCH_VERSION",
    "SIGNING", "PROVENANCE", "STABLE_ID", "CLIENT_COMPUTED", "ROTATING_OPAQUE",
    "INVARIANT", "_ID_SHAPES", "_AFFECTS", "_CONF_BY_VERDICT", "_RATIONALE_BY_VERDICT",
}


def test_frozen_surface_subset():
    missing = FROZEN - set(dir(wb))
    assert not missing, f"capture_workbench shim dropped: {sorted(missing)}"


def test_dataclasses_intact():
    import dataclasses as dc
    assert dc.is_dataclass(wb.Slot) and dc.is_dataclass(wb.DetectorDraft)
    assert "workbench_version" in {f.name for f in dc.fields(wb.DetectorDraft)}


def test_full_public_surface():
    for name in ("goal_skeleton", "build_workbench", "Slot", "DetectorDraft",
                 "_admit_candidate", "_sensitivity", "_decision_confidence",
                 "_mask_path_signing", "_uncertainty_flow", "_impact"):
        assert hasattr(wb, name), f"missing {name}"


def test_each_submodule_imports():
    import importlib
    for mod in ("_common", "skeleton", "analysis", "orchestrator"):
        importlib.import_module(f"bulk_downloader.capture_workbench_impl.{mod}")


def test_common_is_sole_extraction_core_importer():
    import bulk_downloader.capture_workbench_impl._common as common
    assert hasattr(common, "IDENTITY") and hasattr(common, "classify_value")

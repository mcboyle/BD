"""A canceled parent transfer must stay canceled in its progress rollup."""

from bulk_downloader.runner_progress_telemetry import HierarchicalProgressTracker

BD_GATE_SCOPE = "module"


def test_canceled_parent_with_completed_child_stays_canceled():
    tracker = HierarchicalProgressTracker()
    tracker.create_stream("download", total_bytes=4)
    tracker.create_stream("part", parent_id="download", total_bytes=4)
    tracker.start_stream("download")
    tracker.start_stream("part")
    tracker.update_stream("part", completed_bytes=4)
    tracker.complete_stream("part")
    assert tracker.get_rollup("part")["status"] == "completed"

    tracker.cancel_stream("download")
    assert tracker.get_stream("download").status.value == "cancelled"
    assert tracker.get_rollup("download")["status"] == "cancelled"

    tracker.start_stream("download")
    tracker.complete_stream("download")
    assert tracker.get_rollup("download")["status"] == "completed"

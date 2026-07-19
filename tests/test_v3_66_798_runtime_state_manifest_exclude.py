"""v3.66.798 (BUILD-HYG): two runtime-state carriers must be excluded from
the release manifest -- live_recordings/ (DIR-scoped) and
plugins/.plugin_state.json (PATH-scoped).

Same leak class as app_config.json @263, state/heartbeat.json @B5 and
.hypothesis @748, with fresh evidence for both members:

* live_recordings/ -- app.py resolves ``_live_state_dir`` to
  <DATA_DIR or .>/live_recordings (app.py ~5418) and live_recorder.init()
  mkdirs it; recordings.json (live runtime state) then lives under the
  deployed tree. Unexcluded, a release built in a tree where the live
  recorder ever initialized SHIPS that state, and an ``unzip -o`` overlay
  of such a zip CLOBBERS the operator's live recordings on stash.

* plugins/.plugin_state.json -- plugins.py::_quarantine_state_path()
  anchors plugin quarantine state at _plugin_dir()/.plugin_state.json,
  i.e. inside the install tree. At the first v3.66.797 build attempt it
  was written by test_v3_66_776_plugin_metrics_residual during an in-tree
  band run, PACKED INTO THE RELEASE ZIP, and "namelist: clean" certified
  it -- zero exclusion rules covered it. PATH-scoped (the app_config.json
  precedent) because plugins/ itself SHIPS plugins.json and must keep
  shipping it; only the exact runtime-state path is dropped.

Both regenerate at runtime when absent, so exclusion is safe -- the same
posture the deploy overlay already takes.
"""
from __future__ import annotations


class TestLiveRecordingsExcluded:
    def test_live_recordings_state_excluded(self):
        from bulk_downloader.dev_suite import _manifest_excluded
        assert _manifest_excluded("live_recordings/recordings.json") is True

    def test_anything_under_the_dir_is_excluded(self):
        # DIR-scoped on purpose: every artifact the recorder drops there
        # is runtime state, whatever its name.
        from bulk_downloader.dev_suite import _manifest_excluded
        assert _manifest_excluded("live_recordings/session_0001.wacz") is True

    def test_dir_segment_matches_not_basename_substring(self):
        """Only the DIRECTORY segment matches -- a basename merely
        containing the words must still ship (the NEG guard from the cut
        plan)."""
        from bulk_downloader.dev_suite import _manifest_excluded
        assert _manifest_excluded("frontend/live_recordings_panel.tsx") is False
        assert _manifest_excluded("docs/live_recordings_notes.md") is False


class TestPluginStateExcluded:
    def test_plugin_quarantine_state_excluded(self):
        from bulk_downloader.dev_suite import _manifest_excluded
        assert _manifest_excluded("plugins/.plugin_state.json") is True

    def test_shipped_plugins_json_still_ships(self):
        # plugins/ is a SHIPPED directory (plugins.json is tracked). The
        # exclusion must be the exact state path, never the directory.
        from bulk_downloader.dev_suite import _manifest_excluded
        assert _manifest_excluded("plugins/plugins.json") is False

    def test_path_scoped_not_a_basename_rule(self):
        # A same-named file anywhere else must not be dropped -- what
        # distinguishes the PATHS set from the NAMES set (263 precedent).
        from bulk_downloader.dev_suite import _manifest_excluded
        assert _manifest_excluded("some/dir/.plugin_state.json") is False


class TestNoCollateral:
    def test_existing_exclusions_undisturbed(self):
        from bulk_downloader.dev_suite import _manifest_excluded
        assert _manifest_excluded("app_config.json") is True
        assert _manifest_excluded("state/heartbeat.json") is True

    def test_normal_source_not_excluded(self):
        from bulk_downloader.dev_suite import _manifest_excluded
        assert _manifest_excluded("bulk_downloader/app.py") is False
        assert _manifest_excluded("frontend/dist/index.html") is False


class TestTracksSourceConstants:
    def test_dir_exclusion_tracks_app_live_state_literal(self):
        """Pin the exclusion to the literal app.py actually resolves. If
        _live_state_dir is ever renamed, this fails loudly so the
        exclusion follows."""
        import inspect
        from bulk_downloader.dev_suite._common import _MANIFEST_EXCLUDE_DIRS
        from bulk_downloader import app as _app
        src = inspect.getsource(_app)
        assert '"live_recordings"' in src or "'live_recordings'" in src or \
            "./live_recordings" in src, "app.py no longer names live_recordings"
        assert "live_recordings" in _MANIFEST_EXCLUDE_DIRS, (
            "app.py resolves _live_state_dir under the deployed tree -- "
            "live_recordings must be in _MANIFEST_EXCLUDE_DIRS or the "
            "release ships (and the overlay clobbers) live recorder state")

    def test_path_exclusion_tracks_plugins_state_path(self):
        """Pin the exclusion to what plugins.py actually writes."""
        from bulk_downloader.dev_suite.release_lint import _MANIFEST_EXCLUDE_PATHS
        from bulk_downloader import plugins as _pl
        state = _pl._quarantine_state_path()
        rel = f"plugins/{state.name}"
        assert state.name == ".plugin_state.json", state
        assert rel in _MANIFEST_EXCLUDE_PATHS, (
            f"plugins.py persists quarantine state at {rel!r} inside the "
            f"install tree -- it must be in _MANIFEST_EXCLUDE_PATHS or it "
            f"ships in the release zip (it did, at the first 797 build)")

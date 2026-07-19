"""v3.66.263 (BUILD-HYG): the root app_config.json must be excluded from
the release manifest — PATH-scoped, not name-scoped.

The release-zip builder (tools/build_release._walk_tree) and the verifier
(dev_suite.zip_manifest_check) share dev_suite._manifest_excluded() to
decide which files belong in a release. Any app boot in the source tree
runs api_tokens._signing_secret(), which set_config()s a freshly-minted
``api_auth_token_secret`` into the cwd-relative app_config.json
(global_config._CONFIG_FILE). Without an exclusion, that LIVE signing
secret ships inside the release zip — and an ``unzip -o`` overlay of it
clobbers the operator's real secret on stash. (Observed at the 262 cut:
the DEC-2 test_client + bd-cut steps injected the secret; it had to be
scrubbed out by hand before shipping.)

Why PATH-scoped and not added to _MANIFEST_EXCLUDE_NAMES like its sibling
sites_config.json: there is a second file named app_config.json under
frontend/ (referenced by the SPA source) that must STILL ship. A bare
basename exclusion would wrongly drop the frontend twin. So only the
exact root-relative path "app_config.json" is excluded.

The app re-seeds app_config.json on a fresh first run when absent, so
dropping it from the manifest is safe — the same posture the deploy
overlay already takes.
"""
from __future__ import annotations


class TestRootAppConfigExcluded:
    def test_root_app_config_excluded(self):
        from bulk_downloader.dev_suite import _manifest_excluded
        assert _manifest_excluded("app_config.json") is True


class TestScopedNotBasename:
    def test_frontend_app_config_still_ships(self):
        # The SPA twin is referenced by frontend source and must ship.
        from bulk_downloader.dev_suite import _manifest_excluded
        assert _manifest_excluded("frontend/app_config.json") is False

    def test_exclusion_is_path_scoped_not_a_basename_rule(self):
        # A same-named file in ANY other directory must not be dropped —
        # this is what distinguishes the PATHS set from the NAMES set and
        # stops a future maintainer from "simplifying" it to a basename.
        from bulk_downloader.dev_suite import _manifest_excluded
        assert _manifest_excluded("some/dir/app_config.json") is False
        assert _manifest_excluded("config_snapshots/app_config.json") is False


class TestNoCollateral:
    def test_sibling_sites_config_still_excluded(self):
        """Regression guard — don't disturb the existing operator-config
        exclusion (sites_config.json is name-scoped and has no twin)."""
        from bulk_downloader.dev_suite import _manifest_excluded
        assert _manifest_excluded("sites_config.json") is True

    def test_normal_source_not_excluded(self):
        """Sanity: the exclusion isn't accidentally too broad."""
        from bulk_downloader.dev_suite import _manifest_excluded
        assert _manifest_excluded("bulk_downloader/app.py") is False
        assert _manifest_excluded("tests/test_api.py") is False


class TestTracksSourceConstant:
    def test_exclusion_tracks_global_config_path_constant(self):
        """Pin the exclusion to the cwd-relative config path the app
        actually writes (global_config._CONFIG_FILE). If that file is
        ever renamed, this fails loudly so the exclusion is updated."""
        from bulk_downloader.dev_suite import _MANIFEST_EXCLUDE_PATHS
        from bulk_downloader import global_config as gc
        rel = str(gc._CONFIG_FILE).replace("\\", "/")
        assert rel in _MANIFEST_EXCLUDE_PATHS, (
            f"global_config._CONFIG_FILE={rel!r} must be in "
            f"_MANIFEST_EXCLUDE_PATHS — else the release zip ships the "
            f"live api_auth_token_secret")

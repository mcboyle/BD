"""v3.64.3: dev_suite._manifest_excluded must filter runtime sentinels.

The release-zip builder uses dev_suite._manifest_excluded() to decide
which files belong in a release. Two sentinel files were silently
missing from the exclusion list — .integrity_last_run and
.fts_optimize_last — both generated as a side effect of the
build_release.py endpoint-catalog gate (it imports bulk_downloader.app,
which boots db.py, which writes both sentinels). In any environment
where the suite or app had previously run, those sentinels would
contaminate the release zip with stale runtime state.

This test pins the fix: both names are now in the exclusion set.
A future maintainer who renames or removes either of these without
also updating dev_suite._MANIFEST_EXCLUDE_NAMES will see this test
fail.
"""
from __future__ import annotations


class TestRuntimeSentinelsExcluded:
    def test_integrity_last_run_excluded(self):
        from bulk_downloader.dev_suite import _manifest_excluded
        assert _manifest_excluded(".integrity_last_run") is True

    def test_fts_optimize_last_excluded(self):
        from bulk_downloader.dev_suite import _manifest_excluded
        assert _manifest_excluded(".fts_optimize_last") is True

    def test_integrity_check_last_still_excluded(self):
        """Regression guard — don't lose the existing exclusion."""
        from bulk_downloader.dev_suite import _manifest_excluded
        assert _manifest_excluded(".integrity_check_last") is True

    def test_video_hashes_db_excluded(self):
        """v3.66.781: the video dedup DB (dedup.get_default_registry default
        'video_hashes.db', WAL-mode) is written to cwd on first use and leaked
        into the 781 build. Sibling of downloader_history.db; must be excluded."""
        from bulk_downloader.dev_suite import _manifest_excluded
        assert _manifest_excluded("video_hashes.db") is True

    def test_video_hashes_db_wal_sidecars_excluded(self):
        """WAL mode (dedup.py PRAGMA journal_mode=WAL) can drop -wal/-shm sidecars
        next to the DB, exactly as downloader_history.db does -- exclude both."""
        from bulk_downloader.dev_suite import _manifest_excluded
        assert _manifest_excluded("video_hashes.db-wal") is True
        assert _manifest_excluded("video_hashes.db-shm") is True

    def test_video_hashes_db_matches_dedup_default(self):
        """Pin the exclusion to the source: dedup.get_default_registry's default
        db path must stay in _MANIFEST_EXCLUDE_NAMES so a rename can't re-open the
        leak silently."""
        import inspect

        from bulk_downloader import dedup
        from bulk_downloader.dev_suite import _MANIFEST_EXCLUDE_NAMES
        default = inspect.signature(dedup.get_default_registry).parameters["db_path"].default
        assert default in _MANIFEST_EXCLUDE_NAMES, (
            f"dedup default db {default!r} must be manifest-excluded -- else it "
            f"ships in any zip built after the app/suite ran in the tree")

    def test_a_normal_source_file_is_not_excluded(self):
        """Sanity: the exclusion isn't accidentally too broad."""
        from bulk_downloader.dev_suite import _manifest_excluded
        assert _manifest_excluded("bulk_downloader/app.py") is False
        assert _manifest_excluded("tests/test_api.py") is False

    def test_exclusion_matches_sentinel_definitions_in_db_py(self):
        """The sentinel filenames live in db.py as string constants.
        If db.py ever renames them, _MANIFEST_EXCLUDE_NAMES needs to
        track the rename. This test pins the link by reading the
        actual constants db.py defines."""
        from bulk_downloader import db
        from bulk_downloader.dev_suite import _MANIFEST_EXCLUDE_NAMES
        # db._INTEGRITY_STATE_FILE is the .integrity_last_run sentinel.
        assert db._INTEGRITY_STATE_FILE in _MANIFEST_EXCLUDE_NAMES, (
            f"db._INTEGRITY_STATE_FILE = {db._INTEGRITY_STATE_FILE!r} "
            f"must be in dev_suite._MANIFEST_EXCLUDE_NAMES — otherwise "
            f"the release zip ships with stale runtime state")


class TestReleaseZipsExcludedFromManifest:
    """v3.65.1: release zip output (BulkDownloader_v*.zip) must be
    excluded from the manifest verifier.

    The build_release.py flow is: write the zip, then verify the zip
    contents against `root.rglob('*')`. If *.zip isn't excluded, the
    just-written zip appears in the source-tree walk AFTER it's been
    written, and the verifier reports it 'missing from zip' (because
    the zip can't contain itself). Latent in v3.65.0 — bit when the
    build was run in a tree where the output dir IS the repo root."""

    def test_release_zip_excluded(self):
        from bulk_downloader.dev_suite import _manifest_excluded
        assert _manifest_excluded("BulkDownloader_v3_65_1.zip") is True

    def test_arbitrary_zip_excluded(self):
        """The exclusion is by suffix, not by exact match — any zip
        in the tree is build output (or a stray from prior builds)."""
        from bulk_downloader.dev_suite import _manifest_excluded
        assert _manifest_excluded("scratch.zip") is True
        assert _manifest_excluded("nested/dir/archive.zip") is True

    def test_zip_suffix_in_exclude_suffixes(self):
        """Pin the change at the constant level."""
        from bulk_downloader.dev_suite import _MANIFEST_EXCLUDE_SUFFIXES
        assert ".zip" in _MANIFEST_EXCLUDE_SUFFIXES, (
            "release zips must be excluded from the manifest verifier — "
            "without this the builder self-references and fails verify")

    def test_non_zip_files_still_included(self):
        """Sanity: exclusion is suffix-bounded, doesn't accidentally
        match 'foo-zip' or files that contain 'zip' in the middle."""
        from bulk_downloader.dev_suite import _manifest_excluded
        assert _manifest_excluded("bulk_downloader/app.py") is False
        assert _manifest_excluded("ziplog.txt") is False
        assert _manifest_excluded("notes-zip-overview.md") is False


class TestB5RuntimeArtifactsExcluded:
    """v3.65.1 B5: three more runtime artifacts that leaked into the
    first v3.65.1 build attempt when the build was run AFTER the suite
    had executed in the same tree.

    The most serious is vapid_keys.json — it contains a PEM private
    key generated by the push-subscription module on first import.
    Same defect-class as the v3.64.3 .integrity_last_run finding,
    worse blast radius (any operator deploying a zip with someone
    else's vapid_keys.json gets their web-push origin compromised).

    state/ holds the heartbeat-to-disk loop's working files. It's
    mkdir'd at runtime, never source. Excluded as a directory so other
    state files (if any are added later) are caught too.

    test_singleton.db is created by test_v3_43_72_dedup.py via
    get_default_registry("test_singleton.db") in cwd. Test artifact.
    """

    def test_vapid_keys_json_excluded(self):
        """Critical — this file holds a private key. Must NEVER ship
        in a release zip."""
        from bulk_downloader.dev_suite import _manifest_excluded
        assert _manifest_excluded("vapid_keys.json") is True

    def test_vapid_keys_json_in_exclude_names(self):
        """Pin at the constant level."""
        from bulk_downloader.dev_suite import _MANIFEST_EXCLUDE_NAMES
        assert "vapid_keys.json" in _MANIFEST_EXCLUDE_NAMES, (
            "vapid_keys.json contains a private key — must be in the "
            "exclude list. Removing this exclusion would re-introduce "
            "the B5 security issue.")

    def test_state_directory_excluded(self):
        """state/ is the heartbeat-to-disk working directory."""
        from bulk_downloader.dev_suite import _manifest_excluded
        assert _manifest_excluded("state/heartbeat.json") is True

    def test_state_directory_catches_arbitrary_state_files(self):
        """Directory-level exclusion, not just heartbeat.json — any
        file inside state/ is runtime."""
        from bulk_downloader.dev_suite import _manifest_excluded
        assert _manifest_excluded("state/some_other_file.json") is True
        assert _manifest_excluded("state/nested/deep.txt") is True

    def test_state_in_exclude_dirs(self):
        from bulk_downloader.dev_suite import _MANIFEST_EXCLUDE_DIRS
        assert "state" in _MANIFEST_EXCLUDE_DIRS

    def test_test_singleton_db_excluded(self):
        from bulk_downloader.dev_suite import _manifest_excluded
        assert _manifest_excluded("test_singleton.db") is True

    def test_state_exclusion_is_not_substring_match(self):
        """A directory-level exclusion checks path-component equality,
        not substring. tests/test_state_machine.py contains 'state'
        but is NOT inside a 'state' directory — must NOT be excluded."""
        from bulk_downloader.dev_suite import _manifest_excluded
        assert _manifest_excluded("tests/test_state_machine.py") is False
        assert _manifest_excluded("docs/state_diagram.md") is False

    def test_vapid_keys_exclusion_is_not_substring_match(self):
        """vapid_keys.json is excluded by exact filename, not by
        substring. A docs file with 'vapid_keys' in the name is fine."""
        from bulk_downloader.dev_suite import _manifest_excluded
        assert _manifest_excluded("docs/vapid_keys_design.md") is False
        assert _manifest_excluded("bulk_downloader/push.py") is False


class TestPremigrationBackupExcluded:
    """v3.66.783 (BUILD-HYG): the pre-migration DB backup must not ship.

    migrations._backup_db_before_migration() copies the live DB aside as
    ``<db>.premigration.bak`` (migrations.py: ``dst = src + ".premigration.bak"``)
    before a schema migration mutates it. Any build run in a tree whose app
    has booted through a migration leaks that backup -- the 782 zip shipped
    downloader_history.db.premigration.bak (135 KB of live history), a latent
    leak present since <=780 and the same class as the video_hashes.db /
    downloader_history.db runtime-DB fixes.

    The exclusion is SUFFIX-scoped, not exact-name, because the backup name
    is derived generically (src + ".premigration.bak") for whatever DB is being
    migrated -- so a future migrated DB's backup is caught by the same rule.
    """

    def test_downloader_history_premigration_bak_excluded(self):
        """The exact file that leaked into the 782 zip."""
        from bulk_downloader.dev_suite import _manifest_excluded
        assert _manifest_excluded("downloader_history.db.premigration.bak") is True

    def test_premigration_bak_is_suffix_generic(self):
        """Derived from the generator (src + '.premigration.bak'), so any
        migrated DB's backup is excluded -- not just today's one file."""
        from bulk_downloader.dev_suite import _manifest_excluded
        assert _manifest_excluded("video_hashes.db.premigration.bak") is True
        assert _manifest_excluded("nested/dir/some.db.premigration.bak") is True

    def test_premigration_bak_suffix_in_exclude_suffixes(self):
        """Pin the change at the constant level."""
        from bulk_downloader.dev_suite import _MANIFEST_EXCLUDE_SUFFIXES
        assert ".premigration.bak" in _MANIFEST_EXCLUDE_SUFFIXES, (
            "the pre-migration DB backup suffix must be manifest-excluded -- "
            "else it ships in any zip built after a migration ran in the tree")

    def test_exclusion_matches_migrations_source(self):
        """Pin the exclusion to the generator: the suffix migrations.py appends
        must be the suffix we exclude, so a rename there trips this test rather
        than silently re-opening the leak."""
        import inspect

        from bulk_downloader import migrations
        from bulk_downloader.dev_suite import _MANIFEST_EXCLUDE_SUFFIXES
        src = inspect.getsource(migrations._backup_db_before_migration)
        assert '.premigration.bak"' in src, (
            "migrations._backup_db_before_migration no longer appends "
            "'.premigration.bak' -- update the exclusion to track the rename")
        assert any(
            "premigration.bak" in s for s in _MANIFEST_EXCLUDE_SUFFIXES), (
            "the suffix migrations appends is not in _MANIFEST_EXCLUDE_SUFFIXES")

    def test_plain_bak_not_over_excluded(self):
        """Safety: the suffix is '.premigration.bak', NOT a bare '.bak' --
        a legitimate '.bak' source file must still ship (same caution the @43
        comment applied to '.tmp')."""
        from bulk_downloader.dev_suite import _manifest_excluded
        assert _manifest_excluded("notes.bak") is False
        assert _manifest_excluded("config.bak") is False
        assert _manifest_excluded("bulk_downloader/app.py") is False

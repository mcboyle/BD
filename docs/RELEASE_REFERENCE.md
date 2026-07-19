# Release reference

Release gate: `tools/verify_release.py` (version + docs + templates + zip manifest + tests). See `docs/VERIFY_RELEASE.md`.

Changelog: **136** releases, **2087** feature bullets, **78** fix bullets.

Checklist: bump `bulk_downloader/__init__.py` __version__; prepend a matching `## vX.Y.Z` CHANGELOG entry; regen ENDPOINT_CATALOG / FUNCTION_INDEX if routes/functions changed; build; verify from the extracted zip.

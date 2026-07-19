"""v3.66.756 -- runtime-mutable template drafts must be EXCLUDED from the release.

THE DEFECT (MUTABLE-IN-ZIP): templates/drafts/<host>.template-draft.json is written at
RUNTIME by dom_analyzer.pin_candidate / build_draft (via template_manager.DRAFTS_DIR) and
read/globbed at runtime by app_template_manager. It is NOT source. Yet the 755 release zip
shipped templates/drafts/x.template-draft.json -- so an `unzip -o` overlay deploy would drop
a stale developer-side draft on top of the operator's runtime drafts dir, and the graph/zip
gates (which glob the disk) would then see a file the tree "shouldn't" have.

The suffix .template-draft.json is UNIQUELY the runtime artifact: nothing loads a *shipped*
draft at startup, and no source file legitimately carries that suffix. So exclude by suffix
(dev_suite._MANIFEST_EXCLUDE_SUFFIXES), the same mechanism that drops .pyc/.log runtime state.

RED-first: on the pristine tree the draft is NOT excluded (it ships).
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)


class TestTemplateDraftExcluded:
    def test_a_template_draft_is_excluded(self):
        from bulk_downloader.dev_suite import _manifest_excluded
        assert _manifest_excluded("templates/drafts/example.com.template-draft.json") is True, (
            "a runtime-generated .template-draft.json is NOT excluded from the release -- it "
            "will ship and an overlay deploy will clobber the operator's runtime drafts")

    def test_the_specific_shipped_draft_is_excluded(self):
        from bulk_downloader.dev_suite import _manifest_excluded
        assert _manifest_excluded("templates/drafts/x.template-draft.json") is True

    def test_a_draft_anywhere_is_excluded_by_suffix_not_path(self):
        """Suffix-scoped: the exclusion follows the artifact wherever DRAFTS_DIR resolves,
        not one hard-coded path."""
        from bulk_downloader.dev_suite import _manifest_excluded
        assert _manifest_excluded("some/other/dir/host.template-draft.json") is True

    def test_a_real_template_still_ships(self):
        """POS guard: a committed, NON-draft template must still be included -- the exclusion
        must be the draft suffix, never all of templates/."""
        from bulk_downloader.dev_suite import _manifest_excluded
        assert _manifest_excluded("templates/some_real_template.html") is False
        assert _manifest_excluded("templates/base.json") is False

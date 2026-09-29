"""tpl95-reptyle-1: an applied user template's rows lead the reviewed template's hints.

Measured on test2 (harness-work/UIUX-20260928/download-95/B7-B/p1/reptyle/tplv2b/app-log-v2b.txt,
B7-B/tpl-reptyle/template-v2-apply.json): templates/apply of "B7B reptyle O1517 v2" returned
ok with added_roles row_selectors, yet every run logged
'learned hit via [.ant-modal.download-modal ...:has(div:text-is("2160p")) button.modal-download-button]'
and never the template's 1080p row. That selector is the REVIEWED app.reptyle.com template
(templates/reviewed/app.reptyle.com.template.json): the runner's merge_template_download_hints
prepends reviewed rows ahead of the site's learned rows, and the applied template's rows live
in the learned rows, so a reviewed host always outranks the operator's applied template.
(dfxtra, with no reviewed template, did hit its applied row.)

Contract pinned here: the rows (and triggers) of the template recorded by /templates/apply
(cfg["applied_template"]) that are still in the site's learned block are tried FIRST, then the
reviewed hints, then the other learned rows. With no applied template the order is unchanged;
a row that decay dropped from the learned block is not revived; a draft-test override still
drives on its own.
"""
from __future__ import annotations

# An ordinary module test: its subject is the module under test, not the tree.
BD_GATE_SCOPE = "module"

import inspect

from bulk_downloader import template_assist as ta

REPTYLE_MOVIE = "https://app.reptyle.com/movie/123"
REVIEWED_2160 = ('.ant-modal.download-modal .ant-space-horizontal:has(div:text-is("2160p")) '
                 'button.modal-download-button')
# B7-B's applied v2 template (template-v2-request.json).
APPLIED_1080 = ('.ant-modal.download-modal .ant-space-horizontal:has(div:text-is("1080p")) '
                'button.modal-download-button')
APPLIED_TRIGGER = "button:has-text('Download Full Movie')"
OLD_LEARNED = "a.some-old-learned-row"

APPLIED = {"row_selectors": [APPLIED_1080], "trigger_selectors": [APPLIED_TRIGGER],
           "url_attribute": ""}


class _Page:
    def __init__(self, url=REPTYLE_MOVIE):
        self.url = url


def _learned():
    # merge_learned prepended the applied rows at /templates/apply time.
    return {"row_selectors": [APPLIED_1080, OLD_LEARNED],
            "trigger_selectors": [APPLIED_TRIGGER]}


def test_applied_template_rows_are_tried_before_reviewed_hints():
    merged, tmpl = ta.merge_template_download_hints(_Page(), _learned(), applied=APPLIED)
    assert tmpl is not None, "fixture: the reviewed app.reptyle.com template must match"
    rows = merged["row_selectors"]
    assert rows[0] == APPLIED_1080, (
        "TPL95-REPTYLE1: the applied template's row is not tried first -- the reviewed "
        f"template's rows outrank it: {rows[:2]!r}")
    assert rows.index(REVIEWED_2160) < rows.index(OLD_LEARNED), (
        "reviewed hints still lead the site's other learned rows")
    assert rows.count(APPLIED_1080) == 1
    assert merged["trigger_selectors"][0] == APPLIED_TRIGGER


def test_without_an_applied_template_reviewed_hints_lead_as_before():
    """Control: the pre-existing order (reviewed first) when nothing was applied."""
    learned = {"row_selectors": [OLD_LEARNED]}
    before, _ = ta.merge_template_download_hints(_Page(), dict(learned))
    after, _ = ta.merge_template_download_hints(_Page(), dict(learned), applied={})
    assert before == after
    assert after["row_selectors"][0] == REVIEWED_2160


def test_a_row_decay_dropped_is_not_revived():
    """Control: only applied rows still present in the learned block are pinned."""
    learned = {"row_selectors": [OLD_LEARNED]}
    merged, _ = ta.merge_template_download_hints(_Page(), learned, applied=APPLIED)
    assert merged["row_selectors"][0] == REVIEWED_2160
    assert APPLIED_1080 not in merged["row_selectors"][:1]


def test_draft_override_still_drives_alone():
    """Control: a draft-test override is a separate branch; applied rows do not jump it."""
    draft = {"host": "app.reptyle.com",
             "selectors": {"download": {"row_selectors": ["div.draft-row"]}}}
    merged, tmpl = ta.merge_template_download_hints(
        _Page(), _learned(), override_template=draft, applied=APPLIED)
    assert tmpl is draft
    assert merged["row_selectors"][0] == "div.draft-row"


def test_applied_template_download_reads_the_recorded_template():
    lookups = []

    def lookup(tid):
        lookups.append(tid)
        return {"id": tid, "learned": {"download": dict(APPLIED)}}

    got = ta.applied_template_download({"applied_template": "user_b7b_reptyle_v2"}, lookup)
    assert lookups == ["user_b7b_reptyle_v2"]
    assert got["row_selectors"] == [APPLIED_1080]
    assert ta.applied_template_download({}, lookup) == {}
    assert ta.applied_template_download({"applied_template": "gone"}, lambda _t: None) == {}


def test_runner_passes_the_applied_template_to_the_merge():
    """Structural: the download path hands the site's applied template to the merge
    (the runner needs a live page to exercise end to end)."""
    from bulk_downloader import runner as runner_module

    src = inspect.getsource(runner_module)
    call = src.find("learned_dl, _reviewed_template = merge_template_download_hints(")
    end = src.find("triggers_to_try=", call)
    assert call != -1 and end != -1
    window = src[call:end]
    assert "applied=applied_template_download(self.config)" in window, (
        "TPL95-REPTYLE1: the runner never tells the merge which template was applied")

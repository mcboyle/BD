"""RED-first tests for Cut 623 / C3: selector version history + revert.

Sandbox-runner conventions: zero-arg, tempfile.mkdtemp (not tmp_path), base_dir
params for hermeticity (no monkeypatch), restore any global in try/finally.

The versioning engine tracks a template's selector block (its ``learned``) over
time so an operator can see what changed and revert. Recording is append-only to
a separate ``selector_history.json`` and must be fail-open (never break a save)
and never-raise on read.
"""
from __future__ import annotations

import os
import tempfile


def _base():
    return tempfile.mkdtemp(prefix="bdselver_")


def _tmpl(tid="t1", dl_trigger="button.dl"):
    return {
        "id": tid,
        "name": "T",
        "learned": {"download": {"trigger": dl_trigger, "row_selectors": ["a.item"]}},
    }


# ── record / list ───────────────────────────────────────────────────────

def test_record_then_list_shows_the_version():
    from bulk_downloader import selector_versions as V
    base = _base()
    vid = V.record_template_version(_tmpl(), source="save", base_dir=base)
    assert vid is not None
    vers = V.list_versions("t1", base_dir=base)
    assert len(vers) == 1
    assert vers[0]["source"] == "save"
    assert "ts" in vers[0] and "version" in vers[0]


def test_identical_snapshot_does_not_duplicate():
    from bulk_downloader import selector_versions as V
    base = _base()
    V.record_template_version(_tmpl(dl_trigger="button.dl"), base_dir=base)
    V.record_template_version(_tmpl(dl_trigger="button.dl"), base_dir=base)  # no change
    assert len(V.list_versions("t1", base_dir=base)) == 1


def test_changed_snapshot_creates_a_new_version():
    from bulk_downloader import selector_versions as V
    base = _base()
    V.record_template_version(_tmpl(dl_trigger="button.dl"), base_dir=base)
    V.record_template_version(_tmpl(dl_trigger="button.NEW"), base_dir=base)
    assert len(V.list_versions("t1", base_dir=base)) == 2


# ── get / diff / revert ─────────────────────────────────────────────────

def test_get_version_returns_the_full_selector_snapshot():
    from bulk_downloader import selector_versions as V
    base = _base()
    V.record_template_version(_tmpl(dl_trigger="button.dl"), base_dir=base)
    vers = V.list_versions("t1", base_dir=base)
    snap = V.get_version("t1", vers[0]["version"], base_dir=base)
    assert snap["download"]["trigger"] == "button.dl"


def test_diff_versions_reports_changed_keys():
    from bulk_downloader import selector_versions as V
    base = _base()
    V.record_template_version(_tmpl(dl_trigger="button.old"), base_dir=base)
    V.record_template_version(_tmpl(dl_trigger="button.new"), base_dir=base)
    vers = V.list_versions("t1", base_dir=base)  # newest first
    d = V.diff_versions("t1", vers[1]["version"], vers[0]["version"], base_dir=base)
    # download.trigger changed old->new
    joined = repr(d)
    assert "trigger" in joined and ("button.old" in joined or "button.new" in joined)


def test_revert_returns_old_selectors_and_is_fail_closed_on_miss():
    from bulk_downloader import selector_versions as V
    base = _base()
    V.record_template_version(_tmpl(dl_trigger="button.v1"), base_dir=base)
    vers = V.list_versions("t1", base_dir=base)
    restored = V.revert_selectors("t1", vers[0]["version"], base_dir=base)
    assert restored["download"]["trigger"] == "button.v1"
    # unknown version / unknown template -> None (fail closed, never raises)
    assert V.revert_selectors("t1", "no-such-version", base_dir=base) is None
    assert V.revert_selectors("no-such-template", vers[0]["version"], base_dir=base) is None


# ── read paths never raise on empty/missing store ───────────────────────

def test_reads_never_raise_on_empty_store():
    from bulk_downloader import selector_versions as V
    base = _base()  # no history file yet
    assert V.list_versions("anything", base_dir=base) == []
    assert V.get_version("anything", "v", base_dir=base) is None
    assert V.revert_selectors("anything", "v", base_dir=base) is None


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    p = f = 0
    for fn in fns:
        try:
            fn(); p += 1; print(f"  [PASS] {fn.__name__}")
        except Exception as e:
            f += 1; print(f"  [FAIL] {fn.__name__}: {type(e).__name__}: {e}")
    print(f"\n{p} passed / {f} failed")
    raise SystemExit(1 if f else 0)

"""GCW-2 — per-entry delete (HARD REQUIREMENT) in the /capture picker rail.

The legacy teach overlay deletes a SINGLE mis-picked entry from a multi-pick list
without nuking the list (``learn.py:621`` per-entry splice), plus undo. The
``/capture`` SPA picker only had single-value slots and a ``removeField`` that
blanked the WHOLE slot — the operator's prior pain. GCW-2 turns ``row_selectors``
into a multi-pick list (download pages expose several row/resolution selectors;
the runner already consumes ``row_selectors`` as a LIST —
``(row_selectors or []).index(matched_selector)`` at runner.py:416), appends each
pick, and removes ONE entry at a time with an undo. All deletes are draft-only
(the live page is untouched) — no /api/ call in the delete path.

Same source-scan idiom as test_v3_66_270_promote_suffix: the handler is
client-side TS, so the sandbox pins the load-bearing markers (the full React
behaviour is proven by tsc + vite + a Playwright run; jsdom unit tests can't run
here). The wire-shape change (row_selectors string -> list) is a deliberate
schema-alignment with the runner's list semantics.

run_tests.py conventions: zero-arg test functions; repo root from
Path(__file__).resolve().parent.parent; no pytest builtins.
"""
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

CAPTURE = REPO / "frontend" / "src" / "routes" / "CaptureWorkflow.tsx"


def _read(p: Path) -> str:
    assert p.exists(), f"missing source file: {p}"
    return p.read_text(encoding="utf-8")


def test_row_selectors_is_a_multipick_list():
    """row_selectors must be modelled as a multi-pick list (entries), not a
    single overwritten value, so multiple picks accumulate."""
    src = _read(CAPTURE)
    assert "entries" in src, "the multi-pick field must carry an entries list"
    # the multi field is flagged so single fields (login_*) stay single-value
    assert "multi" in src, (
        "row_selectors must be flagged multi so login fields stay single-value"
    )


def test_per_entry_delete_removes_one_index_not_whole_slot():
    """Deleting one entry must target a single index and KEEP the rest — not
    blank the whole slot. Pin the index-targeted removal helper."""
    src = _read(CAPTURE)
    assert "removePickEntry" in src, (
        "a per-entry delete handler (removePickEntry) must exist"
    )
    block = src[src.find("function removePickEntry"):]
    block = block[:1200]
    assert block, "removePickEntry not found"
    # index-targeted removal: filter/splice by index, never a whole-list reset
    assert ("filter((_, i) => i !== index)" in block
            or "splice(index, 1)" in block), (
        "per-entry delete must remove a single index (filter/splice), "
        "not blank the whole slot"
    )


def test_per_entry_delete_has_undo():
    """The operator can undo a mistaken delete (restore the last removed
    entry)."""
    src = _read(CAPTURE)
    assert "undoRemovePickEntry" in src, "an undo handler must exist"
    # an undo buffer holds the last-deleted entry to restore it
    assert "undoBuf" in src or "lastDeleted" in src, (
        "undo needs a buffer holding the last-deleted entry"
    )


def test_per_entry_delete_is_draft_only_no_api_call():
    """Deletes are draft-only — the live page is untouched. The delete handler
    must NOT call any /api/ or /cockpit/api/ endpoint."""
    src = _read(CAPTURE)
    start = src.find("function removePickEntry")
    assert start >= 0
    block = src[start:start + 1200]
    assert "apiPost" not in block and "apiGet" not in block, (
        "per-entry delete must not hit the backend — it is draft-only"
    )


def test_pick_appends_to_multi_list_with_dedupe():
    """A landed pick on the multi field appends to entries (dedupe), instead of
    overwriting a single value."""
    src = _read(CAPTURE)
    # the poll loop's apply branch must append for multi fields
    assert "f.multi" in src, "the pick-apply branch must distinguish multi fields"
    # v3.66.274 renamed the appended value to `chosen` (group_selector for multi
    # fields, picked.selector otherwise) and dedupes against that same value.
    # The dedupe must guard against whatever is actually appended.
    assert (
        "includes(chosen)" in src
        or "includes(picked.selector)" in src
    ), "appending a pick should dedupe against existing entries"


def test_assemble_draft_sends_row_selectors_as_list():
    """assembleDraft must send row_selectors as a LIST (the canonical schema +
    runner list semantics), built from the entries, not a single string."""
    src = _read(CAPTURE)
    block = src[src.find("function assembleDraft"):]
    block = block[:700]
    assert "row_selectors" in block
    # built from entries (a list), not a single .value string
    assert "entries" in block, (
        "assembleDraft must build row_selectors from the entries list"
    )


if __name__ == "__main__":
    for _n, _f in sorted(globals().items()):
        if _n.startswith("test_") and callable(_f):
            _f()
            print("PASS", _n)
    print("ALL PASS")

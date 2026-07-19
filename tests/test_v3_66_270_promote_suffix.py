"""GCW-1 — /capture in-UI Promote builds the DRAFT-suffixed filename (v3.66.270).

Defect (RED on pristine v3.66.269):
``CaptureWorkflow.tsx`` ``promote()`` built the promote body as
``file: `${siteId.trim()}.template.json` `` — the *reviewed* suffix
(``_REVIEWED_SUFFIX = ".template.json"``). But ``template_manager.promote_draft``
runs ``_safe_name(filename, _DRAFT_SUFFIX)`` where ``_DRAFT_SUFFIX =
".template-draft.json"``, and ``_safe_name`` rejects any name not ending in that
draft suffix. The two suffixes do not overlap ("x.template-draft.json" does not
end with ".template.json"), so the in-UI Promote button could NEVER succeed — it
always returned ``400 invalid draft filename``. The curl workaround (POST with the
correct ``.template-draft.json`` name) worked because it hit the same path with a
valid draft basename.

Fix: the body literal must carry ``.template-draft.json`` and must NOT carry the
bare reviewed suffix ``.template.json``; the field still takes the bare host
(``siteId.trim()``) with the suffix appended once, so a typed host round-trips to
``<host>.template-draft.json``.

This is the same source-scan idiom as test_v3_43_22_teach_shift_click and
test_v3_66_269_capture_layout: the click/promote handler is client-side TS, so the
sandbox pins the load-bearing source markers (the full SPA behaviour is proven
separately by tsc + vite + a Playwright run; jsdom unit tests can't run here).

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


def _promote_block(src: str) -> str:
    """Return just the body of the promote() function.

    Scoped from ``async function promote()`` to the next top-level
    ``async function `` so a ``.template.json`` elsewhere in the file can't
    mask the assertion (today line 454 is the only occurrence, but the scope
    keeps the pin honest under later edits).
    """
    start = src.find("async function promote()")
    assert start >= 0, "promote() function not found in CaptureWorkflow.tsx"
    nxt = src.find("async function ", start + len("async function promote()"))
    return src[start:nxt] if nxt > start else src[start:start + 800]


def test_promote_body_uses_draft_suffix():
    """The promote body must build the DRAFT-suffixed filename."""
    block = _promote_block(_read(CAPTURE))
    assert ".template-draft.json" in block, (
        "promote() must build a `.template-draft.json` filename — "
        "promote_draft -> _safe_name requires the draft suffix"
    )


def test_promote_body_not_reviewed_suffix():
    """The promote body must NOT carry the bare reviewed suffix.

    This is the load-bearing RED->GREEN flip: the bug was exactly the
    reviewed suffix ``.template.json``. ``.template-draft.json`` does not
    contain ``.template.json`` as a substring, so a clean fix passes and any
    regression back to the reviewed suffix fails.
    """
    block = _promote_block(_read(CAPTURE))
    assert ".template.json" not in block, (
        "promote() must not send the reviewed suffix `.template.json` — "
        "promote_draft rejects it as `invalid draft filename` (400)"
    )


def test_promote_field_round_trips_trimmed_host():
    """The field still takes the bare host (trimmed), suffix appended once."""
    block = _promote_block(_read(CAPTURE))
    assert "siteId.trim()" in block, (
        "promote() should build the draft name from the trimmed host id"
    )
    # the call + enable semantics are unchanged by the fix; pin them so the
    # manual ENABLE gate stays a single, explicit promote+enable POST.
    assert "/api/template_manager/promote" in block
    assert "enable: true" in block

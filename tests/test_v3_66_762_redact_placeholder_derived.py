"""v3.66.762 -- the redaction PLACEHOLDER must have ONE source.

DERIVE-AUDIT (MEDIUM): tools/build_template_from_wacz.py carried a hand-kept
`_SCRUBBED = "<scrubbed>"` literal with a "capture_artifact_redact.PLACEHOLDER
(kept in sync)" comment. A redaction sentinel copied by hand can drift from the
canonical one; the build_template_from_wacz path feeds a SHIPPED artifact
(templates), so a drifted sentinel would silently stop treating scrubbed
selectors as scrubbed. Import the canonical PLACEHOLDER instead.

RED-first on the pristine tree: the literal assignment exists (test 1 fails) and
there is no `import PLACEHOLDER as _SCRUBBED` (test 2 fails).
"""
import ast
import pathlib

TOOL = pathlib.Path(__file__).resolve().parents[1] / "tools" / "build_template_from_wacz.py"


def _tree():
    return ast.parse(TOOL.read_text(encoding="utf-8"))


def test_scrubbed_is_not_a_mirrored_string_literal():
    lit = [n.value.value for n in ast.walk(_tree())
           if isinstance(n, ast.Assign)
           and any(isinstance(t, ast.Name) and t.id == "_SCRUBBED" for t in n.targets)
           and isinstance(n.value, ast.Constant) and isinstance(n.value.value, str)]
    assert not lit, ("_SCRUBBED is a hand-kept string literal %r mirroring "
                     "capture_artifact_redact.PLACEHOLDER -- import it instead so the "
                     "redaction sentinel has one source." % lit)


def test_scrubbed_is_imported_as_placeholder():
    imported = any(
        isinstance(n, ast.ImportFrom)
        and any(a.name == "PLACEHOLDER" and a.asname == "_SCRUBBED" for a in n.names)
        for n in ast.walk(_tree()))
    assert imported, ("_SCRUBBED must be bound by `from bulk_downloader."
                      "capture_artifact_redact import PLACEHOLDER as _SCRUBBED`.")


def test_placeholder_value_is_still_the_scrubbed_sentinel():
    # Belt-and-suspenders: the canonical value is unchanged, so the swap is
    # behavior-neutral -- both comparison sites (sel == _SCRUBBED) keep working.
    from bulk_downloader.capture_artifact_redact import PLACEHOLDER
    assert PLACEHOLDER == "<scrubbed>"

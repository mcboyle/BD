"""library-fix r1-r2: bughunt deep-spa-library-templates-mobile F1-F11, F6b.

Library and Template Manager read failures, odd payload shapes, narrow and
large-text layout, the Tags window, stored ratings, the bulk id box, repeated
ids, the disabled Promote reason and the Disable outcome. The behaviour runs
in the two tracked vitest specs below; the layout findings (F3 F4 F5) pin the
class contract there and were measured on a built rig (library-fix/r1/ev/probe-tree3.jsonl).
"""

import pytest

from tests.frontend_vitest import run_vitest

BD_GATE_SCOPE = "module"


@pytest.mark.parametrize(
    ("spec", "count"),
    [
        ("src/routes/Library.libraryFix.test.tsx", 17),
        ("src/routes/TemplateManager.libraryFix.test.tsx", 14),
    ],
)
def test_library_fix_vitest(spec, count):
    receipt = run_vitest(spec, expected_tests=count)
    expected = {
        "spec": spec,
        "files_passed": 1,
        "files_collected": 1,
        "tests_passed": count,
        "tests_collected": count,
    }
    assert receipt == expected, (
        f"library-fix vitest receipt mismatch: expected={expected!r}, observed={receipt!r}"
    )

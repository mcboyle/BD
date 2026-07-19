#!/usr/bin/env python3
"""RED-first tests for decomp_lint -- the playbook's two transforms + the lazy-cycle rule.

Sandbox-invisible structural mistakes a moved module can introduce:
  - a MODULE-LEVEL import of another monolith (app/runner/dev_suite/deep_detect) ->
    breaks the lazy-held inter-monolith cycle at load,
  - a stray __file__ (path math must live in one _common helper),
  - a depth-suspect Path(__file__).parents[N] (the .parents[2] vs [1] slip).
AST-only; synthetic sources; runner-safe."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "tools"))
import decomp_lint as dl  # noqa: E402

DIRTY = '''
import os
from bulk_downloader import runner          # module-level monolith import (cycle trap)
from pathlib import Path

_ROOT = Path(__file__).parents[1]            # depth-suspect AND stray __file__

def handler():
    with open(__file__) as fh:               # stray __file__
        return fh.read()
'''

CLEAN = '''
import os
from pathlib import Path

def handler():
    from bulk_downloader import runner       # LAZY (in-function) -- allowed
    return runner.do()
'''


def _cats(src):
    return {c for (c, ln, msg) in dl.lint_source(src)}


def test_dirty_flags_all_three():
    cats = _cats(DIRTY)
    assert "MODULE_LEVEL_MONOLITH_IMPORT" in cats, cats
    assert "STRAY_FILE" in cats, cats
    assert "DEPTH_SUSPECT" in cats, cats


def test_clean_module_is_silent():
    findings = dl.lint_source(CLEAN)
    assert findings == [], findings


def test_non_monolith_sibling_import_not_flagged():
    # importing a NON-monolith sibling at module level is fine (app_common etc.)
    src = "from bulk_downloader.app_common import _check_csrf\n"
    assert _cats(src) == set(), _cats(src)


def test_module_level_import_bulk_downloader_runner_dotted():
    src = "import bulk_downloader.runner\n"
    assert "MODULE_LEVEL_MONOLITH_IMPORT" in _cats(src), _cats(src)


def test_relative_sibling_monolith_import_flagged():
    src = "from . import deep_detect\n"
    assert "MODULE_LEVEL_MONOLITH_IMPORT" in _cats(src), _cats(src)


def _run():
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    fails = 0
    for fn in fns:
        try:
            fn()
            print(f"  PASS {fn.__name__}")
        except Exception as e:
            fails += 1
            print(f"  FAIL {fn.__name__}: {e!r}")
    print(f"{len(fns) - fails}/{len(fns)} passed")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(_run())

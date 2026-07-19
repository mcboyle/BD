#!/usr/bin/env python3
"""RED-first tests for decomp_deletions -- the overlay-can't-delete (.py->package) guard.

`unzip -o` adds/replaces but never deletes, so a .py->package cut leaves the old
`X.py` shadowing the new `X/` package at import. This tool must surface that.
Runner-safe: zero-arg fns; synthetic fixtures only (no live tree needed)."""
import os
import sys
import shutil
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "tools"))
import decomp_deletions as dd  # noqa: E402


def test_py_to_package_conversion_flagged():
    old = {"bulk_downloader/dev_suite.py", "bulk_downloader/other.py", "bulk_downloader/app.py"}
    new = {"bulk_downloader/dev_suite/__init__.py", "bulk_downloader/dev_suite/core.py",
           "bulk_downloader/other.py", "bulk_downloader/app.py"}
    rep = dd.compute(old, new)
    assert "bulk_downloader/dev_suite.py" in rep["deletions"], rep
    assert ("bulk_downloader/dev_suite.py", "bulk_downloader/dev_suite/") in rep["conversions"], rep
    assert "rm bulk_downloader/dev_suite.py" in rep["rm_lines"], rep
    assert "bulk_downloader/other.py" not in rep["deletions"]
    assert "bulk_downloader/app.py" not in rep["deletions"]


def test_noop_when_identical():
    s = {"a/b.py", "a/c.py"}
    rep = dd.compute(s, set(s))
    assert rep["deletions"] == [] and rep["conversions"] == []


def test_plain_deletion_without_conversion():
    old = {"x/gone.py", "x/stay.py"}
    new = {"x/stay.py"}
    rep = dd.compute(old, new)
    assert rep["deletions"] == ["x/gone.py"], rep
    assert rep["conversions"] == []  # no x/gone/ package appeared


def test_dir_listing_roundtrip():
    d = tempfile.mkdtemp()
    try:
        os.makedirs(os.path.join(d, "pkg", "sub"))
        open(os.path.join(d, "pkg", "a.py"), "w").close()
        open(os.path.join(d, "pkg", "sub", "b.py"), "w").close()
        paths = dd.list_paths(d)
        assert "pkg/a.py" in paths and "pkg/sub/b.py" in paths, paths
    finally:
        shutil.rmtree(d)


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

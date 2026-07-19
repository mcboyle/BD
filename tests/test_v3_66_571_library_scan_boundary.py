"""F-CBD03-03 -- library scanner missing-pass must scope on a path BOUNDARY.

bulk_downloader/library.py::_scan_worker's second pass marks library rows
file_exists=0 when their path wasn't seen this scan, but only "for files under
our scanned roots" -- implemented at :822 as
    if not any(fp.startswith(root) for root in roots): continue
`str.startswith` is a raw prefix test, so with a configured root '/data/vids'
an unrelated sibling '/data/vids-backup/movie.mp4' matches and its rows get
swept into scope (wrongly invalidated by a scan that never covered them).

The fix adds a module-level boundary helper `_fp_under_root(fp, root)` (fp == root
or fp.startswith(root + os.sep), root trailing-sep-normalised) wired at :822.

RED on pristine 3.66.570 (helper absent -> ImportError; raw prefix still in
source); GREEN after. Pure import + source read -- run_tests.py-safe (zero-arg
tests, no caplog/tmp_path/monkeypatch fixtures, no subprocess).
"""

import os


def test_fp_under_root_boundary_semantics():
    """The helper treats sibling dirs sharing a name prefix as OUT of scope,
    while real descendants and the root itself are IN scope."""
    from bulk_downloader.library import _fp_under_root

    root = os.path.join(os.sep + "data", "vids")          # /data/vids
    sibling = os.path.join(os.sep + "data", "vids-backup", "movie.mp4")
    descendant = os.path.join(root, "movie.mp4")           # /data/vids/movie.mp4
    nested = os.path.join(root, "sub", "clip.mp4")
    other = os.path.join(os.sep + "data", "audio", "x.mp3")

    # the core defect: sibling sharing the 'vids' prefix must NOT be under root
    assert _fp_under_root(sibling, root) is False, (
        f"{sibling!r} must NOT count as under {root!r} (prefix-sibling)")
    assert _fp_under_root(other, root) is False

    # real membership still holds
    assert _fp_under_root(descendant, root) is True
    assert _fp_under_root(nested, root) is True
    assert _fp_under_root(root, root) is True, "root itself is in scope"


def test_fp_under_root_trailing_separator_normalised():
    """A root passed with a trailing separator behaves identically."""
    from bulk_downloader.library import _fp_under_root
    root = os.path.join(os.sep + "data", "vids")
    root_slash = root + os.sep
    assert _fp_under_root(os.path.join(root, "m.mp4"), root_slash) is True
    assert _fp_under_root(os.path.join(os.sep + "data", "vids-x", "m.mp4"),
                          root_slash) is False


def test_missing_pass_scope_uses_boundary_not_raw_prefix():
    """Source backstop: the raw `fp.startswith(root)` scope predicate is gone and
    the boundary helper is wired into the missing-pass scan loop."""
    import bulk_downloader.library as lib
    src = open(lib.__file__, encoding="utf-8").read()
    assert "fp.startswith(root)" not in src, (
        "missing-pass still uses the raw string-prefix scope test "
        "`fp.startswith(root)` (sibling dirs leak into scope)")
    assert "_fp_under_root(" in src, (
        "the boundary helper _fp_under_root should be wired into the scope check")


if __name__ == "__main__":
    for fn in sorted(k for k in dict(globals()) if k.startswith("test_")):
        globals()[fn]()
        print("PASS", fn)

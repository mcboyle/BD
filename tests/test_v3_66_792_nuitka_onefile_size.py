"""MOD-7 cut 2 fix (v3.66.792) -- measure_size must size the onefile ARTIFACT.

nuitka_eval.measure_size rolled up the WHOLE dist_dir. For a --standalone .dist
that is right (the directory IS the shippable artifact). For a --onefile build,
Nuitka keeps its intermediate trees (<name>.build / .onefile-build / <name>.dist)
INSIDE the output dir alongside the real <name>.bin -- and the shippable artifact
is just that single .bin. Rolling up the whole dir over-reported ~18x: a witnessed
onefile whose .bin was 117.6 MB measured as 2.2 GB, which read as a catastrophic
'retain' when the true single-file build actually BEAT pyinstaller.

This is a denominator-too-broad defect (the mirror of the too-narrow ones): the
size check counted things that are not the artifact. The fix: detect a onefile
output dir (top-level .bin + build-leftover subdirs) and size only the .bin;
keep the whole-dir rollup for a standalone .dist.
"""
import os


def _load():
    from importlib import import_module
    return import_module("tools.nuitka_eval")


def _make_onefile_output(root):
    """A synthetic Nuitka --onefile OUTPUT dir: the shippable .bin at top level
    plus the build-leftover trees Nuitka keeps."""
    os.makedirs(root, exist_ok=True)
    binp = os.path.join(root, "downloader_ui.bin")
    with open(binp, "wb") as f:
        f.write(b"\x7fELF" + b"\0" * (5_000_000 - 4))   # ~5 MB artifact
    # leftovers that MUST NOT be counted
    for sub, size in (("downloader_ui.build", 30_000_000),
                      ("downloader_ui.onefile-build", 10_000_000),
                      ("downloader_ui.dist", 90_000_000)):
        d = os.path.join(root, sub)
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, "junk.o"), "wb") as f:
            f.write(b"\0" * size)
    return binp


def _make_standalone_dist(root):
    """A synthetic Nuitka --standalone .dist dir: the .bin PLUS its runtime
    siblings; the WHOLE dir is the artifact, no *.build subdir."""
    os.makedirs(root, exist_ok=True)
    with open(os.path.join(root, "downloader_ui.bin"), "wb") as f:
        f.write(b"\0" * 2_000_000)
    with open(os.path.join(root, "libpython3.12.so"), "wb") as f:
        f.write(b"\0" * 8_000_000)   # runtime sibling -- part of the artifact
    os.makedirs(os.path.join(root, "bulk_downloader"), exist_ok=True)
    with open(os.path.join(root, "bulk_downloader", "app.pyc"), "wb") as f:
        f.write(b"\0" * 1_000_000)


def test_onefile_measures_the_bin_not_the_leftovers(tmp_path):
    """The one that fails on the shipped bug: measure_size over a onefile output
    dir must return the .bin size (~5 MB), NOT the dir total (~135 MB)."""
    ne = _load()
    out = os.path.join(str(tmp_path), "dist_nuitka_onefile")
    binp = _make_onefile_output(out)
    bin_size = os.path.getsize(binp)
    dir_total = sum(os.path.getsize(os.path.join(dp, f))
                    for dp, _, fns in os.walk(out) for f in fns)
    got = ne.measure_size(out)
    # the artifact is the .bin, not the dir full of build leftovers
    assert got == bin_size, (
        "measure_size counted build leftovers: got %s, .bin is %s, whole dir is %s"
        % (got, bin_size, dir_total))
    # and it must be dramatically smaller than the naive dir rollup
    assert got < dir_total / 5


def test_standalone_dist_still_rolls_up_the_whole_dir(tmp_path):
    """The fix must NOT regress the standalone case: a .dist dir IS the artifact,
    so its size is the whole-dir rollup (bin + runtime siblings)."""
    ne = _load()
    dist = os.path.join(str(tmp_path), "downloader_ui.dist")
    _make_standalone_dist(dist)
    expected = sum(os.path.getsize(os.path.join(dp, f))
                   for dp, _, fns in os.walk(dist) for f in fns)
    got = ne.measure_size(dist)
    assert got == expected, f"standalone rollup changed: got {got}, expected {expected}"
    assert got > 10_000_000   # bin + libpython + pyc, all counted


def test_plain_dir_without_bin_still_rolls_up(tmp_path):
    """A directory with no .bin and no build-leftovers (the generic case the
    original test exercised) still rolls up -- the onefile branch must not
    swallow it."""
    ne = _load()
    d = os.path.join(str(tmp_path), "plain")
    os.makedirs(d)
    with open(os.path.join(d, "a"), "wb") as f:
        f.write(b"x" * 1000)
    assert ne.measure_size(d) == 1000


def test_missing_dir_is_still_unknown(tmp_path):
    ne = _load()
    assert ne.measure_size(os.path.join(str(tmp_path), "nope")) is None


def test_is_onefile_output_discriminates(tmp_path):
    """The discriminator itself: a onefile output dir (bin + *.build) is onefile;
    a standalone .dist (bin + .so, no *.build) is not."""
    ne = _load()
    of = os.path.join(str(tmp_path), "of")
    _make_onefile_output(of)
    sd = os.path.join(str(tmp_path), "downloader_ui.dist")
    _make_standalone_dist(sd)
    assert ne._is_onefile_output(of) is True
    assert ne._is_onefile_output(sd) is False

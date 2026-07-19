"""v3.66.464 -- scandir sweep (growth-prevention): route the remaining full-tree
rglob("*") walks in cockpit_core through one shared os.scandir helper.

Live data (build 3.66.462) showed smart_search / release_readiness /
next_best_action at 0.16-0.23s -- cheap NOW because the corpus is tiny
(sites_loaded=0). But all three do a full-tree `for f in root.rglob("*")` walk
that scales with artifact volume. Unlike the 462 warehouse fix (whose win was
removing an O(N log N) sort), these use PLAIN rglob -- so the lever here is
different: rglob materializes a Path object per entry (including every directory)
and re-stats for is_dir; os.scandir reuses the cached DirEntry.is_dir from
readdir and builds a Path only for files. Measured ~2.2x faster on a dir-heavy
tree (1500 dirs x 4 files). So this is PREVENTION (protects against growth), not
a current-live fix.

_scandir_files(root) yields exactly the same FILE set as
(f for f in root.rglob("*") if f.is_file()), just without the dir-Path churn,
so the three call sites become a drop-in: `for f in _scandir_files(root):` with
the per-call `f.is_file()` guard removed (the helper yields only files).

RED on 463 (call sites still use rglob; helper absent) -> GREEN. Runs under
run_tests.py + real pytest.
"""
import inspect
import os
import sys
import tempfile
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

_SWEPT = ("smart_search", "release_readiness", "next_best_action")


def _code_only(fn) -> str:
    """Source of fn with comments + docstring stripped, so structural assertions
    match real CODE, not prose."""
    src = inspect.getsource(fn)
    out = []
    in_doc = False
    doc_q = None
    for raw in src.splitlines():
        line = raw
        # crude docstring strip (triple-quote toggles)
        stripped = line.strip()
        if not in_doc and (stripped.startswith('"""') or stripped.startswith("'''")):
            q = stripped[:3]
            if stripped.count(q) >= 2 and len(stripped) > 3:
                continue  # single-line docstring
            in_doc = True
            doc_q = q
            continue
        if in_doc:
            if doc_q in line:
                in_doc = False
            continue
        code = line.split("#", 1)[0]
        out.append(code)
    return "\n".join(out)


def test_swept_functions_use_scandir_helper_not_rglob():
    """Each swept function must route its full-tree walk through _scandir_files
    and contain no rglob in CODE. RED on 463 (still rglob) -> GREEN."""
    from tools import cockpit_core as cc

    for name in _SWEPT:
        fn = getattr(cc, name)
        code = _code_only(fn)
        assert "_scandir_files(" in code, (
            "%s must walk via _scandir_files (the shared scandir helper)" % name
        )
        assert "rglob(" not in code, (
            "%s still uses rglob in code -- route it through _scandir_files" % name
        )


def test_scandir_files_matches_rglob_fileset():
    """_scandir_files must yield EXACTLY the regular-file set that
    (rglob('*') + is_file) would, on a seeded nested tree -- faithful drop-in.
    RED on 463 (helper absent -> AttributeError) -> GREEN."""
    from tools import cockpit_core as cc

    tmp = Path(tempfile.mkdtemp(prefix="t464_"))
    made = set()
    for d in ("", "a", "a/b", "a/b/c", "x/y"):
        sub = tmp / d if d else tmp
        sub.mkdir(parents=True, exist_ok=True)
        for fn in ("f.md", "g.json", "h.txt", "skip.bin"):
            p = sub / fn
            p.write_text("x")
            made.add(p.resolve())
    (tmp / "emptydir").mkdir()  # dir with no files -> must not appear

    ref = {f.resolve() for f in tmp.rglob("*") if f.is_file()}
    got = {p.resolve() for p in cc._scandir_files(tmp)}
    assert got == ref, "scandir fileset != rglob fileset (diff=%s)" % (
        (got ^ ref))
    assert got == made, "scandir yielded unexpected/missing files"
    # only files -- no directory ever yielded
    assert all(p.is_file() for p in cc._scandir_files(tmp))


def test_scandir_helper_faster_than_rglob():
    """On a dir-heavy tree the helper must beat plain rglob (the prevention
    premise). Same-process ratio; gate generous to stay CI-stable."""
    import time
    from tools import cockpit_core as cc

    tmp = Path(tempfile.mkdtemp(prefix="t464p_"))
    for i in range(700):
        s = tmp / ("t_%06x" % i) / "out"
        s.mkdir(parents=True)
        for fn in ("a.json", "b.md", "c.txt", "d.log"):
            (s / fn).write_text("x")

    def via_rglob():
        return sum(1 for f in tmp.rglob("*")
                   if f.is_file() and f.suffix in (".md", ".json", ".txt"))

    def via_scandir():
        return sum(1 for f in cc._scandir_files(tmp)
                   if f.suffix in (".md", ".json", ".txt"))

    assert via_rglob() == via_scandir()

    def best(fn, reps=5):
        b = 1e9
        for _ in range(reps):
            t = time.perf_counter()
            fn()
            b = min(b, time.perf_counter() - t)
        return b

    r = best(via_rglob)
    s = best(via_scandir)
    assert s < r * 0.85, (
        "scandir helper not meaningfully faster than rglob "
        "(rglob=%.4fs scandir=%.4fs ratio=%.2f) -- prevention premise fails" % (
            r, s, s / r))


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print("ok", name)

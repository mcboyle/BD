"""v3.66.461 -- cockpit bugfix slice (RED-first).

Three live-stash bugs found in the v3.66.460 cockpit sweep, fixed together:

  BUG-1 (High)  bg_scheduler module-level init passes ``capture_enqueue_fn=
                _capture_enqueue`` at app.py:~1659, but ``_capture_enqueue`` is
                ``def``'d ~1500 lines LATER (~3152). At module-load time the name
                is unbound -> NameError -> swallowed by the init ``except`` ->
                "bg_scheduler init failed: name '_capture_enqueue' is not
                defined" every boot, so the WHOLE scheduler never starts. The
                sibling args dodge this by being deferring lambdas; only this one
                is a bare forward reference. Invisible to the test suite because
                the whole block is gated on BD_DISABLE_KEEPALIVE (=1 in tests).
                Fix: wrap the reference in a deferring lambda like its siblings.

  BUG-2 (Med-H) cockpit_core.artifact_warehouse() makes THREE stat syscalls per
                file (Path.is_file() + .stat().st_size + .stat().st_mtime) over
                an unbounded rglob of three roots -> ~10s on a big live tree.
                Fix: one stat() per entry, file-ness via stat.S_ISREG on the
                already-fetched mode. Output contract preserved exactly.

  BUG-3 (Med)   cockpit SPA go()/mountTab() do ``host.innerHTML = await
                PAGES[x]()`` with NO "am I still active?" guard between the await
                and the write, so a slow prior tab resolves late and paints under
                the new tab's header. Fix: a monotonic __navGen token captured at
                entry and re-checked after the await before the innerHTML write.

These tests are RED on pristine 460 and GREEN after the fix. They are written
zero-arg (run_tests.py-compatible) and also pass under real pytest.
"""
import ast
import os
import sys
import tempfile
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


# ---------------------------------------------------------------------------
# BUG-1 -- the bg_scheduler capture_enqueue_fn forward-reference
# ---------------------------------------------------------------------------
def _find_register_default_tasks_call(tree):
    """Return the Call node for ``*.register_default_tasks(...)`` in app.py."""
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            f = node.func
            if isinstance(f, ast.Attribute) and f.attr == "register_default_tasks":
                return node
    return None


def test_bug1_capture_enqueue_is_deferred_not_forward_ref():
    """The module-level bg_scheduler wiring must NOT pass _capture_enqueue as a
    bare Name (a forward reference resolved at load time, before its def). It
    must be deferred (a lambda), so the name resolves at CALL time when the
    module is fully loaded. RED on pristine (bare Name) -> GREEN (Lambda)."""
    src = (_ROOT / "bulk_downloader" / "app.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    call = _find_register_default_tasks_call(tree)
    assert call is not None, "register_default_tasks call not found in app.py"
    kw = next((k for k in call.keywords if k.arg == "capture_enqueue_fn"), None)
    assert kw is not None, "capture_enqueue_fn keyword not found"
    # The bug: kw.value is ast.Name(id='_capture_enqueue') -- a forward ref.
    assert not isinstance(kw.value, ast.Name), (
        "capture_enqueue_fn is a bare Name (forward reference) -- this is the "
        "BUG-1 NameError at module load; wrap it in a deferring lambda."
    )
    assert isinstance(kw.value, ast.Lambda), (
        "capture_enqueue_fn should be a deferring lambda so _capture_enqueue "
        "resolves at call time, not load time."
    )


def test_bug1_capture_enqueue_lambda_forwards_correctly():
    """The deferring lambda must forward to _capture_enqueue with the same
    (site_id, urls) shape bg_scheduler calls enqueue_fn with."""
    src = (_ROOT / "bulk_downloader" / "app.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    call = _find_register_default_tasks_call(tree)
    kw = next((k for k in call.keywords if k.arg == "capture_enqueue_fn"), None)
    assert isinstance(kw.value, ast.Lambda)
    # body must call _capture_enqueue
    names = {
        n.id for n in ast.walk(kw.value.body)
        if isinstance(n, ast.Name)
    }
    assert "_capture_enqueue" in names, (
        "the lambda must forward to _capture_enqueue"
    )


# ---------------------------------------------------------------------------
# BUG-2 -- artifact_warehouse single-stat-per-file
# ---------------------------------------------------------------------------
def _run_warehouse_with_seed(nfiles):
    """Seed nfiles under each root, count Path.stat calls during a warehouse
    scan, return (result, stat_count, expected_names)."""
    from tools import cockpit_core as cc

    saved = {k: os.environ.get(k) for k in
             ("BD_CAPTURES_ROOT", "BD_FRAMEWORK_REPORTS", "BD_COCKPIT_TASKS")}
    tmp = tempfile.mkdtemp(prefix="bd_warehouse_")
    orig_stat = Path.stat
    counter = {"n": 0}

    def _counting_stat(self, *a, **k):
        counter["n"] += 1
        return orig_stat(self, *a, **k)

    try:
        cap = Path(tmp) / "cap"
        rep = Path(tmp) / "rep"
        tsk = Path(tmp) / "tsk"
        for d in (cap, rep, tsk):
            d.mkdir(parents=True, exist_ok=True)
        os.environ["BD_CAPTURES_ROOT"] = str(cap)
        os.environ["BD_FRAMEWORK_REPORTS"] = str(rep)
        os.environ["BD_COCKPIT_TASKS"] = str(tsk)
        expected = []
        for i in range(nfiles):
            (cap / f"clip{i}.wacz").write_bytes(b"PK\x03\x04stub")
            expected.append(f"clip{i}.wacz")
            (rep / f"report{i}.md").write_text("# r", encoding="utf-8")
            expected.append(f"report{i}.md")
        total_files = nfiles * 2  # cap + rep (tsk empty)
        Path.stat = _counting_stat
        try:
            result = cc.artifact_warehouse()
        finally:
            Path.stat = orig_stat
        return result, counter["n"], expected, total_files
    finally:
        Path.stat = orig_stat
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def test_bug2_warehouse_one_stat_per_file():
    """The warehouse must stat each file at most ~once. Measured as the per-file
    slope (stats scale linearly; framework overhead from resolve/is_dir/rglob is
    a constant that cancels in the delta). RED on pristine (~3 stats/file:
    is_file + st_size + st_mtime) -> GREEN (~1/file). Threshold 1.5."""
    _r1, n1, _e1, files1 = _run_warehouse_with_seed(5)    # 10 files
    _r2, n2, _e2, files2 = _run_warehouse_with_seed(15)   # 30 files
    slope = (n2 - n1) / (files2 - files1)
    assert slope <= 1.5, (
        f"warehouse stats ~{slope:.2f}/file (>1.5 means redundant per-file "
        f"stats -- the BUG-2 slowness; pristine is ~3/file)."
    )


def test_bug2_warehouse_output_contract_preserved():
    """The fix must not change the output: every seeded file appears, with the
    name/path/root/size/mtime fields, bucketed correctly."""
    result, _stat_count, expected, _tot = _run_warehouse_with_seed(3)
    assert "categories" in result and "_note" in result
    cats = result["categories"]
    names = [f["name"] for v in cats.values() for f in v]
    for e in expected:
        assert e in names, f"{e} missing from warehouse output"
    # field shape intact
    for v in cats.values():
        for f in v:
            assert set(f) >= {"name", "path", "root", "size", "mtime"}
    # .wacz -> Captures, .md -> Reports
    assert any(f["name"].endswith(".wacz") for f in cats.get("Captures", []))
    assert any(f["name"].endswith(".md") for f in cats.get("Reports", []))


# ---------------------------------------------------------------------------
# BUG-3 -- cockpit tab-switch stale-render guard (source-scan)
# ---------------------------------------------------------------------------
def _slice_js_function(src, header):
    """Return the brace-balanced body text of a JS function starting at
    ``header`` (e.g. 'async function go(p, deeplink){')."""
    i = src.find(header)
    if i < 0:
        return ""
    j = i + len(header)
    depth = 1
    while j < len(src) and depth:
        c = src[j]
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
        j += 1
    return src[i:j]


def test_bug3_navgen_token_declared():
    """A monotonic nav-generation token must exist to drop stale renders."""
    src = (_ROOT / "tools" / "cockpit_console.py").read_text(encoding="utf-8")
    assert "__navGen" in src, (
        "no __navGen nav-generation token -- BUG-3 stale tab-render guard absent"
    )


def test_bug3_go_guards_stale_render():
    """go() must capture the gen at entry and re-check it after the await,
    before writing #main, so a superseded slow page can't overwrite."""
    src = (_ROOT / "tools" / "cockpit_console.py").read_text(encoding="utf-8")
    body = _slice_js_function(src, "async function go(p, deeplink){")
    assert body, "go() not found"
    assert "++__navGen" in body, "go() must capture a new nav generation at entry"
    assert "__navGen" in body and "return" in body, (
        "go() must re-check the nav generation after the await and bail if stale"
    )
    # the guard must sit on the awaited render, i.e. reference __navGen after
    # the PAGES await
    after_await = body.split("await PAGES[p]()", 1)
    assert len(after_await) == 2, "go() await on PAGES[p]() not found"
    assert "__navGen" in after_await[1], (
        "the stale-guard check must come AFTER the PAGES[p]() await"
    )


def test_bug3_mounttab_guards_stale_render():
    """mountTab() has the same await-then-write race and needs the same guard."""
    src = (_ROOT / "tools" / "cockpit_console.py").read_text(encoding="utf-8")
    body = _slice_js_function(src, "async function mountTab(group,sub){")
    assert body, "mountTab() not found"
    assert "++__navGen" in body, "mountTab() must capture a new nav generation"
    after_await = body.split("await PAGES[sub]()", 1)
    assert len(after_await) == 2, "mountTab() await on PAGES[sub]() not found"
    assert "__navGen" in after_await[1], (
        "mountTab() must re-check the nav generation after the await"
    )


# Allow direct execution under run_tests.py (zero-arg discovery) and pytest.
if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print("ok", name)

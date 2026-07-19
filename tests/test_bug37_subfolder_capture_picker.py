"""BUG-3/7 -- DOM-analyzer picker/loader can't see or resolve captures in
subfolders (onboarding/guided captures land under
captures/template_onboarding/<host>_<ts>/). The picker enumerated with the
NON-recursive list_captures and resolved by bare basename, so subfolder captures
were invisible ("No captures found") and unloadable, even though the recursive
scan_captures lists them.

Fix: picker endpoint -> scan_captures (rel_path token); loader/tree resolve via a
rel_path-or-basename router so a subfolder capture (rel_path with "/") resolves.

Resolution-level test with an injected root (no valid WACZ body needed -- we only
assert resolution SUCCEEDS, i.e. we get past the "unknown capture" gate).
"""
import os
import tempfile
from pathlib import Path

os.environ.setdefault("BD_HOME", tempfile.mkdtemp())
os.environ.setdefault("BD_DISABLE_KEEPALIVE", "1")

from bulk_downloader import dom_analyzer as da


def _mk_root():
    root = Path(tempfile.mkdtemp(prefix="bug37_"))
    # a flat (top-level) capture + a subfolder (onboarding) capture
    flat = root / "captures"
    flat.mkdir(parents=True, exist_ok=True)
    (flat / "flat_host_0.wacz").write_bytes(b"PK\x03\x04placeholder")
    sub = flat / "template_onboarding" / "sub.host.com_abcd1234_ts"
    sub.mkdir(parents=True, exist_ok=True)
    (sub / "sub_host_0.wacz").write_bytes(b"PK\x03\x04placeholder")
    return root


def test_subfolder_capture_is_enumerated_by_scan():
    root = _mk_root()
    names = {c["name"] for c in da.scan_captures(root=root)}
    assert "sub_host_0.wacz" in names, f"scan must list subfolder capture; got {names}"


def test_old_flat_picker_misses_subfolder_capture():
    # documents the bug: list_captures (the old picker source) is non-recursive
    root = _mk_root()
    names = {c["name"] for c in da.list_captures(root=root)}
    assert "sub_host_0.wacz" not in names, "list_captures unexpectedly recursive?"
    assert "flat_host_0.wacz" in names, "list_captures should still see flat captures"


def test_subfolder_capture_resolves_by_rel_path_token():
    root = _mk_root()
    rel = next(c["rel_path"] for c in da.scan_captures(root=root)
               if c["name"] == "sub_host_0.wacz")
    # the loader path must resolve this rel_path token (BUG-3/7 fix)
    assert da._resolve_capture_any(rel, root=root) is not None, \
        f"subfolder rel_path {rel!r} must resolve"


def test_flat_basename_token_still_resolves():
    # backward compat: a bare basename (legacy picker) still resolves a flat capture
    root = _mk_root()
    assert da._resolve_capture_any("flat_host_0.wacz", root=root) is not None, \
        "flat basename must still resolve (backward compat)"


def test_subfolder_capture_resolves_by_bare_basename():
    # BUG-3/7 end-to-end WITHOUT a FE rebuild: an un-rebuilt picker sends the bare
    # basename; the recursive fallback must resolve a UNIQUE subfolder basename.
    root = _mk_root()
    assert da._resolve_capture_any("sub_host_0.wacz", root=root) is not None, \
        "unique subfolder basename must resolve via recursive fallback"


def test_ambiguous_basename_does_not_resolve():
    # safety: a basename present in TWO subfolders must NOT resolve (no wrong-file)
    root = _mk_root()
    dup = root / "captures" / "template_onboarding" / "other.host_9999_ts"
    dup.mkdir(parents=True, exist_ok=True)
    (dup / "sub_host_0.wacz").write_bytes(b"PK\x03\x04placeholder")  # same basename
    assert da._resolve_capture_any("sub_host_0.wacz", root=root) is None, \
        "ambiguous basename must refuse to resolve (avoid wrong-file)"


if __name__ == "__main__":
    import traceback
    for n in [k for k in sorted(dict(globals())) if k.startswith("test_")]:
        try:
            globals()[n](); print(f"PASS  {n}")
        except AssertionError as e:
            print(f"FAIL  {n}: {e}")
        except Exception as e:
            print(f"ERROR {n}: {type(e).__name__}: {e}")

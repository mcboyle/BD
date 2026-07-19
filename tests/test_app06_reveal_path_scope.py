"""F-APP06-01 -- /api/file/reveal accepts an arbitrary absolute path when
path_allowlist is empty (the legacy-permissive default), launching the host
file manager at it.

_validate_path keeps empty-allowlist == permissive for backward compat (many
callers: cookie_file, download_dir), so the fix is scoped to reveal: a new
_validate_reveal_path requires the path to be within a reveal-safe root -- the
configured path_allowlist if set, else BD's download roots (BD_HOME +
~/Downloads/bulk_downloader + configured download_dirs). The reveal action can
never open an arbitrary absolute path even when the global allowlist is empty.
"""
import os
import tempfile
import inspect

os.environ.setdefault("BD_HOME", tempfile.mkdtemp())
os.environ.setdefault("BD_DISABLE_KEEPALIVE", "1")


def test_reveal_rejects_arbitrary_path_with_empty_allowlist():
    import bulk_downloader.app as app
    orig = app._app_cfg.get("path_allowlist")
    app._app_cfg["path_allowlist"] = []          # legacy-permissive default
    try:
        ok, _ = app._validate_reveal_path("/etc/shadow")
        assert ok is False, "arbitrary abs path must be rejected with empty allowlist"
        ok2, _ = app._validate_reveal_path("/nonexistent_root_xyz/file")
        assert ok2 is False
    finally:
        app._app_cfg["path_allowlist"] = orig if orig is not None else []


def test_reveal_allows_path_under_download_root():
    import bulk_downloader.app as app
    bd_home = os.environ["BD_HOME"]
    target = os.path.join(bd_home, "sub", "file.mp4")   # under a safe default root
    orig = app._app_cfg.get("path_allowlist")
    app._app_cfg["path_allowlist"] = []
    try:
        ok, msg = app._validate_reveal_path(target)
        assert ok is True, f"path under BD_HOME must be allowed: {msg}"
    finally:
        app._app_cfg["path_allowlist"] = orig if orig is not None else []


def test_reveal_honors_configured_allowlist():
    import bulk_downloader.app as app
    orig = app._app_cfg.get("path_allowlist")
    root = tempfile.mkdtemp()
    app._app_cfg["path_allowlist"] = [root]
    try:
        ok, _ = app._validate_reveal_path(os.path.join(root, "x.mp4"))
        assert ok is True, "path under a configured allowlist root must be allowed"
        ok2, _ = app._validate_reveal_path("/etc/shadow")
        assert ok2 is False, "path outside the configured allowlist must be rejected"
    finally:
        app._app_cfg["path_allowlist"] = orig if orig is not None else []


def test_reveal_route_uses_reveal_validator():
    import bulk_downloader.app_file as af
    assert "_validate_reveal_path" in inspect.getsource(af), \
        "reveal route must use the reveal-scoped validator"


if __name__ == "__main__":
    import traceback
    for n in [k for k in sorted(dict(globals())) if k.startswith("test_")]:
        try:
            globals()[n](); print(f"PASS  {n}")
        except AssertionError as e:
            print(f"FAIL  {n}: {e}")
        except Exception:
            print(f"ERROR {n}"); traceback.print_exc()

"""test_v3_66_353_dead_modules_removed.py — end state of the cleanup cut.

app_actions_center (blueprint emptied @344, registered 0 routes) and
app_monitoring (fully unregistered @345, empty blueprint) were dead code kept
only because an overlay deploy cannot delete files. This cut removes them from
the source tree (a physical `rm` follows on the deployed host). These assert the
removed end state — the same "module absent is the real invariant" pattern used
when the legacy templates dir was retired in v3.66.339.

RED on the pristine 352 tree (the modules still exist) → GREEN after the cut.
run_tests.py conventions: zero-arg test_* functions, plain asserts.
"""
import importlib.util
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
_BD = _REPO / "bulk_downloader"


def test_app_actions_center_file_gone():
    assert not (_BD / "app_actions_center.py").exists(), \
        "bulk_downloader/app_actions_center.py should be removed"


def test_app_monitoring_file_gone():
    assert not (_BD / "app_monitoring.py").exists(), \
        "bulk_downloader/app_monitoring.py should be removed"


def test_modules_not_importable():
    assert importlib.util.find_spec("bulk_downloader.app_actions_center") is None
    assert importlib.util.find_spec("bulk_downloader.app_monitoring") is None


def test_app_py_no_longer_wires_actions_center():
    src = (_BD / "app.py").read_text(encoding="utf-8")
    # the import in the backlog-dashboard tuple and the register call must be
    # gone (a historical mention in an explanatory comment is fine).
    assert "app_report_center, app_actions_center" not in src, \
        "app.py must not import app_actions_center in the dashboard tuple"
    assert "app_actions_center.register_routes" not in src, \
        "app.py must not register app_actions_center anymore"
    assert "app_monitoring.register_routes" not in src, \
        "app.py must not register app_monitoring anymore"


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items())
           if k.startswith("test_") and callable(v)]
    passed = 0
    for fn in fns:
        fn()
        passed += 1
        print(f"ok  {fn.__name__}")
    print(f"\n{passed}/{len(fns)} passed")

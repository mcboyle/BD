"""v3.66.468 WS2: chromium_extensions launch knob.

A site config may carry ``chromium_extensions: [path, ...]`` -- a list of
unpacked extension directories. The launch path appends them as
``--disable-extensions-except=<csv>`` and ``--load-extension=<csv>`` (Chromium
requires both together, and both work only with a persistent context, which BD
uses). Absent / empty / non-list / non-existent entries are inert -- never a
crash, never a bare flag.

Runner-safe: zero-arg test fns, no pytest builtins, paths from __file__,
tempfile.mkdtemp.
"""
import sys
import tempfile
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from bulk_downloader.runner_browser import BrowserMixin  # noqa: E402


class _Stub(BrowserMixin):
    def __init__(self, config):
        self.config = config
        self.site_id = "demo"


def _args(config, headless=True):
    return _Stub(config)._launch_args(headless=headless)


def test_no_extensions_key_unchanged():
    args = _args({})
    assert not any("--load-extension" in a for a in args), args
    assert not any("--disable-extensions-except" in a for a in args), args


def test_extensions_appended_when_present():
    d1 = tempfile.mkdtemp()
    d2 = tempfile.mkdtemp()
    args = _args({"chromium_extensions": [d1, d2]})
    load = [a for a in args if a.startswith("--load-extension=")]
    excl = [a for a in args if a.startswith("--disable-extensions-except=")]
    assert len(load) == 1 and len(excl) == 1, args
    # both flags carry the same csv of existing dirs
    assert d1 in load[0] and d2 in load[0], load
    assert load[0].split("=", 1)[1] == excl[0].split("=", 1)[1], (load, excl)


def test_nonexistent_paths_filtered_out():
    good = tempfile.mkdtemp()
    args = _args({"chromium_extensions": [good, "/no/such/ext/dir"]})
    load = [a for a in args if a.startswith("--load-extension=")][0]
    assert good in load, load
    assert "/no/such/ext/dir" not in load, load


def test_all_invalid_emits_no_flag():
    args = _args({"chromium_extensions": ["/no/such/a", "/no/such/b"]})
    assert not any("--load-extension" in a for a in args), args
    assert not any("--disable-extensions-except" in a for a in args), args


def test_non_list_value_is_inert():
    for bad in (5, {"x": 1}):
        args = _args({"chromium_extensions": bad})
        assert not any("--load-extension" in a for a in args), (bad, args)


def test_csv_string_value_accepted():
    # the gui-safe text editor stores a comma-separated string; it must work
    # the same as a list (so editing the field can't dead-string the feature).
    d1 = tempfile.mkdtemp()
    d2 = tempfile.mkdtemp()
    args = _args({"chromium_extensions": f"{d1}, {d2}"})
    load = [a for a in args if a.startswith("--load-extension=")]
    assert len(load) == 1 and d1 in load[0] and d2 in load[0], args

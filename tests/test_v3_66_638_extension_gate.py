"""Cut v3.66.638 / INTEROP-GOV-1 gate: chromium-extension loading is gated on the
interop_registry when governance is enabled.

EXT-1's loader (runner_browser._launch_args) loads ``chromium_extensions`` dirs.
With ``interop_governance_enabled`` OFF (the default) that is unchanged
(backward-compatible). With it ON, a dir is loaded ONLY if the interop_registry
permits it (registered + risk-acknowledged + enabled) AND its live content hash
still matches the pinned provenance -- so an un-acked or silently-changed extension
is refused.

RED on pristine 3.66.637: _launch_args ignores interop_governance_enabled and loads
any configured extension dir ungated.
"""
import os
import sys
import tempfile
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))


def _mkext():
    d = tempfile.mkdtemp()
    with open(os.path.join(d, "manifest.json"), "w") as fh:
        fh.write('{"name":"demo","manifest_version":3}')
    return d


def _args(config):
    from bulk_downloader.runner_browser import BrowserMixin

    class _Stub(BrowserMixin):
        def __init__(self, cfg):
            self.config = cfg
            self.site_id = "demo"

    return _Stub(config)._launch_args(headless=True)


def _fresh_registry():
    os.environ["BD_HOME"] = tempfile.mkdtemp()
    import bulk_downloader.interop_registry as ir
    return ir


def _loads(args, d):
    return any(a.startswith("--load-extension=") and d in a for a in args)


def test_governance_off_loads_as_today():
    """Default (no toggle): extension loads -- backward compatible."""
    _fresh_registry()
    d = _mkext()
    assert _loads(_args({"chromium_extensions": [d]}), d)


def test_governance_on_blocks_unregistered_extension():
    _fresh_registry()
    d = _mkext()
    args = _args({"chromium_extensions": [d], "interop_governance_enabled": True})
    assert not any("--load-extension" in a for a in args), args


def test_governance_on_allows_permitted_extension():
    ir = _fresh_registry()
    d = _mkext()
    ir.register("chromium_extension", d, sha256=ir.dir_sha256(d))
    ir.acknowledge("chromium_extension", d)
    ir.set_enabled("chromium_extension", d, True)
    assert _loads(_args({"chromium_extensions": [d], "interop_governance_enabled": True}), d)


def test_governance_on_blocks_changed_extension():
    """Registered + acked + enabled, but the dir changed after registration ->
    the live-hash pin refuses it."""
    ir = _fresh_registry()
    d = _mkext()
    ir.register("chromium_extension", d, sha256=ir.dir_sha256(d))
    ir.acknowledge("chromium_extension", d)
    ir.set_enabled("chromium_extension", d, True)
    with open(os.path.join(d, "manifest.json"), "w") as fh:
        fh.write('{"name":"demo-TAMPERED","manifest_version":3}')   # changed after reg
    args = _args({"chromium_extensions": [d], "interop_governance_enabled": True})
    assert not any("--load-extension" in a for a in args), args


def test_governance_on_partial_ack_blocks():
    """Registered + enabled but NOT acknowledged -> blocked."""
    ir = _fresh_registry()
    d = _mkext()
    ir.register("chromium_extension", d, sha256=ir.dir_sha256(d))
    ir.set_enabled("chromium_extension", d, True)   # enabled but never acknowledged
    args = _args({"chromium_extensions": [d], "interop_governance_enabled": True})
    assert not any("--load-extension" in a for a in args), args

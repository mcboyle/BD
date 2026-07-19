"""v3.66.468 WS4a: the send-to-JDownloader native processor example.

Verifies the example is well-formed and self-gating WITHOUT a live JD: it loads
as a processor, is dormant when JD_SEND is unset, and -- with JD_SEND set and
jd_bridge.get_client_for_site monkeypatched to a fake client -- submits the
payload URL and returns the link id. A submit failure is raised (so the
processor machinery quarantines), not swallowed.

Runner-safe: zero-arg test fns, paths from __file__, module globals restored
in try/finally.
"""
import importlib.util
import os
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from bulk_downloader import plugins as P  # noqa: E402
from bulk_downloader import jd_bridge  # noqa: E402

_EXAMPLE = _REPO / "docs" / "plugin_examples" / "send_to_jdownloader.py"


def _load_example():
    spec = importlib.util.spec_from_file_location("bd_ex_jd", str(_EXAMPLE))
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


class _FakeClient:
    def __init__(self, *, fail=False):
        self._fail = fail
        self.calls = []

    def submit(self, url, cookies="", dest_dir=""):
        self.calls.append((url, cookies, dest_dir))
        if self._fail:
            raise RuntimeError("403 auth")
        return "LINK-123"


def test_example_registers_processor():
    P.reset()
    try:
        _load_example()
        names = [p["name"] for p in P.list_processors()]
        assert "send-to-jdownloader" in names, names
    finally:
        P.reset()


def test_dormant_without_env():
    P.reset()
    old = os.environ.pop("JD_SEND", None)
    try:
        mod = _load_example()
        # call the processor directly; JD_SEND unset -> None, no client built
        assert mod.send_to_jd({"url": "https://x/v.mp4"}) is None
    finally:
        if old is not None:
            os.environ["JD_SEND"] = old
        P.reset()


def test_submits_when_enabled():
    P.reset()
    old = os.environ.get("JD_SEND")
    orig = jd_bridge.get_client_for_site
    fake = _FakeClient()
    try:
        os.environ["JD_SEND"] = "1"
        jd_bridge.get_client_for_site = lambda cfg: fake
        mod = _load_example()
        out = mod.send_to_jd({"url": "https://x/v.mp4", "site_id": "demo"})
        assert out and out.get("submitted") and out.get("link_id") == "LINK-123", out
        assert fake.calls and fake.calls[0][0] == "https://x/v.mp4", fake.calls
    finally:
        jd_bridge.get_client_for_site = orig
        if old is None:
            os.environ.pop("JD_SEND", None)
        else:
            os.environ["JD_SEND"] = old
        P.reset()


def test_submit_failure_raises():
    P.reset()
    old = os.environ.get("JD_SEND")
    orig = jd_bridge.get_client_for_site
    fake = _FakeClient(fail=True)
    try:
        os.environ["JD_SEND"] = "1"
        jd_bridge.get_client_for_site = lambda cfg: fake
        mod = _load_example()
        raised = False
        try:
            mod.send_to_jd({"url": "https://x/v.mp4"})
        except RuntimeError:
            raised = True
        assert raised, "submit failure should raise for quarantine"
    finally:
        jd_bridge.get_client_for_site = orig
        if old is None:
            os.environ.pop("JD_SEND", None)
        else:
            os.environ["JD_SEND"] = old
        P.reset()

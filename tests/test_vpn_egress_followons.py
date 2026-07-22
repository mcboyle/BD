"""v3.66.392 -- VPN egress follow-ons (Track-K continuation).

VPN-MULTICONN: the parallel-range multi_conn client gets a proxy-native path
  so VPN downloads keep multi-connection throughput (it was gated OFF under a
  tunnel @390 because it had no proxy-native path).
VPN-CONTROLPLANE: the discovery/test/deep-detect httpx.Client sites in runner.py
  route through the SAME fail-closed VPN proxy as the payload path, so a
  vpn_required site whose tunnel is down does not egress discovery on the clear
  interface.

Sandbox scope (per the tracker): the proxy-threading + decision wiring is
verified here (signature, behavioral proxy-capture, structural); the live
tunnel-drop validation is on-stash.
"""
from __future__ import annotations

import inspect
import queue
import sys
import types
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent


class _AggregateSrc:
    """runner.py + every runner_*.py mixin module, concatenated. The PHASE 3
    decomposition (v3.66.397+) moved SiteRunner methods (and the
    _ManualDownloadSession class @399, which carries a control-plane site) into
    sibling modules; these are FLOOR (>=) literal-count guards, so reading the
    aggregate keeps every marker findable regardless of which module owns it."""
    def __init__(self, pkg_dir):
        self._paths = [pkg_dir / "runner.py"] + sorted(pkg_dir.glob("runner_*.py"))
    def read_text(self, encoding="utf-8"):
        return "\n".join(p.read_text(encoding=encoding) for p in self._paths)


_RUNNER_PY = _AggregateSrc(_REPO_ROOT / "bulk_downloader")
_MULTI_CONN_PY = _REPO_ROOT / "bulk_downloader" / "multi_conn.py"


# ---- helpers ---------------------------------------------------------------

def _client_constructions(src: str, needle: str = "httpx.Client("):
    """Yield the ~200-char window starting at each httpx.Client( construction."""
    out = []
    i = src.find(needle)
    while i >= 0:
        out.append(src[i:i + 220])
        i = src.find(needle, i + 1)
    return out


def _marker_regions(src: str, marker: str, span: int = 800):
    out = []
    i = src.find(marker)
    while i >= 0:
        out.append(src[i:i + span])
        i = src.find(marker, i + 1)
    return out


# ---- VPN-MULTICONN: multi_conn proxy-native API ----------------------------

def test_multi_conn_probe_accepts_proxy_kw():
    from bulk_downloader import multi_conn
    params = inspect.signature(multi_conn.probe).parameters
    assert "proxy" in params, "multi_conn.probe must accept a proxy= keyword"


def test_multi_conn_download_accepts_proxy_kw():
    from bulk_downloader import multi_conn
    params = inspect.signature(multi_conn.download).parameters
    assert "proxy" in params, "multi_conn.download must accept a proxy= keyword"


def test_multi_conn_every_client_carries_proxy():
    """Safety: NO httpx.Client in multi_conn may be built without threading the
    proxy through -- an unproxied client is a clear-egress leak under a tunnel."""
    src = _MULTI_CONN_PY.read_text(encoding="utf-8")
    constructions = _client_constructions(src)
    assert constructions, "expected at least one httpx.Client( in multi_conn.py"
    missing = [c for c in constructions if "proxy=" not in c]
    assert not missing, (
        "every httpx.Client in multi_conn.py must pass proxy=; "
        f"{len(missing)} construction(s) omit it")


def test_multi_conn_probe_threads_proxy_to_client():
    """Behavioral: probe(url, proxy=X) hands proxy=X to the httpx.Client it builds."""
    try:
        import httpx
    except Exception:
        return  # httpx unavailable in this band -> structural tests still cover it
    from bulk_downloader import multi_conn

    seen = {}

    class _FakeResp:
        status_code = 200
        headers = {"Content-Length": "1048576", "Accept-Ranges": "bytes"}

    class _FakeClient:
        def __init__(self, *a, **kw):
            seen.update(kw)

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def head(self, *a, **kw):
            return _FakeResp()

        def get(self, *a, **kw):
            return _FakeResp()

        def stream(self, *a, **kw):
            return _FakeClient()

    orig = httpx.Client
    httpx.Client = _FakeClient
    try:
        # public literal IP: passes the F-RUN03-02 SSRF host guard (no DNS,
        # classified global) so the proxy-threading path is exercised. A
        # non-resolvable hostname would now be refused before client build.
        multi_conn.probe("http://93.184.216.34/file.bin",
                         proxy="socks5://127.0.0.1:9050")
    finally:
        httpx.Client = orig

    assert seen.get("proxy") == "socks5://127.0.0.1:9050", (
        f"probe did not thread proxy into httpx.Client; saw {seen!r}")


# ---- VPN-MULTICONN: runner gate + threading --------------------------------

def test_runner_try_multi_conn_accepts_proxy_url():
    src = _RUNNER_PY.read_text(encoding="utf-8")
    pos = src.find("def _try_multi_conn_download(")
    assert pos > 0
    end = src.find("\n    def ", pos + 10)
    sig = src[pos:end if end > 0 else pos + 600]
    # signature region (up to the closing ") -> bool:")
    head = sig[:sig.find("-> bool")]
    assert "proxy_url" in head, "_try_multi_conn_download must take a proxy_url param"


def test_runner_multi_conn_gate_no_longer_requires_no_proxy():
    """The @390 'and not proxy_url' guard that disabled multi_conn under a tunnel
    must be gone -- multi_conn now has a proxy-native path."""
    src = _RUNNER_PY.read_text(encoding="utf-8")
    gpos = src.find('self.config.get("use_multi_conn"')
    assert gpos > 0, "multi_conn gate not found"
    # the gate condition spans a few lines up to the colon that opens the try
    gate = src[gpos:gpos + 320]
    cond = gate[:gate.find(":")]
    assert "not proxy_url" not in cond, (
        "the multi_conn gate must no longer disable itself when a proxy is set")


def test_runner_passes_proxy_into_multi_conn():
    src = _RUNNER_PY.read_text(encoding="utf-8")
    # the call site forwards proxy_url
    assert "proxy_url=proxy_url" in src, "runner must forward proxy_url into _try_multi_conn_download"
    # both _mconn calls thread the proxy
    ppos = src.find("_mconn.probe(")
    dpos = src.find("_mconn.download(")
    assert ppos > 0 and dpos > 0
    probe_call = src[ppos:ppos + 200]
    dl_call = src[dpos:dpos + 400]
    assert "proxy=" in probe_call, "_mconn.probe must be called with proxy="
    assert "proxy=" in dl_call, "_mconn.download must be called with proxy="


# ---- VPN-CONTROLPLANE: discovery client binding ----------------------------

def test_runner_controlplane_marked_three_sites():
    src = _RUNNER_PY.read_text(encoding="utf-8")
    n = src.count("VPN-CONTROLPLANE")
    assert n >= 3, (
        f"expected >=3 VPN-CONTROLPLANE markers (discovery/listing/deep-detect); found {n}")


def test_manual_download_session_delegates_proxy_resolution_to_runner():
    """The standalone manual session must resolve its proxy via its owner."""
    import bulk_downloader
    from bulk_downloader import runner_manual

    proxy_sentinel = "socks5://manual-session-sentinel.invalid:1080"
    proxy_resolutions = []
    client_proxies = []

    class _FakeRunner:
        site_id = "manual-proxy-test"
        config = {}

        def _download_proxy_url(self):
            proxy_resolutions.append(proxy_sentinel)
            return proxy_sentinel

    class _FakeLocator:
        def get_attribute(self, name):
            return "https://media.example/video.mp4" if name == "href" else None

    page = types.SimpleNamespace(url="https://members.example/video")

    class _FakeContext:
        pages = [page]

        def cookies(self):
            return []

        def close(self):
            pass

    class _FakeBrowser:
        def close(self):
            pass

    class _FakePlaywright:
        def stop(self):
            pass

    class _FakeResponse:
        status_code = 206
        headers = {"Content-Type": "video/mp4", "Content-Length": "20"}
        content = b"\x00\x00\x00\x18ftypisom" + (b"\x00" * 8)

    class _FakeClient:
        def __init__(self, *args, **kwargs):
            client_proxies.append(kwargs.get("proxy"))

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def get(self, *args, **kwargs):
            return _FakeResponse()

    fake_modules = {
        "bulk_downloader.detect": types.SimpleNamespace(
            find_best_download=lambda *args, **kwargs: {
                "_learned_sel": "a.download",
                "locator": _FakeLocator(),
                "text": "Download",
            }),
        "bulk_downloader.cookies": types.SimpleNamespace(
            pw_to_json=lambda cookies: cookies),
        "bulk_downloader.app": types.SimpleNamespace(
            _is_url_public=lambda url: True),
    }
    missing_import = object()
    saved_modules = {
        name: sys.modules.get(name, missing_import)
        for name in fake_modules
    }
    saved_package_attrs = {
        name.rsplit(".", 1)[1]: getattr(
            bulk_downloader, name.rsplit(".", 1)[1], missing_import)
        for name in fake_modules
    }
    original_client = runner_manual.httpx.Client
    response_q = queue.Queue()
    cancel_q = queue.Queue()
    session = object.__new__(runner_manual._ManualDownloadSession)
    session._runner = _FakeRunner()
    session._cmd_q = queue.Queue()
    session._error = None
    session._ready = runner_manual.threading.Event()
    session._closed = runner_manual.threading.Event()
    session._thread = types.SimpleNamespace(name="manual-proxy-test")
    session._launch = lambda: (
        _FakeBrowser(), _FakeContext(), page, _FakePlaywright())
    session._cmd_q.put(("test_download", {}, response_q))
    session._cmd_q.put(("cancel", None, cancel_q))

    try:
        # Simulate arbitrary import order: a previously imported app remains
        # cached on the package even when its sys.modules entry is replaced.
        bulk_downloader.app = types.SimpleNamespace(
            _is_url_public=lambda url: False)
        sys.modules.update(fake_modules)
        for name, module in fake_modules.items():
            setattr(bulk_downloader, name.rsplit(".", 1)[1], module)
        runner_manual.httpx.Client = _FakeClient
        session._run()
    finally:
        runner_manual.httpx.Client = original_client
        for name, module in saved_modules.items():
            if module is missing_import:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = module
        for name, module in saved_package_attrs.items():
            if module is missing_import:
                if hasattr(bulk_downloader, name):
                    delattr(bulk_downloader, name)
            else:
                setattr(bulk_downloader, name, module)

    response = response_q.get_nowait()
    assert response[0] == "ok", response
    assert proxy_resolutions == [proxy_sentinel]
    assert client_proxies == [proxy_sentinel]


def test_manual_proxy_test_setup_failure_does_not_leak_import_state():
    """A dependency lookup failure must precede all fake-module mutation."""
    import bulk_downloader
    from bulk_downloader import runner_manual

    names = ("app", "detect", "cookies")
    missing = object()
    saved_modules = {
        name: sys.modules.get(f"bulk_downloader.{name}", missing)
        for name in names
    }
    saved_attrs = {
        name: getattr(bulk_downloader, name, missing)
        for name in names
    }
    original_httpx = runner_manual.httpx
    preloaded_app = types.ModuleType("bulk_downloader.app")

    class _FailingHTTPX:
        @property
        def Client(self):
            raise RuntimeError("forced Client setup failure")

    try:
        sys.modules["bulk_downloader.app"] = preloaded_app
        bulk_downloader.app = preloaded_app
        for name in ("detect", "cookies"):
            sys.modules.pop(f"bulk_downloader.{name}", None)
            if hasattr(bulk_downloader, name):
                delattr(bulk_downloader, name)
        runner_manual.httpx = _FailingHTTPX()

        try:
            test_manual_download_session_delegates_proxy_resolution_to_runner()
        except RuntimeError as exc:
            assert str(exc) == "forced Client setup failure"
        else:
            assert False, "expected the forced Client setup failure"

        assert sys.modules["bulk_downloader.app"] is preloaded_app
        assert bulk_downloader.app is preloaded_app
        for name in ("detect", "cookies"):
            assert f"bulk_downloader.{name}" not in sys.modules
            assert not hasattr(bulk_downloader, name)
    finally:
        runner_manual.httpx = original_httpx
        for name, module in saved_modules.items():
            qualified = f"bulk_downloader.{name}"
            if module is missing:
                sys.modules.pop(qualified, None)
            else:
                sys.modules[qualified] = module
        for name, module in saved_attrs.items():
            if module is missing:
                if hasattr(bulk_downloader, name):
                    delattr(bulk_downloader, name)
            else:
                setattr(bulk_downloader, name, module)


def test_runner_controlplane_sites_proxied_and_fail_closed():
    """Each control-plane site resolves _download_proxy_url on its owner, threads
    it into its httpx client, and has a VPNRequiredError fail-closed branch.

    Counts distinctive literals globally (NOT a fixed char window -- see the
    v3.66.391 fixed-window-overflow lesson)."""
    src = _RUNNER_PY.read_text(encoding="utf-8")
    manual_src = (_REPO_ROOT / "bulk_downloader" / "runner_manual.py").read_text(
        encoding="utf-8")
    assert src.count("VPN-CONTROLPLANE") >= 3, "expected >=3 control-plane markers"
    assert src.count("_cp_proxy = self._download_proxy_url()") == 2, (
        "the two SiteRunner control-plane sites must resolve their own proxy")
    assert manual_src.count(
        "_cp_proxy = self._runner._download_proxy_url()") == 1, (
        "the standalone manual session must resolve its stored runner's proxy")
    assert src.count("proxy=_cp_proxy") >= 3, (
        "each control-plane client must thread proxy=_cp_proxy")
    assert src.count("_cpe, vpn_runtime.VPNRequiredError") >= 3, (
        "each control-plane site must fail closed on VPNRequiredError")

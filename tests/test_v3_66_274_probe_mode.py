"""v3.66.274  GCW probe mode: trigger -> media -> first-bytes -> abort.

The GCW-4 e2e gate (273) proves the trigger->media->bytes path by watching for a
real, non-zero download. That is correct but burns the FULL file's bandwidth just
to verify a draft. Probe mode is the optimisation: the trigger still fires (so the
media URL is real and the session/cookies are exercised), but the runner then
HTTP-streams only the FIRST BYTES (<=256 KB) and ABORTS — no file is written, no
download_dir is needed. It records the same ``/api/history`` verdict the GCW-4
gate reads, so a reachable + non-zero probe is a pass exactly like a full
download, just far cheaper.

Sandbox surface (no network / no headed browser, so the live trigger is
stash-only):
  * ``_do_probe_fetch`` against a faked ``httpx.stream`` — done + file_size>0 on a
    2xx with bytes; needs_review + 0 on a non-2xx; writes NO file either way.
  * ``_process_one`` routes a ``probe`` job around the no-dl-dir branch (source).
  * ``test_extract`` accepts ``probe`` and stamps the job flag (source).
  * SPA Test step exposes the toggle and sends ``probe`` (source-scans, GCW style).
"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path


# ─── _do_probe_fetch behaviour (faked httpx, no network, no file) ────────
class _FakeResp:
    def __init__(self, status, headers, chunks):
        self.status_code = status
        self.headers = headers
        self._chunks = chunks

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def iter_bytes(self):
        for c in self._chunks:
            yield c


class _FakeHttpx:
    """Stands in for the runner's module-level ``httpx``; records the call and
    returns a canned streaming response."""

    def __init__(self, resp):
        self._resp = resp
        self.calls = []

    def stream(self, method, url, **kw):
        self.calls.append((method, url, kw))
        return self._resp

    class Timeout:  # _do_probe_fetch constructs httpx.Timeout(...)
        def __init__(self, *a, **k):
            pass


class _FakeCtx:
    def cookies(self):
        return [{"name": "sess", "value": "abc"}]


class _DLStub:
    def __init__(self, url):
        self.url = url
        self.suggested_filename = "clip.mp4"

    def cancel(self):
        pass


class _FakeRunner:
    """Minimal stand-in carrying just what _do_probe_fetch touches."""

    def __init__(self):
        self.config = {"name": "ProbeSite"}
        self.site_id = "pb01"
        self.updates = []
        self._proxy = None  # None = degrade open; set to an Exception to fail closed

    def _update_job(self, url, status, message, **extra):
        self.updates.append((url, status, message, extra))

    def _download_proxy_url(self):
        # F-RUN02-02: _do_probe_fetch resolves the fail-closed VPN download proxy
        # before building the httpx client. In the outcome scenarios there is no
        # VPN -> None (degrade open, same as a site that is not vpn_required). A
        # test sets ``_proxy`` to a VPNRequiredError to exercise the fail-closed
        # path (the real _download_proxy_url raises for vpn_required + tunnel down).
        if isinstance(self._proxy, Exception):
            raise self._proxy
        return self._proxy


def _run_probe(status, headers, chunks):
    """Call SiteRunner._do_probe_fetch with fakes; capture the db_log row."""
    from bulk_downloader import runner as R
    from bulk_downloader import runner_transport as RT

    captured = {}

    def _fake_db_log(site_id, name, url, st, fn, size, note, *a, **k):
        captured.update(dict(site_id=site_id, name=name, url=url, status=st,
                             filename=fn, size=size, note=note))

    orig_httpx = RT.httpx
    orig_db_log = RT.db_log
    fake = _FakeHttpx(_FakeResp(status, headers, chunks))
    self = _FakeRunner()
    cwd0 = os.getcwd()
    with tempfile.TemporaryDirectory() as td:
        os.chdir(td)
        try:
            RT.httpx = fake
            RT.db_log = _fake_db_log
            R.SiteRunner._do_probe_fetch(
                self, "https://x.example/v/1", object(), _FakeCtx(),
                _DLStub("https://cdn.example/clip.mp4"), {"score": 1080}, "1080p",
                "clip.mp4")
            # No file should have been written anywhere under the temp cwd.
            leftover = [p for p in Path(td).rglob("*") if p.is_file()]
        finally:
            RT.httpx = orig_httpx
            RT.db_log = orig_db_log
            os.chdir(cwd0)
    return captured, self, fake, leftover


def test_probe_done_on_reachable_nonzero():
    """A 2xx with real bytes -> done, file_size = bytes received (>0), no file."""
    cap, self, fake, leftover = _run_probe(
        200, {"Content-Type": "video/mp4", "Content-Length": "104857600"},
        [b"x" * 65536, b"y" * 65536])
    assert cap["status"] == "done", cap
    assert cap["size"] > 0, cap
    assert leftover == [], f"probe must write NO file, saw {leftover}"
    # The trigger's Playwright download was cancelled and we fetched the media URL.
    assert fake.calls and fake.calls[0][1] == "https://cdn.example/clip.mp4"


def test_probe_caps_first_bytes():
    """Probe never counts more than 256 KiB, even across an oversized chunk."""
    big = [b"z" * (256 * 1024 + 4096), b"y" * 65536]
    cap, self, fake, _ = _run_probe(
        200, {"Content-Type": "video/mp4"}, big)
    assert cap["status"] == "done"
    assert cap["size"] == 256 * 1024, cap
    assert fake.calls[0][2]["headers"]["Range"] == "bytes=0-262143"


def test_probe_needs_review_on_error_status():
    """A non-2xx (403/404) -> needs_review, file_size 0 (the gate must NOT pass)."""
    cap, self, fake, leftover = _run_probe(
        403, {"Content-Type": "text/html"}, [b"<html>forbidden"])
    assert cap["status"] == "needs_review", cap
    assert cap["size"] == 0, cap
    assert leftover == []


def test_probe_fails_closed_when_vpn_required_tunnel_down():
    """F-RUN02-02: a vpn_required site whose tunnel is down -> the probe fails
    closed (needs_review) BEFORE building any httpx client, so no media bytes
    are sampled on the clear interface."""
    from bulk_downloader import runner as R
    from bulk_downloader import runner_transport as RT
    from bulk_downloader import vpn_runtime

    captured = {}

    def _fake_db_log(site_id, name, url, st, fn, size, note, *a, **k):
        captured.update(status=st, note=note, size=size)

    fake = _FakeHttpx(_FakeResp(200, {"Content-Type": "video/mp4"}, [b"x" * 4096]))
    self = _FakeRunner()
    self._proxy = vpn_runtime.VPNRequiredError("tunnel down")  # resolver raises
    orig_httpx, orig_db_log = RT.httpx, RT.db_log
    try:
        RT.httpx = fake
        RT.db_log = _fake_db_log
        R.SiteRunner._do_probe_fetch(
            self, "https://x.example/v/1", object(), _FakeCtx(),
            _DLStub("https://cdn.example/clip.mp4"), {"score": 1080}, "1080p",
            "clip.mp4")
    finally:
        RT.httpx = orig_httpx
        RT.db_log = orig_db_log

    assert fake.calls == [], "must fail closed BEFORE any httpx.stream (no clear-net sample)"
    assert self.updates and self.updates[-1][1] == "needs_review", self.updates
    assert captured.get("status") == "needs_review" and captured.get("size") == 0, captured


# ─── source-scan: runner routing + app.py flag ──────────────────────────
def _runner_src() -> str:
    root = Path(__file__).resolve().parent.parent
    _pkg = root / "bulk_downloader"
    _files = [_pkg / "runner.py"] + sorted(_pkg.glob("runner_*.py"))
    return "\n".join(p.read_text(encoding="utf-8") for p in _files)


def _app_src() -> str:
    root = Path(__file__).resolve().parent.parent
    pkg = root / "bulk_downloader"
    parts = [(pkg / "app.py").read_text(encoding="utf-8")]
    parts += [p.read_text(encoding="utf-8") for p in sorted(pkg.glob("app_*.py"))]
    return "\n".join(parts)


def test_process_one_routes_probe_before_dl_dir():
    src = _runner_src()
    assert "_do_probe_fetch" in src
    assert 'get("probe")' in src or "get('probe')" in src


def test_test_extract_accepts_probe_flag():
    src = _app_src()
    assert 'body.get("probe"' in src or "body.get('probe'" in src
    assert '["probe"]' in src or "['probe']" in src


# ─── SPA source-scans (GCW-1/2/3/4 style) ────────────────────────────────
def _spa_src() -> str:
    root = Path(__file__).resolve().parent.parent
    return (root / "frontend" / "src" / "routes"
            / "CaptureWorkflow.tsx").read_text(encoding="utf-8")


def test_spa_has_probe_toggle_state():
    src = _spa_src()
    assert "probe" in src
    assert "setProbe" in src


def test_spa_test_body_sends_probe():
    # runTest's test_extract body must include the probe flag.
    src = _spa_src()
    assert "probe," in src or "probe:" in src


def test_spa_probe_toggle_label():
    # A visible toggle so the operator can choose the cheap path.
    assert "Probe" in _spa_src()

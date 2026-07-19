"""RED-first guard for v3.66.551: _pick_fastest_mirror SSRF host-guard (F-RUN03-04).

TelemetryMixin._pick_fastest_mirror (opt-in speculative_mirror_select, default off)
HEADs the site-controlled file_url and config-built mirrors via httpx with
follow_redirects=True. It validates the URL SCHEME (rejects file://gopher:// -- a
v3.42.0 audit fix) but has NO internal-IP / is_global check, so an http(s) URL pointing
at an internal address (169.254.169.254, localhost, 10.x) is still probed -- an
internal-service status oracle (which mirror "wins"). SSRF sibling of F-RUN03-02;
HEAD-only + opt-in, so lower severity.

The fix adds the canonical _common._is_safe_public_host host check to probe() before the
HEAD (skipping a non-public candidate, matching the existing scheme-reject "return"),
and stops following redirects (follow_redirects=False) so a public mirror can't 30x us
to an internal host -- the winner logic already accepts 3xx as "alive", so no behavior
is lost.

RED on the pre-551 tree: no host guard -> a loopback file_url is HEADed (httpx.Client.head
patched to a spy so nothing hits the network). GREEN once probe() skips the non-public
candidate before the HEAD (the sibling public mirror is still HEADed, proving the method
ran and only the internal candidate was refused).

Convention: zero-arg fns; httpx.Client.head patched on the class and restored in
try/finally; the method is invoked on a minimal fake self (no runner boot).
"""
import types
import httpx
import bulk_downloader.runner_telemetry as rt

_LOOPBACK = "http://127.0.0.1:5599/file.bin"     # loopback literal, no DNS
_PUBLIC = "http://93.184.216.34/file.bin"        # public unicast literal


class _FakeResp:
    status_code = 200


_heads = []


def _spy_head(self, url, **_k):
    _heads.append(url)
    return _FakeResp()


def _fake_runner(mirror_list):
    f = types.SimpleNamespace()
    f.config = {"speculative_mirror_select": True}
    f._build_mirror_urls = lambda file_url: list(mirror_list)
    f._download_proxy_url = lambda: None
    f.log_event = lambda *a, **k: None
    return f


def _run(file_url, mirrors):
    _heads.clear()
    orig = httpx.Client.head
    httpx.Client.head = _spy_head
    try:
        return rt.TelemetryMixin._pick_fastest_mirror(_fake_runner(mirrors), file_url)
    finally:
        httpx.Client.head = orig


def test_no_head_to_loopback_file_url():
    # loopback primary + a public mirror: the loopback must be skipped before the HEAD.
    _run(_LOOPBACK, ["http://93.184.216.34/mirror.bin"])
    assert not any("127.0.0.1" in u for u in _heads), \
        f"loopback file_url must not be HEADed, but was: {_heads}"


def test_no_head_to_loopback_mirror():
    # public primary + a loopback mirror: the loopback mirror must be skipped too.
    _run(_PUBLIC, ["http://127.0.0.1:5599/mirror.bin"])
    assert not any("127.0.0.1" in u for u in _heads), \
        f"loopback mirror must not be HEADed, but was: {_heads}"


def test_public_file_url_reaches_head():
    # regression: a public file_url must PASS the guard and be HEADed, proving the
    # guard doesn't over-block. Green on both pre- and post-fix trees.
    _run(_PUBLIC, ["http://8.8.8.8/mirror.bin"])
    assert any("93.184.216.34" in u for u in _heads), \
        f"public file_url should be HEADed, got {_heads}"

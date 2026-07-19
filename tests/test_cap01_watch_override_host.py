"""F-CAP01-01 -- live_recorder.watch() override path host-allowlist bypass.

When BOTH site_override and room_override are supplied, watch() skips
parse_live_url (which host-gates the URL against the cam-site allowlist,
_SITE_PATTERNS) and validates only the URL *shape*. So an override could aim
streamlink/ffmpeg at ANY http(s) host (e.g. cloud metadata / an internal
service) -- an SSRF. The fix enforces the SAME cam-site host allowlist on the
override path: an override can relabel site/room but cannot point the recorder
at a non-cam host.

Test hosts are deliberately NON-cam (SSRF/internal shapes); registry/adult cam
hosts are never used. is_available is forced True so the sandbox backend
presence does not mask the host check.
"""
import os
import tempfile

os.environ.setdefault("BD_HOME", tempfile.mkdtemp())
os.environ.setdefault("BD_DISABLE_KEEPALIVE", "1")

_BAD_HOSTS = [
    "http://169.254.169.254/live",   # cloud metadata / link-local
    "http://internal.example/room",  # arbitrary internal name
    "http://10.0.0.5/x",             # RFC1918 host that is not a cam site
]


def _with_backend(fn):
    from bulk_downloader import live_recorder as lr
    orig = lr.is_available
    lr.is_available = lambda: True
    try:
        return fn(lr)
    finally:
        lr.is_available = orig


def test_watch_override_rejects_non_cam_host():
    out = tempfile.mkdtemp()

    def run(lr):
        for bad in _BAD_HOSTS:
            res = lr.watch(bad, out, site_override="mysite", room_override="myroom")
            assert res.get("ok") is False, \
                f"override to non-cam host must be refused: {bad} -> {res}"
            assert res.get("recording_id") is None, \
                f"override to non-cam host must NOT create a recording: {bad} -> {res}"
    _with_backend(run)


if __name__ == "__main__":
    import traceback
    try:
        test_watch_override_rejects_non_cam_host(); print("PASS  test_watch_override_rejects_non_cam_host")
    except AssertionError as e:
        print(f"FAIL  test_watch_override_rejects_non_cam_host: {e}")
    except Exception:
        print("ERROR"); traceback.print_exc()

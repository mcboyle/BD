"""v3.66.754c -- /api/thumbs/* must confine the body path to a configured media root.

THE POSTURE (operator decision @754: CONFINE, not remove):

    POST /api/thumbs/single {"path": "<anything readable>"} -> os.path.abspath() ->
    os.path.isfile() -> ffmpeg -i <path>

The three /api/thumbs/{single,contact_sheet,sprite_sheet} routes take an operator-supplied
absolute path and hand it to ffmpeg with only a presence check -- no confinement to a media
root. They are dark controls (operator_facing, spa_wired=False; no frontend caller), which
is exactly why the arbitrary-path surface went unnoticed. ffmpeg will not render /etc/passwd
as video, but "operator-supplied absolute path -> subprocess, no allowlist" is a real
arbitrary-file-read / SSRF-adjacent posture.

THE FIX reuses the EXISTING in-tree pattern from /api/thumbnails/serve/ (realpath +
startswith(download_dir) + 403 on traversal). Here the allowed roots are the set of every
configured site download_dir; a path under none of them is rejected 403 BEFORE ffmpeg.

RED-first: on the pristine tree a path outside every root reaches the generator (no 403).
"""
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)


def _client(tmp_home, roots):
    os.environ["BD_HOME"] = tmp_home
    os.environ.setdefault("BD_DISABLE_KEEPALIVE", "1")
    import importlib
    import bulk_downloader.app as A
    importlib.reload(A) if "bulk_downloader.app" in sys.modules else None
    from bulk_downloader.app import app
    app.config["TESTING"] = True
    # register a site whose download_dir is an allowed root
    for i, r in enumerate(roots):
        A.s_cfg["site_%d" % i] = {"download_dir": r}
    c = app.test_client()
    return A, c


def _csrf(c):
    # mirror the fixture pattern used elsewhere; tolerate CSRF-exempt test config
    try:
        r = c.get("/api/csrf")
        import json as _j
        tok = (r.get_json(silent=True) or {}).get("csrf_token", "")
    except Exception:
        tok = ""
    return {"X-CSRFToken": tok, "X-CSRF-Token": tok, "Content-Type": "application/json"}


def test_a_path_outside_every_media_root_is_rejected_403():
    with tempfile.TemporaryDirectory() as home, tempfile.TemporaryDirectory() as root:
        A, c = _client(home, [root])
        hdr = _csrf(c)
        # a real, readable file that is NOT under any configured download_dir
        outside = os.path.join(home, "outside.mp4")
        open(outside, "wb").write(b"\x00\x00")
        r = c.post("/api/thumbs/single", json={"path": outside}, headers=hdr)
        assert r.status_code == 403, (
            "a path outside every configured media root reached the thumb generator "
            "(status %s) -- /api/thumbs/single does not confine body['path'] before ffmpeg"
            % r.status_code)


def test_all_three_thumbs_routes_confine():
    with tempfile.TemporaryDirectory() as home, tempfile.TemporaryDirectory() as root:
        A, c = _client(home, [root])
        hdr = _csrf(c)
        outside = os.path.join(home, "x.mp4")
        open(outside, "wb").write(b"\x00\x00")
        for route in ("/api/thumbs/single", "/api/thumbs/contact_sheet",
                      "/api/thumbs/sprite_sheet"):
            r = c.post(route, json={"path": outside}, headers=hdr)
            assert r.status_code == 403, (
                "%s did not 403 an out-of-root path -- confinement must cover all three "
                "routes, not just one" % route)


def test_a_path_inside_a_configured_root_is_not_rejected_by_confinement():
    """POS half: confinement must not 403 a legitimate in-root path. (It may still fail
    downstream for other reasons -- ffmpeg, missing file -- but NOT with the 403 gate.)"""
    with tempfile.TemporaryDirectory() as home, tempfile.TemporaryDirectory() as root:
        A, c = _client(home, [root])
        hdr = _csrf(c)
        inside = os.path.join(root, "clip.mp4")
        open(inside, "wb").write(b"\x00\x00")
        r = c.post("/api/thumbs/single", json={"path": inside}, headers=hdr)
        assert r.status_code != 403, (
            "a path INSIDE a configured download_dir was rejected by the confinement gate "
            "(403) -- the allowlist is too strict and blocks legitimate media")


def test_traversal_out_of_a_root_is_rejected():
    """`<root>/../secret` resolves outside the root via realpath and must 403."""
    with tempfile.TemporaryDirectory() as home, tempfile.TemporaryDirectory() as root:
        A, c = _client(home, [root])
        hdr = _csrf(c)
        secret = os.path.join(home, "secret.mp4")
        open(secret, "wb").write(b"\x00\x00")
        traversal = os.path.join(root, "..", os.path.basename(home), "secret.mp4")
        r = c.post("/api/thumbs/single", json={"path": traversal}, headers=hdr)
        assert r.status_code == 403, (
            "a `..` traversal out of the configured root was not rejected -- confinement "
            "must use realpath, not a raw string prefix")

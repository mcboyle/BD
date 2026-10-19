"""misc-surface C1/C2 -- /api/thumbnails/* must confine every path to a configured media root.

C1 (HIGH): POST /api/thumbnails/regenerate took source_path AND download_dir from the request
body with no confinement, then queued ffmpeg -y with skip_existing=False. Any readable video
anywhere on disk got a .jpg written (or an existing one overwritten) beside it ("sidecar") or
under <body download_dir>/thumbs/ ("parallel"). The sibling /api/thumbs/* routes already 403
the same path via app_thumbs._confine_path (v3.66.754c); this route never got it. Confining
source_path alone is not enough: an in-root source with download_dir set to a PARENT of the
root makes parallel mode write <parent>/thumbs/<root-name>/x.jpg, outside every root.

C2 (LOW): GET /api/thumbnails/serve/ ran os.path.isfile(thumb_path) BEFORE the realpath
containment check, so an out-of-root target answered 404 (absent) vs 403 (present) -- an
existence oracle for *.jpg outside download_dir. Containment must run first.

RED-first: on the pristine tree the out-of-root regenerate answers 200 queued, and the
out-of-root serve of an absent file answers 404.
"""
import os
import sys
import tempfile
import time

BD_GATE_SCOPE = "module"  # O906.1 gate marker (precollect guard)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

SEED = b"SEED-C1-NOT-A-THUMB"


def _client(tmp_home, roots):
    os.environ["BD_HOME"] = tmp_home
    os.environ.setdefault("BD_DISABLE_KEEPALIVE", "1")
    import bulk_downloader.app as A
    from bulk_downloader.app import app
    app.config["TESTING"] = True
    for i, r in enumerate(roots):
        A.s_cfg["site_%d" % i] = {"download_dir": r}
    return A, app.test_client()


def _csrf(c):
    tok = (c.get("/api/csrf").get_json(silent=True) or {}).get("csrf_token", "")
    return {"X-CSRFToken": tok, "X-CSRF-Token": tok}


def _seeded_video(d, name="x"):
    os.makedirs(os.path.join(d, "thumbs"), exist_ok=True)
    vid = os.path.join(d, name + ".mp4")
    open(vid, "wb").write(b"\x00\x00")
    seeds = [os.path.join(d, name + ".jpg"), os.path.join(d, "thumbs", name + ".jpg")]
    for s in seeds:
        open(s, "wb").write(SEED)
    return vid, seeds


def _assert_seeds_intact(seeds):
    time.sleep(1.0)  # give a (wrongly) queued worker job its chance to write
    for s in seeds:
        assert open(s, "rb").read() == SEED, (
            "C1-WRITE-ESCAPE: %s was overwritten by the thumbnail worker" % s)


def test_c1_out_of_root_source_is_rejected_403_and_nothing_written():
    with tempfile.TemporaryDirectory() as home, tempfile.TemporaryDirectory() as root, \
            tempfile.TemporaryDirectory() as outside:
        A, c = _client(home, [root])
        hdr = _csrf(c)
        vid, seeds = _seeded_video(outside)
        for mode in ("sidecar", "parallel"):
            r = c.post("/api/thumbnails/regenerate", headers=hdr, json={
                "source_path": vid, "output_dir_mode": mode, "download_dir": outside})
            assert r.status_code == 403, (
                "C1-SOURCE-UNCONFINED: /api/thumbnails/regenerate queued an out-of-root "
                "source_path (mode=%s, status %s %r)" % (mode, r.status_code, r.get_json()))
        _assert_seeds_intact(seeds)


def test_c1_out_of_root_download_dir_is_rejected_403():
    """In-root source, download_dir = PARENT of the root: parallel mode would write
    <parent>/thumbs/<root-name>/x.jpg, outside every configured root."""
    with tempfile.TemporaryDirectory() as home, tempfile.TemporaryDirectory() as parent:
        root = os.path.join(parent, "media")
        os.makedirs(root)
        A, c = _client(home, [root])
        hdr = _csrf(c)
        vid, _ = _seeded_video(root)
        r = c.post("/api/thumbnails/regenerate", headers=hdr, json={
            "source_path": vid, "output_dir_mode": "parallel", "download_dir": parent})
        assert r.status_code == 403, (
            "C1-DOWNLOAD-DIR-UNCONFINED: /api/thumbnails/regenerate accepted a body "
            "download_dir outside every root (status %s %r)" % (r.status_code, r.get_json()))


def test_c1_in_root_source_and_download_dir_are_not_rejected():
    """POS half: confinement must not 403 a legitimate in-root regenerate."""
    with tempfile.TemporaryDirectory() as home, tempfile.TemporaryDirectory() as root:
        A, c = _client(home, [root])
        hdr = _csrf(c)
        vid, _ = _seeded_video(root)
        for body in ({"source_path": vid},
                     {"source_path": vid, "output_dir_mode": "parallel", "download_dir": root}):
            r = c.post("/api/thumbnails/regenerate", headers=hdr, json=body)
            assert r.status_code != 403, (
                "C1-OVERCONFINED: an in-root regenerate was refused 403 (%r)" % r.get_json())
            if A._THUMB_AVAILABLE:
                assert (r.get_json() or {}).get("queued") is True, r.get_json()


def test_c2_serve_containment_runs_before_existence_check():
    """A symlinked subdir under the root points outside it. Present or absent, the
    out-of-root target must answer the same 403 -- never 404 -- or it is an oracle."""
    with tempfile.TemporaryDirectory() as home, tempfile.TemporaryDirectory() as root, \
            tempfile.TemporaryDirectory() as outside:
        A, c = _client(home, [root])
        if not A._THUMB_AVAILABLE:
            return
        os.symlink(outside, os.path.join(root, "link"))
        open(os.path.join(outside, "present.jpg"), "wb").write(SEED)
        absent = c.get("/api/thumbnails/serve/site_0/link/absent.mp4")
        present = c.get("/api/thumbnails/serve/site_0/link/present.mp4")
        assert (absent.status_code, present.status_code) == (403, 403), (
            "C2-EXISTENCE-ORACLE: out-of-root serve answered absent=%s present=%s; "
            "containment must run before isfile" % (absent.status_code, present.status_code))

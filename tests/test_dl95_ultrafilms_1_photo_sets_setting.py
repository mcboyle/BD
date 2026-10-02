"""dl95-ultrafilms-1 (O1654) -- the "Photo sets" setting: off | zip (default) | extract.

Live, test2 v3.66 (harness-work/UIUX-20260928/download-95/B1/p1/ultrafilms): an ultrafilms
/members/content/item/<uuid>-<slug> page that is a PHOTO SET (no video on the page) saved
"the-simple-things_leona-mia_1000px.zip" -- 77 .jpg members, 0 video. Movie items on the same
site took the learned .mp4 button. Not a ranking bug (O1654): the operator wants an OPTION.

Contract (O1654):
  off     -> the photo-set item is refused with the existing "No video on this page" outcome
             (needs_review), extended from /gallery/ paths to "the only download is an image
             archive"; nothing is kept.
  zip     -> DEFAULT, today's behaviour: the archive is saved (row 722: zips pass integrity).
  extract -> the archive's images are unpacked into the scene folder (<archive stem>/), image
             types only, no path traversal; then the zip is dropped.
  Videos are always saved: a movie page, and an archive that carries any video, are unaffected
  in all three modes. Global default in app config; a per-site value overrides it ("inherit" or
  blank = use the global).
"""

BD_GATE_SCOPE = "module"

import io
import logging
import stat
import threading
import zipfile
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from bulk_downloader import global_config
from bulk_downloader import runner_transport as transport
from bulk_downloader.runner_auth import AuthMixin

SCENE = "https://ultrafilms.com/members/content/item/f17ee957-the-simple-things"
PHOTO_LEAF = "the-simple-things_leona-mia_1000px.zip"
MOVIE_LEAF = "creative-mood_ellie-luna_leona-mia_7680x4320.mp4"
JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 60
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 60
MP4 = b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 200


def _zip_bytes(members):
    """members: list of (name, bytes) or (name, bytes, external_attr)."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for m in members:
            info = zipfile.ZipInfo(m[0])
            if len(m) > 2:
                info.external_attr = m[2]
            zf.writestr(info, m[1])
    return buf.getvalue()


# The live archive's shape: one directory entry + images under it.
PHOTO_SET = _zip_bytes([("the-simple-things/", b"")]
                       + [(f"the-simple-things/{i:03d}.jpg", JPEG + bytes([i])) for i in range(1, 4)]
                       + [("the-simple-things/cover.png", PNG)])


class _Runner(transport.TransportMixin):
    _login_wall_rejects = getattr(AuthMixin, "_login_wall_rejects", lambda self, *a: False)

    def __init__(self, dl_dir, site_mode=None):
        self.site_id = "ultrafilms"
        self.config = {
            "name": "UltraFilms",
            "use_http_dl": True,
            "verify_hash": False,
            "verify_integrity": True,
            "learned": {"download": {"row_selectors": ["a.download[href]"],
                                     "url_attribute": "src"}},
        }
        if site_mode is not None:
            self.config["photo_sets"] = site_mode
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self.jobs = {}
        self.log = logging.getLogger("dl95_ultrafilms_1_test")
        self.dl_dir = dl_dir
        self.events = []

    def _probe_for_higher_tier(self, url, **kw):
        return url

    def _build_mirror_urls(self, url):
        return []

    def _history_title_fields(self, _url):
        return {}

    def _verify_integrity_or_quarantine(self, page_url, final_path, filename, downloaded_size):
        return True, False, ""

    def _probe_video_height(self, _path):
        return 0

    def _embed_metadata_if_mp4(self, *a, **kw):
        pass

    def _size_on_disk_after_tagging(self, _path, size):
        return size

    def _update_job(self, url, status, msg="", **kw):
        self.jobs[url] = {"status": status, "msg": msg, **kw}

    def log_event(self, *a, **kw):
        self.events.append((a, kw))


@pytest.fixture
def run(tmp_path, monkeypatch):
    """Drive the real _do_download for one learned href; the transfer writes `payload`."""
    def _run(leaf, payload, *, global_mode=None, site_mode=None):
        store = {} if global_mode is None else {"photo_sets": global_mode}
        monkeypatch.setattr(global_config, "get", lambda k, d=None: store.get(k, d))
        history = []
        monkeypatch.setattr(transport, "db_log", lambda *a, **kw: history.append((a, kw)))
        dest = tmp_path / "ultrafilms"
        dest.mkdir(exist_ok=True)
        runner = _Runner(dest, site_mode)
        fetched = []

        def transfer(_url, _page, _ctx, media, attempts, final):
            fetched.append(media)
            Path(final).write_bytes(payload)
            return len(payload), len(payload)
        runner._run_http_attempts_with_resume = transfer
        locator = MagicMock()
        locator.get_attribute.side_effect = (
            lambda attr: f"https://ultrafilms.com/members/download/{leaf}" if attr == "src" else None)
        best = {"locator": locator, "score": 0, "text": "Download", "_via_learned": True,
                "_learned_sel": "a.download[href]"}
        page = MagicMock()
        page.url = SCENE
        runner._do_download(page, MagicMock(), SCENE, best, dest, "auto")
        assert len(fetched) == 1, f"PHOTO_SETS_HARNESS: expected one transfer, got {fetched}"
        return runner.jobs[SCENE], dest, history, tmp_path
    return _run


def _files(root):
    return sorted(p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file())


# ── the mode matrix: photo-set page and movie page x off | zip | extract ───────────────────

@pytest.mark.parametrize("mode", ["off", "zip", "extract", None])
def test_movie_page_is_saved_unchanged_in_every_mode(run, mode):
    job, dest, history, _ = run(MOVIE_LEAF, MP4, global_mode=mode)
    assert job["status"] == "done", f"PHOTO_SETS_MOVIE_TOUCHED mode={mode}: {job}"
    assert _files(dest) == [job["filename"]] and job["filename"].endswith(".mp4"), _files(dest)
    assert (dest / job["filename"]).read_bytes() == MP4
    assert [h[0][3] for h in history] == ["done"]


@pytest.mark.parametrize("mode", ["zip", None, "bogus"])
def test_photo_set_zip_mode_is_the_default_and_keeps_the_archive(run, mode):
    job, dest, history, _ = run(PHOTO_LEAF, PHOTO_SET, global_mode=mode)
    assert job["status"] == "done", job
    assert _files(dest) == [job["filename"]] and job["filename"].endswith(".zip"), _files(dest)
    assert (dest / job["filename"]).read_bytes() == PHOTO_SET


def test_photo_set_off_is_refused_with_the_no_video_outcome(run):
    job, dest, history, _ = run(PHOTO_LEAF, PHOTO_SET, global_mode="off")
    assert job["status"] == "needs_review", f"PHOTO_SETS_OFF_KEPT: {job}"
    assert job["msg"].startswith("No video on this page"), job["msg"]
    assert "photo-set archive" in job["msg"], job["msg"]
    assert _files(dest) == [], f"PHOTO_SETS_OFF_LEFT_FILES: {_files(dest)}"
    assert [h[0][3] for h in history] == ["needs_review"]


def test_photo_set_extract_unpacks_images_and_drops_the_zip(run):
    job, dest, history, _ = run(PHOTO_LEAF, PHOTO_SET, global_mode="extract")
    assert job["status"] == "done", job
    stem = PHOTO_LEAF[:-len(".zip")]
    assert _files(dest) == [f"{stem}/001.jpg", f"{stem}/002.jpg", f"{stem}/003.jpg",
                            f"{stem}/cover.png"], f"PHOTO_SETS_EXTRACT_LAYOUT: {_files(dest)}"
    assert (dest / stem / "002.jpg").read_bytes() == JPEG + bytes([2])
    assert not list(dest.glob("*.zip")), "PHOTO_SETS_EXTRACT_KEPT_ZIP"
    assert job["filename"] == stem and "4 images" in job["msg"], job
    (args, kw), = history
    assert args[3] == "done" and kw.get("file_path") == str(dest / stem), history


def test_archive_carrying_a_video_is_never_treated_as_a_photo_set(run):
    mixed = _zip_bytes([("a.jpg", JPEG), ("clip.mp4", MP4)])
    for mode in ("off", "extract"):
        job, dest, _h, _ = run(PHOTO_LEAF, mixed, global_mode=mode)
        assert job["status"] == "done", f"PHOTO_SETS_VIDEO_ARCHIVE_TOUCHED mode={mode}: {job}"
        assert (dest / job["filename"]).read_bytes() == mixed
        (dest / job["filename"]).unlink()


# ── per-site override over the global default ─────────────────────────────────────────────

@pytest.mark.parametrize("global_mode,site_mode,expect", [
    ("zip", "extract", "extract"),
    ("extract", "off", "off"),
    ("off", "zip", "zip"),
    ("extract", "inherit", "extract"),
    ("off", "", "off"),
    (None, "inherit", "zip"),
])
def test_site_value_overrides_the_global_default(run, global_mode, site_mode, expect):
    job, dest, _h, _ = run(PHOTO_LEAF, PHOTO_SET, global_mode=global_mode, site_mode=site_mode)
    got = ("off" if job["status"] == "needs_review"
           else "extract" if (dest / PHOTO_LEAF[:-4]).is_dir() else "zip")
    assert got == expect, f"PHOTO_SETS_OVERRIDE global={global_mode} site={site_mode}: {got} {job}"


# ── traversal-safe extraction ─────────────────────────────────────────────────────────────

def test_extract_cannot_write_outside_the_scene_folder(run):
    hostile = _zip_bytes([
        ("../../escape.jpg", JPEG), ("/abs.jpg", JPEG), ("sub/../../../up.jpg", JPEG),
        ("..\\..\\win.jpg", JPEG), ("C:/drive.jpg", JPEG), ("x/1.jpg", JPEG + b"x"),
        ("y/1.jpg", JPEG + b"y"),
        ("link.jpg", b"../../outside.jpg", (stat.S_IFLNK | 0o777) << 16),
    ])
    job, dest, _h, root = run(PHOTO_LEAF, hostile, global_mode="extract")
    scene = dest / PHOTO_LEAF[:-4]
    # Every hostile member is a .jpg; tmp_path also holds the suite's history DB.
    outside = sorted(p.relative_to(root).as_posix() for p in root.rglob("*.jpg")
                     if scene not in p.parents)
    assert outside == [], f"PHOTO_SETS_TRAVERSAL wrote outside the scene folder: {outside}"
    assert all(n.startswith(scene.name + "/") for n in _files(dest)), _files(dest)
    assert not any(p.is_symlink() for p in scene.rglob("*")), "PHOTO_SETS_SYMLINK_EXTRACTED"
    names = _files(scene)
    assert all("/" not in n for n in names), names
    assert {"escape.jpg", "abs.jpg", "up.jpg", "win.jpg", "drive.jpg", "1.jpg"} <= set(names), names
    assert len(names) == 7, f"PHOTO_SETS_COLLISION_LOST: {names}"


def test_extract_writes_image_types_only(tmp_path):
    from bulk_downloader import photo_sets
    src = tmp_path / "set_1000px.zip"
    src.write_bytes(_zip_bytes([("a.jpg", JPEG), ("run.sh", b"#!/bin/sh\n"), ("b.webp", b"RIFF")]))
    n, out = photo_sets.extract_images(src, tmp_path / "set_1000px")
    assert n == 2 and _files(out) == ["a.jpg", "b.webp"], _files(out)


# ── settings surface: global key, per-site enum field, API validation ─────────────────────

def test_global_key_declared_with_zip_default():
    from bulk_downloader.global_config import GLOBAL_CONFIG_SCHEMA
    spec = GLOBAL_CONFIG_SCHEMA.get("photo_sets")
    assert spec and spec["type"] is str and spec["safe_default"] == "zip", spec


def test_per_site_field_is_an_enum_in_the_site_editor():
    from bulk_downloader.app_kernel import CFG_FIELDS
    from bulk_downloader.app_settings_center import _field_descriptor
    assert "photo_sets" in CFG_FIELDS
    d = _field_descriptor("photo_sets")
    assert d["type"] == "enum" and d["enum"] == ["inherit", "off", "zip", "extract"], d


def test_global_api_rejects_an_unknown_photo_sets_value(monkeypatch):
    from flask import Flask
    from bulk_downloader import app_global_config as agc
    cfg = {}
    monkeypatch.setattr(agc, "_app__app_cfg", lambda: cfg)
    monkeypatch.setattr(agc, "_save_app_config", lambda: None)
    app = Flask(__name__)
    app.register_blueprint(agc.global_config_bp)
    c = app.test_client()
    bad = c.post("/api/global_config", json={"photo_sets": "unpack"})
    assert bad.status_code == 400 and "photo_sets" not in cfg, bad.get_json()
    ok = c.post("/api/global_config", json={"photo_sets": "extract"})
    assert ok.status_code == 200 and cfg.get("photo_sets") == "extract", ok.get_json()

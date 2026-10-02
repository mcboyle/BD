"""The "Photo sets" download setting (dl95-ultrafilms-1, operator order O1654).

Some members areas give movies and photo sets the SAME page shape. UltraFilms is the
measured case: /members/content/item/<uuid>-<slug> is a movie on one item and a photo set
on the next, and a photo set's only download is "<slug>_1000px.zip". On test2 that saved
the-simple-things_leona-mia_1000px.zip, which held 77 .jpg and no video. That was not a
ranking bug, because the page had no video to choose. The operator wants a choice:

  off     -- refuse the item with the "No video on this page" outcome (needs_review), the
             outcome already used for a confirmed /gallery/ page. Nothing is kept.
  zip     -- DEFAULT, the behaviour before this setting: keep the archive (row 722 made zips
             pass integrity through testzip).
  extract -- unpack the archive's images into the scene folder (<archive stem>/), then drop
             the zip.

A video is always saved. The setting only acts on an archive whose every file is an image,
so a movie page and an archive that carries a video are untouched in all three modes.

The global default is app config "photo_sets". A site's own "photo_sets" overrides it;
"inherit" or blank means use the global value. An unknown value falls back to the next
level and finally to "zip". That is today's behaviour, so a bad value never drops a download.
"""
from __future__ import annotations

import os
import shutil
import stat
import sys
import zipfile
from pathlib import Path
from typing import NamedTuple

MODES = ("off", "zip", "extract")
DEFAULT_MODE = "zip"
SITE_INHERIT = "inherit"
SITE_CHOICES = (SITE_INHERIT,) + MODES

IMAGE_EXTS = frozenset({
    ".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".tif", ".tiff", ".heic", ".avif",
})
# Archive housekeeping some packers add; it is not content, so it neither makes an archive
# a photo set nor stops one from being one. It is never extracted.
_JUNK_LEAVES = frozenset({".ds_store", "thumbs.db", "desktop.ini"})
_MAX_LEAF = 200


class Outcome(NamedTuple):
    action: str          # "keep" | "refuse" | "extracted"
    path: Path           # the file kept, or the folder extracted into
    images: int = 0      # image members (refuse) / images written (extracted)
    size: int = 0        # bytes written (extracted)
    message: str = ""


def effective_mode(site_cfg) -> str:
    """The site's value if it names a mode, else the global value, else "zip"."""
    site = str((site_cfg or {}).get("photo_sets") or "").strip().lower()
    if site in MODES:
        return site
    try:
        from . import global_config
        glob = str(global_config.get("photo_sets", DEFAULT_MODE) or "").strip().lower()
    except Exception:
        glob = DEFAULT_MODE
    return glob if glob in MODES else DEFAULT_MODE


def _is_symlink(info: zipfile.ZipInfo) -> bool:
    return stat.S_ISLNK(info.external_attr >> 16)


def _leaf(name: str) -> str:
    """The member's bare file name: no directories, no drive, no backslash paths."""
    return name.replace("\\", "/").rsplit("/", 1)[-1].rsplit(":", 1)[-1].strip()


def _is_junk(info: zipfile.ZipInfo) -> bool:
    parts = info.filename.replace("\\", "/").split("/")
    return parts[0] == "__MACOSX" or _leaf(info.filename).lower() in _JUNK_LEAVES


def _is_image_name(leaf: str) -> bool:
    return os.path.splitext(leaf)[1].lower() in IMAGE_EXTS


def photo_set_image_count(path) -> int:
    """How many image members a photo-set archive holds; 0 if `path` is not one.

    A photo set is a readable .zip with at least one image, whose every regular file is an
    image. Directory entries, symlinks and packer housekeeping are ignored. Any other file,
    a video above all, means it is not a photo set.
    """
    path = Path(path)
    if path.suffix.lower() != ".zip" or not path.is_file():
        return 0
    try:
        with zipfile.ZipFile(path) as zf:
            infos = zf.infolist()
    except Exception:
        return 0
    images = 0
    for info in infos:
        if info.is_dir() or _is_symlink(info) or _is_junk(info):
            continue
        if not _is_image_name(_leaf(info.filename)):
            return 0
        images += 1
    return images


def _free_name(parent: Path, name: str) -> Path:
    candidate = parent / name
    i = 1
    while candidate.exists():
        candidate = parent / f"{name}_{i}"
        i += 1
    return candidate


def extract_images(zip_path, dest_dir) -> tuple:
    """Write the archive's image members, flattened, into a NEW folder at `dest_dir`.

    Path safety: every member is written under its bare file name, so a member name can
    never pick the directory ("../x", "/x", "C:/x", "a\\..\\x" all land as "x" inside the
    folder). Symlink members and non-image members are skipped. Clashing names get "_1",
    "_2", and so on. Each write is also checked to resolve directly inside the folder.
    The work happens in a sibling ".extracting" folder that is renamed into place only
    when every member is written. On any error that partial folder is removed and the
    error propagates, so the caller keeps the archive.

    Returns (images_written, folder). If `dest_dir` exists, a "_1" style name is used.
    """
    zip_path = Path(zip_path)
    dest = _free_name(Path(dest_dir).parent, Path(dest_dir).name)
    work = _free_name(dest.parent, dest.name + ".extracting")
    work.mkdir(parents=True)
    root = work.resolve()
    written = 0
    used = set()
    try:
        with zipfile.ZipFile(zip_path) as zf:
            for info in zf.infolist():
                if info.is_dir() or _is_symlink(info) or _is_junk(info):
                    continue
                leaf = _leaf(info.filename)[:_MAX_LEAF]
                if leaf in ("", ".", "..") or "\x00" in leaf or not _is_image_name(leaf):
                    continue
                stem, ext = os.path.splitext(leaf)
                name, i = leaf, 1
                while name.lower() in used:
                    name, i = f"{stem}_{i}{ext}", i + 1
                target = work / name
                if target.resolve().parent != root:
                    continue
                with zf.open(info) as src, open(target, "xb") as out:
                    shutil.copyfileobj(src, out, 1 << 20)
                used.add(name.lower())
                written += 1
        os.rename(work, dest)
    except BaseException:
        shutil.rmtree(work, ignore_errors=True)
        raise
    return written, dest


def no_video_message(images: int) -> str:
    return (
        "No video on this page -- its only download is a photo-set archive "
        f"({images} images, no video). The 'Photo sets' setting is off, so nothing was "
        "kept; set it to zip or extract to save photo sets.")


def apply(site_cfg, final_path) -> Outcome:
    """Apply the effective "Photo sets" mode to a file that has just been saved."""
    path = Path(final_path)
    mode = effective_mode(site_cfg)
    if mode == DEFAULT_MODE:
        return Outcome("keep", path)
    images = photo_set_image_count(path)
    if images <= 0:
        return Outcome("keep", path)
    if mode == "off":
        path.unlink(missing_ok=True)
        return Outcome("refuse", path, images, 0, no_video_message(images))
    try:
        written, folder = extract_images(path, path.with_suffix(""))
    except Exception as e:
        sys.stderr.write(f"  photo_sets: extract of {path.name} failed ({type(e).__name__}: {e}); "
                         "the archive is kept\n")
        return Outcome("keep", path)
    if written <= 0:
        os.rmdir(folder)  # the empty folder this call just made; the archive is kept
        return Outcome("keep", path)
    size = sum(p.stat().st_size for p in folder.iterdir() if p.is_file())
    path.unlink(missing_ok=True)
    return Outcome("extracted", folder, written, size)

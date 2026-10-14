"""INTEROP-EXT-4: Chrome extension ``.crx`` unpack + manifest validation + MV3
service-worker warning.

EXT-1/2/3 load an operator-supplied UNPACKED extension dir into the driven
Chromium (interop_registry provenance ``dir_sha256`` + risk-ack + ``is_permitted``
gate). EXT-4 lets the operator supply a packed ``.crx`` instead: unpack it to a
dir (which then registers exactly like an unpacked dir -- same server-side
``dir_sha256`` pin), validate its manifest, and surface MV3 service-worker
caveats for the driven Chromium.

Pure + stdlib-only (``struct`` + ``zipfile``), so fully sandbox-testable with
synthetic ``.crx`` fixtures; the only runtime gate is EXERCISING the load (headed
Chromium, which extensions force), deferred to the operator.

CRX container format (both handled)::

    CRX2:  "Cr24" | u32 version=2 | u32 pubkey_len | u32 sig_len | pubkey | sig | ZIP
    CRX3:  "Cr24" | u32 version=3 | u32 header_len | header(protobuf)          | ZIP

All integers little-endian. The signature is NOT verified here -- provenance is
the ``dir_sha256`` pin computed AFTER unpack (authoritative, operator never
supplies a hash); this module only locates + extracts the embedded ZIP.
"""
from __future__ import annotations

import hashlib
import io
import json
import os
import stat
import struct
import unicodedata
import zipfile

__all__ = ["CrxError", "parse_crx", "unpack_crx", "read_manifest",
           "validate_manifest", "crx_info"]

CRX_MAGIC = b"Cr24"

# interop-fix r1 (O1789 F6): bounds on an operator-named .crx. The file is read
# only up to MAX_CRX_BYTES; the ZIP's entry count and DECLARED uncompressed total
# are checked before anything is written (zipfile truncates each member at its
# declared file_size, so the declared total bounds the bytes extractall writes).
MAX_CRX_BYTES = 128 * 1024 * 1024
MAX_UNPACKED_BYTES = 512 * 1024 * 1024
MAX_ENTRIES = 10000
MAX_MANIFEST_BYTES = 1024 * 1024

# F14: one text for every reason the file cannot be read (missing, permission,
# not a regular file) -- the OS error named the cause, an existence oracle.
_READ_ERROR = "cannot read CRX file"


class CrxError(Exception):
    """A ``.crx`` could not be parsed/unpacked, or its manifest is unreadable."""


def parse_crx(data: bytes) -> bytes:
    """Return the embedded ZIP bytes from CRX2/CRX3 ``data``.

    Raises :class:`CrxError` on a bad magic, unknown version, a header length
    that runs past the file, or a payload that is not a ZIP."""
    if len(data) < 16 or data[:4] != CRX_MAGIC:
        raise CrxError("not a CRX file (bad magic; expected 'Cr24')")
    version = struct.unpack_from("<I", data, 4)[0]
    if version == 2:
        pubkey_len, sig_len = struct.unpack_from("<II", data, 8)
        off = 16 + pubkey_len + sig_len
    elif version == 3:
        header_len = struct.unpack_from("<I", data, 8)[0]
        off = 12 + header_len
    else:
        raise CrxError(f"unsupported CRX version {version} (expected 2 or 3)")
    if off > len(data):
        raise CrxError("truncated CRX header (declared length exceeds file size)")
    zip_bytes = data[off:]
    if zip_bytes[:2] != b"PK":
        raise CrxError("no ZIP payload found after the CRX header")
    return zip_bytes


def _read_crx(crx_path) -> bytes:
    """Read at most MAX_CRX_BYTES of a REGULAR file. Opened non-blocking and
    checked with fstat on the open fd, so a FIFO/device/dir is refused rather than
    read (or blocked on). Every read failure raises the same _READ_ERROR."""
    flags = os.O_RDONLY | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_BINARY", 0)
    try:
        fd = os.open(crx_path, flags)
    except (OSError, ValueError):
        raise CrxError(_READ_ERROR) from None
    try:
        with os.fdopen(fd, "rb") as f:
            st = os.fstat(f.fileno())
            if not stat.S_ISREG(st.st_mode):
                raise CrxError(_READ_ERROR)
            data = f.read(MAX_CRX_BYTES + 1)
    except OSError:
        raise CrxError(_READ_ERROR) from None
    if len(data) > MAX_CRX_BYTES:
        raise CrxError(f"CRX file too large (> {MAX_CRX_BYTES} B cap)")
    return data


def _check_zip(zf: zipfile.ZipFile, dest: str) -> None:
    """Entry-count cap, declared-unpacked-size cap and zip-slip, all before any
    write. Raises :class:`CrxError`."""
    infos = zf.infolist()
    if len(infos) > MAX_ENTRIES:
        raise CrxError(f"CRX zip has too many entries ({len(infos)} > {MAX_ENTRIES})")
    total = sum(i.file_size for i in infos)
    if total > MAX_UNPACKED_BYTES:
        raise CrxError(f"CRX unpacked size too large ({total} B > {MAX_UNPACKED_BYTES} B cap)")
    files: dict = {}
    dirs: set = set()
    for i in infos:
        # F6/F7 (PM ruling rule 5): no "..", absolute or symlink members at all,
        # not only the ones that would land outside dest.
        name = i.filename
        parts = name.replace("\\", "/").split("/")
        if ".." in parts or name.startswith(("/", "\\")) or (len(name) > 1 and name[1] == ":"):
            raise CrxError(f"unsafe path in CRX zip (zip-slip): {name}")
        if stat.S_ISLNK(i.external_attr >> 16):
            raise CrxError(f"symlink member in CRX zip: {name}")
        target = os.path.abspath(os.path.join(dest, name))
        if target != dest and not target.startswith(dest + os.sep):
            raise CrxError(f"unsafe path in CRX zip (zip-slip): {name}")
        # interop-fix r2 (R196-D F16-ALIAS): extractall drops "" and "." parts (and,
        # on Windows, trailing dots and illegal characters), so "./manifest.json"
        # would overwrite the manifest validated from the ZIP. One member per
        # extracted path: refuse "."/empty parts and any two members that land on
        # the same path (compared casefolded + NFC, as an insensitive disk would).
        is_dir = parts[-1] == "" and len(parts) > 1
        if is_dir:
            parts = parts[:-1]
        key = [_extract_part(p) for p in parts]
        if "" in key or "." in parts:
            raise CrxError(f"unsafe path in CRX zip (empty or '.' part): {name}")
        for n in range(1, len(key)):
            dirs.add("/".join(key[:n]))
        k = "/".join(key)
        if is_dir:
            dirs.add(k)
        elif k in files:
            raise CrxError(f"duplicate path in CRX zip: {name} (also {files[k]})")
        else:
            files[k] = name
    clash = sorted(set(files) & dirs)
    if clash:
        raise CrxError(f"duplicate path in CRX zip: {files[clash[0]]} is also a directory")


_WIN_ILLEGAL = str.maketrans(':<>|"?*', "_______")


def _extract_part(part: str) -> str:
    """One path part as extractall (posix or Windows) and a case- and
    normalisation-insensitive disk (NTFS, APFS) would see it; "" means the part
    is dropped on extraction."""
    p = unicodedata.normalize("NFC", part).translate(_WIN_ILLEGAL).rstrip(". ").casefold()
    return unicodedata.normalize("NFC", p)


def _zip_manifest(zf: zipfile.ZipFile) -> dict:
    """``manifest.json`` read from the ZIP itself (bounded), so it can be
    validated before anything is extracted."""
    try:
        info = zf.getinfo("manifest.json")
    except KeyError:
        raise CrxError("manifest.json not found in the extension") from None
    if info.file_size > MAX_MANIFEST_BYTES:
        raise CrxError(f"manifest.json too large ({info.file_size} B > {MAX_MANIFEST_BYTES} B cap)")
    try:
        return json.loads(zf.read(info).decode("utf-8"))
    except (ValueError, RecursionError) as e:
        raise CrxError(f"manifest.json is not valid JSON: {e}")


def unpack_crx(crx_path, dest_dir) -> str:
    """Unpack the ``.crx`` at ``crx_path`` into ``dest_dir`` and return the
    destination dir (created if needed). Zip-slip safe and size/entry capped:
    any entry resolving outside ``dest_dir``, or a ZIP over the caps, raises
    :class:`CrxError` before anything is written."""
    zip_bytes = parse_crx(_read_crx(crx_path))
    dest = os.path.abspath(dest_dir)
    try:
        with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
            _check_zip(zf, dest)
            os.makedirs(dest, exist_ok=True)
            zf.extractall(dest)
    except zipfile.BadZipFile as e:
        raise CrxError(f"CRX payload is not a valid ZIP: {e}")
    return dest


def read_manifest(ext_dir) -> dict:
    """Load ``manifest.json`` from an unpacked extension dir. Raises
    :class:`CrxError` if it is absent or not valid JSON."""
    p = os.path.join(ext_dir, "manifest.json")
    if not os.path.exists(p):
        raise CrxError("manifest.json not found in the extension")
    try:
        with open(p, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        raise CrxError(f"manifest.json is not valid JSON: {e}")


def validate_manifest(manifest: dict):
    """Validate an extension manifest for the driven-Chromium load path.

    Returns ``(ok, errors, warnings)``; ``ok`` is False iff there are errors.

      * errors  -- ``manifest_version`` absent or not 2/3; missing ``name``.
      * warnings -- an MV3 ``background.service_worker`` (the driven Chromium
        must run headed or ``headless=new`` -- old headless cannot host
        extension service workers -- and the worker can be evicted between
        events, so long-lived in-worker state is unreliable); MV2 deprecation.
    """
    errors: list = []
    warnings: list = []
    mv = manifest.get("manifest_version") if isinstance(manifest, dict) else None
    if mv not in (2, 3):
        errors.append(f"manifest_version must be 2 or 3 (got {mv!r})")
    name = (manifest.get("name") if isinstance(manifest, dict) else None) or ""
    if not str(name).strip():
        errors.append("manifest 'name' is required")
    bg = manifest.get("background") if isinstance(manifest, dict) else None
    if mv == 3 and isinstance(bg, dict) and bg.get("service_worker"):
        warnings.append(
            "MV3 background.service_worker: the driven Chromium must run headed "
            "or headless=new (old headless cannot host extension service "
            "workers), and the worker may be evicted between events -- "
            "long-lived in-worker state is unreliable.")
    if mv == 2:
        warnings.append(
            "MV2 is deprecated in current Chromium; prefer an MV3 build.")
    return (not errors, errors, warnings)


def crx_info(crx_path, dest_dir=None, *, dest_root=None) -> dict:
    """Unpack ``crx_path`` into ``dest_dir`` -- or, with ``dest_root``, into
    ``<dest_root>/<sha256 of the .crx bytes>`` -- read + validate the manifest,
    and return a summary dict::

        {dir, name, version, manifest_version, mv3_service_worker,
         ok, errors, warnings}

    Raises :class:`CrxError` only for unpack/manifest-read failures; a manifest
    that fails validation returns ``ok=False`` with ``errors`` and ``dir=None``:
    the manifest is read from the ZIP and validated FIRST, so a rejected CRX
    writes nothing (F6)."""
    data = _read_crx(crx_path)
    zip_bytes = parse_crx(data)
    if dest_root is not None:
        dest_dir = os.path.join(dest_root, hashlib.sha256(data).hexdigest())
    if dest_dir is None:
        raise CrxError("no unpack destination")
    dest = os.path.abspath(dest_dir)
    try:
        with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
            _check_zip(zf, dest)
            manifest = _zip_manifest(zf)
            ok, errors, warnings = validate_manifest(manifest)
            ext_dir = None
            if ok:
                os.makedirs(dest, exist_ok=True)
                zf.extractall(dest)
                ext_dir = dest
    except zipfile.BadZipFile as e:
        raise CrxError(f"CRX payload is not a valid ZIP: {e}")
    bg = manifest.get("background") if isinstance(manifest, dict) else None
    return {
        "dir": ext_dir,
        "name": manifest.get("name") if isinstance(manifest, dict) else None,
        "version": manifest.get("version") if isinstance(manifest, dict) else None,
        "manifest_version": manifest.get("manifest_version") if isinstance(manifest, dict) else None,
        "mv3_service_worker": bool(
            isinstance(manifest, dict)
            and manifest.get("manifest_version") == 3
            and isinstance(bg, dict) and bg.get("service_worker")),
        "ok": ok,
        "errors": errors,
        "warnings": warnings,
    }

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

import io
import json
import os
import struct
import zipfile

__all__ = ["CrxError", "parse_crx", "unpack_crx", "read_manifest",
           "validate_manifest", "crx_info"]

CRX_MAGIC = b"Cr24"


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


def unpack_crx(crx_path, dest_dir) -> str:
    """Unpack the ``.crx`` at ``crx_path`` into ``dest_dir`` and return the
    destination dir (created if needed). Zip-slip safe: any entry resolving
    outside ``dest_dir`` raises :class:`CrxError` before anything is written."""
    try:
        with open(crx_path, "rb") as f:
            data = f.read()
    except OSError as e:
        raise CrxError(f"cannot read CRX: {e}")
    zip_bytes = parse_crx(data)
    dest = os.path.abspath(dest_dir)
    os.makedirs(dest, exist_ok=True)
    try:
        with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
            for name in zf.namelist():
                target = os.path.abspath(os.path.join(dest, name))
                if target != dest and not target.startswith(dest + os.sep):
                    raise CrxError(f"unsafe path in CRX zip (zip-slip): {name}")
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


def crx_info(crx_path, dest_dir) -> dict:
    """Unpack ``crx_path`` into ``dest_dir``, read + validate the manifest, and
    return a summary dict::

        {dir, name, version, manifest_version, mv3_service_worker,
         ok, errors, warnings}

    Raises :class:`CrxError` only for unpack/manifest-read failures; a manifest
    that unpacks but fails validation returns ``ok=False`` with ``errors``."""
    ext_dir = unpack_crx(crx_path, dest_dir)
    manifest = read_manifest(ext_dir)
    ok, errors, warnings = validate_manifest(manifest)
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

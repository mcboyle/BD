"""INTEROP-EXT-4 -- Chrome extension .crx unpack + manifest validation + MV3
service-worker warning.

RED-first. On pristine v3.66.689:
  * ``bulk_downloader.extension_crx`` does not exist -> every unit test RED
    (ImportError).
  * ``app_interop.api_interop_register`` does not reference ``extension_crx`` /
    handle a ``.crx`` item_id -> the structural wiring guard RED.
After the cut all pass.

EXT-1/2/3 load an operator-supplied UNPACKED extension dir into the driven
Chromium via the interop_registry (provenance ``dir_sha256`` + risk-ack +
``is_permitted`` gate). EXT-4 lets the operator supply a packed ``.crx``: unpack
it to a dir (which then registers exactly like an unpacked dir), validate the
manifest, and surface MV3 service-worker caveats. Pure + stdlib-only, so fully
sandbox-testable with synthetic ``.crx`` fixtures; the only runtime gate is
EXERCISING the load (headed Chromium), deferred to the operator.
"""
import io
import json
import os
import re
import struct
import zipfile


# ---------------------------------------------------------------------------
# synthetic .crx fixture builders (no external tooling)
# ---------------------------------------------------------------------------
def _zip_bytes(files: dict) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, content in files.items():
            zf.writestr(name, content)
    return buf.getvalue()


def _crx3(zip_bytes: bytes, header: bytes = b"\x00" * 8) -> bytes:
    # "Cr24" | u32 version=3 | u32 header_len | header | ZIP
    return b"Cr24" + struct.pack("<I", 3) + struct.pack("<I", len(header)) + header + zip_bytes


def _crx2(zip_bytes: bytes, pubkey: bytes = b"\x01" * 4, sig: bytes = b"\x02" * 4) -> bytes:
    # "Cr24" | u32 version=2 | u32 pubkey_len | u32 sig_len | pubkey | sig | ZIP
    return (b"Cr24" + struct.pack("<I", 2)
            + struct.pack("<I", len(pubkey)) + struct.pack("<I", len(sig))
            + pubkey + sig + zip_bytes)


_MV3 = {"manifest_version": 3, "name": "Test MV3", "version": "1.0",
        "background": {"service_worker": "sw.js"}}
_MV2 = {"manifest_version": 2, "name": "Test MV2", "version": "1.0",
        "background": {"scripts": ["bg.js"]}}


def _crx_file(tmp, crx_bytes, name="ext.crx"):
    p = os.path.join(tmp, name)
    with open(p, "wb") as f:
        f.write(crx_bytes)
    return p


# ---------------------------------------------------------------------------
# parse_crx: locate the embedded ZIP in CRX2 / CRX3
# ---------------------------------------------------------------------------
def test_parse_crx3_returns_zip_payload():
    from bulk_downloader.extension_crx import parse_crx
    z = _zip_bytes({"manifest.json": json.dumps(_MV3)})
    out = parse_crx(_crx3(z))
    assert out == z, "CRX3 parse must return the exact embedded ZIP bytes"
    assert out[:2] == b"PK"


def test_parse_crx2_returns_zip_payload():
    from bulk_downloader.extension_crx import parse_crx
    z = _zip_bytes({"manifest.json": json.dumps(_MV2)})
    out = parse_crx(_crx2(z))
    assert out == z, "CRX2 parse must return the exact embedded ZIP bytes"


def test_parse_crx_bad_magic_raises():
    from bulk_downloader.extension_crx import parse_crx, CrxError
    try:
        parse_crx(b"NOPE" + b"\x00" * 20)
        assert False, "bad magic must raise CrxError"
    except CrxError:
        pass


def test_parse_crx_unknown_version_raises():
    from bulk_downloader.extension_crx import parse_crx, CrxError
    body = b"Cr24" + struct.pack("<I", 9) + b"\x00" * 16
    try:
        parse_crx(body)
        assert False, "unknown version must raise CrxError"
    except CrxError:
        pass


# ---------------------------------------------------------------------------
# unpack_crx: extract to a dir, zip-slip safe
# ---------------------------------------------------------------------------
def test_unpack_crx_extracts_manifest(tmp_path):
    from bulk_downloader.extension_crx import unpack_crx
    z = _zip_bytes({"manifest.json": json.dumps(_MV3), "sw.js": "// worker"})
    crx = _crx_file(str(tmp_path), _crx3(z))
    dest = unpack_crx(crx, os.path.join(str(tmp_path), "unpacked"))
    assert os.path.exists(os.path.join(dest, "manifest.json"))
    assert os.path.exists(os.path.join(dest, "sw.js"))


def test_unpack_crx_rejects_zip_slip(tmp_path):
    from bulk_downloader.extension_crx import unpack_crx, CrxError
    z = _zip_bytes({"../escape.js": "pwned", "manifest.json": json.dumps(_MV2)})
    crx = _crx_file(str(tmp_path), _crx3(z))
    try:
        unpack_crx(crx, os.path.join(str(tmp_path), "unpacked2"))
        assert False, "a zip entry escaping the dest dir must raise CrxError"
    except CrxError:
        pass


# ---------------------------------------------------------------------------
# validate_manifest: errors + MV3 warning
# ---------------------------------------------------------------------------
def test_validate_mv3_service_worker_warns():
    from bulk_downloader.extension_crx import validate_manifest
    ok, errors, warnings = validate_manifest(_MV3)
    assert ok and not errors, (ok, errors)
    assert any("service_worker" in w or "headless=new" in w for w in warnings), warnings


def test_validate_mv2_ok_with_deprecation_warning_no_mv3_warn():
    from bulk_downloader.extension_crx import validate_manifest
    ok, errors, warnings = validate_manifest(_MV2)
    assert ok and not errors
    assert any("MV2" in w or "deprecated" in w for w in warnings), warnings
    assert not any("service_worker" in w for w in warnings)


def test_validate_missing_manifest_version_is_error():
    from bulk_downloader.extension_crx import validate_manifest
    ok, errors, _ = validate_manifest({"name": "x"})
    assert not ok and any("manifest_version" in e for e in errors), errors


def test_validate_missing_name_is_error():
    from bulk_downloader.extension_crx import validate_manifest
    ok, errors, _ = validate_manifest({"manifest_version": 3})
    assert not ok and any("name" in e for e in errors), errors


# ---------------------------------------------------------------------------
# crx_info: combined unpack + read + validate summary
# ---------------------------------------------------------------------------
def test_crx_info_summary(tmp_path):
    from bulk_downloader.extension_crx import crx_info
    z = _zip_bytes({"manifest.json": json.dumps(_MV3), "sw.js": "// w"})
    crx = _crx_file(str(tmp_path), _crx3(z))
    info = crx_info(crx, os.path.join(str(tmp_path), "u3"))
    assert info["name"] == "Test MV3"
    assert info["manifest_version"] == 3
    assert info["mv3_service_worker"] is True
    assert info["ok"] is True
    assert os.path.isdir(info["dir"])


# ---------------------------------------------------------------------------
# structural wiring: the register route accepts a .crx
# ---------------------------------------------------------------------------
def _app_interop_src():
    here = os.path.dirname(os.path.abspath(__file__))
    root = os.path.dirname(here)
    p = os.path.join(root, "bulk_downloader", "app_interop.py")
    return open(p, encoding="utf-8").read()


def test_register_route_handles_crx():
    src = _app_interop_src()
    m = re.search(r"def api_interop_register\(.*?(?=\n@interop_bp\.route|\ndef )", src, re.S)
    assert m, "could not locate api_interop_register"
    body = m.group(0)
    assert "extension_crx" in body, "register must unpack a .crx via extension_crx"
    assert ".crx" in body, "register must branch on a .crx item_id"

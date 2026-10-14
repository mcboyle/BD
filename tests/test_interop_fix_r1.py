"""interop-fix r1 (O1789 app-core-surface F6, F11 interop half, F12, F13, F14, F15).

  F6  a .crx item_id was read whole (no cap) and extractall()ed BEFORE its manifest
      was validated: a 20 KB CRX whose manifest fails validation wrote 20 MiB next to
      itself. Now: file-size cap, entry-count cap, declared-unpacked-size cap, and the
      manifest is read from the ZIP and validated before anything is extracted.
  F11 a deeply nested JSON body raised RecursionError out of get_json -> 500.
  F12 enable {"enabled": "false"} enabled the item (bool("false") is True).
  F13 a corrupt registry file read as {} and the next write replaced it (records lost).
  F14 the CRX read error echoed the OS error ("[Errno 2] No such file ...").
  F15 the routes called _check_csrf() and discarded its 403.
  F7  (AMEND 0854Z, PM ruling ANSWER-pm-B-F7-0858Z) any server dir/file was accepted
      as an item_id and hashed/read/unpacked. Now: realpath strictly inside
      BD_HOME/interop/<kind>/ or an operator interop_extra_roots dir, tree-hash caps,
      no symlink following, CRX unpack into the root, legacy records flagged.
  F16-ALIAS (r2, R196-D) "./manifest.json" after the validated "manifest.json"
      overwrote it on extract. Now: "."/empty parts and colliding member paths refused.

Local Flask test client + scratch BD_HOME; no live app, no network.
"""
from __future__ import annotations

import io
import json
import hashlib
import os
import pathlib
import sys
import warnings
import zipfile

import pytest

BD_GATE_SCOPE = "module"

TOK = "interop-fix-token-not-a-secret"
AUTH = {"Authorization": f"Bearer {TOK}"}
MV3 = {"manifest_version": 3, "name": "Fix MV3", "version": "1.0"}


@pytest.fixture
def client(fresh_app, monkeypatch, tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("BD_HOME", str(home))
    monkeypatch.setenv("BD_AUTH_TOKEN", TOK)
    return fresh_app


def _home():
    return os.environ["BD_HOME"]


def _zip(files: dict, compression=zipfile.ZIP_DEFLATED) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression) as z:
        for name, data in files.items():
            z.writestr(name, data)
    return buf.getvalue()


def _crx(path, files: dict, compression=zipfile.ZIP_DEFLATED):
    path.write_bytes(b"Cr24" + (3).to_bytes(4, "little") + (0).to_bytes(4, "little")
                     + _zip(files, compression))
    return path


def _root(kind="chromium_extension"):
    """The per-kind interop root (F7): path item_ids must live inside it."""
    p = os.path.join(_home(), "interop", kind)
    os.makedirs(p, exist_ok=True)
    return pathlib.Path(p)


def _dirs_in(p):
    """Every directory under p -- an extraction leaves at least one."""
    return sorted(str(x) for x in pathlib.Path(p).rglob("*") if x.is_dir())


def _register(client, item_id, kind="chromium_extension"):
    return client.post("/api/interop/register", headers=AUTH,
                       json={"kind": kind, "item_id": str(item_id)})


def _tree_bytes(d):
    total = 0
    for root, _dirs, files in os.walk(d):
        for f in files:
            total += os.path.getsize(os.path.join(root, f))
    return total


# --------------------------------------------------------------------------- F6
def test_f6_cap_literals():
    from bulk_downloader import extension_crx as crx
    assert crx.MAX_CRX_BYTES == 128 * 1024 * 1024
    assert crx.MAX_UNPACKED_BYTES == 512 * 1024 * 1024
    assert crx.MAX_ENTRIES == 10000
    assert crx.MAX_MANIFEST_BYTES == 1024 * 1024


def test_f6_invalid_manifest_extracts_nothing(client):
    # The P09 shape: manifest has no manifest_version -> 400; nothing may be written.
    root = _root()
    crx = _crx(root / "x.crx", {"manifest.json": '{"name":"p","version":"1"}',
                                "big.bin": b"\0" * (2 * 1024 * 1024)})
    r = _register(client, crx)
    made = _dirs_in(root)
    wrote = sum(_tree_bytes(d) for d in made)
    assert r.status_code == 400, r.get_json()
    assert "invalid CRX manifest" in r.get_json()["error"]
    assert not made, f"F6: invalid manifest yet {wrote} B extracted to {made}"


def test_f6_missing_manifest_extracts_nothing(client):
    root = _root()
    crx = _crx(root / "m.crx", {"big.bin": b"\0" * 4096})
    r = _register(client, crx)
    assert r.status_code == 400
    assert not _dirs_in(root), "F6: manifest-less CRX was extracted"


def test_f6_unpacked_size_cap_refused_before_write(client, monkeypatch):
    from bulk_downloader import extension_crx as crx_mod
    monkeypatch.setattr(crx_mod, "MAX_UNPACKED_BYTES", 1024 * 1024, raising=False)
    root = _root()
    crx = _crx(root / "b.crx", {"manifest.json": json.dumps(MV3),
                                "big.bin": b"\0" * (2 * 1024 * 1024)})
    r = _register(client, crx)
    made = _dirs_in(root)
    wrote = sum(_tree_bytes(d) for d in made)
    assert r.status_code == 400, f"F6: over-cap CRX -> {r.status_code}; {wrote} B extracted"
    assert "too large" in r.get_json()["error"]
    assert not made, f"F6: over-cap CRX extracted {wrote} B"


def test_f6_entry_count_cap(client, monkeypatch):
    from bulk_downloader import extension_crx as crx_mod
    monkeypatch.setattr(crx_mod, "MAX_ENTRIES", 5, raising=False)
    files = {"manifest.json": json.dumps(MV3)}
    files.update({f"f{i}.js": "//" for i in range(10)})
    root = _root()
    crx = _crx(root / "n.crx", files)
    r = _register(client, crx)
    assert r.status_code == 400, f"F6: 11 entries over a cap of 5 -> {r.status_code}"
    assert "too many entries" in r.get_json()["error"]
    assert not _dirs_in(root)


def test_f6_crx_file_size_cap(client, monkeypatch):
    from bulk_downloader import extension_crx as crx_mod
    monkeypatch.setattr(crx_mod, "MAX_CRX_BYTES", 1024, raising=False)
    root = _root()
    crx = _crx(root / "s.crx", {"manifest.json": json.dumps(MV3),
                                "pad.bin": os.urandom(4096)}, zipfile.ZIP_STORED)
    r = _register(client, crx)
    assert r.status_code == 400, f"F6: {crx.stat().st_size} B CRX over a 1024 B cap -> {r.status_code}"
    assert "too large" in r.get_json()["error"]
    assert not _dirs_in(root)


def test_f6_crx_read_is_bounded(tmp_path, monkeypatch):
    # The read itself is capped (not just checked afterwards): a huge file is never
    # pulled into memory whole.
    from bulk_downloader import extension_crx as crx_mod
    monkeypatch.setattr(crx_mod, "MAX_CRX_BYTES", 1024, raising=False)
    crx = _crx(tmp_path / "r.crx", {"manifest.json": json.dumps(MV3),
                                    "pad.bin": os.urandom(8192)}, zipfile.ZIP_STORED)
    sizes = []
    real_fdopen, real_open = os.fdopen, open

    class Spy:
        def __init__(self, fh):
            self._fh = fh

        def read(self, n=-1):
            sizes.append(n)
            return self._fh.read(n)

        def __getattr__(self, a):
            return getattr(self._fh, a)

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return self._fh.__exit__(*exc)

    monkeypatch.setattr(os, "fdopen", lambda *a, **k: Spy(real_fdopen(*a, **k)))
    monkeypatch.setattr("builtins.open", lambda f, *a, **k: Spy(real_open(f, *a, **k))
                        if str(f) == str(crx) else real_open(f, *a, **k))
    with pytest.raises(crx_mod.CrxError, match="too large"):
        crx_mod.crx_info(str(crx), str(tmp_path / "r.crx.unpacked"))
    assert sizes and all(n is not None and 0 <= n <= 1025 for n in sizes), \
        f"F6: CRX read sizes {sizes} for an {crx.stat().st_size} B file under a 1024 B cap"


def test_f6_oversized_manifest_refused(tmp_path, monkeypatch):
    from bulk_downloader import extension_crx as crx_mod
    monkeypatch.setattr(crx_mod, "MAX_MANIFEST_BYTES", 64, raising=False)
    crx = _crx(tmp_path / "mf.crx", {"manifest.json": json.dumps(dict(MV3, description="d" * 200))})
    with pytest.raises(crx_mod.CrxError, match="manifest.json too large"):
        crx_mod.crx_info(str(crx), str(tmp_path / "mf.crx.unpacked"))
    assert not (tmp_path / "mf.crx.unpacked").exists()


def test_f6_control_valid_crx_registers(client):
    crx = _crx(_root() / "ok.crx", {"manifest.json": json.dumps(MV3), "sw.js": "// w"})
    r = _register(client, crx)
    assert r.status_code == 200, r.get_json()
    unpacked = pathlib.Path(r.get_json()["record"]["item_id"])
    assert (unpacked / "manifest.json").is_file() and (unpacked / "sw.js").is_file()


# --------------------------------------------------------------------------- F11
DEEP = "[" * 100000 + "]" * 100000


@pytest.mark.parametrize("route", ["register", "acknowledge", "enable"])
def test_f11_deep_json_is_400(client, monkeypatch, route):
    monkeypatch.setitem(client.application.config, "PROPAGATE_EXCEPTIONS", False)
    r = client.post(f"/api/interop/{route}", data=DEEP, content_type="application/json", headers=AUTH)
    assert r.status_code == 400, f"F11: 100k-deep JSON to /api/interop/{route} -> {r.status_code}"


def test_f11_control_shallow_json(client, monkeypatch):
    monkeypatch.setitem(client.application.config, "PROPAGATE_EXCEPTIONS", False)
    r = client.post("/api/interop/register", headers=AUTH, json={"kind": "jd_plugin", "item_id": "c"})
    assert r.status_code == 200


# --------------------------------------------------------------------------- F12
def _reg_ack(client, iid):
    assert client.post("/api/interop/register", headers=AUTH,
                       json={"kind": "jd_plugin", "item_id": iid}).status_code == 200
    assert client.post("/api/interop/acknowledge", headers=AUTH,
                       json={"kind": "jd_plugin", "item_id": iid}).status_code == 200


@pytest.mark.parametrize("val", ["false", "true", "0", 0, 1, None, [], {}])
def test_f12_enabled_must_be_json_bool(client, val):
    from bulk_downloader import interop_registry as ir
    _reg_ack(client, "p")
    r = client.post("/api/interop/enable", headers=AUTH,
                    json={"kind": "jd_plugin", "item_id": "p", "enabled": val})
    assert r.status_code == 400, f"F12: enabled={val!r} -> {r.status_code} {r.get_json()}"
    assert ir.get("jd_plugin", "p")["enabled"] is False
    assert ir.is_permitted("jd_plugin", "p") is False


@pytest.mark.parametrize("val", [True, False])
def test_f12_control_json_bool(client, val):
    from bulk_downloader import interop_registry as ir
    _reg_ack(client, "p")
    r = client.post("/api/interop/enable", headers=AUTH,
                    json={"kind": "jd_plugin", "item_id": "p", "enabled": val})
    assert r.status_code == 200 and r.get_json()["enabled"] is val
    assert ir.get("jd_plugin", "p")["enabled"] is val


def test_f12_control_omitted_enabled_disables(client):
    _reg_ack(client, "p")
    r = client.post("/api/interop/enable", headers=AUTH, json={"kind": "jd_plugin", "item_id": "p"})
    assert r.status_code == 200 and r.get_json()["enabled"] is False


# --------------------------------------------------------------------------- F13
def _corrupt_registry(client):
    _reg_ack(client, "keep-me")
    reg = os.path.join(_home(), "interop_registry.json")
    with open(reg, encoding="utf-8") as fh:
        good = fh.read()
    torn = good[:-5]
    with open(reg, "w", encoding="utf-8") as fh:
        fh.write(torn)
    return reg, torn


@pytest.mark.parametrize("route,body", [
    ("register", {"kind": "jd_plugin", "item_id": "new2"}),
    ("acknowledge", {"kind": "jd_plugin", "item_id": "keep-me"}),
    ("enable", {"kind": "jd_plugin", "item_id": "keep-me", "enabled": True}),
])
def test_f13_corrupt_registry_not_overwritten(client, route, body):
    reg, torn = _corrupt_registry(client)
    r = client.post(f"/api/interop/{route}", headers=AUTH, json=body)
    with open(reg, encoding="utf-8") as fh:
        after = fh.read()
    assert after == torn, (f"F13: {route} over a corrupt registry -> {r.status_code}; "
                           f"file rewritten, keep-me present={'keep-me' in after}")
    assert r.status_code >= 400 and r.get_json()["ok"] is False
    assert "unreadable" in r.get_json()["error"]


@pytest.mark.parametrize("content", ["[]", "", "{\"a\": ", "\xff"])
def test_f13_corrupt_shapes_refuse_write(client, content):
    from bulk_downloader import interop_registry as ir
    reg = os.path.join(_home(), "interop_registry.json")
    with open(reg, "w", encoding="latin-1") as fh:
        fh.write(content)
    try:
        ir.register("jd_plugin", "x")
        raised = None
    except Exception as e:  # noqa: BLE001 -- the type is asserted below
        raised = e
    with open(reg, encoding="latin-1") as fh:
        after = fh.read()
    assert after == content, f"F13: register over a corrupt registry {content!r} rewrote it -> {after[:60]!r}"
    assert isinstance(raised, OSError) and "unreadable" in str(raised), repr(raised)


def test_f13_reads_fail_closed_on_corrupt(client):
    from bulk_downloader import interop_registry as ir
    _reg_ack(client, "keep-me")
    ir.set_enabled("jd_plugin", "keep-me", True)
    assert ir.is_permitted("jd_plugin", "keep-me") is True
    reg = os.path.join(_home(), "interop_registry.json")
    with open(reg, "a", encoding="utf-8") as fh:
        fh.write("garbage")
    assert ir.is_permitted("jd_plugin", "keep-me") is False
    assert ir.list_all() == []


def test_f13_control_valid_registry_keeps_records(client):
    from bulk_downloader import interop_registry as ir
    _reg_ack(client, "keep-me")
    assert client.post("/api/interop/register", headers=AUTH,
                       json={"kind": "jd_plugin", "item_id": "new1"}).status_code == 200
    assert ir.get("jd_plugin", "keep-me")["risk_acknowledged"] is True
    assert ir.get("jd_plugin", "new1") is not None


def test_f13_control_missing_registry_starts_empty(client):
    assert not os.path.exists(os.path.join(_home(), "interop_registry.json"))
    assert client.post("/api/interop/register", headers=AUTH,
                       json={"kind": "jd_plugin", "item_id": "first"}).status_code == 200


# --------------------------------------------------------------------------- F14
def test_f14_read_error_text_is_errno_free(client):
    # In-root paths reach the read; each cause must give the same text.
    root = _root()
    cases = {"missing": root / "nope" / "x.crx"}
    d = root / "dir.crx"
    d.mkdir()
    cases["directory"] = d
    if os.geteuid() != 0:
        noperm = _crx(root / "noperm.crx", {"manifest.json": json.dumps(MV3)})
        noperm.chmod(0)
        cases["no-permission"] = noperm
    errs = {}
    for label, p in cases.items():
        r = _register(client, p)
        assert r.status_code == 400, (label, r.status_code)
        errs[label] = r.get_json()["error"]
    for label, e in errs.items():
        assert "Errno" not in e and "No such file" not in e and "Permission" not in e \
            and "directory" not in e.lower().replace("crx", ""), f"F14: {label} -> {e!r}"
    assert len(set(errs.values())) == 1, f"F14: read errors differ by cause: {errs}"


def test_f14_outside_root_existence_is_not_disclosed(client, tmp_path):
    # Out-of-root paths are refused before any open: an existing readable CRX, a
    # missing one and a directory give the same refusal.
    real = _crx(tmp_path / "real.crx", {"manifest.json": json.dumps(MV3)})
    d = tmp_path / "d.crx"
    d.mkdir()
    errs = {}
    for label, p in {"exists": real, "missing": tmp_path / "nope.crx", "directory": d}.items():
        r = _register(client, p)
        errs[label] = (r.status_code, r.get_json()["error"])
    assert set(errs.values()) == {(400, "item_id outside interop roots")}, f"F14/F7: {errs}"


def test_f14_control_bad_magic_still_reported(client):
    p = _root() / "bad.crx"
    p.write_bytes(b"NOTACRX-" * 4)
    r = _register(client, p)
    assert r.status_code == 400 and "bad magic" in r.get_json()["error"]


# --------------------------------------------------------------------------- F15
@pytest.mark.parametrize("route,body", [
    ("register", {"kind": "jd_plugin", "item_id": "csrf"}),
    ("acknowledge", {"kind": "jd_plugin", "item_id": "csrf"}),
    ("enable", {"kind": "jd_plugin", "item_id": "csrf", "enabled": True}),
])
def test_f15_route_csrf_refusal_is_returned(client, monkeypatch, route, body):
    from flask import jsonify
    from bulk_downloader import interop_registry as ir
    ir.register("jd_plugin", "csrf")
    before = ir.get("jd_plugin", "csrf")
    app_mod = sys.modules["bulk_downloader.app"]
    monkeypatch.setattr(app_mod, "_check_csrf",
                        lambda *a, **k: (jsonify({"error": "csrf refused (test)"}), 403))
    r = client.post(f"/api/interop/{route}", headers=AUTH, json=body)
    assert r.status_code == 403, f"F15: route-level _check_csrf 403 discarded on {route} -> {r.status_code}"
    after = ir.get("jd_plugin", "csrf")
    assert (after["risk_acknowledged"], after["enabled"]) == (before["risk_acknowledged"], before["enabled"])


def test_f15_control_csrf_pass_through(client):
    r = client.post("/api/interop/register", headers=AUTH, json={"kind": "jd_plugin", "item_id": "ok"})
    assert r.status_code == 200


# --------------------------------------------------------------------------- F7
# PM ruling ANSWER-pm-B-F7-0858Z: one root per kind under BD_HOME/interop/ + the
# operator's interop_extra_roots; realpath strictly inside a root of its own kind.
OUTSIDE = "item_id outside interop roots"
PATH_KINDS = ("chromium_extension", "ytdlp_plugin", "gallerydl_plugin")


def _ext_dir(parent, name="ext", files=None):
    d = pathlib.Path(parent) / name
    d.mkdir(parents=True)
    for rel, data in (files or {"manifest.json": json.dumps(MV3)}).items():
        (d / rel).parent.mkdir(parents=True, exist_ok=True)
        (d / rel).write_bytes(data if isinstance(data, bytes) else data.encode())
    return d


def _offline_hash(d):
    # Independent of interop_registry: relpath \0 bytes \0 over sorted regular files.
    h = hashlib.sha256()
    for f in sorted(p for p in pathlib.Path(d).rglob("*") if p.is_file() and not p.is_symlink()):
        h.update(str(f.relative_to(d)).encode())
        h.update(b"\0")
        h.update(f.read_bytes())
        h.update(b"\0")
    return h.hexdigest()


def _no_hash(monkeypatch):
    from bulk_downloader import interop_registry as ir
    calls = []
    real = ir.dir_sha256
    monkeypatch.setattr(ir, "dir_sha256", lambda p, **k: calls.append(str(p)) or real(p, **k))
    return calls


@pytest.mark.parametrize("kind", PATH_KINDS)
def test_f7_root_slash_is_400(client, monkeypatch, kind):
    calls = _no_hash(monkeypatch)
    r = _register(client, "/", kind)
    assert r.status_code == 400, f"F7: item_id '/' as {kind} -> {r.status_code}"
    assert r.get_json()["error"] == OUTSIDE
    assert calls == [], f"F7: dir_sha256 computed for a refused path: {calls}"


@pytest.mark.parametrize("kind", PATH_KINDS)
def test_f7_root_itself_is_400(client, kind):
    r = _register(client, _root(kind), kind)
    assert r.status_code == 400 and r.get_json()["error"] == OUTSIDE, (kind, r.status_code)


def test_f7_symlink_out_of_root_is_400(client, tmp_path, monkeypatch):
    outside = _ext_dir(tmp_path, "outside")
    link = _root() / "link"
    link.symlink_to(outside, target_is_directory=True)
    calls = _no_hash(monkeypatch)
    r = _register(client, link)
    assert r.status_code == 400, f"F7: symlink out of the root -> {r.status_code} {r.get_json()}"
    assert r.get_json()["error"] == OUTSIDE
    assert calls == []


def test_f7_sibling_kind_root_is_400(client):
    d = _ext_dir(_root("ytdlp_plugin"), "plug")
    r = _register(client, d, "chromium_extension")
    assert r.status_code == 400 and r.get_json()["error"] == OUTSIDE, r.status_code


def test_f7_dotdot_escape_is_400(client, tmp_path):
    _ext_dir(tmp_path, "esc")
    r = _register(client, str(_root()) + "/../../../esc")
    assert r.status_code == 400 and r.get_json()["error"] == OUTSIDE


def test_f7_outside_dir_is_400_and_not_hashed(client, tmp_path, monkeypatch):
    # The P10 shape: an arbitrary server dir is hashed and the digest returned.
    d = _ext_dir(tmp_path, "anywhere")
    calls = _no_hash(monkeypatch)
    r = _register(client, d)
    assert r.status_code == 400, f"F7: out-of-root dir hashed -> {r.status_code} {r.get_json()}"
    assert calls == []


def test_f7_control_in_root_dir_registers_with_correct_hash(client):
    d = _ext_dir(_root(), "good", {"manifest.json": json.dumps(MV3), "js/a.js": "//a", "a-b": "x"})
    r = _register(client, d)
    assert r.status_code == 200, r.get_json()
    assert r.get_json()["record"]["sha256"] == _offline_hash(d)


@pytest.mark.parametrize("kind", ["ytdlp_plugin", "gallerydl_plugin"])
def test_f7_control_in_root_plugin_kinds_register(client, kind):
    r = _register(client, _root(kind) / "p", kind)
    assert r.status_code == 200, (kind, r.get_json())


def test_f7_control_jd_plugin_hostname_not_contained(client):
    r = _register(client, "example.com", "jd_plugin")
    assert r.status_code == 200


def test_f7_extra_root_from_config_only(client, tmp_path, monkeypatch):
    from bulk_downloader import global_config
    extra = tmp_path / "extra"
    d = _ext_dir(extra, "e")
    assert _register(client, d).status_code == 400            # not a root yet
    real_get = global_config.get
    monkeypatch.setattr(global_config, "get", lambda k, default=None: [str(extra), "rel/dir"]
                        if k == "interop_extra_roots" else real_get(k, default))
    assert _register(client, d).status_code == 200            # operator root
    assert _register(client, extra).status_code == 400         # the root itself
    r = client.post("/api/interop/register", headers=AUTH,
                    json={"kind": "chromium_extension", "item_id": str(tmp_path / "x"),
                          "interop_extra_roots": [str(tmp_path)]})
    assert r.status_code == 400                                # never from the body


def test_f7_dir_hash_caps_literals():
    from bulk_downloader import interop_registry as ir
    assert ir.MAX_TREE_FILES == 20000 and ir.MAX_TREE_BYTES == 2 * 1024 ** 3


def test_f7_over_file_cap_is_400(client, monkeypatch):
    from bulk_downloader import interop_registry as ir
    monkeypatch.setattr(ir, "MAX_TREE_FILES", 3)
    d = _ext_dir(_root(), "many", {f"f{i}": "x" for i in range(5)})
    r = _register(client, d)
    assert r.status_code == 400, f"F7: 5 files over a cap of 3 -> {r.status_code} {r.get_json()}"
    assert ir.get("chromium_extension", str(d)) is None


def test_f7_over_byte_cap_is_400_without_reading(client):
    from bulk_downloader import interop_registry as ir
    d = _ext_dir(_root(), "huge", {"manifest.json": json.dumps(MV3)})
    with open(d / "sparse.bin", "wb") as fh:          # sparse: no 2 GiB of I/O
        fh.truncate(ir.MAX_TREE_BYTES + 1)
    r = _register(client, d)
    assert r.status_code == 400, f"F7: 2 GiB+1 tree -> {r.status_code}"
    assert ir.dir_sha256(d) == ir.OVER_CAP
    with pytest.raises(ir.TreeTooLarge):
        ir.dir_sha256(d, strict=True)


def test_f7_over_cap_is_never_permitted(client):
    from bulk_downloader import interop_registry as ir
    ir.register("chromium_extension", "/x", sha256=ir.OVER_CAP)
    ir.acknowledge("chromium_extension", "/x")
    ir.set_enabled("chromium_extension", "/x", True)
    assert ir.is_permitted("chromium_extension", "/x", ir.OVER_CAP) is False


def test_f7_tree_hash_does_not_follow_symlinks(client, tmp_path):
    from bulk_downloader import interop_registry as ir
    secret = tmp_path / "secret.txt"
    secret.write_text("one")
    d = _ext_dir(_root(), "lnk")
    (d / "s").symlink_to(secret)
    h1 = ir.dir_sha256(d)
    secret.write_text("two -- different content")
    assert ir.dir_sha256(d) == h1, "F7: dir_sha256 read through a symlink inside the tree"


@pytest.mark.parametrize("member", ["../evil.js", "a/../../evil.js", "/abs.js", "a/../b.js"])
def test_f7_crx_dotdot_or_absolute_member_refused(client, member):
    root = _root()
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("manifest.json", json.dumps(MV3))
        z.writestr(zipfile.ZipInfo(member), "//")
    (root / "dd.crx").write_bytes(b"Cr24" + (3).to_bytes(4, "little") + (0).to_bytes(4, "little")
                                  + buf.getvalue())
    r = _register(client, root / "dd.crx")
    assert r.status_code == 400, f"F7: CRX member {member!r} -> {r.status_code}"
    assert not _dirs_in(root)


def test_f7_crx_symlink_member_refused(client):
    root = _root()
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("manifest.json", json.dumps(MV3))
        zi = zipfile.ZipInfo("link.js")
        zi.external_attr = (0o120777 << 16)
        z.writestr(zi, "/etc/passwd")
    (root / "sl.crx").write_bytes(b"Cr24" + (3).to_bytes(4, "little") + (0).to_bytes(4, "little")
                                  + buf.getvalue())
    r = _register(client, root / "sl.crx")
    assert r.status_code == 400, f"F7: CRX symlink member -> {r.status_code}"
    assert "symlink" in r.get_json()["error"]
    assert not _dirs_in(root)


def test_f7_crx_unpacks_into_root_by_sha(client, tmp_path):
    root = _root()
    crx = _crx(root / "ok.crx", {"manifest.json": json.dumps(MV3)})
    r = _register(client, crx)
    assert r.status_code == 200, r.get_json()
    want = os.path.join(os.path.realpath(root), hashlib.sha256(crx.read_bytes()).hexdigest())
    assert r.get_json()["record"]["item_id"] == want
    assert not (root / "ok.crx.unpacked").exists()


def test_f7_legacy_outside_record_flagged_not_erased(client, tmp_path):
    from bulk_downloader import interop_registry as ir
    legacy = str(_ext_dir(tmp_path, "legacy"))
    inroot = str(_ext_dir(_root(), "in"))
    ir.register("chromium_extension", legacy, sha256="ab" * 32)
    ir.register("chromium_extension", inroot, sha256="cd" * 32)
    ir.acknowledge("chromium_extension", legacy)
    items = {it["item_id"]: it for it in client.get("/api/interop/registry", headers=AUTH).get_json()["items"]}
    assert items[legacy]["outside_roots"] is True
    assert items[inroot]["outside_roots"] is False
    r = client.post("/api/interop/enable", headers=AUTH,
                    json={"kind": "chromium_extension", "item_id": legacy, "enabled": True})
    assert r.status_code == 400 and r.get_json()["error"] == OUTSIDE, r.status_code
    rec = ir.get("chromium_extension", legacy)
    assert rec is not None and rec["enabled"] is False and rec["risk_acknowledged"] is True
    assert client.post("/api/interop/enable", headers=AUTH,
                       json={"kind": "chromium_extension", "item_id": legacy,
                             "enabled": False}).status_code == 200
    assert client.post("/api/interop/enable", headers=AUTH,
                       json={"kind": "chromium_extension", "item_id": inroot,
                             "enabled": True}).status_code == 200


# ------------------------------------------------------------- r2 F16-ALIAS
# R196-D: _zip_manifest validates the member named exactly "manifest.json", but
# extractall drops "." parts, so a later "./manifest.json" replaced it on disk.
def _crx_members(path, members):
    """A CRX from (name, data) pairs, duplicates allowed."""
    buf = io.BytesIO()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")  # zipfile warns on a duplicate name
        with zipfile.ZipFile(buf, "w") as z:
            for name, data in members:
                z.writestr(name, data)
    path.write_bytes(b"Cr24" + (3).to_bytes(4, "little") + (0).to_bytes(4, "little")
                     + buf.getvalue())
    return path


EVIL = {"manifest_version": 3, "name": "evil", "version": "9",
        "background": {"service_worker": "sw.js"}}


@pytest.mark.parametrize("second", [{"manifest_version": 1}, EVIL], ids=["invalid", "evil-sw"])
def test_f16_alias_manifest_cannot_replace_validated_one(client, second):
    root = _root()
    crx = _crx_members(root / "alias.crx", [("manifest.json", json.dumps(MV3)),
                                            ("./manifest.json", json.dumps(second))])
    r = _register(client, crx)
    made = _dirs_in(root)
    on_disk = [json.loads((pathlib.Path(d) / "manifest.json").read_text())
               for d in made if (pathlib.Path(d) / "manifest.json").is_file()]
    assert r.status_code == 400, (
        f"F16-ALIAS: './manifest.json' after a valid manifest -> {r.status_code}, on disk {on_disk}")
    assert "'.' part" in r.get_json()["error"], r.get_json()
    assert not made, f"F16-ALIAS: refused CRX still extracted to {made}"


@pytest.mark.parametrize("members", [
    [("manifest.json", "{}"), ("a/./b.js", "1")],
    [("manifest.json", "{}"), ("a//b.js", "1")],
    [("manifest.json", "{}"), ("sw.js", "1"), ("sw.js", "2")],
    [("manifest.json", "{}"), ("Manifest.json", "{}")],
    [("manifest.json", "{}"), ("manifest.json.", "{}")],
    [("manifest.json", "{}"), ("manifest.json ", "{}")],
    [("manifest.json", "{}"), ("lib", "1"), ("lib/x.js", "2")],
    [("manifest.json", "{}"), ("lib/x.js", "2"), ("LIB", "1")],
    [("manifest.json", "{}"), ("...", "1")],
    [("manifest.json", "{}"), ("a?b.js", "1"), ("a_b.js", "2")],
    [("manifest.json", "{}"), ("caf" + chr(0xE9) + ".js", "1"), ("cafe" + chr(0x301) + ".js", "2")],
], ids=["dot-part", "empty-part", "exact-dup", "case", "win-trailing-dot",
        "win-trailing-space", "file-then-dir", "dir-then-file-case", "dots-only",
        "win-illegal-char", "nfc-nfd"])
def test_f16_aliased_member_paths_refused_before_write(tmp_path, members):
    from bulk_downloader import extension_crx as crx_mod
    crx = _crx_members(tmp_path / "a.crx", [(n, json.dumps(MV3) if n == "manifest.json" else d)
                                            for n, d in members])
    dest = tmp_path / "out"
    with pytest.raises(crx_mod.CrxError) as e:
        crx_mod.crx_info(str(crx), str(dest))
    assert not dest.exists(), f"F16-ALIAS: {[n for n, _ in members]} wrote {dest}"
    assert "duplicate path" in str(e.value) or "'.' part" in str(e.value), str(e.value)


def test_f16_control_nested_dirs_and_dir_entries_register(client):
    crx = _crx_members(_root() / "nest.crx", [
        ("manifest.json", json.dumps(MV3)), ("lib/", ""), ("lib/x.js", "1"),
        ("lib/y.js", "2"), ("lib/sub/z.js", "3"), ("libx.js", "4"), ("a.b.js", "5")])
    r = _register(client, crx)
    assert r.status_code == 200, f"F16 control: plain nested CRX refused: {r.get_json()}"
    d = pathlib.Path(r.get_json()["record"]["item_id"])
    assert json.loads((d / "manifest.json").read_text()) == MV3
    assert (d / "lib/sub/z.js").read_text() == "3" and (d / "a.b.js").read_text() == "5"

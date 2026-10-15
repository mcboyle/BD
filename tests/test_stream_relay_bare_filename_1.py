"""stream404-bare-filename r1 -- /stream/<token> 404s every real download.

history.filename is a bare BASENAME (runner_transport records
``final_path.name``), but stream_relay.serve_range handed it straight to
os.path.isfile, i.e. resolved it against the process CWD. Live on spare3
(acd7231b): POST /api/stream/token/5 -> 200, GET /stream/<tok> -> 404, 0 B;
.95 is the same (app cwd /home/mboyle/BulkDownloader, files under
/home/mboyle/Downloads/bulk_downloader/<site>/).

A bare name is now resolved against the configured download roots, and the
resolved file must stay inside them: a ``..`` walk or a symlink pointing out
answers 403, a basename that matches several files answers 404 (never a guess).
"""
from __future__ import annotations

import os

import pytest

from bulk_downloader import app_state, db, global_config, stream_relay

BD_GATE_SCOPE = "module"

BODY = b"\x00\x00\x00\x18ftypmp42" + bytes(range(256)) * 4


def _drain(gen):
    return b"".join(gen) if gen else b""


@pytest.fixture
def lib(tmp_path, monkeypatch):
    """Download root tmp/lib for site s1; CWD is tmp/cwd, where
    ``../outside/secret.mp4`` DOES exist -- so a cwd-relative lookup is
    observable, not just absent."""
    root = tmp_path / "lib"
    root.mkdir()
    (tmp_path / "outside").mkdir()
    (tmp_path / "outside" / "secret.mp4").write_bytes(b"SECRET")
    cwd = tmp_path / "cwd"
    cwd.mkdir()
    monkeypatch.chdir(cwd)
    monkeypatch.setattr(app_state, "s_cfg", {"s1": {"download_dir": str(root)}})
    return root


def test_bare_filename_served_from_download_root(lib):
    (lib / "Scene A [360p].mp4").write_bytes(BODY)
    gen, headers, status = stream_relay.serve_range("Scene A [360p].mp4", "")
    assert status == 200, (
        f"STREAM_BARE_FILENAME_404: bare history filename not resolved against "
        f"the download root {lib}: status {status}")
    assert _drain(gen) == BODY and headers["Content-Length"] == str(len(BODY))


def test_bare_filename_range_request(lib):
    (lib / "Scene A [360p].mp4").write_bytes(BODY)
    gen, headers, status = stream_relay.serve_range("Scene A [360p].mp4", "bytes=0-11")
    assert status == 206, f"STREAM_BARE_FILENAME_404: range on bare name -> {status}"
    assert _drain(gen) == BODY[:12]


def test_bare_filename_in_template_subfolder(lib):
    """The recorded basename lost the subfolder the filename template made."""
    (lib / "Studio" / "2026").mkdir(parents=True)
    (lib / "Studio" / "2026" / "Nested.mp4").write_bytes(BODY)
    gen, _headers, status = stream_relay.serve_range("Nested.mp4", "")
    assert status == 200, f"STREAM_BARE_FILENAME_NESTED_404: status {status}"
    assert _drain(gen) == BODY


def test_traversal_out_of_root_refused(lib):
    gen, _headers, status = stream_relay.serve_range("../outside/secret.mp4", "")
    body = _drain(gen)
    assert status == 403 and body == b"", (
        f"STREAM_TRAVERSAL_SERVED: '../outside/secret.mp4' -> {status}, {len(body)} B")


def test_symlink_out_of_root_refused(lib, tmp_path):
    (lib / "link.mp4").symlink_to(tmp_path / "outside" / "secret.mp4")
    gen, _headers, status = stream_relay.serve_range("link.mp4", "")
    body = _drain(gen)
    assert status == 403 and body == b"", (
        f"STREAM_SYMLINK_OUT_SERVED: link.mp4 -> outside root: {status}, {len(body)} B")


def test_same_basename_under_two_roots_not_guessed(lib, tmp_path, monkeypatch):
    lib2 = tmp_path / "lib2"
    lib2.mkdir()
    (lib / "dup.mp4").write_bytes(b"ONE")
    (lib2 / "dup.mp4").write_bytes(b"TWO")
    monkeypatch.setattr(app_state, "s_cfg", {"s1": {"download_dir": str(lib)},
                                             "s2": {"download_dir": str(lib2)}})
    gen, _headers, status = stream_relay.serve_range("dup.mp4", "")
    assert gen is None and status == 404, f"STREAM_AMBIGUOUS_GUESSED: status {status}"


def test_control_missing_bare_name_is_404(lib):
    gen, _headers, status = stream_relay.serve_range("nope.mp4", "")
    assert gen is None and status == 404


def test_control_absolute_path_unchanged(lib):
    p = lib / "Abs.mp4"
    p.write_bytes(BODY)
    gen, _headers, status = stream_relay.serve_range(str(p), "")
    assert status == 200 and _drain(gen) == BODY


def test_control_no_roots_configured_is_404(lib, monkeypatch):
    (lib / "Scene A [360p].mp4").write_bytes(BODY)
    monkeypatch.setattr(app_state, "s_cfg", {})
    gen, _headers, status = stream_relay.serve_range("Scene A [360p].mp4", "")
    assert gen is None and status == 404


def test_stream_route_serves_bare_history_filename(fresh_app, tmp_path, monkeypatch):
    """The live path: history row with a bare filename -> mint -> GET /stream."""
    monkeypatch.setattr(global_config, "_CONFIG_FILE", tmp_path / "app_config.json")
    monkeypatch.setattr(global_config, "_cached", None)
    monkeypatch.setattr(global_config, "_cached_mtime", 0.0)
    root = tmp_path / "lib"
    root.mkdir()
    (root / "Masked _ Site [360p].mp4").write_bytes(BODY)
    monkeypatch.setitem(app_state.s_cfg, "s1", {"download_dir": str(root)})
    assert not os.path.exists("Masked _ Site [360p].mp4")  # not in CWD
    with db.db_conn() as cx:
        hid = cx.execute(
            "INSERT INTO history(site_id, status, filename, file_size) "
            "VALUES ('s1', 'done', 'Masked _ Site [360p].mp4', ?)",
            (len(BODY),)).lastrowid
    r = fresh_app.post(f"/api/stream/token/{hid}", json={"ttl_seconds": 600, "max_uses": 3})
    assert r.status_code == 200, r.get_json()
    s = fresh_app.get(f"/stream/{r.get_json()['token']}")
    assert s.status_code == 200, (
        f"STREAM_BARE_FILENAME_404: GET /stream/<tok> for history row {hid} "
        f"(bare filename) -> {s.status_code}, {len(s.data)} B")
    assert s.data == BODY

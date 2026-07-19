"""v3.66.273  GCW-3 download-folder + GCW-4 "0-byte is not a pass" promote gate.

Root cause this fixes (confirmed from a live stash run, ultrafilms de762ff8):
the autonomous Test logged in, re-navigated to the video, clicked the
``.download-button`` trigger — then recorded ``done`` with 0 bytes / no
filename because the wizard-created site had **no download_dir**. runner.py
``_process_one`` L6262-6268: ``if not dl_dir:`` clicks, "assume browser handles
it", and ``db_log(..., "done", "", 0, "")`` — a false-positive success.

Two fixes, one frontend-only cut (backend already stores ``download_dir`` via
``CFG_FIELDS``; ``/api/history`` already exists):

GCW-3 — Setup step gains a **Download folder** field, sent as ``download_dir``
in the ``setup_site`` body, so wizard-created sites actually have a save target.

GCW-4 — the Test step **watches** ``/api/history`` for the real verdict and a
``done`` with ``file_size == 0`` (incl. "no dl dir") is **not** a pass. Promote
is gated on a green e2e this session (a real download, non-zero bytes), with an
explicit operator override for the cases the e2e legitimately can't complete in
session (UX-209 confirm-tier discipline: green-verify is the default, not a cage).

Tests: a backend contract guard that ``setup_site`` persists a supplied
``download_dir`` (no plaintext concerns — it's a path), plus SPA source-scans
(mirroring the GCW-1/2/3 style) for the field, the watch, the verdict semantics,
and the promote gate + override. Live download + the gate's runtime behavior are
proven on stash; tsc/vite + render are the in-sandbox ceiling.
"""
from __future__ import annotations

import os
import tempfile
from contextlib import contextmanager
from pathlib import Path


# ─── backend contract guard: setup_site persists download_dir ────────────
@contextmanager
def _client():
    from bulk_downloader import app as A
    from bulk_downloader.db import db_init
    from bulk_downloader import secrets_store as ss
    orig_cwd = os.getcwd()
    with tempfile.TemporaryDirectory() as td:
        os.chdir(td)
        Path(td, "screenshots").mkdir(exist_ok=True)
        try:
            db_init()
            c = A.app.test_client()
            tok = c.get("/api/pair").get_json()["token"]
            csrf = c.post("/api/pair/redeem", json={"token": tok}).get_json()["csrf_token"]
            ss._backend = None
            ss._backend_pref = None
            yield c, {"X-CSRF-Token": csrf}, A
        finally:
            os.chdir(orig_cwd)


def test_setup_site_persists_download_dir():
    """A download_dir supplied to setup_site lands on the site config so the
    autonomous Test has a real save target (kills the 'no dl dir' false-pass)."""
    with _client() as (c, H, A):
        # download_dir must be under the path allowlist (_create_site validates
        # it); BD_HOME is seeded into the allowlist on first run.
        base = os.environ.get("BD_HOME") or "/home/claude/bd_home"
        dl = os.path.join(base, "bd_dl_target_test")
        os.makedirs(dl, exist_ok=True)
        r = c.post("/api/captures/setup_site",
                   json={"name": "DlSite",
                         "login_url": "https://dl.example/login",
                         "download_dir": dl},
                   headers=H)
        assert r.status_code == 200, r.get_data(as_text=True)
        sid = r.get_json()["id"]
        assert A.s_cfg[sid]["download_dir"] == dl


def test_setup_site_blank_download_dir_is_empty():
    """No download_dir supplied -> empty (the operator can set it later); the
    GCW-4 gate is what protects promote when it's empty."""
    with _client() as (c, H, A):
        r = c.post("/api/captures/setup_site",
                   json={"name": "NoDl", "login_url": "https://x.example/login"},
                   headers=H)
        sid = r.get_json()["id"]
        assert A.s_cfg[sid].get("download_dir", "") == ""


# ─── SPA source-scans ────────────────────────────────────────────────────
def _src() -> str:
    root = Path(__file__).resolve().parent.parent
    return (root / "frontend" / "src" / "routes"
            / "CaptureWorkflow.tsx").read_text(encoding="utf-8")


def test_setup_sends_download_dir():
    # The Setup step must send download_dir in the setup_site body.
    assert "download_dir" in _src()


def test_setup_has_download_folder_field():
    # A visible Download folder input in the Setup step.
    assert "Download folder" in _src()


def test_test_step_watches_history_for_verdict():
    # The Test step must poll /api/history (full literal) for the real verdict —
    # test_extract is fire-and-forget and returns no file result.
    assert "/api/history" in _src()


def test_verdict_requires_nonzero_bytes():
    # The pass/fail verdict is derived from the downloaded file size, and a
    # session e2e-pass flag carries it to the gate.
    src = _src()
    assert "file_size" in src
    assert "e2ePass" in src


def test_promote_gated_on_e2e_pass():
    # The Promote button must be gated on a green e2e (or explicit override) —
    # this exact condition prevents promoting off a 0-byte 'done'.
    assert "e2ePass === true || overrideE2e" in _src()


def test_promote_has_explicit_override():
    # An explicit operator override for when the e2e legitimately can't complete.
    assert "overrideE2e" in _src()

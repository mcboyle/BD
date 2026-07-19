"""v3.66.509: GUI plugin install -- POST /api/plugins/install (multipart upload)
+ GET /api/plugins/installed (managed-install registry + disclaimer + ack state).

The upload route stages the uploaded file to a temp path and hands it to the
existing managed install path (plugins.install_plugin): manifest ast-read,
api-range gate, at-your-own-risk ack, atomic stage, registry record. NO plugin
code is executed, and install does NOT enable/load (operator hits Reload after).

RED on pristine 508: both routes 404.

Runner-safe: zero-arg fns, tempfile.mkdtemp, plugins._plugin_dir overridden +
restored in finally, sessionless test client (so _check_csrf skips).
"""
import io
import sys
import json
import shutil
import tempfile
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from bulk_downloader import plugins as P  # noqa: E402


def _with_tmp_plugin_dir():
    tmp = tempfile.mkdtemp()
    pdir = Path(tmp) / "plugins"
    pdir.mkdir(parents=True, exist_ok=True)
    orig = P._plugin_dir
    P._plugin_dir = lambda: pdir

    def restore():
        P._plugin_dir = orig
        shutil.rmtree(tmp, ignore_errors=True)

    return pdir, restore


def _man(name="x", version="1.0.0", **extra):
    keys = {"name": name, "version": version, "api_version": 2}
    keys.update(extra)
    return "PLUGIN = " + json.dumps(keys) + "\n"


def _client():
    from bulk_downloader import app as A
    return A.app.test_client()


def _upload(c, text, name="up.py", **fields):
    data = {"file": (io.BytesIO(text.encode("utf-8")), name)}
    data.update(fields)
    return c.post("/api/plugins/install", data=data,
                  content_type="multipart/form-data")


# ── GET /api/plugins/installed ─────────────────────────────────────────
def test_installed_endpoint_returns_registry_disclaimer_and_ack():
    pdir, restore = _with_tmp_plugin_dir()
    try:
        r = _client().get("/api/plugins/installed")
        assert r.status_code == 200, r.get_json()
        d = r.get_json()
        assert d.get("ok") is True
        assert isinstance(d.get("installed"), list)
        assert isinstance(d.get("disclaimer"), str) and d["disclaimer"]
        assert d.get("risk_acknowledged") in (True, False)
    finally:
        restore()


# ── POST /api/plugins/install ──────────────────────────────────────────
def test_install_no_file_is_400():
    pdir, restore = _with_tmp_plugin_dir()
    try:
        r = _client().post("/api/plugins/install", data={},
                           content_type="multipart/form-data")
        assert r.status_code == 400
        assert r.get_json().get("installed") is False
    finally:
        restore()


def test_install_without_ack_refused_with_disclaimer_and_no_file_landed():
    pdir, restore = _with_tmp_plugin_dir()
    try:
        r = _upload(_client(), _man(name="noack"), name="noack.py")
        assert r.status_code == 400
        d = r.get_json()
        assert d.get("installed") is False
        assert "risk" in (d.get("reason") or "").lower()
        assert d.get("disclaimer")                       # GUI surfaces this
        assert not (pdir / "noack.py").exists()          # nothing staged
    finally:
        restore()


def test_install_with_ack_lands_file_and_registers():
    pdir, restore = _with_tmp_plugin_dir()
    try:
        r = _upload(_client(), _man(name="good"), name="good.py", ack="1")
        assert r.status_code == 200, r.get_json()
        d = r.get_json()
        assert d.get("installed") is True
        assert d.get("file") == "good.py"
        assert (pdir / "good.py").exists()
        assert "good.py" in [rec["file"] for rec in P.installed_registry()]
    finally:
        restore()


def test_persist_ack_lets_a_later_install_skip_ack():
    pdir, restore = _with_tmp_plugin_dir()
    try:
        c = _client()
        # first upload persists the ack
        r1 = _upload(c, _man(name="a"), name="a.py", persist_ack="1")
        assert r1.status_code == 200 and r1.get_json().get("installed") is True
        # a second upload with NO ack flag now succeeds (flag is persisted)
        r2 = _upload(c, _man(name="b"), name="b.py")
        assert r2.status_code == 200, r2.get_json()
        assert r2.get_json().get("installed") is True
        assert P.read_config().get("risk_acknowledged") is True
    finally:
        restore()


def test_binary_upload_is_rejected_not_crashed():
    pdir, restore = _with_tmp_plugin_dir()
    try:
        data = {"file": (io.BytesIO(b"\x00\x01\x02\xff garbage"), "x.py"),
                "ack": "1"}
        r = _client().post("/api/plugins/install", data=data,
                           content_type="multipart/form-data")
        assert r.status_code == 400
        assert r.get_json().get("installed") is False
    finally:
        restore()

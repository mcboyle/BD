"""Bucket 2 (GUI-config parity): the `.env` editor for deploy/path/port/host env vars.

These env vars are read at boot / by external CLI tools, never by the running
service, so a *live* GUI write is meaningless. The honest design is
edit -> persist to a `.env` -> "applies on restart", with validation. This suite
pins that contract RED-first (every assertion should fail on pristine v3.66.503,
which has no `_envfile` loader and no `/api/settings/envfile` editor endpoint).

Covers:
  * bulk_downloader._envfile boot loader: resolve path, parse, setdefault semantics
    (real env / systemd always wins; the .env is a fallback seed).
  * the editor key set == _DEPLOY_ONLY (minus host-managed) + BD_DISABLE_VPN_RUNTIME,
    re-derived from tools/config_surface_inventory.py source (drift guard).
  * GET /api/settings/envfile: saved (.env) vs effective (os.environ) + restart_pending.
  * POST validation: foundation-path rejection (400, not persisted), port rejection,
    bool-ish disable flags, unknown-key rejection.
  * atomic .env write merges managed keys + preserves unrelated lines.
  * forbidden-artifact guard: diff_release_zips lists `.env` as forbidden-sensitive.

Sandbox-valid: stdlib + the blueprint module (Flask in prestaged_site_packages);
does NOT boot app.py. Zero-arg test functions per run_tests.py; restores os.environ
in try/finally (monkeypatch is unreliable in the custom harness).
"""
import ast
import os
import sys
import json
import tempfile
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO))

from flask import Flask  # noqa: E402
from bulk_downloader import app_envfile_editor as EE  # noqa: E402
from bulk_downloader import _envfile as EF  # noqa: E402


def _app():
    app = Flask(__name__)
    EE.register_routes(app)
    return app


def _deploy_only_from_source():
    """Re-derive _DEPLOY_ONLY + _IMPORT_TIME from the inventory tool's source
    (stale-copy-of-derived-fact: never trust a hard-coded list)."""
    src = (_REPO / "tools" / "config_surface_inventory.py").read_text()
    tree = ast.parse(src)
    out = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name) and t.id in ("_DEPLOY_ONLY", "_IMPORT_TIME"):
                    out[t.id] = set(ast.literal_eval(node.value))
    return out


# host-managed infra vars that must NOT be GUI-writable (mirrors the plan)
# host-managed infra vars + the bootstrap .env pointer that must NOT be GUI-writable
_HOST_MANAGED = {"DISPLAY", "PLAYWRIGHT_BROWSERS_PATH", "APPDATA",
                 "FLASK_DEBUG", "BULK_DOWNLOADER_DEBUG", "BD_ENVFILE"}


# ── 1. boot loader: parse + setdefault (real env wins) ───────────────────────
def test_envfile_parse_skips_blanks_and_comments():
    parsed = EF.parse_envfile("# comment\n\nBD_PORT=9000\nBD_HOST=0.0.0.0\n  # indented\nBAD LINE\n")
    assert parsed.get("BD_PORT") == "9000"
    assert parsed.get("BD_HOST") == "0.0.0.0"
    assert "# comment" not in parsed
    # a line with no '=' is ignored, not crashed on
    assert "BAD LINE" not in parsed


def test_load_envfile_setdefault_real_env_wins():
    d = tempfile.mkdtemp()
    envpath = Path(d) / ".env"
    envpath.write_text("BD_ENVTEST_NEW=fromfile\nBD_ENVTEST_PRESET=fromfile\n")
    saved = dict(os.environ)
    try:
        os.environ.pop("BD_ENVTEST_NEW", None)
        os.environ["BD_ENVTEST_PRESET"] = "fromenv"   # real env already set
        os.environ["BD_ENVFILE"] = str(envpath)
        n = EF.load_envfile()
        # unset key gets seeded from the file
        assert os.environ.get("BD_ENVTEST_NEW") == "fromfile"
        # already-set key is NOT overwritten (setdefault semantics)
        assert os.environ.get("BD_ENVTEST_PRESET") == "fromenv"
        assert isinstance(n, int) and n >= 1
    finally:
        os.environ.clear()
        os.environ.update(saved)


def test_resolve_envfile_path_honors_override_else_cwd():
    saved = dict(os.environ)
    try:
        os.environ["BD_ENVFILE"] = "/tmp/explicit/.env"
        assert str(EF.resolve_envfile_path()) == "/tmp/explicit/.env"
        os.environ.pop("BD_ENVFILE", None)
        # falls back to cwd/.env (systemd WorkingDirectory=APP_DIR), NOT BD_HOME
        assert EF.resolve_envfile_path() == Path.cwd() / ".env"
    finally:
        os.environ.clear()
        os.environ.update(saved)


def test_envfile_imported_at_package_top():
    """The loader must run before any env-reading import -> imported at the very
    top of bulk_downloader/__init__.py (before __version__)."""
    init_src = (_REPO / "bulk_downloader" / "__init__.py").read_text()
    assert "_envfile" in init_src
    assert init_src.index("_envfile") < init_src.index("__version__")


# ── 2. editor key set == derived deploy set (drift guard) ────────────────────
def test_editor_key_set_matches_deploy_only_source():
    sets = _deploy_only_from_source()
    expected = (sets["_DEPLOY_ONLY"] - _HOST_MANAGED) | {"BD_DISABLE_VPN_RUNTIME"}
    keys = {r["name"] for r in EE._keys()}
    assert keys == expected, f"drift: only-in-editor={keys - expected} only-in-source={expected - keys}"
    # host-managed infra vars must never be writable
    assert keys.isdisjoint(_HOST_MANAGED)


def test_editor_marks_foundation_paths():
    meta = {r["name"]: r for r in EE._keys()}
    for k in ("BD_REPO", "BD_ROOT", "BD_HOME", "BD_INSTALL_DIR"):
        assert meta[k]["foundation"] is True
    # a port is not a foundation path
    assert meta["BD_PORT"]["foundation"] is False
    # the VPN disable flag carries a danger note
    assert meta["BD_DISABLE_VPN_RUNTIME"]["danger"] is True
    assert meta["BD_DISABLE_VPN_RUNTIME"]["danger_note"]


def test_editor_reader_class_drives_honest_copy():
    """Per-var 'applies' reader-class (deep plan 1.1): bind-time ports are hard
    restart; call-time path roots are restart-recommended (split-brain); CLI-only
    vars say so; BD_URL is informational."""
    meta = {r["name"]: r for r in EE._keys()}
    assert meta["BD_PORT"]["applies"] == "restart"
    assert meta["BD_HOST"]["applies"] == "restart"
    assert meta["BD_HOME"]["applies"] == "restart-recommended"
    assert meta["BD_CAPTURES_ROOT"]["applies"] == "restart-recommended"
    assert meta["BD_RELEASE_ARCHIVE"]["applies"] == "cli-tool"
    assert meta["BD_DOWNLOAD_DIR"]["applies"] == "cli-tool"
    assert meta["BD_URL"]["applies"] == "informational"
    assert meta["BD_DISABLE_VPN_RUNTIME"]["applies"] == "restart"
    # every applies value has UX copy
    for r in EE._keys():
        assert EF.APPLIES.get(r["applies"])


# ── 3. GET state: saved vs effective + restart_pending ───────────────────────
def test_get_envfile_surfaces_saved_vs_live_pending():
    d = tempfile.mkdtemp()
    envpath = Path(d) / ".env"
    envpath.write_text("BD_PORT=9000\n")
    saved = dict(os.environ)
    try:
        os.environ["BD_ENVFILE"] = str(envpath)
        os.environ["BD_PORT"] = "5555"        # live differs from saved
        c = _app().test_client()
        r = c.get("/api/settings/envfile")
        assert r.status_code == 200
        body = r.get_json()
        assert body["ok"] is True
        rows = {x["name"]: x for x in body["env"]}
        assert rows["BD_PORT"]["saved"] == "9000"
        assert rows["BD_PORT"]["effective"] == "5555"
        assert rows["BD_PORT"]["restart_pending"] is True
        # a key neither in .env nor env is not pending
        assert rows["BD_KB_DIR"]["restart_pending"] is False
    finally:
        os.environ.clear()
        os.environ.update(saved)


# ── 4. POST validation ───────────────────────────────────────────────────────
def test_post_rejects_nonexistent_foundation_path():
    d = tempfile.mkdtemp()
    envpath = Path(d) / ".env"
    saved = dict(os.environ)
    try:
        os.environ["BD_ENVFILE"] = str(envpath)
        c = _app().test_client()
        r = c.post("/api/settings/envfile",
                   json={"updates": {"BD_REPO": "/no/such/path/xyz123"}})
        assert r.status_code == 400
        body = r.get_json()
        assert body["ok"] is False
        assert "BD_REPO" in body["rejected"]
        # nothing persisted on a rejected write
        assert not envpath.exists() or "BD_REPO" not in envpath.read_text()
    finally:
        os.environ.clear()
        os.environ.update(saved)


def test_post_rejects_bad_port():
    d = tempfile.mkdtemp()
    envpath = Path(d) / ".env"
    saved = dict(os.environ)
    try:
        os.environ["BD_ENVFILE"] = str(envpath)
        c = _app().test_client()
        for bad in ("notanint", "0", "70000", "-1"):
            r = c.post("/api/settings/envfile", json={"updates": {"BD_PORT": bad}})
            assert r.status_code == 400, f"{bad!r} should be rejected"
            assert "BD_PORT" in r.get_json()["rejected"]
    finally:
        os.environ.clear()
        os.environ.update(saved)


def test_post_rejects_unknown_key():
    saved = dict(os.environ)
    try:
        os.environ["BD_ENVFILE"] = str(Path(tempfile.mkdtemp()) / ".env")
        c = _app().test_client()
        r = c.post("/api/settings/envfile", json={"updates": {"NOT_A_BD_VAR": "x"}})
        assert r.status_code == 400
        assert "NOT_A_BD_VAR" in r.get_json()["rejected"]
    finally:
        os.environ.clear()
        os.environ.update(saved)


def test_post_accepts_and_writes_atomically_preserving_other_lines():
    d = tempfile.mkdtemp(); Path(d, "existing_dir").mkdir()
    envpath = Path(d) / ".env"
    envpath.write_text("# user comment\nUNRELATED_LINE=keepme\nBD_PORT=1111\n")
    saved = dict(os.environ)
    try:
        os.environ["BD_ENVFILE"] = str(envpath)
        c = _app().test_client()
        r = c.post("/api/settings/envfile", json={"updates": {
            "BD_PORT": "8080",
            "BD_REPO": str(Path(d, "existing_dir")),   # valid existing dir
            "BD_DISABLE_VPN_RUNTIME": "1",
        }})
        assert r.status_code == 200, r.get_json()
        body = r.get_json()
        assert body["ok"] is True and body["restart_required"] is True
        txt = envpath.read_text()
        parsed = EF.parse_envfile(txt)
        assert parsed["BD_PORT"] == "8080"            # updated in place
        assert parsed["BD_REPO"] == str(Path(d, "existing_dir"))
        assert parsed["BD_DISABLE_VPN_RUNTIME"] == "1"
        # unrelated content preserved
        assert parsed["UNRELATED_LINE"] == "keepme"
        assert "# user comment" in txt
    finally:
        os.environ.clear()
        os.environ.update(saved)


# ── 5. forbidden-artifact guard: .env never ships ────────────────────────────
def test_diff_release_zips_forbids_dotenv():
    import importlib.util
    p = _REPO / "tools" / "diff_release_zips.py"
    spec = importlib.util.spec_from_file_location("diff_release_zips", p)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert mod.FORBIDDEN_SENSITIVE.search(".env")
    assert mod.FORBIDDEN_SENSITIVE.search("BulkDownloader/.env")

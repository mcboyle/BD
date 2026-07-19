"""v3.66.317 — CLI->GUI parity Phase 4.2: the FINAL env tranche.

The 4 operator-deferred Advanced vars + the 3 historically-EXCLUDED vars, all
promoted to full live-writable GUI controls per an explicit operator directive
(single-operator LAN; risk acknowledged in-session). Three of the seven had NO
functional read site before this cut; option 2 (build the gate) was chosen, so
this cut WIRES them:

  FULL via the canonical store > env > default call-time pattern (GUI write wins):
    BD_AUTH_TOKEN  -> app._expected_token()            (store > env > app_config)
    BD_TOKEN       -> app._accepted_tokens()           (NEW: a second accepted token)
    BD_DEV_MODE    -> dev_tools.is_dev_mode()          (NEW gate: DISABLE hard-kill,
                                                        else store > env > default-ON)
    BD_TEST_MODE   -> app.app_test_mode()              (NEW: advisory flag, /api/health;
                                                        no security/behavior effect)
    BD_COCKPIT_SHELL     -> cockpit_shell._shell_pref()/shell_enabled()
    BD_COCKPIT_TASKS     -> cockpit_core.tasks_root()  (value honored, no jail)
    BD_FRAMEWORK_REPORTS -> cockpit_core.reports_root() (value honored, no jail)

RED-first: every promote/wire/classify assertion fails on pristine v3.66.316.
Zero-arg tests; env + store restored in try/finally; chdir to a tmpdir so the
global_config store file is isolated.
"""
import json
import os
import sys
import tempfile
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO))
sys.path.insert(0, str(_REPO / "tools"))

_PROMOTED_ENV = (
    "BD_AUTH_TOKEN", "BD_TOKEN", "BD_DEV_MODE", "BD_TEST_MODE",
    "BD_COCKPIT_SHELL", "BD_COCKPIT_TASKS", "BD_FRAMEWORK_REPORTS",
)
# Each promoted env var -> its global_config store key.
_STORE_KEY = {
    "BD_AUTH_TOKEN": "auth_token", "BD_TOKEN": "bd_token",
    "BD_DEV_MODE": "dev_mode", "BD_TEST_MODE": "test_mode",
    "BD_COCKPIT_SHELL": "cockpit_shell", "BD_COCKPIT_TASKS": "cockpit_tasks",
    "BD_FRAMEWORK_REPORTS": "framework_reports",
}


def _fresh_store(d: dict) -> None:
    from bulk_downloader import global_config as GC
    Path("app_config.json").write_text(json.dumps(d), encoding="utf-8")
    GC._cached = None
    GC._cached_mtime = 0.0


def _save_env(*names):
    return {n: os.environ.get(n) for n in names}


def _restore_env(saved):
    for n, v in saved.items():
        if v is None:
            os.environ.pop(n, None)
        else:
            os.environ[n] = v


# ── schema ───────────────────────────────────────────────────────────────────
def test_schema_has_all_seven_keys():
    from bulk_downloader import global_config as GC
    s = GC.GLOBAL_CONFIG_SCHEMA
    assert s["auth_token"]["type"] is str
    assert s["bd_token"]["type"] is str
    assert s["dev_mode"]["type"] is str
    assert s["test_mode"]["type"] is bool
    assert s["cockpit_shell"]["type"] is str
    assert s["cockpit_tasks"]["type"] is str
    assert s["framework_reports"]["type"] is str


# ── BD_AUTH_TOKEN: store > env, blank store defers (no lockout) ───────────────
def test_auth_token_store_over_env():
    from bulk_downloader import app as A
    cwd = os.getcwd(); tmp = tempfile.mkdtemp(); os.chdir(tmp)
    saved = _save_env("BD_AUTH_TOKEN")
    try:
        os.environ.pop("BD_AUTH_TOKEN", None)
        _fresh_store({"auth_token": "GUI_TOK"})
        assert A._expected_token() == "GUI_TOK"          # store honored, env unset
        os.environ["BD_AUTH_TOKEN"] = "ENV_TOK"
        _fresh_store({"auth_token": "GUI_TOK"})
        assert A._expected_token() == "GUI_TOK"           # store WINS over env
        _fresh_store({})
        assert A._expected_token() == "ENV_TOK"           # blank store defers to env
    finally:
        _restore_env(saved); os.chdir(cwd)


# ── BD_TOKEN: a SECOND accepted server-side token ────────────────────────────
def test_bd_token_secondary_accepted():
    from bulk_downloader import app as A
    cwd = os.getcwd(); tmp = tempfile.mkdtemp(); os.chdir(tmp)
    saved = _save_env("BD_TOKEN", "BD_AUTH_TOKEN")
    try:
        os.environ.pop("BD_TOKEN", None)
        os.environ.pop("BD_AUTH_TOKEN", None)
        _fresh_store({"auth_token": "PRIMARY", "bd_token": "SECOND"})
        toks = A._accepted_tokens()
        assert "PRIMARY" in toks and "SECOND" in toks     # both accepted
        _fresh_store({"auth_token": "PRIMARY"})
        assert "SECOND" not in A._accepted_tokens()        # blank bd_token not accepted
        _fresh_store({})
        assert A._accepted_tokens() == []                  # nothing set -> auth unconfigured
    finally:
        _restore_env(saved); os.chdir(cwd)


# ── BD_DEV_MODE: NEW gate — DISABLE hard-kill, else store > env > default-ON ──
def test_dev_mode_gate_store_env_and_kill_switch():
    from bulk_downloader import dev_tools as DT
    cwd = os.getcwd(); tmp = tempfile.mkdtemp(); os.chdir(tmp)
    saved = _save_env("BD_DEV_MODE", "BD_DEV_MODE_DISABLE")
    try:
        os.environ.pop("BD_DEV_MODE", None)
        os.environ.pop("BD_DEV_MODE_DISABLE", None)
        _fresh_store({})
        assert DT.is_dev_mode() is True                    # unset -> v3.47.7 default ON
        _fresh_store({"dev_mode": "0"})
        assert DT.is_dev_mode() is False                   # GUI "0" disables (the new gate)
        os.environ["BD_DEV_MODE"] = "0"
        _fresh_store({"dev_mode": "1"})
        assert DT.is_dev_mode() is True                    # store "1" WINS over env "0"
        os.environ["BD_DEV_MODE_DISABLE"] = "1"
        _fresh_store({"dev_mode": "1"})
        assert DT.is_dev_mode() is False                   # DISABLE kill-switch wins over all
    finally:
        _restore_env(saved); os.chdir(cwd)


# ── BD_TEST_MODE: advisory flag (store > env > False), no behavior effect ─────
def test_test_mode_advisory_flag():
    from bulk_downloader import app as A
    cwd = os.getcwd(); tmp = tempfile.mkdtemp(); os.chdir(tmp)
    saved = _save_env("BD_TEST_MODE")
    try:
        os.environ.pop("BD_TEST_MODE", None)
        _fresh_store({})
        assert A.app_test_mode() is False                  # default off
        _fresh_store({"test_mode": True})
        assert A.app_test_mode() is True                   # store honored
        _fresh_store({})
        os.environ["BD_TEST_MODE"] = "1"
        assert A.app_test_mode() is True                   # env seed when store unset
    finally:
        _restore_env(saved); os.chdir(cwd)


# ── BD_COCKPIT_SHELL: store > env > "1" (the enable preference) ───────────────
def test_cockpit_shell_pref_store_over_env():
    import cockpit_shell as CS
    cwd = os.getcwd(); tmp = tempfile.mkdtemp(); os.chdir(tmp)
    saved = _save_env("BD_COCKPIT_SHELL")
    try:
        os.environ.pop("BD_COCKPIT_SHELL", None)
        _fresh_store({})
        assert CS._shell_pref() == "1"                     # default-on preserved
        _fresh_store({"cockpit_shell": "0"})
        assert CS._shell_pref() == "0"                     # GUI hard-off
        os.environ["BD_COCKPIT_SHELL"] = "0"
        _fresh_store({"cockpit_shell": "1"})
        assert CS._shell_pref() == "1"                     # store WINS over env "0"
    finally:
        _restore_env(saved); os.chdir(cwd)


# ── BD_COCKPIT_TASKS / BD_FRAMEWORK_REPORTS: value honored, store > env ──────
def test_path_roots_store_over_env():
    import cockpit_core as CC
    cwd = os.getcwd(); tmp = tempfile.mkdtemp(); os.chdir(tmp)
    saved = _save_env("BD_COCKPIT_TASKS", "BD_FRAMEWORK_REPORTS")
    try:
        os.environ.pop("BD_COCKPIT_TASKS", None)
        os.environ.pop("BD_FRAMEWORK_REPORTS", None)
        _fresh_store({"cockpit_tasks": "/tmp/gui_tasks", "framework_reports": "/tmp/gui_reports"})
        assert str(CC.tasks_root()) == str(Path("/tmp/gui_tasks").resolve())
        assert str(CC.reports_root()) == str(Path("/tmp/gui_reports").resolve())
        os.environ["BD_COCKPIT_TASKS"] = "/tmp/env_tasks"
        _fresh_store({"cockpit_tasks": "/tmp/gui_tasks"})
        assert str(CC.tasks_root()) == str(Path("/tmp/gui_tasks").resolve())  # store wins
        _fresh_store({})
        assert str(CC.tasks_root()) == str(Path("/tmp/env_tasks").resolve())  # env seed
    finally:
        _restore_env(saved); os.chdir(cwd)


# ── inventory: all seven full ────────────────────────────────────────────────
def test_inventory_all_seven_full():
    import config_surface_inventory as P2
    d = P2.build(str(_REPO))
    items = {it["key"]: it for it in d["items"] if it["kind"] == "env_var"}
    for k in _PROMOTED_ENV:
        assert items[k]["runtime_tunable"] is True, k
        assert items[k]["gui_exposure"] == "full", (k, items[k]["gui_exposure"])


# ── manifest: all seven full ─────────────────────────────────────────────────
def test_manifest_all_seven_full():
    m = json.loads((_REPO / "reports/config_gui_manifest.json").read_text()).get("exposed", {})
    for k in _PROMOTED_ENV:
        assert m.get(k) == "full", (k, m.get(k))


# ── danger classification: auth/shell/path carry a note; test_mode does not ──
def test_danger_notes():
    import config_surface_inventory as P2
    d = P2.build(str(_REPO))
    items = {it["key"]: it for it in d["items"] if it["kind"] == "env_var"}
    for k in ("BD_AUTH_TOKEN", "BD_TOKEN", "BD_COCKPIT_SHELL",
              "BD_COCKPIT_TASKS", "BD_FRAMEWORK_REPORTS", "BD_DEV_MODE"):
        assert items[k]["danger"] is True, k
        assert items[k].get("danger_note"), k
    assert items["BD_TEST_MODE"]["danger"] is False     # advisory -> no disclaimer


# ── version pin (stable >= tuple compare, per the 311 lesson) ────────────────
def test_version_at_least_317():
    from bulk_downloader import __version__ as v
    parts = tuple(int(x) for x in v.split(".")[:3])
    assert parts >= (3, 66, 317), v

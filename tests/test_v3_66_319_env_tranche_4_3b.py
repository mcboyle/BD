"""v3.66.319 — CLI->GUI parity Phase 4.3b: BD_AUTONOMY_ENABLED -> FULL (non-guard).

Promotes the autonomy final-apply switch to a GUI-writable control (store > env >
default OFF), per explicit operator directive (option A). DANGER-classified: the
GUI control must surface the autonomous-action disclaimer.

Safety preserved: arming via store NEVER bypasses the other two apply factors —
_effective_mode() still requires (not frozen) AND (Class B == auto_with_guardrails)
in addition to armed. This test pins that the store ARMS but does not bypass.

New schema key `autonomy_enabled` (str tri-state: ""=default(off via env), "1"=on,
"0"=off, safety=True). Danger note via _AUTONOMY_ENABLE.

RED-first: fails on pristine v3.66.318 (no schema key, env-only arm read, no
manifest entry, no danger note). Zero-arg; env + store restored in try/finally.
"""
import importlib
import json
import os
import sys
import tempfile
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO))
sys.path.insert(0, str(_REPO / "tools"))

_MANIFEST = _REPO / "reports" / "config_gui_manifest.json"


def _fresh_store(d: dict) -> None:
    from bulk_downloader import global_config as GC
    Path("app_config.json").write_text(json.dumps(d), encoding="utf-8")
    GC._cached = None
    GC._cached_mtime = 0.0


class _Env:
    def __init__(self, names):
        self.names = names

    def __enter__(self):
        self.saved = {n: os.environ.get(n) for n in self.names}
        for n in self.names:
            os.environ.pop(n, None)
        return self

    def __exit__(self, *a):
        for n, v in self.saved.items():
            if v is None:
                os.environ.pop(n, None)
            else:
                os.environ[n] = v


# ── schema ───────────────────────────────────────────────────────────────────
def test_schema_has_autonomy_key():
    from bulk_downloader import global_config as GC
    s = GC.GLOBAL_CONFIG_SCHEMA
    assert "autonomy_enabled" in s, "missing schema key autonomy_enabled"
    assert s["autonomy_enabled"]["type"] is str       # tri-state ""/"1"/"0"
    assert s["autonomy_enabled"]["safety"] is True     # DANGER
    assert s["autonomy_enabled"].get("safe_default") == ""


# ── arm read: store > env seed > default OFF ──────────────────────────────────
def test_autonomy_armed_store_over_env():
    import autonomy_center as AC
    importlib.reload(AC)
    cwd = os.getcwd(); tmp = tempfile.mkdtemp(); os.chdir(tmp)
    try:
        with _Env(["BD_AUTONOMY_ENABLED"]):
            # nothing set -> default OFF
            _fresh_store({})
            assert AC._autonomy_armed() is False
            # env seed "1" -> armed
            os.environ["BD_AUTONOMY_ENABLED"] = "1"
            _fresh_store({})
            assert AC._autonomy_armed() is True
            # store "0" wins over env "1" (GUI can DISARM)
            _fresh_store({"autonomy_enabled": "0"})
            assert AC._autonomy_armed() is False
            # store "1" arms even with env unset (GUI can ARM)
            os.environ.pop("BD_AUTONOMY_ENABLED", None)
            _fresh_store({"autonomy_enabled": "1"})
            assert AC._autonomy_armed() is True
            # store "" -> falls back to env (unset) -> OFF
            _fresh_store({"autonomy_enabled": ""})
            assert AC._autonomy_armed() is False
    finally:
        os.chdir(cwd)


# ── safety: arming via store does NOT bypass the other two apply factors ──────
def test_armed_does_not_bypass_frozen_or_policy():
    import autonomy_center as AC
    importlib.reload(AC)
    import autonomy_policy as AP
    cwd = os.getcwd(); tmp = tempfile.mkdtemp(); os.chdir(tmp)
    try:
        with _Env(["BD_AUTONOMY_ENABLED"]):
            # armed via store, but Class B NOT at auto -> still suggest (not apply)
            _fresh_store({"autonomy_enabled": "1"})
            assert AC._autonomy_armed() is True
            mode = AC._effective_mode()["mode"]
            # frozen or non-auto policy must keep it out of "apply"; the default
            # sandbox policy is not auto_with_guardrails, so armed alone != apply.
            assert mode in ("suggest", "skipped"), mode
    finally:
        os.chdir(cwd)


# ── manifest + danger note ────────────────────────────────────────────────────
def test_manifest_and_danger():
    m = json.loads(_MANIFEST.read_text(encoding="utf-8"))
    assert m["exposed"].get("BD_AUTONOMY_ENABLED") == "full"
    import config_surface_inventory as csi
    d = csi.build(str(_REPO))
    items = {it["key"]: it for it in d["items"]}
    it = items["BD_AUTONOMY_ENABLED"]
    assert it["danger"] is True
    assert "autonomous" in it["danger_note"].lower()
    assert it["gui_exposure"] == "full"
    assert it["runtime_tunable"] is True

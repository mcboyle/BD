"""v3.66.316 — CLI->GUI parity Phase 4.2: bucket-3 GUARD cut (single follow-on).

Promotes the two Advanced-tranche vars whose call-time read sites live in GUARD
files (held back from the 315 non-guard cut):

  - BD_HUD_OVERLAY   (tools/capture_session.py:_hud_enabled  == guard #3) -> FULL
  - BD_LINT_KB_ALLOW (tools/build_release.py KB-lint forward == guard #7) -> FULL

Both read store > env seed > default at call time. hud_overlay is the global
decorative-HUD enable (bool, default ON; the per-capture --no-hud flag still
wins). lint_kb_allow is a comma-list of KB-lint --allow-missing-ref names; it is
read when build_release runs the KB-lint gate (build-time, same-host store).

GUARD DISCIPLINE: this cut touches TWO guard files (capture_session.py AGAIN +
build_release.py). Both new SHAs are re-baselined from the EXTRACTED ZIP and
guards_full_sha256 + guards_note updated.

RED-first: every promote assertion fails on pristine v3.66.315. Zero-arg tests;
env + store restored in try/finally; cwd restored.
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

_FULL = ("BD_HUD_OVERLAY", "BD_LINT_KB_ALLOW")


def _fresh_store(d: dict) -> None:
    from bulk_downloader import global_config as GC
    Path("app_config.json").write_text(json.dumps(d), encoding="utf-8")
    GC._cached = None
    GC._cached_mtime = 0.0


# ── schema ───────────────────────────────────────────────────────────────────
def test_schema_has_bucket3_keys():
    from bulk_downloader import global_config as GC
    s = GC.GLOBAL_CONFIG_SCHEMA
    assert s["hud_overlay"]["type"] is bool
    assert s["lint_kb_allow"]["type"] is str


# ── full: capture_session HUD store > env > default ─────────────────────────
def test_hud_overlay_store_over_env():
    import capture_session as CS
    importlib.reload(CS)
    cwd = os.getcwd(); tmp = tempfile.mkdtemp(); os.chdir(tmp)
    saved = os.environ.get("BD_HUD_OVERLAY")

    class _A:  # stand-in for argparse args; no --no-hud
        no_hud = False
    try:
        os.environ.pop("BD_HUD_OVERLAY", None)
        _fresh_store({"hud_overlay": False})
        assert CS._hud_enabled(_A()) is False           # store honored (off), env unset
        os.environ["BD_HUD_OVERLAY"] = "1"
        _fresh_store({"hud_overlay": False})
        assert CS._hud_enabled(_A()) is False           # store wins over env=1
        _fresh_store({})
        os.environ["BD_HUD_OVERLAY"] = "0"
        assert CS._hud_enabled(_A()) is False           # env seed when store unset
        _fresh_store({})
        os.environ.pop("BD_HUD_OVERLAY", None)
        assert CS._hud_enabled(_A()) is True            # default ON
        # per-capture --no-hud still wins over a store/env "on"
        _fresh_store({"hud_overlay": True})

        class _B:
            no_hud = True
        assert CS._hud_enabled(_B()) is False
    finally:
        if saved is None:
            os.environ.pop("BD_HUD_OVERLAY", None)
        else:
            os.environ["BD_HUD_OVERLAY"] = saved
        os.chdir(cwd)


# ── full: build_release lint-allow store > env > default ────────────────────
def test_lint_kb_allow_store_over_env():
    import build_release as BR
    importlib.reload(BR)
    cwd = os.getcwd(); tmp = tempfile.mkdtemp(); os.chdir(tmp)
    saved = os.environ.get("BD_LINT_KB_ALLOW")
    try:
        os.environ.pop("BD_LINT_KB_ALLOW", None)
        _fresh_store({"lint_kb_allow": "FOO_REF, BAR_REF"})
        assert BR._resolve_lint_kb_allow() == ["FOO_REF", "BAR_REF"]   # store honored
        os.environ["BD_LINT_KB_ALLOW"] = "ENV_REF"
        _fresh_store({"lint_kb_allow": "FOO_REF"})
        assert BR._resolve_lint_kb_allow() == ["FOO_REF"]              # store wins
        _fresh_store({})
        assert BR._resolve_lint_kb_allow() == ["ENV_REF"]             # env seed
        os.environ.pop("BD_LINT_KB_ALLOW", None)
        _fresh_store({})
        assert BR._resolve_lint_kb_allow() == []                      # default empty
    finally:
        if saved is None:
            os.environ.pop("BD_LINT_KB_ALLOW", None)
        else:
            os.environ["BD_LINT_KB_ALLOW"] = saved
        os.chdir(cwd)


# ── inventory + manifest ─────────────────────────────────────────────────────
def test_inventory_full():
    import config_surface_inventory as P2
    d = P2.build(str(_REPO))
    items = {it["key"]: it for it in d["items"] if it["kind"] == "env_var"}
    for k in _FULL:
        assert items[k]["gui_exposure"] == "full", (k, items[k]["gui_exposure"])


def test_guard_vars_carry_danger_note():
    """Both back guard files -> the GUI control must surface the guard danger_note."""
    import config_surface_inventory as P2
    d = P2.build(str(_REPO))
    items = {it["key"]: it for it in d["items"] if it["kind"] == "env_var"}
    for k in _FULL:
        assert items[k]["danger"] is True, k


def test_manifest_ledgers_bucket3():
    m = json.loads((_REPO / "reports/config_gui_manifest.json").read_text()).get("exposed", {})
    for k in _FULL:
        assert m.get(k) == "full", k


def test_ratchet_open_dropped():
    base = json.loads((_REPO / "reports/config_parity_baseline.json").read_text())
    assert base["open_count"] <= 53, base["open_count"]   # 55 - 2 full
    for k in _FULL:
        assert k not in base["open"], k


# ── SPA controls present ─────────────────────────────────────────────────────
def test_spa_controls_present():
    blob = ""
    for p in (_REPO / "frontend" / "src").rglob("*.ts*"):
        try:
            blob += p.read_text(encoding="utf-8", errors="replace")
        except Exception:
            pass
    assert "hud_overlay" in blob
    assert "lint_kb_allow" in blob


# ── version floor ────────────────────────────────────────────────────────────
def test_landed_at_or_after_316():
    from bulk_downloader import __version__
    parts = tuple(int(x) for x in __version__.split(".")[:3])
    assert parts >= (3, 66, 316), __version__

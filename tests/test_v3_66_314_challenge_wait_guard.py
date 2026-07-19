"""v3.66.314 — CLI->GUI parity Phase 4.2: the deferred Challenge GUARD var.

This is the explicitly-authorized guard cut that promotes the one Challenge var
held back from the 313 non-guard cut:

  - BD_CHALLENGE_WAIT_S  (tools/capture_session.py == GUARD file #3) -> FULL

The read site is a call-time getter inside ``_challenge_wait_seconds()``:
    float(os.environ.get("BD_CHALLENGE_WAIT_S", "20") or 0)
so it is promotable by the canonical pattern: a global_config key read
store > env seed > default at call time (GUI write authoritative; env is the
seed when the store key is unset). ``challenge_wait_s`` is stored as a STR — the
read site float()-parses it, matching env semantics and dodging the
int-vs-float type_mismatch edge (mirrors honeypot_score_threshold @313).

GUARD DISCIPLINE: capture_session.py is a release guard. This cut is ISOLATED
(guard-only) and its new SHA is re-baselined from the EXTRACTED ZIP.

RED-first: every promote assertion fails on pristine v3.66.313. Zero-arg tests;
env + store restored in try/finally.
"""
import json
import os
import sys
import tempfile
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO))
sys.path.insert(0, str(_REPO / "tools"))

_PROMOTED = "BD_CHALLENGE_WAIT_S"


def _fresh_store(d: dict) -> None:
    from bulk_downloader import global_config as GC
    Path("app_config.json").write_text(json.dumps(d), encoding="utf-8")
    GC._cached = None
    GC._cached_mtime = 0.0


# ── schema ───────────────────────────────────────────────────────────────────
def test_schema_has_challenge_wait_key():
    from bulk_downloader import global_config as GC
    s = GC.GLOBAL_CONFIG_SCHEMA
    assert "challenge_wait_s" in s, "challenge_wait_s missing from schema"
    assert s["challenge_wait_s"]["type"] is str, "stored as str (read site float-parses)"
    assert s["challenge_wait_s"]["safe_default"] == "20", s["challenge_wait_s"]["safe_default"]


# ── full: capture_session honors store over env (store > env seed > default) ──
def test_challenge_wait_store_over_env():
    import importlib
    import capture_session as CS
    importlib.reload(CS)
    cwd = os.getcwd(); tmp = tempfile.mkdtemp(); os.chdir(tmp)
    saved = os.environ.get(_PROMOTED)
    try:
        # store honored, env unset
        os.environ.pop(_PROMOTED, None)
        _fresh_store({"challenge_wait_s": "5"})
        assert CS._challenge_wait_seconds() == 5.0
        # store wins over env
        os.environ[_PROMOTED] = "30"
        _fresh_store({"challenge_wait_s": "5"})
        assert CS._challenge_wait_seconds() == 5.0
        # env is the seed when store unset
        _fresh_store({})
        assert CS._challenge_wait_seconds() == 30.0
        # default when neither set
        os.environ.pop(_PROMOTED, None)
        _fresh_store({})
        assert CS._challenge_wait_seconds() == 20.0
    finally:
        if saved is None:
            os.environ.pop(_PROMOTED, None)
        else:
            os.environ[_PROMOTED] = saved
        os.chdir(cwd)


def test_challenge_wait_disable_via_store_zero():
    """A store write of "0" disables the wait (parity with BD_CHALLENGE_WAIT_S=0)."""
    import importlib
    import capture_session as CS
    importlib.reload(CS)
    cwd = os.getcwd(); tmp = tempfile.mkdtemp(); os.chdir(tmp)
    saved = os.environ.get(_PROMOTED)
    try:
        os.environ.pop(_PROMOTED, None)
        _fresh_store({"challenge_wait_s": "0"})
        assert CS._challenge_wait_seconds() == 0.0
    finally:
        if saved is None:
            os.environ.pop(_PROMOTED, None)
        else:
            os.environ[_PROMOTED] = saved
        os.chdir(cwd)


# ── inventory + manifest ─────────────────────────────────────────────────────
def test_inventory_full():
    import config_surface_inventory as P2
    d = P2.build(str(_REPO))
    items = {it["key"]: it for it in d["items"] if it["kind"] == "env_var"}
    assert items[_PROMOTED]["gui_exposure"] == "full", (
        _PROMOTED, items[_PROMOTED]["gui_exposure"])


def test_manifest_ledgers_challenge_wait():
    m = json.loads((_REPO / "reports/config_gui_manifest.json").read_text()).get("exposed", {})
    assert m.get(_PROMOTED) == "full", _PROMOTED


def test_ratchet_open_dropped():
    base = json.loads((_REPO / "reports/config_parity_baseline.json").read_text())
    assert base["open_count"] <= 75, base["open_count"]   # 76 - 1 full; ceiling
    assert _PROMOTED not in base["open"], _PROMOTED


# ── SPA control present ──────────────────────────────────────────────────────
def test_spa_challenge_wait_control_present():
    blob = ""
    for p in (_REPO / "frontend" / "src").rglob("*.ts*"):
        try:
            blob += p.read_text(encoding="utf-8", errors="replace")
        except Exception:
            pass
    assert "challenge_wait_s" in blob


# ── version floor ────────────────────────────────────────────────────────────
def test_landed_at_or_after_314():
    from bulk_downloader import __version__
    parts = tuple(int(x) for x in __version__.split(".")[:3])
    assert parts >= (3, 66, 314), __version__

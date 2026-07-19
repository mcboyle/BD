"""v3.66.490 K3 (plugin-v3 kind): namer plugins.

Own the output filename/path (Plex/Jellyfin/Stash-shaped naming). ONE winner by
priority; falls back to the built-in namer when no plugin produces a valid path.

Contract:
  name(meta, ctx) -> relative_path        # validated: relative, no traversal,
                                          # sanitized per component

run_namer(meta, ctx) -> Optional[str]:
  * priority order (lower first); the first plugin returning a NON-EMPTY,
    VALID relative path wins;
  * a plugin returning ""/None/invalid (absolute or ``..`` traversal) is
    SKIPPED -- a later valid namer may still win;
  * returns None when no plugin produces a valid path -> caller uses the
    built-in namer;
  * CWE-22: absolute paths and ``..`` traversal are rejected; each path
    component is sanitized (mirrors the /screenshots/ basename discipline).

K3 raises PLUGIN_API_MAX to 5.

Runner-safe: zero-arg fns, no pytest builtins, globals restored in try/finally.
"""
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from bulk_downloader import plugins as P  # noqa: E402


def test_api_max_raised_to_5_keeps_prior_compatible():
    assert P.PLUGIN_API_MAX >= 5
    for v in (2, 3, 4, 5):
        ok, _ = P.api_compatible({"api_version": v})
        assert ok


def test_namer_capability_documented():
    assert getattr(P, "CAP_NAMER", None) == "namer"
    ke = P.known_events()
    assert P.CAP_NAMER in ke["capabilities"]
    assert ke["api_max"] >= 5


def test_register_list_status_reset():
    P.reset()
    try:
        P.register_namer(lambda m, c: "a/b.mp4", name="n1", priority=10)
        assert "n1" in [n["name"] for n in P.list_namers()]
        assert "namers" in P.status()
        P.reset()
        assert P.list_namers() == []
    finally:
        P.reset()


# ── (a) plugin path used when present ─────────────────────────────────
def test_plugin_path_used_when_present():
    P.reset()
    try:
        P.register_namer(lambda m, c: "Show/S01/ep01.mp4", name="plex")
        out = P.run_namer({"title": "ep01"}, {})
        assert out == "Show/S01/ep01.mp4"
    finally:
        P.reset()


# ── (b) built-in fallback when absent / empty ─────────────────────────
def test_builtin_fallback_when_no_namer():
    P.reset()
    try:
        assert P.run_namer({"title": "x"}, {}) is None
    finally:
        P.reset()


def test_empty_or_none_namer_falls_through():
    P.reset()
    try:
        P.register_namer(lambda m, c: "", name="empty", priority=10)
        P.register_namer(lambda m, c: None, name="none", priority=20)
        assert P.run_namer({}, {}) is None
    finally:
        P.reset()


# ── (c) CWE-22 path traversal / absolute rejected ─────────────────────
def test_traversal_rejected():
    P.reset()
    try:
        P.register_namer(lambda m, c: "../../etc/passwd", name="evil")
        assert P.run_namer({}, {}) is None
    finally:
        P.reset()


def test_absolute_path_rejected():
    P.reset()
    try:
        P.register_namer(lambda m, c: "/etc/passwd", name="abs")
        assert P.run_namer({}, {}) is None
    finally:
        P.reset()


def test_windows_drive_absolute_rejected():
    P.reset()
    try:
        P.register_namer(lambda m, c: "C:/Windows/x.mp4", name="drv")
        assert P.run_namer({}, {}) is None
    finally:
        P.reset()


def test_traversal_namer_skipped_then_valid_wins():
    P.reset()
    try:
        P.register_namer(lambda m, c: "../escape.mp4", name="evil", priority=10)
        P.register_namer(lambda m, c: "Safe/ok.mp4", name="safe", priority=20)
        assert P.run_namer({}, {}) == "Safe/ok.mp4"
    finally:
        P.reset()


def test_components_sanitized():
    P.reset()
    try:
        # forbidden chars inside a component get sanitized, separators kept
        P.register_namer(lambda m, c: 'Show: <bad>/ep?.mp4', name="dirty")
        out = P.run_namer({}, {})
        assert out is not None
        for ch in '<>:"|?*':
            assert ch not in out
        assert "/" in out  # legit separator preserved
    finally:
        P.reset()


# ── (d) priority winner among multiple namers ─────────────────────────
def test_priority_winner():
    P.reset()
    try:
        P.register_namer(lambda m, c: "low/win.mp4", name="low", priority=10)
        P.register_namer(lambda m, c: "high/lose.mp4", name="high", priority=20)
        assert P.run_namer({}, {}) == "low/win.mp4"
    finally:
        P.reset()


# ── exception isolation ───────────────────────────────────────────────
def test_throwing_namer_isolated():
    P.reset()
    try:
        def boom(m, c):
            raise RuntimeError("nope")
        P.register_namer(boom, name="boom", priority=10)
        P.register_namer(lambda m, c: "ok/x.mp4", name="ok", priority=20)
        out = P.run_namer({}, {})  # must not raise
        assert out == "ok/x.mp4"
    finally:
        P.reset()


# ── decorator parity ──────────────────────────────────────────────────
def test_namer_decorator():
    P.reset()
    try:
        @P.namer(priority=5, name="deco")
        def _n(meta, ctx):
            return "deco/path.mp4"
        assert P.run_namer({}, {}) == "deco/path.mp4"
    finally:
        P.reset()

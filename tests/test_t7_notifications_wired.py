"""T7 notify/tg/alerts tranche — migration pins (v3.66.210).

Proves the 7 legacy-only notify/tg/alerts families are now SPA-wired
(they drop out of the legacy_parity legacy-only set), the /notifications
route is lazy-loaded with an inbound nav link, the secret inputs are
write-only ((R) rule: GET masks them, the matching capture redaction
ships the same cut), writes are never one-click (they arm a Pending),
and the ratchet baseline committed the 41 -> 34 shrink. RED on pristine
v3.66.209 (none of these were wired; baseline was 41; capture_redact had
no code/k key).

run_tests.py conventions: zero-arg test functions; repo root from
__file__; no pytest builtins.
"""
import importlib.util
import json
import re
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SRC = REPO / "frontend" / "src"

# The 7 legacy-only families T7 ports into the SPA /notifications route.
T7_ENDPOINTS = [
    "/api/notify/apprise/settings",
    "/api/notify/apprise/validate",
    "/api/notify/apprise/test",
    "/api/tg/status",
    "/api/tg/settings",
    "/api/tg/test",
    "/api/alerts/active",
]


def _load_legacy_parity():
    spec = importlib.util.spec_from_file_location(
        "legacy_parity", REPO / "tools" / "legacy_parity.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_all_7_t7_endpoints_are_spa_wired():
    """None of the 7 T7 families may remain in the legacy-only set."""
    lp = _load_legacy_parity()
    legacy_only = set(lp.measure()["legacy_only"])
    norm = {re.sub(r"\{[^}]+\}", "*", e) for e in legacy_only}
    still_legacy = [ep for ep in T7_ENDPOINTS if ep in norm]
    assert not still_legacy, (
        "T7 endpoints still legacy-only (not SPA-wired): " + repr(still_legacy))


def test_t7_full_literals_present_in_hook():
    """The wiring must be FULL /api/ string literals (the scanner cannot
    credit concatenated base vars). Pin each in the hook source."""
    hook = (SRC / "hooks" / "useNotificationsData.ts").read_text(encoding="utf-8")
    for ep in T7_ENDPOINTS:
        assert f'"{ep}"' in hook or f"`{ep}" in hook, f"{ep} not a full literal"


def test_t7_route_lazy_and_nav_linked():
    """/notifications is a lazy, default-exported route with an inbound
    nav link (nav_reachability)."""
    app = (SRC / "App.tsx").read_text(encoding="utf-8")
    assert 'import("./routes/Notifications")' in app
    assert 'path="/notifications"' in app
    route = (SRC / "routes" / "Notifications.tsx").read_text(encoding="utf-8")
    assert "export default function Notifications" in route
    cp = (SRC / "components" / "CommandPalette.tsx").read_text(encoding="utf-8")
    assert 'go("/notifications")' in cp


def test_t7_secrets_are_write_only():
    """(R) rule: secret inputs are write-only. The route never seeds the
    apprise URL / tg token fields from GET, and clears them after save."""
    route = (SRC / "routes" / "Notifications.tsx").read_text(encoding="utf-8")
    # write-only paste fields start empty
    assert 'useState("")' in route
    # GET never echoes the raw URLs — only the set-flag + count are read
    assert "notify_apprise_urls_count" in route
    assert "tg_bot_token_set" in route
    # the secret state is cleared after a successful save (not retained)
    assert "setAppriseUrls(\"\")" in route
    assert "setTgToken(\"\")" in route


def test_t7_writes_never_one_click():
    """save/test writes arm a Pending and dispatch from a confirm dialog,
    never straight from the page onClick."""
    route = (SRC / "routes" / "Notifications.tsx").read_text(encoding="utf-8")
    assert "const confirmRun = () =>" in route
    # no save/test mutation fires directly from an onClick
    assert not re.search(r"onClick=\{[^}]*save(Apprise|Tg)\.mutate", route)
    assert not re.search(r"onClick=\{[^}]*test(Apprise|Tg)\.mutate", route)


def test_t7_backend_apprise_get_masks_urls():
    """The legacy GET /api/notify/apprise/settings no longer echoes raw
    apprise URLs (PREP_AUDIT §8 leak). RED on pristine 209."""
    import os
    os.environ.setdefault("BD_DISABLE_KEEPALIVE", "1")
    from bulk_downloader.app import (
        app, _load_global_notify_settings, _save_global_notify_settings)
    cfg = _load_global_notify_settings()
    cfg["notify_apprise_urls"] = "tgram://111:SECRETTOKEN/222"
    _save_global_notify_settings(cfg)
    body = app.test_client().get("/api/notify/apprise/settings").get_data(as_text=True)
    assert "SECRETTOKEN" not in body, "raw apprise token leaked in GET"
    j = json.loads(body)
    s = j["settings"]
    assert "notify_apprise_urls" not in s, "raw URLs still echoed"
    assert s.get("notify_apprise_urls_set") is True
    assert s.get("notify_apprise_urls_count") == 1


def test_t7_sensitive_qs_key_covers_code_and_k():
    """SENSITIVE_QS_KEY folds in the code (vix) / k (bang) analytics keys —
    exact-match only (no geocode/key/kind over-match). RED on 209."""
    from bulk_downloader.capture_redact import SENSITIVE_QS_KEY as R
    assert R.search("code") and R.search("k")
    assert not R.search("geocode")
    assert not R.search("zipcode")
    assert not R.search("kind")


def test_t7_ratchet_committed_34():
    """The ratchet baseline committed the 41 -> 34 shrink.

    Pin the T7 ceiling, not the snapshot: T7 committed 34, but the ratchet is
    monotonic-down and later tranches (T8/T10 -> 10, T9 -> 1) drive it lower.
    A hard `== 34` re-breaks on every future migration; `<= 34` stays a real
    "T7 did not regress" guard. (Stale-magnitude-floor footgun: matches the
    test_legacy_parity floor relax 20 -> 0.)
    """
    base = json.loads((REPO / "reports" / "legacy_parity_baseline.json").read_text())
    assert base["legacy_only_count"] <= 34, base["legacy_only_count"]
    for ep in T7_ENDPOINTS:
        assert ep not in base["legacy_only"], f"{ep} still in baseline"

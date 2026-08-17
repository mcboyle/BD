"""Current push and service-worker SPA contract.

Proves the four push endpoint families remain SPA-wired:

  - 4 push families wired via the new hooks/usePush.ts (FULL /api/ literals);
    info is useQuery, subscribe/test/unsubscribe are useMutation (never
    auto-fire; armed by the user toggling the section or a B-tier test confirm).
  - PushSection is imported and rendered in the Notifications route.
  - The SPA registers the EXISTING root-scope /sw.js in main.tsx (a same-scope
    UPDATE, not a new SW) and the enable path reuses an existing subscription
    via getSubscription() before ever calling subscribe() — the subscription-
    survival gate T9 was split to protect.

RED on pristine v3.66.212 (push unwired; no usePush hook; no PushSection mount;
no SPA /sw.js registration; the 4 push families still in the baseline-5).

run_tests.py conventions: zero-arg test functions; repo root from __file__;
no pytest builtins.
"""
import re
from pathlib import Path

BD_GATE_SCOPE = "repo-wide"

REPO = Path(__file__).resolve().parent.parent
SRC = REPO / "frontend" / "src"

PUSH_ENDPOINTS = [
    "/api/push/info",
    "/api/push/subscribe",
    "/api/push/test",
    "/api/push/unsubscribe",
]
def _fn_body(text, name):
    """Slice an exported function body from `export [async] function NAME` to
    the next top-level `export [async] function` (or EOF). Good enough to scope
    a hint check."""
    m = re.search(r"export (?:async )?function " + re.escape(name) + r"\b", text)
    assert m, f"{name} not found"
    rest = text[m.end():]
    nxt = re.search(r"\nexport (?:async )?function ", rest)
    return rest[: nxt.start()] if nxt else rest


def test_t9b_push_literals_present_in_hook():
    """FULL /api/ literals — the scanner cannot credit concatenated bases."""
    hook = (SRC / "hooks" / "usePush.ts").read_text(encoding="utf-8")
    for ep in PUSH_ENDPOINTS:
        assert f'"{ep}"' in hook or f"`{ep}" in hook, \
            f"{ep} not a full literal in usePush.ts"


def test_t9b_info_is_query_writes_are_mutations():
    """info is a query; subscribe/test/unsubscribe are mutations (never
    auto-fire)."""
    hook = (SRC / "hooks" / "usePush.ts").read_text(encoding="utf-8")
    info = _fn_body(hook, "usePushInfo")
    assert re.search(r"\buseQuery\b", info), "info must be a query"
    for w in ("usePushSubscribe", "usePushUnsubscribe", "usePushTest"):
        body = _fn_body(hook, w)
        assert "useMutation" in body, f"{w} must be a mutation (never auto-fire)"


def test_t9b_subscription_survival_reuse():
    """The enable path must reuse an existing subscription before subscribing —
    getSubscription() ahead of subscribe() so the legacy endpoint survives."""
    hook = (SRC / "hooks" / "usePush.ts").read_text(encoding="utf-8")
    body = _fn_body(hook, "buildSubscriptionForEnable")
    gs = body.find("getSubscription")
    sub = body.find(".subscribe(")
    assert gs != -1, "enable path must read getSubscription() first"
    assert sub != -1, "enable path must be able to mint a new subscription"
    assert gs < sub, "getSubscription() must precede subscribe() (reuse first)"
    # subscribe() must be gated behind the 'no existing subscription' branch.
    assert re.search(r"if\s*\(\s*!\s*sub\s*\)", body), \
        "subscribe() must be guarded by `if (!sub)` (reuse, never re-mint)"


def test_t9b_section_mounted_in_notifications():
    """PushSection is imported and rendered in the Notifications route."""
    n = (SRC / "routes" / "Notifications.tsx").read_text(encoding="utf-8")
    assert 'from "@/components/sections/PushSection"' in n, \
        "PushSection not imported"
    assert "<PushSection" in n, "PushSection not rendered"


def test_t9b_spa_registers_root_sw():
    """The SPA boot registers the EXISTING root-scope /sw.js (survival: a
    same-scope update, not a new SW)."""
    main = (SRC / "main.tsx").read_text(encoding="utf-8")
    assert re.search(r'serviceWorker\.register\(\s*"/sw\.js"', main), \
        "main.tsx must register /sw.js"
    assert '"/sw.js"' in main and 'scope: "/"' in main, \
        "registration must target root scope /"
